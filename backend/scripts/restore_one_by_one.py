"""Safe restore: skip SQL DDL (schema already there), COPY one block at a time
with fresh connection each, to avoid Zeabur PG OOM/timeout on big transactions."""
import sys
import time
from pathlib import Path
import psycopg

sys.path.insert(0, str(Path(__file__).parent))
from restore_pg_dump import parse_dump

dump_path = Path("backend/data/dumps/local_dump_20260620_0119.sql")
conn_str = "postgresql://root:3Z0Kr15pnMsNBkRg6L7xlAc4v92YC8wP@43.157.93.89:32309/zeabur"

# parse dump
text = dump_path.read_text(encoding="utf-8")
segments = parse_dump(text)
print(f"[parse] {len(segments)} segments")

# Pre-truncate all (one conn)
with psycopg.connect(conn_str) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        tables = [r[0] for r in cur.fetchall()]
        quoted = ', '.join(f'"{t}"' for t in tables)
        cur.execute(f'TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE')
        conn.commit()
        print(f"[pre] truncated {len(tables)} tables")

# Now COPY each block with a FRESH connection
t0 = time.time()
total_rows = 0
for kind, *payload in segments:
    if kind != "copy":
        continue
    copy_stmt, rows_text = payload
    tbl = copy_stmt.split()[1].split("(")[0].strip('"').split(".")[-1]
    cols_part = copy_stmt.split("(", 1)[1].rsplit(")", 1)[0]
    col_names = [c.strip().strip('"') for c in cols_part.split(",")]
    n_expected = sum(1 for r in rows_text.split("\n") if r)
    t_block = time.time()
    with psycopg.connect(conn_str, connect_timeout=30, autocommit=False) as conn:
        with conn.cursor() as cur:
            with cur.copy(copy_stmt) as copy:
                n = 0
                for row in rows_text.split("\n"):
                    if not row:
                        continue
                    cells = row.split("\t")
                    cells = [None if c == r"\N" else c for c in cells]
                    copy.write_row(cells)
                    n += 1
            if "id" in col_names:
                try:
                    cur.execute(
                        'SELECT setval(pg_get_serial_sequence(%s, %s), '
                        'COALESCE((SELECT MAX(id) FROM "%s"), 1), true)',
                        (f'"{tbl}"', "id", tbl),
                    )
                except Exception:
                    pass
        conn.commit()
    total_rows += n
    print(f"  [{tbl}] {n} rows (total {total_rows}, block {time.time()-t_block:.1f}s, overall {time.time()-t0:.1f}s)")

# Verify
print()
print("[verify]")
with psycopg.connect(conn_str, connect_timeout=10) as conn:
    with conn.cursor() as cur:
        for tbl in ['holdings', 'price_cache', 'security_master', 'trading_calendar', 'fund_daily_nav', 'watchlist']:
            cur.execute(f'SELECT count(*) FROM "{tbl}"')
            cnt = cur.fetchone()[0]
            print(f'  {tbl}: {cnt}')
