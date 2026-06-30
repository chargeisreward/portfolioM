"""intraday_change_service.py — 总览「当日涨跌幅」按全持仓加权计算。

用户 2026-06-30 反馈：
  原 KPI 算法（'/api/penetration/kpi' intraday_change_pct）只取 PriceCache.change_pct
  实时字段，.OF 基金 / 下钻成分股都被排除在分子外，导致数值偏小或不更新。

新算法（spec 2026-06-30）：
  以「分析 → 全持仓」页数据为口径（已含下钻成分股）：
    - undrilled direct_stock  → PriceCache[today] vs prev close → dcp
    - undrilled_fund (.OF)    → FundDailyNav[today] vs prev → dcp
    - drilled 成分股          → FundDrillSnapshot[today].current_price_cny
                                vs prev current_price_cny → dcp（同 code 多 fund 取均值）
    - cash                     → 跳过（不参与分子，但仍占分母权重）

  最终：
    intraday_change_pct = Σ(emv_i × dcp_i/100) / Σ(emv_i) × 100

参考：
  - 全持仓 API: backend/main.py:2988 get_full_holding_table
  - 数据源服务: backend/services/full_holding_service.py
  - 手工测算: 2026-06-30 user 2 / 6/30 = +0.4563%
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _price_cache_map(db: Session, dates: list[_date]) -> dict[str, dict[str, float]]:
    """读指定日期的 PriceCache，输出 {code: {YYYY-MM-DD: close_px}}。

    同 code 同 date 多行（intraday 多次抓取）取最后一个 close_px。
    """
    out: dict[str, dict[str, float]] = {}
    if not dates:
        return out
    rows = db.execute(
        text("""
            SELECT stock_code, trade_date, close_px
            FROM price_cache
            WHERE trade_date = ANY(:dates) AND close_px IS NOT NULL
            ORDER BY stock_code, trade_date, id
        """),
        {"dates": dates},
    ).fetchall()
    for r in rows:
        code = r[0]
        if code not in out:
            out[code] = {}
        out[code][r[1].isoformat()] = float(r[2])
    return out


def _fund_daily_nav_map(db: Session, dates: list[_date]) -> dict[str, dict[str, float]]:
    """FundDailyNav → {code: {YYYY-MM-DD: nav}}。"""
    out: dict[str, dict[str, float]] = {}
    if not dates:
        return out
    rows = db.execute(
        text("""
            SELECT fund_code, trade_date, nav
            FROM fund_daily_nav
            WHERE trade_date = ANY(:dates)
        """),
        {"dates": dates},
    ).fetchall()
    for r in rows:
        code = r[0]
        if code not in out:
            out[code] = {}
        out[code][r[1].isoformat()] = float(r[2])
    return out


def _drill_snapshot_prices(
    db: Session, dates: list[_date]
) -> dict[str, dict[str, list[float]]]:
    """FundDrillSnapshot.current_price_cny → {code: {date: [price list per fund]}}。"""
    out: dict[str, dict[str, list[float]]] = {}
    if not dates:
        return out
    rows = db.execute(
        text("""
            SELECT stock_code, as_of_date, current_price_cny
            FROM fund_drill_snapshot
            WHERE as_of_date = ANY(:dates) AND current_price_cny IS NOT NULL
        """),
        {"dates": dates},
    ).fetchall()
    for r in rows:
        code = r[0]
        if code not in out:
            out[code] = {}
        if r[1].isoformat() not in out[code]:
            out[code][r[1].isoformat()] = []
        out[code][r[1].isoformat()].append(float(r[2]))
    return out


def _get_dcp(
    code: str,
    today_iso: str,
    prev_iso: str,
    source_type: str | None,
    is_cash: bool,
    price_cache: dict[str, dict[str, float]],
    fund_navs: dict[str, dict[str, float]],
    drill_prices: dict[str, dict[str, list[float]]],
) -> float | None:
    """计算单证券 dcp (%)。返回 None 表示无价。"""
    if is_cash:
        return None
    if source_type == "undrilled_fund":
        nav = fund_navs.get(code, {})
        n_t = nav.get(today_iso)
        n_p = nav.get(prev_iso)
        if n_t is not None and n_p is not None and n_p > 0:
            return (n_t - n_p) / n_p * 100.0
        return None
    if source_type == "direct_stock":
        ps = price_cache.get(code, {})
        p_t = ps.get(today_iso)
        p_p = ps.get(prev_iso)
        if p_t is not None and p_p is not None and p_p > 0:
            return (p_t - p_p) / p_p * 100.0
        return None
    # drilled / default
    dps = drill_prices.get(code, {})
    p_t_list = dps.get(today_iso, [])
    p_p_list = dps.get(prev_iso, [])
    if not p_t_list or not p_p_list:
        return None
    p_t = sum(p_t_list) / len(p_t_list)
    p_p = sum(p_p_list) / len(p_p_list)
    if p_p <= 0:
        return None
    return (p_t - p_p) / p_p * 100.0


def compute_intraday_change_pct(
    db: Session, as_of_date: _date, user_id: int
) -> dict:
    """计算指定用户在 as_of_date 的「当日涨跌幅」（按全持仓市值加权）。

    Returns:
      {
        "as_of_date": str,
        "prev_trade_date": str,
        "intraday_change_pct": float | None,
        "breakdown": {
            "total_emv_cny": float, "covered_emv_cny": float,
            "covered_count": int, "total_count": int,
            "coverage_rate": float,
            "top_contributions": [{"code", "source", "emv", "dcp", "contrib"}, ...]
        }
      }
    """
    from services.full_holding_service import build_full_holding_for_user

    # 1. 拿 undrilled + drilled（与 full-holding-table API 严格同口径）
    full = build_full_holding_for_user(db, as_of_date, user_id)
    undrilled_rows = full["undrilled"]
    drilled_stocks = full["drilled"]

    # 2. 找「最新一对连续交易日」both today + prev 都有 drill_snapshot 数据
    # （as_of_date 参数仅作 hint；实际 today 用 max(fund_drill_snapshot.as_of_date)，
    #  prev 用同表中第二大的 date — 保证 drilled stocks 有可比价）
    drill_dates = [
        r[0] for r in db.execute(
            text("""
                SELECT DISTINCT as_of_date FROM fund_drill_snapshot
                ORDER BY as_of_date DESC
                LIMIT 10
            """)
        ).fetchall()
    ]
    if len(drill_dates) < 2:
        return {
            "as_of_date": as_of_date.isoformat(),
            "prev_trade_date": None,
            "intraday_change_pct": None,
            "breakdown": {"total_emv_cny": 0, "covered_emv_cny": 0,
                          "covered_count": 0, "total_count": 0,
                          "coverage_rate": 0, "top_contributions": []},
        }
    today_dt = drill_dates[0]   # max
    prev_dt = drill_dates[1]    # second-max
    today_iso = today_dt.isoformat()
    prev_iso = prev_dt.isoformat()

    # 3. 加载价格
    price_cache = _price_cache_map(db, [today_dt, prev_dt])
    fund_navs = _fund_daily_nav_map(db, [today_dt, prev_dt])
    drill_prices = _drill_snapshot_prices(db, [today_dt, prev_dt])

    # 4. 计算每行 dcp + 加权
    total_emv = 0.0
    covered_emv = 0.0
    weighted_sum_pct = 0.0  # 单位：CNY（即 emv × dcp% / 100）
    contributions = []
    covered_count = 0
    total_count = 0

    # undrilled
    for r in undrilled_rows:
        emv = float(r.get("est_market_value_cny") or 0)
        if emv <= 0:
            continue
        total_count += 1
        total_emv += emv
        dcp = _get_dcp(
            r["stock_code"], today_iso, prev_iso,
            r.get("source_type"), False,
            price_cache, fund_navs, drill_prices,
        )
        if dcp is not None:
            weighted_sum_pct += dcp * emv / 100.0
            covered_emv += emv
            covered_count += 1
            contributions.append({
                "code": r["stock_code"], "source": r.get("source_type"),
                "emv": emv, "dcp": dcp,
                "contrib": dcp * emv / 100.0,
            })

    # drilled (含 CASH)
    for s in drilled_stocks:
        code = s.get("stock_code")
        emv = float(s.get("est_market_value_cny") or 0)
        is_cash = bool(s.get("is_cash")) or code == "CASH"
        if emv <= 0:
            continue
        total_count += 1
        total_emv += emv
        if is_cash:
            continue
        dcp = _get_dcp(
            code, today_iso, prev_iso,
            "drilled", False,
            price_cache, fund_navs, drill_prices,
        )
        if dcp is not None:
            weighted_sum_pct += dcp * emv / 100.0
            covered_emv += emv
            covered_count += 1
            contributions.append({
                "code": code, "source": "drilled",
                "emv": emv, "dcp": dcp,
                "contrib": dcp * emv / 100.0,
            })

    intraday_change_pct = (
        round(weighted_sum_pct / total_emv * 100, 4) if total_emv > 0 else None
    )

    contributions.sort(key=lambda x: abs(x["contrib"]), reverse=True)

    return {
        "as_of_date": today_iso,
        "prev_trade_date": prev_iso,
        "intraday_change_pct": intraday_change_pct,
        "breakdown": {
            "total_emv_cny": round(total_emv, 2),
            "covered_emv_cny": round(covered_emv, 2),
            "covered_count": covered_count,
            "total_count": total_count,
            "coverage_rate": round(covered_emv / total_emv * 100, 2) if total_emv > 0 else 0,
            "top_contributions": contributions[:10],
        },
    }