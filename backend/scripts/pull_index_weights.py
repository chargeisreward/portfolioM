"""pull_index_weights.py — 项目内指数权重建模与落库 (spec user follow-up).

参照 sourceData/download_index_cons.py 的方法，通过 akshare 拉取指数成分股权重数据。
存储到 index_constituent_snapshot.weight 字段（新增 FLOAT 列）。

数据源：
  - 中证指数（CSI）：ak.index_stock_cons_weight_csindex(symbol=code)
    → 返回日期、成分券代码、成分券名称、权重（%）
  - 深交所（SZSE）：ak.index_stock_cons(symbol=code)
    → 仅成分股名单，无权重

数据落到 index_constituent_snapshot 表中，
与 5/29 持仓快照 (as_of_date=2026-05-29) 同表存。

用法:
  python scripts/pull_index_weights.py                       # 拉所有 12 个指数
  python scripts/pull_index_weights.py --codes 000300 000510  # 指定
  python scripts/pull_index_weights.py --as-of-date 2026-05-29
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import SessionLocal
from models import IndexConstituentSnapshot

logger = logging.getLogger(__name__)


INDEX_CONFIG = {
    "000300": "沪深300",
    "000510": "中证A500",
    "000688": "科创50",
    "000685": "科创芯片",
    "931160": "通信设备",
    "H30269": "红利低波",
    "931468": "红利质量",
    "000813": "细分化工",
    "931994": "电网设备主题",
    "930914": "港股通高股息",
    "931233": "港股通央企红利",
    "399673": "创业板50",
}


def _to_exchange_label(s):
    if not s:
        return None
    s = str(s).strip()
    if "深圳" in s or s.upper() == "SZSE" or "Shenzhen" in s.lower():
        return "SZSE"
    if "上海" in s or s.upper() == "SSE" or "Shanghai" in s.lower():
        return "SSE"
    if "香港" in s or "HK" in s.upper():
        return "HKEx"
    return s[:8]


def _pad_stock_code(raw: str, exchange: str) -> str:
    """A 6-digit + .SH/.SZ, HK 5-digit + .HK."""
    raw = str(raw).strip()
    if not raw.isdigit():
        return raw
    if len(raw) <= 5:
        return f"{raw.zfill(5)}.HK"
    if raw.startswith(("6", "9", "5")):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def _safe_float(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _pull_csi_weights(idx_code: str, idx_name: str) -> list[dict]:
    """akshare index_stock_cons_weight_csindex — 含权重。"""
    import akshare as ak
    df = ak.index_stock_cons_weight_csindex(symbol=idx_code)
    df.columns = [str(c).strip() for c in df.columns]
    base_date_col = next((c for c in df.columns if "日期" in c), df.columns[0])
    code_col = next((c for c in df.columns if "成分券代码" in c), None)
    name_col = next((c for c in df.columns if "成分券名称" in c), None)
    idx_name_col = next((c for c in df.columns if "指数名称" in c), None)
    exch_col = next((c for c in df.columns if "交易所" in c and "英文" not in c), None)
    weight_col = next((c for c in df.columns if c == "权重"), None)
    if not code_col or not weight_col:
        logger.error("[%s] missing required cols: %s", idx_code, df.columns.tolist())
        return []

    rows = []
    base_date = df[base_date_col].iloc[0]
    if isinstance(base_date, str):
        try:
            base_date = datetime.strptime(base_date[:10], "%Y-%m-%d").date()
        except ValueError:
            base_date = _date(2026, 5, 29)
    elif hasattr(base_date, "date"):
        base_date = base_date.date()

    for _, r in df.iterrows():
        raw_code = str(r.get(code_col)).strip()
        exch_label = _to_exchange_label(r.get(exch_col)) if exch_col else None
        stock_code = _pad_stock_code(raw_code, exch_label or "")
        weight = _safe_float(r.get(weight_col))
        rows.append({
            "as_of_date": base_date,
            "index_code": idx_code,
            "index_name": str(r.get(idx_name_col) or idx_name).strip() if idx_name_col else idx_name,
            "stock_code": stock_code,
            "stock_name": str(r.get(name_col) or "").strip() if name_col else None,
            "exchange": exch_label,
            "weight": weight,
            "source": "akshare_csi",
        })
    return rows


def _pull_szse_cons(idx_code: str, idx_name: str) -> list[dict]:
    """akshare index_stock_cons — 仅成分股名单，无权重。"""
    import akshare as ak
    df = ak.index_stock_cons(symbol=idx_code)
    df.columns = [str(c).strip() for c in df.columns]
    base_date_col = next((c for c in df.columns if "纳入" in c or "日期" in c), df.columns[0])
    code_col = next((c for c in df.columns if "品种代码" in c), None)
    name_col = next((c for c in df.columns if "品种名称" in c), None)
    if not code_col:
        logger.error("[%s] missing 品种代码 col; cols=%s", idx_code, df.columns.tolist())
        return []
    rows = []
    base_date = df[base_date_col].iloc[0]
    if isinstance(base_date, str):
        try:
            base_date = datetime.strptime(base_date[:10], "%Y-%m-%d").date()
        except ValueError:
            base_date = _date(2026, 5, 29)
    elif hasattr(base_date, "date"):
        base_date = base_date.date()
    for _, r in df.iterrows():
        raw_code = str(r.get(code_col)).strip()
        stock_code = _pad_stock_code(raw_code, "SZSE")
        rows.append({
            "as_of_date": base_date,
            "index_code": idx_code,
            "index_name": idx_name,
            "stock_code": stock_code,
            "stock_name": str(r.get(name_col) or "").strip() if name_col else None,
            "exchange": "SZSE",
            "weight": None,   # SZSE 无权重
            "source": "akshare_szse",
        })
    return rows


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of-date", default="2026-05-29",
                    help="as_of_date for snapshot (overrides akshare-reported date)")
    ap.add_argument("--codes", nargs="*", help="指定指数代码，默认全部")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    as_of = datetime.strptime(args.as_of_date, "%Y-%m-%d").date()
    codes = args.codes or list(INDEX_CONFIG.keys())
    db = SessionLocal()
    try:
        total_inserted = 0
        for code in codes:
            name = INDEX_CONFIG.get(code, code)
            logger.info(">>> %s (%s)", code, name)
            try:
                if code == "399673":
                    rows = _pull_szse_cons(code, name)
                else:
                    rows = _pull_csi_weights(code, name)
            except Exception as e:
                logger.error("[%s] pull failed: %s", code, e)
                continue
            if not rows:
                logger.warning("[%s] no rows", code)
                continue
            # Override as_of_date for deterministic storage
            for r in rows:
                r["as_of_date"] = as_of
            # Wipe existing for this (as_of, index_code) to keep one source-of-truth
            db.query(IndexConstituentSnapshot).filter(
                IndexConstituentSnapshot.as_of_date == as_of,
                IndexConstituentSnapshot.index_code == code,
            ).delete(synchronize_session=False)
            db.commit()
            # Bulk insert (drop "source" since IndexConstituentSnapshot has no such col)
            objs = []
            for r in rows:
                r2 = {k: v for k, v in r.items() if k != "source"}
                objs.append(IndexConstituentSnapshot(**r2))
            db.bulk_save_objects(objs)
            db.commit()
            total_inserted += len(objs)
            with_w = sum(1 for r in rows if r.get("weight") is not None)
            logger.info("    inserted %d rows (%d with weight, %d without)",
                        len(objs), with_w, len(objs) - with_w)
        logger.info("DONE: total_inserted=%d", total_inserted)
    finally:
        db.close()


if __name__ == "__main__":
    main()