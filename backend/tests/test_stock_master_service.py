"""stock_master_service 测试。"""
import pytest
from datetime import datetime
from sqlalchemy import text

from database import Base
from models_master import StockMaster


@pytest.fixture(autouse=True)
def _ensure_stock_master_table(in_memory_db):
    """in_memory_db 是裸 SQLite，需要手动建 stock_master 表。"""
    StockMaster.__table__.create(bind=in_memory_db.get_bind(), checkfirst=True)
    yield


def _make_stock(code="600519.SH", name="贵州茅台"):
    return dict(
        stock_code=code, stock_name=name,
        exchange="SH", currency="CNY",
        asset_type="a_share_equity",
        is_listed=True, is_drillable=False,
    )


def test_create_and_list(in_memory_db):
    from services.stock_master_service import create_stock, list_stocks, get_stock
    create_stock(in_memory_db, _make_stock())
    items = list_stocks(in_memory_db)["items"]
    assert len(items) == 1
    assert items[0]["stock_code"] == "600519.SH"
    assert get_stock(in_memory_db, "600519.SH")["stock_name"] == "贵州茅台"


def test_update_partial(in_memory_db):
    from services.stock_master_service import create_stock, update_stock, get_stock
    create_stock(in_memory_db, _make_stock())
    update_stock(in_memory_db, "600519.SH", {"note": "蓝筹龙头"})
    assert get_stock(in_memory_db, "600519.SH")["note"] == "蓝筹龙头"


def test_delete_blocks_when_holding_exists(in_memory_db):
    """有持仓的股票不能删除 (沿用 security_master_service 行为)。"""
    from services.stock_master_service import create_stock, delete_stock
    create_stock(in_memory_db, _make_stock())
    in_memory_db.execute(text("""
        CREATE TABLE holdings (id INTEGER PRIMARY KEY, security_code VARCHAR(20))
    """))
    in_memory_db.execute(text(
        "INSERT INTO holdings (security_code) VALUES ('600519.SH')"
    ))
    in_memory_db.commit()
    with pytest.raises(ValueError, match="持仓"):
        delete_stock(in_memory_db, "600519.SH")