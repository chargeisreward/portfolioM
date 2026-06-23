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
