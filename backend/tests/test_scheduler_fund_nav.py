"""TDD tests for job_pull_fund_nav — .OF 基金净值定时拉取任务。

测试场景：
1. 正常拉取：有 2 只 .OF 持仓，fetch 返回数据 → 写入 fund_daily_nav，返回 planned/pulled
2. fetch 返回空：planned>0, pulled=0, coverage=0.0
3. 无 .OF 持仓：planned=0, pulled=0
4. 增量补缺：已有部分日期 → planned 只计缺口数（不含已存在），pulled=planned

planned 语义：本次需补缺的 (基金 × 日期) 数（不含已存在），覆盖率 = pulled / planned。
- planned=0 表示无缺口（数据已完整），coverage=None
- planned>0 且 pulled=0 表示拉取失败，coverage=0.0
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models import Holding, FundDailyNav


@pytest.fixture
def fresh_db():
    """独立 SQLite + SessionLocal monkeypatch（让 job 内部 SessionLocal() 拿到测试 db）。"""
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

    # monkeypatch scheduler.SessionLocal → 返回测试 session
    import services.scheduler as sched_mod
    original_session_local = sched_mod.SessionLocal
    sched_mod.SessionLocal = lambda: session

    yield session

    sched_mod.SessionLocal = original_session_local
    session.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def _add_holding(db, code, name, qty, user_id=1):
    db.add(Holding(
        user_id=user_id, security_code=code, security_name=name,
        quantity=qty, price=1.0, currency="CNY", amount=qty, amount_cny=qty,
        asset_type="a_share_equity",
    ))
    db.commit()


def _mock_nav_rows(fund_code, days_back):
    """构造 mock NAV 行（fund_nav_fetcher.parse_nav_row 的输出格式）。"""
    rows = []
    today = date.today()
    for i in range(days_back):
        d = today - timedelta(days=i)
        rows.append({
            "trade_date": d,
            "nav": 1.0 + i * 0.01,
            "accumulated_nav": 1.5 + i * 0.01,
            "daily_return": 0.5,
        })
    return rows


def test_job_pull_fund_nav_normal(fresh_db, monkeypatch):
    """正常：2 只 .OF 持仓，fund_daily_nav 空，fetch 返回 3 天 → 写入 6 行。"""
    _add_holding(fresh_db, "015885.OF", "基金A", 100)
    _add_holding(fresh_db, "024329.OF", "基金B", 200)

    call_log = []
    def mock_fetch(fund_code, start, end, **kw):
        call_log.append(fund_code)
        return _mock_nav_rows(fund_code, 3)

    monkeypatch.setattr("services.scheduler.fetch_nav_all", mock_fetch)

    from services.scheduler import job_pull_fund_nav
    result = job_pull_fund_nav(days=3)

    assert result["planned"] == 6  # 2 基金 × 3 天
    assert result["pulled"] == 6
    assert result["filled_total"] == 6
    assert len(call_log) == 2

    # 验证 fund_daily_nav 写入
    nav_rows = fresh_db.query(FundDailyNav).all()
    assert len(nav_rows) == 6


def test_job_pull_fund_nav_fetch_empty(fresh_db, monkeypatch):
    """fetch 返回空 → planned>0, pulled=0, coverage=0.0。"""
    _add_holding(fresh_db, "017470.OF", "基金C", 100)

    monkeypatch.setattr("services.scheduler.fetch_nav_all",
                        lambda *a, **kw: [])

    from services.scheduler import job_pull_fund_nav
    result = job_pull_fund_nav(days=5)

    assert result["planned"] == 5  # 1 基金 × 5 天
    assert result["pulled"] == 0
    assert result["filled_total"] == 0

    nav_rows = fresh_db.query(FundDailyNav).all()
    assert len(nav_rows) == 0


def test_job_pull_fund_nav_no_of_holdings(fresh_db, monkeypatch):
    """无 .OF 持仓 → planned=0, pulled=0。"""
    _add_holding(fresh_db, "600000.SH", "浦发银行", 100)

    from services.scheduler import job_pull_fund_nav
    result = job_pull_fund_nav(days=5)

    assert result["planned"] == 0
    assert result["pulled"] == 0


def test_job_pull_fund_nav_skip_existing(fresh_db, monkeypatch):
    """fund_daily_nav 已有部分日期 → 只补缺口（增量，不覆盖已有）。"""
    _add_holding(fresh_db, "015885.OF", "基金A", 100)

    # 预写入 1 天
    today = date.today()
    fresh_db.add(FundDailyNav(
        fund_code="015885.OF", trade_date=today, nav=2.0, source="test",
    ))
    fresh_db.commit()

    monkeypatch.setattr("services.scheduler.fetch_nav_all",
                        lambda *a, **kw: _mock_nav_rows("015885.OF", 3))

    from services.scheduler import job_pull_fund_nav
    result = job_pull_fund_nav(days=3)

    # planned=2（仅缺口数，today 已存在不计），pulled=2（补缺成功）
    assert result["planned"] == 2
    assert result["pulled"] == 2

    nav_rows = fresh_db.query(FundDailyNav).filter(
        FundDailyNav.fund_code == "015885.OF"
    ).all()
    assert len(nav_rows) == 3  # 1 预写入 + 2 新拉取


def test_job_pull_fund_nav_filters_non_trading_days(fresh_db, monkeypatch):
    """TradingCalendar 有数据时，planned 只计交易日缺口（非交易日不计入）。"""
    from models import TradingCalendar
    _add_holding(fresh_db, "015885.OF", "基金A", 100)

    # 构造过去 3 天的日历：今天(Sat 非交易日)、昨天(Fri 交易日)、前天(Thu 交易日)
    today = date.today()
    d1 = today - timedelta(days=1)
    d2 = today - timedelta(days=2)
    fresh_db.add(TradingCalendar(market="CN", date=today, is_trading=False, source="test"))
    fresh_db.add(TradingCalendar(market="CN", date=d1, is_trading=True, source="test"))
    fresh_db.add(TradingCalendar(market="CN", date=d2, is_trading=True, source="test"))
    fresh_db.commit()

    # mock fetch 返回 3 天数据（含非交易日 today）
    monkeypatch.setattr("services.scheduler.fetch_nav_all",
                        lambda *a, **kw: _mock_nav_rows("015885.OF", 3))

    from services.scheduler import job_pull_fund_nav
    result = job_pull_fund_nav(days=3)

    # planned=2（只计 2 个交易日，today 非交易日被过滤掉）
    assert result["planned"] == 2
    # pulled=2（mock 返回 3 天，但 today 非交易日不在 target_dates，所以只写 2 天）
    assert result["pulled"] == 2


def test_job_pull_fund_nav_no_calendar_fallback(fresh_db, monkeypatch):
    """TradingCalendar 无数据时，回退到原 target_dates（不过滤，向后兼容）。"""
    _add_holding(fresh_db, "015885.OF", "基金A", 100)
    # 不预写入任何 TradingCalendar 数据

    monkeypatch.setattr("services.scheduler.fetch_nav_all",
                        lambda *a, **kw: _mock_nav_rows("015885.OF", 3))

    from services.scheduler import job_pull_fund_nav
    result = job_pull_fund_nav(days=3)

    # 无日历数据 → 不过滤 → planned=3（1基金×3天）
    assert result["planned"] == 3
    assert result["pulled"] == 3
