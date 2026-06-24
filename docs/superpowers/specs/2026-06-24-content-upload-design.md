# 内容上传套件 — 子项目 2 设计文档

> **日期**: 2026-06-24
> **范围**: 子项目 2（内容上传套件）
> **状态**: 设计已确认，待写实施计划
> **前置**: 子项目 1（管理员数据运维管理重构）已完成

## 1. 背景与目标

### 1.1 问题

当前内容导入全部依赖本地文件系统读取：
- 指数构成数据：爬虫（仅 CSI300）+ Excel 手动导入（本地路径）
- 股票分析报告：从 `researcher/` 目录解析 DOCX（本地路径）
- 产业链报告：从 `researcher/` 目录解析 MD（本地路径）
- 财务数据：从 `sourceData/` 目录解析 Excel（本地路径）

**痛点**：
- 无法通过 Web UI 上传，必须 SSH 到服务器放文件
- 指数构成 PDF 无法解析（只支持 Excel）
- 无文件上传基础设施（FastAPI UploadFile + 静态文件服务）
- 无 PDF 解析能力
- 无 OCR 能力

### 1.2 目标

- 建立文件上传基础设施（FastAPI UploadFile + uploads/ 目录 + 静态文件服务）
- 实现 4 类上传功能：
  1. 指数构成 PDF 上传 + 三层解析（pdfplumber → OCR → AI 辅助）
  2. 股票分析报告 DOCX 上传（复用现有解析器）
  3. 产业链报告 MD 上传（复用现有解析器）
  4. 财务数据上传（Excel 批量 + 前端表单单条）
- 文件按类型分子目录持久化存储，自动添加时间戳

### 1.3 不在范围内

- yfinance 集成（非中港 PE/PB/PS）→ 子项目 3
- 已有爬虫功能的改造
- 前端样式大改

## 2. 文件存储设计

### 2.1 目录结构

位置：`backend/uploads/`（与 `main.py` 同级，便于 StaticFiles 挂载）

```
backend/uploads/
├── pdf/     # 指数构成 PDF
│   └── 沪深300_000300_20260624_153022.pdf
├── doc/     # 股票分析报告 DOCX
│   └── 688041.SH_20260624_153022.docx
├── md/      # 产业链报告 MD
│   └── AI产业链_20260624_153022.md
└── csv/     # 财务数据 Excel/CSV
    └── 全部A股_20260624_153022.xlsx
```

### 2.2 命名规则

```
{原始文件名去掉扩展名}_{时间戳YYYYMMDD_HHMMSS}.{扩展名}
```

时间戳避免重名覆盖。原始文件名保留用于人工识别。原始文件名中的特殊字符（路径分隔符、空格）需清洗为下划线。

### 2.3 静态文件服务

在 `backend/main.py` 中新增：

```python
from fastapi.staticfiles import StaticFiles
import os

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
```

管理员可通过 `GET /uploads/{category}/{filename}` 预览/下载已上传文件。

## 3. PDF 解析三层策略

### 3.1 策略概述

```
PDF 输入
  │
  ▼
第一层：pdfplumber 表格提取
  │ ├─ 成功（表格结构清晰）→ 返回结果
  │ └─ 失败/结果不确定 ↓
  ▼
第二层：OCR（pytesseract）
  │ ├─ 成功（识别出表格）→ 返回结果
  │ └─ 失败/结果不确定 ↓
  ▼
第三层：AI 辅助（LLM API）
  │ ├─ 成功 → 返回结果
  │ └─ 失败 → 返回错误，提示用户手动修正
```

### 3.2 第一层：pdfplumber

- 适用于文本型 PDF（非扫描件）
- `pdfplumber.extract_tables()` 提取表格
- 识别列：股票代码、股票名称、权重
- 成功标准：提取到 >= 10 条记录，且代码列格式匹配（6 位数字或带后缀）

### 3.3 第二层：OCR

