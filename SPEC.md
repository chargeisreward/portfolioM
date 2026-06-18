# PortfolioM — 投资组合穿透分析系统

## Context

个人投资者持有一个 363 万 CNY 的组合，资产横跨 A 股基金/ETF（70%）、债券（19.6%）、黄金（10.4%）和美股个股（少量）。组合中 25 只国内基金/ETF 跟踪不同指数，底层持仓高度重叠且不透明。目前只有一份 Excel 持仓清单，无法回答以下问题：

- "我的组合实际持有哪些股票？每只占比多少？"
- "这些股票分布在上中下游产业链的哪个环节？"
- "组合整体的利润增速偏高中低哪一档？和沪深 300 比如何？"
- "TTM PE、Forecast PE 是否合理？"

需要一个系统来自动化这个穿透和分析流程。

---

## Current State

一份 Excel 文件 `_2026-06-04.xlsx`，单 sheet（持仓明细），47 行：

| 数据类型 | 示例 | 数量 |
|----------|------|------|
| A股联接基金/ETF（.OF） | 国泰半导体ETF联接C（007818） | 25 只 |
| 交易所 ETF（.SZ） | 159326.SZ 半导体设备ETF | 2 只 |
| 美股个股 | GOOGL, NVDA, INTC, SNDK | 4 只 |
| 美股 ETF | QQQ | 1 只 |

无后端系统、无数据库、无 API、无前端界面。分析靠手动。

---

## Proposed Change

构建三层架构：

```
┌────────────────────────────────────────────────────────┐
│                     Frontend                           │
│         React 18 + Vite + CSS Variables 仪表盘          │
└────────────────────────┬───────────────────────────────┘
                         │ HTTP REST API
┌────────────────────────┴───────────────────────────────┐
│                 Backend (FastAPI)                       │
│    /api/holdings  /api/penetration  /api/analysis       │
│    /api/securities  /api/security-types  /api/scheduler │
│    /api/data-browser  /api/prices  /api/etf/*           │
└────────────────────────┬───────────────────────────────┘
                         │
┌────────────────────────┴───────────────────────────────┐
│                    Data Layer                           │
│  SQLite → PostgreSQL（部署时切换）                       │
│  爬虫 ETL：中证指数公司 → 成分股 → 财务数据              │
│  定时调度：APScheduler（实时行情/财务/行业数据）          │
└────────────────────────────────────────────────────────┘
```

### 数据源总览

数据获取方式参考同作者另一项目 `data_get.md` 中验证过的架构，使用多源互补 + 缓存降级策略。

| 数据源 | 用途 | 认证 | 费用 | 优先级 |
|--------|------|------|------|--------|
| **腾讯财经 API** (qt.gtimg.cn) | A股/美股实时行情 + PE + 市值 | 无 | 免费 | 🥇 首选 |
| **腾讯K线 API** (web.ifzq.gtimg.cn) | 历史 K 线（前复权） | 无 | 免费 | 🥇 首选 |
| **akshare** | A 股行情 + 基础财务 | 无 | 免费 | 🥇 A股首选 |
| **yfinance** | 美股财务数据、全球备用 | 无 | 免费 | 🥈 备用 |
| **中证指数公司** (csindex.com.cn) | ETF 跟踪指数的成分股列表 | 无 | 免费 | 独家来源 |
| **天天基金网** (fund.eastmoney.com) | 基金→跟踪指数映射 | 无 | 免费 | 独家来源 |
| **Tushare Pro** | 深度 A 股财务数据 | Token | 积分 | 🥉 补充 |
| **申万宏源**（通过腾讯接口） | 行业分类 | 无 | 免费 | 产业链映射 |

#### 腾讯财经 API（首选，免费，3-5 秒延迟）

**实时行情**（含 PE、市值、行业）：
```
GET https://qt.gtimg.cn/q=sz000001,sh600519,usNVDA.OQ,usTSM.N
```
无交易所后缀（US ticker 直接用 `usNVDA`），返回管道符分隔格式含 70+ 字段。

**历史 K 线**（前复权 qfq）：
```
GET https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=usNVDA.OQ,day,2024-01-01,2026-06-11,2000,qfq
```
美股需完整交易所后缀：`.N`=NYSE, `.OQ`=NASDAQ, `.AM`=NYSE Arca。

**User-Agent**: `Mozilla/5.0 (Windows NT 10.0; Win64; x64)`

#### 多源回退策略

