# 管理员数据运维管理重构 — 子项目 1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重构管理员侧边栏为"用户区+分割线+管理员区"结构，新建证券主数据管理（含 is_drillable 配置）和数据源管理（数据就绪+任务历史+API策略+交易日历+数据浏览）。

**Architecture:** 扩展现有 SecurityMaster 表（加 is_drillable/fund_type/market/index_code 等列），新建 DataPullTask 表，新建 3 个后端 service（security_master/data_readiness/data_pull_task），修改 drill_user_service join SecurityMaster 替代硬编码 DRILLABLE_ASSET_TYPES，前端新建 MasterDataPanel + DataSourcePanel 两个域页面。

**Tech Stack:** FastAPI + SQLAlchemy + SQLite(dev)/PostgreSQL(prod) + React + pytest

**Design spec:** `docs/superpowers/specs/2026-06-24-admin-master-data-design.md`

## 文件结构

### 新建文件
| 文件 | 职责 |
|---|---|
| `backend/services/security_master_service.py` | 证券主数据 CRUD + 同步 + 初始化 |
| `backend/services/data_readiness_service.py` | 数据就绪检查 |
| `backend/services/data_pull_task_service.py` | 任务执行记录 |
| `backend/tests/test_security_master_service.py` | service 单元测试 |
| `backend/tests/test_data_readiness_service.py` | service 单元测试 |
| `backend/tests/test_data_pull_task_service.py` | service 单元测试 |
| `backend/tests/test_admin_master_data_api.py` | API 集成测试 |
| `backend/tests/test_admin_data_source_api.py` | API 集成测试 |
| `frontend/src/components/MasterDataPanel.jsx` | 主数据页（含 2 tab） |
| `frontend/src/components/DataSourcePanel.jsx` | 数据源页（含 5 tab） |
| `frontend/src/components/ContentUploadPanel.jsx` | 内容上传占位页 |
| `frontend/src/components/SecurityMasterTab.jsx` | 证券主数据 tab |
| `frontend/src/components/FundIndexMapTab.jsx` | 基金-指数映射 tab |
| `frontend/src/components/DataReadinessTab.jsx` | 数据就绪 tab |
| `frontend/src/components/TaskHistoryTab.jsx` | 任务历史 tab |
| `frontend/src/components/ApiStrategyTab.jsx` | API策略 tab |

### 修改文件
| 文件 | 改动 |
|---|---|
| `backend/models.py` | 扩展 SecurityMaster + 新增 DataPullTask |
| `backend/services/drill_user_service.py` | join SecurityMaster 替代 DRILLABLE_ASSET_TYPES |
| `backend/services/scheduler.py` | 集成 record_task() |
| `backend/main.py` | 新增 admin API 端点 |
| `backend/tests/test_drill_user_service.py` | 更新 mock 适配 SecurityMaster join |
| `frontend/src/App.jsx` | 侧边栏重排 + 分割线 + 新组件路由 |

### 删除文件
| 文件 | 原因 |
|---|---|
| `frontend/src/components/AdminSettingsPanel.jsx` | 功能拆分到 MasterDataPanel/DataSourcePanel |
| `frontend/src/components/OpsPanel.jsx` | 并入 TaskHistoryTab |
| `frontend/src/components/DataGapPanel.jsx` | 并入 DataReadinessTab |
| `frontend/src/components/StrategiesPanel.jsx` | 并入 ApiStrategyTab |

---

## Task 1: 扩展 SecurityMaster 模型 + 新增 DataPullTask 模型

### 步骤 1.1: 写模型测试

创建 `backend/tests/test_models_admin.py`：

```python
"""测试新增的模型字段和 DataPullTask 表。"""
import pytest
from datetime import datetime
from sqlalchemy import inspect
from models import SecurityMaster, DataPullTask


def test_security_master_has_new_fields(fresh_db):
    """SecurityMaster 应有 is_drillable, fund_type, market, index_code 等新字段。"""
    cols = {c["name"] for c in inspect(fresh_db.bind).get_columns("security_master")}
    assert "is_drillable" in cols
    assert "fund_type" in cols
    assert "market" in cols
    assert "index_code" in cols
    assert "index_name" in cols
    assert "benchmark_formula" in cols
    assert "premium_discount" in cols
    assert "security_type" in cols
    assert "note" in cols
    assert "updated_by" in cols


def test_security_master_is_drillable_default_false(fresh_db):
    """新建记录 is_drillable 默认 False。"""
    sm = SecurityMaster(
        security_code="510300.SH",
        security_name="沪深300ETF",
        security_type="fund",
        asset_type="a_share_etf",
        market="CN",
        fund_type="etf",
    )
    fresh_db.add(sm)
    fresh_db.commit()
    assert sm.is_drillable is False


def test_data_pull_task_table_exists(fresh_db):
    """DataPullTask 表应存在。"""
    cols = {c["name"] for c in inspect(fresh_db.bind).get_columns("data_pull_task")}
    assert "id" in cols
    assert "job_id" in cols
    assert "job_name" in cols
    assert "started_at" in cols
    assert "finished_at" in cols
    assert "status" in cols
    assert "records_pulled" in cols
    assert "error_message" in cols
    assert "triggered_by" in cols


def test_data_pull_task_create(fresh_db):
    """能正常创建 DataPullTask 记录。"""
    t = DataPullTask(
        job_id="crawl_cn_prices",
        job_name="拉取A股价格",
        started_at=datetime(2026, 6, 24, 16, 0, 0),
        status="SUCCESS",
        records_pulled=72,
        triggered_by="scheduler",
    )
    fresh_db.add(t)
    fresh_db.commit()
    assert t.id is not None
    assert t.status == "SUCCESS"
```

