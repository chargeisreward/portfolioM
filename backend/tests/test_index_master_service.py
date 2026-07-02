"""index_master_service 测试。"""
import pytest


def _ensure_index_table(db):
    from models_master import IndexMaster
    IndexMaster.__table__.create(bind=db.get_bind(), checkfirst=True)


def test_create_and_get(in_memory_db):
    _ensure_index_table(in_memory_db)
    from services.index_master_service import create_index, get_index
    create_index(in_memory_db, dict(
        index_code="000300.SH", index_name="沪深300",
        exchange="SH", currency="CNY", category="宽基",
    ))
    i = get_index(in_memory_db, "000300.SH")
    assert i["index_name"] == "沪深300"
    assert i["category"] == "宽基"


def test_list_with_filters(in_memory_db):
    _ensure_index_table(in_memory_db)
    from services.index_master_service import create_index, list_indices
    for code, name, cat in [
        ("000300.SH", "沪深300", "宽基"),
        ("000905.SH", "中证500", "宽基"),
        ("399006.SZ", "创业板指", "宽基"),
        ("399371.SZ", "国证成长", "行业"),
    ]:
        create_index(in_memory_db, dict(
            index_code=code, index_name=name, category=cat,
        ))
    res = list_indices(in_memory_db, category="宽基")
    assert res["total"] == 3


def test_create_sets_timestamps_when_missing(in_memory_db):
    """不传 first_pulled_at 等应自动填充当前时间。"""
    _ensure_index_table(in_memory_db)
    from services.index_master_service import create_index
    res = create_index(in_memory_db, dict(
        index_code="HSI", index_name="恒生指数", exchange="HK",
    ))
    assert res["first_pulled_at"] is not None
    assert res["last_pulled_at"] is not None
    assert res["last_verified_at"] is not None
