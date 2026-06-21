"""东财研报爬虫 (a-stock-data skill §2.1)

- eastmoney_reports(code) -- 拉研报列表 (含 PDF infoCode)
- download_report_pdf(info_code, target_dir) -- 下载单份研报 PDF

注意:
- PDF 下载必须带 Referer: https://data.eastmoney.com/, 否则 403
- 东财报告接口为公开 JSON, 走 em_get() 节流
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from config import DATA_DIR_PDF, EM_PDF_URL, EM_REPORT_API_URL
from crawlers._http import em_get
from models import ResearchReport
from database import SessionLocal
from services.dedup import already_updated_today

logger = logging.getLogger(__name__)

_FILENAME_SAFE = re.compile(r'[\/:*?"<>|\r\n\t]')


def _safe_filename(s: str, max_len: int = 80) -> str:
    s = _FILENAME_SAFE.sub("_", s).strip().strip(".")
    return s[:max_len] if len(s) > max_len else s


def fetch_reports(code: str, max_pages: int = 3, *, force: bool = False) -> list[dict]:
    """拉取指定股票的研报列表 (含 PDF infoCode, 评级, 预测 EPS).

    force: True 跳过 dedup 守门（手动强制重拉）
    返回: [{info_code, title, org_name, publish_date, rating,
             predict_eps_current, predict_eps_next, industry}, ...]
    """
    # dedup: 今天已有该股研报则跳过
    if not force:
        db = SessionLocal()
        try:
            if already_updated_today(
                db, ResearchReport, "fetched_at",
                filter_col="stock_code", filter_val=code,
            ):
                logger.info("股票 %s 今日研报已抓，跳过", code)
                return []
        finally:
            db.close()

    all_rows: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*",
            "pageSize": "100",
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": "2000-01-01",
            "endTime": "2030-01-01",
            "pageNo": str(page),
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "code": code,
            "rcode": "",
            "p": str(page),
            "pageNum": str(page),
            "pageNumber": str(page),
        }
        headers = {"Referer": "https://data.eastmoney.com/"}
        resp = em_get(EM_REPORT_API_URL, params=params, headers=headers, timeout=30)
        if resp is None or resp.status_code != 200:
            logger.warning("研报列表请求失败 code=%s page=%d", code, page)
            break
        try:
            d = resp.json()
        except Exception as e:
            logger.warning("研报列表 JSON 解析失败 code=%s: %s", code, e)
            break
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            all_rows.append({
                "info_code": r.get("infoCode", ""),
                "title": (r.get("title") or "").strip(),
                "org_name": r.get("orgSName") or "",
                "publish_date": (r.get("publishDate") or "")[:10],
                "rating": r.get("emRatingName") or "",
                "predict_eps_current": _safe_float(r.get("predictThisYearEps")),
                "predict_eps_next": _safe_float(r.get("predictNextYearEps")),
                "industry": r.get("indvInduName") or "",
            })
        total_page = d.get("TotalPage") or 1
        if page >= total_page:
            break

    # 去重 (按 info_code)
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in all_rows:
        if r["info_code"] and r["info_code"] not in seen:
            seen.add(r["info_code"])
            deduped.append(r)
    return deduped


def download_report_pdf(
    info_code: str,
    title: str = "",
    publish_date: str = "",
    org_name: str = "",
    target_dir: str | None = None,
) -> str | None:
    """下载单份研报 PDF, 返回保存路径 (相对 DATA_DIR_PDF) 或 None."""
    if not info_code:
        return None
    base = Path(target_dir) if target_dir else DATA_DIR_PDF
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)

    fname = "_".join(filter(None, [publish_date, org_name, _safe_filename(title)])) + ".pdf"
    target = base / fname
    if target.exists() and target.stat().st_size >= 1024:
        return str(target.relative_to(DATA_DIR_PDF.parent) if DATA_DIR_PDF.parent in target.parents else target)

    url = EM_PDF_URL.format(info_code=info_code)
    headers = {"Referer": "https://data.eastmoney.com/"}
    resp = em_get(url, headers=headers, timeout=60)
    if resp is None or resp.status_code != 200:
        logger.warning("研报 PDF 下载失败 info_code=%s status=%s",
                       info_code, resp.status_code if resp else "None")
        return None
    if len(resp.content) < 1024:
        logger.warning("研报 PDF 内容过小 info_code=%s size=%d", info_code, len(resp.content))
        return None
    target.write_bytes(resp.content)
    return str(target)


def _safe_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
