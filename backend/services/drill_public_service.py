"""公共下钻 service — 只读 fund_drill_snapshot + fund_index_map 表。
不知道 user_id，不读 Holding 表。可独立复用。

数据来源：scheduler 每日生成的 fund_drill_snapshot 预计算表。
index_code/index_name 通过 FundIndexMap join 获取（FundDrillSnapshot 无此字段）。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date

from sqlalchemy.orm import Session

from models import FundDrillSnapshot, FundIndexMap

logger = logging.getLogger(__name__)


def _load_fund_index_map(db: Session) -> dict[str, tuple[str, str]]:
    """加载 fund_code → (index_code, index_name) 映射。"""
    rows = db.query(FundIndexMap).all()
    return {r.fund_code: (r.index_code.split(".")[0], r.index_name or "") for r in rows}


def _resolve_snapshot_date(db: Session, as_of: _date) -> _date | None:
    """解析实际可用的 snapshot 日期（带 2 次拉取规则门控）。

    门控：as_of 先 clamp 到 get_confirmed_as_of(db)，再查 ≤ 的最近日期。
    2 次拉取规则（2026-06-26）：T 日 snapshot 在 T+1 日 08:00 后才视为已确认，
    避免前端在 T 日 09:00 查到尚未生成的 T 日 snapshot 而回退到 T-1。

    回退策略（按优先级）：
    1. ≤ effective_as_of 的最近有数据日期
    2. 若 effective_as_of 早于所有 snapshot，回退到表中最新 snapshot 日期

    返回 None 仅当表完全为空。
    """
    from sqlalchemy import func
    from services.trading_calendar import get_confirmed_as_of

    # 2 次规则门控：as_of 不超过 confirmed
    confirmed = get_confirmed_as_of(db)
    effective_as_of = min(as_of, confirmed)

    # 1. ≤ effective_as_of 的最近日期
    row = db.query(func.max(FundDrillSnapshot.as_of_date)).filter(
        FundDrillSnapshot.as_of_date <= effective_as_of
    ).scalar()
    if row is not None:
        return row
    # 2. as_of 早于所有 snapshot → 取最新 snapshot
    latest = db.query(func.max(FundDrillSnapshot.as_of_date)).scalar()
    if latest is not None and latest > as_of:
        logger.info(
            f"_resolve_snapshot_date: as_of {as_of} (effective={effective_as_of}) "
            f"早于所有 snapshot，回退到最新 {latest}"
        )
    return latest


def get_public_cards(db: Session, as_of: _date) -> list[dict]:
    """返回所有公共下钻卡片（按指数分组）。

    只读 fund_drill_snapshot + fund_index_map，不含任何用户数据。
    若 as_of 当天无 snapshot，自动回退到最近有数据的日期。

    返回结构：
    [
        {
            "index_code": "000300",
            "index_name": "沪深300",
            "as_of": "2026-06-24",
            "fund_codes": ["510300.SH", ...],
            "stock_count": 300,
            "total_weight": 1.0,
            "static_amount_cny": 12345.6,        # Σ shares_eq × baseline_price（公共层基准日金额）
            "weighted_pe": 15.23,                # 调和平均 PE
            "weighted_pb": 2.45,                 # 调和平均 PB
            "weighted_ps": 3.12,                 # 调和平均 PS
            "weighted_dividend_yield": 1.85,     # 算术平均股息率
        },
    ]
    """
    # 日期回退：若 as_of 当天无数据，取 ≤ as_of 的最近日期
    effective_date = _resolve_snapshot_date(db, as_of)
    if effective_date is None:
        return []
    if effective_date != as_of:
        logger.info(f"get_public_cards: as_of {as_of} 无数据，回退到 {effective_date}")

    rows = db.query(FundDrillSnapshot).filter(
        FundDrillSnapshot.as_of_date == effective_date
    ).all()

    if not rows:
        return []

    # 加载 fund_code → (index_code, index_name) 映射
    fund_map = _load_fund_index_map(db)

    by_index: dict[str, dict] = {}
    for r in rows:
        fund_code = r.fund_code
        if fund_code not in fund_map:
            continue
        idx_code, idx_name = fund_map[fund_code]
        is_cash = (r.stock_code == "CASH")

        if idx_code not in by_index:
            by_index[idx_code] = {
                "index_code": idx_code,
                "index_name": idx_name or idx_code,
                "as_of": effective_date.isoformat(),
                "fund_codes": set(),
                "stock_set": set(),
                "total_weight": 0.0,
                # 估值加权累加器（2026-06-25 补全）
                "weight_basis_sum": 0.0,            # Σ shares_eq × baseline_price
                "virt_pe": 0.0,                     # Σ weight_basis / pe_dyn
                "virt_pb": 0.0,
                "virt_ps": 0.0,
                "sum_dy_weighted": 0.0,             # Σ weight_basis × dy_dyn
            }
        bucket = by_index[idx_code]
        bucket["fund_codes"].add(fund_code)
        # 现金-下钻行不计入 stock_set / total_weight（现金不是股票，无指数权重）
        if not is_cash:
            bucket["stock_set"].add(r.stock_code)
            bucket["total_weight"] += (r.weight_pct or 0.0) / 100.0

        # 现金-下钻行不参与 PE/PB/PS/股息率加权（现金无盈利/净资产/营收/分红）
        if is_cash:
            continue

        # 估值加权计算（动态调整公式 + 调和平均 PE/PB/PS + 算术平均股息率）
        # 双币种规则 (2026-06-25)：weight_basis 用本币(CNY)字段，保证 A 股/H 股量纲一致。
        # 本币字段在公共数据层(drill_snapshot.py)算好，下游直接取，不临时计算。
        # weight_basis = shares_equivalent × baseline_price_cny（公共层等价"1 份基金对应基准日 CNY 金额"）
        shares_eq = r.shares_equivalent or 0.0
        baseline_price_cny = r.baseline_price_cny or 0.0
        current_price_cny = r.current_price_cny or 0.0
        weight_basis = shares_eq * baseline_price_cny

        if weight_basis > 0 and baseline_price_cny > 0 and current_price_cny > 0:
            price_ratio = current_price_cny / baseline_price_cny
            # PE/PB/PS 优先用持久化的动态值（来自估值表 *_dynamic 字段，已基于最新收盘价调整）
            # 若动态值为空（如海外股），fallback 到基准日值 × price_ratio 实时算
            pe_dyn = r.pe_ttm_dynamic if r.pe_ttm_dynamic else (
                r.pe_ttm * price_ratio if r.pe_ttm else None
            )
            pb_dyn = r.pb_mrq_dynamic if r.pb_mrq_dynamic else (
                r.pb_mrq * price_ratio if r.pb_mrq else None
            )
            ps_dyn = r.ps_ttm_dynamic if r.ps_ttm_dynamic else (
                r.ps_ttm * price_ratio if r.ps_ttm else None
            )
            # 股息率无 dynamic 字段，仍用 dividend_yield × (baseline/current) 实时算
            dy_dyn = (r.dividend_yield / price_ratio) if r.dividend_yield else None

            bucket["weight_basis_sum"] += weight_basis
            # 调和平均：累加"利润贡献"weight_basis / pe_dyn
            if pe_dyn and pe_dyn > 0:
                bucket["virt_pe"] += weight_basis / pe_dyn
            if pb_dyn and pb_dyn > 0:
                bucket["virt_pb"] += weight_basis / pb_dyn
            if ps_dyn and ps_dyn > 0:
                bucket["virt_ps"] += weight_basis / ps_dyn
            # 算术平均：累加 weight_basis × dy_dyn
            if dy_dyn is not None:
                bucket["sum_dy_weighted"] += weight_basis * dy_dyn

    cards = []
    for bucket in by_index.values():
        wbs = bucket["weight_basis_sum"]
        cards.append({
            "index_code": bucket["index_code"],
            "index_name": bucket["index_name"],
            "as_of": bucket["as_of"],
            "fund_codes": sorted(bucket["fund_codes"]),
            "stock_count": len(bucket["stock_set"]),
            "total_weight": round(bucket["total_weight"], 4),
            # 公共层金额 + 加权估值（2026-06-25 补全）
            "static_amount_cny": round(wbs, 4) if wbs else 0.0,
            "weighted_pe": round(wbs / bucket["virt_pe"], 4) if bucket["virt_pe"] else None,
            "weighted_pb": round(wbs / bucket["virt_pb"], 4) if bucket["virt_pb"] else None,
            "weighted_ps": round(wbs / bucket["virt_ps"], 4) if bucket["virt_ps"] else None,
            "weighted_dividend_yield": round(bucket["sum_dy_weighted"] / wbs, 4) if wbs else None,
        })
    cards.sort(key=lambda c: c["stock_count"], reverse=True)
    return cards


def get_public_detail(db: Session, as_of: _date, index_code: str) -> dict | None:
    """返回某指数的公共下钻明细（成分股 + 基金穿透关系）。

    只读 fund_drill_snapshot + fund_index_map，不含任何用户数据。
    无数据返回 None。

    返回结构：
    {
        "index_code": "000300",
        "index_name": "沪深300",
        "as_of": "2026-06-24",
        "constituents": [
            {"stock_code": "600519.SH", "stock_name": "贵州茅台", "weight_pct": 5.23,
             "baseline_price": 1500.0, "current_price": 1600.0, "shares_equivalent": 0.001},
        ],
        "funds": [
            {"fund_code": "510300.SH", "fund_name": "华泰柏瑞沪深300ETF",
             "shares_equivalent": 1234567.0},
        ],
    }
    """
    idx_code = index_code.split(".")[0]

    # 日期回退：若 as_of 当天无数据，取 ≤ as_of 的最近日期
    effective_date = _resolve_snapshot_date(db, as_of)
    if effective_date is None:
        return None
    if effective_date != as_of:
        logger.info(f"get_public_detail: as_of {as_of} 无数据，回退到 {effective_date}")

    # 通过 FundIndexMap 找到跟踪该 index 的 fund_codes
    fund_maps = db.query(FundIndexMap).filter(
        FundIndexMap.index_code.startswith(idx_code)
    ).all()
    if not fund_maps:
        return None

    fund_codes = [fm.fund_code for fm in fund_maps]
    fund_name_map = {fm.fund_code: fm.index_name or "" for fm in fund_maps}
    index_name = fund_maps[0].index_name or idx_code

    # 查这些 fund 的 drill snapshot
    rows = db.query(FundDrillSnapshot).filter(
        FundDrillSnapshot.as_of_date == effective_date,
        FundDrillSnapshot.fund_code.in_(fund_codes),
    ).all()

    if not rows:
        return None

    constituents_by_code: dict[str, dict] = {}
    funds_by_code: dict[str, dict] = {}

    for r in rows:
        is_cash = (r.stock_code == "CASH")
        # 成分股
        if r.stock_code not in constituents_by_code:
            constituents_by_code[r.stock_code] = {
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "weight_pct": r.weight_pct,                    # orchestration 现有 join 用此 key
                "weight_at_baseline_pct": r.weight_pct,        # 前端期望字段名（2026-06-25）
                "baseline_price": r.baseline_price,            # 原币基准价
                "current_price": r.current_price,              # 原币当日价
                # 双币种 (2026-06-25)：本币(CNY)字段，公共层算好，前端/下游直接取
                "baseline_price_cny": r.baseline_price_cny,    # 本币基准价
                "current_price_cny": r.current_price_cny,      # 本币当日价
                "currency": r.currency,                        # 原币币种
                "fx_rate": r.fx_rate,                          # 当日汇率 (to_cny)
                "shares_equivalent": 0.0,
                # 估值字段（基准日值，2026-06-25 补全）
                "pe_ttm": r.pe_ttm,
                "pb_mrq": r.pb_mrq,
                "ps_ttm": r.ps_ttm,
                "dividend_yield": r.dividend_yield,
                # 动态估值字段（基于最新收盘价调整，2026-06-25 补全）
                # 前端明细表优先显示动态值，fallback 到基准日值
                "pe_ttm_dynamic": r.pe_ttm_dynamic,
                "pb_mrq_dynamic": r.pb_mrq_dynamic,
                "ps_ttm_dynamic": r.ps_ttm_dynamic,
                # 现金-下钻标记（前端特殊渲染）
                "is_cash": is_cash,
            }
        constituents_by_code[r.stock_code]["shares_equivalent"] += (r.shares_equivalent or 0.0)

        # 基金
        if r.fund_code not in funds_by_code:
            funds_by_code[r.fund_code] = {
                "fund_code": r.fund_code,
                "fund_name": fund_name_map.get(r.fund_code, ""),
                "shares_equivalent": 0.0,
            }
        funds_by_code[r.fund_code]["shares_equivalent"] += (r.shares_equivalent or 0.0)

    constituents = list(constituents_by_code.values())
    # 按权重降序排列，现金-下钻行排末尾
    constituents.sort(key=lambda c: (c.get("is_cash", False), -(c.get("weight_pct", 0) or 0)))

    funds = list(funds_by_code.values())
    funds.sort(key=lambda f: f["shares_equivalent"], reverse=True)

    return {
        "index_code": idx_code,
        "index_name": index_name,
        "as_of": effective_date.isoformat(),
        "constituents": constituents,
        "funds": funds,
    }
