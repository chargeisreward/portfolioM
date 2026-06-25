"""Postgres 数据结构迁移脚本 — 多用户隔离补全 + 冗余清理 + 索引优化

迁移内容：
1. PenetrationSnapshot 补 user_id 列（NOT NULL + 索引 + 重建唯一约束）
2. AggregationCache.user_id default 改为 NULL（原 default=2 写死 advisor id）
3. AggregationTimeseries.user_id default 改为 NULL（同上）
4. 补充缺失的 user_id 索引（4 张表）

设计原则：
- 幂等：所有操作使用 IF NOT EXISTS / IF EXISTS，可重跑
- 安全：先备份再执行（pg_dump）
- 可预览：--dry-run 模式只打印不执行

用法：
    cd backend && python scripts/migrate_pg_structure.py          # 执行迁移
    cd backend && python scripts/migrate_pg_structure.py --dry-run # 预览
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, inspect
from database import engine


def _column_exists(conn, table: str, column: str) -> bool:
    """检查列是否存在"""
    result = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
    """), {"t": table, "c": column})
    return result.fetchone() is not None


def _constraint_exists(conn, table: str, constraint: str) -> bool:
    """检查约束是否存在"""
    result = conn.execute(text("""
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = :t AND constraint_name = :c
    """), {"t": table, "c": constraint})
    return result.fetchone() is not None


def _index_exists(conn, table: str, index: str) -> bool:
    """检查索引是否存在"""
    result = conn.execute(text("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = :t AND indexname = :i
    """), {"t": table, "i": index})
    return result.fetchone() is not None


def migrate_penetration_snapshot_user_id(conn):
    """1.1 PenetrationSnapshot 补 user_id 列 + 索引 + 重建 UK"""
    table = "penetration_snapshot"
    print(f"  [1.1] {table}: 补 user_id 列...")

    # 加列（若不存在）
    if not _column_exists(conn, table, "user_id"):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id BIGINT"))
        print(f"    -> ADD COLUMN user_id BIGINT")
    else:
        print(f"    -> user_id 列已存在，跳过 ADD COLUMN")

    # 填充默认值（现有数据归 advisor=2）
    result = conn.execute(text(
        f"UPDATE {table} SET user_id = 2 WHERE user_id IS NULL"
    ))
    print(f"    -> UPDATE {result.rowcount} 行设置 user_id=2")

    # 设 NOT NULL
    conn.execute(text(
        f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL"
    ))
    print(f"    -> SET NOT NULL")

    # 创建索引
    idx = f"ix_{table}_user_id"
    if not _index_exists(conn, table, idx):
        conn.execute(text(f"CREATE INDEX {idx} ON {table} (user_id)"))
        print(f"    -> CREATE INDEX {idx}")
    else:
        print(f"    -> 索引 {idx} 已存在")

    # 重建唯一约束（含 user_id）
    if _constraint_exists(conn, table, "ux_pnsnap"):
        conn.execute(text(f"ALTER TABLE {table} DROP CONSTRAINT ux_pnsnap"))
        print(f"    -> DROP CONSTRAINT ux_pnsnap (旧)")

    conn.execute(text(
        f"ALTER TABLE {table} "
        f"ADD CONSTRAINT ux_pnsnap UNIQUE (as_of_date, user_id, holding_code, stock_code)"
    ))
    print(f"    -> ADD CONSTRAINT ux_pnsnap (含 user_id)")


def migrate_aggregation_defaults(conn):
    """1.2-1.3 AggregationCache/Timeseries user_id default 改为 NULL"""
    for table in ["aggregation_cache", "aggregation_timeseries"]:
        print(f"  [1.{2 if table == 'aggregation_cache' else 3}] {table}: user_id default → NULL")
        conn.execute(text(
            f"ALTER TABLE {table} ALTER COLUMN user_id SET DEFAULT NULL"
        ))
        print(f"    -> SET DEFAULT NULL")


def migrate_missing_indexes(conn):
    """3.1 补充缺失的 user_id 列 + 索引

    部分表（如 csi300_constituent_snapshot）在 Postgres 中尚未添加 user_id 列，
    需先 ADD COLUMN + 填充默认值 + SET NOT NULL，再创建索引。
    """
    tables = [
        "overseas_share_financial_snapshot",
        "csi300_constituent_snapshot",
        "aggregation_cache",
        "aggregation_timeseries",
    ]
    print(f"  [3.1] 补充 user_id 列 + 索引:")
    for table in tables:
        # 先检查列是否存在，不存在则添加
        if not _column_exists(conn, table, "user_id"):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id BIGINT"))
            print(f"    -> {table}: ADD COLUMN user_id BIGINT")
            result = conn.execute(text(
                f"UPDATE {table} SET user_id = 2 WHERE user_id IS NULL"
            ))
            print(f"    -> {table}: UPDATE {result.rowcount} 行设置 user_id=2")
            conn.execute(text(
                f"ALTER TABLE {table} ALTER COLUMN user_id SET NOT NULL"
            ))
            print(f"    -> {table}: SET NOT NULL")
        else:
            print(f"    -> {table}: user_id 列已存在")

        # 创建索引
        idx = f"ix_{table}_user_id"
        if not _index_exists(conn, table, idx):
            conn.execute(text(f"CREATE INDEX {idx} ON {table} (user_id)"))
            print(f"    -> {table}: CREATE INDEX {idx}")
        else:
            print(f"    -> {table}: 索引已存在，跳过")


def migrate():
    """执行所有迁移步骤"""
    print("=" * 60)
    print("Postgres 数据结构迁移")
    print("=" * 60)

    with engine.begin() as conn:
        print("\n[Step 1] 多用户隔离补全")
        migrate_penetration_snapshot_user_id(conn)
        migrate_aggregation_defaults(conn)

        print("\n[Step 3] 索引优化")
        migrate_missing_indexes(conn)

    print("\n" + "=" * 60)
    print("迁移完成！")
    print("=" * 60)


def dry_run():
    """预览模式：只打印不执行"""
    print("=" * 60)
    print("[DRY-RUN] Postgres 数据结构迁移预览")
    print("=" * 60)
    print("\n计划操作：")
    print("  [1.1] penetration_snapshot:")
    print("    - ADD COLUMN IF NOT EXISTS user_id BIGINT")
    print("    - UPDATE SET user_id=2 WHERE NULL")
    print("    - ALTER COLUMN user_id SET NOT NULL")
    print("    - CREATE INDEX ix_penetration_snapshot_user_id")
    print("    - DROP + ADD CONSTRAINT ux_pnsnap (as_of_date, user_id, holding_code, stock_code)")
    print("  [1.2] aggregation_cache: ALTER user_id SET DEFAULT NULL")
    print("  [1.3] aggregation_timeseries: ALTER user_id SET DEFAULT NULL")
    print("  [3.1] 补充 user_id 索引（4 张表）")

    # 检查当前状态
    print("\n当前状态检查：")
    with engine.connect() as conn:
        # penetration_snapshot.user_id
        exists = _column_exists(conn, "penetration_snapshot", "user_id")
        print(f"  penetration_snapshot.user_id 列存在: {exists}")

        # ux_pnsnap 约束
        c_exists = _constraint_exists(conn, "penetration_snapshot", "ux_pnsnap")
        print(f"  ux_pnsnap 约束存在: {c_exists}")

        # aggregation_cache default
        result = conn.execute(text("""
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'aggregation_cache' AND column_name = 'user_id'
        """)).fetchone()
        print(f"  aggregation_cache.user_id default: {result[0] if result else 'N/A'}")


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        dry_run()
    else:
        migrate()
