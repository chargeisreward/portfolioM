"""akshare_index_poller 测试 — 通过 _fetch_fn 参数注入 mock fetcher。

注: 直接 patch("akshare.stock_zh_index_spot_em") 会触发 akshare 模块级 import,
而 akshare 在 Python 3.14 上有 py_mini_racer 循环导入问题,导致 patch 即失败。
所以采用 fetcher 注入模式。
"""
import pytest
import pandas as pd


def _make_fake_fetch(df):
    """生成返回固定 dataframe 的 fetcher。"""
    return lambda: df


@pytest.fixture
def fake_fetch_3_indices():
    return _make_fake_fetch(pd.DataFrame({
        "代码": ["000300", "000905", "399006"],
        "名称": ["沪深300", "中证500", "创业板指"],
    }))


@pytest.fixture(autouse=True)
def ensure_tables(in_memory_db):
    """data_pull_task + index_master 表 in-memory SQLite 没有,测试时 autouse 创建。"""
    from sqlalchemy import text
    in_memory_db.execute(text("""
        CREATE TABLE IF NOT EXISTS data_pull_task (
            id INTEGER PRIMARY KEY,
            job_id VARCHAR(60), job_name VARCHAR(100),
            started_at TIMESTAMP, finished_at TIMESTAMP,
            status VARCHAR(20), records_pulled INTEGER,
            error_message TEXT, triggered_by VARCHAR(40),
            created_at TIMESTAMP,
            planned_count INTEGER, success_count INTEGER, coverage_rate FLOAT
        )
    """))
    in_memory_db.execute(text("""
        CREATE TABLE IF NOT EXISTS index_master (
            index_code VARCHAR(20) PRIMARY KEY,
            index_name VARCHAR(100) NOT NULL,
            exchange VARCHAR(20), currency VARCHAR(10),
            category VARCHAR(50), constituent_count INTEGER,
            source VARCHAR(40), is_active BOOLEAN DEFAULT 1,
            first_pulled_at TIMESTAMP, last_pulled_at TIMESTAMP,
            last_verified_at TIMESTAMP,
            created_at TIMESTAMP, updated_at TIMESTAMP,
            updated_by INTEGER
        )
    """))
    in_memory_db.commit()
    return in_memory_db


def test_poll_inserts_new_indices(in_memory_db, fake_fetch_3_indices):
    from services.akshare_index_poller import poll_index_master
    result = poll_index_master(in_memory_db, _fetch_fn=fake_fetch_3_indices)
    assert result["status"] == "success"
    assert result["inserted"] == 3
    assert result["skipped"] == 0


def test_poll_updates_existing(in_memory_db, fake_fetch_3_indices):
    from datetime import datetime
    from models_master import IndexMaster
    from services.akshare_index_poller import poll_index_master

    db = in_memory_db
    db.add(IndexMaster(
        index_code="000300", index_name="旧名",
        exchange="SH", currency="CNY", source="akshare",
        first_pulled_at=datetime.utcnow(),
    ))
    db.commit()

    result = poll_index_master(db, _fetch_fn=fake_fetch_3_indices)
    assert result["updated"] == 1
    refreshed = db.query(IndexMaster).filter_by(index_code="000300").first()
    assert refreshed.index_name == "沪深300"


def test_poll_marks_inactive_disappeared(in_memory_db, fake_fetch_3_indices):
    """上次见到的 code 本次没出现 → is_active=False。"""
    from datetime import datetime
    from models_master import IndexMaster
    from services.akshare_index_poller import poll_index_master

    db = in_memory_db
    db.add(IndexMaster(
        index_code="999999", index_name="已下架指数",
        is_active=True, source="akshare",
        first_pulled_at=datetime.utcnow(),
    ))
    db.commit()

    poll_index_master(db, _fetch_fn=fake_fetch_3_indices)
    refreshed = db.query(IndexMaster).filter_by(index_code="999999").first()
    assert refreshed.is_active is False


def test_poll_records_failure(in_memory_db):
    """fetcher 抛错时,应写 DataPullTask(status='FAILED')。"""
    from sqlalchemy import text
    from services.akshare_index_poller import poll_index_master

    in_memory_db.execute(text("""
        CREATE TABLE IF NOT EXISTS data_pull_task (
            id INTEGER PRIMARY KEY,
            job_id VARCHAR(60), job_name VARCHAR(100),
            started_at TIMESTAMP, finished_at TIMESTAMP,
            status VARCHAR(20), records_pulled INTEGER,
            error_message TEXT, triggered_by VARCHAR(40),
            created_at TIMESTAMP
        )
    """))
    in_memory_db.commit()

    def failing_fetch():
        raise Exception("akshare 临时失败")

    result = poll_index_master(in_memory_db, _fetch_fn=failing_fetch)
    assert result["status"] == "failed"
    assert "akshare 临时失败" in result["error"]