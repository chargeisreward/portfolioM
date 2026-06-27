"""TDD: 验证 run_penetration 的 user_id 隔离（2026-06-27）。

根因：run_penetration 不接受 user_id 参数，全表扫描 Holding 且 5 处
FullHoldingSnapshot/PenetrationSnapshot 构造不设 user_id，DB 层 DEFAULT 2
静默兜底，导致所有用户穿透数据全归 user_id=2，其他用户恒为 0。

修复后期望：
  1. run_penetration(db, as_of, user_id) 接受 user_id 参数
  2. Holding 查询按 user_id 过滤
  3. 写入的 FullHoldingSnapshot / PenetrationSnapshot 行带正确 user_id
  4. _wipe 只清理目标 user_id 的数据，不影响其他用户
  5. 多用户分别调用不互相覆盖
"""
import os
os.environ["APP_PASSWORD"] = ""

import tempfile
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models import (
    AShareFinancialSnapshot,
    FundIndexMap,
    FullHoldingSnapshot,
    Holding,
    IndexConstituentSnapshot,
    PenetrationSnapshot,
)


AS_OF = date(2026, 5, 29)


# ============================================================
# fixtures
# ============================================================

@pytest.fixture
def fresh_db():
    """临时文件 SQLite（无 DB DEFAULT 兜底，能暴露 NOT NULL 违规）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    db = TestSession()
    yield db
    db.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def _seed_public_data(db):
    """种入公共数据（FundIndexMap + 指数成分股 + 成分股财务快照）。

    这些是市场公共数据，不带 user_id 隔离（UK 不含 user_id），只种一次。
    run_penetration 的 _resolve_snapshot_for_code 查询时不按 user_id 过滤。
    """
    # 两只基金都跟踪沪深300
    db.add(FundIndexMap(fund_code="510300.SH", index_code="000300.SH",
                        index_name="沪深300", as_of_date=AS_OF))
    db.add(FundIndexMap(fund_code="159001.SZ", index_code="000300.SH",
                        index_name="沪深300", as_of_date=AS_OF))
    # 指数成分股（600519 茅台，权重 5%）
    db.add(IndexConstituentSnapshot(
        index_code="000300", as_of_date=AS_OF,
        stock_code="600519.SH", stock_name="贵州茅台", weight=5.0,
    ))
    # 成分股财务快照（公共数据，UK 是 as_of_date+stock_code，不含 user_id）
    db.add(AShareFinancialSnapshot(
        user_id=1, as_of_date=AS_OF,
        stock_code="600519.SH", stock_name="贵州茅台",
        pe_ttm=30.0, pe_ttm_dynamic=30.0,
        baseline_price=1800.0, current_price=1800.0,
    ))
    db.commit()


def _seed_holding(db, uid, fund_code, fund_name, holding_amount_cny):
    """种入一条用户持仓（user-personal 数据）。"""
    db.add(Holding(
        user_id=uid,
        security_code=fund_code,
        security_name=fund_name,
        quantity=1000, price=1.5, currency="CNY",
        amount=holding_amount_cny, amount_cny=holding_amount_cny,
        asset_type="a_share_etf",
    ))
    db.commit()


# ============================================================
# 测试 1: run_penetration 接受 user_id 参数且只处理该用户持仓
# ============================================================

class TestRunPenetrationUserIdParam:
    """验证 run_penetration 接受 user_id 并按用户隔离。"""

    def test_accepts_user_id_param(self, fresh_db):
        """run_penetration 必须接受 user_id 参数（当前签名不接受 → TypeError）。"""
        from services.penetration_v2 import run_penetration
        _seed_public_data(fresh_db)
        _seed_holding(fresh_db, uid=10, fund_code="510300.SH",
                      fund_name="华泰柏瑞沪深300", holding_amount_cny=10000)
        # 调用带 user_id 参数 — 修复前会 TypeError
        report = run_penetration(fresh_db, AS_OF, user_id=10)
        assert report.holdings_seen == 1, f"应只看到 user_id=10 的 1 条持仓，实际={report.holdings_seen}"

    def test_filters_holding_by_user(self, fresh_db):
        """run_penetration 只处理目标 user_id 的持仓，不混入其他用户。"""
        from services.penetration_v2 import run_penetration
        _seed_public_data(fresh_db)
        # 用户 10 持有 510300
        _seed_holding(fresh_db, uid=10, fund_code="510300.SH",
                      fund_name="华泰柏瑞沪深300", holding_amount_cny=10000)
        # 用户 20 持有 159001
        _seed_holding(fresh_db, uid=20, fund_code="159001.SZ",
                      fund_name="易方达沪深300", holding_amount_cny=5000)
        # 为 user 10 跑
        report10 = run_penetration(fresh_db, AS_OF, user_id=10)
        assert report10.holdings_seen == 1, "user 10 应只看到自己的 1 条"
        # 为 user 20 跑
        report20 = run_penetration(fresh_db, AS_OF, user_id=20)
        assert report20.holdings_seen == 1, "user 20 应只看到自己的 1 条"


# ============================================================
# 测试 2: 写入的行带正确 user_id
# ============================================================

class TestSnapshotCarriesUserId:
    """验证 FullHoldingSnapshot / PenetrationSnapshot 行带正确 user_id。"""

    def test_full_holding_snapshot_user_id(self, fresh_db):
        """FullHoldingSnapshot 行的 user_id 必须等于传入的 user_id，而非 DB 兜底。"""
        from services.penetration_v2 import run_penetration
        _seed_public_data(fresh_db)
        _seed_holding(fresh_db, uid=30, fund_code="510300.SH",
                      fund_name="华泰柏瑞沪深300", holding_amount_cny=10000)
        run_penetration(fresh_db, AS_OF, user_id=30)

        rows = fresh_db.query(FullHoldingSnapshot).all()
        assert len(rows) > 0, "应有 FullHoldingSnapshot 行"
        bad = [r for r in rows if r.user_id != 30]
        assert not bad, f"存在 user_id≠30 的行: {[(r.id, r.user_id) for r in bad]}"

    def test_penetration_snapshot_user_id(self, fresh_db):
        """PenetrationSnapshot 行的 user_id 必须等于传入的 user_id。"""
        from services.penetration_v2 import run_penetration
        _seed_public_data(fresh_db)
        _seed_holding(fresh_db, uid=40, fund_code="510300.SH",
                      fund_name="华泰柏瑞沪深300", holding_amount_cny=10000)
        run_penetration(fresh_db, AS_OF, user_id=40)

        rows = fresh_db.query(PenetrationSnapshot).all()
        assert len(rows) > 0, "应有 PenetrationSnapshot 行"
        bad = [r for r in rows if r.user_id != 40]
        assert not bad, f"存在 user_id≠40 的行: {[(r.id, r.user_id) for r in bad]}"


# ============================================================
# 测试 3: _wipe 只清理目标 user_id，不影响其他用户
# ============================================================

class TestWipeIsScopedToUser:
    """验证 _wipe（或 run_penetration 开头的清理）按 user_id 过滤。"""

    def test_wipe_does_not_delete_other_user(self, fresh_db):
        """为 user 50 跑 penetration 不应清掉 user 60 已有的数据。"""
        from services.penetration_v2 import run_penetration
        _seed_public_data(fresh_db)
        # 先为 user 50 跑（写入 user_id=50 的数据）
        _seed_holding(fresh_db, uid=50, fund_code="510300.SH",
                      fund_name="华泰柏瑞沪深300", holding_amount_cny=10000)
        run_penetration(fresh_db, AS_OF, user_id=50)
        n50_before = fresh_db.query(FullHoldingSnapshot).filter(
            FullHoldingSnapshot.user_id == 50).count()
        assert n50_before > 0

        # 再为 user 60 跑（_wipe 不应删掉 user 50 的数据）
        _seed_holding(fresh_db, uid=60, fund_code="159001.SZ",
                      fund_name="易方达沪深300", holding_amount_cny=5000)
        run_penetration(fresh_db, AS_OF, user_id=60)

        n50_after = fresh_db.query(FullHoldingSnapshot).filter(
            FullHoldingSnapshot.user_id == 50).count()
        assert n50_after == n50_before, f"user 50 的数据被误删: before={n50_before}, after={n50_after}"

        n60 = fresh_db.query(FullHoldingSnapshot).filter(
            FullHoldingSnapshot.user_id == 60).count()
        assert n60 > 0, "user 60 应有自己的数据"


# ============================================================
# 测试 4: 两用户不交叉污染
# ============================================================

class TestTwoUsersNoCrossContamination:
    """验证两用户分别调用 run_penetration 不互相污染。"""

    def test_each_user_sees_only_own_data(self, fresh_db):
        """两用户各自跑后，FullHoldingSnapshot 按 user_id 严格隔离。"""
        from services.penetration_v2 import run_penetration
        _seed_public_data(fresh_db)
        _seed_holding(fresh_db, uid=70, fund_code="510300.SH",
                      fund_name="华泰柏瑞沪深300", holding_amount_cny=10000)
        _seed_holding(fresh_db, uid=80, fund_code="159001.SZ",
                      fund_name="易方达沪深300", holding_amount_cny=5000)

        run_penetration(fresh_db, AS_OF, user_id=70)
        run_penetration(fresh_db, AS_OF, user_id=80)

        # user 70 的行全部 user_id=70
        rows70 = fresh_db.query(FullHoldingSnapshot).filter(
            FullHoldingSnapshot.user_id == 70).all()
        assert rows70, "user 70 应有数据"
        assert all(r.user_id == 70 for r in rows70)
        # user 70 的来源基金应是 510300，不是 159001
        sources70 = {r.source_holding_code for r in rows70}
        assert "510300.SH" in sources70
        assert "159001.SZ" not in sources70, "user 70 不应看到 user 80 的基金"

        # user 80 的行全部 user_id=80
        rows80 = fresh_db.query(FullHoldingSnapshot).filter(
            FullHoldingSnapshot.user_id == 80).all()
        assert rows80, "user 80 应有数据"
        assert all(r.user_id == 80 for r in rows80)
        sources80 = {r.source_holding_code for r in rows80}
        assert "159001.SZ" in sources80
        assert "510300.SH" not in sources80, "user 80 不应看到 user 70 的基金"
