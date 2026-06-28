"""trading_rebuild_service 单元测试。

覆盖 6 个函数的所有分支：
- ensure_initial_snapshot：首次建立 / 幂等 / initial_cash
- fetch_daily_price：PriceCache / FundDailyNav / 缺失 / forward fill
- rebuild_holdings_to_date：申购 / 赎回 / 多日 / 周末 / CASH 为负 / 新证券 / force / 增量 / 同步 Holding
- get_snapshot_for_date / get_snapshot_date_range / get_trades_for_date
"""
import os
import tempfile

import pytest
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
from database import Base
from models import (
    Holding,
    HoldingDailySnapshot,
    Transaction,
    TradingSession,
    PriceCache,
    FundDailyNav,
    SecurityMaster,
)
from services.trading_rebuild_service import (
    ensure_initial_snapshot,
    fetch_daily_price,
    rebuild_holdings_to_date,
    get_snapshot_for_date,
    get_snapshot_date_range,
    get_trades_for_date,
    DEFAULT_START_DATE,
)


@pytest.fixture
def fresh_db():
    """每个测试用独立的临时文件 SQLite（避免 :memory: 多连接隔离问题）。

    参考 backend/tests/test_security_master_service.py 中的 fresh_db fixture。
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    session = TestSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------- ensure_initial_snapshot ----------

def test_ensure_initial_snapshot_builds_from_holding(fresh_db):
    """首次建立：从 Holding 表复制到起始日快照 + CASH 行。"""
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=10000, price=4.5, currency="CNY", amount_cny=45000,
        asset_type="a_share_etf",
    ))
    fresh_db.commit()

    session = ensure_initial_snapshot(fresh_db, 1, date(2025, 7, 19), initial_cash=0.0)

    assert session.initial_snapshot_built is True
    assert session.start_date == date(2025, 7, 19)

    snapshots = fresh_db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == 1,
        HoldingDailySnapshot.as_of_date == date(2025, 7, 19),
    ).all()
    # 1 证券行 + 1 CASH 行
    assert len(snapshots) == 2

    etf_row = next(s for s in snapshots if s.security_code == "510300.SH")
    assert etf_row.quantity == 10000
    assert etf_row.is_initial is True
    assert etf_row.is_cash is False

    cash_row = next(s for s in snapshots if s.security_code == "CASH")
    assert cash_row.quantity == 0.0
    assert cash_row.is_cash is True
    assert cash_row.is_initial is True
    assert cash_row.amount_cny == 0.0


def test_ensure_initial_snapshot_idempotent(fresh_db):
    """幂等性：第二次调用不重复创建快照。"""
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=10000, price=4.5, currency="CNY", amount_cny=45000,
        asset_type="a_share_etf",
    ))
    fresh_db.commit()

    ensure_initial_snapshot(fresh_db, 1, date(2025, 7, 19))
    ensure_initial_snapshot(fresh_db, 1, date(2025, 7, 19))

    count = fresh_db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == 1,
        HoldingDailySnapshot.as_of_date == date(2025, 7, 19),
    ).count()
    assert count == 2  # 1 证券 + 1 CASH，不重复


def test_ensure_initial_snapshot_with_initial_cash(fresh_db):
    """initial_cash 正确写入 CASH 行。"""
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, price=4.0, currency="CNY", amount_cny=4000,
        asset_type="a_share_etf",
    ))
    fresh_db.commit()

    ensure_initial_snapshot(fresh_db, 1, date(2025, 7, 19), initial_cash=5000.0)

    cash_row = fresh_db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == 1,
        HoldingDailySnapshot.as_of_date == date(2025, 7, 19),
        HoldingDailySnapshot.security_code == "CASH",
    ).first()
    assert cash_row.quantity == 5000.0
    assert cash_row.amount_cny == 5000.0


def test_ensure_initial_snapshot_default_start_date(fresh_db):
    """默认起始日为 2025-07-19。"""
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=100, price=4.0, currency="CNY", amount_cny=400,
        asset_type="a_share_etf",
    ))
    fresh_db.commit()

    session = ensure_initial_snapshot(fresh_db, 1)
    assert session.start_date == DEFAULT_START_DATE
    assert session.start_date == date(2025, 7, 19)


# ---------- fetch_daily_price ----------

def test_fetch_daily_price_from_price_cache(fresh_db):
    """PriceCache 命中。"""
    fresh_db.add(PriceCache(
        stock_code="510300.SH", trade_date=date(2025, 7, 18),
        close_px=4.5, source="tencent",
    ))
    fresh_db.commit()

    result = fetch_daily_price(fresh_db, "510300.SH", date(2025, 7, 19))
    assert result is not None
    assert result["price"] == 4.5
    assert result["currency"] == "CNY"
    assert result["fx_rate"] == 1.0
    assert result["price_cny"] == 4.5


def test_fetch_daily_price_from_fund_nav(fresh_db):
    """FundDailyNav 命中（.OF 基金）。"""
    fresh_db.add(FundDailyNav(
        fund_code="110011.OF", trade_date=date(2025, 7, 18),
        nav=1.234, source="akshare",
    ))
    fresh_db.commit()

    result = fetch_daily_price(fresh_db, "110011.OF", date(2025, 7, 19))
    assert result is not None
    assert result["price"] == 1.234
    assert result["currency"] == "CNY"
    assert result["fx_rate"] == 1.0


def test_fetch_daily_price_missing(fresh_db):
    """价格缺失返回 None。"""
    result = fetch_daily_price(fresh_db, "999999.SH", date(2025, 7, 19))
    assert result is None


def test_fetch_daily_price_forward_fill(fresh_db):
    """trade_date <= as_of 取最新一条（forward fill）。"""
    fresh_db.add(PriceCache(
        stock_code="510300.SH", trade_date=date(2025, 7, 10),
        close_px=4.0, source="tencent",
    ))
    fresh_db.add(PriceCache(
        stock_code="510300.SH", trade_date=date(2025, 7, 15),
        close_px=4.5, source="tencent",
    ))
    fresh_db.commit()

    # as_of=7/19，应取 7/15 的 4.5（不是 7/10 的 4.0）
    result = fetch_daily_price(fresh_db, "510300.SH", date(2025, 7, 19))
    assert result["price"] == 4.5


def test_fetch_daily_price_of_fallback_to_nav(fresh_db):
    """OF 代码无 PriceCache 时 fallback 到 FundDailyNav。"""
    # 不加 PriceCache，只加 FundDailyNav
    fresh_db.add(FundDailyNav(
        fund_code="005827.OF", trade_date=date(2025, 7, 18),
        nav=1.567, source="akshare",
    ))
    fresh_db.commit()

    result = fetch_daily_price(fresh_db, "005827.OF", date(2025, 7, 19))
    assert result is not None
    assert result["price"] == 1.567


# ---------- rebuild_holdings_to_date ----------

def _setup_initial_holding(db, user_id=1, code="510300.SH", qty=10000, price=4.5):
    """辅助：建一个起始 Holding 并建起始快照。"""
    db.add(Holding(
        user_id=user_id, security_code=code, security_name="沪深300ETF",
        quantity=qty, price=price, currency="CNY", amount_cny=qty * price,
        asset_type="a_share_etf",
    ))
    db.commit()
    ensure_initial_snapshot(db, user_id, date(2025, 7, 19))


def test_rebuild_single_buy(fresh_db):
    """单笔申购：新建一笔持仓行，现金减少。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="buy", confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.commit()

    result = rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))
    assert result["days_built"] >= 1

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    cash = next(s for s in snap if s["is_cash"])

    # 买入新建一笔：原行 10000 + 新行 1000 = 总 11000
    assert len(etf_rows) == 2  # 原始行 + 交易新建行
    assert sum(r["quantity"] for r in etf_rows) == 11000
    assert cash["quantity"] == -4500  # 0 - 4500


