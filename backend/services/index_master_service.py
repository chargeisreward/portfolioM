"""指数主数据 service — CRUD。"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from models_master import IndexMaster


def _to_dict(im: IndexMaster) -> dict:
    return {
        "index_code": im.index_code,
        "index_name": im.index_name,
        "exchange": im.exchange,
        "currency": im.currency,
        "category": im.category,
        "constituent_count": im.constituent_count,
        "source": im.source,
        "is_active": im.is_active,
        "first_pulled_at": im.first_pulled_at.isoformat() if im.first_pulled_at else None,
        "last_pulled_at": im.last_pulled_at.isoformat() if im.last_pulled_at else None,
        "last_verified_at": im.last_verified_at.isoformat() if im.last_verified_at else None,
        "updated_at": im.updated_at.isoformat() if im.updated_at else None,
    }


def list_indices(
    db: Session, category: str | None = None, is_active: bool | None = None,
    search: str | None = None, page: int = 1, page_size: int = 50,
) -> dict:
    q = db.query(IndexMaster)
    if category:
        q = q.filter(IndexMaster.category == category)
    if is_active is not None:
        q = q.filter(IndexMaster.is_active == is_active)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (IndexMaster.index_code.ilike(like))
            | (IndexMaster.index_name.ilike(like))
        )
    total = q.count()
    rows = q.order_by(IndexMaster.index_code).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return {
        "items": [_to_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


def get_index(db: Session, code: str) -> dict | None:
    im = db.query(IndexMaster).filter_by(index_code=code).first()
    return _to_dict(im) if im else None


def create_index(db: Session, data: dict) -> dict:
    im = IndexMaster(**{
        k: v for k, v in data.items()
        if k in IndexMaster.__table__.columns
    })
    if not im.first_pulled_at:
        im.first_pulled_at = datetime.utcnow()
    if not im.last_pulled_at:
        im.last_pulled_at = datetime.utcnow()
    if not im.last_verified_at:
        im.last_verified_at = datetime.utcnow()
    db.add(im)
    db.commit()
    db.refresh(im)
    return _to_dict(im)


def update_index(db: Session, code: str, data: dict) -> dict | None:
    im = db.query(IndexMaster).filter_by(index_code=code).first()
    if not im:
        return None
    _ALLOWED = {c.name for c in IndexMaster.__table__.columns}
    for k, v in data.items():
        if k in _ALLOWED and k != "index_code":
            setattr(im, k, v)
    db.commit()
    db.refresh(im)
    return _to_dict(im)


def delete_index(db: Session, code: str) -> bool:
    im = db.query(IndexMaster).filter_by(index_code=code).first()
    if not im:
        return False
    db.delete(im)
    db.commit()
    return True
