# 内容上传套件 — 子项目 2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立文件上传基础设施，实现 4 类上传功能（指数构成 PDF / 股票分析报告 DOCX / 产业链报告 MD / 财务数据 Excel+表单），PDF 解析采用三层策略（pdfplumber → OCR → AI 辅助）。

**Architecture:** 新建 5 个后端 service（upload/pdf_parser/llm/financial_upload + 复用 analyst_parser），新增 7 个 admin API 端点（/api/admin/upload/*），前端新建 ContentUploadPanel 含 4 tab。文件持久化到 backend/uploads/{pdf,doc,md,csv}/ 子目录，按时间戳命名。

**Tech Stack:** FastAPI UploadFile + python-multipart + pdfplumber + pytesseract + pdf2image + Pillow + React + axios FormData

**Design spec:** `docs/superpowers/specs/2026-06-24-content-upload-design.md`

## 文件结构

### 新建文件
| 文件 | 职责 |
|---|---|
| `backend/services/upload_service.py` | 文件保存 + 路径管理 + 时间戳命名 |
| `backend/services/pdf_parser_service.py` | 三层解析（pdfplumber → OCR → AI） |
| `backend/services/llm_service.py` | LLM API 调用（AI 辅助层） |
| `backend/services/financial_upload_service.py` | Excel 导入 + 单条写入 |
| `backend/tests/test_upload_service.py` | service 单元测试 |
| `backend/tests/test_pdf_parser_service.py` | service 单元测试 |
| `backend/tests/test_llm_service.py` | service 单元测试 |
| `backend/tests/test_financial_upload_service.py` | service 单元测试 |
| `backend/tests/test_upload_api.py` | API 集成测试 |
| `frontend/src/components/IndexPdfUploadTab.jsx` | 指数 PDF 上传 tab |
| `frontend/src/components/AnalystReportTab.jsx` | 股票报告上传 tab |
| `frontend/src/components/IndustryChainTab.jsx` | 产业链报告上传 tab |
| `frontend/src/components/FinancialUploadTab.jsx` | 财务数据上传 tab |

### 修改文件
| 文件 | 改动 |
|---|---|
| `backend/main.py` | 新增 7 个 upload API 端点 + StaticFiles 挂载 |
| `backend/requirements.txt` | 新增 5 个依赖 |
| `frontend/src/api.js` | 添加 admin token 拦截器（修复子项目 1 遗留问题） |
| `frontend/src/components/ContentUploadPanel.jsx` | 替换占位为 4 tab 实现 |
| `frontend/src/App.jsx` | 登录时存储 admin token 到 localStorage |

### 新建目录
```
backend/uploads/{pdf,doc,md,csv}/
```

---

## Task 1: 文件上传基础设施（upload_service + StaticFiles + 依赖）

### 步骤 1.1: 添加依赖

在 `backend/requirements.txt` 末尾添加：

```
pdfplumber>=0.11.0
pytesseract>=0.3.10
Pillow>=10.0.0
pdf2image>=1.17.0
python-multipart
```

### 步骤 1.2: 创建 uploads 目录

```bash
cd backend
mkdir -p uploads/pdf uploads/doc uploads/md uploads/csv
```

在 `backend/uploads/pdf/` 下创建 `.gitkeep` 文件（空文件），确保目录被 git 跟踪。同理对 doc/md/csv 目录。

### 步骤 1.3: 写 upload_service 测试

创建 `backend/tests/test_upload_service.py`：

```python
"""upload_service 单元测试。"""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_save_upload_file(monkeypatch, tmp_path):
    """save_upload_file 保存文件到 uploads/{category}/，返回相对路径。"""
    # 模拟 UPLOAD_DIR 为临时目录
    monkeypatch.setattr("services.upload_service.UPLOAD_DIR", str(tmp_path))

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
    monkeypatch.setattr("services.upload_service.UPLOAD_DIR", str(tmp_path))

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
```

### 步骤 1.4: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_upload_service.py -v
```

预期：4 个测试全部 FAIL（模块不存在）。

### 步骤 1.5: 实现 upload_service.py

创建 `backend/services/upload_service.py`：

```python
"""文件上传 service — 保存 + 路径管理 + 时间戳命名。

依赖：FastAPI UploadFile
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

# uploads 目录（与 main.py 同级）
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")

# 支持的文件类别 → 子目录
CATEGORIES = {"pdf", "doc", "md", "csv"}


def _clean_filename(name: str) -> str:
    """清洗文件名：特殊字符（空格、路径分隔符）替换为下划线。"""
    # 去掉路径部分（防止目录穿越）
    name = os.path.basename(name)
    # 替换空格和特殊字符为下划线
    name = re.sub(r"[\s/\\:*?\"<>|]+", "_", name)
    return name


def save_upload_file(file: UploadFile, category: str) -> str:
    """保存上传文件到 uploads/{category}/，返回相对路径。

    命名规则：{原始文件名去掉扩展名}_{时间戳YYYYMMDD_HHMMSS}.{扩展名}
    """
    if category not in CATEGORIES:
        raise ValueError(f"不支持的文件类别: {category}，支持: {CATEGORIES}")

    # 确保目录存在
    category_dir = os.path.join(UPLOAD_DIR, category)
    os.makedirs(category_dir, exist_ok=True)

    # 清洗文件名
    original_name = _clean_filename(file.filename or "unnamed")
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix or f".{category}"

    # 时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_filename = f"{stem}_{timestamp}{suffix}"

    # 保存文件
    relative_path = os.path.join("uploads", category, new_filename)
    full_path = os.path.join(UPLOAD_DIR, category, new_filename)

    content = file.read()
    with open(full_path, "wb") as f:
        f.write(content)

    return relative_path


def list_uploads(category: str | None = None) -> list[dict]:
    """列出已上传文件。返回 [{category, filename, path, size, modified}]。"""
    result = []
    categories = [category] if category else CATEGORIES
    for cat in categories:
        cat_dir = os.path.join(UPLOAD_DIR, cat)
        if not os.path.isdir(cat_dir):
            continue
        for filename in sorted(os.listdir(cat_dir)):
            if filename.startswith("."):
                continue
            full_path = os.path.join(cat_dir, filename)
            if not os.path.isfile(full_path):
                continue
            stat = os.stat(full_path)
            result.append({
                "category": cat,
                "filename": filename,
                "path": f"uploads/{cat}/{filename}",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return result


def get_upload_path(relative_path: str) -> str:
    """获取文件完整路径（从相对路径）。"""
    # 防止目录穿越
    safe_path = os.path.normpath(relative_path).lstrip("\\/")
    return os.path.join(UPLOAD_DIR, os.path.dirname(safe_path), os.path.basename(safe_path))
```

### 步骤 1.6: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_upload_service.py -v
```

预期：4 个测试全部 PASS。

### 步骤 1.7: 在 main.py 挂载 StaticFiles

在 `backend/main.py` 中找到 `app = FastAPI(...)` 之后的位置（约第 38 行），添加：

```python
import os
from fastapi.staticfiles import StaticFiles

# 静态文件服务：uploads 目录
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
```

### 步骤 1.8: commit

```bash
cd backend
git add services/upload_service.py tests/test_upload_service.py requirements.txt main.py
git add uploads/pdf/.gitkeep uploads/doc/.gitkeep uploads/md/.gitkeep uploads/csv/.gitkeep
git commit -m "feat(infra): upload_service + StaticFiles mount + deps (Task 1)"
```

---

## Task 2: LLM 服务（AI 辅助层）

### 步骤 2.1: 写 llm_service 测试

创建 `backend/tests/test_llm_service.py`：

```python
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
```

### 步骤 2.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_llm_service.py -v
```

预期：4 个测试全部 FAIL（模块不存在）。

### 步骤 2.3: 实现 llm_service.py

创建 `backend/services/llm_service.py`：

```python
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
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=payload)

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
```

### 步骤 2.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_llm_service.py -v
```

预期：4 个测试全部 PASS。

### 步骤 2.5: commit

```bash
cd backend
git add services/llm_service.py tests/test_llm_service.py
git commit -m "feat(service): llm_service for AI-assisted parsing (Task 2)"
```

---

## Task 3: PDF 解析服务（三层策略）

### 步骤 3.1: 写 pdf_parser_service 测试

创建 `backend/tests/test_pdf_parser_service.py`：

```python
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
```

### 步骤 3.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_pdf_parser_service.py -v
```

预期：5 个测试全部 FAIL（模块不存在）。

### 步骤 3.3: 实现 pdf_parser_service.py

创建 `backend/services/pdf_parser_service.py`：

```python
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
```

### 步骤 3.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_pdf_parser_service.py -v
```

预期：5 个测试全部 PASS。

### 步骤 3.5: commit

```bash
cd backend
git add services/pdf_parser_service.py tests/test_pdf_parser_service.py
git commit -m "feat(service): pdf_parser_service with 3-layer strategy (Task 3)"
```

---

## Task 4: 指数构成 PDF 上传端点

### 步骤 4.1: 写 API 集成测试

创建 `backend/tests/test_upload_api.py`：

```python
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
    monkeypatch.setattr(
        "main.parse_index_pdf",
        lambda path, index_code: mock_result,
        raising=False
    )
    # 由于 main.py 中是延迟 import，需要 patch 模块级
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
```

### 步骤 4.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_upload_api.py -v
```

预期：3 个测试全部 FAIL（端点不存在）。

### 步骤 4.3: 实现指数 PDF 上传端点

在 `backend/main.py` 中找到最后一个 admin 端点之后（约第 5045 行之后），添加：

```python
# ========== 内容上传端点（子项目 2）==========

import secrets
import time
from datetime import datetime as _dt
from fastapi import UploadFile, File, Form

# 解析结果内存缓存：{task_id: {index_code, as_of_date, constituents, parsed_at}}
_parse_cache: dict[str, dict] = {}
_PARSE_CACHE_TTL = 3600  # 1 小时


def _cleanup_parse_cache():
    """清理过期的解析缓存。"""
    now = time.time()
    expired = [k for k, v in _parse_cache.items() if now - v["parsed_at"] > _PARSE_CACHE_TTL]
    for k in expired:
        del _parse_cache[k]


@app.post("/api/admin/upload/index-pdf")
async def admin_upload_index_pdf(
    index_code: str = Form(...),
    as_of_date: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传指数构成 PDF，返回解析预览。"""
    from services.upload_service import save_upload_file
    from services.pdf_parser_service import parse_index_pdf

    _cleanup_parse_cache()

    # 保存文件
    relative_path = save_upload_file(file, "pdf")

    # 解析 PDF
    full_path = os.path.join(os.path.dirname(__file__), relative_path)
    result = parse_index_pdf(full_path, index_code)

    if not result.success:
        return {
            "status": "parse_failed",
            "method": result.method,
            "error": result.error,
            "preview": [],
        }

    # 暂存解析结果
    task_id = secrets.token_urlsafe(8)
    _parse_cache[task_id] = {
        "index_code": index_code,
        "as_of_date": as_of_date,
        "constituents": result.constituents,
        "parsed_at": time.time(),
    }

    return {
        "status": "success",
        "task_id": task_id,
        "method": result.method,
        "preview": result.constituents,
    }


@app.post("/api/admin/upload/index-pdf/confirm")
def admin_confirm_index_pdf(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """确认写入指数成分股。"""
    from models import IndexConstituentSnapshot
    from datetime import date as _date

    _cleanup_parse_cache()

    task_id = body.get("task_id")
    if not task_id or task_id not in _parse_cache:
        raise HTTPException(404, "task_id 不存在或已过期")

    cached = _parse_cache.pop(task_id)
    as_of = _date.fromisoformat(cached["as_of_date"])
    index_code = cached["index_code"]

    # 删除旧数据（同 index_code + as_of_date）
    db.query(IndexConstituentSnapshot).filter(
        IndexConstituentSnapshot.as_of_date == as_of,
        IndexConstituentSnapshot.index_code == index_code,
    ).delete()

    # 写入新数据
    saved = 0
    for c in cached["constituents"]:
        stock_code = c.get("stock_code", "")
        if not stock_code:
            continue
        snap = IndexConstituentSnapshot(
            as_of_date=as_of,
            index_code=index_code,
            stock_code=stock_code,
            stock_name=c.get("stock_name"),
            weight=c.get("weight"),
        )
        db.add(snap)
        saved += 1

    db.commit()
    return {"status": "ok", "saved": saved}
```

### 步骤 4.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_upload_api.py -v
```

预期：3 个测试全部 PASS。

### 步骤 4.5: commit

```bash
cd backend
git add main.py tests/test_upload_api.py
git commit -m "feat(api): index-pdf upload + confirm endpoints (Task 4)"
```

---

## Task 5: 股票分析报告上传端点

### 步骤 5.1: 写 API 集成测试

在 `backend/tests/test_upload_api.py` 末尾添加：

```python
# ========== 股票分析报告上传测试 ==========

def test_upload_analyst_report_success(client, fresh_db, monkeypatch):
    """上传 DOCX 股票报告并解析成功。"""
    # mock save_upload_file
    monkeypatch.setattr(
        "services.upload_service.save_upload_file",
        lambda file, category: f"uploads/{category}/{file.filename}"
    )
    # mock analyst_parser.parse_company_report
    from services.analyst_parser import parse_company_report
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
```

### 步骤 5.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_upload_api.py::test_upload_analyst_report_success tests/test_upload_api.py::test_upload_analyst_report_no_stock_code -v
```

预期：2 个测试 FAIL（端点不存在）。

### 步骤 5.3: 实现股票报告上传端点

在 `backend/main.py` 的指数 PDF 端点之后添加：

```python
from typing import List
from services.analyst_parser import parse_company_report, _parse_stock_code_from_filename
from models import AnalystCompanyReport


@app.post("/api/admin/upload/analyst-report")
async def admin_upload_analyst_report(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """上传股票分析报告 DOCX（支持多文件）。"""
    from services.upload_service import save_upload_file

    results = []
    for file in files:
        filename = file.filename or ""
        try:
            # 从文件名解析股票代码
            stock_code, _ = _parse_stock_code_from_filename(filename)
            if not stock_code:
                results.append({
                    "filename": filename,
                    "stock_code": None,
                    "status": "error",
                    "error": "无法解析股票代码（文件名需包含 6 位数字 + .SH/.SZ/.HK）",
                })
                continue

            # 保存文件
            relative_path = save_upload_file(file, "doc")
            full_path = os.path.join(os.path.dirname(__file__), relative_path)

            # 解析 DOCX
            parsed = parse_company_report(full_path)

            # Upsert 到 AnalystCompanyReport
            existing = db.query(AnalystCompanyReport).filter(
                AnalystCompanyReport.stock_code == stock_code
            ).first()

            if existing:
                existing.stock_name = parsed.get("stock_name")
                existing.section_1_market_focus = parsed.get("section_1_market_focus")
                existing.section_2_core_competence = parsed.get("section_2_core_competence")
                existing.section_3_supply_demand = parsed.get("section_3_supply_demand")
                existing.section_4_marginal_change = parsed.get("section_4_marginal_change")
                existing.section_5_valuation = parsed.get("section_5_valuation")
                existing.section_6_risk = parsed.get("section_6_risk")
                existing.raw_text = parsed.get("raw_text")
                existing.source_file = relative_path
            else:
                report = AnalystCompanyReport(
                    stock_code=stock_code,
                    stock_name=parsed.get("stock_name"),
                    section_1_market_focus=parsed.get("section_1_market_focus"),
                    section_2_core_competence=parsed.get("section_2_core_competence"),
                    section_3_supply_demand=parsed.get("section_3_supply_demand"),
                    section_4_marginal_change=parsed.get("section_4_marginal_change"),
                    section_5_valuation=parsed.get("section_5_valuation"),
                    section_6_risk=parsed.get("section_6_risk"),
                    raw_text=parsed.get("raw_text"),
                    source_file=relative_path,
                )
                db.add(report)

            db.commit()
            results.append({
                "filename": filename,
                "stock_code": stock_code,
                "status": "success",
                "error": None,
            })

        except Exception as e:
            results.append({
                "filename": filename,
                "stock_code": None,
                "status": "error",
                "error": str(e),
            })

    return {"results": results}
```

### 步骤 5.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_upload_api.py::test_upload_analyst_report_success tests/test_upload_api.py::test_upload_analyst_report_no_stock_code -v
```

预期：2 个测试 PASS。

### 步骤 5.5: commit

```bash
cd backend
git add main.py tests/test_upload_api.py
git commit -m "feat(api): analyst-report upload endpoint (Task 5)"
```

---

## Task 6: 产业链报告上传端点

### 步骤 6.1: 写 API 集成测试

在 `backend/tests/test_upload_api.py` 末尾添加：

```python
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

    summary_content = b"# AI产业链总结"
    company_content = b"| 位置 | 公司 | 代码 |\n|---|---|---|\n| 上游 | 公司A | 600001 |"

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
```

### 步骤 6.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_upload_api.py::test_upload_industry_chain_success -v
```

预期：测试 FAIL（端点不存在）。

### 步骤 6.3: 实现产业链报告上传端点

在 `backend/main.py` 的股票报告端点之后添加：

```python
from services.analyst_parser import parse_chain_summary, parse_chain_company_list
from models import AnalystIndustryChain, AnalystIndustryChainCompany


@app.post("/api/admin/upload/industry-chain")
async def admin_upload_industry_chain(
    chain_name: str = Form(...),
    summary_file: UploadFile = File(...),
    company_list_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传产业链报告（总结 MD + 公司清单 MD）。"""
    from services.upload_service import save_upload_file

    # 保存文件
    summary_path = save_upload_file(summary_file, "md")
    company_path = save_upload_file(company_list_file, "md")

    summary_full = os.path.join(os.path.dirname(__file__), summary_path)
    company_full = os.path.join(os.path.dirname(__file__), company_path)

    # 解析
    summary_parsed = parse_chain_summary(summary_full)
    company_parsed = parse_chain_company_list(company_full)

    # Upsert 产业链总结
    existing_chain = db.query(AnalystIndustryChain).filter(
        AnalystIndustryChain.chain_name == chain_name
    ).first()

    if existing_chain:
        existing_chain.narrative_md = summary_parsed.get("narrative_md")
        existing_chain.source_file = summary_path
    else:
        chain = AnalystIndustryChain(
            chain_name=chain_name,
            narrative_md=summary_parsed.get("narrative_md"),
            source_file=summary_path,
        )
        db.add(chain)

    # 删除旧公司清单并写入新清单
    db.query(AnalystIndustryChainCompany).filter(
        AnalystIndustryChainCompany.chain_name == chain_name
    ).delete()

    companies_saved = 0
    for c in company_parsed.get("companies", []):
        company = AnalystIndustryChainCompany(
            chain_name=chain_name,
            chain_position=c.get("chain_position", ""),
            sub_segment=c.get("sub_segment"),
            company_name=c.get("company_name", ""),
            stock_code=c.get("stock_code"),
            market_cap_range=c.get("market_cap_range"),
            relevance_stars=c.get("relevance_stars"),
            relevance_reason=c.get("relevance_reason"),
            latest_progress=c.get("latest_progress"),
            order_visibility=c.get("order_visibility"),
            earnings_elasticity=c.get("earnings_elasticity"),
            customer_onboarding=c.get("customer_onboarding"),
            source_file=company_path,
        )
        db.add(company)
        companies_saved += 1

    db.commit()
    return {
        "status": "success",
        "chain_saved": True,
        "companies_saved": companies_saved,
    }
```

### 步骤 6.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_upload_api.py::test_upload_industry_chain_success -v
```

预期：测试 PASS。

### 步骤 6.5: commit

```bash
cd backend
git add main.py tests/test_upload_api.py
git commit -m "feat(api): industry-chain upload endpoint (Task 6)"
```

---

## Task 7: 财务数据上传服务 + 端点

### 步骤 7.1: 写 financial_upload_service 测试

创建 `backend/tests/test_financial_upload_service.py`：

```python
"""financial_upload_service 单元测试。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models import AShareFinancialSnapshot, HKShareFinancialSnapshot


@pytest.fixture
def fresh_db():
    """临时文件 SQLite。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    db = TestSession()
    yield db
    db.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_upsert_financial_single_a_share(fresh_db):
    """单条写入 A 股财务数据。"""
    from services.financial_upload_service import upsert_financial_single

    result = upsert_financial_single(fresh_db, {
        "stock_code": "600519.SH",
        "stock_name": "贵州茅台",
        "pe_ttm": 30.5,
        "pb_mrq": 10.2,
        "ps_ttm": 15.0,
        "dividend_yield": 1.5,
        "market_cap": 20000,
        "as_of_date": "2026-06-24",
    })

    assert result["status"] == "ok"
    assert result["market"] == "CN"

    # 验证数据库
    snap = fresh_db.query(AShareFinancialSnapshot).filter(
        AShareFinancialSnapshot.stock_code == "600519.SH",
        AShareFinancialSnapshot.as_of_date == date(2026, 6, 24),
    ).first()
    assert snap is not None
    assert snap.pe_ttm == 30.5
    assert snap.pb_mrq == 10.2


def test_upsert_financial_single_hk(fresh_db):
    """单条写入港股财务数据。"""
    from services.financial_upload_service import upsert_financial_single

    result = upsert_financial_single(fresh_db, {
        "stock_code": "00700.HK",
        "stock_name": "腾讯控股",
        "pe_ttm": 25.0,
        "as_of_date": "2026-06-24",
    })

    assert result["status"] == "ok"
    assert result["market"] == "HK"

    snap = fresh_db.query(HKShareFinancialSnapshot).filter(
        HKShareFinancialSnapshot.stock_code == "00700.HK",
    ).first()
    assert snap is not None
    assert snap.pe_ttm == 25.0


def test_upsert_financial_single_unsupported_code(fresh_db):
    """不支持的代码后缀返回错误。"""
    from services.financial_upload_service import upsert_financial_single

    with pytest.raises(ValueError, match="不支持"):
        upsert_financial_single(fresh_db, {
            "stock_code": "000001.OF",
            "as_of_date": "2026-06-24",
        })


def test_upsert_financial_single_update_existing(fresh_db):
    """更新已存在的记录。"""
    from services.financial_upload_service import upsert_financial_single

    # 第一次写入
    upsert_financial_single(fresh_db, {
        "stock_code": "600519.SH",
        "pe_ttm": 30.0,
        "as_of_date": "2026-06-24",
    })

    # 第二次更新
    upsert_financial_single(fresh_db, {
        "stock_code": "600519.SH",
        "pe_ttm": 35.0,
        "as_of_date": "2026-06-24",
    })

    count = fresh_db.query(AShareFinancialSnapshot).filter(
        AShareFinancialSnapshot.stock_code == "600519.SH",
        AShareFinancialSnapshot.as_of_date == date(2026, 6, 24),
    ).count()
    assert count == 1  # 不应有重复

    snap = fresh_db.query(AShareFinancialSnapshot).filter(
        AShareFinancialSnapshot.stock_code == "600519.SH"
    ).first()
    assert snap.pe_ttm == 35.0
```

### 步骤 7.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_financial_upload_service.py -v
```

预期：4 个测试全部 FAIL（模块不存在）。

### 步骤 7.3: 实现 financial_upload_service.py

创建 `backend/services/financial_upload_service.py`：

```python
"""财务数据上传 service — Excel 导入 + 单条写入。

依赖：AShareFinancialSnapshot, HKShareFinancialSnapshot
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from models import AShareFinancialSnapshot, HKShareFinancialSnapshot

logger = logging.getLogger(__name__)


def _detect_market(stock_code: str) -> str:
    """根据代码后缀判断市场。

    Returns: "CN" / "HK"
    Raises: ValueError 如果不支持的代码后缀
    """
    code = stock_code.upper()
    if code.endswith(".SH") or code.endswith(".SZ"):
        return "CN"
    if code.endswith(".HK"):
        return "HK"
    raise ValueError(f"不支持的代码后缀: {stock_code}（仅支持 .SH/.SZ/.HK）")


def upsert_financial_single(db: Session, data: dict) -> dict:
    """单条写入财务数据（upsert）。

    Args:
        db: 数据库会话
        data: {stock_code, stock_name, pe_ttm, pb_mrq, ps_ttm, dividend_yield,
               market_cap, eps_fy1, eps_fy2, industry_sw, as_of_date, ...}

    Returns: {status, market}
    """
    stock_code = data.get("stock_code", "")
    if not stock_code:
        raise ValueError("stock_code 不能为空")

    market = _detect_market(stock_code)
    as_of = data.get("as_of_date")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)

    model = AShareFinancialSnapshot if market == "CN" else HKShareFinancialSnapshot

    # 查找已存在记录（同 stock_code + as_of_date）
    existing = db.query(model).filter(
        model.stock_code == stock_code,
        model.as_of_date == as_of,
    ).first()

    # 可写入的字段
    fields = (
        "stock_name", "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield",
        "market_cap", "eps_fy1", "eps_fy2",
        "swy_l1", "swy_l2", "swy_l3", "swy_l4",
        "csi_l1", "csi_l2", "csi_l3", "csi_l4",
        "se_l1", "se_l2", "se_l3", "se_l4",
        "industry_sw",
    )

    if existing:
        for f in fields:
            if f in data:
                setattr(existing, f, data[f])
    else:
        kwargs = {"stock_code": stock_code, "as_of_date": as_of, "user_id": 1}
        for f in fields:
            if f in data:
                kwargs[f] = data[f]
        snap = model(**kwargs)
        db.add(snap)

    db.commit()
    return {"status": "ok", "market": market}


def import_excel_batch(db: Session, excel_path: str, market: str, as_of_date: date) -> dict:
    """Excel 批量导入财务数据。

    复用现有 import_a_share_financials / import_hk_share_financials 逻辑。

    Args:
        db: 数据库会话
        excel_path: Excel 文件路径
        market: "CN" / "HK"
        as_of_date: 截止日期

    Returns: {status, imported, errors}
    """
    if market == "CN":
        from scripts.import_a_share_financials import import_a_share
    elif market == "HK":
        from scripts.import_hk_share_financials import import_hk_share
    else:
        return {"status": "error", "imported": 0, "errors": [f"不支持的市场: {market}"]}

    try:
        report = import_a_share(db, as_of_date, Path(excel_path)) if market == "CN" else import_hk_share(db, as_of_date, Path(excel_path))
        return {
            "status": "ok",
            "imported": report.rows_inserted,
            "errors": report.errors,
        }
    except Exception as e:
        logger.exception("Excel 导入失败")
        return {"status": "error", "imported": 0, "errors": [str(e)]}
```

### 步骤 7.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_financial_upload_service.py -v
```

预期：4 个测试全部 PASS。

### 步骤 7.5: 写财务数据上传 API 测试

在 `backend/tests/test_upload_api.py` 末尾添加：

```python
# ========== 财务数据上传测试 ==========

def test_upload_financials_single(client, fresh_db):
    """单条财务数据上传。"""
    res = client.post(
        "/api/admin/upload/financials/single",
        json={
            "stock_code": "600519.SH",
            "stock_name": "贵州茅台",
            "pe_ttm": 30.5,
            "as_of_date": "2026-06-24",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "ok"


def test_upload_financials_single_unsupported(client, fresh_db):
    """不支持的代码后缀返回 400。"""
    res = client.post(
        "/api/admin/upload/financials/single",
        json={
            "stock_code": "000001.OF",
            "as_of_date": "2026-06-24",
        },
    )
    assert res.status_code == 400


def test_upload_financials_excel(client, fresh_db, monkeypatch):
    """Excel 批量上传（mock import 函数）。"""
    monkeypatch.setattr(
        "services.upload_service.save_upload_file",
        lambda file, category: f"uploads/{category}/{file.filename}"
    )
    # mock import_a_share
    from scripts.import_common import ImportReport
    mock_report = ImportReport(as_of_date=date(2026, 6, 24), table="a_share_financial_snapshot")
    mock_report.rows_inserted = 100

    monkeypatch.setattr(
        "services.financial_upload_service.import_a_share",
        lambda db, as_of, path: mock_report,
        raising=False
    )
    import services.financial_upload_service
    monkeypatch.setattr(
        services.financial_upload_service,
        "import_a_share",
        lambda db, as_of, path: mock_report,
        raising=False
    )

    excel_content = b"fake excel"
    res = client.post(
        "/api/admin/upload/financials",
        data={"market": "CN", "as_of_date": "2026-06-24"},
        files={"file": ("financials.xlsx", io.BytesIO(excel_content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert res.status_code == 200, res.text
    assert res.json()["imported"] == 100
```

### 步骤 7.6: 实现财务数据上传端点

在 `backend/main.py` 的产业链端点之后添加：

```python
from services.financial_upload_service import upsert_financial_single, import_excel_batch


@app.post("/api/admin/upload/financials/single")
def admin_upload_financials_single(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """单条财务数据上传。"""
    try:
        result = upsert_financial_single(db, body)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/admin/upload/financials")
async def admin_upload_financials_excel(
    market: str = Form(...),
    as_of_date: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Excel 批量上传财务数据。"""
    from services.upload_service import save_upload_file

    # 保存文件
    relative_path = save_upload_file(file, "csv")
    full_path = os.path.join(os.path.dirname(__file__), relative_path)

    # 解析日期
    as_of = date.fromisoformat(as_of_date)

    # 导入
    result = import_excel_batch(db, full_path, market, as_of)
    return result
```

### 步骤 7.7: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_upload_api.py::test_upload_financials_single tests/test_upload_api.py::test_upload_financials_single_unsupported tests/test_upload_api.py::test_upload_financials_excel -v
```

预期：3 个测试 PASS。

### 步骤 7.8: commit

```bash
cd backend
git add services/financial_upload_service.py tests/test_financial_upload_service.py tests/test_upload_api.py main.py
git commit -m "feat(api): financials upload (single + Excel batch) endpoints (Task 7)"
```

---

## Task 8: 前端 admin token 拦截器 + ContentUploadPanel

### 步骤 8.1: 添加 admin token 拦截器到 api.js

在 `frontend/src/api.js` 中找到 axios 拦截器部分（约第 13-24 行），在 `api.interceptors.request.use` 回调中添加 admin token 注入：

```javascript
// 自动注入 session token（从 localStorage）
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('portfoliom_session')
  if (token) {
    config.headers['x-session-token'] = token
  }
  // admin 端点注入 x-admin-token
  if (config.url?.startsWith('/admin/') || config.url?.includes('/admin/')) {
    const adminToken = localStorage.getItem('portfoliom_admin_token')
    if (adminToken) {
      config.headers['x-admin-token'] = adminToken
    }
  }
  // 注入 view_as（多用户视图代理）
  const viewAsId = localStorage.getItem('portfoliom_view_as')
  if (viewAsId) {
    config.params = { ...(config.params || {}), view_as: viewAsId }
  }
  return config
})
```

### 步骤 8.2: 在 App.jsx 登录时存储 admin token

在 `frontend/src/App.jsx` 中找到登录成功后的处理逻辑（搜索 `localStorage.setItem(TOKEN_KEY`），在存储 session token 后添加 admin token 存储：

```javascript
// 登录成功后存储 session
localStorage.setItem(TOKEN_KEY, sessionToken)
// admin 用户存储 admin token（用于 admin API 鉴权）
// admin token = 登录密码（与后端 APP_PASSWORD/ADMIN_TOKEN 一致）
if (currentUser?.is_admin) {
  localStorage.setItem('portfoliom_admin_token', loginPassword)
} else {
  localStorage.removeItem('portfoliom_admin_token')
}
```

注意：需要找到登录处理函数，将 `loginPassword`（用户输入的密码）传入。如果登录函数中没有保存密码变量，需要调整。

### 步骤 8.3: 创建 ContentUploadPanel.jsx

替换 `frontend/src/components/ContentUploadPanel.jsx`：

```javascript
import React, { useState } from 'react'
import IndexPdfUploadTab from './IndexPdfUploadTab'
import AnalystReportTab from './AnalystReportTab'
import IndustryChainTab from './IndustryChainTab'
import FinancialUploadTab from './FinancialUploadTab'

/**
 * 内容上传页 — 4 tab：指数 PDF / 股票报告 / 产业链 / 财务数据。
 * 复用现有 .subtab-bar / .subtab 样式。
 */
