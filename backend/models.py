"""SQLAlchemy ORM models for PortfolioM"""
from datetime import date, datetime
from sqlalchemy import Column, Integer, Float, String, Date, DateTime, Text, JSON, Boolean, UniqueConstraint, BigInteger, ForeignKey
from sqlalchemy.dialects.sqlite import INTEGER as SQLITE_INTEGER
from database import Base
import enum

# BigInteger 在 SQLite 上需降级为 INTEGER 才能触发 autoincrement
BigIntPK = BigInteger().with_variant(SQLITE_INTEGER, "sqlite")


class AssetType(str, enum.Enum):
    """资产大类"""
    A_SHARE_EQUITY = "a_share_equity"       # A股联接基金
    A_SHARE_ETF = "a_share_etf"             # A股交易所ETF
    HK_EQUITY = "hk_equity"                 # 港股基金
    US_STOCK = "us_stock"                   # 美股个股
    US_ETF = "us_etf"                       # 美股ETF
    BOND = "bond"                           # 债券基金
    GOLD = "gold"                           # 黄金基金
    COMMODITY = "commodity"                 # 商品
    QDII_EQUITY = "qdii_equity"             # QDII股票基金
    QDII_BOND = "qdii_bond"                 # QDII债券
    CASH = "cash"                           # 现金/货基


class ChainPosition(str, enum.Enum):
    """产业链位置"""
    UPSTREAM = "upstream"
    MIDSTREAM = "midstream"
    DOWNSTREAM = "downstream"
    OTHER = "other"
    FINANCIAL = "financial"  # 金融类


