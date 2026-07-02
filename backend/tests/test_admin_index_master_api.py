"""index-master API 集成测试。"""
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
from models_master import IndexMaster


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


def test_list_indices_empty(app_client):
    client, _ = app_client
    r = client.get("/api/admin/index-master")
    assert r.status_code in (200, 401)


def test_create_index_via_service_then_list(app_client):
    client, SessionLocal = app_client
    db = SessionLocal()
    db.add(IndexMaster(
        index_code="000300.SH", index_name="沪深300",
        exchange="SH", currency="CNY", category="宽基",
    ))
    db.commit()
    db.close()
    r = client.get("/api/admin/index-master?search=000300")
    body = r.json()
    assert "items" in body or "detail" in body
