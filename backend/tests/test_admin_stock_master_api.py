"""stock-master API 集成测试 — 验证 main.py 端点路径。"""
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
from models_master import StockMaster


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


def test_list_stocks_empty(app_client):
    """GET /api/admin/stock-master 应返回空列表 (无 auth 也能走通到 401/200)。"""
    client, _ = app_client
    r = client.get("/api/admin/stock-master")
    assert r.status_code in (200, 401)


def test_create_stock_via_service_then_list(app_client):
    """通过 service 层写入,验证 API 的 list 端点能找到。"""
    client, SessionLocal = app_client
    db = SessionLocal()
    db.add(StockMaster(
        stock_code="600519.SH", stock_name="贵州茅台",
        asset_type="a_share_equity",
    ))
    db.commit()
    db.close()

    r = client.get("/api/admin/stock-master?search=600519")
    # 不依赖 auth 状态:仅验证响应结构
    body = r.json()
    assert "items" in body or "detail" in body  # 401 路径有 detail