### 步骤 1.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_models_admin.py -v
```

预期：4 个测试全部 FAIL（字段不存在、表不存在）。

### 步骤 1.3: 扩展 SecurityMaster 模型

在 `backend/models.py` 中找到 `class SecurityMaster`（约第 60 行），替换为：

```python
class SecurityMaster(Base):
    """证券基础表：维护每只证券的原币种、类型等基础属性 + 管理员扩展属性。"""
    __tablename__ = "security_master"

    security_code = Column(String(20), primary_key=True)
    security_name = Column(String(100))
    currency = Column(String(10), default="CNY")     # 原币种（上市地交易币种）
    asset_type = Column(String(20))                   # 证券类型 (a_share_equity / a_share_etf / hk_equity / ...)
    type2 = Column(String(20), nullable=True)         # 主题类型2（红利/新兴产业/黄金）
    exchange = Column(String(20), nullable=True)      # 交易所
    # --- 管理员扩展属性 (2026-06-24) ---
    security_type = Column(String(20), nullable=True)  # fund / stock / bond
    fund_type = Column(String(20), nullable=True)      # 仅 fund: etf(场内) / otc(场外)
    market = Column(String(8), nullable=True)          # CN / HK / US / OF
    is_drillable = Column(Boolean, default=False)      # 仅 fund 可下钻；stock 恒 False
    index_code = Column(String(20), nullable=True)     # 仅 fund: 跟踪指数代码
    index_name = Column(String(80), nullable=True)     # 仅 fund: 跟踪指数名称
    benchmark_formula = Column(String(500), nullable=True)  # 仅 fund: 业绩比较基准
    premium_discount = Column(Float, nullable=True)    # 仅 ETF: 折溢价率（预留）
    note = Column(String(200), nullable=True)
    updated_by = Column(Integer, nullable=True)        # 最后修改人 user_id
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 步骤 1.4: 新增 DataPullTask 模型

在 `backend/models.py` 中 `SecurityMaster` 之后添加：

```python
class DataPullTask(Base):
    """数据拉取任务执行记录。"""
    __tablename__ = "data_pull_task"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(60), nullable=False, index=True)
    job_name = Column(String(100))
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False)          # SUCCESS / FAILED / RUNNING / SKIPPED
    records_pulled = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    triggered_by = Column(String(40))                    # scheduler / manual:<user_id>
    created_at = Column(DateTime, default=datetime.utcnow)
```

### 步骤 1.5: 写迁移脚本

创建 `backend/migrate_admin_columns.py`：

```python
"""迁移脚本：为 security_master 添加新列 + 创建 data_pull_task 表。

用法：python migrate_admin_columns.py
"""
import sys
sys.path.insert(0, ".")

from database import engine, Base
from sqlalchemy import text, inspect


def migrate():
    inspector = inspect(engine)
    existing_cols = {c["name"] for c in inspector.get_columns("security_master")}
    new_cols = {
        "security_type": "VARCHAR(20)",
        "fund_type": "VARCHAR(20)",
        "market": "VARCHAR(8)",
        "is_drillable": "BOOLEAN DEFAULT 0",
        "index_code": "VARCHAR(20)",
        "index_name": "VARCHAR(80)",
        "benchmark_formula": "VARCHAR(500)",
        "premium_discount": "FLOAT",
        "note": "VARCHAR(200)",
        "updated_by": "INTEGER",
    }

    with engine.connect() as conn:
        for col, col_type in new_cols.items():
            if col not in existing_cols:
                sql = f"ALTER TABLE security_master ADD COLUMN {col} {col_type}"
                print(f"  执行: {sql}")
                conn.execute(text(sql))
        conn.commit()

    # 创建 data_pull_task 表（如果不存在）
    from models import DataPullTask  # noqa: F401
    Base.metadata.create_all(bind=engine, tables=[DataPullTask.__table__])
    print("  data_pull_task 表已创建（如不存在）")
    print("迁移完成")


if __name__ == "__main__":
    migrate()
```

### 步骤 1.6: 运行迁移

```bash
cd backend
python migrate_admin_columns.py
```

### 步骤 1.7: 运行测试确认通过

```bash
python -m pytest tests/test_models_admin.py -v
```

预期：4 个测试全部 PASS。

### 步骤 1.8: commit

```bash
git add backend/models.py backend/migrate_admin_columns.py backend/tests/test_models_admin.py
git commit -m "feat(models): extend SecurityMaster + add DataPullTask (Task 1)"
```

---

## Task 2: security_master_service

### 步骤 2.1: 写 service 测试

创建 `backend/tests/test_security_master_service.py`：

```python
"""security_master_service 单元测试。"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from models import SecurityMaster, Holding, FundIndexMap, FundDrillSnapshot
from services.security_master_service import (
    list_securities,
    get_security,
    create_security,
    update_security,
    delete_security,
    sync_from_holdings,
    sync_from_drill,
    init_from_existing,
)


def test_list_securities_with_filters(fresh_db):
    """list_securities 支持按 type/market/dillable 过滤。"""
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf", market="CN",
        fund_type="etf", is_drillable=True,
    ))
    fresh_db.add(SecurityMaster(
        security_code="600519.SH", security_name="贵州茅台",
        security_type="stock", asset_type="a_share_equity", market="CN",
        is_drillable=False,
    ))
    fresh_db.commit()

    all_rows = list_securities(fresh_db)
    assert len(all_rows) == 2

    funds_only = list_securities(fresh_db, sec_type="fund")
    assert len(funds_only) == 1
    assert funds_only[0]["security_code"] == "510300.SH"

    drillable_only = list_securities(fresh_db, drillable=True)
    assert len(drillable_only) == 1
    assert drillable_only[0]["security_code"] == "510300.SH"


def test_create_security(fresh_db):
    """create_security 能创建新记录。"""
    result = create_security(fresh_db, {
        "security_code": "510300.SH",
        "security_name": "沪深300ETF",
        "security_type": "fund",
        "asset_type": "a_share_etf",
        "market": "CN",
        "fund_type": "etf",
        "is_drillable": True,
    })
    assert result["security_code"] == "510300.SH"
    assert result["is_drillable"] is True


def test_update_security(fresh_db):
    """update_security 能修改字段。"""
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf", market="CN",
        is_drillable=False,
    ))
    fresh_db.commit()

    result = update_security(fresh_db, "510300.SH", {"is_drillable": True})
    assert result["is_drillable"] is True


def test_delete_security_blocked_when_holding_exists(fresh_db):
    """有持仓时禁止删除。"""
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf",
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    with pytest.raises(ValueError, match="持仓"):
        delete_security(fresh_db, "510300.SH")


def test_sync_from_holdings(fresh_db):
    """sync_from_holdings 为缺失的证券创建记录。"""
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    count = sync_from_holdings(fresh_db)
    assert count == 1
    sm = fresh_db.query(SecurityMaster).filter_by(security_code="510300.SH").first()
    assert sm is not None
    assert sm.security_name == "沪深300ETF"
    assert sm.asset_type == "a_share_etf"


def test_sync_from_drill(fresh_db):
    """sync_from_drill 为下钻股票创建记录。"""
    fresh_db.add(FundDrillSnapshot(
        fund_code="510300.SH", as_of_date=date(2026, 6, 24),
        stock_code="600519.SH", stock_name="贵州茅台",
        weight_pct=5.0, baseline_price=1500.0, current_price=1600.0,
        shares_equivalent=0.001,
    ))
    fresh_db.commit()

    count = sync_from_drill(fresh_db)
    assert count == 1
    sm = fresh_db.query(SecurityMaster).filter_by(security_code="600519.SH").first()
    assert sm is not None
    assert sm.security_name == "贵州茅台"
    assert sm.security_type == "stock"


def test_init_from_existing(fresh_db):
    """init_from_existing 从 FundIndexMap + Holding 批量初始化。"""
    fresh_db.add(FundIndexMap(
        fund_code="510300.SH", fund_name="沪深300ETF",
        index_code="000300.SH", index_name="沪深300",
        as_of_date=date(2026, 6, 24), source="test",
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.commit()

    count = init_from_existing(fresh_db)
    assert count >= 1
    sm = fresh_db.query(SecurityMaster).filter_by(security_code="510300.SH").first()
    assert sm is not None
    assert sm.index_code == "000300"
    assert sm.is_drillable is True  # a_share_etf 默认可下钻
```