```
US ticker → 腾讯财经 API（实时） + 腾讯 K 线（历史）+ yfinance（财务补充）
A 股      → akshare（行情）+ 腾讯 K 线（历史）+ Tushare（财务可选）
港股      → 腾讯财经 API (hk_00700) + yfinance 备用
备用      → yfinance（通用 fallback，需 2-5 秒请求间隔避免限流）
```

#### 数据缓存表（5 张核心表）

| 表名 | 用途 | 更新频率 | 更新策略 |
|------|------|----------|----------|
| `StockInfoCache` | 公司行情/财务缓存（JSON） | 15 分钟（行情）/ 每日（财务） | 有旧值保旧，不覆盖为 None |
| `PriceCache` | 日频复权价格 | 交易时段每15分钟 | 腾讯K线 → yfinance fallback |
| `StockFinancial` | 财务数据（PE、营收增速、利润增速） | 每日 | 多源合并 |
| `SecurityMaster` | 证券基础信息（原币种/类型） | 从持仓同步 | 一次配置，手动更新 |
| `SecurityTypeConfig` | 证券类型配置（精度等） | 手动管理 | 种子数据初始化 |

### 数据流

```
┌──────────┐   爬取指数成分股    ┌──────────┐  Tushare/腾讯/yfinance   ┌──────────┐
│ ETF 列表 ├──────────────────→│ 成分股表 ├───────────────────────→│ 财务数据  │
│ (Excel)  │  中证指数公司网站   │ stock_id │  TTM PE/利润增速/行业   │           │
└──────────┘                    └─────┬────┘                       └──────────┘
        │                              │                              │
        │                    ┌─────────┘                              │
        │                    │  腾讯证券API（补充行情/行业）           │
        │                    ↓                                        │
        │              ┌──────────────────┐                           │
        └─────────────→│  穿透计算引擎     ├───────────────────────────┘
                       │  权重递归分解     │
                       └────────┬─────────┘
                                ↓
                          ┌──────────────────────────────────────┐
                          │        底层股票穿透表 + 分析          │
                          │  上中下游 | 高中低增长 | PE 估值      │
                          └──────────────────────────────────────┘
```

---

## Implementation Details

### 1. 数据模型（SQLAlchemy ORM）

```python
class SecurityMaster(models.Base):
    """证券基础表：维护每只证券的原币种、类型等基础属性"""
    security_code: str   # PK，证券代码
    security_name: str   # 证券名称
    currency: str        # 原币种（上市地交易币种），如 CNY/USD/HKD
    asset_type: str      # 证券类型代码
    exchange: str        # 交易所

class SecurityTypeConfig(models.Base):
    """证券类型配置表：不同类型证券的显示精度等配置"""
    asset_type: str       # PK，证券类型代码
    type_name: str        # 类型中文名
    price_precision: int  # 单价显示小数位数（基金=4，股票=2）
    amount_precision: int # 金额显示小数位数
    sort_order: int       # 排序权重

class Holding(models.Base):
    """组合持仓（从 Excel 导入）"""
    security_code: str
    security_name: str
    quantity: float      # 持仓数量/份额
    price: float|None    # 最新单价（原币种）
    currency: str        # 原币种
    amount: float        # 持仓金额（原币种）
    amount_cny: float    # 持仓金额（CNY）
    asset_type: str      # 证券类型

class Fund(models.Base):
    """基金/ETF 基础信息"""
    code: str          # 007818.OF
    name: str          # 国泰CES半导体ETF联接C
    type: str          # equity_link/bond/gold/hk_etf/us_etf
    tracking_index: str|None  # 跟踪指数代码（如 990001）

class IndexConstituent(models.Base):
    """指数成分股（爬虫结果）"""
    index_code: str    # 中证指数代码
    stock_code: str    # 股票代码
    stock_name: str
    weight: float      # 权重（%）
    market_cap: float  # 总市值
    as_of_date: date

class StockFinancial(models.Base):
    """个股财务数据"""
    stock_code: str
    stock_name: str
    ttm_pe: float|None
    revenue_growth: float|None   # 营收增速(%)
    profit_growth: float|None    # 净利润增速(%)
    profit_growth_fy1: float|None # 预测FY1增速
    profit_growth_fy2: float|None # 预测FY2增速
    market_cap: float
    industry_citic: str|None      # 中信行业分类
    industry_eastmoney: str|None  # 东方财富行业分类
    as_of_date: date

class ExchangeRate(models.Base):
    """汇率缓存"""
    from_currency: str
    to_currency: str
    rate: float
    source: str
    date: date

class PriceCache(models.Base):
    """日频复权价格缓存"""
    stock_code: str
    trade_date: date
    open_px: float
    close_px: float
    high_px: float
    low_px: float
    volume: float
    source: str

class StockInfoCache(models.Base):
    """公司行情/财务 JSON 缓存"""
    stock_code: str   # PK
    stock_name: str
    data_json: JSON
    updated_at: datetime

class PenetrationResult(models.Base):
    """穿透计算结果"""
    security_code: str
    stock_code: str
    stock_name: str
    weight: float
    industry: str
    chain_position: str
    ttm_pe: float|None
    profit_growth: float|None
    market_cap: float|None

class Csi300Baseline(models.Base):
    """沪深300基准数据"""
    metric: str
    value: float
    as_of_date: date
```

