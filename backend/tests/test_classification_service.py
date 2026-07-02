"""classification_service 测试。"""
import pytest


def _ensure_classification_tables(db):
    from models_master import Classification, ClassificationAssign
    Classification.__table__.create(bind=db.get_bind(), checkfirst=True)
    ClassificationAssign.__table__.create(bind=db.get_bind(), checkfirst=True)


def test_create_list_dimension(in_memory_db):
    _ensure_classification_tables(in_memory_db)
    from services.classification_service import create_classification, list_classifications
    create_classification(in_memory_db, dict(dimension="theme", code="dividend", display_label="红利"))
    create_classification(in_memory_db, dict(dimension="theme", code="gold", display_label="黄金"))
    items = list_classifications(in_memory_db, dimension="theme")
    codes = {i["code"] for i in items}
    assert {"dividend", "gold"} == codes


def test_unique_constraint_violation(in_memory_db):
    _ensure_classification_tables(in_memory_db)
    from services.classification_service import create_classification
    from sqlalchemy.exc import IntegrityError
    create_classification(in_memory_db, dict(dimension="theme", code="x", display_label="X"))
    with pytest.raises(IntegrityError):
        create_classification(in_memory_db, dict(dimension="theme", code="x", display_label="X2"))


def test_assign_and_get_assignments(in_memory_db):
    _ensure_classification_tables(in_memory_db)
    from services.classification_service import (
        create_classification, assign, get_assignments, unassign,
    )
    cid = create_classification(in_memory_db, dict(
        dimension="theme", code="dividend", display_label="红利",
    ))["id"]
    assign(in_memory_db, entity_type="fund", entity_code="510300.SH", classification_id=cid)
    result = get_assignments(in_memory_db, entity_type="fund", entity_code="510300.SH")
    assert len(result) == 1
    assert result[0]["code"] == "dividend"

    # idempotent
    assign(in_memory_db, entity_type="fund", entity_code="510300.SH", classification_id=cid)

    # unassign
    assert unassign(in_memory_db, entity_type="fund", entity_code="510300.SH", classification_id=cid)
    result = get_assignments(in_memory_db, entity_type="fund", entity_code="510300.SH")
    assert len(result) == 0


def test_deactivate_keeps_record(in_memory_db):
    """停用不改物理删除,保留 FK 完整性。"""
    _ensure_classification_tables(in_memory_db)
    from services.classification_service import create_classification, deactivate_classification, get_classification
    cid = create_classification(in_memory_db, dict(
        dimension="theme", code="soon_dead", display_label="即将废弃",
    ))["id"]
    deactivate_classification(in_memory_db, cid)
    result = get_classification(in_memory_db, cid)
    assert result["is_active"] is False
    assert result["code"] == "soon_dead"  # 记录仍在,只是 is_active=False
