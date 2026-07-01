"""三源路由器 v2 — 顶层 + 异常类 + 聚合/分桶。"""
import os
os.environ.setdefault("APP_PASSWORD", "")

from unittest.mock import MagicMock, patch  # noqa: E402


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


def test_resolve_overseas_quote_code_tencent_db_hit():
    """DB 命中：直接返回 api_code_map 已有的映射，不调 LLM。"""
    from services.overseas_financial_v2_service import resolve_overseas_quote_code

    db = MagicMock()
    # transform_code 返回 'usNVDA'（DB 命中）
    with patch("services.overseas_financial_v2_service.transform_code", return_value="usNVDA") as m_tc:
        out = resolve_overseas_quote_code("NVDA", "tencent_quote", db)
    assert out == "usNVDA"
    m_tc.assert_called_once()


def test_resolve_overseas_quote_code_llm_fallback_used_when_heuristic_fails():
    """启发式失败 → LLM 兜底 → 拿到候选，验真，返回。"""
    from services.overseas_financial_v2_service import resolve_overseas_quote_code

    db = MagicMock()
    # 1) DB miss; 2) _default_transform miss; 3) raw probe miss; 4) LLM 给候选
    with patch("services.overseas_financial_v2_service.transform_code", return_value=None), \
         patch("services.overseas_financial_v2_service._default_transform", return_value=None), \
         patch("services.overseas_financial_v2_service.tencent_get") as m_tencent, \
         patch("services.overseas_financial_v2_service._llm_get_candidates",
               return_value=["usMETA"]) as m_llm:
        # raw probe 返回非 200 → 不命中
        m_tencent.return_value = MagicMock(status_code=200, text="")
        # 但 raw probe 因为空 text 没匹配 → 落到 LLM
        # LLM 候选 usMETA → 验证：fetch_tencent_quote("usMETA") 命中（mock）
        with patch("crawlers.price_data.fetch_tencent_quote", return_value={"pe_ttm": 25.0}):
            out = resolve_overseas_quote_code("META", "tencent_quote", db)
    assert out == "usMETA"
    assert m_llm.call_count >= 1  # 启发式失败 → LLM 必须被调用


def test_resolve_overseas_quote_code_max_three_llm_rounds():
    """LLM 连续 3 轮失败 → 返回 None（不阻塞其他 code）。"""
    from services.overseas_financial_v2_service import resolve_overseas_quote_code, MAX_LLM_ROUNDS
    assert MAX_LLM_ROUNDS == 3, "spec §5.4 承诺单 code 最多 3 轮 LLM"
    db = MagicMock()
    with patch("services.overseas_financial_v2_service.transform_code", return_value=None), \
         patch("services.overseas_financial_v2_service._default_transform", return_value=None), \
         patch("services.overseas_financial_v2_service.tencent_get",
               return_value=MagicMock(status_code=200, text="")), \
         patch("services.overseas_financial_v2_service._llm_get_candidates",
               return_value=[]):  # LLM 给空候选 / 验证失败
        out = resolve_overseas_quote_code("WEIRDCODE", "tencent_quote", db)
    assert out is None