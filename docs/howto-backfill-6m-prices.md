# 如何为持仓和穿透证券补全 6 个月收盘价

本指南介绍如何为 PortfolioM 拉取过去 6 个月（约 180 天）的收盘价，覆盖持仓中的基金/ETF/个股，以及基金穿透后的底层 A 股、港股。结果写入 `price_cache` 表，供资产走势、估值时序、3 个月涨跌幅等功能使用。

---

## 你需要什么

- 本地已配置好 Python 环境（`backend/requirements.txt` 已安装）
- 数据库已初始化（运行过 `backend/main.py` 或在命令行 import 过 `database`）
- 已导入最新月度快照（如 `sourceData/202605数据/`）
- 网络可以访问腾讯财经 API、`web.ifzq.gtimg.cn`、akshare

---

## 步骤 1：确保交易日历已初始化

服务启动时会自动初始化 2020–2030 年 CN / HK / US / OF 的交易日历。如果跳过服务直接跑脚本，可以先手动初始化：

```bash
cd backend
python scripts/init_calendar.py
```

验证：

```sql
SELECT market, COUNT(*) FROM trading_calendar GROUP BY market;
```

预期每市场约 4000 行。

---

## 步骤 2：为持仓中的基金拉取历史净值

持仓中的 `.OF` 场外基金没有 K 线，只有每日净值。运行：

```bash
cd backend
python scripts/pull_fund_nav.py --days 180
```

脚本会：
1. 读取 `holdings` 表中所有 `.OF` 代码
2. 用 akshare 拉取每只基金最近 180 天的单位净值
3. 通过 `is_trading_day("OF", date, db)` 过滤非交易日
4. 写入 `price_cache`，`source='akshare_fund'`

预期输出示例：

```
INFO 007818.OF: 122 rows written
INFO 014856.OF: 121 rows written
...
DONE: funds=25 total_rows=3042
```

---

## 步骤 3：为持仓中的个股和 ETF 拉取历史 K 线

对于 `.SH` / `.SZ` / `.HK` / 美股代码，运行通用回补端点：

```bash
curl -X POST "http://localhost:8000/api/admin/backfill-prices?days=180" \
  -H "x-admin-token: $ADMIN_TOKEN"
```

或者在无服务环境下直接运行等价脚本：

```python
from backend.database import SessionLocal
from backend.main import admin_backfill_prices
db = SessionLocal()
admin_backfill_prices(days=180, db=db)
```

这个端点会：
1. 遍历 `holdings` 所有行
2. `.OF` 走 akshare 基金净值
3. 其他代码走 `fetch_price_history(ticker, 180)`（腾讯 K 线 → yfinance 备用）
4. 用 `is_trading_day(market, date, db)` 过滤非交易日
5. 写入 `price_cache`

---

## 步骤 4：为穿透后的底层证券拉取 6 个月价格

基金穿透后，底层可能包含大量 A 股和港股。运行：

```bash
cd backend
python scripts/pull_history_prices.py --days 180 --market AH --max-codes 2000
```

参数说明：

| 参数 | 含义 |
|------|------|
| `--days 180` | 拉取最近 180 天 |
| `--market AH` | 同时处理 A 股和港股；可改为 `A` 或 `H` |
| `--max-codes 2000` | 最多处理 2000 只股票 |

脚本会读取 `a_share_financial_snapshot` 和 `hk_share_financial_snapshot` 中当前业务日期（默认 `2026-05-29`）的全部股票代码，逐个调用腾讯 K 线接口。

---

## 步骤 5：补全仍缺 current_price 的快照行

月度快照导入时已经尝试从 `price_cache` 找 `current_price`，但如果当时价格缓存为空，部分行会留空。运行：

```bash
cd backend
python scripts/fill_prices_tencent.py
```

或调用 API：

```bash
curl -X POST "http://localhost:8000/api/admin/fill-prices-tencent?as_of_date=2026-05-29" \
  -H "x-admin-token: $ADMIN_TOKEN"
```

这会：
1. 找出 `current_price` 为空的 A 股 / 港股快照行
2. 调用腾讯实时行情补一次最新价
3. 写入 `price_cache`（`source='tencent_fill'`）
4. 重算 `pe_ttm_dynamic` / `pb_mrq_dynamic` / `ps_ttm_dynamic`

---

## 步骤 6：验证完整性

### 6.1 查看 price_cache 总行数

```sql
SELECT source, COUNT(*) FROM price_cache GROUP BY source;
```

### 6.2 检查某只股票的交易日覆盖

```sql
SELECT trade_date, close_px, source
FROM price_cache
WHERE stock_code = '600519.SH'
  AND trade_date >= '2025-12-18'
ORDER BY trade_date;
```

### 6.3 检查缺失的交易日

```sql
WITH expected AS (
  SELECT date FROM trading_calendar
  WHERE market = 'CN'
    AND is_trading = 1
    AND date >= '2025-12-18'
    AND date <= '<today>'  -- 替换为当天日期，例如 2026-06-28
)
SELECT e.date
FROM expected e
LEFT JOIN price_cache p
  ON p.stock_code = '600519.SH' AND p.trade_date = e.date
WHERE p.trade_date IS NULL;
```

### 6.4 通过 API 检查时序缺口

```bash
curl "http://localhost:8000/api/penetration/timeseries?scope=portfolio&metric=pe_weighted&window=180"
```

返回中的 `missing_dates` 字段会列出当前窗口内缺少数据的交易日。

---

## 步骤 7：重新计算聚合和时序

价格补全后，需要重新计算穿透金额、聚合缓存和时序：

```bash
curl -X POST "http://localhost:8000/api/admin/recalc-aggregation?as_of_date=2026-05-29" \
  -H "x-admin-token: $ADMIN_TOKEN"
```

这会：
1. 重算所有维度聚合（`aggregation_cache`）
2. 重算当日的 `aggregation_timeseries`

---

## 故障排查

### 问题：脚本返回 `fetched=0`

可能原因：
- 数据库里没有 `a_share_financial_snapshot` / `hk_share_financial_snapshot` 数据 → 先运行月度导入
- 网络无法访问腾讯 K 线 → 检查能否打开 `https://web.ifzq.gtimg.cn`
- 所有代码都被 `max-codes` 截断 → 调大参数

### 问题：某只股票价格明显少于 120 条

检查：

```bash
cd backend
python -c "
from crawlers.price_data import fetch_tencent_kline
print(len(fetch_tencent_kline('600519.SH', 180)))
"
```

如果返回很少，可能是该股票停牌、退市或代码映射错误。可以在 `api_code_map` 中添加映射，或用 yfinance 补充。

### 问题：OF 基金净值缺失

检查 akshare 是否可用：

```python
import akshare as ak
ak.fund_open_fund_info_em(symbol="007818", indicator="单位净值走势")
```

### 问题：/api/trend 仍然显示断层

`/api/trend` 使用 last-known backward-fill：如果某只股票在 90 天窗口内完全没有任何价格，它会被跳过（不编造）。这是预期行为。检查 `skipped_holdings` 字段，然后为这些代码单独跑 `fetch_price_history`。

---

## 下一步

- 验证 3 个月涨跌幅：`/api/penetration/full-holding?as_of_date=2026-05-29`
- 查看资产走势：`/api/trend?days=180`
- 查看估值时序：`/api/penetration/timeseries?window=180`

更多技术细节见 [`reference-price-system.md`](./reference-price-system.md)。
