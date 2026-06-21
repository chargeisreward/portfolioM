"""巨潮公告爬虫 (a-stock-data skill §7.1)

- orgId 动态查 szse_stock.json (模块级缓存, 6198 只股)
- 老硬编码 (gssx0{code}) 仅作 fallback, 避免 601xxx 段全 0 (#19)
- Referer / Origin 必带, 否则会被拒
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from config import (
    CNINFO_ORGID_URL,
    CNINFO_QUERY_URL,
    EASTMONTH_USER_AGENT,
)
from crawlers._http import em_get, em_post
from models import Announcement
from database import SessionLocal
from services.dedup import already_persisted_today

logger = logging.getLogger(__name__)

# 巨潮 股票 -> orgId 映射 (模块级缓存, 首次调用时拉取一次, 全程复用)
_CNINFO_ORGID_MAP: dict[str, str] = {}


def _load_orgid_map(force: bool = False) -> dict[str, str]:
    """拉取巨潮官方 szse_stock.json. 失败返回空 dict (fallback 走硬编码规则)."""
    global _CNINFO_ORGID_MAP
    if _CNINFO_ORGID_MAP and not force:
        return _CNINFO_ORGID_MAP
    try:
        r = em_get(
            CNINFO_ORGID_URL,
            headers={"User-Agent": EASTMONTH_USER_AGENT},
            timeout=15.0,
        )
        if r.status_code == 200:
            stock_list = r.json().get("stockList") or []
            _CNINFO_ORGID_MAP = {s["code"]: s["orgId"] for s in stock_list if s.get("code")}
            logger.info("巨潮 orgId 映射表已加载: %d 只股", len(_CNINFO_ORGID_MAP))
    except Exception as e:
        logger.warning("巨潮 orgId 映射表拉取失败 (回退硬编码): %s", e)
    return _CNINFO_ORGID_MAP


def _orgid_for(code: str) -> str:
    """查股票真实 orgId. 硬编码仅作 fallback."""
    m = _load_orgid_map()
    org = m.get(code)
    if org:
        return org
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith("8") or code.startswith("4"):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def _ts_to_date(ts) -> str:
    """巨潮 announcementTime 是 Unix 毫秒整数."""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000.0).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            return ""
    return str(ts)[:10] if ts else ""


def fetch_announcements(
    code: str,
    page_size: int = 30,
    se_date: str = "",
    *,
    force: bool = False,
) -> list[dict]:
    """拉取指定股票的公告列表.

    code: 6 位股票代码 (如 688017)
    se_date: 起始日期 "YYYY-MM-DD", 空串=不限
    force: True 跳过 dedup 守门（手动强制重拉）
    返回: [{announcement_id, title, announcement_type, publish_date, url, org_id}, ...]
    """
    # dedup: 今天已抓到该股公告则跳过
    if not force:
        db = SessionLocal()
        try:
            if already_persisted_today(
                db, Announcement, "publish_date",
                filter_col="stock_code", filter_val=code,
            ):
                logger.info("股票 %s 今日公告已抓，跳过", code)
                return []
        finally:
            db.close()

    org_id = _orgid_for(code)
    payload = {
        "stock": f"{code},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": se_date,
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": EASTMONTH_USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    try:
        r = em_post(CNINFO_QUERY_URL, data=payload, headers=headers, timeout=15.0)
    except Exception as e:
        logger.warning("巨潮公告请求失败 code=%s: %s", code, e)
        return []
    if r.status_code != 200:
        logger.warning("巨潮公告返回异常 code=%s status=%d", code, r.status_code)
        return []
    try:
        d = r.json()
    except Exception as e:
        logger.warning("巨潮公告 JSON 解析失败 code=%s: %s", code, e)
        return []

    rows: list[dict] = []
    for item in d.get("announcements") or []:
        title = (item.get("announcementTitle") or "").strip()
        if not title:
            continue
        anno_id = item.get("announcementId") or ""
        rows.append({
            "announcement_id": anno_id,
            "title": title,
            "announcement_type": item.get("announcementTypeName") or "",
            "publish_date": _ts_to_date(item.get("announcementTime")),
            "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={anno_id}",
            "org_id": org_id,
        })
    return rows
