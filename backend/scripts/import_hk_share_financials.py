"""Import 全部港股.xlsx → hk_share_financial_snapshot (spec §2.3).

User-specified columns (only these are used; duplicates ignored):
  - 所属行业名称 (申万行业 2021, L1/L2/L3)
  - 所属中证行业名称(2021) (L1/L2/L3/L4)
  - 市盈率(PE,TTM), 市净率(PB,MRQ), 市销率(PS,TTM), 股息率(TTM)
  - 收盘价 (原始币种, 元)
  - 一致预测每股收益(FY1), 一致预测每股收益(FY2)

Excel duplicates columns (raw + summary). The summary line (later occurrence)
holds real values; raw occurrences are often "--" placeholders. So we always
pick the LAST occurrence of each metric column.
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

from models import HKShareFinancialSnapshot
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

CAND_CODE = ["证券代码"]
CAND_NAME = ["证券名称"]
CAND_PE = ["市盈率(PE,TTM)"]
CAND_PB = ["市净率(PB,MRQ)"]
CAND_PS = ["市销率(PS,TTM)"]
CAND_DIV = ["股息率(TTM)"]
CAND_PRICE = ["收盘价"]
CAND_EPS1 = ["一致预测每股收益(FY1)"]
CAND_EPS2 = ["一致预测每股收益(FY2)"]


def _find_col(df: pd.DataFrame, candidates: list[str]) -> int | None:
    """Pick the LAST occurrence (positional column index)."""
    last = None
    for i, c in enumerate(df.columns):
        if c in candidates:
            last = i
    return last


def _find_col_at(df: pd.DataFrame, candidates: list[str], level: int) -> int | None:
    """For repeated columns, pick the Nth occurrence (positional column index)."""
    found: list[int] = []
    for i, c in enumerate(df.columns):
        if c in candidates:
            found.append(i)
    if 0 <= level < len(found):
        return found[level]
    return None


def _get(row, key):
    if key is None:
        return None
    try:
        if isinstance(key, int):
            return row.iloc[key]
        return row[key]
    except (KeyError, IndexError, AttributeError, TypeError):
        return None


def import_hk_share(db: Session, as_of_date: _date, source_path: Path) -> ImportReport:
    report = ImportReport(as_of_date=as_of_date, table="hk_share_financial_snapshot")
    if not source_path.exists():
        report.errors.append(f"file not found: {source_path}")
        return report

    df = normalize_columns(read_excel(source_path))
    col_code = next((c for c in CAND_CODE if c in df.columns), None)
    if not col_code:
        report.errors.append(f"missing 证券代码; cols={df.columns.tolist()}")
        return report

    col_name = _find_col(df, CAND_NAME)
    col_pe = _find_col(df, CAND_PE)
    col_pb = _find_col(df, CAND_PB)
    col_ps = _find_col(df, CAND_PS)
    col_div = _find_col(df, CAND_DIV)
    col_price = _find_col(df, CAND_PRICE)
    col_eps1 = _find_col(df, CAND_EPS1)
    col_eps2 = _find_col(df, CAND_EPS2)

    # 申万行业 (2021) — first 4 occurrences of "所属行业名称"
    col_swy_l1 = _find_col_at(df, ["所属行业名称"], 0)
    col_swy_l2 = _find_col_at(df, ["所属行业名称"], 1)
    col_swy_l3 = _find_col_at(df, ["所属行业名称"], 2)
    col_swy_l4 = _find_col_at(df, ["所属行业名称"], 3)

    # 中证行业 (2021) — first 4 occurrences of "所属中证行业名称(2021)"
    col_csi_l1 = _find_col_at(df, ["所属中证行业名称(2021)"], 0)
    col_csi_l2 = _find_col_at(df, ["所属中证行业名称(2021)"], 1)
    col_csi_l3 = _find_col_at(df, ["所属中证行业名称(2021)"], 2)
    col_csi_l4 = _find_col_at(df, ["所属中证行业名称(2021)"], 3)

    # 战略新兴产业 (2021) — first 4 occurrences of "所属战略性新兴产业"
    col_se_l1 = _find_col_at(df, ["所属战略性新兴产业"], 0)
    col_se_l2 = _find_col_at(df, ["所属战略性新兴产业"], 1)
    col_se_l3 = _find_col_at(df, ["所属战略性新兴产业"], 2)
    col_se_l4 = _find_col_at(df, ["所属战略性新兴产业"], 3)

    wipe_as_of(db, HKShareFinancialSnapshot, as_of_date)

    rows: list[HKShareFinancialSnapshot] = []
    resolved_price = 0
    eps1_count = eps2_count = 0
    for _, r in df.iterrows():
        code_raw = _get(r, col_code)
        code = str(code_raw or "").strip()
        if not code or code == "nan":
            report.rows_skipped += 1
            continue
        baseline, current, current_date = resolve_price_pair(db, code, as_of_date)
        if not baseline and col_price is not None:
            excel_price = safe_to_float(_get(r, col_price))
            baseline = excel_price if excel_price and excel_price > 0 else None
        if baseline and current:
            resolved_price += 1
        pe_baseline = safe_to_float(_get(r, col_pe))
        pb_baseline = safe_to_float(_get(r, col_pb))
        ps_baseline = safe_to_float(_get(r, col_ps))
        div_baseline = safe_to_float(_get(r, col_div))
        eps1 = safe_to_float(_get(r, col_eps1))
        eps2 = safe_to_float(_get(r, col_eps2))
        if eps1 is not None:
            eps1_count += 1
        if eps2 is not None:
            eps2_count += 1
        swy1 = _get(r, col_swy_l1)
        swy2 = _get(r, col_swy_l2)
        swy3 = _get(r, col_swy_l3)
        swy4 = _get(r, col_swy_l4)
        csi1 = _get(r, col_csi_l1)
        csi2 = _get(r, col_csi_l2)
        csi3 = _get(r, col_csi_l3)
        csi4 = _get(r, col_csi_l4)
        se1 = _get(r, col_se_l1)
        se2 = _get(r, col_se_l2)
        se3 = _get(r, col_se_l3)
        se4 = _get(r, col_se_l4)
        name = _get(r, col_name)
        rows.append(
            HKShareFinancialSnapshot(
                as_of_date=as_of_date,
                stock_code=code,
                stock_name=str(name or "").strip() if name else None,
                pe_ttm=pe_baseline,
                pb_mrq=pb_baseline,
                ps_ttm=ps_baseline,
                dividend_yield=div_baseline,
                market_cap=None,
                eps_fy1=eps1,
                eps_fy2=eps2,
                # 申万 2021 (L1-L4)
                swy_l1=str(swy1 or "").strip() if swy1 else None,
                swy_l2=str(swy2 or "").strip() if swy2 else None,
                swy_l3=str(swy3 or "").strip() if swy3 else None,
                swy_l4=str(swy4 or "").strip() if swy4 else None,
                # 中证 2021 (L1-L4)
                csi_l1=str(csi1 or "").strip() if csi1 else None,
                csi_l2=str(csi2 or "").strip() if csi2 else None,
                csi_l3=str(csi3 or "").strip() if csi3 else None,
                csi_l4=str(csi4 or "").strip() if csi4 else None,
                # 战略新兴产业 (L1-L4)
                se_l1=str(se1 or "").strip() if se1 else None,
                se_l2=str(se2 or "").strip() if se2 else None,
                se_l3=str(se3 or "").strip() if se3 else None,
                se_l4=str(se4 or "").strip() if se4 else None,
                # Backward compat aliases
                industry_l1=str(swy1 or "").strip() if swy1 else None,
                industry_l2=str(swy2 or "").strip() if swy2 else None,
                industry_l3=str(swy3 or "").strip() if swy3 else None,
                industry_l4=str(swy4 or "").strip() if swy4 else None,
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
    report.note = f"prices_resolved={resolved_price}/{len(rows)} eps_fy1={eps1_count} eps_fy2={eps2_count}"
    logger.info("  hk_share rows=%d prices_resolved=%d eps_fy1=%d eps_fy2=%d",
                len(rows), resolved_price, eps1_count, eps2_count)
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
        source_path = (backend_root.parent / "sourceData" / "202605数据" / "全部港股.xlsx").resolve()
    as_of = _date.fromisoformat(args.as_of_date)
    db = SessionLocal()
    try:
        rep = import_hk_share(db, as_of, source_path)
        print(rep)
    finally:
        db.close()


if __name__ == "__main__":
    main()
