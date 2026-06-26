"""2 次拉取规则单元测试 — 覆盖 _is_past_cutoff / _get_stock_price / _get_fx_rate / get_confirmed_as_of。

规则（2026-06-26）：
  - T 日 20:00 第 1 次拉 T 日收盘价
  - T+1 日 08:00 第 2 次（关门时间）
  - T+1 日 08:00 后：允许 ≤ T 回退填入 T 字段（is_stale=True）
  - T+1 日 08:00 前：拒绝回退（strict_mode=True）

验证属性（非"不抛异常"）：
  - _is_past_cutoff：边界时刻 T+1 07:59:59 → False；T+1 08:00:00 → True
  - _get_stock_price(strict_mode=True)：T 有价→(price,False)；T 无价未过cutoff→(None,False)；
    T 无价已过cutoff+T-1有价→(T-1价,True)；T 无价已过cutoff+T-1..T-7都无价→(None,False)
  - _get_stock_price(strict_mode=False)：保留原立即回退（T 无价→T-1价,True）
  - _get_fx_rate：同上 4 个 case
  - get_confirmed_as_of：now>=08:00→today-1；now<08:00→today-2；跳过非交易日
"""
import os
os.environ.setdefault("APP_PASSWORD", "")
from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

import pytest

from services.drill_snapshot import (
    _is_past_cutoff,
    _get_stock_price,
    _get_fx_rate,
)
from services.trading_calendar import get_confirmed_as_of


# ============================================================
# _is_past_cutoff
# ============================================================

class TestIsPastCutoff:
    """_is_past_cutoff：判断是否过 as_of_date + 1day 08:00（北京时间）。"""

    @patch("services.drill_snapshot.datetime")
    def test_before_cutoff_returns_false(self, mock_dt):
        """T+1 07:59:59 → 未过 cutoff → False"""
        mock_dt.now.return_value = datetime(2026, 6, 26, 7, 59, 59)
        mock_dt.combine = datetime.combine
        assert _is_past_cutoff(date(2026, 6, 25)) is False

    @patch("services.drill_snapshot.datetime")
    def test_at_cutoff_returns_true(self, mock_dt):
        """T+1 08:00:00 → 刚过 cutoff → True"""
        mock_dt.now.return_value = datetime(2026, 6, 26, 8, 0, 0)
        mock_dt.combine = datetime.combine
        assert _is_past_cutoff(date(2026, 6, 25)) is True

    @patch("services.drill_snapshot.datetime")
    def test_after_cutoff_returns_true(self, mock_dt):
        """T+1 12:00 → 已过 cutoff → True"""
        mock_dt.now.return_value = datetime(2026, 6, 26, 12, 0, 0)
        mock_dt.combine = datetime.combine
        assert _is_past_cutoff(date(2026, 6, 25)) is True

    @patch("services.drill_snapshot.datetime")
    def test_same_day_returns_false(self, mock_dt):
        """T 日当天 20:00 → 未过 cutoff → False（T+1 08:00 还没到）"""
        mock_dt.now.return_value = datetime(2026, 6, 25, 20, 0, 0)
        mock_dt.combine = datetime.combine
        assert _is_past_cutoff(date(2026, 6, 25)) is False


# ============================================================
# _get_stock_price
# ============================================================

def _make_price_row(px):
    """构造 mock PriceCache row，close_px=px。"""
    r = MagicMock()
    r.close_px = px
    return r


def _mock_db_price(first_results):
    """构造 mock db：db.query(PriceCache).filter(...).order_by(...).first()
    按调用顺序返回 first_results 列表中的元素。

    用 "600519"（无后缀）作为 stock_code，确保 norm==stock_code，
    _query_price_for_date 每次只调 1 次 first()。
    """
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.side_effect = first_results
    return db


