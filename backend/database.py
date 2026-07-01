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
    # === 启动时自动 seed admin (若无 users) ===
    _ensure_seed_admin()

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
        # === Multi-user: user_id columns (auth-upgrade M1) ===
        ("holdings", "user_id", "BIGINT NOT NULL DEFAULT 1"),
        ("watchlist", "user_id", "BIGINT NOT NULL DEFAULT 1"),
        ("access_sessions", "user_id", "BIGINT"),
        ("penetration_snapshot", "user_id", "BIGINT NOT NULL DEFAULT 2"),
        ("csi300_constituent_snapshot", "user_id", "BIGINT NOT NULL DEFAULT 2"),
        # === 2026-06-25 FundDrillSnapshot 估值字段补全 ===
        ("fund_drill_snapshot", "pe_ttm", "FLOAT"),
        ("fund_drill_snapshot", "pb_mrq", "FLOAT"),
        ("fund_drill_snapshot", "ps_ttm", "FLOAT"),
        ("fund_drill_snapshot", "dividend_yield", "FLOAT"),
        # === 2026-06-25 FundDrillSnapshot 动态估值字段（来自 A/H 估值表的 *_dynamic 字段）===
        ("fund_drill_snapshot", "pe_ttm_dynamic", "FLOAT"),
        ("fund_drill_snapshot", "pb_mrq_dynamic", "FLOAT"),
        ("fund_drill_snapshot", "ps_ttm_dynamic", "FLOAT"),
        # === 2026-06-25 FundDrillSnapshot 双币种：baseline_price_cny（本币基准价，公共层算好）===
        ("fund_drill_snapshot", "baseline_price_cny", "FLOAT"),
        # === 2026-06-26 HoldingDailySnapshot holding_uid：区分同代码不同批次 ===
        ("holding_daily_snapshot", "holding_uid", "INTEGER"),
        # === 2026-06-27 DataPullTask 监控扩展（planned_count/success_count/coverage_rate）===
        ("data_pull_task", "planned_count", "INTEGER"),
        ("data_pull_task", "success_count", "INTEGER"),
        ("data_pull_task", "coverage_rate", "FLOAT"),
        # === 2026-07-01 PriceCache 数据新鲜度跟踪 ===
        # UPSERT 不更新 created_at，新增 updated_at 反映真实"最近一次 upsert 时间"
        # _upsert_change_pct 显式设置 updated_at = datetime.utcnow()
        ("price_cache", "updated_at", "TIMESTAMP"),
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

        # === Watchlist PK 改复合 (user_id, code) ===
        try:
            pk_info = insp.get_pk_constraint("watchlist")
            pk_cols = pk_info.get("constrained_columns", []) if pk_info else []
            if pk_cols == ["code"]:
                if "sqlite" in DATABASE_URL:
                    # SQLite 不能 DROP/ADD PK — 重建表
                    conn.execute(text("""
                        CREATE TABLE watchlist_new (
                            user_id BIGINT NOT NULL DEFAULT 1,
                            code VARCHAR(20) NOT NULL,
                            name VARCHAR(100),
                            market VARCHAR(10),
                            industry VARCHAR(50),
                            weight FLOAT,
                            added_at DATETIME,
                            PRIMARY KEY (user_id, code)
                        )
                    """))
                    conn.execute(text("""
                        INSERT OR IGNORE INTO watchlist_new
                            (user_id, code, name, market, industry, weight, added_at)
                        SELECT user_id, code, name, market, industry, weight, added_at
                        FROM watchlist
                    """))
                    conn.execute(text("DROP TABLE watchlist"))
                    conn.execute(text("ALTER TABLE watchlist_new RENAME TO watchlist"))
                else:
                    # PG: 直接 DROP/ADD PK
                    conn.execute(text("ALTER TABLE watchlist DROP CONSTRAINT IF EXISTS watchlist_pkey"))
                    conn.execute(text("ALTER TABLE watchlist ADD PRIMARY KEY (user_id, code)"))
        except Exception as e:
            print(f"[init_db] watchlist PK migration: {e}")

        # === 索引 ===
        for ix_table, ix_col in [("holdings", "user_id"), ("watchlist", "user_id"),
                                  ("access_sessions", "user_id"),
                                  # 2026-06-26 交易记录驱动的持仓重建
                                  ("transaction_record", "user_id"),
                                  ("transaction_record", "trade_date"),
                                  ("holding_daily_snapshot", "user_id"),
                                  ("holding_daily_snapshot", "as_of_date"),
                                  ("trading_session", "user_id")]:
            try:
                ix_name = f"ix_{ix_table}_{ix_col}"
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {ix_name} ON {ix_table} ({ix_col})"))
            except Exception:
                pass

        # === 2026-06-26 holding_daily_snapshot 唯一约束变更（加入 holding_uid）===
        # 同代码不同批次（不同 Holding.id）需可共存，NULL=交易新建/CASH 不受约束
        try:
            ucs = insp.get_unique_constraints("holding_daily_snapshot") or []
            need_update = True
            for uc in ucs:
                if uc.get("name") == "ux_holding_daily_user_date_code":
                    if "holding_uid" in (uc.get("column_names") or []):
                        need_update = False
                    break
            if need_update:
                conn.execute(text(
                    "ALTER TABLE holding_daily_snapshot "
                    "DROP CONSTRAINT IF EXISTS ux_holding_daily_user_date_code"
                ))
                conn.execute(text(
                    "ALTER TABLE holding_daily_snapshot "
                    "ADD CONSTRAINT ux_holding_daily_user_date_code "
                    "UNIQUE (user_id, as_of_date, security_code, holding_uid)"
                ))
                print("[init_db] holding_daily_snapshot UC 已更新（含 holding_uid）")
        except Exception as e:
            print(f"[init_db] holding_daily_snapshot UC migration: {e}")


def _ensure_seed_admin():
    """启动时若无 users 表行，自动 seed 一个 admin 账户。"""
    try:
        import bcrypt
        from sqlalchemy.orm import Session
        from sqlalchemy import text
        from config import SEED_ADMIN_USERNAME, SEED_ADMIN_PASSWORD
        from models import User
        # 用 globals()['engine'] 而非模块顶部 import-time 的 engine（支持测试 monkeypatch）
        eng = globals()["engine"]
        with Session(eng) as db:
            if db.query(User).count() > 0:
                return
            pw_hash = bcrypt.hashpw(SEED_ADMIN_PASSWORD.encode(),
                                    bcrypt.gensalt(rounds=10)).decode()
            admin = User(
                username=SEED_ADMIN_USERNAME, password_hash=pw_hash,
                is_admin=True, is_advisor=False,
                display_name="系统管理员", is_active=True
            )
            db.add(admin); db.commit()
            print(f"[SEED] 已创建 admin 用户 (id={admin.id}): {SEED_ADMIN_USERNAME}")
    except Exception as e:
        print(f"[SEED] 自动 seed admin 失败（不影响启动）: {e}")
