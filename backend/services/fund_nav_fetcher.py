"""fund_nav_fetcher.py — 东财 lsjz 直连 NAV 拉取（绕过 akshare）。

从 pull_fund_nav_em.py 提取的公共逻辑，供脚本与服务层共用。

字段映射：
  FSRQ  → trade_date
  DWJZ  → nav (单位净值)
  LJJZ  → accumulated_nav (累计净值)
  JZZZL → daily_return (日增长率%)
"""
from __future__ import annotations

import time
from datetime import datetime

import httpx

EM_NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def fetch_nav_page(
    client: httpx.Client,
    fund_code: str,
    page: int,
    page_size: int,
    start: str,
    end: str,
) -> tuple[list[dict], int]:
    """Fetch one page of NAV history. Returns (rows, total_count)."""
    params = {
        "fundCode": fund_code,
        "pageIndex": str(page),
        "pageSize": str(page_size),
        "startDate": start,
        "endDate": end,
    }
    headers = {
        **EM_HEADERS,
        "Referer": f"https://fundf10.eastmoney.com/jjjz_{fund_code}.html",
    }
    r = client.get(EM_NAV_URL, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("ErrCode") not in (0, "0"):
        raise RuntimeError(f"em api err: {d.get('ErrMsg')} for {fund_code}")
    rows = (d.get("Data") or {}).get("LSJZList") or []
    return rows, int(d.get("TotalCount") or 0)


def fetch_nav_all(
    fund_code: str,
    start: str,
    end: str,
    page_size: int = 200,
    page_interval: float = 0.3,
) -> list[dict]:
    """Paginate until all rows in [start, end] are fetched.

    page_interval: 分页请求间隔（秒），默认 0.3。
    """
    client = httpx.Client()
    try:
        page = 1
        all_rows: list[dict] = []
        total = 0
        while True:
            rows, total = fetch_nav_page(client, fund_code, page, page_size, start, end)
            all_rows.extend(rows)
            if not rows or len(all_rows) >= total:
                break
            page += 1
            time.sleep(page_interval)
        return all_rows
    finally:
        client.close()


def parse_nav_row(r: dict) -> dict | None:
    """东财原始 row → 标准 NAV 行 (跳过 NAV 为空的)。"""
    td = r.get("FSRQ") or ""
    if not td or len(td) < 10:
        return None
    try:
        trade_date = datetime.strptime(td[:10], "%Y-%m-%d").date()
    except Exception:
        return None
    nav_s = r.get("DWJZ") or ""
    if not nav_s:
        return None
    try:
        nav = float(nav_s)
    except (TypeError, ValueError):
        return None
    acc_s = r.get("LJJZ") or ""
    acc = None
    if acc_s:
        try:
            acc = float(acc_s)
        except (TypeError, ValueError):
            acc = None
    ret_s = r.get("JZZZL") or ""
    ret = None
    if ret_s:
        try:
            ret = float(ret_s)
        except (TypeError, ValueError):
            ret = None
    return {
        "trade_date": trade_date,
        "nav": nav,
        "accumulated_nav": acc,
        "daily_return": ret,
    }
