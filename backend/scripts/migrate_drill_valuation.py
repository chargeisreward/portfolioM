"""下钻估值数据补全迁移脚本 — 2026-06-25

迁移内容：
1. 三张估值表（a_share / hk_share / overseas_share_financial_snapshot）user_id 改 nullable
   —— 估值是市场公共数据，与持仓交易无关，不应按 user 隔离
2. FundDrillSnapshot 加 4 字段：pe_ttm / pb_mrq / ps_ttm / dividend_yield
3. 回填现有 fund_drill_snapshot 行（按 stock_code 后缀路由 A/H 表，取 ≤ as_of_date 最近估值行）

设计原则：
- 幂等：所有操作使用 IF NOT EXISTS / IF EXISTS，可重跑
- 安全：建议执行前 pg_dump 备份
- 可预览：--dry-run 模式只打印不执行
- 跨库兼容：PG 用 information_schema，SQLite 用 PRAGMA table_info

用法：
    cd backend && python scripts/migrate_drill_valuation.py          # 执行迁移
    cd backend && python scripts/migrate_drill_valuation.py --dry-run # 预览
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, inspect
from database import engine, DATABASE_URL


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

def _is_sqlite() -> bool:
    return "sqlite" in DATABASE_URL


def _column_exists(conn, table: str, column: str) -> bool:
    """检查列是否存在（PG / SQLite 兼容）。"""
    if _is_sqlite():
        result = conn.execute(text(f"PRAGMA table_info({table})"))
        return any(row[1] == column for row in result)
    result = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
    """), {"t": table, "c": column})
    return result.fetchone() is not None


def _column_nullable(conn, table: str, column: str) -> bool:
    """检查列当前是否 nullable（PG only，SQLite 返回 False）。"""
    if _is_sqlite():
        # SQLite PRAGMA table_info 第 3 列是 notnull（0=nullable, 1=notnull）
        result = conn.execute(text(f"PRAGMA table_info({table})"))
        for row in result:
            if row[1] == column:
                return row[3] == 0
        return False
    result = conn.execute(text("""
        SELECT is_nullable FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
    """), {"t": table, "c": column})
    row = result.fetchone()
    return row[0] == "YES" if row else False


# --------------------------------------------------------------------------
# 迁移步骤
# --------------------------------------------------------------------------

def migrate_valuation_tables_user_id_nullable(conn):
    """1. 三张估值表 user_id 改 nullable。"""
    tables = [
        "a_share_financial_snapshot",
        "hk_share_financial_snapshot",
        "overseas_share_financial_snapshot",
    ]
    print("\n[Step 1] 三张估值表 user_id 改 nullable")
    for table in tables:
        if not _column_exists(conn, table, "user_id"):
            print(f"  {table}: user_id 列不存在，跳过")
            continue
        if _column_nullable(conn, table, "user_id"):
            print(f"  {table}: user_id 已 nullable，跳过")
            continue
        if _is_sqlite():
            # SQLite 不支持 ALTER COLUMN，需要重建表（这里直接跳过，开发库重建代价低）
            # 实际 SQLite 通常会通过 create_all 重建，此处仅打印
            print(f"  {table}: SQLite 不支持 ALTER COLUMN DROP NOT NULL，跳过（建议重建库）")
        else:
            conn.execute(text(
                f"ALTER TABLE {table} ALTER COLUMN user_id DROP NOT NULL"
            ))
            print(f"  {table}: ALTER COLUMN user_id DROP NOT NULL ✓")


def migrate_fund_drill_snapshot_add_valuation_columns(conn):
    """2. FundDrillSnapshot 加 4 估值字段。"""
    table = "fund_drill_snapshot"
    columns = [
        ("pe_ttm", "FLOAT"),
        ("pb_mrq", "FLOAT"),
        ("ps_ttm", "FLOAT"),
        ("dividend_yield", "FLOAT"),
    ]
    print("\n[Step 2] FundDrillSnapshot 加 4 估值字段")
    for col, coltype in columns:
        if _column_exists(conn, table, col):
            print(f"  {table}.{col} 已存在，跳过")
            continue
        if _is_sqlite():
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
        else:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {coltype}'))
        print(f"  {table}: ADD COLUMN {col} {coltype} ✓")


