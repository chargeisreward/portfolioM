"""pull_fund_nav.py — fetch fund unit-NAV history from akshare.

For each drillable OF fund (i.e., 场外基金 with .OF suffix):
  - Fetch NAV time series via akshare.fund_open_fund_info_em
  - Save to fund_daily_nav (nav, accumulated_nav, daily_return per trade_date)

This enables precise drill-down:
  shares = fund_shares_outstanding × fund_nav_5_29 × weight / stock_close_5_29
  dev   = Σ(shares × current_price) / prev_fund_value - 1
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import SessionLocal
from models import (
    AShareFinancialSnapshot,
    FundDailyNav,
    FundIndexMap,
    Holding,
    HKShareFinancialSnapshot,
)

logger = logging.getLogger(__name__)


def _akshare_fund_nav(fund_code: str, days: int):
    """Try multiple akshare APIs to get NAV history for one OF fund."""
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed")
        return []

    # Strip .OF suffix (akshare wants just the 6-digit code)
    code = fund_code.replace(".OF", "").strip()

    rows = []
    try:
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势", period="6月")
        if df is None or df.empty:
            return []
        df.columns = [str(c).strip() for c in df.columns]
        date_col = next((c for c in df.columns if "日期" in c), df.columns[0])
        nav_col = next((c for c in df.columns if "单位净值" in c), None)
        if not nav_col:
            logger.warning("akshare NAV: no nav col for %s; cols=%s", fund_code, df.columns.tolist())
            return []
        acc_col = next((c for c in df.columns if "累计" in c), None)
        ret_col = next((c for c in df.columns if "增长率" in c or "日增长" in c), None)
        for _, r in df.iterrows():
            td_obj = pd_date(r.get(date_col))
            if td_obj is None:
                continue
            nav = safe_float(r.get(nav_col))
            if nav is None:
                continue
            rows.append({
                "trade_date": td_obj,
                "nav": nav,
                "accumulated_nav": safe_float(r.get(acc_col)) if acc_col else None,
                "daily_return": safe_float(r.get(ret_col)) if ret_col else None,
            })
    except Exception as e:
        logger.warning("akshare NAV failed for %s: %s", fund_code, e)
    return rows


def pd_date(v):
    if v is None:
        return None
    if hasattr(v, "year"):
        return _date(v.year, v.month, v.day)
    if isinstance(v, str):
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except Exception:
            try:
                return datetime.strptime(v[:10], "%Y%m%d").date()
            except Exception:
                return None
    return None


def safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def list_drillable_fund_codes(db) -> list[str]:
    """All holding fund codes that are drillable (have constituents)."""
    holdings = db.query(Holding).all()
    fund_codes = {h.security_code for h in holdings}
    out = []
    for fc in fund_codes:
        fm = db.query(FundIndexMap).filter_by(fund_code=fc).first()
        if fm:
            out.append(fc)
    return sorted(out)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--max-codes", type=int, default=30, help="最多基金数（防网络超时）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        codes = list_drillable_fund_codes(db)
        logger.info("drillable funds: %d (processing up to %d)", len(codes), args.max_codes)
        inserted = 0
        for idx, fc in enumerate(codes[:args.max_codes], 1):
            if idx % 5 == 0:
                logger.info(f"  [{idx}/{min(len(codes), args.max_codes)}] {fc} inserted={inserted}")
            rows = _akshare_fund_nav(fc, args.days)
            if not rows:
                continue
            existing = {
                r.trade_date: r
                for r in db.query(FundDailyNav).filter_by(fund_code=fc).all()
            }
            for r in rows:
                td = r["trade_date"]
                if td in existing:
                    e = existing[td]
                    if e.nav != r["nav"]:
                        e.nav = r["nav"]
                        e.accumulated_nav = r["accumulated_nav"]
                        e.daily_return = r["daily_return"]
                        inserted += 1
                else:
                    db.add(FundDailyNav(
                        fund_code=fc,
                        trade_date=td,
                        nav=r["nav"],
                        accumulated_nav=r["accumulated_nav"],
                        daily_return=r["daily_return"],
                        source="akshare",
                    ))
                    inserted += 1
            db.commit()
        logger.info(f"DONE: inserted={inserted}")
    finally:
        db.close()


if __name__ == "__main__":
    main()