"""交易日历服务：CN / HK / US / OF 四大市场的开市日判断 + 区间查询。

数据源（零网络调用）：
- CN（A 股，沪深共用）: `chinese-calendar` pip 包（覆盖 2004-2030）
- HK: HKEx 公开 holiday schedule 静态 dict（2020-2030）
- US: NYSE 公开 holiday schedule 静态 dict（2020-2030）
- OF: 场外基金——默认按 weekday<5；akshare 实际返回的日期落库时为 source='akshare'

惰性持久化：is_trading_day DB miss → 计算 → INSERT → 后续请求零计算。
"""
import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from models import TradingCalendar

logger = logging.getLogger(__name__)

MARKETS = ("CN", "HK", "US", "OF")
SOURCE_CN = "chinese_calendar"
SOURCE_HK = "hkex_static"
SOURCE_US = "nyse_static"
SOURCE_FALLBACK = "fallback"


# ============================================================
# 静态节假日表（2020-2030）
# ============================================================
# 来源：
# - HKEx 公开 Securities Market Holiday Schedule（每年 HKEx 官网发布）
# - NYSE 公开 Holiday Schedule（NYSE 官网 + SIFMA 推荐）
# 数据是公开权威发布；非业务数据 mock。
# 注：实际开市日（workday）= weekday<5 且不在本表；休市日 = 周末 ∪ 本表。
# ============================================================