def backfill_fund_drill_snapshot_valuation(conn):
    """3. 回填现有 fund_drill_snapshot 行的估值字段。

    路由策略：
    - stock_code 以 .SH / .SZ / .BJ 结尾 → a_share_financial_snapshot
    - stock_code 以 .HK 结尾 → hk_share_financial_snapshot
    - 其他（如 .US）→ overseas_share_financial_snapshot（当前表为空，留 NULL）

    取 ≤ as_of_date 的最近估值行，不按 user_id 过滤。
    """
    print("\n[Step 3] 回填 fund_drill_snapshot 估值字段")

    # 检查 fund_drill_snapshot 是否有数据
    count_result = conn.execute(text("SELECT COUNT(*) FROM fund_drill_snapshot"))
    total_rows = count_result.scalar() or 0
    if total_rows == 0:
        print(f"  fund_drill_snapshot 表为空，跳过回填")
        return

    print(f"  待回填总行数: {total_rows}")

    # 统计已回填行数
    already_filled_result = conn.execute(text(
        "SELECT COUNT(*) FROM fund_drill_snapshot WHERE pe_ttm IS NOT NULL"
    ))
    already_filled = already_filled_result.scalar() or 0
    if already_filled == total_rows:
        print(f"  全部 {total_rows} 行已回填，跳过")
        return

    print(f"  已回填: {already_filled}, 待处理: {total_rows - already_filled}")

    # 逐行回填（Python 循环 + 路由）
    rows = conn.execute(text(
        "SELECT id, stock_code, as_of_date FROM fund_drill_snapshot "
        "WHERE pe_ttm IS NULL"
    )).fetchall()

    backfilled = 0
    for row in rows:
        row_id, stock_code, as_of_date = row
        # 路由到对应估值表
        code_upper = (stock_code or "").upper()
        if code_upper.endswith(".HK"):
            model_table = "hk_share_financial_snapshot"
        elif code_upper.endswith((".US", ".O")):
            model_table = "overseas_share_financial_snapshot"
        else:
            # .SH / .SZ / .BJ / 无后缀都视为 A 股
            model_table = "a_share_financial_snapshot"

        # 查 ≤ as_of_date 的最近估值行（不按 user_id 过滤）
        # 同时支持带后缀和不带后缀的 stock_code 匹配
        code_norm = stock_code.split(".")[0]
        snap_row = conn.execute(text(f"""
            SELECT pe_ttm, pb_mrq, ps_ttm, dividend_yield
            FROM {model_table}
            WHERE as_of_date <= :as_of
              AND (stock_code = :code OR stock_code = :code_norm)
            ORDER BY as_of_date DESC
            LIMIT 1
        """), {"as_of": as_of_date, "code": stock_code, "code_norm": code_norm}).fetchone()

        if snap_row:
            conn.execute(text("""
                UPDATE fund_drill_snapshot
                SET pe_ttm = :pe, pb_mrq = :pb, ps_ttm = :ps, dividend_yield = :dy
                WHERE id = :id
            """), {
                "pe": snap_row[0], "pb": snap_row[1],
                "ps": snap_row[2], "dy": snap_row[3],
                "id": row_id,
            })
            backfilled += 1

    print(f"  回填完成: {backfilled} / {len(rows)} 行")


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def migrate():
    """执行所有迁移步骤。"""
    print("=" * 60)
    print("下钻估值数据补全迁移 — 2026-06-25")
    print(f"数据库: {'SQLite' if _is_sqlite() else 'PostgreSQL'}")
    print("=" * 60)

    with engine.begin() as conn:
        migrate_valuation_tables_user_id_nullable(conn)
        migrate_fund_drill_snapshot_add_valuation_columns(conn)
        backfill_fund_drill_snapshot_valuation(conn)

    print("\n" + "=" * 60)
    print("迁移完成！")
    print("=" * 60)


def dry_run():
    """预览模式：只打印不执行。"""
    print("=" * 60)
    print("[DRY-RUN] 下钻估值数据补全迁移预览")
    print(f"数据库: {'SQLite' if _is_sqlite() else 'PostgreSQL'}")
    print("=" * 60)
    print("\n计划操作：")
    print("  [Step 1] 三张估值表 user_id 改 nullable")
    for table in ["a_share_financial_snapshot", "hk_share_financial_snapshot",
                  "overseas_share_financial_snapshot"]:
        print(f"    - {table}: ALTER COLUMN user_id DROP NOT NULL")
    print("  [Step 2] FundDrillSnapshot 加 4 字段")
    for col in ["pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield"]:
        print(f"    - fund_drill_snapshot: ADD COLUMN IF NOT EXISTS {col} FLOAT")
    print("  [Step 3] 回填 fund_drill_snapshot 现有行的估值字段")
    print("    - 按 stock_code 后缀路由 A/H/Overseas 表")
    print("    - 取 ≤ as_of_date 的最近估值行（不按 user_id 过滤）")

    # 当前状态检查
    print("\n当前状态检查：")
    with engine.connect() as conn:
        # fund_drill_snapshot 估值字段
        for col in ["pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield"]:
            exists = _column_exists(conn, "fund_drill_snapshot", col)
            print(f"  fund_drill_snapshot.{col} 列存在: {exists}")

        # fund_drill_snapshot 总行数
        count = conn.execute(text("SELECT COUNT(*) FROM fund_drill_snapshot")).scalar() or 0
        print(f"  fund_drill_snapshot 总行数: {count}")
        if count > 0:
            filled = conn.execute(text(
                "SELECT COUNT(*) FROM fund_drill_snapshot WHERE pe_ttm IS NOT NULL"
            )).scalar() or 0
            print(f"  fund_drill_snapshot 已回填估值行数: {filled}")

        # 三张估值表 user_id nullable 状态
        for table in ["a_share_financial_snapshot", "hk_share_financial_snapshot",
                       "overseas_share_financial_snapshot"]:
            nullable = _column_nullable(conn, table, "user_id")
            print(f"  {table}.user_id nullable: {nullable}")


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        dry_run()
    else:
        migrate()
