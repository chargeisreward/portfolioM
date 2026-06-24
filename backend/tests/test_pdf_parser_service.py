"""pdf_parser_service 单元测试。

注意：pdfplumber/OCR/AI 的完整集成测试需要真实 PDF 文件，
这里主要测试三层策略的调度逻辑和 ParseResult 结构。
"""
import os
from unittest.mock import patch, MagicMock

import pytest


def test_parse_result_dataclass():
    """ParseResult 数据类能正常创建。"""
    from services.pdf_parser_service import ParseResult

    r = ParseResult(
        success=True,
        method="pdfplumber",
        constituents=[{"stock_code": "600519", "stock_name": "贵州茅台", "weight": 5.0}],
        confidence=0.95,
        error=None,
    )
    assert r.success is True
    assert r.method == "pdfplumber"
    assert len(r.constituents) == 1
    assert r.confidence == 0.95


def test_parse_index_pdf_pdfplumber_success():
    """第一层 pdfplumber 成功时直接返回，不调用 OCR/AI。"""
    from services.pdf_parser_service import parse_index_pdf, ParseResult

    mock_result = ParseResult(
        success=True,
        method="pdfplumber",
        constituents=[{"stock_code": str(i).zfill(6), "stock_name": f"股票{i}", "weight": 1.0} for i in range(20)],
        confidence=0.9,
        error=None,
    )

    with patch("services.pdf_parser_service._parse_with_pdfplumber", return_value=mock_result) as mock_pdf:
        with patch("services.pdf_parser_service._parse_with_ocr") as mock_ocr:
            with patch("services.pdf_parser_service._parse_with_ai") as mock_ai:
                result = parse_index_pdf("/fake/path.pdf", "000300")

                assert result.success is True
                assert result.method == "pdfplumber"
                mock_pdf.assert_called_once()
                mock_ocr.assert_not_called()
                mock_ai.assert_not_called()


def test_parse_index_pdf_fallback_to_ocr():
    """第一层失败时回退到第二层 OCR。"""
    from services.pdf_parser_service import parse_index_pdf, ParseResult

    mock_pdf_result = ParseResult(success=False, method="pdfplumber", constituents=[], confidence=0.0, error="no tables")
    mock_ocr_result = ParseResult(
        success=True,
        method="ocr",
        constituents=[{"stock_code": str(i).zfill(6), "stock_name": f"股票{i}", "weight": 1.0} for i in range(15)],
        confidence=0.8,
        error=None,
    )

    with patch("services.pdf_parser_service._parse_with_pdfplumber", return_value=mock_pdf_result):
        with patch("services.pdf_parser_service._parse_with_ocr", return_value=mock_ocr_result):
            with patch("services.pdf_parser_service._parse_with_ai") as mock_ai:
                result = parse_index_pdf("/fake/path.pdf", "000300")

                assert result.success is True
                assert result.method == "ocr"
                mock_ai.assert_not_called()


def test_parse_index_pdf_fallback_to_ai():
    """前两层失败时回退到第三层 AI。"""
    from services.pdf_parser_service import parse_index_pdf, ParseResult

    mock_pdf_result = ParseResult(success=False, method="pdfplumber", constituents=[], confidence=0.0, error="no tables")
    mock_ocr_result = ParseResult(success=False, method="ocr", constituents=[], confidence=0.0, error="ocr failed")
    mock_ai_result = ParseResult(
        success=True,
        method="ai",
        constituents=[{"stock_code": str(i).zfill(6), "stock_name": f"股票{i}", "weight": 1.0} for i in range(12)],
        confidence=0.7,
        error=None,
    )

    with patch("services.pdf_parser_service._parse_with_pdfplumber", return_value=mock_pdf_result):
        with patch("services.pdf_parser_service._parse_with_ocr", return_value=mock_ocr_result):
            with patch("services.pdf_parser_service._parse_with_ai", return_value=mock_ai_result):
                result = parse_index_pdf("/fake/path.pdf", "000300")

                assert result.success is True
                assert result.method == "ai"


def test_parse_index_pdf_all_layers_fail():
    """三层全部失败时返回错误。"""
    from services.pdf_parser_service import parse_index_pdf, ParseResult

    mock_pdf_result = ParseResult(success=False, method="pdfplumber", constituents=[], confidence=0.0, error="no tables")
    mock_ocr_result = ParseResult(success=False, method="ocr", constituents=[], confidence=0.0, error="ocr failed")
    mock_ai_result = ParseResult(success=False, method="ai", constituents=[], confidence=0.0, error="LLM 未配置")

    with patch("services.pdf_parser_service._parse_with_pdfplumber", return_value=mock_pdf_result):
        with patch("services.pdf_parser_service._parse_with_ocr", return_value=mock_ocr_result):
            with patch("services.pdf_parser_service._parse_with_ai", return_value=mock_ai_result):
                result = parse_index_pdf("/fake/path.pdf", "000300")

                assert result.success is False
                assert "三层解析均失败" in (result.error or "")
