"""yfinance 增强：fetch_yfinance_info 含 PB/PS + _infer_market_from_ticker。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
from unittest.mock import patch, MagicMock


def test_infer_market_from_ticker_us():
    """无后缀默认 US。"""
    from crawlers.price_data import _infer_market_from_ticker
    assert _infer_market_from_ticker("AAPL") == "US"
    assert _infer_market_from_ticker("MSFT") == "US"


def test_infer_market_from_ticker_korea():
    """韩国市场后缀。"""
    from crawlers.price_data import _infer_market_from_ticker
    assert _infer_market_from_ticker("005930.KS") == "KR"
    assert _infer_market_from_ticker("035420.KQ") == "KR"


def test_infer_market_from_ticker_japan():
    """日本市场后缀。"""
    from crawlers.price_data import _infer_market_from_ticker
    assert _infer_market_from_ticker("7203.T") == "JP"


def test_infer_market_from_ticker_europe():
    """欧洲市场后缀。"""
    from crawlers.price_data import _infer_market_from_ticker
    assert _infer_market_from_ticker("SHEL.L") == "GB"
    assert _infer_market_from_ticker("SAP.DE") == "DE"
    assert _infer_market_from_ticker("MC.PA") == "FR"


def test_fetch_yfinance_info_has_pb_ps():
    """fetch_yfinance_info 返回 PB 和 PS 字段。"""
    from crawlers.price_data import fetch_yfinance_info

    mock_info = {
        "shortName": "Apple Inc",
        "trailingPE": 28.5,
        "priceToBook": 45.2,
        "priceToSalesTrailing12Months": 7.8,
        "marketCap": 3000000000000,
        "totalRevenue": 400000000000,
        "netIncomeToCommon": 100000000000,
        "earningsGrowth": 0.15,
        "revenueGrowth": 0.08,
        "dividendYield": 0.005,
        "forwardEPS": 6.5,
        "sector": "Technology",
        "industry": "Consumer Electronics",
    }

    with patch("crawlers.price_data.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info
        mock_yf.Ticker.return_value = mock_ticker

        result = fetch_yfinance_info("AAPL")

    assert result is not None
    assert result["pe_ttm"] == 28.5
    assert result["pb_mrq"] == 45.2
    assert result["ps_ttm"] == 7.8
    assert result["market"] == "US"
    assert result["sector"] == "Technology"
    assert result["eps_fy1"] == 6.5
    assert result["source"] == "yfinance"


def test_fetch_yfinance_info_none_values():
    """yfinance 返回 None 时不报错。"""
    from crawlers.price_data import fetch_yfinance_info

    mock_info = {
        "shortName": "Test ETF",
        "trailingPE": None,
        "priceToBook": None,
        "priceToSalesTrailing12Months": None,
        "marketCap": 0,
    }

    with patch("crawlers.price_data.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info
        mock_yf.Ticker.return_value = mock_ticker

        result = fetch_yfinance_info("TEST")

    assert result is not None
    assert result["pe_ttm"] is None
    assert result["pb_mrq"] is None
    assert result["ps_ttm"] is None
