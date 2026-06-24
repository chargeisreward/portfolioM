"""下钻编排 service — 唯一耦合点。
调 public service + user service，join 后返回完整结果。

join 公式：
  user_drill_shares = user_quantity × fund.shares_equivalent
  user_hold_shares = total_drill_shares × constituent.weight
  user_hold_value = user_hold_shares × constituent.current_price
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy.orm import Session

from services import drill_public_service as public_service
from services import drill_user_service as user_service

logger = logging.getLogger(__name__)


def list_drillable_cards(db: Session, as_of: _date, user_id: int) -> list[dict]:
    """返回用户可见的下钻卡片列表。

    join 逻辑：
    1. public.get_public_cards(as_of) → 所有公共卡片
    2. user.get_user_fund_codes(user_id) → 用户基金代码集合
    3. if not user_fund_codes → return []
    4. 过滤：只保留 fund_codes ∩ user_fund_codes 非空的卡片
    5. 计算 est_market_value_cny
    """
    public_cards = public_service.get_public_cards(db, as_of)
    user_fund_codes = user_service.get_user_fund_codes(db, user_id)

    if not user_fund_codes:
        return []

    if not public_cards:
        return []

    # 获取用户持仓明细（用于计算 est_market_value）
    user_holdings = user_service.get_user_fund_holdings(
        db, user_id, list(user_fund_codes)
    )

    result = []
    for card in public_cards:
        overlap = set(card["fund_codes"]) & user_fund_codes
        if not overlap:
            continue
        est_value = sum(
            user_holdings[f]["amount_cny"]
            for f in overlap
            if f in user_holdings
        )
        result.append({
            **card,
            "user_fund_codes": sorted(overlap),
            "est_market_value_cny": round(est_value, 4),
        })

    result.sort(key=lambda c: c.get("est_market_value_cny", 0), reverse=True)
    return result


def get_drill_detail(
    db: Session, as_of: _date, index_code: str, user_id: int
) -> dict | None:
    """返回用户可见的下钻明细。

    join 逻辑：
    1. public.get_public_detail(as_of, index_code) → 公共明细
    2. if not public_detail → return None
    3. user.get_user_fund_holdings(user_id, fund_codes) → 用户持仓
    4. if not user_holdings → return None
    5. join：计算 user_drill_shares / user_hold_shares / user_hold_value
    """
    public_detail = public_service.get_public_detail(db, as_of, index_code)
    if not public_detail:
        return None

    fund_codes = [f["fund_code"] for f in public_detail.get("funds", [])]
    user_holdings = user_service.get_user_fund_holdings(db, user_id, fund_codes)

    if not user_holdings:
        return None

    # join：计算每只基金的 user_drill_shares
    funds_joined = []
    total_drill_shares = 0.0
    for f in public_detail["funds"]:
        h = user_holdings.get(f["fund_code"])
        if not h:
            continue
        user_drill_shares = h["quantity"] * (f.get("shares_equivalent") or 0.0)
        funds_joined.append({
            **f,
            "user_quantity": h["quantity"],
            "user_drill_shares": round(user_drill_shares, 4),
        })
        total_drill_shares += user_drill_shares

    # join：计算每个成分股的 user_hold_shares / user_hold_value
    constituents_joined = []
    for c in public_detail["constituents"]:
        weight = (c.get("weight_pct") or 0.0) / 100.0
        user_hold_shares = total_drill_shares * weight
        current_price = c.get("current_price") or 0.0
        user_hold_value = user_hold_shares * current_price
        constituents_joined.append({
            **c,
            "user_hold_shares": round(user_hold_shares, 4),
            "user_hold_value": round(user_hold_value, 4),
        })

    return {
        **public_detail,
        "funds": funds_joined,
        "constituents": constituents_joined,
        "total_user_drill_shares": round(total_drill_shares, 4),
    }
