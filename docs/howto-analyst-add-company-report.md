# How to Add a New Company Research Report

This guide adds a 9th company report to the existing 8. The end result is a new card on the Analyst panel with the same six sections as the others.

## Prerequisites

- A DOCX file containing the six-section research framework (see step 1 for the keyword list).
- Backend running with `researcher/` readable from `BASE_DIR / "researcher"`.
- `ADMIN_TOKEN` environment variable.

## Steps

### 1. Build the DOCX

The file must be a valid DOCX whose paragraph styles include `Heading 1` (or Chinese equivalents that match the parser's keyword list). Use these section titles, in order:

1. `一、市场为什么关注` (or any heading containing `市场为什么关注`)
2. `二、核心竞争力` (also matches `核心经营变量`, `Fundamentals`)
3. `三、供需格局` (also matches `竞争格局`, `Industry`)
4. `四、边际变化` (also matches `Marginal Change`)
5. `五、怎么估值` (also matches `估值`, `Valuation`)
6. `六、风险是什么` (also matches `Alpha`, `市场忽视了什么`, `Risk`)

If you cannot use Word's heading styles, type the section titles as plain text — the parser falls back to a plain-text keyword scan when no heading styles are detected.

The first paragraph of the report should ideally start with the company name in parentheses:

```
寒武纪（688256.SH）：国内 AI 训练芯片龙头...
```

This lets `_extract_stock_name` auto-fill the company name. Without it, the parser leaves `stock_name` null and `SecurityMaster` becomes the only source.

### 2. Name the file

The filename must start with the stock code (6 digits + 2-letter exchange suffix):

```text
researcher/688256.SH公司研究框架.docx
```

The exchange suffix must match the snapshot table for the stock. Use `SH` for Shanghai, `SZ` for Shenzhen, `HK` for Hong Kong.

### 3. Drop the file in place

```bash
cp ~/Desktop/寒武纪研究报告.docx researcher/688256.SH公司研究框架.docx
```

The directory is at the repo root, not inside `backend/`.

### 4. Ingest

```bash
curl -X POST http://localhost:8001/api/admin/analyst/ingest \
     -H "x-admin-token: $ADMIN_TOKEN"
```

The response will report one new `company_reports.parsed`. Existing rows for other stocks are upserted, not deleted.

### 5. Verify

```bash
curl "http://localhost:8001/api/analyst/core-companies?as_of_date=$(date +%Y-%m-%d)" \
     -H "x-session-token: $TOKEN" \
     | jq '.companies[] | select(.stock_code == "688256.SH")'
```

The output should include `stock_name`, `report_sections` populated for all six sections, and `portfolio` either populated or null depending on whether the stock is currently held.

The Analyst panel will display the new card after the next data refresh (Vite picks up changes without a restart).

## Verification

Open `http://localhost:5173`, click **分析师** → **核心公司**. The new card appears in the grid, sorted by portfolio weight descending.

## Troubleshooting

- **`stock_name` is `null` in the API** — the first paragraph did not match `(.{2,20})[（(]<code>[）)][:：]`. Add a "公司名（证券代码）：" line at the top of the document.
- **Six sections all empty** — the parser could not find any matching heading. Confirm the heading text matches one of the keyword patterns above (case-sensitive for the English keywords).
- **The card appears but `report_available: false`** — every section is empty and `raw_text` is null. This happens when the DOCX is corrupt or uses unsupported Word features. Re-export from Word as `.docx` (not `.doc` or `.docx.xml`).
- **Updates are not visible in the UI** — the panel is keyed by `stock_code`; if you reuse a code, the old report is overwritten. New stock codes appear as a new card; the existing card list is ordered by stock_code.