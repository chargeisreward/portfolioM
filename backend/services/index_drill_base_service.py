"""指数下钻基础数据 service — 模拟基金（10000 份）卡片 + 双日并排明细。

需求来源：主数据页面 > 指数下钻基础数据 tab（2026-06-28）。

核心概念：
  - "模拟基金"：固定 95% 股票 + 5% 现金，假设持有 10000 份
  - 卡片本身不计算占比/偏差，金额 = nav × 10000
  - 卡片内双日并排：最新日 vs 基期（数据业务日期 = current_business_date）

数据依赖：
  - SecurityMaster（is_drillable=True 的基金）
  - IndexConstituentSnapshot（检查是否有指数构成）
  - FundDrillSnapshot（shares_equivalent + 单价 + 估值字段）
  - FundDailyNav（基金净值）

约当数量算法（基期/最新日各自计算）：
  user_shares = 10000 × shares_equivalent
  est_market_value = user_shares × current_price_cny
  amount = nav × 10000
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any

from sqlalchemy import func as _func
from sqlalchemy.orm import Session

from types import SimpleNamespace

from models import (
    FundDailyNav,
    FundDrillSnapshot,
    IndexConstituentSnapshot,
    PriceCache,
    SecurityMaster,
)

logger = logging.getLogger(__name__)

DRILL_SHARES = 100000  # 模拟基金固定份额（每十万份）


def _get_latest_drill_snapshot_rows(
    db: Session, fund_code: str, latest_date: _date
) -> list[FundDrillSnapshot]:
    """取 ≤ latest_date 的最新一条 snapshot 的所有行。

    若 latest_date 当日有数据，直接返回；否则取 ≤ latest_date 的最大 as_of_date。
    """
    target_date = (
        db.query(_func.max(FundDrillSnapshot.as_of_date))
        .filter(
            FundDrillSnapshot.fund_code == fund_code,
            FundDrillSnapshot.as_of_date <= latest_date,
        )
        .scalar()
    )
    if not target_date:
        return []
    return (
        db.query(FundDrillSnapshot)
        .filter(
            FundDrillSnapshot.fund_code == fund_code,
            FundDrillSnapshot.as_of_date == target_date,
        )
        .all()
    )


def _get_baseline_drill_snapshot_rows(
    db: Session, fund_code: str, baseline_date: _date
) -> list[FundDrillSnapshot]:
    """取基期当日的 snapshot 行；缺失则回退到 ≤ baseline_date 的最新一条。"""
    target_date = (
        db.query(_func.max(FundDrillSnapshot.as_of_date))
        .filter(
            FundDrillSnapshot.fund_code == fund_code,
            FundDrillSnapshot.as_of_date <= baseline_date,
        )
        .scalar()
    )
    if not target_date:
        return []
    return (
        db.query(FundDrillSnapshot)
        .filter(
            FundDrillSnapshot.fund_code == fund_code,
            FundDrillSnapshot.as_of_date == target_date,
        )
        .all()
    )


def _get_nav_price(db: Session, fund_code: str, as_of_date: _date):
    """取 ≤ as_of_date 的最新净值/收盘价。

    .OF 基金查 FundDailyNav.nav；非 .OF 证券（如 .SH/.SZ ETF）查 PriceCache.close_px。
    返回带 .nav 属性的对象（兼容 _compute_card_metrics 和 get_drill_base_detail 的 `nav.nav` 访问）。
    """
    if fund_code.endswith('.OF'):
        target_date = (
            db.query(_func.max(FundDailyNav.trade_date))
            .filter(
                FundDailyNav.fund_code == fund_code,
                FundDailyNav.trade_date <= as_of_date,
            )
            .scalar()
        )
        if not target_date:
            return None
        return (
            db.query(FundDailyNav)
            .filter(
                FundDailyNav.fund_code == fund_code,
                FundDailyNav.trade_date == target_date,
            )
            .first()
        )
    # 非 .OF（ETF 等）→ PriceCache.close_px
    target_date = (
        db.query(_func.max(PriceCache.trade_date))
        .filter(
            PriceCache.stock_code == fund_code,
            PriceCache.trade_date <= as_of_date,
        )
        .scalar()
    )
    if not target_date:
        return None
    pc = (
        db.query(PriceCache)
        .filter(
            PriceCache.stock_code == fund_code,
            PriceCache.trade_date == target_date,
        )
        .first()
    )
    if not pc:
        return None
    return SimpleNamespace(nav=pc.close_px)


def _get_latest_nav(db: Session, fund_code: str, latest_date: _date):
    """取 ≤ latest_date 的最新净值/收盘价。"""
    return _get_nav_price(db, fund_code, latest_date)


def _get_fund_latest_nav(db: Session, fund_code: str, today: _date):
    """per-fund latest nav date（≤ today），返回 (nav_date, nav_obj)。

    用于 v3 重构（2026-06-30）：admin 按每只 fund 自己的最新 nav day 计算下钻，
    严格 same-day basis（分子分母同日）。
    """
    nav_date = (
        db.query(_func.max(FundDailyNav.trade_date))
        .filter(
            FundDailyNav.fund_code == fund_code,
            FundDailyNav.trade_date <= today,
        )
        .scalar()
    )
    if not nav_date:
        return None, None
    nav = (
        db.query(FundDailyNav)
        .filter_by(fund_code=fund_code, trade_date=nav_date)
        .first()
    )
    return nav_date, nav


def _get_baseline_nav(db: Session, fund_code: str, baseline_date: _date):
    """取 ≤ baseline_date 的最新净值/收盘价。"""
    return _get_nav_price(db, fund_code, baseline_date)


def _has_index_constituents(db: Session, index_code: str) -> bool:
    """检查 IndexConstituentSnapshot 是否有该指数的数据（任意日期）。"""
    if not index_code:
        return False
    # 兼容 index_code 带/不带后缀的写法
    idx_norm = index_code.split(".")[0]
    row = (
        db.query(IndexConstituentSnapshot.id)
        .filter(
            (IndexConstituentSnapshot.index_code == index_code)
            | (IndexConstituentSnapshot.index_code == idx_norm)
        )
        .first()
    )
    return row is not None


def _compute_card_metrics(
    drill_rows: list[FundDrillSnapshot],
    nav: FundDailyNav | None,
    baseline_shares_eq_map: dict[str, float] | None = None,
) -> dict:
    """计算单日卡片指标（基于 10000 份模拟基金）。

    v3 修复（2026-06-30）：约当数量自基期计算后不变，shares_eq 用 baseline_shares_eq_map（5/29），
    但用当天 current_price_cny 算 per-10000-share 市值。PE/PB/PS/DY 同理用 baseline_mv 算。

    算法：
      amount = nav × 10000  # 基金总额
      For each stock (skip CASH):
          user_shares = 10000 × baseline_shares_eq (固定)
          est_market_value = user_shares × current_price_cny (当天)
      weighted_pe = Σ est_mv / Σ (est_mv / pe_ttm_dynamic)
      weighted_pb = Σ est_mv / Σ (est_mv / pb_mrq_dynamic)
      weighted_ps = Σ est_mv / Σ (est_mv / ps_ttm_dynamic)
      weighted_dividend_yield = Σ (est_mv × dy) / Σ est_mv
    """
    if not drill_rows or not nav:
        return {
            "amount": None,
            "pe": None,
            "pb": None,
            "ps": None,
            "dividend_yield": None,
            "stock_count": 0,
            "deviation_pct": None,
        }

    if baseline_shares_eq_map is None:
        # 兼容旧调用：fallback 到 drill_rows 自带 shares_eq（v2 行为）
        baseline_shares_eq_map = {}

    amount = (nav.nav or 0.0) * DRILL_SHARES
    total_mv = 0.0  # 股票市值（用于 PE/PB/PS/DY）
    total_mv_with_cash = 0.0  # 含 CASH 的完整市值（仅用于 deviation_pct）
    total_pe_inv = 0.0  # Σ mv/pe
    total_pb_inv = 0.0
    total_ps_inv = 0.0
    total_dy_mv = 0.0
    stock_count = 0

    for r in drill_rows:
        if r.stock_code == "CASH":
            # CASH 行：按基期 CASH shares_eq (不变) × 10000 × 1.0 CNY
            # 公式：下钻现金 = 10000 × fund_price × 5% (固定)
            cash_baseline_se = baseline_shares_eq_map.get("CASH", r.shares_equivalent or 0.0)
            cash_mv = DRILL_SHARES * cash_baseline_se * (r.current_price_cny or 1.0)
            total_mv_with_cash += cash_mv
            continue
        # 关键：shares_eq 用 baseline (固定)，current_price 用当天
        user_shares = DRILL_SHARES * baseline_shares_eq_map.get(r.stock_code, r.shares_equivalent or 0.0)
        mv = user_shares * (r.current_price_cny or 0.0)
        total_mv += mv
        total_mv_with_cash += mv
        stock_count += 1

        pe = r.pe_ttm_dynamic if r.pe_ttm_dynamic is not None else r.pe_ttm
        pb = r.pb_mrq_dynamic if r.pb_mrq_dynamic is not None else r.pb_mrq
        ps = r.ps_ttm_dynamic if r.ps_ttm_dynamic is not None else r.ps_ttm
        if pe and pe > 0:
            total_pe_inv += mv / pe
        if pb and pb > 0:
            total_pb_inv += mv / pb
        if ps and ps > 0:
            total_ps_inv += mv / ps
        if r.dividend_yield:
            total_dy_mv += mv * r.dividend_yield

    # 单位值偏差（与用户层 DrillableFundsPage dev_pct 公式一致）：
    #   drift = (Σ shares_eq × price_cny + cash_value) / nav - 1
    # 公式上：10000×(drift_per_share) / (10000×nav) = drift_per_share
    # 与用户层 dev_pct 数值上完全相同（口径一致）
    deviation_pct = (
        round((total_mv_with_cash / amount - 1) * 100, 4)
        if amount > 0 and total_mv_with_cash > 0
        else None
    )

    return {
        "amount": round(amount, 2),
        "pe": round(total_mv / total_pe_inv, 4) if total_pe_inv > 0 else None,
        "pb": round(total_mv / total_pb_inv, 4) if total_pb_inv > 0 else None,
        "ps": round(total_mv / total_ps_inv, 4) if total_ps_inv > 0 else None,
        "dividend_yield": round(total_dy_mv / total_mv, 4) if total_mv > 0 else None,
        "stock_count": stock_count,
        "deviation_pct": deviation_pct,
    }


def list_drill_base_cards(
    db: Session,
    today: _date,
) -> dict:
    """列出所有 is_drillable=True 基金的卡片数据（per-fund latest, v3 重构 2026-06-30）。

    返回 {"cards": [...], "as_of": str}  (无全局 baseline_date/latest_date 概念,
    每只 fund 在自己的 nav_date 上有数据)
    """
    drillable_funds = (
        db.query(SecurityMaster)
        .filter(SecurityMaster.is_drillable.is_(True))
        .order_by(SecurityMaster.security_code)
        .all()
    )

    cards: list[dict] = []
    for fund in drillable_funds:
        index_code = fund.index_code
        has_constituents = _has_index_constituents(db, index_code or "")

        if not has_constituents:
            cards.append({
                "fund_code": fund.security_code,
                "fund_name": fund.security_name,
                "index_code": index_code,
                "index_name": fund.index_name,
                "has_constituents": False,
                "status": "缺指数构成",
                "nav_date": None,
            })
            continue

        card = _build_drill_base_card(db, fund, today)
        cards.append(card)

    return {
        "cards": cards,
        "as_of": today.isoformat(),
    }


def _build_drill_base_card(
    db: Session,
    fund: SecurityMaster,
    today: _date,
) -> dict:
    """构建单只基金的模拟下钻卡片（10000 份模拟基金，per-fund latest）。

    v3 重构（2026-06-30）：
    - 不再使用全局 latest_date，改为每只 fund 自己的最新 nav date
    - 严格 same-day basis：shares_eq × current_price_cny 与 fund_nav 同一天
    - 完整返回 constituents 数组（给"下钻全持仓"用）
    - 扁平化字段（前端可直接读 card.weighted_pe / card.per_10k_value 等）
    """
    fund_code = fund.security_code

    # per-fund 最新 nav date（≤ today）
    nav_date, latest_nav = _get_fund_latest_nav(db, fund_code, today)
    if not nav_date or not latest_nav:
        return {
            "fund_code": fund_code,
            "fund_name": fund.security_name,
            "index_code": fund.index_code,
            "index_name": fund.index_name,
            "has_constituents": False,
            "status": "无最新净值",
            "nav_date": None,
            "fund_nav": None,
            "per_10k_value": None,
            "deviation_pct": None,
            "stock_count": 0,
        }

    # 同日 drill snapshot（与 nav_date 严格同一天 basis）
    latest_rows = _get_latest_drill_snapshot_rows(db, fund_code, nav_date)

    # 5/29 baseline shares_eq 映射（v3 修复：shares_eq 自基期后不变 — 用户原话 2026-06-30）
    from services.data_version import current_business_date
    baseline_date = current_business_date(today)
    baseline_shares_eq_map: dict[str, float] = {}
    if baseline_date:
        baseline_rows_for_shares = db.query(FundDrillSnapshot).filter(
            FundDrillSnapshot.fund_code == fund_code,
            FundDrillSnapshot.as_of_date == baseline_date,
        ).all()
        for r in baseline_rows_for_shares:
            if r.shares_equivalent is not None:
                baseline_shares_eq_map[r.stock_code] = r.shares_equivalent

    latest_metrics = _compute_card_metrics(latest_rows, latest_nav, baseline_shares_eq_map)

    # 完整 constituents 数组（给"下钻全持仓"用）— shares_eq 用 baseline（固定）
    constituents_breakdown = [
        {
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "weight_pct": r.weight_pct,
            "current_price": r.current_price,
            "current_price_cny": r.current_price_cny,
            "shares_equivalent": baseline_shares_eq_map.get(r.stock_code, r.shares_equivalent),
            "currency": r.currency,
            "fx_rate": r.fx_rate,
            "fx_date": r.fx_date.isoformat() if r.fx_date else None,
            "pe_ttm": r.pe_ttm,
            "pb_mrq": r.pb_mrq,
            "ps_ttm": r.ps_ttm,
            "dividend_yield": r.dividend_yield,
            "pe_ttm_dynamic": r.pe_ttm_dynamic,
            "pb_mrq_dynamic": r.pb_mrq_dynamic,
            "ps_ttm_dynamic": r.ps_ttm_dynamic,
        }
        for r in latest_rows if r.stock_code != "CASH"
    ]
    cash_shares_equivalent = baseline_shares_eq_map.get("CASH", 0.0)

    return {
        "fund_code": fund_code,
        "fund_name": fund.security_name,
        "index_code": fund.index_code,
        "index_name": fund.index_name,
        "has_constituents": True,
        "nav_date": nav_date.isoformat(),                # per-fund
        "fund_nav": float(latest_nav.nav),              # per-fund
        "per_10k_value": latest_metrics["amount"],      # = nav × 10000
        "deviation_pct": latest_metrics["deviation_pct"],
        "stock_count": latest_metrics["stock_count"],
        "weighted_pe": latest_metrics["pe"],
        "weighted_pb": latest_metrics["pb"],
        "weighted_ps": latest_metrics["ps"],
        "weighted_dividend_yield": latest_metrics["dividend_yield"],
        "cash_shares_equivalent": cash_shares_equivalent,
        "constituents": constituents_breakdown,
    }


def get_drill_base_detail(
    db: Session,
    fund_code: str,
    baseline_date: _date | None,
    latest_date: _date | None,
) -> dict | None:
    """获取单只基金的双日并排明细。

    返回结构：
    {
      "fund_code": "...", "fund_name": "...", "index_code": "...",
      "baseline_date": "...", "latest_date": "...",
      "baseline_nav": 4.523, "latest_nav": 4.612,
      "baseline_amount": 45230.0, "latest_amount": 46120.0,
      "stocks": [
        {
          "stock_code": "...", "stock_name": "...",
          "baseline": {weight_pct, shares_equivalent, user_shares, current_price,
                       current_price_cny, pe_ttm, pb_mrq, ps_ttm, dividend_yield,
                       est_market_value},
          "latest": {weight_pct, shares_equivalent, user_shares, current_price,
                     current_price_cny, pe_ttm_dynamic, pb_mrq_dynamic, ps_ttm_dynamic,
                     dividend_yield, est_market_value}
        }
      ]
    }
    """
    fund = (
        db.query(SecurityMaster)
        .filter(SecurityMaster.security_code == fund_code)
        .first()
    )
    if not fund:
        return None

    # 读基期/最新日 snapshot 行
    baseline_rows: list[FundDrillSnapshot] = []
    latest_rows: list[FundDrillSnapshot] = []
    baseline_actual_date: _date | None = None
    latest_actual_date: _date | None = None
    if baseline_date:
        baseline_rows = _get_baseline_drill_snapshot_rows(db, fund_code, baseline_date)
        if baseline_rows:
            baseline_actual_date = baseline_rows[0].as_of_date
    if latest_date:
        latest_rows = _get_latest_drill_snapshot_rows(db, fund_code, latest_date)
        if latest_rows:
            latest_actual_date = latest_rows[0].as_of_date

    baseline_nav = _get_baseline_nav(db, fund_code, baseline_date) if baseline_date else None
    latest_nav = _get_latest_nav(db, fund_code, latest_date) if latest_date else None

    baseline_nav_value = baseline_nav.nav if baseline_nav else None
    latest_nav_value = latest_nav.nav if latest_nav else None
    baseline_amount = round(baseline_nav_value * DRILL_SHARES, 2) if baseline_nav_value else None
    latest_amount = round(latest_nav_value * DRILL_SHARES, 2) if latest_nav_value else None

    # 按最新日 effective_weight_pct 降序排序（反映当前实际权重，2026-06-28）
    baseline_map: dict[str, FundDrillSnapshot] = {r.stock_code: r for r in baseline_rows}
    latest_map: dict[str, FundDrillSnapshot] = {r.stock_code: r for r in latest_rows}
    all_codes = set(baseline_map.keys()) | set(latest_map.keys())

    # 计算最新日 + 基期 总市值（含 CASH），用于 effective_weight_pct
    # 注（2026-06-30 修复）：之前只算非 CASH 股票归一化到 95%，CASH 硬写 5，
    # 导致 105% 总和 + 实际漂移后 CASH 仍写 5。现在改为全口径 mv / total × 100。
    cash_row_b = baseline_map.get("CASH")
    cash_row_l = latest_map.get("CASH")
    # 现金市值（CNY/份）= cash_shares_equivalent × 1.0
    cash_mv_baseline = (cash_row_b.shares_equivalent or 0.0) * 1.0 if cash_row_b else 0.0
    cash_mv_latest = (cash_row_l.shares_equivalent or 0.0) * 1.0 if cash_row_l else 0.0

    total_mv_baseline = cash_mv_baseline  # 累加 stock mv
    total_mv_latest = cash_mv_latest
    # 缓存每只 stock 的 mv 用于排序 + 计算权重
    stock_mv_baseline: dict[str, float] = {}
    stock_mv_latest: dict[str, float] = {}
    for code in all_codes:
        if code == "CASH":
            continue
        b_row = baseline_map.get(code)
        l_row = latest_map.get(code)
        if b_row:
            mv_b = (b_row.shares_equivalent or 0.0) * (b_row.current_price_cny or 0.0)
            stock_mv_baseline[code] = mv_b
            total_mv_baseline += mv_b
        if l_row:
            mv_l = (l_row.shares_equivalent or 0.0) * (l_row.current_price_cny or 0.0)
            stock_mv_latest[code] = mv_l
            total_mv_latest += mv_l

    def _sort_by_eff_weight(code: str) -> float:
        # 优先按最新日 mv 排序（与 effective_weight_pct 一致）
        mv_l = stock_mv_latest.get(code)
        if mv_l is not None:
            return -mv_l
        mv_b = stock_mv_baseline.get(code)
        if mv_b is not None:
            return -mv_b
        b_row = baseline_map.get(code)
        if b_row and b_row.weight_pct is not None:
            return -b_row.weight_pct * 0.001
        return 0.0  # CASH 排最后

    all_codes = sorted(all_codes, key=_sort_by_eff_weight)

    stocks: list[dict] = []
    # 累加器（用于前端 tfoot 显示「基期合计」「最新日合计」）
    sum_w_baseline = 0.0
    sum_w_latest = 0.0
    sum_mv_baseline = 0.0
    sum_mv_latest = 0.0

    for code in all_codes:
        b_row = baseline_map.get(code)
        l_row = latest_map.get(code)
        # 股票名称优先取最新日，回退基期
        stock_name = (l_row.stock_name if l_row else None) or (b_row.stock_name if b_row else None)

        # === 计算 effective_weight_pct（基金内实际占比 — 2026-06-30 修复）===
        # - 基期: stocks 官方权重 × 0.95 (95% 权益部分)，CASH = 5.0
        #         → 总和 = 95 + 5 = 100%
        # - 最新日: 全口径 mv / Σ(含 CASH) × 100
        #         → 反映真实漂移：股票上涨则权重大、现金缩水；股票下跌则反之
        #         → 总和 = 100%
        if code == "CASH":
            eff_w_baseline = 5.0
            if total_mv_latest > 0:
                eff_w_latest = round(cash_mv_latest / total_mv_latest * 100, 4)
            else:
                eff_w_latest = None
        else:
            # 基期股票权重 = 官方 × 0.95
            if b_row and b_row.weight_pct is not None:
                eff_w_baseline = round(b_row.weight_pct * 0.95, 4)
            else:
                eff_w_baseline = None
            # 最新日 = stock_mv_latest / total_mv_latest × 100
            if total_mv_latest > 0:
                mv_l = stock_mv_latest.get(code, 0.0)
                eff_w_latest = round(mv_l / total_mv_latest * 100, 4)
            else:
                eff_w_latest = None

        if eff_w_baseline is not None:
            sum_w_baseline += eff_w_baseline
        if eff_w_latest is not None:
            sum_w_latest += eff_w_latest
        sum_mv_baseline += stock_mv_baseline.get(code, 0.0)
        sum_mv_latest += stock_mv_latest.get(code, 0.0)

        stocks.append({
            "stock_code": code,
            "stock_name": stock_name,
            "baseline": _snapshot_to_detail(b_row, is_baseline=True, effective_weight_pct=eff_w_baseline),
            "latest": _snapshot_to_detail(l_row, is_baseline=False, effective_weight_pct=eff_w_latest),
        })

    return {
        "fund_code": fund.security_code,
        "fund_name": fund.security_name,
        "index_code": fund.index_code,
        "index_name": fund.index_name,
        "baseline_date": (baseline_actual_date or baseline_date).isoformat() if (baseline_actual_date or baseline_date) else None,
        "latest_date": (latest_actual_date or latest_date).isoformat() if (latest_actual_date or latest_date) else None,
        "baseline_nav": baseline_nav_value,
        "latest_nav": latest_nav_value,
        "baseline_amount": baseline_amount,
        "latest_amount": latest_amount,
        # 合计（2026-06-30 新增）— 给前端 tfoot 用
        "totals": {
            "sum_weight_baseline": round(sum_w_baseline, 4),
            "sum_weight_latest": round(sum_w_latest, 4),
            "sum_mv_baseline": round(sum_mv_baseline, 2),
            "sum_mv_latest": round(sum_mv_latest, 2),
            "cash_mv_baseline": round(cash_mv_baseline, 4),
            "cash_mv_latest": round(cash_mv_latest, 4),
        },
        "stocks": stocks,
    }


def _snapshot_to_detail(
    row: FundDrillSnapshot | None,
    is_baseline: bool,
    effective_weight_pct: float | None = None,
) -> dict:
    """将 FundDrillSnapshot 行转为明细 dict。

    is_baseline=True：返回基期字段（pe_ttm/pb_mrq/ps_ttm 静态值）
    is_baseline=False：返回最新日字段（pe_ttm_dynamic/pb_mrq_dynamic/ps_ttm_dynamic 动态值）

    effective_weight_pct: 实际权重（消费层动态计算）。
        - 基期: = weight_pct（官方权重）
        - 最新日: = (shares_eq × current_price_cny) / Σ(非CASH mv) × 95
        weight_pct 字段始终为官方权重（输入参数），effective_weight_pct 反映股价漂移。

    约当数量 user_shares = 10000 × shares_equivalent
    est_market_value = user_shares × current_price_cny
    """
    if not row:
        return {
            "weight_pct": None,
            "effective_weight_pct": effective_weight_pct,
            "shares_equivalent": None,
            "user_shares": None,
            "current_price": None,
            "current_price_cny": None,
            "pe_ttm": None,
            "pb_mrq": None,
            "ps_ttm": None,
            "dividend_yield": None,
            "est_market_value": None,
        }

    user_shares = DRILL_SHARES * (row.shares_equivalent or 0.0)
    est_mv = user_shares * (row.current_price_cny or 0.0)

    if is_baseline:
        pe_v = row.pe_ttm
        pb_v = row.pb_mrq
        ps_v = row.ps_ttm
    else:
        pe_v = row.pe_ttm_dynamic if row.pe_ttm_dynamic is not None else row.pe_ttm
        pb_v = row.pb_mrq_dynamic if row.pb_mrq_dynamic is not None else row.pb_mrq
        ps_v = row.ps_ttm_dynamic if row.ps_ttm_dynamic is not None else row.ps_ttm

    return {
        "weight_pct": row.weight_pct,
        "effective_weight_pct": effective_weight_pct,
        "shares_equivalent": row.shares_equivalent,
        "user_shares": round(user_shares, 4),
        "current_price": row.current_price,
        "current_price_cny": row.current_price_cny,
        "pe_ttm": pe_v,
        "pb_mrq": pb_v,
        "ps_ttm": ps_v,
        "dividend_yield": row.dividend_yield,
        "est_market_value": round(est_mv, 4),
    }
