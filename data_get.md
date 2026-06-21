# 项目数据获取方式总览

本仪表盘的数据获取分为 **四大类别**：实时行情数据、历史价格数据、公司财务数据、产业链/行业数据。以下按数据源和方法详细说明。

---

## 一、实时行情数据（实时报价 + PE + 市值）

### 1.1 腾讯财经 API（美股首选，免费）

**Endpoint**:
```
https://qt.gtimg.cn/q=usNVDA.OQ,usTSM.N,usAVGO.OQ
```

**文件**: `backend/price_data.py`

**实现函数**:
- `get_stock_info(ticker)`（约 line 679）— 多源实时报价主入口，按优先级依次尝试各数据源
- `_parse_tencent_quote(ticker)`（约 line 470）— 解析腾讯实时行情 JSON

**数据内容**: 当前价、涨跌幅、昨收、开盘、最高最低、成交量、换手率、振幅、PE_TTM、市值

**Ticker 映射表**: `TENCENT_US_MAP`（约 line 69-116）
- 35+ 美股/ADR ticker，格式 `us{ticker}`（如 `usNVDA`、`usTSM`）
- 无交易所后缀 — 腾讯实时行情 API 不需要

**特点**:
- 免费、无需 API Key
- 并发查询无限制
- 数据实时性约 3-5 秒延迟
- User-Agent: `Mozilla/5.0 (Windows NT 10.0; Win64; x64)`

### 1.2 yfinance（全球市场备用）

**文件**: `backend/price_data.py`（约 line 234）

**实现函数**: `_fetch_yfinance(ticker, days, interval)`

**数据内容**: 当前价、PE_TTM、市值、营收、净利润、行业分类、股息率

**Ticker 映射**: `YFINANCE_TICKER_MAP`（约 line 164）
- 特殊格式映射：`SOX → ^SOX`（费城半导体指数）、`000660 → 000660.KS`（SK Hynix）

**特点**:
- IP 限流严重，需 2-5 秒请求间隔
- 美股数据完整，韩国/日本等全球市场可用
- 依赖 `yfinance` 包

### 1.3 akshare（A 股行情）

**文件**: `backend/price_data.py`（约 line 170）、`backend/refresh_company_data.py`（约 line 45）

**实现函数**: `_fetch_akshare_a_share(code, days)`、`_get_akshare_info(ticker)`

**数据内容**: 当前价、涨跌幅、总市值、市盈率-动态、名称

**Ticker 映射**: `A_SHARE_MAP`（约 line 41）
- 中芯国际 `688981`（A 股代码）

**港股映射**: `HK_TICKER_MAP`（约 line 52）
- 腾讯 `hk_00700`、阿里 `hk_09988`、小米 `hk_01810`

**特点**:
- 国内 A 股数据首选
- 无需 API Key
- 依赖 `akshare` 包
- 数据频率实时

### 1.4 Naver API（韩国股票）

**文件**: `backend/price_data.py`（约 line 340）

**实现函数**: `_fetch_naver_korean_info(ticker)`

**数据内容**: 当前价（韩元 + 美元换算）、PE_TTM、EPS、市值（韩元 + 美元）

**Ticker 映射**: `NAVER_KOREAN_MAP`（约 line 171）
- SK Hynix: `000660` → Naver 代码 `000660`
- Samsung: `SMSN` → Naver 代码 `005930`

**汇率**: 硬编码 `KRW_USD_RATE = 1300.0`（约值，用于韩元→美元换算）

**Endpoint**:
```
https://m.stock.naver.com/api/stock/{code}/basicInfo
https://m.stock.naver.com/api/stock/{code}/integration
```

**特点**:
- 韩国股票数据首选
- 免费，移动端 API
- 需 `User-Agent` 模拟移动端

### 1.5 十五分钟自动刷新

**文件**: `backend/scheduler.py`（约 line 134）

**调度器**: `refresh_follow_prices_15min()`
- 每 15 分钟执行一次
- 遍历所有关注（Follow）列表，逐个调用 `get_stock_info()`
- 结果写入 `StockInfoCache` 表

