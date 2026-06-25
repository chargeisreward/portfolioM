"""验证 _ensure_seed_admin 自动 seed"""
import os


def test_ensure_seed_admin_creates_one_user(tmp_path):
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"

    # 创建独立 engine + 建表 + 跑 seed
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    test_engine = create_engine(db_url, poolclass=StaticPool)
    import models
    from database import Base, _ensure_seed_admin
    Base.metadata.create_all(bind=test_engine)

    # 替换 module-level engine
    import database
    original_engine = database.engine
    database.engine = test_engine
    try:
        # 验证无 user
        S = sessionmaker(bind=test_engine)
        with S() as db:
            assert db.query(models.User).count() == 0
        # 跑 seed
        _ensure_seed_admin()
        with S() as db:
            users = db.query(models.User).all()
            assert len(users) == 1
            assert users[0].is_admin is True
            assert users[0].username == "admin"
    finally:
        database.engine = original_engine


def test_ensure_seed_admin_skips_when_users_exist(tmp_path):
    db_file = tmp_path / "test2.db"
    db_url = f"sqlite:///{db_file}"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    test_engine = create_engine(db_url, poolclass=StaticPool)
    import models
    from database import Base, _ensure_seed_admin
    Base.metadata.create_all(bind=test_engine)

    import database
    original_engine = database.engine
    database.engine = test_engine
    try:
        S = sessionmaker(bind=test_engine)
        with S() as db:
            db.add(models.User(username="existing", password_hash="x", is_active=True))
            db.commit()
        _ensure_seed_admin()
        with S() as db:
            assert db.query(models.User).count() == 1
            assert db.query(models.User).filter(models.User.username == "existing").first() is not None
    finally:
        database.engine = original_engine