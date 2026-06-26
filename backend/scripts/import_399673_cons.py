"""Import 399673_cons.xlsx → index_constituent_snapshot for index 399673.

This file uses columns: 日期 / 样本代码 / 样本简称 / 所属行业 / 总市值(亿元) / 权重（%）.
The codes are 6-digit (Shenzhen 创业板). Date is parsed from 日期 column.
Weights are populated (unlike the SZSE feed inside 指数构成.xlsx).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import IndexConstituentSnapshot
from scripts.import_common import (
    ImportReport,
    normalize_columns,
    read_excel,
    safe_to_float,
    wipe_as_of,
)

logger = logging.getLogger(__name__)

INDEX_CODE = "399673"
INDEX_NAME = "创业板50"
EXCHANGE = "SZSE"  # All 300xxx are Shenzhen 创业板


def import_399673(db: Session, source_path: Path) -> ImportReport:
    report = ImportReport(as_of_date=_date(1970, 1, 1), table=f"index_constituent_snapshot[{INDEX_CODE}]")
    if not source_path.exists():
        report.errors.append(f"file not found: {source_path}")
        return report

    df = normalize_columns(read_excel(source_path))
    if "样本代码" not in df.columns:
        report.errors.append(f"missing 样本代码; cols={df.columns.tolist()}")
        return report

    # Determine as_of_date from the 日期 column (all rows share the same date)
    # 基础数据基准期5月29日
    as_of = _date(2026, 5, 29)
    if "日期" in df.columns and not df.empty:
        v = df["日期"].iloc[0]
        if isinstance(v, _date):
            as_of = v
        elif isinstance(v, str):
            try:
                as_of = datetime.strptime(v[:10], "%Y-%m-%d").date()
            except ValueError:
                pass

    report.as_of_date = as_of

    # Wipe existing rows for this (as_of, index_code) and replace with the new data
    db.query(IndexConstituentSnapshot).filter(
        IndexConstituentSnapshot.as_of_date == as_of,
        IndexConstituentSnapshot.index_code == INDEX_CODE,
    ).delete(synchronize_session=False)

    rows: list[IndexConstituentSnapshot] = []
    for _, r in df.iterrows():
        code_raw = r.get("样本代码")
        if code_raw is None or (isinstance(code_raw, float) and pd.isna(code_raw)):
            continue
        try:
            code_str = str(int(code_raw)).zfill(6) if isinstance(code_raw, float) and code_raw.is_integer() else str(code_raw).strip()
        except (ValueError, TypeError):
            code_str = str(code_raw).strip()
        if not code_str or code_str == "nan":
            continue
        rows.append(IndexConstituentSnapshot(
            as_of_date=as_of,
            index_code=INDEX_CODE,
            index_name=INDEX_NAME,
            stock_code=f"{code_str}.SZ",  # All 300xxx → 深圳创业板
            stock_name=str(r.get("样本简称") or "").strip(),
            exchange=EXCHANGE,
            weight=safe_to_float(r.get("权重（%）") or r.get("权重")),
            source="szse_cons_xlsx",
        ))

    if rows:
        db.bulk_save_objects(rows)
        db.commit()
    report.rows_inserted = len(rows)
    logger.info("399673 import: as_of=%s rows=%d", as_of, len(rows))
    return report


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None)
    args = ap.parse_args()
    from database import SessionLocal
    backend_root = Path(__file__).resolve().parents[1]
    if args.source:
        source_path = Path(args.source)
        if not source_path.is_absolute():
            source_path = (backend_root / args.source).resolve()
    else:
        source_path = (backend_root.parent / "sourceData" / "202605数据" / "399673_cons.xlsx").resolve()
    db = SessionLocal()
    try:
        rep = import_399673(db, source_path)
        print(rep)
    finally:
        db.close()


if __name__ == "__main__":
    main()