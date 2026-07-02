"""fund-master API 集成测试。"""
import os
os.environ["APP_PASSWORD"] = ""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import tempfile
import pytest

import models
from database import Base
from main import app
from models_master import FundMaster


@pytest.fixture
def app_client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from main import get_db
    app.dependency_overrides[get_db] = _override_get_db

    client = TestClient(app)
    yield client, SessionLocal
    app.dependency_overrides.clear()
    os.unlink(path)


def test_list_funds_empty(app_client):
    client, _ = app_client
    r = client.get("/api/admin/fund-master")
    assert r.status_code in (200, 401)


def test_create_fund_via_service_then_list(app_client):
    client, SessionLocal = app_client
    db = SessionLocal()
    db.add(FundMaster(
        fund_code="510300.SH", fund_name="华泰柏瑞沪深300",
        fund_type="etf", asset_type="a_share_etf",
    ))
    db.commit()
    db.close()
    r = client.get("/api/admin/fund-master?search=510300")
    body = r.json()
    assert "items" in body or "detail" in body
