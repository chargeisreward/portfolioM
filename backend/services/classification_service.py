"""分类维度 service — 字典 CRUD + assign 关联。"""
from __future__ import annotations
from sqlalchemy.orm import Session
from models_master import Classification, ClassificationAssign


def _to_dict(c: Classification) -> dict:
    return {
        "id": c.id, "dimension": c.dimension, "code": c.code,
        "display_label": c.display_label, "sort_order": c.sort_order,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def list_classifications(
    db: Session, dimension: str, is_active: bool | None = True,
) -> list[dict]:
    q = db.query(Classification).filter_by(dimension=dimension)
    if is_active is not None:
        q = q.filter(Classification.is_active == is_active)
    rows = q.order_by(Classification.sort_order, Classification.code).all()
    return [_to_dict(r) for r in rows]


def get_classification(db: Session, cid: int) -> dict | None:
    c = db.query(Classification).filter_by(id=cid).first()
    return _to_dict(c) if c else None


def create_classification(db: Session, data: dict) -> dict:
    c = Classification(**{k: v for k, v in data.items()
                          if k in Classification.__table__.columns})
    db.add(c)
    db.commit()
    db.refresh(c)
    return _to_dict(c)


def update_classification(db: Session, cid: int, data: dict) -> dict | None:
    c = db.query(Classification).filter_by(id=cid).first()
    if not c:
        return None
    _ALLOWED = {col.name for col in Classification.__table__.columns}
    for k, v in data.items():
        if k in _ALLOWED and k != "id":
            setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return _to_dict(c)


def deactivate_classification(db: Session, cid: int) -> bool:
    """停用 (is_active=False) 而非物理删除,保 FK 完整性。"""
    c = db.query(Classification).filter_by(id=cid).first()
    if not c:
        return False
    c.is_active = False
    db.commit()
    return True


def assign(
    db: Session, entity_type: str, entity_code: str, classification_id: int,
) -> bool:
    """把分类赋给一个实体。已存在则跳过 (idempotent)。"""
    existing = db.query(ClassificationAssign).filter_by(
        entity_type=entity_type, entity_code=entity_code,
        classification_id=classification_id,
    ).first()
    if existing:
        return False
    db.add(ClassificationAssign(
        entity_type=entity_type, entity_code=entity_code,
        classification_id=classification_id,
    ))
    db.commit()
    return True


def unassign(
    db: Session, entity_type: str, entity_code: str, classification_id: int,
) -> bool:
    n = db.query(ClassificationAssign).filter_by(
        entity_type=entity_type, entity_code=entity_code,
        classification_id=classification_id,
    ).delete()
    db.commit()
    return n > 0


def get_assignments(
    db: Session, entity_type: str, entity_code: str,
) -> list[dict]:
    """列出实体的所有分类 (含 dimension / display_label)。"""
    rows = db.query(Classification).join(
        ClassificationAssign,
        ClassificationAssign.classification_id == Classification.id,
    ).filter(
        ClassificationAssign.entity_type == entity_type,
        ClassificationAssign.entity_code == entity_code,
    ).all()
    return [_to_dict(r) for r in rows]
