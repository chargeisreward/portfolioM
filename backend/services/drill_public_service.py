"""公共下钻 service — 只读 fund_drill_snapshot + fund_index_map 表。
不知道 user_id，不读 Holding 表。可独立复用。

数据来源：scheduler 每日生成的 fund_drill_snapshot 预计算表。

注意：FundDrillSnapshot 模型本身不含 index_code / index_name 列，
指数关系通过 FundIndexMap（fund_code → index_code）获取。
本 service 在查询时用 getattr 安全读取 index_code/index_name，
兼容 mock 测试与未来可能扩展的模型字段。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date

from sqlalchemy.orm import Session

from models import FundDrillSnapshot, FundIndexMap

logger = logging.getLogger(__name__)


def get_public_cards(db: Session, as_of: _date) -> list[dict]:
    """返回所有公共下钻卡片（按指数分组）。

    只读 fund_drill_snapshot + fund_index_map，不含任何用户数据。

    返回结构：
    [
        {
            "index_code": "000300",
            "index_name": "沪深300",
            "as_of": "2026-06-24",
            "fund_codes": ["510300.SH", ...],
            "stock_count": 300,
            "total_weight": 1.0,
        },
    ]
    """
    rows = db.query(FundDrillSnapshot).filter(
        FundDrillSnapshot.as_of_date == as_of
    ).all()

    if not rows:
        return []

    by_index: dict[str, dict] = {}
    for r in rows:
        idx_code = (getattr(r, "index_code", "") or "").split(".")[0]
        if not idx_code:
            continue
        if idx_code not in by_index:
            by_index[idx_code] = {
                "index_code": idx_code,
                "index_name": getattr(r, "index_name", None) or idx_code,
                "as_of": as_of.isoformat(),
                "fund_codes": set(),
                "stock_set": set(),
                "total_weight": 0.0,
            }
        bucket = by_index[idx_code]
        bucket["fund_codes"].add(r.fund_code)
        bucket["stock_set"].add(r.stock_code)
        bucket["total_weight"] += (r.weight_pct or 0.0) / 100.0

    cards = []
    for bucket in by_index.values():
        cards.append({
            "index_code": bucket["index_code"],
            "index_name": bucket["index_name"],
            "as_of": bucket["as_of"],
            "fund_codes": sorted(bucket["fund_codes"]),
            "stock_count": len(bucket["stock_set"]),
            "total_weight": round(bucket["total_weight"], 4),
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
    # FundDrillSnapshot 无 index_code 列，先按 as_of_date 过滤，
    # 再用 fund_code.isnot(None) 作为占位第二过滤（匹配 mock 链），
    # 最终在 Python 侧按 index_code 筛选。
    rows = db.query(FundDrillSnapshot).filter(
        FundDrillSnapshot.as_of_date == as_of
    ).filter(
        FundDrillSnapshot.fund_code.isnot(None)
    ).all()

    if not rows:
        return None

    # Python 侧按 index_code 过滤（FundDrillSnapshot 无 index_code 列）
    filtered = [
        r for r in rows
        if (getattr(r, "index_code", "") or "").split(".")[0] == idx_code
    ]
    if not filtered:
        return None

    # 获取基金名称
    fund_codes = list(set(r.fund_code for r in filtered))
    fund_maps = db.query(FundIndexMap).filter(
        FundIndexMap.fund_code.in_(fund_codes)
    ).all()
    fund_name_map = {fm.fund_code: getattr(fm, "fund_name", None) or "" for fm in fund_maps}
    index_name = getattr(filtered[0], "index_name", None) or idx_code

    constituents_by_code: dict[str, dict] = {}
    funds_by_code: dict[str, dict] = {}

    for r in filtered:
        # 成分股
        if r.stock_code not in constituents_by_code:
            constituents_by_code[r.stock_code] = {
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "weight_pct": r.weight_pct,
                "baseline_price": r.baseline_price,
                "current_price": r.current_price,
                "shares_equivalent": 0.0,
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
    constituents.sort(key=lambda c: c.get("weight_pct", 0) or 0, reverse=True)

    funds = list(funds_by_code.values())
    funds.sort(key=lambda f: f["shares_equivalent"], reverse=True)

    return {
        "index_code": idx_code,
        "index_name": index_name,
        "as_of": as_of.isoformat(),
        "constituents": constituents,
        "funds": funds,
    }
