"""公共下钻截面快照生成器（按 fund × as_of_date — 2026-06-24）

算法（按用户最终确认）:
  1. 读取 fund_index_map 的所有可下钻基金（fund → index_code 映射）
  2. 对每只 fund：读取 index_constituents[最近月份] 的成分股 + 权重
  3. 取每只成分股 T 日 current_price；缺失用 T-1 价（视为停牌）
  4. 校验：获得收盘价的成分股占比 >= 95% 才生成截面
  5. 算 shares_equivalent = fund_price × 0.95 × (weight/100) / current_price
     其中 fund_price 从 Holding.price 取（fund 当日基金价格，所有 user 的均价也行）
  6. 写入 fund_drill_snapshot（公共数据）

user 层：user_drill[s] = Holding.quantity × shares_equivalent[s]
        user_cash    = Holding.quantity × fund_price × 0.05

注意：fund_price 的来源 — Holding 表每个 user 都有该 fund 的 price，
  为保证一致性，取该 fund 在 Holding 表里的「所有 user 价格均值」作为公共 fund_price。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from models import (
    FundDrillSnapshot,
    FundIndexMap,
    Holding,
    IndexConstituentSnapshot,
    PriceCache,
)

logger = logging.getLogger(__name__)

STOCK_PRICE_FRESHNESS_RATIO = 0.95   # 至少 95% 成分股有当日价才生成截面
STALE_PRICE_FALLBACK_DAYS = 7        # 缺失可用 T-1..T-7 任意一天的价作为替补
CASH_RATIO = 0.05                    # 5% 现金
STOCK_RATIO = 1 - CASH_RATIO         # 95% 成分股


def _latest_index_constituents(db: Session, idx_code: str, on_or_before: date) -> list[IndexConstituentSnapshot]:
    """取 idx_code 在 on_or_before 或之前的最近一份成分股快照。"""
    return (
        db.query(IndexConstituentSnapshot)
        .filter(
            IndexConstituentSnapshot.index_code == idx_code,
            IndexConstituentSnapshot.as_of_date <= on_or_before,
        )
        .order_by(IndexConstituentSnapshot.as_of_date.desc())
        .all()
    )


def _get_stock_price(db: Session, stock_code: str, as_of_date: date) -> tuple[float | None, bool]:
    """返回 (price, is_stale)。先 T 日，找不到则向前回退 STALE_PRICE_FALLBACK_DAYS 天。"""
    norm = stock_code.split(".")[0]
    candidates = [as_of_date - timedelta(days=d) for d in range(STALE_PRICE_FALLBACK_DAYS + 1)]
    for i, d in enumerate(candidates):
        row = (
            db.query(PriceCache)
            .filter(PriceCache.stock_code == stock_code, PriceCache.trade_date == d)
            .order_by(PriceCache.trade_date.desc())
            .first()
        )
        if row and row.close_px:
            return float(row.close_px), (i > 0)
        # 后缀无关查询（如 688041 在 .SH 数据源中存为 688041 而非 688041.SH）
        if norm != stock_code:
            row = (
                db.query(PriceCache)
                .filter(PriceCache.stock_code == norm, PriceCache.trade_date == d)
                .order_by(PriceCache.trade_date.desc())
                .first()
            )
            if row and row.close_px:
                return float(row.close_px), (i > 0)
    return None, False


def _get_fund_price(db: Session, fund_code: str) -> float | None:
    """取该 fund 在 Holding 表里所有 user 价格的均值；若无任何 holding，返回 None。"""
    rows = db.query(Holding.price).filter(Holding.security_code == fund_code).all()
    prices = [r[0] for r in rows if r[0] is not None and r[0] > 0]
    if not prices:
        return None
    return sum(prices) / len(prices)


def generate_drill_snapshot_for_date(db: Session, as_of_date: date) -> dict:
    """为 as_of_date 生成所有可下钻基金的截面快照。

    Returns: {
      "as_of_date": str,
      "funds_processed": int,
      "funds_skipped_no_index_map": int,
      "funds_skipped_no_constituents": int,
      "funds_skipped_low_freshness": int,
      "rows_inserted": int,
      "details": [...],
    }
    """
    fund_maps = db.query(FundIndexMap).all()
    funds_processed = 0
    funds_skipped_no_index_map = 0  # 现在 fund_index_map 没有 as_of_date，跳过此
    funds_skipped_no_constituents = 0
    funds_skipped_low_freshness = 0
    rows_inserted = 0
    details = []

    # 先删掉该 as_of_date 已有记录（幂等）
    db.query(FundDrillSnapshot).filter(FundDrillSnapshot.as_of_date == as_of_date).delete()
    db.commit()

    for fund_map in fund_maps:
        fund_code = fund_map.fund_code
        idx_code = fund_map.index_code.split(".")[0]
        constituents = _latest_index_constituents(db, idx_code, as_of_date)
        if not constituents:
            funds_skipped_no_constituents += 1
            details.append({"fund": fund_code, "skip": "no_constituents", "index": idx_code})
            continue

        # 价格校验
        priced = []
        missing = []
        for s in constituents:
            price, stale = _get_stock_price(db, s.stock_code, as_of_date)
            if price is None:
                missing.append(s.stock_code)
            else:
                priced.append((s, price, stale))

        ratio = len(priced) / len(constituents) if constituents else 0
        if ratio < STOCK_PRICE_FRESHNESS_RATIO:
            funds_skipped_low_freshness += 1
            details.append({
                "fund": fund_code, "index": idx_code,
                "skip": "low_freshness",
                "ratio": round(ratio, 4),
                "missing": missing[:20],
            })
            logger.warning(
                "drill snapshot skip %s: fresh %d/%d (%.2f%%), missing: %s",
                fund_code, len(priced), len(constituents), ratio * 100, missing[:10],
            )
            continue

        # 取基金价格
        fund_price = _get_fund_price(db, fund_code)
        if fund_price is None or fund_price <= 0:
            details.append({"fund": fund_code, "skip": "no_fund_price", "index": idx_code})
            continue

        # 生成 shares_equivalent
        fund_rows = []
        for s, price, stale in priced:
            weight_pct = s.weight if (s.weight and s.weight > 0) else (100.0 / len(constituents))
            shares_eq = fund_price * STOCK_RATIO * (weight_pct / 100.0) / price
            fund_rows.append(FundDrillSnapshot(
                fund_code=fund_code,
                as_of_date=as_of_date,
                stock_code=s.stock_code,
                stock_name=s.stock_name,
                weight_pct=float(weight_pct),
                baseline_price=getattr(s, "baseline_price", None),
                current_price=float(price),
                shares_equivalent=float(shares_eq),
                is_stale_price=bool(stale),
            ))

        db.bulk_save_objects(fund_rows)
        funds_processed += 1
        rows_inserted += len(fund_rows)
        details.append({
            "fund": fund_code, "index": idx_code,
            "fund_price": round(fund_price, 4),
            "constituents": len(constituents),
            "priced": len(priced),
            "stale_count": sum(1 for _, _, st in priced if st),
            "rows": len(fund_rows),
        })
        logger.info(
            "drill snapshot %s %s: %d/%d priced, %d rows",
            fund_code, as_of_date, len(priced), len(constituents), len(fund_rows),
        )

    db.commit()
    return {
        "as_of_date": as_of_date.isoformat(),
        "funds_processed": funds_processed,
        "funds_skipped_no_index_map": funds_skipped_no_index_map,
        "funds_skipped_no_constituents": funds_skipped_no_constituents,
        "funds_skipped_low_freshness": funds_skipped_low_freshness,
        "rows_inserted": rows_inserted,
        "details": details,
    }


def get_drill_snapshot_for_fund(db: Session, fund_code: str, as_of_date: date) -> list[FundDrillSnapshot] | None:
    """取某 fund 在 as_of_date 的下钻截面；若当日缺失，回退到最近一天。"""
    rows = (
        db.query(FundDrillSnapshot)
        .filter(FundDrillSnapshot.fund_code == fund_code, FundDrillSnapshot.as_of_date == as_of_date)
        .all()
    )
    if rows:
        return rows
    # 回退：取该 fund 最近的截面日期
    latest_date = (
        db.query(FundDrillSnapshot.as_of_date)
        .filter(FundDrillSnapshot.fund_code == fund_code, FundDrillSnapshot.as_of_date <= as_of_date)
        .order_by(FundDrillSnapshot.as_of_date.desc())
        .first()
    )
    if not latest_date:
        return None
    return (
        db.query(FundDrillSnapshot)
        .filter(FundDrillSnapshot.fund_code == fund_code, FundDrillSnapshot.as_of_date == latest_date[0])
        .all()
    )