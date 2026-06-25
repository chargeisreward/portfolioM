"""验证 Postgres 数据结构迁移结果

分两层验证：
1. 模型元数据层（无需 DB 连接）— 检查 BigInteger/user_id/UK 定义
2. Postgres 实际结构层（仅 Postgres 运行）— 检查列/约束/默认值/索引

运行：cd backend && python -m pytest tests/test_pg_migration.py -v
"""
import os
os.environ.setdefault("APP_PASSWORD", "")

import pytest
from database import Base
import models
from config import DATABASE_URL


# ============ 1. 模型元数据层 ============

def test_penetration_snapshot_model_has_user_id():
    """PenetrationSnapshot 模型含 user_id 字段"""
    cols = {c.name: c for c in Base.metadata.tables["penetration_snapshot"].columns}
    assert "user_id" in cols, "penetration_snapshot 缺 user_id 列"


def test_penetration_snapshot_model_user_id_is_biginteger():
    """PenetrationSnapshot.user_id 类型为 BigInteger"""
    col = Base.metadata.tables["penetration_snapshot"].columns["user_id"]
    # BigInteger 在 SQLAlchemy 中 type 为 BigInteger
    type_name = type(col.type).__name__
    assert type_name == "BigInteger", f"期望 BigInteger，实际 {type_name}"


def test_penetration_snapshot_uk_includes_user_id():
    """PenetrationSnapshot 唯一约束含 user_id"""
    table = Base.metadata.tables["penetration_snapshot"]
    uk_cols = set()
    for c in table.constraints:
        if c.__class__.__name__ == "UniqueConstraint":
            uk_cols = {col.name for col in c.columns}
    assert "user_id" in uk_cols, f"UX 不含 user_id: {uk_cols}"
    assert uk_cols == {"as_of_date", "user_id", "holding_code", "stock_code"}, \
        f"UX 列不匹配: {uk_cols}"


def test_aggregation_cache_model_user_id_default_none():
    """AggregationCache.user_id default 为 None"""
    col = Base.metadata.tables["aggregation_cache"].columns["user_id"]
    assert col.default is None or col.default.arg is None, \
        f"default 应为 None，实际 {col.default}"


def test_aggregation_timeseries_model_user_id_default_none():
    """AggregationTimeseries.user_id default 为 None"""
    col = Base.metadata.tables["aggregation_timeseries"].columns["user_id"]
    assert col.default is None or col.default.arg is None, \
        f"default 应为 None，实际 {col.default}"


def test_all_user_id_columns_are_biginteger_in_models():
    """所有含 user_id 列的表，模型层类型均为 BigInteger"""
    expected_bigint_tables = [
        "holdings",
        "access_sessions",
        "watchlist",
        "penetration_results",
        "penetration_snapshot",
        "a_share_financial_snapshot",
        "hk_share_financial_snapshot",
        "overseas_share_financial_snapshot",
        "full_holding_snapshot",
        "csi300_constituent_snapshot",
        "aggregation_cache",
        "aggregation_timeseries",
    ]
    for table_name in expected_bigint_tables:
        assert table_name in Base.metadata.tables, f"表 {table_name} 不在 metadata 中"
        cols = Base.metadata.tables[table_name].columns
        assert "user_id" in cols, f"{table_name} 缺 user_id 列"
        type_name = type(cols["user_id"].type).__name__
        assert type_name == "BigInteger", \
            f"{table_name}.user_id 期望 BigInteger，实际 {type_name}"


def test_hk_share_financial_snapshot_no_duplicate_se_fields():
    """HKShareFinancialSnapshot 不应重复定义 se_l1/l2/l3/l4（models.py 清理验证）"""
    # 模型层每个列名只出现一次，重复定义会被后者覆盖但不会报错
    # 这里检查列数量合理（se_l1/l2/l3/l4 各只一个）
    cols = Base.metadata.tables["hk_share_financial_snapshot"].columns
    se_cols = [c.name for c in cols if c.name.startswith("se_")]
    # 期望 se_l1, se_l2, se_l3, se_l4 各一个（共 4 个）
    assert len(se_cols) == 4, f"se_* 列数量异常: {se_cols}"
    assert set(se_cols) == {"se_l1", "se_l2", "se_l3", "se_l4"}


