# Drilled-Dimension Analysis Reference

The drilled-dimension analysis compares the portfolio's **drilled-down stock holdings** against the CSI300 index, grouped by industry classification dimensions such as SW L1–L3, CSI L1–L4, and A-share strategic emerging (SE L1). It is intentionally narrower than the full-holding industry breakdown: only securities reached by drilling through index funds are included, so the comparison reflects the equity exposure hidden inside fund holdings rather than direct stock picks.

---

## API Endpoint

### `GET /api/penetration/dimension-drilled`

Returns bucketed portfolio and CSI300 rows for one dimension, plus per-bucket stock details.

#### Query parameters

| Name | Type | Required | Default | Constraint | Description |
|------|------|----------|---------|------------|-------------|
| `dim` | string | yes | — | one of `swy1`, `swy2`, `swy3`, `swy4`, `csi1`, `csi2`, `csi3`, `csi4`, `se1`, `se2`, `se3`, `se4`, `l1`, `l2`, `chain`, `growth_tier`, `competition` | Classification dimension to aggregate by. |
| `as_of_date` | ISO date (`YYYY-MM-DD`) | yes | — | — | Business date used for holdings, snapshots, and CSI300 weights. |
| `market` | string | no | `A+H` | `^(A\+H|A|H)$` | Restrict stocks by market. `A` = A-share only (`.SH`, `.SZ`, or 6-digit codes), `H` = HK only (`.HK` or 5-digit codes), `A+H` = no restriction. |

#### Response schema

```json
{
  "as_of_date": "2026-06-18",
  "dim": "swy1",
  "portfolio": [
    {
      "key": "电子",
      "stock_count": 12,
      "amount_cny": 1234567.89,
      "weight_pct": 23.45,
      "pe_weighted": 36.5,
      "pb_weighted": 4.2,
      "ps_weighted": 3.1
    }
  ],
  "csi300": [
    {
      "key": "电子",
      "stock_count": 45,
      "weight_pct": 18.30,
      "pe_weighted": 28.4,
      "pb_weighted": 3.5,
      "ps_weighted": 2.4
    }
  ],
  "stock_details": {
    "电子": [
      {
        "stock_code": "688981.SH",
        "stock_name": "中芯国际",
        "shares_equivalent": 1500,
        "current_price": 52.3,
        "current_price_cny": 52.3,
        "currency": "CNY",
        "amount_cny": 78450.0,
        "pe_ttm": 85.4,
        "pb_mrq": 3.2,
        "ps_ttm": 12.1,
        "weight_pct": 6.35
      }
    ]
  },
  "totals": {
    "portfolio": {
      "stock_count": 72,
      "amount_cny": 12345678.9,
      "pe_weighted": 32.1,
      "pb_weighted": 3.8,
      "ps_weighted": 2.9
    },
    "csi300": {
      "stock_count": 300,
      "amount_cny": null,
      "pe_weighted": 21.2,
      "pb_weighted": 2.1,
      "ps_weighted": 1.5
    }
  }
}
```

Field notes:

- `key` is the bucket label. Empty, `--`, `nan`, `None`, or `其他` are normalized to `其他`.
- `weight_pct` for portfolio is `amount_cny / total_amount × 100`.
- `weight_pct` for CSI300 is `weight / total_weight × 100` from `csi300_constituent_snapshot`.
- `pe_weighted` / `pb_weighted` / `ps_weighted` are computed with the virtual-earnings method: `Σamount / Σ(amount / metric)`. See [Drill-down math and valuation](./explanation-drilled-dimension-math.md).
- `amount_cny` for USD/HKD stocks is converted using the latest `ExchangeRate` row for `USD→CNY` or `HKD→CNY`.
- `stock_details[].weight_pct` uses the drilled-only total as its denominator.

#### Error responses

- `400 Bad Request` — `dim` is not in the supported mapping.
- `401 Unauthorized` — missing or invalid session token.

---

## React Components

### `<DrilledDimensionPanel />`

`frontend/src/components/DrilledDimensionPanel.jsx`

Renders the tabular comparison panel with expandable per-stock detail rows.

#### Props

| Prop | Type | Required | Description |
|------|------|----------|-------------|
| `dim` | `string` | yes | Dimension key passed to the API (`swy1`, `csi2`, `se1`, …). |
| `bizDate` | `string \| null` | yes | Business date in `YYYY-MM-DD` format. The panel shows a loading state until this is set. |
| `market` | `'A+H' \| 'A' \| 'H'` | no | `'A+H'` | Market filter passed to the API. |
| `label` | `string` | no | `DIM_LABELS[dim] \|\| dim` | Human-readable tab label shown in the header. |