**触发方式**: APScheduler 后台调度，服务启动时在 `main.py` startup 中注册。

---

## 二、历史价格数据（K 线 + 复权）

### 2.1 腾讯财经 K 线 API（首选）

**Endpoint**:
```
https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=usNVDA.OQ,day,2023-01-01,2026-06-11,2000,qfq
```

**文件**: `backend/price_data.py`（约 line 294）

**实现函数**: `_fetch_tencent_kline(ticker, days)`

**数据内容**: 日期、开盘、收盘、最高、最低、成交量、复权因子

**参数说明**:
- `qfq` = 前复权（forward-adjusted），用于计算收益率
- 交易所后缀：`.N` = NYSE、`.OQ` = NASDAQ、`.AM` = NYSE Arca

**Ticker 映射**: `TENCENT_KLINE_MAP`（约 line 119-159）
- 与实时行情格式不同，K 线需要完整的交易所后缀（如 `usNVDA.OQ`、`usTSM.N`）

### 2.2 三年历史数据回填

**文件**: `backend/backfill_3y_prices.py`

**实现函数**: `backfill_3y_prices()`
- 遍历所有有 ticker 的公司（去重）
- 调用 `fetch_price_history(ticker, days=1095)` 获取过去 3 年复权价格
- 写入 `PriceCache` 表

**执行时机**:
- 手动运行 `python backfill_3y_prices.py`
- 服务启动时可通过 API 触发

### 2.3 多源策略与限流

```
US ticker → Tencent Kline API (qfq前复权)
Korean stocks → yfinance (天然复权 Adj Close)
A-share → akshare
Fallback → yfinance（通用）
```

yfinance 调用间隔：2-5 秒，避免 IP 限流。

### 2.4 数据同步：PriceCache → MarketData

**文件**: `backend/backfill_3y_prices.py`（约 line 85）

**实现函数**: `sync_market_data()`
- 将 `PriceCache` 表的日频价格数据同步到 `MarketData` 表
- 关联 `company_id`，供产业链分析页面使用

---

## 三、公司财务数据

### 3.1 yfinance 财务数据（主要来源）

**文件**: `backend/refresh_company_data.py`

**数据字段**:
| 字段 | 来源 | 说明 |
|------|------|------|
| revenue_b / net_income_b | yfinance `totalRevenue` / `netIncomeToCommon` | 单位：亿美元 |
| pe_ttm | Tencent API / yfinance `trailingPE` | 首选腾讯，备选 yfinance |
| market_cap_b | Tencent API / yfinance `marketCap` / Naver | 单位：亿美元 |
| ps_ttm / pb / dividend_yield | yfinance | 辅助估值指标 |

### 3.2 公司数据刷新流程

**主函数**: `refresh_all_company_data(db)`（约 line 76）
1. 查询所有产业链关联公司（去重）
2. US ticker → Tencent API（实时报价 + PE）
3. Korean stocks → Naver API → yfinance fallback
4. A-share → akshare
5. 所有公司 → yfinance 补充财务数据
6. 写入 `StockInfoCache` 表（JSON 字段）
7. 同步 `Financial` 表（fiscal_year=2025）

**调度**: 每日 7:00 / 19:00（`scheduler.py` `refresh_company_financials`）

### 3.3 组合跟踪期间收益计算

**文件**: `backend/portfolio_tracking.py`

**函数**: `compute_holding_returns(db, ticker)`
- 从 `PriceCache` 读取日频数据
- 使用 `bisect_right` 定位各期间起始日期
- 计算 7 段涨跌幅：1日、1周、3月、6月、1年、3年
- 期间定义：1日=前1交易日、1周≈7日历日、1月≈30日、3月≈90日、6月≈180日、1年≈365日、3年≈1095日

