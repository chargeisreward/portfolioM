"""seed 测试用户：admin / advisor_x / user_a / user_b / user_c

用户密码策略（2026-06-24 用户确认）：
  - 用户2 + 管理员：admin / user_b → 234567
  - 原用户 + 顾问：user_a / advisor_x → 123456（继承原密码）
  - 新建用户3：user_c → 112233

用法：
    cd backend && python scripts/seed_users.py           # 缺则补，已存在跳过
    cd backend && python scripts/seed_users.py --reset   # 重置所有测试用户密码/角色（不删数据）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from config import DATABASE_URL, SEED_ADMIN_USERNAME
from database import Base
import models


def hash_pw(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=10)).decode()


# (username, password, is_advisor, is_admin, display_name)
SEEDS = [
    (SEED_ADMIN_USERNAME, "234567",    False, True,  "系统管理员"),  # 管理员 234567
    ("advisor_x",         "123456",    True,  False, "张顾问"),     # 顾问 继承原密码 123456
    ("user_a",            "123456",    False, False, "王先生"),     # 原用户1 继承原密码 123456
    ("user_b",            "234567",    False, False, "李女士"),     # 用户2 234567
    ("user_c",            "112233",    False, False, "赵客户"),     # 新增用户3 112233
]


def seed(reset_passwords: bool = False):
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    for username, pw, is_adv, is_adm, display in SEEDS:
        existing = db.query(models.User).filter(models.User.username == username).first()
        if existing:
            if reset_passwords:
                existing.password_hash = hash_pw(pw)
                existing.is_advisor = is_adv
                existing.is_admin = is_adm
                existing.display_name = display
                existing.is_active = True
                print(f"[reset] {username} -> pw={pw}")
            else:
                print(f"[skip] {username} already exists (use --reset to update pw)")
            continue
        db.add(models.User(
            username=username, password_hash=hash_pw(pw),
            is_advisor=is_adv, is_admin=is_adm,
            display_name=display, is_active=True,
        ))
        print(f"[add] {username} (pw={pw})")
    db.commit()
    print("Done.")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    seed(reset_passwords=reset)