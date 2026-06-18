# Fund Penetration & Industry Aggregation Analysis

**Status:** implemented (backend complete; frontend integrated, pending final polish and end-to-end verification)
**Date:** 2026-06-17
**Updated:** 2026-06-18
**Author:** Claude
**Spec owner:** PortfolioM user

> See also:
> - [`../../project-status.md`](../project-status.md) — current implementation status and pending tasks
> - [`../../reference-price-system.md`](../reference-price-system.md) — price cache and trading calendar reference
> - [`../../howto-backfill-6m-prices.md`](../howto-backfill-6m-prices.md) — how to backfill 6-month closing prices

## Context

The PortfolioM app tracks a personal portfolio. Most holdings are funds/ETFs that
**track an underlying index**, so to analyze the actual exposure (industry mix,
PE/PB/PS, chain position, growth tier, competition), each fund holding must be
**penetrated** down to its constituent stocks. The result must then be
**aggregated** across all stock-level exposures and **compared to CSI 300** as
the benchmark.

A new monthly data source `sourceData/YYYYMM数据/` provides, for the last
business day of each month:
- A股 full universe valuation snapshot (PE/PB/PS/EPS forecasts/market cap)
- 港股 full universe valuation snapshot (same + 4-level Shenwan industry)
- Index constituent snapshots (12 indices, weight + member list)
- Fund→index mapping (22 funds, with the benchmark formula)

Until now, only one-shot point-in-time `penetration_results` existed. This
design introduces a **snapshot-per-as-of-date** model so historical
computations are reproducible, traceable, and update cleanly when the next
monthly snapshot is imported.

### Hard constraints (inherited from rules.md and user instructions)

1. **No mock data, no fabricated values** — every cell must trace back to a
   real source (Excel file, price_cache row, or a computed derivation of
   those).
2. **No forward-fill, no backward-fill across missing dates** — gaps in the
   time-series stay as gaps; charts draw broken segments
   (`connectNulls: false`).
3. **No hardcoded business data on the frontend** — KPI tiles, tables,
   charts all come from backend APIs.
4. **Price = previous trading-day close**, not live quote. The UI displays
   `current_price_date` for each market.
5. **Calculation window is monthly**: business_date = MAX(as_of_date WHERE
   as_of_date <= today). The most recent monthly snapshot is the active
   basis; when the next snapshot is imported, computations for the affected
   window are recalculated (see §3.6).

---

## §1 Data Model

Nine new tables, all keyed on `as_of_date` so each monthly import becomes a
self-contained snapshot that can be queried and diff'd against later
snapshots.

### 1.1 `fund_index_map`

Fund→index tracking relationship (from `基金-指数.xlsx`, 22 rows).

| column | type | notes |
|---|---|---|
| `fund_code` | String(20) PK | e.g. `007818.OF` |
| `fund_name` | String(80) | |
| `benchmark_formula` | String(500) | original formula, e.g. `中证全指半导体产品与设备指数*95%+活期存款利率*5%` |
| `index_code` | String(20) | e.g. `931160.CSI` |
| `index_name` | String(80) | |
| `as_of_date` | Date PK | import batch date |
| `source` | String(40) | `excel_202605` |
| `note` | String(200) | optional tag, e.g. `主动管理` |

### 1.2 `index_constituent_snapshot`

Index constituent snapshot.

| column | type | notes |
|---|---|---|
| `id` | Integer PK | autoincrement |
| `as_of_date` | Date indexed | |
| `index_code` | String(20) indexed | e.g. `000300` |
| `index_name` | String(80) | |
| `stock_code` | String(20) indexed | |
| `stock_name` | String(80) | |
| `exchange` | String(8) | SSE/SZSE/HKEx |
| `weight` | Float | weight % |
| `source` | String(40) | `csindex_official` / `szse` / `akshare` / `csi_official` |

UK: `(as_of_date, index_code, stock_code)`.

### 1.3 `a_share_financial_snapshot`

A-share valuation snapshot.

