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


INITIAL_TRADE_IMPORT_BATCH = "seed_initial_20250719"


def ensure_initial_snapshot(
    db: Session,
    user_id: int,
    start_date: date = DEFAULT_START_DATE,
    initial_cash: float = 0.0,
) -> TradingSession:
    """确保起始持仓快照已建立（幂等）。

    优先从虚拟初始交易（import_batch='seed_initial_20250719'）建立起始快照：
    - holding_uid 按虚拟交易 Transaction.id 升序顺序编号（1, 2, 3...）
    - price=0.0（虚拟交易买入价格设为 0）

    Fallback：若虚拟交易不存在（admin 等用户），从 Holding 表读取，holding_uid=h.id（向后兼容）

    1. 查 TradingSession；存在且 initial_snapshot_built=True → 直接返回
    2. 清除旧起始日快照（force rebuild 场景）
    3. 优先读取虚拟初始交易；若无则 fallback 到 Holding 表
    4. 逐行写入 holding_daily_snapshot (as_of_date=start_date, is_initial=True)
    5. 写入 CASH 行 (security_code='CASH', quantity=initial_cash, is_cash=True)
    6. 创建/更新 TradingSession (initial_snapshot_built=True)

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

    # 优先读取虚拟初始交易（import_batch='seed_initial_20250719'）
    initial_trades = db.query(Transaction).filter(
        Transaction.user_id == user_id,
        Transaction.import_batch == INITIAL_TRADE_IMPORT_BATCH,
        Transaction.trade_date == start_date,
    ).order_by(Transaction.id.asc()).all()

    if initial_trades:
        # 从虚拟交易建立起始快照：holding_uid 从 1 顺序编号
        uid_counter = 0
        for trade in initial_trades:
            uid_counter += 1
            currency = guess_currency_from_code(trade.security_code)
            fx_rate = get_rate(db, currency, "CNY", start_date)
            # 虚拟交易 price=0.0（用户要求买入价格设为 0）
            price = 0.0
            price_cny = 0.0
            amount_cny = 0.0

            db.add(HoldingDailySnapshot(
                user_id=user_id,
                as_of_date=start_date,
                security_code=trade.security_code,
                security_name=trade.security_name,
                quantity=trade.confirmed_shares or 0.0,
                price=price,
                price_cny=price_cny,
                currency=currency,
                fx_rate=fx_rate,
                amount_cny=amount_cny,
                asset_type=_get_asset_type_from_master(db, trade.security_code),
                is_cash=False,
                is_initial=True,
                holding_uid=uid_counter,
            ))
        logger.info(
            "ensure_initial_snapshot: built from %d virtual trades, user_id=%s, start_date=%s",
            len(initial_trades), user_id, start_date,
        )
    else:
        # Fallback：虚拟交易不存在（admin 等用户），从 Holding 表读取
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
        logger.info(
            "ensure_initial_snapshot: fallback to Holding table, %d rows, user_id=%s, start_date=%s",
            len(holdings), user_id, start_date,
        )

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
    """取某证券某日收盘价。返回 {price, price_cny, currency, fx_rate, price_date} 或 None。

    数据源优先级：
    1. PriceCache 表 (stock_code + trade_date <= as_of，取最新 close_px)
    2. .OF 后缀 → FundDailyNav 表 (fund_code + trade_date <= as_of，取 nav)
    3. 都没有 → None（价格留空，不阻断重算）

    Args:
        db: 数据库会话
        security_code: 证券代码
        as_of: 查询日期

    Returns:
        dict with price/price_cny/currency/fx_rate/price_date, or None
        price_date: 价格实际日期（PriceCache.trade_date / FundDailyNav.trade_date）；
                    调用方可用于判断价格是否对齐 as_of。
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
            "price_date": pc.trade_date,
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
                "price_date": fdn.trade_date,
            }

    return None


