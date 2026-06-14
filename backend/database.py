"""Database engine and session management"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
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