- 适用于扫描型 PDF 或 pdfplumber 提取失败
- 用 `pdf2image` 将 PDF 页转为图片
- 用 `pytesseract` 识别图片中的文字和表格
- 后处理：正则提取股票代码（6 位数字）、名称、权重
- 成功标准：同第一层

### 3.4 第三层：AI 辅助

- 前两层结果不确定时触发
- 将 PDF 文本/OCR 结果发送给 LLM
- Prompt：解析指数成分股表格，返回 JSON `[{stock_code, stock_name, weight}, ...]`
- LLM 选择：可配置（OpenAI / Claude / 通义千问）
- 成功标准：LLM 返回有效 JSON 且记录数 >= 10
- **未配置 LLM 时**：如果环境变量 `LLM_API_KEY` 未设置或为空，第三层直接返回 `ParseResult(success=False, method="ai", error="LLM 未配置")`，前端提示用户手动修正或配置 LLM

### 3.5 解析结果处理

```python
@dataclass
class ParseResult:
    success: bool
    method: str          # "pdfplumber" / "ocr" / "ai"
    constituents: list   # [{stock_code, stock_name, weight}, ...]
    confidence: float    # 0-1
    error: str | None
```

## 4. 四类上传功能

### 4.1 指数构成 PDF 上传

**前端**：
1. 选择指数（从 SecurityMaster 中 is_drillable=True 的基金关联的指数列表）
2. 选择 as_of_date（默认今天）
3. 拖拽/选择 PDF 文件
4. 点击上传
5. 显示解析进度 + 结果预览（表格）
6. 确认后写入数据库

**后端**：
1. 保存 PDF 到 `uploads/pdf/`
2. 调用 `pdf_parser_service.parse_index_pdf(path, index_code)`
3. 三层解析策略
4. 将解析结果暂存到内存字典 `_parse_cache: dict[str, dict]`（key=task_id, value={index_code, as_of_date, constituents, parsed_at}）
5. 返回 task_id + 预览结果
6. 用户确认后从内存字典取出并写入 `IndexConstituentSnapshot`
7. **TTL 清理**：内存字典中超过 1 小时的条目自动清理（避免内存泄漏）
8. **task_id 生成**：`secrets.token_urlsafe(8)` 生成短 ID

**API**：
```
POST /api/admin/upload/index-pdf
  参数：index_code, as_of_date, file (UploadFile)
  返回：{task_id, status, method, preview: [{stock_code, stock_name, weight}, ...]}

POST /api/admin/upload/index-pdf/confirm
  参数：task_id
  返回：{status, saved: N}
  错误：task_id 不存在或已过期 → 404
```

### 4.2 股票分析报告上传（DOCX）

**前端**：
1. 拖拽/选择 DOCX 文件（支持多文件）
2. 显示上传进度
3. 显示每文件的解析状态（成功/失败）
4. 点击查看解析结果

**后端**：
1. 保存 DOCX 到 `uploads/doc/`
2. 复用 `analyst_parser.parse_company_report(path)`（已接受 `str | Path`，无需改造）
3. 从文件名解析股票代码：复用 `analyst_parser._parse_stock_code_from_filename(filename)`（已存在，正则匹配 6 位数字 + .SH/.SZ/.HK 后缀）
4. Upsert 到 `AnalystCompanyReport`
5. 如果文件名无法解析出股票代码，返回错误跳过该文件

**API**：
```
POST /api/admin/upload/analyst-report
  参数：files (List[UploadFile])
  返回：{results: [{filename, stock_code, status, error}, ...]}
```

### 4.3 产业链报告上传（MD）

**前端**：
1. 选择产业链名称（输入或下拉）
2. 上传"总结报告"MD 文件
3. 上传"公司清单"MD 文件
4. 显示解析结果预览

**后端**：
1. 保存 MD 到 `uploads/md/`
2. 复用 `analyst_parser.parse_chain_summary(path)` 和 `parse_chain_company_list(path)`
3. Upsert 到 `AnalystIndustryChain` + `AnalystIndustryChainCompany`

