"""PDF 解析 service — 三层策略（pdfplumber → OCR → AI 辅助）。

策略：
1. pdfplumber 提取表格（文本型 PDF）
2. OCR 识别（扫描型 PDF）
3. AI 辅助解析（前两层失败时）

成功标准：提取到 >= 10 条记录，且代码列格式匹配（6 位数字或带后缀）
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from services.llm_service import parse_table_with_llm

logger = logging.getLogger(__name__)

# 成功标准：至少 10 条记录
MIN_CONSTITUENTS = 10

# 股票代码正则：6 位数字（可带 .SH/.SZ/.HK 后缀）
STOCK_CODE_PATTERN = re.compile(r"^\d{6}(\.(SH|SZ|HK))?$")


@dataclass
class ParseResult:
    """解析结果。"""
    success: bool
    method: str          # "pdfplumber" / "ocr" / "ai"
    constituents: list   # [{stock_code, stock_name, weight}, ...]
    confidence: float    # 0-1
    error: str | None


def parse_index_pdf(pdf_path: str, index_code: str) -> ParseResult:
    """三层策略解析指数构成 PDF。"""
    logger.info("开始解析 PDF: %s (index=%s)", pdf_path, index_code)

    # 第一层：pdfplumber
    result = _parse_with_pdfplumber(pdf_path)
    if result.success:
        logger.info("pdfplumber 解析成功: %d 条", len(result.constituents))
        return result

    logger.warning("pdfplumber 失败: %s，尝试 OCR", result.error)

    # 第二层：OCR
    result = _parse_with_ocr(pdf_path)
    if result.success:
        logger.info("OCR 解析成功: %d 条", len(result.constituents))
        return result

    logger.warning("OCR 失败: %s，尝试 AI 辅助", result.error)

    # 第三层：AI 辅助
    # 收集前两层的文本用于 AI
    text_for_ai = _extract_raw_text(pdf_path)
    result = _parse_with_ai(text_for_ai, index_code)
    if result.success:
        logger.info("AI 辅助解析成功: %d 条", len(result.constituents))
        return result

    logger.error("三层解析均失败")
    return ParseResult(
        success=False,
        method=result.method,
        constituents=[],
        confidence=0.0,
        error=f"三层解析均失败: pdfplumber/ocr/ai 均未成功",
    )


def _validate_constituents(constituents: list[dict]) -> bool:
    """验证解析结果是否达标。"""
    if len(constituents) < MIN_CONSTITUENTS:
        return False
    # 至少 80% 的代码格式正确
    valid = sum(1 for c in constituents if STOCK_CODE_PATTERN.match(str(c.get("stock_code", ""))))
    return valid >= len(constituents) * 0.8


def _parse_with_pdfplumber(pdf_path: str) -> ParseResult:
    """第一层：pdfplumber 表格提取。"""
    try:
        import pdfplumber
    except ImportError:
        return ParseResult(False, "pdfplumber", [], 0.0, "pdfplumber 未安装")

    try:
        constituents = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if not row or len(row) < 2:
                            continue
                        # 尝试识别列：代码、名称、权重
                        code = str(row[0] or "").strip()
                        name = str(row[1] or "").strip() if len(row) > 1 else ""
                        weight = float(row[2]) if len(row) > 2 and row[2] else None

                        # 过滤非数据行（表头等）
                        if not STOCK_CODE_PATTERN.match(code):
                            continue
                        constituents.append({
                            "stock_code": code,
                            "stock_name": name,
                            "weight": weight,
                        })

        if _validate_constituents(constituents):
            return ParseResult(True, "pdfplumber", constituents, 0.9, None)
        return ParseResult(False, "pdfplumber", constituents, 0.3, f"仅提取到 {len(constituents)} 条，不达标")

    except Exception as e:
        logger.exception("pdfplumber 解析异常")
        return ParseResult(False, "pdfplumber", [], 0.0, str(e))


def _parse_with_ocr(pdf_path: str) -> ParseResult:
    """第二层：OCR 识别。"""
    try:
        import pytesseract
        from pdf2image import convert_from_path
        from PIL import Image
    except ImportError:
        return ParseResult(False, "ocr", [], 0.0, "pytesseract/pdf2image 未安装")

    try:
        # PDF 转图片
        images = convert_from_path(pdf_path)
        all_text = []
        for img in images:
            text = pytesseract.image_to_string(img, lang="chi_sim+eng")
            all_text.append(text)

        full_text = "\n".join(all_text)
        constituents = _extract_constituents_from_text(full_text)

        if _validate_constituents(constituents):
            return ParseResult(True, "ocr", constituents, 0.8, None)
        return ParseResult(False, "ocr", constituents, 0.3, f"OCR 仅提取到 {len(constituents)} 条，不达标")

    except Exception as e:
        logger.exception("OCR 解析异常")
        return ParseResult(False, "ocr", [], 0.0, str(e))


def _parse_with_ai(text: str, index_code: str) -> ParseResult:
    """第三层：AI 辅助解析。"""
    prompt = f"""解析以下指数成分股表格文本，返回 JSON 数组，每个元素包含：
- stock_code: 股票代码（6 位数字，可带 .SH/.SZ/.HK 后缀）
- stock_name: 股票名称
- weight: 权重（浮点数，可为 null）

指数代码：{index_code}

返回格式：[{{"stock_code": "600519", "stock_name": "贵州茅台", "weight": 5.0}}]
"""

    result = parse_table_with_llm(text, prompt)
    if result and _validate_constituents(result):
        return ParseResult(True, "ai", result, 0.7, None)
    if result:
        return ParseResult(False, "ai", result, 0.3, f"AI 返回 {len(result)} 条，不达标")
    return ParseResult(False, "ai", [], 0.0, "LLM 未配置或解析失败")


def _extract_raw_text(pdf_path: str) -> str:
    """提取 PDF 原始文本（用于 AI 辅助）。"""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception:
        return ""


def _extract_constituents_from_text(text: str) -> list[dict]:
    """从 OCR 文本中正则提取成分股。"""
    constituents = []
    # 匹配：6 位数字 + 中文名 + 可选权重
    pattern = re.compile(r"(\d{6}(?:\.(?:SH|SZ|HK))?)\s+([\u4e00-\u9fa5A-Za-z]+)\s*([\d.]+)?")

    for match in pattern.finditer(text):
        code = match.group(1)
        name = match.group(2)
        weight_str = match.group(3)
        try:
            weight = float(weight_str) if weight_str else None
        except ValueError:
            weight = None
        constituents.append({"stock_code": code, "stock_name": name, "weight": weight})

    return constituents
