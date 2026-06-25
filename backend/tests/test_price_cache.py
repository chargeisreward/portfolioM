"""测试公共价格缓冲层逻辑

用 mock 隔离真实 API 调用，专注测试缓冲层逻辑：
- TTL 命中/过期
- 价格不变续期
- API 失败返回过期缓存
- 手动失效
- 统计函数
"""
import os
os.environ.setdefault("APP_PASSWORD", "")
from datetime import datetime, timedelta
from unittest.mock import patch
import pytest
from database import SessionLocal, engine
from models import RealtimePriceCache
from services import price_cache
from services.price_cache import (
    get_realtime_price,
    invalidate_cache_for,
    get_cache_stats,
    TTL_MINUTES,
    FAIL_RETRY_MINUTES,
    PRICE_TOLERANCE,
)


@pytest.fixture
def db():
    """每个测试用独立 session，测试后清理缓存表"""
    session = SessionLocal()
    # 清空缓存表
    session.query(RealtimePriceCache).delete()
    session.commit()
    yield session
    session.query(RealtimePriceCache).delete()
    session.commit()
    session.close()


def _mock_api_return(price, source="tencent"):
    """构造 mock _fetch_price_from_api 返回值"""
    return price, source


# ============ 1. 基本命中/刷新 ============

def test_first_call_refreshes(db):
    """首次调用：缓存不存在 → 调 API → status=refreshed"""
    with patch.object(price_cache, "_fetch_price_from_api", return_value=(199.0, "tencent")):
        price, source, status = get_realtime_price(db, "NVDA", "us_stock", "USD")
    assert price == 199.0
    assert source == "tencent"
    assert status == "refreshed"
    # 验证缓存写入
    cache = db.query(RealtimePriceCache).filter_by(code="NVDA").first()
    assert cache is not None
    assert cache.price == 199.0
    assert cache.source == "tencent"


def test_second_call_hits_cache(db):
    """第二次调用：缓存未过期 → status=hit，不调 API"""
    with patch.object(price_cache, "_fetch_price_from_api", return_value=(199.0, "tencent")) as mock_api:
        # 第一次：refreshed
        get_realtime_price(db, "NVDA", "us_stock", "USD")
        assert mock_api.call_count == 1
        # 第二次：hit（不应调 API）
        price, source, status = get_realtime_price(db, "NVDA", "us_stock", "USD")
        assert mock_api.call_count == 1  # 仍然只调了 1 次
    assert price == 199.0
    assert status == "hit"


def test_expired_cache_triggers_refresh(db):
    """缓存过期 → 重新调 API"""
    # 先写入一条已过期的缓存
    now = datetime.utcnow()
    cache = RealtimePriceCache(
        code="NVDA", price=150.0, prev_price=None, source="tencent",
        last_updated=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=5),  # 已过期
    )
    db.add(cache)
    db.commit()

    with patch.object(price_cache, "_fetch_price_from_api", return_value=(199.0, "tencent")):
        price, source, status = get_realtime_price(db, "NVDA", "us_stock", "USD")
    assert price == 199.0
    assert status == "refreshed"
    # 验证 prev_price 保存了旧值
    cache = db.query(RealtimePriceCache).filter_by(code="NVDA").first()
    assert cache.prev_price == 150.0
    assert cache.price == 199.0


# ============ 2. 价格不变续期 ============

def test_price_unchanged_renews_ttl(db):
    """价格不变（容差内）→ 正常续期 15min，prev_price 更新"""
    # 先写入缓存 price=100.0
    now = datetime.utcnow()
    cache = RealtimePriceCache(
        code="TEST", price=100.0, prev_price=99.0, source="tencent",
        last_updated=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=5),  # 过期
    )
    db.add(cache)
    db.commit()

    # API 返回相同价格（差值 < 0.0001）
    with patch.object(price_cache, "_fetch_price_from_api", return_value=(100.00005, "tencent")):
        price, source, status = get_realtime_price(db, "TEST", "us_stock", "USD")
    assert status == "refreshed"
    assert abs(price - 100.0) < 0.01
    # 验证续期：expires_at 应在 now+14min ~ now+16min 之间
    cache = db.query(RealtimePriceCache).filter_by(code="TEST").first()
    new_now = datetime.utcnow()
    assert new_now + timedelta(minutes=14) < cache.expires_at < new_now + timedelta(minutes=16)