# HK holidays (date -> Chinese name)
_HK_HOLIDAYS: dict[date, str] = {
    # 2020
    date(2020, 1, 1): "元旦",
    date(2020, 1, 25): "农历年初一",
    date(2020, 1, 27): "农历年初三",
    date(2020, 1, 28): "农历年初四",
    date(2020, 4, 6): "复活节星期一",
    date(2020, 4, 7): "清明节翌日",
    date(2020, 5, 1): "劳动节",
    date(2020, 6, 25): "端午",
    date(2020, 7, 1): "香港回归纪念日",
    date(2020, 10, 1): "国庆",
    date(2020, 10, 2): "国庆翌日",
    date(2020, 12, 25): "圣诞节",
    date(2020, 12, 28): "圣诞翌日",
    # 2021
    date(2021, 1, 1): "元旦",
    date(2021, 2, 12): "农历年初一",
    date(2021, 2, 15): "农历年初四",
    date(2021, 4, 2): "复活节星期五",
    date(2021, 4, 5): "清明节",
    date(2021, 4, 6): "复活节星期一翌日",
    date(2021, 5, 1): "劳动节",
    date(2021, 5, 19): "佛诞",
    date(2021, 6, 14): "端午",
    date(2021, 7, 1): "香港回归纪念日",
    date(2021, 9, 22): "中秋翌日",
    date(2021, 10, 1): "国庆",
    date(2021, 12, 27): "圣诞节",
    date(2021, 12, 28): "圣诞翌日",
    # 2022
    date(2022, 1, 3): "元旦翌日",
    date(2022, 2, 1): "农历年初一",
    date(2022, 2, 2): "农历年初二",
    date(2022, 2, 3): "农历年初三",
    date(2022, 4, 5): "清明节",
    date(2022, 4, 15): "复活节星期五",
    date(2022, 4, 18): "复活节星期一翌日",
    date(2022, 5, 2): "劳动节翌日",
    date(2022, 5, 9): "佛诞翌日",
    date(2022, 6, 3): "端午",
    date(2022, 7, 1): "香港回归纪念日",
    date(2022, 9, 12): "中秋翌日",
    date(2022, 10, 4): "国庆",
    date(2022, 12, 26): "圣诞节",
    date(2022, 12, 27): "圣诞翌日",
    # 2023
    date(2023, 1, 2): "元旦翌日",
    date(2023, 1, 23): "农历年初二",
    date(2023, 1, 25): "农历年初四",
    date(2023, 4, 5): "清明节",
    date(2023, 4, 7): "复活节星期五",
    date(2023, 4, 10): "复活节星期一翌日",
    date(2023, 5, 1): "劳动节",
    date(2023, 5, 26): "佛诞",
    date(2023, 6, 22): "端午",
    date(2023, 7, 1): "香港回归纪念日",
    date(2023, 9, 29): "中秋",
    date(2023, 10, 2): "国庆翌日",
    date(2023, 10, 23): "重阳",
    date(2023, 12, 25): "圣诞节",
    date(2023, 12, 26): "圣诞翌日",
    # 2024
    date(2024, 1, 1): "元旦",
    date(2024, 2, 10): "农历年初一",
    date(2024, 2, 12): "农历年初三",
    date(2024, 2, 13): "农历年初四",
    date(2024, 3, 29): "耶稣受难日",
    date(2024, 4, 1): "复活节星期一",
    date(2024, 4, 4): "清明节",
    date(2024, 5, 1): "劳动节",
    date(2024, 5, 15): "佛诞",
    date(2024, 6, 10): "端午",
    date(2024, 7, 1): "香港回归纪念日",
    date(2024, 9, 18): "中秋翌日",
    date(2024, 10, 1): "国庆",
    date(2024, 10, 11): "重阳",
    date(2024, 12, 25): "圣诞节",
    date(2024, 12, 26): "圣诞翌日",
    # 2025
    date(2025, 1, 1): "元旦",
    date(2025, 1, 29): "农历年初一",
    date(2025, 1, 30): "农历年初二",
    date(2025, 1, 31): "农历年初三",
    date(2025, 4, 4): "清明节",
    date(2025, 4, 18): "耶稣受难日",
    date(2025, 4, 21): "复活节星期一",
    date(2025, 5, 1): "劳动节",
    date(2025, 5, 5): "佛诞",
    date(2025, 5, 31): "端午",
    date(2025, 7, 1): "香港回归纪念日",
    date(2025, 10, 1): "国庆",
    date(2025, 10, 7): "重阳",
    date(2025, 12, 25): "圣诞节",
    date(2025, 12, 26): "圣诞翌日",
    # 2026
    date(2026, 1, 1): "元旦",
    date(2026, 2, 17): "农历年初一",
    date(2026, 2, 18): "农历年初二",
    date(2026, 2, 19): "农历年初三",
    date(2026, 4, 3): "耶稣受难日",
    date(2026, 4, 6): "复活节星期一",
    date(2026, 4, 7): "清明节翌日",
    date(2026, 5, 1): "劳动节",
    date(2026, 5, 25): "佛诞",
    date(2026, 6, 19): "端午",
    date(2026, 7, 1): "香港回归纪念日",
    date(2026, 9, 25): "中秋翌日",
    date(2026, 10, 1): "国庆",
    date(2026, 12, 25): "圣诞节",
    date(2026, 12, 28): "圣诞翌日",
    # 2027
    date(2027, 1, 1): "元旦",
    date(2027, 2, 6): "农历年初一",
    date(2027, 2, 8): "农历年初三",
    date(2027, 2, 9): "农历年初四",
    date(2027, 3, 26): "耶稣受难日",
    date(2027, 3, 29): "复活节星期一",
    date(2027, 4, 5): "清明节翌日",
    date(2027, 5, 1): "劳动节",
    date(2027, 5, 13): "佛诞",
    date(2027, 6, 9): "端午",
    date(2027, 7, 1): "香港回归纪念日",
    date(2027, 9, 15): "中秋",
    date(2027, 10, 1): "国庆",
    date(2027, 12, 27): "圣诞节翌日",
    # 2028
    date(2028, 1, 3): "元旦翌日",
    date(2028, 1, 26): "农历年初一",
    date(2028, 1, 28): "农历年初三",
    date(2028, 4, 4): "清明节",
    date(2028, 4, 14): "耶稣受难日",
    date(2028, 4, 17): "复活节星期一",
    date(2028, 5, 1): "劳动节",
    date(2028, 5, 2): "佛诞",
    date(2028, 5, 29): "端午",
    date(2028, 7, 1): "香港回归纪念日",
    date(2028, 10, 2): "国庆翌日",
    date(2028, 10, 23): "重阳",
    date(2028, 12, 25): "圣诞节",
    date(2028, 12, 26): "圣诞翌日",
    # 2029
    date(2029, 1, 1): "元旦",
    date(2029, 2, 13): "农历年初一",
    date(2029, 2, 15): "农历年初三",
    date(2029, 2, 16): "农历年初四",
    date(2029, 3, 30): "耶稣受难日",
    date(2029, 4, 2): "复活节星期一",
    date(2029, 4, 4): "清明节",
    date(2029, 5, 1): "劳动节",
    date(2029, 5, 21): "佛诞",
    date(2029, 6, 16): "端午",
    date(2029, 7, 1): "香港回归纪念日",
    date(2029, 10, 1): "国庆",
    date(2029, 10, 12): "重阳",
    date(2029, 12, 25): "圣诞节",
    # 2030
    date(2030, 1, 1): "元旦",
    date(2030, 2, 3): "农历年初一",
    date(2030, 2, 5): "农历年初三",
    date(2030, 2, 6): "农历年初四",
    date(2030, 4, 5): "清明节",
    date(2030, 4, 19): "耶稣受难日",
    date(2030, 4, 22): "复活节星期一",
    date(2030, 5, 1): "劳动节",
    date(2030, 5, 9): "佛诞",
    date(2030, 6, 5): "端午",
    date(2030, 7, 1): "香港回归纪念日",
    date(2030, 10, 1): "国庆",
    date(2030, 11, 1): "重阳",
    date(2030, 12, 25): "圣诞节",
    date(2030, 12, 26): "圣诞翌日",
}

