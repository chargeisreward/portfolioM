# Overseas yfinance Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 新建 OverseasShareFinancialSnapshot 表，将 yfinance 获取的海外市场财务数据结构化存储，并集成到穿透分析链路中，实现非中港市场 PE/PB/PS 自动补足。

**Architecture:** 新建模型 + 增强 yfinance + service 层 + scheduler 集成 + 穿透分析集成 + API 端点。数据流：yfinance API → fetch_yfinance_info（增强）→ overseas_financial_service（upsert）→ OverseasShareFinancialSnapshot 表 → resolve_dynamic_metrics_for_stock（穿透分析）。

**Tech Stack:** FastAPI, SQLAlchemy, yfinance, pytest, SQLite/PostgreSQL

**Spec:** `docs/superpowers/specs/2026-06-24-overseas-yfinance-design.md`

## File Structure

| 文件 | 职责 | 操作 |
|------|------|------|
| `backend/models.py` | 新增 OverseasShareFinancialSnapshot 模型 | 修改 |
| `backend/crawlers/price_data.py` | 增强 fetch_yfinance_info + 新增 _infer_market_from_ticker | 修改 |
| `backend/services/overseas_financial_service.py` | upsert + fetch_and_store | 新建 |
| `backend/services/aggregation.py` | resolve_dynamic_metrics_for_stock 增加海外查询 | 修改 |
| `backend/services/scheduler.py` | job_update_financial_fundamentals 增加海外写入 | 修改 |
| `backend/main.py` | 2 个 API 端点 | 修改 |
| `backend/tests/test_overseas_financial_service.py` | service 单元测试 | 新建 |
| `backend/tests/test_overseas_financial_api.py` | API 集成测试 | 新建 |
| `backend/tests/test_aggregation_overseas.py` | 穿透分析集成测试 | 新建 |

---

## Task 1: OverseasShareFinancialSnapshot 模型

### 步骤 1.1: 在 models.py 中添加模型

在 `backend/models.py` 的 `HKShareFinancialSnapshot` 类之后添加：

```python
class OverseasShareFinancialSnapshot(Base):
    """海外市场（非 A 股、非港股）估值快照。"""
    __tablename__ = "overseas_share_financial_snapshot"
    __table_args__ = (
        UniqueConstraint("as_of_date", "stock_code",
                         name="ux_osfs_asof_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)
    stock_name = Column(String(80))
    market = Column(String(8), nullable=False, index=True)
    pe_ttm = Column(Float)
    pb_mrq = Column(Float)
    ps_ttm = Column(Float)
    dividend_yield = Column(Float)
    market_cap = Column(Float)
    eps_fy1 = Column(Float)
    eps_fy2 = Column(Float)
    sector = Column(String(60))
    industry = Column(String(80))
    baseline_price = Column(Float)
    current_price = Column(Float)
    current_price_date = Column(Date)
    pe_ttm_dynamic = Column(Float)
    pb_mrq_dynamic = Column(Float)
    ps_ttm_dynamic = Column(Float)
    source = Column(String(40))
    created_at = Column(DateTime, default=datetime.utcnow)
```

### 步骤 1.2: 验证模型可创建表

```powershell
cd backend ; python -c "from models import OverseasShareFinancialSnapshot; print('OK')"
```

预期输出：`OK`

### 步骤 1.3: commit

```powershell
cd d:\claude_code_project\PortfolioM\.worktrees\auth-upgrade
git add backend/models.py
git commit -m "feat(model): OverseasShareFinancialSnapshot for overseas markets (Task 1)"
```

---

## Task 2: yfinance 增强

### 步骤 2.1: 写测试

创建 `backend/tests/test_yfinance_enhanced.py`：

