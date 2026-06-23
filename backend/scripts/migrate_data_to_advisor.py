"""迁移原单用户数据到 advisor 用户

执行背景（2026-06-24 用户确认）：
  - 多用户升级前，单用户系统的 44 行 holdings + 4 行 watchlist 全在 user_id=1（admin）
  - 现需要把这一份数据归到 advisor 自己（advisor 用 user 身份能看到的部分）
  - 同时把 user_a 重命名为 user，advisor_x 重命名为 advisor（测试用）

用法：
    cd backend && python scripts/migrate_data_to_advisor.py            # 真改
    cd backend && python scripts/migrate_data_to_advisor.py --dry-run  # 只打印
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from config import DATABASE_URL


# (原 username, 新 username, 新 display_name)
RENAME_USERS = [
    ("user_a",    "user",    "王用户"),
    ("advisor_x", "advisor", "李顾问"),
]

# user_id 迁移映射：把原 user_id=1 (admin) 的数据搬到 advisor 的新 id
# advisor 在迁移后会是 user_id=2（因为它当前就是 id=2；admin 永远是 1）
# 此脚本只动 user_id=1 的 holdings/watchlist → user_id=2
SOURCE_USER_ID = 1
TARGET_USER_ID = 2  # advisor

TABLES_TO_MIGRATE = [
    "holdings",
    "watchlist",
]


def run(dry_run: bool = False):
    engine = create_engine(DATABASE_URL)
    with engine.begin() as c:  # begin() = 自动 commit
        # --- 1. 用户名重命名 ---
        for old, new, display in RENAME_USERS:
            existing_new = c.execute(
                text("SELECT id FROM users WHERE username = :n"), {"n": new}
            ).fetchone()
            if existing_new:
                print(f"  [skip rename] {new} 已存在（id={existing_new[0]}），跳过 {old} → {new}")
                continue
            row = c.execute(
                text("SELECT id FROM users WHERE username = :n"), {"n": old}
            ).fetchone()
            if not row:
                print(f"  [skip rename] 原用户 {old} 不存在，跳过")
                continue
            uid = row[0]
            if dry_run:
                print(f"  [dry-run rename] users[{uid}]: {old} -> {new} (display='{display}')")
            else:
                c.execute(
                    text("UPDATE users SET username=:n, display_name=:d WHERE id=:id"),
                    {"n": new, "d": display, "id": uid},
                )
                print(f"  [renamed] users[{uid}]: {old} -> {new} (display='{display}')")

        # --- 2. 数据迁移 ---
        # 先确认 target 用户存在
        tgt = c.execute(
            text("SELECT id, username FROM users WHERE id = :id"),
            {"id": TARGET_USER_ID},
        ).fetchone()
        if not tgt:
            print(f"  [ERROR] target user_id={TARGET_USER_ID} 不存在，跳过数据迁移")
            return
        print(f"  [target] user_id={tgt[0]} username={tgt[1]}")

        for table in TABLES_TO_MIGRATE:
            # 检查表是否存在
            tbl_exists = c.execute(
                text("SELECT 1 FROM pg_tables WHERE tablename = :t"),
                {"t": table},
            ).fetchone()
            if not tbl_exists:
                print(f"  [skip] 表 {table} 不存在，跳过")
                continue

            # 检查 user_id 列是否存在
            col_exists = c.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'user_id'"
                ),
                {"t": table},
            ).fetchone()
            if not col_exists:
                print(f"  [skip] 表 {table} 无 user_id 列，跳过")
                continue

            # 统计当前 user_id=1 的行
            n = c.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE user_id = :uid"),
                {"uid": SOURCE_USER_ID},
            ).scalar()
            if n == 0:
                print(f"  [skip] {table}: user_id={SOURCE_USER_ID} 无数据")
                continue

            if dry_run:
                print(f"  [dry-run migrate] {table}: {n} rows user_id {SOURCE_USER_ID} -> {TARGET_USER_ID}")
            else:
                c.execute(
                    text(f"UPDATE {table} SET user_id = :t WHERE user_id = :s"),
                    {"t": TARGET_USER_ID, "s": SOURCE_USER_ID},
                )
                print(f"  [migrated] {table}: {n} rows → user_id={TARGET_USER_ID}")

    print("Done.")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)