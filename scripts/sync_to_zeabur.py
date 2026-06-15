"""本地 SQLite → 远端 Postgres 数据同步
用法: python scripts/sync_to_zeabur.py [backend_url]
例:   python scripts/sync_to_zeabur.py https://portback.zeabur.app
"""
import os
import sys
import sqlite3
import json
import urllib.request
import urllib.parse

BACKEND_URL = sys.argv[1] if len(sys.argv) > 1 else "https://portback.zeabur.app"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or (sys.argv[2] if len(sys.argv) > 2 else "123456")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "portfolio.db")
DB_PATH = os.path.abspath(DB_PATH)

TABLES = [
    "security_type_config",
    "security_master",
    "holdings",
    "watchlist",
]


def fetch_table(table):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows


def post_sync(table, rows, truncate=True):
    url = f"{BACKEND_URL}/api/admin/sync-table"
    payload = json.dumps({"table": table, "rows": rows, "truncate": truncate}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "X-Admin-Token": ADMIN_TOKEN},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print(f"=== Syncing to {BACKEND_URL} ===")
    print(f"Source DB: {DB_PATH}")
    for t in TABLES:
        try:
            rows = fetch_table(t)
        except sqlite3.OperationalError as e:
            print(f"  {t}: SKIP ({e})")
            continue
        if not rows:
            print(f"  {t}: 0 rows, skip")
            continue
        print(f"  {t}: posting {len(rows)} rows...", end=" ", flush=True)
        # 分批（每批 200 行，防止 POST body 太大）
        BATCH = 200
        total_inserted = 0
        first = True
        for i in range(0, len(rows), BATCH):
            batch = rows[i : i + BATCH]
            res = post_sync(t, batch, truncate=first)
            first = False
            if res.get("status") != "ok":
                print(f"FAIL: {res}")
                return
            total_inserted += res.get("inserted", 0)
        print(f"OK (inserted={total_inserted})")
    print("=== Done ===")


if __name__ == "__main__":
    main()
