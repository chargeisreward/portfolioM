"""llm_service 交易记录相关函数单元测试（parse_trades_with_llm /
classify_market_with_llm / verify_security_name_with_llm）。"""
from unittest.mock import patch, MagicMock

import pytest


# ============================================================================
# parse_trades_with_llm
# ============================================================================


def test_parse_trades_with_llm_no_api_key(monkeypatch):
    """未配置 LLM_API_KEY 时返回 None。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from services.llm_service import parse_trades_with_llm

    result = parse_trades_with_llm("基金申购记录...")
    assert result is None


def test_parse_trades_with_llm_success(monkeypatch):
    """成功解析交易记录。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '''[
            {"trade_date":"2025-08-15","security_code":"006829.OF","security_name":"华泰柏瑞红利低波",
             "trade_type":"buy","confirmed_shares":1000.0,"confirmed_amount":-1500.0,"nav_price":1.5},
            {"trade_date":"2025-09-01","security_code":"006829.OF","security_name":"华泰柏瑞红利低波",
             "trade_type":"sell","confirmed_shares":-500.0,"confirmed_amount":800.0,"nav_price":1.6}
        ]'''}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import parse_trades_with_llm

        result = parse_trades_with_llm("基金交易记录...")

    assert result is not None
    assert len(result) == 2
    assert result[0]["trade_type"] == "buy"
    assert result[0]["confirmed_shares"] == 1000.0
    assert result[0]["confirmed_amount"] == -1500.0
    assert result[1]["trade_type"] == "sell"
    assert result[1]["confirmed_shares"] == -500.0
    assert result[1]["confirmed_amount"] == 800.0


def test_parse_trades_with_llm_markdown_wrapper(monkeypatch):
    """LLM 返回带 markdown 代码块标记的 JSON 也能正确解析。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '```json\n[]\n```'}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import parse_trades_with_llm

        result = parse_trades_with_llm("无交易")
        assert result == []


def test_parse_trades_with_llm_api_error(monkeypatch):
    """API 返回错误时返回 None。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import parse_trades_with_llm

        result = parse_trades_with_llm("text")
        assert result is None


def test_parse_trades_with_llm_invalid_json(monkeypatch):
    """LLM 返回非 JSON 时返回 None。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "not a json"}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import parse_trades_with_llm

        result = parse_trades_with_llm("text")
        assert result is None


def test_parse_trades_with_llm_non_array_json(monkeypatch):
    """LLM 返回 JSON 对象（非数组）时返回 None。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"key": "value"}'}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import parse_trades_with_llm

        result = parse_trades_with_llm("text")
        assert result is None


# ============================================================================
# classify_market_with_llm
# ============================================================================


def test_classify_market_with_llm_no_api_key(monkeypatch):
    """未配置 LLM_API_KEY 时返回 None。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from services.llm_service import classify_market_with_llm

    result = classify_market_with_llm("600519.SH", "贵州茅台")
    assert result is None


@pytest.mark.parametrize("market", ["CN", "HK", "US", "OF"])
def test_classify_market_with_llm_success(monkeypatch, market):
    """成功判定市场 — 4 种市场标识。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": market}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import classify_market_with_llm

        result = classify_market_with_llm("anycode", "anyname")
        assert result == market


def test_classify_market_with_llm_lowercase(monkeypatch):
    """LLM 返回小写时也能识别并转大写。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "cn"}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import classify_market_with_llm

        result = classify_market_with_llm("600519.SH", "贵州茅台")
        assert result == "CN"


def test_classify_market_with_llm_invalid_response(monkeypatch):
    """LLM 返回非预期值时返回 None。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "UNKNOWN_MARKET"}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import classify_market_with_llm

        result = classify_market_with_llm("anycode", "anyname")
        assert result is None


def test_classify_market_with_llm_with_context(monkeypatch):
    """带上下文的判定。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "HK"}}]
    }

    with patch("httpx.post", return_value=mock_response) as mock_post:
        from services.llm_service import classify_market_with_llm

        result = classify_market_with_llm("00700", "腾讯控股", context="港股通交易")
        assert result == "HK"

        # 验证 context 已传入 user prompt
        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]
        user_content = payload["messages"][1]["content"]
        assert "港股通交易" in user_content


# ============================================================================
# verify_security_name_with_llm
# ============================================================================


def test_verify_security_name_with_llm_empty_input():
    """任一名称为空时返回 False（不调 LLM）。"""
    from services.llm_service import verify_security_name_with_llm

    assert verify_security_name_with_llm("", "贵州茅台") is False
    assert verify_security_name_with_llm("茅台", "") is False
    assert verify_security_name_with_llm("", "") is False


def test_verify_security_name_with_llm_fast_path_contains():
    """简单包含关系直接返回 True，不调 LLM。"""
    from services.llm_service import verify_security_name_with_llm

    # input_name 是 api_name 的子串
    assert verify_security_name_with_llm("茅台", "贵州茅台") is True
    # api_name 是 input_name 的子串
    assert verify_security_name_with_llm("华泰柏瑞中证红利低波ETF联接A",
                                          "红利低波") is True
    # 完全相同
    assert verify_security_name_with_llm("腾讯控股", "腾讯控股") is True


def test_verify_security_name_with_llm_llm_match(monkeypatch):
    """无包含关系时调用 LLM，返回 True。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "true"}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import verify_security_name_with_llm

        # "腾讯" vs "腾讯控股" 已被包含关系处理；用一个真正无包含的例
        result = verify_security_name_with_llm("茅台", "MAOTAI")
        assert result is True


def test_verify_security_name_with_llm_llm_mismatch(monkeypatch):
    """LLM 判定为不同证券时返回 False。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "false"}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import verify_security_name_with_llm

        result = verify_security_name_with_llm("茅台", "阿里巴巴")
        assert result is False


def test_verify_security_name_with_llm_no_api_key(monkeypatch):
    """未配置 LLM_API_KEY 且无包含关系时返回 False。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from services.llm_service import verify_security_name_with_llm

    result = verify_security_name_with_llm("茅台", "阿里巴巴")
    assert result is False
