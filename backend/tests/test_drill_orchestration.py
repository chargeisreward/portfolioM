"""orchestration service 单元测试 — join 逻辑。"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from services.drill_orchestration_service import (
    list_drillable_cards,
    get_drill_detail,
)


def _mock_per_fund_row(fund_code, static_sum, est_sum):
    """构造 FundDrillSnapshot 聚合查询返回的 mock 行。"""
    r = MagicMock()
    r.fund_code = fund_code
    r.static_sum = static_sum
    r.est_sum = est_sum
    return r


class TestListDrillableCards:
    """测试 list_drillable_cards — join public + user。"""

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_returns_filtered_cards_with_est_value(self, mock_user, mock_public):
        """正常情况：过滤公共卡片，只保留用户持有的基金。

        新公式（2026-06-25）：
          card_est = user_quantity × per_fund_est[f]
          est_deviation_pct = ((card_est + card_cash) / card_fund_value - 1) × 100
          weight_pct = card_est / user_total_est × 100
        """
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
        # mock db.query 返回 per_fund_static / per_fund_est 聚合行
        # 假设 per_fund_static["510300.SH"] = 1.9, per_fund_est["510300.SH"] = 2.02
        # 实际查询链：db.query(col1,col2,col3).filter(...).group_by(...).all()
        # （单次 .filter(as_of, fund_code.in_) 调用，非链式 .filter().filter()）
        mock_query = MagicMock()
        mock_filtered = MagicMock()
        mock_grouped = MagicMock()
        mock_grouped.all.return_value = [_mock_per_fund_row("510300.SH", 1.9, 2.02)]
        mock_query.filter.return_value = mock_filtered
        mock_filtered.group_by.return_value = mock_grouped
        # query 链：db.query(...).filter(...).group_by(...).all()
        db.query.side_effect = [mock_query]

        cards = list_drillable_cards(db, date(2026, 6, 24), user_id=2)
        assert len(cards) == 1
        assert cards[0]["index_code"] == "000300"
        assert "510300.SH" in cards[0]["user_fund_codes"]
        # 手算：
        # card_est = 10000 × 2.02 = 20200.0（mock 未含 CASH 行，真实场景 per_fund_est 会含 CASH）
        # card_fund_value = 45000.0
        # est_deviation_pct = (20200 / 45000 - 1) × 100 = -55.1111
        # static_amount_cny = 10000 × 1.9 = 19000.0
        assert cards[0]["est_market_value_cny"] == 20200.0
        assert cards[0]["static_amount_cny"] == 19000.0
        assert cards[0]["est_deviation_pct"] == round((20200 / 45000 - 1) * 100, 4)
        assert cards[0]["weight_pct"] == 100.0

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

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_cards_sorted_by_weighted_pe_descending(self, mock_user, mock_public):
        """多卡片按 weighted_pe 从高到低排序，无 PE 数据的排末尾。"""
        db = MagicMock()
        # 3 张卡片：PE=15, PE=None, PE=30 → 期望顺序 30, 15, None
        mock_public.get_public_cards.return_value = [
            {"index_code": "000300", "index_name": "沪深300", "as_of": "2026-06-24",
             "fund_codes": ["510300.SH"], "stock_count": 300, "total_weight": 1.0,
             "weighted_pe": 15.0},
            {"index_code": "000905", "index_name": "中证500", "as_of": "2026-06-24",
             "fund_codes": ["510500.SH"], "stock_count": 500, "total_weight": 1.0,
             "weighted_pe": None},
            {"index_code": "399006", "index_name": "创业板指", "as_of": "2026-06-24",
             "fund_codes": ["159915.SZ"], "stock_count": 100, "total_weight": 1.0,
             "weighted_pe": 30.0},
        ]
        mock_user.get_user_fund_codes.return_value = {"510300.SH", "510500.SH", "159915.SZ"}
        mock_user.get_user_fund_holdings.return_value = {
            "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
            "510500.SH": {"quantity": 5000.0, "amount_cny": 20000.0, "price": 4.0},
            "159915.SZ": {"quantity": 8000.0, "amount_cny": 30000.0, "price": 3.75},
        }
        # mock db.query 返回 per_fund 聚合行（3 只基金）
        mock_query = MagicMock()
        mock_filtered = MagicMock()
        mock_grouped = MagicMock()
        mock_grouped.all.return_value = [
            _mock_per_fund_row("510300.SH", 1.9, 2.02),
            _mock_per_fund_row("510500.SH", 1.5, 1.6),
            _mock_per_fund_row("159915.SZ", 1.2, 1.3),
        ]
        mock_query.filter.return_value = mock_filtered
        mock_filtered.group_by.return_value = mock_grouped
        db.query.side_effect = [mock_query]

        cards = list_drillable_cards(db, date(2026, 6, 24), user_id=2)
        assert len(cards) == 3
        # 期望顺序：创业板指(PE=30) → 沪深300(PE=15) → 中证500(PE=None)
        assert cards[0]["index_code"] == "399006"
        assert cards[0]["weighted_pe"] == 30.0
        assert cards[1]["index_code"] == "000300"
        assert cards[1]["weighted_pe"] == 15.0
        assert cards[2]["index_code"] == "000905"
        assert cards[2]["weighted_pe"] is None


class TestGetDrillDetail:
    """测试 get_drill_detail — join public + user，计算 user_hold_shares。"""

    @patch("services.drill_orchestration_service.public_service")
    @patch("services.drill_orchestration_service.user_service")
    def test_returns_detail_with_user_drill_shares(self, mock_user, mock_public):
        """正常情况：返回含 user_hold_shares 的明细。

        新逻辑（2026-06-25 修正）：
          user_hold_shares = user_quantity × shares_equivalent（per-fund × per-stock 明细）
          shares_equivalent 字段替换为用户约当股数（不再是每份基金对应股数）
          CASH 行来自公共层 get_public_detail（FundDrillSnapshot 的 CASH 行），非编排层追加
        """
        db = MagicMock()
        # 公共层返回的 constituents 包含 CASH 行（来自 FundDrillSnapshot）
        # CASH shares_equivalent = fund_price × 0.05 = 4.5 × 0.05 = 0.225（每份基金含现金）
        mock_public.get_public_detail.return_value = {
            "index_code": "000300",
            "index_name": "沪深300",
            "as_of": "2026-06-24",
            "constituents": [
                {"stock_code": "600519.SH", "stock_name": "贵州茅台",
                 "weight_pct": 5.23, "baseline_price": 1500.0,
                 "current_price": 1600.0, "shares_equivalent": 0.001,
                 "pe_ttm": 30.0, "pb_mrq": 10.0, "ps_ttm": 15.0, "dividend_yield": 1.5,
                 "pe_ttm_dynamic": 32.0, "pb_mrq_dynamic": 10.67, "ps_ttm_dynamic": 16.0,
                 "is_cash": False},
                {"stock_code": "CASH", "stock_name": "下钻-现金",
                 "weight_pct": 5.0, "baseline_price": 1.0,
                 "current_price": 1.0, "shares_equivalent": 0.225,
                 "pe_ttm": None, "pb_mrq": None, "ps_ttm": None, "dividend_yield": None,
                 "pe_ttm_dynamic": None, "pb_mrq_dynamic": None, "ps_ttm_dynamic": None,
                 "is_cash": True},
            ],
            "funds": [
                {"fund_code": "510300.SH", "fund_name": "沪深300ETF",
                 "shares_equivalent": 0.001},
            ],
        }
        mock_user.get_user_fund_holdings.return_value = {
            "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
        }
        # mock db.query(FundDrillSnapshot) 返回 per-fund × per-stock 明细行（含 CASH 行）
        # 双币种 (2026-06-25)：下游 get_drill_detail 改用 current_price_cny（本币）算市值，
        # mock 必须设置 current_price_cny 属性，否则 MagicMock 真值会干扰数值计算。
        mock_drill_row = MagicMock()
        mock_drill_row.fund_code = "510300.SH"
        mock_drill_row.stock_code = "600519.SH"
        mock_drill_row.shares_equivalent = 0.001
        mock_drill_row.current_price = 1600.0       # 原币（A 股 fx=1）
        mock_drill_row.current_price_cny = 1600.0   # 本币 CNY（A 股本币=原币）
        mock_cash_row = MagicMock()
        mock_cash_row.fund_code = "510300.SH"
        mock_cash_row.stock_code = "CASH"
        mock_cash_row.shares_equivalent = 0.225  # fund_price(4.5) × 0.05
        mock_cash_row.current_price = 1.0           # 原币
        mock_cash_row.current_price_cny = 1.0       # 本币 CNY
        db.query.return_value.filter.return_value.all.return_value = [mock_drill_row, mock_cash_row]

        detail = get_drill_detail(db, date(2026, 6, 24), "000300", user_id=2)
        assert detail is not None
        # fund 级别 user_drill_shares = 10000 × 0.001 = 10.0
        assert detail["funds"][0]["user_drill_shares"] == 10.0
        # constituent 级别：shares_equivalent 替换为用户约当股数 = 10000 × 0.001 = 10.0
        const = detail["constituents"][0]
        assert const["shares_equivalent"] == 10.0  # 用户约当股数，不再是 0.001
        assert const["user_hold_shares"] == 10.0
        # 估算市值 = 约当股数 × 当前价 = 10.0 × 1600 = 16000.0
        assert const["user_hold_value"] == 16000.0
        assert const["est_market_value_cny"] == 16000.0
        # 动态字段透传
        assert const["pe_ttm_dynamic"] == 32.0

        # 下钻-现金行（来自公共层）：cash = 10000 × 0.225 × 1.0 = 2250.0
        cash_row = detail["constituents"][-1]
        assert cash_row["is_cash"] is True
        assert cash_row["stock_code"] == "CASH"
        assert cash_row["stock_name"] == "下钻-现金"
        # user_hold_shares = 10000 × 0.225 = 2250.0, user_hold_value = 2250.0 × 1.0 = 2250.0
        assert cash_row["est_market_value_cny"] == 2250.0
        # 合计 = 16000 + 2250 = 18250
        total = sum(c["est_market_value_cny"] for c in detail["constituents"])
        assert total == 18250.0

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
