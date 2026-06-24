"""数据就绪检查 service — 检查各数据源在指定日期的就绪状态。

依赖：Holding, FundDrillSnapshot, IndexConstituentSnapshot, AShareFinancialSnapshot, HKShareFinancialSnapshot, FundDailyNav
"""
from __future__ import annotations

import logging
from datetime import date as _date
from sqlalchemy.orm import Session

from models import (
    Holding, FundDrillSnapshot, IndexConstituentSnapshot,
    AShareFinancialSnapshot, HKShareFinancialSnapshot, FundDailyNav,
)

logger = logging.getLogger(__name__)


def _check_cn_prices(db: Session, as_of: _date) -> dict:
    """检查 CN 价格就绪状态。"""
    cn_codes = {r[0] for r in db.query(Holding.security_code).filter(
        Holding.security_code.like("%.SH") | Holding.security_code.like("%.SZ")
    ).all()}
    if not cn_codes:
        return {"source": "CN价格", "expected": 0, "actual": 0, "status": "ok"}
    actual = db.query(FundDailyNav).filter(
        FundDailyNav.trade_date == as_of,
        FundDailyNav.fund_code.in_(cn_codes),
    ).count()
    expected = len(cn_codes)
    return {
        "source": "CN价格",
        "expected": expected,
        "actual": actual,
        "status": "ok" if actual >= expected else ("missing" if actual == 0 else "partial"),
    }


def _check_drill_snapshot(db: Session, as_of: _date) -> dict:
    """检查下钻 snapshot 就绪状态。"""
    actual = db.query(FundDrillSnapshot).filter(FundDrillSnapshot.as_of_date == as_of).count()
    return {
        "source": "下钻snapshot",
        "expected": 1,
        "actual": actual,
        "status": "ok" if actual > 0 else "missing",
    }


def _check_constituents(db: Session, as_of: _date) -> dict:
    """检查成分股就绪状态。"""
    actual = db.query(IndexConstituentSnapshot).filter(IndexConstituentSnapshot.as_of_date == as_of).count()
    return {
        "source": "成分股",
        "expected": 1,
        "actual": actual,
        "status": "ok" if actual > 0 else "missing",
    }


def _check_financials(db: Session, as_of: _date) -> dict:
    """检查财务数据就绪状态。"""
    a_count = db.query(AShareFinancialSnapshot).filter(AShareFinancialSnapshot.as_of_date == as_of).count()
    h_count = db.query(HKShareFinancialSnapshot).filter(HKShareFinancialSnapshot.as_of_date == as_of).count()
    total = a_count + h_count
    return {
        "source": "财务数据",
        "expected": 1,
        "actual": total,
        "status": "ok" if total > 0 else "missing",
    }


def _check_hk_prices(db: Session, as_of: _date) -> dict:
    """检查 HK 价格就绪状态。"""
    hk_codes = {r[0] for r in db.query(Holding.security_code).filter(Holding.security_code.like("%.HK")).all()}
    return {
        "source": "HK价格",
        "expected": len(hk_codes),
        "actual": 0,  # TODO: 接入 HK 价格表后补充
        "status": "ok" if len(hk_codes) == 0 else "missing",
    }


def _check_us_prices(db: Session, as_of: _date) -> dict:
    """检查 US 价格就绪状态。"""
    us_codes = {r[0] for r in db.query(Holding.security_code).filter(
        ~Holding.security_code.like("%.SH") & ~Holding.security_code.like("%.SZ") & ~Holding.security_code.like("%.HK") & ~Holding.security_code.like("%.OF")
    ).all()}
    return {
        "source": "US价格",
        "expected": len(us_codes),
        "actual": 0,  # TODO: 接入 US 价格表后补充
        "status": "ok" if len(us_codes) == 0 else "missing",
    }


def get_data_readiness(db: Session, as_of: _date) -> list[dict]:
    """检查各数据源在 as_of 的就绪状态。返回 [{source, expected, actual, status}, ...]"""
    return [
        _check_cn_prices(db, as_of),
        _check_hk_prices(db, as_of),
        _check_us_prices(db, as_of),
        _check_financials(db, as_of),
        _check_constituents(db, as_of),
        _check_drill_snapshot(db, as_of),
    ]