```python
"""yfinance 增强：fetch_yfinance_info 含 PB/PS + _infer_market_from_ticker。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
from unittest.mock import patch, MagicMock


def test_infer_market_from_ticker_us():
    """无后缀默认 US。"""
    from crawlers.price_data import _infer_market_from_ticker
    assert _infer_market_from_ticker("AAPL") == "US"
    assert _infer_market_from_ticker("MSFT") == "US"


def test_infer_market_from_ticker_korea():
    """韩国市场后缀。"""
    from crawlers.price_data import _infer_market_from_ticker
    assert _infer_market_from_ticker("005930.KS") == "KR"
    assert _infer_market_from_ticker("035420.KQ") == "KR"


def test_infer_market_from_ticker_japan():
    """日本市场后缀。"""
    from crawlers.price_data import _infer_market_from_ticker
    assert _infer_market_from_ticker("7203.T") == "JP"


def test_infer_market_from_ticker_europe():
    """欧洲市场后缀。"""
    from crawlers.price_data import _infer_market_from_ticker
    assert _infer_market_from_ticker("SHEL.L") == "GB"
    assert _infer_market_from_ticker("SAP.DE") == "DE"
    assert _infer_market_from_ticker("MC.PA") == "FR"


def test_fetch_yfinance_info_has_pb_ps():
    """fetch_yfinance_info 返回 PB 和 PS 字段。"""
    from crawlers.price_data import fetch_yfinance_info

    mock_info = {
        "shortName": "Apple Inc",
        "trailingPE": 28.5,
        "priceToBook": 45.2,
        "priceToSalesTrailing12Months": 7.8,
        "marketCap": 3000000000000,
        "totalRevenue": 400000000000,
        "netIncomeToCommon": 100000000000,
        "earningsGrowth": 0.15,
        "revenueGrowth": 0.08,
        "dividendYield": 0.005,
        "forwardEPS": 6.5,
        "sector": "Technology",
        "industry": "Consumer Electronics",
    }

    with patch("crawlers.price_data.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info
        mock_yf.Ticker.return_value = mock_ticker

        result = fetch_yfinance_info("AAPL")

    assert result is not None
    assert result["pe_ttm"] == 28.5
    assert result["pb_mrq"] == 45.2
    assert result["ps_ttm"] == 7.8
    assert result["market"] == "US"
    assert result["sector"] == "Technology"
    assert result["eps_fy1"] == 6.5
    assert result["source"] == "yfinance"


def test_fetch_yfinance_info_none_values():
    """yfinance 返回 None 时不报错。"""
    from crawlers.price_data import fetch_yfinance_info

    mock_info = {
        "shortName": "Test ETF",
        "trailingPE": None,
        "priceToBook": None,
        "priceToSalesTrailing12Months": None,
        "marketCap": 0,
    }

    with patch("crawlers.price_data.yf") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info
        mock_yf.Ticker.return_value = mock_ticker

        result = fetch_yfinance_info("TEST")

    assert result is not None
    assert result["pe_ttm"] is None
    assert result["pb_mrq"] is None
    assert result["ps_ttm"] is None
```

### 步骤 2.2: 运行测试确认失败

```powershell
cd backend ; python -m pytest tests/test_yfinance_enhanced.py -v
```

预期：测试失败（`_infer_market_from_ticker` 不存在，`fetch_yfinance_info` 无 PB/PS 字段）。

### 步骤 2.3: 实现

在 `backend/crawlers/price_data.py` 中：

1. 增强 `fetch_yfinance_info` 函数（替换现有版本）：

```python
def fetch_yfinance_info(ticker: str) -> dict | None:
    """yfinance 财务信息补充（增强版：含 PB/PS + market 推断）"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "code": ticker,
            "name": info.get("shortName", ""),
            "market": _infer_market_from_ticker(ticker),
            "pe_ttm": info.get("trailingPE"),
            "pb_mrq": info.get("priceToBook"),
            "ps_ttm": info.get("priceToSalesTrailing12Months"),
            "market_cap_b": info.get("marketCap", 0) / 1e8,
            "revenue_b": info.get("totalRevenue", 0) / 1e8,
            "net_income_b": info.get("netIncomeToCommon", 0) / 1e8,
            "profit_growth": info.get("earningsGrowth"),
            "revenue_growth": info.get("revenueGrowth"),
            "dividend_yield": info.get("dividendYield"),
            "eps_fy1": info.get("forwardEPS"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "source": "yfinance",
        }
    except Exception:
        return None
```