class GrowthTier(str, enum.Enum):
    """增长层级"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class Competition(str, enum.Enum):
    """竞争格局"""
    MONOPOLY = "monopoly"       # 垄断
    OLIGOPOLY = "oligopoly"     # 寡头
    COMPETITIVE = "competitive" # 充分竞争
    UNKNOWN = "unknown"


class Currency(str, enum.Enum):
    """币种"""
    CNY = "CNY"  # 人民币
    USD = "USD"  # 美元
    HKD = "HKD"  # 港币
    CAD = "CAD"  # 加元


class SecurityMaster(Base):
    """证券基础表：维护每只证券的原币种、类型等基础属性"""
    __tablename__ = "security_master"

    security_code = Column(String(20), primary_key=True)
    security_name = Column(String(100))
    currency = Column(String(10), default="CNY")     # 原币种（上市地交易币种）
    asset_type = Column(String(20))                   # 证券类型
    type2 = Column(String(20), nullable=True)         # 主题类型2（红利/新兴产业/黄金）
    exchange = Column(String(20), nullable=True)      # 交易所
    updated_at = Column(DateTime, default=datetime.utcnow)


class SecurityTypeConfig(Base):
    """证券类型配置表：不同类型证券的显示精度等配置"""
    __tablename__ = "security_type_config"

    asset_type = Column(String(20), primary_key=True)     # 证券类型代码
    type_name = Column(String(50))                         # 类型中文名
    price_precision = Column(Integer, default=2)           # 单价显示小数位数
    amount_precision = Column(Integer, default=0)          # 金额显示小数位数
    sort_order = Column(Integer, default=0)                # 排序权重
    updated_at = Column(DateTime, default=datetime.utcnow)


# ---------- 持仓 ----------

class Holding(Base):
    """组合持仓（从 Excel 导入）"""
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, default=1, index=True)
    security_code = Column(String(20), nullable=False, index=True)
    security_name = Column(String(100))
    quantity = Column(Float, default=0.0)        # 持仓数量
    price = Column(Float, nullable=True)         # 最新单价（计价币种）
    currency = Column(String(10), default="CNY") # 计价币种（CNY/USD/HKD）
    amount = Column(Float, default=0.0)          # 持仓金额 = quantity × price（原始币种）
    amount_cny = Column(Float, default=0.0)      # 折算人民币金额
    asset_type = Column(String(20), default=AssetType.A_SHARE_EQUITY.value)
    import_batch = Column(String(20))            # 导入批次标记
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- 汇率表 ----------

class ExchangeRate(Base):
    """汇率（每日从人行爬取）"""
    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rate_date = Column(Date, nullable=False, index=True)        # 汇率日期
    from_currency = Column(String(10), nullable=False)           # 原始币种（USD/HKD）
    to_currency = Column(String(10), nullable=False)             # 目标币种（CNY/CAD）
    rate = Column(Float, nullable=False)                         # 汇率：1 from = rate to
    source = Column(String(50), default="PBOC")                  # 来源：PBOC
    created_at = Column(DateTime, default=datetime.utcnow)


class AccessAttempt(Base):
    """按 IP 累计失败登录次数 + 锁定到期时间"""
    __tablename__ = "access_attempts"

    ip = Column(String(64), primary_key=True)
    # 不同窗口的失败计数（每次失败全部 +1）
    fails_1h = Column(Integer, default=0)    # 达到 10 → 禁 1h
    fails_1d = Column(Integer, default=0)    # 达到 20 → 禁 1d
    fails_1mo = Column(Integer, default=0)   # 达到 30 → 禁 1mo
    fails_1y = Column(Integer, default=0)    # 达到 40 → 禁 1y
    banned_until = Column(DateTime, nullable=True)  # 锁定到期时间
    last_fail_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)


class AccessSession(Base):
    """成功的 session token（前端存 localStorage）"""
    __tablename__ = "access_sessions"

    token = Column(String(64), primary_key=True)
    ip = Column(String(64), nullable=True)
    user_id = Column(BigInteger, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


# ---------- ETF/基金基础信息 ----------

class Fund(Base):
    """基金/ETF 基础信息"""
    __tablename__ = "funds"

    code = Column(String(20), primary_key=True)
    name = Column(String(100))
    asset_type = Column(String(20))
    tracking_index_code = Column(String(20), nullable=True)   # 跟踪指数代码
    tracking_index_name = Column(String(100), nullable=True)  # 跟踪指数名称
    is_etf_link = Column(Integer, default=0)  # 是否为ETF联接基金
    updated_at = Column(DateTime, default=datetime.utcnow)


# ---------- 指数成分股（爬虫结果） ----------

class IndexConstituent(Base):
    """指数成分股"""
    __tablename__ = "index_constituents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    index_code = Column(String(20), index=True, nullable=False)
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(100))
    weight = Column(Float, default=0.0)         # 权重 %
    market_cap = Column(Float, nullable=True)    # 总市值（亿元）
    as_of_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- 个股财务数据 ----------

class StockFinancial(Base):
    """个股财务数据"""
    __tablename__ = "stock_financials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100))
    ttm_pe = Column(Float, nullable=True)
    revenue_growth = Column(Float, nullable=True)     # 营收增速(%)
    profit_growth = Column(Float, nullable=True)      # 净利润增速(%)
    profit_growth_fy1 = Column(Float, nullable=True)  # 预测FY1增速
    profit_growth_fy2 = Column(Float, nullable=True)  # 预测FY2增速
    market_cap = Column(Float, nullable=True)          # 总市值（亿元）
    industry_sw = Column(String(50), nullable=True)    # 申万行业
    chain_position = Column(String(20), nullable=True) # 产业链位置
    competition = Column(String(20), nullable=True)    # 竞争格局
    data_source = Column(String(50), nullable=True)    # 数据来源
    as_of_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- 价格缓存（参考 data_get.md §7.1） ----------

class PriceCache(Base):
    """日频复权价格缓存"""
    __tablename__ = "price_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, index=True)
    trade_date = Column(Date, nullable=False)
    open_px = Column(Float, nullable=True)
    close_px = Column(Float, nullable=True)
    high_px = Column(Float, nullable=True)
    low_px = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    source = Column(String(20))  # tencent / yfinance / akshare
    created_at = Column(DateTime, default=datetime.utcnow)


class StockInfoCache(Base):
    """行情/财务数据 JSON 缓存（参考 data_get.md §7.1）"""
    __tablename__ = "stock_info_cache"

    stock_code = Column(String(20), primary_key=True)
    stock_name = Column(String(100))
    data_json = Column(JSON)       # {pe_ttm, market_cap_b, revenue_b, ...}
    updated_at = Column(DateTime, default=datetime.utcnow)


# ---------- 穿透结果 ----------

class PenetrationResult(Base):
    """底层股票穿透表（按 user 隔离的个人衍生数据 — 2026-06-24）"""
    __tablename__ = "penetration_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)  # 多用户隔离
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100))
    penetration_weight = Column(Float, default=0.0)    # 穿透后权重 %
    penetration_amount = Column(Float, default=0.0)     # 穿透后金额（CNY）
    asset_category = Column(String(20))                  # 归属大类
    industry_sw = Column(String(50), nullable=True)
    chain_position = Column(String(20), nullable=True)
    growth_tier = Column(String(20), nullable=True)
    competition = Column(String(20), nullable=True)
    ttm_pe = Column(Float, nullable=True)
    forecast_pe_1y = Column(Float, nullable=True)
    forecast_pe_2y = Column(Float, nullable=True)
    revenue_growth = Column(Float, nullable=True)
    profit_growth = Column(Float, nullable=True)
    calculated_at = Column(DateTime, default=datetime.utcnow)


# ---------- 沪深300 分析基准 ----------

class Csi300Baseline(Base):
    """沪深300 分析基准数据"""
    __tablename__ = "csi300_baselines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dimension = Column(String(30), nullable=False)   # industry_chain / growth / valuation
    category = Column(String(30))                     # upstream/high_growth/pe_range...
    weight = Column(Float, default=0.0)
    value = Column(Float, nullable=True)
    as_of_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Watchlist(Base):
    """用户关注清单（自选股）— PK 改 (user_id, code)"""
    __tablename__ = "watchlist"

    user_id = Column(BigInteger, primary_key=True, nullable=False, default=1)
    code = Column(String(20), primary_key=True, nullable=False)        # 证券代码（含后缀）
    name = Column(String(100), nullable=True)          # 名称（首次添加时从行情拉取）
    market = Column(String(10), nullable=True)         # 美股/A股/港股
    industry = Column(String(50), nullable=True)       # 行业（首次添加时拉取）
    weight = Column(Float, default=5.0)                # 用户设定的权重 %
    added_at = Column(DateTime, default=datetime.utcnow)


# ---------- 交易日历 ----------

class TradingCalendar(Base):
    """各主要市场交易日历。CN=沪深(共用)、HK=港交所、US=NYSE/NASDAQ、OF=场外基金。
    来源：CN 来自 chinese-calendar 库；HK/US 来自官方公开 holiday schedule 静态表。
    is_trading=False 表示周末或法定节假日。"""
    __tablename__ = "trading_calendar"
    __table_args__ = (UniqueConstraint('market', 'date', name='ux_trading_calendar_market_date'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    market = Column(String(8), nullable=False, index=True)     # CN | HK | US | OF
    date = Column(Date, nullable=False, index=True)
    is_trading = Column(Boolean, nullable=False)              # True=开市；False=休市（周末/节假日）
    source = Column(String(40), nullable=False)               # chinese_calendar / hkex_static / nyse_static / akshare / fallback
    note = Column(String(100), nullable=True)                  # 节假日名（国庆/Thanksgiving/...）
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- API 代码映射 ----------

class ApiCodeMap(Base):
    """不同 API 拉取同一证券时进行的代码转换。
    code_in = 持仓里写法的标准 code（如 'NVDA'、'159326.SZ'、'006829.OF'）
    api_strategy = API 策略 id（tencent_kline / tencent_quote / akshare_fund_nav / ...）
    code_out = 该 API 实际调用时用的 code（如 'usNVDA.OQ'、'006829'）
    """
    __tablename__ = "api_code_map"
    __table_args__ = (UniqueConstraint('code_in', 'api_strategy', name='ux_api_code_map_in_api'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    code_in = Column(String(30), nullable=False, index=True)   # 标准代码（持仓写法）
    api_strategy = Column(String(40), nullable=False, index=True)  # API 策略 id
    code_out = Column(String(60), nullable=False)              # 该 API 调用时用的代码
    market = Column(String(8), nullable=True, index=True)      # CN/HK/US/OF（统计用）
    note = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# Fund Penetration & Industry Aggregation (spec §1)
# All tables are keyed on `as_of_date` so each monthly import is a snapshot.
# ============================================================================


class FundIndexMap(Base):
    """基金→指数追踪关系（来自 sourceData/YYYYMM数据/基金-指数.xlsx）。"""
    __tablename__ = "fund_index_map"

    fund_code = Column(String(20), primary_key=True)
    fund_name = Column(String(80))
    benchmark_formula = Column(String(500))           # 业绩比较基准原文
    index_code = Column(String(20), nullable=False)
    index_name = Column(String(80))
    as_of_date = Column(Date, primary_key=True)
    source = Column(String(40), default="excel")
    note = Column(String(200))


class IndexConstituentSnapshot(Base):
    """指数成分股快照（多时点）。"""
    __tablename__ = "index_constituent_snapshot"
    __table_args__ = (
        UniqueConstraint("as_of_date", "index_code", "stock_code",
                         name="ux_ics_asof_index_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    as_of_date = Column(Date, nullable=False, index=True)
    index_code = Column(String(20), nullable=False, index=True)
    index_name = Column(String(80))
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80))
    exchange = Column(String(8))                       # SSE/SZSE/HKEx
    weight = Column(Float)                             # 5/29 权重 % (akshare 拉取)
    baseline_price = Column(Float)                      # 5/29 当日收盘价（fund_drill_snapshot 算法用）


class FundDrillSnapshot(Base):
    """公共下钻截面快照（按 fund × as_of_date 批量生成 — 2026-06-24 引入）。

    算法（spec §3.2 weight-invariant + 用户 2026-06-24 补丁）：
      1. 读取 index_constituents[最近月份] 的成分股 + 权重 weight + baseline_price
      2. 取每只成分股 T 日 current_price；缺失用 T-1 价（视为停牌）
      3. 校验：当日获得收盘价的成分股占比 >= 95% 才生成
      4. 权重和 = Σ(weight)，若 < 100%，差额 × 95% 加入「下钻-现金」(weight_deficit_cash 列)
      5. shares_equivalent = fund_price × 0.95 × (weight/100) / current_price
         其中 fund_price = Holding.price（fund 当日基金价格）
         5% 现金部分：cash_per_unit = fund_price × 0.05（不需要存股票级记录）
      6. QDII 港股：current_price 是原币（HKD），shares_eq 用原币价算
         current_price_cny = current_price × fx_rate（5% 现金也用 CNY）
      7. user 层：user_drill[s] = Holding.quantity × shares_equivalent[s]
         user_cash = Holding.quantity × fund_price × 0.05

    公共数据，不带 user_id。
    """
    __tablename__ = "fund_drill_snapshot"
    __table_args__ = (
        UniqueConstraint("fund_code", "as_of_date", "stock_code",
                         name="ux_fds_code_date_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    fund_code = Column(String(20), nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80))
    weight_pct = Column(Float, nullable=False)            # 指数权重 %（来自 index_constituents）
    baseline_price = Column(Float)                          # 成分股基准日收盘价（原币）
    current_price = Column(Float, nullable=False)           # 成分股当日收盘价（原币，缺失时用 T-1）
    shares_equivalent = Column(Float, nullable=False)       # 1 份基金对应股数（基于原币价）
    is_stale_price = Column(Boolean, default=False)          # True=current_price 是 T-1 替补
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # === 2026-06-24 双币种补丁 ===
    currency = Column(String(8))                             # 原币 (HKD / CNY / USD)
    current_price_cny = Column(Float)                        # 本币 (CNY) 收盘价
    cny_currency = Column(String(8), default='CNY')           # 本币币种
    fx_rate = Column(Float)                                  # 当日汇率 (to_cny)
    fx_date = Column(Date)                                   # 汇率日期
    weight_deficit_cash = Column(Float, default=0)            # 权重和 < 100% 时的差额×95% 划入下钻-现金


class FundDailyNav(Base):
    """基金每日净值 (用于下钻精确定价)。

    每只可下钻基金 + 每个交易日 一行：
      nav = 单位净值 (per-share unit net value)
      accumulated_nav = 累计净值
      fund_shares_outstanding = 基金份额 (可以从 holding 表反推)
    数据源: akshare fund_open_fund_info_em
    """
    __tablename__ = "fund_daily_nav"
    __table_args__ = (
        UniqueConstraint("fund_code", "trade_date", name="ux_fdn_code_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    fund_code = Column(String(20), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    nav = Column(Float)                                # 单位净值
    accumulated_nav = Column(Float)
    daily_return = Column(Float)                       # 日涨幅%
    source = Column(String(40), default="akshare")
    created_at = Column(DateTime, default=datetime.utcnow)
    weight = Column(Float)                             # 权重 %
    source = Column(String(40))
    created_at = Column(DateTime, default=datetime.utcnow)


class AShareFinancialSnapshot(Base):
    """A 股估值快照（含动态 PE/PB/PS = 当前价相对 baseline 的调整）。"""
    __tablename__ = "a_share_financial_snapshot"
    __table_args__ = (
        UniqueConstraint("as_of_date", "stock_code",
                         name="ux_asfs_asof_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)  # 多用户隔离
    as_of_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80))
    pe_ttm = Column(Float)
    pb_mrq = Column(Float)
    ps_ttm = Column(Float)
    dividend_yield = Column(Float)
    market_cap = Column(Float)                          # 亿元
    eps_fy1 = Column(Float)
    eps_fy2 = Column(Float)
    # 申万 2021 (L1-L4) — A 股 Excel 现在提供 4 级
    swy_l1 = Column(String(40))
    swy_l2 = Column(String(60))
    swy_l3 = Column(String(60))
    swy_l4 = Column(String(60))
    # 中证 2021 (L1-L4)
    csi_l1 = Column(String(40))
    csi_l2 = Column(String(60))
    csi_l3 = Column(String(60))
    csi_l4 = Column(String(60))
    # 战略新兴产业 (L1-L3) — A 股 3 级
    se_l1 = Column(String(60))
    se_l2 = Column(String(60))
    se_l3 = Column(String(60))
    se_l4 = Column(String(60))                          # A 股无 L4，留空
    # Backward compat
    industry_sw = Column(String(50))
    baseline_price = Column(Float)                      # as_of_date 当日收盘价
    current_price = Column(Float)                       # 上一交易日收盘价
    current_price_date = Column(Date)
    pe_ttm_dynamic = Column(Float)
    pb_mrq_dynamic = Column(Float)
    ps_ttm_dynamic = Column(Float)
    source = Column(String(40))
    created_at = Column(DateTime, default=datetime.utcnow)


class HKShareFinancialSnapshot(Base):
    """港股估值快照（A 股字段 + 申万 L1-L3 + 中证 L1-L4）。"""
    __tablename__ = "hk_share_financial_snapshot"
    __table_args__ = (
        UniqueConstraint("as_of_date", "stock_code",
                         name="ux_hkfs_asof_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)  # 多用户隔离
    as_of_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80))
    pe_ttm = Column(Float)
    pb_mrq = Column(Float)
    ps_ttm = Column(Float)
    dividend_yield = Column(Float)
    market_cap = Column(Float)
    eps_fy1 = Column(Float)
    eps_fy2 = Column(Float)
    # 申万 2021 (L1-L3)
    swy_l1 = Column(String(40))
    swy_l2 = Column(String(60))
    swy_l3 = Column(String(60))
    swy_l4 = Column(String(60))
    # 中证 2021 (L1-L4)
    csi_l1 = Column(String(40))
    csi_l2 = Column(String(60))
    csi_l3 = Column(String(60))
    csi_l4 = Column(String(60))
    # 战略新兴产业 (L1-L4)
    se_l1 = Column(String(60))
    se_l2 = Column(String(60))
    se_l3 = Column(String(60))
    se_l4 = Column(String(60))
    # Backward compat aliases
    industry_l1 = Column(String(40))
    industry_l2 = Column(String(60))
    industry_l3 = Column(String(60))
    industry_l4 = Column(String(60))
    # 战略新兴产业 (L1-L4) — HK 4 级
    se_l1 = Column(String(60))
    se_l2 = Column(String(60))
    se_l3 = Column(String(60))
    se_l4 = Column(String(60))
    baseline_price = Column(Float)
    current_price = Column(Float)
    current_price_date = Column(Date)
    pe_ttm_dynamic = Column(Float)
    pb_mrq_dynamic = Column(Float)
    ps_ttm_dynamic = Column(Float)
    source = Column(String(40))
    created_at = Column(DateTime, default=datetime.utcnow)


class PenetrationSnapshot(Base):
    """基金下钻结果（按持仓单只下钻）。"""
    __tablename__ = "penetration_snapshot"
    __table_args__ = (
        UniqueConstraint("as_of_date", "holding_code", "stock_code",
                         name="ux_pnsnap"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    as_of_date = Column(Date, nullable=False, index=True)
    holding_code = Column(String(20), nullable=False, index=True)
    holding_name = Column(String(80))
    holding_amount_cny = Column(Float)
    index_code = Column(String(20))
    index_name = Column(String(80))
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80))
    weight_at_baseline = Column(Float)                  # 5/29 权重 %
    amount_cny_dynamic = Column(Float)                  # 权重不变 × 当日股价调整
    amount_cny_static = Column(Float)                   # 仅按权重×金额（无价格调整）
    baseline_price = Column(Float)
    current_price = Column(Float)
    calculation_method = Column(String(20), default="weight_invariant")
    created_at = Column(DateTime, default=datetime.utcnow)


class FullHoldingSnapshot(Base):
    """全持仓快照（下钻基金 + 直接股票 + 不下钻基金 + 现金）。
    一只成分股可能来自多只上层基金，所以 UK 只约束 (as_of_date, stock_code,
    source_holding_code)，每行都是唯一的"这只股票从这个来源获得 X 金额"。
    同一只股票的多个来源会被合并到 full_holding_view 中。

    行业字段存储 7 套体系：
      swy_l1/l2/l3 (申万 2021, 3 级) + csi_l1/l2/l3/l4 (中证 2021, 4 级)
    行业聚合时通过 dropdown 选择其中一套。
    """
    __tablename__ = "full_holding_snapshot"
    __table_args__ = (
        UniqueConstraint("as_of_date", "stock_code", "source_holding_code",
                         name="ux_fhsnap"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)  # 多用户隔离
    as_of_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80))
    source_type = Column(String(20))                     # drilled_fund | direct_stock | undrilled_fund | cash
    source_holding_code = Column(String(100))            # 上层持仓 code
    amount_cny = Column(Float)
    # 7 industry systems
    swy_l1 = Column(String(40), default="其他")
    swy_l2 = Column(String(60), default="其他")
    swy_l3 = Column(String(60), default="其他")
    swy_l4 = Column(String(60), default="其他")
    csi_l1 = Column(String(40), default="其他")
    csi_l2 = Column(String(60), default="其他")
    csi_l3 = Column(String(60), default="其他")
    csi_l4 = Column(String(60), default="其他")
    se_l1 = Column(String(60), default="其他")
    se_l2 = Column(String(60), default="其他")
    se_l3 = Column(String(60), default="其他")
    se_l4 = Column(String(60), default="其他")
    # Backward compat (deprecated)
    industry_l1 = Column(String(40), default="其他")
    industry_l2 = Column(String(60), default="其他")
    chain_position = Column(String(20), default="other")
    growth_tier = Column(String(20), default="unknown")
    competition = Column(String(20), default="unknown")
    pe_ttm_dynamic = Column(Float)
    pb_mrq_dynamic = Column(Float)
    ps_ttm_dynamic = Column(Float)
    eps_fy1 = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class AggregationCache(Base):
    """聚合结果缓存（按维度、行业、组合/CSI300 双源）。"""
    __tablename__ = "aggregation_cache"
    __table_args__ = (
        UniqueConstraint("as_of_date", "scope", "dimension", "key", "user_id",
                         name="ux_aggcache"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, default=2)  # 多用户隔离（2026-06-24）
    as_of_date = Column(Date, nullable=False, index=True)
    scope = Column(String(20))                            # portfolio | csi300
    dimension = Column(String(20))                        # l1 | l2 | chain | growth_tier | competition | all
    key = Column(String(80))                              # 电子 / 中游 / high / _total
    stock_count = Column(Integer)
    amount_cny = Column(Float)
    weight_pct = Column(Float)
    virtual_earnings = Column(Float)                      # Σ(amount / pe)
    pe_weighted = Column(Float)                            # virtual_earnings / amount
    pe_simple_avg = Column(Float)                          # 简单算术平均（仅供对照）
    pb_weighted = Column(Float)
    ps_weighted = Column(Float)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Csi300ConstituentSnapshot(Base):
    """沪深300 成分股快照（单独表，便于基准对比）。"""
    __tablename__ = "csi300_constituent_snapshot"
    __table_args__ = (
        UniqueConstraint("as_of_date", "stock_code",
                         name="ux_csi300snap"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)  # 多用户隔离
    as_of_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80))
    swy_l1 = Column(String(40), default="其他")
    swy_l2 = Column(String(60), default="其他")
    swy_l3 = Column(String(60), default="其他")
    swy_l4 = Column(String(60), default="其他")
    csi_l1 = Column(String(40), default="其他")
    csi_l2 = Column(String(60), default="其他")
    csi_l3 = Column(String(60), default="其他")
    csi_l4 = Column(String(60), default="其他")
    se_l1 = Column(String(60), default="其他")
    se_l2 = Column(String(60), default="其他")
    se_l3 = Column(String(60), default="其他")
    se_l4 = Column(String(60), default="其他")
    industry_l1 = Column(String(40), default="其他")
    industry_l2 = Column(String(60), default="其他")
    chain_position = Column(String(20), default="other")
    growth_tier = Column(String(20), default="unknown")
    competition = Column(String(20), default="unknown")
    weight = Column(Float)
    baseline_price = Column(Float)
    current_price = Column(Float)
    current_price_date = Column(Date)
    pe_ttm_dynamic = Column(Float)
    pb_mrq_dynamic = Column(Float)
    ps_ttm_dynamic = Column(Float)
    source = Column(String(40))
    created_at = Column(DateTime, default=datetime.utcnow)


class AggregationTimeseries(Base):
    """组合 / CSI300 估值指标日时序（点击展开趋势图）。"""
    __tablename__ = "aggregation_timeseries"
    __table_args__ = (
        UniqueConstraint("calc_date", "scope", "user_id", name="ux_aggts"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, default=2)  # 多用户隔离（2026-06-24）
    calc_date = Column(Date, nullable=False, index=True)
    business_date = Column(Date, nullable=False)          # 该 calc_date 使用的业务日期
    scope = Column(String(20))                            # portfolio | csi300
    stock_count = Column(Integer)
    total_amount_cny = Column(Float)
    virtual_earnings = Column(Float)
    pe_weighted = Column(Float)
    pb_weighted = Column(Float)
    ps_weighted = Column(Float)
    price_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# 资讯数据 (a-stock-data skill §5-7)
# ============================================================================


def _title_hash(title: str) -> str:
    """稳定标题去重键：md5 前 12 位。跨运行/跨表保持一致。"""
    import hashlib
    if not title:
        return ""
    return hashlib.md5(title.strip().encode("utf-8")).hexdigest()[:12]


class GlobalFlashNews(Base):
    """东财 7×24 全球快讯（替代已下线财联社快讯；skill §5.3）"""
    __tablename__ = "global_flash_news"
    __table_args__ = (UniqueConstraint('title_hash', name='ux_gfn_title'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    title_hash = Column(String(12), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    summary = Column(Text)
    source = Column(String(50))
    url = Column(String(500))
    published_at = Column(DateTime, nullable=False, index=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class StockNews(Base):
    """个股新闻（东财 search-api-web；skill §5.1）"""
    __tablename__ = "stock_news"
    __table_args__ = (UniqueConstraint('stock_code', 'title_hash', name='ux_news_code_title'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, index=True)
    title_hash = Column(String(12), nullable=False)
    title = Column(String(500), nullable=False)
    summary = Column(Text)
    source = Column(String(50))
    url = Column(String(500))
    published_at = Column(DateTime, nullable=False, index=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class Announcement(Base):
    """巨潮公告全文检索（cninfo；skill §7.1）"""
    __tablename__ = "announcements"
    __table_args__ = (UniqueConstraint('stock_code', 'announcement_id', name='ux_ann_code_id'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, index=True)
    org_id = Column(String(20))
    announcement_id = Column(String(40), nullable=False)
    title = Column(String(500), nullable=False)
    announcement_type = Column(String(50))
    publish_date = Column(Date, nullable=False, index=True)
    url = Column(String(500))
    fetched_at = Column(DateTime, default=datetime.utcnow)


class ResearchReport(Base):
    """东财研报列表（reportapi.eastmoney.com；skill §2.1）"""
    __tablename__ = "research_reports"
    __table_args__ = (UniqueConstraint('info_code', name='ux_rr_info'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    info_code = Column(String(40), nullable=False)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100))
    title = Column(String(500), nullable=False)
    org_name = Column(String(100))
    publish_date = Column(Date, nullable=False, index=True)
    rating = Column(String(20))                       # 买入/增持/中性/...
    predict_eps_current = Column(Float, nullable=True)  # 当年 EPS 预测
    predict_eps_next = Column(Float, nullable=True)     # 明年 EPS 预测
    industry = Column(String(80))
    pdf_path = Column(String(500))                    # 本地相对路径，None 表示未下载
    pdf_downloaded_at = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class HotStockSignal(Base):
    """同花顺当日强势股 + 题材归因 reason（skill §3.1，零鉴权 73ms）"""
    __tablename__ = "hot_stock_signals"
    __table_args__ = (UniqueConstraint('signal_date', 'stock_code', name='ux_hss_date_code'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(100))
    close = Column(Float)
    change_pct = Column(Float)
    turnover_pct = Column(Float)
    amount = Column(Float)
    dde_net = Column(Float)             # 大单净量
    market = Column(String(10))         # 沪/深/北
    reason_tags = Column(Text)          # "+" 分隔的题材归因
    rank = Column(Integer)              # 当日涨幅排名
    fetched_at = Column(DateTime, default=datetime.utcnow)


# ============================================================================
# 分析师研究数据（来自 researcher/ 目录）
# ============================================================================


class AnalystCompanyReport(Base):
    """公司研究报告（DOCX 解析后的 6 段式框架）"""
    __tablename__ = "analyst_company_report"
    __table_args__ = (UniqueConstraint('stock_code', name='ux_acr_code'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80), nullable=True)
    exchange = Column(String(8), nullable=True)
    section_1_market_focus = Column(Text, nullable=True)      # 市场关注
    section_2_core_competence = Column(Text, nullable=True)   # 核心竞争力
    section_3_supply_demand = Column(Text, nullable=True)     # 供需格局
    section_4_marginal_change = Column(Text, nullable=True)   # 边际变化
    section_5_valuation = Column(Text, nullable=True)         # 估值
    section_6_risk = Column(Text, nullable=True)              # 风险
    raw_text = Column(Text, nullable=True)
    source_file = Column(String(500), nullable=True)
    parsed_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AnalystIndustryChain(Base):
    """产业链总结报告（Markdown 原文）"""
    __tablename__ = "analyst_industry_chain"
    __table_args__ = (UniqueConstraint('chain_name', name='ux_aic_chain'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    chain_name = Column(String(80), nullable=False, index=True)
    narrative_md = Column(Text, nullable=True)
    source_file = Column(String(500), nullable=True)
    parsed_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AnalystIndustryChainCompany(Base):
    """产业链公司清单（Markdown 表格行）"""
    __tablename__ = "analyst_industry_chain_company"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chain_name = Column(String(80), nullable=False, index=True)
    chain_position = Column(String(80), nullable=False)
    sub_segment = Column(String(80), nullable=True)
    company_name = Column(String(80), nullable=False)
    stock_code = Column(String(20), nullable=True, index=True)
    market_cap_range = Column(String(40), nullable=True)
    relevance_stars = Column(Integer, nullable=True)
    relevance_reason = Column(Text, nullable=True)
    latest_progress = Column(Text, nullable=True)
    order_visibility = Column(String(40), nullable=True)
    earnings_elasticity = Column(String(40), nullable=True)
    customer_onboarding = Column(String(200), nullable=True)
    extra_json = Column(JSON, nullable=True)   # 存放未映射列（技术路线/产品适配点等）
    source_file = Column(String(500), nullable=True)
    row_index = Column(Integer, nullable=True)
    parsed_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================================
# Multi-user / Permissions (auth-upgrade M1)
# ============================================================================

class User(Base):
    __tablename__ = "users"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(64), nullable=True)
    is_advisor = Column(Boolean, nullable=False, default=False, index=True)
    is_admin = Column(Boolean, nullable=False, default=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserRelation(Base):
    __tablename__ = "user_relations"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    advisor_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    client_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="PENDING")
    initiator_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("advisor_user_id", "client_user_id", name="uq_relation"),)


class IndexClassification(Base):
    __tablename__ = "index_classification"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    index_code = Column(String(32), unique=True, nullable=False, index=True)
    index_name = Column(String(128), nullable=True)
    category = Column(String(64), nullable=True)
    theme = Column(String(64), nullable=True)
    benchmark_formula = Column(Text, nullable=True)
    source = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DataGapReport(Base):
    __tablename__ = "data_gap_report"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    gap_type = Column(String(32), nullable=False, index=True)
    stock_code = Column(String(32), nullable=True, index=True)
    index_code = Column(String(32), nullable=True, index=True)
    as_of_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="OPEN")
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)


class HoldingImportLog(Base):
    __tablename__ = "holding_import_log"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    import_source = Column(String(16), nullable=False)
    file_name = Column(String(255), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    imported_at = Column(DateTime, default=datetime.utcnow, index=True)
