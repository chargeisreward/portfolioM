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


def test_fetch_in_batches_merges_three_sources():
    """三源并行：tencent 提供 PE_1，naver 提供 PE_2，yfinance 提供 PB_1。"""
    from services.overseas_financial_v2_service import _fetch_in_batches
    db = MagicMock()

    partitioned = {
        "tencent_quote": ["NVDA", "AAPL"],
        "naver_quote": ["005930.KS"],
        "yfinance": ["SHEL.L"],
    }

    with patch("services.overseas_financial_v2_service._fetch_tencent_group",
               return_value=({"NVDA": {"pe_ttm": 30.5, "source": "tencent"},
                              "AAPL": {"pe_ttm": 25.0, "source": "tencent"}}, [])) as m_t, \
         patch("services.overseas_financial_v2_service._fetch_naver_group",
               return_value=({"005930.KS": {"pe_ttm": 12.0, "source": "naver"}}, [])) as m_n, \
         patch("services.overseas_financial_v2_service._fetch_yfinance_group",
               return_value=({"SHEL.L": {"pe_ttm": 8.0, "pb_mrq": 1.2,
                                        "ps_ttm": 0.5, "dividend_yield": 0.04,
                                        "source": "yfinance"}}, [])) as m_y:
        results, errors = _fetch_in_batches(db, partitioned, 50)

    assert results["NVDA"]["pe_ttm"] == 30.5
    assert results["005930.KS"]["pe_ttm"] == 12.0
    assert results["SHEL.L"]["pb_mrq"] == 1.2
    assert m_t.called and m_n.called and m_y.called


def test_fetch_in_batches_rate_limited_raises():
    """任一源整批 RateLimitedError → 抛 RateLimitedError（顶层捕获并退避）。"""
    from services.overseas_financial_v2_service import (
        _fetch_in_batches, RateLimitedError,
    )
    import pytest
    db = MagicMock()
    partitioned = {"tencent_quote": ["NVDA"], "naver_quote": [], "yfinance": []}

    with patch("services.overseas_financial_v2_service._fetch_tencent_group",
               side_effect=RateLimitedError("pvtoo.match")):
        with pytest.raises(RateLimitedError):
            _fetch_in_batches(db, partitioned, 50)


def test_fetch_naver_group_escalates_naver_rate_limited():
    """_fetch_naver_korean_info 抛 NaverRateLimited → _fetch_naver_group → RateLimitedError。

    验证 503/429/anti-bot 路径不再是死代码。
    """
    import services.overseas_financial_v2_service as svc
    from services.overseas_financial_v2_service import (
        _fetch_naver_group, RateLimitedError,
    )
    from crawlers.price_data import NaverRateLimited
    import pytest

    with patch.object(svc, "_fetch_naver_korean_info",
                      side_effect=NaverRateLimited("naver HTTP 503 for 005930.KS")):
        with pytest.raises(RateLimitedError):
            _fetch_naver_group(["005930.KS"])


def test_fetch_tencent_group_escalates_rate_limited():
    """fetch_tencent_quote 抛 rate-limit 特征异常 → _fetch_tencent_group → RateLimitedError。"""
    import services.overseas_financial_v2_service as svc
    from services.overseas_financial_v2_service import (
        _fetch_tencent_group, RateLimitedError,
    )
    import pytest

    class FakePvTooError(Exception):
        """模拟腾讯 pvtoo.match 反爬异常。"""

    with patch.object(svc, "fetch_tencent_quote",
                      side_effect=FakePvTooError("pvtoo.match captcha required")):
        with pytest.raises(RateLimitedError):
            _fetch_tencent_group(["NVDA"])


def test_top_level_entry_writes_to_both_tables():
    """顶层入口：fetch+upsert 双写 OverseasShareFinancialSnapshot + StockInfoCache。"""
    from services.overseas_financial_v2_service import fetch_overseas_financials_three_source
    from unittest.mock import MagicMock, patch
    from datetime import date

    db = MagicMock()

    with patch("services.overseas_financial_v2_service.collect_codes",
               return_value=({"NVDA"}, 49)) as m_collect, \
         patch("services.overseas_financial_v2_service._partition_codes_by_source",
               return_value={"tencent_quote": ["NVDA"], "naver_quote": [], "yfinance": []}) as m_part, \
         patch("services.overseas_financial_v2_service._fetch_in_batches",
               return_value=({"NVDA": {"pe_ttm": 30.0, "source": "tencent"}}, [])) as m_fetch, \
         patch("services.overseas_financial_v2_service.upsert_overseas_financial",
               return_value={"status": "ok"}) as m_upsert, \
         patch("services.overseas_financial_v2_service._dual_write_stock_info_cache") as m_dual:
        result = fetch_overseas_financials_three_source(db, date(2026, 6, 29))

    m_collect.assert_called_once()
    m_upsert.assert_called_once()
    m_dual.assert_called_once_with(db, "NVDA", {"pe_ttm": 30.0, "source": "tencent"})
    assert result["fetched"] == 1
    assert result["stored"] == 1
    assert result["skipped_cached"] == 49
    assert result["rate_limited"] is False


def test_top_level_entry_skipped_all_returns_empty():
    """全部 code 当日已落库 → 早返回，不调 fetch。"""
    from services.overseas_financial_v2_service import fetch_overseas_financials_three_source
    from unittest.mock import MagicMock, patch
    from datetime import date

    db = MagicMock()

    with patch("services.overseas_financial_v2_service.collect_codes",
               return_value=(set(), 50)), \
         patch("services.overseas_financial_v2_service._fetch_in_batches") as m_fetch:
        result = fetch_overseas_financials_three_source(db, date(2026, 6, 29))

    m_fetch.assert_not_called()
    assert result["fetched"] == 0
    assert result["stored"] == 0
    assert result["skipped_cached"] == 50