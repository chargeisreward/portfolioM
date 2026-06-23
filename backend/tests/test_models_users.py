"""验证 5 张新表能 create_all"""
from sqlalchemy import create_engine
from database import Base
import models


def test_users_table_exists():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert "users" in Base.metadata.tables
    assert "user_relations" in Base.metadata.tables
    assert "index_classification" in Base.metadata.tables
    assert "data_gap_report" in Base.metadata.tables
    assert "holding_import_log" in Base.metadata.tables


def test_users_columns():
    cols = {c.name for c in Base.metadata.tables["users"].columns}
    assert {"id", "username", "password_hash", "is_advisor",
            "is_admin", "is_active", "created_at"}.issubset(cols)