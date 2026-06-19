"""Import 全部A股.xlsx → a_share_financial_snapshot.

At import time, resolve baseline_price (close on as_of_date) and current_price
(prev close before today) from price_cache, then write pe_ttm_dynamic etc.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import AShareFinancialSnapshot
from scripts.import_common import (
    ImportReport,
    compute_dynamic,
    normalize_columns,
    read_excel,
    resolve_price_pair,
    safe_to_float,
    wipe_as_of,
)

logger = logging.getLogger(__name__)

CAND_CODE = ["证券代码", "股票代码"]
CAND_NAME = ["证券名称", "股票名称"]
CAND_PE = ["市盈率PE(TTM)", "PE(TTM)", "市盈率(PE,TTM)"]
CAND_PB = ["市净率PB(扣除商誉)", "市净率PB(最新)", "PB(MRQ)", "市净率(PB,MRQ)"]
CAND_PS = ["市销率PS(TTM)", "PS(TTM)", "市销率(PS,TTM)"]
CAND_DIV = ["股息率(TTM)", "股息率"]
CAND_MCAP = ["总市值", "总市值(亿元)"]
CAND_EPS1 = ["一致预期每股盈利(FY1)", "EPS(FY1)"]
CAND_EPS2 = ["一致预期每股盈利(FY2)", "EPS(FY2)"]
CAND_PRICE = ["收盘价"]  # Excel's 5/29 close — used as baseline_price fallback


def _find_col_at(df: pd.DataFrame, candidates: list[str], level: int):
    """For repeated columns, pick the Nth occurrence as POSITIONAL INDEX (int).
    Returns None if not enough occurrences.
    Using positional index avoids the duplicate-column-name Series ambiguity.
    """
    found: list[int] = []
    for i, c in enumerate(df.columns):
        if c in candidates:
            found.append(i)
    if 0 <= level < len(found):
        return found[level]
    return None


def _g(row, key):
    """Get cell value by either column name or positional index."""
    if key is None:
        return None
    try:
        if isinstance(key, int):
            return row.iloc[key]
        return row[key]
    except (KeyError, IndexError, AttributeError, TypeError, ValueError):
        return None


def import_a_share(db: Session, as_of_date: _date, source_path: Path) -> ImportReport:
    report = ImportReport(as_of_date=as_of_date, table="a_share_financial_snapshot")
    if not source_path.exists():
        report.errors.append(f"file not found: {source_path}")
        return report

    df = normalize_columns(read_excel(source_path))
    col_code = next((c for c in CAND_CODE if c in df.columns), None)
    if not col_code:
        report.errors.append(f"missing 证券代码; cols={df.columns.tolist()}")
        return report
    col_name = next((c for c in CAND_NAME if c in df.columns), None)
    col_pe = next((c for c in CAND_PE if c in df.columns), None)
    col_pb = next((c for c in CAND_PB if c in df.columns), None)
    col_ps = next((c for c in CAND_PS if c in df.columns), None)
    col_div = next((c for c in CAND_DIV if c in df.columns), None)
    col_mcap = next((c for c in CAND_MCAP if c in df.columns), None)
    col_eps1 = next((c for c in CAND_EPS1 if c in df.columns), None)
    col_eps2 = next((c for c in CAND_EPS2 if c in df.columns), None)
    col_price = next((c for c in CAND_PRICE if c in df.columns), None)

    # 所属行业名称 (4 levels, 申万) — first 4 occurrences (positional)
    col_swy_l1 = _find_col_at(df, ["所属行业名称"], 0)
    col_swy_l2 = _find_col_at(df, ["所属行业名称"], 1)
    col_swy_l3 = _find_col_at(df, ["所属行业名称"], 2)
    col_swy_l4 = _find_col_at(df, ["所属行业名称"], 3)

    # 所属战略性新兴产业 (3 levels) — first 3 occurrences
    col_se_l1 = _find_col_at(df, ["所属战略性新兴产业"], 0)
    col_se_l2 = _find_col_at(df, ["所属战略性新兴产业"], 1)
    col_se_l3 = _find_col_at(df, ["所属战略性新兴产业"], 2)

    # 所属中证行业名称(2021) (4 levels) — first 4 occurrences
    col_csi_l1 = _find_col_at(df, ["所属中证行业名称(2021)"], 0)
    col_csi_l2 = _find_col_at(df, ["所属中证行业名称(2021)"], 1)
    col_csi_l3 = _find_col_at(df, ["所属中证行业名称(2021)"], 2)
    col_csi_l4 = _find_col_at(df, ["所属中证行业名称(2021)"], 3)

    wipe_as_of(db, AShareFinancialSnapshot, as_of_date)

    rows: list[AShareFinancialSnapshot] = []
    resolved_price = 0
    for _, r in df.iterrows():
        code = str(r.get(col_code) or "").strip()
        if not code or code == "nan":
            report.rows_skipped += 1
            continue
        baseline, current, current_date = resolve_price_pair(db, code, as_of_date)
        if not baseline and col_price:
            baseline = safe_to_float(r.get(col_price))
            baseline = baseline if baseline and baseline > 0 else None
        if baseline and current:
            resolved_price += 1
        pe_baseline = safe_to_float(r.get(col_pe)) if col_pe else None
        pb_baseline = safe_to_float(r.get(col_pb)) if col_pb else None
        ps_baseline = safe_to_float(r.get(col_ps)) if col_ps else None
        rows.append(
            AShareFinancialSnapshot(
                as_of_date=as_of_date,
                stock_code=code,
                stock_name=str(r.get(col_name) or "").strip() if col_name else None,
                pe_ttm=pe_baseline,
                pb_mrq=pb_baseline,
                ps_ttm=ps_baseline,
                dividend_yield=safe_to_float(r.get(col_div)) if col_div else None,
                market_cap=safe_to_float(r.get(col_mcap)) if col_mcap else None,
                eps_fy1=safe_to_float(r.get(col_eps1)) if col_eps1 else None,
                eps_fy2=safe_to_float(r.get(col_eps2)) if col_eps2 else None,
                # 申万 4 级
                swy_l1=str(_g(r, col_swy_l1) or "").strip() if col_swy_l1 is not None else None,
                swy_l2=str(_g(r, col_swy_l2) or "").strip() if col_swy_l2 is not None else None,
                swy_l3=str(_g(r, col_swy_l3) or "").strip() if col_swy_l3 is not None else None,
                swy_l4=str(_g(r, col_swy_l4) or "").strip() if col_swy_l4 is not None else None,
                # 中证 4 级
                csi_l1=str(_g(r, col_csi_l1) or "").strip() if col_csi_l1 is not None else None,
                csi_l2=str(_g(r, col_csi_l2) or "").strip() if col_csi_l2 is not None else None,
                csi_l3=str(_g(r, col_csi_l3) or "").strip() if col_csi_l3 is not None else None,
                csi_l4=str(_g(r, col_csi_l4) or "").strip() if col_csi_l4 is not None else None,
                # 战新 3 级 (A 股无 L4)
                se_l1=str(_g(r, col_se_l1) or "").strip() if col_se_l1 is not None else None,
                se_l2=str(_g(r, col_se_l2) or "").strip() if col_se_l2 is not None else None,
                se_l3=str(_g(r, col_se_l3) or "").strip() if col_se_l3 is not None else None,
                # Backward compat
                industry_sw=str(_g(r, col_swy_l1) or "").strip() if col_swy_l1 is not None else None,
                baseline_price=baseline,
                current_price=current,
                current_price_date=current_date,
                pe_ttm_dynamic=compute_dynamic(pe_baseline, baseline, current),
                pb_mrq_dynamic=compute_dynamic(pb_baseline, baseline, current),
                ps_ttm_dynamic=compute_dynamic(ps_baseline, baseline, current),
                source="excel",
            )
        )

    if rows:
        db.bulk_save_objects(rows)
        db.commit()
    report.rows_inserted = len(rows)
    report.note = f"prices_resolved={resolved_price}/{len(rows)}"
    logger.info("  a_share rows=%d prices_resolved=%d", len(rows), resolved_price)
    return report


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of-date", required=True)
    ap.add_argument("--source", default=None)
    args = ap.parse_args()
    from database import SessionLocal
    backend_root = Path(__file__).resolve().parents[1]
    if args.source:
        source_path = Path(args.source)
        if not source_path.is_absolute():
            source_path = (backend_root / args.source).resolve()
    else:
        source_path = (backend_root.parent / "sourceData" / "202605数据" / "全部A股.xlsx").resolve()
    as_of = _date.fromisoformat(args.as_of_date)
    db = SessionLocal()
    try:
        rep = import_a_share(db, as_of, source_path)
        print(rep)
    finally:
        db.close()


if __name__ == "__main__":
    main()