### 2. 爬虫系统（ETL Pipeline）

**爬虫 1：ETF → 跟踪指数映射**
- 输入：25 只基金代码
- 来源：天天基金网（fund.eastmoney.com）基金详情页
- 输出：每只基金跟踪的指数代码和名称
- 更新频率：按需（一次配置，几乎不变）

**爬虫 2：指数成分股**
- 输入：指数代码列表
- 来源：中证指数公司（csiindex.com）→ 成分股列表页面
- 方法：requests + lxml 解析 HTML 表格
- 输出：`IndexConstituent` 表
- 更新频率：每日（开盘前）
- 容错：爬取失败时使用缓存数据

**爬虫 3：个股财务数据**
- 输入：所有成分股 stock_code 列表 + 美股列表
- 来源 A 股：Tushare Pro API（`fina_indicator`、`daily_basic`）
- 来源 腾讯：腾讯证券 API 作为补充/备用（财务指标、营收利润增速等）
- 来源 美股：yfinance（`info`、`financials`）
- 输出：`StockFinancial` 表
- 更新频率：每日

**爬虫 4：日行情价格**
- 来源 A 股：Tushare Pro API（`daily`）
- 来源 美股：yfinance（`history`）
- 来源 腾讯：腾讯证券行情接口获取实时价格 + 历史 K 线
- 输出：`StockPrice` 表

**爬虫 5：行业分类（腾讯/申万）**
- 来源：腾讯证券 API 获取个股行业归属（申万行业分类）
- 补充：Tushare 获取中信/申万行业代码
- 输出：更新 `StockFinancial.industry_*` 字段
- 用途：产业链上中下游映射的基础

### 3. 穿透计算引擎

核心算法：**权重递归分解**

```
输入：组合持仓（基金 + 直接持股）
处理：
  1. 对每只基金：
     a. 查跟踪指数
     b. 取指数成分股权重
     c. 基金金额 × 成分股权重 = 底层股票持有金额
  2. 合并同只股票：多个基金持有 + 直接持股
  3. 归一化：底层股票金额 / 总金额 × 100%
输出：底层股票穿透表（含权重%）
```

**特殊处理：**
- 债券基金（006829、014856、006517）：不穿透，整体视为"债券类"资产
- 黄金基金（008701、008702、002611）：不穿透，整体视为"黄金类"资产
- QDII 基金（纳斯达克100、港股通等）：按跟踪指数穿透到美股/港股
- 美股个股（GOOGL、NVDA、INTC、SNDK）：直接纳入穿透表
- QQQ：穿透到纳斯达克100成分股

### 4. 分析引擎

#### 4.1 产业链位置映射

```
东方财富行业分类 → 上中下游映射规则：

上游（上游/原材料）：
  有色金属、钢铁、煤炭、石油、化工、采掘、农林牧渔

中游（中游/制造）：
  机械设备、电子、电气设备、汽车、国防军工、建筑材料、
  建筑装饰、交通运输

下游（下游/消费服务）：
  食品饮料、医药生物、家用电器、商业贸易、休闲服务、
  传媒、计算机、通信、房地产、银行、非银金融

其他：综合、公用事业等
```

#### 4.2 增长分层（沪深300 基准法）

```
算法：
  1. 取沪深300 所有成分股的 profit_growth + 权重
  2. 按 profit_growth 从高到低排序
  3. 累加权重：
     - 0% → 33%:  高增长（阈值 = 第33%分位点的利润增速值）
     - 33% → 66%: 中增长
     - 66% → 100%: 低增长
  4. 用同样的 profit_growth 阈值切割组合持仓的底层股票

输出：组合 vs 沪深300 的增长率分布对比
```

#### 4.3 估值分析

```
对每只底层股票计算：
  - TTM PE
  - Forecast PE_1yr = TTM PE / (1 + profit_growth_fy1)
  - Forecast PE_2yr = TTM PE / (1 + profit_growth_fy2)^2

聚合到组合层面：
  - 加权平均 TTM PE
  - 加权平均 Forecast PE_1yr/2yr
  - 沪深300 同指标对比
```

