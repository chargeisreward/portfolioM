"""Naver Mobile API: 韩股 PE 单股拉取。"""
import os
os.environ.setdefault("APP_PASSWORD", "")

import pytest
from unittest.mock import patch, MagicMock
import requests


def test_fetch_naver_korean_info_returns_pe_and_market_cap():
    """Naver 200 → 解析 items[0].closePrice / per / marketValueOpenShares。"""
    from crawlers import price_data

    fake_body = {
        "stockInfo": {
            "stockCode": "005930",
            "stockName": "삼성전자",
            "closePrice": "70000",
            "marketValueOpenShares": "400000000000000",
            "per": "12.34",
        },
        "totalInfos": [],
    }
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = fake_body

    with patch.object(price_data, "naver_get", return_value=fake_resp):
        out = price_data._fetch_naver_korean_info("005930")

    assert out is not None
    assert out["source"] == "naver"
    assert out["code"] == "005930"
    assert out["pe_ttm"] == 12.34
    assert out["market_cap"] > 0
    assert out["name"] == "삼성전자"


def test_fetch_naver_korean_info_returns_none_on_503():
    """Naver 503 → 返回 None（不抛出）。"""
    from crawlers import price_data

    fake_resp = MagicMock()
    fake_resp.status_code = 503
    fake_resp.text = "Service Unavailable"

    with patch.object(price_data, "naver_get", return_value=fake_resp):
        out = price_data._fetch_naver_korean_info("005930")

    assert out is None


def test_fetch_naver_korean_info_returns_none_on_html_response():
    """Naver 反爬返回 HTML 而非 JSON → None。"""
    from crawlers import price_data

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.side_effect = ValueError("not JSON")

    with patch.object(price_data, "naver_get", return_value=fake_resp):
        out = price_data._fetch_naver_korean_info("005930")

    assert out is None