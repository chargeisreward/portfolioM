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
    ExchangeRate,
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
    """取 idx_code 在 on_or_before 或之前的最近一份「带权重的」成分股快照。

    优先取权重和 > 0 的 as_of_date（用户 2026-06-24 补丁）：
      - 如果 5/29 有 weight（非空），就用 5/29（指数公司真实权重）
      - 如果 5/29 weight 全空（如 399673 PG 旧数据），才用最近的 6/15 兜底
    """
    # 步骤 1: 找到 5/29 是否有 weight
    may29 = (
        db.query(IndexConstituentSnapshot)
        .filter(
            IndexConstituentSnapshot.index_code == idx_code,
            IndexConstituentSnapshot.as_of_date == date(2026, 5, 29),
        )
        .all()
    )
    if may29 and any((r.weight or 0) > 0 for r in may29):
        return may29  # 优先用 5/29 真实权重

    # 步骤 2: 回退到 on_or_before 之前的最新一份
    max_date_sub = (
        db.query(IndexConstituentSnapshot.as_of_date)
        .filter(
            IndexConstituentSnapshot.index_code == idx_code,
            IndexConstituentSnapshot.as_of_date <= on_or_before,
        )
        .order_by(IndexConstituentSnapshot.as_of_date.desc())
        .limit(1)
        .scalar_subquery()
    )
    return (
        db.query(IndexConstituentSnapshot)
        .filter(
            IndexConstituentSnapshot.index_code == idx_code,
            IndexConstituentSnapshot.as_of_date == max_date_sub,
        )
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


def _guess_currency(stock_code: str) -> str:
    """根据股票代码推原币。"""
    code = (stock_code or "").upper().strip()
    if code.endswith(".HK"):
        return "HKD"
    if code.endswith(".US"):
        return "USD"
    return "CNY"


def _get_fx_rate(db: Session, from_ccy: str, to_ccy: str, as_of_date: date) -> float | None:
    """取 T 日汇率 (from→to)；若 T 日缺失，向前回退 7 天。"""
    if from_ccy == to_ccy:
        return 1.0
    for d in [as_of_date - timedelta(days=i) for i in range(7)]:
        row = db.query(ExchangeRate).filter(
            ExchangeRate.rate_date == d,
            ExchangeRate.from_currency == from_ccy,
            ExchangeRate.to_currency == to_ccy,
        ).first()
        if row and row.rate:
            return float(row.rate)
    return None


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

        # === 2026-06-24 补丁 ===
        # 5/29 权重和 < 100% 时，差额 × 95% 划入下钻-现金
        # priced 是 list[(IndexConstituentSnapshot, price, stale)]
        weight_sum = sum((s.weight or 0) for s, _, _ in priced)
        weight_deficit = max(0.0, 100.0 - weight_sum)  # 0 if weight_sum >= 100
        weight_deficit_cash = weight_deficit * STOCK_RATIO  # *0.95
        # 也保留 fallback: 个别成分股 weight=NULL 时, 用 100/N 等权
        default_w = 100.0 / len(constituents) if constituents else 0.0

        # 生成 shares_equivalent
        fund_rows = []
        for s, price, stale in priced:
            # 个别 weight NULL 用 fallback 等权
            weight_pct = s.weight if (s.weight and s.weight > 0) else default_w
            currency = _guess_currency(s.stock_code)
            # 汇率折算 (2026-06-24 补丁): 港股/美股用 CNY 价算 shares_eq
            fx_rate = _get_fx_rate(db, currency, "CNY", as_of_date)
            fx_date = as_of_date if fx_rate else None
            price_cny = float(price) * fx_rate if fx_rate else float(price)
            # shares_eq 用 CNY 价算: shares = (fund_price_CNY × 0.95 × weight) / price_CNY
            # 这样: shares_eq × current_price_cny = fund_price × 0.95 × weight / 100
            shares_eq = fund_price * STOCK_RATIO * (weight_pct / 100.0) / price_cny
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
                currency=currency,
                current_price_cny=price_cny,
                cny_currency="CNY" if price_cny is not None else None,
                fx_rate=fx_rate,
                fx_date=fx_date,
                weight_deficit_cash=0.0,  # 仅在首行写入
            ))
        # 把 weight_deficit_cash 标在第一行 (代表「该 fund 整批」)
        if fund_rows:
            fund_rows[0].weight_deficit_cash = weight_deficit_cash

        # 用 INSERT ... ON CONFLICT DO NOTHING 幂等写入
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from datetime import datetime as _dt
        stmt = pg_insert(FundDrillSnapshot.__table__).values([
            {
                "fund_code": r.fund_code,
                "as_of_date": r.as_of_date,
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "weight_pct": r.weight_pct,
                "baseline_price": r.baseline_price,
                "current_price": r.current_price,
                "shares_equivalent": r.shares_equivalent,
                "is_stale_price": r.is_stale_price,
                "currency": r.currency,
                "current_price_cny": r.current_price_cny,
                "cny_currency": r.cny_currency,
                "fx_rate": r.fx_rate,
                "fx_date": r.fx_date,
                "weight_deficit_cash": r.weight_deficit_cash,
                "updated_at": _dt.utcnow(),
            }
            for r in fund_rows
        ])
        stmt = stmt.on_conflict_do_nothing(index_elements=["fund_code", "as_of_date", "stock_code"])
        result = db.execute(stmt)
        funds_processed += 1
        rows_inserted += result.rowcount or 0
        details.append({
            "fund": fund_code, "index": idx_code,
            "fund_price": round(fund_price, 4),
            "constituents": len(constituents),
            "priced": len(priced),
            "stale_count": sum(1 for _, _, st in priced if st),
            "rows": len(fund_rows),
            "weight_sum": round(weight_sum, 4),
            "weight_deficit_cash": round(weight_deficit_cash, 4),
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