#### 4.4 其他分析维度

```
- 竞争格局：寡头垄断 / 充分竞争（基于行业+市占率规则判定）
- 收入增长分层（同利润增长算法）
- 市值分布（大盘/中盘/小盘）
- 行业集中度（前3/前5行业集中度%）
```

### 5. API 设计（FastAPI）

```
# 持仓
GET  /api/holdings                    → 原始持仓列表
GET  /api/holdings/summary            → 组合概览（总额、大类分布）
GET  /api/holdings/converted?target=CNY → 持仓（含币种转换、证券基础信息、类型精度配置）
POST /api/holdings/import             → 上传新 Excel 更新持仓
POST /api/holdings/fill-prices        → 手动触发价格填充

# 证券基础
GET  /api/securities                  → 证券基础信息列表
GET  /api/securities/{code}           → 单只证券基础信息
PUT  /api/securities/{code}           → 新增/更新证券基础信息
POST /api/securities/sync-from-holdings → 从持仓同步证券基础信息

# 证券类型配置
GET  /api/security-types              → 类型配置列表
PUT  /api/security-types/{asset_type} → 新增/更新类型配置
POST /api/security-types/seed         → 初始化类型配置种子数据

# 汇率
GET  /api/exchange-rates              → 汇率列表
POST /api/exchange-rates/update       → 手动更新汇率

# ETF / 穿透
POST /api/crawl/etf-mapping           → 爬取 ETF→指数映射
POST /api/crawl/constituents          → 爬取指数成分股
POST /api/penetration/calculate       → 执行穿透计算
GET  /api/penetration/table           → 底层股票穿透表
GET  /api/penetration/summary         → 穿透汇总（行业/板块）

# 分析
GET  /api/analysis/industry-chain     → 上中下游分析
GET  /api/analysis/growth             → 高中低增长分析
GET  /api/analysis/valuation          → PE 估值分析

# 价格
GET  /api/prices?codes=...&days=30    → 价格走势数据
GET  /api/prices/bonds                → 债券（2%约当）收益曲线

# 沪深300
POST /api/csi300/recalc               → 重算沪深300基准

# 爬虫
POST /api/crawl/all                   → 全量爬取

# 调度器
GET  /api/scheduler/status            → 调度器状态
POST /api/scheduler/trigger/{job_id}  → 手动触发定时任务

# 数据浏览
GET  /api/data-browser/tables         → 数据表列表（分类结构）
GET  /api/data-browser/{table}?page=1&page_size=50 → 分页浏览数据表
```

### 6. 后台调度与数据缓存

参考 `data_get.md` 第七至九节已验证的调度模式。

#### 6.1 定时调度（APScheduler）

调度器实现：`backend/services/scheduler.py`，使用 `BackgroundScheduler`（Asia/Shanghai 时区）。

| 任务 | Job ID | 频率 | 数据源 | 操作 |
|------|--------|------|--------|------|
| 实时行情抓取 | `realtime_prices` | 交易时段每15分钟 | 腾讯财经 API | 更新 `holdings.price/amount/amount_cny` + 写入 `price_cache` |
| 财务基本面更新 | `financial_fundamentals` | 每日 7:00 / 19:00 | 腾讯 + yfinance | 增量写入 `stock_info_cache` + 运行穿透计算 |
| 行业/爬虫数据 | `industry_crawler` | 每日 6:00 / 20:00 | 中证指数 + 汇率 | ETF映射 + 成分股 + 沪深300基准 + 汇率更新 |

**交易时段判断**：
- A股：9:30-15:00 CST
- 美股：21:30-4:00 CST（次日）
- 非交易时段自动跳过，手动触发可强制执行（`force=True`）

**调度器管理**：
- `start_scheduler()` / `stop_scheduler()` — 应用启动/关闭时自动调用
- `GET /api/scheduler/status` — 查看调度器运行状态和下次执行时间
- `POST /api/scheduler/trigger/{job_id}` — 手动触发指定任务

#### 6.2 数据缓存策略

**数据拉取与页面加载分离**：页面加载只从数据库读取，数据拉取由定时任务完成。