2. 在 `fetch_yfinance_info` 之前添加 `_infer_market_from_ticker` 函数：

```python
def _infer_market_from_ticker(ticker: str) -> str:
    """根据 yfinance ticker 后缀推断市场代码。"""
    if "." not in ticker:
        return "US"
    suffix = ticker.rsplit(".", 1)[-1].upper()
    market_map = {
        "KS": "KR", "KQ": "KR",
        "T": "JP",
        "L": "GB",
        "DE": "DE",
        "PA": "FR",
        "AS": "NL",
        "MI": "IT",
        "SW": "CH",
        "AX": "AU",
        "TO": "CA",
    }
    return market_map.get(suffix, suffix)
```

### 步骤 2.4: 运行测试确认通过

```powershell
cd backend ; python -m pytest tests/test_yfinance_enhanced.py -v
```

预期：6 个测试全部 PASS。

### 步骤 2.5: commit

```powershell
git add backend/crawlers/price_data.py backend/tests/test_yfinance_enhanced.py
git commit -m "feat(crawler): enhance fetch_yfinance_info with PB/PS + market inference (Task 2)"
```

---

## Task 3: overseas_financial_service

### 步骤 3.1: 写测试

创建 `backend/tests/test_overseas_financial_service.py`：

```python
"""overseas_financial_service 单元测试。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models import OverseasShareFinancialSnapshot


@pytest.fixture
def fresh_db():
    """临时文件 SQLite。"""
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


def test_upsert_overseas_financial_create(fresh_db):
    """单条创建。"""
    from services.overseas_financial_service import upsert_overseas_financial

    result = upsert_overseas_financial(fresh_db, {
        "stock_code": "AAPL",
        "stock_name": "Apple Inc",
        "market": "US",
        "pe_ttm": 28.5,
        "pb_mrq": 45.2,
        "ps_ttm": 7.8,
        "as_of_date": "2026-06-24",
    })

    assert result["status"] == "ok"
    assert result["market"] == "US"

    snap = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "AAPL",
        OverseasShareFinancialSnapshot.as_of_date == date(2026, 6, 24),
    ).first()
    assert snap is not None
    assert snap.pe_ttm == 28.5
    assert snap.pb_mrq == 45.2
    assert snap.ps_ttm == 7.8


def test_upsert_overseas_financial_update(fresh_db):
    """单条更新。"""
    from services.overseas_financial_service import upsert_overseas_financial

    upsert_overseas_financial(fresh_db, {
        "stock_code": "AAPL",
        "pe_ttm": 28.0,
        "as_of_date": "2026-06-24",
    })

    upsert_overseas_financial(fresh_db, {
        "stock_code": "AAPL",
        "pe_ttm": 30.0,
        "as_of_date": "2026-06-24",
    })

    count = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "AAPL",
    ).count()
    assert count == 1

    snap = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "AAPL",
    ).first()
    assert snap.pe_ttm == 30.0


def test_upsert_overseas_financial_market_infer(fresh_db):
    """market 未提供时从 ticker 推断。"""
    from services.overseas_financial_service import upsert_overseas_financial

    result = upsert_overseas_financial(fresh_db, {
        "stock_code": "005930.KS",
        "pe_ttm": 15.0,
        "as_of_date": "2026-06-24",
    })

    assert result["market"] == "KR"

    snap = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "005930.KS",
    ).first()
    assert snap.market == "KR"


def test_fetch_and_store_overseas_financials(fresh_db):
    """批量获取（mock yfinance）。"""
    from services.overseas_financial_service import fetch_and_store_overseas_financials

    mock_yf_info = {
        "code": "AAPL",
        "name": "Apple Inc",
        "market": "US",
        "pe_ttm": 28.5,
        "pb_mrq": 45.2,
        "ps_ttm": 7.8,
        "market_cap_b": 30000.0,
        "dividend_yield": 0.005,
        "eps_fy1": 6.5,
        "sector": "Technology",
        "industry": "Consumer Electronics",
    }

    with patch("services.overseas_financial_service.fetch_yfinance_info", return_value=mock_yf_info):
        with patch("services.overseas_financial_service.time"):
            result = fetch_and_store_overseas_financials(
                fresh_db, ["AAPL"], date(2026, 6, 24)
            )

    assert result["status"] == "ok"
    assert result["fetched"] == 1
    assert result["stored"] == 1

    snap = fresh_db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == "AAPL",
    ).first()
    assert snap is not None
    assert snap.sector == "Technology"


def test_fetch_and_store_overseas_financials_empty(fresh_db):
    """yfinance 返回空时记录错误。"""
    from services.overseas_financial_service import fetch_and_store_overseas_financials

    with patch("services.overseas_financial_service.fetch_yfinance_info", return_value=None):
        with patch("services.overseas_financial_service.time"):
            result = fetch_and_store_overseas_financials(
                fresh_db, ["BADCODE"], date(2026, 6, 24)
            )

    assert result["fetched"] == 0
    assert result["stored"] == 0
    assert len(result["errors"]) == 1
```

