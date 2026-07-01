"""intraday_change_service.py — 总览「当日涨跌幅」按全持仓加权计算（智能路由）。

用户 2026-06-30 反馈 + 2026-07-01 修正：
  原 KPI 算法（'/api/penetration/kpi' intraday_change_pct）只取 PriceCache.change_pct
  实时字段，.OF 基金 / 下钻成分股都被排除在分子外，导致数值偏小或不更新。
  再者：当日涨跌幅完全用 admin 最新 drill pair (latest vs prev)，日历日 ≠ 实际 today。

新算法（spec 2026-07-01 智能路由）：
  per-holding routing，让数据接口返回值决定使用哪种口径（不硬写时间窗口）：
    - cash                                          → 跳过
    - undrilled_fund (.OF)                          → drill pair（FundDailyNav，无实时）
    - direct_stock / drilled 成分股                 → 优先 PriceCache[today].change_pct
                                                     (cron 每 5min 写腾讯 parts[32])
                                                     非零 → 用；否则 → drill pair

  关键：cron 不区分市场地覆写所有 code：
    - 在 A 股时段，cron 把美股 code 覆写为腾讯返回的"上一次美股收盘涨跌幅"
    - 在美股时段，cron 把 A 股 code 覆写为腾讯返回的"上一次 A 股收盘涨跌幅"
    - 港股同理
  → 非交易时段返回"最近一天收盘涨跌幅"，交易时段返回实时涨跌幅。
  → 无需硬编码市场开放窗口，让数据接口的返回值决定。

  最终：
    intraday_change_pct = Σ(emv_i × dcp_i/100) / Σ(emv_i) × 100

参考：
  - 全持仓 API: backend/main.py:2988 get_full_holding_table
  - 数据源服务: backend/services/full_holding_service.py
  - cron: backend/services/scheduler.py:447 job_fetch_intraday_change_pct
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _classify_market(code: str | None, source_type: str | None = None) -> str:
    """按 code 后缀/前缀返回所属市场。

    Returns: 'a_share' | 'hk' | 'us' | 'fund' | 'cash' | 'unknown'
    """
    if not code:
        return "unknown"
    c = code.strip()
    cu = c.upper()
    if cu == "CASH":
        return "cash"
    if cu.endswith(".OF"):
        return "fund"
    # Exchange suffix
    if cu.endswith(".SH") or cu.endswith(".SZ"):
        return "a_share"
    if cu.endswith(".HK"):
        return "hk"
    if cu.endswith((".OQ", ".N", ".O", ".OA")):
        return "us"
    # Numeric codes
    if c.isdigit() and len(c) == 6:
        return "a_share"
    if c.isdigit() and len(c) == 5:
        return "hk"
    # Already-prefixed: sh600519 / sz000001 / hk00700 / usNVDA
    cl = c.lower()
    if cl.startswith(("sh", "sz")):
        return "a_share"
    if cl.startswith("hk"):
        return "hk"
    if cl.startswith("us"):
        return "us"
    return "unknown"


def _load_pc_change_pct(db: Session, today: _date) -> dict[str, float]:
    """读 PriceCache[today].change_pct → {code: change_pct}。

    同 (code, today) 多行（cron 每5min 覆写）取最新一行的值。
    """
    rows = db.execute(
        text("""
            SELECT DISTINCT ON (stock_code) stock_code, change_pct
            FROM price_cache
            WHERE trade_date = :today
              AND change_pct IS NOT NULL
            ORDER BY stock_code, id DESC
        """),
        {"today": today},
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows}


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
    """智能路由：当日涨跌幅按全持仓市值加权。

    per-holding routing:
      - cash                       → 跳过
      - undrilled_fund (.OF)       → drill pair (FundDailyNav)
      - direct_stock / drilled     → 优先 PriceCache[today].change_pct (cron 写);
                                    非零 → 用；否则 → drill pair

    Returns:
      {
        "as_of_date": str (今天 = date.today()),
        "intraday_change_pct": float | None,
        "breakdown": {
            "total_emv_cny", "covered_emv_cny",
            "covered_count", "total_count", "coverage_rate",
            "realtime_count": int,           # 用了 PriceCache[today] 的只数
            "fallback_count": int,           # 回落 drill pair 的只数
            "top_contributions": [{"code", "source", "market", "emv", "dcp", "contrib"}, ...]
        }
      }
    """
    from services.full_holding_service import build_full_holding_for_user

    # 1. 拿 undrilled + drilled（与 full-holding-table API 严格同口径）
    full = build_full_holding_for_user(db, as_of_date, user_id)
    undrilled_rows = full["undrilled"]
    drilled_stocks = full["drilled"]

    # 2. 找「最新一对连续 drill snapshot 日期」→ admin fallback 口径
    #    （与持仓权重快照完全同口径 — 用户持仓权重的分母日）
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
            "as_of_date": _date.today().isoformat(),
            "intraday_change_pct": None,
            "breakdown": {"total_emv_cny": 0, "covered_emv_cny": 0,
                          "covered_count": 0, "total_count": 0,
                          "coverage_rate": 0,
                          "realtime_count": 0, "fallback_count": 0,
                          "top_contributions": []},
        }
    latest_drill = drill_dates[0]   # max
    prev_drill = drill_dates[1]      # second-max
    latest_drill_iso = latest_drill.isoformat()
    prev_drill_iso = prev_drill.isoformat()

    # 3. 日历 today — 数据接口的 "今日"（realtime 数据的目标日期）
    today = _date.today()
    today_iso = today.isoformat()

    # 4. 加载 realtime 源 (PriceCache[today].change_pct)
    pc_change = _load_pc_change_pct(db, today)

    # 5. 加载 fallback 源（drill pair 价格）
    price_cache = _price_cache_map(db, [latest_drill, prev_drill])
    fund_navs = _fund_daily_nav_map(db, [latest_drill, prev_drill])
    drill_prices = _drill_snapshot_prices(db, [latest_drill, prev_drill])

    # 6. 计算每行 dcp + 加权
    total_emv = 0.0
    covered_emv = 0.0
    weighted_sum_pct = 0.0  # 单位：CNY（即 emv × dcp% / 100）
    contributions = []
    covered_count = 0
    total_count = 0
    realtime_count = 0
    fallback_count = 0

    def _route(code: str, source_type: str | None) -> tuple[float | None, str]:
        """per-holding 路由。返回 (dcp, source_used)。"""
        # cash 跳过（caller 处理）
        if code == "CASH":
            return None, "cash"
        market = _classify_market(code, source_type)
        # .OF 基金：FundDailyNav 路径（无 realtime）
        if market == "fund" or source_type == "undrilled_fund":
            dcp = _get_dcp(
                code, latest_drill_iso, prev_drill_iso,
                "undrilled_fund", False,
                price_cache, fund_navs, drill_prices,
            )
            return dcp, "fund_nav"
        # direct_stock / drilled：先试 realtime
        rt = pc_change.get(code)
        if rt is not None and rt != 0.0:
            return float(rt), "realtime"
        # 回落 drill pair
        dcp = _get_dcp(
            code, latest_drill_iso, prev_drill_iso,
            "drilled" if source_type == "drilled" else "direct_stock",
            False,
            price_cache, fund_navs, drill_prices,
        )
        return dcp, "drill_pair"

    # undrilled
    for r in undrilled_rows:
        code = r["stock_code"]
        emv = float(r.get("est_market_value_cny") or 0)
        if emv <= 0:
            continue
        total_count += 1
        total_emv += emv
        if code == "CASH" or r.get("source_type") == "cash":
            continue
        dcp, src = _route(code, r.get("source_type"))
        if dcp is not None:
            weighted_sum_pct += dcp * emv / 100.0
            covered_emv += emv
            covered_count += 1
            if src == "realtime":
                realtime_count += 1
            else:
                fallback_count += 1
            contributions.append({
                "code": code, "source": src, "market": _classify_market(code, r.get("source_type")),
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
        dcp, src = _route(code, "drilled")
        if dcp is not None:
            weighted_sum_pct += dcp * emv / 100.0
            covered_emv += emv
            covered_count += 1
            if src == "realtime":
                realtime_count += 1
            else:
                fallback_count += 1
            contributions.append({
                "code": code, "source": src, "market": _classify_market(code, "drilled"),
                "emv": emv, "dcp": dcp,
                "contrib": dcp * emv / 100.0,
            })

    intraday_change_pct = (
        round(weighted_sum_pct / total_emv * 100, 4) if total_emv > 0 else None
    )

    contributions.sort(key=lambda x: abs(x["contrib"]), reverse=True)

    return {
        "as_of_date": today_iso,
        "intraday_change_pct": intraday_change_pct,
        "breakdown": {
            "total_emv_cny": round(total_emv, 2),
            "covered_emv_cny": round(covered_emv, 2),
            "covered_count": covered_count,
            "total_count": total_count,
            "coverage_rate": round(covered_emv / total_emv * 100, 2) if total_emv > 0 else 0,
            "realtime_count": realtime_count,
            "fallback_count": fallback_count,
            "top_contributions": contributions[:10],
        },
    }