"""同花顺热点爬虫 (a-stock-data skill §3.1)

- fetch_hot_stocks(date) -- 当日强势股 + 题材归因 reason (73ms, 零鉴权)

注意:
- GBK 编码 (Server response charset=GBK)
- 仅 User-Agent 即可, 不需要 cookie
- 与 hexin-v 鉴权的 iwencai 选股接口完全无关
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from config import THS_HOT_URL, THS_USER_AGENT
from crawlers._http import ths_get
from models import HotStockSignal
from database import SessionLocal
from services.dedup import already_persisted_today

logger = logging.getLogger(__name__)


def fetch_hot_stocks(date_: str | date | None = None, *, force: bool = False) -> list[dict]:
    """同花顺当日强势股 + 题材归因.

    date_: 'YYYY-MM-DD' 格式, None=今天
    force: True 跳过 dedup 守门（手动强制重拉）
    返回: [{stock_code, stock_name, close, change_pct, turnover_pct,
            amount, dde_net, market, reason_tags, rank}, ...]
    失败/节假日返回 [].
    """
    if date_ is None:
        date_str = date.today().strftime("%Y-%m-%d")
    elif isinstance(date_, date):
        date_str = date_.strftime("%Y-%m-%d")
    else:
        date_str = str(date_)

    # dedup: 今天已抓过则跳过
    if not force:
        db = SessionLocal()
        try:
            if already_persisted_today(db, HotStockSignal, "signal_date"):
                logger.info("热点 %s 已抓，跳过", date_str)
                return []
        finally:
            db.close()

    url = THS_HOT_URL.format(date=date_str)
    headers = {"User-Agent": THS_USER_AGENT}
    resp = ths_get(url, headers=headers, timeout=10)
    if resp is None or resp.status_code != 200:
        logger.warning("同花顺热点请求失败 date=%s status=%s",
                       date_str, resp.status_code if resp else "None")
        return []
    try:
        d = resp.json()
    except Exception as e:
        logger.warning("同花顺热点 JSON 解析失败: %s", e)
        return []
    if d.get("errocode", 0) != 0:
        logger.info("同花顺热点业务错误: %s", d.get("errormsg"))
        return []

    items = d.get("data") or []
    rows: list[dict] = []
    for i, it in enumerate(items, 1):
        rows.append({
            "stock_code": str(it.get("code") or ""),
            "stock_name": it.get("name") or "",
            "close": _safe_float(it.get("close")),
            "change_pct": _safe_float(it.get("zhangfu")),
            "turnover_pct": _safe_float(it.get("huanshou")),
            "amount": _safe_float(it.get("chengjiaoe")),
            "dde_net": _safe_float(it.get("ddejingliang")),
            "market": it.get("market") or "",
            "reason_tags": it.get("reason") or "",
            "rank": i,
        })
    return rows


def _safe_float(v: Any) -> float | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ---------- 题材热度聚合 ----------

def aggregate_theme_hotness(rows: list[dict], top_n: int = 20) -> list[dict]:
    """从热点列表提取题材词频 (reason 用 '+' 分隔).

    返回: [{tag, count, avg_change_pct}, ...] 按 count 降序
    """
    from collections import defaultdict
    counter: dict[str, int] = defaultdict(int)
    change_sum: dict[str, float] = defaultdict(float)
    change_n: dict[str, int] = defaultdict(int)
    for r in rows:
        reason = r.get("reason_tags") or ""
        chg = r.get("change_pct")
        for tag in [t.strip() for t in str(reason).split("+") if t.strip()]:
            counter[tag] += 1
            if chg is not None:
                change_sum[tag] += chg
                change_n[tag] += 1
    out = []
    for tag, cnt in counter.items():
        n = change_n[tag]
        out.append({
            "tag": tag,
            "count": cnt,
            "avg_change_pct": round(change_sum[tag] / n, 2) if n else None,
        })
    out.sort(key=lambda x: (-x["count"], -x["avg_change_pct"] or 0))
    return out[:top_n]
