"""overseas_fund_nav.py — 海外基金 NAV 延迟公布例外规则。

海外市场基金（QDII / 港股通）NAV 公布比 A 股基金晚数天，2 次拉取规则对它们
太严格。本模块为持仓中的海外基金提供"门外规则"：

  1. 名称关键词识别海外基金（QDII / 港股通 / 恒生 / 港股 / 纳斯达克 / 标普）
  2. 回拉最近 5 个 CN 交易日的 NAV（东财 lsjz 直连，绕过 akshare）
  3. 与已落地 fund_daily_nav 比对，任一字段不同且新值有效 → 覆写
  4. 同步覆写 PriceCache[code, td].close_px
  5. 返回受影响 trade_date 列表，供调用方级联重算 fund_drill_snapshot

识别方式说明：经探查，东财 API 类型分类对港股通基金天生失效（港股通归
"指数型-股票"，无海外标记），名称关键词匹配是唯一 9/9 全中方案。详见
.trae/documents/overseas-fund-nav-delay-rule.md §2。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from models import FundDailyNav, Holding, PriceCache, SecurityMaster
from services.fund_nav_fetcher import fetch_nav_all, parse_nav_row
from services.trading_calendar import is_trading_day

logger = logging.getLogger(__name__)

OVERSEAS_NAME_KEYWORDS = ["QDII", "港股通", "恒生", "港股", "纳斯达克", "标普"]


def is_overseas_fund(name: str) -> bool:
    """名称关键词匹配识别海外基金（QDII / 港股通）。"""
    return any(kw in name for kw in OVERSEAS_NAME_KEYWORDS)


def get_overseas_fund_holdings(db: Session) -> list[Holding]:
    """持仓中的海外基金（按 security_code 去重）。

    优先取 SecurityMaster.security_name；缺失时 fallback Holding.security_name。
    """
    rows = (
        db.query(Holding, SecurityMaster.security_name)
        .outerjoin(SecurityMaster, SecurityMaster.security_code == Holding.security_code)
        .all()
    )
    seen: set[str] = set()
    out: list[Holding] = []
    for h, sm_name in rows:
        if h.security_code in seen:
            continue
        name = sm_name or h.security_name or ""
        if name and is_overseas_fund(name):
            seen.add(h.security_code)
            out.append(h)
    return out


def _recent_trading_days(db: Session, end_date: date, n: int = 5) -> list[date]:
    """从 end_date 向前取 n 个 CN 交易日（含 end_date 若为交易日），降序返回。"""
    days: list[date] = []
    d = end_date
    # 最多回退 n*4+15 天避免死循环（覆盖超长假期）
    for _ in range(n * 4 + 15):
        if is_trading_day("CN", d, db):
            days.append(d)
            if len(days) >= n:
                break
        d -= timedelta(days=1)
    return days


def _find_row(rows: list[dict], td: date) -> dict | None:
    for r in rows:
        if r["trade_date"] == td:
            return r
    return None


def _overwrite_price_cache(db: Session, code: str, td: date, nav: float) -> None:
    """覆写 PriceCache[code, td].close_px = nav；无则插入。"""
    existing = (
        db.query(PriceCache)
        .filter(PriceCache.stock_code == code, PriceCache.trade_date == td)
        .all()
    )
    if existing:
        for pc in existing:
            pc.close_px = nav
    else:
        db.add(PriceCache(
            stock_code=code,
            trade_date=td,
            close_px=nav,
            source="overseas_lookback",
        ))


def lookback_and_overwrite_nav(
    db: Session,
    fund_code: str,
    lookback_days: int = 5,
) -> list[date]:
    """回拉最近 N 个交易日 NAV + 比对 + 覆写 fund_daily_nav & PriceCache。

    返回受影响的 trade_date 列表（供调用方级联重算 snapshot）。
    幂等：相同数据再跑不会重复覆写（比对无变化不入 affected）。
    """
    # 1. 取最近 N 个 CN 交易日窗口
    trading_days = _recent_trading_days(db, date.today(), lookback_days)
    if not trading_days:
        logger.warning("overseas_nav: 无最近 %d 个交易日 for %s", lookback_days, fund_code)
        return []
    start = min(trading_days)
    end = max(trading_days)

    # 2. 东财 lsjz 直连拉窗口内 NAV
    bare_code = fund_code.replace(".OF", "").strip()
    try:
        rows_raw = fetch_nav_all(bare_code, start.isoformat(), end.isoformat())
    except Exception as e:
        logger.warning("overseas_nav: lsjz 拉取失败 %s: %s", fund_code, e)
        return []
    rows = [r for r in (parse_nav_row(x) for x in rows_raw) if r]
    if not rows:
        logger.info("overseas_nav: %s 窗口 %s..%s 无 NAV 数据", fund_code, start, end)
        return []

    # 3. 比对 + 覆写 fund_daily_nav
    existing = {
        r.trade_date: r
        for r in db.query(FundDailyNav).filter_by(fund_code=fund_code).all()
    }
    affected: list[date] = []
    for r in rows:
        td = r["trade_date"]
        if td in existing:
            e = existing[td]
            # 比对策略（同 pull_fund_nav_em.py）：任一字段不同且新值有效 → 覆写
            changed = (
                e.nav != r["nav"]
                or (e.accumulated_nav is None and r["accumulated_nav"] is not None)
                or (e.accumulated_nav != r["accumulated_nav"] and r["accumulated_nav"] is not None)
                or (e.daily_return != r["daily_return"] and r["daily_return"] is not None)
            )
            if changed:
                e.nav = r["nav"]
                if r["accumulated_nav"] is not None:
                    e.accumulated_nav = r["accumulated_nav"]
                if r["daily_return"] is not None:
                    e.daily_return = r["daily_return"]
                affected.append(td)
        else:
            db.add(FundDailyNav(
                fund_code=fund_code,
                trade_date=td,
                nav=r["nav"],
                accumulated_nav=r["accumulated_nav"],
                daily_return=r["daily_return"],
                source="eastmoney_overseas_lookback",
            ))
            affected.append(td)

    # 4. 同步覆写 PriceCache[code, td].close_px
    for td in affected:
        row = _find_row(rows, td)
        if row is not None:
            _overwrite_price_cache(db, fund_code, td, row["nav"])

    db.commit()
    if affected:
        logger.info(
            "overseas_nav: %s 覆写 %d 天 [%s]",
            fund_code, len(affected), ",".join(d.isoformat() for d in affected),
        )
    return affected
