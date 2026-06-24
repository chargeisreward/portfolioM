"""financial_upload_service 单元测试。"""
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
from models import AShareFinancialSnapshot, HKShareFinancialSnapshot


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


def test_upsert_financial_single_a_share(fresh_db):
    """单条写入 A 股财务数据。"""
    from services.financial_upload_service import upsert_financial_single

    result = upsert_financial_single(fresh_db, {
        "stock_code": "600519.SH",
        "stock_name": "贵州茅台",
        "pe_ttm": 30.5,
        "pb_mrq": 10.2,
        "ps_ttm": 15.0,
        "dividend_yield": 1.5,
        "market_cap": 20000,
        "as_of_date": "2026-06-24",
    })

    assert result["status"] == "ok"
    assert result["market"] == "CN"

    # 验证数据库
    snap = fresh_db.query(AShareFinancialSnapshot).filter(
        AShareFinancialSnapshot.stock_code == "600519.SH",
        AShareFinancialSnapshot.as_of_date == date(2026, 6, 24),
    ).first()
    assert snap is not None
    assert snap.pe_ttm == 30.5
    assert snap.pb_mrq == 10.2


def test_upsert_financial_single_hk(fresh_db):
    """单条写入港股财务数据。"""
    from services.financial_upload_service import upsert_financial_single

    result = upsert_financial_single(fresh_db, {
        "stock_code": "00700.HK",
        "stock_name": "腾讯控股",
        "pe_ttm": 25.0,
        "as_of_date": "2026-06-24",
    })

    assert result["status"] == "ok"
    assert result["market"] == "HK"

    snap = fresh_db.query(HKShareFinancialSnapshot).filter(
        HKShareFinancialSnapshot.stock_code == "00700.HK",
    ).first()
    assert snap is not None
    assert snap.pe_ttm == 25.0


def test_upsert_financial_single_unsupported_code(fresh_db):
    """不支持的代码后缀返回错误。"""
    from services.financial_upload_service import upsert_financial_single

    with pytest.raises(ValueError, match="不支持"):
        upsert_financial_single(fresh_db, {
            "stock_code": "000001.OF",
            "as_of_date": "2026-06-24",
        })


def test_upsert_financial_single_update_existing(fresh_db):
    """更新已存在的记录。"""
    from services.financial_upload_service import upsert_financial_single

    # 第一次写入
    upsert_financial_single(fresh_db, {
        "stock_code": "600519.SH",
        "pe_ttm": 30.0,
        "as_of_date": "2026-06-24",
    })

    # 第二次更新
    upsert_financial_single(fresh_db, {
        "stock_code": "600519.SH",
        "pe_ttm": 35.0,
        "as_of_date": "2026-06-24",
    })

    count = fresh_db.query(AShareFinancialSnapshot).filter(
        AShareFinancialSnapshot.stock_code == "600519.SH",
        AShareFinancialSnapshot.as_of_date == date(2026, 6, 24),
    ).count()
    assert count == 1  # 不应有重复

    snap = fresh_db.query(AShareFinancialSnapshot).filter(
        AShareFinancialSnapshot.stock_code == "600519.SH"
    ).first()
    assert snap.pe_ttm == 35.0