| 表名 | 用途 | 更新频率 | 策略 |
|------|------|----------|------|
| `StockInfoCache` | 公司行情/财务 JSON 缓存 | 15 分/日 | 增量拉取，有旧值保旧 |
| `PriceCache` | 日频复权价格 | 交易时段每15分钟 | 腾讯 K 线 → yfinance fallback |
| `StockFinancial` | 财务指标 | 每日 | 多源合并，来源标注 |
| `SecurityMaster` | 证券基础信息（币种/类型） | 从持仓同步 | 一次配置，手动更新 |
| `SecurityTypeConfig` | 证券类型配置（精度等） | 手动管理 | 种子数据初始化 |

**幂等设计**：所有写入按 `(code, date)` 唯一，避免重复。

#### 6.3 数据质量保障

- 所有外部调用有 `safe_collect()` 异常保护
- 缓存优先保留已有值：`if new is None and old exists: keep old`
- 前端表格缺数据显示 "-" 不崩溃
- `verify_data_integrity()` 校验不同来源数据差异（PE 差异 > 5 告警）

### 7. 前端（React + Vite + CSS Variables）

技术栈：React 18 + Vite + CSS Variables 主题系统（非 ECharts）。

页面布局（侧边栏导航）：

```
┌──────────────────────────────────────────────────────────────┐
│  侧边栏导航                                                   │
│  [总览] [分析] [分析师] [交易] [关注] [数据] [设置]            │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  总览页面（OverviewPanel）：                                   │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  组合概览卡片（总资产/持仓数/大类分布）                     │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │  持仓表格                                               │ │
│  │  [CNY][USD][CAD] 货币切换                                │ │
│  │  类型筛选：[全部][债券][A股基][黄金][QDII][港股][美股]...  │ │
│  │  ┌──────┬──────┬────┬────┬────┬──────┬──────┬──────┐    │ │
│  │  │ 代码  │ 名称  │类型 │占比 │数量 │单价·原│金额·原│金额·本│   │ │
│  │  ├──────┼──────┼────┼────┼────┼──────┼──────┼──────┤    │ │
│  │  │ 86px │300px │46px│62px│78px│ 88px │100px │108px │    │ │
│  │  └──────┴──────┴────┴────┴────┴──────┴──────┴──────┘    │ │
│  │  合计行：筛选后金额·本合计 + 占比                          │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  分析页面（AnalysisPanel）：                                   │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  穿透表 / 产业链分析 / 增长分层 / 估值分析                  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  数据浏览页面（DataBrowser）：                                 │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  外层标签页：[持仓] [行情] [分析] [基础]                   │ │
│  │  内层标签页：[持仓] [证券基础] [证券类型配置] ...           │ │
│  │  分页表格：每页50条，上一页/下一页                          │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

**前端特性**：
- 货币切换：顶栏 [CNY] [USD] [CAD] 按钮，实时转换金额
- 类型筛选：表格上方筛选按钮，与表格数据联动
- 数字右对齐 + 等宽字体（GeistMono）
- 千分位格式化（`toLocaleString('en-US')`）
- 金额整数化（`Math.round()`）
- 单价精度从 `SecurityTypeConfig` 读取（基金4位，股票2位）
- 金额·原 = 数量 × 单价（原币种），带原币种符号

### 7. 债券与黄金处理

- **债券**（006829、014856、006517）：不穿透，建模为 `年化 2% 收益率 + 微小波动（日波动 ±0.05%）` 的类现金资产
- **黄金**（008701、008702、002611）：通过 Tushare/AkShare 获取黄金现货价格跟踪，以 AU99.99 为基准

---

## 项目结构

```
D:\claude_code_project\PortfolioM\
├── backend/
│   ├── main.py              # FastAPI 入口 + uvicorn + 调度器启停
│   ├── config.py            # 配置（DB URL、Tushare Token 等）
│   ├── database.py          # SQLAlchemy 引擎 + session
│   ├── models.py            # ORM 模型定义（11 个模型）
│   ├── schemas.py           # Pydantic 请求/响应模型
│   ├── crawlers/
│   │   ├── etf_index.py              # ETF→指数映射爬虫
│   │   ├── exchange_rates.py         # 汇率获取 + 币种推断
│   │   ├── index_constituents.py     # 指数成分股爬虫（中证指数公司）
│   │   └── price_data.py             # 统一行情入口（腾讯首选 → yfinance/akshare 回退）
│   ├── services/
│   │   ├── csi300.py                 # 沪深300 数据工具
│   │   ├── growth_bucketer.py        # 增长分层器
│   │   ├── importer.py               # Excel 导入 + 价格填充
│   │   ├── penetration.py            # 穿透计算引擎
│   │   └── scheduler.py              # APScheduler 定时调度
│   ├── data/
│   │   ├── seed_constituents.csv     # 成分股种子数据
│   │   └── seed_financials.csv       # 财务种子数据
│   └── requirements.txt
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── App.jsx                   # 主布局 + 侧边栏导航
│       ├── App.css                   # 全局样式 + 主题变量
│       ├── api.js                    # API 客户端（30+ 端点）
│       ├── components/
│       │   ├── OverviewPanel.jsx     # 总览（持仓表格 + 货币切换 + 类型筛选）
│       │   ├── AnalysisPanel.jsx     # 分析（穿透/产业链/增长/估值）
│       │   ├── DataBrowser.jsx       # 数据浏览（双重标签页 + 分页）
│       │   ├── TradingPanel.jsx      # 交易
│       │   ├── WatchPanel.jsx        # 关注
│       │   ├── SettingsPanel.jsx     # 设置
│       │   └── StyleGallery.jsx      # 样式展示
│       └── main.jsx
├── SPEC.md                  # 项目设计文档
├── data_get.md              # 数据获取方式总览
└── portfolio.db             # SQLite 数据库
```

---

## 文档

- [`data_get.md`](./data_get.md) — 全项目数据源总览（腾讯 / yfinance / akshare / Tushare / 中证指数 / 天天基金）
- [`docs/project-status.md`](./docs/project-status.md) — 当前实现状态与待办任务清单
- [`docs/superpowers/specs/2026-06-17-fund-penetration-analysis-design.md`](./docs/superpowers/specs/2026-06-17-fund-penetration-analysis-design.md) — 基金穿透与行业聚合设计（spec §1–§7 已实现）
- [`docs/reference-price-system.md`](./docs/reference-price-system.md) — 价格缓存、交易日历、价格拉取脚本参考
- [`docs/howto-backfill-6m-prices.md`](./docs/howto-backfill-6m-prices.md) — 如何为持仓和穿透证券补全 6 个月收盘价

---

## Acceptance Criteria

1. ✅ 从 Excel 导入持仓数据，存储到数据库
2. ✅ 自动识别每只基金跟踪的指数
3. ✅ 爬取中证指数成分股，生成底层股票穿透表（权重%）
4. ✅ 美股穿透：QQQ → 纳斯达克100成分股；个股直接纳入
5. ✅ 债券类（20% 仓位）简化为年化 2% 类现金资产
6. ✅ 黄金按 AU99.99 价格跟踪
7. ✅ 产业链分析（上中下游）+ 沪深300 对比
8. ✅ 增长分层（沪深300 加权分位法）+ 沪深300 对比
9. ✅ 估值分析（TTM PE + Forecast PE 1Y/2Y）+ 沪深300 对比
10. ✅ 竞争格局分析（寡头/竞争）
11. ✅ 价格趋势跟踪（多股叠加走势图）
12. ✅ 前端仪表盘可视化所有数据

---

## Testing Plan

| 层 | 测试内容 | 数量 |
|----|----------|------|
| 穿透引擎 | 单基金 → 成分股权重分解验证 | +5 |
| 分析引擎 | 增长分层边界值、沪深300阈值计算 | +8 |
| API | 每个端点 HTTP 200/404/422 | +15 |
| 爬虫 | Mock 指数页面 HTML，验证解析 | +5 |
| 前端 | 组件渲染 + API mock 数据 | +8 |
| E2E | 完整流程: 导入→爬取→穿透→分析→显示 | +3 |

---

## Rollback Plan

- 数据库：SQLite 文件可随时删除重建；保留原始 Excel 作为数据源原点
- 爬虫失败：使用上次成功缓存，不阻塞 API
- 前端构建：`git revert` + 重新部署

---

## Effort Estimate

| 模块 | 时间 |
|------|------|
| 数据模型 + 数据库 + 导入 | ~1h |
| ETF→指数映射 + 穿透引擎 | ~1.5h |
| 爬虫系统（3 类爬虫） | ~2h |
| 财务数据采集（Tushare + yfinance） | ~1.5h |
| 分析引擎（产业链 + 增长分层 + 估值） | ~2h |
| API 路由（12 个端点） | ~1.5h |
| 前端框架搭建 + 组件开发 | ~3h |
| 债券/黄金简化模型 | ~0.5h |
| 沪深300 对比模块 | ~1h |
| 集成测试 + Bug 修复 | ~1h |
| **合计** | **~14h** |

---

## Files Reference

| File | Purpose |
|------|---------|
| `backend/models.py` | 所有 ORM 模型（11 个：SecurityMaster, SecurityTypeConfig, Holding, ExchangeRate, Fund, IndexConstituent, StockFinancial, PriceCache, StockInfoCache, PenetrationResult, Csi300Baseline） |
| `backend/main.py` | FastAPI 入口 + 30 个 API 端点 + 调度器启停 |
| `backend/schemas.py` | Pydantic 请求/响应模型 |
| `backend/services/penetration.py` | 核心穿透算法 |
| `backend/services/penetration_v2.py` | 新版快照穿透（weight-invariant recompute） |
| `backend/services/aggregation.py` | 多维度聚合 + CSI300 对比 + 估值时序 |
| `backend/services/data_version.py` | 月度快照版本解析 |
| `backend/services/trading_calendar.py` | CN/HK/US/OF 交易日历 |
| `backend/services/code_map.py` | API 代码映射表 |
| `backend/services/price_filler.py` | 缺失 current_price 补全 |
| `backend/services/drillable_funds.py` | 可下钻基金卡片数据 |
| `backend/services/growth_bucketer.py` | 沪深300加权分位增长分层 |
| `backend/services/scheduler.py` | APScheduler 定时调度（3 类任务） |
| `backend/services/importer.py` | Excel 导入 + 价格填充 |
| `backend/services/csi300.py` | 沪深300 数据工具 |
| `backend/scripts/import_fund_index_map.py` | 基金→指数映射导入 |
| `backend/scripts/import_index_constituents.py` | 指数成分股快照导入 |
| `backend/scripts/import_a_share_financials.py` | A 股估值快照导入 |
| `backend/scripts/import_hk_share_financials.py` | 港股估值快照导入 |
| `backend/scripts/import_399673_cons.py` | 创业板 50 官方权重导入 |
| `backend/scripts/import_common.py` | 导入通用工具（价格解析、动态指标） |
| `backend/scripts/pull_history_prices.py` | A+H 底层证券 6 个月历史价拉取 |
| `backend/scripts/pull_fund_nav.py` | OF 基金历史净值拉取 |
| `backend/scripts/fill_prices_tencent.py` | 腾讯行情补全 current_price CLI |
| `backend/scripts/crawl_index_official.py` | 官方指数成分股下载（进行中） |
| `backend/crawlers/price_data.py` | 统一行情入口（腾讯/yfinance/akshare） |
| `backend/crawlers/exchange_rates.py` | 汇率获取 + 币种推断 |
| `backend/crawlers/etf_index.py` | ETF→指数映射爬虫 |
| `backend/crawlers/index_constituents.py` | 中证指数成分股爬虫 |
| `frontend/src/components/OverviewPanel.jsx` | 总览（持仓表格 + 货币切换 + 类型筛选） |
| `frontend/src/components/DataBrowser.jsx` | 数据浏览（双重标签页 + 分页） |
| `frontend/src/components/AnalysisPanel.jsx` | 分析（穿透/产业链/增长/估值） |
| `frontend/src/api.js` | API 客户端（30+ 端点封装） |

---

## Out of Scope

- 复杂衍生品（期权、期货）分析
- 机器学习预测模型
- 多账户/多组合管理
- 用户认证系统
- 自动化交易执行
- 云端部署配置（用户自行处理）
- Wind 终端数据集成（无 Wind Python 包，用 Tushare + 腾讯证券API 替代）

---

## Related

- Tushare Pro API: https://tushare.pro/document/2
- yfinance: https://github.com/ranaroussi/yfinance
- 中证指数公司: https://www.csindex.com.cn
- 天天基金网: https://fund.eastmoney.com

---

# Current Implementation Status (2026-06-13)

## What Has Been Built

### Backend
- **FastAPI** with 19+ endpoints on `http://localhost:8014`
- **SQLAlchemy ORM** with 12 tables: `Holding`, `Fund`, `IndexConstituent`, `StockFinancial`, `PenetrationResult`, `PriceCache`, `StockInfoCache`, `ExchangeRate`, `Csi300Baseline`, `SecurityMaster`, `SecurityTypeConfig`
- **Data sources**:
  - **Tencent Finance API** (qt.gtimg.cn) — US stocks real-time price (NVDA, GOOGL, QQQ, etc.)
  - **akshare** `fund_open_fund_info_em` — Chinese fund NAV (单位净值, fallback 累计净值 for QDII/bond funds)
  - **akshare** `currency_boc_safe` — PBoC middle rate (with /100 unit conversion)
  - **yfinance** — US stock fundamentals fallback
