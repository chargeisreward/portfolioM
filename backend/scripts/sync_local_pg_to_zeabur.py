"""本地 docker Postgres → Zeabur Postgres 全量同步脚本

设计目标:
  - 幂等可重跑: 先 TRUNCATE 目标表再全量写入
  - 处理 PG→PG 的类型差异 (JSON/BOOLEAN/bytea 等)
  - 分块写入, 避免 Zeabur free-tier PG (287MB) OOM
  - 同步 SERIAL 序列

用法:
  python backend/scripts/sync_local_pg_to_zeabur.py --dry-run     # 仅比对
  python backend/scripts/sync_local_pg_to_zeabur.py              # 执行同步
  python backend/scripts/sync_local_pg_to_zeabur.py --tables holdings,price_cache
"""
import argparse
import json
import os
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
import models  # noqa: F401, E402 - 注册所有 ORM 模型

# ---- 配置 ----
SOURCE_URL = "postgresql+psycopg://portfoliom:localdev@localhost:5432/portfoliom"

# 目标库地址从环境变量读取，避免把生产密码提交到 git。
# 示例: export ZEABUR_DATABASE_URL="postgresql+psycopg://root:...@host:port/db"
# 或在命令行传 --target-url。
DEFAULT_TARGET_URL = os.environ.get("ZEABUR_DATABASE_URL")

# models 里识别出来的特殊类型列
BOOLEAN_COLUMNS = {"is_trading"}
JSON_COLUMNS = {"data_json"}


def get_table_columns(engine, table_name: str) -> list[str]:
    return [c["name"] for c in inspect(engine).get_columns(table_name)]


