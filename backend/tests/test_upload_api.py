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
