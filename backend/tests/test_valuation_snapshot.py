"""估值表日截面服务测试 — TDD

覆盖 8 个核心场景：
1. 建表后表存在 + 列齐全
2. 单日重算写入行数正确（cash + holdings）
3. as_of_date <= get_confirmed_as_of → is_locked=True
4. rebuild_valuation_to_date 跳过已锁定日
5. force_from 触发后已锁定日被解锁重算
6. 截面不存在时 get_valuation_snapshot 自动重算
7. user_a 的截面不污染 user_b（跨用户隔离）
8. 现金行始终排第一

测试策略：
- 用临时文件 SQLite（参考 test_trading_rebuild.py）
- 预插入 HoldingDailySnapshot 数据（绕过 get_snapshot_for_date 复杂逻辑）
- monkeypatch mock get_confirmed_as_of 返回固定日期
- 不测 _resolve_public_metrics（依赖公共表，e2e 覆盖）
"""
import os
import tempfile

import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
from database import Base
from models import (
    HoldingDailySnapshot,
    ValuationDailySnapshot,
)
from services import valuation_snapshot_service as vss


@pytest.fixture
def fresh_db():
    """每个测试用独立的临时文件 SQLite。"""
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


def _insert_holding_snapshot(db, user_id, as_of, code, name, qty, price, amount,
                              asset_type="a_share_etf", is_cash=False):
    """辅助：插入一行 HoldingDailySnapshot（_rebuild_one_day 的数据源）。"""
    db.add(HoldingDailySnapshot(
        user_id=user_id,
        as_of_date=as_of,
        security_code=code,
        security_name=name,
        quantity=qty,
        price=price,
        price_cny=price,
        currency="CNY",
        fx_rate=1.0,
        amount_cny=amount,
        asset_type=asset_type,
        is_cash=is_cash,
        is_initial=False,
        holding_uid=None,
    ))
    db.commit()


def _mock_confirmed_as_of(fixed_date):
    """返回一个 mock get_confirmed_as_of 函数，固定返回 fixed_date。"""
    def _mock(db):
        return fixed_date
    return _mock


# ---------- 1. 建表 ----------

def test_table_creation(fresh_db):
    """建表后 valuation_daily_snapshot 表存在 + 列齐全。"""
    inspector = inspect(fresh_db.bind)
    assert "valuation_daily_snapshot" in inspector.get_table_names()
    cols = {c["name"] for c in inspector.get_columns("valuation_daily_snapshot")}
    expected = {
        "id", "user_id", "as_of_date", "security_code", "security_name",
        "quantity", "price", "price_cny", "currency", "fx_rate", "amount_cny",
        "asset_type", "type2", "is_cash", "holding_uid",
        "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield", "market_cap",
        "is_locked", "locked_at", "created_at", "updated_at",
    }
    missing = expected - cols
    assert not missing, f"missing columns: {missing}"


# ---------- 2. 单日重算 ----------

def test_rebuild_one_day_writes_rows(fresh_db, monkeypatch):
    """单日重算写入行数正确（cash + holdings）。"""
    as_of = date(2025, 7, 19)
    _insert_holding_snapshot(fresh_db, 1, as_of, "CASH", "现金", 10000, 1.0, 10000, is_cash=True)
    _insert_holding_snapshot(fresh_db, 1, as_of, "510300.SH", "沪深300ETF", 1000, 4.5, 4500)

    # mock get_confirmed_as_of 返回较早日期，避免自动锁定
    monkeypatch.setattr(vss, "get_confirmed_as_of", _mock_confirmed_as_of(date(2025, 7, 18)))

    n = vss._rebuild_one_day(fresh_db, 1, as_of)
    assert n == 2  # CASH + 510300.SH

    rows = fresh_db.query(ValuationDailySnapshot).filter(
        ValuationDailySnapshot.user_id == 1,
        ValuationDailySnapshot.as_of_date == as_of,
    ).all()
    assert len(rows) == 2
    codes = {r.security_code for r in rows}
    assert codes == {"CASH", "510300.SH"}