def test_rebuild_single_sell(fresh_db):
    """单笔赎回：份额减少，现金增加。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="sell", confirmed_shares=2000, confirmed_amount=9000,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf = next(s for s in snap if s["security_code"] == "510300.SH")
    cash = next(s for s in snap if s["is_cash"])

    assert etf["quantity"] == 8000  # 10000 - 2000
    assert cash["quantity"] == 9000  # 0 + 9000


def test_rebuild_multi_day(fresh_db):
    """多日重算：买入新建、卖出按 uid 从小大扣减。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="buy", confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 21),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="sell", confirmed_shares=500, confirmed_amount=2300,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 21))

    # 7/20 验证：买入新建一笔
    snap20 = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf20_rows = [s for s in snap20 if s["security_code"] == "510300.SH"]
    assert len(etf20_rows) == 2  # 原始行 + 交易新建行
    assert sum(r["quantity"] for r in etf20_rows) == 11000  # 10000 + 1000

    # 7/21 验证：卖出从原始行（uid 小）扣减
    snap21 = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 21))
    etf21_rows = [s for s in snap21 if s["security_code"] == "510300.SH"]
    cash21 = next(s for s in snap21 if s["is_cash"])
    assert sum(r["quantity"] for r in etf21_rows) == 10500  # 11000 - 500
    assert cash21["quantity"] == -4500 + 2300  # -4500 + 2300 = -2200


