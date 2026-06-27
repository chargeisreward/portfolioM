"""估值表日截面服务 — 维护 valuation_daily_snapshot 表。

核心职责：
1. rebuild_valuation_to_date: 逐日重算截面（已锁定日跳过，未锁定日 wipe+重算+检查锁定）
2. get_valuation_snapshot: 读取截面（含 is_locked + holdings + kpi）

数据来源：
- 持仓+股价+市值: HoldingDailySnapshot (已有，按 user_id+as_of_date)
- PE/PB/PS: AShare/HK/OverseasShareFinancialSnapshot 公共估值快照
- type2: SecurityMaster.type2
- 锁定条件: as_of_date <= get_confirmed_as_of(db) 且非现金行无价格缺失
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import (
    AShareFinancialSnapshot,
    HKShareFinancialSnapshot,
    HoldingDailySnapshot,
    OverseasShareFinancialSnapshot,
    SecurityMaster,
    ValuationDailySnapshot,
)
from services.trading_calendar import get_confirmed_as_of
from services.trading_rebuild_service import get_snapshot_for_date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 公共估值快照解析（PE/PB/PS/dividend_yield/market_cap）
# ---------------------------------------------------------------------------

def _resolve_public_metrics(db: Session, stock_code: str, as_of_date: _date) -> dict:
    """从 A/H/Overseas 公共估值快照表解析关键指标。

    Returns: {pe_ttm, pb_mrq, ps_ttm, dividend_yield, market_cap}（缺省 None）
    """
    code_norm = stock_code.split(".")[0]
    is_hk = stock_code.upper().endswith(".HK")
    is_overseas = not (stock_code.endswith(".SH") or stock_code.endswith(".SZ") or is_hk)

    # A 股
    if not is_hk and not is_overseas:
        for suffix in (".SZ", ".SH"):
            snap = (
                db.query(AShareFinancialSnapshot)
                .filter(AShareFinancialSnapshot.as_of_date == as_of_date)
                .filter(AShareFinancialSnapshot.stock_code == f"{code_norm}{suffix}")
                .first()
            )
            if snap:
                return {
                    "pe_ttm": snap.pe_ttm,
                    "pb_mrq": snap.pb_mrq,
                    "ps_ttm": snap.ps_ttm,
                    "dividend_yield": snap.dividend_yield,
                    "market_cap": snap.market_cap,
                }
        return _empty_metrics()

    # 港股
    if is_hk:
        snap = (
            db.query(HKShareFinancialSnapshot)
            .filter(HKShareFinancialSnapshot.as_of_date == as_of_date)
            .filter(HKShareFinancialSnapshot.stock_code == stock_code)
            .first()
        )
        if not snap:
            # 试 padded code
            padded = code_norm.zfill(5)
            snap = (
                db.query(HKShareFinancialSnapshot)
                .filter(HKShareFinancialSnapshot.as_of_date == as_of_date)
                .filter(HKShareFinancialSnapshot.stock_code == f"{padded}.HK")
                .first()
            )
        if snap:
            return {
                "pe_ttm": snap.pe_ttm,
                "pb_mrq": snap.pb_mrq,
                "ps_ttm": snap.ps_ttm,
                "dividend_yield": snap.dividend_yield,
                "market_cap": snap.market_cap,
            }
        return _empty_metrics()

    # 海外
    snap = (
        db.query(OverseasShareFinancialSnapshot)
        .filter(OverseasShareFinancialSnapshot.stock_code == stock_code)
        .order_by(OverseasShareFinancialSnapshot.as_of_date.desc())
        .first()
    )
    if snap:
        return {
            "pe_ttm": snap.pe_ttm,
            "pb_mrq": snap.pb_mrq,
            "ps_ttm": snap.ps_ttm,
            "dividend_yield": snap.dividend_yield,
            "market_cap": snap.market_cap,
        }
    return _empty_metrics()


def _empty_metrics() -> dict:
    return {
        "pe_ttm": None,
        "pb_mrq": None,
        "ps_ttm": None,
        "dividend_yield": None,
        "market_cap": None,
    }


def _resolve_type2(db: Session, security_code: str) -> Optional[str]:
    """从 SecurityMaster 读 type2 字段。"""
    sm = (
        db.query(SecurityMaster)
        .filter(SecurityMaster.security_code == security_code)
        .first()
    )
    return sm.type2 if sm else None


# ---------------------------------------------------------------------------
# 锁定逻辑
# ---------------------------------------------------------------------------

def _check_and_lock(db: Session, user_id: int, as_of_date: _date) -> bool:
    """锁定条件（两者均须满足）：
    1. as_of_date <= get_confirmed_as_of(db)（T+1 08:00 后视为已确认）
    2. 该日非现金行无价格缺失（price IS NULL 或 amount_cny=0 视为缺失）

    有价格缺失时不锁定，等待后续价格补齐后重算时再锁定。
    Returns: True 若已锁定，False 若未锁定
    """
    confirmed_as_of = get_confirmed_as_of(db)
    if as_of_date > confirmed_as_of:
        return False

    # 价格完整性检查：非现金行 price IS NULL 或 amount_cny=0 视为缺失
    missing_count = (
        db.query(ValuationDailySnapshot)
        .filter(
            ValuationDailySnapshot.user_id == user_id,
            ValuationDailySnapshot.as_of_date == as_of_date,
            ValuationDailySnapshot.is_cash == False,  # noqa: E712
            or_(
                ValuationDailySnapshot.price == None,  # noqa: E711
                ValuationDailySnapshot.amount_cny == 0,
            ),
        )
        .count()
    )
    if missing_count > 0:
        logger.warning(
            "check_and_lock: skip locking user_id=%s as_of=%s — %s non-cash rows missing price",
            user_id, as_of_date, missing_count,
        )
        return False

    now = datetime.utcnow()
    rows = (
        db.query(ValuationDailySnapshot)
        .filter(
            ValuationDailySnapshot.user_id == user_id,
            ValuationDailySnapshot.as_of_date == as_of_date,
            ValuationDailySnapshot.is_locked == False,  # noqa: E712
        )
        .all()
    )
    for r in rows:
        r.is_locked = True
        r.locked_at = now
    db.commit()
    return True


# ---------------------------------------------------------------------------
# 重算单日
# ---------------------------------------------------------------------------

def _wipe_one_day(db: Session, user_id: int, as_of_date: _date) -> int:
    """清掉该用户该日的所有估值截面行。返回删除行数。"""
    deleted = (
        db.query(ValuationDailySnapshot)
        .filter(
            ValuationDailySnapshot.user_id == user_id,
            ValuationDailySnapshot.as_of_date == as_of_date,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def _rebuild_one_day(db: Session, user_id: int, as_of_date: _date) -> int:
    """重算单日截面：wipe → 拉 HoldingDailySnapshot → join 公共估值快照 → 写入。

    Returns: 写入行数。若 HoldingDailySnapshot 无该日数据，返回 0（不报错）。
    """
    _wipe_one_day(db, user_id, as_of_date)

    holdings = get_snapshot_for_date(db, user_id, as_of_date)
    if not holdings:
        logger.warning(
            "rebuild_one_day: no HoldingDailySnapshot for user_id=%s as_of=%s, skip",
            user_id, as_of_date,
        )
        return 0

    # 预加载 type2 缓存（避免每行查 SecurityMaster）
    codes = {h["security_code"] for h in holdings}
    sm_by_code: dict[str, SecurityMaster] = {
        sm.security_code: sm
        for sm in db.query(SecurityMaster)
        .filter(SecurityMaster.security_code.in_(codes))
        .all()
    }

    rows: list[ValuationDailySnapshot] = []
    for h in holdings:
        code = h["security_code"]
        sm = sm_by_code.get(code)
        type2 = sm.type2 if sm else None
        # 关键指标从公共估值快照取（仅对非现金行）
        if h.get("is_cash"):
            metrics = _empty_metrics()
        else:
            metrics = _resolve_public_metrics(db, code, as_of_date)

        rows.append(ValuationDailySnapshot(
            user_id=user_id,
            as_of_date=as_of_date,
            security_code=code,
            security_name=h.get("security_name"),
            quantity=h.get("quantity"),
            price=h.get("price"),
            price_cny=h.get("price_cny"),
            currency=h.get("currency") or "CNY",
            fx_rate=h.get("fx_rate") or 1.0,
            amount_cny=h.get("amount_cny") or 0.0,
            asset_type=h.get("asset_type"),
            type2=type2,
            is_cash=bool(h.get("is_cash")),
            holding_uid=None,  # HoldingDailySnapshot 不带 holding_uid 到 get_snapshot_for_date，统一 NULL
            is_locked=False,
            locked_at=None,
            **metrics,
        ))

    db.bulk_save_objects(rows)
    db.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# 主入口：rebuild_valuation_to_date
# ---------------------------------------------------------------------------

def rebuild_valuation_to_date(
    db: Session,
    user_id: int,
    target_date: _date,
    force_from: Optional[_date] = None,
) -> dict:
    """从 force_from（或起始日）到 target_date 逐日重算估值截面。

    逻辑：
    - force_from 指定（trade 编辑触发）：强制从该日起重算（含已锁定的也解锁重算）
    - force_from 未指定：
        * 已锁定日 → 跳过
        * 未锁定日 → wipe + 重算 + 检查锁定条件

    Returns: {days_processed, days_skipped_locked, days_locked_now, total_rows}
    """
    # 决定起始日
    if force_from is not None:
        start = force_from
        # force_from 触发：先解锁所有 >= force_from 的已锁定行
        unlocked = (
            db.query(ValuationDailySnapshot)
            .filter(
                ValuationDailySnapshot.user_id == user_id,
                ValuationDailySnapshot.as_of_date >= start,
                ValuationDailySnapshot.as_of_date <= target_date,
                ValuationDailySnapshot.is_locked == True,  # noqa: E712
            )
            .update({ValuationDailySnapshot.is_locked: False, ValuationDailySnapshot.locked_at: None},
                    synchronize_session=False)
        )
        db.commit()
        if unlocked:
            logger.info("force_from=%s unlocked %s rows for user_id=%s", force_from, unlocked, user_id)
    else:
        # 找最早的未锁定日（如果都锁定则全部跳过）
        first_unlocked = (
            db.query(ValuationDailySnapshot.as_of_date)
            .filter(
                ValuationDailySnapshot.user_id == user_id,
                ValuationDailySnapshot.as_of_date <= target_date,
                ValuationDailySnapshot.is_locked == False,  # noqa: E712
            )
            .order_by(ValuationDailySnapshot.as_of_date.asc())
            .first()
        )
        if first_unlocked:
            start = first_unlocked[0]
        else:
            # 没有未锁定行 — 检查 target_date 是否已有截面
            existing = (
                db.query(ValuationDailySnapshot)
                .filter(
                    ValuationDailySnapshot.user_id == user_id,
                    ValuationDailySnapshot.as_of_date == target_date,
                )
                .first()
            )
            if existing:
                # 已有截面且全部锁定 — 跳过
                return {
                    "days_processed": 0,
                    "days_skipped_locked": 1,
                    "days_locked_now": 0,
                    "total_rows": 0,
                }
            # target_date 无截面 — 需要新建
            start = target_date

    days_processed = 0
    days_skipped_locked = 0
    days_locked_now = 0
    total_rows = 0

    cur = start
    while cur <= target_date:
        if force_from is None:
            # 检查该日是否已锁定 — 跳过
            locked_exists = (
                db.query(ValuationDailySnapshot)
                .filter(
                    ValuationDailySnapshot.user_id == user_id,
                    ValuationDailySnapshot.as_of_date == cur,
                    ValuationDailySnapshot.is_locked == True,  # noqa: E712
                )
                .first()
            )
            if locked_exists:
                days_skipped_locked += 1
                cur += timedelta(days=1)
                continue

        rows_written = _rebuild_one_day(db, user_id, cur)
        days_processed += 1
        total_rows += rows_written

        # 检查并锁定
        if rows_written > 0:
            locked = _check_and_lock(db, user_id, cur)
            if locked:
                days_locked_now += 1

        cur += timedelta(days=1)

    return {
        "days_processed": days_processed,
        "days_skipped_locked": days_skipped_locked,
        "days_locked_now": days_locked_now,
        "total_rows": total_rows,
    }


# ---------------------------------------------------------------------------
# 读取 API 数据
# ---------------------------------------------------------------------------

def get_valuation_snapshot(db: Session, user_id: int, as_of_date: _date) -> Optional[dict]:
    """读取估值表截面。

    Returns: {as_of_date, is_locked, locked_at, holdings[]} 或 None（截面不存在）
    若截面不存在，自动触发 rebuild_valuation_to_date(user_id, as_of_date)。
    """
    rows = (
        db.query(ValuationDailySnapshot)
        .filter(
            ValuationDailySnapshot.user_id == user_id,
            ValuationDailySnapshot.as_of_date == as_of_date,
        )
        .order_by(
            ValuationDailySnapshot.is_cash.desc(),  # 现金行排第一
            ValuationDailySnapshot.security_code,
        )
        .all()
    )

    if not rows:
        # 截面不存在 — 尝试重算单日
        logger.info("valuation_snapshot not found, try rebuild for user_id=%s as_of=%s",
                    user_id, as_of_date)
        n = _rebuild_one_day(db, user_id, as_of_date)
        if n == 0:
            return None
        _check_and_lock(db, user_id, as_of_date)
        # 重新查询
        rows = (
            db.query(ValuationDailySnapshot)
            .filter(
                ValuationDailySnapshot.user_id == user_id,
                ValuationDailySnapshot.as_of_date == as_of_date,
            )
            .order_by(
                ValuationDailySnapshot.is_cash.desc(),
                ValuationDailySnapshot.security_code,
            )
            .all()
        )
        if not rows:
            return None

    is_locked = rows[0].is_locked
    locked_at = rows[0].locked_at

    return {
        "as_of_date": as_of_date.isoformat(),
        "is_locked": is_locked,
        "locked_at": locked_at.isoformat() if locked_at else None,
        "holdings": [
            {
                "security_code": r.security_code,
                "security_name": r.security_name,
                "quantity": r.quantity,
                "price": r.price,
                "price_cny": r.price_cny,
                "currency": r.currency,
                "amount_cny": r.amount_cny,
                "asset_type": r.asset_type,
                "type2": r.type2,
                "is_cash": r.is_cash,
                "holding_uid": r.holding_uid,
                "pe_ttm": r.pe_ttm,
                "pb_mrq": r.pb_mrq,
                "ps_ttm": r.ps_ttm,
                "dividend_yield": r.dividend_yield,
                "market_cap": r.market_cap,
            }
            for r in rows
        ],
    }


def get_valuation_date_range(db: Session, user_id: int) -> Optional[tuple[_date, _date]]:
    """返回估值截面日期范围 (min, max)。无截面返回 None。"""
    from sqlalchemy import func
    result = db.query(
        func.min(ValuationDailySnapshot.as_of_date),
        func.max(ValuationDailySnapshot.as_of_date),
    ).filter(
        ValuationDailySnapshot.user_id == user_id,
    ).first()
    if result and result[0] and result[1]:
        return (result[0], result[1])
    return None
