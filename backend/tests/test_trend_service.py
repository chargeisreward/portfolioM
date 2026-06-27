"""TDD tests for trend_service — .OF 基金 NAV 补充到 pc_map（chart 专用）。

背景：/api/trend 只从 PriceCache 取价，.OF 基金在 PriceCache 无数据 → 整只 holding 被 skip →
chart 总资产缺失 .OF 部分导致"虚假下跌"。

修复：对 .OF 基金，从 fund_daily_nav 补充到 pc_map，_resolve_px 自然 backward-fill
（已实现：某日无价时用该日之前最近的真实价）。
"""
import os
import tempfile
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models import FundDailyNav, PriceCache
from services.trend_service import load_of_nav_to_pc_map, resolve_px


@pytest.fixture
def fresh_db():
    """独立 SQLite + 完整 schema。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_load_of_nav_no_price_cache(fresh_db):
    """.OF 基金在 PriceCache 无数据时，从 fund_daily_nav 补充到 pc_map。"""
    today = date.today()
    d1 = today - timedelta(days=1)
    d2 = today - timedelta(days=2)
    fresh_db.add(FundDailyNav(fund_code="015885.OF", trade_date=d1, nav=2.5, source="test"))
    fresh_db.add(FundDailyNav(fund_code="015885.OF", trade_date=d2, nav=2.4, source="test"))
    fresh_db.commit()

    pc_map = {}  # 015885.OF 无 PriceCache
    cutoff = today - timedelta(days=30)
    load_of_nav_to_pc_map(fresh_db, pc_map, ["015885.OF"], cutoff)

    assert "015885.OF" in pc_map
    assert pc_map["015885.OF"][d1.isoformat()] == 2.5
    assert pc_map["015885.OF"][d2.isoformat()] == 2.4


def test_load_of_nav_price_cache_priority(fresh_db):
    """PriceCache 有数据时，fund_daily_nav 不覆盖（PriceCache 优先）。"""
    today = date.today()
    d = today - timedelta(days=1)
    d_iso = d.isoformat()

    fresh_db.add(PriceCache(stock_code="015885.OF", trade_date=d, close_px=2.0, source="tencent"))
    fresh_db.add(FundDailyNav(fund_code="015885.OF", trade_date=d, nav=2.5, source="test"))
    fresh_db.commit()

    pc_map = {"015885.OF": {d_iso: 2.0}}
    cutoff = today - timedelta(days=30)
    load_of_nav_to_pc_map(fresh_db, pc_map, ["015885.OF"], cutoff)

    # PriceCache 2.0 优先，不被 fund_daily_nav 2.5 覆盖
    assert pc_map["015885.OF"][d_iso] == 2.0


def test_load_of_nav_empty_codes(fresh_db):
    """of_codes 为空时，不报错，pc_map 不变。"""
    pc_map = {}
    cutoff = date.today() - timedelta(days=30)
    load_of_nav_to_pc_map(fresh_db, pc_map, [], cutoff)
    assert pc_map == {}


def test_load_of_nav_no_fund_daily_nav(fresh_db):
    """fund_daily_nav 也无数据时，不报错（pc_map 不新增空 entry）。"""
    pc_map = {}
    cutoff = date.today() - timedelta(days=30)
    load_of_nav_to_pc_map(fresh_db, pc_map, ["999999.OF"], cutoff)
    # 不应在 pc_map 中留下空 dict（否则下游 eligible 判断会误以为有数据）
    assert "999999.OF" not in pc_map


def test_load_of_nav_cutoff_filter(fresh_db):
    """cutoff 之前的 fund_daily_nav 数据不被加载。"""
    today = date.today()
    old_date = today - timedelta(days=100)  # 超出 cutoff
    recent_date = today - timedelta(days=5)  # 在 cutoff 内
    fresh_db.add(FundDailyNav(fund_code="015885.OF", trade_date=old_date, nav=1.0, source="test"))
    fresh_db.add(FundDailyNav(fund_code="015885.OF", trade_date=recent_date, nav=2.0, source="test"))
    fresh_db.commit()

    pc_map = {}
    cutoff = today - timedelta(days=30)  # 只看过去 30 天
    load_of_nav_to_pc_map(fresh_db, pc_map, ["015885.OF"], cutoff)

    assert recent_date.isoformat() in pc_map.get("015885.OF", {})
    assert old_date.isoformat() not in pc_map.get("015885.OF", {})


# -----------------------------------------------------------------------------
# resolve_px — backward-fill 取价（修复 close_px=NULL 导致的虚假下跌）
# -----------------------------------------------------------------------------

def test_resolve_px_exact_match():
    """当日有真实价 → 直接返回。"""
    d = "2026-06-26"
    code_map = {d: 4.907}
    assert resolve_px(code_map, d, days=90) == 4.907


def test_resolve_px_none_value_triggers_backward_fill():
    """当日 close_px=None（如休市日被 intraday job 写入空行）→ 继续向前找真实价。

    这是 6-27 虚假下跌的根因修复：price_cache 有 6-27 行但 close_px=NULL，
    _resolve_px 不应返回 None 跳过该 holding，而应 backward-fill 到最近的真实价。
    """
    d27 = "2026-06-27"
    d26 = "2026-06-26"
    code_map = {d27: None, d26: 4.907}  # 6-27 有行但 None, 6-26 有真实价
    # 6-27 应 backward-fill 到 6-26 的 4.907，而非返回 None
    assert resolve_px(code_map, d27, days=90) == 4.907


def test_resolve_px_no_match_returns_none():
    """窗口内完全无价 → 返回 None（不编造）。

    距离 12 天，days=3 + 5 天容差 = 8 天仍找不到 → 返回 None。
    """
    code_map = {"2026-06-15": 5.0}
    assert resolve_px(code_map, "2026-06-27", days=3) is None  # 12 天 > days+5=8 容差


def test_resolve_px_empty_map():
    """空 code_map → 返回 None。"""
    assert resolve_px({}, "2026-06-27", days=90) is None