class TestGetStockPriceStrict:
    """_get_stock_price(strict_mode=True) — 2 次拉取规则。"""

    def test_t_has_price_returns_non_stale(self):
        """T 日有价 → (price, False)"""
        db = _mock_db_price([_make_price_row(1505.0)])
        price, stale = _get_stock_price(db, "600519", date(2026, 6, 25), strict_mode=True)
        assert price == 1505.0
        assert stale is False

    @patch("services.drill_snapshot._is_past_cutoff", return_value=False)
    def test_t_no_price_before_cutoff_returns_none(self, _mock_cutoff):
        """T 日无价 + 未过 cutoff → (None, False)（拒绝回退）"""
        db = _mock_db_price([None])
        price, stale = _get_stock_price(db, "600519", date(2026, 6, 25), strict_mode=True)
        assert price is None
        assert stale is False

    @patch("services.drill_snapshot._is_past_cutoff", return_value=True)
    def test_t_no_price_after_cutoff_fallback_t1(self, _mock_cutoff):
        """T 日无价 + 已过 cutoff + T-1 有价 → (T-1 价, True)"""
        # first() 调用顺序：T 日(None) → T-1(1453)
        db = _mock_db_price([None, _make_price_row(1453.0)])
        price, stale = _get_stock_price(db, "600519", date(2026, 6, 25), strict_mode=True)
        assert price == 1453.0
        assert stale is True

    @patch("services.drill_snapshot._is_past_cutoff", return_value=True)
    def test_t_no_price_after_cutoff_all_fallback_empty(self, _mock_cutoff):
        """T 日无价 + 已过 cutoff + T-1..T-7 都无价 → (None, False)"""
        # first() 调用顺序：T 日(None) → T-1..T-7 都 None（共 8 次）
        db = _mock_db_price([None] * 8)
        price, stale = _get_stock_price(db, "600519", date(2026, 6, 25), strict_mode=True)
        assert price is None
        assert stale is False


class TestGetStockPriceNonStrict:
    """_get_stock_price(strict_mode=False) — 保留原立即回退。"""

    def test_t_has_price_returns_non_stale(self):
        """T 日有价 → (price, False)"""
        db = _mock_db_price([_make_price_row(1505.0)])
        price, stale = _get_stock_price(db, "600519", date(2026, 6, 25), strict_mode=False)
        assert price == 1505.0
        assert stale is False

    def test_t_no_price_fallback_t1_immediate(self):
        """T 日无价 → 立即回退 T-1 → (T-1 价, True)（不检查 cutoff）"""
        # strict_mode=False：candidates = [T, T-1, T-2, ...]；T 无价→T-1 有价
        db = _mock_db_price([None, _make_price_row(1453.0)])
        price, stale = _get_stock_price(db, "600519", date(2026, 6, 25), strict_mode=False)
        assert price == 1453.0
        assert stale is True


# ============================================================
# _get_fx_rate
# ============================================================

def _make_rate_row(rate):
    """构造 mock ExchangeRate row，rate=rate。"""
    r = MagicMock()
    r.rate = rate
    return r


def _mock_db_rate(first_results):
    """构造 mock db：db.query(ExchangeRate).filter(...).first()
    按调用顺序返回 first_results。
    """
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = first_results
    return db


class TestGetFxRateStrict:
    """_get_fx_rate(strict_mode=True) — 2 次拉取规则。"""

    def test_same_currency_returns_1(self):
        """from==to → 1.0（不查 DB）"""
        db = MagicMock()
        assert _get_fx_rate(db, "CNY", "CNY", date(2026, 6, 25), strict_mode=True) == 1.0

    def test_t_has_rate_returns_it(self):
        """T 日有汇率 → 返回 rate"""
        db = _mock_db_rate([_make_rate_row(0.85)])
        assert _get_fx_rate(db, "USD", "CNY", date(2026, 6, 25), strict_mode=True) == 0.85

    @patch("services.drill_snapshot._is_past_cutoff", return_value=False)
    def test_t_no_rate_before_cutoff_returns_none(self, _mock_cutoff):
        """T 日无汇率 + 未过 cutoff → None（拒绝回退）"""
        db = _mock_db_rate([None])
        assert _get_fx_rate(db, "USD", "CNY", date(2026, 6, 25), strict_mode=True) is None

    @patch("services.drill_snapshot._is_past_cutoff", return_value=True)
    def test_t_no_rate_after_cutoff_fallback_t1(self, _mock_cutoff):
        """T 日无汇率 + 已过 cutoff + T-1 有汇率 → 返回 T-1 rate"""
        # first() 调用顺序：T 日(None) → T-1(0.84)
        db = _mock_db_rate([None, _make_rate_row(0.84)])
        assert _get_fx_rate(db, "USD", "CNY", date(2026, 6, 25), strict_mode=True) == 0.84


