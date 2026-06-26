"""pull_fund_nav_em.py — pull fund NAV via 东财 fundapi 直连（绕过 akshare）。

akshare 在 Python 3.14 + py_mini_racer 环境下启动失败（循环 import），
而 pull_fund_nav.py 又有 update 逻辑 bug（accumulated_nav 只在 nav 变化时
才更新，导致历史已写入 None 值的记录无法被回填）。本脚本：

  1. 用东财 api.fund.eastmoney.com/f10/lsjz 直连拿 LJJZ/DWJZ/JZZZL
  2. 分页拉满 startDate..endDate（默认过去 6 个月）
  3. 写入策略：nav 变化 OR accumulated_nav 从空变成有值 OR 任何字段不同 → 更新
  4. 数据库幂等

字段映射：
  FSRQ  → trade_date
  DWJZ  → nav (单位净值)
  LJJZ  → accumulated_nav (累计净值)
  JZZZL → daily_return (日增长率%)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as _date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import SessionLocal
from models import FundDailyNav, FundIndexMap, Holding
from services.fund_nav_fetcher import fetch_nav_all, parse_nav_row

logger = logging.getLogger(__name__)


def list_drillable_fund_codes(db) -> list[str]:
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
    ap.add_argument("--end-date", type=str, default="2026-06-18")
    ap.add_argument("--max-codes", type=int, default=200)
    args = ap.parse_args()

    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    start = end - timedelta(days=args.days)
    start_s = start.isoformat()
    end_s = end.isoformat()

    db = SessionLocal()
    try:
        codes = list_drillable_fund_codes(db)
        logger.info("drillable funds: %d (processing up to %d), window=%s..%s",
                    len(codes), args.max_codes, start_s, end_s)

        inserted = 0
        updated = 0
        for idx, fc in enumerate(codes[:args.max_codes], 1):
            try:
                rows_raw = fetch_nav_all(fc.replace(".OF", "").strip(), start_s, end_s)
            except Exception as e:
                logger.warning("fetch failed for %s: %s", fc, e)
                continue
            rows = [r for r in (parse_nav_row(x) for x in rows_raw) if r]
            if not rows:
                logger.warning("[%d/%d] %s: no rows", idx, min(len(codes), args.max_codes), fc)
                continue

            existing = {
                r.trade_date: r
                for r in db.query(FundDailyNav).filter_by(fund_code=fc).all()
            }
            nav5_29 = None
            for r in rows:
                td = r["trade_date"]
                if td in existing:
                    e = existing[td]
                    # 修复原脚本 bug: nav 变化 OR accumulated_nav 从空 → 有值 OR 任何字段不同
                    if (e.nav != r["nav"]
                        or (e.accumulated_nav is None and r["accumulated_nav"] is not None)
                        or (e.accumulated_nav != r["accumulated_nav"] and r["accumulated_nav"] is not None)
                        or (e.daily_return != r["daily_return"] and r["daily_return"] is not None)):
                        e.nav = r["nav"]
                        if r["accumulated_nav"] is not None:
                            e.accumulated_nav = r["accumulated_nav"]
                        if r["daily_return"] is not None:
                            e.daily_return = r["daily_return"]
                        updated += 1
                else:
                    db.add(FundDailyNav(
                        fund_code=fc,
                        trade_date=td,
                        nav=r["nav"],
                        accumulated_nav=r["accumulated_nav"],
                        daily_return=r["daily_return"],
                        source="eastmoney",
                    ))
                    inserted += 1
                if td == _date(2026, 5, 29):  # 基础数据基准期5月29日
                    nav5_29 = r["accumulated_nav"]
            db.commit()
            logger.info("[%d/%d] %s: rows=%d  cum_nav_5_29=%s",
                        idx, min(len(codes), args.max_codes), fc, len(rows), nav5_29)
        logger.info("DONE: inserted=%d updated=%d", inserted, updated)
    finally:
        db.close()


if __name__ == "__main__":
    main()
