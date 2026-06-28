# Tutorial: Your first drilled-dimension analysis

In this tutorial you will open the drilled-dimension analysis panel, compare your portfolio's sector weights against CSI300, and inspect the underlying stocks driving a divergence. By the end you will know how to spot an overweight/expensive sector bet.

---

## What you'll need

- PortfolioM backend and frontend running locally.
- Holdings imported and penetration calculation completed (so that drillable funds have been expanded into stocks).
- A business date with snapshot data (for example `2026-05-29` — the business date, see [reference-data-business-date.md](./reference-data-business-date.md)).

---

## Step 1: Open the 申万L1 drilled panel

In the frontend, click the **分析** tab in the top navigation. Then click **申万L1**.

You will see a table with columns: 行业, 只数, 金额(CNY), 权重%, 组合PE, CSI300权重%, CSI300 PE, 权重差异%, PE差异.

The panel header also shows the drilled-only total and the overall portfolio vs CSI300 PE:

```text
申万L1 — 仅下钻证券 · 12 项
下钻合计 12,345,678 CNY · 组合PE 32.1 · CSI300 PE 21.2
```

If the panel shows "暂无下钻证券数据", verify that:

- Holdings have been imported.
- `FundIndexMap` has mappings for the business date.
- `FundDailyNav` has NAV data for the business date (`2026-05-29`) and the latest trading day on or before today.

---

## Step 2: Find a sector where you diverge from CSI300

Look at the **权重差异%** column. Find a row with a large positive number (red). For example:

| 行业 | 权重% | 组合PE | CSI300权重% | CSI300 PE | 权重差异% | PE差异 |
|------|-------|--------|-------------|-----------|-----------|--------|
| 电子 | 28.5% | 36.5 | 18.3% | 28.4 | +10.2% | +8.1 |

This tells you:

- Your drilled portfolio has 28.5% in 电子, while CSI300 has 18.3%.
- You are **overweight** 电子 by 10.2 percentage points.
- Your 电子 bucket trades at 36.5× PE, while CSI300 电子 trades at 28.4×.
- You are paying an 8.1-point PE premium for that overweight.

A red/red combination means a high-conviction, high-valuation bet.

---

## Step 3: Expand the row to see the underlying stocks

Click the **电子** row. The detail table expands below it.

You will see rows like:

| 代码 | 名称 | 约当数量 | 最近收盘价 | 资产值 | 权重% | PE | PS | PB |
|------|------|----------|------------|--------|-------|----|----|----|
| 688981.SH | 中芯国际 | 1,500 | 52.30 | 78,450 | 0.64% | 85.4 | 12.1 | 3.2 |
| 002371.SZ | 北方华创 | 800 | 320.50 | 256,400 | 2.08% | 42.3 | 8.5 | 5.1 |

The stocks are sorted by asset value descending. The largest positions are the main drivers of the sector overweight and valuation.

Ask yourself:

- Is the overweight intentional?
- Are the expensive names (high PE) also the largest positions?
- Would you still make this bet if you saw it expressed as single stocks instead of as a fund?

---

## What you built

You now know how to:

- Navigate to a drilled-dimension panel.
- Compare portfolio and CSI300 weights and valuations side by side.
- Identify a sector bet and its valuation premium/discount.
- Expand a bucket to see the individual stocks behind the number.

Next, read [How to read the drilled-dimension panel](./howto-read-drilled-dimension-panel.md) for a full column reference, or [Drill-down math and valuation](./explanation-drilled-dimension-math.md) to understand the weighted PE formula.
