"""统一行情数据入口（参考 data_get.md §1-2）

多源回退策略：
  US ticker → 腾讯财经API（实时）+ 腾讯K线（历史）+ yfinance（财务）
  A股      → akshare（行情）+ 腾讯K线
  港股      → 腾讯财经API + yfinance 备用
  备用      → yfinance（通用 fallback）
"""
import re
import json
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import yfinance as yf
from datetime import date, datetime
from typing import Optional

from config import TENCENT_USER_AGENT, TENCENT_QUOTE_URL, TENCENT_KLINE_URL


# ---------- 腾讯财经 API（实时行情，首选） ----------

def fetch_tencent_quote(ticker: str) -> dict | None:
    """
    从腾讯财经 API 获取实时行情。
    返回 {price, pe_ttm, market_cap, name, industry, ...}
    """
    # Format ticker for Tencent API
    tencent_code = _to_tencent_ticker(ticker)
    if not tencent_code:
        return None

    headers = {"User-Agent": TENCENT_USER_AGENT}
    url = TENCENT_QUOTE_URL.format(tencent_code)

    try:
        resp = requests.get(url, headers=headers, timeout=(3, 5), verify=False)
        resp.encoding = "gbk"
        text = resp.text

        # Parse the pipe-delimited format
        # Format: v_qqqc="code~name~...~pe~market_cap~..."
        match = re.search(r'"(.*?)"', text)
        if not match:
            return None

        parts = match.group(1).split("~")
        if len(parts) < 50:
            return None

        result = {
            "code": ticker,
            "name": _safe_get(parts, 1),
            "price": _safe_float(parts, 3),
            "pe_ttm": _safe_get(parts, 39),   # PE_TTM field
            "market_cap": _safe_float(parts, 45),  # 总市值（万元）
            "high": _safe_float(parts, 33),
            "low": _safe_float(parts, 34),
            "open": _safe_float(parts, 5),
            "prev_close": _safe_float(parts, 4),
            "volume": _safe_float(parts, 36),
            "turnover_rate": _safe_get(parts, 38),
            "amplitude": _safe_get(parts, 43),
            "industry": _safe_get(parts, 40),  # 所属行业（申万）
            "source": "tencent",
        }
        return result

    except Exception:
        return None


def _to_tencent_ticker(ticker: str) -> str | None:
    """Convert standard ticker to Tencent API format"""
    t = ticker.upper().strip()

    # US stocks: just usNVDA (exchange suffix breaks the API)
    if t in ("GOOGL", "NVDA", "INTC", "SNDK", "AMD", "AAPL", "MSFT", "AMZN", "TSLA", "QQQ"):
        return f"us{t}"

    # A-share: sh600000 / sz000001
    if t.isdigit() and len(t) == 6:
        prefix = "sh" if t.startswith(("5", "6")) else "sz"
        return f"{prefix}{t}"

    # HK stocks: hk00700
    if t.endswith(".HK") or (t.isdigit() and len(t) == 5):
        code = t.replace(".HK", "")
        return f"hk{code}"

    # A shares (6xxxxx = SH, 0xxxxx/3xxxxx = SZ)
    if t.endswith(".OF"):
        return None  # OTC funds not directly supported

    # A-share ETFs with exchange suffix (e.g. 159326.SZ → sz159326)
    if t.endswith(".SZ") or t.endswith(".SH"):
        suffix = t[-2:].lower()
        return f"{suffix}{t[:-3]}"

    return t


# ---------- 腾讯K线 API（历史数据，首选） ----------

