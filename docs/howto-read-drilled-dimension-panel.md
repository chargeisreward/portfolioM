# How to read the drilled-dimension panel

The drilled-dimension panel shows whether your portfolio's implied sector bets differ from the CSI300 index. This guide walks through each column and explains what the red/green colors mean.

---

## Prerequisites

- You have imported holdings and run penetration calculation so that drillable funds are expanded into underlying stocks.
- The business date is set (the panel loads `current_business_date` from `/api/data-version`).
- You are on the **分析 → 申万L1** (or L2/L3, 中证L1–L4, A股战略新兴) tab.

---

## Step 1: Understand what is being compared

Open a tab such as **申万L1**. The table compares:

- **组合 (portfolio)** — your drilled-down holdings, grouped by SW L1 industry.
- **CSI300** — the same industries in the CSI300 index, using index weights.

Only securities reached by drilling index funds are included. Direct stocks and undrilled funds are not in this view.

---

## Step 2: Read the core columns

| Column | What it tells you | Good to know |
|--------|-------------------|--------------|
| **{维度}** | Industry bucket, e.g. `电子`, `医药生物`, `其他`. | `其他` means the stock has no classification or a placeholder value. |
| **只数** | Number of distinct stocks in this bucket. | Counted after deduplication across funds and indices. |
| **金额(CNY)** | Total market value of the bucket in CNY. | USD/HKD positions are converted at the latest exchange rate. |
| **权重%** | Bucket value / total drilled value × 100. | This is your active sector exposure inside drilled funds. |
| **组合PE** | Weighted PE of the bucket. | Uses virtual-earnings weighting: larger positions matter more. |
| **CSI300权重%** | Index weight of the same bucket in CSI300. | Based on `csi300_constituent_snapshot`. |
| **CSI300 PE** | Weighted PE of the same bucket in CSI300. | Uses the same virtual-earnings method. |
| **权重差异%** | Portfolio weight − CSI300 weight. | Positive means overweight; negative means underweight. |
| **PE差异** | Portfolio PE − CSI300 PE. | Positive means you are paying a higher multiple for that sector. |

---

## Step 3: Interpret the colors

The panel follows Chinese market coloring: **red = high / positive**, **green = low / negative**.

- **权重%** — red if your portfolio weight is higher than CSI300, green if lower.
- **组合PE** — red if your bucket PE is higher than CSI300's, green if lower.
- **权重差异%** — red for positive (overweight), green for negative (underweight).
- **PE差异** — red for positive (more expensive), green for negative (cheaper).

A row that is red on both weight and PE means you are both overweight and paying a higher valuation for that sector.

---

## Step 4: Drill into a bucket

Click any row to expand the stock detail table. It shows:

- **代码 / 名称** — the underlying stock.
- **约当数量** — equivalent shares inferred from the fund's 5/29 NAV and constituent weight.
- **最近收盘价** — latest close converted to CNY.
- **资产值** — `约当数量 × 最近收盘价`, in CNY.
- **权重%** — stock value / total drilled value × 100.
- **PE / PS / PB** — valuation ratios from the financial snapshot.

Use this to see which single stocks are driving a sector overweight or high valuation.

---

## Step 5: Spot common patterns

| Pattern | What it usually means |
|---------|----------------------|
| Overweight + higher PE | Conviction bet: you accept a richer valuation for more exposure. |
| Overweight + lower PE | Value tilt: more exposure at a cheaper valuation than the index. |
| Underweight + higher PE | Avoidance: you are steering clear of an expensive sector. |
| Large `其他` bucket | Classification data is missing. Check the financial snapshots and import source files. |
| CSI300 weight exists but portfolio weight is zero | You have no drilled exposure to that sector. |
| Portfolio weight exists but CSI300 weight is zero | You hold a sector that is not in CSI300 at all. |

---

## Troubleshooting

### All PE values show "-"

The financial snapshot has no `pe_ttm_dynamic` or `pe_ttm` for those stocks. Verify that `refresh_company_financials` or the price crawler has run for the business date.

### USD/HKD amounts look wrong

Check `/api/exchange-rates/latest`. If USD or HKD rates are stale, the conversion will be off. The rate source is the PBoC crawling pipeline.

### A bucket contains stocks from the wrong market

Confirm you selected the right `market` filter. For example, **A股战略新兴** sends `market=A` automatically.

### `其他` is unexpectedly large

`其他` appears when:

- The classification column is null or a placeholder (`--`, `nan`, `None`, `其他`).
- The CSI300 table has no mapping for that stock.
- The A-share / HK-share snapshot has not been imported for the business date.

Inspect `a_share_financial_snapshot` / `hk_share_financial_snapshot` for the `swy_l1`/`csi_l1`/`se_l1` values of the stocks in question.

---

## Related documentation

- [Drilled-dimension analysis reference](./reference-dimension-drilled.md) — API and data model details.
- [Drill-down math and valuation](./explanation-drilled-dimension-math.md) — how the numbers are computed.
- [Tutorial: first drilled-dimension analysis](./tutorial-first-drilled-dimension-analysis.md) — a complete walkthrough.
