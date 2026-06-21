"""简化版 restore: 跑 dump 然后立即查"""
import sys
import time
from pathlib import Path
import psycopg

# import the parse + restore machinery
sys.path.insert(0, str(Path(__file__).parent))
from restore_pg_dump import parse_dump

dump_path = Path("backend/data/dumps/local_dump_20260620_0119.sql")
conn_str = "postgresql://root:3Z0Kr15pnMsNBkRg6L7xlAc4v92YC8wP@43.157.93.89:32309/zeabur"

print(f"[parse] reading {dump_path}")
text = dump_path.read_text(encoding="utf-8")
segments = parse_dump(text)
print(f"[parse] {len(segments)} segments")

# Find holdings COPY segment
holdings_seg = None
for s in segments:
    if s[0] == "copy" and "holdings" in s[1]:
        holdings_seg = s
        break

if not holdings_seg:
    print("NO HOLDINGS SEGMENT!")
    sys.exit(1)

print(f"[run] holdings copy segment, payload len={len(holdings_seg[2])}")
copy_stmt, rows_text = holdings_seg[1], holdings_seg[2]

t0 = time.time()
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
            print(f"[copy] wrote {n_rows} rows in {time.time()-t0:.1f}s")
        # setval
        cur.execute(
            'SELECT setval(pg_get_serial_sequence(%s, %s), COALESCE((SELECT MAX(id) FROM "holdings"), 1), true)',
            ('"holdings"', "id"),
        )
        conn.commit()
        print(f"[commit] done in {time.time()-t0:.1f}s")

# Verify in SAME connection (will close first)
with psycopg.connect(conn_str, connect_timeout=10) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM holdings")
        print(f"[verify same process, new conn] holdings count = {cur.fetchone()[0]}")
