"""东财资讯数据爬虫 (a-stock-data skill §5.1 + §5.3)

提供：
- fetch_global_flash_news()  -- 东财 7x24 全球快讯 (替代已下线财联社快讯)
- fetch_stock_news(code)     -- 个股新闻 (JSONP 接口 search-api-web)

风控: 所有东财请求走 crawlers._http.em_get(), 自动节流 + Keep-Alive + 失败重试.
已知坑 (skill §5.1 #18): 部分大陆住宅 IP 会被间歇风控, 代码对空结果安全返回 [].
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from crawlers._http import em_get
from config import EM_GLOBAL_NEWS_URL, EM_STOCK_NEWS_URL

logger = logging.getLogger(__name__)


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return _HTML_TAG_RE.sub("", s)


def _parse_eastmoney_time(s: Any) -> datetime:
    """解析东财时间字段. 多种格式兜底, 失败返回 utcnow()."""
    if not s:
        return datetime.utcnow()
    if isinstance(s, (int, float)):
        ts = float(s)
        if ts > 1e12:
            ts = ts / 1000.0
        try:
            return datetime.utcfromtimestamp(ts)
        except (ValueError, OSError):
            return datetime.utcnow()
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.utcnow()


# ---------- 7x24 全球快讯 (skill §5.3) ----------

def fetch_global_flash_news(page_size: int = 50) -> list[dict]:
    """东财全球财经资讯 7x24 滚动.

    返回: [{title, summary, source, url, published_at: datetime}, ...]
    """
    params = {
        "client": "web",
        "biz": "web_724",
        "fastColumn": "102",
        "sortEnd": "",
        "pageSize": str(page_size),
        "req_trace": str(uuid.uuid4()),
    }
    headers = {"Referer": "https://kuaixun.eastmoney.com/"}
    resp = em_get(EM_GLOBAL_NEWS_URL, params=params, headers=headers, timeout=10)
    if resp is None or resp.status_code != 200:
        logger.warning("全球快讯请求失败 status=%s", resp.status_code if resp else "None")
        return []
    try:
        d = resp.json()
    except Exception as e:
        logger.warning("全球快讯 JSON 解析失败: %s", e)
        return []

    items = (d.get("data") or {}).get("fastNewsList") or []
    rows: list[dict] = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        rows.append({
            "title": title,
            "summary": (it.get("summary") or "")[:500],
            "source": it.get("mediaName") or "东方财富",
            "url": it.get("url") or "",
            "published_at": _parse_eastmoney_time(it.get("showTime")),
        })
    return rows


# ---------- 个股新闻 (skill §5.1) ----------

def fetch_stock_news(code: str, page_size: int = 20) -> list[dict]:
    """个股相关新闻 (JSONP 接口).

    注意: 东财实际返回里 result.cmsArticleWebOld 直接就是文章列表 (V3.2.1 修复).
    失败时返回 [].
    """
    cb = "jQuery_news"
    inner_params = json.dumps({
        "uid": "",
        "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": page_size,
                "preTag": "",
                "postTag": "",
            }
        },
    }, separators=(",", ":"))
    params = {"cb": cb, "param": inner_params}
    headers = {"Referer": "https://so.eastmoney.com/"}
    resp = em_get(EM_STOCK_NEWS_URL, params=params, headers=headers, timeout=15)
    if resp is None or resp.status_code != 200:
        logger.warning("个股新闻请求失败 code=%s status=%s", code, resp.status_code if resp else "None")
        return []
    text = resp.text
    try:
        l = text.index("(")
        r = text.rindex(")")
        d = json.loads(text[l + 1:r])
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("个股新闻 JSONP 解析失败 code=%s: %s", code, e)
        return []

    articles = (d.get("result") or {}).get("cmsArticleWebOld") or []
    rows: list[dict] = []
    for a in articles:
        title = _strip_html(a.get("title", "")).strip()
        if not title:
            continue
        rows.append({
            "title": title,
            "summary": _strip_html(a.get("content", ""))[:500],
            "source": a.get("mediaName") or "东方财富",
            "url": a.get("url") or "",
            "published_at": _parse_eastmoney_time(a.get("date")),
        })
    return rows
