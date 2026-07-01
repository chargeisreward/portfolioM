"""三源路由器 v2 — 顶层 + 异常类 + 聚合/分桶。"""
import os
os.environ.setdefault("APP_PASSWORD", "")

import pytest
from unittest.mock import patch, MagicMock, call
from datetime import date


def test_rate_limited_error_is_exception():
    """RateLimitedError 是 Exception 子类。"""
    from services.overseas_financial_v2_service import RateLimitedError
    err = RateLimitedError("test")
    assert isinstance(err, Exception)
    assert "test" in str(err)


def test_partition_codes_by_source_us_hk_goes_tencent():
    """US/HK 主源 = tencent_quote。"""
    from services.overseas_financial_v2_service import _partition_codes_by_source
    parts = _partition_codes_by_source(["NVDA", "00700.HK", "AAPL", "QQQ"])
    assert set(parts["tencent_quote"]) == {"NVDA", "00700.HK", "AAPL", "QQQ"}
    assert parts["yfinance"] == []
    assert parts["naver_quote"] == []


def test_partition_codes_by_source_kr_goes_naver():
    """KR 后缀(.KS/.KQ)走 naver；纯 6 位无点 → 落到 yfinance。"""
    from services.overseas_financial_v2_service import _partition_codes_by_source
    parts = _partition_codes_by_source(["005930.KS", "035420.KQ", "7203.T"])
    assert set(parts["naver_quote"]) == {"005930.KS", "035420.KQ"}
    assert "7203.T" in parts["yfinance"]


def test_partition_codes_by_source_europe_japan_yfinance():
    """欧洲/日本 → yfinance。"""
    from services.overseas_financial_v2_service import _partition_codes_by_source
    parts = _partition_codes_by_source(["SHEL.L", "SAP.DE", "MC.PA", "7203.T"])
    assert all(c in parts["yfinance"] for c in ["SHEL.L", "SAP.DE", "MC.PA", "7203.T"])
    assert parts["tencent_quote"] == []
    assert parts["naver_quote"] == []