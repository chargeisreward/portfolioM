"""orchestration service 单元测试 — join 逻辑。"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from services.drill_orchestration_service import (
    list_drillable_cards,
    get_drill_detail,
)


class TestListDrillableCards:
    """测试 list_drillable_cards — join public + user。"""

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_returns_filtered_cards_with_est_value(self, mock_user, mock_public):
        """正常情况：过滤公共卡片，只保留用户持有的基金。"""
        db = MagicMock()
        mock_public.get_public_cards.return_value = [
            {
                "index_code": "000300",
                "index_name": "沪深300",
                "as_of": "2026-06-24",
                "fund_codes": ["510300.SH", "159919.SZ"],
                "stock_count": 300,
                "total_weight": 1.0,
            },
            {
                "index_code": "000905",
                "index_name": "中证500",
                "as_of": "2026-06-24",
                "fund_codes": ["510500.SH"],
                "stock_count": 500,
                "total_weight": 1.0,
            },
        ]
        mock_user.get_user_fund_codes.return_value = {"510300.SH"}
        mock_user.get_user_fund_holdings.return_value = {
            "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
        }

        cards = list_drillable_cards(db, date(2026, 6, 24), user_id=2)
        assert len(cards) == 1
        assert cards[0]["index_code"] == "000300"
        assert cards[0]["est_market_value_cny"] == 45000.0
        assert "510300.SH" in cards[0]["user_fund_codes"]

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_returns_empty_when_no_holdings(self, mock_user, mock_public):
        """用户无持仓时返回空列表。"""
        db = MagicMock()
        mock_public.get_public_cards.return_value = [
            {"index_code": "000300", "fund_codes": ["510300.SH"], "stock_count": 300},
        ]
        mock_user.get_user_fund_codes.return_value = set()

        cards = list_drillable_cards(db, date(2026, 6, 24), user_id=999)
        assert cards == []

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_returns_empty_when_no_snapshot(self, mock_user, mock_public):
        """无 snapshot 时返回空列表。"""
        db = MagicMock()
        mock_public.get_public_cards.return_value = []
        mock_user.get_user_fund_codes.return_value = {"510300.SH"}

        cards = list_drillable_cards(db, date(2026, 6, 24), user_id=2)
        assert cards == []


class TestGetDrillDetail:
    """测试 get_drill_detail — join public + user，计算 user_drill_shares。"""

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_returns_detail_with_user_drill_shares(self, mock_user, mock_public):
        """正常情况：返回含 user_drill_shares 的明细。"""
        db = MagicMock()
        mock_public.get_public_detail.return_value = {
            "index_code": "000300",
            "index_name": "沪深300",
            "as_of": "2026-06-24",
            "constituents": [
                {"stock_code": "600519.SH", "stock_name": "贵州茅台",
                 "weight_pct": 5.23, "baseline_price": 1500.0,
                 "current_price": 1600.0, "shares_equivalent": 0.001},
            ],
            "funds": [
                {"fund_code": "510300.SH", "fund_name": "沪深300ETF",
                 "shares_equivalent": 0.001},
            ],
        }
        mock_user.get_user_fund_holdings.return_value = {
            "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
        }

        detail = get_drill_detail(db, date(2026, 6, 24), "000300", user_id=2)
        assert detail is not None
        assert detail["funds"][0]["user_drill_shares"] == 10.0  # 10000 * 0.001
        assert "user_hold_shares" in detail["constituents"][0]

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_returns_none_when_no_public_detail(self, mock_user, mock_public):
        """公共层无数据时返回 None。"""
        db = MagicMock()
        mock_public.get_public_detail.return_value = None

        detail = get_drill_detail(db, date(2026, 6, 24), "999999", user_id=2)
        assert detail is None

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_returns_none_when_no_holdings(self, mock_user, mock_public):
        """用户无持仓时返回 None。"""
        db = MagicMock()
        mock_public.get_public_detail.return_value = {
            "index_code": "000300",
            "constituents": [{"stock_code": "600519.SH"}],
            "funds": [{"fund_code": "510300.SH"}],
        }
        mock_user.get_user_fund_holdings.return_value = {}

        detail = get_drill_detail(db, date(2026, 6, 24), "000300", user_id=999)
        assert detail is None
