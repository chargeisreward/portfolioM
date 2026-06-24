"""LLM 服务 — 调用 LLM API 解析表格文本。

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


def parse_table_with_llm(text: str, prompt: str) -> list[dict] | None:
    """调用 LLM 解析表格文本，返回结构化结果。

    Args:
        text: 待解析的文本（PDF 提取或 OCR 结果）
        prompt: 解析指令

    Returns:
        解析结果列表，失败返回 None
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
            {"role": "system", "content": "你是表格解析助手，返回纯 JSON 数组，不要包含 markdown 代码块标记。"},
            {"role": "user", "content": f"{prompt}\n\n待解析文本：\n{text}"},
        ],
        "temperature": 0.1,
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=60.0)

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

        result = json.loads(content)
        if isinstance(result, list):
            return result
        logger.error("LLM 返回非数组 JSON: %s", type(result))
        return None

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error("LLM 响应解析失败: %s", e)
        return None
    except httpx.HTTPError as e:
        logger.error("LLM API 调用失败: %s", e)
        return None