| column | type | notes |
|---|---|---|
| `id` | Integer PK | |
| `as_of_date` | Date indexed | |
| `stock_code` | String(20) indexed | |
| `stock_name` | String(80) | |
| `pe_ttm` | Float | snapshot PE |
| `pb_mrq` | Float | snapshot PB |
| `ps_ttm` | Float | snapshot PS |
| `dividend_yield` | Float | snapshot yield % |
| `market_cap` | Float | 亿元 |
| `eps_fy1` | Float | consensus EPS FY1 |
| `eps_fy2` | Float | consensus EPS FY2 |
| `current_price` | Float | prev close |
| `current_price_date` | Date | when prev close was sampled |
| `baseline_price` | Float | close on `as_of_date` |
| `pe_ttm_dynamic` | Float | `pe_ttm * (current_price / baseline_price)` |
| `pb_mrq_dynamic` | Float | same formula |
| `ps_ttm_dynamic` | Float | same formula |
| `source` | String(40) | |

UK: `(as_of_date, stock_code)`.

### 1.4 `hk_share_financial_snapshot`

HK-share valuation snapshot — same as A-share plus 4-level Shenwan industry
columns (L1/L2/L3/L4). HK codes are stored as 5-digit strings (e.g. `00700`).

### 1.5 `penetration_snapshot`

Per-holding penetration result.

| column | type | notes |
|---|---|---|
| `id` | Integer PK | |
| `as_of_date` | Date indexed | |
| `holding_code` | String(20) indexed | upper-layer fund/ETF code |
| `holding_name` | String(80) | |
| `holding_amount_cny` | Float | holding value at `as_of_date` |
| `index_code` | String(20) | index that was tracked |
| `index_name` | String(80) | |
| `stock_code` | String(20) indexed | penetrated stock |
| `stock_name` | String(80) | |
| `weight_at_baseline` | Float | weight % on `as_of_date` |
| `amount_cny_dynamic` | Float | weight-invariant recompute (see §3.2) |
| `amount_cny_static` | Float | `(weight/100) * holding_amount_cny` (no price move) |
| `baseline_price` | Float | |
| `current_price` | Float | |
| `calculation_method` | String(20) | always `weight_invariant` |

UK: `(as_of_date, holding_code, stock_code)`.

### 1.6 `full_holding_snapshot`

Merged flat holding table: drilled funds + direct stocks + undrilled funds +
cash. Single source of truth for any aggregation.

| column | type | notes |
|---|---|---|
| `id` | Integer PK | |
| `as_of_date` | Date indexed | |
| `stock_code` | String(20) indexed | |
| `stock_name` | String(80) | |
| `source_type` | String(20) | `drilled_fund` / `direct_stock` / `undrilled_fund` / `cash` |
| `source_holding_code` | String(20) | upper-layer fund code (null for direct) |
| `amount_cny` | Float | CNY exposure |
| `industry_l1` | String(40) | resolved industry, default `其他` |
| `industry_l2` | String(60) | resolved L2, default `其他` |
| `chain_position` | String(20) | from financials |
| `growth_tier` | String(20) | from financials |
| `competition` | String(20) | from financials |
| `pe_ttm_dynamic` | Float | null for non-stock rows |
| `pb_mrq_dynamic` | Float | |
| `ps_ttm_dynamic` | Float | |
| `eps_fy1` | Float | |

UK: `(as_of_date, stock_code, source_type, source_holding_code)`.

### 1.7 `aggregation_cache`

Per-dimension aggregation. **One row per `(as_of_date, scope, dimension,
key)`** — both portfolio and CSI 300 are populated.

