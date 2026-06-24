"""data_pull_task_service 单元测试。"""
import os
import tempfile

import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
from database import Base
from models import DataPullTask
from services.data_pull_task_service import (
    record_task_start,
    record_task_finish,
    list_tasks,
)


@pytest.fixture
def fresh_db():
    """每个测试用独立的临时文件 SQLite（避免 :memory: 多连接隔离问题）。

    参考 backend/tests/test_security_master_service.py 中的 fresh_db fixture 实现，
    但本测试只测 service 层，不需要 monkeypatch main.get_db。
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_record_task_start(fresh_db):
    """record_task_start 创建 RUNNING 状态记录。"""
    task = record_task_start(fresh_db, "crawl_cn_prices", "拉取A股价格", "scheduler")
    assert task["job_id"] == "crawl_cn_prices"
    assert task["status"] == "RUNNING"
    assert task["started_at"] is not None


def test_record_task_finish_success(fresh_db):
    """record_task_finish 更新为 SUCCESS。"""
    task = record_task_start(fresh_db, "crawl_cn_prices", "拉取A股价格", "scheduler")
    finished = record_task_finish(fresh_db, task["id"], "SUCCESS", records_pulled=72)
    assert finished["status"] == "SUCCESS"
    assert finished["records_pulled"] == 72
    assert finished["finished_at"] is not None


def test_record_task_finish_failed(fresh_db):
    """record_task_finish 更新为 FAILED + error_message。"""
    task = record_task_start(fresh_db, "crawl_cn_prices", "拉取A股价格", "scheduler")
    finished = record_task_finish(fresh_db, task["id"], "FAILED", error_message="timeout")
    assert finished["status"] == "FAILED"
    assert finished["error_message"] == "timeout"


def test_list_tasks_filter_by_status(fresh_db):
    """list_tasks 支持按 status 过滤。"""
    t1 = record_task_start(fresh_db, "job1", "任务1", "scheduler")
    record_task_finish(fresh_db, t1["id"], "SUCCESS")
    t2 = record_task_start(fresh_db, "job2", "任务2", "scheduler")

    all_tasks = list_tasks(fresh_db)
    assert len(all_tasks["items"]) == 2

    running_only = list_tasks(fresh_db, status="RUNNING")
    assert len(running_only["items"]) == 1
    assert running_only["items"][0]["job_id"] == "job2"
