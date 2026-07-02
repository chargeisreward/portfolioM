"""fund_master_service 测试。"""
import pytest


def _ensure_fund_table(db):
    from models_master import FundMaster
    FundMaster.__table__.create(bind=db.get_bind(), checkfirst=True)


def _make_fund(code="510300.SH", name="华泰柏瑞沪深300ETF"):
    return dict(
        fund_code=code, fund_name=name,
        fund_type="etf", currency="CNY",
        asset_type="a_share_etf",
        is_drillable=True,
    )


def test_create_and_list(in_memory_db):
    _ensure_fund_table(in_memory_db)
    from services.fund_master_service import create_fund, list_funds, get_fund
    create_fund(in_memory_db, _make_fund())
    items = list_funds(in_memory_db)["items"]
    assert len(items) == 1
    assert items[0]["fund_code"] == "510300.SH"
    assert get_fund(in_memory_db, "510300.SH")["fund_name"] == "华泰柏瑞沪深300ETF"


def test_update_partial(in_memory_db):
    _ensure_fund_table(in_memory_db)
    from services.fund_master_service import create_fund, update_fund, get_fund
    create_fund(in_memory_db, _make_fund())
    update_fund(in_memory_db, "510300.SH", {"note": "蓝筹 ETF"})
    assert get_fund(in_memory_db, "510300.SH")["note"] == "蓝筹 ETF"


def test_delete_blocks_when_holding_exists(in_memory_db):
    _ensure_fund_table(in_memory_db)
    from services.fund_master_service import create_fund, delete_fund
    from sqlalchemy import text
    create_fund(in_memory_db, _make_fund())
    in_memory_db.execute(text("""
        CREATE TABLE IF NOT EXISTS holdings (id INTEGER PRIMARY KEY, security_code VARCHAR(20))
    """))
    in_memory_db.execute(text("INSERT INTO holdings (security_code) VALUES ('510300.SH')"))
    in_memory_db.commit()
    with pytest.raises(ValueError, match="持仓"):
        delete_fund(in_memory_db, "510300.SH")


def test_filter_by_asset_type_and_search(in_memory_db):
    _ensure_fund_table(in_memory_db)
    from services.fund_master_service import create_fund, list_funds
    create_fund(in_memory_db, dict(fund_code="510300.SH", fund_name="沪深300ETF",
                                    fund_type="etf", asset_type="a_share_etf"))
    create_fund(in_memory_db, dict(fund_code="161725.OF", fund_name="招商白酒",
                                    fund_type="otc", asset_type="a_share_equity"))
    res = list_funds(in_memory_db, asset_type="a_share_etf")
    assert res["total"] == 1
    assert res["items"][0]["fund_code"] == "510300.SH"
