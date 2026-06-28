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


# ============================================================================
# parse_trades_with_llm — 新增 trade_type 解析测试（dividend/split/rights/conversion/others）
# ============================================================================


def _make_mock_response(content: str):
    """构造 LLM mock 响应。"""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return mock


def test_parse_trades_dividend(monkeypatch):
    """分红解析：shares=0, amount+, type=dividend。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    mock_resp = _make_mock_response('''[{
        "trade_date":"2025-09-01","security_code":"006829.OF","security_name":"华泰柏瑞红利低波",
        "trade_type":"dividend","confirmed_shares":0,"confirmed_amount":100.0,"remarks":"分红"
    }]''')
    with patch("httpx.post", return_value=mock_resp):
        from services.llm_service import parse_trades_with_llm
        result = parse_trades_with_llm("分红记录")
    assert result is not None and len(result) == 1
    assert result[0]["trade_type"] == "dividend"
    assert result[0]["confirmed_shares"] == 0
    assert result[0]["confirmed_amount"] == 100.0


def test_parse_trades_split(monkeypatch):
    """拆分解析：shares+, amount=0, type=split。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    mock_resp = _make_mock_response('''[{
        "trade_date":"2025-10-15","security_code":"510300.SH","security_name":"沪深300ETF",
        "trade_type":"split","confirmed_shares":1000.0,"confirmed_amount":0,"remarks":"拆分"
    }]''')
    with patch("httpx.post", return_value=mock_resp):
        from services.llm_service import parse_trades_with_llm
        result = parse_trades_with_llm("拆分记录")
    assert result is not None and len(result) == 1
    assert result[0]["trade_type"] == "split"
    assert result[0]["confirmed_shares"] == 1000.0
    assert result[0]["confirmed_amount"] == 0


def test_parse_trades_rights(monkeypatch):
    """配股解析：shares+, amount-, type=rights。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    mock_resp = _make_mock_response('''[{
        "trade_date":"2025-11-01","security_code":"600519.SH","security_name":"贵州茅台",
        "trade_type":"rights","confirmed_shares":100.0,"confirmed_amount":-5000.0,"remarks":"配股"
    }]''')
    with patch("httpx.post", return_value=mock_resp):
        from services.llm_service import parse_trades_with_llm
        result = parse_trades_with_llm("配股记录")
    assert result is not None and len(result) == 1
    assert result[0]["trade_type"] == "rights"
    assert result[0]["confirmed_shares"] == 100.0
    assert result[0]["confirmed_amount"] == -5000.0


def test_parse_trades_conversion_double_records(monkeypatch):
    """转换解析：双条记录（from shares-/to shares+）。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    mock_resp = _make_mock_response('''[
        {"trade_date":"2025-09-20","security_code":"006829.OF","security_name":"华泰柏瑞红利低波",
         "trade_type":"conversion","confirmed_shares":-1000.0,"confirmed_amount":0,"remarks":"转换到 招商中证白酒"},
        {"trade_date":"2025-09-20","security_code":"161725.OF","security_name":"招商中证白酒",
         "trade_type":"conversion","confirmed_shares":1000.0,"confirmed_amount":0,"remarks":"从 华泰柏瑞红利低波 转入"}
    ]''')
    with patch("httpx.post", return_value=mock_resp):
        from services.llm_service import parse_trades_with_llm
        result = parse_trades_with_llm("基金转换")
    assert result is not None and len(result) == 2
    assert result[0]["trade_type"] == "conversion"
    assert result[0]["security_code"] == "006829.OF"
    assert result[0]["confirmed_shares"] == -1000.0
    assert result[1]["trade_type"] == "conversion"
    assert result[1]["security_code"] == "161725.OF"
    assert result[1]["confirmed_shares"] == 1000.0
    # 双条 security_code 不同（唯一约束不冲突）
    assert result[0]["security_code"] != result[1]["security_code"]


