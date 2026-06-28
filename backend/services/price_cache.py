"""实时价格公共缓冲层

多用户共享的价格缓存，避免每个用户刷新都调 API。

设计：
- TTL 15 分钟：expires_at = last_updated + 15min
- 查询流程：缓存命中（expires_at > now）→ 返回；过期 → 调 API → 更新缓存 → 返回
- 价格不变（容差 0.0001）→ 正常续期 15min（避免收盘后反复调 API）
- API 失败 → 返回过期缓存 + 短推迟 5min（避免短时间内反复重试）

用法：
    from services.price_cache import get_realtime_price
    price, source, status = get_realtime_price(db, "007339.OF", "a_share_equity", "CNY")
    # status: "hit" / "refreshed" / "stale" / "miss"
"""
from datetime import datetime, timedelta
import logging
from sqlalchemy.orm import Session
from models import RealtimePriceCache

logger = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================
TTL_MINUTES = 15          # 正常 TTL
FAIL_RETRY_MINUTES = 5    # API 失败时短推迟
PRICE_TOLERANCE = 0.0001  # 价格不变容差


# ============================================================
# 公共 API
# ============================================================
def get_realtime_price(db: Session, code: str, asset_type: str, currency: str) -> tuple:
    """获取实时价格（公共缓冲层）

    价格策略：
    - 公募基金(.OF)：无实时价格 → 查 FundDailyNav 上一日收盘净值，不走 TTL 缓存
    - 股票/ETF：有实时价格 → 走 RealtimePriceCache（15min TTL，腾讯接口）

    Args:
        db: 数据库 session
        code: 证券代码（持仓写法，如 007339.OF / NVDA / 159326.SZ）
        asset_type: 资产类型（a_share_equity / us_stock / hk_equity / ...）
        currency: 计价币种（CNY / USD / HKD）

    Returns:
        (price, source, status)
        - price: float | None
        - source: str | None（tencent / fund_daily_nav）
        - status: "hit"（缓存命中）/ "refreshed"（API刷新）/ "stale"（API失败返回过期缓存）/ "miss"（无缓存且API失败）/ "nav"（基金净值直查）
    """
    # 公募基金（场外）：无实时价格，直接查 FundDailyNav 上一日收盘净值
    if code.endswith(".OF"):
        return _get_fund_nav_from_db(db, code)

    now = datetime.utcnow()

    # 1. 查缓存
    cache = db.query(RealtimePriceCache).filter(RealtimePriceCache.code == code).first()

    # 2. 命中（未过期）
    if cache and cache.expires_at > now:
        return cache.price, cache.source, "hit"

    # 3. 过期或不存在 → 调 API
    new_price, source = _fetch_price_from_api(db, code, asset_type)

    # 4. API 成功
    if new_price is not None and new_price > 0:
        prev_price = cache.price if cache else None
        # 判断价格不变（容差 0.0001）
        is_unchanged = (
            prev_price is not None
            and abs(new_price - prev_price) < PRICE_TOLERANCE
        )
        # 更新缓存（价格不变也续期 15min，避免收盘后反复调 API）
        new_expires = now + timedelta(minutes=TTL_MINUTES)
        if cache:
            cache.prev_price = prev_price
            cache.price = new_price
            cache.source = source
            cache.last_updated = now
            cache.expires_at = new_expires
        else:
            cache = RealtimePriceCache(
                code=code,
                price=new_price,
                prev_price=prev_price,
                source=source,
                last_updated=now,
                expires_at=new_expires,
            )
            db.add(cache)
        db.commit()
        if is_unchanged:
            logger.debug(f"price_cache: {code} price unchanged ({new_price}), renewed TTL")
        else:
            logger.debug(f"price_cache: {code} refreshed to {new_price} from {source}")
        return new_price, source, "refreshed"

    # 5. API 失败 → 返回过期缓存 + 短推迟 5min
    if cache:
        cache.expires_at = now + timedelta(minutes=FAIL_RETRY_MINUTES)
        db.commit()
        logger.warning(f"price_cache: {code} API failed, returning stale {cache.price} (retry in {FAIL_RETRY_MINUTES}min)")
        return cache.price, cache.source, "stale"

    # 6. 无缓存且 API 失败
    logger.warning(f"price_cache: {code} API failed and no cache available")
    return None, None, "miss"


def invalidate_cache_for(db: Session, code: str) -> None:
    """手动失效某 code 的缓存（管理员强制刷新时用）"""
    cache = db.query(RealtimePriceCache).filter(RealtimePriceCache.code == code).first()
    if cache:
        cache.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()


def get_cache_stats(db: Session) -> dict:
    """获取缓存统计（监控用）"""
    now = datetime.utcnow()
    from sqlalchemy import func
    total = db.query(func.count(RealtimePriceCache.code)).scalar() or 0
    fresh = db.query(func.count(RealtimePriceCache.code)).filter(
        RealtimePriceCache.expires_at > now
    ).scalar() or 0
    stale = total - fresh
    return {"total": total, "fresh": fresh, "stale": stale}


