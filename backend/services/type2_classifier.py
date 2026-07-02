"""Backfill classification (theme) assignments from fund name keywords.

主题分类规则（按优先级匹配，先命中先返回）：
  黄金    — asset_type == 'gold'
  红利    — 名称含 红利 / 低波 / 质量 / 港股通红利
  新兴产业 — 名称含 科创 / 创业 / 芯片 / 通信 / 通讯 / 半导体 / 电网

类型范围：仅对基金/ETF/指数写入 theme 分类；股票(us_stock) 留空。

迁移说明 (2026-07-02):
- 旧版本写入 security_master.type2 + security_master.asset_type
- 新版本写 classification(dimension='theme') + classification_assign(entity_type='fund', ...)
- 旧表 security_master 已重命名为 security_master_legacy,新代码不读
"""
from typing import Iterable

from sqlalchemy.orm import Session

from models import Holding
from models_master import FundMaster, Classification, ClassificationAssign

GOLD_KW = ("黄金",)
DIVIDEND_KW = ("红利", "低波", "质量", "港股通红利")
EMERGING_KW = ("科创", "创业", "芯片", "通信", "通讯", "半导体", "电网")

# asset_type 范围：仅这些写入 theme 分类
TYPED_TYPES = {
    "a_share_equity", "a_share_etf",
    "hk_equity", "qdii_equity", "us_etf",
    "gold",  # 黄金也写 type2=黄金
}


def classify_type2(security_name: str, asset_type: str) -> str | None:
    """根据基金名 + 类型返回 type2 标签 (中文)。None 表示不写。"""
    if asset_type not in TYPED_TYPES:
        return None
    if asset_type == "gold":
        return "黄金"
    if not security_name:
        return None
    name = security_name
    # 优先级：红利 优先于 新兴产业（避免误判）
    for kw in DIVIDEND_KW:
        if kw in name:
            return "红利"
    for kw in EMERGING_KW:
        if kw in name:
            return "新兴产业"
    return None


def _ensure_classification(
    db: Session, dimension: str, code: str, display_label: str,
) -> Classification:
    """获取 (或创建) 一个 classification 记录。返回 ORM 对象。"""
    c = db.query(Classification).filter_by(
        dimension=dimension, code=code,
    ).first()
    if c:
        return c
    c = Classification(
        dimension=dimension, code=code, display_label=display_label,
        sort_order=0, is_active=True,
    )
    db.add(c)
    db.flush()
    return c


def _assign_theme(
    db: Session, entity_type: str, entity_code: str, label_zh: str,
) -> bool:
    """把一个 theme 分类 label 赋给实体。已存在则跳过。"""
    code_map = {"黄金": "gold", "红利": "dividend", "新兴产业": "emerging"}
    code = code_map[label_zh]
    c = _ensure_classification(db, "theme", code, label_zh)
    existing = db.query(ClassificationAssign).filter_by(
        entity_type=entity_type, entity_code=entity_code,
        classification_id=c.id,
    ).first()
    if existing:
        return False
    db.add(ClassificationAssign(
        entity_type=entity_type, entity_code=entity_code,
        classification_id=c.id,
    ))
    return True


def backfill_classification_theme(db: Session) -> dict:
    """扫所有 holdings 引用的基金，按名称打 theme 标签，写 classification_assign。

    Returns:
        dict: {updated, skipped, dividend, emerging, gold}
    """
    # 取所有基金 (fund_master) — 替代旧的 security_master 扫描
    funds = db.query(FundMaster).all()
    fund_by_code = {f.fund_code: f for f in funds}

    # holdings 用于补 name (某些基金 master 没填名)
    holdings = db.query(Holding).all()
    holding_by_code = {h.security_code: h for h in holdings}

    stats = {"updated": 0, "skipped": 0, "dividend": 0, "emerging": 0, "gold": 0}
    seen = set()

    for code in fund_by_code.keys():
        if code in seen:
            continue
        seen.add(code)

        f = fund_by_code[code]
        h = holding_by_code.get(code)
        asset_type = f.asset_type or (h.asset_type if h else None)
        name = f.fund_name or (h.security_name if h else "") or ""
        label = classify_type2(name, asset_type)

        if not label:
            continue

        if _assign_theme(db, "fund", code, label):
            stats["updated"] += 1
            if label == "红利":
                stats["dividend"] += 1
            elif label == "新兴产业":
                stats["emerging"] += 1
            elif label == "黄金":
                stats["gold"] += 1
        else:
            stats["skipped"] += 1

    db.commit()
    return stats


if __name__ == "__main__":
    from database import SessionLocal
    db = SessionLocal()
    print(backfill_classification_theme(db))
    db.close()