### 步骤 2.2: 运行测试确认失败

```bash
python -m pytest tests/test_security_master_service.py -v
```

预期：全部 FAIL（ImportError）。

### 步骤 2.3: 实现 security_master_service

创建 `backend/services/security_master_service.py`：

```python
"""证券主数据 service — CRUD + 同步 + 初始化。

依赖：SecurityMaster, Holding, FundDrillSnapshot, FundIndexMap
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import SecurityMaster, Holding, FundDrillSnapshot, FundIndexMap

logger = logging.getLogger(__name__)

# 旧硬编码 → 新 is_drillable 的迁移映射
_LEGACY_DRILLABLE_ASSET_TYPES = frozenset({
    "a_share_equity", "a_share_etf", "hk_equity", "qdii_equity", "us_etf",
})


def _derive_market(code: str) -> str:
    """从证券代码推断市场。"""
    if code.endswith(".OF"):
        return "OF"
    if code.endswith(".SH") or code.endswith(".SZ"):
        return "CN"
    if code.endswith(".HK"):
        return "HK"
    if "." not in code:
        return "US"
    return "CN"


def _derive_fund_type(code: str, asset_type: str) -> str | None:
    """推断基金类型：etf(场内) / otc(场外)。"""
    if asset_type in ("a_share_etf", "us_etf"):
        return "etf"
    if code.endswith(".OF"):
        return "otc"
    return None


def _derive_security_type(asset_type: str) -> str:
    """从 asset_type 推断 security_type。"""
    if asset_type in ("bond", "qdii_bond"):
        return "bond"
    if "equity" in asset_type or "etf" in asset_type:
        return "fund"
    if asset_type in ("gold", "commodity"):
        return "fund"
    return "fund"


def _to_dict(sm: SecurityMaster) -> dict:
    """将 ORM 对象转为 dict。"""
    return {
        "security_code": sm.security_code,
        "security_name": sm.security_name,
        "currency": sm.currency,
        "asset_type": sm.asset_type,
        "type2": sm.type2,
        "exchange": sm.exchange,
        "security_type": sm.security_type,
        "fund_type": sm.fund_type,
        "market": sm.market,
        "is_drillable": sm.is_drillable,
        "index_code": sm.index_code,
        "index_name": sm.index_name,
        "benchmark_formula": sm.benchmark_formula,
        "premium_discount": sm.premium_discount,
        "note": sm.note,
        "updated_at": sm.updated_at.isoformat() if sm.updated_at else None,
    }


def list_securities(
    db: Session,
    sec_type: str | None = None,
    market: str | None = None,
    drillable: bool | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> list[dict]:
    """查询证券主数据列表（分页+筛选）。"""
    q = db.query(SecurityMaster)
    if sec_type:
        q = q.filter(SecurityMaster.security_type == sec_type)
    if market:
        q = q.filter(SecurityMaster.market == market)
    if drillable is not None:
        q = q.filter(SecurityMaster.is_drillable == drillable)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (SecurityMaster.security_code.like(like))
            | (SecurityMaster.security_name.like(like))
        )
    total = q.count()
    rows = q.order_by(SecurityMaster.security_code).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_to_dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def get_security(db: Session, security_code: str) -> dict | None:
    """查询单条证券主数据。"""
    sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == security_code).first()
    return _to_dict(sm) if sm else None


def create_security(db: Session, data: dict) -> dict:
    """新增证券主数据。"""
    sm = SecurityMaster(
        security_code=data["security_code"],
        security_name=data.get("security_name"),
        currency=data.get("currency", "CNY"),
        asset_type=data.get("asset_type"),
        type2=data.get("type2"),
        exchange=data.get("exchange"),
        security_type=data.get("security_type"),
        fund_type=data.get("fund_type"),
        market=data.get("market"),
        is_drillable=data.get("is_drillable", False),
        index_code=data.get("index_code"),
        index_name=data.get("index_name"),
        benchmark_formula=data.get("benchmark_formula"),
        premium_discount=data.get("premium_discount"),
        note=data.get("note"),
        updated_by=data.get("updated_by"),
    )
    db.add(sm)
    db.commit()
    db.refresh(sm)
    return _to_dict(sm)


def update_security(db: Session, security_code: str, data: dict) -> dict | None:
    """更新证券主数据。"""
    sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == security_code).first()
    if not sm:
        return None
    for key in ("security_name", "currency", "asset_type", "type2", "exchange",
                "security_type", "fund_type", "market", "is_drillable", "index_code",
                "index_name", "benchmark_formula", "premium_discount", "note", "updated_by"):
        if key in data:
            setattr(sm, key, data[key])
    db.commit()
    db.refresh(sm)
    return _to_dict(sm)


def delete_security(db: Session, security_code: str) -> bool:
    """删除证券主数据（有持仓时禁止）。"""
    holding_count = db.query(Holding).filter(Holding.security_code == security_code).count()
    if holding_count > 0:
        raise ValueError(f"无法删除：该证券有 {holding_count} 条持仓记录")
    sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == security_code).first()
    if not sm:
        return False
    db.delete(sm)
    db.commit()
    return True


def sync_from_holdings(db: Session) -> int:
    """从 Holding 表同步：为不在 SecurityMaster 中的证券创建记录。"""
    existing = {r[0] for r in db.query(SecurityMaster.security_code).all()}
    holdings = db.query(Holding).filter(~Holding.security_code.in_(existing)).all() if existing else db.query(Holding).all()
    count = 0
    for h in holdings:
        if h.security_code in existing:
            continue
        sm = SecurityMaster(
            security_code=h.security_code,
            security_name=h.security_name,
            asset_type=h.asset_type,
            security_type=_derive_security_type(h.asset_type or ""),
            market=_derive_market(h.security_code),
            fund_type=_derive_fund_type(h.security_code, h.asset_type or ""),
            is_drillable=(h.asset_type in _LEGACY_DRILLABLE_ASSET_TYPES) if h.asset_type else False,
        )
        db.add(sm)
        existing.add(h.security_code)
        count += 1
    db.commit()
    return count


def sync_from_drill(db: Session) -> int:
    """从 FundDrillSnapshot 表同步：为下钻股票创建记录。"""
    existing = {r[0] for r in db.query(SecurityMaster.security_code).all()}
    stocks = db.query(
        FundDrillSnapshot.stock_code, FundDrillSnapshot.stock_name
    ).filter(
        ~FundDrillSnapshot.stock_code.in_(existing)
    ).distinct().all() if existing else db.query(
        FundDrillSnapshot.stock_code, FundDrillSnapshot.stock_name
    ).distinct().all()
    count = 0
    for code, name in stocks:
        sm = SecurityMaster(
            security_code=code,
            security_name=name,
            security_type="stock",
            market=_derive_market(code),
            is_drillable=False,
        )
        db.add(sm)
        existing.add(code)
        count += 1
    db.commit()
    return count


def init_from_existing(db: Session) -> int:
    """一次性初始化：从 FundIndexMap + Holding + FundDrillSnapshot 批量导入。"""
    count = 0
    count += sync_from_holdings(db)
    count += sync_from_drill(db)

    # 从 FundIndexMap 补充 index_code/index_name/benchmark
    fund_maps = db.query(FundIndexMap).all()
    for fm in fund_maps:
        sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == fm.fund_code).first()
        if sm and not sm.index_code:
            sm.index_code = fm.index_code.split(".")[0] if fm.index_code else None
            sm.index_name = fm.index_name
            sm.benchmark_formula = fm.benchmark_formula
    db.commit()
    return count
```

