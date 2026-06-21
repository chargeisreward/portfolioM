# How to add a drilled dimension

This guide shows how to add a new classification dimension to the drilled-dimension analysis. The example adds a hypothetical `sector` dimension; adapt the column names to your actual data.

---

## Prerequisites

- You can run the local backend and frontend.
- You know which database column holds the new classification (for example `a_share_financial_snapshot.my_sector`).
- The classification exists for both A-share and HK-share snapshots, or you have a plan for fallback behavior.

---

## Step 1: Add the dimension to the API mapping

Open `backend/main.py` and find the `DIM_COL_DRILLED` dictionary inside `get_dimension_drilled()`.

```python
DIM_COL_DRILLED = {
    "swy1": "swy_l1", "swy2": "swy_l2", ...
    "sector": "my_sector",   # <-- add this
}
```

The key is the public `dim` value; the value is the database column name used by both portfolio and CSI300 snapshots.

---

## Step 2: Ensure the column exists on all relevant tables

The `get_dimension_drilled()` handler reads the column from:

- `AShareFinancialSnapshot`
- `HKShareFinancialSnapshot`
- `Csi300ConstituentSnapshot`

Add the column to each model in `backend/models.py` if it is missing. For example:

```python
# AShareFinancialSnapshot and HKShareFinancialSnapshot
my_sector = Column(String(60))

# Csi300ConstituentSnapshot
my_sector = Column(String(60), default="其他")
```

Run the database migration or let `Base.metadata.create_all()` create the column on startup.

---

## Step 3: Populate the classification data

Update the data import or crawler that writes `a_share_financial_snapshot` and `hk_share_financial_snapshot` so the new column is filled. Empty or placeholder values are automatically normalized to `其他` by `_norm_bucket_key()`, but real values give better results.

If the classification only exists for A-shares, HK rows will fall into `其他`. Document this limitation in the UI label.

---

## Step 4: Add the tab to the frontend

Open `frontend/src/components/AnalysisPanel.jsx` and add an entry to the `DIMS` array:

```jsx
const DIMS = [
  { id: 'drill', label: '下钻', special: 'drill' },
  { id: 'full', label: '全持仓' },
  { id: 'swy1', label: '申万L1' },
  // ... existing tabs ...
  { id: 'sector', label: '自定义板块', dim: 'sector', market: 'A+H' },
]
```

- `id` is the React key and tab identifier.
- `dim` is the value passed to the API.
- `market` defaults to `A+H` if omitted.

The existing routing code already sends any `dim` in `DRILLED_DIMS` to `DrilledDimensionPanel`. Add your new `dim` to that list:

```jsx
const DRILLED_DIMS = ['swy1', 'swy2', 'swy3', 'csi1', 'csi2', 'csi3', 'csi4', 'se1', 'sector']
```

---

## Step 5: Add a display label (optional)

If you want a nicer label than the raw `dim` value, add it to `DIM_LABELS` in `DrilledDimensionPanel.jsx`:

```jsx
const DIM_LABELS = {
  // ... existing labels ...
  sector: '自定义板块',
}
```

---

## Step 6: Verify the endpoint

Start the backend and call the new dimension:

```bash
curl -s "http://localhost:8015/api/penetration/dimension-drilled?dim=sector&as_of_date=2026-06-18&market=A+H" \
  -H "x-session-token: $PORTFOLIOM_SESSION" | jq '.portfolio[:3]'
```

You should see buckets keyed by your new classification.

---

## Step 7: Verify the UI

Start the frontend, open the **分析** tab, and click the new tab. Confirm:

- The table loads without errors.
- Buckets match the values in the database.
- Expanding a row shows the underlying stocks.
- The total row at the bottom sums to 100%.

---

## Common pitfalls

| Problem | Cause | Fix |
|---------|-------|-----|
| New tab does not appear | `DIMS` entry missing or malformed | Check JSX syntax and that `id` is unique. |
| API returns `Unsupported dim` | `DIM_COL_DRILLED` missing the key | Add it in `backend/main.py`. |
| All rows are `其他` | New column is null or not populated | Update the import/crawler that writes snapshots. |
| HK stocks missing from buckets | Column missing from `HKShareFinancialSnapshot` | Add the column and re-import. |
| CSI300 weights missing | Column missing from `Csi300ConstituentSnapshot` | Add the column with default `其他`. |

---

## Related documentation

- [Drilled-dimension analysis reference](./reference-dimension-drilled.md) — full API and component reference.
- [Drill-down math and valuation](./explanation-drilled-dimension-math.md) — how the aggregation works.
- [How to read the drilled-dimension panel](./howto-read-drilled-dimension-panel.md) — interpreting the results.