def test_parse_trades_others(monkeypatch):
    """其他类型解析。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    mock_resp = _make_mock_response('''[{
        "trade_date":"2025-12-01","security_code":"510300.SH","security_name":"沪深300ETF",
        "trade_type":"others","confirmed_shares":0,"confirmed_amount":50.0,"remarks":"手续费返还"
    }]''')
    with patch("httpx.post", return_value=mock_resp):
        from services.llm_service import parse_trades_with_llm
        result = parse_trades_with_llm("其他交易")
    assert result is not None and len(result) == 1
    assert result[0]["trade_type"] == "others"


def test_parse_trades_of_vs_etf_distinction(monkeypatch):
    """场外基金 .OF 代码 + ETF 名称冲突时，LLM 按代码后缀返回（代码为准）。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    # LLM 返回 .OF 代码但名称含 ETF（应保留 .OF 代码，名称由 LLM 判断）
    mock_resp = _make_mock_response('''[{
        "trade_date":"2025-08-01","security_code":"006829.OF","security_name":"华泰柏瑞红利低波ETF联接A",
        "trade_type":"buy","confirmed_shares":1000.0,"confirmed_amount":-1500.0
    }]''')
    with patch("httpx.post", return_value=mock_resp):
        from services.llm_service import parse_trades_with_llm
        result = parse_trades_with_llm("买入场外基金")
    assert result is not None and len(result) == 1
    # 代码保留 .OF 后缀（联接基金，场外）
    assert result[0]["security_code"].endswith(".OF")
    # 名称含"联接"表示是场外联接版（不是纯 ETF）
    assert "联接" in result[0]["security_name"]


# ============================================================================
# verify_security_with_llm
# ============================================================================


def test_verify_security_with_llm_of_code_etf_name_mismatch():
    """场外基金 .OF 代码 + ETF 名称（不含联接）→ verified=False。"""
    from services.llm_service import verify_security_with_llm
    result = verify_security_with_llm("006829.OF", "沪深300ETF", "沪深300ETF")
    assert result["verified"] is False
    assert "不符" in result["reason"]


def test_verify_security_with_llm_sz_code_lianjie_name_mismatch():
    """场内 ETF .SZ 代码 + 联接名称 → verified=False。"""
    from services.llm_service import verify_security_with_llm
    result = verify_security_with_llm("159919.SZ", "沪深300ETF联接A", "沪深300ETF")
    assert result["verified"] is False
    assert "不符" in result["reason"]


def test_verify_security_with_llm_no_sm_name():
    """sm_name=None（主数据不存在）→ verified=False。"""
    from services.llm_service import verify_security_with_llm
    result = verify_security_with_llm("006829.OF", "华泰柏瑞红利低波", None)
    assert result["verified"] is False
    assert "主数据不存在" in result["reason"]


def test_verify_security_with_llm_name_match_fast_path():
    """名称包含关系快速匹配 → verified=True（不调 LLM）。"""
    from services.llm_service import verify_security_with_llm
    # 用户名称是 SM 名称的子串
    result = verify_security_with_llm("006829.OF", "红利低波", "华泰柏瑞红利低波ETF联接A")
    assert result["verified"] is True
    assert result["reason"] == "匹配"


def test_verify_security_with_llm_name_mismatch(monkeypatch):
    """名称不匹配且无包含关系 → 调 LLM 返回 false → verified=False。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    mock_resp = _make_mock_response("false")
    with patch("httpx.post", return_value=mock_resp):
        from services.llm_service import verify_security_with_llm
        result = verify_security_with_llm("600519.SH", "茅台", "阿里巴巴")
    assert result["verified"] is False
    assert "不匹配" in result["reason"]


def test_verify_security_with_llm_of_code_lianjie_name_ok():
    """场外基金 .OF 代码 + 名称含联接 → 通过后缀校验，名称匹配 → verified=True。"""
    from services.llm_service import verify_security_with_llm
    # 名称"红利低波"是 SM 名称的子串，走快速路径（不依赖 LLM_API_KEY）
    result = verify_security_with_llm(
        "006829.OF", "红利低波", "华泰柏瑞中证红利低波ETF联接A"
    )
    assert result["verified"] is True
