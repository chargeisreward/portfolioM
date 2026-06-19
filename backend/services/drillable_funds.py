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


def _aggregate_holdings_by_fund(db: Session) -> dict[str, dict]:
    """Sum quantities and amount_cny per fund_code (across all buy batches)."""
    out: dict[str, dict] = {}
    for h in db.query(Holding).all():
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
    """
    out: dict[str, dict] = {}
    for fc in fund_codes:
        nav529 = db.query(FundDailyNav).filter_by(fund_code=fc, trade_date=AS_OF_DATE).first()
        nav618 = db.query(FundDailyNav).filter_by(fund_code=fc, trade_date=AS_OF_DATE_T1).first()
        out[fc] = {
            "nav_529": nav529.nav if nav529 else None,
            "cumnav_529": nav529.accumulated_nav if nav529 else None,
            "nav_618": nav618.nav if nav618 else None,
            "cumnav_618": nav618.accumulated_nav if nav618 else None,
        }
    return out


def list_drillable_indices(db: Session, as_of_date: _date) -> list[dict]:
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
    holdings_agg = _aggregate_holdings_by_fund(db)

    a_snap = {a.stock_code.split(".")[0]: a for a in
              db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap = {h.stock_code.split(".")[0]: h for h in
              db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}

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


def get_index_drill_detail(db: Session, index_code: str, as_of_date: _date) -> dict:
    """Drill-down detail using precise formula per stock."""
    idx_code = index_code.split(".")[0]
    fund_maps = db.query(FundIndexMap).filter(
        FundIndexMap.index_code.startswith(idx_code),
        FundIndexMap.as_of_date == as_of_date,
    ).all()
    if not fund_maps:
        return {"error": "no funds tracking this index"}
    fund_codes = [f.fund_code for f in fund_maps]
    holdings_agg = _aggregate_holdings_by_fund(db)
    fund_navs = _load_fund_navs(db, fund_codes)

    a_snap = {a.stock_code.split(".")[0]: a for a in
              db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap = {h.stock_code.split(".")[0]: h for h in
              db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}

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
        code_norm = code.split(".")[0]
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