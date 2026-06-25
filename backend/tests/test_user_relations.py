"""测试 /api/auth/relations 双向预占 PENDING→ACTIVE 流"""
import os
os.environ["APP_PASSWORD"] = ""

import bcrypt
import pytest
import tempfile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
import main as _main
from database import Base
from main import app
from models import User, UserRelation


@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    monkeypatch.setattr(_database, "engine", test_engine)
    monkeypatch.setattr(_database, "SessionLocal", TestSession)
    def _patched_get_db():
        db = TestSession()
        try: yield db
        finally: db.close()
    monkeypatch.setattr(_main, "get_db", _patched_get_db)
    yield TestSession()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try: os.unlink(path)
    except OSError: pass


@pytest.fixture
def client(fresh_db):
    return TestClient(app)


def _seed_user(db, username="u", password="pw_aaaa_1", is_admin=False, is_advisor=False):
    u = User(
        username=username,
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode(),
        display_name=username,
        is_admin=is_admin, is_advisor=is_advisor, is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_pending_to_active_flow(client, fresh_db):
    """用户发起 → 顾问确认 → ACTIVE"""
    adv = _seed_user(fresh_db, "adv1", password="pw_adv1_aaaa_1", is_advisor=True)
    cli = _seed_user(fresh_db, "cli1", password="pw_cli1_aaaa_1")
    t_cli = _login(client, "cli1", "pw_cli1_aaaa_1")
    # 客户发起
    r = client.post("/api/auth/relations", json={"advisor_username": "adv1"},
                    headers={"x-session-token": t_cli})
    assert r.status_code == 200
    rel_id = r.json()["relation_id"]
    assert r.json()["status"] == "created"
    # 顾问确认
    t_adv = _login(client, "adv1", "pw_adv1_aaaa_1")
    r2 = client.post(f"/api/auth/relations/{rel_id}/confirm",
                     headers={"x-session-token": t_adv})
    assert r2.status_code == 200
    assert r2.json()["status"] == "active"
    # 双方都能在 list 看到 ACTIVE
    rl = client.get("/api/auth/relations", headers={"x-session-token": t_cli}).json()
    assert any(r["status"] == "ACTIVE" for r in rl["as_client"])


def test_cannot_confirm_self_initiated(client, fresh_db):
    """发起人不能确认自己的关联"""
    _seed_user(fresh_db, "adv2", password="pw_adv2_aaaa_1", is_advisor=True)
    _seed_user(fresh_db, "cli2", password="pw_cli2_aaaa_1")
    t_cli = _login(client, "cli2", "pw_cli2_aaaa_1")
    r = client.post("/api/auth/relations", json={"advisor_username": "adv2"},
                    headers={"x-session-token": t_cli})
    rel_id = r.json()["relation_id"]
    # 客户不能确认自己
    r2 = client.post(f"/api/auth/relations/{rel_id}/confirm",
                     headers={"x-session-token": t_cli})
    assert r2.status_code == 400


def test_advisor_invites_client(client, fresh_db):
    """顾问主动邀请客户 → 客户确认"""
    _seed_user(fresh_db, "adv3", password="pw_adv3_aaaa_1", is_advisor=True)
    _seed_user(fresh_db, "cli3", password="pw_cli3_aaaa_1")
    t_adv = _login(client, "adv3", "pw_adv3_aaaa_1")
    r = client.post("/api/auth/relations", json={"client_username": "cli3"},
                    headers={"x-session-token": t_adv})
    assert r.status_code == 200
    rel_id = r.json()["relation_id"]
    t_cli = _login(client, "cli3", "pw_cli3_aaaa_1")
    r2 = client.post(f"/api/auth/relations/{rel_id}/confirm",
                     headers={"x-session-token": t_cli})
    assert r2.status_code == 200
    assert r2.json()["status"] == "active"


def test_cancel_dissolves_relation(client, fresh_db):
    """任一方取消 → CANCELLED；view_as 因 ACTIVE 校验失败 → 403"""
    adv = _seed_user(fresh_db, "adv4", password="pw_adv4_aaaa_1", is_advisor=True)
    cli = _seed_user(fresh_db, "cli4", password="pw_cli4_aaaa_1")
    # 建 ACTIVE 关联
    rel = UserRelation(advisor_user_id=adv.id, client_user_id=cli.id,
                       status="ACTIVE", initiator_user_id=cli.id)
    fresh_db.add(rel); fresh_db.commit()
    t_cli = _login(client, "cli4", "pw_cli4_aaaa_1")
    r = client.post(f"/api/auth/relations/{rel.id}/cancel",
                    headers={"x-session-token": t_cli})
    assert r.status_code == 200
    # 再 cancel 不应报错（已 CANCELLED）
    r2 = client.post(f"/api/auth/relations/{rel.id}/cancel",
                     headers={"x-session-token": t_cli})
    assert r2.status_code == 200
    fresh_db.refresh(rel)
    assert rel.status == "CANCELLED"


def test_plain_user_cannot_invite_client(client, fresh_db):
    """普通 user 不能用 client_username 邀请别人"""
    _seed_user(fresh_db, "plain1", password="pw_plain1_aaa_1")
    _seed_user(fresh_db, "plain2", password="pw_plain2_aaa_1")
    t = _login(client, "plain1", "pw_plain1_aaa_1")
    r = client.post("/api/auth/relations", json={"client_username": "plain2"},
                    headers={"x-session-token": t})
    assert r.status_code == 403


def test_duplicate_returns_exists(client, fresh_db):
    """重复发起 → status=exists"""
    _seed_user(fresh_db, "adv5", password="pw_adv5_aaaa_1", is_advisor=True)
    _seed_user(fresh_db, "cli5", password="pw_cli5_aaaa_1")
    t_cli = _login(client, "cli5", "pw_cli5_aaaa_1")
    r1 = client.post("/api/auth/relations", json={"advisor_username": "adv5"},
                     headers={"x-session-token": t_cli})
    assert r1.json()["status"] == "created"
    r2 = client.post("/api/auth/relations", json={"advisor_username": "adv5"},
                     headers={"x-session-token": t_cli})
    assert r2.json()["status"] == "exists"


def test_view_as_requires_active_relation(client, fresh_db):
    """advisor 试图 view_as 非 ACTIVE 关联的客户 → 403"""
    _seed_user(fresh_db, "adv6", password="pw_adv6_aaaa_1", is_advisor=True)
    cli = _seed_user(fresh_db, "cli6", password="pw_cli6_aaaa_1")
    t_adv = _login(client, "adv6", "pw_adv6_aaaa_1")
    # 没建任何关联 → view_as 403
    r = client.get(f"/api/holdings/summary?view_as={cli.id}",
                   headers={"x-session-token": t_adv})
    assert r.status_code == 403


def test_view_as_with_active_relation_works(client, fresh_db):
    """advisor + ACTIVE 关联 → view_as 成功"""
    adv = _seed_user(fresh_db, "adv7", password="pw_adv7_aaaa_1", is_advisor=True)
    cli = _seed_user(fresh_db, "cli7", password="pw_cli7_aaaa_1")
    rel = UserRelation(advisor_user_id=adv.id, client_user_id=cli.id,
                       status="ACTIVE", initiator_user_id=cli.id)
    fresh_db.add(rel); fresh_db.commit()
    t_adv = _login(client, "adv7", "pw_adv7_aaaa_1")
    r = client.get(f"/api/holdings/summary?view_as={cli.id}",
                   headers={"x-session-token": t_adv})
    assert r.status_code == 200