### 步骤 3.2: 运行测试确认失败

```powershell
cd backend ; python -m pytest tests/test_overseas_financial_service.py -v
```

预期：5 个测试全部 FAIL（模块不存在）。

### 步骤 3.3: 实现

创建 `backend/services/overseas_financial_service.py`：

```python
"""海外市场财务数据 service — yfinance 获取 + upsert。"""
from __future__ import annotations

import logging
import time
from datetime import date

from sqlalchemy.orm import Session

from models import OverseasShareFinancialSnapshot

logger = logging.getLogger(__name__)


def upsert_overseas_financial(db: Session, data: dict) -> dict:
    """单条写入海外财务数据（upsert）。

    Args:
        db: 数据库会话
        data: {stock_code, stock_name, market, pe_ttm, pb_mrq, ps_ttm,
               dividend_yield, market_cap, eps_fy1, sector, industry, as_of_date}

    Returns: {status, market}
    """
    stock_code = data.get("stock_code", "")
    if not stock_code:
        raise ValueError("stock_code 不能为空")

    market = data.get("market")
    if not market:
        from crawlers.price_data import _infer_market_from_ticker
        market = _infer_market_from_ticker(stock_code)
    as_of = data.get("as_of_date")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)

    existing = db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == stock_code,
        OverseasShareFinancialSnapshot.as_of_date == as_of,
    ).first()

    fields = (
        "stock_name", "market", "pe_ttm", "pb_mrq", "ps_ttm",
        "dividend_yield", "market_cap", "eps_fy1",
        "sector", "industry",
    )

    if existing:
        for f in fields:
            if f in data:
                setattr(existing, f, data[f])
    else:
        kwargs = {"stock_code": stock_code, "as_of_date": as_of, "user_id": 1, "market": market}
        for f in fields:
            if f in data:
                kwargs[f] = data[f]
        snap = OverseasShareFinancialSnapshot(**kwargs)
        db.add(snap)

    db.commit()
    return {"status": "ok", "market": market}


def fetch_and_store_overseas_financials(db: Session, stock_codes: list[str], as_of_date: date) -> dict:
    """批量从 yfinance 获取海外财务数据并存储。

    Args:
        db: 数据库会话
        stock_codes: yfinance ticker 列表
        as_of_date: 截止日期

    Returns: {status, fetched, stored, errors}
    """
    from crawlers.price_data import fetch_yfinance_info

    fetched = 0
    stored = 0
    errors = []

    for code in stock_codes:
        try:
            yf_info = fetch_yfinance_info(code)
            if not yf_info:
                errors.append(f"{code}: yfinance 返回空")
                continue

            fetched += 1

            data = {
                "stock_code": code,
                "stock_name": yf_info.get("name", ""),
                "market": yf_info.get("market", "US"),
                "pe_ttm": yf_info.get("pe_ttm"),
                "pb_mrq": yf_info.get("pb_mrq"),
                "ps_ttm": yf_info.get("ps_ttm"),
                "dividend_yield": yf_info.get("dividend_yield"),
                "market_cap": yf_info.get("market_cap_b"),
                "eps_fy1": yf_info.get("eps_fy1"),
                "sector": yf_info.get("sector"),
                "industry": yf_info.get("industry"),
                "as_of_date": as_of_date,
            }

            upsert_overseas_financial(db, data)
            stored += 1

            time.sleep(3)

        except Exception as e:
            errors.append(f"{code}: {str(e)}")
            logger.warning("获取海外财务数据失败 [%s]: %s", code, e)
            continue

    return {"status": "ok", "fetched": fetched, "stored": stored, "errors": errors}
```

