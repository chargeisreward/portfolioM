"""Import 指数构成.xlsx → index_constituent_snapshot + csi300_constituent_snapshot.

Each non-summary sheet is named `{index_name}_{code}`. The as_of_date is
taken from `权重基准日` column on the 汇总 sheet — different indices may
have different as_of_date (e.g. 创业板50 = 2026-06-15).
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date as _date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import IndexConstituentSnapshot, Csi300ConstituentSnapshot
from scripts.import_common import (
    ImportReport,
    normalize_columns,
    read_excel,
    safe_to_float,
    wipe_as_of,
)

logger = logging.getLogger(__name__)

# Sheets named like "沪深300_000300" or "创业板50_399673"
SHEET_CODE_RE = re.compile(r"_(\w+)$")
CSI300_CODE = "000300"


def _parse_as_of(value) -> _date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, _date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _to_exchange_label(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    if "深圳" in s or "Shenzhen" in s.lower() or s.upper() == "SZSE":
        return "SZSE"
    if "上海" in s or "Shanghai" in s.lower() or s.upper() == "SSE":
        return "SSE"
    if "香港" in s or "HK" in s.upper():
        return "HKEx"
    return s[:8]


def _stock_code_with_suffix(raw_code: str, exchange: str | None) -> str | None:
    """Add .SH/.SZ/.HK suffix to a 6-digit (or 5-digit HK) code if missing."""
    if not raw_code:
        return None
    raw = raw_code.strip()
    if "." in raw:
        # Normalize HK 4-digit codes (e.g. 0005.HK -> 00005.HK) to match HKEX convention
        if raw.upper().endswith(".HK"):
            code_part = raw.rsplit(".", 1)[0]
            if code_part.isdigit() and len(code_part) == 4:
                return f"0{code_part}.HK"
        return raw
    if not raw.isdigit():
        return raw
    if raw.isdigit():
        # Pad numeric codes to 6 digits and assign exchange by prefix / explicit label.
        if exchange == "SZSE" or raw.startswith(("0", "3", "2")):
            return f"{int(raw):06d}.SZ"
        if exchange == "SSE" or raw.startswith(("6", "9", "5")):
            return f"{int(raw):06d}.SH"
    if len(raw) == 5:
        return f"{raw}.HK"
    return raw


def _process_sheet(db: Session, sheet_name: str, df: pd.DataFrame,
                  as_of_date: _date) -> tuple[int, ImportReport | None]:
    """Process a single sheet. Returns (row_count, csi300_report_if_any)."""
    m = SHEET_CODE_RE.search(sheet_name)
    if not m:
        return 0, None
    index_code_raw = m.group(1)
    # Strip exchange suffix from index code if present (e.g. 000300.SH -> 000300)
    index_code = index_code_raw.split(".")[0]
    df = normalize_columns(df)

    col_code = next((c for c in ["成分券代码", "证券代码", "股票代码"] if c in df.columns), None)
    col_name = next((c for c in ["成分券名称", "证券名称", "股票名称"] if c in df.columns), None)
    col_weight = next((c for c in ["权重", "权重(%)"] if c in df.columns), None)
    col_exch = next((c for c in ["交易所", "上市交易所"] if c in df.columns), None)

    if not col_code:
        return 0, None

    rows: list[IndexConstituentSnapshot] = []
    csi_rows: list[Csi300ConstituentSnapshot] = []
    for _, r in df.iterrows():
        raw_code = r.get(col_code)
        if raw_code is None or (isinstance(raw_code, float) and pd.isna(raw_code)):
            continue
        try:
            sc_raw = str(int(raw_code)) if isinstance(raw_code, float) and raw_code.is_integer() else str(raw_code).strip()
        except (ValueError, TypeError):
            sc_raw = str(raw_code).strip()
        if not sc_raw or sc_raw == "nan":
            continue
        exch_label = _to_exchange_label(str(r.get(col_exch) or "") if col_exch else None)
        stock_code = _stock_code_with_suffix(sc_raw, exch_label)
        if not stock_code:
            continue
        weight = safe_to_float(r.get(col_weight)) if col_weight else None
        rows.append(
            IndexConstituentSnapshot(
                as_of_date=as_of_date,
                index_code=index_code,
                index_name=sheet_name.split("_")[0],
                stock_code=stock_code,
                stock_name=str(r.get(col_name) or "").strip() if col_name else None,
                exchange=exch_label,
                weight=weight,
            )
        )
        if index_code == CSI300_CODE:
            csi_rows.append(
                Csi300ConstituentSnapshot(
                    as_of_date=as_of_date,
                    stock_code=stock_code,
                    stock_name=str(r.get(col_name) or "").strip() if col_name else None,
                    industry_l1="其他",
                    industry_l2="其他",
                    chain_position="other",
                    growth_tier="unknown",
                    competition="unknown",
                    weight=weight,
                    source="excel",
                )
            )

    if rows:
        # Wipe existing rows for this (as_of, index_code) before bulk insert
        db.query(IndexConstituentSnapshot).filter(
            IndexConstituentSnapshot.as_of_date == as_of_date,
            IndexConstituentSnapshot.index_code == index_code,
        ).delete(synchronize_session=False)
        db.bulk_save_objects(rows)
        db.commit()

    if csi_rows:
        db.query(Csi300ConstituentSnapshot).filter(
            Csi300ConstituentSnapshot.as_of_date == as_of_date,
        ).delete(synchronize_session=False)
        db.bulk_save_objects(csi_rows)
        db.commit()

    return len(rows), None


def import_index_constituents(db: Session, source_path: Path) -> ImportReport:
    report = ImportReport(as_of_date=_date(1970, 1, 1), table="index_constituent_snapshot")
    if not source_path.exists():
        report.errors.append(f"file not found: {source_path}")
        return report

    xl = pd.ExcelFile(source_path)

    # 1. Parse 汇总 sheet for as_of_date per index
    index_dates: dict[str, _date] = {}
    if "汇总" in xl.sheet_names:
        summary = normalize_columns(pd.read_excel(xl, sheet_name="汇总"))
        sc = next((c for c in ["指数代码"] if c in summary.columns), None)
        sd = next((c for c in ["权重基准日"] if c in summary.columns), None)
        if sc and sd:
            for _, r in summary.iterrows():
                ic = str(r.get(sc) or "").strip()
                d = _parse_as_of(r.get(sd))
                if ic and d:
                    index_dates[ic.split(".")[0]] = d

    total = 0
    for sheet in xl.sheet_names:
        if sheet == "汇总":
            continue
        m = SHEET_CODE_RE.search(sheet)
        if not m:
            continue
        index_code = m.group(1).split(".")[0]
        as_of = index_dates.get(index_code) or _date(2026, 5, 29)  # 基础数据基准期5月29日 fallback
        df = pd.read_excel(xl, sheet_name=sheet)
        count, _ = _process_sheet(db, sheet, df, as_of)
        total += count
        logger.info("  sheet=%-20s as_of=%s rows=%d", sheet, as_of, count)

    report.rows_inserted = total
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
        source_path = (backend_root.parent / "sourceData" / "202605数据" / "指数构成.xlsx").resolve()
    db = SessionLocal()
    try:
        rep = import_index_constituents(db, source_path)
        print(rep)
    finally:
        db.close()


if __name__ == "__main__":
    main()