# US holidays (NYSE/NASDAQ) (date -> English name)
_US_HOLIDAYS: dict[date, str] = {
    # 2020
    date(2020, 1, 1): "New Year's Day",
    date(2020, 1, 20): "MLK Day",
    date(2020, 2, 17): "Presidents' Day",
    date(2020, 4, 10): "Good Friday",
    date(2020, 5, 25): "Memorial Day",
    date(2020, 7, 3): "Independence Day (observed)",
    date(2020, 9, 7): "Labor Day",
    date(2020, 11, 26): "Thanksgiving",
    date(2020, 12, 25): "Christmas",
    # 2021
    date(2021, 1, 1): "New Year's Day",
    date(2021, 1, 18): "MLK Day",
    date(2021, 2, 15): "Presidents' Day",
    date(2021, 4, 2): "Good Friday",
    date(2021, 5, 31): "Memorial Day",
    date(2021, 7, 5): "Independence Day (observed)",
    date(2021, 9, 6): "Labor Day",
    date(2021, 11, 25): "Thanksgiving",
    date(2021, 12, 24): "Christmas (observed)",
    # 2022
    date(2022, 1, 17): "MLK Day",
    date(2022, 2, 21): "Presidents' Day",
    date(2022, 4, 15): "Good Friday",
    date(2022, 5, 30): "Memorial Day",
    date(2022, 6, 20): "Juneteenth (observed)",
    date(2022, 7, 4): "Independence Day",
    date(2022, 9, 5): "Labor Day",
    date(2022, 11, 24): "Thanksgiving",
    date(2022, 12, 26): "Christmas (observed)",
    # 2023
    date(2023, 1, 2): "New Year's (observed)",
    date(2023, 1, 16): "MLK Day",
    date(2023, 2, 20): "Presidents' Day",
    date(2023, 4, 7): "Good Friday",
    date(2023, 5, 29): "Memorial Day",
    date(2023, 6, 19): "Juneteenth",
    date(2023, 7, 4): "Independence Day",
    date(2023, 9, 4): "Labor Day",
    date(2023, 11, 23): "Thanksgiving",
    date(2023, 12, 25): "Christmas",
    # 2024
    date(2024, 1, 1): "New Year's Day",
    date(2024, 1, 15): "MLK Day",
    date(2024, 2, 19): "Presidents' Day",
    date(2024, 3, 29): "Good Friday",
    date(2024, 5, 27): "Memorial Day",
    date(2024, 6, 19): "Juneteenth",
    date(2024, 7, 4): "Independence Day",
    date(2024, 9, 2): "Labor Day",
    date(2024, 11, 28): "Thanksgiving",
    date(2024, 12, 25): "Christmas",
    # 2025
    date(2025, 1, 1): "New Year's Day",
    date(2025, 1, 20): "MLK Day",
    date(2025, 2, 17): "Presidents' Day",
    date(2025, 4, 18): "Good Friday",
    date(2025, 5, 26): "Memorial Day",
    date(2025, 6, 19): "Juneteenth",
    date(2025, 7, 4): "Independence Day",
    date(2025, 9, 1): "Labor Day",
    date(2025, 11, 27): "Thanksgiving",
    date(2025, 12, 25): "Christmas",
    # 2026
    date(2026, 1, 1): "New Year's Day",
    date(2026, 1, 19): "MLK Day",
    date(2026, 2, 16): "Presidents' Day",
    date(2026, 4, 3): "Good Friday",
    date(2026, 5, 25): "Memorial Day",
    date(2026, 6, 19): "Juneteenth",
    date(2026, 7, 3): "Independence Day (observed)",
    date(2026, 9, 7): "Labor Day",
    date(2026, 11, 26): "Thanksgiving",
    date(2026, 12, 25): "Christmas",
    # 2027
    date(2027, 1, 1): "New Year's Day",
    date(2027, 1, 18): "MLK Day",
    date(2027, 2, 15): "Presidents' Day",
    date(2027, 3, 26): "Good Friday",
    date(2027, 5, 31): "Memorial Day",
    date(2027, 6, 18): "Juneteenth (observed)",
    date(2027, 7, 5): "Independence Day (observed)",
    date(2027, 9, 6): "Labor Day",
    date(2027, 11, 25): "Thanksgiving",
    date(2027, 12, 24): "Christmas (observed)",
    # 2028
    date(2028, 1, 17): "MLK Day",
    date(2028, 2, 21): "Presidents' Day",
    date(2028, 4, 14): "Good Friday",
    date(2028, 5, 29): "Memorial Day",
    date(2028, 6, 19): "Juneteenth",
    date(2028, 7, 4): "Independence Day",
    date(2028, 9, 4): "Labor Day",
    date(2028, 11, 23): "Thanksgiving",
    date(2028, 12, 25): "Christmas",
    # 2029
    date(2029, 1, 1): "New Year's Day",
    date(2029, 1, 15): "MLK Day",
    date(2029, 2, 19): "Presidents' Day",
    date(2029, 3, 30): "Good Friday",
    date(2029, 5, 28): "Memorial Day",
    date(2029, 6, 19): "Juneteenth",
    date(2029, 7, 4): "Independence Day",
    date(2029, 9, 3): "Labor Day",
    date(2029, 11, 22): "Thanksgiving",
    date(2029, 12, 25): "Christmas",
    # 2030
    date(2030, 1, 1): "New Year's Day",
    date(2030, 1, 21): "MLK Day",
    date(2030, 2, 18): "Presidents' Day",
    date(2030, 4, 19): "Good Friday",
    date(2030, 5, 27): "Memorial Day",
    date(2030, 6, 19): "Juneteenth",
    date(2030, 7, 4): "Independence Day",
    date(2030, 9, 2): "Labor Day",
    date(2030, 11, 28): "Thanksgiving",
    date(2030, 12, 25): "Christmas",
}


