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
    # 相对路径使用正斜杠（URL 风格，跨平台一致）
    relative_path = f"uploads/{category}/{new_filename}"
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