| column | type | notes |
|---|---|---|
| `id` | Integer PK | |
| `as_of_date` | Date indexed | |
| `scope` | String(20) | `portfolio` / `csi300` |
| `dimension` | String(20) | `l1` / `l2` / `chain` / `growth_tier` / `competition` |
| `key` | String(80) | `电子` / `中游` / `high` / `_total` |
| `stock_count` | Integer | |
| `amount_cny` | Float | |
| `weight_pct` | Float | |
| `virtual_earnings` | Float | Σ(amount / pe) |
| `pe_weighted` | Float | virtual_earnings / amount |
| `pe_simple_avg` | Float | informational only |
| `pb_weighted` | Float | |
| `ps_weighted` | Float | |
| `updated_at` | DateTime | |

### 1.8 `csi300_constituent_snapshot`

Same shape as `index_constituent_snapshot` but for CSI 300 only — used for
benchmark comparison.

UK: `(as_of_date, stock_code)`.

### 1.9 `aggregation_timeseries`

Daily portfolio / CSI 300 valuation metric time-series. Powers the click-to-
expand trend chart in §4.

| column | type | notes |
|---|---|---|
| `id` | Integer PK | |
| `calc_date` | Date indexed | calendar day the metric is for |
| `business_date` | Date | the snapshot `as_of_date` used for this calc |
| `scope` | String(20) | `portfolio` / `csi300` |
| `stock_count` | Integer | |
| `total_amount_cny` | Float | |
| `virtual_earnings` | Float | |
| `pe_weighted` | Float | |
| `pb_weighted` | Float | |
| `ps_weighted` | Float | |
| `price_date` | Date | the prev close used |

UK: `(calc_date, scope)`.

### 1.10 Lightweight schema migrations

Following the existing pattern in `backend/database.py` (lines 47–68), new
columns on existing tables will be added via ALTER-on-startup. New tables
above are created via `Base.metadata.create_all` in `init_db()`.

---

## §2 Data Ingestion

### 2.1 `sourceData/` directory layout

```
sourceData/
├── data_version.csv                # meta-table: list of imported versions
├── 202604数据/
│   ├── 基金-指数.xlsx
│   ├── 指数构成.xlsx
│   ├── 全部A股-取数模板.xlsx
│   └── 全部港股.xlsx
├── 202605数据/                     # current active
│   ├── 基金-指数.xlsx
│   ├── 指数构成.xlsx
│   ├── 全部A股-取数模板.xlsx
│   └── 全部港股.xlsx
└── (202607数据/  — to be delivered in early July)
```

### 2.2 `data_version.csv`

```csv
as_of_date,source_folder,imported_at,note
2026-04-30,202604数据,2026-05-05T10:00:00,4月底
2026-05-29,202605数据,2026-06-13T10:00:00,5月底
```

Logic in `backend/services/data_version.py`:

```python
def current_business_date(today: date) -> date | None:
    """MAX(as_of_date) WHERE as_of_date <= today."""
    versions = list_available_versions()
    candidates = [v["as_of_date"] for v in versions if v["as_of_date"] <= today]
    return max(candidates) if candidates else None
```

### 2.3 One importer per Excel file

Each importer is idempotent — running twice for the same `as_of_date` skips
already-loaded rows and only backfills the dynamic price fields.

| script | source | target table |
|---|---|---|
| `scripts/import_fund_index_map.py` | `基金-指数.xlsx` | `fund_index_map` |
| `scripts/import_index_constituents.py` | `指数构成.xlsx` (12 sheets) | `index_constituent_snapshot` |
| `scripts/import_a_share_financials.py` | `全部A股-取数模板.xlsx` | `a_share_financial_snapshot` |
| `scripts/import_hk_share_financials.py` | `全部港股.xlsx` | `hk_share_financial_snapshot` |

Each returns an `ImportReport { as_of_date, rows_inserted, rows_skipped,
errors[] }`.

### 2.4 Baseline + dynamic pricing at import time

For every stock in the snapshot:

1. `baseline_price` = the close at `as_of_date` from `price_cache`
   (`SELECT close_px FROM price_cache WHERE stock_code=? AND trade_date
   <= as_of_date ORDER BY trade_date DESC LIMIT 1`).
2. `current_price` = the prev close before `today`
   (`MAX(trade_date) WHERE trade_date < today`); if none, fall back to the
   most recent available close, and record the date in `current_price_date`.