def test_rebuild_sell_multi_batch_by_uid(fresh_db):
    """卖出多批次：按 holding_uid 从小到大依次扣减，直至累计=卖出数量。

    场景：同代码两笔原始持仓（uid=1 qty=3000, uid=2 qty=5000）
    卖出 4000 → uid=1 扣完 3000（→0），uid=2 扣 1000（→4000）
    """
    # 建两笔同代码原始持仓（不同 Holding.id → 不同 holding_uid）
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=3000, price=4.5, currency="CNY", amount_cny=3000 * 4.5,
        asset_type="a_share_etf",
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=5000, price=4.5, currency="CNY", amount_cny=5000 * 4.5,
        asset_type="a_share_etf",
    ))
    fresh_db.commit()
    ensure_initial_snapshot(fresh_db, 1, date(2025, 7, 19))

    # 卖出 4000（跨两笔）
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="sell", confirmed_shares=4000, confirmed_amount=18000,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    # 验证：uid 小的先扣完，uid 大的扣剩余
    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    assert len(etf_rows) == 2  # 两笔原始持仓

    # 按 holding_uid 排序验证（uid 小的应扣至 0）
    etf_rows.sort(key=lambda r: (r.get("holding_uid") is None, r.get("holding_uid") or 0))
    # 注：get_snapshot_for_date 不返回 holding_uid，需直接查 DB
    db_rows = fresh_db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == 1,
        HoldingDailySnapshot.as_of_date == date(2025, 7, 20),
        HoldingDailySnapshot.security_code == "510300.SH",
    ).order_by(HoldingDailySnapshot.holding_uid.asc()).all()

    assert len(db_rows) == 2
    assert db_rows[0].holding_uid < db_rows[1].holding_uid
    assert db_rows[0].quantity == 0     # uid 小的先扣完
    assert db_rows[1].quantity == 4000  # uid 大的扣剩余 1000

    # 总量正确
    assert sum(r.quantity for r in db_rows) == 4000  # 8000 - 4000

    # CASH 增加
    cash = next(s for s in snap if s["is_cash"])
    assert cash["quantity"] == 18000  # 0 + 18000


def test_rebuild_sell_buy_then_sell_uid_order(fresh_db):
    """买入新建（uid=递增非 NULL）后卖出：原始 uid 先扣，新建 uid 后扣。

    场景：原始 uid=1 qty=3000 → 买入新建 uid=2 qty=1000 → 卖出 3500
    预期：uid=1 扣 3000（→0），uid=2 扣 500（→500）
    """
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=3000, price=4.5, currency="CNY", amount_cny=3000 * 4.5,
        asset_type="a_share_etf",
    ))
    fresh_db.commit()
    ensure_initial_snapshot(fresh_db, 1, date(2025, 7, 19))

    # 07-20 买入 1000（新建 uid=max_uid+1=2 行，非 NULL）
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="buy", confirmed_shares=1000, confirmed_amount=4500,
    ))
    # 07-21 卖出 3500（跨原始 uid=1 + 交易新建 uid=2）
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 21),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="sell", confirmed_shares=3500, confirmed_amount=15750,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 21))

    # 验证 07-21 快照：按 holding_uid 排序（全部非 NULL）
    db_rows = fresh_db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == 1,
        HoldingDailySnapshot.as_of_date == date(2025, 7, 21),
        HoldingDailySnapshot.security_code == "510300.SH",
    ).order_by(HoldingDailySnapshot.holding_uid.asc()).all()

    assert len(db_rows) == 2  # 原始行 + 买入新建行
    # 全部 holding_uid 非 NULL
    assert all(r.holding_uid is not None for r in db_rows)
    assert db_rows[0].holding_uid < db_rows[1].holding_uid

    assert db_rows[0].quantity == 0     # 原始 uid 小，先扣完 3000
    assert db_rows[1].quantity == 500   # 新建 uid 大，后扣 500（1000-500）
    assert sum(r.quantity for r in db_rows) == 500  # 4000 - 3500


