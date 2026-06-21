"""诊断: 跑全部 SQL stmts, 然后单独跑 holdings COPY, 看是否写入持久"""
import sys
import time
from pathlib import Path
import psycopg

sys.path.insert(0, str(Path(__file__).parent))
from restore_pg_dump import parse_dump

dump_path = Path("backend/data/dumps/local_dump_20260620_0119.sql")
conn_str = "postgresql://root:3Z0Kr15pnMsNBkRg6L7xlAc4v92YC8wP@43.157.93.89:32309/zeabur"

print(f"[parse] reading {dump_path}")
text = dump_path.read_text(encoding="utf-8")
segments = parse_dump(text)
print(f"[parse] {len(segments)} segments")

# 先 truncate 所有
with psycopg.connect(conn_str) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        tables = [r[0] for r in cur.fetchall()]
        quoted = ', '.join(f'"{t}"' for t in tables)
        cur.execute(f'TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE')
        conn.commit()
        print(f"[pre] truncated {len(tables)} tables")

# Run 全部 SQL stmts
t0 = time.time()
n_sql = 0
with psycopg.connect(conn_str, connect_timeout=30, autocommit=False) as conn:
    for kind, *payload in segments:
        if kind == "sql":
            stmt = payload[0]
            try:
                with conn.cursor() as cur:
                    cur.execute(stmt)
                conn.commit()
                n_sql += 1
            except Exception as e:
                conn.rollback()
                if "DuplicateTable" not in type(e).__name__ and "DuplicateObject" not in type(e).__name__:
                    pass  # ignore other dup
print(f"[sql] ran {n_sql} sql stmts in {time.time()-t0:.1f}s")

# Now run ONLY holdings COPY
holdings_seg = None
for s in segments:
    if s[0] == "copy" and "holdings" in s[1]:
        holdings_seg = s
        break
copy_stmt, rows_text = holdings_seg[1], holdings_seg[2]

print(f"[copy] running holdings COPY block")
with psycopg.connect(conn_str, connect_timeout=30, autocommit=False) as conn:
    with conn.cursor() as cur:
        with cur.copy(copy_stmt) as copy:
            n_rows = 0
            for row in rows_text.split("\n"):
                if not row:
                    continue
                cells = row.split("\t")
                cells = [None if c == r"\N" else c for c in cells]
                copy.write_row(cells)
                n_rows += 1
        cur.execute('SELECT setval(pg_get_serial_sequence(%s, %s), COALESCE((SELECT MAX(id) FROM "holdings"), 1), true)', ('"holdings"', "id"))
        conn.commit()
        print(f"[copy] wrote {n_rows} rows")

# Verify in SAME process, new connection
with psycopg.connect(conn_str, connect_timeout=10) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM holdings")
        print(f"[verify, new conn in same process] holdings = {cur.fetchone()[0]}")
