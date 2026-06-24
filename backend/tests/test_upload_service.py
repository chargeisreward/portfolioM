"""upload_service 单元测试。"""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_save_upload_file(monkeypatch, tmp_path):
    """save_upload_file 保存文件到 uploads/{category}/，返回相对路径。"""
    # 模拟 UPLOAD_DIR 为临时目录
    monkeypatch.setattr("services.upload_service.UPLOAD_DIR", str(tmp_path / "uploads"))

    from services.upload_service import save_upload_file

    # 模拟 UploadFile
    mock_file = MagicMock()
    mock_file.filename = "test.pdf"
    mock_file.read = MagicMock(return_value=b"%PDF-1.4 fake content")

    result = save_upload_file(mock_file, "pdf")

    # 验证返回相对路径
    assert result.startswith("uploads/pdf/")
    assert result.endswith(".pdf")

    # 验证文件已保存
    full_path = tmp_path / result
    assert full_path.exists()
    assert full_path.read_bytes() == b"%PDF-1.4 fake content"


def test_save_upload_file_cleans_filename(monkeypatch, tmp_path):
    """文件名中的特殊字符被清洗为下划线。"""
    monkeypatch.setattr("services.upload_service.UPLOAD_DIR", str(tmp_path / "uploads"))

    from services.upload_service import save_upload_file

    mock_file = MagicMock()
    mock_file.filename = "沪深300 / 000300.pdf"
    mock_file.read = MagicMock(return_value=b"content")

    result = save_upload_file(mock_file, "pdf")

    # 验证路径中无空格和斜杠（除了目录分隔符）
    filename = result.split("/")[-1]
    assert " " not in filename
    assert "/" not in filename


def test_list_uploads(monkeypatch, tmp_path):
    """list_uploads 列出已上传文件。"""
    # 创建测试文件
    pdf_dir = tmp_path / "uploads" / "pdf"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "file1_20260624_153022.pdf").write_bytes(b"content1")
    (pdf_dir / "file2_20260624_160000.pdf").write_bytes(b"content2")

    monkeypatch.setattr("services.upload_service.UPLOAD_DIR", str(tmp_path / "uploads"))

    from services.upload_service import list_uploads

    result = list_uploads("pdf")
    assert len(result) == 2
    assert any(f["filename"].startswith("file1") for f in result)


def test_get_upload_path(monkeypatch, tmp_path):
    """get_upload_path 返回文件完整路径。"""
    monkeypatch.setattr("services.upload_service.UPLOAD_DIR", str(tmp_path / "uploads"))

    from services.upload_service import get_upload_path

    result = get_upload_path("pdf/test.pdf")
    assert "pdf" in result
    assert result.endswith("test.pdf")