def test_price_changed_updates_price(db):
    """价格变化（超出容差）→ 更新 price + prev_price"""
    now = datetime.utcnow()
    cache = RealtimePriceCache(
        code="TEST", price=100.0, prev_price=99.0, source="tencent",
        last_updated=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=5),
    )
    db.add(cache)
    db.commit()

    # API 返回不同价格（差值 > 0.0001）
    with patch.object(price_cache, "_fetch_price_from_api", return_value=(105.0, "tencent")):
        price, source, status = get_realtime_price(db, "TEST", "us_stock", "USD")
    assert price == 105.0
    assert status == "refreshed"
    cache = db.query(RealtimePriceCache).filter_by(code="TEST").first()
    assert cache.price == 105.0
    assert cache.prev_price == 100.0  # 旧 price 变成 prev_price


# ============ 3. API 失败 ============

def test_api_failure_returns_stale(db):
    """API 失败 + 有过期缓存 → 返回过期缓存 + 推迟 5min"""
    now = datetime.utcnow()
    cache = RealtimePriceCache(
        code="STALE", price=50.0, prev_price=49.0, source="tencent",
        last_updated=now - timedelta(minutes=30),
        expires_at=now - timedelta(minutes=15),  # 已过期
    )
    db.add(cache)
    db.commit()

    # API 返回 None（失败）
    with patch.object(price_cache, "_fetch_price_from_api", return_value=(None, None)):
        price, source, status = get_realtime_price(db, "STALE", "us_stock", "USD")
    assert price == 50.0  # 返回过期缓存值
    assert status == "stale"
    # 验证推迟 5min
    cache = db.query(RealtimePriceCache).filter_by(code="STALE").first()
    new_now = datetime.utcnow()
    assert new_now + timedelta(minutes=4) < cache.expires_at < new_now + timedelta(minutes=6)


def test_api_failure_no_cache_returns_miss(db):
    """API 失败 + 无缓存 → 返回 None, status=miss"""
    with patch.object(price_cache, "_fetch_price_from_api", return_value=(None, None)):
        price, source, status = get_realtime_price(db, "NOCACHE", "us_stock", "USD")
    assert price is None
    assert status == "miss"


# ============ 4. 手动失效 ============

def test_invalidate_cache(db):
    """手动失效 → 下次调用必须调 API"""
    with patch.object(price_cache, "_fetch_price_from_api", return_value=(100.0, "tencent")):
        get_realtime_price(db, "INVAL", "us_stock", "USD")
        # 失效
        invalidate_cache_for(db, "INVAL")
        # 下次应重新调 API
        with patch.object(price_cache, "_fetch_price_from_api", return_value=(105.0, "tencent")) as mock_api:
            price, source, status = get_realtime_price(db, "INVAL", "us_stock", "USD")
            assert mock_api.call_count == 1
    assert price == 105.0
    assert status == "refreshed"


# ============ 5. 统计 ============

def test_cache_stats(db):
    """统计函数返回正确的 total/fresh/stale"""
    now = datetime.utcnow()
    # 2 条 fresh + 1 条 stale
    db.add(RealtimePriceCache(
        code="F1", price=1.0, source="t", last_updated=now,
        expires_at=now + timedelta(minutes=10),
    ))
    db.add(RealtimePriceCache(
        code="F2", price=2.0, source="t", last_updated=now,
        expires_at=now + timedelta(minutes=5),
    ))
    db.add(RealtimePriceCache(
        code="S1", price=3.0, source="t", last_updated=now - timedelta(minutes=30),
        expires_at=now - timedelta(minutes=10),
    ))
    db.commit()

    stats = get_cache_stats(db)
    assert stats["total"] == 3
    assert stats["fresh"] == 2
    assert stats["stale"] == 1


# ============ 6. 配置常量 ============

def test_config_constants():
    """验证配置常量值"""
    assert TTL_MINUTES == 15
    assert FAIL_RETRY_MINUTES == 5
    assert PRICE_TOLERANCE == 0.0001
