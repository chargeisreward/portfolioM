"""测试 /api/trades/confirm 端点的部分成功语义。

覆盖 4 个场景：
- 全成功：所有交易校验通过 → 全部入库 + 触发 rebuild
- 部分成功：部分校验失败 → 失败的不入库，成功的入库 + rebuild
- 全失败消息：校验失败的 error 字段为"名称或代码可能有误"
- rebuild 触发条件：仅当 confirmed 非空时触发 rebuild
"""
import os
os.environ["APP_PASSWORD"] = ""

import bcrypt
import pytest
import tempfile
from datetime import date
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401  必须先 import 让 Base 知道所有 model
import database as _database
import main as _main
from database import Base
from main import app
from models import User, Transaction


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


def _login(client, username, password):
    """登录并返回 token。"""
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"登录失败: {r.text}"
    return r.json()["token"]


def _make_trade(trade_date, code, name, trade_type, shares, amount):
    """构造单笔交易请求体。"""
    return {
        "trade_date": str(trade_date),
        "security_code": code,
        "security_name": name,
        "trade_type": trade_type,
        "confirmed_shares": shares,
        "confirmed_amount": amount,
        "nav_price": None,
        "nav_date": None,
        "fee": None,
        "remarks": None,
    }


# ============================================================================
# 测试 1：全成功
# ============================================================================

def test_confirm_all_success(client, fresh_db):
    """2 笔交易全部校验通过 → 全部入库 + 触发 rebuild。"""
    _seed_user(fresh_db, "alice", "pw_alice_1", is_advisor=True)
    token = _login(client, "alice", "pw_alice_1")

    trades = [
        _make_trade(date(2025, 7, 20), "510300.SH", "沪深300ETF", "buy", 1000, 4500),
        _make_trade(date(2025, 7, 21), "159919.SZ", "300ETF", "buy", 500, 2000),
    ]

    mock_verify = MagicMock(return_value={
        "verified": True, "reason": "匹配",
        "security_code": "510300.SH", "security_name": "沪深300ETF",
    })
    mock_rebuild = MagicMock(return_value={"days_built": 1})
    mock_valuation = MagicMock(return_value=None)

    with patch("main.verify_security_for_confirm", mock_verify), \
         patch("main.rebuild_holdings_to_date", mock_rebuild), \
         patch("main.rebuild_valuation_to_date", mock_valuation):
        r = client.post(
            "/api/trades/confirm",
            json={"trades": trades},
            headers={"x-session-token": token},
        )

    assert r.status_code == 200, f"请求失败: {r.text}"
    body = r.json()
    assert body["confirmed_count"] == 2
    assert body["failed_count"] == 0
    assert len(body["confirmed"]) == 2
    assert all(c["success"] is True for c in body["confirmed"])
    assert all(c["trade_id"] is not None for c in body["confirmed"])

    # DB 中 Transaction 表有 2 条记录
    txs = fresh_db.query(Transaction).filter(Transaction.user_id == 1).all()
    assert len(txs) == 2

    # rebuild 被触发
    assert mock_rebuild.call_count == 1
    assert mock_valuation.call_count == 1


# ============================================================================
# 测试 2：部分成功
# ============================================================================

def test_confirm_partial_success(client, fresh_db):
    """2 笔交易，第 1 笔通过，第 2 笔失败 → 只入库第 1 笔 + rebuild。"""
    _seed_user(fresh_db, "bob", "pw_bob_1", is_advisor=True)
    token = _login(client, "bob", "pw_bob_1")

    trades = [
        _make_trade(date(2025, 7, 20), "510300.SH", "沪深300ETF", "buy", 1000, 4500),
        _make_trade(date(2025, 7, 20), "999999.SH", "未知证券", "buy", 100, 1000),
    ]

    # 第 1 笔通过，第 2 笔失败
    mock_verify = MagicMock(side_effect=[
        {"verified": True, "reason": "匹配", "security_code": "510300.SH", "security_name": "沪深300ETF"},
        {"verified": False, "reason": "名称不匹配", "security_code": "999999.SH", "security_name": "未知证券"},
    ])
    mock_rebuild = MagicMock(return_value={"days_built": 1})
    mock_valuation = MagicMock(return_value=None)

    with patch("main.verify_security_for_confirm", mock_verify), \
         patch("main.rebuild_holdings_to_date", mock_rebuild), \
         patch("main.rebuild_valuation_to_date", mock_valuation):
        r = client.post(
            "/api/trades/confirm",
            json={"trades": trades},
            headers={"x-session-token": token},
        )

    assert r.status_code == 200, f"请求失败: {r.text}"
    body = r.json()
    assert body["confirmed_count"] == 1
    assert body["failed_count"] == 1

    # index 正确对应
    assert body["confirmed"][0]["index"] == 0
    assert body["failed"][0]["index"] == 1

    # DB 中只有 1 条 Transaction（第 1 笔）
    txs = fresh_db.query(Transaction).filter(Transaction.user_id == 1).all()
    assert len(txs) == 1
    assert txs[0].security_code == "510300.SH"

    # 仍有成功条目 → rebuild 被触发
    assert mock_rebuild.call_count == 1


