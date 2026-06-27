"""把 full_holding_snapshot 的 UK 改为含 user_id（多用户隔离修复）。

背景：原 UK 是 (as_of_date, stock_code, source_holding_code)，不含 user_id。
当多个用户持有相同基金时，穿透结果落到同一只成分股，UK 会冲突。
新 UK = (as_of_date, user_id, stock_code, source_holding_code)，与
PenetrationSnapshot 的 ux_pnsnap 设计一致。

幂等：检测当前 UK 列定义，已是新形态则跳过。

用法：
    cd backend && python scripts/migrate_fhsnap_uk.py
    cd backend && python scripts/migrate_fhsnap_uk.py --dry-run
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from config import DATABASE_URL


# 旧/新 UK 列定义（顺序敏感）
OLD_UK_COLS = ("as_of_date", "stock_code", "source_holding_code")
NEW_UK_COLS = ("as_of_date", "user_id", "stock_code", "source_holding_code")
UK_NAME = "ux_fhsnap"
TABLE = "full_holding_snapshot"


def _current_uk_cols(c) -> tuple[str, ...] | None:
    """返回当前 ux_fhsnap 的列定义（按 ordinal_position 排序）。无则返回 None。"""
    rows = c.execute(
        text(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_name = :uk
              AND tc.table_name = :t
            ORDER BY kcu.ordinal_position
            """
        ),
        {"uk": UK_NAME, "t": TABLE},
    ).fetchall()
    return tuple(r[0] for r in rows) if rows else None


def run(dry_run: bool = False):
    engine = create_engine(DATABASE_URL)
    with engine.begin() as c:
        # 表存在性
        exists = c.execute(
            text("SELECT 1 FROM pg_tables WHERE tablename = :t"),
            {"t": TABLE},
        ).fetchone()
        if not exists:
            print(f"  [skip] {TABLE} not exists")
            return

        current = _current_uk_cols(c)
        if current == NEW_UK_COLS:
            print(f"  [skip] {UK_NAME} already on {NEW_UK_COLS}")
            return

        if current == OLD_UK_COLS:
            if dry_run:
                print(f"  [dry-run] would change {UK_NAME}: {OLD_UK_COLS} -> {NEW_UK_COLS}")
                return
            # DROP 旧 UK + ADD 新 UK
            c.execute(text(f"ALTER TABLE {TABLE} DROP CONSTRAINT {UK_NAME}"))
            c.execute(
                text(
                    f"ALTER TABLE {TABLE} "
                    f"ADD CONSTRAINT {UK_NAME} UNIQUE ({', '.join(NEW_UK_COLS)})"
                )
            )
            print(f"  [migrated] {UK_NAME}: {OLD_UK_COLS} -> {NEW_UK_COLS}")
            return

        # 意外形态：报错 fail loud
        raise RuntimeError(
            f"Unexpected {UK_NAME} columns: {current}. "
            f"Expected either {OLD_UK_COLS} or {NEW_UK_COLS}."
        )
    print("Done.")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
