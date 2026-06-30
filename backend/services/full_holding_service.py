"""full_holding_service.py — 共享给 full-holding-table API + intraday-change service 的核心 builder。

把 backend/main.py:2988 get_full_holding_table 内的 undrilled + drilled 构建逻辑抽出来，
返回与 API 完全一致的数据结构，确保两处口径严格一致。

Returns: {
    "undrilled": [
        {"stock_code", "stock_name", "source_type", "est_market_value_cny", "is_cash", ...},
        ...
    ],
    "drilled": [
        {"stock_code", "stock_name", "est_market_value_cny", "is_cash", "current_price", ...},
        ...
    ],
    "as_of_date": str,
}
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _infer_source_type_from_holding(acc: dict) -> str:
    """从 holding 推断 source_type (与 full_holding 兼容)."""
    cur = acc.get("currency", "CNY")
    at = (acc.get("asset_type") or "").lower()
    code = acc.get("security_code", "")
    # USD/HKD 一律视为直接持股 (含 QQQ 等美股 ETF)
    if cur in ("USD", "HKD"):
        return "direct_stock"
    # CNY 基金 / 债券 / 黄金 视为未下钻基金
    if code.endswith(".OF") or any(k in at for k in ("fund", "etf", "bond", "gold")):
        return "undrilled_fund"
    # A 股直接持股
    if at in ("a_share_equity", ""):
        return "direct_stock"
    return "undrilled_fund"


def _lookup_snap(code: str, a_snap: dict, h_snap: dict):
    """双键查找快照 (raw code 与 suffixed 都试)."""
    snap = a_snap.get(code) or h_snap.get(code)
    if snap:
        return snap
    norm = code.split(".")[0]
    snap = a_snap.get(norm) or h_snap.get(norm)
    if snap:
        return snap
    if norm.isdigit():
        for k in (norm.zfill(5), norm.zfill(6)):
            snap = a_snap.get(k) or h_snap.get(k)
            if snap:
                return snap
    return None


def _estimate_market_value_for_holding(code: str, acc: dict, snap, db) -> tuple:
    """对未下钻 holding 估算 数量 × 收盘价 (用户口径)."""
    from models import FundDailyNav, PriceCache
    qty = acc.get("quantity", 0.0) or 0.0
    cur = acc.get("currency", "CNY")
    if code.endswith(".OF"):
        nav = None
        nav_row = (
            db.query(FundDailyNav)
            .filter(FundDailyNav.fund_code == code, FundDailyNav.nav.isnot(None))
            .order_by(FundDailyNav.trade_date.desc())
            .first()
        )
        if nav_row:
            nav = float(nav_row.nav)
        if nav is None and acc.get("price") is not None:
            nav = float(acc["price"])
        if nav is None or nav <= 0:
            return None, qty, None
        # OF 基金 amount_cny 已是原币（CNY），无需 fx
        return nav * qty, qty, nav

    # 直接持股 / 美股 / 港股：取 price_cache
    price = None
    if snap and snap.current_price is not None:
        price = float(snap.current_price)
    if price is None:
        pc_row = (
            db.query(PriceCache.close_px)
            .filter(
                PriceCache.stock_code == code,
                PriceCache.close_px.isnot(None),
            )
            .order_by(PriceCache.trade_date.desc())
            .first()
        )
        if pc_row and pc_row[0]:
            price = float(pc_row[0])
    if price is None and acc.get("price") is not None:
        price = float(acc["price"])
    if price is None or price <= 0:
        return None, qty, None
    return price * qty, qty, price


def build_full_holding_for_user(
    db: Session, as_of_date: _date, user_id: int
) -> dict:
    """构建指定 user 在 as_of_date 的 full-holding-table 数据。

    复用 main.py:2988 get_full_holding_table 的核心逻辑（去 request 依赖）。
    Returns: {"undrilled": [...], "drilled": [...], "as_of_date": str}
    """
    from models import (
        AShareFinancialSnapshot, HKShareFinancialSnapshot, Holding, FundIndexMap,
    )
    from services.drill_orchestration_service import get_all_drill_constituents

    # 1) 一次性加载 snapshot
    a_snap_raw = {a.stock_code.split(".")[0]: a for a in
                  db.query(AShareFinancialSnapshot).filter(
                      AShareFinancialSnapshot.as_of_date == as_of_date,
                  ).all()}
    h_snap_raw = {h.stock_code.split(".")[0]: h for h in
                  db.query(HKShareFinancialSnapshot).filter(
                      HKShareFinancialSnapshot.as_of_date == as_of_date,
                  ).all()}

    def _norm_keys(snap_dict):
        for k, v in list(snap_dict.items()):
            snap_dict.setdefault(v.stock_code, v)
        return snap_dict
    a_snap = _norm_keys(a_snap_raw)
    h_snap = _norm_keys(h_snap_raw)

    # 2) Holding 聚合（按代码）
    holdings = db.query(Holding).filter(Holding.user_id == user_id).all()
    by_code: dict[str, dict] = {}
    for h in holdings:
        code = h.security_code
        if not code:
            continue
        acc = by_code.setdefault(code, {
            "security_code": code,
            "security_name": h.security_name,
            "quantity": 0.0,
            "amount": 0.0,
            "amount_cny": 0.0,
            "currency": h.currency or "CNY",
            "asset_type": h.asset_type or "",
            "price": h.price,
        })
        acc["quantity"] += (h.quantity or 0.0)
        acc["amount"] += (h.amount or 0.0)
        acc["amount_cny"] += (h.amount_cny or 0.0)

    # 3) 可下钻基金（来自 FundIndexMap）
    drillable_codes = {m.fund_code for m in db.query(FundIndexMap).all()}

    # 4) undrilled 段
    undrilled_out: list[dict] = []
    for code, acc in by_code.items():
        if code in drillable_codes:
            continue
        source_type = _infer_source_type_from_holding(acc)
        snap = _lookup_snap(code, a_snap, h_snap)
        pe_v = pb_v = ps_v = dy_v = None
        if snap:
            pe_v = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
            pb_v = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
            ps_v = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
            dy_v = snap.dividend_yield

        est_value_raw, shares, fallback_price = _estimate_market_value_for_holding(
            code, acc, snap, db,
        )
        baseline_price = snap.baseline_price if snap else None
        current_price = snap.current_price if snap else fallback_price

        # FX 折算 (2026-06-25 规则：双币种字段)
        from crawlers.exchange_rates import get_rate
        cur = acc["currency"]
        rate = get_rate(db, cur, "CNY") if cur != "CNY" else 1.0
        if rate is None:
            rate = 1.0
        est_value_cny = (est_value_raw * rate) if est_value_raw is not None else None
        if current_price is not None and cur != "CNY":
            current_price_cny = current_price * rate
        else:
            current_price_cny = current_price

        undrilled_out.append({
            "stock_code": code,
            "stock_name": acc["security_name"],
            "source_type": source_type,
            "amount_cny": acc["amount_cny"],
            "shares": shares,
            "baseline_price": baseline_price,
            "current_price": current_price,
            "current_price_cny": current_price_cny,
            "est_market_value_cny": est_value_cny,
            "pe_ttm_dynamic": pe_v,
            "pb_mrq_dynamic": pb_v,
            "ps_ttm_dynamic": ps_v,
            "dividend_yield": dy_v,
            "fund_currency": cur,
            "is_cash": False,
        })

    # 5) drilled 段
    drilled_resp = get_all_drill_constituents(db, as_of_date, user_id)
    drilled_out: list[dict] = []
    if drilled_resp:
        for s in drilled_resp.get("stocks", []):
            code = s["stock_code"]
            drilled_out.append({
                "stock_code": code,
                "stock_name": s.get("stock_name"),
                "shares_equivalent": s.get("shares_equivalent", 0.0),
                "baseline_price": s.get("baseline_price"),
                "current_price": s.get("current_price"),
                "current_price_cny": s.get("current_price_cny"),
                "est_market_value_cny": s.get("est_market_value_cny", 0.0),
                "is_cash": s.get("is_cash", False),
                "indices": s.get("indices", []),
            })

    return {
        "as_of_date": as_of_date.isoformat(),
        "undrilled": undrilled_out,
        "drilled": drilled_out,
    }