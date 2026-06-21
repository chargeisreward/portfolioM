"""Application configuration"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "backend" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Database - SQLite for local dev, override via env for cloud
# Cloud: 优先读 POSTGRES_CONNECTION_STRING (zeabur 自动注入) → DATABASE_URL → SQLite 本地
DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "portfolio.db"))
DATABASE_URL = (
    os.environ.get("POSTGRES_CONNECTION_STRING")
    or os.environ.get("POSTGRES_URI")
    or os.environ.get("DATABASE_URL")
    or f"sqlite:///{DB_PATH}"
)

# Tushare token (optional, for deeper A-share financials)
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# Data sources
TENCENT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={}"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# ETF index mapping - fund.eastmoney.com
FUND_DETAIL_URL = "https://fund.eastmoney.com/{}.html"
FUND_PORTFOLIO_URL = "https://fund.eastmoney.com/pingzhongdata/{}.js"

# CSI index
CSI_INDEX_URL = "https://www.csindex.com.cn/zh-CN/indices/index-detail/{}#indices"
CSI_CONSTITUENTS_URL = "https://www.csindex.com.cn/csindex/constituents/list?indexCode={}"

# CSI 300 code
CSI300_CODE = "000300"

# Growth bucketing weights
GROWTH_HIGH_THRESHOLD = 0.33   # Top 33% weight = high growth
GROWTH_MED_THRESHOLD = 0.66   # Next 33% = medium growth

# ============================================================================
# 资讯数据源常量 (a-stock-data skill §1-7)
# ============================================================================

EASTMONTH_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
THS_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"

# 东财全局资讯 (skill §5.3 — 替代已下线财联社快讯)
EM_GLOBAL_NEWS_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"

# 东财个股新闻 JSONP (skill §5.1)
EM_STOCK_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"

# 巨潮公告 (skill §7.1)
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_ORGID_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"

# 东财研报 (skill §2.1)
EM_REPORT_API_URL = "https://reportapi.eastmoney.com/report/list"
EM_PDF_URL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"

# 同花顺热点 (skill §3.1)
THS_HOT_URL = "http://zx.10jqka.com.cn/event/api/getharden/date/{date}/orderby/date/orderway/desc/charset/GBK/"

# 东财数据中心 (skill §3.5/3.6/4.x 共用)
EM_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 限流节流 (skill "数据源优先级 & 东财防封")
# 东财有风控：≥1s 间隔 + 随机抖动；批量筛选可调到 1.5-2s
EM_MIN_INTERVAL = 1.0
# 腾讯不封 IP 但也别太快：~3 req/s 安全
TENCENT_MIN_INTERVAL = 0.3
# 同花顺零鉴权但别太快：~2 req/s 安全
THS_MIN_INTERVAL = 0.5

# 资讯数据本地缓存
DATA_DIR_INFO = DATA_DIR / "info_cache"
DATA_DIR_PDF = DATA_DIR / "research_pdfs"
DATA_DIR_INFO.mkdir(parents=True, exist_ok=True)
DATA_DIR_PDF.mkdir(parents=True, exist_ok=True)