### 步骤 3.4: 运行测试确认通过

```powershell
cd backend ; python -m pytest tests/test_overseas_financial_service.py -v
```

预期：5 个测试全部 PASS。

### 步骤 3.5: commit

```powershell
git add backend/services/overseas_financial_service.py backend/tests/test_overseas_financial_service.py
git commit -m "feat(service): overseas_financial_service with upsert + fetch_and_store (Task 3)"
```

---

## Task 4: 穿透分析集成

### 步骤 4.1: 写测试

创建 `backend/tests/test_aggregation_overseas.py`：

```python
"""穿透分析集成测试：resolve_dynamic_metrics_for_stock 支持海外市场。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
from database import Base
from models import OverseasShareFinancialSnapshot


@pytest.fixture
def fresh_db():
    """临时文件 SQLite。"""
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


def test_resolve_dynamic_metrics_overseas(fresh_db):
    """海外股票 PE/PB/PS 解析。"""
    from services.aggregation import resolve_dynamic_metrics_for_stock

    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        market="US",
        pe_ttm=28.5,
        pb_mrq=45.2,
        ps_ttm=7.8,
        pe_ttm_dynamic=29.0,
        pb_mrq_dynamic=45.5,
        ps_ttm_dynamic=7.9,
    ))
    fresh_db.commit()

    pe, pb, ps = resolve_dynamic_metrics_for_stock(fresh_db, "AAPL")
    assert pe == 29.0
    assert pb == 45.5
    assert ps == 7.9


def test_resolve_dynamic_metrics_overseas_not_found(fresh_db):
    """海外股票无数据时返回 None。"""
    from services.aggregation import resolve_dynamic_metrics_for_stock

    pe, pb, ps = resolve_dynamic_metrics_for_stock(fresh_db, "NONEXIST")
    assert pe is None
    assert pb is None
    assert ps is None


def test_resolve_dynamic_metrics_overseas_latest(fresh_db):
    """取最新快照。"""
    from services.aggregation import resolve_dynamic_metrics_for_stock

    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 20),
        stock_code="AAPL",
        market="US",
        pe_ttm_dynamic=28.0,
        pb_mrq_dynamic=44.0,
        ps_ttm_dynamic=7.0,
    ))
    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        market="US",
        pe_ttm_dynamic=29.0,
        pb_mrq_dynamic=45.0,
        ps_ttm_dynamic=7.5,
    ))
    fresh_db.commit()

    pe, pb, ps = resolve_dynamic_metrics_for_stock(fresh_db, "AAPL")
    assert pe == 29.0
    assert pb == 45.0
    assert ps == 7.5
```

### 步骤 4.2: 运行测试确认失败

```powershell
cd backend ; python -m pytest tests/test_aggregation_overseas.py -v
```

预期：3 个测试 FAIL（resolve_dynamic_metrics_for_stock 不查 OverseasShareFinancialSnapshot）。

### 步骤 4.3: 实现

在 `backend/services/aggregation.py` 中：

1. 在 import 部分添加 `OverseasShareFinancialSnapshot`：

