"""迁移脚本测试 — dry-run 输出 + bond 鉴别。"""

import pytest


@pytest.fixture
def seeded_legacy_db(in_memory_db):
    """Seed security_master_legacy with stock/fund/bond/qdii_bond mix."""
    from sqlalchemy import text
    db = in_memory_db
    db.execute(text("""
        CREATE TABLE security_master_legacy (
            security_code VARCHAR(20) PRIMARY KEY,
            security_name VARCHAR(100),
            currency VARCHAR(10) DEFAULT 'CNY',
            asset_type VARCHAR(20),
            type2 VARCHAR(20),
            exchange VARCHAR(20),
            security_type VARCHAR(20),
            fund_type VARCHAR(20),
            market VARCHAR(8),
            is_drillable BOOLEAN DEFAULT 0,
            index_code VARCHAR(20),
            index_name VARCHAR(80),
            benchmark_formula VARCHAR(500),
            premium_discount FLOAT,
            note VARCHAR(200),
            updated_by INTEGER,
            updated_at TIMESTAMP
        )
    """))
    rows = [
        ("600519.SH", "贵州茅台", "a_share_equity", "stock", None, "红利", None, 0, "000300.SH", "沪深300", 0),
        ("000001.SZ", "平安银行", "a_share_equity", "stock", None, None, None, 0, None, None, 0),
        ("510300.SH", "华泰柏瑞沪深300ETF", "a_share_etf", "fund", "etf", "新兴产业", "SH", 1, "000300.SH", "沪深300", 0),
        ("161725.OF", "招商中证白酒", "a_share_equity", "fund", "otc", "红利", None, 1, "399012.SZ", "中证白酒", 0),
        ("019547.SH", "19国债07", "bond", "bond", None, None, "SH", 0, None, None, 0),
        ("007360.OF", "易方达中短期债", "qdii_bond", "bond", "otc", None, None, 0, None, None, 0),
        ("005078.OF", "广发双债", "bond", "bond", "otc", None, None, 0, None, None, 0),
    ]
    for code, name, at, st, ft, t2, ex, drill, idx_c, idx_n, _ in rows:
        db.execute(
            text(
                "INSERT INTO security_master_legacy "
                "(security_code, security_name, asset_type, type2, exchange, "
                " security_type, fund_type, is_drillable, index_code, index_name, premium_discount) "
                "VALUES (:code, :name, :at, :t2, :ex, :st, :ft, :drill, :idx_c, :idx_n, :pd)"
            ),
            {"code": code, "name": name, "at": at, "t2": t2, "ex": ex,
             "st": st, "ft": ft, "drill": drill, "idx_c": idx_c, "idx_n": idx_n, "pd": 0},
        )
    db.commit()
    return db


def test_dry_run_counts(seeded_legacy_db):
    """dry-run 报告应反映正确分流:
    - 2 stocks (600519.SH, 000001.SZ)
    - 4 funds (510300.SH, 161725.OF, 007360.OF, 005078.OF) ← 含 3 bond-as-fund
    - 1 stock-bond (019547.SH, actual bond)
    """
    from scripts.migrate_split_security_master import dry_run_report
    report = dry_run_report(seeded_legacy_db)
    assert report["legacy_total"] == 7
    assert report["to_stock_master"] == 3
    assert report["to_fund_master"] == 4
    assert report["index_master_added"] >= 0
    assert report["classification_added"] >= 0


def test_dry_run_warns_on_unknown_type2(seeded_legacy_db):
    """未知的 type2 值应被列入 warnings。"""
    from sqlalchemy import text
    db = seeded_legacy_db
    db.execute(
        text(
            "INSERT INTO security_master_legacy "
            "(security_code, security_name, asset_type, type2, security_type) "
            "VALUES (:code, :name, :at, :t2, :st)"
        ),
        {"code": "999999.SH", "name": "未知主题测试",
         "at": "a_share_equity", "t2": "balanced", "st": "stock"},
    )
    db.commit()
    from scripts.migrate_split_security_master import dry_run_report
    report = dry_run_report(db)
    assert any("balanced" in w for w in report["warnings"])


def test_infer_fund_when_security_type_empty(seeded_legacy_db):
    """security_type=None + asset_type=a_share_etf → 应去 fund_master,且发 warning。"""
    from sqlalchemy import text
    db = seeded_legacy_db
    db.execute(
        text(
            "INSERT INTO security_master_legacy "
            "(security_code, security_name, asset_type, security_type) "
            "VALUES (:code, :name, :at, :st)"
        ),
        {"code": "510500.OF", "name": "中证500联接",
         "at": "a_share_etf", "st": None},
    )
    db.commit()
    from scripts.migrate_split_security_master import dry_run_report
    report = dry_run_report(db)
    assert report["to_fund_master"] == 5  # +1 vs base 4
    assert any("510500.OF" in w and "inferred" in w
               for w in report["warnings"])


def test_infer_stock_when_security_type_empty(seeded_legacy_db):
    """security_type=None + asset_type=us_stock → 应去 stock_master,且发 warning。"""
    from sqlalchemy import text
    db = seeded_legacy_db
    db.execute(
        text(
            "INSERT INTO security_master_legacy "
            "(security_code, security_name, asset_type, security_type) "
            "VALUES (:code, :name, :at, :st)"
        ),
        {"code": "AAPL", "name": "Apple Inc",
         "at": "us_stock", "st": None},
    )
    db.commit()
    from scripts.migrate_split_security_master import dry_run_report
    report = dry_run_report(db)
    # base: 3 stocks (600519.SH, 000001.SZ, 019547.SH) + 1 from AAPL = 4
    assert report["to_stock_master"] == 4
    assert any("AAPL" in w and "inferred" in w
               for w in report["warnings"])


def test_type2_us_tech_no_warning(seeded_legacy_db):
    """type2='us_tech' 现在已在映射表里 → 不应 warning。"""
    from sqlalchemy import text
    db = seeded_legacy_db
    db.execute(
        text(
            "INSERT INTO security_master_legacy "
            "(security_code, security_name, asset_type, type2, security_type) "
            "VALUES (:code, :name, :at, :t2, :st)"
        ),
        {"code": "513500.SH", "name": "标普500ETF",
         "at": "us_etf", "t2": "us_tech", "st": "fund"},
    )
    db.commit()
    from scripts.migrate_split_security_master import dry_run_report
    report = dry_run_report(db)
    assert not any("us_tech" in w for w in report["warnings"])


def test_type2_broad_index_no_warning(seeded_legacy_db):
    """type2='broad_index' 现在已在映射表里 → 不应 warning。"""
    from sqlalchemy import text
    db = seeded_legacy_db
    db.execute(
        text(
            "INSERT INTO security_master_legacy "
            "(security_code, security_name, asset_type, type2, security_type) "
            "VALUES (:code, :name, :at, :t2, :st)"
        ),
        {"code": "510300.SH2", "name": "沪深300ETF-test",
         "at": "a_share_etf", "t2": "broad_index", "st": "fund"},
    )
    db.commit()
    from scripts.migrate_split_security_master import dry_run_report
    report = dry_run_report(db)
    assert not any("broad_index" in w for w in report["warnings"])
