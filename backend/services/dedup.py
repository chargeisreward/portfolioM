"""
通用 dedup 守门工具（Phase 3 of data-pulling refactor）。

非实时 crawler 在抓取前先检查"今日是否已有数据"，避免重复拉取：
- 严格模式（默认）：今天已有则跳过，日志说明跳过原因
- 调用方传 `force=True` 时绕过 dedup（手动强制重拉用）

设计：
- 业务"今天"按本地时间 Asia/Shanghai 算（UTC+8），与 scheduler cron 一致
- DB 字段统一存 UTC（`datetime.utcnow()`），但调用方用本地"今天"比较
- 时间戳字段（fetched_at / updated_at）→ `already_updated_today`
- 日期字段（as_of_date / publish_date / signal_date）→ `already_persisted_today`
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, date as _date, timedelta
from typing import Type, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

logger = logging.getLogger(__name__)

# Asia/Shanghai UTC offset (no DST since 1991)
_TZ_OFFSET_HOURS = 8


def today_midnight_local() -> datetime:
    """返回本地（Asia/Shanghai）今天的 00:00:00。

    DB 字段用 UTC 写入，所以查询时用同样的本地零点做下限：
      `WHERE updated_at >= today_midnight_local()`
    """
    local_now = datetime.utcnow() + timedelta(hours=_TZ_OFFSET_HOURS)
    return datetime.combine(local_now.date(), dt_time.min)


def today_local_date() -> _date:
    """返回本地（Asia/Shanghai）今天的日期。"""
    local_now = datetime.utcnow() + timedelta(hours=_TZ_OFFSET_HOURS)
    return local_now.date()


def already_updated_today(
    db: Session,
    model: Type,
    ts_col_name: str = "updated_at",
    filter_col: Optional[str] = None,
    filter_val=None,
) -> bool:
    """检查 today_midnight_local() 之后是否有任一行记录（按 ts_col）。

    适用于 `Fund.updated_at` / `GlobalFlashNews.fetched_at` 这类 UTC 时间戳字段。
    返回 True 表示今天已经抓过，调用方应当跳过。
    db 为 None 时返回 False（不守门，向后兼容迁移期的旧调用方）。
    """
    if db is None:
        return False
    ts_col = getattr(model, ts_col_name, None)
    if ts_col is None:
        logger.warning("dedup: %s 没有字段 %s，跳过检查", model.__name__, ts_col_name)
        return False
    q = db.query(func.count()).select_from(model).filter(ts_col >= today_midnight_local())
    if filter_col is not None and filter_val is not None:
        fcol = getattr(model, filter_col, None)
        if fcol is not None:
            q = q.filter(fcol == filter_val)
        else:
            logger.warning("dedup: %s 没有字段 %s，跳过 filter", model.__name__, filter_col)
    count = q.scalar() or 0
    return count > 0


def already_persisted_today(
    db: Session,
    model: Type,
    date_col_name: str,
    filter_col: Optional[str] = None,
    filter_val=None,
) -> bool:
    """检查 date_col == today 是否已有记录。

    适用于 `IndexConstituent.as_of_date` / `HotStockSignal.signal_date`
    / `Announcement.publish_date` 这类 Date 字段。
    返回 True 表示今天已经持久化过，调用方应当跳过。
    db 为 None 时返回 False（不守门，向后兼容迁移期的旧调用方）。
    """
    if db is None:
        return False
    date_col = getattr(model, date_col_name, None)
    if date_col is None:
        logger.warning("dedup: %s 没有字段 %s，跳过检查", model.__name__, date_col_name)
        return False
    today = today_local_date()
    q = db.query(func.count()).select_from(model).filter(date_col == today)
    if filter_col is not None and filter_val is not None:
        fcol = getattr(model, filter_col, None)
        if fcol is not None:
            q = q.filter(fcol == filter_val)
        else:
            logger.warning("dedup: %s 没有字段 %s，跳过 filter", model.__name__, filter_col)
    count = q.scalar() or 0
    return count > 0