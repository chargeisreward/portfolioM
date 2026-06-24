"""overseas_financial_service 单元测试。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from unittest.mock import patch
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


def test_upsert_overseas_financial_create(fresh_db):
    """单条创建。"""
    from services.overseas_financial_service import upsert_overseas_financial

    result = upsert_overseas_financial(fresh_db, {
        "stock_code": "AAPL",
        "stock_name": "Apple Inc",
        "market": "US",
        "pe_ttm": 28.5,
        "pb_mrq": 45.2,
        "ps_ttm": 7.8,
        "as_of_date": "2026-06-24",
    })

    assert result["status"] == "ok"
    assert result["market"] == "US"

    snap = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "AAPL",
        OverseasShareFinancialSnapshot.as_of_date == date(2026, 6, 24),
    ).first()
    assert snap is not None
    assert snap.pe_ttm == 28.5
    assert snap.pb_mrq == 45.2
    assert snap.ps_ttm == 7.8


def test_upsert_overseas_financial_update(fresh_db):
    """单条更新。"""
    from services.overseas_financial_service import upsert_overseas_financial

    upsert_overseas_financial(fresh_db, {
        "stock_code": "AAPL",
        "pe_ttm": 28.0,
        "as_of_date": "2026-06-24",
    })

    upsert_overseas_financial(fresh_db, {
        "stock_code": "AAPL",
        "pe_ttm": 30.0,
        "as_of_date": "2026-06-24",
    })

    count = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "AAPL",
    ).count()
    assert count == 1

    snap = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "AAPL",
    ).first()
    assert snap.pe_ttm == 30.0


def test_upsert_overseas_financial_market_infer(fresh_db):
    """market 未提供时从 ticker 推断。"""
    from services.overseas_financial_service import upsert_overseas_financial

    result = upsert_overseas_financial(fresh_db, {
        "stock_code": "005930.KS",
        "pe_ttm": 15.0,
        "as_of_date": "2026-06-24",
    })

    assert result["market"] == "KR"

    snap = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "005930.KS",
    ).first()
    assert snap.market == "KR"


def test_fetch_and_store_overseas_financials(fresh_db):
    """批量获取（mock yfinance）。"""
    from services.overseas_financial_service import fetch_and_store_overseas_financials

    mock_yf_info = {
        "code": "AAPL",
        "name": "Apple Inc",
        "market": "US",
        "pe_ttm": 28.5,
        "pb_mrq": 45.2,
        "ps_ttm": 7.8,
        "market_cap_b": 30000.0,
        "dividend_yield": 0.005,
        "eps_fy1": 6.5,
        "sector": "Technology",
        "industry": "Consumer Electronics",
    }

    with patch("services.overseas_financial_service.fetch_yfinance_info", return_value=mock_yf_info):
        with patch("services.overseas_financial_service.time"):
            result = fetch_and_store_overseas_financials(
                fresh_db, ["AAPL"], date(2026, 6, 24)
            )

    assert result["status"] == "ok"
    assert result["fetched"] == 1
    assert result["stored"] == 1

    snap = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "AAPL",
    ).first()
    assert snap is not None
    assert snap.sector == "Technology"


def test_fetch_and_store_overseas_financials_empty(fresh_db):
    """yfinance 返回空时记录错误。"""
    from services.overseas_financial_service import fetch_and_store_overseas_financials

    with patch("services.overseas_financial_service.fetch_yfinance_info", return_value=None):
        with patch("services.overseas_financial_service.time"):
            result = fetch_and_store_overseas_financials(
                fresh_db, ["BADCODE"], date(2026, 6, 24)
            )

    assert result["fetched"] == 0
    assert result["stored"] == 0
    assert len(result["errors"]) == 1