# ============================================================================
# 测试 3：失败消息
# ============================================================================

def test_confirm_failed_message(client, fresh_db):
    """1 笔交易校验失败 → error 为"名称或代码可能有误" + 不入库 + 不触发 rebuild。"""
    _seed_user(fresh_db, "carol", "pw_carol_1", is_advisor=True)
    token = _login(client, "carol", "pw_carol_1")

    trades = [
        _make_trade(date(2025, 7, 20), "999999.SH", "未知证券", "buy", 100, 1000),
    ]

    mock_verify = MagicMock(return_value={
        "verified": False, "reason": "名称不匹配",
        "security_code": "999999.SH", "security_name": "未知证券",
    })
    mock_rebuild = MagicMock(return_value={"days_built": 0})
    mock_valuation = MagicMock(return_value=None)

    with patch("main.verify_security_for_confirm", mock_verify), \
         patch("main.rebuild_holdings_to_date", mock_rebuild), \
         patch("main.rebuild_valuation_to_date", mock_valuation):
        r = client.post(
            "/api/trades/confirm",
            json={"trades": trades},
            headers={"x-session-token": token},
        )

    assert r.status_code == 200, f"请求失败: {r.text}"
    body = r.json()
    assert body["confirmed_count"] == 0
    assert body["failed_count"] == 1
    assert body["failed"][0]["error"] == "名称或代码可能有误"

    # DB 中 Transaction 表为空
    txs = fresh_db.query(Transaction).filter(Transaction.user_id == 1).all()
    assert len(txs) == 0

    # 无成功条目 → rebuild 未被调用
    assert mock_rebuild.call_count == 0


# ============================================================================
# 测试 4：rebuild 触发条件
# ============================================================================

def test_confirm_triggers_rebuild_only_when_confirmed(client, fresh_db):
    """3 笔交易全部失败 → rebuild_holdings + rebuild_valuation 均未被调用。"""
    _seed_user(fresh_db, "dave", "pw_dave_1", is_advisor=True)
    token = _login(client, "dave", "pw_dave_1")

    trades = [
        _make_trade(date(2025, 7, 20), "999999.SH", "未知证券1", "buy", 100, 1000),
        _make_trade(date(2025, 7, 20), "888888.SH", "未知证券2", "buy", 200, 2000),
        _make_trade(date(2025, 7, 20), "777777.SH", "未知证券3", "buy", 300, 3000),
    ]

    mock_verify = MagicMock(return_value={
        "verified": False, "reason": "名称不匹配",
        "security_code": "999999.SH", "security_name": "未知证券",
    })
    mock_rebuild = MagicMock(return_value={"days_built": 0})
    mock_valuation = MagicMock(return_value=None)

    with patch("main.verify_security_for_confirm", mock_verify), \
         patch("main.rebuild_holdings_to_date", mock_rebuild), \
         patch("main.rebuild_valuation_to_date", mock_valuation):
        r = client.post(
            "/api/trades/confirm",
            json={"trades": trades},
            headers={"x-session-token": token},
        )

    assert r.status_code == 200, f"请求失败: {r.text}"
    body = r.json()
    assert body["confirmed_count"] == 0
    assert body["failed_count"] == 3

    # 全部失败 → rebuild 和 valuation 均未被调用
    assert mock_rebuild.call_count == 0
    assert mock_valuation.call_count == 0

    # DB 中 Transaction 表为空
    txs = fresh_db.query(Transaction).filter(Transaction.user_id == 1).all()
    assert len(txs) == 0
