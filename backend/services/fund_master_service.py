"""基金主数据 service — CRUD。"""
from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import text

from models_master import FundMaster


def _to_dict(fm: FundMaster) -> dict:
    return {
        "fund_code": fm.fund_code,
        "fund_name": fm.fund_name,
        "fund_type": fm.fund_type,
        "currency": fm.currency,
        "asset_type": fm.asset_type,
        "benchmark_formula": fm.benchmark_formula,
        "is_drillable": fm.is_drillable,
        "note": fm.note,
        "updated_at": fm.updated_at.isoformat() if fm.updated_at else None,
    }


def list_funds(
    db: Session, asset_type: str | None = None, fund_type: str | None = None,
    search: str | None = None, page: int = 1, page_size: int = 50,
) -> dict:
    q = db.query(FundMaster)
    if asset_type:
        q = q.filter(FundMaster.asset_type == asset_type)
    if fund_type:
        q = q.filter(FundMaster.fund_type == fund_type)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (FundMaster.fund_code.ilike(like))
            | (FundMaster.fund_name.ilike(like))
        )
    total = q.count()
    rows = q.order_by(FundMaster.fund_code).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return {
        "items": [_to_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


def get_fund(db: Session, code: str) -> dict | None:
    fm = db.query(FundMaster).filter_by(fund_code=code).first()
    return _to_dict(fm) if fm else None


def create_fund(db: Session, data: dict) -> dict:
    fm = FundMaster(**{k: v for k, v in data.items() if k in FundMaster.__table__.columns})
    db.add(fm)
    db.commit()
    db.refresh(fm)
    return _to_dict(fm)


def update_fund(db: Session, code: str, data: dict) -> dict | None:
    fm = db.query(FundMaster).filter_by(fund_code=code).first()
    if not fm:
        return None
    _ALLOWED = {c.name for c in FundMaster.__table__.columns}
    for k, v in data.items():
        if k in _ALLOWED and k != "fund_code":
            setattr(fm, k, v)
    db.commit()
    db.refresh(fm)
    return _to_dict(fm)


def delete_fund(db: Session, code: str) -> bool:
    n = db.execute(text(
        "SELECT COUNT(*) FROM holdings WHERE security_code=:c"
    ), {"c": code}).scalar() or 0
    if n > 0:
        raise ValueError(f"无法删除: 该基金有 {n} 条持仓记录")
    fm = db.query(FundMaster).filter_by(fund_code=code).first()
    if not fm:
        return False
    db.delete(fm)
    db.commit()
    return True