export default function ContentUploadPanel() {
  const [tab, setTab] = useState('indexPdf')

  return (
    <div style={{ padding: 16 }}>
      <div className="subtab-bar">
        <button
          className={tab === 'indexPdf' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('indexPdf')}
        >
          指数构成 PDF
        </button>
        <button
          className={tab === 'analyst' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('analyst')}
        >
          股票分析报告
        </button>
        <button
          className={tab === 'chain' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('chain')}
        >
          产业链报告
        </button>
        <button
          className={tab === 'financials' ? 'subtab active' : 'subtab'}
          onClick={() => setTab('financials')}
        >
          财务数据
        </button>
      </div>
      {tab === 'indexPdf' && <IndexPdfUploadTab />}
      {tab === 'analyst' && <AnalystReportTab />}
      {tab === 'chain' && <IndustryChainTab />}
      {tab === 'financials' && <FinancialUploadTab />}
    </div>
  )
}
```

### 步骤 8.4: 创建 IndexPdfUploadTab.jsx

创建 `frontend/src/components/IndexPdfUploadTab.jsx`：

```javascript
import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

/**
 * 指数构成 PDF 上传 tab。
 * 选择指数 → 上传 PDF → 预览解析结果 → 确认写入。
 */
export default function IndexPdfUploadTab() {
  const [indexList, setIndexList] = useState([])
  const [selectedIndex, setSelectedIndex] = useState('')
  const [asOfDate, setAsOfDate] = useState(new Date().toISOString().slice(0, 10))
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [preview, setPreview] = useState(null)
  const [taskId, setTaskId] = useState(null)
  const [error, setError] = useState('')

  /** 加载可下钻基金关联的指数列表。 */
  const loadIndexList = useCallback(async () => {
    try {
      const res = await api.get('/admin/security-master', { params: { drillable: true, page_size: 200 } })
      const items = res.data.items || []
      // 提取 index_code/index_name 去重
      const indexMap = new Map()
      items.forEach(item => {
        if (item.index_code) {
          indexMap.set(item.index_code, item.index_name || item.index_code)
        }
      })
      setIndexList(Array.from(indexMap.entries()).map(([code, name]) => ({ code, name })))
    } catch (e) {
      console.error('加载指数列表失败', e)
    }
  }, [])

  useEffect(() => { loadIndexList() }, [loadIndexList])

  /** 上传 PDF。 */
  const handleUpload = async () => {
    if (!selectedIndex) { alert('请选择指数'); return }
    if (!file) { alert('请选择 PDF 文件'); return }
    setUploading(true)
    setError('')
    setPreview(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('index_code', selectedIndex)
      formData.append('as_of_date', asOfDate)
      const res = await api.post('/admin/upload/index-pdf', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      if (res.data.status === 'success') {
        setPreview(res.data.preview)
        setTaskId(res.data.task_id)
      } else {
        setError(res.data.error || '解析失败')
      }
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setUploading(false)
    }
  }

  /** 确认写入。 */
  const handleConfirm = async () => {
    if (!taskId) return
    try {
      const res = await api.post('/admin/upload/index-pdf/confirm', { task_id: taskId })
      alert(`写入成功：${res.data.saved} 条`)
      setPreview(null)
      setTaskId(null)
      setFile(null)
    } catch (e) {
      alert('确认失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  return (
    <div>
      <div className="raised" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <select className="ig" value={selectedIndex} onChange={e => setSelectedIndex(e.target.value)}>
            <option value="">选择指数</option>
            {indexList.map(idx => (
              <option key={idx.code} value={idx.code}>{idx.name} ({idx.code})</option>
            ))}
          </select>
          <input type="date" className="ig" value={asOfDate} onChange={e => setAsOfDate(e.target.value)} />
          <input type="file" accept=".pdf" onChange={e => setFile(e.target.files[0])} />
          <button className="btn-ghost" onClick={handleUpload} disabled={uploading}>
            {uploading ? '解析中...' : '上传解析'}
          </button>
        </div>
        {error && <div style={{ color: 'red', marginTop: 8 }}>{error}</div>}
      </div>

      {preview && (
        <div className="raised" style={{ padding: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <strong>解析结果预览（{preview.length} 条）</strong>
            <button className="btn-ghost" onClick={handleConfirm}>确认写入</button>
          </div>
          <div style={{ maxHeight: 400, overflow: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr><th>股票代码</th><th>股票名称</th><th>权重</th></tr>
              </thead>
              <tbody>
                {preview.slice(0, 100).map((c, i) => (
                  <tr key={i}>
                    <td>{c.stock_code}</td>
                    <td>{c.stock_name}</td>
                    <td>{c.weight ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {preview.length > 100 && <div style={{ padding: 8, color: 'var(--text-muted)' }}>仅显示前 100 条，共 {preview.length} 条</div>}
          </div>
        </div>
      )}
    </div>
  )
}
```

### 步骤 8.5: 创建 AnalystReportTab.jsx

创建 `frontend/src/components/AnalystReportTab.jsx`：

```javascript
import React, { useState } from 'react'
import { rawApi as api } from '../api'

/**
 * 股票分析报告上传 tab。
 * 多文件拖拽 → 上传 → 显示每文件状态。
 */
export default function AnalystReportTab() {
  const [files, setFiles] = useState([])
  const [uploading, setUploading] = useState(false)
  const [results, setResults] = useState(null)

  /** 上传文件。 */
  const handleUpload = async () => {
    if (files.length === 0) { alert('请选择文件'); return }
    setUploading(true)
    setResults(null)
    try {
      const formData = new FormData()
      files.forEach(f => formData.append('files', f))
      const res = await api.post('/admin/upload/analyst-report', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResults(res.data.results)
    } catch (e) {
      alert('上传失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <div className="raised" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input type="file" multiple accept=".docx" onChange={e => setFiles(Array.from(e.target.files))} />
          <button className="btn-ghost" onClick={handleUpload} disabled={uploading}>
            {uploading ? '上传中...' : '上传'}
          </button>
        </div>
        <div style={{ marginTop: 8, color: 'var(--text-muted)', fontSize: 12 }}>
          文件名需包含股票代码（6 位数字 + .SH/.SZ/.HK），如 "688041.SH公司研究框架.docx"
        </div>
      </div>

      {results && (
        <div className="raised" style={{ padding: 16 }}>
          <strong>上传结果</strong>
          <table className="data-table" style={{ marginTop: 8 }}>
            <thead>
              <tr><th>文件名</th><th>股票代码</th><th>状态</th><th>错误</th></tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i}>
                  <td>{r.filename}</td>
                  <td>{r.stock_code || '-'}</td>
                  <td style={{ color: r.status === 'success' ? 'green' : 'red' }}>{r.status}</td>
                  <td>{r.error || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
```

### 步骤 8.6: 创建 IndustryChainTab.jsx

创建 `frontend/src/components/IndustryChainTab.jsx`：

```javascript
import React, { useState } from 'react'
import { rawApi as api } from '../api'

/**
 * 产业链报告上传 tab。
 * 输入产业链名称 → 上传总结 + 公司清单 → 显示结果。
 */
export default function IndustryChainTab() {
  const [chainName, setChainName] = useState('')
  const [summaryFile, setSummaryFile] = useState(null)
  const [companyFile, setCompanyFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)

  /** 上传。 */
  const handleUpload = async () => {
    if (!chainName) { alert('请输入产业链名称'); return }
    if (!summaryFile || !companyFile) { alert('请选择两个文件'); return }
    setUploading(true)
    setResult(null)
    try {
      const formData = new FormData()
      formData.append('chain_name', chainName)
      formData.append('summary_file', summaryFile)
      formData.append('company_list_file', companyFile)
      const res = await api.post('/admin/upload/industry-chain', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResult(res.data)
    } catch (e) {
      alert('上传失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <div className="raised" style={{ padding: 16, marginBottom: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input className="ig" placeholder="产业链名称（如：AI产业链）" value={chainName} onChange={e => setChainName(e.target.value)} />
          <div>
            <label style={{ marginRight: 8 }}>总结报告 MD：</label>
            <input type="file" accept=".md" onChange={e => setSummaryFile(e.target.files[0])} />
          </div>
          <div>
            <label style={{ marginRight: 8 }}>公司清单 MD：</label>
            <input type="file" accept=".md" onChange={e => setCompanyFile(e.target.files[0])} />
          </div>
          <button className="btn-ghost" onClick={handleUpload} disabled={uploading} style={{ alignSelf: 'flex-start' }}>
            {uploading ? '上传中...' : '上传'}
          </button>
        </div>
      </div>

      {result && (
        <div className="raised" style={{ padding: 16 }}>
          <strong>上传结果</strong>
          <div style={{ marginTop: 8 }}>产业链保存：{result.chain_saved ? '成功' : '失败'}</div>
          <div>公司清单保存：{result.companies_saved} 条</div>
        </div>
      )}
    </div>
  )
}
```

### 步骤 8.7: 创建 FinancialUploadTab.jsx

创建 `frontend/src/components/FinancialUploadTab.jsx`：

```javascript
import React, { useState } from 'react'
import { rawApi as api } from '../api'

/**
 * 财务数据上传 tab。
 * 子 tab 切换：Excel 批量 / 单条表单。
 */
export default function FinancialUploadTab() {
  const [subtab, setSubtab] = useState('excel')

  return (
    <div>
      <div className="subtab-bar" style={{ marginBottom: 12 }}>
        <button className={subtab === 'excel' ? 'subtab active' : 'subtab'} onClick={() => setSubtab('excel')}>
          Excel 批量
        </button>
        <button className={subtab === 'single' ? 'subtab active' : 'subtab'} onClick={() => setSubtab('single')}>
          单条表单
        </button>
      </div>
      {subtab === 'excel' ? <ExcelUpload /> : <SingleForm />}
    </div>
  )
}

/** Excel 批量上传。 */
function ExcelUpload() {
  const [market, setMarket] = useState('CN')
  const [asOfDate, setAsOfDate] = useState(new Date().toISOString().slice(0, 10))
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)

  const handleUpload = async () => {
    if (!file) { alert('请选择文件'); return }
    setUploading(true)
    setResult(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('market', market)
      formData.append('as_of_date', asOfDate)
      const res = await api.post('/admin/upload/financials', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResult(res.data)
    } catch (e) {
      alert('上传失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="raised" style={{ padding: 16 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="ig" value={market} onChange={e => setMarket(e.target.value)}>
          <option value="CN">A 股</option>
          <option value="HK">港股</option>
        </select>
        <input type="date" className="ig" value={asOfDate} onChange={e => setAsOfDate(e.target.value)} />
        <input type="file" accept=".xlsx,.xls" onChange={e => setFile(e.target.files[0])} />
        <button className="btn-ghost" onClick={handleUpload} disabled={uploading}>
          {uploading ? '上传中...' : '上传'}
        </button>
      </div>
      {result && (
        <div style={{ marginTop: 12 }}>
          <div>状态：{result.status}</div>
          <div>导入：{result.imported} 条</div>
          {result.errors?.length > 0 && (
            <div style={{ color: 'red', marginTop: 4 }}>
              错误：{result.errors.slice(0, 5).join('; ')}
              {result.errors.length > 5 && ` 等 ${result.errors.length} 条`}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** 单条表单。 */
function SingleForm() {
  const [form, setForm] = useState({
    stock_code: '',
    stock_name: '',
    pe_ttm: '',
    pb_mrq: '',
    ps_ttm: '',
    dividend_yield: '',
    market_cap: '',
    as_of_date: new Date().toISOString().slice(0, 10),
  })
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!form.stock_code) { alert('请输入股票代码'); return }
    setSaving(true)
    try {
      // 转换数值字段
      const data = { ...form }
      ;['pe_ttm', 'pb_mrq', 'ps_ttm', 'dividend_yield', 'market_cap'].forEach(k => {
        if (data[k] === '') delete data[k]
        else data[k] = parseFloat(data[k])
      })
      await api.post('/admin/upload/financials/single', data)
      alert('保存成功')
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="raised" style={{ padding: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, maxWidth: 600 }}>
        <label>股票代码 *</label>
        <input className="ig" value={form.stock_code} onChange={e => setForm({ ...form, stock_code: e.target.value })} placeholder="600519.SH" />
        <label>股票名称</label>
        <input className="ig" value={form.stock_name} onChange={e => setForm({ ...form, stock_name: e.target.value })} />
        <label>PE(TTM)</label>
        <input className="ig" type="number" value={form.pe_ttm} onChange={e => setForm({ ...form, pe_ttm: e.target.value })} />
        <label>PB(MRQ)</label>
        <input className="ig" type="number" value={form.pb_mrq} onChange={e => setForm({ ...form, pb_mrq: e.target.value })} />
        <label>PS(TTM)</label>
        <input className="ig" type="number" value={form.ps_ttm} onChange={e => setForm({ ...form, ps_ttm: e.target.value })} />
        <label>股息率</label>
        <input className="ig" type="number" value={form.dividend_yield} onChange={e => setForm({ ...form, dividend_yield: e.target.value })} />
        <label>总市值(亿)</label>
        <input className="ig" type="number" value={form.market_cap} onChange={e => setForm({ ...form, market_cap: e.target.value })} />
        <label>截止日期 *</label>
        <input className="ig" type="date" value={form.as_of_date} onChange={e => setForm({ ...form, as_of_date: e.target.value })} />
      </div>
      <button className="btn-ghost" onClick={handleSave} disabled={saving} style={{ marginTop: 12 }}>
        {saving ? '保存中...' : '保存'}
      </button>
    </div>
  )
}
```

### 步骤 8.8: 前端 build 验证

```bash
cd frontend
npm run build
```

预期：build 成功，无错误。

### 步骤 8.9: commit

```bash
cd d:\claude_code_project\PortfolioM\.worktrees\auth-upgrade
git add frontend/src/api.js frontend/src/App.jsx frontend/src/components/ContentUploadPanel.jsx frontend/src/components/IndexPdfUploadTab.jsx frontend/src/components/AnalystReportTab.jsx frontend/src/components/IndustryChainTab.jsx frontend/src/components/FinancialUploadTab.jsx
git commit -m "feat(ui): ContentUploadPanel with 4 tabs + admin token interceptor (Task 8)"
```

---

## Task 9: 集成测试 + 最终验证

### 步骤 9.1: 运行全部后端测试

```bash
cd backend
python -m pytest tests/ -v
```

预期：所有测试 PASS（包括子项目 1 的 54 个测试 + 子项目 2 新增的 ~20 个测试）。

### 步骤 9.2: 前端 build

```bash
cd frontend
npm run build
```

预期：build 成功。

### 步骤 9.3: 更新 Project_development.md

在 `Project_development.md` 的"项目修复"章节后添加"子项目 2：内容上传套件"章节：

```markdown
## 子项目 2：内容上传套件（2026-06-24）

### 完成内容

1. **文件上传基础设施**：backend/uploads/{pdf,doc,md,csv}/ 目录 + StaticFiles 挂载 + upload_service
2. **PDF 三层解析**：pdfplumber → OCR（pytesseract）→ AI 辅助（LLM API）
3. **4 类上传功能**：
   - 指数构成 PDF 上传 + 确认写入
   - 股票分析报告 DOCX 上传（复用 analyst_parser）
   - 产业链报告 MD 上传（复用 analyst_parser）
   - 财务数据上传（Excel 批量 + 单条表单）
4. **前端**：ContentUploadPanel 4 tab + admin token 拦截器修复

### 新增文件
- backend/services/upload_service.py
- backend/services/pdf_parser_service.py
- backend/services/llm_service.py
- backend/services/financial_upload_service.py
- frontend/src/components/{IndexPdfUploadTab,AnalystReportTab,IndustryChainTab,FinancialUploadTab}.jsx

### 新增依赖
- pdfplumber, pytesseract, Pillow, pdf2image, python-multipart

### 系统依赖
- tesseract-ocr（OCR 引擎）
- poppler（pdf2image 依赖）
```

### 步骤 9.4: commit

```bash
cd d:\claude_code_project\PortfolioM\.worktrees\auth-upgrade
git add Project_development.md
git commit -m "docs: update Project_development.md for subproject 2"
```

---

## 自审清单

### Spec 覆盖
- [x] 文件存储设计 → Task 1
- [x] PDF 三层解析 → Task 3
- [x] 指数构成 PDF 上传 → Task 4
- [x] 股票分析报告上传 → Task 5
- [x] 产业链报告上传 → Task 6
- [x] 财务数据上传（Excel + 单条）→ Task 7
- [x] 前端 4 tab → Task 8
- [x] admin token 鉴权 → Task 8（修复子项目 1 遗留问题）
- [x] 测试策略 → 每个 Task 都有测试

### Placeholder scan
- [x] 无 TBD/TODO
- [x] 每个步骤都有完整代码
- [x] 每个测试都有实际断言

### Type consistency
- [x] ParseResult dataclass 在 Task 3 定义，Task 4 使用
- [x] upload_service 函数签名在 Task 1 定义，Task 4-7 使用
- [x] API 路径前缀一致（/api/admin/upload/）
- [x] admin token 注入方式一致（x-admin-token 头）
