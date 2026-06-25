"""公共层 service 单元测试 — 只读 fund_drill_snapshot，无 user_id。"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from services.drill_public_service import get_public_cards, get_public_detail


class TestGetPublicCards:
    """测试 get_public_cards — 返回所有公共下钻卡片。"""

    @patch("services.drill_public_service._resolve_snapshot_date", return_value=date(2026, 6, 24))
    def test_returns_cards_grouped_by_index(self, mock_resolve):
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
        # 双币种 (2026-06-25)：本币字段（A 股 fx=1，本币=原币）
        mock_snapshot.baseline_price_cny = 1500.0
        mock_snapshot.current_price_cny = 1600.0
        # 2026-06-25 估值字段补全
        mock_snapshot.pe_ttm = 30.0
        mock_snapshot.pb_mrq = 10.0
        mock_snapshot.ps_ttm = 15.0
        mock_snapshot.dividend_yield = 1.5
        # 动态字段显式设为 None → 走 fallback 实时计算路径（pe_dyn = pe_ttm × price_ratio）
        # 动态值路径由 test_drill_valuation_e2e.py 覆盖
        mock_snapshot.pe_ttm_dynamic = None
        mock_snapshot.pb_mrq_dynamic = None
        mock_snapshot.ps_ttm_dynamic = None

        mock_fund_map = MagicMock()
        mock_fund_map.fund_code = "510300.SH"
        mock_fund_map.index_code = "000300.SH"
        mock_fund_map.index_name = "沪深300"

        # _resolve_snapshot_date 已 patch，不再消耗 query；
        # 实际 query 顺序：1) FundDrillSnapshot 行 2) FundIndexMap 行
        db.query.side_effect = [
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_snapshot])))),
            MagicMock(all=MagicMock(return_value=[mock_fund_map])),
        ]

        cards = get_public_cards(db, date(2026, 6, 24))
        assert len(cards) >= 1
        assert cards[0]["index_code"] == "000300"
        assert "510300.SH" in cards[0]["fund_codes"]
        assert cards[0]["stock_count"] >= 1
        # 2026-06-25: 断言估值字段
        assert "static_amount_cny" in cards[0]
        assert "weighted_pe" in cards[0]
        assert "weighted_pb" in cards[0]
        assert "weighted_ps" in cards[0]
        assert "weighted_dividend_yield" in cards[0]
        # 手算（fallback 实时计算路径，因 pe_ttm_dynamic=None）：
        # weight_basis = 0.001 × baseline_price_cny(1500) = 1.5
        # price_ratio = current_price_cny(1600) / baseline_price_cny(1500) = 1.0667
        # pe_dyn = 30 × 1.0667 = 32.0
        # virt_pe = 1.5 / 32.0 = 0.046875
        # weighted_pe = 1.5 / 0.046875 = 32.0
        assert cards[0]["static_amount_cny"] == round(1.5, 4)
        assert cards[0]["weighted_pe"] == round(32.0, 4)

    def test_returns_empty_when_no_snapshot(self):
        """无 snapshot 时返回空列表。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        cards = get_public_cards(db, date(2026, 6, 24))
        assert cards == []


class TestGetPublicDetail:
    """测试 get_public_detail — 返回某指数的公共下钻明细。"""

    @patch("services.drill_public_service._resolve_snapshot_date", return_value=date(2026, 6, 24))
    def test_returns_detail_with_constituents_and_funds(self, mock_resolve):
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
        # 双币种 (2026-06-25)：本币字段 + 币种信息（get_public_detail 透传给前端）
        mock_snapshot.baseline_price_cny = 1500.0
        mock_snapshot.current_price_cny = 1600.0
        mock_snapshot.currency = "CNY"
        mock_snapshot.fx_rate = 1.0
        # 2026-06-25 估值字段
        mock_snapshot.pe_ttm = 30.0
        mock_snapshot.pb_mrq = 10.0
        mock_snapshot.ps_ttm = 15.0
        mock_snapshot.dividend_yield = 1.5
        # 动态字段显式设为 None（避免 MagicMock 真值干扰，动态值路径由 e2e 测试覆盖）
        mock_snapshot.pe_ttm_dynamic = None
        mock_snapshot.pb_mrq_dynamic = None
        mock_snapshot.ps_ttm_dynamic = None

        # _resolve_snapshot_date 已 patch，不再消耗 query；
        # 实际 query 顺序：1) FundIndexMap 2) FundDrillSnapshot（单次 .filter(as_of, fund_code.in_)）
        db.query.side_effect = [
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_fund_map])))),
            MagicMock(filter=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_snapshot])))),
        ]

        detail = get_public_detail(db, date(2026, 6, 24), "000300")
        assert detail is not None
        assert detail["index_code"] == "000300"
        assert len(detail["constituents"]) >= 1
        assert len(detail["funds"]) >= 1
        # 2026-06-25: 成分股应含估值字段 + weight_at_baseline_pct
        const = detail["constituents"][0]
        assert "pe_ttm" in const
        assert "pb_mrq" in const
        assert "ps_ttm" in const
        assert "dividend_yield" in const
        assert "weight_at_baseline_pct" in const
        assert const["pe_ttm"] == 30.0
        # 动态字段应透传 None
        assert const["pe_ttm_dynamic"] is None

    def test_returns_none_when_index_not_found(self):
        """index_code 不存在时返回 None。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        detail = get_public_detail(db, date(2026, 6, 24), "999999")
        assert detail is None