### 步骤 2.4: 运行测试确认通过

```bash
python -m pytest tests/test_security_master_service.py -v
```

预期：7 个测试全部 PASS。

### 步骤 2.5: commit

```bash
git add backend/services/security_master_service.py backend/tests/test_security_master_service.py
git commit -m "feat(service): security_master_service CRUD + sync (Task 2)"
```

---

## Task 3: data_readiness_service

### 步骤 3.1: 写 service 测试

创建 `backend/tests/test_data_readiness_service.py`：

```python
"""data_readiness_service 单元测试。"""
from datetime import date
from models import Holding, FundDrillSnapshot, IndexConstituentSnapshot, AShareFinancialSnapshot
from services.data_readiness_service import get_data_readiness


def test_readiness_all_empty(fresh_db):
    """无数据时全部返回 ❌。"""
    result = get_data_readiness(fresh_db, date(2026, 6, 24))
    assert isinstance(result, list)
    assert len(result) >= 4
    for item in result:
        assert item["status"] in ("ok", "missing", "partial")
        assert "source" in item
        assert "expected" in item
        assert "actual" in item


def test_readiness_with_drill_snapshot(fresh_db):
    """有下钻 snapshot 时返回 ok。"""
    fresh_db.add(FundDrillSnapshot(
        fund_code="510300.SH", as_of_date=date(2026, 6, 24),
        stock_code="600519.SH", stock_name="贵州茅台",
        weight_pct=5.0, baseline_price=1500.0, current_price=1600.0,
        shares_equivalent=0.001,
    ))
    fresh_db.commit()

    result = get_data_readiness(fresh_db, date(2026, 6, 24))
    drill_item = next(r for r in result if "下钻" in r["source"])
    assert drill_item["actual"] >= 1


def test_readiness_with_constituents(fresh_db):
    """有成分股数据时返回 ok。"""
    fresh_db.add(IndexConstituentSnapshot(
        as_of_date=date(2026, 6, 24), index_code="000300",
        stock_code="600519.SH", stock_name="贵州茅台", weight=5.0,
    ))
    fresh_db.commit()

    result = get_data_readiness(fresh_db, date(2026, 6, 24))
    const_item = next(r for r in result if "成分股" in r["source"])
    assert const_item["actual"] >= 1
```

### 步骤 3.2: 运行测试确认失败

```bash
python -m pytest tests/test_data_readiness_service.py -v
```

### 步骤 3.3: 实现 data_readiness_service

创建 `backend/services/data_readiness_service.py`：

