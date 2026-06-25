"""LLM 服务 — 调用 LLM API 解析表格文本 + 交易记录解析。

配置通过环境变量：
- LLM_API_KEY — API 密钥（未设置时返回 None）
- LLM_API_BASE — API 地址（默认 OpenAI）
- LLM_MODEL — 模型名称（默认 gpt-4o-mini）
"""
from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


def _call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.1,
              timeout: float = 60.0) -> str | None:
    """LLM API 调用底层函数（共用）。

    Args:
        system_prompt: 系统提示词
        user_prompt: 用户输入
        temperature: 温度参数
        timeout: 超时秒数

    Returns:
        LLM 响应文本（已清理 markdown 代码块标记），失败返回 None
    """
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        logger.warning("LLM_API_KEY 未配置，跳过 AI 辅助解析")
        return None

    api_base = os.environ.get("LLM_API_BASE", DEFAULT_API_BASE)
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)

    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        if response.status_code != 200:
            logger.error("LLM API 返回错误 %d: %s", response.status_code, response.text)
            return None

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        # 清理可能的 markdown 代码块标记
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        return content

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("LLM 响应解析失败: %s", e)
        return None
    except httpx.HTTPError as e:
        logger.error("LLM API 调用失败: %s", e)
        return None


def parse_table_with_llm(text: str, prompt: str) -> list[dict] | None:
    """调用 LLM 解析表格文本，返回结构化结果。

    Args:
        text: 待解析的文本（PDF 提取或 OCR 结果）
        prompt: 解析指令

    Returns:
        解析结果列表，失败返回 None
    """
    system_prompt = "你是表格解析助手，返回纯 JSON 数组，不要包含 markdown 代码块标记。"
    user_prompt = f"{prompt}\n\n待解析文本：\n{text}"
    content = _call_llm(system_prompt, user_prompt)
    if content is None:
        return None

    try:
        result = json.loads(content)
        if isinstance(result, list):
            return result
        logger.error("LLM 返回非数组 JSON: %s", type(result))
        return None
    except json.JSONDecodeError as e:
        logger.error("LLM 响应 JSON 解析失败: %s", e)
        return None


# ============================================================================
# 交易记录解析（2026-06-26 新增）
# ============================================================================


# parse_trades_with_llm 的 system prompt
_TRADE_PARSE_SYSTEM = """你是基金/股票交易记录解析助手。从用户粘贴的自由文本中提取交易记录，返回纯 JSON 数组（不要 markdown 代码块标记）。

每条交易记录字段：
- trade_date: 交易日期（YYYY-MM-DD）。优先用"确认日期"，其次用"净值日期"。如仅有一个日期则视为确认日期。
- nav_date: 净值日期（YYYY-MM-DD，可选，若无则不填）
- security_code: 证券代码（保留后缀如 .OF/.SZ/.SH/.HK；如无后缀原样返回）
- security_name: 证券名称
- trade_type: 交易类型。申购/buy/买入 → "buy"；赎回/sell/卖出 → "sell"；分红 → "dividend"；其他 → "others"
- confirmed_shares: 确认份额（数字，申购为正，赎回为负）
- confirmed_amount: 确认金额（数字，申购为负即资金流出，赎回为正即资金流入）
- nav_price: 净值/单价（数字，可选）
- fee: 手续费（数字，可选，若无则不填）
- remarks: 备注（可选）

规则：
1. 申购：份额增加（正），金额减少（负，资金流出）
2. 赎回：份额减少（负），金额增加（正，资金流入）
3. 仅返回 JSON 数组，每元素含上述字段（可选字段可省略）
4. 若无法识别为合法交易记录，返回空数组 []
5. 金额、份额按原文数字，不要做单位换算"""


