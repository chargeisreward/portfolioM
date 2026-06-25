"""backfill_360days.py — 一次性补足所有持仓证券过去 360 天的收盘价到 price_cache。

用途：总览页面走势图（90/180/360 天 资产值/收益率）需要完整 360 天历史价。
数据源：
  - .OF 基金 → 东财 lsjz 直连 API（绕过 akshare，避免 Python 3.14 py_mini_racer 循环 import）
  - 非 .OF（A股/港股/美股 ETF/个股）→ 腾讯 K 线 API（一次返回 ~1 年日线）

特性：
  - 幂等可重跑：exists 检查 (stock_code, trade_date)，已有则跳过
  - 多批次：逐 code 调 API，每 code 一次调用（东财分页拉满）
  - 交易日过滤：.OF 按 weekday<5；非 .OF 按各自市场交易日历
  - 失败隔离：单个 code 失败不阻塞其他 code
"""
import sys
import os
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from database import SessionLocal
from models import Holding, PriceCache
from crawlers.price_data import fetch_tencent_kline
from services.trading_calendar import is_trading_day, _market_for_code

DAYS = 360
AKSHARE_SLEEP = 0.5  # 东财调用间隔，避免限流

# 东财 lsjz API（历史净值直连，绕过 akshare）
EM_NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _fetch_em_page(client, fund_code, page, page_size, start, end):
    """东财 lsjz 单页拉取。返回 (rows, total_count)。"""
    params = {
        "fundCode": fund_code,
        "pageIndex": str(page),
        "pageSize": str(page_size),
        "startDate": start,
        "endDate": end,
    }
    headers = {**EM_HEADERS, "Referer": f"https://fundf10.eastmoney.com/jjjz_{fund_code}.html"}
    r = client.get(EM_NAV_URL, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("ErrCode") not in (0, "0"):
        raise RuntimeError(f"em api err: {d.get('ErrMsg')} for {fund_code}")
    rows = (d.get("Data") or {}).get("LSJZList") or []
    return rows, int(d.get("TotalCount") or 0)


def _fetch_em_all(fund_code, start, end, page_size=200):
    """东财 lsjz 分页拉满 [start, end] 区间。返回 [{date, close}, ...]。"""
    client = httpx.Client()
    try:
        page = 1
        all_rows = []
        total = 0
        while True:
            rows, total = _fetch_em_page(client, fund_code, page, page_size, start, end)
            all_rows.extend(rows)
            if not rows or len(all_rows) >= total:
                break
            page += 1
            time.sleep(0.3)
        # 解析：DWJZ=单位净值, LJJZ=累计净值；QDII/债券基金可能只有 LJJZ
        out = []
        for r in all_rows:
            td = r.get("FSRQ") or ""
            if not td or len(td) < 10:
                continue
            try:
                d = datetime.strptime(td[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            nav_s = r.get("DWJZ") or ""
            acc_s = r.get("LJJZ") or ""
            # 优先用单位净值，空则用累计净值（QDII/债券基金）
            close = None
            if nav_s:
                try:
                    close = float(nav_s)
                except (TypeError, ValueError):
                    pass
            if close is None and acc_s:
                try:
                    close = float(acc_s)
                except (TypeError, ValueError):
                    pass
            if close is None:
                continue  # 两个净值都空，跳过
            out.append({"date": d.isoformat(), "close": close})
        return out
    finally:
        client.close()


def main():
    db = SessionLocal()
    try:
        # 1. 获取所有用户持仓的唯一证券代码（跨所有 user 的并集）
        codes = [r[0] for r in db.query(Holding.security_code).distinct().all()]
        print("=== 360 天历史价回填 ===")
        print(f"唯一持仓代码数: {len(codes)}")
        cutoff = date.today() - timedelta(days=DAYS)
        print(f"窗口: {cutoff} ~ {date.today()}")
        print()

        results = []

        for i, code in enumerate(codes, 1):
            is_fund = code.endswith(".OF")
            market = _market_for_code(code)
            print(f"[{i}/{len(codes)}] {code} (fund={is_fund}, market={market})...", end=" ", flush=True)

            try:
                if is_fund:
                    # 东财直连，绕过 akshare
                    fund_code = code.replace(".OF", "").strip()
                    history = _fetch_em_all(fund_code, cutoff.isoformat(), date.today().isoformat())
                    time.sleep(AKSHARE_SLEEP)
                else:
                    # 直接调 fetch_tencent_kline，绕过 fetch_price_history 的 dedup gate
                    history = fetch_tencent_kline(code, days=DAYS + 5)
            except Exception as e:
                print(f"FETCH_ERROR: {str(e)[:150]}")
                results.append({"code": code, "status": "fetch_error", "error": str(e)[:200]})
                continue

            if not history:
                print("NO_DATA")
                results.append({"code": code, "status": "no_data"})
                continue

            written = 0
            skipped_exists = 0
            skipped_nontrading = 0
            skipped_before_cutoff = 0

            for p in history:
                try:
                    d = date.fromisoformat(p["date"])
                except (ValueError, TypeError):
                    continue
                if d < cutoff:
                    skipped_before_cutoff += 1
                    continue
                # 交易日过滤
                try:
                    if not is_trading_day(market, d, db):
                        skipped_nontrading += 1
                        continue
                except Exception:
                    pass  # 日历失败不阻塞
                # exists 检查（幂等）
                exists = db.query(PriceCache).filter(
                    PriceCache.stock_code == code,
                    PriceCache.trade_date == d,
                ).first()
                if exists:
                    skipped_exists += 1
                    continue
                db.add(PriceCache(
                    stock_code=code,
                    trade_date=d,
                    open_px=p.get("open"),
                    close_px=p.get("close"),
                    high_px=p.get("high"),
                    low_px=p.get("low"),
                    volume=p.get("volume"),
                    source="em_fund" if is_fund else "tencent",
                ))
                written += 1

            try:
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"COMMIT_ERROR: {str(e)[:150]}")
                results.append({"code": code, "status": "commit_error", "error": str(e)[:200]})
                continue

            print(f"fetched={len(history)} written={written} exists={skipped_exists} nontrading={skipped_nontrading} before_cutoff={skipped_before_cutoff}")
            results.append({
                "code": code, "status": "ok", "fetched": len(history),
                "written": written, "exists": skipped_exists,
            })

        # === 汇总 ===
        print()
        print("=== 回填汇总 ===")
        ok = sum(1 for r in results if r["status"] == "ok")
        no_data = sum(1 for r in results if r["status"] == "no_data")
        errors = sum(1 for r in results if r["status"] in ("fetch_error", "commit_error"))
        total_written = sum(r.get("written", 0) for r in results)
        print(f"成功: {ok}, 无数据: {no_data}, 错误: {errors}, 总写入行数: {total_written}")

        if no_data > 0:
            print(f"无数据代码: {[r['code'] for r in results if r['status']=='no_data']}")
        if errors > 0:
            print(f"错误代码: {[(r['code'], r.get('error','')[:80]) for r in results if r['status'] in ('fetch_error','commit_error')]}")

        # === 覆盖率验证 ===
        print()
        print("=== 覆盖率验证 ===")
        print(f"{'代码':<14} {'行数':>6} {'最早':>12} {'最晚':>12} {'状态'}")
        print("-" * 70)
        all_ok = True
        for code in codes:
            rows = db.query(PriceCache.trade_date).filter(
                PriceCache.stock_code == code
            ).all()
            if not rows:
                print(f"{code:<14} {'0':>6} {'-':>12} {'-':>12} ❌ 0行")
                all_ok = False
                continue
            dates = sorted([r[0] for r in rows])
            min_d = dates[0]
            max_d = dates[-1]
            gap_days = (min_d - cutoff).days
            if gap_days <= 7:
                status = "✓"
            elif gap_days <= 30:
                status = f"⚠ 起点晚 {gap_days} 天"
                all_ok = False
            else:
                status = f"❌ 起点晚 {gap_days} 天"
                all_ok = False
            print(f"{code:<14} {len(rows):>6} {str(min_d):>12} {str(max_d):>12} {status}")

        print()
        if all_ok:
            print("✓ 所有代码覆盖率达标")
        else:
            print("⚠ 部分代码覆盖率不足（可能是新基金/新股上市不足 360 天）")

    finally:
        db.close()


if __name__ == "__main__":
    main()
