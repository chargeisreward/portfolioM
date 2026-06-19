"""SQLite → 本地 docker Postgres 全量迁移脚本

设计目标:
  - 幂等: 已迁过的表跳过 (target row count >= source row count)
  - 可重跑: 不删数据，只追加 + 同步序列
  - 健壮: pandas.read_sql_table → df.to_sql, 自动处理 sqlite/PG dialect 差异
  - Dry-run: --dry-run 只比对 row counts 不写数据

用法:
  python backend/scripts/migrate_sqlite_to_pg.py --dry-run     # 比对
  python backend/scripts/migrate_sqlite_to_pg.py              # 真迁移
  python backend/scripts/migrate_sqlite_to_pg.py --tables funds,holdings  # 单表
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.types import JSON as SA_JSON

# 让脚本能找到 models.py / config.py
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from database import Base  # noqa: E402
import models  # noqa: F401, E402  - 注册所有 ORM 模型

# ---- 配置 ----
SOURCE_URL = "sqlite:///" + str(BACKEND_DIR.parent / "portfolio.db")
TARGET_URL = "postgresql+psycopg://portfoliom:localdev@localhost:5432/portfoliom"

# models 里识别出来的 boolean / json 列 (写死避免反射的额外开销)
BOOLEAN_COLUMNS = {"is_trading"}
JSON_COLUMNS = {"data_json"}


def get_table_columns(engine, table_name: str) -> list[str]:
    return [c["name"] for c in inspect(engine).get_columns(table_name)]


def count_rows(engine, table_name: str) -> int:
    # 关键: 用 with 块让 connection 退出时自动 commit/rollback,
    # 避免 'idle in transaction' 持锁阻塞后续 TRUNCATE
    with engine.connect() as conn:
        return conn.execute(
            text(f'SELECT COUNT(*) FROM "{table_name}"')
        ).scalar() or 0


def widen_overlong_varchar_columns(src_engine, tgt_engine) -> list[tuple]:
    """扫描 SQLite 中所有 VARCHAR(N) 列, 若数据超 N 字符, 把 PG 对应列拓宽
    到合适长度 (向上取整到 50/100/200/500 或 1000).
    Returns: list of (table, column, old_len, new_len) for logging.
    """
    BUCKETS = [50, 100, 200, 500, 1000]
    widened = []
    src_insp = inspect(src_engine)
    tgt_insp = inspect(tgt_engine)

    for tbl in src_insp.get_table_names():
        if tbl not in tgt_insp.get_table_names():
            continue
        for src_col in src_insp.get_columns(tbl):
            col_name = src_col["name"]
            col_type = str(src_col["type"]).upper()
            # 只处理 SQLite 端的 VARCHAR(N) / CHAR(N)
            if not (col_type.startswith("VARCHAR") or col_type.startswith("CHAR")):
                continue
            import re as _re
            m = _re.search(r"\((\d+)\)", col_type)
            if not m:
                continue
            old_len = int(m.group(1))
            # SQLite 是弱类型, 可能有 None/数字等 — 用 LENGTH(COALESCE(col,''))
            with src_engine.connect() as conn:
                max_len = conn.execute(text(
                    f'SELECT MAX(LENGTH(COALESCE("{col_name}", \'\'))) '
                    f'FROM "{tbl}"'
                )).scalar() or 0
            if max_len <= old_len:
                continue
            new_len = next((b for b in BUCKETS if b >= max_len), max_len + 100)
            # PG 端 ALTER COLUMN TYPE
            try:
                with tgt_engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE "{tbl}" ALTER COLUMN "{col_name}" '
                        f'TYPE VARCHAR({new_len})'
                    ))
                widened.append((tbl, col_name, old_len, max_len, new_len))
                print(f"  [WIDEN] {tbl}.{col_name} VARCHAR({old_len}) -> "
                      f"VARCHAR({new_len}) (max data len={max_len})")
            except Exception as e:
                print(f"  [WIDEN-FAIL] {tbl}.{col_name}: {e}")
    return widened


def sync_serial_sequence(engine, table_name: str, pk_col: str) -> int:
    """重置 PG SERIAL 序列到当前 MAX(pk), 让后续 INSERT 接着用。
    仅当 PK 是 INTEGER/BIGINT 等数字类型时才设置 sequence
    (对 VARCHAR/TEXT PK 直接跳过, 例如 funds.code / watchlist.code)
    """
    insp = inspect(engine)
    col_info = next((c for c in insp.get_columns(table_name) if c["name"] == pk_col), None)
    if col_info is None:
        return -1
    col_type = str(col_info["type"]).upper()
    if not any(t in col_type for t in ("INTEGER", "INT", "BIGINT", "SMALLINT", "SERIAL")):
        # 非整数 PK (e.g. VARCHAR 'code'), 无 sequence 可同步
        return -2

    seq_name = f"{table_name}_{pk_col}_seq"
    with engine.connect() as conn:
        max_id = conn.execute(
            text(f'SELECT COALESCE(MAX("{pk_col}"), 0) FROM "{table_name}"')
        ).scalar() or 0
    try:
        with engine.begin() as conn:
            conn.execute(text(f"SELECT setval(:seq, :mx, true)"),
                         {"seq": seq_name, "mx": max(max_id, 1)})
        return max_id
    except Exception as e:
        # 序列名可能不是默认约定 (如模型用 Integer PK without Sequence)
        return -1


def migrate_table(src_engine, tgt_engine, table_name: str,
                  dry_run: bool = False, chunk_size: int = 2000,
                  truncate: bool = False) -> dict:
    """单表迁移"""
    src_cols = get_table_columns(src_engine, table_name)
    tgt_cols = get_table_columns(tgt_engine, table_name)
    common = [c for c in src_cols if c in tgt_cols]
    src_only = [c for c in src_cols if c not in tgt_cols]

    src_count = count_rows(src_engine, table_name)
    tgt_count = count_rows(tgt_engine, table_name)

    info = {
        "src": src_count,
        "tgt_before": tgt_count,
        "common": common,
        "src_only": src_only,
    }

    if dry_run:
        print(f"  [{table_name}] src={src_count:>8} tgt={tgt_count:>8} "
              f"common={len(common)}/{len(src_cols)} src_only={src_only or '[]'}")
        return info

    if not truncate and tgt_count >= src_count and src_count > 0:
        print(f"  [{table_name}] [SKIP] target has {tgt_count} >= source {src_count} "
              f"(use --truncate to force full replace)")
        info["status"] = "skip"
        return info

    if truncate and tgt_count > 0:
        with tgt_engine.begin() as conn:
            conn.execute(text(f'TRUNCATE TABLE "{table_name}" CASCADE'))
        print(f"  [{table_name}] [TRUNCATE] cleared {tgt_count} rows")
        tgt_count = 0

    # 读源数据
    df = pd.read_sql_table(table_name, src_engine, columns=common)
    print(f"  [{table_name}] read {len(df)} rows from SQLite")

    # Boolean: SQLite 存 0/1 int, PG BOOLEAN 列需要 bool
    for col in common:
        if col in BOOLEAN_COLUMNS and col in df.columns:
            df[col] = df[col].astype("boolean")

    # JSON: SQLite 存 TEXT, pandas 读到后可能是 str / dict / NaN.
    # 统一转成 dict/list 让 psycopg JSON adapter 处理 (不要再 json.dumps,
    # 否则 adapter 会包成 JSON string 形成 double-encode)
    for col in common:
        if col in JSON_COLUMNS and col in df.columns:
            def _coerce_json(v):
                if pd.isna(v):
                    return None
                if isinstance(v, (dict, list)):
                    return v
                if isinstance(v, str):
                    try:
                        return json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        return v  # 无法解析就原样塞回去, 由 PG 报错
                return v
            df[col] = df[col].apply(_coerce_json)

    # 写目标 (分块)
    inserted = 0
    t0 = time.time()
    # 指定 JSON 列的 SQLAlchemy 类型, 否则 pandas 默认按 object → VARCHAR,
    # psycopg bind str 到 PG JSON 列时会报 datatype mismatch
    dtype = {col: SA_JSON() for col in JSON_COLUMNS if col in df.columns}
    # PG 单条 INSERT 参数上限 65535, chunk_size * num_cols 必须 < 65535
    n_cols = max(len(df.columns), 1)
    safe_chunk = max(1, min(chunk_size, 60000 // n_cols))
    if safe_chunk < chunk_size:
        print(f"  [{table_name}] (chunk_size auto-reduced {chunk_size} -> {safe_chunk} "
              f"for {n_cols} cols to stay under PG 65535 param limit)")
    with tgt_engine.begin() as conn:
        for start in range(0, len(df), safe_chunk):
            chunk = df.iloc[start:start + safe_chunk]
            # method='multi' 让 psycopg 走 execute_batch 单语句多行, 大幅提速
            chunk.to_sql(table_name, conn, if_exists="append",
                         index=False, method="multi",
                         chunksize=safe_chunk, dtype=dtype or None)
            inserted += len(chunk)
    elapsed = time.time() - t0
    print(f"  [{table_name}] inserted {inserted} rows in {elapsed:.1f}s "
          f"({inserted/max(elapsed, 0.01):.0f} rows/s)")

    # 同步 SERIAL 序列 (PG 的 autoincrement)
    pk_info = inspect(tgt_engine).get_pk_constraint(table_name)
    for pk_col in pk_info.get("constrained_columns", []):
        new_max = sync_serial_sequence(tgt_engine, table_name, pk_col)
        if new_max >= 0:
            print(f"  [{table_name}] sequence {table_name}_{pk_col}_seq -> {new_max}")
        elif new_max == -2:
            print(f"  [{table_name}] PK '{pk_col}' is non-integer, skip sequence sync")

    info["inserted"] = inserted
    info["status"] = "ok"
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="only compare row counts, no writes")
    parser.add_argument("--tables", default="",
                        help="comma-separated table names (default: all)")
    parser.add_argument("--chunk-size", type=int, default=2000)
    parser.add_argument("--truncate", action="store_true",
                        help="TRUNCATE target table before insert (full replace, "
                             "replaces default append behavior)")
    args = parser.parse_args()

    print(f">>> Source: {SOURCE_URL}")
    print(f">>> Target: {TARGET_URL}")
    mode = "DRY-RUN" if args.dry_run else ("MIGRATE-TRUNCATE" if args.truncate else "MIGRATE-APPEND")
    print(f">>> Mode:   {mode}")
    if args.tables:
        print(f">>> Tables: {args.tables}")
    print()

    src_engine = create_engine(SOURCE_URL, connect_args={"check_same_thread": False})
    tgt_engine = create_engine(TARGET_URL)

    # Step 1: 同步 schema
    print(">>> Step 1: Base.metadata.create_all on target")
    if not args.dry_run:
        Base.metadata.create_all(bind=tgt_engine)
        # Step 1b: 检测 SQLite 数据超长, 自动拓宽 PG VARCHAR 列
        print(">>> Step 1b: Widen over-long VARCHAR columns")
        widen_overlong_varchar_columns(src_engine, tgt_engine)
    print()

    # Step 2: 列出待迁移表
    src_tables = sorted(inspect(src_engine).get_table_names())
    tgt_tables = sorted(inspect(tgt_engine).get_table_names())

    if args.tables:
        target_tables = [t.strip() for t in args.tables.split(",")]
    else:
        target_tables = src_tables

    print(f">>> Step 2: Process {len(target_tables)} tables")
    print()

    results = {}
    for i, t in enumerate(target_tables, 1):
        if t not in src_tables:
            print(f"[{i}/{len(target_tables)}] {t}: [WARN] not in source, skip")
            continue
        if t not in tgt_tables:
            print(f"[{i}/{len(target_tables)}] {t}: [WARN] not in target schema, "
                  f"running without --dry-run will create it, skip")
            continue
        print(f"[{i}/{len(target_tables)}] {t}:")
        try:
            results[t] = migrate_table(src_engine, tgt_engine, t,
                                       dry_run=args.dry_run,
                                       chunk_size=args.chunk_size,
                                       truncate=args.truncate)
        except Exception as e:
            print(f"  [ERROR] {e}")
            import traceback; traceback.print_exc()
            results[t] = {"error": str(e)}

    # Step 3: 汇总
    print()
    print(">>> Summary:")
    print(f"  {'table':<40} {'src':>10} {'tgt_before':>11} {'inserted':>10} {'status':<10}")
    print(f"  {'-'*40} {'-'*10} {'-'*11} {'-'*10} {'-'*10}")
    for t, r in results.items():
        if "error" in r:
            print(f"  {t:<40} {'-':>10} {'-':>11} {'-':>10} {'ERROR':<10}")
        elif args.dry_run:
            print(f"  {t:<40} {r['src']:>10} {r['tgt_before']:>11} {'-':>10} {'DRY-RUN':<10}")
        else:
            print(f"  {t:<40} {r['src']:>10} {r['tgt_before']:>11} "
                  f"{r.get('inserted', '-'):>10} {r.get('status', 'ok'):<10}")


if __name__ == "__main__":
    main()
