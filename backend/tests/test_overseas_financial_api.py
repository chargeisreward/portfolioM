"""海外财务数据 API 集成测试。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
import main as _main
from database import Base
from main import app
from models import OverseasShareFinancialSnapshot, Holding


@pytest.fixture
def fresh_db(monkeypatch):
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
    monkeypatch.setattr(_database, "engine", test_engine)
    monkeypatch.setattr(_database, "SessionLocal", TestSession)

    def _patched_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(_main, "get_db", _patched_get_db)
    yield TestSession()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def client(fresh_db):
    """TestClient，带 x-admin-token 头。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    return TestClient(app, headers={"x-admin-token": admin_token})


def test_list_overseas_financials_empty(client, fresh_db):
    """空表查询。"""
    res = client.get("/api/admin/overseas-financials")
    assert res.status_code == 200
    assert res.json()["items"] == []
    assert res.json()["total"] == 0


def test_list_overseas_financials_with_data(client, fresh_db):
    """有数据时查询。"""
    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        stock_name="Apple Inc",
        market="US",
        pe_ttm=28.5,
        pb_mrq=45.2,
        ps_ttm=7.8,
    ))
    fresh_db.commit()

    res = client.get("/api/admin/overseas-financials")
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["stock_code"] == "AAPL"
    assert res.json()["items"][0]["market"] == "US"


def test_list_overseas_financials_filter_market(client, fresh_db):
    """按 market 过滤。"""
    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1, as_of_date=date(2026, 6, 24),
        stock_code="AAPL", market="US", pe_ttm=28.5,
    ))
    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1, as_of_date=date(2026, 6, 24),
        stock_code="005930.KS", market="KR", pe_ttm=15.0,
    ))
    fresh_db.commit()

    res = client.get("/api/admin/overseas-financials?market=KR")
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["stock_code"] == "005930.KS"


def test_refresh_overseas_financials(client, fresh_db):
    """手动触发更新（mock yfinance）。"""
    # 添加一个 US 持仓
    fresh_db.add(Holding(
        user_id=1, security_code="AAPL", quantity=100,
        asset_type="us_stock",
    ))
    fresh_db.commit()

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
            res = client.post("/api/admin/overseas-financials/refresh")

    assert res.status_code == 200, res.text
    assert res.json()["fetched"] == 1
    assert res.json()["stored"] == 1


def test_refresh_overseas_financials_no_holdings(client, fresh_db):
    """无海外持仓时返回提示。"""
    res = client.post("/api/admin/overseas-financials/refresh")
    assert res.status_code == 200
    assert res.json()["fetched"] == 0
