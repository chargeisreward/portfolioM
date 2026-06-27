"""TDD 测试：KPI csi300_pe 口径一致性 — 2026-06-27。

Bug: 总览卡片「300:」PE 与持仓卡片「全部沪深 300 证券」PE 不一致。
  - 持仓卡片 PE=16.2：来自 get_public_cards（fund_drill_snapshot，每日更新，6-26 数据）
  - 总览 KPI PE=17.8：来自 _csi300_scope_totals（csi300_constituent_snapshot，月度，5-29 数据）

修复：KPI csi300_pe 改用 get_public_cards，与持仓卡片、下钻页面三处一致。
  - get_public_cards 独立于 user_id（不读 Holding），王用户不持有 HS300 也能显示
  - 用同一函数保证数值完全一致
  - fallback：fund_drill_snapshot 无 000300 数据时回退到 _csi300_scope_totals
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
    User, Holding, FundIndexMap, FundDrillSnapshot,
    AShareFinancialSnapshot, Csi300ConstituentSnapshot,
)


# ============================================================
# fixtures
# ============================================================

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


def _seed_user(db, username, password="pw_aaaa_1"):
    u = User(
        username=username,
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode(),
        display_name=username,
        is_admin=False, is_advisor=False, is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _login(client, username, password="pw_aaaa_1"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


AS_OF = date(2026, 6, 23)

# 两个口径用不同 PE 值区分：
#   fund_drill_snapshot (get_public_cards)  → PE=30
#   csi300_constituent_snapshot (_csi300_scope_totals) → PE=25
# 修复后 KPI csi300_pe 应 == 30（get_public_cards 口径）
DRILL_PE = 30.0
CSI300_SNAP_PE = 25.0


def _seed_public_drill_data(db):
    """构造 fund_drill_snapshot 公共数据（510300.SH → 000300，单只成分股 pe=30）。"""
    # FundIndexMap: 510300.SH → 000300
    db.add(FundIndexMap(
        fund_code="510300.SH", fund_name="华泰柏瑞沪深300",
        index_code="000300", index_name="沪深300",
        as_of_date=AS_OF,
    ))
    # FundDrillSnapshot: 510300.SH 下钻 600519.SH
    db.add(FundDrillSnapshot(
        fund_code="510300.SH", as_of_date=AS_OF,
        stock_code="600519.SH", stock_name="贵州茅台",
        weight_pct=5.0,
        baseline_price=1800.0, current_price=1800.0,
        shares_equivalent=0.01,
        currency="CNY",
        baseline_price_cny=1800.0, current_price_cny=1800.0,
        pe_ttm_dynamic=DRILL_PE,  # ← get_public_cards 口径
    ))


def _seed_csi300_constituent_snapshot(db):
    """构造 csi300_constituent_snapshot 公共数据（同一只股票，pe=25 用于区分口径）。"""
    db.add(Csi300ConstituentSnapshot(
        user_id=1, as_of_date=AS_OF,
        stock_code="600519.SH", stock_name="贵州茅台",
        weight=5.0,
        baseline_price=1800.0, current_price=1800.0,
        pe_ttm_dynamic=CSI300_SNAP_PE,  # ← _csi300_scope_totals 口径
    ))
    # AShareFinancialSnapshot: _csi300_scope_totals 的 price_ratio + pe fallback
    db.add(AShareFinancialSnapshot(
        as_of_date=AS_OF,
        stock_code="600519.SH", stock_name="贵州茅台",
        pe_ttm=DRILL_PE, pe_ttm_dynamic=DRILL_PE,
        baseline_price=1800.0, current_price=1800.0,
    ))


# ============================================================
# 测试
# ============================================================

class TestKpiCsi300PeConsistency:
    """验证 KPI csi300_pe 来源 = get_public_cards（与持仓卡片一致）。"""

    def test_csi300_pe_from_get_public_cards(self, client, fresh_db):
        """KPI csi300_pe 应来自 get_public_cards（DRILL_PE=30），而非 _csi300_scope_totals（25）。

        RED（当前代码用 _csi300_scope_totals）→ GREEN（改用 get_public_cards）。
        """
        a = _seed_user(fresh_db, "pe_a")
        # 用户 A 持有 510300.SH（有下钻基金）
        fresh_db.add(Holding(
            user_id=a.id, security_code="510300.SH", security_name="华泰柏瑞沪深300",
            quantity=10, price=4.0, currency="CNY", amount=40, amount_cny=40,
            asset_type="a_share_etf",
        ))
        _seed_public_drill_data(fresh_db)
        _seed_csi300_constituent_snapshot(fresh_db)
        fresh_db.commit()

        ta = _login(client, "pe_a")
        r = client.get(
            f"/api/penetration/kpi?as_of_date={AS_OF.isoformat()}",
            headers={"x-session-token": ta},
        )
        assert r.status_code == 200, r.text
        csi300_pe = r.json()["values"]["csi300_pe"]
        assert csi300_pe == DRILL_PE, (
            f"csi300_pe 应来自 get_public_cards 口径（{DRILL_PE}），"
            f"实际={csi300_pe}（可能是 _csi300_scope_totals 口径 {CSI300_SNAP_PE}）"
        )

    def test_csi300_pe_independent_of_user_holdings(self, client, fresh_db):
        """用户不持有任何 HS300 基金时，csi300_pe 仍有值（来自公共 fund_drill_snapshot）。

        这是上一个 bug 的回归测试：旧实现用 list_drillable_indices(user_id)，
        用户不持有 HS300 基金时 csi300_pe=None。
        """
        # 用户 B 持有一只非 HS300 股票（不持有任何 HS300 基金）
        b = _seed_user(fresh_db, "pe_b", password="pw_bbbb_1")
        fresh_db.add(Holding(
            user_id=b.id, security_code="000001.SZ", security_name="平安银行",
            quantity=100, price=10, currency="CNY", amount=1000, amount_cny=1000,
            asset_type="a_share_equity",
        ))
        _seed_public_drill_data(fresh_db)
        _seed_csi300_constituent_snapshot(fresh_db)
        fresh_db.commit()

        tb = _login(client, "pe_b", "pw_bbbb_1")
        r = client.get(
            f"/api/penetration/kpi?as_of_date={AS_OF.isoformat()}",
            headers={"x-session-token": tb},
        )
        assert r.status_code == 200, r.text
        csi300_pe = r.json()["values"]["csi300_pe"]
        assert csi300_pe is not None, "用户不持有 HS300 基金时 csi300_pe 不应为 None"
        assert csi300_pe == DRILL_PE, (
            f"csi300_pe 应来自公共 fund_drill_snapshot（{DRILL_PE}），实际={csi300_pe}"
        )

    def test_csi300_pe_fallback_when_no_fund_drill(self, client, fresh_db):
        """fund_drill_snapshot 无 000300 数据时，fallback 到 _csi300_scope_totals。

        场景：fund_drill_snapshot 表为空（scheduler 未运行），但
        csi300_constituent_snapshot 有数据 → KPI 应 fallback 返回 CSI300_SNAP_PE。
        """
        a = _seed_user(fresh_db, "pe_c", password="pw_cccc_1")
        # 不构造 fund_drill_snapshot（无下钻数据）
        # 只构造 csi300_constituent_snapshot
        _seed_csi300_constituent_snapshot(fresh_db)
        fresh_db.commit()

        ta = _login(client, "pe_c", "pw_cccc_1")
        r = client.get(
            f"/api/penetration/kpi?as_of_date={AS_OF.isoformat()}",
            headers={"x-session-token": ta},
        )
        assert r.status_code == 200, r.text
        csi300_pe = r.json()["values"]["csi300_pe"]
        assert csi300_pe == CSI300_SNAP_PE, (
            f"fund_drill_snapshot 无数据时应 fallback 到 _csi300_scope_totals"
            f"（{CSI300_SNAP_PE}），实际={csi300_pe}"
        )