```python
from models import (
    AggregationCache,
    AggregationTimeseries,
    AShareFinancialSnapshot,
    Csi300ConstituentSnapshot,
    FullHoldingSnapshot,
    HKShareFinancialSnapshot,
    OverseasShareFinancialSnapshot,
)
```

2. 在 `resolve_dynamic_metrics_for_stock` 函数末尾（`return None, None, None` 之前）添加海外查询：

```python
    # 3. 查海外市场
    o_snap = db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == stock_code,
    ).order_by(OverseasShareFinancialSnapshot.as_of_date.desc()).first()
    if o_snap:
        return o_snap.pe_ttm_dynamic, o_snap.pb_mrq_dynamic, o_snap.ps_ttm_dynamic
    return None, None, None
```

### 步骤 4.4: 运行测试确认通过

```powershell
cd backend ; python -m pytest tests/test_aggregation_overseas.py -v
```

预期：3 个测试全部 PASS。

### 步骤 4.5: commit

```powershell
git add backend/services/aggregation.py backend/tests/test_aggregation_overseas.py
git commit -m "feat(aggregation): resolve_dynamic_metrics supports overseas markets (Task 4)"
```

---

## Task 5: Scheduler 集成

### 步骤 5.1: 修改 scheduler

在 `backend/services/scheduler.py` 的 `job_update_financial_fundamentals` 函数中，在现有 US 持仓写入 StockInfoCache 逻辑之后、穿透计算之前，添加海外写入逻辑。

找到 `job_update_financial_fundamentals` 函数中的 `# 基本面更新后运行穿透计算` 注释，在其之前添加：

```python
        # === 新增：海外持仓写入 OverseasShareFinancialSnapshot ===
        from services.overseas_financial_service import fetch_and_store_overseas_financials
        overseas_holdings = db.query(Holding).filter(
            Holding.asset_type.in_([
                AssetType.US_STOCK.value,
                AssetType.US_ETF.value,
            ])
        ).all()
        overseas_codes = list(set(h.security_code for h in overseas_holdings))

        if overseas_codes:
            result = fetch_and_store_overseas_financials(db, overseas_codes, today)
            logger.info("海外财务数据更新：fetched=%d, stored=%d, errors=%d",
                       result["fetched"], result["stored"], len(result["errors"]))
```

### 步骤 5.2: 验证 scheduler 可加载

```powershell
cd backend ; python -c "from services.scheduler import job_update_financial_fundamentals; print('OK')"
```

预期输出：`OK`

### 步骤 5.3: commit

```powershell
git add backend/services/scheduler.py
git commit -m "feat(scheduler): integrate overseas financials into job_update_financial_fundamentals (Task 5)"
```

---

## Task 6: API 端点

### 步骤 6.1: 写测试

创建 `backend/tests/test_overseas_financial_api.py`：

