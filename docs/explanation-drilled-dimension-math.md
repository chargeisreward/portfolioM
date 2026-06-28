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

**Key principle: `shares_equivalent` is computed once at baseline and stays fixed for the entire cycle until the next baseline import.**

### Baseline date computation (one-time)

```text
~~fund_value_at_baseline = Holding.quantity × FundDailyNav.nav_baseline~~
~~stock_amount_baseline  = fund_value_at_baseline × constituent_weight_pct / 100~~
~~shares_equivalent      = stock_amount_baseline / constituent_baseline_price~~

# 2026-06-28 修订：与 drill_snapshot.py L437 实施对齐
# fund_price    = mean(Holding.price)  # 所有 user 持有该基金的价格均值
# STOCK_RATIO   = 0.95                 # 基金 95% 配股票 + 5% 配现金
# price_cny     = PriceCache.close_px × ExchangeRate.rate
# shares_eq     = fund_price × STOCK_RATIO × (weight_pct / 100) / price_cny
# 等价含义: shares_eq × current_price_cny = fund_price × 0.95 × weight / 100
```

- ~~`nav_baseline` is the fund's unit net asset value on the baseline date (`current_business_date`, currently 2026-05-29), sourced from `FundDailyNav`.~~
- **2026-06-28 修订**：`fund_price` is the mean of `Holding.price` across all users holding this fund（**非** `FundDailyNav.nav_baseline`；代码 `drill_snapshot.py L22-24` 标注 TODO 未来应改用 `FundDailyNav.nav_baseline`）。
- `constituent_weight_pct` comes from `IndexConstituentSnapshot` for the same baseline date.
- ~~`constituent_baseline_price` is the stock's closing price on the baseline date from `a_share_financial_snapshot` or `hk_share_financial_snapshot`.~~
- **2026-06-28 修订**：`price_cny` is the stock's closing price on the baseline date from `PriceCache.close_px × ExchangeRate.rate`（**非** `a_share_financial_snapshot`；估值字段 pe/pb/ps/dividend_yield 才从 financial snapshot 取）。
- `STOCK_RATIO = 0.95` 表示基金 95% 配股票篮子 + 5% 配现金（CASH 行单独处理，见下文）。

If a fund tracks the same index through multiple fund codes, or if the same stock appears in several tracked indices, the equivalent shares and amounts are summed.

### Non-baseline days (inherit, do not recompute)

For any `as_of_date > baseline_date`:

```text
shares_equivalent(as_of_date) = shares_equivalent(baseline_date)   # 直接继承
```

The generator (`drill_snapshot.py`) loads the baseline row for the same `fund_code + stock_code` and copies its `shares_equivalent` verbatim. **Current day's `fund_price` and `current_price` are NOT used to recompute shares.** They are still recorded on the row for valuation purposes (current market value, dynamic PE, etc.).

If a stock is new (not present in baseline data, e.g., newly listed or index constituent adjustment), the generator falls back to computing `shares_equivalent` on that day to avoid NULL values. This is an edge case; the normal path is inheritance.

### Why baseline-fixed (not daily-rebalanced)

If shares were recomputed daily using current prices, the basket would automatically "rebalance" back to the index's official weights every day — stocks that went up would have fewer shares, stocks that went down would have more. That is **wrong** for a buy-and-hold fund: the investor's actual share count does not change just because prices move. The baseline-fixed rule preserves the economic reality of the baseline position until the next rebalance event (new baseline data import).

### When baseline data is re-imported

When the admin imports a new batch of baseline data (new `data_version.csv` with a later `as_of_date`), `current_business_date` advances. The scheduler's next run will detect `as_of_date == new_baseline_date` and recompute `shares_equivalent` from scratch using the new baseline prices/NAV. Old snapshots remain as-is (historical record); new snapshots from the new baseline onward inherit the new baseline shares.

### Effective weight (display-only, not stored)

Because `shares_equivalent` is fixed but prices move, the **actual portfolio weight** of each constituent drifts over time. The display layer computes this on the fly:

