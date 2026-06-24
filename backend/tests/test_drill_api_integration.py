"""下钻 API 集成测试 — 用户 × 端点 × view_as 矩阵。

验证三层 service 架构在 HTTP 层的端到端行为：
  1. admin 无持仓 → 空列表
  2. admin view_as=advisor → 返回卡片
  3. advisor 直接登录 → 返回自己的卡片
  4. 普通用户 view_as 他人 → 403
  5. index-drill 返回含 user_drill_shares 的明细
  6. index-drill 无数据 → 404
"""
import os
os.environ["APP_PASSWORD"] = ""

import bcrypt
import pytest
import tempfile
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
import main as _main
from database import Base
from main import app
from models import (
    User,
    UserRelation,
    Holding,
    FundIndexMap,
    FundDrillSnapshot,
)


# ========== fixtures ==========

@pytest.fixture
def fresh_db(monkeypatch):
    """每个测试用独立的临时文件 SQLite。"""
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
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(_main, "get_db", _patched_get_db)
    yield TestSession()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def client(fresh_db):
    return TestClient(app)


def _seed_user(db, username, password, is_admin=False, is_advisor=False):
    """创建用户并返回。"""
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


def _seed_relation(db, advisor, client_user):
    """创建 advisor → client 的 ACTIVE 关系。"""
    rel = UserRelation(
        advisor_user_id=advisor.id,
        client_user_id=client_user.id,
        status="ACTIVE",
        initiator_user_id=advisor.id,
    )
    db.add(rel)
    db.commit()
    return rel


def _seed_drill_data(db, as_of=date(2026, 6, 24)):
    """种子下钻公共数据：1 只基金跟踪 1 个指数，2 只成分股。"""
    # 基金→指数映射
    fim = FundIndexMap(
        fund_code="510300.SH",
        fund_name="沪深300ETF",
        index_code="000300.SH",
        index_name="沪深300",
        as_of_date=as_of,
        source="test",
    )
    db.add(fim)

    # 公共下钻截面：2 只成分股
    snap1 = FundDrillSnapshot(
        fund_code="510300.SH",
        as_of_date=as_of,
        stock_code="600519.SH",
        stock_name="贵州茅台",
        weight_pct=5.0,
        baseline_price=1500.0,
        current_price=1600.0,
        shares_equivalent=0.001,
        currency="CNY",
        current_price_cny=1600.0,
    )
    snap2 = FundDrillSnapshot(
        fund_code="510300.SH",
        as_of_date=as_of,
        stock_code="000858.SZ",
        stock_name="五粮液",
        weight_pct=3.0,
        baseline_price=200.0,
        current_price=210.0,
        shares_equivalent=0.002,
        currency="CNY",
        current_price_cny=210.0,
    )
    db.add(snap1)
    db.add(snap2)
    db.commit()


def _seed_holding(db, user_id, fund_code="510300.SH", quantity=10000.0, amount_cny=45000.0):
    """种子用户持仓。"""
    h = Holding(
        user_id=user_id,
        security_code=fund_code,
        security_name="沪深300ETF",
        quantity=quantity,
        price=4.5,
        currency="CNY",
        amount=quantity * 4.5,
        amount_cny=amount_cny,
        asset_type="a_share_etf",
        import_batch="test",
    )
    db.add(h)
    db.commit()
    return h


def _login(client, username, password):
    """登录并返回 token。"""
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"login failed: {r.text}"
    body = r.json()
    assert body["status"] == "ok", f"login error: {body}"
    return body["token"]


# ========== 测试 ==========

AS_OF = "2026-06-24"