def test_rebuild_weekend_forward_fill(fresh_db):
    """周末 forward fill：无交易无价格，纯复制前一日。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    # 2025-07-19 是周六，07-20 是周日，07-21 是周一
    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 21))

    # 周日应有快照（纯复制周六）
    snap20 = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    assert snap20 is not None
    etf20 = next(s for s in snap20 if s["security_code"] == "510300.SH")
    assert etf20["quantity"] == 10000  # 无交易，沿用


def test_rebuild_cash_negative_allowed(fresh_db):
    """CASH 允许为负（初始现金不足申购金额）。"""
    _setup_initial_holding(fresh_db, qty=100, price=4.5)

    # 设置初始现金 100，申购金额 1000（超出）
    session = fresh_db.query(TradingSession).filter(TradingSession.user_id == 1).first()
    # 重建起始快照带 initial_cash=100
    fresh_db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == 1,
        HoldingDailySnapshot.as_of_date == date(2025, 7, 19),
    ).delete()
    fresh_db.query(TradingSession).filter(TradingSession.user_id == 1).delete()
    fresh_db.commit()
    ensure_initial_snapshot(fresh_db, 1, date(2025, 7, 19), initial_cash=100)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="buy", confirmed_shares=100, confirmed_amount=1000,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    cash = next(s for s in snap if s["is_cash"])
    assert cash["quantity"] == 100 - 1000  # -900
    assert cash["amount_cny"] == -900


def test_rebuild_new_security_trade(fresh_db):
    """新证券交易：自动创建持仓行，asset_type 从 SecurityMaster 查。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    # 预建 SecurityMaster
    fresh_db.add(SecurityMaster(
        security_code="159919.SZ", security_name="沪深300ETF",
        asset_type="a_share_etf", market="CN", is_drillable=True,
    ))
    fresh_db.commit()

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="159919.SZ", security_name="沪深300ETF",
        trade_type="buy", confirmed_shares=5000, confirmed_amount=20000,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    new_row = next(s for s in snap if s["security_code"] == "159919.SZ")
    assert new_row["quantity"] == 5000
    assert new_row["asset_type"] == "a_share_etf"


def test_rebuild_force_full(fresh_db):
    """force=True 全量重算：清除旧快照重新计算。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="buy", confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.commit()

    # 第一次重算
    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    # force 全量重算
    result = rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20), force=True)

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    assert sum(r["quantity"] for r in etf_rows) == 11000  # 10000 + 1000

    # 验证没有重复行（原始 1 行 + 买入新建 1 行 = 2 行）
    count = fresh_db.query(HoldingDailySnapshot).filter(
        HoldingDailySnapshot.user_id == 1,
        HoldingDailySnapshot.as_of_date == date(2025, 7, 20),
        HoldingDailySnapshot.security_code == "510300.SH",
    ).count()
    assert count == 2


def test_rebuild_incremental(fresh_db):
    """增量重算：从 last_rebuild_date+1 开始，只重算新日子。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", trade_type="buy",
        confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.commit()

    # 第一次重算到 7/20
    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    # 新增 7/21 交易
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 21),
        security_code="510300.SH", trade_type="sell",
        confirmed_shares=500, confirmed_amount=2300,
    ))
    fresh_db.commit()

    # 增量重算到 7/21
    result = rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 21))
    assert result["days_built"] == 1  # 只重算 7/21 一天

    snap21 = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 21))
    etf21_rows = [s for s in snap21 if s["security_code"] == "510300.SH"]
    assert sum(r["quantity"] for r in etf21_rows) == 10500  # 11000 - 500


