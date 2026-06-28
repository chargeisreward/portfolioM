# PortfolioM 权限升级 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 PortfolioM 从单密码全局访问改造为「用户 / 顾问 / 管理员」三角色体系，按 user 隔离 holdings / watchlist，新增顾问-客户双向关联和数据补足提示。

**Architecture:**
- 单表 + 角色字段（`users.is_advisor`, `users.is_admin`）
- 视图代理：advisor/admin 可切换查看 ACTIVE 关联客户，写入仍只对自己
- 持仓 / 关注 / 导入日志 / 关系表按 user 隔离；分析师报告 / 指数构成 / 价格缓存 / 资讯共享
- 数据补足靠新 scheduler job + admin 「数据补足」页面

**Tech Stack:** FastAPI + SQLAlchemy + Pydantic v2 + bcrypt + React (Vite) + Axios + APScheduler + PostgreSQL 16

**Spec:** `docs/superpowers/specs/2026-06-23-portfoliom-permission-upgrade-design.md`

**Milestones:**
- M1 — Schema + seed（无功能）
- M2 — Auth + 鉴权依赖 + Login UI
- M3 — 数据隔离 + 菜单过滤 + 视图代理 + 运维面板
- M4 — 顾问-客户关联
- M5 — 数据补足（scheduler + admin UI）

**Scope note:** 这是单个大型主题；保留单 plan 但按 milestone 分批提交 + 验证。

---

## Milestone 1 — Schema + Seed

### Task 1.1: 安装 bcrypt 并加 config 常量

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/config.py`

- [ ] **Step 1: 加 bcrypt 到 requirements.txt**

```bash
cd D:/claude_code_project/PortfolioM
grep -q "^bcrypt" backend/requirements.txt || echo "bcrypt==4.1.2" >> backend/requirements.txt
```

- [ ] **Step 2: 安装**

```bash
cd backend
pip install bcrypt==4.1.2
```

Expected: Successfully installed bcrypt-4.1.2

- [ ] **Step 3: 加 config 常量**

修改 `backend/config.py`，在文件末尾追加：

```python
# 多用户/权限
BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "10"))
SEED_ADMIN_USERNAME = os.environ.get("SEED_ADMIN_USERNAME", "admin")
SEED_ADMIN_PASSWORD = os.environ.get("SEED_ADMIN_PASSWORD", "admin123")
```

- [ ] **Step 4: 验证导入**

```bash
cd backend && python -c "from config import BCRYPT_ROUNDS, SEED_ADMIN_USERNAME; print(BCRYPT_ROUNDS, SEED_ADMIN_USERNAME)"
```

Expected: `10 admin`

- [ ] **Step 5: 提交**

```bash
cd D:/claude_code_project/PortfolioM
git add backend/requirements.txt backend/config.py
git commit -m "feat(auth): add bcrypt dependency and seed admin constants"
```

---

### Task 1.2: 新增 5 张表的 ORM model

**Files:**
- Modify: `backend/models.py`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_models_users.py`：

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models

