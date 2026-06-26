"""公共下钻截面快照生成器（按 fund × as_of_date — 2026-06-24）

算法（按用户最终确认）:
  1. 读取 fund_index_map 的所有可下钻基金（fund → index_code 映射）
  2. 对每只 fund：读取 index_constituents[最近月份] 的成分股 + 权重
  3. 取每只成分股 T 日 current_price；缺失用 T-1 价（视为停牌）
  4. 校验：获得收盘价的成分股占比 >= 95% 才生成截面
  5. 算 shares_equivalent = fund_price × 0.95 × (weight/100) / current_price
     其中 fund_price 从 Holding.price 取（fund 当日基金价格，所有 user 的均价也行）
  6. 追加"现金-下钻"行（stock_code="CASH"）：基金 95% 配指数 + 5% 配现金，
     指数中股票权重合计 100% 但基金中股票合计 95%，其余 5% = 现金-下钻。
     shares_equivalent = fund_price × 0.05, current_price = 1.0
  7. 写入 fund_drill_snapshot（公共数据）

user 层：user_drill[s] = Holding.quantity × shares_equivalent[s]
        user_cash    = Holding.quantity × (fund_price × 0.05)  # 来自 CASH 行

注意：fund_price 的来源 — Holding 表每个 user 都有该 fund 的 price，
  为保证一致性，取该 fund 在 Holding 表里的「所有 user 价格均值」作为公共 fund_price。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from models import (
    AShareFinancialSnapshot,
    ExchangeRate,
    FundDrillSnapshot,
    FundIndexMap,
    HKShareFinancialSnapshot,
    Holding,
    IndexConstituentSnapshot,
    PriceCache,
)
from sqlalchemy import func as sa_func

logger = logging.getLogger(__name__)

STOCK_PRICE_FRESHNESS_RATIO = 0.95   # 至少 95% 成分股有当日价才生成截面
STALE_PRICE_FALLBACK_DAYS = 7        # 缺失可用 T-1..T-7 任意一天的价作为替补
CASH_RATIO = 0.05                    # 5% 现金
STOCK_RATIO = 1 - CASH_RATIO         # 95% 成分股


def _load_valuation_snapshots(db: Session, as_of_date: date) -> dict[str, dict]:
    """预加载 ≤ as_of_date 的最新 A/H 估值批次（不按 user_id 过滤）。

    基准日可变：取 ≤ as_of_date 的最新批次（非硬编码 5/29），未来导入新基准日数据时自动切换。
    估值是市场公共数据，与持仓交易无关，故不按 user_id 过滤。

    返回结构：
        {
            "600519.SH": {
                "pe_ttm": 30.5, "pb_mrq": 10.2, "ps_ttm": 15.0, "dividend_yield": 1.2,
                "pe_ttm_dynamic": 32.1, "pb_mrq_dynamic": 10.8, "ps_ttm_dynamic": 15.7,
            },
            "600519": <同上>,  # 同时存无后缀 norm key 作 fallback
            ...
        }

    动态字段（pe_ttm_dynamic 等）来自估值表的 *_dynamic 列，
    已由导入流程基于最新收盘价相对 baseline 的调整每日持久化保存。
    """
    # 找 A 股估值表的最新批次日期（≤ as_of_date）
    a_latest = db.query(sa_func.max(AShareFinancialSnapshot.as_of_date)).filter(
        AShareFinancialSnapshot.as_of_date <= as_of_date
    ).scalar()
    # 找 H 股估值表的最新批次日期
    h_latest = db.query(sa_func.max(HKShareFinancialSnapshot.as_of_date)).filter(
        HKShareFinancialSnapshot.as_of_date <= as_of_date
    ).scalar()

    out: dict[str, dict] = {}
    if a_latest is not None:
        for r in db.query(AShareFinancialSnapshot).filter(
            AShareFinancialSnapshot.as_of_date == a_latest
        ).all():
            v = {
                "pe_ttm": r.pe_ttm,
                "pb_mrq": r.pb_mrq,
                "ps_ttm": r.ps_ttm,
                "dividend_yield": r.dividend_yield,
                "pe_ttm_dynamic": r.pe_ttm_dynamic,
                "pb_mrq_dynamic": r.pb_mrq_dynamic,
                "ps_ttm_dynamic": r.ps_ttm_dynamic,
            }
            out[r.stock_code] = v
            # 同时存无后缀 norm key
            norm = r.stock_code.split(".")[0]
            if norm != r.stock_code:
                out.setdefault(norm, v)
    if h_latest is not None:
        for r in db.query(HKShareFinancialSnapshot).filter(
            HKShareFinancialSnapshot.as_of_date == h_latest
        ).all():
            v = {
                "pe_ttm": r.pe_ttm,
                "pb_mrq": r.pb_mrq,
                "ps_ttm": r.ps_ttm,
                "dividend_yield": r.dividend_yield,
                "pe_ttm_dynamic": r.pe_ttm_dynamic,
                "pb_mrq_dynamic": r.pb_mrq_dynamic,
                "ps_ttm_dynamic": r.ps_ttm_dynamic,
            }
            out[r.stock_code] = v
            norm = r.stock_code.split(".")[0]
            if norm != r.stock_code:
                out.setdefault(norm, v)

    logger.info(
        "valuation snapshots loaded: a_latest=%s, h_latest=%s, total_codes=%d",
        a_latest, h_latest, len(out),
    )
    return out


def _latest_index_constituents(db: Session, idx_code: str, on_or_before: date) -> list[IndexConstituentSnapshot]:
    """取 idx_code 在 on_or_before 或之前的最近一份「带权重的」成分股快照。

    优先取权重和 > 0 的 as_of_date（用户 2026-06-24 补丁）：
      - 如果 5/29 有 weight（非空），就用 5/29（指数公司真实权重）
      - 如果 5/29 weight 全空（如 399673 PG 旧数据），才用最近的 6/15 兜底
    """
    # 步骤 1: 基础数据基准期5月29日 — 找到该日是否有 weight
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

    # 预加载估值快照（A/H 估值表，≤ as_of_date 的最新批次，不按 user_id 过滤）
    # 用于内层循环 join 写入 pe_ttm / pb_mrq / ps_ttm / dividend_yield 字段
    valuation_snaps = _load_valuation_snapshots(db, as_of_date)

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
            # 双币种规则 (2026-06-25): baseline_price_cny 在公共层算好，下游取公共数据不临时算
            # fx_rate 缺失时 fallback 到原币价（与 price_cny 同逻辑）
            bp_orig = getattr(s, "baseline_price", None)
            if bp_orig is not None:
                baseline_price_cny = float(bp_orig) * fx_rate if fx_rate else float(bp_orig)
            else:
                baseline_price_cny = None
            # shares_eq 用 CNY 价算: shares = (fund_price_CNY × 0.95 × weight) / price_CNY
            # 这样: shares_eq × current_price_cny = fund_price × 0.95 × weight / 100
            shares_eq = fund_price * STOCK_RATIO * (weight_pct / 100.0) / price_cny
            # 2026-06-25: join A/H 估值表写入 4 字段（基准日值，公共层加权时再做动态调整）
            v = valuation_snaps.get(s.stock_code) or valuation_snaps.get(s.stock_code.split(".")[0])
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
                baseline_price_cny=baseline_price_cny,
                cny_currency="CNY" if price_cny is not None else None,
                fx_rate=fx_rate,
                fx_date=fx_date,
                weight_deficit_cash=0.0,  # 仅在首行写入
                pe_ttm=(v.get("pe_ttm") if v else None),
                pb_mrq=(v.get("pb_mrq") if v else None),
                ps_ttm=(v.get("ps_ttm") if v else None),
                dividend_yield=(v.get("dividend_yield") if v else None),
                pe_ttm_dynamic=(v.get("pe_ttm_dynamic") if v else None),
                pb_mrq_dynamic=(v.get("pb_mrq_dynamic") if v else None),
                ps_ttm_dynamic=(v.get("ps_ttm_dynamic") if v else None),
            ))
        # 把 weight_deficit_cash 标在第一行 (代表「该 fund 整批」)
        if fund_rows:
            fund_rows[0].weight_deficit_cash = weight_deficit_cash

        # 现金-下钻行：基金 5% 现金部分（公共数据分解 — 2026-06-25）
        # 基金 = 95% 指数 + 5% 现金。指数中股票权重合计 100%，但基金中股票合计 95%，
        # 其余 5% 分配给现金-下钻。现金也是资产，需计入合计。
        # shares_equivalent = fund_price × CASH_RATIO（每份基金含现金金额）
        # current_price = 1.0 → 估算市值 = shares_eq × price = fund_price × 0.05
        fund_rows.append(FundDrillSnapshot(
            fund_code=fund_code,
            as_of_date=as_of_date,
            stock_code="CASH",
            stock_name="下钻-现金",
            weight_pct=CASH_RATIO * 100.0,  # 5.0
            baseline_price=1.0,
            current_price=1.0,
            shares_equivalent=float(fund_price * CASH_RATIO),
            is_stale_price=False,
            currency="CNY",
            current_price_cny=1.0,
            baseline_price_cny=1.0,
            cny_currency="CNY",
            fx_rate=1.0,
            fx_date=as_of_date,
            weight_deficit_cash=0.0,
            pe_ttm=None,
            pb_mrq=None,
            ps_ttm=None,
            dividend_yield=None,
            pe_ttm_dynamic=None,
            pb_mrq_dynamic=None,
            ps_ttm_dynamic=None,
        ))

        # 用 INSERT ... ON CONFLICT DO UPDATE 幂等写入（2026-06-25 修正）
        # 原 on_conflict_do_nothing 会导致补字段重跑时已存在行被跳过，
        # pe_ttm/pb_mrq/ps_ttm/dividend_yield 永远是 NULL。改为 DO UPDATE 全量刷新。
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
                "baseline_price_cny": r.baseline_price_cny,
                "cny_currency": r.cny_currency,
                "fx_rate": r.fx_rate,
                "fx_date": r.fx_date,
                "weight_deficit_cash": r.weight_deficit_cash,
                "pe_ttm": r.pe_ttm,
                "pb_mrq": r.pb_mrq,
                "ps_ttm": r.ps_ttm,
                "dividend_yield": r.dividend_yield,
                "pe_ttm_dynamic": r.pe_ttm_dynamic,
                "pb_mrq_dynamic": r.pb_mrq_dynamic,
                "ps_ttm_dynamic": r.ps_ttm_dynamic,
                "updated_at": _dt.utcnow(),
            }
            for r in fund_rows
        ])
        # 冲突时全量更新（同一 as_of_date 重跑应刷新所有字段，保证估值字段等能补齐）
        stmt = stmt.on_conflict_do_update(
            index_elements=["fund_code", "as_of_date", "stock_code"],
            set_={
                "stock_name": stmt.excluded.stock_name,
                "weight_pct": stmt.excluded.weight_pct,
                "baseline_price": stmt.excluded.baseline_price,
                "current_price": stmt.excluded.current_price,
                "shares_equivalent": stmt.excluded.shares_equivalent,
                "is_stale_price": stmt.excluded.is_stale_price,
                "currency": stmt.excluded.currency,
                "current_price_cny": stmt.excluded.current_price_cny,
                "baseline_price_cny": stmt.excluded.baseline_price_cny,
                "cny_currency": stmt.excluded.cny_currency,
                "fx_rate": stmt.excluded.fx_rate,
                "fx_date": stmt.excluded.fx_date,
                "weight_deficit_cash": stmt.excluded.weight_deficit_cash,
                "pe_ttm": stmt.excluded.pe_ttm,
                "pb_mrq": stmt.excluded.pb_mrq,
                "ps_ttm": stmt.excluded.ps_ttm,
                "dividend_yield": stmt.excluded.dividend_yield,
                "pe_ttm_dynamic": stmt.excluded.pe_ttm_dynamic,
                "pb_mrq_dynamic": stmt.excluded.pb_mrq_dynamic,
                "ps_ttm_dynamic": stmt.excluded.ps_ttm_dynamic,
                "updated_at": _dt.utcnow(),
            },
        )
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
    """取某 fund 在 as_of_date 的下钻截面；若当日缺失，先后向再前向回退到最近一天。"""
    rows = (
        db.query(FundDrillSnapshot)
        .filter(FundDrillSnapshot.fund_code == fund_code, FundDrillSnapshot.as_of_date == as_of_date)
        .all()
    )
    if rows:
        return rows
    # 后向回退：取该 fund <= as_of_date 的最近截面日期
    latest_date = (
        db.query(FundDrillSnapshot.as_of_date)
        .filter(FundDrillSnapshot.fund_code == fund_code, FundDrillSnapshot.as_of_date <= as_of_date)
        .order_by(FundDrillSnapshot.as_of_date.desc())
        .first()
    )
    if latest_date:
        return (
            db.query(FundDrillSnapshot)
            .filter(FundDrillSnapshot.fund_code == fund_code, FundDrillSnapshot.as_of_date == latest_date[0])
            .all()
        )
    # 前向回退：取该 fund > as_of_date 的最近截面日期（基础数据基准期5月29日无下钻截面时用后续截面）
    next_date = (
        db.query(FundDrillSnapshot.as_of_date)
        .filter(FundDrillSnapshot.fund_code == fund_code, FundDrillSnapshot.as_of_date > as_of_date)
        .order_by(FundDrillSnapshot.as_of_date.asc())
        .first()
    )
    if not next_date:
        return None
    return (
        db.query(FundDrillSnapshot)
        .filter(FundDrillSnapshot.fund_code == fund_code, FundDrillSnapshot.as_of_date == next_date[0])
        .all()
    )