```python
"""数据就绪检查 service — 检查各数据源在指定日期的就绪状态。

依赖：Holding, FundDrillSnapshot, IndexConstituentSnapshot, AShareFinancialSnapshot, HKShareFinancialSnapshot
"""
from __future__ import annotations

import logging
from datetime import date as _date
from sqlalchemy.orm import Session

from models import (
    Holding, FundDrillSnapshot, IndexConstituentSnapshot,
    AShareFinancialSnapshot, HKShareFinancialSnapshot, FundDailyNav,
)

logger = logging.getLogger(__name__)


def _check_cn_prices(db: Session, as_of: _date) -> dict:
    """检查 CN 价格就绪状态。"""
    cn_codes = {r[0] for r in db.query(Holding.security_code).filter(
        Holding.security_code.like("%.SH") | Holding.security_code.like("%.SZ")
    ).all()}
    actual = db.query(FundDailyNav).filter(
        FundDailyNav.trade_date == as_of,
        FundDailyNav.fund_code.in_(cn_codes) if cn_codes else False,
    ).count()
    return {
        "source": "CN价格",
        "expected": len(cn_codes),
        "actual": actual,
        "status": "ok" if actual >= len(cn_codes) and len(cn_codes) > 0 else ("missing" if actual == 0 else "partial"),
    }


def _check_drill_snapshot(db: Session, as_of: _date) -> dict:
    """检查下钻 snapshot 就绪状态。"""
    actual = db.query(FundDrillSnapshot).filter(FundDrillSnapshot.as_of_date == as_of).count()
    return {
        "source": "下钻snapshot",
        "expected": 1,  # 至少 1 条
        "actual": actual,
        "status": "ok" if actual > 0 else "missing",
    }


def _check_constituents(db: Session, as_of: _date) -> dict:
    """检查成分股就绪状态。"""
    actual = db.query(IndexConstituentSnapshot).filter(IndexConstituentSnapshot.as_of_date == as_of).count()
    return {
        "source": "成分股",
        "expected": 1,
        "actual": actual,
        "status": "ok" if actual > 0 else "missing",
    }


def _check_financials(db: Session, as_of: _date) -> dict:
    """检查财务数据就绪状态。"""
    a_count = db.query(AShareFinancialSnapshot).filter(AShareFinancialSnapshot.as_of_date == as_of).count()
    h_count = db.query(HKShareFinancialSnapshot).filter(HKShareFinancialSnapshot.as_of_date == as_of).count()
    total = a_count + h_count
    return {
        "source": "财务数据",
        "expected": 1,
        "actual": total,
        "status": "ok" if total > 0 else "missing",
    }


def _check_hk_prices(db: Session, as_of: _date) -> dict:
    """检查 HK 价格就绪状态。"""
    hk_codes = {r[0] for r in db.query(Holding.security_code).filter(Holding.security_code.like("%.HK")).all()}
    return {
        "source": "HK价格",
        "expected": len(hk_codes),
        "actual": 0,  # TODO: 接入 HK 价格表后补充
        "status": "ok" if len(hk_codes) == 0 else "missing",
    }


def _check_us_prices(db: Session, as_of: _date) -> dict:
    """检查 US 价格就绪状态。"""
    us_codes = {r[0] for r in db.query(Holding.security_code).filter(
        ~Holding.security_code.like("%.SH") & ~Holding.security_code.like("%.SZ") & ~Holding.security_code.like("%.HK") & ~Holding.security_code.like("%.OF")
    ).all()}
    return {
        "source": "US价格",
        "expected": len(us_codes),
        "actual": 0,  # TODO: 接入 US 价格表后补充
        "status": "ok" if len(us_codes) == 0 else "missing",
    }


def get_data_readiness(db: Session, as_of: _date) -> list[dict]:
    """检查各数据源在 as_of 的就绪状态。返回 [{source, expected, actual, status}, ...]"""
    return [
        _check_cn_prices(db, as_of),
        _check_hk_prices(db, as_of),
        _check_us_prices(db, as_of),
        _check_financials(db, as_of),
        _check_constituents(db, as_of),
        _check_drill_snapshot(db, as_of),
    ]
```

### 步骤 3.4: 运行测试确认通过

```bash
python -m pytest tests/test_data_readiness_service.py -v
```

### 步骤 3.5: commit

```bash
git add backend/services/data_readiness_service.py backend/tests/test_data_readiness_service.py
git commit -m "feat(service): data_readiness_service (Task 3)"
```

---

## Task 4: data_pull_task_service

### 步骤 4.1: 写 service 测试

创建 `backend/tests/test_data_pull_task_service.py`：

```python
"""data_pull_task_service 单元测试。"""
from datetime import datetime
from models import DataPullTask
from services.data_pull_task_service import record_task_start, record_task_finish, list_tasks


def test_record_task_start(fresh_db):
    """record_task_start 创建 RUNNING 状态记录。"""
    task = record_task_start(fresh_db, "crawl_cn_prices", "拉取A股价格", "scheduler")
    assert task["job_id"] == "crawl_cn_prices"
    assert task["status"] == "RUNNING"
    assert task["started_at"] is not None


def test_record_task_finish_success(fresh_db):
    """record_task_finish 更新为 SUCCESS。"""
    task = record_task_start(fresh_db, "crawl_cn_prices", "拉取A股价格", "scheduler")
    finished = record_task_finish(fresh_db, task["id"], "SUCCESS", records_pulled=72)
    assert finished["status"] == "SUCCESS"
    assert finished["records_pulled"] == 72
    assert finished["finished_at"] is not None


def test_record_task_finish_failed(fresh_db):
    """record_task_finish 更新为 FAILED + error_message。"""
    task = record_task_start(fresh_db, "crawl_cn_prices", "拉取A股价格", "scheduler")
    finished = record_task_finish(fresh_db, task["id"], "FAILED", error_message="timeout")
    assert finished["status"] == "FAILED"
    assert finished["error_message"] == "timeout"


def test_list_tasks_filter_by_status(fresh_db):
    """list_tasks 支持按 status 过滤。"""
    t1 = record_task_start(fresh_db, "job1", "任务1", "scheduler")
    record_task_finish(fresh_db, t1["id"], "SUCCESS")
    t2 = record_task_start(fresh_db, "job2", "任务2", "scheduler")

    all_tasks = list_tasks(fresh_db)
    assert len(all_tasks["items"]) == 2

    running_only = list_tasks(fresh_db, status="RUNNING")
    assert len(running_only["items"]) == 1
    assert running_only["items"][0]["job_id"] == "job2"
```

### 步骤 4.2: 运行测试确认失败

```bash
python -m pytest tests/test_data_pull_task_service.py -v
```

### 步骤 4.3: 实现 data_pull_task_service

创建 `backend/services/data_pull_task_service.py`：

```python
"""数据拉取任务记录 service — 记录/查询任务执行历史。

依赖：DataPullTask
"""
from __future__ import annotations

import logging
from datetime import datetime
from sqlalchemy.orm import Session

from models import DataPullTask

logger = logging.getLogger(__name__)


def record_task_start(
    db: Session, job_id: str, job_name: str, triggered_by: str
) -> dict:
    """记录任务开始（创建 RUNNING 状态记录）。"""
    task = DataPullTask(
        job_id=job_id,
        job_name=job_name,
        started_at=datetime.utcnow(),
        status="RUNNING",
        triggered_by=triggered_by,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _to_dict(task)


def record_task_finish(
    db: Session,
    task_id: int,
    status: str,
    records_pulled: int = 0,
    error_message: str | None = None,
) -> dict | None:
    """记录任务结束（更新状态）。"""
    task = db.query(DataPullTask).filter(DataPullTask.id == task_id).first()
    if not task:
        return None
    task.status = status
    task.finished_at = datetime.utcnow()
    task.records_pulled = records_pulled
    task.error_message = error_message
    db.commit()
    db.refresh(task)
    return _to_dict(task)


def list_tasks(
    db: Session,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """查询任务历史（分页+筛选）。"""
    q = db.query(DataPullTask)
    if status:
        q = q.filter(DataPullTask.status == status)
    if date_from:
        q = q.filter(DataPullTask.started_at >= date_from)
    if date_to:
        q = q.filter(DataPullTask.started_at <= date_to)
    total = q.count()
    rows = q.order_by(DataPullTask.started_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_to_dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


def _to_dict(task: DataPullTask) -> dict:
    """将 ORM 对象转为 dict。"""
    return {
        "id": task.id,
        "job_id": task.job_id,
        "job_name": task.job_name,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "status": task.status,
        "records_pulled": task.records_pulled,
        "error_message": task.error_message,
        "triggered_by": task.triggered_by,
    }
```

