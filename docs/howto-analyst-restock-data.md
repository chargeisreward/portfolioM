# How to Ingest Analyst Data

Re-parse the `researcher/` directory and overwrite the three `analyst_*` tables. Use this when you add or edit a DOCX company report, or when you change a Markdown chain summary or company list.

The ingest endpoint reads every `.docx`, `*总结报告.md`, and `*公司清单.md` under `researcher/`, extracts structured data, and upserts it. Existing rows for the same `stock_code` or `chain_name` are overwritten; chain company lists are deleted and re-inserted per chain.

## Prerequisites

- Backend running on `http://localhost:8001`.
- `ADMIN_TOKEN` environment variable (same as `APP_PASSWORD`).
- `researcher/` directory at the repo root, with files named as in the rules below.

## Steps

### 1. Lay out files under `researcher/`

Filename conventions that the parser relies on:

- **Company report (DOCX)**: must start with the stock code (digits + exchange suffix). Example: `688041.SH公司研究框架.docx`.
- **Chain summary (Markdown)**: must end with `总结报告.md` and contain a substring ending in `产业链`. Example: `AI产业链 总结报告.md`.
- **Chain company list (Markdown)**: must end with `公司清单.md` and contain a substring ending in `产业链`. Example: `AI产业链 公司清单.md`.

### 2. Write the company list table

The chain company list parser expects a Markdown table whose column headers map as follows:

| Header text | Model field |
|---|---|
| 产业链位置 | `chain_position` |
| 细分环节 | `sub_segment` |
| 公司简称 | `company_name` |
| 证券代码 | `stock_code` |
| 市值区间 | `market_cap_range` |
| 相关程度 | `relevance_stars` (count of `★`) |
| 相关理由 | `relevance_reason` |
| 最新进展 | `latest_progress` |
| 订单能见度 | `order_visibility` |
| 业绩弹性 | `earnings_elasticity` |
| 客户导入 | `customer_onboarding` |

Empty cells in `产业链位置` and `细分环节` are inherited from the previous row, so a vertical group can be written without repeating the position. Any column header not in the map above is stored verbatim in `extra_json`.

Example:

```markdown
|产业链位置|细分环节|公司简称|证券代码|相关程度|
|-|-|-|-|-|
|上游-算力核心硬件|AI芯片|海光信息|688041.SH|★★★★★|
||光模块|中际旭创|300308.SZ|★★★★★|
```

### 3. Trigger the ingest endpoint

```bash
curl -X POST http://localhost:8001/api/admin/analyst/ingest \
     -H "x-admin-token: $ADMIN_TOKEN"
```

Expected response:

```json
{
  "status": "ok",
  "company_reports": {"parsed": 8, "errors": 0},
  "industry_chains": {"parsed": 3, "errors": 0},
  "company_list_rows": {"parsed": 113, "errors": 0}
}
```

A non-zero `errors` count means some files failed to parse. The page will still load; failed files simply do not appear. Inspect the backend log for the per-file error message.

### 4. Verify

Pick any company code and check the core-companies endpoint:

```bash
curl "http://localhost:8001/api/analyst/core-companies?as_of_date=$(date +%Y-%m-%d)" \
     -H "x-session-token: $TOKEN"
```

The response should list the 8 stocks with `report_sections` populated.

## Verification

The frontend Analyst tab now shows the updated content without a restart (Vite HMR). If a section is missing, the parser likely could not find a matching heading keyword; see [Explanation: analyst parser section matching](./explanation-analyst-parser-section-matching.md).

## Troubleshooting

- **`researcher/AI产业链 公司清单.md` errors with `column mismatch`** — a row has a different column count than the header. Count `|` characters; separators must align.
- **A company report's six sections are all empty** — the DOCX uses unusual heading styles. The parser falls back to plain-text detection when `Heading` styles are absent; the fallback relies on the section keywords appearing as plain text. If the source uses Chinese-only headings, check the keyword list in `services/analyst_parser.py:32`.
- **Chain company list rows are missing the `产业链位置`** — every row needs either an explicit value or a non-empty value in the previous row. The first row of the table must be explicit.
- **DOCX parse fails on Windows** — `python-docx` is imported lazily; if it is missing, the parser falls back to direct XML extraction. Both paths produce equivalent output.