class TestDrillIsolation:
    """下钻数据隔离 + view_as 矩阵测试。"""

    def test_admin_no_holdings_returns_empty(self, client, fresh_db):
        """admin 无持仓 → 空列表。"""
        _seed_user(fresh_db, "admin", "admin123", is_admin=True)
        _seed_drill_data(fresh_db)

        token = _login(client, "admin", "admin123")
        r = client.get(
            f"/api/penetration/drillable-indices?as_of_date={AS_OF}",
            headers={"x-session-token": token},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["as_of_date"] == AS_OF
        assert body["indices"] == []

    def test_admin_view_as_advisor_returns_cards(self, client, fresh_db):
        """admin view_as=advisor → 返回卡片。"""
        admin = _seed_user(fresh_db, "admin", "admin123", is_admin=True)
        advisor = _seed_user(fresh_db, "advisor", "advisor123", is_advisor=True)
        _seed_drill_data(fresh_db)
        _seed_holding(fresh_db, advisor.id)

        token = _login(client, "admin", "admin123")
        r = client.get(
            f"/api/penetration/drillable-indices?as_of_date={AS_OF}&view_as={advisor.id}",
            headers={"x-session-token": token},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["indices"]) == 1
        card = body["indices"][0]
        assert card["index_code"] == "000300"
        assert "est_market_value_cny" in card
        assert card["est_market_value_cny"] == 45000.0

    def test_advisor_direct_login_returns_own_cards(self, client, fresh_db):
        """advisor 直接登录 → 返回自己的卡片。"""
        advisor = _seed_user(fresh_db, "advisor", "advisor123", is_advisor=True)
        _seed_drill_data(fresh_db)
        _seed_holding(fresh_db, advisor.id)

        token = _login(client, "advisor", "advisor123")
        r = client.get(
            f"/api/penetration/drillable-indices?as_of_date={AS_OF}",
            headers={"x-session-token": token},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["indices"]) == 1
        assert body["indices"][0]["index_code"] == "000300"

    def test_regular_user_cannot_view_as_others(self, client, fresh_db):
        """普通用户 view_as 他人 → 403。"""
        admin = _seed_user(fresh_db, "admin", "admin123", is_admin=True)
        user = _seed_user(fresh_db, "user", "user123")
        _seed_drill_data(fresh_db)
        _seed_holding(fresh_db, admin.id)

        token = _login(client, "user", "user123")
        r = client.get(
            f"/api/penetration/drillable-indices?as_of_date={AS_OF}&view_as={admin.id}",
            headers={"x-session-token": token},
        )
        assert r.status_code == 403

    def test_advisor_view_as_related_client(self, client, fresh_db):
        """顾问 view_as 关联客户 → 返回客户卡片。"""
        advisor = _seed_user(fresh_db, "advisor", "advisor123", is_advisor=True)
        client_user = _seed_user(fresh_db, "client", "client123")
        _seed_relation(fresh_db, advisor, client_user)
        _seed_drill_data(fresh_db)
        _seed_holding(fresh_db, client_user.id)

        token = _login(client, "advisor", "advisor123")
        r = client.get(
            f"/api/penetration/drillable-indices?as_of_date={AS_OF}&view_as={client_user.id}",
            headers={"x-session-token": token},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["indices"]) == 1
        assert body["indices"][0]["index_code"] == "000300"

    def test_advisor_view_as_unrelated_user_403(self, client, fresh_db):
        """顾问 view_as 非关联用户 → 403。"""
        advisor = _seed_user(fresh_db, "advisor", "advisor123", is_advisor=True)
        stranger = _seed_user(fresh_db, "stranger", "stranger123")
        _seed_drill_data(fresh_db)

        token = _login(client, "advisor", "advisor123")
        r = client.get(
            f"/api/penetration/drillable-indices?as_of_date={AS_OF}&view_as={stranger.id}",
            headers={"x-session-token": token},
        )
        assert r.status_code == 403


class TestDrillDetail:
    """下钻明细端点测试。"""

    def test_index_drill_returns_detail_with_user_shares(self, client, fresh_db):
        """index-drill 返回含 user_drill_shares 的明细。"""
        admin = _seed_user(fresh_db, "admin", "admin123", is_admin=True)
        advisor = _seed_user(fresh_db, "advisor", "advisor123", is_advisor=True)
        _seed_drill_data(fresh_db)
        _seed_holding(fresh_db, advisor.id, quantity=10000.0, amount_cny=45000.0)

        token = _login(client, "admin", "admin123")
        r = client.get(
            f"/api/penetration/index-drill?as_of_date={AS_OF}&index_code=000300&view_as={advisor.id}",
            headers={"x-session-token": token},
        )
        assert r.status_code == 200, r.text
        detail = r.json()
        assert detail["index_code"] == "000300"
        assert "constituents" in detail
        assert "funds" in detail
        assert "total_user_drill_shares" in detail
        # 验证 join：user_drill_shares = quantity × shares_equivalent
        fund = detail["funds"][0]
        assert fund["user_quantity"] == 10000.0
        # shares_equivalent 在 public 层被聚合（0.001 + 0.002 来自两只成分股行）
        # 但 fund 级别的 shares_equivalent 是各成分股行的累加
        # user_drill_shares = 10000 × shares_equivalent
        assert fund["user_drill_shares"] > 0
        # 验证成分股有 user_hold_shares
        const = detail["constituents"][0]
        assert "user_hold_shares" in const
        assert "user_hold_value" in const

    def test_index_drill_404_when_no_snapshot(self, client, fresh_db):
        """index-drill 无 snapshot → 404。"""
        admin = _seed_user(fresh_db, "admin", "admin123", is_admin=True)
        _seed_drill_data(fresh_db)

        token = _login(client, "admin", "admin123")
        r = client.get(
            f"/api/penetration/index-drill?as_of_date={AS_OF}&index_code=999999",
            headers={"x-session-token": token},
        )
        assert r.status_code == 404

    def test_index_drill_404_when_user_no_holdings(self, client, fresh_db):
        """index-drill 用户无持仓 → 404。"""
        admin = _seed_user(fresh_db, "admin", "admin123", is_admin=True)
        _seed_drill_data(fresh_db)
        # admin 无持仓

        token = _login(client, "admin", "admin123")
        r = client.get(
            f"/api/penetration/index-drill?as_of_date={AS_OF}&index_code=000300",
            headers={"x-session-token": token},
        )
        assert r.status_code == 404

    def test_index_drill_401_when_not_logged_in(self, client, fresh_db):
        """index-drill 未登录 → 401。"""
        _seed_drill_data(fresh_db)
        r = client.get(
            f"/api/penetration/index-drill?as_of_date={AS_OF}&index_code=000300"
        )
        assert r.status_code == 401
