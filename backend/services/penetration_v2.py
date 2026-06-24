"""
Penetration pipeline v2 (spec §3).

For each holding:
  1. Decide whether to drill (asset_type rule).
  2. Look up the tracking index for the fund.
  3. For each constituent s of that index:
       amount_dynamic[s] = (weight[s]/100) * A_fund * (current_price[s] / baseline_price[s])
       amount_static[s]  = (weight[s]/100) * A_fund
  4. Insert rows into penetration_snapshot + full_holding_snapshot.

Asset types that do NOT drill: bond, gold, qdii_bond, cash.
Drill types: a_share_equity, a_share_etf, hk_equity, qdii_equity, us_etf (when index known).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Iterable

from sqlalchemy import delete
from sqlalchemy.orm import Session

from models import (
    AShareFinancialSnapshot,
    FundIndexMap,
    FullHoldingSnapshot,
    HKShareFinancialSnapshot,
    Holding,
    IndexConstituentSnapshot,
    OverseasShareFinancialSnapshot,
    PenetrationSnapshot,
)

logger = logging.getLogger(__name__)

DRILLABLE_TYPES = {
    "a_share_equity",
    "a_share_etf",
    "hk_equity",
    "qdii_equity",
    "us_etf",
}


@dataclass
class PenetrationReport:
    as_of_date: _date
    holdings_seen: int = 0
    holdings_drilled: int = 0
    holdings_skipped: list[str] = field(default_factory=list)
    rows_inserted_pnsnap: int = 0
    rows_inserted_fhsnap: int = 0
    errors: list[str] = field(default_factory=list)


def _normalize_index_code(code: str) -> str:
    """Strip exchange suffix from tracking index code so it matches index_constituent_snapshot."""
    return code.split(".")[0].strip()


def _stock_code_matches(a: str, b: str) -> bool:
    """Match stock codes ignoring exchange suffix."""
    a_norm = a.split(".")[0]
    b_norm = b.split(".")[0]
    return a_norm == b_norm


def _pad_hk_code(code: str) -> str:
    """Pad HK codes to 5 digits (CSI/CFE use 4-digit, Excel uses 5-digit).

    Returns the bare numeric code (no suffix). For non-HK codes, returns
    the code unchanged.
    """
    raw = code.split(".")[0]
    if not raw.isdigit():
        return raw
    # If length is 4 or less, pad to 5; if already 5+, leave alone
    if len(raw) <= 5:
        return raw.zfill(5)
    return raw


def _is_hk_code(code: str) -> bool:
    return code.upper().endswith(".HK")


def _resolve_snapshot_for_code(stock_code: str, as_of_date, db):
    """Return (snapshot, kind) where kind ∈ {'a', 'hk', 'overseas'}. Handles HK 4 vs 5 digit mismatch.

    For HK codes, tries the raw normalized code first, then the padded version.
    For overseas codes, matches stock_code exactly and takes the latest as_of_date.
    """
    code_norm = stock_code.split(".")[0]
    is_hk = _is_hk_code(stock_code)

    # Try A-share
    a_snap = (
        db.query(AShareFinancialSnapshot)
        .filter(AShareFinancialSnapshot.as_of_date == as_of_date)
        .filter(AShareFinancialSnapshot.stock_code == f"{code_norm}.SZ")
        .first()
    )
    if not a_snap:
        a_snap = (
            db.query(AShareFinancialSnapshot)
            .filter(AShareFinancialSnapshot.as_of_date == as_of_date)
            .filter(AShareFinancialSnapshot.stock_code == f"{code_norm}.SH")
            .first()
        )
    if a_snap and not is_hk:
        return a_snap, "a"

    # Try HK with raw code first
    h_snap = (
        db.query(HKShareFinancialSnapshot)
        .filter(HKShareFinancialSnapshot.as_of_date == as_of_date)
        .filter(HKShareFinancialSnapshot.stock_code == f"{code_norm}.HK")
        .first()
    )
    if not h_snap:
        # Try with padded code
        padded = _pad_hk_code(stock_code)
        if padded != code_norm:
            h_snap = (
                db.query(HKShareFinancialSnapshot)
                .filter(HKShareFinancialSnapshot.as_of_date == as_of_date)
                .filter(HKShareFinancialSnapshot.stock_code == f"{padded}.HK")
                .first()
            )
    if h_snap:
        return h_snap, "hk"

    # Try Overseas (US/KR/JP/EU etc.) — exact match, latest as_of_date
    o_snap = (
        db.query(OverseasShareFinancialSnapshot)
        .filter(OverseasShareFinancialSnapshot.stock_code == stock_code)
        .order_by(OverseasShareFinancialSnapshot.as_of_date.desc())
        .first()
    )
    if o_snap:
        return o_snap, "overseas"

    if a_snap:
        return a_snap, "a"
    return None, None


def _get_constituents(db: Session, index_code: str, as_of_date: _date) -> list[IndexConstituentSnapshot]:
    return (
        db.query(IndexConstituentSnapshot)
        .filter(IndexConstituentSnapshot.index_code == index_code)
        .filter(IndexConstituentSnapshot.as_of_date == as_of_date)
        .all()
    )


def _resolve_industry(stock_code: str, as_of_date: _date, db: Session):
    """Resolve all industry systems + chain/growth/competition.

    Returns dict with keys:
      swy_l1..l4 (申万), csi_l1..l4 (中证), se_l1..l4 (战略新兴),
      chain_position, growth_tier, competition
    """
    snap, kind = _resolve_snapshot_for_code(stock_code, as_of_date, db)
    if kind == "overseas":
        # 海外市场用 yfinance 的 sector/industry 替代申万/中证分级
        return {
            "swy_l1": snap.sector or "其他",
            "swy_l2": snap.industry or "其他",
            "swy_l3": "其他",
            "swy_l4": "其他",
            "csi_l1": "其他", "csi_l2": "其他", "csi_l3": "其他", "csi_l4": "其他",
            "se_l1": "其他", "se_l2": "其他", "se_l3": "其他", "se_l4": "其他",
            "chain_position": "other",
            "growth_tier": "unknown",
            "competition": "unknown",
        }
    if kind == "hk":
        return {
            "swy_l1": snap.swy_l1 or "其他",
            "swy_l2": snap.swy_l2 or "其他",
            "swy_l3": snap.swy_l3 or "其他",
            "swy_l4": snap.swy_l4 or "其他",
            "csi_l1": snap.csi_l1 or "其他",
            "csi_l2": snap.csi_l2 or "其他",
            "csi_l3": snap.csi_l3 or "其他",
            "csi_l4": snap.csi_l4 or "其他",
            "se_l1": snap.se_l1 or "其他",
            "se_l2": snap.se_l2 or "其他",
            "se_l3": snap.se_l3 or "其他",
            "se_l4": snap.se_l4 or "其他",
            "chain_position": "other",
            "growth_tier": "unknown",
            "competition": "unknown",
        }
    if kind == "a":
        # A-share snapshot now has industry columns; fall back to StockFinancial if missing
        from models import StockFinancial
        sf = (
            db.query(StockFinancial)
            .filter(StockFinancial.stock_code.like(f"{stock_code.split('.')[0]}%"))
            .order_by(StockFinancial.as_of_date.desc())
            .first()
        )
        return {
            "swy_l1": snap.swy_l1 or (sf.industry_sw if sf and sf.industry_sw else "其他"),
            "swy_l2": snap.swy_l2 or (sf.industry_sw if sf and sf.industry_sw else "其他"),
            "swy_l3": snap.swy_l3 or (sf.industry_sw if sf and sf.industry_sw else "其他"),
            "swy_l4": snap.swy_l4 or "其他",
            "csi_l1": snap.csi_l1 or "其他",
            "csi_l2": snap.csi_l2 or "其他",
            "csi_l3": snap.csi_l3 or "其他",
            "csi_l4": snap.csi_l4 or "其他",
            "se_l1": snap.se_l1 or "其他",
            "se_l2": snap.se_l2 or "其他",
            "se_l3": snap.se_l3 or "其他",
            "se_l4": snap.se_l4 or "其他",
            "chain_position": sf.chain_position if sf and sf.chain_position else "other",
            "growth_tier": sf.growth_tier if sf and sf.growth_tier else "unknown",
            "competition": sf.competition if sf and sf.competition else "unknown",
        }
    return {
        "swy_l1": "其他", "swy_l2": "其他", "swy_l3": "其他", "swy_l4": "其他",
        "csi_l1": "其他", "csi_l2": "其他", "csi_l3": "其他", "csi_l4": "其他",
        "se_l1": "其他", "se_l2": "其他", "se_l3": "其他", "se_l4": "其他",
        "chain_position": "other", "growth_tier": "unknown", "competition": "unknown",
    }


def _resolve_dynamic_metrics(stock_code: str, as_of_date: _date, db: Session):
    """Resolve (pe_dynamic, pb_dynamic, ps_dynamic, eps_fy1)."""
    snap, _ = _resolve_snapshot_for_code(stock_code, as_of_date, db)
    if snap:
        return snap.pe_ttm_dynamic, snap.pb_mrq_dynamic, snap.ps_ttm_dynamic, snap.eps_fy1
    return None, None, None, None


def _wipe(db: Session, as_of_date: _date):
    db.query(PenetrationSnapshot).filter(PenetrationSnapshot.as_of_date == as_of_date).delete(synchronize_session=False)
    db.query(FullHoldingSnapshot).filter(FullHoldingSnapshot.as_of_date == as_of_date).delete(synchronize_session=False)
    db.commit()


def run_penetration(db: Session, as_of_date: _date) -> PenetrationReport:
    """Drill all eligible holdings and write penetration_snapshot + full_holding_snapshot.

    full_holding_snapshot is then merged by stock_code so the same security
    exposed via multiple funds collapses into one row with summed amount.
    """
    report = PenetrationReport(as_of_date=as_of_date)
    _wipe(db, as_of_date)

    # Aggregate holdings by security_code (multiple buy batches collapse to one row)
    agg: dict[str, dict] = {}
    for h in db.query(Holding).all():
        code = h.security_code
        if code not in agg:
            agg[code] = {
                "security_code": code,
                "security_name": h.security_name,
                "asset_type": (h.asset_type or "").lower(),
                "amount_cny": 0.0,
            }
        agg[code]["amount_cny"] += (h.amount_cny or 0.0)

    report.holdings_seen = len(agg)

    # Pre-load financial snapshots into memory for fast lookup
    a_snap_by_code: dict[str, AShareFinancialSnapshot] = {}
    for a in db.query(AShareFinancialSnapshot).filter(AShareFinancialSnapshot.as_of_date == as_of_date).all():
        a_snap_by_code[a.stock_code.split(".")[0]] = a
    h_snap_by_code: dict[str, HKShareFinancialSnapshot] = {}
    for h in db.query(HKShareFinancialSnapshot).filter(HKShareFinancialSnapshot.as_of_date == as_of_date).all():
        h_snap_by_code[h.stock_code.split(".")[0]] = h

    pn_rows: list[PenetrationSnapshot] = []
    fh_rows: list[FullHoldingSnapshot] = []  # raw, pre-merge

    for h in agg.values():
        h_code = h["security_code"]
        h_amt = h["amount_cny"]
        asset_type = h["asset_type"]

        # Decide drill eligibility
        if asset_type not in DRILLABLE_TYPES:
            ind = _resolve_industry(h_code, as_of_date, db)
            pe, pb, ps, eps = _resolve_dynamic_metrics(h_code, as_of_date, db)
            if asset_type in ("us_stock", "us_etf"):
                stype = "direct_stock"
            elif asset_type == "cash":
                stype = "cash"
            else:
                stype = "undrilled_fund"
            fh_rows.append(FullHoldingSnapshot(
                as_of_date=as_of_date,
                stock_code=h_code,
                stock_name=h["security_name"],
                source_type=stype,
                source_holding_code=h_code,
                amount_cny=round(h_amt, 4),
                swy_l1=ind["swy_l1"], swy_l2=ind["swy_l2"], swy_l3=ind["swy_l3"], swy_l4=ind["swy_l4"],
                csi_l1=ind["csi_l1"], csi_l2=ind["csi_l2"], csi_l3=ind["csi_l3"], csi_l4=ind["csi_l4"],
                se_l1=ind["se_l1"], se_l2=ind["se_l2"], se_l3=ind["se_l3"], se_l4=ind["se_l4"],
                industry_l1=ind["swy_l1"], industry_l2=ind["swy_l2"],
                chain_position=ind["chain_position"], growth_tier=ind["growth_tier"], competition=ind["competition"],
                pe_ttm_dynamic=pe, pb_mrq_dynamic=pb, ps_ttm_dynamic=ps, eps_fy1=eps,
            ))
            report.holdings_skipped.append(f"{h_code}({asset_type})")
            continue

        fund_map = db.query(FundIndexMap).filter(
            FundIndexMap.fund_code == h_code,
            FundIndexMap.as_of_date == as_of_date,
        ).first()
        if not fund_map:
            report.holdings_skipped.append(f"{h_code}(no fund_index_map)")
            fh_rows.append(FullHoldingSnapshot(
                as_of_date=as_of_date,
                stock_code=h_code,
                stock_name=h["security_name"],
                source_type="undrilled_fund",
                source_holding_code=h_code,
                amount_cny=round(h_amt, 4),
            ))
            continue

        idx_code = _normalize_index_code(fund_map.index_code)
        constituents = _get_constituents(db, idx_code, as_of_date)
        if not constituents:
            report.holdings_skipped.append(f"{h_code}(no constituents for {idx_code})")
            fh_rows.append(FullHoldingSnapshot(
                as_of_date=as_of_date,
                stock_code=h_code,
                stock_name=h["security_name"],
                source_type="undrilled_fund",
                source_holding_code=h_code,
                amount_cny=round(h_amt, 4),
            ))
            continue

        report.holdings_drilled += 1
        for s in constituents:
            weight_pct = (s.weight or 0.0)
            amount_static = (weight_pct / 100.0) * h_amt
            snap, _kind = _resolve_snapshot_for_code(s.stock_code, as_of_date, db)
            ratio = 1.0
            baseline_price = None
            current_price = None
            if snap and snap.baseline_price and snap.current_price and snap.baseline_price > 0:
                baseline_price = snap.baseline_price
                current_price = snap.current_price
                ratio = snap.current_price / snap.baseline_price
            amount_dynamic = amount_static * ratio

            pn_rows.append(PenetrationSnapshot(
                as_of_date=as_of_date,
                holding_code=h_code,
                holding_name=h["security_name"],
                holding_amount_cny=round(h_amt, 4),
                index_code=idx_code,
                index_name=s.index_name or fund_map.index_name,
                stock_code=s.stock_code,
                stock_name=s.stock_name,
                weight_at_baseline=weight_pct,
                amount_cny_dynamic=round(amount_dynamic, 4),
                amount_cny_static=round(amount_static, 4),
                baseline_price=baseline_price,
                current_price=current_price,
                calculation_method="weight_invariant",
            ))

            ind = _resolve_industry(s.stock_code, as_of_date, db)
            pe, pb, ps, eps = _resolve_dynamic_metrics(s.stock_code, as_of_date, db)
            fh_rows.append(FullHoldingSnapshot(
                as_of_date=as_of_date,
                stock_code=s.stock_code,
                stock_name=s.stock_name,
                source_type="drilled_fund",
                source_holding_code=h_code,
                amount_cny=round(amount_dynamic, 4),
                swy_l1=ind["swy_l1"], swy_l2=ind["swy_l2"], swy_l3=ind["swy_l3"], swy_l4=ind["swy_l4"],
                csi_l1=ind["csi_l1"], csi_l2=ind["csi_l2"], csi_l3=ind["csi_l3"], csi_l4=ind["csi_l4"],
                se_l1=ind["se_l1"], se_l2=ind["se_l2"], se_l3=ind["se_l3"], se_l4=ind["se_l4"],
                industry_l1=ind["swy_l1"], industry_l2=ind["swy_l2"],
                chain_position=ind["chain_position"], growth_tier=ind["growth_tier"], competition=ind["competition"],
                pe_ttm_dynamic=pe, pb_mrq_dynamic=pb, ps_ttm_dynamic=ps, eps_fy1=eps,
            ))

    # Merge fh_rows by stock_code — sum amounts, take first non-null of
    # metadata fields (name, industry, pe/pb/ps).
    merged: dict[str, FullHoldingSnapshot] = {}
    for r in fh_rows:
        m = merged.get(r.stock_code)
        if m is None:
            merged[r.stock_code] = FullHoldingSnapshot(
                as_of_date=r.as_of_date,
                stock_code=r.stock_code,
                stock_name=r.stock_name,
                source_type=r.source_type,
                source_holding_code=r.source_holding_code,
                amount_cny=r.amount_cny,
                swy_l1=r.swy_l1, swy_l2=r.swy_l2, swy_l3=r.swy_l3, swy_l4=r.swy_l4,
                csi_l1=r.csi_l1, csi_l2=r.csi_l2, csi_l3=r.csi_l3, csi_l4=r.csi_l4,
                se_l1=r.se_l1, se_l2=r.se_l2, se_l3=r.se_l3, se_l4=r.se_l4,
                industry_l1=r.industry_l1,
                industry_l2=r.industry_l2,
                chain_position=r.chain_position,
                growth_tier=r.growth_tier,
                competition=r.competition,
                pe_ttm_dynamic=r.pe_ttm_dynamic,
                pb_mrq_dynamic=r.pb_mrq_dynamic,
                ps_ttm_dynamic=r.ps_ttm_dynamic,
                eps_fy1=r.eps_fy1,
            )
        else:
            m.amount_cny = round((m.amount_cny or 0) + (r.amount_cny or 0), 4)
            # First non-null wins (don't fabricate from later empty)
            for field in ("stock_name",
                          "swy_l1", "swy_l2", "swy_l3", "swy_l4",
                          "csi_l1", "csi_l2", "csi_l3", "csi_l4",
                          "se_l1", "se_l2", "se_l3", "se_l4",
                          "industry_l1", "industry_l2",
                          "chain_position", "growth_tier", "competition",
                          "pe_ttm_dynamic", "pb_mrq_dynamic", "ps_ttm_dynamic",
                          "eps_fy1"):
                if getattr(m, field) in (None, "", "其他", "other", "unknown") \
                        and getattr(r, field) not in (None, "", "其他", "other", "unknown"):
                    setattr(m, field, getattr(r, field))
            # Track multiple sources via comma-separated codes (read-only)
            if r.source_holding_code and r.source_holding_code not in (m.source_holding_code or ""):
                # Append; will be visible to /dimension-detail
                extras = (m.source_holding_code or "") + "," + r.source_holding_code
                m.source_holding_code = ",".join(sorted(set(extras.split(","))))

    if pn_rows:
        db.bulk_save_objects(pn_rows)
    if merged:
        db.bulk_save_objects(list(merged.values()))
    db.commit()
    report.rows_inserted_pnsnap = len(pn_rows)
    report.rows_inserted_fhsnap = len(merged)
    return report