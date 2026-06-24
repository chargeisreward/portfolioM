"""llm_service 单元测试。"""
import os
from unittest.mock import patch, MagicMock

import pytest


def test_parse_table_with_llm_no_api_key(monkeypatch):
    """未配置 LLM_API_KEY 时返回 None。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    from services.llm_service import parse_table_with_llm

    result = parse_table_with_llm("some text", "parse the table")
    assert result is None


def test_parse_table_with_llm_success(monkeypatch):
    """配置 LLM_API_KEY 时调用 API 并返回解析结果。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_API_BASE", "https://api.test.com/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    # 模拟 httpx 响应
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '[{"stock_code":"600519","stock_name":"贵州茅台","weight":5.0}]'}}]
    }

    with patch("httpx.post", return_value=mock_response) as mock_post:
        from services.llm_service import parse_table_with_llm

        result = parse_table_with_llm("some text", "parse the table")

        assert result is not None
        assert len(result) == 1
        assert result[0]["stock_code"] == "600519"

        # 验证 API 调用参数
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "test-key" in str(call_args.kwargs.get("headers", {}))


def test_parse_table_with_llm_api_error(monkeypatch):
    """API 返回错误时返回 None。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import parse_table_with_llm

        result = parse_table_with_llm("some text", "parse the table")
        assert result is None


def test_parse_table_with_llm_invalid_json(monkeypatch):
    """LLM 返回无效 JSON 时返回 None。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "not a json"}}]
    }

    with patch("httpx.post", return_value=mock_response):
        from services.llm_service import parse_table_with_llm

        result = parse_table_with_llm("some text", "parse the table")
        assert result is None
