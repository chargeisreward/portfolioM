"""用户层 service 单元测试 — 只读 Holding，无下钻结构。"""
import os
import tempfile

import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
from database import Base
from services.drill_user_service import get_user_fund_codes, get_user_fund_holdings


@pytest.fixture
def fresh_db():
    """每个测试用独立的临时文件 SQLite（避免 :memory: 多连接隔离问题）。

    参考 backend/tests/test_security_master_service.py 中的 fresh_db fixture 实现。
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


class TestGetUserFundCodes:
    """测试 get_user_fund_codes — 返回用户可下钻基金代码集合。"""

    def test_returns_fund_codes_for_drillable_asset_types(self, fresh_db):
        """正常情况：返回可下钻 asset_type 的基金代码。

        SecurityMaster 表为空时 fallback 到旧硬编码逻辑。
        """
        from models import Holding
        fresh_db.add(Holding(
            user_id=2, security_code="510300.SH", security_name="沪深300ETF",
            quantity=10000.0, asset_type="a_share_etf",
        ))
        fresh_db.add(Holding(
            user_id=2, security_code="600519.SH", security_name="贵州茅台",
            quantity=100.0, asset_type="a_share_equity",
        ))
        fresh_db.add(Holding(
            user_id=2, security_code="BOND001", security_name="债券基金",
            quantity=1000.0, asset_type="bond",
        ))
        fresh_db.commit()

        codes = get_user_fund_codes(fresh_db, user_id=2)
        assert "510300.SH" in codes
        assert "600519.SH" in codes
        assert "BOND001" not in codes

    def test_returns_empty_set_when_no_holdings(self, fresh_db):
        """无持仓时返回空集合。"""
        codes = get_user_fund_codes(fresh_db, user_id=999)
        assert codes == set()


def test_get_user_fund_codes_uses_security_master(fresh_db):
    """get_user_fund_codes 应 join SecurityMaster.is_drillable 过滤。"""
    from models import SecurityMaster, Holding
    # 基金 A: asset_type 可下钻 + is_drillable=True → 应包含
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf", is_drillable=True,
    ))
    # 基金 B: asset_type 可下钻 + is_drillable=False → 新逻辑应排除
    fresh_db.add(SecurityMaster(
        security_code="159901.SZ", security_name="深100ETF",
        security_type="fund", asset_type="a_share_etf", is_drillable=False,
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="159901.SZ", security_name="深100ETF",
        quantity=500, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    from services.drill_user_service import get_user_fund_codes
    codes = get_user_fund_codes(fresh_db, 1)
    assert "510300.SH" in codes
    assert "159901.SZ" not in codes  # is_drillable=False，即使 asset_type 可下钻


def test_get_user_fund_codes_falls_back_to_fund_index_map(fresh_db):
    """SecurityMaster 有数据但 is_drillable 全 False 时，应回退到 FundIndexMap 查找可下钻基金。

    场景：admin 未设置任何 is_drillable=True，但 FundIndexMap 中有映射的基金应被视为可下钻。
    """
    from models import SecurityMaster, Holding, FundIndexMap
    # SecurityMaster 有数据，但 is_drillable 全 False
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf", is_drillable=False,
    ))
    fresh_db.add(SecurityMaster(
        security_code="159901.SZ", security_name="深100ETF",
        security_type="fund", asset_type="a_share_etf", is_drillable=False,
    ))
    # FundIndexMap 中只有 510300.SH 有映射
    from datetime import date as _d
    fresh_db.add(FundIndexMap(
        fund_code="510300.SH", as_of_date=_d(2026, 6, 24),
        index_code="000300.SH", index_name="沪深300",
    ))
    # 用户持仓
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="159901.SZ", security_name="深100ETF",
        quantity=500, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    from services.drill_user_service import get_user_fund_codes
    codes = get_user_fund_codes(fresh_db, 1)
    # 510300.SH 在 FundIndexMap 中 → 应返回
    assert "510300.SH" in codes
    # 159901.SZ 不在 FundIndexMap 中 → 不应返回
    assert "159901.SZ" not in codes


class TestGetUserFundHoldings:
    """测试 get_user_fund_holdings — 返回用户在指定基金上的持仓。"""

    def test_returns_holdings_for_specified_funds(self):
        """正常情况：返回指定基金的持仓明细。"""
        db = MagicMock()
        mock_holding = MagicMock()
        mock_holding.security_code = "510300.SH"
        mock_holding.quantity = 10000.0
        mock_holding.amount_cny = 45000.0
        mock_holding.price = 4.5

        db.query.return_value.filter.return_value.filter.return_value.all.return_value = [mock_holding]

        holdings = get_user_fund_holdings(db, user_id=2, fund_codes=["510300.SH"])
        assert "510300.SH" in holdings
        assert holdings["510300.SH"]["quantity"] == 10000.0
        assert holdings["510300.SH"]["amount_cny"] == 45000.0

    def test_returns_empty_dict_when_no_holdings(self):
        """无持仓时返回空字典。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        holdings = get_user_fund_holdings(db, user_id=999, fund_codes=["510300.SH"])
        assert holdings == {}
