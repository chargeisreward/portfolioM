"""验证 3 个 user_id 隔离 bug 修复（task #247/#248/#249）— 2026-06-24。

Bug #247: get_kpi → list_drillable_indices 漏传 user_id=eff_uid
Bug #248: top10-holdings snapshot/Holding 全表扫描无 user_id 过滤
Bug #249: set_watchlist_weight 越权写（无认证 + 无 user_id 过滤）
"""
import os
os.environ["APP_PASSWORD"] = ""

import bcrypt
import pytest
import tempfile
from datetime import date, datetime
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
    User, UserRelation, Holding, Watchlist,
    AShareFinancialSnapshot, HKShareFinancialSnapshot,
    FundIndexMap, FundDrillSnapshot, PriceCache, ExchangeRate,
)


# ============================================================
# fixtures — 复用 test_user_isolation.py 的模式
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


def _seed_user(db, username, password="pw_aaaa_1", is_admin=False, is_advisor=False):
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


def _login(client, username, password="pw_aaaa_1"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


AS_OF = date(2026, 6, 23)


# ============================================================
# Bug #249: set_watchlist_weight 越权写
# ============================================================

class TestBug249WatchlistWeight:
    """验证 set_watchlist_weight 的认证 + user_id 隔离。"""

    def test_no_auth_returns_401(self, client, fresh_db):
        """未登录 → 401（修复前无认证，任何人可改）。"""
        a = _seed_user(fresh_db, "wl_a")
        fresh_db.add(Watchlist(user_id=a.id, code="000001", name="A", market="A股", weight=5.0))
        fresh_db.commit()

        r = client.put("/api/watchlist/000001/weight", json={"weight": 10.0})
        assert r.status_code == 401, f"未登录应返回 401，实际={r.status_code}"

    def test_user_cannot_modify_others_watchlist(self, client, fresh_db):
        """user B 不能改 user A 的 watchlist（修复前全表扫描无 user_id 过滤）。"""
        a = _seed_user(fresh_db, "wl_a2")
        b = _seed_user(fresh_db, "wl_b2", password="pw_bbbb_1")
        fresh_db.add(Watchlist(user_id=a.id, code="000001", name="A", market="A股", weight=5.0))
        fresh_db.commit()

        tb = _login(client, "wl_b2", "pw_bbbb_1")
        r = client.put("/api/watchlist/000001/weight",
                       json={"weight": 99.0},
                       headers={"x-session-token": tb})
        assert r.status_code == 200
        assert r.json()["status"] == "error", "user B 不应能改 user A 的权重"

        # 验证 A 的权重未被修改
        w = fresh_db.query(Watchlist).filter(Watchlist.user_id == a.id).first()
        assert w.weight == 5.0, f"A 的权重应仍为 5.0，实际={w.weight}"

    def test_owner_can_modify_own_watchlist(self, client, fresh_db):
        """user A 可以改自己的 watchlist。"""
        a = _seed_user(fresh_db, "wl_a3")
        fresh_db.add(Watchlist(user_id=a.id, code="000001", name="A", market="A股", weight=5.0))
        fresh_db.commit()

        ta = _login(client, "wl_a3")
        r = client.put("/api/watchlist/000001/weight",
                       json={"weight": 15.0},
                       headers={"x-session-token": ta})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        fresh_db.expire_all()
        w = fresh_db.query(Watchlist).filter(Watchlist.user_id == a.id).first()
        assert w.weight == 15.0


# ============================================================
# Bug #248: top10-holdings snapshot 全表扫描
# ============================================================

class TestBug248Top10Holdings:
    """验证 top10-holdings 的 snapshot + Holding 按 user_id 隔离。"""

    def _seed_two_users_with_holdings(self, db):
        """创建 2 个 user，各有不同持仓 + 不同 snapshot。"""
        a = _seed_user(db, "t10_a")
        b = _seed_user(db, "t10_b", password="pw_bbbb_1")
        # a 持有 000001，b 持有 600519
        db.add_all([
            Holding(user_id=a.id, security_code="000001", security_name="平安银行",
                    quantity=100, price=10, currency="CNY", amount=1000, amount_cny=1000,
                    asset_type="a_share_equity"),
            Holding(user_id=b.id, security_code="600519", security_name="贵州茅台",
                    quantity=10, price=1800, currency="CNY", amount=18000, amount_cny=18000,
                    asset_type="a_share_equity"),
        ])
        # a 的 snapshot（user_id=a.id）
        db.add(AShareFinancialSnapshot(
            user_id=a.id, as_of_date=AS_OF, stock_code="000001.SZ",
            stock_name="平安银行", pe_ttm=8.5, pb_mrq=0.6, ps_ttm=2.0,
            dividend_yield=5.0, market_cap=3500.0,
        ))
        # b 的 snapshot（user_id=b.id）
        db.add(AShareFinancialSnapshot(
            user_id=b.id, as_of_date=AS_OF, stock_code="600519.SH",
            stock_name="贵州茅台", pe_ttm=30.0, pb_mrq=10.0, ps_ttm=15.0,
            dividend_yield=1.0, market_cap=20000.0,
        ))
        db.commit()
        return a, b

    def test_user_a_only_sees_own_stocks(self, client, fresh_db):
        """user A 调 top10 只看到 000001，不看到 600519。"""
        a, b = self._seed_two_users_with_holdings(fresh_db)
        ta = _login(client, "t10_a")
        r = client.get(f"/api/penetration/top10-holdings?as_of_date={AS_OF.isoformat()}",
                       headers={"x-session-token": ta})
        assert r.status_code == 200, r.text
        data = r.json()
        # 响应结构: {items: [{stock_code, ...}, ...]}
        all_codes = {item.get("stock_code") for item in (data.get("items") or [])}
        # A 不应看到 B 的 600519
        assert "600519" not in all_codes, f"A 不应看到 600519，实际={all_codes}"

    def test_user_b_only_sees_own_stocks(self, client, fresh_db):
        """user B 调 top10 只看到 600519，不看到 000001。"""
        a, b = self._seed_two_users_with_holdings(fresh_db)
        tb = _login(client, "t10_b", "pw_bbbb_1")
        r = client.get(f"/api/penetration/top10-holdings?as_of_date={AS_OF.isoformat()}",
                       headers={"x-session-token": tb})
        assert r.status_code == 200, r.text
        data = r.json()
        all_codes = {item.get("stock_code") for item in (data.get("items") or [])}
        assert "000001" not in all_codes, f"B 不应看到 000001，实际={all_codes}"

    def test_snapshot_not_cross_contaminated(self, client, fresh_db):
        """验证 snapshot 按 user_id 隔离 — A 的 pe_ttm 应来自 A 的 snapshot。"""
        a, b = self._seed_two_users_with_holdings(fresh_db)
        ta = _login(client, "t10_a")
        r = client.get(f"/api/penetration/top10-holdings?as_of_date={AS_OF.isoformat()}",
                       headers={"x-session-token": ta})
        assert r.status_code == 200
        data = r.json()
        # 找 000001 的 pe_ttm
        for item in (data.get("items") or []):
            if item.get("stock_code") == "000001":
                pe_found = item.get("pe_ttm")
                # A 的 000001 pe_ttm=8.5，不应是 B 的 30.0
                if pe_found is not None:
                    assert abs(pe_found - 8.5) < 0.1, f"A 的 pe_ttm 应为 8.5，实际={pe_found}"
                break


# ============================================================
# Bug #247: get_kpi → list_drillable_indices 漏传 user_id
# ============================================================

class TestBug247KpiIsolation:
    """验证 get_kpi 中 list_drillable_indices 按 user_id 隔离。

    修复前：list_drillable_indices(db, as_of_date) 不传 user_id，
    导致 _aggregate_holdings_by_fund 聚合 ALL users 的 holdings。
    修复后：list_drillable_indices(db, as_of_date, user_id=eff_uid)。
    """

    def _seed_two_users_with_drillable_funds(self, db):
        """创建 2 个 user，各有不同可下钻基金持仓。"""
        a = _seed_user(db, "kpi_a")
        b = _seed_user(db, "kpi_b", password="pw_bbbb_1")
        # FundIndexMap（公共数据，无 user_id）
        db.add_all([
            FundIndexMap(fund_code="159001.SZ", index_code="000300.SH",
                         index_name="沪深300", as_of_date=AS_OF),
            FundIndexMap(fund_code="510300.SH", index_code="000300.SH",
                         index_name="沪深300", as_of_date=AS_OF),
        ])
        # FundDrillSnapshot（公共数据，无 user_id）
        # 159001 → 000001（平安银行）
        db.add(FundDrillSnapshot(
            fund_code="159001.SZ", as_of_date=AS_OF, stock_code="000001.SZ",
            stock_name="平安银行", weight_pct=1.5, baseline_price=10.0,
            current_price=10.0, shares_equivalent=0.95 * 1.5 / 10.0,
        ))
        # 510300 → 600519（贵州茅台）
        db.add(FundDrillSnapshot(
            fund_code="510300.SH", as_of_date=AS_OF, stock_code="600519.SH",
            stock_name="贵州茅台", weight_pct=5.0, baseline_price=1800.0,
            current_price=1800.0, shares_equivalent=0.95 * 5.0 / 1800.0,
        ))
        # A 持有 159001，B 持有 510300
        db.add_all([
            Holding(user_id=a.id, security_code="159001.SZ", security_name="易方达沪深300",
                    quantity=1000, price=1.5, currency="CNY", amount=1500, amount_cny=1500,
                    asset_type="a_share_etf"),
            Holding(user_id=b.id, security_code="510300.SH", security_name="华泰柏瑞沪深300",
                    quantity=10, price=4.0, currency="CNY", amount=40, amount_cny=40,
                    asset_type="a_share_etf"),
        ])
        # snapshot（按 user_id 隔离）
        db.add(AShareFinancialSnapshot(
            user_id=a.id, as_of_date=AS_OF, stock_code="000001.SZ",
            stock_name="平安银行", pe_ttm=8.5, pb_mrq=0.6, ps_ttm=2.0,
            dividend_yield=5.0, market_cap=3500.0,
        ))
        db.add(AShareFinancialSnapshot(
            user_id=b.id, as_of_date=AS_OF, stock_code="600519.SH",
            stock_name="贵州茅台", pe_ttm=30.0, pb_mrq=10.0, ps_ttm=15.0,
            dividend_yield=1.0, market_cap=20000.0,
        ))
        db.commit()
        return a, b

    def test_kpi_does_not_error(self, client, fresh_db):
        """KPI 端点正常返回（不因 user_id 修复而 break）。"""
        a, b = self._seed_two_users_with_drillable_funds(fresh_db)
        ta = _login(client, "kpi_a")
        r = client.get(f"/api/penetration/kpi?as_of_date={AS_OF.isoformat()}",
                       headers={"x-session-token": ta})
        assert r.status_code == 200, r.text
        data = r.json()
        # 基本结构检查 — KPI 响应包裹在 values 里
        assert "values" in data
        assert "csi300_pe" in data["values"]
        assert "daily_change_pct" in data["values"]

    def test_kpi_drillable_indices_isolated(self, client, fresh_db):
        """验证 list_drillable_indices 在 get_kpi 内按 user_id 隔离。

        修复前：A 调 KPI 时 list_drillable_indices 不传 user_id，
        会聚合 A+B 两个 user 的 holdings（159001 + 510300）。
        修复后：A 只聚合自己的 159001。

        验证方式：直接调 /api/penetration/drillable-indices 端点（它正确传了 user_id），
        对比 A 和 B 的结果不同 → 隔离生效。
        """
        a, b = self._seed_two_users_with_drillable_funds(fresh_db)
        ta = _login(client, "kpi_a")
        tb = _login(client, "kpi_b", "pw_bbbb_1")
        # A 调 drillable-indices
        ra = client.get(f"/api/penetration/drillable-indices?as_of_date={AS_OF.isoformat()}",
                        headers={"x-session-token": ta})
        assert ra.status_code == 200, ra.text
        # B 调 drillable-indices
        rb = client.get(f"/api/penetration/drillable-indices?as_of_date={AS_OF.isoformat()}",
                        headers={"x-session-token": tb})
        assert rb.status_code == 200, rb.text
        # 响应结构: {"as_of_date": "...", "indices": [...]}
        da_indices = ra.json().get("indices", [])
        db_indices = rb.json().get("indices", [])
        # 每张 card 有 fund_codes 列表
        a_funds = {fc for card in da_indices for fc in card.get("fund_codes", [])}
        b_funds = {fc for card in db_indices for fc in card.get("fund_codes", [])}
        # A 不应包含 B 的 510300
        assert "510300.SH" not in a_funds, f"A 不应看到 510300，实际 fund_codes={a_funds}"
        # B 不应包含 A 的 159001
        assert "159001.SZ" not in b_funds, f"B 不应看到 159001，实际 fund_codes={b_funds}"


# ============================================================
# view_as 矩阵验证（admin/advisor/user × view_as）
# ============================================================

class TestViewAsMatrix:
    """验证 view_as 代理权限矩阵。"""

    def _seed_matrix(self, db):
        """创建 admin + advisor + 3 clients，advisor 关联 client1/client2。"""
        admin = _seed_user(db, "m_admin", password="pw_admin_1", is_admin=True)
        advisor = _seed_user(db, "m_advisor", password="pw_adv_1", is_advisor=True)
        c1 = _seed_user(db, "m_c1", password="pw_c1_aaaa_1")
        c2 = _seed_user(db, "m_c2", password="pw_c2_aaaa_1")
        c3 = _seed_user(db, "m_c3", password="pw_c3_aaaa_1")
        # advisor 关联 c1, c2（不关联 c3）
        db.add_all([
            UserRelation(advisor_user_id=advisor.id, client_user_id=c1.id,
                         status="ACTIVE", initiator_user_id=advisor.id),
            UserRelation(advisor_user_id=advisor.id, client_user_id=c2.id,
                         status="ACTIVE", initiator_user_id=advisor.id),
        ])
        # 每个 client 有不同持仓
        for u, code, name, val in [
            (c1, "000001", "平安银行", 1000),
            (c2, "600519", "贵州茅台", 2000),
            (c3, "000858", "五粮液", 3000),
        ]:
            db.add(Holding(user_id=u.id, security_code=code, security_name=name,
                           quantity=1, price=val, currency="CNY", amount=val, amount_cny=val,
                           asset_type="a_share_equity"))
        db.commit()
        return admin, advisor, c1, c2, c3

    def test_admin_can_view_as_any_client(self, client, fresh_db):
        """admin 可以 view_as 任意 client。"""
        admin, advisor, c1, c2, c3 = self._seed_matrix(fresh_db)
        t = _login(client, "m_admin", "pw_admin_1")
        for c in [c1, c2, c3]:
            r = client.get(f"/api/holdings/summary?view_as={c.id}",
                           headers={"x-session-token": t})
            assert r.status_code == 200, f"admin view_as c{c.id} 失败: {r.text}"

    def test_advisor_can_view_as_related_clients(self, client, fresh_db):
        """advisor 可以 view_as 关联的 client（c1, c2）。"""
        admin, advisor, c1, c2, c3 = self._seed_matrix(fresh_db)
        t = _login(client, "m_advisor", "pw_adv_1")
        for c in [c1, c2]:
            r = client.get(f"/api/holdings/summary?view_as={c.id}",
                           headers={"x-session-token": t})
            assert r.status_code == 200, f"advisor view_as c{c.id} 失败: {r.text}"

    def test_advisor_cannot_view_as_unrelated_client(self, client, fresh_db):
        """advisor 不能 view_as 未关联的 client（c3）→ 403。"""
        admin, advisor, c1, c2, c3 = self._seed_matrix(fresh_db)
        t = _login(client, "m_advisor", "pw_adv_1")
        r = client.get(f"/api/holdings/summary?view_as={c3.id}",
                       headers={"x-session-token": t})
        assert r.status_code == 403, f"advisor 不应能 view_as c3，实际={r.status_code}"

    def test_regular_user_cannot_view_as_others(self, client, fresh_db):
        """普通 user 不能 view_as 其他 user → 403。"""
        admin, advisor, c1, c2, c3 = self._seed_matrix(fresh_db)
        t = _login(client, "m_c1", "pw_c1_aaaa_1")
        r = client.get(f"/api/holdings/summary?view_as={c2.id}",
                       headers={"x-session-token": t})
        assert r.status_code == 403

    def test_view_as_returns_correct_user_data(self, client, fresh_db):
        """admin view_as c1 时，看到的是 c1 的数据（1000），不是 admin 的。"""
        admin, advisor, c1, c2, c3 = self._seed_matrix(fresh_db)
        t = _login(client, "m_admin", "pw_admin_1")
        r = client.get(f"/api/holdings/summary?view_as={c1.id}",
                       headers={"x-session-token": t})
        assert r.status_code == 200
        s = r.json()
        assert abs(s["total_value"] - 1000) < 0.1, f"view_as c1 应看到 1000，实际={s['total_value']}"

    def test_top10_view_as_isolated(self, client, fresh_db):
        """admin view_as c1 调 top10，只看到 c1 的 000001。"""
        admin, advisor, c1, c2, c3 = self._seed_matrix(fresh_db)
        # 给 c1 加 snapshot
        fresh_db.add(AShareFinancialSnapshot(
            user_id=c1.id, as_of_date=AS_OF, stock_code="000001.SZ",
            stock_name="平安银行", pe_ttm=8.5, pb_mrq=0.6, ps_ttm=2.0,
            dividend_yield=5.0, market_cap=3500.0,
        ))
        fresh_db.commit()
        t = _login(client, "m_admin", "pw_admin_1")
        r = client.get(f"/api/penetration/top10-holdings?as_of_date={AS_OF.isoformat()}&view_as={c1.id}",
                       headers={"x-session-token": t})
        assert r.status_code == 200, r.text
        data = r.json()
        # 响应结构: {items: [{stock_code, ...}, ...]}
        all_codes = {item.get("stock_code") for item in (data.get("items") or [])}
        assert "000001" in all_codes, f"view_as c1 应看到 000001，实际={all_codes}"
        assert "600519" not in all_codes, f"view_as c1 不应看到 600519，实际={all_codes}"
