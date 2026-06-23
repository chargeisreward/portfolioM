"""数据补足检测 — 扫描 3 类缺口写入 data_gap_report
1. stock_report_gap：每个 user 持仓的穿透后 ≥0.8% 但无分析师报告
2. index_constituent_gap：上月月底缺指数构成快照
3. index_classification_gap：指数基金缺分类
"""
import calendar
from datetime import date, datetime
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    User, Holding, FundIndexMap, IndexClassification,
    IndexConstituentSnapshot, AnalystCompanyReport, DataGapReport, AssetType,
)


def _prev_month_end(today: date) -> date:
    """上月月底（如 today=2026-07-05 → 2026-06-30）"""
    if today.month == 1:
        return date(today.year - 1, 12, 31)
    last_day = calendar.monthrange(today.year, today.month - 1)[1]
    return date(today.year, today.month - 1, last_day)


def _gap_exists(db: Session, **kwargs) -> bool:
    """检查 OPEN 状态的同类缺口是否已存在（去重）"""
    q = db.query(DataGapReport).filter(DataGapReport.status == "OPEN")
    for k, v in kwargs.items():
        q = q.filter(getattr(DataGapReport, k) == v)
    return db.query(q.exists()).scalar()


def detect_all_gaps(db: Session, today: Optional[date] = None) -> dict:
    """主入口：扫描 3 类缺口。返回 {inserted: N, types: {...}}"""
    today = today or date.today()
    inserted: list[DataGapReport] = []

    users = db.query(User).filter(User.is_active == True).all()

    # 1. stock_report_gap
    for u in users:
        total_est = db.query(func.coalesce(func.sum(Holding.amount_cny), 0)).filter(
            Holding.user_id == u.id
        ).scalar() or 0
        if total_est <= 0:
            continue
        for h in db.query(Holding).filter(Holding.user_id == u.id).all():
            if not h.amount_cny:
                continue
            # 简单版：直接用直接持仓的 amount_cny / total
            ratio = h.amount_cny / total_est if total_est > 0 else 0
            if ratio < 0.008:
                continue
            if h.asset_type not in (AssetType.A_SHARE_EQUITY.value, AssetType.HK_EQUITY.value):
                continue
            code = h.security_code
            has_report = db.query(AnalystCompanyReport).filter(
                AnalystCompanyReport.stock_code == code
            ).first()
            if has_report:
                continue
            if _gap_exists(db, user_id=u.id, gap_type="stock_report", stock_code=code):
                continue
            g = DataGapReport(
                user_id=u.id, gap_type="stock_report", stock_code=code,
                description=f"{h.security_name or code} 占比 {ratio:.2%} ≥ 0.8%，无分析师报告",
            )
            db.add(g)
            inserted.append(g)

    # 2. index_constituent_gap — 上月月底快照缺失
    last_month_end = _prev_month_end(today)
    # 全部已映射的指数（去重）
    index_codes = set(
        row[0] for row in db.query(FundIndexMap.index_code)
        .filter(FundIndexMap.index_code.isnot(None)).distinct().all()
    )
    for ic in index_codes:
        has_snap = db.query(IndexConstituentSnapshot).filter(
            IndexConstituentSnapshot.index_code == ic,
            IndexConstituentSnapshot.as_of_date == last_month_end,
        ).first()
        if has_snap:
            continue
        if _gap_exists(db, gap_type="index_constituent", index_code=ic, as_of_date=last_month_end):
            continue
        fmap = db.query(FundIndexMap).filter(FundIndexMap.index_code == ic).first()
        name = (fmap.index_name if fmap else None) or ic
        g = DataGapReport(
            gap_type="index_constituent", index_code=ic, as_of_date=last_month_end,
            description=f"{name} 缺 {last_month_end} 指数构成快照",
        )
        db.add(g)
        inserted.append(g)

    # 3. index_classification_gap
    for ic in index_codes:
        has_cls = db.query(IndexClassification).filter(
            IndexClassification.index_code == ic
        ).first()
        if has_cls:
            continue
        if _gap_exists(db, gap_type="index_classification", index_code=ic):
            continue
        fmap = db.query(FundIndexMap).filter(FundIndexMap.index_code == ic).first()
        name = (fmap.index_name if fmap else None) or ic
        g = DataGapReport(
            gap_type="index_classification", index_code=ic,
            description=f"{name} 缺分类（category/theme）",
        )
        db.add(g)
        inserted.append(g)

    db.commit()
    return {
        "inserted": len(inserted),
        "by_type": {
            "stock_report": sum(1 for g in inserted if g.gap_type == "stock_report"),
            "index_constituent": sum(1 for g in inserted if g.gap_type == "index_constituent"),
            "index_classification": sum(1 for g in inserted if g.gap_type == "index_classification"),
        },
        "today": today.isoformat(),
    }