class TestGetFxRateNonStrict:
    """_get_fx_rate(strict_mode=False) — 保留原立即回退。"""

    def test_t_no_rate_fallback_t1_immediate(self):
        """T 日无汇率 → 立即回退 T-1（不检查 cutoff）"""
        # strict_mode=False：[T, T-1, ...]；T 无→T-1 有
        db = _mock_db_rate([None, _make_rate_row(0.84)])
        assert _get_fx_rate(db, "USD", "CNY", date(2026, 6, 25), strict_mode=False) == 0.84


# ============================================================
# get_confirmed_as_of
# ============================================================

class TestGetConfirmedAsOf:
    """get_confirmed_as_of：返回当前时刻"已确认收盘"的最近交易日。

    语义：T 日收盘价在 T+1 08:00 后才确认。
      - now >= 08:00 → candidate = today - 1
      - now <  08:00 → candidate = today - 2
    """

    @patch("services.trading_calendar.datetime")
    @patch("services.trading_calendar.is_trading_day", return_value=True)
    def test_after_0800_returns_today_minus_1(self, _mock_trading, mock_dt):
        """now=2026-06-26 09:00 → confirmed=2026-06-25（today-1，过 08:00）"""
        mock_dt.now.return_value = datetime(2026, 6, 26, 9, 0, 0)
        mock_dt.time = datetime.time
        db = MagicMock()
        assert get_confirmed_as_of(db, "CN") == date(2026, 6, 25)

    @patch("services.trading_calendar.datetime")
    @patch("services.trading_calendar.is_trading_day", return_value=True)
    def test_before_0800_returns_today_minus_2(self, _mock_trading, mock_dt):
        """now=2026-06-26 07:00 → confirmed=2026-06-24（today-2，未过 08:00）"""
        mock_dt.now.return_value = datetime(2026, 6, 26, 7, 0, 0)
        mock_dt.time = datetime.time
        db = MagicMock()
        assert get_confirmed_as_of(db, "CN") == date(2026, 6, 24)

    @patch("services.trading_calendar.datetime")
    @patch("services.trading_calendar.is_trading_day")
    def test_skip_non_trading_day(self, _mock_trading, mock_dt):
        """candidate 是非交易日 → 向前找最近交易日。

        now=2026-06-29 09:00（周一）→ candidate=2026-06-28（周日，非交易日）
        → 向前找：2026-06-27（周六，非交易日）→ 2026-06-26（周五，交易日）
        """
        mock_dt.now.return_value = datetime(2026, 6, 29, 9, 0, 0)
        mock_dt.time = datetime.time
        db = MagicMock()
        # is_trading_day 调用顺序：candidate=28(False) → 27(False) → 26(True)
        _mock_trading.side_effect = [False, False, True]
        assert get_confirmed_as_of(db, "CN") == date(2026, 6, 26)

    @patch("services.trading_calendar.datetime")
    @patch("services.trading_calendar.is_trading_day", return_value=True)
    def test_at_0800_exact_returns_today_minus_1(self, _mock_trading, mock_dt):
        """now=2026-06-26 08:00:00 → confirmed=2026-06-25（边界：>= 08:00）"""
        mock_dt.now.return_value = datetime(2026, 6, 26, 8, 0, 0)
        mock_dt.time = datetime.time
        db = MagicMock()
        assert get_confirmed_as_of(db, "CN") == date(2026, 6, 25)