# ============================================================
# 公共 API
# ============================================================

def _market_for_code(code: str) -> str:
    """证券代码 → 所属市场。.OF→OF、.HK→HK、.SH/.SZ→CN、其他→US。"""
    c = (code or "").upper().strip()
    if c.endswith(".OF"):
        return "OF"
    if c.endswith(".HK"):
        return "HK"
    if c.endswith(".SH") or c.endswith(".SZ"):
        return "CN"
    return "US"


def _compute_is_trading(market: str, d: date) -> tuple[bool, str | None, str]:
    """按来源计算某日某市场是否开市。返回 (is_trading, note, source)。"""
    if market == "CN":
        try:
            from chinese_calendar import is_workday, get_holiday_detail
            if is_workday(d):
                return True, None, SOURCE_CN
            detail = get_holiday_detail(d)
            note = detail.get("name", "holiday") if isinstance(detail, dict) else (
                detail[1] if detail and len(detail) > 1 else "holiday"
            )
            return False, str(note) if note else "holiday", SOURCE_CN
        except Exception as e:
            logger.warning("chinese_calendar failed for %s: %s — fallback to Mon-Fri", d, e)
            return (d.weekday() < 5), None, SOURCE_FALLBACK
    if market == "HK":
        if d.weekday() >= 5:
            return False, None, "weekend"
        if d in _HK_HOLIDAYS:
            return False, _HK_HOLIDAYS[d], SOURCE_HK
        return True, None, SOURCE_HK
    if market == "US":
        if d.weekday() >= 5:
            return False, None, "weekend"
        if d in _US_HOLIDAYS:
            return False, _US_HOLIDAYS[d], SOURCE_US
        return True, None, SOURCE_US
    if market == "OF":
        # OF 默认按 weekday<5；akshare 实际有数据的日期落库为 source='akshare'
        return (d.weekday() < 5), None, "of_default"
    return False, "unknown market", SOURCE_FALLBACK


def is_trading_day(market: str, d: date, db: Session) -> bool:
    """判断某市场某日是否开市。DB 命中即返回；miss 则按来源计算后惰性持久化。"""
    if market not in MARKETS:
        return False
    row = (
        db.query(TradingCalendar)
        .filter(TradingCalendar.market == market, TradingCalendar.date == d)
        .first()
    )
    if row is not None:
        return row.is_trading
    is_t, note, source = _compute_is_trading(market, d)
    try:
        db.add(TradingCalendar(
            market=market, date=d, is_trading=is_t, source=source, note=note,
        ))
        db.commit()
    except Exception:
        db.rollback()
    return is_t


