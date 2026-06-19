"""Import 基金-指数.xlsx → fund_index_map (spec §2.3)."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import FundIndexMap
from scripts.import_common import (
    ImportReport,
    normalize_columns,
    read_excel,
    safe_to_float,
    wipe_as_of,
)

logger = logging.getLogger(__name__)

CAND_FUND_CODE = ["证券代码", "基金代码"]
CAND_FUND_NAME = ["证券简称", "基金简称", "基金名称"]
CAND_BENCHMARK = ["业绩比较基准", "业绩比较基准\n[截止日期]"]
CAND_INDEX_NAME = ["跟踪指数名称", "跟踪指数"]
CAND_INDEX_CODE = ["跟踪指数代码", "跟踪指数代码\n"]


def import_fund_index_map(db: Session, as_of_date: _date, source_path: Path) -> ImportReport:
    report = ImportReport(as_of_date=as_of_date, table="fund_index_map")
    if not source_path.exists():
        report.errors.append(f"file not found: {source_path}")
        return report

    df = normalize_columns(read_excel(source_path))
    col_code = next((c for c in CAND_FUND_CODE if c in df.columns), None)
    col_name = next((c for c in CAND_FUND_NAME if c in df.columns), None)
    col_bench = next((c for c in CAND_BENCHMARK if c in df.columns), None)
    col_idx_name = next((c for c in CAND_INDEX_NAME if c in df.columns), None)
    col_idx_code = next((c for c in CAND_INDEX_CODE if c in df.columns), None)

    if not col_code or not col_idx_code:
        report.errors.append(
            f"required columns missing; found cols={df.columns.tolist()}"
        )
        return report

    wipe_as_of(db, FundIndexMap, as_of_date)

    rows: list[FundIndexMap] = []
    for _, r in df.iterrows():
        fund_code = str(r.get(col_code) or "").strip()
        if not fund_code or fund_code == "nan" or "数据来源" in fund_code:
            report.rows_skipped += 1
            continue
        idx_code = str(r.get(col_idx_code) or "").strip()
        if not idx_code or idx_code in ("--", "—"):
            report.rows_skipped += 1
            continue
        rows.append(
            FundIndexMap(
                fund_code=fund_code,
                fund_name=str(r.get(col_name) or "").strip() if col_name else None,
                benchmark_formula=str(r.get(col_bench) or "").strip() if col_bench else None,
                index_code=idx_code,
                index_name=str(r.get(col_idx_name) or "").strip() if col_idx_name else None,
                as_of_date=as_of_date,
                source="excel",
                note=None,
            )
        )

    if rows:
        db.bulk_save_objects(rows)
        db.commit()
    report.rows_inserted = len(rows)
    return report


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--source", default=None,
                    help="default: <backend>/../sourceData/202605数据/基金-指数.xlsx")
    args = ap.parse_args()

    from database import SessionLocal
    as_of = _date.fromisoformat(args.as_of_date)
    backend_root = Path(__file__).resolve().parents[1]
    if args.source:
        source_path = Path(args.source)
        if not source_path.is_absolute():
            source_path = (backend_root / args.source).resolve()
    else:
        source_path = (backend_root.parent / "sourceData" / "202605数据" / "基金-指数.xlsx").resolve()
    db = SessionLocal()
    try:
        rep = import_fund_index_map(db, as_of, source_path)
        print(rep)
        for e in rep.errors:
            print(f"  ERR: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()