**EPS 推导**: `derive_eps_metrics(company_id, ticker, cache_data, db)`
- `EPS_TTM = price / PE_TTM`
- `FY2025 EPS = net_income * current_price / market_cap_b`
- 前瞻 EPS 通过 `EPS_TTM` × `(1 + g)^n` 复合（g 默认 15% 或从 Forecasts 表读取）
- 2026E/2027E 前瞻 = 价格 / 对应年 EPS

---

## 四、产业链/行业数据（14 采集器）

### 4.1 采集器架构

**基类**: `backend/industry_collector/base.py` — `BaseCollector`
- 幂等检查（同指标同天不重复）
- 自动创建 `KeyIndicator` 记录
- 写入 `IndicatorObservation` + 自动计算边际变化
- 来源标注 + 数据质量标注

**统一入口**: `backend/industry_collector/__init__.py`
```python
async def collect_all(source=None, db=None)
```

### 4.2 全量采集器列表

| 数据源 | 采集器类 | 指标 | 频率 | 方法 |
|--------|----------|------|------|------|
| **TSMC IR** | `TSMCMonthlyRevenueCollector` | TSMC 月度营收 | 月度 | 网页爬虫 |
| | `TSMCCoWoSCollector` | CoWoS 产能/利用率 | 季度 | 法说会爬虫 |
| **TrendForce** | `TrendForceCollector` | DRAM/NAND 合约价、HBM、企业 SSD | 季度 | 网页爬虫 |
| **WSTS/SIA** | `WSTSSIACollector` | 全球半导体销售 | 月度 | 网页爬虫 |
| **SEMI** | `SEMICollector` | 半导体设备市场 | 月度 | 网页爬虫 |
| | `SEMIWaferCollector` | 晶圆产能 | 季度 | 网页爬虫 |
| **ASML IR** | `ASMLCollector` | EUV 光刻机营收 | 季度 | 网页爬虫 |
| **NVIDIA IR** | `NVIDIAIRCollector` | 数据中心营收 | 季度 | SEC Filing/新闻稿 |
| **中国海关** | `ChinaCustomsICImportCollector` | IC 进口额 | 月度 | 网页爬虫 |
| | `ChinaCustomsICExportCollector` | IC 出口额 | 月度 | 网页爬虫 |
| **Hyperscaler Capex** | 4 个采集器（AMZN/MSFT/GOOG/META） | 云巨头资本开支 | 季度 | 网页爬虫 |
| **GPU 云** | `GPUCloudPriceCollector` | GPU 云租赁价格 | 季度 | 网页爬虫 |
| **Synopsys/Cadence** | `SynopsysBacklogCollector` / `CadenceBacklogCollector` | EDA 订单积压 | 季度 | 网页爬虫 |
| **分销商** | 3 个采集器（Arrow/Avnet/WPG） | 元器件分销营收 | 季度 | 网页爬虫 |
| **封测 OSAT** | 3 个采集器（CoWoS/资本开支/ASE） | 先进封装营收 | 季度 | 网页爬虫 |
| **ODM 服务器** | 5 个采集器（广达/纬创/英业达/和硕/纬颖） | 服务器代工月营收 | 月度 | 网页爬虫 |
| **ARM IR** | `ARMRoyaltyCollector` | ARM 版税营收 | 季度 | 网页爬虫 |

**采集方法**: 全部通过 HTTP 请求 + BeautifulSoup 网页爬虫，从各公司 IR 页面或行业机构公开数据获取。

**调度**: 每日 6:00 / 18:00（`scheduler.py` `auto_collect_and_analyze`）

### 4.3 产业链市场规模数据

**文件**: `backend/data_pipeline/market_size_collector.py`

覆盖 10+ 产业链环节的市场规模（2025/2026/2027E）：
- 数据来源：WSTS Spring 2026 Forecast、Gartner 2025 Preliminary Ranking、TrendForce AI/HBM Roadshow、Yole Advanced Packaging 2025、SEMI Year-End Report 2025、IDC Server Market Tracker
- 每个数据点标注了具体来源引用和出处 URL

### 4.4 宏观经济数据

**文件**: `backend/data_pipeline/macro_collector.py`