# ============================================================
# 内部：从 API 获取价格（仅股票/ETF，.OF 走 _get_fund_nav_from_db）
# ============================================================
def _fetch_price_from_api(db: Session, code: str, asset_type: str) -> tuple:
    """从腾讯接口获取股票/ETF 实时价格

    价格策略：
    - 开放式基金(.OF) 无实时价格，不在此处理（由 get_realtime_price 走 FundDailyNav）
    - 股票/ETF 优先腾讯接口

    Returns:
        (price, source) — price 为 None 表示获取失败
    """
    from crawlers.price_data import fetch_tencent_quote

    # .OF 基金不调实时 API（无实时价格）
    if code.endswith(".OF"):
        return None, None

    # 股票/ETF 优先腾讯接口（US stocks / US ETF / A股 ETF / 港股 / A股）
    info = fetch_tencent_quote(code)
    if info and info.get("price"):
        return float(info["price"]), "tencent"

    return None, None


def _get_fund_nav_from_db(db: Session, code: str) -> tuple:
    """查 FundDailyNav 表获取基金上一日收盘净值

    公募基金（场外）无实时价格，使用定时任务拉取的上一日收盘净值。
    不走 RealtimePriceCache（FundDailyNav 本身是 DB 查询，足够快）。

    注意：FundDailyNav.fund_code 存的是带后缀的持仓写法（如 160424.OF），
    与 holdings.security_code 一致，直接用 code 查询。

    Returns:
        (nav, source, status)
        - status: "nav"（命中 FundDailyNav）/ "miss"（FundDailyNav 无数据）
    """
    from models import FundDailyNav

    # fund_code 存的是带后缀的完整代码（如 160424.OF），直接用 code 查
    row = (
        db.query(FundDailyNav)
        .filter(FundDailyNav.fund_code == code)
        .order_by(FundDailyNav.trade_date.desc())
        .first()
    )
    # Fallback: 尝试去掉后缀（兼容历史数据可能存的纯数字代码）
    if not row:
        fund_code_bare = code.replace(".OF", "").replace(".SZ", "").replace(".SH", "")
        row = (
            db.query(FundDailyNav)
            .filter(FundDailyNav.fund_code == fund_code_bare)
            .order_by(FundDailyNav.trade_date.desc())
            .first()
        )
    if row and row.nav and row.nav > 0:
        return row.nav, "fund_daily_nav", "nav"
    # Fallback: 累计净值（QDII/bond 可能只有累计净值）
    if row and row.accumulated_nav and row.accumulated_nav > 0:
        return row.accumulated_nav, "fund_daily_nav", "nav"
    logger.warning(f"price_cache: {code} no nav in FundDailyNav")
    return None, None, "miss"


# ============================================================
# 混合取价：交易时段用实时价，非交易时段用收盘价
# ============================================================
def get_latest_price(db: Session, code: str, asset_type: str, currency: str) -> tuple:
    """获取最新价格（交易时段用实时价，非交易时段用收盘价）

    解决问题：腾讯 API 在非交易时段返回 A 股盘中实时价（可能是盘中最低点），
    而非收盘价。总览 KPI 需要在非交易时段用收盘价，交易时段用实时价。

    策略：
    - .OF 基金：直接查 FundDailyNav（同 get_realtime_price）
    - A 股（.SH/.SZ）：
      - 交易时段（北京时间 9:30-15:00，周一至周五）→ 调 get_realtime_price（实时价）
      - 非交易时段 → 查 PriceCache 最新 close_px（收盘价）
    - 其他（美股/港股）：调 get_realtime_price（保持现状）

    Returns:
        (price, source, status)
        - status: "realtime" / "close" / "nav" / "miss"
    """
    # .OF 基金：直接查 FundDailyNav
    if code.endswith(".OF"):
        return get_realtime_price(db, code, asset_type, currency)

    # A 股（.SH/.SZ）：交易时段用实时价，非交易时段用收盘价
    if code.endswith(".SH") or code.endswith(".SZ"):
        if _is_cn_trading_hours():
            price, source, _ = get_realtime_price(db, code, asset_type, currency)
            if price and price > 0:
                return price, source, "realtime"
            # 实时价获取失败 → 回退收盘价
        # 非交易时段 或 实时价失败 → 查 PriceCache 最新收盘价
        close_price = _get_close_from_price_cache(db, code)
        if close_price:
            return close_price, "price_cache_close", "close"
        # 收盘价也无 → 最终回退 get_realtime_price
        return get_realtime_price(db, code, asset_type, currency)

    # 其他（美股/港股）：保持现状
    return get_realtime_price(db, code, asset_type, currency)


def _is_cn_trading_hours() -> bool:
    """判断当前是否在 A 股交易时段（北京时间 9:30-15:00，周一至周五）"""
    from datetime import datetime, timezone, timedelta
    cn_tz = timezone(timedelta(hours=8))
    now_cn = datetime.now(cn_tz)
    # 周末
    if now_cn.weekday() >= 5:
        return False
    # 交易时段 9:30-15:00
    current_minutes = now_cn.hour * 60 + now_cn.minute
    return 570 <= current_minutes <= 900  # 9:30=570, 15:00=900


def _get_close_from_price_cache(db: Session, code: str) -> float | None:
    """查 PriceCache 最新 close_px（跳过 close_px=NULL 的 intraday 行）"""
    from models import PriceCache
    row = (
        db.query(PriceCache)
        .filter(
            PriceCache.stock_code == code,
            PriceCache.close_px.isnot(None),
            PriceCache.close_px > 0,
        )
        .order_by(PriceCache.trade_date.desc())
        .first()
    )
    return row.close_px if row else None
