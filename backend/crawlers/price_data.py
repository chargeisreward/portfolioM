"""统一行情数据入口（参考 data_get.md §1-2）

多源回退策略：
  US ticker → 腾讯财经API（实时）+ 腾讯K线（历史）+ yfinance（财务）
  A股      → akshare（行情）+ 腾讯K线
  港股      → 腾讯财经API + yfinance 备用
  备用      → yfinance（通用 fallback）
"""
import re
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import logging
import yfinance as yf
from datetime import date, datetime
from typing import Optional

from config import TENCENT_USER_AGENT, TENCENT_QUOTE_URL, TENCENT_KLINE_URL
from crawlers._http import naver_get, tencent_get

logger = logging.getLogger(__name__)


# ---------- 腾讯财经 API（实时行情，首选） ----------

def fetch_tencent_quote(ticker: str) -> dict | None:
    """
    从腾讯财经 API 获取实时行情。
    返回 {price, pe_ttm, market_cap, name, industry, ...}
    """
    # 优先用 DB 映射表（api_code_map.tencent_quote）
    from services.code_map import transform_code
    from database import SessionLocal
    db = SessionLocal()
    try:
        mapped = transform_code(ticker, "tencent_quote", db)
    finally:
        db.close()
    if mapped:
        ticker = mapped
    # Format ticker for Tencent API
    tencent_code = _to_tencent_ticker(ticker)
    if not tencent_code:
        return None

    headers = {"User-Agent": TENCENT_USER_AGENT}
    url = TENCENT_QUOTE_URL.format(tencent_code)

    try:
        resp = tencent_get(url, headers=headers, timeout=(3, 5))
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
            "change_pct": _safe_float(parts, 32),  # 原生涨跌幅% (parts[32])
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
    raw = ticker.strip()

    # passthrough：已经被 transform_code 转过的格式（usNVDA.OQ / sh600519）
    if raw.startswith(("sh", "sz", "hk", "us")) and len(raw) > 3:
        return raw

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


# ---------- 腾讯批量行情 API（实时，多 code 单次请求） ----------

# URL 长度防御：~15KB 内浏览器/server 都接受；>17KB 多数 server 会拒
_BATCH_URL_LIMIT = 15000
_BASE_TENCENT_URL = "https://qt.gtimg.cn/q="


def fetch_tencent_quotes_batch(
    user_codes: list[str],
    db: "Session | None" = None,
) -> dict[str, dict]:
    """腾讯批量端点：1 次 HTTP 拿全部 user_codes 的实时 quote。

    Args:
        user_codes: 持仓写法证券代码列表（如 ['600519.SH', 'NVDA', '00700.HK']）
        db: SQLAlchemy Session（用于查 api_code_map）；None 时自动 new SessionLocal()

    Returns:
        {user_code: {"change_pct": float|None, "price": float|None,
                     "prev_close": float|None}}
        无映射 / 接口失败的 code 不在返回 dict 中。
    """
    import re
    from sqlalchemy.orm import Session as _Session

    if not user_codes:
        return {}

    # 1. 用 db session 解析每个 user_code → tencent_code
    own_db = False
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        own_db = True

    try:
        from services.code_map import resolve_tencent_quote_code

        user_to_tc: dict[str, str] = {}
        unresolved: list[str] = []
        for uc in user_codes:
            if not uc:
                continue
            tc = resolve_tencent_quote_code(uc, db)
            if tc is None:
                unresolved.append(uc)
                continue
            user_to_tc[uc] = tc

        if unresolved:
            logger.warning(
                "批量端点 %d 个 code 仍无 tencent 映射，跳过: %s",
                len(unresolved), unresolved[:10],
            )

        if not user_to_tc:
            return {}

        tc_to_user = {v: k for k, v in user_to_tc.items()}
        tencent_codes = list(user_to_tc.values())

        # 2. URL 长度防御：>15KB 自动分块
        out: dict[str, dict] = {}
        chunk: list[str] = []
        chunk_len = len(_BASE_TENCENT_URL)

        for tc in tencent_codes:
            need = len(tc) + 1  # +1 for comma separator
            if chunk_len + need > _BATCH_URL_LIMIT and chunk:
                out.update(_do_batch_request(chunk, tc_to_user))
                chunk = []
                chunk_len = len(_BASE_TENCENT_URL)
            chunk.append(tc)
            chunk_len += need
        if chunk:
            out.update(_do_batch_request(chunk, tc_to_user))

        return out
    finally:
        if own_db:
            db.close()