3. Compute `pe_ttm_dynamic = pe_ttm * (current_price / baseline_price)`
   (skip when baseline_price is null/0 — leave the dynamic column null).

### 2.5 Improving `sourceData/download_index_cons.py`

Current state (per project exploration):
- Only akshare is used → SZSE 创业板50 has no weight, and the script cannot
  be replayed historically.
- No HK index constituents.
- No recovery on per-index failure.

Planned changes:
- Add a `--as-of-date` argument so the same script can pull historical
  snapshots.
- Append three additional data sources after the akshare block:
  - CSI official: `https://www.csindex.com.cn/...` xlsx download
  - SZSE official: `http://www.szse.cn/api/disc/announce/...`
  - HSI official: `https://www.hsi.com.hk/...`
- Wrap each index in its own try/except; collect per-index failures into
  the report rather than aborting the whole run.

### 2.6 New official-source crawler: `scripts/crawl_index_official.py`

Per index provider, one parser that downloads to
`sourceData/{YYYYMM数据}/{index_code}.xlsx` and is then consumed by the
existing `import_index_constituents.py`:

| provider | URL pattern | format |
|---|---|---|
| 中证指数 (CSI) | `/indices/family/detail?indexCode={code}` xlsx download | xlsx |
| 国证指数 (CNINDEX) | `/module/index-detail.html?...&indexCode={code}` | xlsx/csv |
| 深交所 (SZSE) | `api/disc/announce/announce?...` | json |
| 恒生指数 (HSI) | `/eng/indexes/...` | xlsx |

---

## §3 Penetration & Aggregation Engine

### 3.1 Drill-down rule

Driven by `asset_type` from `security_master`:

| asset_type | drill? | reason |
|---|---|---|
| `a_share_equity` | yes | tracks A-share index |
| `a_share_etf` | yes | tracks A-share index |
| `hk_equity` | yes | tracks HK index |
| `qdii_equity` | yes | tracks foreign index |
| `qdii_bond` | no | keep as bond bucket |
| `bond` | no | bond funds don't track equity indices |
| `gold` | no | gold funds keep as gold bucket |
| `cash` | no | cash/money market |
| `us_stock` | no | direct holding |
| `us_etf` | yes if tracking_index_code known | case-by-case |

A fund whose `tracking_index_code` is empty is treated as actively managed
and **not drilled**. A股 funds with a `tracking_index_code` but missing
constituents on that `as_of_date` are surfaced in the import report and
remain un-drilled (with a note in the holding table).

### 3.2 Weight-invariant recompute (recommended method)

User requirement: hold the 5/29 weight distribution; let price moves change
the per-stock amounts.

Inputs:
- Holding amount at `as_of_date`: `A`
- Index constituents: `constituents[s] = weight[s]` (sum = 100%)
- `baseline_price[s]` = close on `as_of_date`
- `current_price[s]` = prev close

Per stock `s`:

```
amount_dynamic[s] = (weight[s] / 100) * A * (current_price[s] / baseline_price[s])
amount_static[s]  = (weight[s] / 100) * A
```

Both are written to `penetration_snapshot`. The difference
`amount_dynamic - amount_static` is the price-move contribution of the
holding.

**Why this method (per user feedback)**: simpler to read, easier to debug,
the "shares held per unit fund" is a single number not split between "shares
× 5/29 price" and "shares × today price", and the recompute for partial
redeems/emissions reduces to the same formula.

### 3.3 Merge into `full_holding_snapshot`

Per holding row in the portfolio:

- If `asset_type ∈ drillable` AND `tracking_index_code` known AND
  constituents exist for the active `as_of_date`:
  for each constituent `s`, insert one row with
  - `source_type = 'drilled_fund'`
  - `amount_cny = amount_dynamic[s]`
  - `industry_*` resolved from the matching financial snapshot