```python
"""海外财务数据 API 集成测试。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
import tempfile
from datetime import date
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models  # noqa: F401
import database as _database
import main as _main
from database import Base
from main import app
from models import OverseasShareFinancialSnapshot, Holding


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
    """TestClient，带 x-admin-token 头。"""
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    return TestClient(app, headers={"x-admin-token": admin_token})


def test_list_overseas_financials_empty(client, fresh_db):
    """空表查询。"""
    res = client.get("/api/admin/overseas-financials")
    assert res.status_code == 200
    assert res.json()["items"] == []
    assert res.json()["total"] == 0


def test_list_overseas_financials_with_data(client, fresh_db):
    """有数据时查询。"""
    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1,
        as_of_date=date(2026, 6, 24),
        stock_code="AAPL",
        stock_name="Apple Inc",
        market="US",
        pe_ttm=28.5,
        pb_mrq=45.2,
        ps_ttm=7.8,
    ))
    fresh_db.commit()

    res = client.get("/api/admin/overseas-financials")
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["stock_code"] == "AAPL"
    assert res.json()["items"][0]["market"] == "US"


def test_list_overseas_financials_filter_market(client, fresh_db):
    """按 market 过滤。"""
    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1, as_of_date=date(2026, 6, 24),
        stock_code="AAPL", market="US", pe_ttm=28.5,
    ))
    fresh_db.add(OverseasShareFinancialSnapshot(
        user_id=1, as_of_date=date(2026, 6, 24),
        stock_code="005930.KS", market="KR", pe_ttm=15.0,
    ))
    fresh_db.commit()

    res = client.get("/api/admin/overseas-financials?market=KR")
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["stock_code"] == "005930.KS"


def test_refresh_overseas_financials(client, fresh_db):
    """手动触发更新（mock yfinance）。"""
    # 添加一个 US 持仓
    fresh_db.add(Holding(
        user_id=1, security_code="AAPL", quantity=100,
        asset_type="us_stock",
    ))
    fresh_db.commit()

    mock_yf_info = {
        "code": "AAPL",
        "name": "Apple Inc",
        "market": "US",
        "pe_ttm": 28.5,
        "pb_mrq": 45.2,
        "ps_ttm": 7.8,
        "market_cap_b": 30000.0,
        "dividend_yield": 0.005,
        "eps_fy1": 6.5,
        "sector": "Technology",
        "industry": "Consumer Electronics",
    }

    with patch("services.overseas_financial_service.fetch_yfinance_info", return_value=mock_yf_info):
        with patch("services.overseas_financial_service.time"):
            res = client.post("/api/admin/overseas-financials/refresh")

    assert res.status_code == 200, res.text
    assert res.json()["fetched"] == 1
    assert res.json()["stored"] == 1


def test_refresh_overseas_financials_no_holdings(client, fresh_db):
    """无海外持仓时返回提示。"""
    res = client.post("/api/admin/overseas-financials/refresh")
    assert res.status_code == 200
    assert res.json()["fetched"] == 0
```

### 步骤 6.2: 运行测试确认失败

```powershell
cd backend ; python -m pytest tests/test_overseas_financial_api.py -v
```

预期：5 个测试 FAIL（端点不存在）。

### 步骤 6.3: 实现端点

在 `backend/main.py` 中（在子项目 2 的财务数据上传端点之后）添加：

```python
from models import OverseasShareFinancialSnapshot


@app.get("/api/admin/overseas-financials")
def admin_list_overseas_financials(
    market: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """查看海外财务数据快照。"""
    query = db.query(OverseasShareFinancialSnapshot)
    if market:
        query = query.filter(OverseasShareFinancialSnapshot.market == market)
    total = query.count()
    items = query.order_by(OverseasShareFinancialSnapshot.as_of_date.desc()) \
        .offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [{
            "stock_code": s.stock_code,
            "stock_name": s.stock_name,
            "market": s.market,
            "as_of_date": str(s.as_of_date),
            "pe_ttm": s.pe_ttm,
            "pb_mrq": s.pb_mrq,
            "ps_ttm": s.ps_ttm,
            "dividend_yield": s.dividend_yield,
            "market_cap": s.market_cap,
            "sector": s.sector,
            "industry": s.industry,
            "source": s.source,
        } for s in items],
        "total": total,
    }


@app.post("/api/admin/overseas-financials/refresh")
def admin_refresh_overseas_financials(db: Session = Depends(get_db)):
    """手动触发海外财务数据更新。"""
    from services.overseas_financial_service import fetch_and_store_overseas_financials
    overseas_holdings = db.query(Holding).filter(
        Holding.asset_type.in_([
            AssetType.US_STOCK.value,
            AssetType.US_ETF.value,
        ])
    ).all()
    overseas_codes = list(set(h.security_code for h in overseas_holdings))
    if not overseas_codes:
        return {"status": "ok", "fetched": 0, "stored": 0, "errors": ["无海外持仓"]}
    result = fetch_and_store_overseas_financials(db, overseas_codes, date.today())
    return result
```

