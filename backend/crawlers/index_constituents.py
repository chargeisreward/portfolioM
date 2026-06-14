"""指数成分股爬虫

从中证指数公司网站爬取指数成分股列表及权重。
"""
import httpx
from datetime import date
from lxml import etree
from sqlalchemy.orm import Session

from models import IndexConstituent
from config import TENCENT_USER_AGENT, CSI_CONSTITUENTS_URL


def crawl_constituents(index_code: str, db: Session, as_of: date | None = None) -> list[dict]:
    """
    爬取指定指数的成分股列表。
    返回 [{stock_code, stock_name, weight, market_cap}, ...]
    """
    if as_of is None:
        as_of = date.today()

    headers = {"User-Agent": TENCENT_USER_AGENT}
    url = CSI_CONSTITUENTS_URL.format(index_code)

    try:
        resp = httpx.get(url, headers=headers, timeout=30)
        resp.encoding = "utf-8"
        data = resp.json()
    except Exception:
        # Fallback: try HTML page parsing
        return _crawl_from_html(index_code, db, as_of)

    constituents = []
    if isinstance(data, list):
        for item in data:
            constituents.append({
                "stock_code": item.get("securityCode", ""),
                "stock_name": item.get("securityName", ""),
                "weight": float(item.get("weight", 0) or 0),
                "market_cap": float(item.get("marketCap", 0) or 0),
            })
    elif isinstance(data, dict) and "list" in data:
        for item in data["list"]:
            constituents.append({
                "stock_code": item.get("securityCode", ""),
                "stock_name": item.get("securityName", ""),
                "weight": float(item.get("weight", 0) or 0),
                "market_cap": float(item.get("marketCap", 0) or 0),
            })

    # Save to DB
    _save_constituents(index_code, constituents, as_of, db)
    return constituents


def _crawl_from_html(index_code: str, db: Session, as_of: date) -> list[dict]:
    """Fallback: parse HTML page for constituents"""
    from config import CSI_INDEX_URL
    headers = {"User-Agent": TENCENT_USER_AGENT}
    url = CSI_INDEX_URL.format(index_code)

    resp = httpx.get(url, headers=headers, timeout=30)
    html = etree.HTML(resp.content)

    constituents = []
    rows = html.xpath('//table[contains(@class, "constituents")]//tr')
    for row in rows[1:]:  # skip header
        cols = row.xpath("./td/text()")
        if len(cols) >= 3:
            constituents.append({
                "stock_code": cols[0].strip(),
                "stock_name": cols[1].strip(),
                "weight": float(cols[2].strip().replace("%", "")),
                "market_cap": 0,
            })

    _save_constituents(index_code, constituents, as_of, db)
    return constituents


def _save_constituents(index_code: str, constituents: list[dict], as_of: date, db: Session):
    """Save constituents to database"""
    # Delete old data for this index
    db.query(IndexConstituent).filter(
        IndexConstituent.index_code == index_code
    ).delete()

    for c in constituents:
        record = IndexConstituent(
            index_code=index_code,
            stock_code=c["stock_code"],
            stock_name=c["stock_name"],
            weight=c["weight"],
            market_cap=c["market_cap"],
            as_of_date=as_of,
        )
        db.add(record)

    db.commit()
