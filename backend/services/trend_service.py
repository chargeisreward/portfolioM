"""trend_service.py — /api/trend 专用辅助逻辑。

当前职责：
1. 对 .OF 基金，从 fund_daily_nav 补充到 pc_map（PriceCache 无 .OF 数据时），
   使 chart 能正确显示 .OF 基金的资产值（避免"虚假下跌"）。
2. resolve_px：backward-fill 取价，正确处理 close_px=None（休市日被 intraday job
   写入空行）的情况，避免整只 holding 被 skip 导致总资产虚假下跌。

设计要点：
- PriceCache 优先：若 PriceCache 已有该 (code, date) 的数据，fund_daily_nav 不覆盖
- 仅 .OF 基金：非 .OF 证券不查 fund_daily_nav
- 不新增空 entry：若 fund_daily_nav 也无数据，不在 pc_map 中留下空 dict
  （否则下游 eligible 判断会误以为有数据）
- resolve_px None 值 backward-fill：当日 close_px=None 时，不返回 None，
  而是继续向前找最近的真实价（修复 6-27 周六休市日虚假下跌 -4.53% 的 bug）
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from sqlalchemy.orm import Session

from models import FundDailyNav

logger = logging.getLogger(__name__)


def load_of_nav_to_pc_map(
    db: Session,
    pc_map: dict[str, dict[str, float]],
    of_codes: list[str],
    cutoff: date,
) -> None:
    """对 .OF 基金，从 fund_daily_nav 补充到 pc_map（chart 专用）。

    Args:
        db: SQLAlchemy Session
        pc_map: {code: {date_iso: price}} — 会被原地修改
        of_codes: 需要补充 NAV 的 .OF 基金代码列表
        cutoff: 只加载 trade_date >= cutoff 的数据

    规则：
        - PriceCache 优先：pc_map 中已有的 (code, date) 不被覆盖
        - 不新增空 entry：fund_daily_nav 无数据的 code 不进 pc_map
    """
    if not of_codes:
        return

    rows = (
        db.query(FundDailyNav)
        .filter(
            FundDailyNav.fund_code.in_(of_codes),
            FundDailyNav.trade_date >= cutoff,
            FundDailyNav.nav.isnot(None),
        )
        .all()
    )

    # 按基金分组，避免给无数据的基金留下空 dict
    nav_by_code: dict[str, dict[str, float]] = {}
    for r in rows:
        d_iso = r.trade_date.isoformat()
        nav_by_code.setdefault(r.fund_code, {})[d_iso] = r.nav

    # 合并到 pc_map（PriceCache 优先，不覆盖）
    for code, nav_map in nav_by_code.items():
        existing = pc_map.setdefault(code, {})
        for d_iso, nav in nav_map.items():
            if d_iso not in existing:  # PriceCache 优先
                existing[d_iso] = nav


def resolve_px(
    code_map: dict[str, float | None],
    d_iso: str,
    days: int = 90,
    cutoff: date | None = None,
) -> float | None:
    """从 code_map 中取出 d_iso 当日的有效价，None 值触发 backward-fill。

    修复 6-27 周六休市日虚假下跌 -4.53% 的核心 bug：
    intraday_change_pct job 在休市日写入 close_px=None 的空行，
    旧版 `if d_iso in code_map: return code_map[d_iso]` 直接返回 None，
    导致整只 holding 被 skip，总资产虚假缺失。

    新规则：
        - 当日有真实价（非 None）→ 直接返回
        - 当日为 None 或缺失 → 向前找最近的真实价（最多 days+5 天）
        - cutoff 检查：超出窗口提前 break
        - 全找不到 → 返回 None（不编造）

    Args:
        code_map: {date_iso: price} — 可能含 None 值
        d_iso: 目标日期 ISO 字符串
        days: backward-fill 窗口（与 /api/trend 的 days 参数一致）
        cutoff: 窗口左边界，超出则停止向前找
    """
    # 当日真实价优先
    if d_iso in code_map:
        v = code_map[d_iso]
        if v is not None:
            return v
        # None 值：继续 backward-fill（不直接返回 None）

    # 向前找最近的真实价
    try:
        d = date.fromisoformat(d_iso)
    except (ValueError, TypeError):
        return None

    for k in range(1, days + 5):
        nd = (d - timedelta(days=k)).isoformat()
        if nd in code_map:
            v = code_map[nd]
            if v is not None:  # 跳过 None，继续找
                return v
        if cutoff is not None and (d - timedelta(days=k)) < cutoff:
            break
    return None
