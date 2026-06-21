"""指数成分股爬虫

从中证指数公司网站爬取指数成分股列表及权重。
"""
from datetime import date
from lxml import etree
from sqlalchemy.orm import Session

from models import IndexConstituent
from config import TENCENT_USER_AGENT, CSI_CONSTITUENTS_URL
from crawlers._http import em_get
from services.dedup import already_persisted_today


def crawl_constituents(
    index_code: str,
    db: Session,
    as_of: date | None = None,
    *,
    force: bool = False,
) -> list[dict]:
    """
    爬取指定指数的成分股列表。
    返回 [{stock_code, stock_name, weight, market_cap}, ...]

    force: True 跳过 dedup 守门（手动强制重拉）
    """
    if as_of is None:
        as_of = date.today()

    # dedup: 该指数今天已抓过则跳过
    if not force and already_persisted_today(
        db, IndexConstituent, "as_of_date",
        filter_col="index_code", filter_val=index_code,
    ):
        from logging import getLogger
        getLogger(__name__).info("指数 %s 今日已抓，跳过", index_code)
        return []

    headers = {"User-Agent": TENCENT_USER_AGENT}
    url = CSI_CONSTITUENTS_URL.format(index_code)

    try:
        resp = em_get(url, headers=headers, timeout=30)
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

    resp = em_get(url, headers=headers, timeout=30)
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
