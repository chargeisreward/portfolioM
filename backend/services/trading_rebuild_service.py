"""交易记录驱动的持仓重算引擎。

从起始日（默认 2025-07-19）的起始持仓快照开始，逐日应用交易记录，
生成每日含现金的持仓快照。最新日数据同步覆盖到 Holding 表，
供 OverviewPanel / 下钻系统使用。

核心流程：
  ensure_initial_snapshot → 逐日复制+交易调整+价格更新 → 同步覆盖 Holding 表

日期规则：确认日期优先 > 净值日期；仅一个日期视为确认日期。
交易方向：buy(申购) shares+ / amount-；sell(赎回) shares- / amount+。
现金允许为负（初始未维护账户内现金）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import func

from models import (
    Holding,
    HoldingDailySnapshot,
    Transaction,
    TradingSession,
    PriceCache,
    FundDailyNav,
    SecurityMaster,
)
from crawlers.exchange_rates import get_rate, guess_currency_from_code
from services.importer import guess_asset_type

logger = logging.getLogger(__name__)

# 起始日（用户需求 1：设当前持仓为 2025-07-19 的持仓）
DEFAULT_START_DATE = date(2025, 7, 19)


def ensure_initial_snapshot(
    db: Session,
    user_id: int,
    start_date: date = DEFAULT_START_DATE,
    initial_cash: float = 0.0,
) -> TradingSession:
    """确保起始持仓快照已建立（幂等）。

    1. 查 TradingSession；存在且 initial_snapshot_built=True → 直接返回
    2. 取当前 Holding 表 user_id=user_id 全部持仓
    3. 逐行写入 holding_daily_snapshot (as_of_date=start_date, is_initial=True)
    4. 写入 CASH 行 (security_code='CASH', quantity=initial_cash, is_cash=True)
    5. 创建/更新 TradingSession (initial_snapshot_built=True)

    Args:
        db: 数据库会话
        user_id: 用户 ID（写入者，不支持 view_as）
        start_date: 起始日，默认 2025-07-19
        initial_cash: 起始现金，默认 0.0

    Returns:
        TradingSession 实例
    """
    session = db.query(TradingSession).filter(
        TradingSession.user_id == user_id,
    ).first()

    # 幂等：已建立则直接返回
    if session and session.initial_snapshot_built:
        return session

    # 清除可能存在的旧起始日快照（force rebuild 场景）
    db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == user_id,
        HoldingDailySnapshot.as_of_date == start_date,
    ).delete()

    # 取当前 Holding 表持仓作为起始持仓
    holdings = db.query(Holding).filter(Holding.user_id == user_id).all()

    for h in holdings:
        currency = h.currency or guess_currency_from_code(h.security_code)
        fx_rate = get_rate(db, currency, "CNY", start_date)
        price = h.price or 0.0
        price_cny = round(price * fx_rate, 4) if price else 0.0
        amount_cny = round((h.quantity or 0.0) * price_cny, 2)

        db.add(HoldingDailySnapshot(
            user_id=user_id,
            as_of_date=start_date,
            security_code=h.security_code,
            security_name=h.security_name,
            quantity=h.quantity or 0.0,
            price=price,
            price_cny=price_cny,
            currency=currency,
            fx_rate=fx_rate,
            amount_cny=amount_cny,
            asset_type=h.asset_type,
            is_cash=False,
            is_initial=True,
            holding_uid=h.id,
        ))

    # 写入 CASH 行（交易形成的现金，不含下钻现金）
    db.add(HoldingDailySnapshot(
        user_id=user_id,
        as_of_date=start_date,
        security_code="CASH",
        security_name="现金",
        quantity=initial_cash,
        price=1.0,
        price_cny=1.0,
        currency="CNY",
        fx_rate=1.0,
        amount_cny=initial_cash,
        asset_type="cash",
        is_cash=True,
        is_initial=True,
    ))

    # 创建/更新 TradingSession
    if session:
        session.start_date = start_date
        session.initial_cash = initial_cash
        session.initial_snapshot_built = True
    else:
        session = TradingSession(
            user_id=user_id,
            start_date=start_date,
            initial_cash=initial_cash,
            initial_snapshot_built=True,
        )
        db.add(session)

    db.commit()
    return session


def fetch_daily_price(
    db: Session,
    security_code: str,
    as_of: date,
) -> dict | None:
    """取某证券某日收盘价。返回 {price, price_cny, currency, fx_rate} 或 None。

    数据源优先级：
    1. PriceCache 表 (stock_code + trade_date <= as_of，取最新 close_px)
    2. .OF 后缀 → FundDailyNav 表 (fund_code + trade_date <= as_of，取 nav)
    3. 都没有 → None（价格留空，不阻断重算）

    Args:
        db: 数据库会话
        security_code: 证券代码
        as_of: 查询日期

    Returns:
        dict with price/price_cny/currency/fx_rate, or None
    """
    # 1. PriceCache（股票/ETF 日频历史价）
    pc = db.query(PriceCache).filter(
        PriceCache.stock_code == security_code,
        PriceCache.trade_date <= as_of,
    ).order_by(PriceCache.trade_date.desc()).first()

    if pc and pc.close_px:
        currency = guess_currency_from_code(security_code)
        fx_rate = get_rate(db, currency, "CNY", as_of)
        price_cny = round(pc.close_px * fx_rate, 4)
        return {
            "price": pc.close_px,
            "price_cny": price_cny,
            "currency": currency,
            "fx_rate": fx_rate,
        }

    # 2. FundDailyNav（.OF 场外基金净值）
    if security_code.upper().endswith(".OF"):
        fdn = db.query(FundDailyNav).filter(
            FundDailyNav.fund_code == security_code,
            FundDailyNav.trade_date <= as_of,
        ).order_by(FundDailyNav.trade_date.desc()).first()

        if fdn and fdn.nav:
            return {
                "price": fdn.nav,
                "price_cny": fdn.nav,
                "currency": "CNY",
                "fx_rate": 1.0,
            }

    return None


def _get_asset_type_from_master(db: Session, security_code: str) -> str:
    """从 SecurityMaster 查 asset_type，fallback 到 guess_asset_type。

    Args:
        db: 数据库会话
        security_code: 证券代码

    Returns:
        asset_type 字符串
    """
    sm = db.query(SecurityMaster).filter(
        SecurityMaster.security_code == security_code,
    ).first()
    if sm and sm.asset_type:
        return sm.asset_type
    return guess_asset_type(security_code)


def rebuild_holdings_to_date(
    db: Session,
    user_id: int,
    target_date: date,
    force: bool = False,
) -> dict:
    """从起始日重算到 target_date，每日写一行到 holding_daily_snapshot。

    流程：
    1. ensure_initial_snapshot（幂等）
    2. 确定重算起点（force 全量 / 增量）
    3. 逐日：复制前一日 + 应用交易 + 更新价格
    4. 更新 TradingSession.last_rebuild_date
    5. 同步最新日到 Holding 表（不回退）

    Args:
        db: 数据库会话
        user_id: 用户 ID
        target_date: 重算目标日期
        force: True 全量重算；False 增量重算

    Returns:
        {start_date, target_date, days_built, latest_rows_count, synced_to_holding}
    """
    session = ensure_initial_snapshot(db, user_id)
    start_date = session.start_date
    old_last_rebuild = session.last_rebuild_date

    # target_date 早于起始日，无需重算
    if target_date < start_date:
        return {
            "start_date": start_date,
            "target_date": target_date,
            "days_built": 0,
            "latest_rows_count": 0,
            "synced_to_holding": False,
        }

    # 确定重算起点
    if force or not old_last_rebuild:
        rebuild_from = start_date
        # 全量重算：清除 start_date 之后的所有快照（保留起始日快照）
        db.query(HoldingDailySnapshot).filter(
            HoldingDailySnapshot.user_id == user_id,
            HoldingDailySnapshot.as_of_date > start_date,
        ).delete()
    else:
        rebuild_from = old_last_rebuild + timedelta(days=1)
        # 增量重算：清除 rebuild_from 到 target_date 之间的旧快照
        if rebuild_from <= target_date:
            db.query(HoldingDailySnapshot).filter(
                HoldingDailySnapshot.user_id == user_id,
                HoldingDailySnapshot.as_of_date >= rebuild_from,
                HoldingDailySnapshot.as_of_date <= target_date,
            ).delete()

    db.flush()

    # 查询当前最大 holding_uid，用作新买入行递增分配的基数
    # 起始日原始持仓的 uid = Holding.id；新买入行从 max_uid+1 递增，确保不重复且非 NULL
    max_uid = db.query(func.max(HoldingDailySnapshot.holding_uid)).filter(
        HoldingDailySnapshot.user_id == user_id,
    ).scalar() or 0
    uid_counter = max_uid

    # 逐日重算
    days_built = 0
    current_date = rebuild_from

    while current_date <= target_date:
        prev_date = current_date - timedelta(days=1)

        # 取前一日快照
        prev_rows = db.query(HoldingDailySnapshot).filter(
            HoldingDailySnapshot.user_id == user_id,
            HoldingDailySnapshot.as_of_date == prev_date,
        ).all()

        # 复制前一日到当日（价格/数量等全部沿用，后续再更新）
        current_rows: list[HoldingDailySnapshot] = []
        for prev in prev_rows:
            current_rows.append(HoldingDailySnapshot(
                user_id=user_id,
                as_of_date=current_date,
                security_code=prev.security_code,
                security_name=prev.security_name,
                quantity=prev.quantity,
                price=prev.price,
                price_cny=prev.price_cny,
                currency=prev.currency,
                fx_rate=prev.fx_rate,
                amount_cny=prev.amount_cny,
                asset_type=prev.asset_type,
                is_cash=prev.is_cash,
                is_initial=False,
                holding_uid=prev.holding_uid,
            ))

        # 应用当日交易
        trades = db.query(Transaction).filter(
            Transaction.user_id == user_id,
            Transaction.trade_date == current_date,
        ).all()

        for trade in trades:
            # 找到 CASH 行
            cash_row = next(
                (r for r in current_rows if r.is_cash),
                None,
            )

            if trade.trade_type == "buy":
                # 买入：新建一笔持仓行，分配非重复 holding_uid（递增）
                uid_counter += 1
                asset_type = _get_asset_type_from_master(db, trade.security_code)
                currency = guess_currency_from_code(trade.security_code)
                new_row = HoldingDailySnapshot(
                    user_id=user_id,
                    as_of_date=current_date,
                    security_code=trade.security_code,
                    security_name=trade.security_name,
                    quantity=abs(trade.confirmed_shares or 0.0),
                    price=None,
                    price_cny=None,
                    currency=currency,
                    fx_rate=get_rate(db, currency, "CNY", current_date),
                    amount_cny=0.0,
                    asset_type=asset_type,
                    is_cash=False,
                    is_initial=False,
                    holding_uid=uid_counter,
                )
                current_rows.append(new_row)
                if cash_row:
                    cash_row.quantity -= abs(trade.confirmed_amount or 0.0)

            elif trade.trade_type == "sell":
                # 卖出：在同代码持仓中，按 holding_uid 从小到大依次扣除
                # holding_uid 全部非 NULL（原始导入 uid=Holding.id，交易新建 uid=递增）
                sell_rows = [r for r in current_rows
                             if r.security_code == trade.security_code and not r.is_cash]
                sell_rows.sort(key=lambda r: r.holding_uid)

                remaining = abs(trade.confirmed_shares or 0.0)
                for srow in sell_rows:
                    if remaining <= 0:
                        break
                    available = srow.quantity or 0.0
                    if available <= 0:
                        continue
                    deduct = min(available, remaining)
                    srow.quantity -= deduct
                    remaining -= deduct

                # 超卖：全部扣完仍不够，最后一笔变负数 + warning（Fail Loud）
                if remaining > 0 and sell_rows:
                    sell_rows[-1].quantity -= remaining
                    logger.warning(
                        f"卖出超卖: user={user_id}, code={trade.security_code}, "
                        f"date={current_date}, 卖出={abs(trade.confirmed_shares or 0.0)}, "
                        f"超卖={remaining}"
                    )
                elif remaining > 0 and not sell_rows:
                    logger.warning(
                        f"卖出无持仓: user={user_id}, code={trade.security_code}, "
                        f"date={current_date}, 卖出={abs(trade.confirmed_shares or 0.0)}"
                    )

                if cash_row:
                    cash_row.quantity += abs(trade.confirmed_amount or 0.0)

        # 更新价格和金额
        for row in current_rows:
            if row.is_cash:
                # CASH 行：amount_cny = quantity（允许为负）
                row.amount_cny = row.quantity
            else:
                # 非现金行：尝试更新价格
                price_info = fetch_daily_price(db, row.security_code, current_date)
                if price_info:
                    row.price = price_info["price"]
                    row.price_cny = price_info["price_cny"]
                    row.currency = price_info["currency"]
                    row.fx_rate = price_info["fx_rate"]
                # 重算 amount_cny = quantity × price_cny
                if row.price_cny and row.quantity:
                    row.amount_cny = round(row.quantity * row.price_cny, 2)
                elif row.price_cny is None:
                    row.amount_cny = 0.0

        # 写入当日快照
        for row in current_rows:
            db.add(row)
        # autoflush=False（SessionLocal 配置），需手动 flush 确保次日查询可见
        if current_rows:
            db.flush()

        days_built += 1
        current_date += timedelta(days=1)

    # 更新 TradingSession.last_rebuild_date
    session.last_rebuild_date = target_date

    # 同步最新日到 Holding 表（仅在 target_date >= old_last_rebuild 时，避免回退）
    synced = False
    if old_last_rebuild is None or target_date >= old_last_rebuild:
        latest_rows = db.query(HoldingDailySnapshot).filter(
            HoldingDailySnapshot.user_id == user_id,
            HoldingDailySnapshot.as_of_date == target_date,
            HoldingDailySnapshot.is_cash == False,  # noqa: E712
        ).all()

        # 删除该 user 的旧 Holding
        db.query(Holding).filter(Holding.user_id == user_id).delete()

        # 用最新快照覆盖
        for row in latest_rows:
            amount = round(row.quantity * row.price, 2) if row.price and row.quantity else 0.0
            db.add(Holding(
                user_id=user_id,
                security_code=row.security_code,
                security_name=row.security_name,
                quantity=row.quantity,
                price=row.price,
                currency=row.currency,
                amount=amount,
                amount_cny=row.amount_cny or 0.0,
                asset_type=row.asset_type or "a_share_equity",
                import_batch=f"rebuild_{target_date.isoformat()}",
            ))
        synced = True

    db.commit()

    # 统计最新日行数（含 CASH）
    latest_count = db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == user_id,
        HoldingDailySnapshot.as_of_date == target_date,
    ).count()

    return {
        "start_date": start_date,
        "target_date": target_date,
        "days_built": days_built,
        "latest_rows_count": latest_count,
        "synced_to_holding": synced,
    }


def get_snapshot_for_date(
    db: Session,
    user_id: int,
    as_of: date,
) -> list[dict] | None:
    """取某日持仓快照。返回行列表（含 CASH 行），或 None（该日无快照）。

    Args:
        db: 数据库会话
        user_id: 用户 ID
        as_of: 查询日期

    Returns:
        list of dict（含 security_code/quantity/price/amount_cny/is_cash 等），或 None
    """
    rows = db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == user_id,
        HoldingDailySnapshot.as_of_date == as_of,
    ).all()

    if not rows:
        return None

    return [
        {
            "security_code": r.security_code,
            "security_name": r.security_name,
            "quantity": r.quantity,
            "price": r.price,
            "price_cny": r.price_cny,
            "currency": r.currency,
            "fx_rate": r.fx_rate,
            "amount_cny": r.amount_cny,
            "asset_type": r.asset_type,
            "is_cash": r.is_cash,
            "is_initial": r.is_initial,
        }
        for r in rows
    ]


def get_snapshot_date_range(
    db: Session,
    user_id: int,
) -> tuple[date, date] | None:
    """取快照日期范围 (min_as_of_date, max_as_of_date)。无快照返回 None。

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        (start_date, end_date) 或 None
    """
    result = db.query(
        func.min(HoldingDailySnapshot.as_of_date),
        func.max(HoldingDailySnapshot.as_of_date),
    ).filter(
        HoldingDailySnapshot.user_id == user_id,
    ).first()

    if result and result[0] and result[1]:
        return (result[0], result[1])
    return None


def get_trades_for_date(
    db: Session,
    user_id: int,
    as_of: date,
) -> list[dict]:
    """取某日交易记录列表。

    Args:
        db: 数据库会话
        user_id: 用户 ID
        as_of: 查询日期

    Returns:
        list of dict（含 trade_date/security_code/trade_type/confirmed_shares 等）
    """
    rows = db.query(Transaction).filter(
        Transaction.user_id == user_id,
        Transaction.trade_date == as_of,
    ).all()

    return [
        {
            "id": r.id,
            "trade_date": r.trade_date,
            "security_code": r.security_code,
            "security_name": r.security_name,
            "trade_type": r.trade_type,
            "confirmed_shares": r.confirmed_shares,
            "confirmed_amount": r.confirmed_amount,
            "nav_price": r.nav_price,
            "nav_date": r.nav_date,
            "fee": r.fee,
            "remarks": r.remarks,
            "security_verified": r.security_verified,
            "security_added_to_master": r.security_added_to_master,
        }
        for r in rows
    ]
