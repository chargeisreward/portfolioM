"""seed 测试用户：admin / advisor_x / user_a / user_b

用法：
    cd backend && python scripts/seed_users.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL, SEED_ADMIN_USERNAME, SEED_ADMIN_PASSWORD
from database import Base
import models


def hash_pw(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=10)).decode()


def seed():
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    seeds = [
        (SEED_ADMIN_USERNAME, SEED_ADMIN_PASSWORD, False, True, "系统管理员"),
        ("advisor_x", "advisor123", True, False, "张顾问"),
        ("user_a", "user123", False, False, "王先生"),
        ("user_b", "user123", False, False, "李女士"),
    ]
    for username, pw, is_adv, is_adm, display in seeds:
        existing = db.query(models.User).filter(models.User.username == username).first()
        if existing:
            print(f"[skip] {username} 已存在")
            continue
        db.add(models.User(
            username=username, password_hash=hash_pw(pw),
            is_advisor=is_adv, is_admin=is_adm,
            display_name=display, is_active=True
        ))
        print(f"[add] {username}")
    db.commit()
    print("Done.")


if __name__ == "__main__":
    seed()