"""admin 证券主数据 API 集成测试。

验证 /api/admin/security-master 和 /api/admin/fund-index-map 端点的端到端行为：
  1. 空表查询返回空列表
  2. 创建后能查到
  3. 更新字段生效
  4. 有持仓时删除返回 400
  5. 从持仓同步
  6. 基金-指数映射 CRUD

注意：admin 端点通过 x-admin-token 头鉴权（独立于用户 session）。
"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
import main as _main
from database import Base
from main import app
from models import SecurityMaster, Holding, FundIndexMap


# ========== fixtures ==========

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
    """TestClient，所有 admin 请求自动带 x-admin-token 头。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    return TestClient(app, headers={"x-admin-token": admin_token})


# ========== 证券主数据测试 ==========

def test_list_security_master_empty(client, fresh_db):
    """空表时返回空列表。"""
    res = client.get("/api/admin/security-master")
    assert res.status_code == 200
    data = res.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_create_and_get_security(client, fresh_db):
    """创建后能查到。"""
    res = client.post("/api/admin/security-master", json={
        "security_code": "510300.SH",
        "security_name": "沪深300ETF",
        "security_type": "fund",
        "asset_type": "a_share_etf",
        "market": "CN",
        "is_drillable": True,
    })
    assert res.status_code == 200, res.text
    assert res.json()["security_code"] == "510300.SH"

    res = client.get("/api/admin/security-master")
    assert res.json()["total"] == 1


def test_update_security(client, fresh_db):
    """更新 is_drillable。"""
    client.post("/api/admin/security-master", json={
        "security_code": "510300.SH",
        "security_type": "fund",
        "is_drillable": False,
    })
    res = client.put("/api/admin/security-master/510300.SH", json={"is_drillable": True})
    assert res.status_code == 200, res.text
    assert res.json()["is_drillable"] is True


def test_delete_security_blocked(client, fresh_db):
    """有持仓时删除返回 400。"""
    client.post("/api/admin/security-master", json={
        "security_code": "510300.SH",
        "security_type": "fund",
    })
    # 添加持仓
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", quantity=100,
        asset_type="a_share_etf",
    ))
    fresh_db.commit()

    res = client.delete("/api/admin/security-master/510300.SH")
    assert res.status_code == 400


def test_delete_security_success(client, fresh_db):
    """无持仓时删除成功。"""
    client.post("/api/admin/security-master", json={
        "security_code": "510300.SH",
        "security_type": "fund",
    })
    res = client.delete("/api/admin/security-master/510300.SH")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # 确认已删除
    res = client.get("/api/admin/security-master")
    assert res.json()["total"] == 0


def test_sync_from_holdings(client, fresh_db):
    """同步持仓。"""
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    res = client.post("/api/admin/security-master/sync-from-holdings")
    assert res.status_code == 200, res.text
    assert res.json()["synced"] == 1

    # 确认同步后能查到
    res = client.get("/api/admin/security-master")
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["security_code"] == "510300.SH"


def test_list_security_with_filters(client, fresh_db):
    """列表支持按 type/market/drillable 过滤。"""
    client.post("/api/admin/security-master", json={
        "security_code": "510300.SH",
        "security_name": "沪深300ETF",
        "security_type": "fund",
        "market": "CN",
        "is_drillable": True,
    })
    client.post("/api/admin/security-master", json={
        "security_code": "600519.SH",
        "security_name": "贵州茅台",
        "security_type": "stock",
        "market": "CN",
        "is_drillable": False,
    })

    # 按 type 过滤
    res = client.get("/api/admin/security-master?type=fund")
    assert res.json()["total"] == 1

    # 按 drillable 过滤
    res = client.get("/api/admin/security-master?drillable=true")
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["security_code"] == "510300.SH"


# ========== 基金-指数映射测试 ==========

def test_fund_index_map_crud(client, fresh_db):
    """基金-指数映射 CRUD。"""
    # 创建
    res = client.post("/api/admin/fund-index-map", json={
        "fund_code": "510300.SH",
        "fund_name": "沪深300ETF",
        "index_code": "000300.SH",
        "index_name": "沪深300",
        "as_of_date": "2026-06-24",
    })
    assert res.status_code == 200, res.text

    # 查询
    res = client.get("/api/admin/fund-index-map")
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["fund_code"] == "510300.SH"

    # 更新
    res = client.put("/api/admin/fund-index-map/510300.SH/2026-06-24", json={
        "index_name": "沪深300指数",
    })
    assert res.status_code == 200, res.text

    # 确认更新生效
    res = client.get("/api/admin/fund-index-map")
    assert res.json()["items"][0]["index_name"] == "沪深300指数"

    # 删除
    res = client.delete("/api/admin/fund-index-map/510300.SH/2026-06-24")
    assert res.status_code == 200

    # 确认已删除
    res = client.get("/api/admin/fund-index-map")
    assert res.json()["total"] == 0


def test_fund_index_map_search(client, fresh_db):
    """基金-指数映射支持搜索。"""
    client.post("/api/admin/fund-index-map", json={
        "fund_code": "510300.SH",
        "fund_name": "沪深300ETF",
        "index_code": "000300.SH",
        "index_name": "沪深300",
        "as_of_date": "2026-06-24",
    })
    client.post("/api/admin/fund-index-map", json={
        "fund_code": "159915.SZ",
        "fund_name": "创业板ETF",
        "index_code": "399006.SZ",
        "index_name": "创业板指",
        "as_of_date": "2026-06-24",
    })

    # 搜索 "沪深"
    res = client.get("/api/admin/fund-index-map?search=沪深")
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["fund_code"] == "510300.SH"