- **Holdings data model** (post-currency refactor):
  - `quantity` (份额/股数)
  - `price` (latest price in original currency)
  - `currency` (CNY/USD/HKD)
  - `amount` (原始金额)
  - `amount_cny` (折算CNY金额)
- **Currency system**:
  - `ExchangeRate` table (rate_date, from_currency, to_currency, rate, source)
  - Daily crawl from PBoC (中间价)
  - `GET /api/exchange-rates` (list), `POST /api/exchange-rates/update` (manual)
  - `GET /api/holdings/converted?target=CNY/USD/CAD` (converted amounts)
- **Penetration engine**: weight recursive decomposition
- **Growth bucketer**: CSI300 weighted quantile cutoffs (33%/66%)
- **Industry chain analyzer**: 申万行业 → 上/中/下游 + 金融 + 其他

### Frontend (React 18)
- **Stack**: Vite + React 18, ECharts via `echarts-for-react`
- **Design system**: x.ai inspired (dark brutalist minimalism)
  - Background `#1f2228`, white text on dark
  - GeistMono monospace font globally
  - 0px border radius, no shadows, no gradients
  - 8px/12px/16px spacing scale
  - Inverse hover (dim to 0.5 opacity)
- **6 pages with sidebar nav**:
  1. **总览** (Overview) — KPI grid 6×1, asset pie, radar chart (组合 vs 沪深300), top-10 chips, full holdings table
  2. **分析** (Analysis) — dimension tabs (产业链/增长/估值/竞争/风险/相关/景气) + chart + drill-down
  3. **分析师** (Analyst) — placeholder (待开发)
  4. **交易** (Trading) — 2 modes (form/upload Excel), FX transfers, cost completion
  5. **关注** (Watch) — security search, weight-based watch list
  6. **设置** (Settings) — password protection, data management

