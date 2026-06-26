"""海外基金 NAV 延迟公布例外规则单元测试。

覆盖 is_overseas_fund / lookback_and_overwrite_nav 的比对覆写逻辑。

验证属性（Rule 9 — 测试验证有意义属性，非"不抛异常"）：
  - is_overseas_fund：9 个目标 → True；5 个非海外 → False；空名称 → False
  - lookback_and_overwrite_nav：
    * nav 变化 → 覆写 + 日期入 affected
    * accumulated_nav 从 None→有值 → 覆写 + 日期入 affected
    * 完全相同 → 不覆写 + 日期不入 affected
    * 新日期（fund_daily_nav 无记录）→ 插入 + 日期入 affected
    * PriceCache 同步覆写/插入
    * lsjz 返回空 → affected = []
    * lsjz 抛异常 → affected = []（内部捕获，不外抛）
"""
import os
os.environ.setdefault("APP_PASSWORD", "")
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from models import FundDailyNav, PriceCache
from services.overseas_fund_nav import is_overseas_fund, lookback_and_overwrite_nav


# ============================================================
# is_overseas_fund
# ============================================================

# 9 个目标基金名称（来自计划 §2 持仓实情 — 6 QDII + 3 港股通，含 4 只美股 QDII）
OVERSEAS_FUNDS = [
    ("006479.OF", "广发纳斯达克100ETF发起联接(QDII)C人民币"),
    ("007722.OF", "天弘标普500(QDII-FOF)C"),
    ("015311.OF", "华泰柏瑞恒生科技ETF联接(QDII)C"),
    ("018388.OF", "华泰柏瑞港股通红利ETF联接C"),
    ("019524.OF", "华泰柏瑞纳斯达克100ETF联接(QDII)A"),
    ("019525.OF", "华泰柏瑞纳斯达克100ETF联接(QDII)C"),
    ("021142.OF", "华夏港股通央企红利ETF联接C"),
    ("015885.OF", "中欧港股数字经济(QDII)C"),
    ("024329.OF", "易方达恒生港股通创新药C"),
]

# 5 个非海外基金名称（纯 A 股基金，名称无任何海外关键词）
NON_OVERSEAS_FUNDS = [
    ("110020.OF", "易方达蓝筹精选混合"),
    ("161725.OF", "招商中证白酒指数(LOF)A"),
    ("005827.OF", "易方达蓝筹精选混合C"),
    ("007466.OF", "中欧远见两年定期开放混合"),
    ("011609.OF", "景顺长城绩优成长混合"),
]


class TestIsOverseasFund:
    """is_overseas_fund：名称关键词匹配识别海外基金（QDII / 港股通）。"""

    @pytest.mark.parametrize("code,name", OVERSEAS_FUNDS)
    def test_overseas_funds_return_true(self, code, name):
        """9 个目标海外基金（含 4 只美股 QDII）→ True"""
        assert is_overseas_fund(name) is True, f"{code} ({name}) 应识别为海外基金"

    @pytest.mark.parametrize("code,name", NON_OVERSEAS_FUNDS)
    def test_non_overseas_funds_return_false(self, code, name):
        """5 个纯 A 股基金 → False"""
        assert is_overseas_fund(name) is False, f"{code} ({name}) 不应识别为海外基金"

    def test_empty_name_returns_false(self):
        """空名称 → False"""
        assert is_overseas_fund("") is False


# ============================================================
# lookback_and_overwrite_nav
# ============================================================

def _make_fund_nav_row(td, nav, acc, ret):
    """构造 mock FundDailyNav 行（模拟 ORM 对象）。"""
    r = MagicMock()
    r.trade_date = td
    r.nav = nav
    r.accumulated_nav = acc
    r.daily_return = ret
    return r


def _make_raw_lsjz(td, nav, acc, ret):
    """构造东财 lsjz 原始行（字符串值，模拟 API 返回）。

    字段映射：FSRQ→日期, DWJZ→净值, LJJZ→累计净值, JZZZL→日增长率
    """
    return {
        "FSRQ": td.isoformat(),
        "DWJZ": str(nav),
        "LJJZ": str(acc) if acc is not None else "",
        "JZZZL": str(ret) if ret is not None else "",
    }