**API**：
```
POST /api/admin/upload/industry-chain
  参数：chain_name, summary_file (UploadFile), company_list_file (UploadFile)
  返回：{status, chain_saved: bool, companies_saved: N}
```

### 4.4 财务数据上传

#### 4.4.1 Excel 批量上传

**前端**：
1. 选择市场（A 股 / 港股）
2. 拖拽/选择 Excel 文件
3. 显示导入进度 + 结果

**后端**：
1. 保存 Excel 到 `uploads/csv/`
2. 复用 `import_a_share_financials.py` / `import_hk_share_financials.py` 逻辑
3. 写入 `AShareFinancialSnapshot` / `HKShareFinancialSnapshot`

**API**：
```
POST /api/admin/upload/financials
  参数：market (CN/HK), file (UploadFile)
  返回：{status, imported: N, errors: [...]}
```

#### 4.4.2 前端表单单条补足

**前端**：
1. 输入 stock_code
2. 填写字段：pe_ttm, pb_mrq, ps_ttm, dividend_yield, market_cap, industry_sw 等
3. 提交

**后端**：
1. 直接 upsert 到 `AShareFinancialSnapshot` / `HKShareFinancialSnapshot`
2. 根据代码后缀判断市场：
   - `.SH` / `.SZ` → A 股（写入 `AShareFinancialSnapshot`）
   - `.HK` → 港股（写入 `HKShareFinancialSnapshot`）
   - `.OF` / 其他 → 返回错误"单条财务上传仅支持 A 股（.SH/.SZ）和港股（.HK）"
3. 必填字段校验：stock_code 不能为空

**API**：
```
POST /api/admin/upload/financials/single
  参数：{stock_code, pe_ttm, pb_mrq, ps_ttm, ...}
  返回：{status}
  错误：不支持的代码后缀 → 400
```

## 5. 后端服务层

```
backend/services/
├── upload_service.py           ← 新建：文件保存 + 路径管理 + 时间戳命名
├── pdf_parser_service.py       ← 新建：三层解析（pdfplumber → OCR → AI）
├── analyst_parser.py           ← 复用：DOCX/MD 解析（已接受 str | Path，无需改造）
├── financial_upload_service.py ← 新建：Excel 导入 + 单条写入
└── llm_service.py              ← 新建：LLM API 调用（AI 辅助层）
```

### 5.1 upload_service.py

```python
def save_upload_file(file: UploadFile, category: str) -> str:
    """保存上传文件到 uploads/{category}/，返回相对路径。"""

def list_uploads(category: str | None = None) -> list[dict]:
    """列出已上传文件。"""

def get_upload_path(filename: str) -> str:
    """获取文件完整路径。"""
```

### 5.2 pdf_parser_service.py

```python
def parse_index_pdf(pdf_path: str, index_code: str) -> ParseResult:
    """三层策略解析指数构成 PDF。"""

def _parse_with_pdfplumber(pdf_path: str) -> ParseResult:
    """第一层：pdfplumber 表格提取。"""

def _parse_with_ocr(pdf_path: str) -> ParseResult:
    """第二层：OCR 识别。"""

def _parse_with_ai(text: str, index_code: str) -> ParseResult:
    """第三层：AI 辅助解析。"""
```

### 5.3 llm_service.py

```python
def parse_table_with_llm(text: str, prompt: str) -> dict | None:
    """调用 LLM 解析表格文本，返回结构化结果。"""
```

LLM 配置通过环境变量：
- `LLM_API_KEY` — API 密钥
- `LLM_API_BASE` — API 地址
- `LLM_MODEL` — 模型名称

## 6. 前端组件

```
ContentUploadPanel.jsx（4 tab）
├── IndexPdfUploadTab.jsx     # 指数构成 PDF 上传
├── AnalystReportTab.jsx      # 股票分析报告上传
├── IndustryChainTab.jsx      # 产业链报告上传
└── FinancialUploadTab.jsx    # 财务数据上传（Excel + 表单切换）
```

### 6.1 IndexPdfUploadTab

