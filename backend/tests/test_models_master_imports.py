"""验证 models_master.py 的 5 张新表可被 SQLAlchemy 正常 import + create。"""


def test_models_master_imports():
    """所有 5 张新表应能从 models_master 导入。"""
    from models_master import (
        StockMaster, FundMaster, IndexMaster,
        Classification, ClassificationAssign,
    )
    assert StockMaster.__tablename__ == "stock_master"
    assert FundMaster.__tablename__ == "fund_master"
    assert IndexMaster.__tablename__ == "index_master"
    assert Classification.__tablename__ == "classification"
    assert ClassificationAssign.__tablename__ == "classification_assign"


def test_create_all_tables(in_memory_db):
    """5 张新表应能在内存 SQLite 上成功 create。"""
    from models_master import Base as MasterBase
    MasterBase.metadata.create_all(bind=in_memory_db.get_bind())
    # 验证表存在
    tables = MasterBase.metadata.tables.keys()
    assert "stock_master" in tables
    assert "fund_master" in tables
    assert "index_master" in tables
    assert "classification" in tables
    assert "classification_assign" in tables


def test_classification_unique_constraint(in_memory_db):
    """Classification 的 (dimension, code) 应有唯一约束。"""
    from models_master import Classification, Base as MasterBase
    MasterBase.metadata.create_all(bind=in_memory_db.get_bind())
    session = in_memory_db
    session.add(Classification(dimension="theme", code="dividend", display_label="红利"))
    session.commit()
    # 第二次插入相同 (dimension, code) 应触发 IntegrityError
    import pytest
    from sqlalchemy.exc import IntegrityError
    session.add(Classification(dimension="theme", code="dividend", display_label="红利2"))
    with pytest.raises(IntegrityError):
        session.commit()
