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
