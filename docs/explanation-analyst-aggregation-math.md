# Analyst Aggregation Math

The Analyst panel exposes three numerical "stories" — single-stock portfolio weight, source-fund equivalents, and chain-vs-CSI300 comparison. Each story has its own denominator and its own weighting convention. This doc explains *why* those choices were made and what they imply when you read the cards.

## The problem

You hold a fund, say `007818.OF`, that owns 47 stocks. The `007818.OF` row in `FullHoldingSnapshot` shows a CNY amount for the fund itself, but the drill-down reveals that the fund's 47 stocks contribute to many industry chains. When you ask "what's my exposure to AI chain?", you cannot just sum the funds' CNY amounts — that double-counts the funds' own weights and ignores the underlying stock picks.

The penetration model already solves this by storing `PenetrationSnapshot` rows that attribute stock amounts back to funds. The Analyst panel reuses that data, plus the CSI300 index weights, to give chain-level answers.

## Single-stock weight

For a stock present on the analyst catalog:

```
weight_pct = stock.amount_cny_in_holdings / total_portfolio_amount * 100
```

Where `total_portfolio_amount` is the sum of every row in `FullHoldingSnapshot` for the business date, regardless of `source_type`.

### Why the broad denominator

A folded card's weight is "what fraction of my portfolio is this one stock", which should match the total portfolio amount (the same denominator used elsewhere in PortfolioM). The drilled-only denominator belongs to the chain-vs-CSI300 comparison, not the per-stock card. Mixing them would make the same stock show different weights on different panels.

## Source-fund equivalent

For a single stock, `get_stock_detail` queries `PenetrationSnapshot` for rows where the fund ultimately holds this stock. For each fund:

```
equivalent_shares = fund.amount_cny_dynamic / current_price
```

The dynamic amount is the CNY value of the share-equivalent on the business date (it already accounts for the fund's daily NAV changes). Dividing by the current closing price of the stock gives back the share count as if you owned the shares directly.

```
ratio_in_portfolio_pct = fund.amount_cny_dynamic / total_portfolio_amount
ratio_in_fund_pct     = fund.amount_cny_dynamic / fund.holding_amount_cny
```

The two ratios answer different questions: "how much of my portfolio is this fund's slice of this stock?" vs "how concentrated is this fund in this stock?". The latter can exceed 100% only if the penetration snapshot is wrong; otherwise a fund's `amount_cny_dynamic` for any one stock should be ≤ its total holding amount.

## Chain-vs-CSI300 portfolio column

For a chain's asset column, only `FullHoldingSnapshot` rows with `source_type in ('drilled_fund', 'direct_stock')` are eligible. This is the **drilled-only** denominator — it strips out undrilled funds and cash, so the comparison reflects equity exposure you actually see in your portfolio.

```
weight_pct = sum(chain_stocks.drilled_amount) / total_drilled_amount * 100
pe_weighted = sum(amount) / sum(amount / pe_ttm_dynamic)
```

The PE/PB/PS formula is **virtual-earnings weighted**. The intuition: if you hold $100 of a stock with PE = 10, and $50 of a stock with PE = 20, your "earnings" are $100/10 + $50/20 = 12.5, and your weighted PE is $150/12.5 = 12. It avoids the trap of arithmetic weighting (which understates rich-P/Es) and equal weighting (which ignores amounts).

Skipped values: any stock with `pe_ttm_dynamic ≤ 0` or `null` is excluded from the divisor's denominator. The numerator (the total amount) still includes that stock's amount, so the resulting weighted PE is computed only over the stocks where PE is meaningful.

## Chain-vs-CSI300 benchmark column

For the CSI300 column, the weights come from `Csi300ConstituentSnapshot.weight` — the raw index weights stored in percentage points. No renormalization is applied:

```
weight_pct = sum(constituents.weight)
pe_weighted = sum(weight) / sum(weight / pe_ttm_dynamic)
```

### Why the raw weight, not "relative to drilled"

CSI300 drill-down elsewhere in PortfolioM uses the constituent weight as-is. Using the same convention in the chain card lets you compare apples to apples across panels: the 300指数 column here shows the same underlying number you'd see in any other CSI300 drill-down.

A renormalized version (e.g. "weight relative to the total CSI300 weight of all your drilled constituents") was considered and rejected: it would hide how concentrated a chain's index exposure is, and it would conflate two distinct questions — "how big is this chain in the index" and "how much of *my* CSI300 exposure falls in this chain".

### Fallback valuation

The CSI300 constituent snapshot's `pe_ttm_dynamic` / `pb_mrq_dynamic` / `ps_ttm_dynamic` may be null for stocks that are constituents but have no financial snapshot. The aggregator then queries `AShareFinancialSnapshot` or `HKShareFinancialSnapshot` (same business date, same code) and uses that row's dynamic value, falling back to the static value.

## Held-stock ordering in the chain card

The backend sorts the chain company list by `chain_position` (upstream → midstream → downstream), then by `relevance_stars` desc, then by `chain_name`. The frontend re-sorts in `ChainCard.jsx` with a different rule — `relevance_stars` desc, then `portfolio_weight_pct` desc — because the user-facing table already filters to held stocks only and the position grouping is less informative when 29 stocks are listed.

This double-sort is intentional: the backend order preserves the chain-position grouping for any future listing that wants it, and the frontend order serves the "what matters most in my portfolio" question.

## Trade-offs

- **Drilled-only denominator** in the chain card vs **total denominator** on the single-stock card — the user must remember which is which. Mixing them in one card would require two weight columns.
- **Static PE fallback** in `AnalystCompanyReport`'s valuation field — the parser keeps the raw text from the DOCX; if the report was written before the latest earnings, the PE shown on the card (from the financial snapshot) will not match the PE quoted in the report text. The `close X.XX` badge does not attempt to reconcile this.
- **Sort cache via `WeakMap`** in `ChainCard.jsx` — the sorted result is memoized on the input list reference. If `companies_in_portfolio` is replaced (e.g. on a refetch), a new sort happens. Acceptable because the input list is replaced wholesale on each fetch.

## Related

- [Drill-down math and valuation](./explanation-drilled-dimension-math.md) — virtual-earnings weighting in the broader drilled-dimension panel; same formula, different denominator.
- [Drilled-dimension analysis reference](./reference-dimension-drilled.md) — the API shape that Analyst reuses for the CSI300 column.