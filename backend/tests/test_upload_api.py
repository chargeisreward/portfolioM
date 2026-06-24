"""upload API 集成测试。

验证 /api/admin/upload/* 端点的端到端行为。
"""
import os
os.environ["APP_PASSWORD"] = ""

import io
import pytest
import tempfile
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
import main as _main
from database import Base
from main import app
from models import SecurityMaster, IndexConstituentSnapshot


# ========== fixtures ==========

@pytest.fixture
def fresh_db(monkeypatch):
    """每个测试用独立的临时文件 SQLite。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    monkeypatch.setattr(_database, "engine", test_engine)
    monkeypatch.setattr(_database, "SessionLocal", TestSession)

    def _patched_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(_main, "get_db", _patched_get_db)
    yield TestSession()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def client(fresh_db):
    """TestClient，所有 admin 请求自动带 x-admin-token 头。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    return TestClient(app, headers={"x-admin-token": admin_token})


# ========== 指数构成 PDF 上传测试 ==========

def test_upload_index_pdf_success(client, fresh_db, monkeypatch):
    """上传指数 PDF 并解析成功（mock pdf_parser_service）。"""
    # mock 解析结果
    from services.pdf_parser_service import ParseResult

    mock_result = ParseResult(
        success=True,
        method="pdfplumber",
        constituents=[
            {"stock_code": "600519", "stock_name": "贵州茅台", "weight": 5.0},
            {"stock_code": "601318", "stock_name": "中国平安", "weight": 4.0},
        ] + [
            {"stock_code": str(i).zfill(6), "stock_name": f"股票{i}", "weight": 1.0}
            for i in range(100, 120)
        ],
        confidence=0.9,
        error=None,
    )

    # mock upload_service.save_upload_file 避免真实写文件
    monkeypatch.setattr(
        "services.upload_service.save_upload_file",
        lambda file, category: f"uploads/{category}/test_{date.today()}.pdf"
    )
    # mock pdf_parser_service.parse_index_pdf
    import services.pdf_parser_service
    monkeypatch.setattr(
        services.pdf_parser_service,
        "parse_index_pdf",
        lambda path, index_code: mock_result
    )

    # 准备 mock PDF 文件
    pdf_content = b"%PDF-1.4 fake content"

    res = client.post(
        "/api/admin/upload/index-pdf",
        data={"index_code": "000300", "as_of_date": "2026-06-24"},
        files={"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")},
    )

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    assert "task_id" in data
    assert data["method"] == "pdfplumber"
    assert len(data["preview"]) == 22


def test_upload_index_pdf_confirm(client, fresh_db, monkeypatch):
    """确认写入指数成分股。"""
    # 先上传获取 task_id
    from services.pdf_parser_service import ParseResult

    mock_result = ParseResult(
        success=True,
        method="pdfplumber",
        constituents=[
            {"stock_code": "600519", "stock_name": "贵州茅台", "weight": 5.0},
        ] + [
            {"stock_code": str(i).zfill(6), "stock_name": f"股票{i}", "weight": 1.0}
            for i in range(100, 120)
        ],
        confidence=0.9,
        error=None,
    )

    monkeypatch.setattr(
        "services.upload_service.save_upload_file",
        lambda file, category: f"uploads/{category}/test.pdf"
    )
    import services.pdf_parser_service
    monkeypatch.setattr(
        services.pdf_parser_service,
        "parse_index_pdf",
        lambda path, index_code: mock_result
    )

    pdf_content = b"%PDF-1.4 fake content"
    res = client.post(
        "/api/admin/upload/index-pdf",
        data={"index_code": "000300", "as_of_date": "2026-06-24"},
        files={"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")},
    )
    task_id = res.json()["task_id"]

    # 确认写入
    res2 = client.post("/api/admin/upload/index-pdf/confirm", json={"task_id": task_id})
    assert res2.status_code == 200, res2.text
    assert res2.json()["saved"] == 21

    # 验证数据库
    count = fresh_db.query(IndexConstituentSnapshot).filter(
        IndexConstituentSnapshot.index_code == "000300",
        IndexConstituentSnapshot.as_of_date == date(2026, 6, 24),
    ).count()
    assert count == 21


