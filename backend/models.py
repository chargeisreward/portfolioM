"""SQLAlchemy ORM models for PortfolioM"""
from datetime import date, datetime
from sqlalchemy import Column, Integer, Float, String, Date, DateTime, Text, JSON
from database import Base
import enum


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
    """底层股票穿透表"""
    __tablename__ = "penetration_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
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
    """用户关注清单（自选股）"""
    __tablename__ = "watchlist"

    code = Column(String(20), primary_key=True)        # 证券代码（含后缀）
    name = Column(String(100), nullable=True)          # 名称（首次添加时从行情拉取）
    market = Column(String(10), nullable=True)         # 美股/A股/港股
    industry = Column(String(50), nullable=True)       # 行业（首次添加时拉取）
    weight = Column(Float, default=5.0)                # 用户设定的权重 %
    added_at = Column(DateTime, default=datetime.utcnow)
