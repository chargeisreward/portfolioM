"""测试新增的模型字段和 DataPullTask 表。"""
import os
import tempfile

import pytest
from datetime import datetime
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
from database import Base
from models import SecurityMaster, DataPullTask


@pytest.fixture
def fresh_db():
    """每个测试用独立的临时文件 SQLite（避免 :memory: 多连接隔离问题）。

    参考 backend/tests/test_auth_login.py 中的 fresh_db fixture 实现，
    但本测试只测模型层，不需要 monkeypatch main.get_db。
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


def test_security_master_has_new_fields(fresh_db):
    """SecurityMaster 应有 is_drillable, fund_type, market, index_code 等新字段。"""
    cols = {c["name"] for c in inspect(fresh_db.bind).get_columns("security_master")}
    assert "is_drillable" in cols
    assert "fund_type" in cols
    assert "market" in cols
    assert "index_code" in cols
    assert "index_name" in cols
    assert "benchmark_formula" in cols
    assert "premium_discount" in cols
    assert "security_type" in cols
    assert "note" in cols
    assert "updated_by" in cols


def test_security_master_is_drillable_default_false(fresh_db):
    """新建记录 is_drillable 默认 False。"""
    sm = SecurityMaster(
        security_code="510300.SH",
        security_name="沪深300ETF",
        security_type="fund",
        asset_type="a_share_etf",
        market="CN",
        fund_type="etf",
    )
    fresh_db.add(sm)
    fresh_db.commit()
    assert sm.is_drillable is False


def test_data_pull_task_table_exists(fresh_db):
    """DataPullTask 表应存在。"""
    cols = {c["name"] for c in inspect(fresh_db.bind).get_columns("data_pull_task")}
    assert "id" in cols
    assert "job_id" in cols
    assert "job_name" in cols
    assert "started_at" in cols
    assert "finished_at" in cols
    assert "status" in cols
    assert "records_pulled" in cols
    assert "error_message" in cols
    assert "triggered_by" in cols


def test_data_pull_task_create(fresh_db):
    """能正常创建 DataPullTask 记录。"""
    t = DataPullTask(
        job_id="crawl_cn_prices",
        job_name="拉取A股价格",
        started_at=datetime(2026, 6, 24, 16, 0, 0),
        status="SUCCESS",
        records_pulled=72,
        triggered_by="scheduler",
    )
    fresh_db.add(t)
    fresh_db.commit()
    assert t.id is not None
    assert t.status == "SUCCESS"
