"""Tests for analyst_parser.py"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.analyst_parser import (
    parse_company_report,
    parse_chain_summary,
    parse_chain_company_list,
    parse_all,
)

RESEARCHER_DIR = Path(__file__).resolve().parent.parent.parent / "researcher"


@pytest.mark.skipif(not (RESEARCHER_DIR / "688041.SH公司研究框架.docx").exists(), reason="no docx fixture")
def test_parse_company_report_sections():
    path = RESEARCHER_DIR / "688041.SH公司研究框架.docx"
    result = parse_company_report(path)
    assert result["success"] is True
    assert result["stock_code"] == "688041.SH"
    assert result["exchange"] == "SH"
    # 6 个核心章节至少大部分能解析到
    assert result["section_1_market_focus"]
    assert result["section_5_valuation"]
    assert "raw_text" in result


@pytest.mark.skipif(not (RESEARCHER_DIR / "AI产业链 总结报告.md").exists(), reason="no summary fixture")
def test_parse_chain_summary():
    path = RESEARCHER_DIR / "AI产业链 总结报告.md"
    result = parse_chain_summary(path)
    assert result["success"] is True
    assert result["chain_name"] == "AI产业链"
    assert result["narrative_md"]


@pytest.mark.skipif(not (RESEARCHER_DIR / "AI产业链 公司清单.md").exists(), reason="no list fixture")
def test_parse_chain_company_list():
    path = RESEARCHER_DIR / "AI产业链 公司清单.md"
    result = parse_chain_company_list(path)
    assert result["success"] is True
    assert result["chain_name"] == "AI产业链"
    assert len(result["rows"]) > 0
    first = result["rows"][0]
    assert first["chain_position"]
    assert first["company_name"]
    assert first["relevance_stars"] is not None
    assert first["relevance_stars"] >= 1


@pytest.mark.skipif(not RESEARCHER_DIR.exists(), reason="researcher dir missing")
def test_parse_all_counts():
    result = parse_all(RESEARCHER_DIR)
    assert len(result["company_reports"]) == 8
    assert len(result["chain_summaries"]) == 3
    assert len(result["chain_company_lists"]) == 3
    assert all(r["success"] for r in result["company_reports"])
    assert all(r["success"] for r in result["chain_summaries"])
    assert all(r["success"] for r in result["chain_company_lists"])


def test_parse_chain_company_list_inherits_position():
    """空单元格应继承上一行的产业链位置。"""
    md = """|产业链位置|细分环节|公司简称|证券代码|市值区间|相关程度|相关理由|最新进展|订单能见度|业绩弹性|客户导入|
|-|-|-|-|-|-|-|-|-|-|-|
|上游-硬件|AI芯片|海光|688041.SH|大|★★★★★|理由|进展|能见度|弹性|导入|
|||寒武纪|688256.SH|大|★★★★★|理由|进展|能见度|弹性|导入|
"""
    # 写临时文件
    tmp = Path("/tmp/test_chain_list.md")
    tmp.write_text(md, encoding="utf-8")
    result = parse_chain_company_list(tmp)
    assert len(result["rows"]) == 2
    assert result["rows"][1]["chain_position"] == "上游-硬件"
    assert result["rows"][1]["company_name"] == "寒武纪"
    tmp.unlink()
