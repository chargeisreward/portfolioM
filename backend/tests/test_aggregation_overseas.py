"""穿透分析集成测试：resolve_dynamic_metrics_for_stock 支持海外市场。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models import OverseasShareFinancialSnapshot


@pytest.fixture
def fresh_db():
    """临时文件 SQLite。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    db = TestSession()
    yield db
    db.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_resolve_dynamic_metrics_overseas(fresh_db):
    """海外股票 PE/PB/PS 解析。"""
    from services.aggregation import resolve_dynamic_metrics_for_stock

    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        market="US",
        pe_ttm=28.5,
        pb_mrq=45.2,
        ps_ttm=7.8,
        pe_ttm_dynamic=29.0,
        pb_mrq_dynamic=45.5,
        ps_ttm_dynamic=7.9,
    ))
    fresh_db.commit()

    pe, pb, ps = resolve_dynamic_metrics_for_stock(fresh_db, "AAPL")
    assert pe == 29.0
    assert pb == 45.5
    assert ps == 7.9


def test_resolve_dynamic_metrics_overseas_not_found(fresh_db):
    """海外股票无数据时返回 None。"""
    from services.aggregation import resolve_dynamic_metrics_for_stock

    pe, pb, ps = resolve_dynamic_metrics_for_stock(fresh_db, "NONEXIST")
    assert pe is None
    assert pb is None
    assert ps is None


def test_resolve_dynamic_metrics_overseas_latest(fresh_db):
    """取最新快照。"""
    from services.aggregation import resolve_dynamic_metrics_for_stock

    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 20),
        stock_code="AAPL",
        market="US",
        pe_ttm_dynamic=28.0,
        pb_mrq_dynamic=44.0,
        ps_ttm_dynamic=7.0,
    ))
    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        market="US",
        pe_ttm_dynamic=29.0,
        pb_mrq_dynamic=45.0,
        ps_ttm_dynamic=7.5,
    ))
    fresh_db.commit()

    pe, pb, ps = resolve_dynamic_metrics_for_stock(fresh_db, "AAPL")
    assert pe == 29.0
    assert pb == 45.0
    assert ps == 7.5