**Layer 1: FRED API**
- 50+ 美国宏观指标（GDP、CPI、PPI、工业生产、产能利用率、就业、零售、PMI 等）
- 需要 `FRED_API_KEY`（免费申请：https://fred.stlouisfed.org/docs/api/api_key.html）
- Endpoint: `https://api.stlouisfed.org/fred/series/observations`
- 数据按 tier 分级：tier 1 = 核心（每天更新）、tier 2 = 辅助（每周）、tier 3 = 参考（每月）

**Layer 2: tedata (Trading Economics)**
- 200+ 国家宏观经济数据
- 通过 pip install tedata + Selenium 使用
- 适用于非美经济体的宏观数据

**Layer 3: akshare 宏观**
- 中国宏观数据（PMI、CPI、GDP、货币供应、利率等）
- 通过 `akshare` 宏微观接口获取

**配置**: `FRED_API_KEY` 从 `.env` 文件加载（`backend/data_pipeline/macro_collector.py` line 32-40）

### 4.5 财务数据采集器

**文件**: `backend/data_pipeline/financial_collector.py`
- 采集各公司更细粒度的财务数据
- 通过 yfinance 获取利润表/资产负债表/现金流量表关键字段

---

## 4.6 下钻维度分析数据流（Drilled-Dimension Analysis）

**文件**: `backend/services/drillable_funds.py`, `backend/main.py`

**端点**: `GET /api/penetration/dimension-drilled?dim={dim}&as_of_date={date}&market={A+H|A|H}`

下钻维度分析只分析「穿透后的底层证券」，即把可下钻基金（FundIndexMap 映射的指数基金）按 5/29 净值还原成对应成分股，再与 CSI300 按同一维度对比。

**输入数据源**:

| 表 / 文件 | 用途 |
|-----------|------|
| `Holding` | 基金持仓数量与金额 |
| `FundIndexMap` | 基金 → 追踪指数映射 |
| `IndexConstituentSnapshot` | 指数 5/29 成分股权重 |
| `FundDailyNav` | 基金 5/29 单位净值 + 累计净值、当前净值 |
| `AShareFinancialSnapshot` / `HKShareFinancialSnapshot` | 成分股 PE/PB/PS、分类字段、收盘价 |
| `Csi300ConstituentSnapshot` | CSI300 权重与分类 fallback |
| `ExchangeRate` | USD/HKD → CNY 汇率 |

**计算链路**:

```text
Holding.quantity × FundDailyNav.nav_529
        ↓
   fund_value_529
        ↓
   × IndexConstituentSnapshot.weight
        ↓
   ÷ snapshot.baseline_price
        ↓
   shares_equivalent × snapshot.current_price
        ↓
   est_market_value_cny
        ↓
   按 dim 列聚合 → 组合 bucket
```

CSI300 侧使用 `Csi300ConstituentSnapshot.weight` 作为权重，PE/PB/PS 优先从 `a_share_financial_snapshot` / `hk_share_financial_snapshot` 读取（因为 `csi300_constituent_snapshot` 的估值字段常为 NULL）。

**完整文档**: 见 `docs/reference-dimension-drilled.md`、`docs/explanation-drilled-dimension-math.md`。

---

## 五、AI 分析数据

### 5.1 DeepSeek API

**文件**: `backend/ai_analysis.py`

**两个分析入口**:

1. **`generate_indicator_analysis()`** — 单指标"一句话分析"
   - 输入：指标名称、最新值、上期值、变化百分比
   - 输出：50-80 字中文分析，解释变化驱动逻辑
   - 模型：`deepseek-chat`，temperature=0.3
   - 缓存：`_analysis_cache` 避免同一数值重复调用

2. **`generate_industry_impact_analysis()`** — 三重影响分析
   - 输出 JSON：行业景气度、产业链影响、重点公司影响
   - 使用 `response_format: json_object`

**Endpoint**: `https://api.deepseek.com/v1/chat/completions`

**配置**:
```python
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
```

**调度**: 自动采集完成后触发批量分析（`scheduler.py` line 97-103）

