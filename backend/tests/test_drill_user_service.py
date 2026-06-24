"""用户层 service 单元测试 — 只读 Holding，无下钻结构。"""
import pytest
from unittest.mock import MagicMock
from services.drill_user_service import get_user_fund_codes, get_user_fund_holdings


class TestGetUserFundCodes:
    """测试 get_user_fund_codes — 返回用户可下钻基金代码集合。"""

    def test_returns_fund_codes_for_drillable_asset_types(self):
        """正常情况：返回可下钻 asset_type 的基金代码。"""
        db = MagicMock()
        mock_holding_1 = MagicMock()
        mock_holding_1.security_code = "510300.SH"
        mock_holding_1.asset_type = "a_share_etf"
        mock_holding_1.quantity = 10000.0

        mock_holding_2 = MagicMock()
        mock_holding_2.security_code = "600519.SH"
        mock_holding_2.asset_type = "a_share_equity"
        mock_holding_2.quantity = 100.0

        mock_holding_3 = MagicMock()
        mock_holding_3.security_code = "BOND001"
        mock_holding_3.asset_type = "bond"
        mock_holding_3.quantity = 1000.0

        db.query.return_value.filter.return_value.all.return_value = [
            mock_holding_1, mock_holding_2, mock_holding_3
        ]

        codes = get_user_fund_codes(db, user_id=2)
        assert "510300.SH" in codes
        assert "600519.SH" in codes
        assert "BOND001" not in codes

    def test_returns_empty_set_when_no_holdings(self):
        """无持仓时返回空集合。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        codes = get_user_fund_codes(db, user_id=999)
        assert codes == set()


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