- Else:
  - `source_type = 'undrilled_fund'` (for funds) / `'direct_stock'` (for
    direct equity) / `'cash'` (for cash)
  - `amount_cny = holding.amount_cny`
  - industry columns = `其他` (or whatever the row resolves to)
  - dynamic PE/PB/PS = null

### 3.4 Aggregation rule (PE/PB/PS)

Per dimension row `(as_of_date, scope, dimension, key)`:

```
stock_count  = COUNT(DISTINCT stock_code)
amount       = SUM(amount_cny)
weight_pct   = amount / SUM(amount) over the same scope
virtual_earnings = SUM(amount_cny / pe_ttm_dynamic)   -- NULLIF on zero
pe_weighted  = virtual_earnings / SUM(amount_cny)
pe_simple_avg= AVG(pe_ttm_dynamic)                    -- informational only
```

**Forbidden**: `AVG(pe_ttm_dynamic)` directly weighted by `weight_pct`
(this is the user rule §3.4 — never weighted-average PE).

### 3.5 CSI 300 comparison

For `scope='csi300'`, run the same SQL against `csi300_constituent_snapshot`
joined to `a_share_financial_snapshot` / `hk_share_financial_snapshot`
(filtered to A-share + HK-listed constituents). CSI 300 weight is the index
weight from the snapshot, used as `amount_cny` proxy for percentage math.

The frontend displays `ΔPE = portfolio.pe_weighted - csi300.pe_weighted` per
row.

### 3.6 Daily aggregation timeseries + recalc policy

After every successful import (manual or scheduled), `recalc_after_import`
runs:

```python
def recalc_after_import(new_version: date, db):
    """Recompute aggregation_timeseries for [new_version, today-1]."""
    trading_days = is_any_market_open_today_path()  # from trading_calendar
    for d in trading_days_in_range(new_version, today() - 1, db):
        upsert_agg_timeseries(d, business_date=new_version, db=db)
```

- Old segments (calc_date < new_version) are **not deleted** — they
  retain their original `business_date`.
- For calc_dates ≥ new_version, rows are upserted (overwrite) with
  `business_date = new_version`.
- The new version becomes active for all queries where
  `calc_date = today` after import.

#### Stability window

- A row is **stable** when `today - business_date < 30 days` (within ~1
  month).
- Beyond that, it is technically a temporary extrapolation using stale
  EPS/BV — but the user accepts this and expects it to be overwritten when
  the next monthly snapshot arrives.
- The application never fills missing dates; gaps in the timeline stay as
  gaps.

---

## §4 Frontend UI

### 4.1 API endpoints (added under existing auth)

| method | path | purpose |
|---|---|---|
| GET | `/api/data-version` | active `as_of_date`, prev-close dates per market, version history |
| GET | `/api/penetration/full-holding` | full merged holding rows |
| GET | `/api/penetration/dimension?dim=...&as_of_date=...` | unified dim aggregation, portfolio vs csi300 |
| GET | `/api/penetration/dimension-detail?dim=...&key=...&as_of_date=...` | rows under a dim bucket |
| GET | `/api/penetration/timeseries?scope=...&metric=...&window=...` | trend points |
| GET | `/api/penetration/kpi` | KPI bar values |
| POST | `/api/admin/import-source-data` | trigger importer for a `source_folder` |
| POST | `/api/admin/recalc-aggregation` | run §3.6 recalc |

### 4.2 Unified dimension response shape

```json
{
  "as_of_date": "2026-05-29",
  "current_price_date": "2026-06-13",
  "dimension": "l1",
  "scope_label": {"portfolio": "组合", "csi300": "CSI300"},
  "portfolio": [
    {
      "key": "电子",
      "stock_count": 12,
      "amount_cny": 158234.50,
      "weight_pct": 23.5,
      "virtual_earnings": 5234.50,
      "pe_weighted": 30.23,
      "pb_weighted": 4.12,
      "ps_weighted": 3.45
    }
  ],
  "csi300": [
    {"key": "电子", "stock_count": 18, "weight_pct": 8.5, "pe_weighted": 28.1, ...}
  ],
  "no_data_dimensions": []
}
```

