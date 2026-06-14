"""ETF → 跟踪指数映射爬虫

从天天基金网获取基金详情，自动识别每只基金跟踪的指数代码和名称。
"""
import re
import httpx
from sqlalchemy.orm import Session
from models import Fund
from config import TENCENT_USER_AGENT


# Known fund → index mapping (hardcoded lookups for common cases)
# These can be verified and expanded over time
KNOWN_INDEX_MAP: dict[str, tuple[str, str]] = {
    "007818": ("990001", "中证全指半导体产品与设备指数"),
    "022500": ("990001", "中证全指半导体产品与设备指数"),
    "007466": ("399967", "中证军工指数"),
    "160424": ("399673", "创业板50指数"),
    "110020": ("000300", "沪深300指数"),
    "007339": ("000300", "沪深300指数"),
    "011609": ("000688", "上证科创板50指数"),
    "011613": ("000688", "上证科创板50指数"),
    "022726": ("000688", "上证科创板芯片指数"),
    "024263": ("931012", "中证机器人指数"),
    "022742": ("000510", "中证A500指数"),
    "159326": ("990001", "中证全指半导体产品与设备指数"),
    "159870": ("990015", "中证细分化工产业主题指数"),
    "018388": ("931583", "中证港股通科技指数"),
    "021142": ("931226", "中证港股通央企综合指数"),
    "019524": ("NDX",  "纳斯达克100指数"),
    "019525": ("NDX",  "纳斯达克100指数"),
    "006479": ("NDX",  "纳斯达克100指数"),
    "015311": ("ACWI", "MSCI全球科技指数"),
    "007722": ("SPX",  "标普500指数"),
}


def crawl_fund_index_map(db: Session) -> int:
    """
    遍历数据库中所有基金，尝试获取跟踪指数信息。
    先用内置已知映射，缺失的尝试从天基金网爬取。
    """
    from models import Holding, AssetType
    funds = db.query(Holding).filter(
        Holding.asset_type.in_([
            AssetType.A_SHARE_EQUITY.value,
            AssetType.A_SHARE_ETF.value,
            AssetType.HK_EQUITY.value,
            AssetType.QDII_EQUITY.value,
        ])
    ).all()

    fund_codes = set()
    for f in funds:
        code = f.security_code
        if code.endswith(".OF"):
            fund_codes.add(code.replace(".OF", ""))
        elif code.endswith(".SZ") or code.endswith(".SH"):
            fund_codes.add(code.replace(".SZ", "").replace(".SH", ""))

    updated = 0
    for short_code in sorted(fund_codes):
        # Check known mapping first
        idx_code, idx_name = KNOWN_INDEX_MAP.get(short_code, (None, None))

        if idx_code is None:
            # Try crawling from fund.eastmoney.com
            try:
                idx_code, idx_name = _crawl_from_eastmoney(short_code)
            except Exception:
                pass

        if idx_code:
            # Upsert into funds table
            existing = db.query(Fund).filter(Fund.code == short_code + ".OF").first()
            if not existing:
                existing = Fund(code=short_code + ".OF")
                db.add(existing)

            existing.tracking_index_code = idx_code
            existing.tracking_index_name = idx_name
            existing.is_etf_link = 1
            existing.updated_at = __import__("datetime").datetime.utcnow()
            updated += 1

    db.commit()
    return updated


def _crawl_from_eastmoney(fund_code: str) -> tuple[str | None, str | None]:
    """Crawl fund detail page from eastmoney to find tracking index"""
    headers = {"User-Agent": TENCENT_USER_AGENT}
    url = f"https://fund.eastmoney.com/{fund_code}.html"
    resp = httpx.get(url, headers=headers, timeout=15)
    resp.encoding = "utf-8"

    # Try to find the tracking index info in the page
    # Pattern 1: "跟踪标的：XXX指数"
    patterns = [
        r"跟踪标的[：:]\s*([^，,<\n]+(?:指数|ETF))",
        r"跟踪指数[：:]\s*([^，,<\n]+)",
        r"本基金[跟踪]*\s*([^\s，,]+指数)",
    ]

    for pat in patterns:
        m = re.search(pat, resp.text)
        if m:
            idx_name = m.group(1).strip()
            # Try to find index code nearby
            code_match = re.search(r"(\d{6})", idx_name)
            if not code_match:
                # Try to find index code in page
                code_match = re.search(r"指数代码[：:]\s*(\d{6})", resp.text)
            idx_code = code_match.group(1) if code_match else f"UNKNOWN_{fund_code}"
            return idx_code, idx_name

    return None, None
