"""下钻估值数据补全端到端测试 — 2026-06-25

验证：
1. weighted_pe / weighted_pb / weighted_ps / weighted_dividend_yield 数值与手算一致
2. est_deviation_pct 公式正确（估算市值/基金市值 - 1，现金已含在 per_fund_est 中）
3. 估值表 user_id 公共化后查询正常（不按 user_id 过滤）
4. drill_snapshot 生成时 join 估值表写入 4 字段
5. 现金-下钻行（CASH）来自公共数据层 FundDrillSnapshot，流经所有下游层
6. 港股通汇率处理（2026-06-25）：HKD 成分股用本币(CNY)字段算市值，est_deviation_pct ≈ 0

数据源：SQLite 临时库 + FundDrillSnapshot + AShareFinancialSnapshot（无 user_id）
"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
from database import Base
from models import (
    FundDrillSnapshot,
    FundIndexMap,
    AShareFinancialSnapshot,
    Holding,
    SecurityMaster,
)
from services.drill_public_service import get_public_cards, get_public_detail
from services.drill_orchestration_service import list_drillable_cards


# ========== fixtures ==========

@pytest.fixture
def e2e_db(monkeypatch):
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

    db = TestSession()
    yield db
    db.close()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


# ========== 种子数据 ==========

def _seed_e2e_data(db, as_of=date(2026, 6, 25)):
    """种子完整下钻数据：
    - 1 只基金 510300.SH 跟踪 000300 指数
    - 2 只 A 股成分股 600519.SH / 000858.SZ
    - FundDrillSnapshot 含 baseline_price / current_price / shares_equivalent
    - AShareFinancialSnapshot 含 pe_ttm / pb_mrq / ps_ttm / dividend_yield（不传 user_id）
    - SecurityMaster.is_drillable=True
    """
    # 基金 → 指数映射
    fim = FundIndexMap(
        fund_code="510300.SH",
        fund_name="沪深300ETF",
        index_code="000300.SH",
        index_name="沪深300",
        as_of_date=as_of,
        source="test",
    )
    db.add(fim)

    # SecurityMaster 标记可下钻
    sm = SecurityMaster(
        security_code="510300.SH",
        security_name="沪深300ETF",
        asset_type="a_share_etf",
        is_drillable=True,
        index_code="000300.SH",
        index_name="沪深300",
    )
    db.add(sm)

    # A 股估值快照（不传 user_id，落库 NULL）
    # 600519.SH: pe_ttm=30.0, pb_mrq=10.0, ps_ttm=15.0, dividend_yield=1.5
    a_snap1 = AShareFinancialSnapshot(
        as_of_date=date(2026, 5, 29),  # 基础数据基准期5月29日
        stock_code="600519.SH",
        stock_name="贵州茅台",
        pe_ttm=30.0,
        pb_mrq=10.0,
        ps_ttm=15.0,
        dividend_yield=1.5,
        baseline_price=1500.0,
        current_price=1500.0,
        source="test",
    )
    # 000858.SZ: pe_ttm=20.0, pb_mrq=5.0, ps_ttm=2.0, dividend_yield=2.0
    a_snap2 = AShareFinancialSnapshot(
        as_of_date=date(2026, 5, 29),  # 基础数据基准期5月29日
        stock_code="000858.SZ",
        stock_name="五粮液",
        pe_ttm=20.0,
        pb_mrq=5.0,
        ps_ttm=2.0,
        dividend_yield=2.0,
        baseline_price=200.0,
        current_price=200.0,
        source="test",
    )
    # 注意：SQLAlchemy Session.add() 签名是 add(instance, warn=False)，
    # 多参数 add 会把第二个对象当作 warn 传入导致漏插入。必须用 add_all。
    db.add_all([a_snap1, a_snap2])

    # 公共下钻截面：2 只成分股
    # 600519.SH: baseline=1500, current=1600, shares_eq=0.001
    #   pe_ttm=30, pe_ttm_dynamic=35（≠ 30×1600/1500=32，验证用动态值而非实时算）
    #   pb_mrq=10, pb_mrq_dynamic=11（≠ 10×1600/1500=10.67）
    #   ps_ttm=15, ps_ttm_dynamic=17（≠ 15×1600/1500=16）
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
        baseline_price_cny=1500.0,  # A 股 fx=1, 本币=原币
        # 直接预填估值字段（模拟 drill_snapshot.py 生成后状态）
        pe_ttm=30.0,
        pb_mrq=10.0,
        ps_ttm=15.0,
        dividend_yield=1.5,
        pe_ttm_dynamic=35.0,
        pb_mrq_dynamic=11.0,
        ps_ttm_dynamic=17.0,
    )
    # 000858.SZ: baseline=200, current=210, shares_eq=0.002
    #   pe_ttm=20, pe_ttm_dynamic=25（≠ 20×210/200=21，验证用动态值）
    #   pb_mrq=5, pb_mrq_dynamic=6（≠ 5×210/200=5.25）
    #   ps_ttm=2, ps_ttm_dynamic=2.5（≠ 2×210/200=2.1）
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
        baseline_price_cny=200.0,  # A 股 fx=1, 本币=原币
        pe_ttm=20.0,
        pb_mrq=5.0,
        ps_ttm=2.0,
        dividend_yield=2.0,
        pe_ttm_dynamic=25.0,
        pb_mrq_dynamic=6.0,
        ps_ttm_dynamic=2.5,
    )
    # 现金-下钻行（公共数据层分解 — 2026-06-25）
    # 基金 = 95% 指数 + 5% 现金。基金价格 = amount_cny / quantity = 45000 / 10000 = 4.5
    # CASH shares_equivalent = fund_price × 0.05 = 4.5 × 0.05 = 0.225（每份基金含现金金额）
    # current_price = 1.0 → 估算市值 = 0.225 × 1.0 = 0.225（每份基金）
    # 估值字段全部为 None（现金无盈利/净资产/营收/分红）
    snap3 = FundDrillSnapshot(
        fund_code="510300.SH",
        as_of_date=as_of,
        stock_code="CASH",
        stock_name="下钻-现金",
        weight_pct=5.0,
        baseline_price=1.0,
        current_price=1.0,
        shares_equivalent=0.225,
        currency="CNY",
        current_price_cny=1.0,
        baseline_price_cny=1.0,
        pe_ttm=None,
        pb_mrq=None,
        ps_ttm=None,
        dividend_yield=None,
        pe_ttm_dynamic=None,
        pb_mrq_dynamic=None,
        ps_ttm_dynamic=None,
    )
    db.add_all([snap1, snap2, snap3])

    db.commit()


def _seed_holding(db, user_id, fund_code="510300.SH", quantity=10000.0,
                  amount_cny=45000.0, price=4.5, security_name="沪深300ETF"):
    """种子用户持仓。"""
    h = Holding(
        user_id=user_id,
        security_code=fund_code,
        security_name=security_name,
        quantity=quantity,
        price=price,
        currency="CNY",
        amount=quantity * price,
        amount_cny=amount_cny,
        asset_type="a_share_etf",
        import_batch="test",
    )
    db.add(h)
    db.commit()


def _seed_hk_drill_data(db, as_of=date(2026, 6, 25)):
    """种子港股通下钻数据（验证汇率处理 — 2026-06-25）。

    场景：1 只港股通基金 018388.OF + 1 只 HKD 成分股 00005.HK + CASH 行。
    基金价格 fund_price = 1.0 CNY，成分股权重 100%，fx_rate = 0.92 (HKD→CNY)。

    成分股原币：baseline=100 HKD, current=110 HKD
    成分股本币：baseline_cny=92, current_cny=101.2 (= 110 × 0.92)
    shares_eq = fund_price × 0.95 × (weight/100) / price_cny = 1.0 × 0.95 × 1.0 / 101.2

    期望（用量纲正确的本币字段）：
      per_fund_est = shares_eq × current_price_cny + CASH × 1.0
                   = (0.95/101.2) × 101.2 + 0.05 = 0.95 + 0.05 = 1.0
      card_est = quantity × 1.0 → est_deviation_pct = 0.0 ✓

    修复前 bug（用原币字段）：
      per_fund_est_wrong = shares_eq × current_price(HKD) + CASH × 1.0
                         = (0.95/101.2) × 110 + 0.05 = 1.0826
      → est_deviation_pct = +8.26% ✗
    """
    fim = FundIndexMap(
        fund_code="018388.OF",
        fund_name="港股通高股息",
        index_code="930914.CSI",
        index_name="港股通高股息",
        as_of_date=as_of,
        source="test",
    )
    db.add(fim)

    sm = SecurityMaster(
        security_code="018388.OF",
        security_name="港股通高股息",
        asset_type="hk_share_etf",
        is_drillable=True,
        index_code="930914.CSI",
        index_name="港股通高股息",
    )
    db.add(sm)

    # HKD 成分股行：shares_eq 用 CNY 价算
    shares_eq_hk = 1.0 * 0.95 * 1.0 / 101.2  # fund_price × 0.95 × weight / price_cny
    snap_hk = FundDrillSnapshot(
        fund_code="018388.OF",
        as_of_date=as_of,
        stock_code="00005.HK",
        stock_name="汇丰控股",
        weight_pct=100.0,
        baseline_price=100.0,            # 原币 HKD
        current_price=110.0,             # 原币 HKD
        shares_equivalent=shares_eq_hk,
        currency="HKD",
        current_price_cny=101.2,         # 本币 CNY = 110 × 0.92
        baseline_price_cny=92.0,         # 本币 CNY = 100 × 0.92
        fx_rate=0.92,
        fx_date=as_of,
        cny_currency="CNY",
        pe_ttm=10.0,
        pb_mrq=2.0,
        pe_ttm_dynamic=11.0,
        pb_mrq_dynamic=2.2,
    )
    # CASH 行（5% 现金）
    snap_cash = FundDrillSnapshot(
        fund_code="018388.OF",
        as_of_date=as_of,
        stock_code="CASH",
        stock_name="下钻-现金",
        weight_pct=5.0,
        baseline_price=1.0,
        current_price=1.0,
        shares_equivalent=0.05,          # fund_price × 0.05 = 1.0 × 0.05
        currency="CNY",
        current_price_cny=1.0,
        baseline_price_cny=1.0,
        fx_rate=1.0,
        fx_date=as_of,
        cny_currency="CNY",
    )
    db.add_all([snap_hk, snap_cash])
    db.commit()


# ========== 测试 ==========

class TestValuationWeighted:
    """测试公共层 weighted_pe / pb / ps / dividend_yield 公式。"""

    def test_weighted_pe_matches_manual_calc(self, e2e_db):
        """验证 weighted_pe 与手算一致。"""
        _seed_e2e_data(e2e_db)

        cards = get_public_cards(e2e_db, date(2026, 6, 25))
        assert len(cards) == 1
        card = cards[0]
        assert card["index_code"] == "000300"

        # 手算（PE/PB/PS 用持久化的动态值 pe_ttm_dynamic，不再实时算 price_ratio）：
        # 600519.SH: weight_basis = 0.001 × 1500 = 1.5
        #            pe_dyn = pe_ttm_dynamic = 35.0
        #            virt_pe += 1.5 / 35.0 = 0.042857
        # 000858.SZ: weight_basis = 0.002 × 200 = 0.4
        #            pe_dyn = pe_ttm_dynamic = 25.0
        #            virt_pe += 0.4 / 25.0 = 0.016
        # Σ weight_basis = 1.5 + 0.4 = 1.9
        # Σ virt_pe = 0.042857 + 0.016 = 0.058857
        # weighted_pe = 1.9 / 0.058857 = 32.28
        expected_pe = round(1.9 / (1.5/35.0 + 0.4/25.0), 4)
        assert card["weighted_pe"] == expected_pe
        assert card["weighted_pe"] > 0

        # weighted_pb（用 pb_mrq_dynamic）
        # 600519: pb_dyn = 11.0, virt_pb += 1.5/11 = 0.136364
        # 000858: pb_dyn = 6.0, virt_pb += 0.4/6 = 0.066667
        # weighted_pb = 1.9 / (0.136364 + 0.066667) = 1.9 / 0.203031 = 9.36
        expected_pb = round(1.9 / (1.5/11.0 + 0.4/6.0), 4)
        assert card["weighted_pb"] == expected_pb

        # weighted_dividend_yield（算术平均，仍用 price_ratio 实时算，因为无 dynamic 字段）
        # 600519: price_ratio = 1600/1500 = 1.0667, dy_dyn = 1.5 / 1.0667 = 1.40625, weighted += 1.5 × 1.40625 = 2.109375
        # 000858: price_ratio = 210/200 = 1.05, dy_dyn = 2.0 / 1.05 = 1.904762, weighted += 0.4 × 1.904762 = 0.761905
        # Σ weighted = 2.109375 + 0.761905 = 2.871279
        # weighted_dy = 2.871279 / 1.9 = 1.5112
        expected_dy = round((1.5 * (1.5/1.0666667) + 0.4 * (2.0/1.05)) / 1.9, 4)
        assert card["weighted_dividend_yield"] == expected_dy

        # static_amount_cny = Σ weight_basis = 1.9
        assert card["static_amount_cny"] == round(1.9, 4)


class TestEstDeviationPct:
    """测试用户层 est_deviation_pct 公式。"""

    def test_est_deviation_pct_formula(self, e2e_db):
        """验证 est_deviation_pct = (card_est / card_fund_value - 1) × 100。

        新公式（2026-06-25）：现金-下钻来自公共数据层 FundDrillSnapshot 的 CASH 行，
        per_fund_est 聚合查询自动包含 CASH 贡献，card_est 已含 5% 现金部分，
        无需在编排层额外加 card_cash。
        """
        _seed_e2e_data(e2e_db)
        # 用户 1 持仓 10000 份 510300.SH, amount_cny=45000
        _seed_holding(e2e_db, user_id=1)

        cards = list_drillable_cards(e2e_db, date(2026, 6, 25), user_id=1)
        assert len(cards) == 1
        card = cards[0]

        # 手算（per_fund_est 含 CASH 行，来自 FundDrillSnapshot 聚合）：
        # per_fund_est["510300.SH"] = 0.001×1600 + 0.002×210 + 0.225×1.0(CASH)
        #                           = 1.6 + 0.42 + 0.225 = 2.245
        # card_est = 10000 × 2.245 = 22450.0
        # card_fund_value = 45000.0
        # est_deviation_pct = (22450 / 45000 - 1) × 100 = -50.1111
        # （真实场景 card_est 含 CASH ≈ card_fund_value → deviation ≈ 0）
        assert card["est_market_value_cny"] == 22450.0
        assert card["est_deviation_pct"] == round((22450 / 45000 - 1) * 100, 4)

        # static_amount_cny = 10000 × per_fund_static（含 CASH 行）
        # per_fund_static = 0.001×1500 + 0.002×200 + 0.225×1.0(CASH)
        #                 = 1.5 + 0.4 + 0.225 = 2.125
        # static = 10000 × 2.125 = 21250.0
        assert card["static_amount_cny"] == 21250.0

        # weight_pct（只有一张卡 → 100%）
        assert card["weight_pct"] == 100.0


class TestValuationUserIdOptional:
    """测试估值表 user_id 公共化后查询正常。"""

    def test_valuation_query_works_without_user_id(self, e2e_db):
        """估值表不传 user_id 时查询正常。"""
        _seed_e2e_data(e2e_db)

        # 直接查 AShareFinancialSnapshot（不带 user_id 过滤）
        rows = e2e_db.query(AShareFinancialSnapshot).filter(
            AShareFinancialSnapshot.as_of_date == date(2026, 5, 29)  # 基础数据基准期5月29日
        ).all()
        assert len(rows) == 2
        # user_id 应该是 NULL（落库时未传）
        for r in rows:
            assert r.user_id is None

    def test_drill_snapshot_joins_valuation_correctly(self, e2e_db):
        """FundDrillSnapshot 已预填估值字段（模拟 drill_snapshot.py 生成后状态）。"""
        _seed_e2e_data(e2e_db)

        rows = e2e_db.query(FundDrillSnapshot).filter(
            FundDrillSnapshot.as_of_date == date(2026, 6, 25)
        ).all()
        # 2 只股票 + 1 CASH 行（公共数据层分解）
        assert len(rows) == 3

        # 验证 4 个估值字段已写入（股票行）
        by_code = {r.stock_code: r for r in rows}
        moutai = by_code["600519.SH"]
        assert moutai.pe_ttm == 30.0
        assert moutai.pb_mrq == 10.0
        assert moutai.ps_ttm == 15.0
        assert moutai.dividend_yield == 1.5

        # CASH 行估值字段应为 None
        cash_row = by_code["CASH"]
        assert cash_row.pe_ttm is None
        assert cash_row.pb_mrq is None
        assert cash_row.ps_ttm is None
        assert cash_row.dividend_yield is None


class TestDetailValuation:
    """测试明细层返回估值字段。"""

    def test_public_detail_returns_valuation_fields(self, e2e_db):
        """get_public_detail constituents 含 pe_ttm 等字段。"""
        _seed_e2e_data(e2e_db)

        detail = get_public_detail(e2e_db, date(2026, 6, 25), "000300")
        assert detail is not None
        # 2 只股票 + 1 CASH 行（公共数据层分解）
        assert len(detail["constituents"]) == 3

        moutai = next(c for c in detail["constituents"] if c["stock_code"] == "600519.SH")
        assert moutai["pe_ttm"] == 30.0
        assert moutai["pb_mrq"] == 10.0
        assert moutai["ps_ttm"] == 15.0
        assert moutai["dividend_yield"] == 1.5
        # 动态字段（2026-06-25 补全）
        assert moutai["pe_ttm_dynamic"] == 35.0
        assert moutai["pb_mrq_dynamic"] == 11.0
        assert moutai["ps_ttm_dynamic"] == 17.0
        assert "weight_at_baseline_pct" in moutai

        # 现金-下钻行（来自公共层 FundDrillSnapshot 的 CASH 行）
        cash_row = next(c for c in detail["constituents"] if c["stock_code"] == "CASH")
        assert cash_row["is_cash"] is True
        assert cash_row["stock_name"] == "下钻-现金"
        assert cash_row["weight_pct"] == 5.0
        assert cash_row["baseline_price"] == 1.0
        assert cash_row["current_price"] == 1.0
        assert cash_row["shares_equivalent"] == 0.225
        # CASH 行估值字段应为 None
        assert cash_row["pe_ttm"] is None
        assert cash_row["pb_mrq"] is None
        assert cash_row["ps_ttm"] is None
        assert cash_row["dividend_yield"] is None

        # CASH 行应排在 constituents 末尾（按权重降序，现金排末尾）
        assert detail["constituents"][-1]["stock_code"] == "CASH"


class TestHKDrillFxRate:
    """测试港股通下钻汇率处理（2026-06-25 双币种修正）。

    验证 HKD 成分股的估算市值用量纲正确的本币(CNY)字段计算，
    est_deviation_pct ≈ 0 而非 +8.26%（修复前的量纲混乱 bug）。
    """

    def test_hk_drill_est_deviation_near_zero(self, e2e_db):
        """港股通基金 est_deviation_pct ≈ 0（本币字段量纲一致）。"""
        _seed_hk_drill_data(e2e_db)
        # 用户持仓 10000 份 018388.OF, fund_price=1.0, amount_cny=10000
        _seed_holding(e2e_db, user_id=1, fund_code="018388.OF",
                      quantity=10000.0, amount_cny=10000.0, price=1.0,
                      security_name="港股通高股息")

        cards = list_drillable_cards(e2e_db, date(2026, 6, 25), user_id=1)
        assert len(cards) == 1
        card = cards[0]

        # per_fund_est (本币) = shares_eq × current_price_cny + CASH × 1.0
        #   = (0.95/101.2) × 101.2 + 0.05 × 1.0 = 0.95 + 0.05 = 1.0
        # card_est = 10000 × 1.0 = 10000.0
        # card_fund_value = amount_cny = 10000.0
        # est_deviation_pct = (10000/10000 - 1) × 100 = 0.0
        assert card["est_market_value_cny"] == 10000.0
        assert card["est_deviation_pct"] == 0.0

        # per_fund_static (本币) = shares_eq × baseline_price_cny + CASH × 1.0
        #   = (0.95/101.2) × 92.0 + 0.05 × 1.0 = 0.863636... + 0.05 = 0.913636...
        # static = 10000 × 0.913636... = 9136.3636
        expected_static = round(10000.0 * ((1.0 * 0.95 / 101.2) * 92.0 + 0.05), 4)
        assert card["static_amount_cny"] == expected_static

    def test_hk_drill_public_detail_returns_cny_fields(self, e2e_db):
        """get_public_detail 返回港股成分股的本币字段。"""
        _seed_hk_drill_data(e2e_db)

        detail = get_public_detail(e2e_db, date(2026, 6, 25), "930914")
        assert detail is not None

        hk = next(c for c in detail["constituents"] if c["stock_code"] == "00005.HK")
        # 原币字段
        assert hk["baseline_price"] == 100.0
        assert hk["current_price"] == 110.0
        assert hk["currency"] == "HKD"
        # 本币字段（公共层算好，下游/前端直接取）
        assert hk["baseline_price_cny"] == 92.0
        assert hk["current_price_cny"] == 101.2
        assert hk["fx_rate"] == 0.92

    def test_hk_drill_detail_est_market_value_uses_cny(self, e2e_db):
        """get_drill_detail 的 est_market_value_cny 用本币价算（非原币）。"""
        from services.drill_orchestration_service import get_drill_detail
        _seed_hk_drill_data(e2e_db)
        _seed_holding(e2e_db, user_id=1, fund_code="018388.OF",
                      quantity=10000.0, amount_cny=10000.0, price=1.0,
                      security_name="港股通高股息")

        detail = get_drill_detail(e2e_db, date(2026, 6, 25), "930914", user_id=1)
        assert detail is not None

        hk = next(c for c in detail["constituents"] if c["stock_code"] == "00005.HK")
        # user_hold_value = user_qty × shares_eq × current_price_cny
        #   = 10000 × (0.95/101.2) × 101.2 = 10000 × 0.95 = 9500.0
        assert hk["est_market_value_cny"] == 9500.0
        # CASH 行: 10000 × 0.05 × 1.0 = 500.0
        cash = next(c for c in detail["constituents"] if c["stock_code"] == "CASH")
        assert cash["est_market_value_cny"] == 500.0
