"""
Aggregation engine (spec §3.4–§3.6).

Virtual-earnings PE rule:
    virtual_earnings = SUM(amount_cny / pe_dynamic)
    pe_weighted      = virtual_earnings / SUM(amount_cny)

NEVER use weighted-average PE.
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    AggregationCache,
    AggregationTimeseries,
    AShareFinancialSnapshot,
    Csi300ConstituentSnapshot,
    FullHoldingSnapshot,
    HKShareFinancialSnapshot,
)

logger = logging.getLogger(__name__)

DIM_COL = {
    # 申万 (A 4-level, HK 4-level)
    "swy1": "swy_l1",
    "swy2": "swy_l2",
    "swy3": "swy_l3",
    "swy4": "swy_l4",
    # 中证 (A 4-level, HK 4-level)
    "csi1": "csi_l1",
    "csi2": "csi_l2",
    "csi3": "csi_l3",
    "csi4": "csi_l4",
    # 战略新兴产业 (A 3-level, HK 4-level)
    "se1": "se_l1",
    "se2": "se_l2",
    "se3": "se_l3",
    "se4": "se_l4",
    # legacy
    "l1": "swy_l1",
    "l2": "swy_l2",
    "chain": "chain_position",
    "growth_tier": "growth_tier",
    "competition": "competition",
}


# ---------- helpers ----------

def _pad_csi_code(raw: str) -> str:
    raw = raw.split(".")[0]
    if not raw.isdigit():
        return raw
    return raw.zfill(6)


def resolve_industry_for_stock(db: Session, stock_code: str) -> tuple[str, str]:
    code_norm = stock_code.split(".")[0]
    h_snap = db.query(HKShareFinancialSnapshot).filter(
        HKShareFinancialSnapshot.stock_code.like(f"{code_norm}%"),
    ).first()
    if h_snap:
        return (h_snap.swy_l1 or h_snap.industry_l1 or "其他",
                h_snap.swy_l2 or h_snap.industry_l2 or "其他")
    padded = _pad_csi_code(stock_code)
    for suffix in (".SZ", ".SH"):
        candidate = f"{padded}{suffix}"
        db.query(AShareFinancialSnapshot).filter_by(stock_code=candidate).first()
    from models import StockFinancial
    sf = db.query(StockFinancial).filter(
        StockFinancial.stock_code.like(f"{code_norm}%"),
    ).first()
    if sf and sf.industry_sw:
        return (sf.industry_sw, sf.industry_sw)
    return ("其他", "其他")


def resolve_dynamic_metrics_for_stock(db: Session, stock_code: str):
    code_norm = stock_code.split(".")[0]
    h_snap = db.query(HKShareFinancialSnapshot).filter(
        HKShareFinancialSnapshot.stock_code.like(f"{code_norm}%"),
    ).first()
    if h_snap:
        return h_snap.pe_ttm_dynamic, h_snap.pb_mrq_dynamic, h_snap.ps_ttm_dynamic
    padded = _pad_csi_code(stock_code)
    for suffix in (".SZ", ".SH"):
        candidate = f"{padded}{suffix}"
        snap = db.query(AShareFinancialSnapshot).filter_by(stock_code=candidate).first()
        if snap:
            return snap.pe_ttm_dynamic, snap.pb_mrq_dynamic, snap.ps_ttm_dynamic
    return None, None, None


# ---------- bucket math ----------

def _build_cache_rows(as_of_date, scope, dim, bucket, total_amount):
    rows: list[AggregationCache] = []
    for key, b in bucket.items():
        amt = b["amount"]
        virt_pe = b["virt_pe"]
        virt_pb = b["virt_pb"]
        virt_ps = b["virt_ps"]
        rows.append(AggregationCache(
            as_of_date=as_of_date,
            scope=scope,
            dimension=dim,
            key=str(key),
            stock_count=len(b["stocks"]),
            amount_cny=round(amt, 4),
            weight_pct=round(amt / total_amount * 100, 4) if total_amount else 0.0,
            virtual_earnings=round(virt_pe, 4),
            # PE = amount / (amount/PE) — inverse of earnings yield
            pe_weighted=round(amt / virt_pe, 4) if virt_pe else None,
            pe_simple_avg=None,
            pb_weighted=round(amt / virt_pb, 4) if virt_pb else None,
            ps_weighted=round(amt / virt_ps, 4) if virt_ps else None,
        ))
    # _total row
    sum_vp = sum(b["virt_pe"] for b in bucket.values())
    sum_vpb = sum(b["virt_pb"] for b in bucket.values())
    sum_vps = sum(b["virt_ps"] for b in bucket.values())
    total_stocks = sum(len(b["stocks"]) for b in bucket.values())
    rows.append(AggregationCache(
        as_of_date=as_of_date,
        scope=scope,
        dimension=dim,
        key="_total",
        stock_count=total_stocks,
        amount_cny=round(total_amount, 4),
        weight_pct=100.0,
        virtual_earnings=round(sum_vp, 4),
        pe_weighted=round(total_amount / sum_vp, 4) if sum_vp else None,
        pe_simple_avg=None,
        pb_weighted=round(total_amount / sum_vpb, 4) if sum_vpb else None,
        ps_weighted=round(total_amount / sum_vps, 4) if sum_vps else None,
    ))
    return rows


# ---------- portfolio ----------

def _norm_bucket_key(k: str | None) -> str:
    """Normalize bucket key — treat null/'--'/empty/'其他' as '其他'."""
    if not k or k in ("--", "—", "nan", "None", ""):
        return "其他"
    return k


def _bucket_portfolio(db: Session, as_of_date: _date, col: str, market: str = "A+H",
                      user_id: int | None = None):
    """Merge per-stock from FullHoldingSnapshot, then bucket by `col`.

    Excludes source_type IN ('undrilled_fund', 'cash') from metric rows —
    only drilled_fund + direct_stock rows count toward PE/PB/PS/amount.

    market filter:
      'A+H' (default) — all stocks
      'A'           — A-share only (stock_code endswith .SH or .SZ)
      'H'           — HK only (stock_code endswith .HK)
    """
    METRIC_SOURCES = ("drilled_fund", "direct_stock")
    q = db.query(
        FullHoldingSnapshot.stock_code,
        FullHoldingSnapshot.swy_l1, FullHoldingSnapshot.swy_l2,
        FullHoldingSnapshot.swy_l3, FullHoldingSnapshot.swy_l4,
        FullHoldingSnapshot.csi_l1, FullHoldingSnapshot.csi_l2,
        FullHoldingSnapshot.csi_l3, FullHoldingSnapshot.csi_l4,
        FullHoldingSnapshot.se_l1, FullHoldingSnapshot.se_l2,
        FullHoldingSnapshot.se_l3, FullHoldingSnapshot.se_l4,
        FullHoldingSnapshot.chain_position,
        FullHoldingSnapshot.growth_tier,
        FullHoldingSnapshot.competition,
        func.sum(FullHoldingSnapshot.amount_cny).label("amount"),
        func.max(FullHoldingSnapshot.pe_ttm_dynamic).label("pe_d"),
        func.max(FullHoldingSnapshot.pb_mrq_dynamic).label("pb_d"),
        func.max(FullHoldingSnapshot.ps_ttm_dynamic).label("ps_d"),
    ).filter(
        FullHoldingSnapshot.as_of_date == as_of_date,
        FullHoldingSnapshot.source_type.in_(METRIC_SOURCES),
    )
    if user_id is not None:
        q = q.filter(FullHoldingSnapshot.user_id == user_id)
    if market == "A":
        q = q.filter(
            (FullHoldingSnapshot.stock_code.like("%.SH")) |
            (FullHoldingSnapshot.stock_code.like("%.SZ"))
        )
    elif market == "H":
        q = q.filter(FullHoldingSnapshot.stock_code.like("%.HK"))
    rows = q.group_by(
        FullHoldingSnapshot.stock_code,
        FullHoldingSnapshot.swy_l1, FullHoldingSnapshot.swy_l2,
        FullHoldingSnapshot.swy_l3, FullHoldingSnapshot.swy_l4,
        FullHoldingSnapshot.csi_l1, FullHoldingSnapshot.csi_l2,
        FullHoldingSnapshot.csi_l3, FullHoldingSnapshot.csi_l4,
        FullHoldingSnapshot.se_l1, FullHoldingSnapshot.se_l2,
        FullHoldingSnapshot.se_l3, FullHoldingSnapshot.se_l4,
        FullHoldingSnapshot.chain_position,
        FullHoldingSnapshot.growth_tier,
        FullHoldingSnapshot.competition,
    ).all()
    bucket: dict[str, dict] = {}
    total_amount = 0.0
    for r in rows:
        swy_l1 = _norm_bucket_key(r.swy_l1)
        swy_l2 = _norm_bucket_key(r.swy_l2)
        swy_l3 = _norm_bucket_key(r.swy_l3)
        swy_l4 = _norm_bucket_key(r.swy_l4)
        csi_l1 = _norm_bucket_key(r.csi_l1)
        csi_l2 = _norm_bucket_key(r.csi_l2)
        csi_l3 = _norm_bucket_key(r.csi_l3)
        csi_l4 = _norm_bucket_key(r.csi_l4)
        se_l1 = _norm_bucket_key(r.se_l1)
        se_l2 = _norm_bucket_key(r.se_l2)
        se_l3 = _norm_bucket_key(r.se_l3)
        se_l4 = _norm_bucket_key(r.se_l4)
        chain, growth, comp = r.chain_position, r.growth_tier, r.competition
        if swy_l1 == "其他":
            swy_l1, swy_l2 = resolve_industry_for_stock(db, r.stock_code)
        pe_d, pb_d, ps_d = r.pe_d, r.pb_d, r.ps_d
        row_dict = {
            "swy_l1": swy_l1, "swy_l2": swy_l2, "swy_l3": swy_l3, "swy_l4": swy_l4,
            "csi_l1": csi_l1, "csi_l2": csi_l2, "csi_l3": csi_l3, "csi_l4": csi_l4,
            "se_l1": se_l1, "se_l2": se_l2, "se_l3": se_l3, "se_l4": se_l4,
            "industry_l1": swy_l1, "industry_l2": swy_l2,
            "chain_position": chain, "growth_tier": growth, "competition": comp,
        }
        key = row_dict.get(col) or "_unknown"
        b = bucket.setdefault(key, {"amount": 0.0, "stocks": set(),
                                     "virt_pe": 0.0, "virt_pb": 0.0, "virt_ps": 0.0})
        amt = r.amount or 0.0
        b["amount"] += amt
        b["stocks"].add(r.stock_code)
        if amt > 0:
            if pe_d and pe_d > 0:
                b["virt_pe"] += amt / pe_d
            if pb_d and pb_d > 0:
                b["virt_pb"] += amt / pb_d
            if ps_d and ps_d > 0:
                b["virt_ps"] += amt / ps_d
        total_amount += amt
    return bucket, total_amount


def _bucket_csi300(db: Session, as_of_date: _date, col: str):
    rows = db.query(
        Csi300ConstituentSnapshot.stock_code,
        Csi300ConstituentSnapshot.swy_l1,
        Csi300ConstituentSnapshot.swy_l2,
        Csi300ConstituentSnapshot.swy_l3,
        Csi300ConstituentSnapshot.csi_l1,
        Csi300ConstituentSnapshot.csi_l2,
        Csi300ConstituentSnapshot.csi_l3,
        Csi300ConstituentSnapshot.csi_l4,
        Csi300ConstituentSnapshot.chain_position,
        Csi300ConstituentSnapshot.growth_tier,
        Csi300ConstituentSnapshot.competition,
        func.max(Csi300ConstituentSnapshot.weight).label("weight"),
        func.max(Csi300ConstituentSnapshot.pe_ttm_dynamic).label("pe_d"),
        func.max(Csi300ConstituentSnapshot.pb_mrq_dynamic).label("pb_d"),
        func.max(Csi300ConstituentSnapshot.ps_ttm_dynamic).label("ps_d"),
    ).filter(
        Csi300ConstituentSnapshot.as_of_date == as_of_date,
    ).group_by(
        Csi300ConstituentSnapshot.stock_code,
        Csi300ConstituentSnapshot.swy_l1,
        Csi300ConstituentSnapshot.swy_l2,
        Csi300ConstituentSnapshot.swy_l3,
        Csi300ConstituentSnapshot.csi_l1,
        Csi300ConstituentSnapshot.csi_l2,
        Csi300ConstituentSnapshot.csi_l3,
        Csi300ConstituentSnapshot.csi_l4,
        Csi300ConstituentSnapshot.chain_position,
        Csi300ConstituentSnapshot.growth_tier,
        Csi300ConstituentSnapshot.competition,
    ).all()
    bucket = {}
    total_amount = 0.0
    for r in rows:
        swy_l1 = _norm_bucket_key(r.swy_l1)
        swy_l2 = _norm_bucket_key(r.swy_l2)
        swy_l3 = _norm_bucket_key(r.swy_l3)
        csi_l1 = _norm_bucket_key(r.csi_l1)
        csi_l2 = _norm_bucket_key(r.csi_l2)
        csi_l3 = _norm_bucket_key(r.csi_l3)
        csi_l4 = _norm_bucket_key(r.csi_l4)
        if swy_l1 == "其他":
            swy_l1, swy_l2 = resolve_industry_for_stock(db, r.stock_code)
            pe_d, pb_d, ps_d = resolve_dynamic_metrics_for_stock(db, r.stock_code)
        else:
            pe_d, pb_d, ps_d = r.pe_d, r.pb_d, r.ps_d
        row_dict = {
            "swy_l1": swy_l1,
            "swy_l2": swy_l2,
            "swy_l3": swy_l3,
            "csi_l1": csi_l1,
            "csi_l2": csi_l2,
            "csi_l3": csi_l3,
            "csi_l4": csi_l4,
            "industry_l1": swy_l1,
            "industry_l2": swy_l2,
            "chain_position": r.chain_position,
            "growth_tier": r.growth_tier,
            "competition": r.competition,
        }
        key = row_dict.get(col) or "_unknown"
        # Price-adjusted weight: 5/29 weight × current/baseline ratio.
        # Fallback to baseline weight when prices unavailable.
        amt = r.weight or 0.0
        code_norm = r.stock_code.split(".")[0]
        baseline_p = current_p = None
        from models import AShareFinancialSnapshot, HKShareFinancialSnapshot as HKFS
        a_snap = db.query(AShareFinancialSnapshot).filter_by(stock_code=f"{code_norm}.SZ", as_of_date=as_of_date).first()
        if not a_snap:
            a_snap = db.query(AShareFinancialSnapshot).filter_by(stock_code=f"{code_norm}.SH", as_of_date=as_of_date).first()
        h_snap = db.query(HKFS).filter_by(stock_code=f"{code_norm}.HK", as_of_date=as_of_date).first()
        if not h_snap and code_norm.isdigit():
            h_snap = db.query(HKFS).filter_by(stock_code=f"{code_norm.zfill(5)}.HK", as_of_date=as_of_date).first()
        snap = a_snap or h_snap
        if snap:
            baseline_p = snap.baseline_price
            current_p = snap.current_price
        if baseline_p and current_p and baseline_p > 0:
            amt = amt * (current_p / baseline_p)
        b = bucket.setdefault(key, {"amount": 0.0, "stocks": set(),
                                     "virt_pe": 0.0, "virt_pb": 0.0, "virt_ps": 0.0})
        b["amount"] += amt
        b["stocks"].add(r.stock_code)
        if amt > 0:
            if pe_d and pe_d > 0:
                b["virt_pe"] += amt / pe_d
            if pb_d and pb_d > 0:
                b["virt_pb"] += amt / pb_d
            if ps_d and ps_d > 0:
                b["virt_ps"] += amt / ps_d
        total_amount += amt
    return bucket, total_amount


# ---------- public ----------

def aggregate_dimension(db: Session, as_of_date: _date, scope: str, dim: str,
                        market: str = "A+H", user_id: int | None = None):
    if dim not in DIM_COL:
        raise ValueError(f"unknown dim: {dim}")
    col = DIM_COL[dim]
    if scope == "portfolio":
        bucket, total = _bucket_portfolio(db, as_of_date, col, market=market, user_id=user_id)
    elif scope == "csi300":
        bucket, total = _bucket_csi300(db, as_of_date, col)
    else:
        raise ValueError(f"unknown scope: {scope}")
    return _build_cache_rows(as_of_date, scope, dim, bucket, total)


def upsert_dimension(db: Session, as_of_date: _date, scope: str, dim: str):
    db.query(AggregationCache).filter(
        AggregationCache.as_of_date == as_of_date,
        AggregationCache.scope == scope,
        AggregationCache.dimension == dim,
    ).delete(synchronize_session=False)
    rows = aggregate_dimension(db, as_of_date, scope, dim)
    if rows:
        db.bulk_save_objects(rows)
    db.commit()


def refresh_all_dimensions(db: Session, as_of_date: _date, scopes=("portfolio", "csi300")):
    for scope in scopes:
        for dim in DIM_COL:
            upsert_dimension(db, as_of_date, scope, dim)
            logger.info("aggregated scope=%s dim=%s", scope, dim)


def write_timeseries_for_day(db: Session, calc_date: _date, business_date: _date):
    for scope in ("portfolio", "csi300"):
        row = db.query(AggregationCache).filter(
            AggregationCache.as_of_date == business_date,
            AggregationCache.scope == scope,
            AggregationCache.dimension == "l1",
            AggregationCache.key == "_total",
        ).first()
        if not row:
            continue
        existing = db.query(AggregationTimeseries).filter(
            AggregationTimeseries.calc_date == calc_date,
            AggregationTimeseries.scope == scope,
        ).first()
        if existing:
            existing.business_date = business_date
            existing.stock_count = row.stock_count
            existing.total_amount_cny = row.amount_cny
            existing.virtual_earnings = row.virtual_earnings
            existing.pe_weighted = row.pe_weighted
            existing.pb_weighted = row.pb_weighted
            existing.ps_weighted = row.ps_weighted
        else:
            db.add(AggregationTimeseries(
                calc_date=calc_date,
                business_date=business_date,
                scope=scope,
                stock_count=row.stock_count,
                total_amount_cny=row.amount_cny,
                virtual_earnings=row.virtual_earnings,
                pe_weighted=row.pe_weighted,
                pb_weighted=row.pb_weighted,
                ps_weighted=row.ps_weighted,
            ))
    db.commit()