```text
effective_weight_pct(stock) = (shares_eq × current_price_cny) / Σ(non-CASH mv) × 95
```

- For the baseline date, `effective_weight_pct` equals `weight_pct` (official weight).
- For non-baseline dates, `effective_weight_pct` reflects the drifted weight.
- The stored `weight_pct` field always holds the official index weight (input parameter); it is NOT the actual portfolio weight on non-baseline days.

The `index_drill_base_service.py` detail endpoint returns both fields; the frontend shows `weight_pct` for the baseline column (labeled "权重%·官方") and `effective_weight_pct` for the latest-day column (labeled "权重%·实际").

### CASH row (5% cash sleeve)

The fund is modeled as 95% stock basket + 5% cash. The CASH row follows the same baseline-fixed rule:

```text
shares_equivalent(CASH) = fund_price_baseline × 0.05   # 固定于基期
effective_weight_pct(CASH) = 5.0                        # 恒为 5%
```

---

## Valuing the position today

Once we have equivalent shares, the current market value is:

```text
~~est_market_value_cny = shares_equivalent × current_price~~

# 2026-06-28 修订：与 index_drill_base_service.py L209-210 实施对齐
# 模拟基金固定份额 DRILL_SHARES = 100000（每十万份）
# user_shares          = DRILL_SHARES × shares_equivalent
# est_market_value_cny = user_shares × current_price_cny
#                     = 100000 × shares_equivalent × current_price_cny
```

- ~~`current_price` is the latest close from the financial snapshot.~~
- **2026-06-28 修订**：`current_price_cny` 来自 `PriceCache.close_px × ExchangeRate.rate`（**非** financial snapshot）。For USD and HKD stocks, the amount is converted to CNY using the most recent `ExchangeRate` row before aggregation.
- ~~If the baseline price or current price is missing, the code falls back to using `stock_amount_529` directly. This avoids dropping a position entirely, but it means the current value is stale.~~
- **2026-06-28 修订**：代码中**无** `stock_amount_529` fallback 逻辑。若 `current_price` 或 `current_price_cny` 缺失，该股票被跳过（不参与估值计算，可能低估基金总值）。

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

~~The math above is implemented in `backend/services/drillable_funds.py`:~~

**2026-06-28 修订**：`drillable_funds.py` 已 DEPRECATED（见文件头注释 L1-10）。当前实施分布在以下模块：

- `backend/services/drill_snapshot.py` — 公共下钻截面快照生成器（`generate_drill_snapshot_for_date`），scheduler 每日 09:00 调用，写入 `fund_drill_snapshot` 表
- `backend/services/index_drill_base_service.py` — 模拟基金卡片视图 + 双日并排明细（`get_drill_base_list` / `get_drill_base_detail`）
- `backend/services/drill_public_service.py` — 公共层只读 `fund_drill_snapshot` + `fund_index_map`
- `backend/services/drill_user_service.py` — 用户层只读 `Holding`
- `backend/services/drill_orchestration_service.py` — join 层，调 public + user，返回完整结果

~~- `list_drillable_indices()` — identifies which indices have tracking funds.~~
~~- `get_index_drill_detail()` — computes equivalent shares for one index.~~
~~- `get_all_drilled_stocks()` — aggregates across all indices.~~

**2026-06-28 修订**：上述函数已迁移至 `drill_orchestration_service.py`（新代码请勿 import `drillable_funds`）。

The dimension aggregation and CSI300 comparison live in `backend/main.py` in the `get_dimension_drilled()` route handler.

---

## Related documentation

- [Drilled-dimension analysis reference](./reference-dimension-drilled.md) — API parameters, response schema, and component props.
- [How to read the drilled-dimension panel](./howto-read-drilled-dimension-panel.md) — interpreting the numbers in the UI.
- [Tutorial: first drilled-dimension analysis](./tutorial-first-drilled-dimension-analysis.md) — a hands-on walkthrough.
