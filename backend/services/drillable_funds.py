"""
Drillable-INDICES service — 计算原则 (用户明确):
  1. 资产/历史资产 → 单位净值 (DWJZ)
     反推 5/29 资产值: fund_value_29 = Holding.quantity × nav_5_29
  2. 期间相对价值变化 → 累计净值 (LJJZ)
     用于「以历史权重 × 期间变化率」还原指数期间资产变化
  3. 约当数量显示保留整数
  4. 多基金指数: 每个基金单独算 cumnav_t1/cumnav_t0 期间变化率,
     再以基金 unit_nav 资产值 (DWJZ × 份额) 为权重, 加权得到指数的相对变化

估值偏差 = est_value / simulated_value - 1
  est_value = Σ shares_equivalent × current_stock_price     (以 5/29 权重 × 6/18 股价模拟)
  simulated_value = Σ fund_value_529 × cumnav_t1/cumnav_t0  (按基金累计净值推的指数 6/18 资产)
  偏差 ≈ 0 表示权重未变 + 持仓未变 + 估值与基金净值一致;
  偏差大说明权重变化 / 申赎 / 估值与净值存在差异。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date

from sqlalchemy.orm import Session

from models import (
    AShareFinancialSnapshot,
    FundDailyNav,
    FundIndexMap,
    HKShareFinancialSnapshot,
    Holding,
    IndexConstituentSnapshot,
)

logger = logging.getLogger(__name__)

AS_OF_DATE = _date(2026, 5, 29)       # 历史权重基准日(对应 5/29 收盘)
AS_OF_DATE_T1 = _date(2026, 6, 18)     # 当前估值日(对应 6/18 收盘)


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
    """For each fund_code: load 5/29 NAV + 累计净值 + 6/18 NAV + 累计净值。

    Returns {fund_code: {
        "nav_529": float|None,    # 单位净值 5/29 (用于反推 5/29 资产)
        "cumnav_529": float|None, # 累计净值 5/29 (用于算期间变化)
        "nav_618": float|None,    # 单位净值 6/18 (sanity check)
        "cumnav_618": float|None, # 累计净值 6/18 (用于算期间变化)
    }}.

    批量查询: 原来每个 fund 2 次 query, 现在一次性 IN 查询。
    """
    out: dict[str, dict] = {fc: {"nav_529": None, "cumnav_529": None, "nav_618": None, "cumnav_618": None} for fc in fund_codes}
    if not fund_codes:
        return out
    rows = (
        db.query(FundDailyNav)
        .filter(
            FundDailyNav.fund_code.in_(fund_codes),
            FundDailyNav.trade_date.in_([AS_OF_DATE, AS_OF_DATE_T1]),
        )
        .all()
    )
    for r in rows:
        fc = r.fund_code
        if r.trade_date == AS_OF_DATE:
            out[fc]["nav_529"] = r.nav
            out[fc]["cumnav_529"] = r.accumulated_nav
        elif r.trade_date == AS_OF_DATE_T1:
            out[fc]["nav_618"] = r.nav
            out[fc]["cumnav_618"] = r.accumulated_nav
    return out


def list_drillable_indices(db: Session, as_of_date: _date, user_id: int | None = None) -> list[dict]:
    """One card per index using unit_nav for assets, cumnav for period change.

    算法:
      fund_value_529 = Holding.quantity × nav_529                       # 单位净值反推 5/29 资产
      period_return = cumnav_618 / cumnav_529 - 1                       # 累计净值算 5/29→6/18 变化
      fund_value_simulated_618 = fund_value_529 × (1 + period_return)   # 按基金推 6/18 资产

      多基金指数: 指数期间变化率 = Σ (fund_value_529_i × period_return_i) / Σ fund_value_529_i
      指数 simulated_value = Σ fund_value_529 × (1 + weighted_period_return)

      约当数量 = fund_value_529 × stock_weight / stock_baseline_price_529
      est_value = Σ 约当数量 × stock_current_price                      # 模拟 6/18 持仓市值

      deviation = (est_value - simulated_value) / simulated_value      # 跟踪误差
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
    fund_navs = _load_fund_navs(db, candidate_funds)

    # Cache constituents + equal-weight fallback
    constituent_cache: dict[str, dict] = {}
    by_index: dict[str, dict] = {}

    for fund_code, info in holdings_agg.items():
        if info["asset_type"] not in ("a_share_equity", "a_share_etf", "hk_equity", "qdii_equity", "us_etf"):
            continue
        fund_map = db.query(FundIndexMap).filter(
            FundIndexMap.fund_code == fund_code,
            FundIndexMap.as_of_date == as_of_date,
        ).first()
        if not fund_map:
            continue
        idx_code = fund_map.index_code.split(".")[0]
        if idx_code not in constituent_cache:
            cons = db.query(IndexConstituentSnapshot).filter(
                IndexConstituentSnapshot.as_of_date == as_of_date,
                IndexConstituentSnapshot.index_code == idx_code,
            ).all()
            has_weight_col = "weight" in [c.name for c in IndexConstituentSnapshot.__table__.columns]
            any_weight = any((getattr(c, "weight", None) for c in cons))
            # Equal-weight fallback when akshare didn't ship weights (e.g. SZSE 创业板50)
            equal_w = (100.0 / len(cons)) if cons else 0.0
            constituent_cache[idx_code] = {
                "rows": cons,
                "equal_weight_pct": equal_w,
                "has_weight_col": has_weight_col,
                "any_weight": any_weight,
            }
        cache = constituent_cache[idx_code]
        constituents = cache["rows"]
        if not constituents:
            continue

        if idx_code not in by_index:
            by_index[idx_code] = {
                "index_code": idx_code,
                "index_name": fund_map.index_name or idx_code,
                "fund_codes": [],
                "stock_set": set(),
                "static_amount_cny": 0.0,           # Σ 5/29 反推资产 (单位净值)
                "est_market_value_cny": 0.0,        # Σ shares × current_price
                "simulated_value_cny": 0.0,         # Σ fund_value_529 × cumnav_period
                "prev_fund_value_total": 0.0,       # Σ Holding.amount_cny (6/18 实际值, audit)
                "virt_pe": 0.0,
                "virt_pb": 0.0,
                "virt_ps": 0.0,
                "sum_dy_weighted": 0.0,
                "fund_5_29_nav_used": None,
            }
        bucket = by_index[idx_code]
        bucket["fund_codes"].append(fund_code)
        bucket["prev_fund_value_total"] += info["amount_cny"]

        # === 单位净值算 5/29 资产 (原则 1) ===
        fund_shares = info["quantity"]
        nav529 = (fund_navs.get(fund_code) or {}).get("nav_529")
        cumnav529 = (fund_navs.get(fund_code) or {}).get("cumnav_529")
        cumnav618 = (fund_navs.get(fund_code) or {}).get("cumnav_618")

        if nav529 and fund_shares > 0:
            fund_value_529 = fund_shares * nav529
            bucket["fund_5_29_nav_used"] = nav529
        else:
            # 无单位净值数据 → fallback 到 6/18 实际资产
            fund_value_529 = info["amount_cny"]

        # === 累计净值算期间变化 (原则 2) ===
        if cumnav529 and cumnav618 and cumnav529 > 0:
            period_return = cumnav618 / cumnav529 - 1
        else:
            period_return = 0.0
        fund_value_simulated_618 = fund_value_529 * (1 + period_return)
        bucket["simulated_value_cny"] += fund_value_simulated_618

        for s in constituents:
            code_norm = s.stock_code.split(".")[0]
            snap = _resolve_snap_for_code(db, s.stock_code, as_of_date, a_snap, h_snap)
            # Prefer stored weight (from akshare pull_index_weights);
            # fall back to equal-weight only when ALL constituents have null weight.
            weight_pct = cache["equal_weight_pct"]
            if cache["has_weight_col"]:
                w = getattr(s, "weight", None)
                if w is not None:
                    weight_pct = w
            baseline_price = snap.baseline_price if snap else None
            current_price = snap.current_price if snap else None
            pe_d = pb_d = ps_d = dy = None
            if snap:
                pe_d = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
                pb_d = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
                ps_d = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
                dy = snap.dividend_yield

            amount_529 = fund_value_529 * (weight_pct / 100.0)
            if baseline_price and baseline_price > 0:
                shares = amount_529 / baseline_price  # keep float for calc
                shares_int = int(shares)               # display as integer
            else:
                shares = None
                shares_int = None
            # 上一日价格 = current_price (latest close); fallback to baseline_price if missing
            prev_price = current_price if (current_price and current_price > 0) else baseline_price
            if shares is not None and prev_price and prev_price > 0:
                est_value = shares * prev_price
            else:
                est_value = amount_529

            # Dynamic PE/PB/PS/股息率 from snapshot using prev_price/baseline_price ratio
            price_ratio = None
            if baseline_price and prev_price and baseline_price > 0 and prev_price > 0:
                price_ratio = prev_price / baseline_price
            pe_dyn = (pe_d * price_ratio) if (pe_d and price_ratio) else pe_d
            pb_dyn = (pb_d * price_ratio) if (pb_d and price_ratio) else pb_d
            ps_dyn = (ps_d * price_ratio) if (ps_d and price_ratio) else ps_d
            dy_dyn = (dy / price_ratio) if (dy and price_ratio) else dy

            bucket["static_amount_cny"] += amount_529
            bucket["est_market_value_cny"] += est_value
            bucket["stock_set"].add(s.stock_code)
            if amount_529 > 0:
                if pe_dyn and pe_dyn > 0:
                    bucket["virt_pe"] += amount_529 / pe_dyn
                if pb_dyn and pb_dyn > 0:
                    bucket["virt_pb"] += amount_529 / pb_dyn
                if ps_dyn and ps_dyn > 0:
                    bucket["virt_ps"] += amount_529 / ps_dyn
                if dy_dyn is not None:
                    bucket["sum_dy_weighted"] += amount_529 * dy_dyn

    cards = []
    total_est = sum(b["est_market_value_cny"] for b in by_index.values())
    for bucket in by_index.values():
        static = bucket["static_amount_cny"]
        est = bucket["est_market_value_cny"]
        simulated = bucket["simulated_value_cny"]
        prev_total = bucket["prev_fund_value_total"]
        virt_pe = bucket["virt_pe"]
        virt_pb = bucket["virt_pb"]
        virt_ps = bucket["virt_ps"]
        dy_sum = bucket["sum_dy_weighted"]

        # 跟踪偏差 = (模拟市值 - 按基金推算资产) / 按基金推算资产
        # 这才是真正的「跟踪误差」或「调仓/估值差异」
        dev_pct = ((est - simulated) / simulated * 100) if simulated else 0.0
        weight_pct = round(est / total_est * 100, 4) if total_est else 0.0
        cards.append({
            "index_code": bucket["index_code"],
            "index_name": bucket["index_name"],
            "fund_codes": sorted(bucket["fund_codes"]),
            "fund_5_29_nav_used": bucket["fund_5_29_nav_used"],
            "stock_count": len(bucket["stock_set"]),
            "static_amount_cny": round(static, 4),                  # 5/29 单位净值反推资产
            "simulated_value_cny": round(simulated, 4),            # 按基金累计净值推 6/18
            "prev_fund_value_total": round(prev_total, 4),         # 6/18 实际持仓 (audit)
            "est_market_value_cny": round(est, 4),                  # 模拟 6/18 持仓市值
            "est_deviation_pct": round(dev_pct, 4),
            "weight_pct": weight_pct,
            "weighted_pe": round(static / virt_pe, 4) if virt_pe else None,
            "weighted_pb": round(static / virt_pb, 4) if virt_pb else None,
            "weighted_ps": round(static / virt_ps, 4) if virt_ps else None,
            "weighted_dividend_yield": round(dy_sum / static, 4) if static else None,
        })

    cards.sort(key=lambda c: (c["weighted_pe"] is None, -(c["weighted_pe"] or 0)))
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
    """Drill-down detail using precise formula per stock.

    支持传入预加载的 holdings_agg / fund_navs / snapshots，避免重复查库。
    user_id 透传到内部 _aggregate_holdings_by_fund；外部传 holdings_agg 则用外部的（2026-06-24）。
    """
    idx_code = index_code.split(".")[0]
    fund_maps = db.query(FundIndexMap).filter(
        FundIndexMap.index_code.startswith(idx_code),
        FundIndexMap.as_of_date == as_of_date,
    ).all()
    if not fund_maps:
        return {"error": "no funds tracking this index"}
    fund_codes = [f.fund_code for f in fund_maps]

    if holdings_agg is None:
        holdings_agg = _aggregate_holdings_by_fund(db, user_id=user_id)
    if fund_navs is None:
        fund_navs = _load_fund_navs(db, fund_codes)
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

    constituents = db.query(IndexConstituentSnapshot).filter(
        IndexConstituentSnapshot.as_of_date == as_of_date,
        IndexConstituentSnapshot.index_code == idx_code,
    ).all()
    has_weight_col = "weight" in [c.name for c in IndexConstituentSnapshot.__table__.columns]
    any_weight = any((getattr(c, "weight", None) for c in constituents))
    eq_w_pct = (100.0 / len(constituents)) if constituents else 0.0

    total_fund_value = sum(holdings_agg.get(fc, {}).get("amount_cny", 0) for fc in fund_codes)
    total_fund_shares = sum(holdings_agg.get(fc, {}).get("quantity", 0) for fc in fund_codes)

    by_stock: dict[str, dict] = {}
    for s in constituents:
        code = s.stock_code
        snap = _resolve_snap_for_code(db, code, as_of_date, a_snap, h_snap)
        weight_pct = eq_w_pct
        if has_weight_col:
            w = getattr(s, "weight", None)
            if w is not None:
                weight_pct = w
        baseline_price = snap.baseline_price if snap else None
        current_price = snap.current_price if snap else None
        pe_v = ps_v = pb_v = dy_v = None
        if snap:
            pe_v = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
            pb_v = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
            ps_v = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
            dy_v = snap.dividend_yield

        total_shares = 0.0
        total_shares_int_disp = 0
        total_amount_529 = 0.0
        for fc in fund_codes:
            finfo = holdings_agg.get(fc)
            if not finfo or finfo["amount_cny"] <= 0:
                continue
            nav529 = (fund_navs.get(fc) or {}).get("nav_529")
            f_shares = finfo["quantity"]
            f_value_529 = (f_shares * nav529) if (nav529 and f_shares > 0) else finfo["amount_cny"]
            amount_529 = f_value_529 * (weight_pct / 100.0)
            total_amount_529 += amount_529
            if baseline_price and baseline_price > 0:
                total_shares += amount_529 / baseline_price
        if total_shares and current_price and current_price > 0:
            est_market_value = total_shares * current_price
            total_shares_int_disp = int(round(total_shares))
        else:
            est_market_value = total_amount_529

        by_stock[code] = {
            "stock_code": code,
            "stock_name": s.stock_name,
            "weight_at_baseline_pct": round(weight_pct, 4),
            "shares_equivalent": total_shares_int_disp if total_shares else None,
            "baseline_price": baseline_price,
            "current_price": current_price,
            "current_price_date": snap.current_price_date.isoformat() if snap and snap.current_price_date else None,
            "est_market_value_cny": round(est_market_value, 4),
            "pe_ttm": pe_v,
            "pb_mrq": pb_v,
            "ps_ttm": ps_v,
            "dividend_yield": dy_v,
        }

    rows = sorted(by_stock.values(), key=lambda r: r["est_market_value_cny"] or 0, reverse=True)
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
    """跨所有可下钻指数聚合成分股 (drill 算法 = 与 get_index_drill_detail 一致).

    支持传入预加载的 indices / holdings_agg / fund_navs / snapshots，避免重复查库。
    user_id 透传到 list_drillable_indices（2026-06-24）。

    Returns: {
      "as_of_date": ...,
      "stocks": [{stock_code, stock_name, shares_equivalent, current_price,
                  baseline_price, current_price_date, est_market_value_cny,
                  pe_ttm, pb_mrq, ps_ttm, dividend_yield, indices}, ...],
      "count": N
    }

    同一股票若同时被多只基金 (跨多个指数) 持有, shares_equivalent 与
    est_market_value_cny 求和; 价格 / 估值指标取首个非空值.
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
                    "shares_equivalent": 0,
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
            acc["shares_equivalent"] += (c.get("shares_equivalent") or 0)
            acc["est_market_value_cny"] += (c.get("est_market_value_cny") or 0)
            acc["indices"].add(idx_code)
            # 价格/估值字段: 取首个非空值
            for k in ("current_price", "baseline_price", "current_price_date",
                      "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield"):
                if acc.get(k) is None and c.get(k) is not None:
                    acc[k] = c.get(k)

    stocks = []
    for s in by_stock.values():
        s["indices"] = sorted(s["indices"])
        s["est_market_value_cny"] = round(s["est_market_value_cny"], 4)
        stocks.append(s)
    stocks.sort(key=lambda r: r["est_market_value_cny"], reverse=True)

    return {
        "as_of_date": as_of_date.isoformat(),
        "stocks": stocks,
        "count": len(stocks),
    }