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