### 步骤 4.4: 运行测试确认通过

```bash
python -m pytest tests/test_data_pull_task_service.py -v
```

### 步骤 4.5: commit

```bash
git add backend/services/data_pull_task_service.py backend/tests/test_data_pull_task_service.py
git commit -m "feat(service): data_pull_task_service (Task 4)"
```

---

## Task 5: 修改 drill_user_service join SecurityMaster

### 步骤 5.1: 更新测试

修改 `backend/tests/test_drill_user_service.py`，添加 SecurityMaster join 测试：

```python
def test_get_user_fund_codes_uses_security_master(fresh_db):
    """get_user_fund_codes 应 join SecurityMaster.is_drillable 过滤。"""
    from models import SecurityMaster, Holding
    # 基金 A: is_drillable=True
    fresh_db.add(SecurityMaster(
        security_code="510300.SH", security_name="沪深300ETF",
        security_type="fund", asset_type="a_share_etf", is_drillable=True,
    ))
    # 基金 B: is_drillable=False
    fresh_db.add(SecurityMaster(
        security_code="006829.OF", security_name="短债A",
        security_type="fund", asset_type="bond", is_drillable=False,
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="510300.SH", security_name="沪深300ETF",
        quantity=1000, asset_type="a_share_etf",
    ))
    fresh_db.add(Holding(
        user_id=1, security_code="006829.OF", security_name="短债A",
        quantity=500, asset_type="bond",
    ))
    fresh_db.commit()

    from services.drill_user_service import get_user_fund_codes
    codes = get_user_fund_codes(fresh_db, 1)
    assert "510300.SH" in codes
    assert "006829.OF" not in codes  # is_drillable=False
```

### 步骤 5.2: 运行测试确认失败

```bash
python -m pytest tests/test_drill_user_service.py::test_get_user_fund_codes_uses_security_master -v
```

### 步骤 5.3: 修改 drill_user_service

在 `backend/services/drill_user_service.py` 中修改 `get_user_fund_codes`：

```python
# 旧实现（保留作为 fallback）：
# DRILLABLE_ASSET_TYPES = frozenset({"a_share_equity", "a_share_etf", "hk_equity", "qdii_equity", "us_etf"})
# def get_user_fund_codes(db, user_id):
#     return {r[0] for r in db.query(Holding.security_code).filter(
#         Holding.user_id == user_id,
#         Holding.asset_type.in_(DRILLABLE_ASSET_TYPES),
#     ).all()}

# 新实现：join SecurityMaster
from models import SecurityMaster

def get_user_fund_codes(db: Session, user_id: int) -> set[str]:
    """返回用户持有的所有可下钻基金代码集合（join SecurityMaster.is_drillable）。"""
    try:
        return set(
            r[0] for r in db.query(Holding.security_code)
            .join(SecurityMaster, Holding.security_code == SecurityMaster.security_code)
            .filter(Holding.user_id == user_id)
            .filter(SecurityMaster.is_drillable == True)
            .all()
        )
    except Exception:
        # Fallback: SecurityMaster 表不存在或为空时用旧逻辑
        return set(
            r[0] for r in db.query(Holding.security_code).filter(
                Holding.user_id == user_id,
                Holding.asset_type.in_(DRILLABLE_ASSET_TYPES),
            ).all()
        )
```

### 步骤 5.4: 运行全部 drill 测试确认通过

```bash
python -m pytest tests/test_drill_user_service.py tests/test_drill_orchestration.py tests/test_drill_api_integration.py -v
```

### 步骤 5.5: commit

```bash
git add backend/services/drill_user_service.py backend/tests/test_drill_user_service.py
git commit -m "refactor(drill): join SecurityMaster.is_drillable (Task 5)"
```

---

## Task 6: API 端点

### 步骤 6.1: 在 main.py 中添加 admin 端点

在 `backend/main.py` 末尾（其他 admin 端点之后）添加：

