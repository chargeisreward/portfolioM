"""股票主数据 service — CRUD。"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from models_master import StockMaster

logger = logging.getLogger(__name__)


def _to_dict(sm: StockMaster) -> dict:
    return {
        "stock_code": sm.stock_code,
        "stock_name": sm.stock_name,
        "exchange": sm.exchange,
        "currency": sm.currency,
        "asset_type": sm.asset_type,
        "is_listed": sm.is_listed,
        "is_drillable": sm.is_drillable,
        "note": sm.note,
        "updated_at": sm.updated_at.isoformat() if sm.updated_at else None,
    }


def list_stocks(
    db: Session, asset_type: str | None = None, market: str | None = None,
    search: str | None = None, page: int = 1, page_size: int = 50,
) -> dict:
    q = db.query(StockMaster)
    if asset_type:
        q = q.filter(StockMaster.asset_type == asset_type)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (StockMaster.stock_code.ilike(like))
            | (StockMaster.stock_name.ilike(like))
        )
    total = q.count()
    rows = q.order_by(StockMaster.stock_code).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return {
        "items": [_to_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


def get_stock(db: Session, code: str) -> dict | None:
    sm = db.query(StockMaster).filter_by(stock_code=code).first()
    return _to_dict(sm) if sm else None


def create_stock(db: Session, data: dict) -> dict:
    sm = StockMaster(**{k: v for k, v in data.items() if k in StockMaster.__table__.columns})
    db.add(sm)
    db.commit()
    db.refresh(sm)
    return _to_dict(sm)


def update_stock(db: Session, code: str, data: dict) -> dict | None:
    sm = db.query(StockMaster).filter_by(stock_code=code).first()
    if not sm:
        return None
    _ALLOWED = {c.name for c in StockMaster.__table__.columns}
    for k, v in data.items():
        if k in _ALLOWED and k != "stock_code":
            setattr(sm, k, v)
    db.commit()
    db.refresh(sm)
    return _to_dict(sm)


def delete_stock(db: Session, code: str) -> bool:
    n = db.execute(text(
        "SELECT COUNT(*) FROM holdings WHERE security_code=:c"
    ), {"c": code}).scalar() or 0
    if n > 0:
        raise ValueError(f"无法删除: 该股票有 {n} 条持仓记录")
    sm = db.query(StockMaster).filter_by(stock_code=code).first()
    if not sm:
        return False
    db.delete(sm)
    db.commit()
    return True