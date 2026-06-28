# PortfolioM 价格与交易日历系统参考

PortfolioM 的价格系统负责把持仓证券和穿透后的底层证券在过去一段时间内的收盘价落到本地数据库，为资产走势、估值时序、3 个月涨跌幅等前端功能提供数据。交易日历系统则决定“哪些日子应该出现价格”，两者配合保证：只有交易日才会被期望有数据，非交易日（周末、法定假日）不被当作缺数据。

---

## 1. 数据模型

### 1.1 `price_cache` — 日频复权价格缓存

| 字段 | 类型 | 说明 |
|------|------|------|
| `stock_code` | String(20) | 证券代码，使用持仓里的标准写法，如 `600519.SH`、`00700.HK`、`NVDA` |
| `trade_date` | Date | 交易日 |
| `open_px` | Float | 开盘价 |
| `close_px` | Float | 收盘价（最常用） |
| `high_px` | Float | 最高价 |
| `low_px` | Float | 最低价 |
| `volume` | Float | 成交量 |
| `source` | String(20) | `tencent`、`tencent_kline`、`akshare`、`akshare_fund`、`yfinance`、`tencent_fill` |

主查询模式：

```sql
SELECT close_px FROM price_cache
WHERE stock_code = ? AND trade_date <= ?
ORDER BY trade_date DESC LIMIT 1;
```

这个模式被 `import_common.resolve_price_pair()`、`/api/trend`、`/api/penetration/full-holding` 的 3 个月涨跌幅计算共同使用。

### 1.2 `trading_calendar` — 交易日历

| 字段 | 类型 | 说明 |
|------|------|------|
| `market` | String(8) | `CN` / `HK` / `US` / `OF` |
| `date` | Date | 日期 |
| `is_trading` | Boolean | 是否开市 |
| `source` | String(40) | 计算来源 |
| `note` | String(100) | 节假日名称 |

唯一约束：`UX_TRADING_CALENDAR_MARKET_DATE`。

市场定义：

| market | 含义 | 开市规则 |
|--------|------|----------|
| `CN` | 沪深 A 股 | `chinese-calendar` 库判断工作日 |
| `HK` | 港交所 | weekday < 5 且不在 HKEx 节假日静态表 |
| `US` | NYSE / NASDAQ | weekday < 5 且不在 NYSE 节假日静态表 |
| `OF` | 场外基金 | 默认 weekday < 5；实际有数据的日期记 `source='akshare'` |

静态节假日表覆盖 2020–2030 年。首次查询某个日期时，如果数据库里没有记录，会惰性计算并插入（`is_trading_day`），后续查询零计算。

---

## 2. 价格源与代码映射

### 2.1 统一入口

`backend/crawlers/price_data.py` 提供两个主要函数：

- `get_stock_info(ticker)` — 实时行情，返回 `{price, pe_ttm, market_cap, ...}`
- `fetch_price_history(ticker, days=365)` — 历史日线，返回 `[{date, open, close, high, low, volume}, ...]`

### 2.2 多源回退链

```
实时行情： 腾讯财经 API → yfinance
历史 K 线：腾讯 K 线 API → yfinance
```

| 市场 | 实时 | 历史 |
|------|------|------|
| A 股 | 腾讯财经 API | 腾讯 K 线（前复权 `qfqday`） |
| 港股 | 腾讯财经 API | 腾讯 K 线（`day`/`qfqday`） |
| 美股 | 腾讯财经 API | 腾讯 K 线（必须带交易所后缀 `.OQ`/`.N`/`.AM`） |
| OF 基金 | 无 | akshare `fund_open_fund_info_em`（净值） |

### 2.3 API 代码转换

持仓里的标准代码和不同 API 需要的格式不同。转换逻辑在 `crawlers/price_data._to_tencent_ticker`、`_to_kline_ticker`，并且可以通过数据库 `api_code_map` 表覆盖。

| 标准代码 | 腾讯实时 | 腾讯 K 线 |
|----------|----------|-----------|
| `600519.SH` | `sh600519` | `sh600519` |
| `159326.SZ` | `sz159326` | `sz159326` |
| `00700.HK` | `hk00700` | `hk00700` |
| `NVDA` | `usNVDA` | `usNVDA.OQ` |
| `QQQ` | `usQQQ` | `usQQQ.OQ` |
| `007818.OF` | 不支持 | 不支持（走 akshare 净值） |

`api_code_map` 表字段：
- `code_in`：标准代码
- `api_strategy`：`tencent_quote` / `tencent_kline` / `akshare_fund_nav`
- `code_out`：API 实际调用代码

---

## 3. 价格拉取脚本

### 3.1 `scripts/pull_history_prices.py`

为 A 股、港股穿透后的底层证券拉取历史 K 线，默认 180 天（约 6 个月）。

```bash
cd backend
python scripts/pull_history_prices.py --days 180 --market AH --max-codes 2000
```

