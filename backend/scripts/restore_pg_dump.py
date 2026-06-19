"""PostgreSQL pg_dump → 远程 PG 流式恢复 (纯 psycopg3, 无 psql 依赖).

策略: 先一次性 read dump 文件, 切成 segments:
  ('meta',   line)        - psql meta-command, 跳过
  ('sql',    text)        - 一个 ; 结尾的 SQL block
  ('copy',   stmt, data)  - COPY FROM stdin + tab-separated rows

然后顺序执行 segments. 不读两次流.

用法:
  python backend/scripts/restore_pg_dump.py --dump <file> --target <conn_str>
"""
import argparse
import re
import sys
import time
from pathlib import Path

import psycopg


COPY_RE = re.compile(
    r"^COPY\s+(?P<tbl>[^\s\(]+)\s*\((?P<cols>[^)]*)\)\s+FROM\s+stdin\s*;\s*$",
    re.IGNORECASE,
)


def parse_dump(text: str) -> list:
    """把整个 dump 文件切成 segments list.

    Returns:
        [("meta", line), ("sql", text), ("copy", stmt, rows_text)]
    """
    segments = []
    sql_buf: list[str] = []
    i = 0
    lines = text.split("\n")
    n = len(lines)

    def flush_sql():
        nonlocal sql_buf
        if sql_buf:
            block = "\n".join(sql_buf).strip()
            if block:
                segments.append(("sql", block))
            sql_buf = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # psql meta-command (除 \\. 作为 COPY 终止符外)
        if stripped.startswith("\\") and stripped != "\\.":
            flush_sql()
            segments.append(("meta", line))
            i += 1
            continue

        # COPY ... FROM stdin;
        m = COPY_RE.match(line)
        if m:
            flush_sql()
            stmt = line.strip()
            rows = []
            i += 1
            while i < n:
                row = lines[i]
                if row.rstrip("\r") == "\\.":
                    i += 1
                    break
                rows.append(row)
                i += 1
            segments.append(("copy", stmt, "\n".join(rows)))
            continue

        # 普通 SQL
        sql_buf.append(line)
        # 遇到行尾 ; 且不在字符串内, 简化: 直接按 ; 触发 (PG 16 dump 没有跨行字符串字面量)
        if stripped.endswith(";"):
            flush_sql()
        i += 1

    flush_sql()
    return segments


def restore(dump_path: Path, conn_str: str, verbose: bool = True):
    print(f"[parse] reading {dump_path} ({dump_path.stat().st_size/1024/1024:.1f} MB)")
    text = dump_path.read_text(encoding="utf-8")
    print(f"[parse] splitting segments...")
    t0 = time.time()
    segments = parse_dump(text)
    print(f"[parse] {len(segments)} segments in {time.time() - t0:.1f}s")

    stats = {"copy_blocks": 0, "copy_rows": 0, "sql_stmts": 0, "meta_skipped": 0}

    t0 = time.time()
    with psycopg.connect(conn_str, connect_timeout=30, autocommit=False) as conn:
        for kind, *payload in segments:
            if kind == "meta":
                stats["meta_skipped"] += 1
                continue

            if kind == "sql":
                stmt = payload[0]
                first_line = stmt.split("\n", 1)[0][:100]
                try:
                    with conn.cursor() as cur:
                        cur.execute(stmt)
                    # 每条 SQL 自己 commit (不是攒一起), 避免后面失败时回滚前面
                    conn.commit()
                except (psycopg.errors.DuplicateTable,
                        psycopg.errors.DuplicateObject,
                        psycopg.errors.DuplicateAlias,
                        psycopg.errors.UniqueViolation,
                        psycopg.errors.InvalidTableDefinition) as e:
                    # 对象已存在 / PK 已存在等 — 回滚当前语句, 跳过
                    conn.rollback()
                    stats["sql_skipped_dup"] = stats.get("sql_skipped_dup", 0) + 1
                except Exception as e:
                    print(f"  [SQL-FAIL] {type(e).__name__}: {e}")
                    print(f"  [SQL-FAIL-FIRST-LINE] {first_line}")
                    conn.rollback()
                    raise
                stats["sql_stmts"] += 1
                if verbose and stats["sql_stmts"] % 100 == 0:
                    print(f"  [sql] {stats['sql_stmts']} stmts, "
                          f"{stats['copy_rows']} copy rows, {time.time() - t0:.1f}s")
                continue

            if kind == "copy":
                copy_stmt, rows_text = payload
                with conn.cursor() as cur:
                    with cur.copy(copy_stmt) as copy:
                        # rows_text tab-separated. \\N 表示 NULL.
                        # psycopg3 copy.write_row 不自动解释 \\N, 需手动转 None
                        n_rows = 0
                        for row in rows_text.split("\n"):
                            if not row:
                                continue
                            cells = row.split("\t")
                            # 把 \\N 字符串转 None (其它保留为 str)
                            cells = [None if c == r"\N" else c for c in cells]
                            copy.write_row(cells)
                            n_rows += 1
                # 每个 COPY block 自己的事务 commit (失败时只丢自己)
                conn.commit()
                stats["copy_blocks"] += 1
                stats["copy_rows"] += n_rows
                if verbose:
                    tbl = copy_stmt.split()[1].split("(")[0]
                    print(f"  [COPY] {tbl} ~{n_rows} rows "
                          f"(total {stats['copy_rows']}, {time.time() - t0:.1f}s)")
                continue

    print()
    print(f"[DONE] {time.time() - t0:.1f}s")
    print(f"  copy blocks: {stats['copy_blocks']}, copy rows (approx): {stats['copy_rows']}")
    print(f"  sql stmts:   {stats['sql_stmts']}")
    print(f"  meta skipped: {stats['meta_skipped']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    restore(Path(args.dump), args.target)


if __name__ == "__main__":
    main()
