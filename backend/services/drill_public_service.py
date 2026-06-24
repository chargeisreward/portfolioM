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

    # 加载 fund_code → (index_code, index_name) 映射
    fund_map = _load_fund_index_map(db)

    by_index: dict[str, dict] = {}
    for r in rows:
        fund_code = r.fund_code
        if fund_code not in fund_map:
            continue
        idx_code, idx_name = fund_map[fund_code]

        if idx_code not in by_index:
            by_index[idx_code] = {
                "index_code": idx_code,
                "index_name": idx_name or idx_code,
                "as_of": as_of.isoformat(),
                "fund_codes": set(),
                "stock_set": set(),
                "total_weight": 0.0,
            }
        bucket = by_index[idx_code]
        bucket["fund_codes"].add(fund_code)
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
        FundDrillSnapshot.as_of_date == as_of,
        FundDrillSnapshot.fund_code.in_(fund_codes),
    ).all()

    if not rows:
        return None

    constituents_by_code: dict[str, dict] = {}
    funds_by_code: dict[str, dict] = {}

    for r in rows:
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