def test_upload_index_pdf_confirm_expired(client, fresh_db):
    """task_id 不存在时返回 404。"""
    res = client.post("/api/admin/upload/index-pdf/confirm", json={"task_id": "nonexistent"})
    assert res.status_code == 404


# ========== 股票分析报告上传测试 ==========

def test_upload_analyst_report_success(client, fresh_db, monkeypatch):
    """上传 DOCX 股票报告并解析成功。"""
    # mock save_upload_file
    monkeypatch.setattr(
        "services.upload_service.save_upload_file",
        lambda file, category: f"uploads/{category}/{file.filename}"
    )
    # mock analyst_parser.parse_company_report
    monkeypatch.setattr(
        "services.analyst_parser.parse_company_report",
        lambda path: {
            "stock_code": "688041.SH",
            "stock_name": "海光信息",
            "section_1_market_focus": "市场关注内容",
            "section_2_core_competence": "核心竞争力",
            "section_3_supply_demand": "供需格局",
            "section_4_marginal_change": "边际变化",
            "section_5_valuation": "估值",
            "section_6_risk": "风险",
            "raw_text": "raw",
        }
    )

    docx_content = b"fake docx content"
    res = client.post(
        "/api/admin/upload/analyst-report",
        files={"files": ("688041.SH公司研究框架.docx", io.BytesIO(docx_content), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["status"] == "success"
    assert data["results"][0]["stock_code"] == "688041.SH"


def test_upload_analyst_report_no_stock_code(client, fresh_db, monkeypatch):
    """文件名无法解析股票代码时返回错误。"""
    monkeypatch.setattr(
        "services.upload_service.save_upload_file",
        lambda file, category: f"uploads/{category}/{file.filename}"
    )

    docx_content = b"fake docx content"
    res = client.post(
        "/api/admin/upload/analyst-report",
        files={"files": ("报告.docx", io.BytesIO(docx_content), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["results"][0]["status"] == "error"
    assert "无法解析股票代码" in data["results"][0]["error"]


# ========== 产业链报告上传测试 ==========

def test_upload_industry_chain_success(client, fresh_db, monkeypatch):
    """上传产业链总结 + 公司清单 MD 文件。"""
    monkeypatch.setattr(
        "services.upload_service.save_upload_file",
        lambda file, category: f"uploads/{category}/{file.filename}"
    )
    # mock parse_chain_summary
    monkeypatch.setattr(
        "services.analyst_parser.parse_chain_summary",
        lambda path: {
            "chain_name": "AI产业链",
            "narrative_md": "# AI产业链总结\n...",
            "source_file": path,
        }
    )
    # mock parse_chain_company_list
    monkeypatch.setattr(
        "services.analyst_parser.parse_chain_company_list",
        lambda path: {
            "chain_name": "AI产业链",
            "companies": [
                {"chain_position": "上游", "company_name": "公司A", "stock_code": "600001"},
                {"chain_position": "下游", "company_name": "公司B", "stock_code": "600002"},
            ],
        }
    )

    summary_content = "# AI产业链总结".encode("utf-8")
    company_content = "| 位置 | 公司 | 代码 |\n|---|---|---|\n| 上游 | 公司A | 600001 |".encode("utf-8")

    res = client.post(
        "/api/admin/upload/industry-chain",
        data={"chain_name": "AI产业链"},
        files={
            "summary_file": ("AI产业链总结.md", io.BytesIO(summary_content), "text/markdown"),
            "company_list_file": ("AI产业链公司清单.md", io.BytesIO(company_content), "text/markdown"),
        },
    )

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    assert data["chain_saved"] is True
    assert data["companies_saved"] == 2