def _do_batch_request(
    tencent_codes: list[str],
    tc_to_user: dict[str, str],
) -> dict[str, dict]:
    """单次批量请求 + 解析。"""
    import re
    from crawlers._http import tencent_get

    if not tencent_codes:
        return {}

    url = _BASE_TENCENT_URL + ",".join(tencent_codes)
    try:
        resp = tencent_get(url, timeout=15.0)
    except Exception as e:
        logger.warning("批量腾讯请求失败 (%d codes): %s", len(tencent_codes), e)
        return {}

    if not resp or resp.status_code != 200:
        logger.warning("批量腾讯请求非 200: %s codes=%d", resp.status_code if resp else "None", len(tencent_codes))
        return {}

    out: dict[str, dict] = {}
    pattern = re.compile(r'v_([a-zA-Z0-9]+)="(.*?)"')
    matched = 0
    for m in pattern.finditer(resp.text):
        tc = m.group(1)
        payload = m.group(2)
        user_code = tc_to_user.get(tc)
        if not user_code:
            continue
        parts = payload.split("~")
        # parts[3]=price, parts[4]=prev_close, parts[32]=change_pct
        if len(parts) < 33:
            continue
        matched += 1
        try:
            change_pct = float(parts[32]) if parts[32] else None
            price = float(parts[3]) if parts[3] else None
            prev_close = float(parts[4]) if parts[4] else None
        except ValueError:
            continue
        out[user_code] = {
            "change_pct": change_pct,
            "price": price,
            "prev_close": prev_close,
        }
    logger.debug("批量腾讯: %d codes sent, %d matched, %d parsed",
                 len(tencent_codes), matched, len(out))
    return out


# ---------- 腾讯K线 API（历史数据，首选） ----------

def fetch_tencent_kline(ticker: str, days: int = 365) -> list[dict]:
    """
    从腾讯K线API获取历史日线数据（前复权）。
    返回 [{date, open, close, high, low, volume}, ...]
    注：A股/港股走 qfqday 字段；美股走 day 字段。
    """
    # 优先用 DB 映射表（api_code_map.tencent_kline）
    from services.code_map import transform_code
    from database import SessionLocal
    db = SessionLocal()
    try:
        mapped = transform_code(ticker, "tencent_kline", db)
    finally:
        db.close()
    if mapped:
        ticker = mapped
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
        resp = tencent_get(TENCENT_KLINE_URL, params=params, headers=headers, timeout=5)
        data = resp.json()

        for key, payload in data.get("data", {}).items():
            if not isinstance(payload, dict):
                continue
            # A 股/港股走 qfqday（前复权），美股走 day
            rows = payload.get("day") or payload.get("qfqday") or []
            if rows:
                out = []
                for item in rows:
                    try:
                        out.append({
                            "date": item[0],
                            "open": float(item[1]),
                            "close": float(item[2]),
                            "high": float(item[3]),
                            "low": float(item[4]),
                            "volume": float(item[5]),
                        })
                    except (ValueError, TypeError, IndexError):
                        continue
                if out:
                    return out
    except Exception:
        pass

    return _fetch_yfinance_kline(ticker, days)


# 美股 → 腾讯 K 线交易所后缀映射（NASDAQ=.OQ, NYSE=.N, NYSE Arca=.AM）
_TENCENT_US_EXCHANGE_SUFFIX = {
    "GOOGL": ".OQ", "NVDA": ".OQ", "INTC": ".OQ", "AMD": ".OQ",
    "AAPL": ".OQ", "MSFT": ".OQ", "AMZN": ".OQ", "TSLA": ".OQ",
    "QQQ": ".OQ",
    "SNDK": ".OQ",  # NASDAQ
}


def _to_kline_ticker(ticker: str) -> str | None:
    """Convert to Tencent K-line ticker format.
    美股必须加交易所后缀 (.OQ/.N/.AM) 才能拿到完整 K 线，否则只返回 1 天。
    A股/港股无后缀（sh/sz/hk + 6/5 位数字）。
    已转换格式 (usNVDA.OQ / sh600519) 直接 passthrough。"""
    t = ticker.upper().strip()
    raw = ticker.strip()
    # 1. passthrough：已经被 transform_code 转过的格式
    if raw.startswith(("sh", "sz", "hk", "us")) and len(raw) > 3:
        return raw
    # 2. 美股：加交易所后缀
    if t in _TENCENT_US_EXCHANGE_SUFFIX:
        return f"us{t}{_TENCENT_US_EXCHANGE_SUFFIX[t]}"
    # 3. A 股：6 位数字 + .SH/.SZ 后缀
    if t.endswith(".SH") and t[:-3].isdigit() and len(t[:-3]) == 6:
        return f"sh{t[:-3]}"
    if t.endswith(".SZ") and t[:-3].isdigit() and len(t[:-3]) == 6:
        return f"sz{t[:-3]}"
    # 4. 港股：5 位数字 + .HK
    if t.endswith(".HK") and t[:-3].isdigit() and len(t[:-3]) == 5:
        return f"hk{t[:-3]}"
    # 5. 纯 6 位数字
    if t.isdigit() and len(t) == 6:
        prefix = "sh" if t.startswith(("5", "6")) else "sz"
        return prefix + t
    # 6. 纯 5 位数字 → 港股
    if t.isdigit() and len(t) == 5:
        return "hk" + t
    # OF 基金不在腾讯 K 线，保留 None 走 yfinance/akshare
    return None


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


