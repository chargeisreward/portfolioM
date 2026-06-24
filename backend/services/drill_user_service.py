"""用户下钻 service — 只读 Holding 表。
不知道下钻结构，不读 fund_drill_snapshot。可独立复用。

可下钻 asset_type：a_share_equity, a_share_etf, hk_equity, qdii_equity, us_etf
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import Holding

logger = logging.getLogger(__name__)

# 可下钻的 asset_type 集合
DRILLABLE_ASSET_TYPES = frozenset({
    "a_share_equity",
    "a_share_etf",
    "hk_equity",
    "qdii_equity",
    "us_etf",
})


def get_user_fund_codes(db: Session, user_id: int) -> set[str]:
    """返回用户持有的所有可下钻基金代码集合。

    过滤 asset_type in DRILLABLE_ASSET_TYPES 且 quantity > 0。

    返回：{"510300.SH", "159919.SZ", ...}
    """
    rows = db.query(Holding).filter(
        Holding.user_id == user_id,
    ).all()

    codes: set[str] = set()
    for h in rows:
        asset_type = (h.asset_type or "").lower()
        if asset_type in DRILLABLE_ASSET_TYPES and (h.quantity or 0) > 0:
            codes.add(h.security_code)
    return codes


def get_user_fund_holdings(db: Session, user_id: int, fund_codes: list[str]) -> dict[str, dict]:
    """返回用户在指定基金上的持仓明细。

    跨买入批次聚合（同一基金多笔买入求和）。

    返回结构：
    {
        "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
    }
    """
    if not fund_codes:
        return {}

    rows = db.query(Holding).filter(
        Holding.user_id == user_id,
    ).filter(
        Holding.security_code.in_(fund_codes),
    ).all()

    out: dict[str, dict] = {}
    for h in rows:
        code = h.security_code
        if code not in out:
            out[code] = {
                "quantity": 0.0,
                "amount_cny": 0.0,
                "price": h.price,
            }
        out[code]["quantity"] += (h.quantity or 0.0)
        out[code]["amount_cny"] += (h.amount_cny or 0.0)

    # 计算平均价格
    for code, info in out.items():
        if info["quantity"] > 0:
            info["price"] = info["amount_cny"] / info["quantity"]

    return out