### 4.3 Dimension-detail response shape

```json
{
  "as_of_date": "2026-05-29",
  "dimension": "l1",
  "key": "电子",
  "stocks": [
    {
      "stock_code": "002475.SZ",
      "stock_name": "立讯精密",
      "amount_cny": 45230.10,
      "weight_pct": 6.72,
      "pe_ttm_dynamic": 25.3,
      "pb_mrq_dynamic": 4.1,
      "ps_ttm_dynamic": 2.8,
      "industry_l2": "消费电子",
      "chain_position": "midstream",
      "source_funds": ["008888.OF", "007818.OF"],
      "is_direct": false
    }
  ]
}
```

### 4.4 KPI endpoint response

```json
{
  "as_of_date": "2026-05-29",
  "current_price_date": "2026-06-13",
  "values": {
    "total_amount_cny": 673200.50,
    "drilled_stock_count": 187,
    "portfolio_pe_weighted": 22.45,
    "high_growth_weight_pct": 18.7,
    "forecast_pe_1y_weighted": 19.83,
    "midstream_weight_pct": 42.3
  }
}
```

### 4.5 `AnalysisPanel.jsx` rewrite

Tabs (only dimensions with data are rendered):

```
[行业(L1)]  [行业(L2)]  [产业链]  [增长分层]  [竞争格局]  [估值时序]
```

All tabs share `DimensionTable`, parametrized by `dim`. Columns:

| 维度 | 组合只数 | 组合金额 | 组合权重% | 组合PE | 组合PB | 组合PS | CSI300 PE | CSI300 PB | CSI300 PS | ΔPE |

- Click a row → expand a child row showing `DimensionDetailTable` with the
  underlying stock list (source funds, dynamic metrics, is_direct flag).
- Click again → collapse.
- Clicking a different row automatically collapses the previously expanded
  one.

Tabs to drop or label as 暂无数据:
- `risk`, `correlation`, `outlook` — no data source, removed.
- `competition` — render only if `a_share_financial_snapshot.competition` is
  non-null for ≥1 row; otherwise 暂无数据 tab.

### 4.6 Click-to-expand trend chart

Every KPI card on OverviewPanel and every metric column header in the
dimension tables can be clicked to toggle an embedded trend chart.

UI:
- Default: trend chart collapsed.
- First click on a metric → expand trend chart (default window = 90 days,
  dropdown for 180 / 360).
- Second click on the same metric → collapse.
- Clicking a different metric → collapse previous, expand new.

Endpoint: `GET /api/penetration/timeseries?scope={portfolio|csi300|both}&metric={pe_weighted|pb_weighted|ps_weighted|virtual_earnings|total_amount}&window={90|180|360}`

Response:

```json
{
  "as_of_date": "2026-05-29",
  "metric": "pe_weighted",
  "window_days": 90,
  "scope": "both",
  "data": [
    {"calc_date": "2026-05-29", "scope": "portfolio", "value": 28.5, "business_date": "2026-05-29"},
    {"calc_date": "2026-05-29", "scope": "csi300", "value": 12.3, "business_date": "2026-05-29"}
  ],
  "missing_dates": ["2026-05-30", "2026-06-01"]
}
```

Frontend:
- `connectNulls: false` on every series — gaps stay broken.
- Tooltip shows `business_date` so the user can see which snapshot underpins
  the segment.
- `missing_dates` are listed in the chart subtitle for transparency.

### 4.7 Top status bar

Persistent across all pages, driven by `GET /api/data-version`:

```
业务日期: 2026-05-29 | A股: 2026-06-13 | 港股: 2026-06-13 | 美股: 2026-06-12
```

Each market label shows the prev-close date for stocks in that market.

### 4.8 `OverviewPanel.jsx` KPI bar real data

Replace the hardcoded KPI numbers with `getKpi()` from `frontend/src/api.js`.
Keep the same 6-card layout; only swap data source.

