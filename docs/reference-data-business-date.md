# Reference: Data Business Date (数据业务日期)

This document explains the concept of **data business date** in PortfolioM, how it differs from other date concepts, and where it is used. It also clarifies a historical misunderstanding around "June 18" that appeared in earlier code and docs.

---

## 1. Definition

The **data business date** (数据业务日期) is the single canonical "as-of" date for portfolio holdings, financial snapshots, and index constituent weights. It is returned by:

```python
from services.data_version import current_business_date
baseline_date = current_business_date(today)
```

**Current value**: `2026-05-29` (sourced from `data_version.csv`).

The data business date does **not** change day-to-day. It only changes when new source data (Excel holdings + monthly index constituents + financial snapshots) is imported via the admin "数据导入" workflow. Until then, every query that needs "baseline" data uses this date.

---

## 2. Data Source

`data_version.csv` (managed by the data import workflow) contains:

```csv
as_of_date,price_dates_cn,price_dates_hk,price_dates_us,imported_at
2026-05-29,2026-05-29,2026-05-29,2026-05-29,2026-05-29T22:14:33+08:00
```

- `as_of_date` → `current_business_date` (the data business date)
- `price_dates_*` → latest available closing-price date per market (also exposed via `/api/data-version`)

---

## 3. Date Concepts Comparison

| Concept | Source | Mutable? | Used For |
|---|---|---|---|
| `today` | `date.today()` | Yes (daily) | Cutoff for "latest" prices / NAV |
| `current_business_date` | `data_version.csv.as_of_date` | No (only on import) | Baseline NAV, index weights, financial snapshots |
| `latest_nav_date` | `MAX(FundDailyNav.trade_date) WHERE trade_date <= today` | Yes (daily) | Latest fund NAV for current-value calculations |
| `FullHoldingSnapshot.as_of_date` | Snapshot table | Per snapshot | Currently identical to `current_business_date`, but do not rely on this coupling |
| `latest_price_date` | `data_version.csv.price_dates_*` | Yes (daily) | Latest available closing-price date per market |

**Key distinction**: `current_business_date` is the **baseline** (基期) for "as-of" data. `latest_nav_date` and `latest_price_date` are **dynamic** and move forward every trading day.

---

## 4. Usage in Code

### 4.1 Baseline NAV (FundDailyNav)

```python
# 基期 NAV — 用于下钻卡片的"基期"列
baseline_nav = db.query(FundDailyNav).filter(
    FundDailyNav.fund_code == fund_code,
    FundDailyNav.trade_date == current_business_date(today),
).first()
```

### 4.2 Index Constituent Weights

```python
# 指数成分股权重 — 月度更新，as_of_date = 业务日期
constituents = db.query(IndexConstituentSnapshot).filter(
    IndexConstituentSnapshot.index_code == index_code,
    IndexConstituentSnapshot.as_of_date == current_business_date(today),
).all()
```

### 4.3 Financial Snapshots

```python
# A股 / 港股财务快照 — as_of_date = 业务日期
a_snap = db.query(AShareFinancialSnapshot).filter(
    AShareFinancialSnapshot.as_of_date == current_business_date(today),
).all()
```

### 4.4 Helper Function (recommended)

`backend/main.py` provides a helper that returns both dates:

```python
from main import _get_baseline_and_latest_nav_dates
baseline_date, latest_nav_date = _get_baseline_and_latest_nav_dates(db)
# baseline_date = current_business_date(today)  — 例如 2026-05-29
# latest_nav_date = MAX(FundDailyNav.trade_date <= today)  — 动态
```

---

## 5. Common Misconception: "June 18"

### The misunderstanding

Earlier code and docs hardcoded `date(2026, 6, 18)` as if it were a special date. **It is not.** June 18 was simply the latest `FundDailyNav.trade_date` available at the time the code was written. Treating it as a fixed constant caused:

1. **Stale data after a few days**: When the latest NAV moved to June 19, 20, …, the code still queried June 18.
2. **Confusion in docs**: Readers assumed June 18 had some calendar significance (e.g., end of an index rebalance period).
3. **Inconsistent results**: Different endpoints hardcoded different dates.

### The fix

All hardcoded `date(2026, 6, 18)` references have been replaced with the dynamic `_get_baseline_and_latest_nav_dates(db)` helper. The helper:

- Returns `baseline_date = current_business_date(today)` (currently `2026-05-29`)
- Returns `latest_nav_date = MAX(FundDailyNav.trade_date WHERE trade_date <= today)` (dynamic)

### How to verify

```bash
# Should return only historical spec files under docs/superpowers/specs/
grep -rn "2026-06-18" docs/
```

---

## 6. API Reference

### `GET /api/data-version`

Returns the current data business date and per-market price dates.

**Response**:
```json
{
  "current_business_date": "2026-05-29",
  "price_dates": {
    "CN": "2026-05-29",
    "HK": "2026-05-29",
    "US": "2026-05-29"
  },
  "imported_at": "2026-05-29T22:14:33+08:00"
}
```

**Frontend usage** (`DrillableFundsPage.jsx`):
```javascript
const dataVer = await getDataVersion()
// dataVer.current_business_date — 业务日期，用于显示
// dataVer.price_dates.CN / .HK / .US — 各市场最新价格日期
```

---

## 7. Related Endpoints

| Endpoint | Uses Business Date? | Uses Latest Date? |
|---|---|---|
| `GET /api/penetration/full-holding` | Yes (`as_of_date` param) | No |
| `GET /api/penetration/dimension-drilled` | Yes (`as_of_date` param) | No |
| `GET /api/penetration/index-drill` | Yes (snapshot date) | Yes (latest price for current value) |
| `GET /api/admin/index-drill-base` | Yes (baseline) | Yes (latest NAV) |
| `GET /api/admin/index-drill-base-detail` | Yes (baseline) | Yes (latest NAV) |
| `GET /api/valuation/snapshot` | Yes (`as_of_date` param) | No |

---

## 8. Anti-Patterns

### ❌ Don't: Hardcode any date

```python
# BAD — will become stale
baseline_nav = db.query(FundDailyNav).filter(
    FundDailyNav.trade_date == date(2026, 5, 29),
).first()
```

### ❌ Don't: Use FullHoldingSnapshot.as_of_date as the business date

```python
# BAD — couples business date to a snapshot table that may diverge
snap_date = db.query(MAX(FullHoldingSnapshot.as_of_date)).scalar()
```

### ✅ Do: Use the helper or `current_business_date`

```python
# GOOD — uses data_version.csv as the single source of truth
from services.data_version import current_business_date
baseline_date = current_business_date(date.today())

# Or use the helper for both dates at once:
from main import _get_baseline_and_latest_nav_dates
baseline_date, latest_nav_date = _get_baseline_and_latest_nav_dates(db)
```

---

## 9. See Also

- `services/data_version.py` — `current_business_date()` implementation
- `backend/main.py` L78 — `_get_baseline_and_latest_nav_dates()` helper
- `services/index_drill_base_service.py` — uses both baseline + latest dates for dual-day drill cards
- [Drilled-Dimension Analysis Reference](./reference-dimension-drilled.md)
- [Price System Reference](./reference-price-system.md)
