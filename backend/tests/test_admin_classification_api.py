"""classification API 集成测试。"""
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
from models_master import Classification


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


def test_list_classifications_endpoint_exists(app_client):
    client, _ = app_client
    r = client.get("/api/admin/classification?dimension=theme")
    assert r.status_code in (200, 401, 422)


def test_create_classification_via_service_then_query(app_client):
    client, SessionLocal = app_client
    db = SessionLocal()
    db.add(Classification(dimension="theme", code="dividend", display_label="红利"))
    db.commit()
    db.close()
    # 通过 API 列出来验证 path 已注册 (不依赖 auth)
    r = client.get("/api/admin/classification?dimension=theme&is_active=true")
    assert r.status_code in (200, 401)