def test_users_table_exists():
    """验证 User 表能 create_all"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert "users" in Base.metadata.tables
    assert "user_relations" in Base.metadata.tables
    assert "index_classification" in Base.metadata.tables
    assert "data_gap_report" in Base.metadata.tables
    assert "holding_import_log" in Base.metadata.tables
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && PYTHONPATH=. pytest tests/test_models_users.py -v
```

Expected: FAIL — `assert "users" in Base.metadata.tables`

- [ ] **Step 3: 实现 5 张新表 model**

修改 `backend/models.py`，在文件末尾的 `# === Access Control ===` 区之后追加：

```python
# === Multi-user / Permissions ===

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(64), nullable=True)
    is_advisor = Column(Boolean, nullable=False, default=False, index=True)
    is_admin = Column(Boolean, nullable=False, default=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserRelation(Base):
    __tablename__ = "user_relations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    advisor_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    client_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="PENDING")
    initiator_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("advisor_user_id", "client_user_id", name="uq_relation"),)


class IndexClassification(Base):
    __tablename__ = "index_classification"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    index_code = Column(String(32), unique=True, nullable=False, index=True)
    index_name = Column(String(128), nullable=True)
    category = Column(String(64), nullable=True)
    theme = Column(String(64), nullable=True)
    benchmark_formula = Column(Text, nullable=True)
    source = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DataGapReport(Base):
    __tablename__ = "data_gap_report"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    gap_type = Column(String(32), nullable=False, index=True)
    stock_code = Column(String(32), nullable=True, index=True)
    index_code = Column(String(32), nullable=True, index=True)
    as_of_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="OPEN")
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)


class HoldingImportLog(Base):
    __tablename__ = "holding_import_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    import_source = Column(String(16), nullable=False)
    file_name = Column(String(255), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    imported_at = Column(DateTime, default=datetime.utcnow, index=True)
```

确保文件顶部 imports 包含 `from sqlalchemy import UniqueConstraint, ForeignKey, Boolean, BigInteger`（如有缺补上）。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd backend && PYTHONPATH=. pytest tests/test_models_users.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/models.py backend/tests/test_models_users.py
git commit -m "feat(auth): add 5 new tables for multi-user (users, user_relations, index_classification, data_gap_report, holding_import_log)"
```

---

### Task 1.3: 给 holdings / watchlist / access_sessions 加 user_id 列

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/database.py`（init_db 加 ALTER）

- [ ] **Step 1: 改 models.py**

找到 `Holding` 类，加：

```python
    user_id = Column(BigInteger, nullable=False, default=1, index=True)
```

找到 `Watchlist` 类（PK 改复合）：

```python
class Watchlist(Base):
    __tablename__ = "watchlist"
    user_id = Column(BigInteger, primary_key=True, nullable=False, default=1)
    code = Column(String(32), primary_key=True, nullable=False)
    name = Column(String(64), nullable=True)
    market = Column(String(16), nullable=True)
    industry = Column(String(64), nullable=True)
    weight = Column(Float, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
```

找到 `AccessSession` 类，加：

```python
    user_id = Column(BigInteger, nullable=True, index=True)
```

- [ ] **Step 2: 写测试**

新建 `backend/tests/test_models_migration_columns.py`：

```python
from database import Base
import models

def test_holding_has_user_id():
    cols = {c.name for c in Base.metadata.tables["holdings"].columns}
    assert "user_id" in cols

def test_watchlist_pk_is_composite():
    pks = [c.name for c in Base.metadata.tables["watchlist"].primary_key.columns]
    assert set(pks) == {"user_id", "code"}

def test_access_session_has_user_id():
    cols = {c.name for c in Base.metadata.tables["access_sessions"].columns}
    assert "user_id" in cols
```

- [ ] **Step 3: 跑测试确认通过**

```bash
cd backend && PYTHONPATH=. pytest tests/test_models_migration_columns.py -v
```

Expected: PASS

- [ ] **Step 4: init_db 加迁移逻辑**

修改 `backend/database.py::init_db()` 末尾追加：

```python
    # === 多用户迁移 ===
    _apply_user_id_migrations(engine)
```

加新函数（在 `init_db` 之上）：

```python
def _apply_user_id_migrations(engine):
    """为已有表加 user_id 列 / 改 watchlist PK。SQLite/PG 兼容。"""
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    with engine.begin() as conn:
        # 1. holdings
        if "holdings" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("holdings")}
            if "user_id" not in cols:
                conn.execute(text("ALTER TABLE holdings ADD COLUMN user_id BIGINT NOT NULL DEFAULT 1"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_holdings_user_id ON holdings (user_id)"))
        # 2. watchlist: 加列 + 改 PK
        if "watchlist" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("watchlist")}
            if "user_id" not in cols:
                conn.execute(text("ALTER TABLE watchlist ADD COLUMN user_id BIGINT NOT NULL DEFAULT 1"))
            # 检查 PK
            pk_cols = [c["name"] for c in insp.get_pk_constraint("watchlist")["constrained_columns"]]
            if pk_cols == ["code"]:
                # PG 改 PK
                try:
                    conn.execute(text("ALTER TABLE watchlist DROP CONSTRAINT watchlist_pkey"))
                    conn.execute(text("ALTER TABLE watchlist ADD PRIMARY KEY (user_id, code)"))
                except Exception:
                    # SQLite 不能 DROP PK；通过重建表
                    conn.execute(text("""
                        CREATE TABLE watchlist_new (
                            user_id BIGINT NOT NULL DEFAULT 1,
                            code VARCHAR(32) NOT NULL,
                            name VARCHAR(64),
                            market VARCHAR(16),
                            industry VARCHAR(64),
                            weight FLOAT,
                            added_at TIMESTAMP,
                            PRIMARY KEY (user_id, code)
                        )
                    """))
                    conn.execute(text("INSERT OR IGNORE INTO watchlist_new SELECT user_id, code, name, market, industry, weight, added_at FROM watchlist"))
                    conn.execute(text("DROP TABLE watchlist"))
                    conn.execute(text("ALTER TABLE watchlist_new RENAME TO watchlist"))
        # 3. access_sessions
        if "access_sessions" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("access_sessions")}
            if "user_id" not in cols:
                conn.execute(text("ALTER TABLE access_sessions ADD COLUMN user_id BIGINT"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_access_sessions_user_id ON access_sessions (user_id)"))
```

- [ ] **Step 5: 验证 init_db 跑通（用临时 SQLite）**

```bash
cd backend && python -c "
from database import engine, init_db
import os
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
from database import engine as e2
e2.dispose()
init_db()
print('OK')
"
```

Expected: `OK`

- [ ] **Step 6: 提交**

```bash
git add backend/models.py backend/database.py backend/tests/test_models_migration_columns.py
git commit -m "feat(auth): add user_id to holdings/watchlist/access_sessions + init_db migration"
```

---

### Task 1.4: Seed 脚本 + 启动时自动建 admin

**Files:**
- Create: `backend/scripts/seed_users.py`
- Modify: `backend/database.py`

- [ ] **Step 1: 创建 seed_users.py**

```python
"""seed 测试用户：admin / advisor_x / user_a / user_b"""
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
        ("user_a",    "user123",    False, False, "王先生"),
        ("user_b",    "user123",    False, False, "李女士"),
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
```

- [ ] **Step 2: 启动 backend 触发 init_db 自动建 admin**

修改 `backend/database.py::init_db()` 末尾追加：

```python
    # 启动时若无 admin 账户，自动 seed 一个
    _ensure_seed_admin()

def _ensure_seed_admin():
    """无 users 时自动 seed admin"""
    from sqlalchemy.orm import Session
    from models import User
    import bcrypt as _bc
    from config import SEED_ADMIN_USERNAME, SEED_ADMIN_PASSWORD
    with Session(engine) as db:
        if db.query(User).count() > 0:
            return
        # 把现有 holdings / watchlist 标 user_id=1（即将插入的 admin）
        try:
            db.execute(text("UPDATE holdings SET user_id = 1 WHERE user_id = 1"))  # no-op
            db.execute(text("UPDATE watchlist SET user_id = 1 WHERE user_id = 1"))
        except Exception:
            pass
        pw_hash = _bc.hashpw(SEED_ADMIN_PASSWORD.encode(), _bc.gensalt(rounds=10)).decode()
        admin = User(
            username=SEED_ADMIN_USERNAME, password_hash=pw_hash,
            is_admin=True, is_advisor=False,
            display_name="系统管理员", is_active=True
        )
        db.add(admin); db.commit()
        print(f"[SEED] 已创建 admin 用户 (id=1): {SEED_ADMIN_USERNAME}")
```

确保 `from sqlalchemy import text` 已 import。

- [ ] **Step 3: 跑 seed 脚本**

```bash
cd backend && python scripts/seed_users.py
```

Expected:
```
[add] admin
[add] advisor_x
[add] user_a
[add] user_b
Done.
```

- [ ] **Step 4: 验证数据库**

```bash
cd backend && python -c "
from database import SessionLocal
from models import User
db = SessionLocal()
for u in db.query(User).all():
    print(u.id, u.username, u.is_advisor, u.is_admin)
db.close()
"
```

Expected: 4 行（id 1~4，admin is_admin=True，advisor_x is_advisor=True，user_a/user_b 都 False）

- [ ] **Step 5: 提交**

```bash
git add backend/scripts/seed_users.py backend/database.py
git commit -m "feat(auth): seed 4 test users + auto-seed admin on first startup"
```

---

## Milestone 2 — Auth + 鉴权依赖 + Login UI

### Task 2.1: 抽出 middleware/auth.py

**Files:**
- Create: `backend/middleware/__init__.py`
- Create: `backend/middleware/auth.py`

- [ ] **Step 1: 创建目录与 __init__.py**

```bash
mkdir -p backend/middleware && touch backend/middleware/__init__.py
```

- [ ] **Step 2: 实现 auth.py**

新建 `backend/middleware/auth.py`：

```python
"""鉴权依赖 + 角色检查"""
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User, UserRelation


def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return getattr(request.state, "user", None)


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "请登录")
    return u


def require_advisor(request: Request, db: Session = Depends(get_db)) -> User:
    u = require_user(request)
    if not (u.is_advisor or u.is_admin):
        raise HTTPException(403, "需要顾问或管理员权限")
    return u


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    u = require_user(request)
    if not u.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return u


def get_effective_user_id(
    request: Request,
    view_as_user_id: int | None,
    user: User,
    db: Session,
) -> int:
    """计算 effective_user_id；advisor/admin 可代理，user 只能自己。"""
    if not view_as_user_id or view_as_user_id == user.id:
        return user.id
    if not (user.is_advisor or user.is_admin):
        raise HTTPException(403, "无权查看其他用户")
    if user.is_advisor and not user.is_admin:
        rel = db.query(UserRelation).filter(
            UserRelation.advisor_user_id == user.id,
            UserRelation.client_user_id == view_as_user_id,
            UserRelation.status == "ACTIVE",
        ).first()
        if not rel:
            raise HTTPException(403, "未与该客户建立 ACTIVE 关联")
    from models import User as U
    target = db.query(U).filter(U.id == view_as_user_id, U.is_active == True).first()
    if not target:
        raise HTTPException(404, "目标用户不存在")
    return target.id
```

- [ ] **Step 3: 写测试**

新建 `backend/tests/test_auth_deps.py`：

```python
from middleware.auth import get_effective_user_id
from models import User

def test_self_no_view_as():
    u = User(id=1, username="u", is_advisor=False, is_admin=False, is_active=True)
    assert get_effective_user_id(None, None, u, db=None) == 1

def test_user_cannot_view_as():
    u = User(id=1, username="u", is_advisor=False, is_admin=False, is_active=True)
    from fastapi import HTTPException
    try:
        get_effective_user_id(None, 2, u, db=None)
        assert False, "should raise"
    except HTTPException as e:
        assert e.status_code == 403

def test_admin_can_view_as_anyone(monkeypatch):
    u = User(id=1, username="admin", is_advisor=False, is_admin=True, is_active=True)
    class FakeQuery:
        def filter(self, *a, **kw): return self
        def first(self): return u  # target = self (id=1) -> works
    class FakeDB:
        def query(self, m): return FakeQuery()
    # view_as=2 (different user), admin can; returns 2
    class Target:
        id = 2; is_active = True
    class FakeQ2:
        def filter(self, *a, **kw): return self
        def first(self): return Target()
    class FakeDB2:
        def query(self, m):
            if m.__name__ == "UserRelation": return FakeQ2()
            return FakeQ2()
    # call with view_as=2, admin
    result = get_effective_user_id(None, 2, u, FakeDB2())
    assert result == 2
```

- [ ] **Step 4: 跑测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_auth_deps.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/middleware backend/tests/test_auth_deps.py
git commit -m "feat(auth): extract auth dependencies (require_user/advisor/admin + get_effective_user_id)"
```

---

### Task 2.2: 重写 `/api/auth/login` 多用户版本

**Files:**
- Modify: `backend/main.py`（约 L226 `POST /api/auth/login`）

- [ ] **Step 1: 找到现有 /api/auth/login**

```bash
cd backend && grep -n '"/api/auth/login"' main.py | head -5
```

- [ ] **Step 2: 重写 endpoint**

替换 `POST /api/auth/login` 函数体为：

```python
class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def auth_login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host
    # IP 限流检查（保留 AccessAttempt 逻辑）
    _check_ip_ban(db, ip)
    user = db.query(User).filter(User.username == body.username, User.is_active == True).first()
    ok = False
    if user:
        try:
            ok = bcrypt.checkpw(body.password.encode(), user.password_hash.encode())
        except Exception:
            ok = False
    # 兼容：旧版单密码 APP_PASSWORD
    if not ok and os.environ.get("APP_PASSWORD"):
        if body.password == os.environ.get("APP_PASSWORD"):
            ok = True
            # 单密码登录：找任意 admin 用户
            user = db.query(User).filter(User.is_admin == True).first()
            if not user:
                # 旧库无 users 表 — 失败
                _record_fail(db, ip); raise HTTPException(401, "单密码登录要求至少存在 admin 用户")
    if not ok:
        _record_fail(db, ip)
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_hex(32)
    sess = AccessSession(
        token=token, ip=ip, user_id=user.id,
        expires_at=datetime.utcnow() + timedelta(hours=24)
    )
    db.add(sess); db.commit()
    user.last_login_at = datetime.utcnow(); db.commit()
    _record_success(db, ip)
    return {
        "status": "ok",
        "token": token,
        "expires_in": 86400,
        "user": {
            "id": user.id, "username": user.username,
            "display_name": user.display_name,
            "is_advisor": user.is_advisor, "is_admin": user.is_admin,
        }
    }


def _check_ip_ban(db, ip):
    a = db.query(AccessAttempt).filter(AccessAttempt.ip == ip).first()
    if a and a.banned_until and a.banned_until > datetime.utcnow():
        raise HTTPException(403, f"IP 已封禁至 {a.banned_until.isoformat()}")


def _record_fail(db, ip):
    a = db.query(AccessAttempt).filter(AccessAttempt.ip == ip).first()
    if not a:
        a = AccessAttempt(ip=ip); db.add(a)
    a.fails_1h = (a.fails_1h or 0) + 1
    a.last_fail_at = datetime.utcnow()
    if a.fails_1h >= 10:
        a.banned_until = datetime.utcnow() + timedelta(hours=1)
    db.commit()


def _record_success(db, ip):
    a = db.query(AccessAttempt).filter(AccessAttempt.ip == ip).first()
    if a:
        a.last_success_at = datetime.utcnow()
        a.fails_1h = 0
        db.commit()
```

确保文件顶部 `import bcrypt, secrets, timedelta`。

- [ ] **Step 3: 新增 /api/auth/me**

在 `/api/auth/login` 之后加：

```python
@app.get("/api/auth/me")
def auth_me(request: Request, db: Session = Depends(get_db)):
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    return {"user": {
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "is_advisor": u.is_advisor, "is_admin": u.is_admin,
    }}
```

- [ ] **Step 4: 写测试**

新建 `backend/tests/test_auth_login.py`：

```python
from fastapi.testclient import TestClient
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["APP_PASSWORD"] = ""
from main import app
from database import Base, engine
import models
import bcrypt

Base.metadata.create_all(engine)
# 直接 seed 一个 admin
from models import User
from sqlalchemy.orm import sessionmaker
S = sessionmaker(bind=engine)
db = S()
pw = bcrypt.hashpw(b"admin123", bcrypt.gensalt(rounds=4)).decode()
db.add(User(username="admin", password_hash=pw, is_admin=True, is_active=True))
db.commit(); db.close()

client = TestClient(app)

def test_login_success():
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["username"] == "admin"
    assert body["user"]["is_admin"] is True
    assert "token" in body

def test_login_wrong_password():
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401

def test_login_unknown_user():
    r = client.post("/api/auth/login", json={"username": "noone", "password": "x"})
    assert r.status_code == 401
```

- [ ] **Step 5: 跑测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_auth_login.py -v
```

Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/main.py backend/tests/test_auth_login.py
git commit -m "feat(auth): multi-user login (username+password+bcrypt) + /api/auth/me"
```

---

### Task 2.3: auth_middleware 注入 user / role / view_as

**Files:**
- Modify: `backend/main.py`（找到 `@app.middleware("http")` 的 `auth_middleware`）

- [ ] **Step 1: 找位置**

```bash
cd backend && grep -n 'def auth_middleware' main.py
```

- [ ] **Step 2: 改造**

在 `auth_middleware` 函数内、`call_next` 之前，加：

```python
    # 注入 user / view_as
    try:
        from database import SessionLocal
        from models import AccessSession, User
        sdb = SessionLocal()
        try:
            sess = sdb.query(AccessSession).filter(AccessSession.token == token).first()
            if sess and sess.expires_at > datetime.utcnow() and sess.user_id:
                u = sdb.query(User).filter(User.id == sess.user_id).first()
                if u and u.is_active:
                    request.state.user = u
                    request.state.user_id = u.id
                    request.state.is_advisor = u.is_advisor
                    request.state.is_admin = u.is_admin
            # view_as 解析（来自 query）
            view_as = request.query_params.get("view_as")
            if view_as:
                try: request.state.view_as_user_id = int(view_as)
                except: pass
        finally:
            sdb.close()
    except Exception as e:
        # 不阻塞主流程
        print(f"[auth middleware] user inject failed: {e}")
```

- [ ] **Step 3: 测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_auth_login.py -v
```

Expected: PASS（应当仍然通过，user 注入是新增能力不影响）

- [ ] **Step 4: 提交**

```bash
git add backend/main.py
git commit -m "feat(auth): middleware injects user/is_advisor/is_admin/view_as_user_id"
```

---

### Task 2.4: 前端 AuthGate 加 username 字段

**Files:**
- Modify: `frontend/src/components/AuthGate.jsx`
- Modify: `frontend/src/api.js`

- [ ] **Step 1: api.js 加 getAuthMe + 改 login**

修改 `login` 导出（保持 signature 不变以兼容现有调用，但后端已支持 username 字段）：

```js
// login(username, password)
export const login = (username, password) => api.post('/auth/login', { username, password }).then(r => r.data)
export const getAuthMe = () => api.get('/auth/me').then(r => r.data)
```

- [ ] **Step 2: AuthGate.jsx 改写**

替换为：

```jsx
import React, { useState, useEffect } from 'react'
import { login, getAuthStatus } from '../api'

export default function AuthGate({ onLoggedIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [status, setStatus] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    getAuthStatus().then(setStatus).catch(() => {})
  }, [])

  async function submit(e) {
    e.preventDefault()
    setErr('')
    if (username.length < 3 || password.length < 6) {
      setErr('用户名至少 3 位；密码至少 6 位')
      return
    }
    setSubmitting(true)
    try {
      const res = await login(username, password)
      onLoggedIn(res.token, res.user)
    } catch (e) {
      const code = e?.response?.status
      if (code === 401) setErr('用户名或密码错误')
      else if (code === 403) setErr('账号被禁用或 IP 被封禁')
      else setErr(e?.response?.data?.detail || '登录失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-gate" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
      <form onSubmit={submit} style={{ width: 320, padding: 24, border: '1px solid var(--border)', borderRadius: 8 }}>
        <h2 style={{ marginTop: 0 }}>PortfolioM</h2>
        <label>用户名</label>
        <input value={username} onChange={e => setUsername(e.target.value)} autoFocus
          style={{ width: '100%', padding: 8, marginBottom: 12, boxSizing: 'border-box' }} />
        <label>密码</label>
        <input type="password" value={password} onChange={e => setPassword(e.target.value)}
          style={{ width: '100%', padding: 8, marginBottom: 12, boxSizing: 'border-box' }} />
        {err && <div style={{ color: 'var(--down)', marginBottom: 8 }}>{err}</div>}
        {status?.banned && <div style={{ color: 'var(--down)' }}>IP 已被封禁至 {status.banned_until}</div>}
        <button type="submit" disabled={submitting} style={{ width: '100%', padding: 10 }}>
          {submitting ? '登录中...' : '登录'}
        </button>
        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-muted)' }}>
          测试账户: admin / admin123 · advisor_x / advisor123 · user_a / user123
        </div>
      </form>
    </div>
  )
}
```

- [ ] **Step 3: App.jsx 接住 user 信息**

修改 `App.jsx`：

```jsx
const [currentUser, setCurrentUser] = useState(null)

const onLoggedIn = (token, user) => {
  setSessionToken(token)
  setCurrentUser(user)
  localStorage.setItem('portfoliom_session_user', JSON.stringify(user))
}

const onLogout = async () => {
  try { await api.logout() } catch {}
  localStorage.removeItem('portfoliom_session')
  localStorage.removeItem('portfoliom_session_user')
  setSessionToken('')
  setCurrentUser(null)
  setViewAsUser(null)
  window.location.reload()
}

// 启动时读 localStorage
useEffect(() => {
  const u = localStorage.getItem('portfoliom_session_user')
  if (u) {
    try { setCurrentUser(JSON.parse(u)) } catch {}
  }
}, [])
```

把 `<AuthGate onLoggedIn={onLoggedIn} />` 改为接 (token, user)。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/AuthGate.jsx frontend/src/api.js frontend/src/App.jsx
git commit -m "feat(auth-ui): AuthGate with username field; App.jsx persists currentUser"
```

---

### Task 2.5: 手动 E2E 测试 M2

- [ ] **Step 1: 启动**

```bash
cd backend && uvicorn main:app --reload --port 8001 &
cd frontend && npm run dev
```

- [ ] **Step 2: 用 admin / admin123 登录**

Expected: 进入应用；sidebar 显示完整菜单；右上角（sidebar footer）显示「系统管理员」

- [ ] **Step 3: 用 user_a / user123 登录**

Expected: 进入应用；sidebar 应只显示 「总览 / 分析 / 分析师 / 交易 / 关注 / 关联 / 设置」（M2 阶段还没过滤，先看是否还能登录）

- [ ] **Step 4: 退出**

Expected: token 清空；AccessSession 表该行被删除（验证：`psql ... -c "SELECT * FROM access_sessions"`）

---

## Milestone 3 — 数据隔离 + 菜单过滤 + 视图代理 + 运维面板

### Task 3.1: 所有读端点携带 view_as + 写端点 require_user

**Files:**
- Modify: `backend/main.py`（80+ 路由逐个加 Depends）

- [ ] **Step 1: 选一组高优先级端点改造**

集中在以下读端点加 `?view_as=<id>` 支持 + 在依赖里 resolve `effective_user_id`：

- `/api/holdings/summary`、`/api/holdings/converted`、`/api/holdings`
- `/api/penetration/*` 全部
- `/api/analysis/*`
- `/api/analyst/*`
- `/api/watchlist`（GET）

模式（替换原 endpoint）：

```python
@app.get("/api/holdings/summary")
def holdings_summary(
    request: Request,
    view_as: int | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    effective_uid = get_effective_user_id(request, view_as, user, db)
    return importer.get_holdings_summary(db, user_id=effective_uid)
```

- [ ] **Step 2: services 层加 user_id 参数**

修改 `services/importer.py::get_holdings_summary`：

```python
def get_holdings_summary(db: Session, user_id: int):
    holdings = db.query(Holding).filter(Holding.user_id == user_id).all()
    ...
```

类似改 `penetration.py`、`aggregation.py`、`drillable_funds.py` 的入口函数。

- [ ] **Step 3: 写测试**

新建 `backend/tests/test_user_isolation.py`：

```python
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from main import app
from database import Base, engine
import models, bcrypt
from models import Holding, User

Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
db = S()

def mk_user(name, pw="pw1234", is_advisor=False, is_admin=False):
    u = User(username=name, password_hash=bcrypt.hashpw(pw.encode(), bcrypt.gensalt(4)).decode(),
             is_advisor=is_advisor, is_admin=is_admin, is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    return u

a = mk_user("a")
b = mk_user("b")
# a 持有 stock 1
db.add(Holding(user_id=a.id, security_code="000001", security_name="平安银行",
               quantity=100, price=10, currency="CNY", amount=1000, amount_cny=1000,
               asset_type="a_share_equity"))
# b 持有 stock 2
db.add(Holding(user_id=b.id, security_code="600519", security_name="茅台",
               quantity=10, price=2000, currency="CNY", amount=20000, amount_cny=20000,
               asset_type="a_share_equity"))
db.commit(); db.close()

# 由于端点需要 token，这里省略完整测试；改用 unit test 直接调 service
def test_holdings_summary_filters_by_user():
    from services.importer import get_holdings_summary
    db2 = S()
    s_a = get_holdings_summary(db2, user_id=a.id)
    s_b = get_holdings_summary(db2, user_id=b.id)
    # a 不应看到 b 的 600519
    codes_a = {h.security_code for h in (s_a.get("holdings") or [])}
    codes_b = {h.security_code for h in (s_b.get("holdings") or [])}
    assert "000001" in codes_a and "600519" not in codes_a
    assert "600519" in codes_b and "000001" not in codes_b
```

- [ ] **Step 4: 跑测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_user_isolation.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/main.py backend/services/importer.py backend/services/penetration.py backend/services/aggregation.py backend/services/drillable_funds.py backend/tests/test_user_isolation.py
git commit -m "feat(isolation): holdings read endpoints carry view_as + services accept user_id"
```

---

### Task 3.2: importer.import_excel(user_id, source)

**Files:**
- Modify: `backend/services/importer.py`

- [ ] **Step 1: 改函数签名**

```python
def import_excel(db: Session, file_path: str, user_id: int,
                 import_source: str = "user_upload",
                 file_name: str | None = None) -> dict:
    # 1. 仅删自己
    db.query(Holding).filter(Holding.user_id == user_id).delete()
    db.commit()
    # 2. 读 Excel（保留原 df = pd.read_excel(file_path) 逻辑）
    df = pd.read_excel(file_path)
    rows = []
    for _, r in df.iterrows():
        rows.append(Holding(
            user_id=user_id,
            security_code=str(r.get("code") or r.get("security_code") or ""),
            security_name=str(r.get("name") or r.get("security_name") or ""),
            quantity=float(r.get("quantity") or 0),
            price=float(r.get("price") or 0),
            currency=str(r.get("currency") or "CNY"),
            amount=float(r.get("amount") or 0),
            amount_cny=float(r.get("amount_cny") or 0),
            asset_type=str(r.get("asset_type") or "a_share_equity"),
            import_batch=f"import_{datetime.utcnow().isoformat()}",
        ))
    db.bulk_save_objects(rows); db.commit()
    # 3. 写 import log
    log = HoldingImportLog(
        user_id=user_id, import_source=import_source,
        file_name=file_name or os.path.basename(file_path),
        row_count=len(rows)
    )
    db.add(log); db.commit()
    return {"row_count": len(rows), "user_id": user_id}
```

- [ ] **Step 2: 改 endpoint**

`POST /api/holdings/import` 改为：

```python
@app.post("/api/holdings/import")
def import_holdings(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    # 找最新上传的 Excel
    from pathlib import Path
    src_dir = Path(os.environ.get("SOURCE_DATA_DIR", "sourceData"))
    files = sorted(src_dir.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise HTTPException(404, "未找到 Excel 文件")
    res = import_excel(db, str(files[0]), user_id=user.id,
                       import_source="user_upload", file_name=files[0].name)
    return res
```

- [ ] **Step 3: 写测试**

新建 `backend/tests/test_importer_user.py`：

```python
import os, tempfile
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
import pandas as pd
from sqlalchemy.orm import sessionmaker
from database import Base, engine
import models, bcrypt
from models import User, Holding, HoldingImportLog
from services.importer import import_excel

Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
db = S()
u = User(username="x", password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt(4)).decode(), is_active=True)
db.add(u); db.commit(); db.refresh(u)

def test_import_only_clears_target_user():
    # 已有 a 的一行
    db.add(Holding(user_id=u.id, security_code="OLD", security_name="old", amount=1, amount_cny=1))
    db.commit()
    # 写临时 Excel
    df = pd.DataFrame({"code": ["000001","600519"], "name":["a","b"], "quantity":[1,1], "price":[1,1],
                       "currency":["CNY","CNY"], "amount":[1,1], "amount_cny":[1,1],
                       "asset_type":["a_share_equity","a_share_equity"]})
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        df.to_excel(f.name, index=False); path = f.name
    res = import_excel(db, path, user_id=u.id, import_source="user_upload", file_name="test.xlsx")
    assert res["row_count"] == 2
    codes = {h.security_code for h in db.query(Holding).filter(Holding.user_id == u.id).all()}
    assert codes == {"000001", "600519"}, f"OLD 应该被替换掉，实际={codes}"
    # import log
    logs = db.query(HoldingImportLog).filter(HoldingImportLog.user_id == u.id).all()
    assert len(logs) == 1
    assert logs[0].import_source == "user_upload"
```

- [ ] **Step 4: 跑测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_importer_user.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/importer.py backend/main.py backend/tests/test_importer_user.py
git commit -m "feat(isolation): import_excel(user_id) only clears target user's holdings + writes import log"
```

---

### Task 3.3: scheduler 按 user 遍历

**Files:**
- Modify: `backend/services/scheduler.py`

- [ ] **Step 1: 找 5 个 job**

```bash
cd backend && grep -n "def job_" services/scheduler.py
```

- [ ] **Step 2: 改造 realtime_prices**

替换函数体内 holdings 查询：

```python
def job_fetch_realtime_prices(force=False, user_id=None):
    """按 user 遍历持仓。user_id=None 表示所有 user。"""
    from models import Holding, User
    db = SessionLocal()
    try:
        q = db.query(Holding)
        if user_id: q = q.filter(Holding.user_id == user_id)
        # ... 剩余逻辑保持 ...
```

同样改造 `job_fill_snapshot_gaps_smart`、`job_financial_fundamentals`、`job_backfill_gaps`、`job_info_stock_news`、`job_info_announcements_research`。

- [ ] **Step 3: 写测试**

新建 `backend/tests/test_scheduler_user_traversal.py`：

```python
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
from sqlalchemy.orm import sessionmaker
from database import Base, engine
import models, bcrypt
from models import User, Holding

Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
db = S()
u1 = User(username="u1", password_hash="x", is_active=True)
u2 = User(username="u2", password_hash="x", is_active=True)
db.add_all([u1, u2]); db.commit()
db.add_all([
    Holding(user_id=u1.id, security_code="A", security_name="a", amount=1, amount_cny=1),
    Holding(user_id=u2.id, security_code="B", security_name="b", amount=1, amount_cny=1),
])
db.commit()

def test_query_filters_by_user():
    codes = {h.security_code for h in db.query(Holding).filter(Holding.user_id == u1.id).all()}
    assert codes == {"A"}
```

- [ ] **Step 4: 跑测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_scheduler_user_traversal.py -v
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/services/scheduler.py backend/tests/test_scheduler_user_traversal.py
git commit -m "feat(scheduler): 5 jobs now iterate per-user holdings"
```

---

### Task 3.4: 前端 App.jsx — 菜单过滤 + view_as banner + 用户切换

**Files:**
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: 加 visibility 字段到 TABS**

```jsx
const TABS = [
  { id: 'overview',   label: '总览',     visibility: ['user','advisor','admin'], icon: ICONS.overview },
  { id: 'analysis',   label: '分析',     visibility: ['user','advisor','admin'], icon: ICONS.analysis },
  { id: 'analyst',    label: '分析师',   visibility: ['user','advisor','admin'], icon: ICONS.analyst },
  { id: 'trading',    label: '交易',     visibility: ['user'],                   icon: ICONS.trading },
  { id: 'watch',      label: '关注',     visibility: ['user','advisor','admin'], icon: ICONS.watch },
  { id: 'relation',   label: '关联',     visibility: ['user','advisor'],        icon: 'M17 20h5v-2a4 4 0 00-3-3.87' },
  { id: 'ops',        label: '运维',     visibility: ['admin'],                  icon: 'M3 12l2-2 4 4 8-8' },
  { id: 'dataGap',    label: '数据补足', visibility: ['admin'],                  icon: 'M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z' },
  { id: 'data',       label: '数据',     visibility: ['advisor','admin'],        icon: ICONS.data },
  { id: 'strategies', label: 'API策略',  visibility: ['admin'],                  icon: 'M13 10V3L4 14h7v7l9-11h-7z' },
  { id: 'settings',   label: '设置',     visibility: ['user','advisor','admin'], icon: ICONS.settings },
]
```

- [ ] **Step 2: 过滤逻辑**

```jsx
const visibleTabs = useMemo(() => {
  if (!currentUser) return []
  return TABS.filter(t => {
    if (currentUser.is_admin) return true
    if (currentUser.is_advisor) return t.visibility.includes('advisor') || t.visibility.includes('user')
    return t.visibility.includes('user')
  })
}, [currentUser])

const userRole = currentUser?.is_admin ? 'admin' : currentUser?.is_advisor ? 'advisor' : 'user'
```

- [ ] **Step 3: view_as 状态**

```jsx
const [viewAsUser, setViewAsUser] = useState(null)
const [allUsers, setAllUsers] = useState([])

// 加载可切换用户列表（advisor/admin）
useEffect(() => {
  if (currentUser?.is_advisor || currentUser?.is_admin) {
    api.getUsers().then(r => setAllUsers(r.users || [])).catch(() => {})
  }
}, [currentUser])
```

- [ ] **Step 4: sidebar 改造**

替换 sidebar 的 TABS 渲染：

```jsx
{visibleTabs.map(tab => (
  <button key={tab.id} className={`nav-item ${activeTab === tab.id ? 'active' : ''}`}
    onClick={() => setActiveTab(tab.id)}>
    {tab.label}
  </button>
))}
```

footer 替换为：

```jsx
<div className="sidebar-footer">
  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
    {currentUser?.display_name || currentUser?.username}
    <span style={{ marginLeft: 6, opacity: 0.6 }}>· {userRole}</span>
  </div>
  {(currentUser?.is_advisor || currentUser?.is_admin) && (
    <select value={viewAsUser?.id || ''} onChange={e => {
      const id = e.target.value ? +e.target.value : null
      setViewAsUser(id ? allUsers.find(u => u.id === id) : null)
    }} style={{ padding: '2px 4px', fontSize: 10 }}>
      <option value="">切换查看...</option>
      {allUsers.filter(u => u.id !== currentUser.id).map(u => (
        <option key={u.id} value={u.id}>{u.display_name || u.username}</option>
      ))}
    </select>
  )}
  <button onClick={onLogout} className="btn-ghost">登出</button>
</div>
```

- [ ] **Step 5: view_as banner**

在主区顶部加：

```jsx
{viewAsUser && (
  <div className="view-as-banner" style={{
    padding: '8px 16px', background: 'var(--accent-soft)', borderBottom: '1px solid var(--border)',
    display: 'flex', justifyContent: 'space-between', alignItems: 'center'
  }}>
    <span>👀 正在查看：<strong>{viewAsUser.display_name || viewAsUser.username}</strong> 的视图</span>
    <button onClick={() => setViewAsUser(null)} className="btn-ghost">切回自己</button>
  </div>
)}
```

- [ ] **Step 6: api.js 加 getUsers**

```js
export const getUsers = () => api.get('/auth/users').then(r => r.data)
```

- [ ] **Step 7: main.py 加 /api/auth/users**

```python
@app.get("/api/auth/users")
def list_users(request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_advisor)):
    users = db.query(User).filter(User.is_active == True).all()
    return {"users": [{
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "is_advisor": u.is_advisor, "is_admin": u.is_admin,
    } for u in users]}
```

- [ ] **Step 8: 提交**

```bash
git add frontend/src/App.jsx frontend/src/api.js backend/main.py
git commit -m "feat(isolation-ui): menu filtering by role + view_as banner + account switcher dropdown"
```

---

### Task 3.5: TradingPanel placeholder + OpsPanel 接管运维按钮

**Files:**
- Modify: `frontend/src/components/TradingPanel.jsx`
- Create: `frontend/src/components/OpsPanel.jsx`

- [ ] **Step 1: TradingPanel 改 placeholder**

```jsx
import React from 'react'

export default function TradingPanel() {
  return (
    <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>
      <h2>交易维护</h2>
      <p>本功能将在下一版本上线。</p>
      <p>当前请使用「导入」功能上传 Excel 持仓文件。</p>
      <button onClick={() => alert('导入功能即将上线')} style={{ padding: '8px 16px', marginTop: 16 }}>
        导入持仓（即将上线）
      </button>
    </div>
  )
}
```

- [ ] **Step 2: OpsPanel 新建**

```jsx
import React from 'react'
import { postImport, postCrawlAll, postPenetration, postRecalcCsi300,
         postFillPrices, triggerSchedulerJob } from '../api'

const Button = ({ label, action, color }) => (
  <button onClick={action} style={{
    padding: '12px 16px', margin: 8, border: '1px solid var(--border)',
    borderRadius: 6, background: color || 'var(--bg)', color: 'var(--text)',
    cursor: 'pointer', minWidth: 180
  }}>{label}</button>
)

export default function OpsPanel() {
  async function run(label, fn) {
    if (!confirm(`确认执行 ${label}？`)) return
    try {
      const r = await fn()
      alert(`${label} 已完成：${JSON.stringify(r).slice(0, 200)}`)
    } catch (e) {
      alert(`${label} 失败：${e?.response?.data?.detail || e.message}`)
    }
  }
  return (
    <div style={{ padding: 24 }}>
      <h2>运维</h2>
      <p style={{ color: 'var(--text-muted)' }}>仅管理员可见。执行数据维护操作。</p>
      <div style={{ display: 'flex', flexWrap: 'wrap' }}>
        <Button label="导入持仓 Excel" action={() => run('导入持仓', postImport)} />
        <Button label="抓取价格/全量" action={() => run('全量抓取', postCrawlAll)} />
        <Button label="执行下钻" action={() => run('下钻', postPenetration)} />
        <Button label="重算 CSI300" action={() => run('CSI300 重算', postRecalcCsi300)} />
        <Button label="补齐价格" action={() => run('补价', postFillPrices)} />
        <Button label="触发 scheduler (detect_data_gaps)" action={() => run('detect_data_gaps', () => triggerSchedulerJob('detect_data_gaps'))} />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: App.jsx 路由**

```jsx
case 'ops': return <OpsPanel />
case 'trading': return <TradingPanel />
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/TradingPanel.jsx frontend/src/components/OpsPanel.jsx frontend/src/App.jsx
git commit -m "feat(ops): OpsPanel takes over admin maintenance buttons; TradingPanel becomes placeholder"
```

---

### Task 3.6: 所有读端点携带 view_as 参数（前端）

**Files:**
- Modify: `frontend/src/api.js`

- [ ] **Step 1: view_as helper**

```jsx
// 在 App.jsx 内
const viewAsParam = viewAsUser?.id ? { view_as: viewAsUser.id } : {}
// 传给每个 api 调用
```

- [ ] **Step 2: 改 api.js helpers 接受 view_as**

把 `getHoldingsSummary` / `getPenetrationTable` / `getKpi` / `getTrend` / `getHoldingsConverted` / `getPenetrationSummary` / `getIndustryChain` / `getGrowthAnalysis` / `getValuation` / `getAnalystCoreCompanies` / `getAnalystStockDetail` / `getAnalystIndustryChains` / `getFullHolding` / `getFullHoldingTable` / `getDimension` / `getDimensionDetail` / `getPortfolioVsCsi300` / `getFullHoldingSummary` / `getDrillableIndices` / `getIndexDrill` / `getAllDrilledStocks` / `getDimensionDrilled` / `getWatchlist` 改为接 `{ viewAs, ...otherParams }`：

例：

```js
export const getHoldingsSummary = ({ viewAs } = {}) =>
  api.get('/holdings/summary', { params: viewAs ? { view_as: viewAs } : {} }).then(r => r.data)
```

对所有读端点应用同样模式。

- [ ] **Step 3: 改调用方**

所有 `OverviewPanel`、`AnalysisPanel`、`AnalystPanel`、`WatchPanel`、`FullHoldingTable` 等组件内调用改为：

```jsx
const viewAs = useContext(ViewAsContext)  // 包装 useContext
api.getHoldingsSummary({ viewAs })
```

或在 App.jsx 顶层通过 props 传 `viewAsUser?.id`。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/api.js frontend/src/components/*.jsx frontend/src/App.jsx
git commit -m "feat(view-as): read API calls carry view_as when advisor/admin switches account"
```

---

## Milestone 4 — 顾问-客户关联

### Task 4.1: 后端 `/api/auth/relations/*` 端点

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: 加 Pydantic schemas**

```python
class RelationCreateIn(BaseModel):
    advisor_username: str | None = None
    client_username: str | None = None
```

- [ ] **Step 2: 加 4 个端点**

放在 `/api/auth/me` 之后：

```python
@app.get("/api/auth/relations")
def list_relations(request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_user)):
    as_adv = db.query(UserRelation, User).join(
        User, User.id == UserRelation.client_user_id
    ).filter(UserRelation.advisor_user_id == user.id).all()
    as_cli = db.query(UserRelation, User).join(
        User, User.id == UserRelation.advisor_user_id
    ).filter(UserRelation.client_user_id == user.id).all()
    def to_dict(rel, other):
        return {
            "id": rel.id,
            "advisor_user_id": rel.advisor_user_id,
            "advisor_username": other.username if rel.client_user_id == user.id else user.username,
            "client_user_id": rel.client_user_id,
            "client_username": other.username if rel.advisor_user_id == user.id else user.username,
            "status": rel.status,
            "initiator_user_id": rel.initiator_user_id,
            "created_at": rel.created_at.isoformat() if rel.created_at else None,
        }
    return {
        "as_advisor": [to_dict(r, u) for r, u in as_adv],
        "as_client": [to_dict(r, u) for r, u in as_cli],
    }


@app.post("/api/auth/relations")
def create_relation(body: RelationCreateIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    if body.advisor_username:
        advisor = db.query(User).filter(User.username == body.advisor_username,
                                        User.is_advisor == True, User.is_active == True).first()
        if not advisor: raise HTTPException(404, "顾问不存在")
        advisor_id, client_id = advisor.id, user.id
    elif body.client_username:
        client = db.query(User).filter(User.username == body.client_username,
                                       User.is_active == True).first()
        if not client: raise HTTPException(404, "用户不存在")
        advisor_id, client_id = user.id, client.id
        if not user.is_advisor:
            raise HTTPException(403, "只有顾问或用户能发起关联")
    else:
        raise HTTPException(400, "请提供 advisor_username 或 client_username")
    existing = db.query(UserRelation).filter(
        UserRelation.advisor_user_id == advisor_id,
        UserRelation.client_user_id == client_id
    ).first()
    if existing and existing.status != "CANCELLED":
        return {"status": "exists", "relation_id": existing.id}
    rel = UserRelation(advisor_user_id=advisor_id, client_user_id=client_id,
                       status="PENDING", initiator_user_id=user.id)
    db.add(rel); db.commit()
    return {"status": "created", "relation_id": rel.id}


@app.post("/api/auth/relations/{rel_id}/confirm")
def confirm_relation(rel_id: int, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_user)):
    rel = db.query(UserRelation).filter(UserRelation.id == rel_id).first()
    if not rel: raise HTTPException(404, "关联不存在")
    if user.id not in (rel.advisor_user_id, rel.client_user_id):
        raise HTTPException(403, "无权操作")
    if rel.initiator_user_id == user.id:
        raise HTTPException(400, "不能确认自己发起的关联")
    rel.status = "ACTIVE"; rel.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "active"}


@app.post("/api/auth/relations/{rel_id}/cancel")
def cancel_relation(rel_id: int, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    rel = db.query(UserRelation).filter(UserRelation.id == rel_id).first()
    if not rel: raise HTTPException(404, "关联不存在")
    if user.id not in (rel.advisor_user_id, rel.client_user_id) and not user.is_admin:
        raise HTTPException(403, "无权操作")
    rel.status = "CANCELLED"; rel.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "cancelled"}
```

- [ ] **Step 2: 写测试**

新建 `backend/tests/test_user_relations.py`：

```python
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from main import app
from database import Base, engine
import models, bcrypt
from models import User, UserRelation

Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
db = S()
adv = User(username="adv", password_hash=bcrypt.hashpw(b"a", bcrypt.gensalt(4)).decode(),
           is_advisor=True, is_active=True)
cli = User(username="cli", password_hash=bcrypt.hashpw(b"c", bcrypt.gensalt(4)).decode(),
           is_active=True)
db.add_all([adv, cli]); db.commit()
db.close()

client = TestClient(app)

def login(u, pw):
    r = client.post("/api/auth/login", json={"username": u, "password": pw})
    assert r.status_code == 200
    return r.json()["token"]

def test_pending_to_active_flow():
    t_cli = login("cli", "c")
    # client 发起
    r = client.post("/api/auth/relations", json={"advisor_username": "adv"},
                    headers={"x-session-token": t_cli})
    assert r.status_code == 200
    rel_id = r.json()["relation_id"]
    # advisor 确认
    t_adv = login("adv", "a")
    r = client.post(f"/api/auth/relations/{rel_id}/confirm",
                    headers={"x-session-token": t_adv})
    assert r.status_code == 200
    assert r.json()["status"] == "active"

def test_cannot_confirm_self_initiated():
    t_cli = login("cli", "c")
    r = client.post("/api/auth/relations", json={"advisor_username": "adv"},
                    headers={"x-session-token": t_cli})
    rel_id = r.json()["relation_id"]
    # client 不能确认自己
    r = client.post(f"/api/auth/relations/{rel_id}/confirm",
                    headers={"x-session-token": t_cli})
    assert r.status_code == 400
```

- [ ] **Step 3: 跑测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_user_relations.py -v
```

Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add backend/main.py backend/tests/test_user_relations.py
git commit -m "feat(relations): /api/auth/relations/* endpoints with PENDING→ACTIVE flow"
```

---

### Task 4.2: 前端 RelationPanel

**Files:**
- Create: `frontend/src/components/RelationPanel.jsx`
- Modify: `frontend/src/api.js`

- [ ] **Step 1: api.js 加 helpers**

```js
export const listRelations = () => api.get('/auth/relations').then(r => r.data)
export const createRelation = (body) => api.post('/auth/relations', body).then(r => r.data)
export const confirmRelation = (id) => api.post(`/auth/relations/${id}/confirm`).then(r => r.data)
export const cancelRelation = (id) => api.post(`/auth/relations/${id}/cancel`).then(r => r.data)
```

- [ ] **Step 2: RelationPanel.jsx**

```jsx
import React, { useEffect, useState } from 'react'
import { listRelations, createRelation, confirmRelation, cancelRelation, getUsers } from '../api'

export default function RelationPanel({ currentUser }) {
  const [relations, setRelations] = useState({ as_advisor: [], as_client: [] })
  const [users, setUsers] = useState([])
  const [inviteTarget, setInviteTarget] = useState('')

  function refresh() {
    listRelations().then(setRelations)
  }
  useEffect(() => { refresh() }, [])
  useEffect(() => {
    if (currentUser?.is_admin) {
      getUsers().then(r => setUsers(r.users || []))
    } else {
      setUsers([])
    }
  }, [currentUser])

  async function invite() {
    if (!inviteTarget) return
    const body = currentUser?.is_advisor || currentUser?.is_admin
      ? { client_username: inviteTarget }
      : { advisor_username: inviteTarget }
    try {
      await createRelation(body)
      setInviteTarget(''); refresh()
    } catch (e) {
      alert(e?.response?.data?.detail || '发起失败')
    }
  }

  async function act(rel, action) {
    try { await action(rel.id); refresh() } catch (e) {
      alert(e?.response?.data?.detail || '操作失败')
    }
  }

  const renderRel = (rel) => {
    const other = currentUser.is_advisor ? rel.client_username : rel.advisor_username
    return (
      <tr key={rel.id}>
        <td>{other}</td>
        <td>{rel.status}</td>
        <td>{rel.initiator_user_id === currentUser.id ? '我发起' : '对方发起'}</td>
        <td>
          {rel.status === 'PENDING' && rel.initiator_user_id !== currentUser.id && (
            <button onClick={() => act(rel, confirmRelation)}>确认</button>
          )}
          {rel.status !== 'CANCELLED' && (
            <button onClick={() => act(rel, cancelRelation)} style={{ marginLeft: 4 }}>取消</button>
          )}
        </td>
      </tr>
    )
  }

  const allRels = [
    ...relations.as_advisor.map(r => ({ ...r, direction: '作为顾问' })),
    ...relations.as_client.map(r => ({ ...r, direction: '作为客户' })),
  ]

  return (
    <div style={{ padding: 24 }}>
      <h2>关联</h2>
      <div style={{ marginBottom: 16, display: 'flex', gap: 8 }}>
        <select value={inviteTarget} onChange={e => setInviteTarget(e.target.value)}>
          <option value="">{currentUser?.is_advisor ? '选择客户...' : '选择顾问...'}</option>
          {(currentUser?.is_advisor ? users : users.filter(u => u.is_advisor && u.id !== currentUser.id))
            .map(u => <option key={u.id} value={u.username}>{u.display_name || u.username}</option>)}
        </select>
        <button onClick={invite} disabled={!inviteTarget}>邀请</button>
      </div>
      <table className="data-table">
        <thead>
          <tr><th>对方</th><th>状态</th><th>发起方</th><th>操作</th></tr>
        </thead>
        <tbody>{allRels.map(renderRel)}</tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 3: App.jsx 路由**

```jsx
case 'relation': return <RelationPanel currentUser={currentUser} />
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/RelationPanel.jsx frontend/src/api.js frontend/src/App.jsx
git commit -m "feat(relations-ui): RelationPanel with invite/confirm/cancel flow"
```

---

## Milestone 5 — 数据补足

### Task 5.1: scheduler 新增 job_detect_data_gaps

**Files:**
- Modify: `backend/services/scheduler.py`
- Create: `backend/services/data_gap_detector.py`

- [ ] **Step 1: 新建 detector**

新建 `backend/services/data_gap_detector.py`：

```python
"""扫描 3 类数据缺口，写入 data_gap_report"""
from datetime import datetime, date
from sqlalchemy import func
from sqlalchemy.orm import Session
from models import (User, Holding, FundIndexMap, IndexClassification,
                    IndexConstituentSnapshot, AnalystCompanyReport,
                    DataGapReport)


def _prev_month_end(today: date) -> date:
    if today.month == 1:
        return date(today.year - 1, 12, 31)
    import calendar
    last_day = calendar.monthrange(today.year, today.month - 1)[1]
    return date(today.year, today.month - 1, last_day)


def detect_all_gaps(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    inserted = []
    users = db.query(User).filter(User.is_active == True).all()
    # 1. stock_report_gap：每个 user 持仓的穿透后 ≥0.8%
    for u in users:
        total_est = db.query(func.coalesce(func.sum(Holding.amount_cny), 0)).filter(
            Holding.user_id == u.id).scalar() or 0
        if total_est <= 0:
            continue
        holdings = db.query(Holding).filter(Holding.user_id == u.id).all()
        # 简单版：直接看 direct_stock 是否 ≥0.8%；下钻部分留 TODO (调 penetration_v2.calculate)
        # 这里用基础实现：
        for h in holdings:
            if h.amount_cny and h.amount_cny / total_est >= 0.008 and h.asset_type == "a_share_equity":
                code = h.security_code
                has = db.query(AnalystCompanyReport).filter(
                    AnalystCompanyReport.stock_code == code).first()
                if not has:
                    if not _gap_exists(db, user_id=u.id, gap_type="stock_report", stock_code=code):
                        g = DataGapReport(user_id=u.id, gap_type="stock_report", stock_code=code,
                                          description=f"{h.security_name} 占比 ≥0.8%，无报告")
                        db.add(g); inserted.append(g)
    # 2. index_constituent_gap：上月月底缺快照
    last_month_end = _prev_month_end(today)
    for fmap in db.query(FundIndexMap).filter(FundIndexMap.index_code.isnot(None)).distinct(FundIndexMap.index_code):
        has = db.query(IndexConstituentSnapshot).filter(
            IndexConstituentSnapshot.index_code == fmap.index_code,
            IndexConstituentSnapshot.as_of_date == last_month_end
        ).first()
        if not has:
            if not _gap_exists(db, gap_type="index_constituent", index_code=fmap.index_code,
                              as_of_date=last_month_end):
                g = DataGapReport(gap_type="index_constituent", index_code=fmap.index_code,
                                  as_of_date=last_month_end,
                                  description=f"{fmap.index_name or fmap.index_code} 缺 {last_month_end} 快照")
                db.add(g); inserted.append(g)
    # 3. index_classification_gap
    for fmap in db.query(FundIndexMap).filter(FundIndexMap.index_code.isnot(None)).distinct(FundIndexMap.index_code):
        has = db.query(IndexClassification).filter(
            IndexClassification.index_code == fmap.index_code).first()
        if not has:
            if not _gap_exists(db, gap_type="index_classification", index_code=fmap.index_code):
                g = DataGapReport(gap_type="index_classification", index_code=fmap.index_code,
                                  description=f"{fmap.index_name or fmap.index_code} 无分类")
                db.add(g); inserted.append(g)
    db.commit()
    return {"inserted": len(inserted)}


def _gap_exists(db, **kwargs) -> bool:
    q = db.query(DataGapReport).filter(DataGapReport.status == "OPEN")
    for k, v in kwargs.items():
        q = q.filter(getattr(DataGapReport, k) == v)
    return db.query(q.exists()).scalar()
```

- [ ] **Step 2: 写测试**

新建 `backend/tests/test_data_gap_detector.py`：

```python
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
from datetime import date
from sqlalchemy.orm import sessionmaker
from database import Base, engine
import models
from models import (User, Holding, FundIndexMap, IndexClassification,
                    IndexConstituentSnapshot, AnalystCompanyReport, DataGapReport)
from services.data_gap_detector import detect_all_gaps

Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
db = S()
u = User(username="u", password_hash="x", is_active=True)
db.add(u); db.commit()
# user 持有 NVDA 占 50%
db.add(Holding(user_id=u.id, security_code="NVDA", security_name="nvda",
               amount=500, amount_cny=500, asset_type="us_stock", quantity=1, price=500))
db.commit()

def test_stock_report_gap_detected():
    detect_all_gaps(db, today=date(2026, 7, 5))
    gaps = db.query(DataGapReport).filter(DataGapReport.gap_type == "stock_report").all()
    assert any(g.stock_code == "NVDA" for g in gaps)

def test_index_classification_gap_detected():
    fmap = FundIndexMap(fund_code="F1", index_code="000300", as_of_date=date(2026, 1, 1),
                        fund_name="f", index_name="沪深300")
    db.add(fmap); db.commit()
    detect_all_gaps(db, today=date(2026, 7, 5))
    gaps = db.query(DataGapReport).filter(DataGapReport.gap_type == "index_classification").all()
    assert any(g.index_code == "000300" for g in gaps)

def test_no_duplicate_open_gaps():
    detect_all_gaps(db, today=date(2026, 7, 5))
    detect_all_gaps(db, today=date(2026, 7, 5))
    gaps = db.query(DataGapReport).filter(DataGapReport.status == "OPEN").all()
    codes = [g.stock_code for g in gaps if g.gap_type == "stock_report"]
    assert len(codes) == len(set(codes))
```

- [ ] **Step 3: 跑测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_data_gap_detector.py -v
```

Expected: PASS

- [ ] **Step 4: 注册到 scheduler**

修改 `backend/services/scheduler.py::start_scheduler()`，在已有 job 后追加：

```python
    scheduler.add_job(
        _wrap_job("detect_data_gaps", job_detect_data_gaps),
        "cron", hour=6, minute=50,
        id="detect_data_gaps",
        name="数据补足检测",
        max_instances=1, misfire_grace_time=180,
    )

def job_detect_data_gaps():
    from database import SessionLocal
    from services.data_gap_detector import detect_all_gaps
    db = SessionLocal()
    try:
        result = detect_all_gaps(db)
        return result
    finally:
        db.close()
```

- [ ] **Step 5: 提交**

```bash
git add backend/services/data_gap_detector.py backend/services/scheduler.py backend/tests/test_data_gap_detector.py
git commit -m "feat(data-gap): detector for stock_report/index_constituent/index_classification gaps + scheduler job"
```

---

### Task 5.2: admin 端点 /api/admin/gap-report + fix-gap

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: 加端点**

```python
@app.get("/api/admin/gap-report")
def list_gaps(gap_type: str | None = None, status: str = "OPEN",
              db: Session = Depends(get_db), user: User = Depends(require_admin)):
    q = db.query(DataGapReport)
    if gap_type: q = q.filter(DataGapReport.gap_type == gap_type)
    if status: q = q.filter(DataGapReport.status == status)
    items = q.order_by(DataGapReport.detected_at.desc()).limit(500).all()
    return {
        "items": [{
            "id": g.id, "user_id": g.user_id, "gap_type": g.gap_type,
            "stock_code": g.stock_code, "index_code": g.index_code,
            "as_of_date": g.as_of_date.isoformat() if g.as_of_date else None,
            "description": g.description, "status": g.status,
            "detected_at": g.detected_at.isoformat() if g.detected_at else None,
        } for g in items],
        "counts": {
            "OPEN": db.query(DataGapReport).filter(DataGapReport.status == "OPEN").count(),
            "FIXED": db.query(DataGapReport).filter(DataGapReport.status == "FIXED").count(),
        }
    }


@app.post("/api/admin/fix-gap/{gap_id}")
def fix_gap(gap_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    g = db.query(DataGapReport).filter(DataGapReport.id == gap_id).first()
    if not g: raise HTTPException(404, "缺口记录不存在")
    if g.gap_type == "index_constituent" and g.index_code and g.as_of_date:
        # 触发抓取
        try:
            from crawlers.index_constituents import crawl_constituents
            crawl_constituents(g.index_code, db, as_of_date=g.as_of_date)
        except Exception as e:
            raise HTTPException(500, f"抓取失败: {e}")
    # index_classification 由前端录入后直接调 _set_classification
    g.status = "FIXED"; g.resolved_at = datetime.utcnow(); db.commit()
    return {"status": "fixed", "id": g.id}


@app.post("/api/admin/index-classification")
def set_classification(body: dict, db: Session = Depends(get_db),
                       user: User = Depends(require_admin)):
    """body: {index_code, index_name?, category, theme?, benchmark_formula?}"""
    code = body.get("index_code")
    if not code: raise HTTPException(400, "index_code 必填")
    cls = db.query(IndexClassification).filter(IndexClassification.index_code == code).first()
    if not cls:
        cls = IndexClassification(index_code=code); db.add(cls)
    cls.index_name = body.get("index_name") or cls.index_name
    cls.category = body.get("category") or cls.category
    cls.theme = body.get("theme") or cls.theme
    cls.benchmark_formula = body.get("benchmark_formula") or cls.benchmark_formula
    db.commit()
    return {"status": "ok"}
```

- [ ] **Step 2: 写测试**

新建 `backend/tests/test_admin_gap_endpoints.py`：

```python
import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from main import app
from database import Base, engine
import models, bcrypt
from models import User, DataGapReport

Base.metadata.create_all(engine)
S = sessionmaker(bind=engine)
db = S()
admin = User(username="admin", password_hash=bcrypt.hashpw(b"admin123", bcrypt.gensalt(4)).decode(),
             is_admin=True, is_active=True)
user = User(username="user_a", password_hash=bcrypt.hashpw(b"user123", bcrypt.gensalt(4)).decode(),
            is_active=True)
db.add_all([admin, user]); db.commit()
# 一条 gap
g = DataGapReport(user_id=user.id, gap_type="index_classification", index_code="000300",
                  description="test", status="OPEN")
db.add(g); db.commit(); db.close()

client = TestClient(app)

def login(u, pw):
    r = client.post("/api/auth/login", json={"username": u, "password": pw})
    return r.json()["token"]

def test_list_gaps_as_admin():
    t = login("admin", "admin123")
    r = client.get("/api/admin/gap-report", headers={"x-session-token": t})
    assert r.status_code == 200
    assert len(r.json()["items"]) >= 1

def test_list_gaps_as_user_forbidden():
    t = login("user_a", "user123")
    r = client.get("/api/admin/gap-report", headers={"x-session-token": t})
    assert r.status_code == 403

def test_set_classification():
    t = login("admin", "admin123")
    r = client.post("/api/admin/index-classification",
        json={"index_code": "000300", "category": "宽基", "theme": "大盘"},
        headers={"x-session-token": t})
    assert r.status_code == 200
```

- [ ] **Step 3: 跑测试**

```bash
cd backend && PYTHONPATH=. pytest tests/test_admin_gap_endpoints.py -v
```

Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add backend/main.py backend/tests/test_admin_gap_endpoints.py
git commit -m "feat(admin-gap): /api/admin/gap-report + fix-gap + index-classification endpoints"
```

---

### Task 5.3: 前端 DataGapPanel

**Files:**
- Create: `frontend/src/components/DataGapPanel.jsx`
- Modify: `frontend/src/api.js`

- [ ] **Step 1: api.js 加 helpers**

```js
export const getGapReport = (params = {}) => api.get('/admin/gap-report', { params }).then(r => r.data)
export const fixGap = (id) => api.post(`/admin/fix-gap/${id}`).then(r => r.data)
export const setClassification = (body) => api.post('/admin/index-classification', body).then(r => r.data)
```

- [ ] **Step 2: DataGapPanel.jsx**

```jsx
import React, { useEffect, useState } from 'react'
import { getGapReport, fixGap, setClassification } from '../api'

const TABS = [
  { key: 'stock_report', label: '个股报告' },
  { key: 'index_constituent', label: '指数构成' },
  { key: 'index_classification', label: '指数分类' },
]

export default function DataGapPanel() {
  const [tab, setTab] = useState('stock_report')
  const [data, setData] = useState({ items: [], counts: { OPEN: 0, FIXED: 0 } })
  const [editing, setEditing] = useState(null)

  function refresh() {
    getGapReport({ gap_type: tab, status: 'OPEN' }).then(setData)
  }
  useEffect(refresh, [tab])

  async function handleFix(g) {
    if (g.gap_type === 'index_classification') {
      setEditing(g)
      return
    }
    if (!confirm(`修复 ${g.id}？`)) return
    try {
      await fixGap(g.id); refresh()
    } catch (e) {
      alert(e?.response?.data?.detail || '修复失败')
    }
  }

  async function submitClassification() {
    try {
      await setClassification({
        index_code: editing.index_code,
        category: editing.category || '',
        theme: editing.theme || '',
      })
      await fixGap(editing.id)
      setEditing(null); refresh()
    } catch (e) {
      alert(e?.response?.data?.detail || '保存失败')
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <h2>数据补足 <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>OPEN: {data.counts.OPEN}</span></h2>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            style={{ padding: '6px 14px', border: '1px solid var(--border)',
              background: tab === t.key ? 'var(--accent)' : 'transparent',
              color: tab === t.key ? '#fff' : 'var(--text)', borderRadius: 4 }}>
            {t.label}
          </button>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr>
            {tab === 'stock_report' && <><th>客户ID</th><th>股票代码</th><th>描述</th></>}
            {tab === 'index_constituent' && <><th>指数代码</th><th>缺失日期</th><th>描述</th></>}
            {tab === 'index_classification' && <><th>指数代码</th><th>描述</th></>}
            <th>检测时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map(g => (
            <tr key={g.id}>
              {tab === 'stock_report' && <>
                <td>{g.user_id}</td><td>{g.stock_code}</td><td>{g.description}</td>
              </>}
              {tab === 'index_constituent' && <>
                <td>{g.index_code}</td><td>{g.as_of_date}</td><td>{g.description}</td>
              </>}
              {tab === 'index_classification' && <>
                <td>{g.index_code}</td><td>{g.description}</td>
              </>}
              <td>{g.detected_at?.slice(0, 16)}</td>
              <td><button onClick={() => handleFix(g)}>立即修复</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: 'var(--bg)', padding: 24, borderRadius: 8, minWidth: 360 }}>
            <h3>编辑分类 - {editing.index_code}</h3>
            <div><label>大类</label>
              <input value={editing.category || ''} onChange={e => setEditing({...editing, category: e.target.value})}
                style={{ width: '100%', padding: 6, margin: '4px 0 12px' }} /></div>
            <div><label>主题</label>
              <input value={editing.theme || ''} onChange={e => setEditing({...editing, theme: e.target.value})}
                style={{ width: '100%', padding: 6, margin: '4px 0 12px' }} /></div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setEditing(null)}>取消</button>
              <button onClick={submitClassification}>保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: App.jsx 路由**

```jsx
case 'dataGap': return <DataGapPanel />
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/DataGapPanel.jsx frontend/src/api.js frontend/src/App.jsx
git commit -m "feat(data-gap-ui): DataGapPanel with 3 tabs + fix flow + classification editor"
```

---

## 最终验证（全部 milestone 后）

### Task F.1: 完整剧本

- [ ] **Step 1: 启动 backend + frontend**

```bash
cd backend && uvicorn main:app --reload --port 8001 &
cd frontend && npm run dev
```

- [ ] **Step 2: 跑全部测试**

```bash
cd backend && PYTHONPATH=. pytest tests/ -v
```

Expected: 全 PASS（包含 test_auth_login / test_user_isolation / test_user_relations / test_data_gap_detector / test_admin_gap_endpoints / test_view_as 等）

- [ ] **Step 3: 9 步剧本（spec §8.2）**

| # | 操作 | 期望 |
|---|------|------|
| 1 | admin 登录 | 看到 9 个菜单 |
| 2 | user_a 登录 | 看到 7 个菜单（无运维/数据补足/API策略） |
| 3 | advisor_x 登录 | 看到 7 个菜单（无运维/数据补足/API策略/交易） |
| 4 | advisor_x 切到 user_a 视图 | banner 显示「正在查看：王先生」；总览是 user_a 的 |
| 5 | advisor_x 在 user_a 视图加关注 NVDA | watchlist 新增 (user_a.id, NVDA) |
| 6 | user_a 关联 advisor_x（PENDING）；advisor_x 确认 → ACTIVE | 双方「关联」tab 看到 ACTIVE |
| 7 | admin 触发 detect_data_gaps | data_gap_report 插入三类缺口 |
| 8 | admin 在「数据补足」录入 000300 分类 | data_gap_report 行 status=FIXED |
| 9 | 退出 admin | token 清空，session 行删，reload 跳登录 |

- [ ] **Step 4: 提交（如果还有修改）**

```bash
git status && git add -A && git commit -m "chore: verify end-to-end"
```

---

## 风险与回退

- **回退单 milestone**：每个 milestone 是一次独立 commit；`git revert HEAD~N` 即可回退
- **数据库升级失败**：`init_db` 内 _apply_user_id_migrations 是幂等的；如失败可手动 DROP 新表 + 删 ALTER 列
- **现有云端**：本期不动；用户确认云端保持不变

---

## 相关 memory

- `portfoliom-deploy-rule` — 云端不动
- `portfolio-data-flow` — 持仓数据流参考
- `cloud-fastapi-pending-rollback` — db.rollback 注意事项