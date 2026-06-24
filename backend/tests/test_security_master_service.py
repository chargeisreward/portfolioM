"""security_master_service 单元测试。"""
import os
import tempfile

import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
from database import Base
from models import SecurityMaster, Holding, FundIndexMap, FundDrillSnapshot
from services.security_master_service import (
    list_securities,
    get_security,
    create_security,
    update_security,
    delete_security,
    sync_from_holdings,
    sync_from_drill,
    init_from_existing,
)


@pytest.fixture
def fresh_db():
    """每个测试用独立的临时文件 SQLite（避免 :memory: 多连接隔离问题）。

    参考 backend/tests/test_models_admin.py 中的 fresh_db fixture 实现，
    但本测试只测 service 层，不需要 monkeypatch main.get_db。
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_list_securities_with_filters(fresh_db):
    """list_securities 支持按 type/market/dillable 过滤。"""
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf", market="CN",
        fund_type="etf", is_drillable=True,
    ))
    fresh_db.add(SecurityMaster(
        security_code="600519.SH", security_name="贵州茅台",
        security_type="stock", asset_type="a_share_equity", market="CN",
        is_drillable=False,
    ))
    fresh_db.commit()

    all_rows = list_securities(fresh_db)
    assert len(all_rows["items"]) == 2

    funds_only = list_securities(fresh_db, sec_type="fund")
    assert len(funds_only["items"]) == 1
    assert funds_only["items"][0]["security_code"] == "510300.SH"

    drillable_only = list_securities(fresh_db, drillable=True)
    assert len(drillable_only["items"]) == 1
    assert drillable_only["items"][0]["security_code"] == "510300.SH"


def test_create_security(fresh_db):
    """create_security 能创建新记录。"""
    result = create_security(fresh_db, {
        "security_code": "510300.SH",
        "security_name": "沪深300ETF",
        "security_type": "fund",
        "asset_type": "a_share_etf",
        "market": "CN",
        "fund_type": "etf",
        "is_drillable": True,
    })
    assert result["security_code"] == "510300.SH"
    assert result["is_drillable"] is True


def test_update_security(fresh_db):
    """update_security 能修改字段。"""
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf", market="CN",
        is_drillable=False,
    ))
    fresh_db.commit()

    result = update_security(fresh_db, "510300.SH", {"is_drillable": True})
    assert result["is_drillable"] is True


def test_delete_security_blocked_when_holding_exists(fresh_db):
    """有持仓时禁止删除。"""
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf",
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    with pytest.raises(ValueError, match="持仓"):
        delete_security(fresh_db, "510300.SH")


def test_sync_from_holdings(fresh_db):
    """sync_from_holdings 为缺失的证券创建记录。"""
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    count = sync_from_holdings(fresh_db)
    assert count == 1
    sm = fresh_db.query(SecurityMaster).filter_by(security_code="510300.SH").first()
    assert sm is not None
    assert sm.security_name == "沪深300ETF"
    assert sm.asset_type == "a_share_etf"


def test_sync_from_drill(fresh_db):
    """sync_from_drill 为下钻股票创建记录。"""
    fresh_db.add(FundDrillSnapshot(
        fund_code="510300.SH", as_of_date=date(2026, 6, 24),
        stock_code="600519.SH", stock_name="贵州茅台",
        weight_pct=5.0, baseline_price=1500.0, current_price=1600.0,
        shares_equivalent=0.001,
    ))
    fresh_db.commit()

    count = sync_from_drill(fresh_db)
    assert count == 1
    sm = fresh_db.query(SecurityMaster).filter_by(security_code="600519.SH").first()
    assert sm is not None
    assert sm.security_name == "贵州茅台"
    assert sm.security_type == "stock"


def test_init_from_existing(fresh_db):
    """init_from_existing 从 FundIndexMap + Holding 批量初始化。"""
    fresh_db.add(FundIndexMap(
        fund_code="510300.SH", fund_name="沪深300ETF",
        index_code="000300.SH", index_name="沪深300",
        as_of_date=date(2026, 6, 24), source="test",
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    count = init_from_existing(fresh_db)
    assert count >= 1
    sm = fresh_db.query(SecurityMaster).filter_by(security_code="510300.SH").first()
    assert sm is not None
    assert sm.index_code == "000300"
    assert sm.is_drillable is True  # a_share_etf 默认可下钻
