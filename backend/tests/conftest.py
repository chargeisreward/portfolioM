"""共享 pytest fixtures。

`in_memory_db` 提供一个内存 SQLite engine + Session,跑完测试自动 drop。
仅用于纯模型层 / 服务层测试,不涉及 FastAPI dependency 覆盖。
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def in_memory_db():
    """内存 SQLite engine + Session。

    - StaticPool 确保 :memory: 在多连接下共享同一数据库
    - check_same_thread=False 让 pytest 在不同线程也能用
    - FK 在每个 connect 上自动开启 (无需调用方再 PRAGMA)
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 默认开启 FK（个别 test_classification_unique_constraint 之类的测试会用到）
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
