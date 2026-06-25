"""trend_heal.py — /api/trend 走势图三级回退自愈服务

取价口径（不考虑 drillable，只按 .OF vs 非 .OF 区分）：
- .OF 基金：use_nav=True，从 fund_daily_nav 取价（基金净值）
- 非 .OF（ETF/股票/港股/美股）：use_nav=False，从 price_cache 取二级市场收盘价

三级回退：
1. 第一级 cache：查 price_cache 现有覆盖
2. 第二级 公共数据：仅 .OF — 从 fund_daily_nav 加载到 price_cache（close_px = nav or accumulated_nav）
3. 第三级 API 拉取：
   - .OF：东财 lsjz → 写 fund_daily_nav + 写 price_cache
   - 非 .OF：腾讯 K 线（fetch_price_history force=True）→ 写 price_cache

覆盖率阈值 90%；360 天固定口径；新上市按实际最早 trade_date 调整 expected_count。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from services.trading_calendar import _market_for_code, expected_trading_dates

logger = logging.getLogger(__name__)

COVERAGE_THRESHOLD = 0.90
TREND_WINDOW_DAYS = 360


def _is_of_code(code: str) -> bool:
    return (code or "").upper().strip().endswith(".OF")


# ============================================================
# 第二级：从 fund_daily_nav 加载到 price_cache（仅 .OF）
# ============================================================
def _load_fund_nav_to_price_cache(db: Session, code: str, cutoff: date) -> int:
    """close_px = nav or accumulated_nav；应用层 exists 检查去重（price_cache 无唯一约束）"""
    from models import FundDailyNav, PriceCache

    rows = db.query(FundDailyNav).filter(
        FundDailyNav.fund_code == code,
        FundDailyNav.trade_date >= cutoff,
    ).all()
    added = 0
    for r in rows:
        px = r.nav or r.accumulated_nav
        if not px or px <= 0:
            continue
        exists = db.query(PriceCache).filter(
            PriceCache.stock_code == code,
            PriceCache.trade_date == r.trade_date,
        ).first()
        if exists:
            continue
        db.add(PriceCache(
            stock_code=code, trade_date=r.trade_date,
            open_px=px, high_px=px, low_px=px, close_px=px,
            volume=0, source="fund_daily_nav",
        ))
        added += 1
    if added:
        db.commit()
    return added


# ============================================================
# 第三级 .OF：东财 lsjz → 写 fund_daily_nav + 写 price_cache
# ============================================================
def _fetch_of_from_api(db: Session, code: str, cutoff: date) -> int:
    """复用 pull_fund_nav_em.py 的 _fetch_all / _parse_row（已验证可用）"""
    from models import FundDailyNav
    from scripts.pull_fund_nav_em import _fetch_all, _parse_row

    bare_code = code.replace(".OF", "").strip()
    end_s = date.today().isoformat()
    start_s = cutoff.isoformat()
    try:
        rows_raw = _fetch_all(bare_code, start_s, end_s)
    except Exception as e:
        logger.warning("heal: eastmoney lsjz fetch failed for %s: %s", code, e)
        return 0
    rows = [r for r in (_parse_row(x) for x in rows_raw) if r]
    if not rows:
        logger.warning("heal: %s no rows from eastmoney", code)
        return 0

    # 写 fund_daily_nav（有 ux_fdn_code_date 唯一约束，exists 检查）
    existing_fdn = {
        r.trade_date: r
        for r in db.query(FundDailyNav).filter_by(fund_code=code).all()
    }
    for r in rows:
        td = r["trade_date"]
        if td in existing_fdn:
            e = existing_fdn[td]
            if (e.nav != r["nav"]
                    or (e.accumulated_nav is None and r["accumulated_nav"] is not None)
                    or (e.accumulated_nav != r["accumulated_nav"] and r["accumulated_nav"] is not None)
                    or (e.daily_return != r["daily_return"] and r["daily_return"] is not None)):
                e.nav = r["nav"]
                if r["accumulated_nav"] is not None:
                    e.accumulated_nav = r["accumulated_nav"]
                if r["daily_return"] is not None:
                    e.daily_return = r["daily_return"]
        else:
            db.add(FundDailyNav(
                fund_code=code, trade_date=td,
                nav=r["nav"], accumulated_nav=r["accumulated_nav"],
                daily_return=r["daily_return"], source="eastmoney",
            ))
    db.commit()

    # 写 price_cache（复用第二级逻辑）
    return _load_fund_nav_to_price_cache(db, code, cutoff)


# ============================================================
# 第三级 非 .OF：腾讯 K 线 → 写 price_cache
# ============================================================
def _fetch_non_of_from_api(db: Session, code: str, cutoff: date) -> int:
    """复用 fetch_price_history(force=True)；返回行字段: date/open/close/high/low/volume"""
    from models import PriceCache
    from crawlers.price_data import fetch_price_history

    try:
        rows = fetch_price_history(code, days=TREND_WINDOW_DAYS, force=True)
    except Exception as e:
        logger.warning("heal: tencent kline fetch failed for %s: %s", code, e)
        return 0
    if not rows:
        logger.warning("heal: %s no rows from tencent", code)
        return 0

    added = 0
    for r in rows:
        td = r.get("date") or r.get("trade_date")
        px = r.get("close") or r.get("close_px")
        if not td or not px:
            continue
        if isinstance(td, str):
            try:
                td = datetime.strptime(td[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
        if td < cutoff:
            continue
        exists = db.query(PriceCache).filter(
            PriceCache.stock_code == code, PriceCache.trade_date == td,
        ).first()
        if exists:
            continue
        db.add(PriceCache(
            stock_code=code, trade_date=td,
            open_px=r.get("open"), high_px=r.get("high"),
            low_px=r.get("low"), close_px=px,
            volume=r.get("volume", 0), source="tencent",
        ))
        added += 1
    if added:
        db.commit()
    return added


# ============================================================
# 主入口：heal_trend_data
# ============================================================
def heal_trend_data(db: Session, holding_codes: list[str], days: int = 360) -> dict:
    """检查并补齐 holding_codes 在过去 days 天的 price_cache 覆盖率。

    Returns:
        {checked, healed, skipped_sufficient, failed, details: [...]}
    """
    from models import PriceCache, FundDailyNav

    cutoff = date.today() - timedelta(days=days)
    details: list[dict] = []
    healed = 0
    skipped = 0
    failed = 0

    for code in holding_codes:
        use_nav = _is_of_code(code)
        market = "OF" if use_nav else _market_for_code(code)

        # 第一级：查 price_cache 现有覆盖（DISTINCT 去重，price_cache 无唯一约束）
        existing_count = db.query(func.count(func.distinct(PriceCache.trade_date))).filter(
            PriceCache.stock_code == code,
            PriceCache.trade_date >= cutoff,
        ).scalar() or 0

        # 新上市按实际最早 trade_date 调整 expected_count
        earliest = db.query(func.min(PriceCache.trade_date)).filter(
            PriceCache.stock_code == code,
        ).scalar()
        if use_nav:
            fdn_earliest = db.query(func.min(FundDailyNav.trade_date)).filter(
                FundDailyNav.fund_code == code,
            ).scalar()
            if fdn_earliest and (not earliest or fdn_earliest < earliest):
                earliest = fdn_earliest

        eff_cutoff = max(cutoff, earliest) if earliest else cutoff
        expected = expected_trading_dates(market, days, db)
        expected_in_window = [d for d in expected if d >= eff_cutoff]
        expected_count = len(expected_in_window) or 1
        coverage_before = existing_count / expected_count

        if coverage_before >= COVERAGE_THRESHOLD:
            skipped += 1
            details.append({
                "code": code, "use_nav": use_nav,
                "coverage_before": round(coverage_before, 3),
                "coverage_after": round(coverage_before, 3),
                "rows_added": 0, "source": "cache_sufficient",
            })
            continue

        # 第二级 + 第三级
        rows_added = 0
        source = ""
        try:
            if use_nav:
                # 第二级：从 fund_daily_nav 加载
                rows_added = _load_fund_nav_to_price_cache(db, code, cutoff)
                source = "fund_daily_nav"
                # 重新检查覆盖率
                existing_count2 = db.query(func.count(func.distinct(PriceCache.trade_date))).filter(
                    PriceCache.stock_code == code, PriceCache.trade_date >= cutoff,
                ).scalar() or 0
                if existing_count2 / expected_count < COVERAGE_THRESHOLD:
                    # 第三级：东财 lsjz
                    added3 = _fetch_of_from_api(db, code, cutoff)
                    rows_added += added3
                    source = "eastmoney_lsjz"
            else:
                # 第三级：腾讯 K 线（非 .OF 直接走第三级）
                rows_added = _fetch_non_of_from_api(db, code, cutoff)
                source = "tencent_kline"

            existing_after = db.query(func.count(func.distinct(PriceCache.trade_date))).filter(
                PriceCache.stock_code == code, PriceCache.trade_date >= cutoff,
            ).scalar() or 0
            coverage_after = existing_after / expected_count
            healed += 1
            details.append({
                "code": code, "use_nav": use_nav,
                "coverage_before": round(coverage_before, 3),
                "coverage_after": round(coverage_after, 3),
                "rows_added": rows_added, "source": source,
            })
        except Exception as e:
            failed += 1
            logger.exception("heal: failed for %s", code)
            details.append({
                "code": code, "use_nav": use_nav,
                "coverage_before": round(coverage_before, 3),
                "coverage_after": round(coverage_before, 3),
                "rows_added": 0, "source": f"failed: {type(e).__name__}: {str(e)[:80]}",
            })

    return {
        "checked": len(holding_codes),
        "healed": healed,
        "skipped_sufficient": skipped,
        "failed": failed,
        "details": details,
    }
