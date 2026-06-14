"""Backfill SecurityMaster.type2 from fund name keywords.

主题分类规则（按优先级匹配，先命中先返回）：
  黄金   — asset_type == 'gold'
  红利   — 名称含 红利 / 低波 / 质量 / 港股通红利
  新兴产业 — 名称含 科创 / 创业 / 芯片 / 通信 / 通讯 / 半导体 / 电网

类型范围：仅对基金/ETF/指数写入 type2；股票(us_stock) 留空。
"""
from sqlalchemy.orm import Session

from models import Holding, SecurityMaster

GOLD_KW = ("黄金",)
DIVIDEND_KW = ("红利", "低波", "质量", "港股通红利")
EMERGING_KW = ("科创", "创业", "芯片", "通信", "通讯", "半导体", "电网")

# asset_type 范围：仅这些写入 type2
TYPED_TYPES = {
    "a_share_equity", "a_share_etf",
    "hk_equity", "qdii_equity", "us_etf",
    "gold",  # 黄金也写 type2=黄金
}


def classify_type2(security_name: str, asset_type: str) -> str | None:
    """根据基金名 + 类型返回 type2 标签。None 表示不写。"""
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


def backfill_type2(db: Session) -> dict:
    """扫所有 holdings 的 security_master，写入 type2。返回统计。"""
    # 取所有 security_master 现有记录
    masters = {m.security_code: m for m in db.query(SecurityMaster).all()}

    # 同时拿 holdings 的 name（master 可能没填 name）
    holdings = db.query(Holding).all()

    stats = {"updated": 0, "skipped": 0, "dividend": 0, "emerging": 0, "gold": 0}
    seen = set()

    for h in holdings:
        if h.security_code in seen:
            continue
        seen.add(h.security_code)

        m = masters.get(h.security_code)
        if not m:
            continue

        # asset_type / name 优先取 master，没有则用 holding
        asset_type = m.asset_type or h.asset_type
        name = m.security_name or h.security_name or ""
        new_type2 = classify_type2(name, asset_type)

        if m.type2 == new_type2:
            stats["skipped"] += 1
            continue

        m.type2 = new_type2
        stats["updated"] += 1
        if new_type2 == "红利":
            stats["dividend"] += 1
        elif new_type2 == "新兴产业":
            stats["emerging"] += 1
        elif new_type2 == "黄金":
            stats["gold"] += 1

    db.commit()
    return stats


if __name__ == "__main__":
    from database import SessionLocal
    db = SessionLocal()
    print(backfill_type2(db))
    db.close()
