"""解析 researcher/ 目录下的分析师报告文件。

支持：
  - DOCX 公司研究报告（6 段式框架）
  - Markdown 产业链总结报告
  - Markdown 产业链公司清单表格
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
except Exception:  # pragma: no cover
    Document = None
    Table = None
    Paragraph = None
    qn = None


RESEARCHER_DIR = Path(__file__).resolve().parent.parent.parent / "researcher"

# DOCX 公司报告 6 段式框架关键字（出现在章节标题中）
SECTION_KEYWORDS = [
    ("section_1_market_focus", ["Narrative", "市场为什么关注"]),
    ("section_2_core_competence", ["Fundamentals", "核心竞争力", "核心经营变量"]),
    ("section_3_supply_demand", ["Industry", "供需格局", "竞争格局"]),
    ("section_4_marginal_change", ["Marginal Change", "边际变化"]),
    ("section_5_valuation", ["Valuation", "怎么估值", "估值"]),
    ("section_6_risk", ["Risk", "Alpha", "风险是什么", "市场忽视了什么"]),
]


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _qn_local(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _get_attr(element: ET.Element, local: str) -> str | None:
    return element.get(_qn_local(local))


def _xml_style_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """从 word/styles.xml 解析段落样式 ID -> 样式名称。"""
    if "word/styles.xml" not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read("word/styles.xml"))
    ns = {"w": W_NS}
    mapping: dict[str, str] = {}
    for style in root.findall(".//w:style[@w:type='paragraph']", ns):
        style_id = _get_attr(style, "styleId")
        name_node = style.find("w:name", ns)
        if style_id and name_node is not None:
            name = _get_attr(name_node, "val") or ""
            mapping[style_id] = name
    return mapping


def _xml_runs_to_md(para: ET.Element, ns: dict[str, str]) -> str:
    """把 paragraph 内所有 run 的文本和加粗/斜体转成 HTML 标记。"""
    out: list[str] = []
    for r in para.findall(".//w:r", ns):
        rPr = r.find("w:rPr", ns)
        bold = rPr is not None and rPr.find("w:b", ns) is not None
        italic = rPr is not None and rPr.find("w:i", ns) is not None
        text = "".join(t.text or "" for t in r.findall("w:t", ns))
        if not text:
            continue
        text = text.replace("\n", " ")
        if bold:
            text = f"<strong>{text}</strong>"
        if italic:
            text = f"<em>{text}</em>"
        out.append(text)
    return "".join(out).strip()


def _xml_table_to_md(tbl: ET.Element, ns: dict[str, str]) -> list[str]:
    """把 word/document.xml 中的 table 转成 Markdown 表格。"""
    rows: list[list[str]] = []
    for tr in tbl.findall("w:tr", ns):
        cells = []
        for tc in tr.findall("w:tc", ns):
            cell_text = "".join(t.text or "" for t in tc.findall(".//w:t", ns)).strip().replace("\n", " ")
            cells.append(cell_text)
        rows.append(cells)
    if not rows:
        return []
    lines = ["| " + " | ".join(cells) + " |" for cells in rows]
    sep = "|" + "|".join(["---"] * len(rows[0])) + "|"
    lines.insert(1, sep)
    return lines


def _extract_docx_paragraphs(path: Path) -> list[str]:
    """不依赖 python-docx，直接从 word/document.xml 抽取富文本 Markdown。"""
    with zipfile.ZipFile(path) as zf:
        data = zf.read("word/document.xml")
        style_map = _xml_style_map(zf)
    root = ET.fromstring(data)
    ns = {"w": W_NS}
    body = root.find(".//w:body", ns)
    if body is None:
        return []

    items: list[str] = []
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            md_text = _xml_runs_to_md(child, ns)
            if not md_text:
                continue
            pPr = child.find("w:pPr", ns)
            style_id = None
            if pPr is not None:
                pStyle = pPr.find("w:pStyle", ns)
                if pStyle is not None:
                    style_id = _get_attr(pStyle, "val")
            style_name = style_map.get(style_id, "")
            prefix = _style_to_prefix(style_name, md_text)
            items.append(prefix + md_text)
        elif tag == "tbl":
            items.extend(_xml_table_to_md(child, ns))
    return items


def _heading_level(style_name: str) -> int | None:
    m = re.match(r"Heading\s*(\d+)", style_name or "")
    return int(m.group(1)) if m else None


def _style_to_prefix(style_name: str, text: str) -> str:
    """根据 DOCX 段落样式生成 Markdown 前缀。"""
    if not style_name:
        return ""
    level = _heading_level(style_name)
    if level:
        return "#" * level + " "
    # 列表段落：若原文已带编号则保留，否则统一用无序列表
    if "List" in style_name:
        if re.match(r"^\d+\.\s", text):
            return ""
        return "- "
    return ""


def _runs_to_md(runs) -> str:
    """把 DOCX run 的加粗/斜体转成 HTML 标记（避免中文标点旁 Markdown ** 解析异常）。"""
    out: list[str] = []
    for r in runs:
        text = r.text or ""
        if not text:
            continue
        text = text.replace("\n", " ")
        if r.bold:
            text = f"<strong>{text}</strong>"
        if r.italic:
            text = f"<em>{text}</em>"
        out.append(text)
    return "".join(out).strip()


def _table_to_md(table) -> list[str]:
    """把 DOCX 表格转成 Markdown 表格行列表。"""
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        rows.append(cells)
    if not rows:
        return []
    lines = ["| " + " | ".join(cells) + " |" for cells in rows]
    sep = "|" + "|".join(["---"] * len(rows[0])) + "|"
    lines.insert(1, sep)
    return lines


def _extract_docx_paragraphs_with_docx(path: Path) -> list[str]:
    if Document is None or qn is None:
        return []
    doc = Document(path)
    items: list[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            md_text = _runs_to_md(p.runs)
            if not md_text:
                continue
            prefix = _style_to_prefix(p.style.name if p.style else None, md_text)
            items.append(prefix + md_text)
        elif child.tag == qn("w:tbl") and Table is not None:
            tbl = Table(child, doc)
            items.extend(_table_to_md(tbl))
    return items


def _extract_docx_text(path: Path) -> list[str]:
    """优先用 python-docx 提取富文本 Markdown；失败则回退到手动 XML 提取。"""
    try:
        return _extract_docx_paragraphs_with_docx(path)
    except Exception:
        return _extract_docx_paragraphs(path)


def _detect_section(header: str, is_heading: bool = True) -> str | None:
    """根据章节标题返回对应的模型字段名；无法识别返回 None。

    python-docx 路径中只把真正的标题行（Heading 样式，已转成 # 前缀）当章节标题，
    避免正文里出现的关键字被误判。
    """
    if not is_heading:
        return None
    for field, keywords in SECTION_KEYWORDS:
        if any(kw in header for kw in keywords):
            return field
    return None


def _parse_stock_code_from_filename(filename: str) -> tuple[str | None, str | None]:
    m = re.search(r"^(\d+)\.([A-Z]{2})", filename)
    if m:
        return m.group(1) + "." + m.group(2), m.group(2)
    return None, None


def _extract_stock_name(first_paras: list[str], stock_code: str) -> str | None:
    """尝试从报告前几个段落提取公司名称。

    匹配模式：海光信息（688041.SH）：...  或  中际旭创(300308.SZ): ...
    """
    code_no_suffix = stock_code.split(".")[0]
    patterns = [
        rf"^(.{{2,20}})[（(]{re.escape(stock_code)}[）)][:：]",
        rf"^(.{{2,20}})[（(]{re.escape(code_no_suffix)}[）)][:：]",
    ]
    for text in first_paras:
        for pat in patterns:
            m = re.match(pat, text)
            if m:
                return m.group(1).strip()
    return None


def parse_company_report(path: str | Path) -> dict[str, Any]:
    """解析单个公司研究报告 DOCX。

    返回字典可直接用于 upsert `AnalystCompanyReport`。
    """
    path = Path(path)
    filename = path.name
    stock_code, exchange = _parse_stock_code_from_filename(filename)

    try:
        paragraphs = _extract_docx_text(path)
    except Exception as e:
        return {
            "success": False,
            "stock_code": stock_code,
            "error": f"extract docx failed: {e}",
        }

    stock_name = _extract_stock_name(paragraphs, stock_code or "")

    # 把段落按行展开：有些 DOCX 会把所有章节标题放在同一段落的不同行
    lines: list[str] = []
    for para in paragraphs:
        for line in para.split("\n"):
            line = line.strip()
            if line:
                lines.append(line)

    sections: dict[str, list[str]] = {field: [] for field, _ in SECTION_KEYWORDS}
    current_field: str | None = None

    # 判断是否为 python-docx 输出的 Markdown（带 # 标题）；否则回退到纯文本标题检测
    uses_rich_markdown = any(line.startswith("#") for line in lines)

    for line in lines:
        # 跳过纯标题行
        if line == filename.replace(".docx", ""):
            continue
        field = _detect_section(line, is_heading=(line.startswith("#") or not uses_rich_markdown))
        if field:
            current_field = field
            continue
        if current_field:
            sections[current_field].append(line)

    result = {
        "success": True,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "exchange": exchange,
        "source_file": str(path),
        "parsed_at": datetime.utcnow(),
    }
    for field, _ in SECTION_KEYWORDS:
        result[field] = "\n".join(sections[field]).strip() or None
    result["raw_text"] = "\n".join(lines)
    return result


def _strip_yaml_frontmatter(text: str) -> str:
    if text.startswith("---"):
        m = re.search(r"^---\n.*?\n---\n", text, re.DOTALL)
        if m:
            return text[m.end():]
    return text


def _parse_chain_name(filename: str) -> str | None:
    """从文件名解析产业链名称，如 'AI产业链 公司清单.md' -> 'AI产业链'。"""
    m = re.search(r"^(.*?)产业链", filename)
    if m:
        return m.group(1).strip() + "产业链"
    return None


def parse_chain_summary(path: str | Path) -> dict[str, Any]:
    """解析产业链总结报告 Markdown。"""
    path = Path(path)
    filename = path.name
    chain_name = _parse_chain_name(filename)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "chain_name": chain_name, "error": str(e)}

    return {
        "success": True,
        "chain_name": chain_name,
        "narrative_md": _strip_yaml_frontmatter(text).strip(),
        "source_file": str(path),
        "parsed_at": datetime.utcnow(),
    }


# 产业链公司清单表格列名 -> 模型字段映射
KNOWN_LIST_COLUMNS = {
    "产业链位置": "chain_position",
    "细分环节": "sub_segment",
    "公司简称": "company_name",
    "证券代码": "stock_code",
    "市值区间": "market_cap_range",
    "相关程度": "relevance_stars",
    "相关理由": "relevance_reason",
    "最新进展": "latest_progress",
    "订单能见度": "order_visibility",
    "客户导入": "customer_onboarding",
    "业绩弹性": "earnings_elasticity",
    "业绩弹性预期": "earnings_elasticity",
}


def _count_stars(text: str) -> int | None:
    if not text:
        return None
    return text.count("★")


def _split_md_table_row(line: str) -> list[str]:
    parts = [p.strip().strip("*") for p in line.split("|")]
    # 去掉首尾因管道符产生的空串
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def parse_chain_company_list(path: str | Path) -> dict[str, Any]:
    """解析产业链公司清单 Markdown 表格。"""
    path = Path(path)
    filename = path.name
    chain_name = _parse_chain_name(filename)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "chain_name": chain_name, "error": str(e)}

    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    lines = text.splitlines()
    in_table = False
    headers: list[str] = []
    col_map: list[str | None] = []
    prev_chain_position = ""
    prev_sub_segment = ""
    row_index = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = _split_md_table_row(line)
        if not cells:
            continue

        # 分隔行 |-|-|-|
        if all(re.match(r"^[-:]+$", c) for c in cells if c):
            in_table = True
            continue

        if not in_table:
            # 表头行
            headers = cells
            col_map = [KNOWN_LIST_COLUMNS.get(h) for h in headers]
            in_table = True
            continue

        # 数据行
        if len(cells) != len(headers):
            errors.append(f"row {row_index} column mismatch: {raw_line[:80]}")
            row_index += 1
            continue

        row: dict[str, Any] = {
            "chain_name": chain_name,
            "source_file": str(path),
            "row_index": row_index,
            "parsed_at": datetime.utcnow(),
            "extra_json": {},
        }
        for idx, cell in enumerate(cells):
            field = col_map[idx]
            if field is None:
                # 未映射列按表头原样存到 extra_json
                header = headers[idx]
                if header and cell:
                    row["extra_json"][header] = cell
                continue
            if field == "chain_position":
                if cell:
                    prev_chain_position = cell
                row[field] = prev_chain_position
            elif field == "sub_segment":
                if cell:
                    prev_sub_segment = cell
                row[field] = prev_sub_segment
            elif field == "relevance_stars":
                row[field] = _count_stars(cell)
            elif field == "stock_code":
                row[field] = cell if cell and cell != "-" else None
            else:
                row[field] = cell or None

        # 未上市或空 code 也保留，因为产业链页面会按 portfolio 过滤
        rows.append(row)
        row_index += 1

    return {
        "success": True,
        "chain_name": chain_name,
        "rows": rows,
        "errors": errors,
        "source_file": str(path),
    }


def parse_all(researcher_dir: str | Path | None = None) -> dict[str, Any]:
    """批量解析 researcher 目录，返回汇总结果（尚未写入数据库）。"""
    if researcher_dir is None:
        researcher_dir = RESEARCHER_DIR
    researcher_dir = Path(researcher_dir)

    company_reports: list[dict[str, Any]] = []
    chain_summaries: list[dict[str, Any]] = []
    chain_company_lists: list[dict[str, Any]] = []

    for path in sorted(researcher_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".docx"):
            company_reports.append(parse_company_report(path))
        elif name.endswith("总结报告.md"):
            chain_summaries.append(parse_chain_summary(path))
        elif name.endswith("公司清单.md"):
            chain_company_lists.append(parse_chain_company_list(path))

    return {
        "company_reports": company_reports,
        "chain_summaries": chain_summaries,
        "chain_company_lists": chain_company_lists,
    }


if __name__ == "__main__":  # pragma: no cover
    import json
    result = parse_all()
    print(json.dumps({
        "companies": [r.get("stock_code") for r in result["company_reports"]],
        "chains": [r.get("chain_name") for r in result["chain_summaries"]],
        "list_rows": {r.get("chain_name"): len(r.get("rows", [])) for r in result["chain_company_lists"]},
    }, ensure_ascii=False, indent=2))