def _infer_market_from_ticker(ticker: str) -> str:
    """根据 yfinance ticker 后缀推断市场代码。"""
    if "." not in ticker:
        return "US"
    suffix = ticker.rsplit(".", 1)[-1].upper()
    market_map = {
        "KS": "KR", "KQ": "KR",
        "T": "JP",
        "L": "GB",
        "DE": "DE",
        "PA": "FR",
        "AS": "NL",
        "MI": "IT",
        "SW": "CH",
        "AX": "AU",
        "TO": "CA",
    }
    return market_map.get(suffix, suffix)


def fetch_yfinance_info(ticker: str) -> dict | None:
    """yfinance 财务信息补充（增强版：含 PB/PS + market 推断）"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "code": ticker,
            "name": info.get("shortName", ""),
            "market": _infer_market_from_ticker(ticker),
            "pe_ttm": info.get("trailingPE"),
            "pb_mrq": info.get("priceToBook"),
            "ps_ttm": info.get("priceToSalesTrailing12Months"),
            "market_cap_b": info.get("marketCap", 0) / 1e8,  # 亿
            "revenue_b": info.get("totalRevenue", 0) / 1e8,
            "net_income_b": info.get("netIncomeToCommon", 0) / 1e8,
            "profit_growth": info.get("earningsGrowth"),
            "revenue_growth": info.get("revenueGrowth"),
            "dividend_yield": info.get("dividendYield"),
            "eps_fy1": info.get("forwardEPS"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
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
            resp = tencent_get(url, headers=headers, timeout=timeout_sec)
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


def fetch_price_history(ticker: str, days: int = 365, *, force: bool = False) -> list[dict]:
    """
    获取历史价格序列。

    force: True 跳过 dedup 守门（手动强制重拉）
    dedup: 如果 PriceCache 已有 ticker 的最新交易日记录，则跳过（避免重复拉 365 天 K 线）。
           注意：交易日历精确比对留给 backfill_gaps job 做完整性检查。
    """
    if not force:
        from models import PriceCache
        from database import SessionLocal
        from datetime import date as _date
        from sqlalchemy import func as _func
        db = SessionLocal()
        try:
            latest = db.query(_func.max(PriceCache.trade_date)).filter(
                PriceCache.stock_code == ticker
            ).scalar()
            if latest is not None and latest >= _date.today():
                return []
        finally:
            db.close()

    return fetch_tencent_kline(ticker, days)


# ---------- 工具函数 ----------

def _compute_change_pct(price: float | None, prev_close: float | None) -> float | None:
    """计算涨跌幅% = (price - prev_close) / prev_close * 100"""
    if price is None or prev_close is None or prev_close <= 0:
        return None
    return round((price - prev_close) / prev_close * 100, 4)


def _safe_get(parts: list, idx: int) -> str | None:
    return parts[idx].strip() if idx < len(parts) and parts[idx].strip() not in ("", "-") else None


def _safe_float(parts: list, idx: int) -> float | None:
    val = _safe_get(parts, idx)
    try:
        return float(val.replace(",", "")) if val else None
    except (ValueError, AttributeError):
        return None


# ---------- Naver Mobile API（韩股，主源） ----------

def _fetch_naver_korean_info(code: str) -> dict | None:
    """Naver Mobile API: 韩股单股行情 + PE。

    URL: https://m.stock.naver.com/api/stock/{code}/integration
    Returns: {code, name, price, pe_ttm, market_cap, source='naver'} 或 None（被反爬/超时）
    """
    # naver_get 在模块顶层 import（crawlers._http）— 测试通过 patch.object 拦截
    # code 容错：剥离 .KS / .KQ 后缀（Naver URL 只接受纯 6 位数字）
    raw = code.strip()
    for suffix in (".KS", ".KQ"):
        if raw.endswith(suffix):
            raw = raw[:-3]
            break

    if not raw.isdigit() or len(raw) != 6:
        return None

    url = f"https://m.stock.naver.com/api/stock/{raw}/integration"
    try:
        resp = naver_get(url, timeout=5.0)
    except Exception:
        return None

    if not resp or resp.status_code != 200:
        return None

    try:
        body = resp.json()
    except Exception:
        return None

    info = body.get("stockInfo", {}) if isinstance(body, dict) else {}
    if not info:
        return None

    pe_raw = info.get("per")
    try:
        pe_ttm = float(pe_raw) if pe_raw not in (None, "", "-") else None
    except (ValueError, TypeError):
        pe_ttm = None

    try:
        market_cap = float(info.get("marketValueOpenShares", "") or 0)
    except (ValueError, TypeError):
        market_cap = None

    try:
        price = float(info.get("closePrice", "") or 0) or None
    except (ValueError, TypeError):
        price = None

    if not any([pe_ttm, market_cap, price]):
        return None

    return {
        "code": code,
        "name": info.get("stockName", ""),
        "price": price,
        "pe_ttm": pe_ttm,
        "market_cap": market_cap,
        "source": "naver",
    }