参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--days` | 180 | 回溯天数 |
| `--market` | `AH` | `A` / `H` / `AH` |
| `--max-codes` | 2000 | 最多处理的股票数 |

数据源：
- A 股：`a_share_financial_snapshot` 中 `as_of_date=2026-05-29` 的所有股票
- 港股：`hk_share_financial_snapshot` 中同 `as_of_date` 的所有股票
- API：`crawlers.price_data.fetch_tencent_kline`

写入逻辑：
- 先查询该股票已有 `price_cache` 记录，避免重复插入
- 新日期写入 `source='tencent_kline'`
- 已存在但收盘价不同的记录会更新

输出示例：

```
DONE: attempted=523 fetched=518 inserted=89420 failed=5 skipped=0
```

### 3.2 `scripts/pull_fund_nav.py`

为 `.OF` 场外基金拉取历史净值，供基金本身走势使用。

```bash
cd backend
python scripts/pull_fund_nav.py --days 180
```

逻辑：
- 读取 `holdings` 中所有以 `.OF` 结尾的代码
- 使用 akshare `fund_open_fund_info_em`
- 只写入交易日（通过 `is_trading_day("OF", date, db)` 判断）
- 写入 `source='akshare_fund'`

### 3.3 `services/price_filler.py`

当 `a_share_financial_snapshot` / `hk_share_financial_snapshot` 里某些股票的 `current_price` 为空时，通过腾讯实时行情接口补一次当前价，并回写 `price_cache` 和重算动态 PE/PB/PS。

调用方式：

```bash
# 通过 API（需要 ADMIN_TOKEN）
curl -X POST "http://localhost:8000/api/admin/fill-prices-tencent?as_of_date=2026-05-29" \
  -H "x-admin-token: $ADMIN_TOKEN"
```

返回示例：

```json
{
  "as_of_date": "2026-05-29",
  "attempted": 47,
  "fetched": 45,
  "persisted_to_price_cache": 45,
  "dynamic_recomputed": 43
}
```

### 3.4 `scripts/fill_prices_tencent.py`

命令行包装，直接调用 `price_filler.fill_prices_for_as_of`。

```bash
cd backend
python scripts/fill_prices_tencent.py
```

---

## 4. 运行时 API 端点

### 4.1 手动触发持仓历史价回补

```http
POST /api/admin/backfill-prices?days=90
```

行为：
- 遍历 `holdings` 表中所有持仓
- `.OF` 基金调用 `fetch_fund_nav_history`
- 其他证券调用 `fetch_price_history`
- 用 `is_trading_day(market, date, db)` 过滤非交易日
- 只插入真实返回的数据，不编造

返回：每条持仓的处理结果、写入行数、`price_cache` 总行数。

### 4.2 手动触发价格缺口检查

```http
POST /api/admin/backfill-gaps?days=90
```

调用 `services.scheduler.job_backfill_gaps(days)`，检查过去 90 天每个交易日的价格完整性。

### 4.3 资产走势

```http
GET /api/trend?days=90&target=CNY
```

使用 `price_cache` + `exchange_rates` 计算每天 `Σ(qty × close_px × fx_rate)`。只使用真实价格；某日无价格时回退到该证券之前最近的真实收盘价（last-known backward-fill），不会 forward-fill 编造未来价格。

### 4.4 单股/多股历史价格

```http
GET /api/prices?codes=NVDA,GOOGL&days=90
```

返回每只证券的收盘价序列，直接调用 `fetch_price_history`。

### 4.5 交易日历查询

```http
GET /api/calendar?market=CN&start=2026-05-01&end=2026-05-31
GET /api/calendar/is-trading?market=US&date=2026-05-29
GET /api/calendar/month?market=HK&year=2026&month=6
GET /api/calendar/summary?market=CN&year=2026
```

---

## 5. 交易日历与价格完整性的配合

核心原则：**价格只应该出现在交易日**。日历系统让代码可以区分以下两种情况：

| 场景 | 是否视为缺数据 | 例子 |
|------|----------------|------|
| 交易日没有价格 | 是，需要补 | 2026-05-29 是交易日但某股票未返回数据 |
| 非交易日没有价格 | 否，正常 | 2026-06-14 周日没有价格 |

这个区分被用在：

1. `pull_history_prices.py`：K 线返回的是自然日，脚本会把所有返回的日期都写入；日历只用于理解和报告，不删除数据。
2. `admin_backfill_prices`：OF 基金净值写入前显式判断 `is_trading_day("OF", ...)`。
3. `/api/penetration/timeseries`：用 `is_trading_day("CN", ...)` 计算缺失交易日列表，返回给前端 `missing_dates`。
4. `/api/trend`：只绘制有真实价格的日期，`connectNulls: false`。

---

## 6. 常见问题

### Q：为什么 price_cache 里会有非交易日的数据？

腾讯 K 线接口在某些市场（如美股）会返回周末数据，或者 akshare 基金净值表包含非交易日。系统不会主动删除它们，但日历查询会让前端知道哪些是“不应有数据的日期”，从而不把它们当作缺数据。

### Q：如何确认 6 个月价格已经补全？

```bash
cd backend
python scripts/pull_history_prices.py --days 180 --market AH
python scripts/pull_fund_nav.py --days 180
```

然后用 `/api/penetration/timeseries?window=180` 查看 `missing_dates`，或查数据库：

```sql
SELECT stock_code, COUNT(*) AS n
FROM price_cache
WHERE trade_date >= '2025-12-18'
GROUP BY stock_code
ORDER BY n;
```

### Q：某只股票最近 6 个月价格明显少于 120 条？

可能原因：
1. 该股票在 6 个月前未上市或停牌
2. 腾讯 K 线接口对该代码返回不完整（可检查 `api_code_map` 是否需要映射）
3. 退市 / 代码变更

处理：先查 `fetch_tencent_kline(code, 180)` 实际返回多少条，再决定是否用 yfinance 或其他源补充。

---

## 7. 相关文档

- [`howto-backfill-6m-prices.md`](./howto-backfill-6m-prices.md) — 如何为持仓和穿透证券补全 6 个月收盘价
- [`superpowers/specs/2026-06-17-fund-penetration-analysis-design.md`](./superpowers/specs/2026-06-17-fund-penetration-analysis-design.md) — 基金穿透与行业聚合设计
- `data_get.md`（项目根目录）— 全项目数据源总览
