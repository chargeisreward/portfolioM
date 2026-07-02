"""QQQ 手动入库 — 单一一次性脚本,不走 akshare 轮询。

用法: cd backend && python -m scripts._seed_qqq
"""
from datetime import datetime

from database import SessionLocal
from models_master import IndexMaster


def seed_qqq():
    db = SessionLocal()
    try:
        existing = db.query(IndexMaster).filter_by(index_code="QQQ").first()
        if existing:
            print(f"QQQ 已存在: id={existing.id}, source={existing.source}")
            return
        db.add(IndexMaster(
            index_code="QQQ",
            index_name="纳斯达克100",
            exchange="US",
            currency="USD",
            category="宽基",
            source="manual_qqq_seed",
            is_active=True,
            first_pulled_at=datetime.utcnow(),
            last_pulled_at=datetime.utcnow(),
            last_verified_at=datetime.utcnow(),
        ))
        db.commit()
        print("QQQ 已写入")
    finally:
        db.close()


if __name__ == "__main__":
    seed_qqq()