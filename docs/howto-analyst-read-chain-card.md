# How to Read an Industry-Chain Card

Each industry-chain card on the 产业链 sub-tab folds into a 3-column comparison table and expands to show the held stocks plus the chain narrative. Use this guide to interpret the weights and the dual PE/PB/PS numbers.

## Prerequisites

- Familiarity with the folded-vs-expanded card pattern from [Tutorial: read your first analyst card](./tutorial-analyst-first-walkthrough.md).

## What the folded card shows

Folded, each chain card displays six rows in a three-column grid: `指标 | 资产 | 300指数`. A small note line above the grid explains the denominators.

```
300指数权重 = 产业链内沪深300成分股原始指数权重之和
资产权重 = 产业链内下钻持仓合计金额 / 全部下钻证券合计金额

指标       资产          300指数
权重       17.17%        15.368
规模(CNY)  616,659       -
PE(加权)   108.7         82.7
PB(加权)   27.0          17.5
PS(加权)   18.2          13.3
股票数     29            16
```

## How the two columns differ

The two columns are computed independently. They do not share a denominator and their totals do not need to add up.

### 资产 column — drilled portfolio holdings

Scope: stocks in this chain that you currently hold, where `FullHoldingSnapshot.source_type` is `drilled_fund` or `direct_stock` (the drilled-only denominator).

- **权重** = `Σ amount_cny` of those stocks, divided by `Σ amount_cny` of every row in `FullHoldingSnapshot` whose `source_type` is in `('drilled_fund', 'direct_stock')` on the same business date.
- **规模(CNY)** = `Σ amount_cny` of the held stocks in this chain (raw CNY).
- **PE/PB/PS (加权)** — virtual-earnings weighted: `Σ(amount) / Σ(amount / pe_ttm_dynamic)`. PE values ≤ 0 or null are skipped from the divisor.
- **股票数** = count of distinct stocks.

### 300指数 column — CSI300 constituents in the chain

Scope: stocks in this chain that appear in `Csi300ConstituentSnapshot` for the business date.

- **权重** = `Σ weight` of those constituents. The weight comes straight from the CSI300 constituent snapshot — it is **not** renormalized to anything else. This matches the convention used by the CSI300 drill-down elsewhere in PortfolioM.
- **规模(CNY)** = `null` (the CSI300 column intentionally omits scale).
- **PE/PB/PS (加权)** — virtual-earnings weighted using the same formula as the asset column, but with `weight` in place of `amount`. If the CSI300 constituent snapshot has no dynamic valuation for a stock, the per-stock PE/PB/PS is fetched from `AShareFinancialSnapshot` / `HKShareFinancialSnapshot` as a fallback.
- **股票数** = count of constituents.

## How to interpret the numbers

- A 资产权重 near 0% but a non-zero 股票数 means the chain mentions the stock but you hold very little of it.
- A 300指数权重 sum across all chains exceeds 100%? Possible, because it sums raw index weights of overlapping constituents (e.g. one stock can be in both AI and Apple narratives). It is **not** a portfolio allocation percentage.
- If 资产 PE is higher than 300指数 PE, the part of the chain you hold is richer on average than the index constituents.

## Expanded view: held stocks

Click the card header to expand. Inside, the **组合** tab lists every stock from the chain that is currently in your portfolio, sorted by `relevance_stars` (descending), then by `组合权重` (descending) as a tie-breaker.

| 产业链位置 | 细分环节 | 公司简称 | 证券代码 | 相关程度 | 组合权重% | 金额(CNY) |
|---|---|---|---|---|---|---|
| 上游-算力核心硬件 | AI芯片 | 寒武纪 | 688256.SH | ★★★★★ | 2.08% | 122,341 |
| 上游-算力核心硬件 | 光模块 | 中际旭创 | 300308.SZ | ★★★★★ | 2.90% | 170,337 |

Stars are colored orange (`#ff8c1a`) for solid stars, gray for empty stars, so a 4-star stock shows four orange stars and one gray star.

## Expanded view: narrative

The **研究报告** tab renders the chain summary Markdown (the content of `AI产业链 总结报告.md`) with bold/italic preserved and numbers highlighted in yellow. No badges are injected here; only the company valuation section gets the close-price badge.

## Verification

You can confirm the numbers shown on the card by running:

```bash
psql "$DATABASE_URL" -c "
  SELECT chain_name, COUNT(*) AS rows
  FROM analyst_industry_chain_company
  GROUP BY chain_name;
"
```

If a chain is missing rows, re-run the ingest endpoint ([How to ingest analyst data](./howto-analyst-restock-data.md)).

## Troubleshooting

- **股票数 > 0 but 资产 PE is null** — every held stock in this chain has a missing or non-positive PE, so the virtual-earnings sum is empty.
- **300指数 column is entirely null** — no stock in the chain is in the CSI300 snapshot for that date.
- **Held-stock table shows "当前 portfolio 未持有该产业链中的公司"** — the chain has no `stock_code` matching any row in `FullHoldingSnapshot` on the business date. Re-ingest data or pick a different business date.