def fetch_tencent_kline(ticker: str, days: int = 365) -> list[dict]:
    """
    从腾讯K线API获取历史日线数据（前复权）。
    返回 [{date, open, close, high, low, volume}, ...]
    """
    kline_ticker = _to_kline_ticker(ticker)
    if not kline_ticker:
        return _fetch_yfinance_kline(ticker, days)

    end = date.today()
    start = end.replace(year=end.year - max(1, days // 365))
    params = {
        "param": f"{kline_ticker},day,{start.isoformat()},{end.isoformat()},{days},qfq"
    }
    headers = {"User-Agent": TENCENT_USER_AGENT}

    try:
        resp = requests.get(TENCENT_KLINE_URL, params=params, headers=headers, timeout=5, verify=False)
        data = resp.json()

        # Navigate: data -> {ticker} -> day -> [ [date, open, close, high, low, vol], ...]
        for key in data.get("data", {}):
            day_data = data["data"][key].get("day", [])
            if day_data:
                return [
                    {
                        "date": item[0],
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "volume": float(item[5]),
                    }
                    for item in day_data
                ]
    except Exception:
        pass

    return _fetch_yfinance_kline(ticker, days)


def _to_kline_ticker(ticker: str) -> str | None:
    """Convert to Tencent K-line ticker format"""
    t = ticker.upper().strip()
    # Same as quote API: just usNVDA (exchange suffix breaks it)
    if t in ("GOOGL", "NVDA", "INTC", "SNDK", "AMD", "AAPL", "MSFT", "AMZN", "TSLA", "QQQ"):
        return f"us{t}"


# ---------- yfinance（备用） ----------

def _fetch_yfinance_kline(ticker: str, days: int = 365) -> list[dict]:
    """yfinance 历史数据备用"""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=f"{days}d")
        return [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": float(row["Open"]),
                "close": float(row["Close"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "volume": float(row["Volume"]),
            }
            for idx, row in hist.iterrows()
        ]
    except Exception:
        return []


def fetch_yfinance_info(ticker: str) -> dict | None:
    """yfinance 财务信息补充"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "code": ticker,
            "name": info.get("shortName", ""),
            "pe_ttm": info.get("trailingPE"),
            "market_cap_b": info.get("marketCap", 0) / 1e8,  # 亿
            "revenue_b": info.get("totalRevenue", 0) / 1e8,
            "net_income_b": info.get("netIncomeToCommon", 0) / 1e8,
            "profit_growth": info.get("earningsGrowth"),
            "revenue_growth": info.get("revenueGrowth"),
            "dividend_yield": info.get("dividendYield"),
            "industry": info.get("industry"),
            "sector": info.get("sector"),
            "source": "yfinance",
        }
    except Exception:
        return None


# ---------- 统一入口 ----------

def get_stock_info(ticker: str, timeout_sec: int = 3) -> dict:
    """
    多源实时报价主入口。
    按优先级依次尝试各数据源。
    每个来源超时 short_timeout 秒，避免长时间挂起。
    """
    # 1. 用 requests 请求腾讯行情（快速，3秒超时）
    tencent_code = _to_tencent_ticker(ticker)
    if tencent_code:
        headers = {"User-Agent": TENCENT_USER_AGENT}
        url = TENCENT_QUOTE_URL.format(tencent_code)
        try:
            resp = requests.get(url, headers=headers, timeout=timeout_sec, verify=False)
            resp.encoding = "gbk"
            text = resp.text
            match = __import__("re").search(r'"(.*?)"', text)
            if match:
                parts = match.group(1).split("~")
                if len(parts) >= 45:
                    price_str = parts[3].strip()
                    if price_str and price_str != "-":
                        result = {
                            "code": ticker,
                            "name": _safe_get(parts, 1),
                            "price": float(price_str) if price_str else None,
                            "pe_ttm": _safe_get(parts, 39),
                            "market_cap": _safe_float(parts, 45),
                            "source": "tencent",
                        }
                        return result
        except Exception:
            pass

    # 2. yfinance fallback with short timeout
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info = stock.info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price:
            return {
                "code": ticker,
                "name": info.get("shortName", ""),
                "price": price,
                "pe_ttm": info.get("trailingPE"),
                "market_cap": info.get("marketCap", 0) / 1e8,
                "source": "yfinance",
            }
    except Exception:
        pass

    return {"code": ticker, "source": "none", "error": "No data available"}


def fetch_price_history(ticker: str, days: int = 365) -> list[dict]:
    """
    获取历史价格序列。
    """
    return fetch_tencent_kline(ticker, days)


# ---------- 工具函数 ----------

def _safe_get(parts: list, idx: int) -> str | None:
    return parts[idx].strip() if idx < len(parts) and parts[idx].strip() not in ("", "-") else None


def _safe_float(parts: list, idx: int) -> float | None:
    val = _safe_get(parts, idx)
    try:
        return float(val.replace(",", "")) if val else None
    except (ValueError, AttributeError):
        return None