# ---------- 3. 锁定逻辑 ----------

def test_lock_when_confirmed(fresh_db, monkeypatch):
    """as_of_date <= get_confirmed_as_of → is_locked=True。"""
    as_of = date(2025, 7, 19)
    _insert_holding_snapshot(fresh_db, 1, as_of, "CASH", "现金", 1000, 1.0, 1000, is_cash=True)
    _insert_holding_snapshot(fresh_db, 1, as_of, "510300.SH", "沪深300ETF", 100, 4.0, 400)

    # mock get_confirmed_as_of 返回 as_of 当日 → 满足锁定条件
    monkeypatch.setattr(vss, "get_confirmed_as_of", _mock_confirmed_as_of(as_of))

    vss._rebuild_one_day(fresh_db, 1, as_of)
    locked = vss._check_and_lock(fresh_db, 1, as_of)
    assert locked is True

    rows = fresh_db.query(ValuationDailySnapshot).filter(
        ValuationDailySnapshot.user_id == 1,
        ValuationDailySnapshot.as_of_date == as_of,
    ).all()
    assert all(r.is_locked for r in rows)
    assert all(r.locked_at is not None for r in rows)


def test_lock_not_triggered_when_unconfirmed(fresh_db, monkeypatch):
    """as_of_date > get_confirmed_as_of → is_locked=False。"""
    as_of = date(2025, 7, 19)
    _insert_holding_snapshot(fresh_db, 1, as_of, "CASH", "现金", 1000, 1.0, 1000, is_cash=True)

    # mock get_confirmed_as_of 返回较早日期 → 不满足锁定条件
    monkeypatch.setattr(vss, "get_confirmed_as_of", _mock_confirmed_as_of(date(2025, 7, 18)))

    vss._rebuild_one_day(fresh_db, 1, as_of)
    locked = vss._check_and_lock(fresh_db, 1, as_of)
    assert locked is False

    rows = fresh_db.query(ValuationDailySnapshot).filter(
        ValuationDailySnapshot.user_id == 1,
        ValuationDailySnapshot.as_of_date == as_of,
    ).all()
    assert all(not r.is_locked for r in rows)


# ---------- 4. 跳过已锁定日 ----------

def test_skip_when_locked(fresh_db, monkeypatch):
    """rebuild_valuation_to_date 跳过已锁定日。"""
    as_of = date(2025, 7, 19)
    _insert_holding_snapshot(fresh_db, 1, as_of, "CASH", "现金", 1000, 1.0, 1000, is_cash=True)
    monkeypatch.setattr(vss, "get_confirmed_as_of", _mock_confirmed_as_of(as_of))

    # 首次重算 + 锁定
    vss._rebuild_one_day(fresh_db, 1, as_of)
    vss._check_and_lock(fresh_db, 1, as_of)

    # 第二次 rebuild_valuation_to_date 应跳过已锁定日
    result = vss.rebuild_valuation_to_date(fresh_db, 1, as_of, force_from=None)
    assert result["days_skipped_locked"] == 1
    assert result["days_processed"] == 0


# ---------- 5. force_from 解锁 ----------

def test_force_from_unlocks(fresh_db, monkeypatch):
    """force_from 触发后已锁定日被解锁重算。"""
    as_of = date(2025, 7, 19)
    _insert_holding_snapshot(fresh_db, 1, as_of, "CASH", "现金", 1000, 1.0, 1000, is_cash=True)
    monkeypatch.setattr(vss, "get_confirmed_as_of", _mock_confirmed_as_of(as_of))

    # 首次重算 + 锁定
    vss._rebuild_one_day(fresh_db, 1, as_of)
    vss._check_and_lock(fresh_db, 1, as_of)
    assert fresh_db.query(ValuationDailySnapshot).filter_by(
        user_id=1, as_of_date=as_of).first().is_locked

    # force_from 触发解锁重算
    result = vss.rebuild_valuation_to_date(fresh_db, 1, as_of, force_from=as_of)
    assert result["days_processed"] == 1
    # force_from 触发后会重新检查锁定条件，因 mock 仍返回 as_of，所以会再次锁定
    assert result["days_locked_now"] == 1