---

## §5 Operational concerns

### 5.1 Import trigger

Two paths:
- **Manual**: `POST /api/admin/import-source-data` body
  `{"source_folder": "202605数据"}` runs all four importers then triggers
  `recalc_after_import`.
- **Scheduled**: optional cron job in `services/scheduler.py` that watches
  `sourceData/` for new `YYYYMM数据/` directories and triggers the same
  pipeline. Default off; user can enable from Settings.

### 5.2 Idempotency

Each importer checks for existing rows on `(as_of_date, ...)` and skips
them. Dynamic price columns are updated each run. Re-running an import on
an unchanged source is a no-op.

### 5.3 Tracing

Every `penetration_snapshot` and `full_holding_snapshot` row carries its
source holding and (where relevant) its source index code, plus
`as_of_date`. The UI provides a `tracing` deep-link for any aggregate row
that lists the contributing holdings and snapshot date.

### 5.4 Data version safety

`data_version.csv` is read-only at runtime; only the admin import endpoint
can append to it. Concurrent updates are serialized via the existing
SQLAlchemy session lock.

---

## §6 Files to create or modify

### New files

- `backend/services/data_version.py`
- `backend/services/penetration_v2.py` (new pipeline, alongside the existing
  `services/penetration.py` which is kept untouched for backward compat)
- `backend/services/aggregation.py`
- `backend/services/csi300_baselines_v2.py` (extend the existing
  `csi300.py`)
- `backend/scripts/import_fund_index_map.py`
- `backend/scripts/import_index_constituents.py`
- `backend/scripts/import_a_share_financials.py`
- `backend/scripts/import_hk_share_financials.py`
- `backend/scripts/crawl_index_official.py`
- `frontend/src/components/IndustryBreakdownPanel.jsx`
- `frontend/src/components/IndustryDrilldownTable.jsx`
- `frontend/src/components/MetricTimeseriesChart.jsx`
- `frontend/src/components/DataVersionBar.jsx`

### Modified files

- `backend/models.py` — add 9 new tables (§1.1–§1.9).
- `backend/database.py` — ALTER TABLE block for new columns on existing
  tables (none required since all new structures are new tables).
- `backend/main.py` — add 8 new endpoints (§4.1), register importer trigger
  + recalc, call `recalc_after_import` on startup if a fresh
  `as_of_date` is detected.
- `backend/services/scheduler.py` — register `recalc_after_import` as a
  cron job, register `import-source-data` watcher (optional).
- `backend/requirements.txt` — only adds nothing new (pandas + openpyxl
  already present from existing `index_constituents.py`).
- `backend/crawlers/index_constituents.py` — extend to support `--as-of-
  date` and per-source fallback (§2.5).
- `sourceData/download_index_cons.py` — same extensions.
- `frontend/src/api.js` — add `getDataVersion`, `getDimension`,
  `getDimensionDetail`, `getTimeseries`, `getKpi`, `importSourceData`,
  `recalcAggregation`.
- `frontend/src/components/AnalysisPanel.jsx` — full rewrite to
  `DimensionTable` (§4.5–§4.6).
- `frontend/src/components/OverviewPanel.jsx` — KPI bar real data + trend
  chart button for valuation metrics (§4.8).
- `frontend/src/App.jsx` — mount `<DataVersionBar />` at the top.
- `.gitignore` — add `sourceData/20[0-9][0-9][0-9][0-9]数据/`.

### Untouched

- `frontend/src/components/TradingPanel.jsx` — no change.
- All existing API endpoints unrelated to penetration.

---

## §7 Verification

1. **Schema**: after `init_db()`, the 9 new tables exist with the listed
   columns.
2. **First import (2026-05-29)**:
   - Run `scripts/import_*.py` against `sourceData/202605数据/`.
   - Row counts: `a_share_financial_snapshot ≈ 5533`,
     `hk_share_financial_snapshot ≈ 2779`, `index_constituent_snapshot`
     ≈ 5500 across 12 indices, `fund_index_map = 22`.
   - Spot-check `a_share_financial_snapshot.current_price` for a known
     stock (e.g. `600519.SH`) against `price_cache`.
