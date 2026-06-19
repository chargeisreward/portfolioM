"""统一 HTTP 节流入口（a-stock-data skill 「数据源优先级 & 东财防封」）

按 skill 实测结论：
- 行情/财务优先用腾讯（不封 IP），接口走 tencent_get()
- 公告/资讯/研报/资金流用东财（有风控），必须经 em_get() 串行限流
- 热点/题材归因用同花顺（零鉴权），接口走 ths_get()
- 所有请求复用 httpx.Client（Keep-Alive）+ 默认 UA + 内置随机抖动

详见: ~/.claude/skills/a-stock-data/SKILL.md
"""
from __future__ import annotations

import logging
import random
import threading
import time

import httpx

from config import (
    EASTMONTH_USER_AGENT,
    EM_MIN_INTERVAL,
    TENCENT_USER_AGENT,
    THS_USER_AGENT,
)

logger = logging.getLogger(__name__)


# ---------- 东财专用节流入口 (有风控) ----------

_em_client: httpx.Client | None = None
_em_lock = threading.Lock()
_em_last_call: float = 0.0


def _get_em_client() -> httpx.Client:
    """懒加载东财专用 Client（Keep-Alive）"""
    global _em_client
    if _em_client is None:
        with _em_lock:
            if _em_client is None:
                _em_client = httpx.Client(
                    headers={"User-Agent": EASTMONTH_USER_AGENT},
                    timeout=httpx.Timeout(15.0, connect=10.0),
                    http2=False,
                )
    return _em_client


def em_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 15.0,
    max_retries: int = 2,
) -> httpx.Response | None:
    """东财统一请求入口：自动节流 + 复用 session + 默认 UA + 失败重试。

    失败重试仅对连接级错误（连接被拒 / 超时 / HTTP 000），不重试业务 4xx/5xx。
    已知坑：部分大陆住宅 IP 调 push2/datacenter/search-api-web 会被间歇风控
    （HTTP 000 / 返回空数据），不是代码问题，重试 + 退避可显著降低影响。
    """
    global _em_last_call
    client = _get_em_client()
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        # 串行限流：最小间隔 + 随机抖动
        wait = EM_MIN_INTERVAL - (time.time() - _em_last_call)
        if wait > 0:
            time.sleep(wait + random.uniform(0.1, 0.5))
        try:
            resp = client.get(url, params=params, headers=headers, timeout=timeout)
            _em_last_call = time.time()
            if resp.status_code == 200:
                return resp
            # 业务错误：直接返回（让上层处理空数据）
            if resp.status_code in (403, 429):
                logger.warning("东财被限流 HTTP %s url=%s", resp.status_code, url[:80])
                if attempt < max_retries:
                    time.sleep(2 + random.uniform(0, 1))
                    continue
            return resp
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            last_exc = e
            logger.warning("东财连接异常 (attempt %d/%d): %s", attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(1 + random.uniform(0, 1))
                continue
            return None
        except Exception as e:
            last_exc = e
            logger.error("东财请求未知异常: %s", e)
            break
    logger.error("em_get 全部重试失败 url=%s err=%s", url[:80], last_exc)
    return None


# ---------- 腾讯财经专用入口 (不封 IP, GBK 编码) ----------

_tencent_client: httpx.Client | None = None
_tencent_lock = threading.Lock()


def _get_tencent_client() -> httpx.Client:
    """懒加载腾讯 Client"""
    global _tencent_client
    if _tencent_client is None:
        with _tencent_lock:
            if _tencent_client is None:
                _tencent_client = httpx.Client(
                    headers={"User-Agent": TENCENT_USER_AGENT},
                    timeout=httpx.Timeout(10.0, connect=5.0),
                )
    return _tencent_client


def tencent_get(url: str, params: dict | None = None, timeout: float = 10.0) -> httpx.Response | None:
    """腾讯财经 GET，GBK 解码在调用方按需处理。"""
    try:
        r = _get_tencent_client().get(url, params=params, timeout=timeout)
        return r
    except Exception as e:
        logger.warning("腾讯请求失败 url=%s err=%s", url[:80], e)
        return None


# ---------- 同花顺专用入口 (零鉴权, 部分接口需 GBK 解码) ----------

_ths_client: httpx.Client | None = None
_ths_lock = threading.Lock()


def _get_ths_client() -> httpx.Client:
    global _ths_client
    if _ths_client is None:
        with _ths_lock:
            if _ths_client is None:
                _ths_client = httpx.Client(
                    headers={"User-Agent": THS_USER_AGENT},
                    timeout=httpx.Timeout(10.0, connect=5.0),
                )
    return _ths_client


def ths_get(url: str, params: dict | None = None, timeout: float = 10.0) -> httpx.Response | None:
    """同花顺 GET。"""
    try:
        return _get_ths_client().get(url, params=params, timeout=timeout)
    except Exception as e:
        logger.warning("同花顺请求失败 url=%s err=%s", url[:80], e)
        return None


def shutdown_clients():
    """优雅关闭所有 Client（FastAPI shutdown 时调用）"""
    global _em_client, _tencent_client, _ths_client
    for c in (_em_client, _tencent_client, _ths_client):
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
    _em_client = _tencent_client = _ths_client = None