def count_rows(engine, table_name: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0


def widen_overlong_varchar_columns(src_engine, tgt_engine) -> list[tuple]:
    """扫描源 PG 中所有 VARCHAR(N) 列, 若数据超 N 字符, 把目标 PG 对应列拓宽."""
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
            if not (col_type.startswith("VARCHAR") or col_type.startswith("CHAR")):
                continue
            import re as _re
            m = _re.search(r"\((\d+)\)", col_type)
            if not m:
                continue
            old_len = int(m.group(1))
            with src_engine.connect() as conn:
                max_len = conn.execute(text(
                    f'SELECT MAX(LENGTH(COALESCE("{col_name}", \'\'))) '
                    f'FROM "{tbl}"'
                )).scalar() or 0
            if max_len <= old_len:
                continue
            new_len = next((b for b in BUCKETS if b >= max_len), max_len + 100)
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
    """重置目标 PG SERIAL 序列到当前 MAX(pk)."""
    insp = inspect(engine)
    col_info = next((c for c in insp.get_columns(table_name) if c["name"] == pk_col), None)
    if col_info is None:
        return -1
    col_type = str(col_info["type"]).upper()
    if not any(t in col_type for t in ("INTEGER", "INT", "BIGINT", "SMALLINT", "SERIAL")):
        return -2

    seq_name = f"{table_name}_{pk_col}_seq"
    with engine.connect() as conn:
        max_id = conn.execute(
            text(f'SELECT COALESCE(MAX("{pk_col}"), 0) FROM "{table_name}"')
        ).scalar() or 0
    try:
        with engine.begin() as conn:
            conn.execute(text("SELECT setval(:seq, :mx, true)"),
                         {"seq": seq_name, "mx": max(max_id, 1)})
        return max_id
    except Exception:
        return -1


def sync_table(src_engine, tgt_engine, table_name: str,
               dry_run: bool = False, chunk_size: int = 2000) -> dict:
    src_cols = get_table_columns(src_engine, table_name)
    tgt_cols = get_table_columns(tgt_engine, table_name)
    common = [c for c in src_cols if c in tgt_cols]
    src_only = [c for c in src_cols if c not in tgt_cols]

    src_count = count_rows(src_engine, table_name)
    tgt_count = count_rows(tgt_engine, table_name)

    info = {"src": src_count, "tgt_before": tgt_count, "common": common, "src_only": src_only}

    if dry_run:
        print(f"  [{table_name}] src={src_count:>8} tgt={tgt_count:>8} "
              f"common={len(common)}/{len(src_cols)} src_only={src_only or '[]'}")
        return info

    # 全量替换: TRUNCATE 目标表
    if tgt_count > 0:
        with tgt_engine.begin() as conn:
            conn.execute(text(f'TRUNCATE TABLE "{table_name}" RESTART IDENTITY CASCADE'))
        print(f"  [{table_name}] [TRUNCATE] cleared {tgt_count} rows")

    if src_count == 0:
        info["inserted"] = 0
        info["status"] = "ok (empty source)"
        return info

    # 读源数据
    df = pd.read_sql_table(table_name, src_engine, columns=common)
    print(f"  [{table_name}] read {len(df)} rows from source")

    # Boolean / JSON 类型修正
    for col in common:
        if col in BOOLEAN_COLUMNS and col in df.columns:
            df[col] = df[col].astype("boolean")
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
                        return v
                return v
            df[col] = df[col].apply(_coerce_json)

    # 写目标
    inserted = 0
    t0 = time.time()
    dtype = {col: SA_JSON() for col in JSON_COLUMNS if col in df.columns}
    n_cols = max(len(df.columns), 1)
    safe_chunk = max(1, min(chunk_size, 60000 // n_cols))
    if safe_chunk < chunk_size:
        print(f"  [{table_name}] chunk_size auto-reduced {chunk_size} -> {safe_chunk} "
              f"for {n_cols} cols")
    for start in range(0, len(df), safe_chunk):
        chunk = df.iloc[start:start + safe_chunk]
        with tgt_engine.begin() as conn:
            chunk.to_sql(table_name, conn, if_exists="append", index=False,
                         method="multi", chunksize=safe_chunk, dtype=dtype or None)
        inserted += len(chunk)
    elapsed = time.time() - t0
    print(f"  [{table_name}] inserted {inserted} rows in {elapsed:.1f}s "
          f"({inserted / max(elapsed, 0.01):.0f} rows/s)")

    # 同步 SERIAL 序列
    pk_info = inspect(tgt_engine).get_pk_constraint(table_name)
    for pk_col in pk_info.get("constrained_columns", []):
        new_max = sync_serial_sequence(tgt_engine, table_name, pk_col)
        if new_max >= 0:
            print(f"  [{table_name}] sequence {table_name}_{pk_col}_seq -> {new_max}")
        elif new_max == -2:
            print(f"  [{table_name}] PK '{pk_col}' non-integer, skip sequence")

    info["inserted"] = inserted
    info["status"] = "ok"
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="only compare row counts")
    parser.add_argument("--tables", default="", help="comma-separated table names")
    parser.add_argument("--chunk-size", type=int, default=2000)
    parser.add_argument("--target-url", default=DEFAULT_TARGET_URL,
                        help="target Postgres URL (defaults to ZEABUR_DATABASE_URL env)")
    args = parser.parse_args()

    target_url = args.target_url
    if not target_url:
        print("[ERROR] 请设置 ZEABUR_DATABASE_URL 环境变量，或传入 --target-url")
        sys.exit(1)

    print(f">>> Source: {SOURCE_URL}")
    print(f">>> Target: {target_url}")
    mode = "DRY-RUN" if args.dry_run else "SYNC-REPLACE"
    print(f">>> Mode:   {mode}")
    if args.tables:
        print(f">>> Tables: {args.tables}")
    print()

    src_engine = create_engine(SOURCE_URL)
    tgt_engine = create_engine(target_url)

    print(">>> Step 1: Ensure target schema")
    if not args.dry_run:
        Base.metadata.create_all(bind=tgt_engine)
        print(">>> Step 1b: Widen over-long VARCHAR columns")
        widen_overlong_varchar_columns(src_engine, tgt_engine)
    print()

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
        if t not in tgt_tables and args.dry_run:
            print(f"[{i}/{len(target_tables)}] {t}: [WARN] not in target, will be created at runtime")
            continue
        print(f"[{i}/{len(target_tables)}] {t}:")
        try:
            results[t] = sync_table(src_engine, tgt_engine, t,
                                    dry_run=args.dry_run, chunk_size=args.chunk_size)
        except Exception as e:
            print(f"  [ERROR] {e}")
            import traceback
            traceback.print_exc()
            results[t] = {"error": str(e)}

    print()
    print(">>> Summary:")
    print(f"  {'table':<40} {'src':>10} {'tgt_before':>11} {'inserted':>10} {'status':<15}")
    print(f"  {'-'*40} {'-'*10} {'-'*11} {'-'*10} {'-'*15}")
    for t, r in results.items():
        if "error" in r:
            print(f"  {t:<40} {'-':>10} {'-':>11} {'-':>10} {'ERROR':<15}")
        elif args.dry_run:
            print(f"  {t:<40} {r['src']:>10} {r['tgt_before']:>11} {'-':>10} {'DRY-RUN':<15}")
        else:
            print(f"  {t:<40} {r['src']:>10} {r['tgt_before']:>11} "
                  f"{r.get('inserted', '-'):>10} {r.get('status', 'ok'):<15}")


if __name__ == "__main__":
    main()
