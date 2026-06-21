"""
TDD tests for services/dedup.py — common dedup gate helpers.

Goal: every non-realtime crawler checks "今天是否已经持久化过" before fetching.
Helpers must work with both timestamp-based columns (fetched_at, updated_at)
and date-based columns (as_of_date, publish_date, signal_date).
"""
from __future__ import annotations

from datetime import datetime, date, timedelta, time as dt_time

import pytest
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from services import dedup

# -----------------------------------------------------------------------------
# In-memory test schema (parallel to production tables)
# -----------------------------------------------------------------------------

TestBase = declarative_base()


class FakeFund(TestBase):
    __tablename__ = "fake_funds"
    code = Column(String(20), primary_key=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class FakeIndexConstituent(TestBase):
    __tablename__ = "fake_index_constituents"
    id = Column(Integer, primary_key=True)
    index_code = Column(String(20), nullable=False)
    stock_code = Column(String(20), nullable=False)
    as_of_date = Column(Date, nullable=False)


class FakeGlobalFlashNews(TestBase):
    __tablename__ = "fake_global_flash_news"
    id = Column(Integer, primary_key=True)
    title = Column(String(200))
    fetched_at = Column(DateTime, default=datetime.utcnow)


class FakeHotStockSignal(TestBase):
    __tablename__ = "fake_hot_stocks"
    id = Column(Integer, primary_key=True)
    stock_code = Column(String(20), nullable=False)
    signal_date = Column(Date, nullable=False)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    TestBase.metadata.create_all(engine)
    Session_ = sessionmaker(bind=engine)
    session = Session_()
    yield session
    session.close()


# -----------------------------------------------------------------------------
# Time helper tests
# -----------------------------------------------------------------------------

def test_today_midnight_local_returns_local_midnight():
    """Local midnight must be today's date at 00:00:00, computed in Asia/Shanghai."""
    midnight = dedup.today_midnight_local()
    assert midnight.time() == dt_time.min
    # local date matches what `today_local_date()` returns
    assert midnight.date() == dedup.today_local_date()


def test_today_local_date_is_reasonable():
    """today_local_date must equal the current Asia/Shanghai date."""
    expected = (datetime.utcnow() + timedelta(hours=8)).date()
    assert dedup.today_local_date() == expected


# -----------------------------------------------------------------------------
# Timestamp-based dedup
# -----------------------------------------------------------------------------

def test_already_updated_today_returns_false_when_empty(db):
    assert dedup.already_updated_today(db, FakeFund, "updated_at") is False


def test_already_updated_today_returns_true_after_recent_write(db):
    """A row with updated_at = now() must count as 'already done today'."""
    fund = FakeFund(code="000001")
    fund.updated_at = datetime.utcnow()  # definitely after today's midnight
    db.add(fund)
    db.commit()
    assert dedup.already_updated_today(db, FakeFund, "updated_at") is True


def test_already_updated_today_ignores_old_rows(db):
    """A row from yesterday must NOT count."""
    fund = FakeFund(code="000001")
    fund.updated_at = datetime.utcnow() - timedelta(days=2)
    db.add(fund)
    db.commit()
    assert dedup.already_updated_today(db, FakeFund, "updated_at") is False


def test_already_updated_today_respects_filter(db):
    """With filter_col/val, only matching rows count."""
    today_fund = FakeFund(code="000001")
    today_fund.updated_at = datetime.utcnow()
    other_fund = FakeFund(code="000002")
    other_fund.updated_at = datetime.utcnow()
    db.add_all([today_fund, other_fund])
    db.commit()

    # Filter to code=000001 → True (has today's row)
    assert dedup.already_updated_today(
        db, FakeFund, "updated_at", filter_col="code", filter_val="000001"
    ) is True
    # Filter to code=999999 → False (no matching row)
    assert dedup.already_updated_today(
        db, FakeFund, "updated_at", filter_col="code", filter_val="999999"
    ) is False


# -----------------------------------------------------------------------------
# Date-based dedup
# -----------------------------------------------------------------------------

def test_already_persisted_today_returns_true_for_today_date(db):
    """A row with as_of_date == today must count as 'already done today'."""
    row = FakeIndexConstituent(
        index_code="000300", stock_code="600519", as_of_date=date.today()
    )
    db.add(row)
    db.commit()
    assert dedup.already_persisted_today(
        db, FakeIndexConstituent, "as_of_date", filter_col="index_code", filter_val="000300"
    ) is True


def test_already_persisted_today_ignores_yesterday(db):
    row = FakeIndexConstituent(
        index_code="000300", stock_code="600519", as_of_date=date.today() - timedelta(days=1)
    )
    db.add(row)
    db.commit()
    assert dedup.already_persisted_today(
        db, FakeIndexConstituent, "as_of_date", filter_col="index_code", filter_val="000300"
    ) is False


def test_already_persisted_today_filter_mismatch(db):
    """Filter to a different index_code must return False even if another index has today."""
    row = FakeIndexConstituent(
        index_code="000300", stock_code="600519", as_of_date=date.today()
    )
    db.add(row)
    db.commit()
    assert dedup.already_persisted_today(
        db, FakeIndexConstituent, "as_of_date", filter_col="index_code", filter_val="399967"
    ) is False


# -----------------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------------

def test_already_updated_today_returns_false_for_missing_column(db):
    """If the column doesn't exist on the model, must return False (don't crash)."""
    assert dedup.already_updated_today(db, FakeFund, "nonexistent_column") is False


def test_already_persisted_today_returns_false_for_missing_column(db):
    assert dedup.already_persisted_today(db, FakeFund, "nonexistent_date") is False