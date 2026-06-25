"""测试 data_gap_detector — 3 类缺口"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
from database import Base
from models import (
    User, Holding, FundIndexMap, IndexClassification,
    IndexConstituentSnapshot, AnalystCompanyReport, DataGapReport, AssetType,
)
from services.data_gap_detector import detect_all_gaps


@pytest.fixture
def db(monkeypatch):
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
    yield TestSession()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try: os.unlink(path)
    except OSError: pass


def test_stock_report_gap_detected(db):
    u = User(username="u1", password_hash="x", is_active=True)
    db.add(u); db.commit()
    db.add(Holding(user_id=u.id, security_code="000001", security_name="平安",
                   quantity=1, price=100, currency="CNY", amount=100, amount_cny=100,
                   asset_type=AssetType.A_SHARE_EQUITY.value))
    db.commit()
    detect_all_gaps(db, today=date(2026, 7, 5))
    gaps = db.query(DataGapReport).filter(DataGapReport.gap_type == "stock_report").all()
    assert any(g.stock_code == "000001" for g in gaps)


def test_stock_report_no_gap_when_report_exists(db):
    u = User(username="u1", password_hash="x", is_active=True)
    db.add(u); db.commit()
    db.add(Holding(user_id=u.id, security_code="000001", security_name="平安",
                   quantity=1, price=100, currency="CNY", amount=100, amount_cny=100,
                   asset_type=AssetType.A_SHARE_EQUITY.value))
    db.add(AnalystCompanyReport(stock_code="000001", stock_name="平安银行"))
    db.commit()
    detect_all_gaps(db, today=date(2026, 7, 5))
    gaps = db.query(DataGapReport).filter(
        DataGapReport.gap_type == "stock_report",
        DataGapReport.stock_code == "000001"
    ).all()
    assert len(gaps) == 0


def test_index_classification_gap_detected(db):
    fmap = FundIndexMap(fund_code="F1", index_code="000300", as_of_date=date(2026, 1, 1),
                        fund_name="沪深300基金", index_name="沪深300")
    db.add(fmap); db.commit()
    detect_all_gaps(db, today=date(2026, 7, 5))
    gaps = db.query(DataGapReport).filter(
        DataGapReport.gap_type == "index_classification",
        DataGapReport.index_code == "000300"
    ).all()
    assert len(gaps) >= 1


def test_index_constituent_gap_detected_in_july(db):
    fmap = FundIndexMap(fund_code="F1", index_code="000300", as_of_date=date(2026, 1, 1),
                        fund_name="沪深300基金", index_name="沪深300")
    db.add(fmap); db.commit()
    detect_all_gaps(db, today=date(2026, 7, 5))
    gaps = db.query(DataGapReport).filter(
        DataGapReport.gap_type == "index_constituent",
        DataGapReport.index_code == "000300",
        DataGapReport.as_of_date == date(2026, 6, 30),
    ).all()
    assert len(gaps) >= 1


def test_no_duplicate_open_gaps(db):
    u = User(username="u1", password_hash="x", is_active=True)
    db.add(u); db.commit()
    db.add(Holding(user_id=u.id, security_code="000001", security_name="平安",
                   quantity=1, price=100, currency="CNY", amount=100, amount_cny=100,
                   asset_type=AssetType.A_SHARE_EQUITY.value))
    db.commit()
    detect_all_gaps(db, today=date(2026, 7, 5))
    detect_all_gaps(db, today=date(2026, 7, 5))
    open_gaps = db.query(DataGapReport).filter(
        DataGapReport.gap_type == "stock_report",
        DataGapReport.status == "OPEN"
    ).all()
    codes = [g.stock_code for g in open_gaps]
    assert len(codes) == len(set(codes))


def test_low_weight_holding_no_gap(db):
    """占比 < 0.8% 不应被报告"""
    u = User(username="u1", password_hash="x", is_active=True)
    db.add(u); db.commit()
    # 大额 + 小额：0.1 / 1000 = 0.01% < 0.8%
    db.add(Holding(user_id=u.id, security_code="BIG", security_name="big",
                   quantity=1, price=1000, currency="CNY", amount=1000, amount_cny=1000,
                   asset_type=AssetType.A_SHARE_EQUITY.value))
    db.add(Holding(user_id=u.id, security_code="TINY", security_name="tiny",
                   quantity=1, price=0.1, currency="CNY", amount=0.1, amount_cny=0.1,
                   asset_type=AssetType.A_SHARE_EQUITY.value))
    db.commit()
    detect_all_gaps(db, today=date(2026, 7, 5))
    stock_gaps = db.query(DataGapReport).filter(
        DataGapReport.gap_type == "stock_report"
    ).all()
    # 0.1 元 远小于 0.8% 阈值
    assert not any(g.stock_code == "TINY" for g in stock_gaps)
    # BIG 1000 也不是，因为 1000/1000.1 = 99.9%，那 BIG 也应该被报
    # 实际上 BIG 占比 99.9% ≥ 0.8%，所以 BIG 应当被报
    assert any(g.stock_code == "BIG" for g in stock_gaps)