def _make_mock_db(fund_nav_existing):
    """构造 mock db：db.query 按 model 参数分流。

    - db.query(FundDailyNav).filter_by(fund_code=...).all() → fund_nav_existing
    - db.query(PriceCache).filter(...).all() → []（模拟无现有 PriceCache → 走 insert 路径）
    """
    db = MagicMock()

    fund_nav_chain = MagicMock()
    fund_nav_chain.filter_by.return_value.all.return_value = fund_nav_existing

    price_cache_chain = MagicMock()
    price_cache_chain.filter.return_value.all.return_value = []

    def query_side_effect(model):
        if model is FundDailyNav:
            return fund_nav_chain
        return price_cache_chain

    db.query.side_effect = query_side_effect
    return db


class TestLookbackAndOverwriteNav:
    """lookback_and_overwrite_nav：回拉 N 交易日 + 比对 + 覆写。"""

    @patch("services.overseas_fund_nav.date")
    @patch("services.overseas_fund_nav.is_trading_day")
    @patch("services.overseas_fund_nav.fetch_nav_all")
    def test_mixed_cases(self, mock_fetch, mock_is_trading, mock_date):
        """一次回拉覆盖 4 种 case：
        - 6/25 nav 变化          → 覆写 + affected
        - 6/24 accumulated_nav None→有值 → 覆写 + affected
        - 6/23 完全相同           → 不覆写 + 不 affected
        - 6/22 新日期（无现有行）  → 插入 + affected
        """
        # 固定 today = 6/26（周五）
        mock_date.today.return_value = date(2026, 6, 26)

        # 6/22-6/26 全部视为交易日
        trading_days = {date(2026, 6, d) for d in range(22, 27)}
        mock_is_trading.side_effect = lambda market, d, _db: d in trading_days

        # lsjz 返回 4 天数据（6/26 NAV 未公布，无 6/26 行 — 这正是海外基金延迟场景）
        mock_fetch.return_value = [
            _make_raw_lsjz(date(2026, 6, 25), 1.2345, 1.5678, 0.12),  # nav 变化
            _make_raw_lsjz(date(2026, 6, 24), 1.2000, 1.5678, 0.50),  # acc None→value
            _make_raw_lsjz(date(2026, 6, 23), 1.2000, 1.5000, 0.50),  # 完全相同
            _make_raw_lsjz(date(2026, 6, 22), 1.2000, 1.5000, 0.50),  # 新日期
        ]

        # 现有 FundDailyNav 行（6/22 无现有行 → 走 insert）
        existing_rows = [
            _make_fund_nav_row(date(2026, 6, 25), 1.2000, 1.5000, 0.50),  # nav 将被改
            _make_fund_nav_row(date(2026, 6, 24), 1.2000, None, 0.50),    # acc None→value
            _make_fund_nav_row(date(2026, 6, 23), 1.2000, 1.5000, 0.50),  # 不变
        ]
        db = _make_mock_db(existing_rows)

        # 执行
        affected = lookback_and_overwrite_nav(db, "006479.OF", lookback_days=5)

        # --- 验证 affected dates（6/25, 6/24, 6/22 — 不含 6/23）---
        assert set(affected) == {date(2026, 6, 25), date(2026, 6, 24), date(2026, 6, 22)}

        # --- 验证 nav 覆写：6/25 existing row 的 nav/acc/ret 被更新 ---
        row_625 = next(r for r in existing_rows if r.trade_date == date(2026, 6, 25))
        assert row_625.nav == 1.2345
        assert row_625.accumulated_nav == 1.5678
        assert row_625.daily_return == 0.12

        # --- 验证 accumulated_nav 覆写：6/24 existing row 的 acc 被更新 ---
        row_624 = next(r for r in existing_rows if r.trade_date == date(2026, 6, 24))
        assert row_624.accumulated_nav == 1.5678

        # --- 验证 6/23 完全相同 → 未被修改 ---
        row_623 = next(r for r in existing_rows if r.trade_date == date(2026, 6, 23))
        assert row_623.nav == 1.2000
        assert row_623.accumulated_nav == 1.5000
        assert row_623.daily_return == 0.50

        # --- 验证 db.add 调用次数 ---
        # 6/22 新 FundDailyNav → 1 add
        # 6/25, 6/24, 6/22 各 1 个 PriceCache（mock 返回空→insert）→ 3 adds
        # 合计 4 add
        assert db.add.call_count == 4

        # --- 验证 db.commit 被调用 ---
        db.commit.assert_called_once()

    @patch("services.overseas_fund_nav.date")
    @patch("services.overseas_fund_nav.is_trading_day")
    @patch("services.overseas_fund_nav.fetch_nav_all")
    def test_lsjz_empty_returns_empty(self, mock_fetch, mock_is_trading, mock_date):
        """lsjz 返回空（NAV 尚未公布）→ affected = []，无覆写。"""
        mock_date.today.return_value = date(2026, 6, 26)
        mock_is_trading.return_value = True
        mock_fetch.return_value = []  # 无数据

        db = _make_mock_db([])

        affected = lookback_and_overwrite_nav(db, "006479.OF", lookback_days=5)

        assert affected == []
        db.add.assert_not_called()
        # lsjz 返回空时函数在 db.commit() 前提前 return（L125-127）
        db.commit.assert_not_called()

    @patch("services.overseas_fund_nav.date")
    @patch("services.overseas_fund_nav.is_trading_day")
    @patch("services.overseas_fund_nav.fetch_nav_all")
    def test_lsjz_exception_returns_empty(self, mock_fetch, mock_is_trading, mock_date):
        """lsjz 抛异常（网络错误等）→ affected = []，异常内部捕获不外抛。"""
        mock_date.today.return_value = date(2026, 6, 26)
        mock_is_trading.return_value = True
        mock_fetch.side_effect = RuntimeError("network timeout")

        db = _make_mock_db([])

        affected = lookback_and_overwrite_nav(db, "006479.OF", lookback_days=5)

        assert affected == []
        db.add.assert_not_called()

    @patch("services.overseas_fund_nav.date")
    @patch("services.overseas_fund_nav.is_trading_day")
    @patch("services.overseas_fund_nav.fetch_nav_all")
    def test_price_cache_overwrite_existing(self, mock_fetch, mock_is_trading, mock_date):
        """PriceCache 已有行时 → 更新 close_px（非插入新行）。"""
        mock_date.today.return_value = date(2026, 6, 26)
        mock_is_trading.return_value = True

        mock_fetch.return_value = [
            _make_raw_lsjz(date(2026, 6, 25), 1.2345, 1.5678, 0.12),
        ]

        # 现有 FundDailyNav（nav 不同 → 触发覆写）
        existing_fund_nav = [
            _make_fund_nav_row(date(2026, 6, 25), 1.2000, 1.5000, 0.50),
        ]

        # 现有 PriceCache 行（模拟已有缓存）
        existing_pc = MagicMock()
        existing_pc.close_px = 1.2000  # 旧值

        db = MagicMock()
        fund_nav_chain = MagicMock()
        fund_nav_chain.filter_by.return_value.all.return_value = existing_fund_nav
        price_cache_chain = MagicMock()
        price_cache_chain.filter.return_value.all.return_value = [existing_pc]

        def query_side_effect(model):
            if model is FundDailyNav:
                return fund_nav_chain
            return price_cache_chain

        db.query.side_effect = query_side_effect

        affected = lookback_and_overwrite_nav(db, "006479.OF", lookback_days=5)

        # 验证 affected 含 6/25
        assert date(2026, 6, 25) in affected

        # 验证 PriceCache.close_px 被更新为新 nav（1.2345），不是插入新行
        assert existing_pc.close_px == 1.2345

        # db.add 只用于 FundDailyNav 不需要（6/25 已有行 → 更新非插入）
        # PriceCache 也走更新路径 → db.add 不应被调用
        db.add.assert_not_called()