### Holdings Table (Current Spec)

| Column | Width | Alignment | Source |
|--------|-------|-----------|--------|
| 代码 | 86px | left | security_code |
| 名称 | 300px (fixed) | left | security_name (ellipsis) |
| 类型 | 46px | center | asset_type (truncated to 3 chars) |
| 占比 | 62px | right | amount_local / total |
| 数量 | 78px | right | quantity (integer with comma) |
| 单价·原 | 88px | right | price (原币种, e.g. CNY 4.31) |
| 金额·原 | 100px | right | amount_original (原币种, e.g. USD 7233) |
| 金额·本 | 108px | right | amount_local in selected currency (bold) |

- **Type filter** (类型筛选) above the table — user-selectable category filter
- **Currency switcher** (CNY/USD/CAD) top-right
- **Summary row** at bottom showing 合计 (total amount·本)
- **Number formatting**:
  - Integers only (no decimals)
  - Thousands separator (`,`)
  - Tabular figures (monospace font)
  - Brightness hierarchy: 类型 muted, 单价 muted, 金额·本 bright
- **Sortable** by any column

## Design Patterns Applied
- **x.ai-style brutalist minimalism** (chose over deep purple, ice frost, cyber neon)
- **Monospace everywhere** (GeistMono via CSS)
- **0px border-radius**, thin white borders at low opacity
- **No emojis** — SVG icons (Heroicons style) in sidebar
- **Token-based theming** via CSS variables

