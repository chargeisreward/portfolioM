"""资讯数据服务层 (DB 写入 + 去重). 详见 SKILL.md §5-7. """
from __future__ import annotations
import logging
from datetime import date as _date, datetime
from sqlalchemy.orm import Session
from models import (
    _title_hash, Announcement, GlobalFlashNews,
    HotStockSignal, ResearchReport, StockNews,
)
logger = logging.getLogger(__name__)


def upsert_global_flash_news(db, rows):
    if not rows: return 0
    written = 0
    for r in rows:
        try:
            title = r.get("title", "")
            if not title: continue
            th = _title_hash(title)
            ex = db.query(GlobalFlashNews).filter(GlobalFlashNews.title_hash == th).first()
            if ex:
                ex.summary = r.get("summary") or ex.summary
                ex.source = r.get("source") or ex.source
                ex.url = r.get("url") or ex.url
                if r.get("published_at"): ex.published_at = r["published_at"]
            else:
                db.add(GlobalFlashNews(
                    title_hash=th, title=title[:500],
                    summary=(r.get("summary") or "")[:5000],
                    source=r.get("source") or "",
                    url=r.get("url") or "",
                    published_at=r.get("published_at") or datetime.utcnow(),
                    fetched_at=datetime.utcnow()))
                written += 1
        except Exception as e:
            logger.warning("写入全球快讯失败: %s", e)
    db.commit()
    return written


def list_global_flash_news(db, limit=50, hours=None):
    q = db.query(GlobalFlashNews)
    if hours:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        q = q.filter(GlobalFlashNews.published_at >= cutoff)
    return q.order_by(GlobalFlashNews.published_at.desc()).limit(limit).all()


def upsert_stock_news(db, code, rows):
    if not rows: return 0
    written = 0
    for r in rows:
        try:
            title = r.get("title", "")
            if not title: continue
            th = _title_hash(title)
            ex = db.query(StockNews).filter(
                StockNews.stock_code == code, StockNews.title_hash == th).first()
            if not ex:
                db.add(StockNews(
                    stock_code=code, title_hash=th, title=title[:500],
                    summary=(r.get("summary") or "")[:5000],
                    source=r.get("source") or "",
                    url=r.get("url") or "",
                    published_at=r.get("published_at") or datetime.utcnow(),
                    fetched_at=datetime.utcnow()))
                written += 1
        except Exception as e:
            logger.warning("写入个股新闻失败: %s", e)
    db.commit()
    return written


def list_stock_news(db, code, limit=30):
    return db.query(StockNews).filter(
        StockNews.stock_code == code
    ).order_by(StockNews.published_at.desc()).limit(limit).all()


def upsert_announcements(db, code, rows):
    if not rows: return 0
    written = 0
    for r in rows:
        try:
            aid = r.get("announcement_id", "")
            if not aid: continue
            ex = db.query(Announcement).filter(
                Announcement.stock_code == code,
                Announcement.announcement_id == aid).first()
            if not ex:
                pd = r.get("publish_date") or ""
                if isinstance(pd, str):
                    try: pd = datetime.strptime(pd, "%Y-%m-%d").date()
                    except ValueError: pd = _date.today()
                db.add(Announcement(
                    stock_code=code, org_id=r.get("org_id") or "",
                    announcement_id=aid, title=(r.get("title") or "")[:500],
                    announcement_type=r.get("announcement_type") or "",
                    publish_date=pd, url=r.get("url") or "",
                    fetched_at=datetime.utcnow()))
                written += 1
        except Exception as e:
            logger.warning("写入公告失败: %s", e)
    db.commit()
    return written


def list_announcements(db, code, limit=30):
    return db.query(Announcement).filter(
        Announcement.stock_code == code
    ).order_by(Announcement.publish_date.desc()).limit(limit).all()


def upsert_research_reports(db, code, rows):
    if not rows: return 0
    written = 0
    for r in rows:
        try:
            ic = r.get("info_code", "")
            if not ic: continue
            ex = db.query(ResearchReport).filter(ResearchReport.info_code == ic).first()
            pd = r.get("publish_date") or ""
            if isinstance(pd, str):
                try: pd = datetime.strptime(pd, "%Y-%m-%d").date()
                except ValueError: pd = _date.today()
            if not ex:
                db.add(ResearchReport(
                    info_code=ic, stock_code=code, stock_name="",
                    title=(r.get("title") or "")[:500],
                    org_name=r.get("org_name") or "",
                    publish_date=pd, rating=r.get("rating") or "",
                    predict_eps_current=r.get("predict_eps_current"),
                    predict_eps_next=r.get("predict_eps_next"),
                    industry=r.get("industry") or "",
                    fetched_at=datetime.utcnow()))
                written += 1
            else:
                if r.get("rating") and not ex.rating: ex.rating = r["rating"]
                if r.get("predict_eps_current") is not None and ex.predict_eps_current is None:
                    ex.predict_eps_current = r["predict_eps_current"]
                if r.get("predict_eps_next") is not None and ex.predict_eps_next is None:
                    ex.predict_eps_next = r["predict_eps_next"]
        except Exception as e:
            logger.warning("写入研报失败: %s", e)
    db.commit()
    return written


def list_research_reports(db, code, limit=30):
    return db.query(ResearchReport).filter(
        ResearchReport.stock_code == code
    ).order_by(ResearchReport.publish_date.desc()).limit(limit).all()


def upsert_hot_stocks(db, signal_date, rows):
    if not rows: return 0
    written = 0
    for r in rows:
        try:
            code = r.get("stock_code", "")
            if not code: continue
            ex = db.query(HotStockSignal).filter(
                HotStockSignal.signal_date == signal_date,
                HotStockSignal.stock_code == code).first()
            if not ex:
                db.add(HotStockSignal(
                    signal_date=signal_date, stock_code=code,
                    stock_name=r.get("stock_name") or "",
                    close=r.get("close"), change_pct=r.get("change_pct"),
                    turnover_pct=r.get("turnover_pct"), amount=r.get("amount"),
                    dde_net=r.get("dde_net"),
                    market=r.get("market") or "",
                    reason_tags=r.get("reason_tags") or "",
                    rank=r.get("rank"), fetched_at=datetime.utcnow()))
                written += 1
        except Exception as e:
            logger.warning("写入热点失败: %s", e)
    db.commit()
    return written


def list_hot_stocks(db, signal_date=None, limit=50):
    q = db.query(HotStockSignal)
    if signal_date: q = q.filter(HotStockSignal.signal_date == signal_date)
    return q.order_by(HotStockSignal.rank.asc()).limit(limit).all()
