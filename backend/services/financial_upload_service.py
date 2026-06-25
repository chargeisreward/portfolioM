"""财务数据上传 service — Excel 导入 + 单条写入。

依赖：AShareFinancialSnapshot, HKShareFinancialSnapshot
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from models import AShareFinancialSnapshot, HKShareFinancialSnapshot

logger = logging.getLogger(__name__)

# 模块级 import，便于测试 mock（monkeypatch.setattr 本模块的 import_a_share）
from scripts.import_a_share_financials import import_a_share  # noqa: E402
from scripts.import_hk_share_financials import import_hk_share  # noqa: E402


def _detect_market(stock_code: str) -> str:
    """根据代码后缀判断市场。

    Returns: "CN" / "HK"
    Raises: ValueError 如果不支持的代码后缀
    """
    code = stock_code.upper()
    if code.endswith(".SH") or code.endswith(".SZ"):
        return "CN"
    if code.endswith(".HK"):
        return "HK"
    raise ValueError(f"不支持的代码后缀: {stock_code}（仅支持 .SH/.SZ/.HK）")


def upsert_financial_single(db: Session, data: dict) -> dict:
    """单条写入财务数据（upsert）。

    Args:
        db: 数据库会话
        data: {stock_code, stock_name, pe_ttm, pb_mrq, ps_ttm, dividend_yield,
               market_cap, eps_fy1, eps_fy2, industry_sw, as_of_date, ...}

    Returns: {status, market}
    """
    stock_code = data.get("stock_code", "")
    if not stock_code:
        raise ValueError("stock_code 不能为空")

    market = _detect_market(stock_code)
    as_of = data.get("as_of_date")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)

    model = AShareFinancialSnapshot if market == "CN" else HKShareFinancialSnapshot

    # 查找已存在记录（同 stock_code + as_of_date）
    existing = db.query(model).filter(
        model.stock_code == stock_code,
        model.as_of_date == as_of,
    ).first()

    # 可写入的字段
    fields = (
        "stock_name", "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield",
        "market_cap", "eps_fy1", "eps_fy2",
        "swy_l1", "swy_l2", "swy_l3", "swy_l4",
        "csi_l1", "csi_l2", "csi_l3", "csi_l4",
        "se_l1", "se_l2", "se_l3", "se_l4",
        "industry_sw",
    )

    if existing:
        for f in fields:
            if f in data:
                setattr(existing, f, data[f])
    else:
        kwargs = {"stock_code": stock_code, "as_of_date": as_of, "user_id": 1}
        for f in fields:
            if f in data:
                kwargs[f] = data[f]
        snap = model(**kwargs)
        db.add(snap)

    db.commit()
    return {"status": "ok", "market": market}


def import_excel_batch(db: Session, excel_path: str, market: str, as_of_date: date) -> dict:
    """Excel 批量导入财务数据。

    复用现有 import_a_share_financials / import_hk_share_financials 逻辑。

    Args:
        db: 数据库会话
        excel_path: Excel 文件路径
        market: "CN" / "HK"
        as_of_date: 截止日期

    Returns: {status, imported, errors}
    """
    if market not in ("CN", "HK"):
        return {"status": "error", "imported": 0, "errors": [f"不支持的市场: {market}"]}

    try:
        if market == "CN":
            report = import_a_share(db, as_of_date, Path(excel_path))
        else:
            report = import_hk_share(db, as_of_date, Path(excel_path))
        return {
            "status": "ok",
            "imported": report.rows_inserted,
            "errors": report.errors,
        }
    except Exception as e:
        logger.exception("Excel 导入失败")
        return {"status": "error", "imported": 0, "errors": [str(e)]}