def test_rebuild_syncs_to_holding(fresh_db):
    """重算后同步覆盖到 Holding 表（不同步 CASH 行）。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", trade_type="buy",
        confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.commit()

    result = rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))
    assert result["synced_to_holding"] is True

    holdings = fresh_db.query(Holding).filter(Holding.user_id == 1).all()
    # 买入新建一笔：原始行 + 交易新建行 = 2 行（CASH 不同步）
    assert len(holdings) == 2
    assert all(h.security_code == "510300.SH" for h in holdings)
    assert sum(h.quantity for h in holdings) == 11000
    assert all(h.import_batch == "rebuild_2025-07-20" for h in holdings)


def test_rebuild_no_rollback(fresh_db):
    """增量重算到历史日期不同步 Holding 表（避免回退）。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", trade_type="buy",
        confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 25),
        security_code="510300.SH", trade_type="buy",
        confirmed_shares=500, confirmed_amount=2300,
    ))
    fresh_db.commit()

    # 先重算到 7/25（两次买入各新建一笔：10000 + 1000 + 500 = 11500）
    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 25))
    holdings_25 = fresh_db.query(Holding).filter(Holding.user_id == 1).all()
    assert sum(h.quantity for h in holdings_25) == 11500

    # 再"重算"到 7/22（历史日期，应不同步）
    result = rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 22))
    assert result["synced_to_holding"] is False

    # Holding 表应保持 7/25 的状态
    holdings = fresh_db.query(Holding).filter(Holding.user_id == 1).all()
    assert sum(h.quantity for h in holdings) == 11500


def test_rebuild_target_before_start(fresh_db):
    """target_date 早于起始日：不重算。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    result = rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 18))
    assert result["days_built"] == 0
    assert result["synced_to_holding"] is False


# ---------- 查询函数 ----------

def test_get_snapshot_for_date_none(fresh_db):
    """无快照返回 None。"""
    result = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 19))
    assert result is None


def test_get_snapshot_for_date_returns_list(fresh_db):
    """有快照返回行列表。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    result = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 19))
    assert result is not None
    assert len(result) == 2  # 1 证券 + 1 CASH

    codes = {r["security_code"] for r in result}
    assert "510300.SH" in codes
    assert "CASH" in codes


def test_get_snapshot_date_range(fresh_db):
    """日期范围正确。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)
    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 21))

    rng = get_snapshot_date_range(fresh_db, 1)
    assert rng is not None
    assert rng[0] == date(2025, 7, 19)
    assert rng[1] == date(2025, 7, 21)


def test_get_snapshot_date_range_empty(fresh_db):
    """无快照返回 None。"""
    assert get_snapshot_date_range(fresh_db, 1) is None


def test_get_trades_for_date(fresh_db):
    """查询某日交易。"""
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="buy", confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.commit()

    trades = get_trades_for_date(fresh_db, 1, date(2025, 7, 20))
    assert len(trades) == 1
    assert trades[0]["security_code"] == "510300.SH"
    assert trades[0]["trade_type"] == "buy"
    assert trades[0]["confirmed_shares"] == 1000

    # 无交易的日期返回空列表
    assert get_trades_for_date(fresh_db, 1, date(2025, 7, 21)) == []


def test_get_trades_for_date_multiple(fresh_db):
    """同日多笔交易全部返回。"""
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", trade_type="buy",
        confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="159919.SZ", trade_type="buy",
        confirmed_shares=500, confirmed_amount=2000,
    ))
    fresh_db.commit()

    trades = get_trades_for_date(fresh_db, 1, date(2025, 7, 20))
    assert len(trades) == 2
    codes = {t["security_code"] for t in trades}
    assert codes == {"510300.SH", "159919.SZ"}


# ---------- 新增 trade_type 测试（dividend/split/rights/conversion/others/unknown）----------

def test_rebuild_dividend_increases_cash(fresh_db):
    """dividend：份额不变，金额+ → 加现金。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="dividend", confirmed_shares=0, confirmed_amount=500,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    cash = next(s for s in snap if s["is_cash"])

    # 份额不变（仍 10000），现金增加 500
    assert len(etf_rows) == 1  # 无新建行
    assert etf_rows[0]["quantity"] == 10000
    assert cash["quantity"] == 500  # 0 + 500