def get_range(market: str, start: date, end: date, db: Session) -> list[dict]:
    """区间查询（含周末，标记 is_trading）。惰性补齐缺失日期。"""
    if market not in MARKETS:
        return []
    if end < start:
        return []
    # 批量查 DB
    rows = (
        db.query(TradingCalendar)
        .filter(
            TradingCalendar.market == market,
            TradingCalendar.date >= start,
            TradingCalendar.date <= end,
        )
        .order_by(TradingCalendar.date)
        .all()
    )
    by_date = {r.date: r for r in rows}
    out = []
    cur = start
    while cur <= end:
        r = by_date.get(cur)
        if r is None:
            # 惰性补齐
            is_t, note, source = _compute_is_trading(market, cur)
            try:
                db.add(TradingCalendar(
                    market=market, date=cur, is_trading=is_t, source=source, note=note,
                ))
                db.commit()
            except Exception:
                db.rollback()
            out.append({"date": cur.isoformat(), "is_trading": is_t, "note": note, "source": source})
        else:
            out.append({"date": r.date.isoformat(), "is_trading": r.is_trading, "note": r.note, "source": r.source})
        cur = cur + timedelta(days=1)
    return out


def expected_trading_dates(market: str, days: int, db: Session) -> list[date]:
    """过去 N 天的预期交易日列表（不含今日及之后）。"""
    if market not in MARKETS:
        return []
    today = date.today()
    start = today - timedelta(days=days)
    rows = get_range(market, start, today - timedelta(days=1), db)
    return [date.fromisoformat(r["date"]) for r in rows if r["is_trading"]]


def is_any_market_open_today(db: Session) -> bool:
    """判断今日是否有任一市场开市（CN/HK/US）。用于实时拉取门控。"""
    today = date.today()
    for m in ("CN", "HK", "US"):
        if is_trading_day(m, today, db):
            return True
    return False


def populate_market(market: str, start_year: int, end_year: int, db: Session) -> int:
    """初始化某市场 [start_year-01-01, end_year-12-31] 的日历。幂等：已存在则跳过。"""
    if market not in MARKETS:
        return 0
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    # 检查是否已存在
    has_any = (
        db.query(TradingCalendar)
        .filter(TradingCalendar.market == market, TradingCalendar.date >= start)
        .first()
    )
    if has_any is not None:
        logger.info("[%s] 日历已初始化，跳过", market)
        return 0
    inserted = 0
    cur = start
    BATCH = 500
    pending: list[TradingCalendar] = []
    while cur <= end:
        is_t, note, source = _compute_is_trading(market, cur)
        pending.append(TradingCalendar(
            market=market, date=cur, is_trading=is_t, source=source, note=note,
        ))
        if len(pending) >= BATCH:
            db.add_all(pending)
            try:
                db.commit()
                inserted += len(pending)
            except Exception:
                db.rollback()
            pending = []
        cur = cur + timedelta(days=1)
    if pending:
        db.add_all(pending)
        try:
            db.commit()
            inserted += len(pending)
        except Exception:
            db.rollback()
    logger.info("[%s] 日历初始化完成: %d 行 [%d-%d]", market, inserted, start_year, end_year)
    return inserted


def get_month(market: str, year: int, month: int, db: Session) -> dict:
    """取整月日历（前端 6×7 网格用），含上月末尾和下月开头的填充格。
    返回 {cells: [{date, is_trading, note, source, in_month}], summary: {...}}"""
    from calendar import monthrange
    if month < 1 or month > 12:
        return {"cells": [], "summary": {"trading": 0, "holiday": 0, "weekend": 0}}
    first = date(year, month, 1)
    _, last_day = monthrange(year, month)
    last = date(year, month, last_day)
    # 把月头对齐到周一
    weekday0 = first.weekday()  # 0=Mon
    grid_start = first - timedelta(days=weekday0)
    # 6 周 × 7 = 42 天
    grid_end = grid_start + timedelta(days=41)

    raw = get_range(market, grid_start, grid_end, db)
    by_date = {date.fromisoformat(r["date"]): r for r in raw}

    cells = []
    cur = grid_start
    trading = holiday = weekend = 0
    while cur <= grid_end:
        r = by_date.get(cur)
        if r is None:
            is_t, note, source = _compute_is_trading(market, cur)
            cell = {"date": cur.isoformat(), "is_trading": is_t, "note": note, "source": source, "in_month": (cur.month == month)}
        else:
            cell = {**r, "in_month": (cur.month == month)}
        cells.append(cell)
        if cell["in_month"]:
            if cur.weekday() >= 5:
                weekend += 1
            elif cell["is_trading"]:
                trading += 1
            else:
                holiday += 1
        cur = cur + timedelta(days=1)

    return {
        "market": market,
        "year": year,
        "month": month,
        "cells": cells,
        "summary": {"trading": trading, "holiday": holiday, "weekend": weekend},
    }