## Files
- `backend/main.py` — FastAPI entry
- `backend/models.py` — ORM models (12 tables)
- `backend/services/importer.py` — Excel import + price fill + currency conversion
- `backend/services/penetration.py` — Penetration engine
- `backend/services/growth_bucketer.py` — Growth bucketing + industry chain
- `backend/services/csi300.py` — CSI300 baseline calculator
- `backend/crawlers/price_data.py` — Unified price crawler (Tencent/yfinance)
- `backend/crawlers/exchange_rates.py` — PBoC rate crawler + currency inference
- `backend/crawlers/etf_index.py` — ETF→index mapping (known + crawl)
- `frontend/src/App.jsx` — App shell with 6-tab sidebar
- `frontend/src/App.css` — Design system (x.ai style)
- `frontend/src/index.css` — CSS variables (dark theme)
- `frontend/src/api.js` — API client
- `frontend/src/components/OverviewPanel.jsx` — Overview + Holdings table
- `frontend/src/components/AnalysisPanel.jsx` — Multi-dim analysis
- `frontend/src/components/TradingPanel.jsx` — Trading page
- `frontend/src/components/WatchPanel.jsx` — Watch securities
- `frontend/src/components/SettingsPanel.jsx` — Settings page

## Running Services
- Backend: `http://localhost:8014` (uvicorn, port 8014)
- Frontend: `http://localhost:5173` (vite dev, proxy → 8014)

## Resumed From
This update follows a 3-hour pause. Pending task: continue iteration on Holdings table design per user feedback.
