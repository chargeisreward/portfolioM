"""公共层 service 单元测试 — 只读 fund_drill_snapshot，无 user_id。"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from services.drill_public_service import get_public_cards, get_public_detail


class TestGetPublicCards:
    """测试 get_public_cards — 返回所有公共下钻卡片。"""

    def test_returns_cards_grouped_by_index(self):
        """正常情况：按 index_code 分组返回卡片。"""
        db = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.fund_code = "510300.SH"
        mock_snapshot.as_of_date = date(2026, 6, 24)
        mock_snapshot.stock_code = "600519.SH"
        mock_snapshot.stock_name = "贵州茅台"
        mock_snapshot.shares_equivalent = 0.001
        mock_snapshot.weight_pct = 5.23
        mock_snapshot.baseline_price = 1500.0
        mock_snapshot.current_price = 1600.0

        mock_fund_map = MagicMock()
        mock_fund_map.fund_code = "510300.SH"
        mock_fund_map.index_code = "000300.SH"
        mock_fund_map.index_name = "沪深300"

        # db.query() 第一次调用返回 snapshot，第二次返回 fund_map
        db.query.side_effect = [
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_snapshot])))),
            MagicMock(all=MagicMock(return_value=[mock_fund_map])),
        ]

        cards = get_public_cards(db, date(2026, 6, 24))
        assert len(cards) >= 1
        assert cards[0]["index_code"] == "000300"
        assert "510300.SH" in cards[0]["fund_codes"]
        assert cards[0]["stock_count"] >= 1

    def test_returns_empty_when_no_snapshot(self):
        """无 snapshot 时返回空列表。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        cards = get_public_cards(db, date(2026, 6, 24))
        assert cards == []


class TestGetPublicDetail:
    """测试 get_public_detail — 返回某指数的公共下钻明细。"""

    def test_returns_detail_with_constituents_and_funds(self):
        """正常情况：返回成分股 + 基金列表。"""
        db = MagicMock()
        mock_fund_map = MagicMock()
        mock_fund_map.fund_code = "510300.SH"
        mock_fund_map.index_code = "000300.SH"
        mock_fund_map.index_name = "沪深300"

        mock_snapshot = MagicMock()
        mock_snapshot.fund_code = "510300.SH"
        mock_snapshot.stock_code = "600519.SH"
        mock_snapshot.stock_name = "贵州茅台"
        mock_snapshot.shares_equivalent = 0.001
        mock_snapshot.weight_pct = 5.23
        mock_snapshot.baseline_price = 1500.0
        mock_snapshot.current_price = 1600.0

        # db.query() 第一次返回 fund_map，第二次返回 snapshot
        # service 第二次 query 用单次 .filter(as_of, fund_code.in_) 调用（非链式 .filter().filter()）
        db.query.side_effect = [
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_fund_map])))),
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_snapshot])))),
        ]

        detail = get_public_detail(db, date(2026, 6, 24), "000300")
        assert detail is not None
        assert detail["index_code"] == "000300"
        assert len(detail["constituents"]) >= 1
        assert len(detail["funds"]) >= 1

    def test_returns_none_when_index_not_found(self):
        """index_code 不存在时返回 None。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        detail = get_public_detail(db, date(2026, 6, 24), "999999")
        assert detail is None
