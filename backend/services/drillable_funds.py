"""Drillable-INDICES service — 2026-06-24 重构（公共下钻层 fund_drill_snapshot）

新算法（按用户确认）：
  公共层（scheduler 批量生成 fund_drill_snapshot）：
    对每只可下钻基金 × 交易日 T：
      1. 读 index_constituents[最近月份] 的成分股 + 权重 weight + baseline_price
      2. 取 T 日成分股 current_price；缺失用 T-1（视为停牌）
      3. 校验：当日获得收盘价的成分股占比 >= 95% 才生成
      4. shares_equivalent = fund_price × 0.95 × (weight/100) / current_price
         5% 现金 = fund_price × 0.05
  user 层（每次查询实时算）：
    user_drill[s] = Holding.quantity × shares_equivalent[s]
    user_cash    = Holding.quantity × fund_price × 0.05

数据依赖：fund_drill_snapshot 表（不带 user_id 的公共数据）+ fund_index_map（基金-指数映射）+ index_constituents。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date

from sqlalchemy.orm import Session

from models import (
    AShareFinancialSnapshot,
    FundDailyNav,
    FundDrillSnapshot,
    FundIndexMap,
    HKShareFinancialSnapshot,
    Holding,
    IndexConstituentSnapshot,
)

logger = logging.getLogger(__name__)


def _resolve_snap_for_code(db: Session, code: str, as_of_date: _date, a_snap, h_snap):
    code_norm = code.split(".")[0]
    snap = a_snap.get(code_norm) or h_snap.get(code_norm)
    if not snap and code_norm.isdigit():
        snap = h_snap.get(code_norm.zfill(5))
    return snap


def _aggregate_holdings_by_fund(db: Session, user_id: int | None = None) -> dict[str, dict]:
    """Sum quantities and amount_cny per fund_code (across all buy batches).
    user_id=None 处理全部 holdings（admin 维护场景）；常规调用应传 user_id（2026-06-24）。"""
    out: dict[str, dict] = {}
    q = db.query(Holding)
    if user_id is not None:
        q = q.filter(Holding.user_id == user_id)
    for h in q.all():
        code = h.security_code
        if code not in out:
            out[code] = {
                "fund_code": code,
                "quantity": 0.0,
                "amount_cny": 0.0,
                "asset_type": (h.asset_type or "").lower(),
            }
        out[code]["quantity"] += (h.quantity or 0.0)
        out[code]["amount_cny"] += (h.amount_cny or 0.0)
    return out


def _load_fund_navs(db: Session, fund_codes: list[str]) -> dict[str, dict]:
    """保留原 FundDailyNav 读取逻辑（用户最新算法已不再用 1.z，但保留以备 debug）。
    返回 {fund_code: {nav_529, cumnav_529, nav_618, cumnav_618}}。"""
    if not fund_codes:
        return {}
    rows = db.query(FundDailyNav).filter(
        FundDailyNav.fund_code.in_(fund_codes),
        FundDailyNav.trade_date.in_([_date(2026, 5, 29), _date(2026, 6, 18)]),
    ).all()
    fund_navs: dict[str, dict] = {}
    for r in rows:
        fund_navs.setdefault(r.fund_code, {})
        if r.trade_date == _date(2026, 5, 29):
            fund_navs[r.fund_code]["nav_529"] = r.nav
            fund_navs[r.fund_code]["cumnav_529"] = r.accumulated_nav
        elif r.trade_date == _date(2026, 6, 18):
            fund_navs[r.fund_code]["nav_618"] = r.nav
            fund_navs[r.fund_code]["cumnav_618"] = r.accumulated_nav
    for fc in fund_codes:
        if fc not in fund_navs:
            fund_navs[fc] = {}
    return fund_navs


def _load_drill_snapshots(db: Session, fund_codes: list[str], as_of_date: _date) -> dict[str, list[FundDrillSnapshot]]:
    """加载每只 fund 在 as_of_date 的下钻截面（缺失回退到最近日期）。

    Returns: {fund_code: [FundDrillSnapshot, ...]}
    """
    from services.drill_snapshot import get_drill_snapshot_for_fund
    out = {}
    for fc in fund_codes:
        rows = get_drill_snapshot_for_fund(db, fc, as_of_date)
        out[fc] = rows or []
    return out


def list_drillable_indices(db: Session, as_of_date: _date, user_id: int | None = None) -> list[dict]:
    """公共下钻卡片（每个 index 一张）— 基于 fund_drill_snapshot。

    数据流：
      1. 聚合 user 持仓（holdings_agg）
      2. 对每只可下钻基金：读 fund_index_map (fund_code → index_code)
      3. 读 fund_drill_snapshot[fund, as_of_date]（公共截面，缺失回退最近日期）
      4. user 层：user_drill_shares = Holding.quantity × fund_drill_snapshot.shares_equivalent
      5. 按 index_code 聚合 card：95% 股票市值 + 5% 现金
    """
    holdings_agg = _aggregate_holdings_by_fund(db, user_id=user_id)

    a_q = db.query(AShareFinancialSnapshot).filter(AShareFinancialSnapshot.as_of_date == as_of_date)
    h_q = db.query(HKShareFinancialSnapshot).filter(HKShareFinancialSnapshot.as_of_date == as_of_date)
    if user_id is not None:
        a_q = a_q.filter(AShareFinancialSnapshot.user_id == user_id)
        h_q = h_q.filter(HKShareFinancialSnapshot.user_id == user_id)
    a_snap = {a.stock_code.split(".")[0]: a for a in a_q.all()}
    h_snap = {h.stock_code.split(".")[0]: h for h in h_q.all()}

    candidate_funds = [fc for fc, info in holdings_agg.items() if info["quantity"] > 0]
    drill_cache = _load_drill_snapshots(db, candidate_funds, as_of_date)

    by_index: dict[str, dict] = {}

    for fund_code, info in holdings_agg.items():
        if info["asset_type"] not in ("a_share_equity", "a_share_etf", "hk_equity", "qdii_equity", "us_etf"):
            continue

        fund_map = db.query(FundIndexMap).filter(FundIndexMap.fund_code == fund_code).first()
        if not fund_map:
            continue
        idx_code = fund_map.index_code.split(".")[0]

        drill_rows = drill_cache.get(fund_code, [])
        if not drill_rows:
            continue

        fund_shares = info["quantity"]
        fund_amount = info["amount_cny"]
        fund_price = (fund_amount / fund_shares) if fund_shares > 0 else None

        if idx_code not in by_index:
            by_index[idx_code] = {
                "index_code": idx_code,
                "index_name": fund_map.index_name or idx_code,
                "fund_codes": [],
                "stock_set": set(),
                "static_amount_cny": 0.0,
                "est_market_value_cny": 0.0,
                "cash_value_cny": 0.0,
                "prev_fund_value_total": 0.0,
                "virt_pe": 0.0,
                "virt_pb": 0.0,
                "virt_ps": 0.0,
                "sum_dy_weighted": 0.0,
            }
        bucket = by_index[idx_code]
        bucket["fund_codes"].append(fund_code)
        bucket["prev_fund_value_total"] += fund_amount

        # 5% 现金
        if fund_price and fund_price > 0:
            bucket["cash_value_cny"] += fund_shares * fund_price * 0.05

        for d in drill_rows:
            user_drill_shares = fund_shares * (d.shares_equivalent or 0.0)
            baseline_price = d.baseline_price or 0.0
            current_price = d.current_price or 0.0
            static_amt = user_drill_shares * baseline_price
            est_amt = user_drill_shares * current_price

            bucket["static_amount_cny"] += static_amt
            bucket["est_market_value_cny"] += est_amt
            bucket["stock_set"].add(d.stock_code)

            stock_norm = d.stock_code.split(".")[0]
            snap = a_snap.get(stock_norm) or h_snap.get(stock_norm)
            if snap:
                pe_d = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
                pb_d = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
                ps_d = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
                dy = snap.dividend_yield
                price_ratio = (current_price / baseline_price) if (baseline_price and baseline_price > 0) else None
                pe_dyn = pe_d * price_ratio if (pe_d and price_ratio) else pe_d
                pb_dyn = pb_d * price_ratio if (pb_d and price_ratio) else pb_d
                ps_dyn = ps_d * price_ratio if (ps_d and price_ratio) else ps_d
                dy_dyn = (dy / price_ratio) if (dy and price_ratio) else dy
                if static_amt > 0:
                    if pe_dyn and pe_dyn > 0:
                        bucket["virt_pe"] += static_amt / pe_dyn
                    if pb_dyn and pb_dyn > 0:
                        bucket["virt_pb"] += static_amt / pb_dyn
                    if ps_dyn and ps_dyn > 0:
                        bucket["virt_ps"] += static_amt / ps_dyn
                    if dy_dyn is not None:
                        bucket["sum_dy_weighted"] += static_amt * dy_dyn

    cards = []
    total_est = sum(b["est_market_value_cny"] for b in by_index.values())
    for bucket in by_index.values():
        static = bucket["static_amount_cny"]
        est = bucket["est_market_value_cny"]
        cash = bucket["cash_value_cny"]
        total_card_value = est + cash
        prev_total = bucket["prev_fund_value_total"]
        virt_pe = bucket["virt_pe"]
        virt_pb = bucket["virt_pb"]
        virt_ps = bucket["virt_ps"]
        dy_sum = bucket["sum_dy_weighted"]
        weight_pct = round(est / total_est * 100, 4) if total_est else 0.0
        cards.append({
            "index_code": bucket["index_code"],
            "index_name": bucket["index_name"],
            "fund_codes": sorted(bucket["fund_codes"]),
            "stock_count": len(bucket["stock_set"]),
            "static_amount_cny": round(static, 4),
            "est_market_value_cny": round(est, 4),
            "cash_value_cny": round(cash, 4),
            "total_value_cny": round(total_card_value, 4),
            "prev_fund_value_total": round(prev_total, 4),
            "weight_pct": weight_pct,
            "weighted_pe": round(static / virt_pe, 4) if virt_pe else None,
            "weighted_pb": round(static / virt_pb, 4) if virt_pb else None,
            "weighted_ps": round(static / virt_ps, 4) if virt_ps else None,
            "weighted_dividend_yield": round(dy_sum / static, 4) if static else None,
        })

    cards.sort(key=lambda c: c.get("est_market_value_cny", 0), reverse=True)
    return cards


def get_index_drill_detail(
    db: Session,
    index_code: str,
    as_of_date: _date,
    *,
    holdings_agg: dict[str, dict] | None = None,
    fund_navs: dict[str, dict] | None = None,
    a_snap: dict[str, AShareFinancialSnapshot] | None = None,
    h_snap: dict[str, HKShareFinancialSnapshot] | None = None,
    user_id: int | None = None,
) -> dict:
    """单一基金下钻明细 — 基于 fund_drill_snapshot。

    对 index_code 下所有 fund_codes：
      user_drill[s] = Holding.quantity × fund_drill_snapshot[fund, T].shares_equivalent[s]
      同一 stock 跨多只 fund 时 shares_equivalent 与 est_market_value_cny 求和。
    """
    idx_code = index_code.split(".")[0]

    # 找出跟踪该 index 的所有基金（不限定 as_of_date）
    fund_maps = db.query(FundIndexMap).filter(
        FundIndexMap.index_code.startswith(idx_code),
    ).all()
    if not fund_maps:
        return {"error": "no funds tracking this index"}
    fund_codes = [f.fund_code for f in fund_maps]

    if holdings_agg is None:
        holdings_agg = _aggregate_holdings_by_fund(db, user_id=user_id)
    if a_snap is None:
        a_q = db.query(AShareFinancialSnapshot).filter(AShareFinancialSnapshot.as_of_date == as_of_date)
        if user_id is not None:
            a_q = a_q.filter(AShareFinancialSnapshot.user_id == user_id)
        a_snap = {a.stock_code.split(".")[0]: a for a in a_q.all()}
    if h_snap is None:
        h_q = db.query(HKShareFinancialSnapshot).filter(HKShareFinancialSnapshot.as_of_date == as_of_date)
        if user_id is not None:
            h_q = h_q.filter(HKShareFinancialSnapshot.user_id == user_id)
        h_snap = {h.stock_code.split(".")[0]: h for h in h_q.all()}

    drill_cache = _load_drill_snapshots(db, fund_codes, as_of_date)

    total_fund_value = sum(holdings_agg.get(fc, {}).get("amount_cny", 0) for fc in fund_codes)
    total_fund_shares = sum(holdings_agg.get(fc, {}).get("quantity", 0) for fc in fund_codes)

    by_stock: dict[str, dict] = {}
    for fc in fund_codes:
        drill_rows = drill_cache.get(fc, [])
        if not drill_rows:
            continue
        f_shares = holdings_agg.get(fc, {}).get("quantity", 0)
        if f_shares <= 0:
            continue
        for d in drill_rows:
            code = d.stock_code
            user_drill_shares = f_shares * (d.shares_equivalent or 0.0)
            current_price = d.current_price or 0.0
            baseline_price = d.baseline_price
            est_market_value = user_drill_shares * current_price

            snap = _resolve_snap_for_code(db, code, as_of_date, a_snap, h_snap)
            pe_v = ps_v = pb_v = dy_v = None
            if snap:
                pe_v = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
                pb_v = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
                ps_v = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
                dy_v = snap.dividend_yield

            if code not in by_stock:
                by_stock[code] = {
                    "stock_code": code,
                    "stock_name": d.stock_name,
                    "weight_at_baseline_pct": d.weight_pct,
                    "shares_equivalent": 0.0,
                    "baseline_price": baseline_price,
                    "current_price": current_price,
                    "current_price_date": None,
                    "est_market_value_cny": 0.0,
                    "pe_ttm": pe_v,
                    "pb_mrq": pb_v,
                    "ps_ttm": ps_v,
                    "dividend_yield": dy_v,
                    "from_funds": set(),
                }
            acc = by_stock[code]
            acc["shares_equivalent"] += user_drill_shares
            acc["est_market_value_cny"] += est_market_value
            acc["from_funds"].add(fc)
            if acc.get("current_price") is None and current_price:
                acc["current_price"] = current_price
            if acc.get("baseline_price") is None and baseline_price:
                acc["baseline_price"] = baseline_price

    rows = []
    for s in by_stock.values():
        s["from_funds"] = sorted(s["from_funds"])
        s["shares_equivalent_int_disp"] = int(round(s["shares_equivalent"]))
        s["shares_equivalent"] = round(s["shares_equivalent"], 4)
        s["est_market_value_cny"] = round(s["est_market_value_cny"], 4)
        rows.append(s)
    rows.sort(key=lambda r: r["est_market_value_cny"], reverse=True)

    return {
        "as_of_date": as_of_date.isoformat(),
        "index_code": idx_code,
        "index_name": fund_maps[0].index_name or idx_code,
        "fund_codes": sorted(fund_codes),
        "total_fund_shares": round(total_fund_shares, 4),
        "total_fund_value_prev": round(total_fund_value, 4),
        "constituents": rows,
    }


def get_all_drilled_stocks(
    db: Session,
    as_of_date: _date,
    *,
    indices: list[dict] | None = None,
    holdings_agg: dict[str, dict] | None = None,
    fund_navs: dict[str, dict] | None = None,
    a_snap: dict | None = None,
    h_snap: dict | None = None,
    user_id: int | None = None,
) -> dict:
    """跨所有可下钻指数聚合成分股（基于 fund_drill_snapshot）。

    shares_equivalent 与 est_market_value_cny 按 stock_code 求和；
    价格/估值字段取首个非空值。
    """
    if indices is None:
        indices = list_drillable_indices(db, as_of_date, user_id=user_id)
    if not indices:
        return {"as_of_date": as_of_date.isoformat(), "stocks": [], "count": 0}

    by_stock: dict[str, dict] = {}
    for idx in indices:
        detail = get_index_drill_detail(
            db, idx["index_code"], as_of_date,
            holdings_agg=holdings_agg,
            fund_navs=fund_navs,
            a_snap=a_snap,
            h_snap=h_snap,
            user_id=user_id,
        )
        if "constituents" not in detail:
            continue
        idx_code = idx["index_code"]
        for c in detail["constituents"]:
            code = c["stock_code"]
            if code not in by_stock:
                by_stock[code] = {
                    "stock_code": code,
                    "stock_name": c.get("stock_name"),
                    "shares_equivalent": 0.0,
                    "current_price": c.get("current_price"),
                    "baseline_price": c.get("baseline_price"),
                    "current_price_date": c.get("current_price_date"),
                    "est_market_value_cny": 0.0,
                    "pe_ttm": c.get("pe_ttm"),
                    "pb_mrq": c.get("pb_mrq"),
                    "ps_ttm": c.get("ps_ttm"),
                    "dividend_yield": c.get("dividend_yield"),
                    "indices": set(),
                }
            acc = by_stock[code]
            acc["shares_equivalent"] += (c.get("shares_equivalent") or 0.0)
            acc["est_market_value_cny"] += (c.get("est_market_value_cny") or 0.0)
            acc["indices"].add(idx_code)
            for k in ("current_price", "baseline_price", "current_price_date",
                      "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield"):
                if acc.get(k) is None and c.get(k) is not None:
                    acc[k] = c.get(k)

    stocks = []
    for s in by_stock.values():
        s["indices"] = sorted(s["indices"])
        s["shares_equivalent_int_disp"] = int(round(s["shares_equivalent"]))
        s["shares_equivalent"] = round(s["shares_equivalent"], 4)
        s["est_market_value_cny"] = round(s["est_market_value_cny"], 4)
        stocks.append(s)
    stocks.sort(key=lambda r: r["est_market_value_cny"], reverse=True)

    return {
        "as_of_date": as_of_date.isoformat(),
        "stocks": stocks,
        "count": len(stocks),
    }