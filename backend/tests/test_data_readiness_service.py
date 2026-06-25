"""data_readiness_service 单元测试。"""
import os
import tempfile
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
from database import Base
from models import Holding, FundDrillSnapshot, IndexConstituentSnapshot
from services.data_readiness_service import get_data_readiness


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


def test_readiness_all_empty(fresh_db):
    """无数据时全部返回 missing。"""
    result = get_data_readiness(fresh_db, date(2026, 6, 24))
    assert isinstance(result, list)
    assert len(result) >= 4
    for item in result:
        assert item["status"] in ("ok", "missing", "partial")
        assert "source" in item
        assert "expected" in item
        assert "actual" in item


def test_readiness_with_drill_snapshot(fresh_db):
    """有下钻 snapshot 时返回 ok。"""
    fresh_db.add(FundDrillSnapshot(
        fund_code="510300.SH", as_of_date=date(2026, 6, 24),
        stock_code="600519.SH", stock_name="贵州茅台",
        weight_pct=5.0, baseline_price=1500.0, current_price=1600.0,
        shares_equivalent=0.001,
    ))
    fresh_db.commit()

    result = get_data_readiness(fresh_db, date(2026, 6, 24))
    drill_item = next(r for r in result if "下钻" in r["source"])
    assert drill_item["actual"] >= 1


def test_readiness_with_constituents(fresh_db):
    """有成分股数据时返回 ok。"""
    fresh_db.add(IndexConstituentSnapshot(
        as_of_date=date(2026, 6, 24), index_code="000300",
        stock_code="600519.SH", stock_name="贵州茅台", weight=5.0,
    ))
    fresh_db.commit()

    result = get_data_readiness(fresh_db, date(2026, 6, 24))
    const_item = next(r for r in result if "成分股" in r["source"])
    assert const_item["actual"] >= 1