**注意**：
- `OverseasShareFinancialSnapshot` 需要在 main.py 顶部 import（从 models import）
- `Holding` 和 `AssetType` 应该已在 main.py 中 import
- `Query`, `Depends`, `Session`, `date` 应该已在 main.py 顶部 import

### 步骤 6.4: 运行测试确认通过

```powershell
cd backend ; python -m pytest tests/test_overseas_financial_api.py -v
```

预期：5 个测试全部 PASS。

### 步骤 6.5: commit

```powershell
git add backend/main.py backend/tests/test_overseas_financial_api.py
git commit -m "feat(api): overseas-financials list + refresh endpoints (Task 6)"
```

---

## Task 7: 集成测试 + 最终验证

### 步骤 7.1: 运行全部子项目 3 测试

```powershell
cd backend ; python -m pytest tests/test_yfinance_enhanced.py tests/test_overseas_financial_service.py tests/test_aggregation_overseas.py tests/test_overseas_financial_api.py -v
```

预期：所有测试 PASS（6 + 5 + 3 + 5 = 19 个）。

### 步骤 7.2: 运行全部后端测试（确认无回归）

```powershell
cd backend ; python -m pytest tests/test_upload_service.py tests/test_llm_service.py tests/test_pdf_parser_service.py tests/test_financial_upload_service.py tests/test_upload_api.py tests/test_yfinance_enhanced.py tests/test_overseas_financial_service.py tests/test_aggregation_overseas.py tests/test_overseas_financial_api.py -v
```

预期：所有测试 PASS（26 + 19 = 45 个）。

### 步骤 7.3: 更新 Project_development.md

在 `Project_development.md` 的子项目 2 章节之后添加子项目 3 章节：

```markdown
### 2026-06-24 子项目 3：yfinance 集成 — 非中港市场 PE/PB/PS 自动补足

**影响范围**：中（7 个 Task，后端 1 模型 + 1 service + 2 API 端点 + scheduler 集成 + 穿透分析集成）

**完成内容**：

| Task | 内容 | Commit |
|------|------|--------|
| Task 1 | OverseasShareFinancialSnapshot 模型 | - |
| Task 2 | yfinance 增强（PB/PS + market 推断） | - |
| Task 3 | overseas_financial_service | - |
| Task 4 | resolve_dynamic_metrics_for_stock 集成海外查询 | - |
| Task 5 | scheduler 集成 | - |
| Task 6 | API 端点（列表 + 手动触发） | - |
| Task 7 | 集成测试 + 最终验证 | - |

**测试结果**：19 个新测试全部通过

**关键设计决策**：
1. 新建 OverseasShareFinancialSnapshot 通用表，market 字段区分 US/KR/JP/EU 等
2. yfinance 增强：补全 PB（priceToBook）和 PS（priceToSalesTrailing12Months）
3. 穿透分析查询顺序：HK → CN → Overseas
4. 复用现有 job_update_financial_fundamentals，不新建独立 job
5. 无新增依赖（yfinance 已安装）
```

### 步骤 7.4: commit

```powershell
git add Project_development.md
git commit -m "docs: update Project_development.md for subproject 3"
```

---

## 自审清单

### Spec 覆盖
- [x] OverseasShareFinancialSnapshot 模型 → Task 1
- [x] yfinance 增强（PB/PS + market 推断）→ Task 2
- [x] overseas_financial_service → Task 3
- [x] resolve_dynamic_metrics_for_stock 集成 → Task 4
- [x] scheduler 集成 → Task 5
- [x] API 端点 → Task 6
- [x] 测试策略 → 每个 Task 都有测试

### Placeholder scan
- [x] 无 TBD/TODO
- [x] 每个步骤都有完整代码
- [x] 每个测试都有实际断言

### Type consistency
- [x] OverseasShareFinancialSnapshot 在 Task 1 定义，Task 3/4/6 使用
- [x] fetch_yfinance_info 在 Task 2 增强，Task 3 使用
- [x] _infer_market_from_ticker 在 Task 2 定义，Task 3 import 使用
- [x] API 路径前缀一致（/api/admin/overseas-financials）