def test_rebuild_split_increases_shares(fresh_db):
    """split：份额+，金额不变 → 新建持仓行，不扣现金。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="split", confirmed_shares=2000, confirmed_amount=0,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    cash = next(s for s in snap if s["is_cash"])

    # 新建一行 qty=2000，总份额 = 12000，现金不变
    assert len(etf_rows) == 2  # 原始行 + 新建行
    assert sum(r["quantity"] for r in etf_rows) == 12000  # 10000 + 2000
    assert cash["quantity"] == 0  # 不扣现金


def test_rebuild_rights_like_buy(fresh_db):
    """rights：与 buy 行为一致（份额+，现金-）。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="rights", confirmed_shares=1000, confirmed_amount=4500,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    cash = next(s for s in snap if s["is_cash"])

    # 新建一行 qty=1000，总份额 = 11000，现金减少 4500
    assert len(etf_rows) == 2  # 原始行 + 新建行
    assert sum(r["quantity"] for r in etf_rows) == 11000  # 10000 + 1000
    assert cash["quantity"] == -4500  # 0 - 4500


def test_rebuild_conversion_transfers_shares(fresh_db):
    """conversion：双条记录，from 扣份额不扣现金，to 新建不扣现金。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    # conversion from 行：510300.SH shares=-3000（扣份额）
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="conversion", confirmed_shares=-3000, confirmed_amount=0,
        remarks="convert_to_510500",
    ))
    # conversion to 行：510500.SH shares=+3000（新建持仓）
    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510500.SH", security_name="中证500ETF",
        trade_type="conversion", confirmed_shares=3000, confirmed_amount=0,
        remarks="convert_from_510300",
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf300_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    etf500_rows = [s for s in snap if s["security_code"] == "510500.SH"]
    cash = next(s for s in snap if s["is_cash"])

    # 510300.SH 份额 = 7000（10000 - 3000），无新建行
    assert len(etf300_rows) == 1  # 只有原始行
    assert etf300_rows[0]["quantity"] == 7000

    # 510500.SH 新建一行 qty=3000
    assert len(etf500_rows) == 1
    assert etf500_rows[0]["quantity"] == 3000

    # CASH 不变
    assert cash["quantity"] == 0


def test_rebuild_others_generic_sign_handling(fresh_db):
    """others：按符号通用处理（shares- 扣份额，amount+ 加现金）。"""
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="others", confirmed_shares=-500, confirmed_amount=2000,
    ))
    fresh_db.commit()

    rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    cash = next(s for s in snap if s["is_cash"])

    # 份额减少 500，现金增加 2000
    assert len(etf_rows) == 1  # 无新建行（shares<0 只扣）
    assert etf_rows[0]["quantity"] == 9500  # 10000 - 500
    assert cash["quantity"] == 2000  # 0 + 2000


def test_rebuild_unknown_type_logs_warning(fresh_db, caplog):
    """未知 trade_type：不崩溃，记录 warning，当日快照无新持仓行。"""
    import logging
    _setup_initial_holding(fresh_db, qty=10000, price=4.5)

    fresh_db.add(Transaction(
        user_id=1, trade_date=date(2025, 7, 20),
        security_code="510300.SH", security_name="沪深300ETF",
        trade_type="unknown_type", confirmed_shares=1000, confirmed_amount=0,
    ))
    fresh_db.commit()

    with caplog.at_level(logging.WARNING, logger="services.trading_rebuild_service"):
        result = rebuild_holdings_to_date(fresh_db, 1, date(2025, 7, 20))

    # 不抛异常
    assert result is not None

    # 捕获 WARNING 日志含 "未知 trade_type: unknown_type"
    assert any(
        "未知 trade_type: unknown_type" in r.message
        for r in caplog.records
    ), f"未捕获到未知 trade_type warning，records={[r.message for r in caplog.records]}"

    # 当日快照：无新持仓行，份额不变，CASH 不变
    snap = get_snapshot_for_date(fresh_db, 1, date(2025, 7, 20))
    etf_rows = [s for s in snap if s["security_code"] == "510300.SH"]
    cash = next(s for s in snap if s["is_cash"])
    assert len(etf_rows) == 1  # 只有原始行，无新建
    assert etf_rows[0]["quantity"] == 10000  # 份额不变
    assert cash["quantity"] == 0  # CASH 不变
