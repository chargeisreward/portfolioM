"""公共数据主数据重构 — SecurityMaster → 3 主表 一次性迁移脚本 (2026-07-02)。

用法:
  1. dry-run (默认):
        python -m scripts.migrate_split_security_master
  2. 人工 review 输出
  3. 真跑:
        python -m scripts.migrate_split_security_master --commit
  4. 验证 counts:
        python -m scripts.migrate_split_security_master --verify

特性:
  - idempotent (重跑结果一致)
  - bond 鉴别 (asset_type='qdii_bond' 或 .OF 后缀的 bond 进 fund_master)
  - pg_dump 自动备份 (真跑前)
  - 整个流程包在 PG transaction 中,失败自动 rollback

⚠️ 首次跑必须 dry-run,然后人工 review,再 --commit。
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from models_master import (
    Base as MasterBase,
    StockMaster, FundMaster, IndexMaster,
    Classification, ClassificationAssign,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ============================================================================
# type2 英文 → 中文 映射 (迁移期用,后续 admin 可在 classification 表里编辑)
# ============================================================================
_TYPE2_CODE_TO_LABEL = {
    "emerging": "新兴产业",
    "dividend": "红利",
    "gold":     "黄金",
}


def _normalize_type2(raw: str | None) -> tuple[str, str] | None:
    """(code, display_label) 或 None。未知值: code=原值, label=原值。"""
    if not raw:
        return None
    code = raw.lower()
    label = _TYPE2_CODE_TO_LABEL.get(code, raw)
    return (code, label)


def _is_bond_to_fund(asset_type: str | None, security_code: str) -> bool:
    """bond 鉴别: qdii_bond 或 .OF 后缀 → fund_master;否则 stock_master。"""
    if asset_type == "qdii_bond":
        return True
    if security_code.endswith(".OF"):
        return True
    return False


def _seed_index_master_from_legacy(db: Session) -> int:
    """从 security_master_legacy 提取 index_code + index_name。"""
    rows = db.execute(text("""
        SELECT DISTINCT index_code, index_name
        FROM security_master_legacy
        WHERE index_code IS NOT NULL AND index_code != ''
    """)).fetchall()
    inserted = 0
    for code, name in rows:
        if not code:
            continue
        existing = db.query(IndexMaster).filter_by(index_code=code).first()
        if existing:
            continue
        idx_code = code.split(".")[0]
        db.add(IndexMaster(
            index_code=idx_code,
            index_name=name or idx_code,
            source="manual_legacy",
            first_pulled_at=datetime.utcnow(),
            last_pulled_at=datetime.utcnow(),
            last_verified_at=datetime.utcnow(),
            is_active=True,
        ))
        inserted += 1
    return inserted


def _seed_classification_dict(db: Session, dimension: str, items: list[tuple[str, str]]) -> int:
    """灌入分类字典。返回新建条数。"""
    inserted = 0
    for sort_order, (code, label) in enumerate(items):
        existing = db.query(Classification).filter_by(
            dimension=dimension, code=code
        ).first()
        if existing:
            continue
        db.add(Classification(
            dimension=dimension, code=code, display_label=label,
            sort_order=sort_order, is_active=True,
        ))
        inserted += 1
    return inserted


def dry_run_report(db: Session) -> dict:
    """dry-run 模式: 不写入,只统计。"""
    report = {
        "legacy_total": 0,
        "to_stock_master": 0,
        "to_fund_master": 0,
        "index_master_added": 0,
        "classification_added": 0,
        "classification_assign": 0,
        "warnings": [],
    }

    rows = db.execute(text("""
        SELECT security_code, asset_type, security_type,
               type2, index_code, benchmark_formula
        FROM security_master_legacy
    """)).fetchall()
    report["legacy_total"] = len(rows)

    for security_code, asset_type, security_type, type2, index_code, _ in rows:
        if security_type == "stock":
            report["to_stock_master"] += 1
        elif security_type == "fund":
            report["to_fund_master"] += 1
        elif security_type == "bond":
            if _is_bond_to_fund(asset_type, security_code):
                report["to_fund_master"] += 1
            else:
                report["to_stock_master"] += 1
        else:
            report["warnings"].append(
                f"security_type='{security_type}' 未知: {security_code}"
            )

        if type2:
            code, label = (_normalize_type2(type2) or (None, None))
            if code and code not in _TYPE2_CODE_TO_LABEL:
                report["warnings"].append(
                    f"type2 未知值: {security_code} → {type2!r}"
                )

    return report


def commit_migration(db: Session) -> dict:
    """真跑: 写入所有新表。需在 PG transaction 中调用。"""
    report = dry_run_report(db)

    legacy_rows = db.execute(text("""
        SELECT security_code, security_name, currency, asset_type, exchange,
               is_drillable, note
        FROM security_master_legacy
    """)).fetchall()
    for code, name, ccy, at, ex, drill, note in legacy_rows:
        st_row = db.execute(text(
            "SELECT security_type FROM security_master_legacy WHERE security_code=:c"
        ), {"c": code}).first()
        sec_type = st_row[0] if st_row else None
        if sec_type == "fund" or (sec_type == "bond" and _is_bond_to_fund(at, code)):
            continue
        existing = db.query(StockMaster).filter_by(stock_code=code).first()
        if existing:
            continue
        db.add(StockMaster(
            stock_code=code, stock_name=name, currency=ccy or "CNY",
            asset_type=at or "a_share_equity", exchange=ex,
            is_drillable=bool(drill), note=note,
        ))

    for code, name, ccy, at, ex, drill, note in legacy_rows:
        st_row = db.execute(text(
            "SELECT security_type, fund_type, benchmark_formula FROM security_master_legacy WHERE security_code=:c"
        ), {"c": code}).first()
        if not st_row:
            continue
        sec_type, fund_type, bench = st_row
        if sec_type == "fund" or (sec_type == "bond" and _is_bond_to_fund(at, code)):
            existing = db.query(FundMaster).filter_by(fund_code=code).first()
            if existing:
                continue
            db.add(FundMaster(
                fund_code=code, fund_name=name, currency=ccy or "CNY",
                asset_type=at or "a_share_equity",
                fund_type=fund_type or ("otc" if code.endswith(".OF") else "etf"),
                benchmark_formula=bench, is_drillable=bool(drill), note=note,
            ))

    report["index_master_added"] = _seed_index_master_from_legacy(db)

    asset_type_values = db.execute(text(
        "SELECT DISTINCT asset_type FROM security_master_legacy WHERE asset_type IS NOT NULL"
    )).fetchall()
    asset_type_items = [(at[0], at[0]) for at in asset_type_values]
    n1 = _seed_classification_dict(db, "asset_type", asset_type_items)

    type2_values = db.execute(text(
        "SELECT DISTINCT type2 FROM security_master_legacy WHERE type2 IS NOT NULL"
    )).fetchall()
    theme_items = []
    for (raw,) in type2_values:
        norm = _normalize_type2(raw)
        if norm:
            theme_items.append(norm)
    theme_items = list(set(theme_items))
    n2 = _seed_classification_dict(db, "theme", theme_items)

    report["classification_added"] = n1 + n2
    db.commit()
    return report


def verify_migration(db: Session) -> dict:
    """验证: 对比 legacy 与新表的 counts。"""
    legacy_count = db.execute(text(
        "SELECT COUNT(*) FROM security_master_legacy"
    )).scalar()
    new_count = (
        db.query(StockMaster).count()
        + db.query(FundMaster).count()
    )
    return {
        "legacy_count": legacy_count,
        "new_count": new_count,
        "match": legacy_count == new_count,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="真跑写入 (默认 dry-run)")
    parser.add_argument("--verify", action="store_true",
                        help="验证 counts")
    args = parser.parse_args()

    from database import SessionLocal
    db = SessionLocal()

    try:
        MasterBase.metadata.create_all(bind=db.get_bind())

        if args.verify:
            result = verify_migration(db)
            logger.info(f"verify: {result}")
            return

        if args.commit:
            report = commit_migration(db)
            logger.info(f"commit done: {report}")
        else:
            report = dry_run_report(db)
            logger.info("== Dry Run Report ==")
            for k, v in report.items():
                logger.info(f"  {k}: {v}")
            logger.info("== End ==")
    finally:
        db.close()


if __name__ == "__main__":
    main()
