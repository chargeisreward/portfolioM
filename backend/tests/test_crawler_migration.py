"""
TDD tests for Phase 2 — migrate crawlers onto the throttled HTTP entry points.

The current code calls raw `requests.get` / `httpx.get` / `httpx.post` directly
in several crawlers, bypassing the rate-limited entry points in
`crawlers/_http.py`. After migration, each crawler must call the appropriate
throttle function (tencent_get / ths_get / em_get / em_post) so that the
centralized retry + interval rules apply uniformly.

Each test:
1. Mocks the throttled entry point.
2. Calls the crawler function.
3. Asserts the throttled entry point was invoked (proving migration happened).
"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import httpx
import pytest


def _ok_resp(text: str = "", json_payload=None, status: int = 200) -> MagicMock:
    """Build a mock httpx.Response with status + text/json."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = text
    if json_payload is not None:
        resp.json.return_value = json_payload
    else:
        resp.json.side_effect = ValueError("no json")
    resp.encoding = "utf-8"
    resp.content = text.encode("utf-8") if text else b""
    return resp


# -----------------------------------------------------------------------------
# price_data.py — must use tencent_get instead of raw requests.get
# -----------------------------------------------------------------------------

def test_fetch_tencent_quote_uses_tencent_get():
    """fetch_tencent_quote must hit tencent_get, not requests.get."""
    from crawlers import price_data

    payload = "~".join([""] * 50) + " body"
    text = f'v_xxx="{payload}"'
    resp = _ok_resp(text=text)

    with patch("crawlers.price_data.tencent_get", return_value=resp) as mock_tg:
        result = price_data.fetch_tencent_quote("sh600519")

    mock_tg.assert_called_once()
    assert result is not None
    assert result["code"] == "sh600519"
    assert result["source"] == "tencent"


def test_fetch_tencent_kline_uses_tencent_get():
    from crawlers import price_data

    # 腾讯 kline 返回结构: {"data": {"sh600519": {"qfqday": [[date, o, c, h, l, v], ...]}}}
    kline_payload = {
        "data": {
            "sh600519": {
                "qfqday": [["2024-01-02", 100.0, 101.0, 102.0, 99.0, 1000.0]]
            }
        }
    }
    resp = _ok_resp(json_payload=kline_payload)

    with patch("crawlers.price_data.tencent_get", return_value=resp) as mock_tg:
        rows = price_data.fetch_tencent_kline("sh600519", days=30)

    mock_tg.assert_called_once()
    assert len(rows) == 1
    assert rows[0]["date"] == "2024-01-02"


def test_get_stock_info_uses_tencent_get():
    """get_stock_info (multi-source) must use tencent_get for the Tencent branch."""
    from crawlers import price_data

    payload_parts = [""] * 50
    payload_parts[1] = "贵州茅台"
    payload_parts[3] = "1700.50"
    payload_parts[45] = "20000"
    text = f'v_xxx="{"~".join(payload_parts)}"'
    resp = _ok_resp(text=text)

    with patch("crawlers.price_data.tencent_get", return_value=resp) as mock_tg:
        result = price_data.get_stock_info("sh600519", timeout_sec=3)

    mock_tg.assert_called_once()
    assert result["price"] == 1700.50


# -----------------------------------------------------------------------------
# etf_index.py — must use em_get instead of raw httpx.get
# -----------------------------------------------------------------------------

def test_crawl_from_eastmoney_uses_em_get():
    """_crawl_from_eastmoney must hit em_get, not httpx.get."""
    from crawlers import etf_index

    # Page text containing a tracking index pattern
    html = '<html><body>跟踪标的：沪深300指数</body></html>'
    resp = _ok_resp(text=html)

    with patch("crawlers.etf_index.em_get", return_value=resp) as mock_em:
        idx_code, idx_name = etf_index._crawl_from_eastmoney("110020")

    mock_em.assert_called_once()
    # Pattern extracts "沪深300指数" → code "000300" from the name regex
    assert idx_name is not None
    assert "沪深300" in idx_name


# -----------------------------------------------------------------------------
# index_constituents.py — must use em_get instead of raw httpx.get
# -----------------------------------------------------------------------------

def test_crawl_constituents_uses_em_get():
    from crawlers import index_constituents

    payload = [{"securityCode": "600519", "securityName": "贵州茅台",
                "weight": "5.0", "marketCap": "2000000000000"}]
    resp = _ok_resp(json_payload=payload)

    with patch("crawlers.index_constituents.em_get", return_value=resp) as mock_em, \
         patch("crawlers.index_constituents._save_constituents"):
        constituents = index_constituents.crawl_constituents("000300", db=None)

    mock_em.assert_called_once()
    assert len(constituents) == 1
    assert constituents[0]["stock_code"] == "600519"


# -----------------------------------------------------------------------------
# announcement_cninfo.py — must use em_get + em_post
# -----------------------------------------------------------------------------

def test_load_orgid_map_uses_em_get():
    """_load_orgid_map must hit em_get, not raw httpx.get."""
    from crawlers import announcement_cninfo

    # 巨潮 stockList 格式
    payload = {"stockList": [{"code": "688017", "orgId": "9900028691"},
                              {"code": "600519", "orgId": "9900008068"}]}
    resp = _ok_resp(json_payload=payload)

    with patch("crawlers.announcement_cninfo.em_get", return_value=resp) as mock_em:
        m = announcement_cninfo._load_orgid_map(force=True)

    mock_em.assert_called_once()
    assert m["688017"] == "9900028691"


def test_fetch_announcements_uses_em_post():
    """fetch_announcements must hit em_post, not raw httpx.post."""
    from crawlers import announcement_cninfo

    payload = {
        "announcements": [
            {
                "announcementId": "1220000001",
                "announcementTitle": "关于回购股份的公告",
                "announcementTypeName": "上市公司公告",
                "announcementTime": 1700000000000,
            }
        ]
    }
    resp = _ok_resp(json_payload=payload)

    with patch("crawlers.announcement_cninfo.em_post", return_value=resp) as mock_ep:
        rows = announcement_cninfo.fetch_announcements("688017")

    mock_ep.assert_called_once()
    assert len(rows) == 1
    assert rows[0]["title"] == "关于回购股份的公告"


# -----------------------------------------------------------------------------
# signal_ths.py — must use ths_get (remove dead _raw_ths_get branch)
# -----------------------------------------------------------------------------

def test_fetch_hot_stocks_uses_ths_get():
    """fetch_hot_stocks must call ths_get directly, not _raw_ths_get wrapper."""
    from crawlers import signal_ths

    payload = {
        "errocode": 0,
        "data": [
            {"code": "600519", "name": "贵州茅台", "close": 1700.5,
             "zhangfu": 1.5, "huanshou": 0.5, "chengjiaoe": 1e8,
             "ddejingliang": 1e6, "market": "sh", "reason": "白酒+涨价"}
        ],
    }
    resp = _ok_resp(json_payload=payload)

    with patch("crawlers.signal_ths.ths_get", return_value=resp) as mock_ths:
        rows = signal_ths.fetch_hot_stocks("2024-01-02", force=True)

    mock_ths.assert_called_once()
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "600519"