def test_fund_daily_nav_no_duplicate_source_created_at():
    """FundDailyNav 不应重复定义 source/created_at"""
    cols = [c.name for c in Base.metadata.tables["fund_daily_nav"].columns]
    assert cols.count("source") == 1, f"source 列重复: {cols}"
    assert cols.count("created_at") == 1, f"created_at 列重复: {cols}"


# ============ 2. Postgres 实际结构层 ============

# 仅 Postgres 运行；SQLite 跳过
is_postgres = "postgresql" in DATABASE_URL
pg_only = pytest.mark.skipif(not is_postgres, reason="仅 Postgres 运行")


@pg_only
def test_pg_penetration_snapshot_has_user_id():
    """Postgres: penetration_snapshot.user_id 列存在且 NOT NULL"""
    from database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'penetration_snapshot' AND column_name = 'user_id'
        """)).fetchone()
    assert r is not None, "penetration_snapshot.user_id 列不存在"
    assert r[0] == "NO", f"penetration_snapshot.user_id 应 NOT NULL，实际 {r[0]}"


@pg_only
def test_pg_penetration_snapshot_uk_includes_user_id():
    """Postgres: ux_pnsnap 唯一约束含 user_id"""
    from database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT column_name FROM information_schema.key_column_usage
            WHERE table_name = 'penetration_snapshot' AND constraint_name = 'ux_pnsnap'
            ORDER BY ordinal_position
        """)).fetchall()
    cols = [row[0] for row in r]
    assert "user_id" in cols, f"ux_pnsnap 不含 user_id: {cols}"
    assert set(cols) == {"as_of_date", "user_id", "holding_code", "stock_code"}, \
        f"ux_pnsnap 列不匹配: {cols}"


@pg_only
def test_pg_aggregation_cache_user_id_default_null():
    """Postgres: aggregation_cache.user_id default 为 NULL"""
    from database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'aggregation_cache' AND column_name = 'user_id'
        """)).fetchone()
    assert r is not None, "aggregation_cache.user_id 列不存在"
    assert r[0] is None, f"default 应为 NULL，实际 {r[0]}"


@pg_only
def test_pg_aggregation_timeseries_user_id_default_null():
    """Postgres: aggregation_timeseries.user_id default 为 NULL"""
    from database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'aggregation_timeseries' AND column_name = 'user_id'
        """)).fetchone()
    assert r is not None, "aggregation_timeseries.user_id 列不存在"
    assert r[0] is None, f"default 应为 NULL，实际 {r[0]}"


@pg_only
def test_pg_csi300_constituent_snapshot_has_user_id():
    """Postgres: csi300_constituent_snapshot.user_id 列存在且 NOT NULL"""
    from database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'csi300_constituent_snapshot' AND column_name = 'user_id'
        """)).fetchone()
    assert r is not None, "csi300_constituent_snapshot.user_id 列不存在"
    assert r[0] == "NO", f"csi300_constituent_snapshot.user_id 应 NOT NULL，实际 {r[0]}"


@pg_only
def test_pg_user_id_indexes_exist():
    """Postgres: 4 张表的 user_id 索引存在"""
    from database import engine
    from sqlalchemy import text
    expected_indexes = [
        ("overseas_share_financial_snapshot", "ix_overseas_share_financial_snapshot_user_id"),
        ("csi300_constituent_snapshot", "ix_csi300_constituent_snapshot_user_id"),
        ("aggregation_cache", "ix_aggregation_cache_user_id"),
        ("aggregation_timeseries", "ix_aggregation_timeseries_user_id"),
    ]
    with engine.connect() as conn:
        for table, idx in expected_indexes:
            r = conn.execute(text("""
                SELECT 1 FROM pg_indexes
                WHERE tablename = :t AND indexname = :i
            """), {"t": table, "i": idx}).fetchone()
            assert r is not None, f"索引 {idx} 不存在于表 {table}"
