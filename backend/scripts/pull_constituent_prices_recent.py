"""pull_constituent_prices_recent.py — 一次性补拉所有 IndexConstituentSnapshot constituent 最近 5 天的价格。

User 2026-06-30 反馈：
  - 6/29 的 300308.SZ 显示 1323.40（= 6/25 的真实收盘价）
  - 因为 price_cache 在 6/24 后无任何数据，drill_snapshot 用 strict_mode=False 回退到 6/25
  - 现在每天 scheduler 只拉 holdings（33 unique codes），从不主动拉 constituent 股票

Root cause: job_backfill_gaps / job_fetch_realtime_prices 都仅遍历 holdings。
本脚本：通过 IndexConstituentSnapshot 找到所有 is_drillable 指数的最新成分，
        调 fetch_tencent_kline 拿最近 days=10 日 K 线，
        把 6/25-6/29 的 close 写入 price_cache（is_stale_price=False）。
"""
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from models import (
    IndexConstituentSnapshot,
    PriceCache,
    SecurityMaster,
    FundIndexMap,
)
from crawlers.price_data import fetch_tencent_kline
from services.code_map import transform_code

# 只补 6/25-6/30（用户指定范围）
TARGET_DATES = [date(2026, 6, 25), date(2026, 6, 26), date(2026, 6, 27),
                date(2026, 6, 28), date(2026, 6, 29), date(2026, 6, 30)]
DAYS_BACK = 12  # 拉近 12 天 K 线（确保能覆盖 6/25-6/29）
SLEEP_BETWEEN = 0.15  # 限流


def main():
    db = SessionLocal()
    try:
        # 1. 找所有 is_drillable 基金关联的指数代码
        drillable_idx_codes = set()
        for sm in db.query(SecurityMaster).filter(SecurityMaster.is_drillable.is_(True)).all():
            if sm.index_code:
                # 兼容带/不带后缀
                drillable_idx_codes.add(sm.index_code.split(".")[0])

        # 加上 FundIndexMap 里的（万一 security_master 没记）
        for fm in db.query(FundIndexMap).all():
            if fm.index_code:
                drillable_idx_codes.add(fm.index_code.split(".")[0])

        # 加后缀形式（000300.SH 等）
        all_idx_variants = set()
        for ic in drillable_idx_codes:
            all_idx_variants.add(ic)
            all_idx_variants.add(f"{ic}.SH")
            all_idx_variants.add(f"{ic}.SZ")
            all_idx_variants.add(f"{ic}.CSI")

        print(f"=== Pull constituent prices (constituent of {len(drillable_idx_codes)} drillable indices) ===")

        # 2. 取最近一个月的成分股快照的所有 stock_code（去重）
        rows = (
            db.query(IndexConstituentSnapshot.stock_code)
            .filter(IndexConstituentSnapshot.index_code.in_(list(all_idx_variants)))
            .distinct()
            .all()
        )
        codes = sorted({r[0] for r in rows if r[0]})
        print(f"Unique constituents to pull: {len(codes)}")

        # 3. 对每只 stock 调腾讯 K 线
        written_total = 0
        skipped_no_data = 0
        skipped_network = 0

        for i, code in enumerate(codes, 1):
            print(f"[{i}/{len(codes)}] {code}...", end=" ", flush=True)
            try:
                # 优先用 db 映射，否则原样调
                transformed = transform_code(code, "tencent_kline", db)
                ticker = transformed or code
                rows = fetch_tencent_kline(ticker, days=DAYS_BACK)
            except Exception as e:
                print(f"NETWORK_ERR: {str(e)[:80]}")
                skipped_network += 1
                continue
            if not rows:
                print("NO_DATA")
                skipped_no_data += 1
                continue

            # 4. 写入 6/25-6/30（只补这区间）
            written = 0
            history_by_date = {}
            for p in rows:
                try:
                    d = date.fromisoformat(p["date"])
                    history_by_date[d] = p
                except (ValueError, TypeError):
                    continue

            for d in TARGET_DATES:
                p = history_by_date.get(d)
                if not p:
                    continue
                exists = (
                    db.query(PriceCache)
                    .filter(PriceCache.stock_code == code, PriceCache.trade_date == d)
                    .first()
                )
                if exists:
                    continue
                db.add(PriceCache(
                    stock_code=code,
                    trade_date=d,
                    open_px=p.get("open"),
                    close_px=p.get("close"),
                    high_px=p.get("high"),
                    low_px=p.get("low"),
                    volume=p.get("volume"),
                    source="tencent_recent_fill",
                ))
                written += 1
            if written:
                db.commit()
            written_total += written
            print(f"wrote={written}")
            time.sleep(SLEEP_BETWEEN)

        print()
        print(f"Done. written={written_total}, no_data={skipped_no_data}, network_err={skipped_network}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