3. **Drill-down**:
   - Hold a known A-share index fund (e.g. `007818.OF`).
   - Verify `penetration_snapshot` rows for it: count ≈ constituent count
     of `931160`; sum of `amount_cny_dynamic` ≈ `holding_amount_cny` (within
     rounding).
   - Compare `amount_cny_dynamic` and `amount_cny_static`; their ratio per
     stock equals `current_price / baseline_price`.
4. **Aggregation**:
   - `dimension=l1` returns 1 portfolio row per industry present in the
     holdings, plus matching CSI 300 rows.
   - `pe_weighted` is **not** equal to the simple average
     (`pe_simple_avg ≠ pe_weighted`) — confirms the virtual-earnings rule.
5. **Time-series**:
   - `timeseries?scope=portfolio&metric=pe_weighted&window=90` returns one
     point per trading day from `current_business_date` to today.
   - Pre-`as_of_date` dates are absent.
   - `connectNulls: false` rendering shows gaps where days are missing.
6. **Recalc after new import (simulated)**:
   - Add a stub `202607数据/` with one new row; import via admin endpoint.
   - Confirm rows with `calc_date >= 2026-06-30` get `business_date =
     2026-06-30` and `pe_weighted` recomputed.
   - Confirm rows with `calc_date < 2026-06-30` are unchanged.
7. **No-mock guarantees**:
   - `frontend/src/components/AnalysisPanel.jsx` has no hardcoded industry
     names, KPI values, or stock codes.
   - `frontend/src/components/OverviewPanel.jsx` KPI tiles come from
     `getKpi()`.
   - Empty-tab dimensions (e.g. `competition` when column is null) are
     rendered as `暂无数据`, not placeholder numbers.
8. **End-to-end UI**:
   - Log in → top status bar shows 业务日期 + 3 market price dates.
   - Open 分析 → click each tab → table loads with real numbers.
   - Click an industry row → expand stock detail.
   - Click the PE column header → embedded 90-day trend chart expands;
     second click collapses.

---

## §8 Implementation Status

As of 2026-06-18, all backend components described in this spec are implemented and the new frontend components are in place. The remaining work is commit cleanup, end-to-end verification, and hardening of the official-source index crawler.

### Completed

- All 9 new tables created in `backend/models.py`
- `backend/services/data_version.py` — version resolution
- Snapshot importers in `backend/scripts/import_*.py`
- `backend/services/penetration_v2.py` — weight-invariant drill-down
- `backend/services/aggregation.py` — virtual-earnings aggregation + CSI300 comparison
- `backend/services/price_filler.py` — missing `current_price` backfill
- All §4.1 API endpoints implemented in `backend/main.py`
- Frontend components: `DataVersionBar`, `IndustryBreakdownPanel`, `IndustryDrilldownTable`, `MetricTimeseriesChart`, `FullHoldingTable`, `DrillableFundsPage`, `PortfolioVsCsi300Card`
- Trading calendar `CN/HK/US/OF` in `backend/services/trading_calendar.py`
- 6-month price backfill scripts: `pull_history_prices.py`, `pull_fund_nav.py`
- Auto-import on startup in `backend/main.py::startup`

### Pending

- Commit the uncommitted implementation files
- Run end-to-end verification against `sourceData/202605数据/`
- Confirm 6-month price completeness for all drilled securities
- Final frontend polish: remove any residual mock values, verify tab/chart interactions
- Harden `backend/scripts/crawl_index_official.py` for CSI / CNINDEX / SZSE / HSI
- Prepare `sourceData/202606数据/` for the next monthly snapshot

See [`../../project-status.md`](../project-status.md) for the full task list.

---

## §9 Open questions for the user before implementation

None at this point. All blocking ambiguities resolved during brainstorming.