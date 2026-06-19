"""Database engine and session management"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from config import DATABASE_URL

# 转换 postgresql:// → postgresql+psycopg:// (psycopg3 驱动)
_normalized_url = DATABASE_URL
if _normalized_url.startswith("postgresql://"):
    _normalized_url = _normalized_url.replace("postgresql://", "postgresql+psycopg://", 1)
elif _normalized_url.startswith("postgres://"):
    _normalized_url = _normalized_url.replace("postgres://", "postgresql+psycopg://", 1)

engine = create_engine(
    _normalized_url,
    connect_args={"check_same_thread": False} if "sqlite" in _normalized_url else {},
    echo=False,
)

# Enable WAL mode for SQLite for better concurrent access
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if "sqlite" in DATABASE_URL:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: get DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables + lightweight column migrations for SQLite"""
    import models  # noqa: F401 — ensure models are registered
    Base.metadata.create_all(bind=engine)

    # Lightweight ALTER for new columns on existing tables
    _MIGRATIONS = [
        ("security_master", "type2", "VARCHAR(20)"),
        ("watchlist", "name", "VARCHAR(100)"),
        ("watchlist", "market", "VARCHAR(10)"),
        ("watchlist", "industry", "VARCHAR(50)"),
        ("watchlist", "weight", "FLOAT"),
        ("watchlist", "added_at", "DATETIME"),
        # 7+4 industry columns for snapshots + full_holding + csi300
        ("a_share_financial_snapshot", "swy_l4", "VARCHAR(60)"),
        ("a_share_financial_snapshot", "se_l1", "VARCHAR(60)"),
        ("a_share_financial_snapshot", "se_l2", "VARCHAR(60)"),
        ("a_share_financial_snapshot", "se_l3", "VARCHAR(60)"),
        ("a_share_financial_snapshot", "se_l4", "VARCHAR(60)"),
        ("hk_share_financial_snapshot", "swy_l4", "VARCHAR(60)"),
        ("hk_share_financial_snapshot", "se_l1", "VARCHAR(60)"),
        ("hk_share_financial_snapshot", "se_l2", "VARCHAR(60)"),
        ("hk_share_financial_snapshot", "se_l3", "VARCHAR(60)"),
        ("hk_share_financial_snapshot", "se_l4", "VARCHAR(60)"),
        ("full_holding_snapshot", "swy_l4", "VARCHAR(60)"),
        ("full_holding_snapshot", "se_l1", "VARCHAR(60)"),
        ("full_holding_snapshot", "se_l2", "VARCHAR(60)"),
        ("full_holding_snapshot", "se_l3", "VARCHAR(60)"),
        ("full_holding_snapshot", "se_l4", "VARCHAR(60)"),
        ("csi300_constituent_snapshot", "swy_l4", "VARCHAR(60)"),
        ("csi300_constituent_snapshot", "se_l1", "VARCHAR(60)"),
        ("csi300_constituent_snapshot", "se_l2", "VARCHAR(60)"),
        ("csi300_constituent_snapshot", "se_l3", "VARCHAR(60)"),
        ("csi300_constituent_snapshot", "se_l4", "VARCHAR(60)"),
        # Index constituent weight (added 2026-06 via pull_index_weights.py)
        ("index_constituent_snapshot", "weight", "FLOAT"),
    ]
    from sqlalchemy import inspect
    insp = inspect(engine)
    with engine.begin() as conn:
        from sqlalchemy import text
        for table, col, coltype in _MIGRATIONS:
            try:
                existing = {c["name"] for c in insp.get_columns(table)}
            except Exception:
                continue
            if col not in existing:
                try:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {coltype}'))
                except Exception:
                    pass
