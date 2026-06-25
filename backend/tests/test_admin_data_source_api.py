"""admin 数据源 API 集成测试。

验证 /api/admin/data-readiness 和 /api/admin/data-pull-tasks 端点的端到端行为：
  1. 数据就绪查询返回多源状态
  2. 空表时任务历史为空
  3. 有任务记录时能查到
  4. 任务历史支持状态过滤

注意：admin 端点通过 x-admin-token 头鉴权（独立于用户 session）。
"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
import main as _main
from database import Base
from main import app
from models import DataPullTask


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


# ========== 数据就绪测试 ==========

def test_data_readiness(client, fresh_db):
    """数据就绪查询返回多源状态。"""
    res = client.get("/api/admin/data-readiness", params={"as_of_date": "2026-06-24"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["as_of_date"] == "2026-06-24"
    assert "items" in data
    # 至少 4 个数据源（实际 6 个：CN价格/HK价格/US价格/财务数据/成分股/下钻snapshot）
    assert len(data["items"]) >= 4
    # 每项有 source/expected/actual/status 字段
    item = data["items"][0]
    assert "source" in item
    assert "expected" in item
    assert "actual" in item
    assert "status" in item


def test_data_readiness_missing_date(client, fresh_db):
    """缺少 as_of_date 参数返回 422。"""
    res = client.get("/api/admin/data-readiness")
    assert res.status_code == 422


# ========== 任务历史测试 ==========

def test_data_pull_tasks_empty(client, fresh_db):
    """空表时任务历史为空。"""
    res = client.get("/api/admin/data-pull-tasks")
    assert res.status_code == 200
    assert res.json()["total"] == 0
    assert res.json()["items"] == []


def test_data_pull_tasks_with_data(client, fresh_db):
    """有任务记录时能查到。"""
    fresh_db.add(DataPullTask(
        job_id="test_job", job_name="测试任务",
        started_at=datetime.utcnow(), status="SUCCESS",
        records_pulled=10, triggered_by="scheduler",
    ))
    fresh_db.commit()

    res = client.get("/api/admin/data-pull-tasks")
    assert res.status_code == 200, res.text
    assert res.json()["total"] == 1
    item = res.json()["items"][0]
    assert item["job_id"] == "test_job"
    assert item["status"] == "SUCCESS"
    assert item["records_pulled"] == 10
    assert item["triggered_by"] == "scheduler"


def test_data_pull_tasks_filter_by_status(client, fresh_db):
    """任务历史支持按状态过滤。"""
    fresh_db.add(DataPullTask(
        job_id="job_success", job_name="成功任务",
        started_at=datetime.utcnow(), status="SUCCESS",
        records_pulled=10, triggered_by="scheduler",
    ))
    fresh_db.add(DataPullTask(
        job_id="job_failed", job_name="失败任务",
        started_at=datetime.utcnow(), status="FAILED",
        records_pulled=0, triggered_by="scheduler",
        error_message="连接超时",
    ))
    fresh_db.commit()

    # 过滤 SUCCESS
    res = client.get("/api/admin/data-pull-tasks?status=SUCCESS")
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["job_id"] == "job_success"

    # 过滤 FAILED
    res = client.get("/api/admin/data-pull-tasks?status=FAILED")
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["job_id"] == "job_failed"
    assert res.json()["items"][0]["error_message"] == "连接超时"


def test_data_pull_tasks_pagination(client, fresh_db):
    """任务历史支持分页。"""
    for i in range(5):
        fresh_db.add(DataPullTask(
            job_id=f"job_{i}", job_name=f"任务{i}",
            started_at=datetime.utcnow(), status="SUCCESS",
            records_pulled=i, triggered_by="scheduler",
        ))
    fresh_db.commit()

    # 第一页，每页 2 条
    res = client.get("/api/admin/data-pull-tasks?page=1&page_size=2")
    assert res.status_code == 200
    assert res.json()["total"] == 5
    assert len(res.json()["items"]) == 2

    # 第二页
    res = client.get("/api/admin/data-pull-tasks?page=2&page_size=2")
    assert len(res.json()["items"]) == 2
