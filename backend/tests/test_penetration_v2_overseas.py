"""penetration_v2 海外股票解析测试。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models import OverseasShareFinancialSnapshot


@pytest.fixture
def fresh_db():
    """临时文件 SQLite。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    db = TestSession()
    yield db
    db.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_resolve_snapshot_overseas(fresh_db):
    """_resolve_snapshot_for_code 能解析海外股票。"""
    from services.penetration_v2 import _resolve_snapshot_for_code

    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        market="US",
        pe_ttm=28.5,
        pe_ttm_dynamic=29.0,
        sector="Technology",
        industry="Consumer Electronics",
    ))
    fresh_db.commit()

    snap, kind = _resolve_snapshot_for_code("AAPL", date(2026, 6, 24), fresh_db)
    assert snap is not None
    assert kind == "overseas"
    assert snap.pe_ttm_dynamic == 29.0


def test_resolve_dynamic_metrics_overseas(fresh_db):
    """_resolve_dynamic_metrics 能返回海外股票的 dynamic 指标。"""
    from services.penetration_v2 import _resolve_dynamic_metrics

    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        market="US",
        pe_ttm_dynamic=29.0,
        pb_mrq_dynamic=45.0,
        ps_ttm_dynamic=7.5,
        eps_fy1=6.5,
    ))
    fresh_db.commit()

    pe, pb, ps, eps = _resolve_dynamic_metrics("AAPL", date(2026, 6, 24), fresh_db)
    assert pe == 29.0
    assert pb == 45.0
    assert ps == 7.5
    assert eps == 6.5


def test_resolve_industry_overseas(fresh_db):
    """_resolve_industry 海外股票用 sector/industry 替代申万分级。"""
    from services.penetration_v2 import _resolve_industry

    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        market="US",
        sector="Technology",
        industry="Consumer Electronics",
    ))
    fresh_db.commit()

    ind = _resolve_industry("AAPL", date(2026, 6, 24), fresh_db)
    assert ind["swy_l1"] == "Technology"
    assert ind["swy_l2"] == "Consumer Electronics"
    assert ind["chain_position"] == "other"
