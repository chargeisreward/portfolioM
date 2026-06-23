"""测试 /api/data-gap/* admin 端点"""
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
from models import User, DataGapReport, IndexClassification


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


def _seed(db, username="u", password="pw_aaaa_1", is_admin=False):
    u = User(
        username=username,
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode(),
        display_name=username,
        is_admin=is_admin, is_advisor=False, is_active=True,
    )
    db.add(u); db.commit(); db.refresh(u)
    return u


def _login(client, u, pw):
    r = client.post("/api/auth/login", json={"username": u, "password": pw})
    assert r.status_code == 200
    return r.json()["token"]


def test_list_gaps_as_admin(client, fresh_db):
    _seed(fresh_db, "adm1", "pw_adm1_aaaa_1", is_admin=True)
    user = _seed(fresh_db, "usr1", "pw_usr1_aaaa_1")
    fresh_db.add(DataGapReport(
        user_id=user.id, gap_type="index_classification", index_code="000300",
        description="test", status="OPEN",
    ))
    fresh_db.commit()
    t = _login(client, "adm1", "pw_adm1_aaaa_1")
    r = client.get("/api/data-gap/report", headers={"x-session-token": t})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) >= 1
    assert body["counts"]["OPEN"] >= 1


def test_list_gaps_as_user_forbidden(client, fresh_db):
    _seed(fresh_db, "adm2", "pw_adm2_aaaa_1", is_admin=True)
    _seed(fresh_db, "usr2", "pw_usr2_aaaa_1")
    t = _login(client, "usr2", "pw_usr2_aaaa_1")
    r = client.get("/api/data-gap/report", headers={"x-session-token": t})
    assert r.status_code == 403


def test_fix_gap_marks_fixed(client, fresh_db):
    _seed(fresh_db, "adm3", "pw_adm3_aaaa_1", is_admin=True)
    g = DataGapReport(
        gap_type="index_classification", index_code="000300",
        description="test", status="OPEN",
    )
    fresh_db.add(g); fresh_db.commit()
    t = _login(client, "adm3", "pw_adm3_aaaa_1")
    r = client.post(f"/api/data-gap/fix/{g.id}", headers={"x-session-token": t})
    assert r.status_code == 200
    fresh_db.refresh(g)
    assert g.status == "FIXED"
    assert g.resolved_at is not None


def test_set_index_classification(client, fresh_db):
    _seed(fresh_db, "adm4", "pw_adm4_aaaa_1", is_admin=True)
    t = _login(client, "adm4", "pw_adm4_aaaa_1")
    r = client.post(
        "/api/data-gap/index-classification",
        json={"index_code": "000300", "category": "宽基", "theme": "大盘"},
        headers={"x-session-token": t},
    )
    assert r.status_code == 200
    cls = fresh_db.query(IndexClassification).filter(
        IndexClassification.index_code == "000300"
    ).first()
    assert cls is not None
    assert cls.category == "宽基"
    assert cls.theme == "大盘"


def test_set_classification_forbidden_for_user(client, fresh_db):
    _seed(fresh_db, "adm5", "pw_adm5_aaaa_1", is_admin=True)
    _seed(fresh_db, "usr5", "pw_usr5_aaaa_1")
    t = _login(client, "usr5", "pw_usr5_aaaa_1")
    r = client.post(
        "/api/data-gap/index-classification",
        json={"index_code": "000300", "category": "宽基"},
        headers={"x-session-token": t},
    )
    assert r.status_code == 403
