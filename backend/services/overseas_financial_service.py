"""海外市场财务数据 service — yfinance 获取 + upsert。"""
from __future__ import annotations

import logging
import time
from datetime import date

from sqlalchemy.orm import Session

from crawlers.price_data import fetch_yfinance_info, _infer_market_from_ticker
from models import OverseasShareFinancialSnapshot

logger = logging.getLogger(__name__)


def upsert_overseas_financial(db: Session, data: dict) -> dict:
    """单条写入海外财务数据（upsert）。

    Args:
        db: 数据库会话
        data: {stock_code, stock_name, market, pe_ttm, pb_mrq, ps_ttm,
               dividend_yield, market_cap, eps_fy1, sector, industry, as_of_date}

    Returns: {status, market}
    """
    stock_code = data.get("stock_code", "")
    if not stock_code:
        raise ValueError("stock_code 不能为空")

    market = data.get("market")
    if not market:
        market = _infer_market_from_ticker(stock_code)
    as_of = data.get("as_of_date")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)

    existing = db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == stock_code,
        OverseasShareFinancialSnapshot.as_of_date == as_of,
    ).first()

    fields = (
        "stock_name", "market", "pe_ttm", "pb_mrq", "ps_ttm",
        "dividend_yield", "market_cap", "eps_fy1",
        "sector", "industry", "source",
    )

    if existing:
        for f in fields:
            if f in data:
                setattr(existing, f, data[f])
        # 更新 dynamic（当 baseline 更新时 dynamic 也更新，spec §8.3）
        if "pe_ttm" in data:
            existing.pe_ttm_dynamic = data["pe_ttm"]
        if "pb_mrq" in data:
            existing.pb_mrq_dynamic = data["pb_mrq"]
        if "ps_ttm" in data:
            existing.ps_ttm_dynamic = data["ps_ttm"]
    else:
        # 估值是市场公共数据，不传 user_id（DB nullable 后 NULL 落库 — 2026-06-25）
        kwargs = {"stock_code": stock_code, "as_of_date": as_of, "market": market}
        for f in fields:
            if f in data:
                kwargs[f] = data[f]
        # 首次写入时 dynamic = baseline（spec §8.3）
        pe_ttm = data.get("pe_ttm")
        pb_mrq = data.get("pb_mrq")
        ps_ttm = data.get("ps_ttm")
        kwargs["pe_ttm_dynamic"] = pe_ttm
        kwargs["pb_mrq_dynamic"] = pb_mrq
        kwargs["ps_ttm_dynamic"] = ps_ttm
        # source 默认 yfinance
        if "source" not in kwargs or not kwargs.get("source"):
            kwargs["source"] = "yfinance"
        snap = OverseasShareFinancialSnapshot(**kwargs)
        db.add(snap)

    db.commit()
    return {"status": "ok", "market": market}


def fetch_and_store_overseas_financials(db: Session, stock_codes: list[str], as_of_date: date) -> dict:
    """批量从 yfinance 获取海外财务数据并存储。

    Args:
        db: 数据库会话
        stock_codes: yfinance ticker 列表
        as_of_date: 截止日期

    Returns: {status, fetched, stored, errors}
    """
    fetched = 0
    stored = 0
    errors = []

    for code in stock_codes:
        try:
            yf_info = fetch_yfinance_info(code)
            if not yf_info:
                errors.append(f"{code}: yfinance 返回空")
                continue

            fetched += 1

            data = {
                "stock_code": code,
                "stock_name": yf_info.get("name", ""),
                "market": yf_info.get("market", "US"),
                "pe_ttm": yf_info.get("pe_ttm"),
                "pb_mrq": yf_info.get("pb_mrq"),
                "ps_ttm": yf_info.get("ps_ttm"),
                "dividend_yield": yf_info.get("dividend_yield"),
                "market_cap": yf_info.get("market_cap_b"),
                "eps_fy1": yf_info.get("eps_fy1"),
                "sector": yf_info.get("sector"),
                "industry": yf_info.get("industry"),
                "as_of_date": as_of_date,
            }

            upsert_overseas_financial(db, data)
            stored += 1

            time.sleep(3)

        except Exception as e:
            errors.append(f"{code}: {str(e)}")
            logger.warning("获取海外财务数据失败 [%s]: %s", code, e)
            continue

    return {"status": "ok", "fetched": fetched, "stored": stored, "errors": errors}