### 5.2 分析结果持久化
- 分析文本写入 `IndicatorObservation.analysis` 字段
- 边际变化数据自动由 `BaseCollector._write_observation()` 计算
- 判断日志写入 `JudgmentLog` 表（`main.py` `/api/judgment-log` 端点）

---

## 六、API Keys 管理

### 当前使用的 API Keys

| Key | 用途 | 配置方式 | 获取地址 |
|-----|------|----------|----------|
| `DEEPSEEK_API_KEY` | DeepSeek AI 分析 | 环境变量（`os.getenv`），Zeabur Dashboard 设置 | https://platform.deepseek.com |
| `FRED_API_KEY` | FRED 宏观数据 | `.env` 文件或环境变量 | https://fred.stlouisfed.org/docs/api/api_key.html |

### 环境变量加载方式

**Zeabur 生产环境**: 在 Zeabur Dashboard → Service → Environment Variables 中设置 `DEEPSEEK_API_KEY` 和 `FRED_API_KEY`。

**本地开发**: 项目根目录 `.env` 文件（已 `.gitignore`）：
```
DEEPSEEK_API_KEY=sk-xxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
FRED_API_KEY=xxxxxxx
```

**`.env` 加载代码**（`backend/data_pipeline/macro_collector.py` line 32-40）：
```python
env_path = Path(__file__).parent.parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
```

**无 Key 时的行为**:
- DeepSeek API: `DEEPSEEK_API_KEY` 为空时 `generate_indicator_analysis()` 和 `generate_industry_impact_analysis()` 返回 `None`，不会崩溃
- FRED: `FRED_API_KEY` 未设置时输出 `"FRED_API_KEY not set — skipping FRED macro data"` 并跳过，不影响其他功能

---

## 七、数据缓存与持久化

### 7.1 数据库缓存表

| 表名 | 用途 | 更新频率 | 数据来源 |
|------|------|----------|----------|
| `StockInfoCache` | 公司行情/财务缓存（JSON） | 15 分钟/每日更新 | 腾讯/yfinance/Naver/akshare |
| `PriceCache` | 日频复权价格 | 按需回填 | Tencent Kline/yfinance |
| `MarketData` | 产业链分析用价格 | 从 PriceCache 同步 | 同 PriceCache |
| `Financial` | 财务数据（FY2025） | 每日 7/19 点 | yfinance |
| `KeyIndicator` | 产业链指标元数据 | 采集器首次运行时创建 | 各采集器 |
| `IndicatorObservation` | 指标观测值 + 边际变化 | 每日 6/18 点 | 各采集器 |

### 7.2 幂等设计
- `BaseCollector._has_today_data()` 检查当天数据避免重复
- `IndicatorObservation` 按 `(indicator_id, date)` 唯一
- `startup_migration.py` 启动迁移幂等（`query first` 模式）

### 7.3 数据库配置

**文件**: `backend/database.py`
```python
DB_PATH = os.environ.get("DB_PATH", "./teck_dashboard.db")
```

- 本地：`./teck_dashboard.db`
- Zeabur 生产：`/data/teck_dashboard.db`（挂载持久卷）
- 种子数据：`backend/seed_db/teck_dashboard.seed`

---

## 八、启动迁移（Startup Migration）

**文件**: `backend/startup_migration.py`

**用途**: Zeabur 持久卷已有旧 DB，新代码部署后自动补入新增公司/关注。

**机制**:
- 只做 DB 操作，不调外部 API（避免 Zeabur 健康检查超时）
- 幂等：重复运行不产生副作用
- 当前确保 SNDK（SanDisk）和 WDC（Western Digital）的数据存在

**执行时机**: 服务启动时在 `main.py` startup 事件中自动运行（Startup Migration 先于 Scheduler 初始化）。

---

## 九、定时调度汇总