def parse_trades_with_llm(text: str) -> list[dict] | None:
    """LLM 解析粘贴的交易记录文本 → 标准化交易数组。

    Args:
        text: 用户粘贴的自由文本（券商 App 复制的交易记录）

    Returns:
        标准化交易记录数组，失败返回 None。字段见 _TRADE_PARSE_SYSTEM。
    """
    content = _call_llm(_TRADE_PARSE_SYSTEM, f"待解析文本：\n{text}")
    if content is None:
        return None

    try:
        result = json.loads(content)
        if isinstance(result, list):
            return result
        logger.error("parse_trades_with_llm 返回非数组 JSON: %s", type(result))
        return None
    except json.JSONDecodeError as e:
        logger.error("parse_trades_with_llm JSON 解析失败: %s", e)
        return None


# classify_market_with_llm 的 system prompt
_MARKET_CLASSIFY_SYSTEM = """你是证券市场判定助手。根据证券代码 + 名称 + 上下文，判定该证券所属市场。仅返回以下 4 个标识之一（无引号、无解释）：

- CN：A 股（沪深交易所）。特征：6 位数字代码 + .SZ/.SH 后缀，或仅 6 位数字
- HK：港股。特征：5 位数字 + .HK 后缀，或中文名含"恒生"/"港股"/"港交所"
- US：美股。特征：字母代码（如 NVDA/AAPL），或中文名含"纳斯达克"/"标普"/"道琼斯"/"美股"
- OF：场外基金。特征：6 位数字 + .OF 后缀，或中文名含"联接"/"ETF联接"

判定优先级：代码后缀 > 中文名关键词 > 代码形态。

仅返回 CN / HK / US / OF 四者之一。无法判定时返回 OF（默认场外基金）。"""


def classify_market_with_llm(security_code: str, security_name: str,
                             context: str = "") -> str | None:
    """LLM 判定证券市场。

    Args:
        security_code: 证券代码
        security_name: 证券名称
        context: 上下文（交易记录片段，可选）

    Returns:
        市场标识 "CN" / "HK" / "US" / "OF"，失败返回 None
    """
    user_prompt = (f"证券代码：{security_code}\n"
                   f"证券名称：{security_name}\n"
                   f"上下文：{context or '(无)'}\n\n"
                   f"请判定市场（仅返回 CN / HK / US / OF 之一）：")
    content = _call_llm(_MARKET_CLASSIFY_SYSTEM, user_prompt, temperature=0.0)
    if content is None:
        return None

    market = content.strip().upper()
    if market in ("CN", "HK", "US", "OF"):
        return market
    logger.error("classify_market_with_llm 返回非预期值: %s", content)
    return None


# verify_security_name_with_llm 的 system prompt
_NAME_VERIFY_SYSTEM = """你是证券名称匹配助手。判断用户提供的名称与 API 返回的名称是否指向同一只证券。仅返回 "true" 或 "false"（小写，无引号、无解释）。

匹配规则（语义相似即 true，不要求严格相等）：
- 简称 vs 全称："茅台" vs "贵州茅台" → true
- 缩写 vs 全称："腾讯" vs "腾讯控股" → true
- 简称 vs 全称（基金）："华泰柏瑞红利低波" vs "华泰柏瑞中证红利低波ETF联接A" → true
- 简称 vs 全称（指数）："沪深300" vs "沪深300指数" → true

不匹配规则（指向不同证券）：
- "腾讯" vs "阿里巴巴" → false
- "茅台" vs "五粮液" → false

仅返回 true 或 false。"""


def verify_security_name_with_llm(input_name: str, api_name: str) -> bool:
    """LLM 验证两个证券名称是否指向同一证券。

    Args:
        input_name: 用户/LLM 解析提供的名称
        api_name: API 拉取的名称

    Returns:
        True 表示匹配（语义相似），False 表示不匹配。LLM 调用失败时默认 False。
    """
    if not input_name or not api_name:
        return False

    # 简单包含关系快速判定（避免 LLM 调用）
    if input_name in api_name or api_name in input_name:
        return True

    user_prompt = (f"用户提供的名称：{input_name}\n"
                   f"API 返回的名称：{api_name}\n\n"
                   f"请判断是否为同一证券（仅返回 true 或 false）：")
    content = _call_llm(_NAME_VERIFY_SYSTEM, user_prompt, temperature=0.0)
    if content is None:
        return False

    return content.strip().lower() == "true"
