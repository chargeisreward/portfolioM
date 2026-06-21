# Drill-down math and valuation

This document explains why the drilled-dimension analysis works the way it does: why it ignores direct stocks and undrilled funds, how a single PE number is produced for an entire industry bucket, and how the CSI300 benchmark is kept comparable.

---

## What "drilled-only" means

A portfolio can hold stocks directly, but it also holds funds whose underlying stocks are not visible on the surface. The **full-holding** view merges direct stocks with the fund-level line items. The **drilled-dimension** view goes one level deeper: it replaces each drillable fund with the actual stocks that fund owns, estimated from the fund's 5/29 NAV and constituent weights.

Why look only at drilled securities?

- Direct stocks are already visible in other tabs. The value of drilling is to expose the equity exposure inside index funds.
- Comparing a full portfolio against CSI300 mixes fund wrappers with stock-level weights, which makes sector bets harder to interpret.
- Drilled-only numbers answer a specific question: *"If I treat every index fund as its underlying basket of stocks, how does my implied sector allocation compare to the index?"*

The trade-off is that non-drillable holdings — cash, bonds, gold ETFs, QDII funds without a mapped index — disappear from this view. They still appear in the full-holding table and overview cards.

---

## From fund shares to equivalent stock shares

The drill-down starts with a fund holding and converts it into an equivalent number of underlying shares.

```text
fund_value_at_baseline = Holding.quantity × FundDailyNav.nav_529
stock_amount_529       = fund_value_at_baseline × constituent_weight_pct / 100
shares_equivalent      = stock_amount_529 / constituent_baseline_price
```

- `nav_529` is the fund's unit net asset value on the baseline date (2026-05-29).
- `constituent_weight_pct` comes from `IndexConstituentSnapshot` for the same baseline date.
- `constituent_baseline_price` is the stock's closing price on the baseline date from `a_share_financial_snapshot` or `hk_share_financial_snapshot`.

If a fund tracks the same index through multiple fund codes, or if the same stock appears in several tracked indices, the equivalent shares and amounts are summed.

---

## Valuing the position today

Once we have equivalent shares, the current market value is:

```text
est_market_value_cny = shares_equivalent × current_price
```

`current_price` is the latest close from the financial snapshot. For USD and HKD stocks, the amount is converted to CNY using the most recent `ExchangeRate` row before aggregation.

If the baseline price or current price is missing, the code falls back to using `stock_amount_529` directly. This avoids dropping a position entirely, but it means the current value is stale.

---

## Virtual earnings: weighted PE, PB, and PS

A bucket can contain dozens of stocks. A simple arithmetic average of their PE ratios would treat a tiny position the same as a large one. Instead, the analysis uses a **virtual earnings** (harmonic-weighted) method:

```text
weighted_pe = Σ amount_cny / Σ (amount_cny / pe_ttm)
```

Why this formula?

- `amount_cny / pe_ttm` is proportional to the earnings represented by that position.
- Summing those earnings gives the bucket's total virtual earnings.
- Dividing the bucket's total amount by total virtual earnings gives the portfolio-weighted PE.

The same pattern applies to PB and PS:

```text
weighted_pb = Σ amount_cny / Σ (amount_cny / pb_mrq)
weighted_ps = Σ amount_cny / Σ (amount_cny / ps_ttm)
```

If a stock has no PE (or it is zero or negative), it contributes nothing to the virtual-earnings sum. The bucket's weighted PE is then based only on the positions that do have a valid PE.

---

## Dynamic vs snapshot PE

The financial snapshots store two PE values:

- `pe_ttm` — the trailing-twelve-months PE as of the baseline date.
- `pe_ttm_dynamic` — adjusted by the ratio of current price to baseline price, so the PE reflects the latest price even when the earnings figure is from the baseline report.

The drilled-dimension endpoint uses `pe_ttm_dynamic` when available and falls back to `pe_ttm` otherwise. This matters because the baseline date can be weeks behind the current business date, and stock prices move.

---

## CSI300 benchmark construction

The CSI300 side is built from `csi300_constituent_snapshot`, which contains the index weights on the same `as_of_date`.

```text
csi_weight_pct = constituent_weight / Σ constituent_weight × 100
weighted_pe    = Σ weight / Σ (weight / pe_dynamic)
```

Important details:

- The CSI300 table's own `swy_l1` … `csi_l4` columns default to `其他` for every row. The endpoint therefore reads the classification from `a_share_financial_snapshot` or `hk_share_financial_snapshot` first, and only falls back to the CSI300 table's value when the snapshot has no classification.
- PE/PB/PS also come from the financial snapshots first, because the CSI300 table's `pe_ttm_dynamic` column is frequently null.
- The same `market` filter is applied to both sides, so an `A`-only request compares the A-share portion of the drilled portfolio against the A-share portion of CSI300.

---

## Market filtering

The `market` parameter restricts which stocks enter both the portfolio and CSI300 buckets:

| `market` | Portfolio rule | CSI300 rule |
|----------|----------------|-------------|
| `A+H` | all drilled stocks | all constituents |
| `A` | code ends with `.SH`/`.SZ` or is a 6-digit number | same |
| `H` | code ends with `.HK` or is a 5-digit number | same |

This is how the A-share strategic emerging tab works: the UI sends `market=A` for the `se1` dimension, so only A-share stocks are counted.

---

## Null and missing classifications

Any empty or placeholder classification is normalized to `其他`:

```python
def _norm_bucket_key(k):
    if not k or k in ("--", "—", "nan", "None", "", "其他"):
        return "其他"
    return k
```

This keeps the table clean and prevents a missing label from splitting one industry into multiple rows.

---

## Tracking the underlying drill logic

The math above is implemented in `backend/services/drillable_funds.py`:

- `list_drillable_indices()` — identifies which indices have tracking funds.
- `get_index_drill_detail()` — computes equivalent shares for one index.
- `get_all_drilled_stocks()` — aggregates across all indices.

The dimension aggregation and CSI300 comparison live in `backend/main.py` in the `get_dimension_drilled()` route handler.

---

## Related documentation

- [Drilled-dimension analysis reference](./reference-dimension-drilled.md) — API parameters, response schema, and component props.
- [How to read the drilled-dimension panel](./howto-read-drilled-dimension-panel.md) — interpreting the numbers in the UI.
- [Tutorial: first drilled-dimension analysis](./tutorial-first-drilled-dimension-analysis.md) — a hands-on walkthrough.