| 任务 | 频率 | 执行函数 | 操作 |
|------|------|----------|------|
| 产业数据采集 + AI 分析 | 每日 6:00 / 18:00 | `auto_collect_and_analyze()` | 运行全量 14+ 采集器 → DeepSeek 分析 |
| 关注价格刷新 | 每 15 分钟 | `refresh_follow_prices_15min()` | 腾讯 API 实时行情 |
| 公司财务数据刷新 | 每日 7:00 / 19:00 | `refresh_company_financials()` | yfinance PE/市值/营收 |
| 事件后涨跌幅刷新 | 每 4 小时 | `refresh_post_event_returns()` | 时间线涨跌幅计算 |
| 宏观经济采集 | 每日增量 | `macro_collector.py` | FRED API + tedata |

**调度器**: APScheduler `BackgroundScheduler`（`scheduler.py`），服务启动时在 `main.py` startup 事件中无条件 `scheduler.start()`。

---

## 十、数据流拓扑

```
┌───────────────────────────────────────────────────────────────────┐
│                        用户前端 (React)                           │
│  ┌──────────┐ ┌────────────┐ ┌─────────────┐ ┌───────────────┐  │
│  │ 市场概览  │ │ 产业链全景  │ │  产业情报    │ │  组合跟踪      │  │
│  └────┬─────┘ └─────┬──────┘ └──────┬──────┘ └───────┬───────┘  │
└───────┼─────────────┼──────────────┼──────────────┼────────────┘
        │             │              │              │
┌───────┼─────────────┼──────────────┼──────────────┼────────────────┐
│       ▼             ▼              ▼              ▼                │
│                         FastAPI 后端 (30+ 端点)                    │
│   ┌────────────────────────────────────────────────────────────┐   │
│   │                       SQLAlchemy DB                         │   │
│   │  StockInfoCache │ PriceCache │ MarketData │ Financial       │   │
│   │  KeyIndicator │ IndicatorObservation │ TimelineEvent        │   │
│   │  Company │ Follow │ Portfolio │ PortfolioHolding            │   │
│   └────────────────────────────────────────────────────────────┘   │
│        ▲              ▲              ▲              ▲              │
│        │              │              │              │              │
│   ┌────┴────┐   ┌────┴────┐   ┌────┴────┐   ┌────┴────┐         │
│   │ 腾讯财经  │   │yfinance │   │ akshare │   │ Naver   │         │
│   │ API(US)  │   │(Global) │   │ (A股)   │   │ (韩国)  │         │
│   └─────────┘   └─────────┘   └─────────┘   └─────────┘         │
│                                                                   │
│   ┌────────────┐   ┌────────────┐   ┌──────────────────────┐     │
│   │ 14 行业采集器│   │FRED 宏观   │   │ DeepSeek AI 分析     │     │
│   │ TSMC/WSTS/  │   │50+ 指标    │   │ 一句话分析 + 三重影响│     │
│   │ NVIDIA/...  │   │GDP/CPI/...│   │ 缓存防重复调用        │     │
│   └────────────┘   └────────────┘   └──────────────────────┘     │
└───────────────────────────────────────────────────────────────────┘
```

## 十一、数据质量保障

### 11.1 数据校验

**文件**: `backend/refresh_company_data.py`（约 line 271）

**`verify_data_integrity(db)`** 函数校验：
- `Financial.pe_ttm` vs `StockInfoCache.pe_ttm` 差异 > 5 的告警
- `Financial.revenue` vs `StockInfoCache.revenue_b` 差异 > 10 的告警

### 11.2 缺数据容错

- 所有采集器有 `safe_collect()` 异常保护
- 前端表格对缺数据统一显示 "-" 不崩溃
- 实时行情缓存 `StockInfoCache` 优先保留已有值：`if cache_data.get("revenue_b") is None and existing_data.get("revenue_b"): cache_data["revenue_b"] = existing_data["revenue_b"]`
- 组合跟踪页面 `compute_holding_returns()` 对数据不足的期间返回 `None` 而非报错

### 11.3 数据质量标注

`IndicatorObservation` 表有 `data_quality` 字段：
- `confirmed` — 确认数据（官方来源）
- `estimated` — 估算数据
- `preliminary` — 初步数据