- 指数选择下拉（从 SecurityMaster 获取 is_drillable=True 的基金关联的指数）
- 日期选择器
- 文件拖拽区
- 解析结果预览表格
- 确认写入按钮

### 6.2 AnalystReportTab

- 多文件拖拽区
- 上传进度条
- 结果列表（文件名 / 股票代码 / 状态）

### 6.3 IndustryChainTab

- 产业链名称输入
- 两个文件上传区（总结报告 + 公司清单）
- 解析结果预览

### 6.4 FinancialUploadTab

- 子 tab 切换：Excel 批量 / 单条表单
- Excel：市场选择 + 文件上传
- 表单：stock_code + 字段输入 + 提交

## 7. 新增依赖

```
# requirements.txt 新增
pdfplumber>=0.11.0      # PDF 表格提取
pytesseract>=0.3.10     # OCR（需系统安装 tesseract-ocr）
Pillow>=10.0.0          # 图像处理
pdf2image>=1.17.0       # PDF 转图片（OCR 前置）
python-multipart        # FastAPI 文件上传支持
```

**系统依赖**：
- tesseract-ocr（OCR 引擎）
- poppler（pdf2image 依赖）

**Windows 安装**（开发环境）：
- tesseract-ocr：从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装包，默认安装到 `C:\Program Files\Tesseract-OCR\`，需将该路径加入系统 PATH
- poppler：从 https://github.com/oschwartz10612/poppler-windows/releases 下载，解压到 `C:\poppler\`，需将 `C:\poppler\Library\bin` 加入系统 PATH

**生产环境（Linux/Docker）**：
- Dockerfile 中添加：`RUN apt-get update && apt-get install -y tesseract-ocr poppler-utils`

**前端依赖**：无需新增，使用现有 `rawApi`（支持 FormData，通过 `fetch` 原生支持）

## 8. 前端 API 调用方式

前端 `api.js` 中 `rawApi` 已基于 `fetch` 实现，支持 FormData：

```javascript
// 上传文件示例
const formData = new FormData();
formData.append('file', file);
formData.append('index_code', indexCode);
formData.append('as_of_date', asOfDate);

const res = await fetch('/api/admin/upload/index-pdf', {
  method: 'POST',
  headers: { 'Authorization': `Bearer ${token}` },  // 不设置 Content-Type，让浏览器自动设置 boundary
  body: formData,
});
```

无需修改 `api.js`，直接在组件中使用 `fetch` 调用上传端点。

## 9. 测试策略

| 层级 | 测试文件 | 测试数 | 覆盖 |
|---|---|---|---|
| upload_service | `test_upload_service.py` | ~4 | 文件保存 + 路径 + 列表 |
| pdf_parser_service | `test_pdf_parser_service.py` | ~5 | 三层解析策略 |
| financial_upload_service | `test_financial_upload_service.py` | ~4 | Excel 导入 + 单条写入 |
| API 集成 | `test_upload_api.py` | ~8 | 端到端上传流程 |

## 10. 迁移计划

1. 创建 `backend/uploads/` 目录及子目录（pdf/doc/md/csv）
2. 安装 Python 依赖：`pip install pdfplumber pytesseract Pillow pdf2image python-multipart`
3. 安装系统依赖（见第 7 节）
4. 配置 LLM 环境变量（可选，如使用 AI 辅助）：
   - `LLM_API_KEY` — API 密钥
   - `LLM_API_BASE` — API 地址（默认 OpenAI）
   - `LLM_MODEL` — 模型名称（默认 gpt-4o-mini）
5. 重启后端服务
6. 前端 build

## 11. 鉴权

所有上传端点走 `/api/admin/upload/` 前缀，复用现有 session token + admin 角色校验（与子项目 1 一致）。

## 12. 后续子项目

### 子项目 3：yfinance 集成

- 后端 yfinance service
- 非中港市场（US 等）PE/PB/PS 自动补足
- scheduler 定时任务
- 仅用于此用途，节省限流额度
