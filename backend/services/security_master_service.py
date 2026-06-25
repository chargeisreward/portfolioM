"""证券主数据 service — CRUD + 同步 + 初始化。

依赖：SecurityMaster, Holding, FundDrillSnapshot, FundIndexMap
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from models import SecurityMaster, Holding, FundDrillSnapshot, FundIndexMap

logger = logging.getLogger(__name__)

# 旧硬编码 → 新 is_drillable 的迁移映射
_LEGACY_DRILLABLE_ASSET_TYPES = frozenset({
    "a_share_equity", "a_share_etf", "hk_equity", "qdii_equity", "us_etf",
})


def _derive_market(code: str) -> str:
    """从证券代码推断市场。"""
    if code.endswith(".OF"):
        return "OF"
    if code.endswith(".SH") or code.endswith(".SZ"):
        return "CN"
    if code.endswith(".HK"):
        return "HK"
    if "." not in code:
        return "US"
    return "CN"


def _derive_fund_type(code: str, asset_type: str) -> str | None:
    """推断基金类型：etf(场内) / otc(场外)。"""
    if asset_type in ("a_share_etf", "us_etf"):
        return "etf"
    if code.endswith(".OF"):
        return "otc"
    return None


def _derive_security_type(asset_type: str) -> str:
    """从 asset_type 推断 security_type。"""
    if asset_type in ("bond", "qdii_bond"):
        return "bond"
    if "equity" in asset_type or "etf" in asset_type:
        return "fund"
    if asset_type in ("gold", "commodity"):
        return "fund"
    return "fund"


def _to_dict(sm: SecurityMaster) -> dict:
    """将 ORM 对象转为 dict。"""
    return {
        "security_code": sm.security_code,
        "security_name": sm.security_name,
        "currency": sm.currency,
        "asset_type": sm.asset_type,
        "type2": sm.type2,
        "exchange": sm.exchange,
        "security_type": sm.security_type,
        "fund_type": sm.fund_type,
        "market": sm.market,
        "is_drillable": sm.is_drillable,
        "index_code": sm.index_code,
        "index_name": sm.index_name,
        "benchmark_formula": sm.benchmark_formula,
        "premium_discount": sm.premium_discount,
        "note": sm.note,
        "updated_at": sm.updated_at.isoformat() if sm.updated_at else None,
    }


def list_securities(
    db: Session,
    sec_type: str | None = None,
    market: str | None = None,
    drillable: bool | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """查询证券主数据列表（分页+筛选）。返回 {items, total, page, page_size}。"""
    q = db.query(SecurityMaster)
    if sec_type:
        q = q.filter(SecurityMaster.security_type == sec_type)
    if market:
        q = q.filter(SecurityMaster.market == market)
    if drillable is not None:
        q = q.filter(SecurityMaster.is_drillable == drillable)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (SecurityMaster.security_code.like(like))
            | (SecurityMaster.security_name.like(like))
        )
    total = q.count()
    rows = q.order_by(SecurityMaster.security_code).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_to_dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def get_security(db: Session, security_code: str) -> dict | None:
    """查询单条证券主数据。"""
    sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == security_code).first()
    return _to_dict(sm) if sm else None


def create_security(db: Session, data: dict) -> dict:
    """新增证券主数据。"""
    sm = SecurityMaster(
        security_code=data["security_code"],
        security_name=data.get("security_name"),
        currency=data.get("currency", "CNY"),
        asset_type=data.get("asset_type"),
        type2=data.get("type2"),
        exchange=data.get("exchange"),
        security_type=data.get("security_type"),
        fund_type=data.get("fund_type"),
        market=data.get("market"),
        is_drillable=data.get("is_drillable", False),
        index_code=data.get("index_code"),
        index_name=data.get("index_name"),
        benchmark_formula=data.get("benchmark_formula"),
        premium_discount=data.get("premium_discount"),
        note=data.get("note"),
        updated_by=data.get("updated_by"),
    )
    db.add(sm)
    db.commit()
    db.refresh(sm)
    return _to_dict(sm)


def update_security(db: Session, security_code: str, data: dict) -> dict | None:
    """更新证券主数据。"""
    sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == security_code).first()
    if not sm:
        return None
    for key in ("security_name", "currency", "asset_type", "type2", "exchange",
                "security_type", "fund_type", "market", "is_drillable", "index_code",
                "index_name", "benchmark_formula", "premium_discount", "note", "updated_by"):
        if key in data:
            setattr(sm, key, data[key])
    db.commit()
    db.refresh(sm)
    return _to_dict(sm)


def delete_security(db: Session, security_code: str) -> bool:
    """删除证券主数据（有持仓时禁止）。"""
    holding_count = db.query(Holding).filter(Holding.security_code == security_code).count()
    if holding_count > 0:
        raise ValueError(f"无法删除：该证券有 {holding_count} 条持仓记录")
    sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == security_code).first()
    if not sm:
        return False
    db.delete(sm)
    db.commit()
    return True


def sync_from_holdings(db: Session) -> int:
    """从 Holding 表同步：为不在 SecurityMaster 中的证券创建记录。"""
    existing = {r[0] for r in db.query(SecurityMaster.security_code).all()}
    holdings = db.query(Holding).filter(~Holding.security_code.in_(existing)).all() if existing else db.query(Holding).all()
    count = 0
    for h in holdings:
        if h.security_code in existing:
            continue
        sm = SecurityMaster(
            security_code=h.security_code,
            security_name=h.security_name,
            asset_type=h.asset_type,
            security_type=_derive_security_type(h.asset_type or ""),
            market=_derive_market(h.security_code),
            fund_type=_derive_fund_type(h.security_code, h.asset_type or ""),
            is_drillable=(h.asset_type in _LEGACY_DRILLABLE_ASSET_TYPES) if h.asset_type else False,
        )
        db.add(sm)
        existing.add(h.security_code)
        count += 1
    db.commit()
    return count


def sync_from_drill(db: Session) -> int:
    """从 FundDrillSnapshot 表同步：为下钻股票创建记录。"""
    existing = {r[0] for r in db.query(SecurityMaster.security_code).all()}
    stocks = db.query(
        FundDrillSnapshot.stock_code, FundDrillSnapshot.stock_name
    ).filter(
        ~FundDrillSnapshot.stock_code.in_(existing)
    ).distinct().all() if existing else db.query(
        FundDrillSnapshot.stock_code, FundDrillSnapshot.stock_name
    ).distinct().all()
    count = 0
    for code, name in stocks:
        sm = SecurityMaster(
            security_code=code,
            security_name=name,
            security_type="stock",
            market=_derive_market(code),
            is_drillable=False,
        )
        db.add(sm)
        existing.add(code)
        count += 1
    db.commit()
    return count


def init_from_existing(db: Session) -> int:
    """一次性初始化：从 FundIndexMap + Holding + FundDrillSnapshot 批量导入。"""
    count = 0
    count += sync_from_holdings(db)
    count += sync_from_drill(db)

    # 从 FundIndexMap 补充 index_code/index_name/benchmark
    fund_maps = db.query(FundIndexMap).all()
    for fm in fund_maps:
        sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == fm.fund_code).first()
        if sm and not sm.index_code:
            sm.index_code = fm.index_code.split(".")[0] if fm.index_code else None
            sm.index_name = fm.index_name
            sm.benchmark_formula = fm.benchmark_formula
    db.commit()
    return count
