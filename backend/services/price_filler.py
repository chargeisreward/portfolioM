"""price_filler.py — fill missing prices via Tencent API (spec §2.4 fallback).

When price_cache has no prev-close for a stock AND the Excel's
baseline_price is the only price we have, we still lack `current_price`
to compute dynamic PE/PB/PS. This module:

  1. Calls `crawlers.price_data.fetch_tencent_quote` for each missing stock.
  2. Persists the close to `price_cache` (so future scheduler runs find it).
  3. Recomputes dynamic PE/PB/PS in the matching snapshot.

Best-effort — failures (no network, unknown ticker) are skipped silently.
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy.orm import Session

from crawlers.price_data import fetch_tencent_quote, _to_tencent_ticker
from models import (
    AShareFinancialSnapshot,
    HKShareFinancialSnapshot,
    PriceCache,
)

logger = logging.getLogger(__name__)


def fetch_current_price(stock_code: str) -> float | None:
    """Call Tencent for current price. Returns prev close, or None."""
    ticker = _to_tencent_ticker(stock_code)
    if not ticker:
        return None
    try:
        info = fetch_tencent_quote(ticker)
        if not info:
            return None
        price = info.get("price")
        return float(price) if price else None
    except Exception as e:
        logger.warning("Tencent price fetch failed for %s: %s", stock_code, e)
        return None


def fill_prices_for_as_of(db: Session, as_of_date: _date, max_codes: int = 200) -> dict:
    """For each snapshot row whose current_price is null, try Tencent.

    Returns counts: { attempted, fetched, persisted, recomputed }.
    """
    from scripts.import_common import compute_dynamic

    attempted = fetched = persisted = recomputed = 0

    # A-share first
    rows = db.query(AShareFinancialSnapshot).filter(
        AShareFinancialSnapshot.as_of_date == as_of_date,
        AShareFinancialSnapshot.current_price.is_(None),
    ).limit(max_codes).all()
    for r in rows:
        attempted += 1
        price = fetch_current_price(r.stock_code)
        if price is None:
            continue
        fetched += 1
        # Persist to price_cache (today's trade_date)
        today = _date.today()
        existing = db.query(PriceCache).filter(
            PriceCache.stock_code == r.stock_code,
            PriceCache.trade_date == today,
        ).first()
        if not existing:
            db.add(PriceCache(
                stock_code=r.stock_code,
                trade_date=today,
                close_px=price,
                open_px=price,
                high_px=price,
                low_px=price,
                source="tencent_fill",
            ))
            persisted += 1
        r.current_price = price
        r.current_price_date = today
        if r.baseline_price and r.baseline_price > 0:
            r.pe_ttm_dynamic = compute_dynamic(r.pe_ttm, r.baseline_price, price)
            r.pb_mrq_dynamic = compute_dynamic(r.pb_mrq, r.baseline_price, price)
            r.ps_ttm_dynamic = compute_dynamic(r.ps_ttm, r.baseline_price, price)
            recomputed += 1
    db.commit()

    # HK next
    rows = db.query(HKShareFinancialSnapshot).filter(
        HKShareFinancialSnapshot.as_of_date == as_of_date,
        HKShareFinancialSnapshot.current_price.is_(None),
    ).limit(max_codes).all()
    for r in rows:
        attempted += 1
        price = fetch_current_price(r.stock_code)
        if price is None:
            continue
        fetched += 1
        today = _date.today()
        existing = db.query(PriceCache).filter(
            PriceCache.stock_code == r.stock_code,
            PriceCache.trade_date == today,
        ).first()
        if not existing:
            db.add(PriceCache(
                stock_code=r.stock_code,
                trade_date=today,
                close_px=price,
                open_px=price,
                high_px=price,
                low_px=price,
                source="tencent_fill",
            ))
            persisted += 1
        r.current_price = price
        r.current_price_date = today
        if r.baseline_price and r.baseline_price > 0:
            r.pe_ttm_dynamic = compute_dynamic(r.pe_ttm, r.baseline_price, price)
            r.pb_mrq_dynamic = compute_dynamic(r.pb_mrq, r.baseline_price, price)
            r.ps_ttm_dynamic = compute_dynamic(r.ps_ttm, r.baseline_price, price)
            recomputed += 1
    db.commit()

    return {
        "as_of_date": as_of_date.isoformat(),
        "attempted": attempted,
        "fetched": fetched,
        "persisted_to_price_cache": persisted,
        "dynamic_recomputed": recomputed,
    }


def overwrite_snapshot_prices(db: Session, as_of_date: _date, max_codes: int = 5000,
                              sleep_between: float = 0.02) -> dict:
    """覆盖所有 snapshot 行的 current_price / current_price_date。

    与 fill_prices_for_as_of 的区别：本函数不区分 NULL/有值，对每行都尝试腾讯，
    成功后**覆盖**写入。所以同一只股票每次都会刷新成最新价。

    用于 scheduler.refresh_snapshot_prices：分析师页面读取 current_price，
    如果只跑 fill_prices_for_as_of，那些「已经被旧 fill 写过」的股票永远不会被更新。

    skip_codes 跳过 .BJ (北交所) 和异常的 nan stock_code — Tencent 不支持这些。
    """
    from scripts.import_common import compute_dynamic

    attempted = fetched = updated = recomputed = 0
    today = _date.today()
    import time as _t

    # 跳过 Tencent 无法识别的代码（.BJ 北交所 / 空代码）
    skip_suffix = (".BJ",)

    def _is_skippable(code: str | None) -> bool:
        if not code or code == "nan":
            return True
        return any(code.endswith(s) for s in skip_suffix)

    for model, label in [(AShareFinancialSnapshot, "A"), (HKShareFinancialSnapshot, "HK")]:
        rows = db.query(model).filter(model.as_of_date == as_of_date).limit(max_codes).all()
        for r in rows:
            if _is_skippable(r.stock_code):
                continue
            attempted += 1
            price = fetch_current_price(r.stock_code)
            if price is None:
                continue
            fetched += 1
            # 写入 price_cache（如果今天还没写）
            existing = db.query(PriceCache).filter(
                PriceCache.stock_code == r.stock_code,
                PriceCache.trade_date == today,
            ).first()
            if not existing:
                db.add(PriceCache(
                    stock_code=r.stock_code,
                    trade_date=today,
                    close_px=price,
                    open_px=price,
                    high_px=price,
                    low_px=price,
                    source="t_overwrite",
                ))
            r.current_price = price
            r.current_price_date = today
            updated += 1
            if r.baseline_price and r.baseline_price > 0:
                r.pe_ttm_dynamic = compute_dynamic(r.pe_ttm, r.baseline_price, price)
                r.pb_mrq_dynamic = compute_dynamic(r.pb_mrq, r.baseline_price, price)
                r.ps_ttm_dynamic = compute_dynamic(r.ps_ttm, r.baseline_price, price)
                recomputed += 1
            if sleep_between:
                _t.sleep(sleep_between)
        db.commit()

    return {
        "as_of_date": as_of_date.isoformat(),
        "attempted": attempted,
        "fetched": fetched,
        "updated": updated,
        "dynamic_recomputed": recomputed,
    }


# ============================================================================
# Smart Gap-Fill (scheduler.job_fill_snapshot_gaps_smart 调用)
# ============================================================================

# 多 API 兜底链：每只股票按 (primary, fallback_1, fallback_2) 顺序尝试
# 各 API 适配：
#   - tencent_kline: A 股 / 港股 / 部分美股，前复权日线
#   - yfinance: 美股 / 港股 / ETF 历史
#   - akshare: A 股 / 港股通历史（限流严，最后兜底）

def _fetch_history_with_fallback(code: str, days: int) -> tuple[list[dict], str]:
    """拉历史 K 线，依次尝试腾讯 → yfinance。返回 (rows, api_name)。
    rows: [{date, open, close, high, low, volume}, ...]（按 date 升序）
    """
    from crawlers.price_data import (
        fetch_tencent_kline,
        _to_tencent_ticker,
    )
    # 1. 腾讯 K 线
    try:
        ticker = _to_tencent_ticker(code)
        if ticker:
            rows = fetch_tencent_kline(ticker, days=days)
            if rows:
                return rows, "tencent"
    except Exception as e:
        logger.debug("tencent kline failed for %s: %s", code, e)

    # 2. yfinance（美股 / 港股 ADR / ETF 兜底）
    try:
        from crawlers.price_data import _fetch_yfinance_kline
        # yfinance 需要原始 ticker（去掉 us/ 之类前缀），但 price_data 内已处理
        rows = _fetch_yfinance_kline(code, days=days)
        if rows:
            return rows, "yfinance"
    except Exception as e:
        logger.debug("yfinance kline failed for %s: %s", code, e)

    return [], "failed"


def fill_snapshot_gaps_smart(db: Session, days: int = 15,
                              max_codes: int = 10000,
                              sleep_between: float = 0.0,
                              batch_commit: int = 100,
                              scope: str = "drilled") -> dict:
    """Smart Gap-Fill：扫描 snapshot 表里 **当前下钻持仓的** 股票过去 N 个交易日的
    PriceCache 缺口，用多 API 兜底链补全，**不覆盖**已有数据。

    设计原则：
      - 默认 scope='drilled'：仅 FullHoldingSnapshot 中 source_type ∈
        ('drilled_fund', 'direct_stock') 的股票 → 836 只 (776 A + 60 HK)，
        远小于全 8300 只；避免 Tencent 对 HK 小票超时时拖累整个流程
      - scope='all'：全 snapshot 表的股票（慎用）
      - 不动 current_price 已经非空且日期 >= PriceCache 最新日期 的快照
      - 每个 gap 只查一次（PriceCache UNIQUE 约束保证幂等）
      - 多 API 兜底：腾讯 K 线 → yfinance → 失败标记
      - 跳过 .BJ（北交所）和 nan / 空 code

    完成后回写 snapshot.current_price / current_price_date（仅更新更晚的）。

    Returns: {
        scope, stocks_total, stocks_checked, skipped_bj,
        gaps_found, gaps_filled, snapshots_updated,
        api_breakdown: {tencent, yfinance, failed}, elapsed_seconds
    }
    """
    from scripts.import_common import compute_dynamic
    from services.trading_calendar import (
        expected_trading_dates, _market_for_code,
    )
    from models import FullHoldingSnapshot
    from services.data_version import current_business_date
    import time as _t

    t0 = _t.time()

    # 1. 决定扫描范围
    if scope == "drilled":
        biz = current_business_date()
        if not biz:
            logger.warning("无业务日期，scope=drilled 无可用快照")
            return {"skipped": "no_biz_date"}
        drilled = db.query(FullHoldingSnapshot.stock_code).filter(
            FullHoldingSnapshot.as_of_date == biz,
            FullHoldingSnapshot.source_type.in_(("drilled_fund", "direct_stock")),
            FullHoldingSnapshot.stock_code.isnot(None),
        ).distinct().all()
        codes = {r[0] for r in drilled if r[0] and r[0] != "nan"}
        scope_note = f"drilled@{biz}"
    else:
        # 全 snapshot 表
        a_codes = {
            r[0] for r in db.query(AShareFinancialSnapshot.stock_code)
            .filter(AShareFinancialSnapshot.stock_code.isnot(None))
            .distinct().all()
            if r[0] and r[0] != "nan"
        }
        hk_codes = {
            r[0] for r in db.query(HKShareFinancialSnapshot.stock_code)
            .filter(HKShareFinancialSnapshot.stock_code.isnot(None))
            .distinct().all()
            if r[0] and r[0] != "nan"
        }
        codes = a_codes | hk_codes
        scope_note = "all"

    # 跳过北交所（Tencent/yfinance/akshare 都不支持历史）
    skip_bj = sum(1 for c in codes if c.endswith(".BJ"))

    results = {
        "scope": scope_note,
        "stocks_total": len(codes),
        "stocks_checked": 0,
        "skipped_bj": skip_bj,
        "gaps_found": 0,
        "gaps_filled": 0,
        "snapshots_updated": 0,
        "api_breakdown": {"tencent": 0, "yfinance": 0, "failed": 0},
    }

    # 2. 按代码遍历
    for code in codes:
        if not code or code == "nan" or code.endswith(".BJ"):
            continue
        results["stocks_checked"] += 1
        try:
            # 2a. 计算该股票预期交易日
            market = _market_for_code(code)
            try:
                expected = set(expected_trading_dates(market, days, db))
            except Exception:
                # Calendar 失败时降级：Mon-Fri 简单规则
                from datetime import timedelta
                expected = set()
                today = _date.today()
                for k in range(days + 2):
                    d = today - timedelta(days=k)
                    if d.weekday() < 5:
                        expected.add(d)
            if not expected:
                continue

            # 2b. 已有 PriceCache 日期
            earliest = min(expected)
            existing = set(
                r[0] for r in db.query(PriceCache.trade_date)
                .filter(
                    PriceCache.stock_code == code,
                    PriceCache.trade_date >= earliest,
                ).all()
            )

            # 2c. 缺口 = expected − existing
            gaps = expected - existing
            results["gaps_found"] += len(gaps)

            if gaps:
                # 2d. 拉历史 K 线（含已有日期，整体覆盖一遍，按 date 选 gap）
                history, api_name = _fetch_history_with_fallback(code, days=days + 5)
                if history:
                    history_by_date = {}
                    for entry in history:
                        try:
                            d = _date.fromisoformat(entry["date"])
                            history_by_date[d] = entry
                        except (ValueError, TypeError):
                            continue
                    for gap_date in gaps:
                        entry = history_by_date.get(gap_date)
                        if not entry or not entry.get("close"):
                            continue
                        # 幂等：再检查一次（避免与并发 realtime_prices 冲突）
                        exists_now = db.query(PriceCache).filter(
                            PriceCache.stock_code == code,
                            PriceCache.trade_date == gap_date,
                        ).first()
                        if exists_now:
                            continue
                        db.add(PriceCache(
                            stock_code=code,
                            trade_date=gap_date,
                            close_px=entry["close"],
                            open_px=entry.get("open"),
                            high_px=entry.get("high"),
                            low_px=entry.get("low"),
                            volume=entry.get("volume"),
                            source=f"sfill_{api_name}",
                        ))
                        results["gaps_filled"] += 1
                        results["api_breakdown"][api_name] = \
                            results["api_breakdown"].get(api_name, 0) + 1
                    # batch commit 防止内存膨胀
                    if results["gaps_filled"] % batch_commit == 0:
                        db.commit()

            # 2e. 回写 snapshot.current_price（仅更晚的日期）
            latest_pc = db.query(PriceCache).filter(
                PriceCache.stock_code == code,
                PriceCache.trade_date >= earliest,
            ).order_by(PriceCache.trade_date.desc()).first()
            if latest_pc and latest_pc.close_px:
                _update_snapshot_current_price(db, code, latest_pc, compute_dynamic)
                results["snapshots_updated"] += 1

            if sleep_between:
                _t.sleep(sleep_between)
        except Exception as e:
            logger.warning("fill_snapshot_gaps_smart 处理 %s 失败: %s", code, e)
            continue

    db.commit()
    results["elapsed_seconds"] = round(_t.time() - t0, 1)
    return results


def _update_snapshot_current_price(db: Session, code: str, latest_pc, compute_dynamic):
    """把最新 PriceCache 行回写到 snapshot 表的 current_price / current_price_date。
    仅当 PriceCache.trade_date > snapshot.current_price_date 时更新 — 不覆盖更早的数据。
    """
    for model in (AShareFinancialSnapshot, HKShareFinancialSnapshot):
        snap = db.query(model).filter(model.stock_code == code).first()
        if not snap:
            continue
        # 不覆盖：仅当 PriceCache 日期更新
        if snap.current_price_date and latest_pc.trade_date <= snap.current_price_date:
            continue
        snap.current_price = latest_pc.close_px
        snap.current_price_date = latest_pc.trade_date
        if snap.baseline_price and snap.baseline_price > 0:
            try:
                snap.pe_ttm_dynamic = compute_dynamic(
                    snap.pe_ttm, snap.baseline_price, latest_pc.close_px
                )
                snap.pb_mrq_dynamic = compute_dynamic(
                    snap.pb_mrq, snap.baseline_price, latest_pc.close_px
                )
                snap.ps_ttm_dynamic = compute_dynamic(
                    snap.ps_ttm, snap.baseline_price, latest_pc.close_px
                )
            except Exception:
                pass
        # 单只股票只更新一次（HK/A 不重复处理）
        break