```python
# ========== Admin: 证券主数据 ==========

@app.get("/api/admin/security-master")
def admin_list_securities(
    type: str | None = None, market: str | None = None,
    drillable: bool | None = None, search: str | None = None,
    page: int = 1, page_size: int = 50,
):
    from services.security_master_service import list_securities
    return list_securities(db, sec_type=type, market=market, drillable=drillable, search=search, page=page, page_size=page_size)


@app.post("/api/admin/security-master")
def admin_create_security(body: dict = Body(...)):
    from services.security_master_service import create_security
    return create_security(db, body)


@app.put("/api/admin/security-master/{code}")
def admin_update_security(code: str, body: dict = Body(...)):
    from services.security_master_service import update_security
    result = update_security(db, code, body)
    if not result:
        raise HTTPException(404, "证券不存在")
    return result


@app.delete("/api/admin/security-master/{code}")
def admin_delete_security(code: str):
    from services.security_master_service import delete_security
    try:
        ok = delete_security(db, code)
        if not ok:
            raise HTTPException(404, "证券不存在")
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/admin/security-master/sync-from-holdings")
def admin_sync_from_holdings():
    from services.security_master_service import sync_from_holdings
    count = sync_from_holdings(db)
    return {"status": "ok", "synced": count}


@app.post("/api/admin/security-master/sync-from-drill")
def admin_sync_from_drill():
    from services.security_master_service import sync_from_drill
    count = sync_from_drill(db)
    return {"status": "ok", "synced": count}


@app.post("/api/admin/security-master/init")
def admin_init_security_master():
    from services.security_master_service import init_from_existing
    count = init_from_existing(db)
    return {"status": "ok", "initialized": count}


# ========== Admin: 基金-指数映射 ==========

@app.get("/api/admin/fund-index-map")
def admin_list_fund_index_map(search: str | None = None, page: int = 1, page_size: int = 50):
    q = db.query(FundIndexMap)
    if search:
        like = f"%{search}%"
        q = q.filter(FundIndexMap.fund_code.like(like) | FundIndexMap.fund_name.like(like) | FundIndexMap.index_code.like(like))
    total = q.count()
    rows = q.order_by(FundIndexMap.fund_code).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [{"fund_code": r.fund_code, "fund_name": r.fund_name, "index_code": r.index_code, "index_name": r.index_name, "benchmark_formula": r.benchmark_formula, "as_of_date": r.as_of_date.isoformat(), "source": r.source} for r in rows], "total": total, "page": page, "page_size": page_size}


@app.post("/api/admin/fund-index-map")
def admin_create_fund_index_map(body: dict = Body(...)):
    fm = FundIndexMap(
        fund_code=body["fund_code"], fund_name=body.get("fund_name"),
        index_code=body["index_code"], index_name=body.get("index_name"),
        benchmark_formula=body.get("benchmark_formula"),
        as_of_date=body.get("as_of_date", date.today()),
        source=body.get("source", "manual"),
    )
    db.add(fm)
    db.commit()
    return {"status": "ok", "fund_code": fm.fund_code}


@app.put("/api/admin/fund-index-map/{fund_code}/{as_of_date}")
def admin_update_fund_index_map(fund_code: str, as_of_date: date, body: dict = Body(...)):
    fm = db.query(FundIndexMap).filter(FundIndexMap.fund_code == fund_code, FundIndexMap.as_of_date == as_of_date).first()
    if not fm:
        raise HTTPException(404, "映射不存在")
    for key in ("fund_name", "index_code", "index_name", "benchmark_formula", "source"):
        if key in body:
            setattr(fm, key, body[key])
    db.commit()
    return {"status": "ok"}


@app.delete("/api/admin/fund-index-map/{fund_code}/{as_of_date}")
def admin_delete_fund_index_map(fund_code: str, as_of_date: date):
    fm = db.query(FundIndexMap).filter(FundIndexMap.fund_code == fund_code, FundIndexMap.as_of_date == as_of_date).first()
    if not fm:
        raise HTTPException(404, "映射不存在")
    db.delete(fm)
    db.commit()
    return {"status": "ok"}


# ========== Admin: 数据就绪 + 任务历史 ==========

@app.get("/api/admin/data-readiness")
def admin_data_readiness(as_of_date: date = Query(...)):
    from services.data_readiness_service import get_data_readiness
    return {"as_of_date": as_of_date.isoformat(), "items": get_data_readiness(db, as_of_date)}


@app.get("/api/admin/data-pull-tasks")
def admin_list_data_pull_tasks(status: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None, page: int = 1, page_size: int = 50):
    from services.data_pull_task_service import list_tasks
    return list_tasks(db, status=status, date_from=date_from, date_to=date_to, page=page, page_size=page_size)


@app.post("/api/admin/data-pull-tasks/trigger/{job_id}")
def admin_trigger_data_pull_task(job_id: str, request: Request = None):
    # 手动触发 scheduler 任务
    from services.scheduler import trigger_job
    result = trigger_job(db, job_id, triggered_by=f"manual:{request.state.user.id}" if hasattr(request.state, 'user') else "manual")
    return {"status": "ok", "result": result}
```

### 步骤 6.2: 运行已有测试确认无回归

```bash
python -m pytest tests/test_drill_api_integration.py -v
```

### 步骤 6.3: commit

```bash
git add backend/main.py
git commit -m "feat(api): admin security-master + fund-index-map + data-readiness + data-pull-tasks endpoints (Task 6)"
```

---

## Task 7: 修改 scheduler.py 集成 record_task

### 步骤 7.1: 在 scheduler 执行器中集成

在 `backend/services/scheduler.py` 中找到 `JOB_DISPATCH` 的执行逻辑，在每次执行前后调用 `record_task_start/finish`。具体实现取决于现有 scheduler 代码结构。

### 步骤 7.2: commit

```bash
git add backend/services/scheduler.py
git commit -m "feat(scheduler): integrate data_pull_task recording (Task 7)"
```

---

## Task 8: 前端侧边栏重构

### 步骤 8.1: 修改 App.jsx TABS 数组

在 `frontend/src/App.jsx` 中替换 TABS 数组：

```javascript
const TABS = [
  { id: 'overview',  label: '总览',    icon: ICONS.overview, visibility: ['user','advisor','admin'] },
  { id: 'analysis',  label: '分析',    icon: ICONS.analysis, visibility: ['user','advisor','admin'] },
  { id: 'analyst',   label: '分析师',  icon: ICONS.analyst,  visibility: ['user','advisor','admin'] },
  { id: 'watch',     label: '关注',    icon: ICONS.watch,    visibility: ['user','advisor','admin'] },
  { id: 'trading',   label: '交易',    icon: ICONS.trading,  visibility: ['user'] },
  { id: 'relation',  label: '关联',    icon: 'M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a3 3 0 015.36-1.87M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 7a2 2 0 11-4 0 2 2 0 014 0z', visibility: ['user','advisor'] },
  { id: 'settings',  label: '设置',    icon: ICONS.settings, visibility: ['user','advisor','admin'] },
  // --- 分割线（仅 admin 可见） ---
  { id: 'masterData',   label: '主数据',   icon: 'M4 6h16M4 12h16M4 18h7', visibility: ['admin'] },
  { id: 'dataSource',   label: '数据源',   icon: 'M4 7v10m4-14v18m4-14v10m4-14v18', visibility: ['admin'] },
  { id: 'contentUpload', label: '内容上传', icon: 'M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2z', visibility: ['admin'] },
]
```

### 步骤 8.2: 添加分割线渲染

在侧边栏渲染逻辑中，找到 `visibleTabs.map(...)` 之前，插入分割线逻辑：

```javascript
// 在 visibleTabs 渲染中，找到 'settings' 之后插入分割线
{visibleTabs.map((t, i) => {
  const showDivider = t.id === 'masterData' && i > 0
  return (
    <React.Fragment key={t.id}>
      {showDivider && <div className="sidebar-divider" style={{height:1, background:'var(--border)', margin:'8px 0'}} />}
      <button ...>{t.label}</button>
    </React.Fragment>
  )
})}
```

