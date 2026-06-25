"""测试 /api/auth/login 多用户版本 + /api/auth/me + middleware 注入"""
import os
os.environ["APP_PASSWORD"] = ""

import bcrypt
import pytest
import tempfile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
import database as _database
import main as _main
from database import Base
from main import app
from models import User, AccessSession


@pytest.fixture
def fresh_db(monkeypatch):
    """每个测试用独立的临时文件 SQLite（避免 :memory: 多连接隔离问题）"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    # monkeypatch database.engine / SessionLocal
    monkeypatch.setattr(_database, "engine", test_engine)
    monkeypatch.setattr(_database, "SessionLocal", TestSession)
    # 也 monkeypatch main.get_db 闭包内的 SessionLocal（_database.get_db）
    def _patched_get_db():
        db = TestSession()
        try: yield db
        finally: db.close()
    monkeypatch.setattr(_main, "get_db", _patched_get_db)
    # auth_middleware 内部用 next(get_db()) 拿 session — 也指向 TestSession
    # get_db 已被 patch 过，但 middleware 用的 `from database import get_db` 引用？
    # 实际 main 顶部 `from database import get_db, init_db`，所以闭包外层绑定的 get_db
    # 也已被 monkeypatch 到 _main.get_db；middleware 调用 next(get_db()) 走的是 main.get_db
    yield TestSession()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try: os.unlink(path)
    except OSError: pass


@pytest.fixture
def client(fresh_db):
    return TestClient(app)


def _seed_user(db, username="admin", password="admin123", is_admin=True, is_advisor=False):
    u = User(
        username=username,
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode(),
        display_name=username,
        is_admin=is_admin,
        is_advisor=is_advisor,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_login_with_username_and_password(client, fresh_db):
    _seed_user(fresh_db, "alice", "pw_alice_1", is_admin=False, is_advisor=False)
    r = client.post("/api/auth/login", json={"username": "alice", "password": "pw_alice_1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["user"]["username"] == "alice"
    assert body["user"]["is_admin"] is False
    assert body["user"]["is_advisor"] is False
    assert "token" in body
    assert body["expires_in"] == 86400


def test_login_wrong_password(client, fresh_db):
    _seed_user(fresh_db, "bob", "right_pw_1")
    r = client.post("/api/auth/login", json={"username": "bob", "password": "wrong_pw_1"})
    # 返回 200 但 status=error（与旧 API 兼容）
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert "用户名或密码" in body["message"]


def test_login_unknown_user(client, fresh_db):
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "any123456"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"


def test_login_inactive_user_rejected(client, fresh_db):
    u = _seed_user(fresh_db, "carol", "pw_carol_1")
    u.is_active = False
    fresh_db.commit()
    r = client.post("/api/auth/login", json={"username": "carol", "password": "pw_carol_1"})
    body = r.json()
    assert body["status"] == "error"


def test_session_persists_user_id(client, fresh_db):
    u = _seed_user(fresh_db, "dave", "pw_dave_1")
    r = client.post("/api/auth/login", json={"username": "dave", "password": "pw_dave_1"})
    token = r.json()["token"]
    sess = fresh_db.query(AccessSession).filter(AccessSession.token == token).first()
    assert sess is not None
    assert sess.user_id == u.id


def test_auth_me_with_valid_token(client, fresh_db):
    _seed_user(fresh_db, "eve", "pw_eve_1", is_advisor=True)
    r = client.post("/api/auth/login", json={"username": "eve", "password": "pw_eve_1"})
    token = r.json()["token"]
    r2 = client.get("/api/auth/me", headers={"x-session-token": token})
    assert r2.status_code == 200
    assert r2.json()["user"]["username"] == "eve"
    assert r2.json()["user"]["is_advisor"] is True


def test_auth_me_without_token_returns_401(client, fresh_db):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_logout_deletes_session(client, fresh_db):
    _seed_user(fresh_db, "frank", "pw_frank_1")
    r = client.post("/api/auth/login", json={"username": "frank", "password": "pw_frank_1"})
    token = r.json()["token"]
    r2 = client.post("/api/auth/logout", headers={"x-session-token": token})
    assert r2.status_code == 200
    # 之后 /me 应该 401
    r3 = client.get("/api/auth/me", headers={"x-session-token": token})
    assert r3.status_code == 401


def test_middleware_injects_user_state(client, fresh_db):
    """middleware 应把 user 注入 request.state.user"""
    _seed_user(fresh_db, "gina", "pw_gina_1", is_admin=False, is_advisor=False)
    r = client.post("/api/auth/login", json={"username": "gina", "password": "pw_gina_1"})
    token = r.json()["token"]
    # /api/auth/me 用 request.state.user；如果注入失败会 401
    r2 = client.get("/api/auth/me", headers={"x-session-token": token})
    assert r2.status_code == 200
    assert r2.json()["user"]["is_admin"] is False
    assert r2.json()["user"]["is_advisor"] is False


def test_password_too_short_rejected(client, fresh_db):
    _seed_user(fresh_db, "harry", "long_enough_1")
    r = client.post("/api/auth/login", json={"username": "harry", "password": "short"})
    body = r.json()
    assert body["status"] == "error"
    assert "密码长度" in body["message"]


# ============================================================================
# HttpOnly Cookie 认证（生产环境安全要求）
# token 不再暴露给 JS，防止 XSS 窃取
# ============================================================================

def test_login_sets_httponly_cookie(client, fresh_db):
    """登录成功后，响应应通过 Set-Cookie 下发 HttpOnly cookie"""
    _seed_user(fresh_db, "cookie_user", "pw_cookie_1")
    r = client.post("/api/auth/login", json={"username": "cookie_user", "password": "pw_cookie_1"})
    assert r.status_code == 200
    # Set-Cookie header 应存在
    set_cookie = r.headers.get("set-cookie", "")
    assert "session_token=" in set_cookie, f"未找到 session_token cookie: {set_cookie}"
    # 必须是 HttpOnly（防 XSS 读取）
    assert "HttpOnly" in set_cookie, f"cookie 缺少 HttpOnly: {set_cookie}"
    # SameSite 必须设置（防 CSRF）
    assert "SameSite=" in set_cookie, f"cookie 缺少 SameSite: {set_cookie}"
    # cookie 值应等于响应体中的 token
    body = r.json()
    assert body["token"] in set_cookie, "cookie 值应与 token 一致"


def test_auth_me_reads_token_from_cookie(client, fresh_db):
    """/auth/me 应能从 cookie 读取 token（不需要 x-session-token header）"""
    _seed_user(fresh_db, "cookie_me", "pw_me_1")
    r = client.post("/api/auth/login", json={"username": "cookie_me", "password": "pw_me_1"})
    token = r.json()["token"]
    # 用 cookie 而非 header 调用 /me
    r2 = client.get("/api/auth/me", cookies={"session_token": token})
    assert r2.status_code == 200
    assert r2.json()["user"]["username"] == "cookie_me"


def test_protected_endpoint_reads_token_from_cookie(client, fresh_db):
    """受保护端点（如 /api/auth/users）应能从 cookie 读 token"""
    _seed_user(fresh_db, "cookie_admin", "pw_admin_1", is_admin=True)
    r = client.post("/api/auth/login", json={"username": "cookie_admin", "password": "pw_admin_1"})
    token = r.json()["token"]
    # /auth/users 需要 advisor+ 权限，用 cookie 调用
    r2 = client.get("/api/auth/users", cookies={"session_token": token})
    assert r2.status_code == 200
    assert "users" in r2.json()


def test_logout_clears_cookie(client, fresh_db):
    """登出应清除 cookie"""
    _seed_user(fresh_db, "cookie_logout", "pw_logout_1")
    r = client.post("/api/auth/login", json={"username": "cookie_logout", "password": "pw_logout_1"})
    token = r.json()["token"]
    r2 = client.post("/api/auth/logout", cookies={"session_token": token})
    assert r2.status_code == 200
    # Set-Cookie 应包含 session_token=; Max-Age=0 或 expires 过期
    set_cookie = r2.headers.get("set-cookie", "")
    assert "session_token=" in set_cookie
    # cookie 应被清除（Max-Age=0 或 expires 在过去）
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower(), \
        f"登出 cookie 未清除: {set_cookie}"


def test_cookie_token_still_works_via_header_too(client, fresh_db):
    """兼容期：x-session-token header 仍应工作（平滑迁移）"""
    _seed_user(fresh_db, "compat_header", "pw_compat_1")
    r = client.post("/api/auth/login", json={"username": "compat_header", "password": "pw_compat_1"})
    token = r.json()["token"]
    # 用 header 调用 /me
    r2 = client.get("/api/auth/me", headers={"x-session-token": token})
    assert r2.status_code == 200