def _create_holding_row(db: Session, user_id: int, current_date: date,
                        security_code: str, security_name: str | None,
                        quantity: float, uid_counter: int) -> HoldingDailySnapshot:
    """创建新的持仓行（非现金），分配 holding_uid。

    buy/rights/split/conversion-to/others+ 共用此函数；
    超卖场景也复用此函数，传入负数 quantity（保留符号，不取 abs）。

    Args:
        db: 数据库会话
        user_id: 用户 ID
        current_date: 当日日期
        security_code: 证券代码
        security_name: 证券名称
        quantity: 持仓数量（正数：买入/配股/拆分/转换to；负数：超卖创建的负持仓行）
        uid_counter: holding_uid 分配基数（调用方负责递增）

    Returns:
        HoldingDailySnapshot 实例（未加到 current_rows，由调用方 append）
    """
    asset_type = _get_asset_type_from_master(db, security_code)
    currency = guess_currency_from_code(security_code)
    return HoldingDailySnapshot(
        user_id=user_id,
        as_of_date=current_date,
        security_code=security_code,
        security_name=security_name,
        quantity=quantity or 0.0,
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


def _deduct_shares(current_rows: list[HoldingDailySnapshot],
                   security_code: str, shares_to_deduct: float,
                   user_id: int, current_date: date,
                   warning_prefix: str = "卖出") -> float:
    """从同代码持仓行中按 holding_uid 升序扣除份额。

    sell/conversion-from/others- 共用此函数。

    扣减规则：
    - 按 holding_uid 升序逐行扣减
    - quantity 扣到 0（含 EPSILON 容差）的行从 current_rows 中移除（持仓不存在了）
    - 超卖（全部扣完仍不够，剩余 >= EPSILON）时返回剩余正份额，由调用方创建负持仓行
    - 浮点残留（< EPSILON）视为 0，不触发超卖逻辑

    Args:
        current_rows: 当日持仓行列表（含 CASH），会被原地修改（移除 qty=0 的行）
        security_code: 要扣份额的证券代码
        shares_to_deduct: 要扣除的份额（正数）
        user_id: 用户 ID（用于 warning）
        current_date: 当日日期（用于 warning）
        warning_prefix: warning 日志前缀（如"卖出"/"转换from"）

    Returns:
        剩余未扣完的份额（>= EPSILON 表示超卖，调用方应创建负持仓行）
    """
    EPSILON = 1e-9
    sell_rows = [r for r in current_rows
                 if r.security_code == security_code and not r.is_cash]
    sell_rows.sort(key=lambda r: r.holding_uid)

    remaining = abs(shares_to_deduct or 0.0)
    for srow in sell_rows:
        if remaining < EPSILON:
            break
        available = srow.quantity or 0.0
        if available < EPSILON:
            continue
        deduct = min(available, remaining)
        srow.quantity -= deduct
        remaining -= deduct
        # 扣减后若进入容差范围，归零（避免 0.029999... 残留）
        if abs(srow.quantity or 0.0) < EPSILON:
            srow.quantity = 0.0

    # 移除 quantity=0 的行（已全部卖出，持仓不存在了）
    zero_rows = [r for r in sell_rows if (r.quantity or 0.0) == 0.0]
    for r in zero_rows:
        if r in current_rows:
            current_rows.remove(r)

    # 浮点残留视为 0，不触发超卖
    if remaining < EPSILON:
        return 0.0

    # 超卖：返回剩余份额，由调用方创建负持仓行（Fail Loud）
    logger.warning(
        f"{warning_prefix}超卖: user={user_id}, code={security_code}, "
        f"date={current_date}, 超卖={remaining}"
    )
    return remaining


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
        # 跳过起始日（ensure_initial_snapshot 已建立 start_date 当天快照），
        # 从 start_date+1 开始逐日复制前一日 + 应用交易
        rebuild_from = start_date + timedelta(days=1)
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
    # 起始日持仓的 uid 由 ensure_initial_snapshot 从虚拟交易顺序编号（1..N）；
    # 新买入行从 max_uid+1 递增，确保不重复且非 NULL
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

            if trade.trade_type in ("buy", "rights"):
                # buy / rights：新建持仓行 + 扣现金（rights 与 buy 相同逻辑：份额+，金额-）
                uid_counter += 1
                new_row = _create_holding_row(
                    db, user_id, current_date, trade.security_code,
                    trade.security_name, trade.confirmed_shares or 0.0, uid_counter,
                )
                current_rows.append(new_row)
                if cash_row:
                    cash_row.quantity -= abs(trade.confirmed_amount or 0.0)

            elif trade.trade_type == "sell":
                # 卖出：扣份额 + 加现金
                remaining = _deduct_shares(
                    current_rows, trade.security_code,
                    trade.confirmed_shares or 0.0, user_id, current_date,
                    warning_prefix="卖出",
                )
                # 超卖：创建负持仓行（数量为负，金额=均价×数量）
                if remaining > 0:
                    uid_counter += 1
                    neg_row = _create_holding_row(
                        db, user_id, current_date, trade.security_code,
                        trade.security_name, -remaining, uid_counter,
                    )
                    nav_price = trade.nav_price or 0.0
                    neg_row.price = nav_price
                    neg_row.price_cny = nav_price
                    neg_row.amount_cny = round(-remaining * nav_price, 2)
                    current_rows.append(neg_row)
                if cash_row:
                    cash_row.quantity += abs(trade.confirmed_amount or 0.0)

            elif trade.trade_type == "dividend":
                # 分红：份额不变，金额+ → 加现金
                if cash_row:
                    cash_row.quantity += abs(trade.confirmed_amount or 0.0)

            elif trade.trade_type == "split":
                # 拆分/折算：份额+，金额不变 → 新建持仓行，不扣现金
                uid_counter += 1
                new_row = _create_holding_row(
                    db, user_id, current_date, trade.security_code,
                    trade.security_name, trade.confirmed_shares or 0.0, uid_counter,
                )
                current_rows.append(new_row)
                # 不扣现金

            elif trade.trade_type == "conversion":
                # 转换：双条记录
                # from 行 (shares 负)：扣份额，不扣现金（类 sell 但无现金变动）
                # to 行 (shares 正)：新建持仓行，不扣现金（类 buy 但无现金变动）
                if (trade.confirmed_shares or 0) < 0:
                    remaining = _deduct_shares(
                        current_rows, trade.security_code,
                        trade.confirmed_shares or 0.0, user_id, current_date,
                        warning_prefix="转换from",
                    )
                    # 超卖：创建负持仓行
                    if remaining > 0:
                        uid_counter += 1
                        neg_row = _create_holding_row(
                            db, user_id, current_date, trade.security_code,
                            trade.security_name, -remaining, uid_counter,
                        )
                        nav_price = trade.nav_price or 0.0
                        neg_row.price = nav_price
                        neg_row.price_cny = nav_price
                        neg_row.amount_cny = round(-remaining * nav_price, 2)
                        current_rows.append(neg_row)
                    # 不扣现金
                elif (trade.confirmed_shares or 0) > 0:
                    uid_counter += 1
                    new_row = _create_holding_row(
                        db, user_id, current_date, trade.security_code,
                        trade.security_name, trade.confirmed_shares or 0.0, uid_counter,
                    )
                    current_rows.append(new_row)
                    # 不扣现金

            elif trade.trade_type == "others":
                # 其他：按符号通用处理（LLM 已设好符号）
                shares_val = trade.confirmed_shares or 0.0
                amt_val = trade.confirmed_amount or 0.0
                if shares_val > 0:
                    # 份额+：新建持仓行
                    uid_counter += 1
                    new_row = _create_holding_row(
                        db, user_id, current_date, trade.security_code,
                        trade.security_name, shares_val, uid_counter,
                    )
                    current_rows.append(new_row)
                elif shares_val < 0:
                    # 份额-：扣份额
                    remaining = _deduct_shares(
                        current_rows, trade.security_code, shares_val,
                        user_id, current_date, warning_prefix="others",
                    )
                    # 超卖：创建负持仓行
                    if remaining > 0:
                        uid_counter += 1
                        neg_row = _create_holding_row(
                            db, user_id, current_date, trade.security_code,
                            trade.security_name, -remaining, uid_counter,
                        )
                        nav_price = trade.nav_price or 0.0
                        neg_row.price = nav_price
                        neg_row.price_cny = nav_price
                        neg_row.amount_cny = round(-remaining * nav_price, 2)
                        current_rows.append(neg_row)
                # 金额按符号处理
                if amt_val > 0 and cash_row:
                    cash_row.quantity += abs(amt_val)
                elif amt_val < 0 and cash_row:
                    cash_row.quantity -= abs(amt_val)

            else:
                logger.warning(
                    f"未知 trade_type: {trade.trade_type}, trade_id={trade.id}, "
                    f"user={user_id}, date={current_date}"
                )

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
                # quantity=0（全部卖出）时必须清零，否则保留旧金额
                if row.price_cny is not None and row.quantity:
                    row.amount_cny = round(row.quantity * row.price_cny, 2)
                else:
                    row.amount_cny = 0.0

        # 写入当日快照（过滤 quantity=0 的非现金行，已全部卖出不存在了）
        for row in current_rows:
            if not row.is_cash and (row.quantity or 0.0) == 0.0:
                continue
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
            "holding_uid": r.holding_uid,
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