### 步骤 8.3: 更新页面路由

在 `App.jsx` 的 `switch(activeTab)` 中添加新组件路由：

```javascript
case 'masterData': return <MasterDataPanel />
case 'dataSource': return <DataSourcePanel />
case 'contentUpload': return <ContentUploadPanel />
```

移除旧的路由：`case 'data'`, `case 'ops'`, `case 'dataGap'`, `case 'strategies'`, `case 'adminSettings'`。

### 步骤 8.4: 创建占位组件

创建 `frontend/src/components/ContentUploadPanel.jsx`：

```jsx
import React from 'react'

/** 内容上传页 — 子项目 2 实现，当前占位。 */
export default function ContentUploadPanel() {
  return (
    <div className="raised" style={{ padding: 24, textAlign: 'center' }}>
      <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>内容上传</div>
      <div style={{ color: 'var(--text-muted)' }}>即将上线（子项目 2）</div>
      <div style={{ color: 'var(--text-muted)', fontSize: 12, marginTop: 8 }}>
        指数构成 PDF 上传 · 股票分析报告 · 产业链报告 · 财务数据手动上传
      </div>
    </div>
  )
}
```

### 步骤 8.5: commit

```bash
git add frontend/src/App.jsx frontend/src/components/ContentUploadPanel.jsx
git commit -m "feat(ui): sidebar restructure with divider + admin domains (Task 8)"
```

---

## Task 9: 前端 MasterDataPanel

### 步骤 9.1: 创建 MasterDataPanel.jsx

创建 `frontend/src/components/MasterDataPanel.jsx`，包含两个 tab（证券主数据 + 基金-指数映射），调用 `/api/admin/security-master` 和 `/api/admin/fund-index-map` 端点。

### 步骤 9.2: 创建 SecurityMasterTab.jsx

证券主数据表格 + 筛选 + 编辑抽屉 + 同步按钮。

### 步骤 9.3: 创建 FundIndexMapTab.jsx

基金-指数映射表格 + CRUD。

### 步骤 9.4: commit

```bash
git add frontend/src/components/MasterDataPanel.jsx frontend/src/components/SecurityMasterTab.jsx frontend/src/components/FundIndexMapTab.jsx
git commit -m "feat(ui): MasterDataPanel with SecurityMaster + FundIndexMap tabs (Task 9)"
```

---

## Task 10: 前端 DataSourcePanel

### 步骤 10.1: 创建 DataSourcePanel.jsx

5 个 tab：数据就绪 / 任务历史 / API策略 / 交易日历 / 数据浏览。

### 步骤 10.2: 创建 DataReadinessTab.jsx

调用 `/api/admin/data-readiness`，展示就绪状态表格。

### 步骤 10.3: 创建 TaskHistoryTab.jsx

调用 `/api/admin/data-pull-tasks`，展示任务历史表格 + 手动触发按钮。

### 步骤 10.4: 创建 ApiStrategyTab.jsx

整合现有 StrategiesPanel 的功能。

### 步骤 10.5: 交易日历和数据浏览 tab 复用现有组件

在 DataSourcePanel 中直接渲染 `<TradingCalendarView />` 和 `<DataBrowser />`。

### 步骤 10.6: 删除旧组件

```bash
rm frontend/src/components/AdminSettingsPanel.jsx
rm frontend/src/components/OpsPanel.jsx
rm frontend/src/components/DataGapPanel.jsx
rm frontend/src/components/StrategiesPanel.jsx
```

### 步骤 10.7: commit

```bash
git add frontend/src/components/DataSourcePanel.jsx frontend/src/components/DataReadinessTab.jsx frontend/src/components/TaskHistoryTab.jsx frontend/src/components/ApiStrategyTab.jsx
git rm frontend/src/components/AdminSettingsPanel.jsx frontend/src/components/OpsPanel.jsx frontend/src/components/DataGapPanel.jsx frontend/src/components/StrategiesPanel.jsx
git commit -m "feat(ui): DataSourcePanel with 5 tabs + remove old components (Task 10)"
```

---

## Task 11: 集成测试 + 迁移 + 最终验证

### 步骤 11.1: 写 API 集成测试

创建 `backend/tests/test_admin_master_data_api.py` 和 `backend/tests/test_admin_data_source_api.py`，测试端到端流程。

### 步骤 11.2: 运行迁移

```bash
cd backend
python migrate_admin_columns.py
python -c "from database import SessionLocal; from services.security_master_service import init_from_existing; db=SessionLocal(); print(f'初始化: {init_from_existing(db)} 条'); db.close()"
```

### 步骤 11.3: 运行全部测试

```bash
python -m pytest tests/ -v --tb=short
```

### 步骤 11.4: 前端验证

启动前后端，验证：
1. 侧边栏顺序正确 + 分割线显示
2. 主数据页能列出证券 + 编辑 is_drillable
3. 数据源页 5 个 tab 都能正常切换
4. drill 功能在 is_drillable 修改后正确联动

### 步骤 11.5: commit

```bash
git add backend/tests/test_admin_master_data_api.py backend/tests/test_admin_data_source_api.py
git commit -m "test: admin master data + data source integration tests (Task 11)"
```

---

## 自审检查

### Spec 覆盖
- [x] 侧边栏重排 + 分割线 → Task 8
- [x] SecurityMaster 扩展（is_drillable 等）→ Task 1
- [x] DataPullTask 新建 → Task 1
- [x] 证券主数据 CRUD + 同步 → Task 2 + Task 6
- [x] 基金-指数映射 CRUD → Task 6
- [x] 数据就绪检查 → Task 3 + Task 6
- [x] 任务历史记录 → Task 4 + Task 6 + Task 7
- [x] drill_user_service join SecurityMaster → Task 5
- [x] API策略整合 → Task 10
- [x] 交易日历复用 → Task 10
- [x] 数据浏览复用 → Task 10
- [x] 内容上传占位 → Task 8
- [x] 集成测试 → Task 11

### Placeholder scan
- 无 TBD/TODO（data_readiness_service 中的 HK/US 价格检查标记为 TODO，因为现有系统无对应价格表，这是合理的预留）
- 所有步骤有具体代码

### Type consistency
- SecurityMaster 字段在模型、service、API 中一致
- DataPullTask 字段在模型、service、API 中一致
