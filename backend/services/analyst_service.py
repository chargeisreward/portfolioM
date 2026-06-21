"""分析师页面后端业务逻辑：入库、查询、聚合。"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from models import (
    AnalystCompanyReport,
    AnalystIndustryChain,
    AnalystIndustryChainCompany,
    AShareFinancialSnapshot,
    Csi300ConstituentSnapshot,
    FullHoldingSnapshot,
    Fund,
    HKShareFinancialSnapshot,
    PenetrationSnapshot,
    PriceCache,
    SecurityMaster,
)
from services.analyst_parser import parse_all


def _resolve_stock_name(db: Session, stock_code: str) -> str | None:
    """优先 SecurityMaster，其次 FullHoldingSnapshot。"""
    sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == stock_code).first()
    if sm and sm.security_name:
        return sm.security_name
    fhs = (
        db.query(FullHoldingSnapshot)
        .filter(FullHoldingSnapshot.stock_code == stock_code)
        .first()
    )
    if fhs and fhs.stock_name:
        return fhs.stock_name
    return None


def _total_portfolio_amount(db: Session, as_of_date: date) -> float:
    total = (
        db.query(func.sum(FullHoldingSnapshot.amount_cny))
        .filter(FullHoldingSnapshot.as_of_date == as_of_date)
        .scalar()
    )
    return float(total or 0.0)


def _total_drilled_amount(db: Session, as_of_date: date) -> float:
    """下钻口径总金额：drilled_fund + direct_stock 的 amount_cny 合计。"""
    METRIC_SOURCES = ("drilled_fund", "direct_stock")
    total = (
        db.query(func.sum(FullHoldingSnapshot.amount_cny))
        .filter(
            FullHoldingSnapshot.as_of_date == as_of_date,
            FullHoldingSnapshot.source_type.in_(METRIC_SOURCES),
        )
        .scalar()
    )
    return float(total or 0.0)


def _get_snapshot_price(db: Session, stock_code: str, as_of_date: date) -> tuple[float | None, date | None]:
    """优先 A/H 财务快照的 current_price/current_price_date，缺失回退 PriceCache。"""
    for Model in (AShareFinancialSnapshot, HKShareFinancialSnapshot):
        row = (
            db.query(Model)
            .filter(Model.stock_code == stock_code, Model.as_of_date == as_of_date)
            .first()
        )
        if row and row.current_price:
            return row.current_price, row.current_price_date

    # 后缀无关再查一次（如 688041 和 688041.SH）
    code_no_suffix = stock_code.split(".")[0]
    for Model in (AShareFinancialSnapshot, HKShareFinancialSnapshot):
        row = (
            db.query(Model)
            .filter(
                Model.as_of_date == as_of_date,
                (Model.stock_code == code_no_suffix) | (Model.stock_code == stock_code),
            )
            .order_by(func.coalesce(Model.current_price_date, Model.as_of_date).desc())
            .first()
        )
        if row and row.current_price:
            return row.current_price, row.current_price_date

    price_row = (
        db.query(PriceCache)
        .filter(PriceCache.stock_code == stock_code)
        .order_by(PriceCache.trade_date.desc())
        .first()
    )
    if price_row and price_row.close_px:
        return price_row.close_px, price_row.trade_date
    return None, None


def _get_snapshot_metrics(db: Session, stock_code: str, as_of_date: date) -> dict[str, float | None]:
    pe = pb = ps = None
    for Model in (AShareFinancialSnapshot, HKShareFinancialSnapshot):
        row = (
            db.query(Model)
            .filter(Model.stock_code == stock_code, Model.as_of_date == as_of_date)
            .first()
        )
        if row:
            pe = row.pe_ttm_dynamic if row.pe_ttm_dynamic is not None else row.pe_ttm
            pb = row.pb_mrq_dynamic if row.pb_mrq_dynamic is not None else row.pb_mrq
            ps = row.ps_ttm_dynamic if row.ps_ttm_dynamic is not None else row.ps_ttm
            break
    return {"pe_ttm_dynamic": pe, "pb_mrq_dynamic": pb, "ps_ttm_dynamic": ps}


# -----------------------------------------------------------------------------
# 入库
# -----------------------------------------------------------------------------

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
        if not item.get("success"):
            row_summary["errors"] += 1
            continue
        chain_name = item["chain_name"]
        # 全量替换：按 chain_name 删除旧行
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


# -----------------------------------------------------------------------------
# 核心公司列表
# -----------------------------------------------------------------------------

def get_core_companies(db: Session, as_of_date: date) -> dict[str, Any]:
    reports = db.query(AnalystCompanyReport).order_by(AnalystCompanyReport.stock_code).all()
    total = _total_portfolio_amount(db, as_of_date)
    companies = []

    for report in reports:
        amount = (
            db.query(func.sum(FullHoldingSnapshot.amount_cny))
            .filter(
                FullHoldingSnapshot.as_of_date == as_of_date,
                FullHoldingSnapshot.stock_code == report.stock_code,
            )
            .scalar()
        ) or 0.0

        metrics = _get_snapshot_metrics(db, report.stock_code, as_of_date)
        latest_close, latest_close_date = _get_snapshot_price(db, report.stock_code, as_of_date)

        stock_name = report.stock_name or _resolve_stock_name(db, report.stock_code)

        portfolio = None
        if amount > 0:
            portfolio = {
                "weight_pct": round(amount / total * 100, 4) if total > 0 else 0.0,
                "amount_cny": round(amount, 4),
                "pe_ttm_dynamic": metrics["pe_ttm_dynamic"],
                "pb_mrq_dynamic": metrics["pb_mrq_dynamic"],
                "ps_ttm_dynamic": metrics["ps_ttm_dynamic"],
                "latest_close": latest_close,
                "latest_close_date": latest_close_date.isoformat() if latest_close_date else None,
            }

        companies.append({
            "stock_code": report.stock_code,
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

    return {"as_of_date": as_of_date.isoformat(), "total_amount_cny": total, "companies": companies}


# -----------------------------------------------------------------------------
# 个股详情（来源基金 + 约当数量）
# -----------------------------------------------------------------------------

def get_stock_detail(db: Session, stock_code: str, as_of_date: date) -> dict[str, Any] | None:
    report = (
        db.query(AnalystCompanyReport)
        .filter(AnalystCompanyReport.stock_code == stock_code)
        .first()
    )

    amount = (
        db.query(func.sum(FullHoldingSnapshot.amount_cny))
        .filter(
            FullHoldingSnapshot.as_of_date == as_of_date,
            FullHoldingSnapshot.stock_code == stock_code,
        )
        .scalar()
    ) or 0.0

    total = _total_portfolio_amount(db, as_of_date)
    metrics = _get_snapshot_metrics(db, stock_code, as_of_date)
    latest_close, latest_close_date = _get_snapshot_price(db, stock_code, as_of_date)

    # 来源基金：用 PenetrationSnapshot 按 holding_code 聚合（动态金额）
    source_rows = (
        db.query(
            PenetrationSnapshot.holding_code,
            PenetrationSnapshot.holding_name,
            func.sum(PenetrationSnapshot.amount_cny_dynamic).label("amount_dynamic"),
            func.max(PenetrationSnapshot.holding_amount_cny).label("holding_amount_cny"),
        )
        .filter(
            PenetrationSnapshot.as_of_date == as_of_date,
            PenetrationSnapshot.stock_code == stock_code,
        )
        .group_by(PenetrationSnapshot.holding_code, PenetrationSnapshot.holding_name)
        .all()
    )

    # 预载基金名
    fund_codes = {r.holding_code for r in source_rows if r.holding_code}
    fund_names = {
        f.code: f.name
        for f in db.query(Fund).filter(Fund.code.in_(fund_codes)).all()
    }

    source_funds = []
    for r in source_rows:
        fund_code = r.holding_code
        fund_amount = r.amount_dynamic or 0.0
        equivalent_shares = (
            fund_amount / latest_close
            if latest_close and latest_close > 0 else None
        )
        holding_total = r.holding_amount_cny or 0.0
        source_funds.append({
            "fund_code": fund_code,
            "fund_name": fund_names.get(fund_code) or r.holding_name or fund_code,
            "equivalent_shares": round(equivalent_shares, 4) if equivalent_shares else None,
            "fund_amount_cny": round(fund_amount, 4),
            "ratio_in_portfolio_pct": round(fund_amount / total * 100, 4) if total > 0 else 0.0,
            "ratio_in_fund_pct": round(fund_amount / holding_total * 100, 4)
            if holding_total > 0 else None,
        })

    source_funds.sort(key=lambda x: x["fund_amount_cny"], reverse=True)

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

    stock_name = None
    if report:
        stock_name = report.stock_name or _resolve_stock_name(db, stock_code)

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "as_of_date": as_of_date.isoformat(),
        "portfolio_weight_pct": round(amount / total * 100, 4) if total > 0 else 0.0,
        "amount_cny": round(amount, 4),
        "pe_ttm_dynamic": metrics["pe_ttm_dynamic"],
        "pb_mrq_dynamic": metrics["pb_mrq_dynamic"],
        "ps_ttm_dynamic": metrics["ps_ttm_dynamic"],
        "latest_close": latest_close,
        "latest_close_date": latest_close_date.isoformat() if latest_close_date else None,
        "source_funds": source_funds,
        "report_sections": report_sections,
    }


# -----------------------------------------------------------------------------
# 产业链列表（仅 portfolio 持仓公司）
# -----------------------------------------------------------------------------

_CHAIN_ORDER = {"上游": 0, "中游": 1, "下游": 2}


def _compute_chain_portfolio_metrics(
    db: Session,
    as_of_date: date,
    stock_codes: list[str],
    drilled_total: float,
) -> dict[str, Any]:
    """计算产业链内当前 portfolio 下钻持仓股票的合并指标（下钻口径）。

    权重 = 产业链内下钻持仓合计金额 / 全部下钻证券合计金额。
    PE/PB/PS 以各股下钻后金额做虚拟盈利加权。
    """
    METRIC_SOURCES = ("drilled_fund", "direct_stock")
    rows = (
        db.query(
            FullHoldingSnapshot.stock_code,
            func.sum(FullHoldingSnapshot.amount_cny).label("amount"),
            func.max(FullHoldingSnapshot.pe_ttm_dynamic).label("pe_d"),
            func.max(FullHoldingSnapshot.pb_mrq_dynamic).label("pb_d"),
            func.max(FullHoldingSnapshot.ps_ttm_dynamic).label("ps_d"),
        )
        .filter(
            FullHoldingSnapshot.as_of_date == as_of_date,
            FullHoldingSnapshot.source_type.in_(METRIC_SOURCES),
            FullHoldingSnapshot.stock_code.in_(stock_codes),
        )
        .group_by(FullHoldingSnapshot.stock_code)
        .all()
    )
    amount = 0.0
    virt_pe = virt_pb = virt_ps = 0.0
    for r in rows:
        amt = r.amount or 0.0
        amount += amt
        if amt > 0:
            if r.pe_d and r.pe_d > 0:
                virt_pe += amt / r.pe_d
            if r.pb_d and r.pb_d > 0:
                virt_pb += amt / r.pb_d
            if r.ps_d and r.ps_d > 0:
                virt_ps += amt / r.ps_d
    return {
        "weight_pct": round(amount / drilled_total * 100, 4) if drilled_total > 0 else 0.0,
        "amount_cny": round(amount, 4),
        "pe_weighted": round(amount / virt_pe, 4) if virt_pe else None,
        "pb_weighted": round(amount / virt_pb, 4) if virt_pb else None,
        "ps_weighted": round(amount / virt_ps, 4) if virt_ps else None,
        "stock_count": len(rows),
    }


def _compute_chain_csi300_metrics(
    db: Session,
    as_of_date: date,
    stock_codes: list[str],
) -> dict[str, Any]:
    """计算产业链内属于沪深300成分股的指标。

    只取产业链中属于沪深300成分股的标的，直接用其原始指数权重做虚拟盈利加权 PE/PB/PS；
    规模（amount_cny）不填。
    """
    rows = (
        db.query(
            Csi300ConstituentSnapshot.stock_code,
            func.max(Csi300ConstituentSnapshot.weight).label("weight"),
            func.max(Csi300ConstituentSnapshot.pe_ttm_dynamic).label("pe_d"),
            func.max(Csi300ConstituentSnapshot.pb_mrq_dynamic).label("pb_d"),
            func.max(Csi300ConstituentSnapshot.ps_ttm_dynamic).label("ps_d"),
        )
        .filter(
            Csi300ConstituentSnapshot.as_of_date == as_of_date,
            Csi300ConstituentSnapshot.stock_code.in_(stock_codes),
        )
        .group_by(Csi300ConstituentSnapshot.stock_code)
        .all()
    )

    total_weight = 0.0
    virt_pe = virt_pb = virt_ps = 0.0
    for r in rows:
        w = r.weight or 0.0
        if w <= 0:
            continue

        pe = r.pe_d
        pb = r.pb_d
        ps = r.ps_d
        if pe is None or pb is None or ps is None:
            metrics = _get_snapshot_metrics(db, r.stock_code, as_of_date)
            pe = pe if pe is not None else metrics["pe_ttm_dynamic"]
            pb = pb if pb is not None else metrics["pb_mrq_dynamic"]
            ps = ps if ps is not None else metrics["ps_ttm_dynamic"]

        total_weight += w
        if pe and pe > 0:
            virt_pe += w / pe
        if pb and pb > 0:
            virt_pb += w / pb
        if ps and ps > 0:
            virt_ps += w / ps

    return {
        "weight_pct": round(total_weight, 4),
        "amount_cny": None,
        "pe_weighted": round(total_weight / virt_pe, 4) if virt_pe else None,
        "pb_weighted": round(total_weight / virt_pb, 4) if virt_pb else None,
        "ps_weighted": round(total_weight / virt_ps, 4) if virt_ps else None,
        "stock_count": len(rows),
    }


def _chain_position_sort_key(chain_position: str) -> int:
    if not chain_position:
        return 99
    for prefix, order in _CHAIN_ORDER.items():
        if chain_position.startswith(prefix):
            return order
    return 99


def get_industry_chains(db: Session, as_of_date: date) -> dict[str, Any]:
    print(f"[DEBUG] get_industry_chains called for {as_of_date}")
    total = _total_portfolio_amount(db, as_of_date)
    drilled_total = _total_drilled_amount(db, as_of_date)

    # 当前 portfolio 中所有持仓股票代码
    held_codes = {
        row[0]
        for row in db.query(FullHoldingSnapshot.stock_code)
        .filter(FullHoldingSnapshot.as_of_date == as_of_date)
        .distinct()
        .all()
    }

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
            amount = (
                db.query(func.sum(FullHoldingSnapshot.amount_cny))
                .filter(
                    FullHoldingSnapshot.as_of_date == as_of_date,
                    FullHoldingSnapshot.stock_code == c.stock_code,
                )
                .scalar()
            ) or 0.0
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
            })

        in_portfolio.sort(
            key=lambda x: (_chain_position_sort_key(x["chain_position"]), -(x["relevance_stars"] or 0))
        )

        stock_codes = [c["stock_code"] for c in in_portfolio]
        portfolio_metrics = _compute_chain_portfolio_metrics(db, as_of_date, stock_codes, drilled_total)
        csi300_metrics = _compute_chain_csi300_metrics(db, as_of_date, stock_codes)

        chains_out.append({
            "chain_name": chain.chain_name,
            "narrative_md": chain.narrative_md,
            "company_count": len(in_portfolio),
            "companies_in_portfolio": in_portfolio,
            "portfolio_metrics": portfolio_metrics,
            "csi300_metrics": csi300_metrics,
        })

    return {"as_of_date": as_of_date.isoformat(), "total_amount_cny": total, "chains": chains_out}