# ---------- 6. 自动重算 ----------

def test_get_valuation_snapshot_auto_rebuild(fresh_db, monkeypatch):
    """截面不存在时 get_valuation_snapshot 自动重算。"""
    as_of = date(2025, 7, 19)
    _insert_holding_snapshot(fresh_db, 1, as_of, "CASH", "现金", 500, 1.0, 500, is_cash=True)
    _insert_holding_snapshot(fresh_db, 1, as_of, "510300.SH", "沪深300ETF", 100, 4.0, 400)
    monkeypatch.setattr(vss, "get_confirmed_as_of", _mock_confirmed_as_of(date(2025, 7, 18)))

    # 截面不存在 → 自动重算
    snap = vss.get_valuation_snapshot(fresh_db, 1, as_of)
    assert snap is not None
    assert snap["as_of_date"] == as_of.isoformat()
    assert len(snap["holdings"]) == 2


# ---------- 7. 跨用户隔离 ----------

def test_user_isolation(fresh_db, monkeypatch):
    """user_a 的截面不污染 user_b。"""
    as_of = date(2025, 7, 19)
    _insert_holding_snapshot(fresh_db, 1, as_of, "CASH", "现金", 1000, 1.0, 1000, is_cash=True)
    _insert_holding_snapshot(fresh_db, 1, as_of, "510300.SH", "沪深300ETF", 100, 4.0, 400)
    _insert_holding_snapshot(fresh_db, 2, as_of, "CASH", "现金", 2000, 1.0, 2000, is_cash=True)
    _insert_holding_snapshot(fresh_db, 2, as_of, "159915.SZ", "创业板ETF", 200, 3.0, 600)
    monkeypatch.setattr(vss, "get_confirmed_as_of", _mock_confirmed_as_of(date(2025, 7, 18)))

    vss._rebuild_one_day(fresh_db, 1, as_of)
    vss._rebuild_one_day(fresh_db, 2, as_of)

    user1_rows = fresh_db.query(ValuationDailySnapshot).filter_by(
        user_id=1, as_of_date=as_of).all()
    user2_rows = fresh_db.query(ValuationDailySnapshot).filter_by(
        user_id=2, as_of_date=as_of).all()

    assert len(user1_rows) == 2
    assert len(user2_rows) == 2
    assert {r.security_code for r in user1_rows} == {"CASH", "510300.SH"}
    assert {r.security_code for r in user2_rows} == {"CASH", "159915.SZ"}


# ---------- 8. 现金行第一 ----------

def test_cash_row_first(fresh_db, monkeypatch):
    """现金行始终排第一（is_cash.desc() 排在前面）。"""
    as_of = date(2025, 7, 19)
    _insert_holding_snapshot(fresh_db, 1, as_of, "510300.SH", "沪深300ETF", 100, 4.0, 400)
    _insert_holding_snapshot(fresh_db, 1, as_of, "CASH", "现金", 1000, 1.0, 1000, is_cash=True)
    _insert_holding_snapshot(fresh_db, 1, as_of, "159915.SZ", "创业板ETF", 200, 3.0, 600)
    monkeypatch.setattr(vss, "get_confirmed_as_of", _mock_confirmed_as_of(date(2025, 7, 18)))

    snap = vss.get_valuation_snapshot(fresh_db, 1, as_of)
    assert snap is not None
    assert snap["holdings"][0]["is_cash"] is True
    assert snap["holdings"][0]["security_code"] == "CASH"
