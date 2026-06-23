"""测试 user 数据隔离：holdings / watchlist / import"""
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
from models import User, Holding, Watchlist, HoldingImportLog


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


def test_holdings_summary_isolated_by_user(client, fresh_db):
    a = _seed_user(fresh_db, "alice", is_admin=False, is_advisor=False)
    b = _seed_user(fresh_db, "bob", is_admin=False, is_advisor=False)
    # alice 持有 1000, bob 持有 2000
    fresh_db.add_all([
        Holding(user_id=a.id, security_code="000001", security_name="x",
                quantity=1, price=1000, currency="CNY", amount=1000, amount_cny=1000,
                asset_type="a_share_equity"),
        Holding(user_id=b.id, security_code="600519", security_name="y",
                quantity=1, price=2000, currency="CNY", amount=2000, amount_cny=2000,
                asset_type="a_share_equity"),
    ])
    fresh_db.commit()

    ta = _login(client, "alice", "pw_aaaa_1")
    ra = client.get("/api/holdings/summary", headers={"x-session-token": ta})
    assert ra.status_code == 200
    s_a = ra.json()
    assert abs(s_a["total_value"] - 1000) < 0.1
    # bob 登录看到自己
    tb = _login(client, "bob", "pw_aaaa_1")
    rb = client.get("/api/holdings/summary", headers={"x-session-token": tb})
    assert rb.status_code == 200
    s_b = rb.json()
    assert abs(s_b["total_value"] - 2000) < 0.1


def test_list_holdings_isolated_by_user(client, fresh_db):
    a = _seed_user(fresh_db, "alice2", password="pw_alice2_1")
    b = _seed_user(fresh_db, "bob2", password="pw_bob2_1")
    fresh_db.add_all([
        Holding(user_id=a.id, security_code="A", security_name="a",
                quantity=1, price=10, currency="CNY", amount=10, amount_cny=10,
                asset_type="a_share_equity"),
        Holding(user_id=b.id, security_code="B", security_name="b",
                quantity=1, price=20, currency="CNY", amount=20, amount_cny=20,
                asset_type="a_share_equity"),
    ])
    fresh_db.commit()

    ta = _login(client, "alice2", "pw_alice2_1")
    rows = client.get("/api/holdings", headers={"x-session-token": ta}).json()
    codes = {r["security_code"] for r in rows}
    assert codes == {"A"}, f"alice 应当只看到 A，实际={codes}"


def test_watchlist_isolated_by_user(client, fresh_db):
    a = _seed_user(fresh_db, "u_a", password="pw_ua_aaaaa_1")
    b = _seed_user(fresh_db, "u_b", password="pw_ub_aaaaa_1")
    fresh_db.add_all([
        Watchlist(user_id=a.id, code="000001", name="A", market="A股", weight=5.0),
        Watchlist(user_id=b.id, code="600519", name="B", market="A股", weight=5.0),
    ])
    fresh_db.commit()

    ta = _login(client, "u_a", "pw_ua_aaaaa_1")
    rows = client.get("/api/watchlist", headers={"x-session-token": ta}).json()
    codes = {r["code"] for r in rows}
    assert codes == {"000001"}


def test_view_as_admin_sees_other_user_holdings(client, fresh_db):
    a = _seed_user(fresh_db, "v_user", password="pw_v_aaaa_1")
    admin = _seed_user(fresh_db, "v_admin", password="pw_v_aaaa_2", is_admin=True)
    fresh_db.add(Holding(user_id=a.id, security_code="X", security_name="x",
                         quantity=1, price=99, currency="CNY", amount=99, amount_cny=99,
                         asset_type="a_share_equity"))
    fresh_db.commit()

    t_admin = _login(client, "v_admin", "pw_v_aaaa_2")
    r = client.get(f"/api/holdings/summary?view_as={a.id}",
                   headers={"x-session-token": t_admin})
    assert r.status_code == 200
    s = r.json()
    assert abs(s["total_value"] - 99) < 0.1


def test_view_as_user_403(client, fresh_db):
    a = _seed_user(fresh_db, "perm_a", password="pw_pa_aaaa_1")
    b = _seed_user(fresh_db, "perm_b", password="pw_pb_aaaa_1")
    ta = _login(client, "perm_a", "pw_pa_aaaa_1")
    # a 试图 view_as b → 403
    r = client.get(f"/api/holdings/summary?view_as={b.id}",
                   headers={"x-session-token": ta})
    assert r.status_code == 403


def test_import_excel_only_clears_target_user(client, fresh_db, tmp_path):
    a = _seed_user(fresh_db, "imp_a", password="pw_impa_aaa1")
    b = _seed_user(fresh_db, "imp_b", password="pw_impb_aaa1")
    # b 已经有一笔
    fresh_db.add(Holding(user_id=b.id, security_code="EXISTING", security_name="b_existing",
                         quantity=1, price=1, currency="CNY", amount=1, amount_cny=1,
                         asset_type="a_share_equity"))
    fresh_db.commit()
    # 写一个 xlsx（importer 从 row 3 开始读 — 写 2 行空白 + 2 行数据）
    from openpyxl import Workbook
    fp = tmp_path / "test.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["code", "name", "col2", "col3"])   # row 1: header (被忽略)
    ws.append([None, None, None, None])             # row 2: blank (被忽略)
    ws.append(["000001", "平安银行", 100, 100])     # row 3: data
    ws.append(["600519", "茅台", 10, 10])           # row 4: data
    wb.save(fp)
    # a 登录、调用 import
    ta = _login(client, "imp_a", "pw_impa_aaa1")
    # monkey-patch DATA_DIR to tmp
    import main as _main_mod
    orig_data_dir = _main_mod.DATA_DIR
    _main_mod.DATA_DIR = tmp_path
    try:
        r = client.post("/api/holdings/import", json={}, headers={"x-session-token": ta})
        assert r.status_code == 200, r.text
    finally:
        _main_mod.DATA_DIR = orig_data_dir
    # 验证 a 有 2 条
    rows_a = fresh_db.query(Holding).filter(Holding.user_id == a.id).all()
    codes_a = {h.security_code for h in rows_a}
    assert codes_a == {"000001", "600519"}
    # 验证 b 仍保留 EXISTING（未误删）
    rows_b = fresh_db.query(Holding).filter(Holding.user_id == b.id).all()
    codes_b = {h.security_code for h in rows_b}
    assert "EXISTING" in codes_b
