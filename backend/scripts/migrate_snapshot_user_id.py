"""给个人衍生估值快照表加 user_id 列 + 迁移现有数据到 advisor

按用户规则（2026-06-24）：
  - penetration_results / full_holding_snapshot / penetration_snapshot
  - a_share_financial_snapshot / hk_share_financial_snapshot
  这些都是按天落库的 user-personal 估值快照，按 user_id 隔离。
  现有 N 行都属于原单用户（advisor 接管），全部 → user_id = advisor.id

用法：
    cd backend && python scripts/migrate_snapshot_user_id.py
    cd backend && python scripts/migrate_snapshot_user_id.py --dry-run
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from config import DATABASE_URL


# advisor 接管了原单用户数据 → user_id = 2
DEFAULT_USER_ID = 2

TABLES = [
    "penetration_results",
    "full_holding_snapshot",
    "penetration_snapshot",
    "a_share_financial_snapshot",
    "hk_share_financial_snapshot",
    "overseas_share_financial_snapshot",
    "csi300_constituent_snapshot",
    "aggregation_cache",
    "aggregation_timeseries",
]


def run(dry_run: bool = False):
    engine = create_engine(DATABASE_URL)
    with engine.begin() as c:
        for t in TABLES:
            exists = c.execute(
                text("SELECT 1 FROM pg_tables WHERE tablename = :t"),
                {"t": t},
            ).fetchone()
            if not exists:
                print(f"  [skip] {t} not exists")
                continue

            has_col = c.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'user_id'"
                ),
                {"t": t},
            ).fetchone()
            if has_col:
                print(f"  [skip] {t} already has user_id")
                continue

            n = c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            if dry_run:
                print(f"  [dry-run] {t}: add user_id column (default {DEFAULT_USER_ID}, n={n} rows)")
            else:
                # 1. 加列（默认 DEFAULT_USER_ID）
                c.execute(
                    text(f"ALTER TABLE {t} ADD COLUMN user_id BIGINT DEFAULT {DEFAULT_USER_ID}")
                )
                # 2. 把所有 NULL 改成 advisor.id
                c.execute(
                    text(f"UPDATE {t} SET user_id = :u WHERE user_id IS NULL"),
                    {"u": DEFAULT_USER_ID},
                )
                # 3. NOT NULL + index
                c.execute(text(f"ALTER TABLE {t} ALTER COLUMN user_id SET NOT NULL"))
                c.execute(text(f"CREATE INDEX ix_{t}_user_id ON {t} (user_id)"))
                print(f"  [migrated] {t}: user_id added (n={n} rows → user_id={DEFAULT_USER_ID})")
    print("Done.")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)