#### Behavior

- Fetches `/api/penetration/dimension-drilled` on mount and whenever `dim`, `bizDate`, or `market` changes.
- Resets the expanded row when the dependency tuple changes.
- Color codes cells using Chinese market convention: **red = high / positive**, **green = low / negative**.
  - Portfolio weight vs CSI300 weight: red if portfolio overweight, green if underweight.
  - Portfolio PE vs CSI300 PE: red if portfolio PE is higher, green if lower.
  - Weight difference and PE difference columns follow the same red/green rule.
- Clicking a row toggles the per-bucket stock detail table rendered by `<DrilledStockDetailTable />`.

### `<DrilledStockDetailTable />`

Internal sub-component of `DrilledDimensionPanel`. Renders the expanded stock list for one bucket.

#### Props

| Prop | Type | Description |
|------|------|-------------|
| `stocks` | `Array<object>` | Items from `stock_details[bucket_key]`, sorted by `amount_cny` descending. |

Columns: code, name, equivalent shares, latest close (CNY), asset value, weight %, PE, PS, PB.

---

## Backend Functions

### `get_dimension_drilled(...)`

`backend/main.py` — FastAPI route handler.

- Validates `dim` against `DIM_COL_DRILLED` mapping.
- Pre-loads `AShareFinancialSnapshot`, `HKShareFinancialSnapshot`, `FundIndexMap`, `FundDailyNav`, and `Holding` rows to avoid repeated queries.
- Calls `get_all_drilled_stocks(...)` for the drilled-only portfolio.
- Buckets portfolio rows by the requested dimension column.
- Buckets CSI300 rows from `Csi300ConstituentSnapshot` by the same dimension.
- Computes totals and returns `portfolio`, `csi300`, `stock_details`, and `totals`.

### `get_all_drilled_stocks(...)`

`backend/services/drillable_funds.py`

Aggregates drill-down results across every drillable index. Accepts pre-loaded `indices`, `holdings_agg`, `fund_navs`, `a_snap`, and `h_snap` for performance.

### `get_index_drill_detail(...)`

`backend/services/drillable_funds.py`

Drills one index into its constituent stocks. Computes equivalent shares from 5/29 fund NAV and constituent weight, then values them at the latest close.

### `list_drillable_indices(...)`

`backend/services/drillable_funds.py`

Returns the list of indices that have at least one tracking fund in `FundIndexMap` for the given date.

---

## Dimension Column Mapping

The `dim` query parameter maps to a database column as follows:

| `dim` | Column | Source table(s) |
|-------|--------|-----------------|
| `swy1` | `swy_l1` | `a_share_financial_snapshot`, `hk_share_financial_snapshot`, `csi300_constituent_snapshot` |
| `swy2` | `swy_l2` | same |
| `swy3` | `swy_l3` | same |
| `swy4` | `swy_l4` | same |
| `csi1` | `csi_l1` | same |
| `csi2` | `csi_l2` | same |
| `csi3` | `csi_l3` | same |
| `csi4` | `csi_l4` | same |
| `se1` | `se_l1` | same |
| `se2` | `se_l2` | same |
| `se3` | `se_l3` | same |
| `se4` | `se_l4` | same |
| `l1` | `swy_l1` | alias |
| `l2` | `swy_l2` | alias |
| `chain` | `chain_position` | `csi300_constituent_snapshot` only |
| `growth_tier` | `growth_tier` | `csi300_constituent_snapshot` only |
| `competition` | `competition` | `csi300_constituent_snapshot` only |

---

## Example: cURL request

```bash
curl -s "http://localhost:8015/api/penetration/dimension-drilled?dim=swy1&as_of_date=2026-06-18&market=A+H" \
  -H "x-session-token: $PORTFOLIOM_SESSION"
```

---

## Related documentation

- [Drill-down math and valuation](./explanation-drilled-dimension-math.md) — how `pe_weighted`, equivalent shares, and CSI300 comparison are computed.
- [How to read the drilled-dimension panel](./howto-read-drilled-dimension-panel.md) — interpreting weight and PE differences.
- [How to add a drilled dimension](./howto-add-drilled-dimension.md) — wiring a new classification into the UI and API.
- [Tutorial: first drilled-dimension analysis](./tutorial-first-drilled-dimension-analysis.md) — step-by-step walkthrough.
