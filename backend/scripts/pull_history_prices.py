"""pull_history_prices.py — 拉取 6 个月历史价落库 (spec user follow-up).

For each stock_code in AShareFinancialSnapshot + HKShareFinancialSnapshot,
fetch ~180 days of K-line history from Tencent and save to price_cache.

This enables:
  - 3-month price change (pct_change_3m) in full_holding endpoint
  - Trend chart 90/180/360 day windows
  - Proper market-cap weighted ratios (CSI300)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import SessionLocal
from models import (
    AShareFinancialSnapshot,
    HKShareFinancialSnapshot,
    PriceCache,
)

logger = logging.getLogger(__name__)


def _dedup_codes(rows):
    return list({r.stock_code for r in rows})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180, help="历史天数（默认 180 = 6 个月）")
    ap.add_argument("--market", choices=("A", "H", "AH"), default="AH")
    ap.add_argument("--max-codes", type=int, default=2000, help="最多处理的股票数（防爆破）")
    args = ap.parse_args()

    from crawlers.price_data import fetch_tencent_kline, _to_kline_ticker

    db = SessionLocal()
    try:
        as_of = _date(2026, 5, 29)  # 基础数据基准期5月29日
        if args.market in ("A", "AH"):
            a_rows = db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of).all()
        else:
            a_rows = []
        if args.market in ("H", "AH"):
            h_rows = db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of).all()
        else:
            h_rows = []

        a_codes = _dedup_codes(a_rows)
        h_codes = _dedup_codes(h_rows)
        all_codes = a_codes + h_codes
        logger.info("snapshots: A=%d unique, H=%d unique, total=%d", len(a_codes), len(h_codes), len(all_codes))

        attempted = fetched = inserted = 0
        skipped = 0
        failed = 0
        total = min(len(all_codes), args.max_codes)
        for idx, stock_code in enumerate(all_codes[:args.max_codes], 1):
            if idx % 50 == 0 or idx == total:
                logger.info(f"  [{idx}/{total}] attempted={attempted} inserted={inserted} failed={failed}")
            attempted += 1
            ticker = _to_kline_ticker(stock_code)
            if not ticker:
                skipped += 1
                continue
            try:
                kline = fetch_tencent_kline(ticker, days=args.days)
            except Exception as e:
                logger.warning(f"  failed {stock_code}: {e}")
                failed += 1
                continue
            if not kline:
                failed += 1
                continue
            fetched += 1
            # Bulk upsert into price_cache
            existing = {
                r.trade_date: r
                for r in db.query(PriceCache).filter(PriceCache.stock_code == stock_code).all()
            }
            for row in kline:
                td = row.get("date")
                if not td:
                    continue
                if isinstance(td, str):
                    try:
                        td_obj = _date.fromisoformat(td[:10])
                    except ValueError:
                        continue
                else:
                    td_obj = td
                px = row.get("close") or row.get("close_px")
                if px is None:
                    continue
                if td_obj in existing:
                    e = existing[td_obj]
                    if e.close_px != px:
                        e.close_px = px
                        e.open_px = row.get("open", e.open_px)
                        e.high_px = row.get("high", e.high_px)
                        e.low_px = row.get("low", e.low_px)
                        e.source = "tencent_kline"
                        inserted += 1
                else:
                    db.add(PriceCache(
                        stock_code=stock_code,
                        trade_date=td_obj,
                        close_px=px,
                        open_px=row.get("open"),
                        high_px=row.get("high"),
                        low_px=row.get("low"),
                        volume=row.get("volume"),
                        source="tencent_kline",
                    ))
                    inserted += 1
            db.commit()
        logger.info(f"DONE: attempted={attempted} fetched={fetched} inserted={inserted} failed={failed} skipped={skipped}")
    finally:
        db.close()


if __name__ == "__main__":
    main()