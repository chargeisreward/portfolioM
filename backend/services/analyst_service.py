"""分析师页面后端业务逻辑 — 与「分析-全持仓」页同源数据。

2026-06-30 用户反馈：
  - 权重应和「分析-全持仓」保持一致（同一分母、同一 emv）
  - PE/PB/PS/股息率应和「分析-下钻」保持一致（drill_snapshot 的 pe_ttm_dynamic 等）
  - 最新收盘价应为最新（不绑定 bizDate）
  - 来源基金 = 各个 fund 下钻得到这个证券的数据（fund_drill_snapshot）
  - 产业链股票市值 = 全持仓口径
  - 表内勾稽关系合理

改造：
  - get_core_companies / get_stock_detail / get_industry_chains 改用
    services.full_holding_service.build_full_holding_for_user
    取代 FullHoldingSnapshot 表（避免与「分析-全持仓」口径不一致）
  - 估值字段 (pe/pb/ps/dy) 改用 fund_drill_snapshot 内的 *_dynamic 字段
    （这些已基于最新收盘价更新过）
  - 最新收盘价 = fund_drill_snapshot.current_price（最近 1 天）> 0
  - 来源基金 = 遍历 fund_drill_snapshot 同 (fund_code, stock_code) 聚合
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from models import (
    AnalystCompanyReport,
    AnalystIndustryChain,
    AnalystIndustryChainCompany,
    AShareFinancialSnapshot,
    HKShareFinancialSnapshot,
    Fund,
    FundDrillSnapshot,
    SecurityMaster,
)
from services.full_holding_service import build_full_holding_for_user
from services.analyst_parser import parse_all

logger = logging.getLogger(__name__)


# ============== helpers ==============

def _latest_price_drill(db: Session, stock_code: str) -> tuple[float | None, date | None]:
    """从 fund_drill_snapshot 取该 code 最新有数据的 current_price 与对应日期。

    优先 latest as_of_date（max），若 current_price 为空则用上一日。
    返回 (price, date) 或 (None, None)。
    """
    row = db.execute(text("""
        SELECT as_of_date, current_price FROM fund_drill_snapshot
        WHERE stock_code = :code AND current_price IS NOT NULL
        ORDER BY as_of_date DESC LIMIT 1
    """), {"code": stock_code}).first()
    if row and row[1] is not None:
        return float(row[1]), row[0]
    return None, None


def _latest_metrics_drill(db: Session, stock_code: str) -> dict[str, float | None]:
    """从 fund_drill_snapshot 取该 code 最新一行 pe/pb/ps/dy 字段。"""
    row = db.execute(text("""
        SELECT pe_ttm_dynamic, pb_mrq_dynamic, ps_ttm_dynamic, dividend_yield
        FROM fund_drill_snapshot
        WHERE stock_code = :code
        ORDER BY as_of_date DESC LIMIT 1
    """), {"code": stock_code}).first()
    if not row:
        return {"pe_ttm_dynamic": None, "pb_mrq_dynamic": None,
                "ps_ttm_dynamic": None, "dividend_yield": None}
    return {
        "pe_ttm_dynamic": float(row[0]) if row[0] is not None else None,
        "pb_mrq_dynamic": float(row[1]) if row[1] is not None else None,
        "ps_ttm_dynamic": float(row[2]) if row[2] is not None else None,
        "dividend_yield": float(row[3]) if row[3] is not None else None,
    }


def _latest_price_undrilled(db: Session, stock_code: str) -> tuple[float | None, date | None]:
    """非下钻证券的最新价：优先 Holding.price，其次 PriceCache 最新 close_px。"""
    from models import Holding, PriceCache
    h = db.query(Holding).filter(Holding.security_code == stock_code).first()
    if h and h.price is not None and h.price > 0:
        return float(h.price), None  # 没有日期字段
    pc = db.query(PriceCache).filter(
        PriceCache.stock_code == stock_code,
        PriceCache.close_px.isnot(None),
    ).order_by(PriceCache.trade_date.desc()).first()
    if pc and pc.close_px:
        return float(pc.close_px), pc.trade_date
    return None, None


def _latest_metrics_undrilled(db: Session, stock_code: str) -> dict[str, float | None]:
    """非下钻证券的估值：优先 AShare/HKShare FinancialSnapshot（最新 as_of_date）。"""
    pe = pb = ps = dy = None
    for Model in (AShareFinancialSnapshot, HKShareFinancialSnapshot):
        row = db.query(Model).filter(
            Model.stock_code == stock_code,
        ).order_by(Model.as_of_date.desc()).first()
        if row:
            pe = row.pe_ttm_dynamic if row.pe_ttm_dynamic is not None else row.pe_ttm
            pb = row.pb_mrq_dynamic if row.pb_mrq_dynamic is not None else row.pb_mrq
            ps = row.ps_ttm_dynamic if row.ps_ttm_dynamic is not None else row.ps_ttm
            dy = row.dividend_yield
            break
    return {"pe_ttm_dynamic": pe, "pb_mrq_dynamic": pb,
            "ps_ttm_dynamic": ps, "dividend_yield": dy}


def _resolve_stock_name(db: Session, stock_code: str) -> str | None:
    sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == stock_code).first()
    if sm and sm.security_name:
        return sm.security_name
    r = db.execute(text("""
        SELECT stock_name FROM fund_drill_snapshot
        WHERE stock_code = :c AND stock_name IS NOT NULL
        LIMIT 1
    """), {"c": stock_code}).first()
    if r and r[0]:
        return r[0]
    return None


# ============== 核心公司 ==============

def get_core_companies(db: Session, as_of_date: date, user_id: int | None = None) -> dict[str, Any]:
    """分析师核心公司 — 口径与「分析-全持仓」一致。

    数据源：
      - 全持仓 emv/weight: build_full_holding_for_user (与 /api/penetration/full-holding-table 同源)
      - PE/PB/PS/DY:        fund_drill_snapshot 最新一行（与下钻页一致）
      - 最新收盘:           fund_drill_snapshot.current_price 最新一行
      - 总分母 total:        build_full_holding_for_user 返回的 sum(est_market_value_cny)
    """
    # 1. 拿全持仓数据（与「分析-全持仓」同源）
    if user_id is None:
        return {"as_of_date": as_of_date.isoformat(), "total_amount_cny": 0,
                "companies": [], "error": "user_id required"}
    full = build_full_holding_for_user(db, as_of_date, user_id)

    # 2. 汇总 emv 字典（按 code 聚合：undrilled + drilled）
    emv_by_code: dict[str, float] = {}
    for r in full["undrilled"]:
        c = r.get("stock_code")
        emv = float(r.get("est_market_value_cny") or 0)
        if c and emv > 0:
            emv_by_code[c] = emv_by_code.get(c, 0.0) + emv
    for r in full["drilled"]:
        c = r.get("stock_code")
        emv = float(r.get("est_market_value_cny") or 0)
        if r.get("is_cash") or c == "CASH":
            continue
        if c and emv > 0:
            emv_by_code[c] = emv_by_code.get(c, 0.0) + emv

    # 3. 全持仓总市值（drilled + undrilled，不含 cash）
    total = sum(emv_by_code.values())

    # 4. 遍历分析师研报，组装每家公司
    reports = db.query(AnalystCompanyReport).order_by(AnalystCompanyReport.stock_code).all()
    companies = []
    for report in reports:
        code = report.stock_code
        amount = emv_by_code.get(code, 0.0)

        # 估值 + 最新价
        metrics = _latest_metrics_drill(db, code)
        latest_close, latest_close_date = _latest_price_drill(db, code)
        if latest_close is None:
            # 下钻无数据 → 可能是非下钻的 OF 基金本身
            metrics = _latest_metrics_undrilled(db, code)
            latest_close, latest_close_date = _latest_metrics_undrilled(db, code)[0], None
            # undrilled 没 date 字段，尝试 _latest_price_undrilled
            px, px_date = _latest_price_undrilled(db, code)
            if px is not None:
                latest_close = px
                latest_close_date = px_date

        stock_name = report.stock_name or _resolve_stock_name(db, code)

        portfolio = None
        if amount > 0:
            portfolio = {
                "weight_pct": round(amount / total * 100, 4) if total > 0 else 0.0,
                "amount_cny": round(amount, 4),
                "pe_ttm_dynamic": metrics["pe_ttm_dynamic"],
                "pb_mrq_dynamic": metrics["pb_mrq_dynamic"],
                "ps_ttm_dynamic": metrics["ps_ttm_dynamic"],
                "dividend_yield": metrics["dividend_yield"],
                "latest_close": latest_close,
                "latest_close_date": latest_close_date.isoformat() if latest_close_date else None,
            }

        companies.append({
            "stock_code": code,
            "stock_name": stock_name,
            "report_available": bool(report.section_1_market_focus or report.raw_text),
            "portfolio": portfolio,
            "report_sections": {
                "market_focus": report.section_1_market_focus,
                "core_competence": report.section_2_core_competence,
                "supply_demand": report.section_3_supply_demand,
                "marginal_change": report.section_4_marginal_change,
                "valuation": report.section_5_valuation,
                "risk": report.section_6_risk,
            },
        })

    companies.sort(key=lambda c: (c["portfolio"] or {}).get("weight_pct", 0), reverse=True)
    return {"as_of_date": as_of_date.isoformat(),
            "total_amount_cny": round(total, 2), "companies": companies}


# ============== 个股详情 ==============

def get_stock_detail(
    db: Session, stock_code: str, as_of_date: date, user_id: int | None = None
) -> dict[str, Any] | None:
    """个股详情：来源基金 + 估值 + 最新价（口径与全持仓/下钻一致）。"""
    if user_id is None:
        return None

    # 1. 全持仓 emv（与「分析-全持仓」同源）
    full = build_full_holding_for_user(db, as_of_date, user_id)

    emv_by_code: dict[str, float] = {}
    for r in full["undrilled"]:
        c = r.get("stock_code")
        emv = float(r.get("est_market_value_cny") or 0)
        if c and emv > 0:
            emv_by_code[c] = emv_by_code.get(c, 0.0) + emv
    for r in full["drilled"]:
        c = r.get("stock_code")
        emv = float(r.get("est_market_value_cny") or 0)
        if r.get("is_cash") or c == "CASH":
            continue
        if c and emv > 0:
            emv_by_code[c] = emv_by_code.get(c, 0.0) + emv

    amount = emv_by_code.get(stock_code, 0.0)
    total = sum(emv_by_code.values())

    # 2. 估值 + 最新价（drill 优先）
    metrics = _latest_metrics_drill(db, stock_code)
    latest_close, latest_close_date = _latest_price_drill(db, stock_code)
    if metrics["pe_ttm_dynamic"] is None and latest_close is None:
        metrics = _latest_metrics_undrilled(db, stock_code)
        latest_close, latest_close_date = _latest_price_undrilled(db, stock_code)

    # 3. 来源基金（fund_drill_snapshot 取每个 fund 最新一行 shares_eq，
    #    乘以用户持仓中该 fund 的 quantity 折算出真实 user-level shares + amount）
    # 用 DISTINCT ON 拿每 fund 最新行
    source_rows = db.execute(text("""
        SELECT DISTINCT ON (fund_code) fund_code, shares_equivalent, as_of_date
        FROM fund_drill_snapshot
        WHERE stock_code = :code AND shares_equivalent IS NOT NULL
        ORDER BY fund_code, as_of_date DESC
    """), {"code": stock_code}).fetchall()

    # user 2 该 fund 的持仓 quantity（聚合）
    from models import Holding
    fund_qty_map: dict[str, float] = {}
    for h in db.query(Holding).filter(
        Holding.user_id == user_id,
        Holding.security_code.in_([r[0] for r in source_rows])
    ).all():
        fund_qty_map[h.security_code] = fund_qty_map.get(h.security_code, 0.0) + (h.quantity or 0.0)

    # 该 fund 在 user 2 持仓中的 emv（drillable 基金从 build_full_holding 排除，
    # 直接用 Holding.amount_cny 聚合 — 口径同 user 2 估值表）
    fund_emv_map: dict[str, float] = {}
    for h in db.query(Holding).filter(
        Holding.user_id == user_id,
        Holding.security_code.in_([r[0] for r in source_rows])
    ).all():
        fund_emv_map[h.security_code] = fund_emv_map.get(h.security_code, 0.0) + (h.amount_cny or 0.0)

    # 取最新 current_price_cny 一次性（drill 最新行）
    latest_price, _ = _latest_price_drill(db, stock_code)

    source_funds = []
    for r in source_rows:
        fund_code = r[0]
        shares_eq = float(r[1] or 0.0)
        user_qty = fund_qty_map.get(fund_code, 0.0)
        # 真实 user-level 300308 股数 = user_qty × shares_eq
        equivalent_shares = user_qty * shares_eq if user_qty > 0 else None
        fund_amount = (latest_price * equivalent_shares) if (latest_price and equivalent_shares) else 0.0
        holding_total = fund_emv_map.get(fund_code, 0.0)

        # 查 fund 名称
        f = db.query(Fund).filter(Fund.code == fund_code).first()
        fund_name = f.name if f else fund_code

        source_funds.append({
            "fund_code": fund_code,
            "fund_name": fund_name,
            "equivalent_shares": round(equivalent_shares, 4) if equivalent_shares is not None else None,
            "fund_amount_cny": round(fund_amount, 4),
            "ratio_in_portfolio_pct": round(fund_amount / total * 100, 4) if total > 0 else 0.0,
            "ratio_in_fund_pct": round(fund_amount / holding_total * 100, 4) if holding_total > 0 else None,
        })

    # 按 fund_amount 排序
    source_funds.sort(key=lambda x: x["fund_amount_cny"], reverse=True)

    # 5. 研报 sections
    report = db.query(AnalystCompanyReport).filter(
        AnalystCompanyReport.stock_code == stock_code).first()
    report_sections = None
    if report:
        report_sections = {
            "market_focus": report.section_1_market_focus,
            "core_competence": report.section_2_core_competence,
            "supply_demand": report.section_3_supply_demand,
            "marginal_change": report.section_4_marginal_change,
            "valuation": report.section_5_valuation,
            "risk": report.section_6_risk,
        }

    stock_name = report.stock_name if report else None
    if not stock_name:
        stock_name = _resolve_stock_name(db, stock_code)

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "as_of_date": as_of_date.isoformat(),
        "portfolio_weight_pct": round(amount / total * 100, 4) if total > 0 else 0.0,
        "amount_cny": round(amount, 4),
        "pe_ttm_dynamic": metrics["pe_ttm_dynamic"],
        "pb_mrq_dynamic": metrics["pb_mrq_dynamic"],
        "ps_ttm_dynamic": metrics["ps_ttm_dynamic"],
        "dividend_yield": metrics["dividend_yield"],
        "latest_close": latest_close,
        "latest_close_date": latest_close_date.isoformat() if latest_close_date else None,
        "source_funds": source_funds,
        "report_sections": report_sections,
    }


# ============== 产业链 ==============

_CHAIN_ORDER = {"上游": 0, "中游": 1, "下游": 2}


def _chain_position_sort_key(chain_position: str) -> int:
    if not chain_position:
        return 99
    for prefix, order in _CHAIN_ORDER.items():
        if chain_position.startswith(prefix):
            return order
    return 99


def get_industry_chains(
    db: Session, as_of_date: date, user_id: int | None = None
) -> dict[str, Any]:
    """产业链卡片 — 口径与「分析-全持仓」一致。

    股票市值 = emv_by_code[code]（来自 build_full_holding_for_user）
    PE/PB/PS = fund_drill_snapshot 最新行的 *_dynamic
    """
    if user_id is None:
        return {"as_of_date": as_of_date.isoformat(), "total_amount_cny": 0,
                "chains": [], "error": "user_id required"}

    # 1. 全持仓 emv（同全持仓页）
    full = build_full_holding_for_user(db, as_of_date, user_id)
    emv_by_code: dict[str, float] = {}
    for r in full["undrilled"]:
        c = r.get("stock_code")
        emv = float(r.get("est_market_value_cny") or 0)
        if c and emv > 0:
            emv_by_code[c] = emv_by_code.get(c, 0.0) + emv
    for r in full["drilled"]:
        c = r.get("stock_code")
        emv = float(r.get("est_market_value_cny") or 0)
        if r.get("is_cash") or c == "CASH":
            continue
        if c and emv > 0:
            emv_by_code[c] = emv_by_code.get(c, 0.0) + emv

    total = sum(emv_by_code.values())
    held_codes = set(emv_by_code.keys())

    chains_out = []
    chains = db.query(AnalystIndustryChain).order_by(AnalystIndustryChain.chain_name).all()

    for chain in chains:
        companies = (
            db.query(AnalystIndustryChainCompany)
            .filter(AnalystIndustryChainCompany.chain_name == chain.chain_name)
            .order_by(
                AnalystIndustryChainCompany.chain_position,
                AnalystIndustryChainCompany.relevance_stars.desc(),
            )
            .all()
        )

        in_portfolio = []
        for c in companies:
            if not c.stock_code or c.stock_code not in held_codes:
                continue
            amount = emv_by_code.get(c.stock_code, 0.0)

            # 估值（drill 优先）
            metrics = _latest_metrics_drill(db, c.stock_code)
            latest_close, latest_close_date = _latest_price_drill(db, c.stock_code)
            if metrics["pe_ttm_dynamic"] is None:
                metrics = _latest_metrics_undrilled(db, c.stock_code)
                px, px_date = _latest_price_undrilled(db, c.stock_code)
                if latest_close is None and px is not None:
                    latest_close = px
                    latest_close_date = px_date

            in_portfolio.append({
                "company_name": c.company_name,
                "stock_code": c.stock_code,
                "chain_position": c.chain_position,
                "sub_segment": c.sub_segment,
                "relevance_stars": c.relevance_stars,
                "relevance_reason": c.relevance_reason,
                "latest_progress": c.latest_progress,
                "order_visibility": c.order_visibility,
                "earnings_elasticity": c.earnings_elasticity,
                "customer_onboarding": c.customer_onboarding,
                "extra_json": c.extra_json,
                "portfolio_weight_pct": round(amount / total * 100, 4) if total > 0 else 0.0,
                "amount_cny": round(amount, 4),
                "pe_ttm_dynamic": metrics["pe_ttm_dynamic"],
                "pb_mrq_dynamic": metrics["pb_mrq_dynamic"],
                "ps_ttm_dynamic": metrics["ps_ttm_dynamic"],
                "dividend_yield": metrics["dividend_yield"],
                "latest_close": latest_close,
                "latest_close_date": latest_close_date.isoformat() if latest_close_date else None,
            })

        in_portfolio.sort(
            key=lambda x: (_chain_position_sort_key(x["chain_position"]), -(x["relevance_stars"] or 0))
        )

        # 产业链组合指标
        stock_codes = [c["stock_code"] for c in in_portfolio]
        chain_emv = sum(emv_by_code.get(c, 0.0) for c in stock_codes)
        virt_pe = virt_pb = virt_ps = virt_dy = 0.0
        for c in in_portfolio:
            amt = c["amount_cny"]
            if amt <= 0:
                continue
            if c["pe_ttm_dynamic"] and c["pe_ttm_dynamic"] > 0:
                virt_pe += amt / c["pe_ttm_dynamic"]
            if c["pb_mrq_dynamic"] and c["pb_mrq_dynamic"] > 0:
                virt_pb += amt / c["pb_mrq_dynamic"]
            if c["ps_ttm_dynamic"] and c["ps_ttm_dynamic"] > 0:
                virt_ps += amt / c["ps_ttm_dynamic"]
            if c["dividend_yield"] and c["dividend_yield"] > 0:
                virt_dy += amt * c["dividend_yield"]

        chains_out.append({
            "chain_name": chain.chain_name,
            "narrative_md": chain.narrative_md,
            "company_count": len(in_portfolio),
            "companies_in_portfolio": in_portfolio,
            "portfolio_metrics": {
                "weight_pct": round(chain_emv / total * 100, 4) if total > 0 else 0.0,
                "amount_cny": round(chain_emv, 4),
                "pe_weighted": round(chain_emv / virt_pe, 4) if virt_pe > 0 else None,
                "pb_weighted": round(chain_emv / virt_pb, 4) if virt_pb > 0 else None,
                "ps_weighted": round(chain_emv / virt_ps, 4) if virt_ps > 0 else None,
                "dividend_yield_weighted": round(virt_dy / chain_emv * 100, 4) if chain_emv > 0 and virt_dy > 0 else None,
                "stock_count": len(in_portfolio),
            },
        })

    return {"as_of_date": as_of_date.isoformat(), "total_amount_cny": round(total, 2), "chains": chains_out}


# ============== 入库（保持不变）==============

def ingest_analyst_data(db: Session, researcher_dir: str | None = None) -> dict[str, Any]:
    parsed = parse_all(researcher_dir)

    company_summary = {"parsed": 0, "errors": 0}
    for item in parsed["company_reports"]:
        if not item.get("success"):
            company_summary["errors"] += 1
            continue
        report = (
            db.query(AnalystCompanyReport)
            .filter(AnalystCompanyReport.stock_code == item["stock_code"])
            .first()
        )
        if report is None:
            report = AnalystCompanyReport(stock_code=item["stock_code"])
            db.add(report)
        for field in (
            "stock_name", "exchange", "section_1_market_focus",
            "section_2_core_competence", "section_3_supply_demand",
            "section_4_marginal_change", "section_5_valuation",
            "section_6_risk", "raw_text", "source_file", "parsed_at",
        ):
            setattr(report, field, item.get(field))
        report.updated_at = item.get("parsed_at")
        company_summary["parsed"] += 1

    chain_summary = {"parsed": 0, "errors": 0}
    for item in parsed["chain_summaries"]:
        if not item.get("success"):
            chain_summary["errors"] += 1
            continue
        chain = (
            db.query(AnalystIndustryChain)
            .filter(AnalystIndustryChain.chain_name == item["chain_name"])
            .first()
        )
        if chain is None:
            chain = AnalystIndustryChain(chain_name=item["chain_name"])
            db.add(chain)
        chain.narrative_md = item.get("narrative_md")
        chain.source_file = item.get("source_file")
        chain.parsed_at = item.get("parsed_at")
        chain.updated_at = item.get("parsed_at")
        chain_summary["parsed"] += 1

    row_summary = {"parsed": 0, "errors": 0}
    for item in parsed["chain_company_lists"]:
        chain_name = item["chain_name"]
        db.query(AnalystIndustryChainCompany).filter(
            AnalystIndustryChainCompany.chain_name == chain_name
        ).delete()
        for row in item.get("rows", []):
            db.add(AnalystIndustryChainCompany(**row))
        row_summary["parsed"] += len(item.get("rows", []))
        if item.get("errors"):
            row_summary["errors"] += len(item["errors"])

    db.commit()
    return {
        "status": "ok",
        "company_reports": company_summary,
        "industry_chains": chain_summary,
        "company_list_rows": row_summary,
    }