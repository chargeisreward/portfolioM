"""幂等建表：valuation_daily_snapshot

估值表日截面表（按 user_id 隔离）— 持仓+股价+市值+关键指标+锁定状态。

用法：
    cd backend && python scripts/migrate_valuation_snapshot.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, inspect

from database import Base
from models import ValuationDailySnapshot
from config import DATABASE_URL


TABLE = ValuationDailySnapshot.__tablename__


def run():
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)

    if TABLE in inspector.get_table_names():
        print(f"  [skip] {TABLE} already exists")
        return

    ValuationDailySnapshot.__table__.create(engine, checkfirst=True)
    print(f"  [ok] {TABLE} created")

    # 校验列齐全
    cols = {c["name"] for c in inspector.get_columns(TABLE)}
    expected = {
        "id", "user_id", "as_of_date", "security_code", "security_name",
        "quantity", "price", "price_cny", "currency", "fx_rate", "amount_cny",
        "asset_type", "type2", "is_cash", "holding_uid",
        "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield", "market_cap",
        "is_locked", "locked_at", "created_at", "updated_at",
    }
    missing = expected - cols
    if missing:
        raise RuntimeError(f"{TABLE} missing columns: {missing}")
    print(f"  [verify] {len(cols)} columns OK")


if __name__ == "__main__":
    run()
