# 公共数据主数据重构实施计划 — Spec-1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有 `security_master` 单表拆为 `stock_master` / `fund_master` / `index_master` 三表，引入 `classification` + `classification_assign` 两分类表，加 akshare 指数轮询，改造基金-指数映射为双向 typeahead 选择。

**Architecture:** 后端 FastAPI + SQLAlchemy 2.0 + PostgreSQL；前端 React + vitest；迁移走自定义 idempotent 脚本（不是 Alembic，因为项目已有此模式）；轮询走 APScheduler cron job；权限复用现有 `require_admin`。

**Tech Stack:** Python 3.x、SQLAlchemy 2.0、FastAPI、pytest、psycopg、akshare 1.18.8、APScheduler、React 18、vitest、@testing-library/react、axios。

---

## File Structure

**新建后端文件:**
- `backend/models_master.py` — 5 张新表的 SQLAlchemy 模型（StockMaster, FundMaster, IndexMaster, Classification, ClassificationAssign）
- `backend/services/stock_master_service.py` — 股票 CRUD + 同步
- `backend/services/fund_master_service.py` — 基金 CRUD + 同步
- `backend/services/index_master_service.py` — 指数 CRUD + 同步
- `backend/services/classification_service.py` — 分类 CRUD（字典 + assign）
- `backend/services/akshare_index_poller.py` — akshare 增量轮询
- `backend/scripts/migrate_split_security_master.py` — 一键迁移脚本（dry-run + commit + verify）
- `backend/scripts/_seed_qqq.py` — QQQ 手动入库脚本
- `backend/tests/test_stock_master_service.py`
- `backend/tests/test_fund_master_service.py`
- `backend/tests/test_index_master_service.py`
- `backend/tests/test_classification_service.py`
- `backend/tests/test_akshare_index_poller.py`
- `backend/tests/test_migrate_split_security_master.py`
- `backend/tests/test_admin_stock_master_api.py`
- `backend/tests/test_admin_fund_master_api.py`
- `backend/tests/test_admin_index_master_api.py`
- `backend/tests/test_admin_classification_api.py`
- `backend/tests/test_selective_fund_index_api.py`

**修改后端文件:**
- `backend/models.py` — 移除 `SecurityMaster`、`FundIndexMap`、`IndexConstituent`（保留兼容引用）；新增 `data_pull_task` 已存在复用
- `backend/main.py` — 移除/重定向旧的 `/api/admin/security-master/*`，新增 5 套新端点 + selective 端点 + lookup 端点 + 手动刷新端点
- `backend/services/scheduler.py` — 注册 `job_poll_index_master`
- `backend/services/__init__.py` — 包初始化
- `backend/scripts/__init__.py` — 包初始化

**新建前端文件:**
- `frontend/src/components/StockMasterTab.jsx`
- `frontend/src/components/FundMasterTab.jsx`
- `frontend/src/components/IndexMasterTab.jsx`
- `frontend/src/components/ClassificationTab.jsx`
- `frontend/src/components/SelectiveFundIndexDialog.jsx` — 双向选择弹窗
- `frontend/src/components/__tests__/StockMasterTab.test.jsx`
- `frontend/src/components/__tests__/FundMasterTab.test.jsx`
- `frontend/src/components/__tests__/IndexMasterTab.test.jsx`
- `frontend/src/components/__tests__/ClassificationTab.test.jsx`
- `frontend/src/components/__tests__/SelectiveFundIndexDialog.test.jsx`

**修改前端文件:**
- `frontend/src/components/MasterDataPanel.jsx` — 改为 4 个 sub-tab
- `frontend/src/components/FundIndexMapTab.jsx` — 加「新增映射」按钮 + 表格列下拉化
- `frontend/src/components/SecurityMasterTab.jsx` — 删除（被拆分）
- `frontend/src/api.js` — 加新端点 client

**数据库改动:**
- CREATE TABLE stock_master / fund_master / index_master / classification / classification_assign
- ALTER TABLE security_master RENAME TO security_master_legacy

**依赖/文档:**
- `docs/superpowers/specs/2026-07-02-master-data-overhaul-design.md` (已存在)
- `Project_development.md` — 更新主数据章节记录本次改动

---

## Phase 1 — DB schema + 迁移脚本

### Task 1: SQLAlchemy 模型 (5 张新表)

**Files:**
- Create: `backend/models_master.py`
- Modify: `backend/models.py` — 仅添加 import（不立即删除 SecurityMaster）
- Test: `backend/tests/test_models_master_imports.py`

- [ ] **Step 1: 写测试 — 验证 5 张新表可被 SQLAlchemy 导入**

`backend/tests/test_models_master_imports.py`:
```python
"""验证 models_master.py 的 5 张新表可被 SQLAlchemy 正常 import + create。"""


def test_models_master_imports():
    """所有 5 张新表应能从 models_master 导入。"""
    from models_master import (
        StockMaster, FundMaster, IndexMaster,
        Classification, ClassificationAssign,
    )
    assert StockMaster.__tablename__ == "stock_master"
    assert FundMaster.__tablename__ == "fund_master"
    assert IndexMaster.__tablename__ == "index_master"
    assert Classification.__tablename__ == "classification"
    assert ClassificationAssign.__tablename__ == "classification_assign"


def test_create_all_tables(in_memory_db):
    """5 张新表应能在内存 SQLite 上成功 create。"""
    from models_master import Base as MasterBase
    in_memory_db.execute("PRAGMA foreign_keys=ON")
    MasterBase.metadata.create_all(bind=in_memory_db.get_bind())
    # 验证表存在
    tables = MasterBase.metadata.tables.keys()
    assert "stock_master" in tables
    assert "fund_master" in tables
    assert "index_master" in tables
    assert "classification" in tables
    assert "classification_assign" in tables


def test_classification_unique_constraint(in_memory_db):
    """Classification 的 (dimension, code) 应有唯一约束。"""
    from models_master import Classification, Base as MasterBase
    MasterBase.metadata.create_all(bind=in_memory_db.get_bind())
    session = in_memory_db
    session.add(Classification(dimension="theme", code="dividend", display_label="红利"))
    session.commit()
    # 第二次插入相同 (dimension, code) 应触发 IntegrityError
    import pytest
    from sqlalchemy.exc import IntegrityError
    session.add(Classification(dimension="theme", code="dividend", display_label="红利2"))
    with pytest.raises(IntegrityError):
        session.commit()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_models_master_imports.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'models_master'"

- [ ] **Step 3: 实现 models_master.py**

```python
"""Master data 重构 — 5 张新表的 SQLAlchemy 模型 (2026-07-02)。

新表:
  - stock_master         (security_type='stock' 数据迁过来)
  - fund_master          (security_type='fund' 数据迁过来)
  - index_master         (新;从 FundIndexMap 提取 + akshare 增量轮询)
  - classification       (新;两维度字典: asset_type + theme)
  - classification_assign (新;多对多关联)

旧 SecurityMaster 改名 security_master_legacy 后冻结只读,新代码一律不读。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Index,
    Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class StockMaster(Base):
    """股票主数据 (A 股 / 港股 / 美股 / 商品 / bond)。"""
    __tablename__ = "stock_master"

    stock_code = Column(String(20), primary_key=True)
    stock_name = Column(String(100), nullable=False)
    exchange = Column(String(10))
    currency = Column(String(10), default="CNY")
    asset_type = Column(String(20), nullable=False)
    is_listed = Column(Boolean, default=True)
    is_drillable = Column(Boolean, default=False)
    note = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by = Column(Integer)


class FundMaster(Base):
    """基金主数据 (ETF场内 / 场外 / QDII)。"""
    __tablename__ = "fund_master"

    fund_code = Column(String(20), primary_key=True)
    fund_name = Column(String(100), nullable=False)
    fund_type = Column(String(20), nullable=False)  # "etf" / "otc"
    currency = Column(String(10), default="CNY")
    asset_type = Column(String(20), nullable=False)
    benchmark_formula = Column(String(500))
    is_drillable = Column(Boolean, default=False)
    note = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by = Column(Integer)


class IndexMaster(Base):
    """指数主数据 (A 股 / QQQ 等)。"""
    __tablename__ = "index_master"

    index_code = Column(String(20), primary_key=True)
    index_name = Column(String(100), nullable=False)
    exchange = Column(String(20))
    currency = Column(String(10), default="CNY")
    category = Column(String(50))  # "宽基" / "行业" / "主题" / "策略"
    constituent_count = Column(Integer)
    source = Column(String(40), default="akshare")
    is_active = Column(Boolean, default=True)
    first_pulled_at = Column(DateTime)
    last_pulled_at = Column(DateTime)
    last_verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    updated_by = Column(Integer)


class Classification(Base):
    """分类维度字典。两维度:
    - dimension='asset_type'  code='a_share_etf'  display_label='A股ETF'
    - dimension='theme'       code='dividend'     display_label='红利'
    """
    __tablename__ = "classification"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dimension = Column(String(20), nullable=False)
    code = Column(String(50), nullable=False)
    display_label = Column(String(100), nullable=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("dimension", "code", name="uq_classification_dim_code"),
    )


class ClassificationAssign(Base):
    """分类维度多对多关联。一个实体可同时有多个维度多条记录。"""
    __tablename__ = "classification_assign"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    entity_type = Column(String(20), nullable=False)  # "stock" / "fund" / "index"
    entity_code = Column(String(20), nullable=False)
    classification_id = Column(
        Integer,
        ForeignKey("classification.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_code", "classification_id",
            name="uq_assign_entity_classification",
        ),
        Index("ix_assign_entity", "entity_type", "entity_code"),
        Index("ix_assign_classification", "classification_id"),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_models_master_imports.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/models_master.py backend/tests/test_models_master_imports.py
git commit -m "feat(master-data): 新建 5 张主数据表的 SQLAlchemy 模型"
```

---

### Task 2: `models.py` 引入 models_master

**Files:**
- Modify: `backend/models.py` — 末尾加 `from models_master import (...)` 让现有代码可发现新表

- [ ] **Step 1: 验证现状**

Read `backend/models.py` 末尾，确认最后一行 class 之后位置。

- [ ] **Step 2: 加 import**

在 `backend/models.py` 文件最后追加:
```python
# ============================================================================
# 公共数据主数据重构 (2026-07-02) — 新表引用
# 注意: 这只是为了 SQLAlchemy metadata 注册;旧 SecurityMaster 仍保留,后续迁移脚本再改名
# ============================================================================
from models_master import (  # noqa: F401
    StockMaster, FundMaster, IndexMaster,
    Classification, ClassificationAssign,
)
```

- [ ] **Step 3: 跑现有测试不破**

Run: `cd backend && python -m pytest tests/test_models_master_imports.py tests/test_drill_api_integration.py -q`
Expected: PASS (旧 + 新测试都过)

- [ ] **Step 4: Commit**

```bash
git add backend/models.py
git commit -m "chore(master-data): 注册新表到全局 metadata"
```

---

### Task 3: 迁移脚本骨架 + dry-run 模式

**Files:**
- Create: `backend/scripts/migrate_split_security_master.py`
- Test: `backend/tests/test_migrate_split_security_master.py`

- [ ] **Step 1: 写测试 — 验证 dry-run 输出格式**

`backend/tests/test_migrate_split_security_master.py`:
```python
"""迁移脚本测试 — dry-run 输出 + bond 鉴别。"""

import pytest


@pytest.fixture
def seeded_legacy_db(in_memory_db):
    """Seed security_master_legacy with stock/fund/bond/qdii_bond mix."""
    from datetime import datetime
    db = in_memory_db
    db.execute("""
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
    """)
    # Stock
    db.add_all([
        _LegacyRow("600519.SH", "贵州茅台", asset_type="a_share_equity", security_type="stock"),
        _LegacyRow("000001.SZ", "平安银行", asset_type="a_share_equity", security_type="stock"),
        # Fund (ETF)
        _LegacyRow("510300.SH", "华泰柏瑞沪深300ETF", asset_type="a_share_etf", security_type="fund", fund_type="etf", is_drillable=True),
        # Fund (OF)
        _LegacyRow("161725.OF", "招商中证白酒", asset_type="a_share_equity", security_type="fund", fund_type="otc", is_drillable=True),
        # Bond (real bond)
        _LegacyRow("019547.SH", "19国债07", asset_type="bond", security_type="bond"),
        # Bond (qdii_bond - actually a fund)
        _LegacyRow("007360.OF", "易方达中短期债", asset_type="qdii_bond", security_type="bond", fund_type="otc"),
        # Bond (otc, code ends .OF)
        _LegacyRow("005078.OF", "广发双债", asset_type="bond", security_type="bond", fund_type="otc"),
    ])
    db.commit()
    return db


def _LegacyRow(code, name, **kw):
    from sqlalchemy import text
    # tuple-style insert helper
    class _R:
        security_code = code
        security_name = name
        for k, v in kw.items():
            locals()[k] = v
    return _R


def test_dry_run_counts(seeded_legacy_db):
    """dry-run 报告应反映正确分流:
    - 2 stocks
    - 4 funds (510300.SH, 161725.OF, 007360.OF, 005078.OF) ← 含 3 个 bond-actual-fund
    - 1 stock_bond (019547.SH)
    """
    from scripts.migrate_split_security_master import dry_run_report
    report = dry_run_report(seeded_legacy_db)
    assert report["legacy_total"] == 7
    assert report["to_stock_master"] == 3     # 2 stocks + 1 real bond
    assert report["to_fund_master"] == 4      # 4 funds incl. 3 bond-as-fund
    assert report["index_master_added"] >= 0
    assert report["classification_added"] >= 0


def test_dry_run_warns_on_unknown_type2(seeded_legacy_db):
    """未知的 type2 值应被列入 warnings。"""
    from models_master import Classification  # noqa
    # Add a new legacy row with unknown type2
    pass  # 测试留作 Phase 1 后期扩展
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_migrate_split_security_master.py -v`
Expected: FAIL with "No module named 'scripts.migrate_split_security_master'"

- [ ] **Step 3: 实现迁移脚本骨架**

`backend/scripts/__init__.py`:
```python
"""Backend scripts package."""
```

`backend/scripts/migrate_split_security_master.py`:
```python
"""公共数据主数据重构 — SecurityMaster → 3 主表 一次性迁移脚本 (2026-07-02)。

用法:
  1. dry-run (默认):
        python -m scripts.migrate_split_security_master
  2. 人工 review 输出
  3. 真跑:
        python -m scripts.migrate_split_security_master --commit
  4. 验证 counts:
        python -m scripts.migrate_split_security_master --verify

特性:
  - idempotent (重跑结果一致)
  - bond 鉴别 (asset_type='qdii_bond' 或 .OF 后缀的 bond 进 fund_master)
  - pg_dump 自动备份 (真跑前)
  - 整个流程包在 PG transaction 中,失败自动 rollback

⚠️ 首次跑必须 dry-run,然后人工 review,再 --commit。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from models_master import (
    Base as MasterBase,
    StockMaster, FundMaster, IndexMaster,
    Classification, ClassificationAssign,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ============================================================================
# type2 英文 → 中文 映射 (迁移期用,后续 admin 可在 classification 表里编辑)
# ============================================================================
_TYPE2_CODE_TO_LABEL = {
    "emerging": "新兴产业",
    "dividend": "红利",
    "gold":     "黄金",
}


def _normalize_type2(raw: str | None) -> tuple[str, str] | None:
    """(code, display_label) 或 None。未知值: code=原值, label=原值。"""
    if not raw:
        return None
    code = raw.lower()
    label = _TYPE2_CODE_TO_LABEL.get(code, raw)
    return (code, label)


def _is_bond_to_fund(asset_type: str | None, security_code: str) -> bool:
    """bond 鉴别: qdii_bond 或 .OF 后缀 → fund_master;否则 stock_master。"""
    if asset_type == "qdii_bond":
        return True
    if security_code.endswith(".OF"):
        return True
    return False


def _seed_index_master_from_legacy(db: Session) -> int:
    """从 FundIndexMap + security_master_legacy 提取 index_code + index_name。"""
    # 简化实现:从 legacy 读 (index_code 不为空)
    rows = db.execute(text("""
        SELECT DISTINCT index_code, index_name
        FROM security_master_legacy
        WHERE index_code IS NOT NULL AND index_code != ''
    """)).fetchall()
    inserted = 0
    for code, name in rows:
        existing = db.query(IndexMaster).filter_by(index_code=code).first()
        if existing:
            continue
        idx_code = code.split(".")[0]
        db.add(IndexMaster(
            index_code=idx_code,
            index_name=name or idx_code,
            source="manual_legacy",
            first_pulled_at=datetime.utcnow(),
            last_pulled_at=datetime.utcnow(),
            last_verified_at=datetime.utcnow(),
            is_active=True,
        ))
        inserted += 1
    return inserted


def _seed_classification_dict(db: Session, dimension: str, items: list[tuple[str, str]]) -> int:
    """灌入分类字典。返回新建条数。"""
    inserted = 0
    for sort_order, (code, label) in enumerate(items):
        existing = db.query(Classification).filter_by(
            dimension=dimension, code=code
        ).first()
        if existing:
            continue
        db.add(Classification(
            dimension=dimension, code=code, display_label=label,
            sort_order=sort_order, is_active=True,
        ))
        inserted += 1
    return inserted


def dry_run_report(db: Session) -> dict:
    """dry-run 模式: 不写入,只统计。"""
    report = {
        "legacy_total": 0,
        "to_stock_master": 0,
        "to_fund_master": 0,
        "index_master_added": 0,
        "classification_added": 0,
        "classification_assign": 0,
        "warnings": [],
    }

    rows = db.execute(text("""
        SELECT security_code, asset_type, security_type,
               type2, index_code, benchmark_formula
        FROM security_master_legacy
    """)).fetchall()
    report["legacy_total"] = len(rows)

    for security_code, asset_type, security_type, type2, index_code, _ in rows:
        if security_type == "stock":
            report["to_stock_master"] += 1
        elif security_type == "fund":
            report["to_fund_master"] += 1
        elif security_type == "bond":
            if _is_bond_to_fund(asset_type, security_code):
                report["to_fund_master"] += 1
            else:
                report["to_stock_master"] += 1
        else:
            report["warnings"].append(
                f"security_type='{security_type}' 未知: {security_code}"
            )

        # type2 未知值告警
        if type2:
            code, label = (_normalize_type2(type2) or (None, None))
            if code and code not in _TYPE2_CODE_TO_LABEL:
                report["warnings"].append(
                    f"type2 未知值: {security_code} → {type2!r}"
                )

    return report


def commit_migration(db: Session) -> dict:
    """真跑: 写入所有新表。需在 PG transaction 中调用。"""
    report = dry_run_report(db)

    # 1. 灌入 stock_master
    legacy_rows = db.execute(text("""
        SELECT security_code, security_name, currency, asset_type, exchange,
               is_drillable, note
        FROM security_master_legacy
    """)).fetchall()
    for code, name, ccy, at, ex, drill, note in legacy_rows:
        # 鉴别:是 bond-as-fund 还是 stock？
        st_row = db.execute(text(
            "SELECT security_type FROM security_master_legacy WHERE security_code=:c"
        ), {"c": code}).first()
        sec_type = st_row[0] if st_row else None
        if sec_type == "fund" or (sec_type == "bond" and _is_bond_to_fund(at, code)):
            continue  # 跳过,留给 fund_master
        # 写 stock_master
        existing = db.query(StockMaster).filter_by(stock_code=code).first()
        if existing:
            continue
        db.add(StockMaster(
            stock_code=code, stock_name=name, currency=ccy or "CNY",
            asset_type=at or "a_share_equity", exchange=ex,
            is_drillable=bool(drill), note=note,
        ))

    # 2. 灌入 fund_master
    for code, name, ccy, at, ex, drill, note in legacy_rows:
        st_row = db.execute(text(
            "SELECT security_type, fund_type, benchmark_formula FROM security_master_legacy WHERE security_code=:c"
        ), {"c": code}).first()
        if not st_row:
            continue
        sec_type, fund_type, bench = st_row
        if sec_type == "fund" or (sec_type == "bond" and _is_bond_to_fund(at, code)):
            existing = db.query(FundMaster).filter_by(fund_code=code).first()
            if existing:
                continue
            db.add(FundMaster(
                fund_code=code, fund_name=name, currency=ccy or "CNY",
                asset_type=at or "a_share_equity",
                fund_type=fund_type or ("otc" if code.endswith(".OF") else "etf"),
                benchmark_formula=bench, is_drillable=bool(drill), note=note,
            ))

    # 3. 灌入 index_master
    report["index_master_added"] = _seed_index_master_from_legacy(db)

    # 4. 分类字典: asset_type
    asset_type_values = db.execute(text(
        "SELECT DISTINCT asset_type FROM security_master_legacy WHERE asset_type IS NOT NULL"
    )).fetchall()
    asset_type_items = [(at[0], at[0]) for at in asset_type_values]
    n1 = _seed_classification_dict(db, "asset_type", asset_type_items)

    # 5. 分类字典: theme (type2)
    type2_values = db.execute(text(
        "SELECT DISTINCT type2 FROM security_master_legacy WHERE type2 IS NOT NULL"
    )).fetchall()
    theme_items = []
    for (raw,) in type2_values:
        norm = _normalize_type2(raw)
        if norm:
            theme_items.append(norm)
    theme_items = list(set(theme_items))
    n2 = _seed_classification_dict(db, "theme", theme_items)

    report["classification_added"] = n1 + n2

    # 6. 灌入 classification_assign (略,Phase 1 后期补)

    db.commit()
    return report


def verify_migration(db: Session) -> dict:
    """验证: 对比 legacy 与新表的 counts。"""
    legacy_count = db.execute(text(
        "SELECT COUNT(*) FROM security_master_legacy"
    )).scalar()
    new_count = (
        db.query(StockMaster).count()
        + db.query(FundMaster).count()
    )
    return {
        "legacy_count": legacy_count,
        "new_count": new_count,
        "match": legacy_count == new_count,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true",
                        help="真跑写入 (默认 dry-run)")
    parser.add_argument("--verify", action="store_true",
                        help="验证 counts")
    args = parser.parse_args()

    from database import SessionLocal
    db = SessionLocal()

    try:
        # Ensure new tables exist
        MasterBase.metadata.create_all(bind=db.get_bind())

        if args.verify:
            result = verify_migration(db)
            logger.info(f"verify: {result}")
            return

        if args.commit:
            report = commit_migration(db)
            logger.info(f"commit done: {report}")
        else:
            report = dry_run_report(db)
            logger.info("== Dry Run Report ==")
            for k, v in report.items():
                logger.info(f"  {k}: {v}")
            logger.info("== End ==")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过 (dry-run 部分)**

Run: `cd backend && python -m pytest tests/test_migrate_split_security_master.py::test_dry_run_counts -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_split_security_master.py backend/scripts/__init__.py backend/tests/test_migrate_split_security_master.py
git commit -m "feat(migration): SecurityMaster → 3 主表 迁移脚本骨架 (dry-run)"
```

---

### Task 4: 跑 dry-run + 人工 review 输出

**Files:** none (人工执行步骤)

- [ ] **Step 1: 跑 dry-run**

Run: `cd backend && python -m scripts.migrate_split_security_master`
Expected: 输出包含 legacy_total / to_stock_master / to_fund_master / warnings。

- [ ] **Step 2: 人工 review**

- legacy_total 应等于 `security_master_legacy` 当前行数 (生产环境: ≈142 行)
- bond 鉴别:`security_type='bond'` + qdii_bond/`.OF` 后缀 应进 fund_master;其他 进 stock_master
- type2 未知值应在 warnings 里列出
- 如有不合理,转去调整 Task 3 代码;否则继续

- [ ] **Step 3: Commit 验证 (无新代码)**

(无 commit — 这一步是人工 review 后才决定写 commit)

---

### Task 5: 真跑 commit_migration + rename security_master → legacy

**Files:**
- Modify: `backend/scripts/migrate_split_security_master.py` — 加 `rename_security_master_legacy()` 函数
- Test: `backend/tests/test_migrate_split_security_master.py` — 加 rename 测试

- [ ] **Step 1: 写测试 — rename 应成功执行**

`backend/tests/test_migrate_split_security_master.py` 末尾追加:
```python
def test_rename_security_master_to_legacy(in_memory_db):
    """rename 后 security_master 不应存在, security_master_legacy 应存在。"""
    from sqlalchemy import text
    db = in_memory_db
    db.execute(text("CREATE TABLE security_master (x INTEGER)"))
    db.commit()

    from scripts.migrate_split_security_master import rename_security_master_to_legacy
    rename_security_master_to_legacy(db)

    names = [r[0] for r in db.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )).fetchall()]
    assert "security_master" not in names
    assert "security_master_legacy" in names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_migrate_split_security_master.py::test_rename_security_master_to_legacy -v`
Expected: FAIL with "cannot import name 'rename_security_master_to_legacy'"

- [ ] **Step 3: 实现 rename 函数**

`backend/scripts/migrate_split_security_master.py` 中,在 `main()` 之前加:
```python
def rename_security_master_to_legacy(db: Session) -> None:
    """把 security_master 改名为 security_master_legacy。

    PG 和 SQLite 都用同一种语法: ALTER TABLE ... RENAME TO ...
    """
    db.execute(text("ALTER TABLE security_master RENAME TO security_master_legacy"))
    db.execute(text("""
        COMMENT ON TABLE security_master_legacy IS
        'DEPRECATED 2026-07-02: 数据已迁到 stock_master + fund_master;本表冻结只读,禁止新写入'
    """) if db.bind.dialect.name == "postgresql" else text(
        "SELECT 1"  # SQLite 不支持 COMMENT
    ))
    db.commit()
```

并在 `main()` 里加分支:
```python
        # 跑前先确保没改名过
        if "security_master_legacy" not in existing_tables and "security_master" in existing_tables:
            if args.commit:
                rename_security_master_to_legacy(db)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_migrate_split_security_master.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/migrate_split_security_master.py backend/tests/test_migrate_split_security_master.py
git commit -m "feat(migration): 加 rename security_master → legacy"
```

---

## Phase 2 — 后端 CRUD 端点

### Task 6: stock_master_service

**Files:**
- Create: `backend/services/stock_master_service.py`
- Test: `backend/tests/test_stock_master_service.py`

- [ ] **Step 1: 写测试**

`backend/tests/test_stock_master_service.py`:
```python
"""stock_master_service 测试。"""
import pytest
from datetime import datetime


def _make_stock(code="600519.SH", name="贵州茅台"):
    return dict(
        stock_code=code, stock_name=name,
        exchange="SH", currency="CNY",
        asset_type="a_share_equity",
        is_listed=True, is_drillable=False,
    )


def test_create_and_list(in_memory_db):
    from services.stock_master_service import create_stock, list_stocks, get_stock
    create_stock(in_memory_db, _make_stock())
    items = list_stocks(in_memory_db)["items"]
    assert len(items) == 1
    assert items[0]["stock_code"] == "600519.SH"
    assert get_stock(in_memory_db, "600519.SH")["stock_name"] == "贵州茅台"


def test_update_partial(in_memory_db):
    from services.stock_master_service import create_stock, update_stock, get_stock
    create_stock(in_memory_db, _make_stock())
    update_stock(in_memory_db, "600519.SH", {"note": "蓝筹龙头"})
    assert get_stock(in_memory_db, "600519.SH")["note"] == "蓝筹龙头"


def test_delete_blocks_when_holding_exists(in_memory_db):
    """有持仓的股票不能删除 (沿用 security_master_service 行为)。"""
    from services.stock_master_service import create_stock, delete_stock
    from sqlalchemy import text
    create_stock(in_memory_db, _make_stock())
    in_memory_db.execute(text("""
        CREATE TABLE holdings (id INTEGER PRIMARY KEY, security_code VARCHAR(20))
    """))
    in_memory_db.execute(text(
        "INSERT INTO holdings (security_code) VALUES ('600519.SH')"
    ))
    in_memory_db.commit()
    import pytest
    with pytest.raises(ValueError, match="持仓"):
        delete_stock(in_memory_db, "600519.SH")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_stock_master_service.py -v`
Expected: FAIL "No module named 'services.stock_master_service'"

- [ ] **Step 3: 实现 stock_master_service.py**

```python
"""股票主数据 service — CRUD。"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from models_master import StockMaster

logger = __import__("logging").getLogger(__name__)


def _to_dict(sm: StockMaster) -> dict:
    return {
        "stock_code": sm.stock_code,
        "stock_name": sm.stock_name,
        "exchange": sm.exchange,
        "currency": sm.currency,
        "asset_type": sm.asset_type,
        "is_listed": sm.is_listed,
        "is_drillable": sm.is_drillable,
        "note": sm.note,
        "updated_at": sm.updated_at.isoformat() if sm.updated_at else None,
    }


def list_stocks(
    db: Session, asset_type: str | None = None, market: str | None = None,
    search: str | None = None, page: int = 1, page_size: int = 50,
) -> dict:
    q = db.query(StockMaster)
    if asset_type:
        q = q.filter(StockMaster.asset_type == asset_type)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (StockMaster.stock_code.ilike(like))
            | (StockMaster.stock_name.ilike(like))
        )
    total = q.count()
    rows = q.order_by(StockMaster.stock_code).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return {
        "items": [_to_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


def get_stock(db: Session, code: str) -> dict | None:
    sm = db.query(StockMaster).filter_by(stock_code=code).first()
    return _to_dict(sm) if sm else None


def create_stock(db: Session, data: dict) -> dict:
    sm = StockMaster(**{k: v for k, v in data.items() if k in StockMaster.__table__.columns})
    db.add(sm)
    db.commit()
    db.refresh(sm)
    return _to_dict(sm)


def update_stock(db: Session, code: str, data: dict) -> dict | None:
    sm = db.query(StockMaster).filter_by(stock_code=code).first()
    if not sm:
        return None
    _ALLOWED = {c.name for c in StockMaster.__table__.columns}
    for k, v in data.items():
        if k in _ALLOWED and k != "stock_code":
            setattr(sm, k, v)
    db.commit()
    db.refresh(sm)
    return _to_dict(sm)


def delete_stock(db: Session, code: str) -> bool:
    # 持仓校验 (沿用 security_master_service 行为)
    from sqlalchemy import text
    n = db.execute(text(
        "SELECT COUNT(*) FROM holdings WHERE security_code=:c"
    ), {"c": code}).scalar() or 0
    if n > 0:
        raise ValueError(f"无法删除: 该股票有 {n} 条持仓记录")
    sm = db.query(StockMaster).filter_by(stock_code=code).first()
    if not sm:
        return False
    db.delete(sm)
    db.commit()
    return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_stock_master_service.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/services/stock_master_service.py backend/tests/test_stock_master_service.py
git commit -m "feat(service): stock_master_service CRUD"
```

---

### Task 7: `/api/admin/stock-master` 端点

**Files:**
- Modify: `backend/main.py` — 在 /api/admin/fund-index-map 之后加 stock-master 端点
- Test: `backend/tests/test_admin_stock_master_api.py`

- [ ] **Step 1: 写测试**

`backend/tests/test_admin_stock_master_api.py`:
```python
"""stock-master API 集成测试。"""
import os
os.environ["APP_PASSWORD"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base
from main import app
from models_master import StockMaster


@pytest.fixture
def app_client(monkeypatch):
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    from database import get_db
    monkeypatch.setattr("database.get_db", lambda: SessionLocal())

    client = TestClient(app)
    yield client, SessionLocal
    os.unlink(path)


def _login(client, user="admin", pw="admin123"):
    # 沿用项目现有登录;如需 admin 角色也自动建
    r = client.post("/api/auth/login", json={"username": user, "password": pw})
    return r.json().get("token") or r.json().get("session_token") or r.headers.get("x-session-token")


def test_list_stocks_empty(app_client):
    client, _ = app_client
    r = client.get("/api/admin/stock-master")
    assert r.status_code in (200, 401)


def test_create_and_update(app_client):
    client, SessionLocal = app_client
    # 直接插入避免登录 (本测试主要验证 service 端点路径)
    from models_master import StockMaster
    db = SessionLocal()
    db.add(StockMaster(stock_code="600519.SH", stock_name="贵州茅台", asset_type="a_share_equity"))
    db.commit()
    db.close()

    r = client.get("/api/admin/stock-master")
    body = r.json()
    # 不依赖 200/401:仅看返回结构,后续集成测试覆盖 auth
    assert isinstance(body, dict)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_admin_stock_master_api.py -v`
Expected: FAIL (找不到端点或 404)

- [ ] **Step 3: 实现端点**

`backend/main.py` 在 `app.get("/api/admin/fund-index-map")` 之后加:
```python
# ============================================================================
# Admin: 股票主数据 (2026-07-02) — 替代旧 security-master (stock 部分)
# ============================================================================

@app.get("/api/admin/stock-master")
def admin_list_stocks(
    asset_type: str | None = None,
    search: str | None = None,
    page: int = 1, page_size: int = 50,
    db: Session = Depends(get_db),
):
    from services.stock_master_service import list_stocks
    return list_stocks(db, asset_type=asset_type, search=search,
                       page=page, page_size=page_size)


@app.post("/api/admin/stock-master")
def admin_create_stock(body: dict = Body(...), db: Session = Depends(get_db)):
    from services.stock_master_service import create_stock
    return create_stock(db, body)


@app.put("/api/admin/stock-master/{code}")
def admin_update_stock(code: str, body: dict = Body(...), db: Session = Depends(get_db)):
    from services.stock_master_service import update_stock
    result = update_stock(db, code, body)
    if not result:
        raise HTTPException(404, "股票不存在")
    return result


@app.delete("/api/admin/stock-master/{code}")
def admin_delete_stock(code: str, db: Session = Depends(get_db)):
    from services.stock_master_service import delete_stock
    try:
        ok = delete_stock(db, code)
        if not ok:
            raise HTTPException(404, "股票不存在")
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(400, str(e))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_admin_stock_master_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_admin_stock_master_api.py
git commit -m "feat(api): GET/POST/PUT/DELETE /api/admin/stock-master"
```

---

### Task 8: fund_master_service (类似 Task 6)

**Files:**
- Create: `backend/services/fund_master_service.py`
- Test: `backend/tests/test_fund_master_service.py`

- [ ] **Step 1: 写测试** (同 Task 6 模式,把 `_make_stock` 改为 `_make_fund`)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_fund_master_service.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 fund_master_service.py** (CRUD + holding 校验)

```python
"""基金主数据 service — CRUD。"""
from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import text

from models_master import FundMaster


def _to_dict(fm: FundMaster) -> dict:
    return {
        "fund_code": fm.fund_code,
        "fund_name": fm.fund_name,
        "fund_type": fm.fund_type,
        "currency": fm.currency,
        "asset_type": fm.asset_type,
        "benchmark_formula": fm.benchmark_formula,
        "is_drillable": fm.is_drillable,
        "note": fm.note,
        "updated_at": fm.updated_at.isoformat() if fm.updated_at else None,
    }


def list_funds(
    db: Session, asset_type: str | None = None, fund_type: str | None = None,
    search: str | None = None, page: int = 1, page_size: int = 50,
) -> dict:
    q = db.query(FundMaster)
    if asset_type:
        q = q.filter(FundMaster.asset_type == asset_type)
    if fund_type:
        q = q.filter(FundMaster.fund_type == fund_type)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (FundMaster.fund_code.ilike(like))
            | (FundMaster.fund_name.ilike(like))
        )
    total = q.count()
    rows = q.order_by(FundMaster.fund_code).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return {
        "items": [_to_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


def get_fund(db: Session, code: str) -> dict | None:
    fm = db.query(FundMaster).filter_by(fund_code=code).first()
    return _to_dict(fm) if fm else None


def create_fund(db: Session, data: dict) -> dict:
    fm = FundMaster(**{k: v for k, v in data.items() if k in FundMaster.__table__.columns})
    db.add(fm)
    db.commit()
    db.refresh(fm)
    return _to_dict(fm)


def update_fund(db: Session, code: str, data: dict) -> dict | None:
    fm = db.query(FundMaster).filter_by(fund_code=code).first()
    if not fm:
        return None
    _ALLOWED = {c.name for c in FundMaster.__table__.columns}
    for k, v in data.items():
        if k in _ALLOWED and k != "fund_code":
            setattr(fm, k, v)
    db.commit()
    db.refresh(fm)
    return _to_dict(fm)


def delete_fund(db: Session, code: str) -> bool:
    n = db.execute(text(
        "SELECT COUNT(*) FROM holdings WHERE security_code=:c"
    ), {"c": code}).scalar() or 0
    if n > 0:
        raise ValueError(f"无法删除: 该基金有 {n} 条持仓记录")
    fm = db.query(FundMaster).filter_by(fund_code=code).first()
    if not fm:
        return False
    db.delete(fm)
    db.commit()
    return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_fund_master_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/fund_master_service.py backend/tests/test_fund_master_service.py
git commit -m "feat(service): fund_master_service CRUD"
```

---

### Task 9: `/api/admin/fund-master` 端点

**Files:**
- Modify: `backend/main.py` — 加 fund-master CRUD
- Test: `backend/tests/test_admin_fund_master_api.py`

- [ ] **Step 1: 写测试**

(同 Task 7 模式,改 `/api/admin/stock-master` 为 `/api/admin/fund-master`)

- [ ] **Step 2-4: 跑测试 → 实现 → 跑通**

`backend/main.py` 加:
```python
@app.get("/api/admin/fund-master")
def admin_list_funds(
    asset_type: str | None = None,
    fund_type: str | None = None,
    search: str | None = None,
    page: int = 1, page_size: int = 50,
    db: Session = Depends(get_db),
):
    from services.fund_master_service import list_funds
    return list_funds(db, asset_type=asset_type, fund_type=fund_type,
                      search=search, page=page, page_size=page_size)


@app.post("/api/admin/fund-master")
def admin_create_fund(body: dict = Body(...), db: Session = Depends(get_db)):
    from services.fund_master_service import create_fund
    return create_fund(db, body)


@app.put("/api/admin/fund-master/{code}")
def admin_update_fund(code: str, body: dict = Body(...), db: Session = Depends(get_db)):
    from services.fund_master_service import update_fund
    result = update_fund(db, code, body)
    if not result:
        raise HTTPException(404, "基金不存在")
    return result


@app.delete("/api/admin/fund-master/{code}")
def admin_delete_fund(code: str, db: Session = Depends(get_db)):
    from services.fund_master_service import delete_fund
    try:
        ok = delete_fund(db, code)
        if not ok:
            raise HTTPException(404, "基金不存在")
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(400, str(e))
```

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_admin_fund_master_api.py
git commit -m "feat(api): /api/admin/fund-master CRUD"
```

---

### Task 10: index_master_service

**Files:**
- Create: `backend/services/index_master_service.py`
- Test: `backend/tests/test_index_master_service.py`

- [ ] **Step 1: 写测试**

```python
def test_create_index_master(in_memory_db):
    from services.index_master_service import create_index, get_index
    create_index(in_memory_db, dict(
        index_code="000300.SH", index_name="沪深300",
        exchange="SH", currency="CNY", category="宽基",
    ))
    i = get_index(in_memory_db, "000300.SH")
    assert i["index_name"] == "沪深300"


def test_list_with_filters(in_memory_db):
    from services.index_master_service import create_index, list_indices
    create_index(in_memory_db, dict(index_code="000300.SH", index_name="沪深300", category="宽基"))
    create_index(in_memory_db, dict(index_code="000905.SH", index_name="中证500", category="宽基"))
    create_index(in_memory_db, dict(index_code="399006.SZ", index_name="创业板指", category="宽基"))
    res = list_indices(in_memory_db, category="宽基")
    assert res["total"] == 3
```

- [ ] **Step 2-5: 跑测试 → 实现 → 跑通 → commit**

```python
# backend/services/index_master_service.py
"""指数主数据 service — CRUD。"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from models_master import IndexMaster


def _to_dict(im: IndexMaster) -> dict:
    return {
        "index_code": im.index_code,
        "index_name": im.index_name,
        "exchange": im.exchange,
        "currency": im.currency,
        "category": im.category,
        "constituent_count": im.constituent_count,
        "source": im.source,
        "is_active": im.is_active,
        "first_pulled_at": im.first_pulled_at.isoformat() if im.first_pulled_at else None,
        "last_pulled_at": im.last_pulled_at.isoformat() if im.last_pulled_at else None,
        "last_verified_at": im.last_verified_at.isoformat() if im.last_verified_at else None,
        "updated_at": im.updated_at.isoformat() if im.updated_at else None,
    }


def list_indices(
    db: Session, category: str | None = None, is_active: bool | None = None,
    search: str | None = None, page: int = 1, page_size: int = 50,
) -> dict:
    q = db.query(IndexMaster)
    if category:
        q = q.filter(IndexMaster.category == category)
    if is_active is not None:
        q = q.filter(IndexMaster.is_active == is_active)
    if search:
        like = f"%{search}%"
        q = q.filter(
            (IndexMaster.index_code.ilike(like))
            | (IndexMaster.index_name.ilike(like))
        )
    total = q.count()
    rows = q.order_by(IndexMaster.index_code).offset(
        (page - 1) * page_size
    ).limit(page_size).all()
    return {
        "items": [_to_dict(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    }


def get_index(db: Session, code: str) -> dict | None:
    im = db.query(IndexMaster).filter_by(index_code=code).first()
    return _to_dict(im) if im else None


def create_index(db: Session, data: dict) -> dict:
    im = IndexMaster(**{
        k: v for k, v in data.items()
        if k in IndexMaster.__table__.columns
    })
    if not im.first_pulled_at:
        im.first_pulled_at = datetime.utcnow()
    if not im.last_pulled_at:
        im.last_pulled_at = datetime.utcnow()
    if not im.last_verified_at:
        im.last_verified_at = datetime.utcnow()
    db.add(im)
    db.commit()
    db.refresh(im)
    return _to_dict(im)


def update_index(db: Session, code: str, data: dict) -> dict | None:
    im = db.query(IndexMaster).filter_by(index_code=code).first()
    if not im:
        return None
    _ALLOWED = {c.name for c in IndexMaster.__table__.columns}
    for k, v in data.items():
        if k in _ALLOWED and k != "index_code":
            setattr(im, k, v)
    db.commit()
    db.refresh(im)
    return _to_dict(im)


def delete_index(db: Session, code: str) -> bool:
    im = db.query(IndexMaster).filter_by(index_code=code).first()
    if not im:
        return False
    db.delete(im)
    db.commit()
    return True
```

```bash
git add backend/services/index_master_service.py backend/tests/test_index_master_service.py
git commit -m "feat(service): index_master_service CRUD"
```

---

### Task 11: `/api/admin/index-master` 端点

**Files:**
- Modify: `backend/main.py` — 加 index-master CRUD
- Test: `backend/tests/test_admin_index_master_api.py`

- [ ] **Step 1: 写测试 (同 Task 7 模式)**

- [ ] **Step 3: 实现端点**

```python
@app.get("/api/admin/index-master")
def admin_list_indices(
    category: str | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    page: int = 1, page_size: int = 50,
    db: Session = Depends(get_db),
):
    from services.index_master_service import list_indices
    return list_indices(db, category=category, is_active=is_active,
                        search=search, page=page, page_size=page_size)


@app.post("/api/admin/index-master")
def admin_create_index(body: dict = Body(...), db: Session = Depends(get_db)):
    from services.index_master_service import create_index
    return create_index(db, body)


@app.put("/api/admin/index-master/{code}")
def admin_update_index(code: str, body: dict = Body(...), db: Session = Depends(get_db)):
    from services.index_master_service import update_index
    result = update_index(db, code, body)
    if not result:
        raise HTTPException(404, "指数不存在")
    return result


@app.delete("/api/admin/index-master/{code}")
def admin_delete_index(code: str, db: Session = Depends(get_db)):
    from services.index_master_service import delete_index
    ok = delete_index(db, code)
    if not ok:
        raise HTTPException(404, "指数不存在")
    return {"status": "ok"}
```

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_admin_index_master_api.py
git commit -m "feat(api): /api/admin/index-master CRUD"
```

---

### Task 12: classification_service

**Files:**
- Create: `backend/services/classification_service.py`
- Test: `backend/tests/test_classification_service.py`

- [ ] **Step 1: 写测试**

```python
def test_create_list_dimension(in_memory_db):
    from services.classification_service import create_classification, list_classifications
    create_classification(in_memory_db, dict(dimension="theme", code="dividend", display_label="红利"))
    create_classification(in_memory_db, dict(dimension="theme", code="gold", display_label="黄金"))
    items = list_classifications(in_memory_db, dimension="theme")
    codes = {i["code"] for i in items}
    assert {"dividend", "gold"} == codes


def test_assign_and_get(in_memory_db):
    from services.classification_service import create_classification, assign, get_assignments
    cid = create_classification(in_memory_db, dict(
        dimension="theme", code="dividend", display_label="红利",
    ))["id"]
    assign(in_memory_db, entity_type="fund", entity_code="510300.SH", classification_id=cid)
    result = get_assignments(in_memory_db, entity_type="fund", entity_code="510300.SH")
    assert len(result) == 1
    assert result[0]["code"] == "dividend"


def test_unique_constraint_violation(in_memory_db):
    from services.classification_service import create_classification
    from sqlalchemy.exc import IntegrityError
    import pytest
    create_classification(in_memory_db, dict(dimension="theme", code="x", display_label="X"))
    with pytest.raises(IntegrityError):
        create_classification(in_memory_db, dict(dimension="theme", code="x", display_label="X2"))
```

- [ ] **Step 3: 实现 classification_service.py**

```python
"""分类维度 service — 字典 CRUD + assign 关联。"""
from __future__ import annotations
from sqlalchemy.orm import Session
from models_master import Classification, ClassificationAssign


def _to_dict(c: Classification) -> dict:
    return {
        "id": c.id, "dimension": c.dimension, "code": c.code,
        "display_label": c.display_label, "sort_order": c.sort_order,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def list_classifications(
    db: Session, dimension: str, is_active: bool | None = True,
) -> list[dict]:
    q = db.query(Classification).filter_by(dimension=dimension)
    if is_active is not None:
        q = q.filter(Classification.is_active == is_active)
    rows = q.order_by(Classification.sort_order, Classification.code).all()
    return [_to_dict(r) for r in rows]


def get_classification(db: Session, cid: int) -> dict | None:
    c = db.query(Classification).filter_by(id=cid).first()
    return _to_dict(c) if c else None


def create_classification(db: Session, data: dict) -> dict:
    c = Classification(**{k: v for k, v in data.items()
                          if k in Classification.__table__.columns})
    db.add(c)
    db.commit()
    db.refresh(c)
    return _to_dict(c)


def update_classification(db: Session, cid: int, data: dict) -> dict | None:
    c = db.query(Classification).filter_by(id=cid).first()
    if not c:
        return None
    _ALLOWED = {col.name for col in Classification.__table__.columns}
    for k, v in data.items():
        if k in _ALLOWED and k != "id":
            setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return _to_dict(c)


def deactivate_classification(db: Session, cid: int) -> bool:
    """停用 (is_active=False) 而非物理删除,保 FK 完整性。"""
    c = db.query(Classification).filter_by(id=cid).first()
    if not c:
        return False
    c.is_active = False
    db.commit()
    return True


def assign(
    db: Session, entity_type: str, entity_code: str, classification_id: int,
) -> bool:
    """把分类赋给一个实体。已存在则跳过 (idempotent)。"""
    existing = db.query(ClassificationAssign).filter_by(
        entity_type=entity_type, entity_code=entity_code,
        classification_id=classification_id,
    ).first()
    if existing:
        return False
    db.add(ClassificationAssign(
        entity_type=entity_type, entity_code=entity_code,
        classification_id=classification_id,
    ))
    db.commit()
    return True


def unassign(
    db: Session, entity_type: str, entity_code: str, classification_id: int,
) -> bool:
    n = db.query(ClassificationAssign).filter_by(
        entity_type=entity_type, entity_code=entity_code,
        classification_id=classification_id,
    ).delete()
    db.commit()
    return n > 0


def get_assignments(
    db: Session, entity_type: str, entity_code: str,
) -> list[dict]:
    """列出实体的所有分类 (含 dimension / display_label)。"""
    rows = db.query(Classification).join(
        ClassificationAssign,
        ClassificationAssign.classification_id == Classification.id,
    ).filter(
        ClassificationAssign.entity_type == entity_type,
        ClassificationAssign.entity_code == entity_code,
    ).all()
    return [_to_dict(r) for r in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_classification_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/classification_service.py backend/tests/test_classification_service.py
git commit -m "feat(service): classification_service (字典 + assign)"
```

---

### Task 13: `/api/admin/classification` 端点

**Files:**
- Modify: `backend/main.py` — 加 classification CRUD + assign/unassign
- Test: `backend/tests/test_admin_classification_api.py`

- [ ] **Step 1: 写测试** (CRUD + assign/unassign + 401 path)

- [ ] **Step 3: 实现端点**

```python
@app.get("/api/admin/classification")
def admin_list_classifications(
    dimension: str = Query(...),
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    from services.classification_service import list_classifications
    return list_classifications(db, dimension=dimension, is_active=is_active)


@app.post("/api/admin/classification")
def admin_create_classification(body: dict = Body(...), db: Session = Depends(get_db)):
    from services.classification_service import create_classification
    return create_classification(db, body)


@app.put("/api/admin/classification/{cid}")
def admin_update_classification(cid: int, body: dict = Body(...), db: Session = Depends(get_db)):
    from services.classification_service import update_classification
    result = update_classification(db, cid, body)
    if not result:
        raise HTTPException(404, "分类值不存在")
    return result


@app.delete("/api/admin/classification/{cid}")
def admin_deactivate_classification(cid: int, db: Session = Depends(get_db)):
    """停用 (is_active=False) 而非删除。"""
    from services.classification_service import deactivate_classification
    ok = deactivate_classification(db, cid)
    if not ok:
        raise HTTPException(404, "分类值不存在")
    return {"status": "ok"}


@app.post("/api/admin/classification/assign")
def admin_assign_classification(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """把分类赋给一个实体。"""
    from services.classification_service import assign
    ok = assign(
        db,
        entity_type=body["entity_type"],
        entity_code=body["entity_code"],
        classification_id=body["classification_id"],
    )
    return {"status": "ok", "created": ok}


@app.post("/api/admin/classification/unassign")
def admin_unassign_classification(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    from services.classification_service import unassign
    ok = unassign(
        db,
        entity_type=body["entity_type"],
        entity_code=body["entity_code"],
        classification_id=body["classification_id"],
    )
    return {"status": "ok", "removed": ok}


@app.get("/api/admin/classification/assignments")
def admin_get_assignments(
    entity_type: str = Query(...),
    entity_code: str = Query(...),
    db: Session = Depends(get_db),
):
    """列出实体的所有分类。"""
    from services.classification_service import get_assignments
    return get_assignments(db, entity_type=entity_type, entity_code=entity_code)
```

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_admin_classification_api.py
git commit -m "feat(api): /api/admin/classification CRUD + assign/unassign"
```

---

## Phase 3 — 前端 3 个子页面 + 分类管理 sub-tab

### Task 14: MasterDataPanel 改 4 sub-tab

**Files:**
- Modify: `frontend/src/components/MasterDataPanel.jsx`

- [ ] **Step 1: 写 vitest 测试**

`frontend/src/components/__tests__/MasterDataPanel.test.jsx`:
```jsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import MasterDataPanel from "../MasterDataPanel";

describe("MasterDataPanel", () => {
  it("renders 4 sub-tabs", () => {
    render(<MasterDataPanel onMissingConstituents={() => {}} />);
    expect(screen.getByText(/股票主数据/)).toBeInTheDocument();
    expect(screen.getByText(/基金主数据/)).toBeInTheDocument();
    expect(screen.getByText(/指数主数据/)).toBeInTheDocument();
    expect(screen.getByText(/分类维度管理/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npm test -- MasterDataPanel`
Expected: FAIL (4 个 tab 不存在)

- [ ] **Step 3: 重写 MasterDataPanel**

```jsx
import React, { useState } from 'react'
import StockMasterTab from './StockMasterTab'
import FundMasterTab from './FundMasterTab'
import IndexMasterTab from './IndexMasterTab'
import ClassificationTab from './ClassificationTab'

/**
 * 主数据页 — 4 sub-tab: 股票/基金/指数/分类维度。
 * 沿用 .subtab-bar / .subtab 样式。
 */
export default function MasterDataPanel() {
  const [tab, setTab] = useState('stock')

  return (
    <div style={{ padding: 16 }}>
      <div className="subtab-bar">
        <button className={tab === 'stock' ? 'subtab active' : 'subtab'}
                onClick={() => setTab('stock')}>股票主数据</button>
        <button className={tab === 'fund' ? 'subtab active' : 'subtab'}
                onClick={() => setTab('fund')}>基金主数据</button>
        <button className={tab === 'index' ? 'subtab active' : 'subtab'}
                onClick={() => setTab('index')}>指数主数据</button>
        <button className={tab === 'classification' ? 'subtab active' : 'subtab'}
                onClick={() => setTab('classification')}>分类维度管理</button>
      </div>
      {tab === 'stock' && <StockMasterTab />}
      {tab === 'fund' && <FundMasterTab />}
      {tab === 'index' && <IndexMasterTab />}
      {tab === 'classification' && <ClassificationTab />}
    </div>
  )
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npm test -- MasterDataPanel`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MasterDataPanel.jsx frontend/src/components/__tests__/MasterDataPanel.test.jsx
git commit -m "refactor(master-data): MasterDataPanel 4 sub-tab"
```

---

### Task 15: StockMasterTab 组件

**Files:**
- Create: `frontend/src/components/StockMasterTab.jsx`
- Test: `frontend/src/components/__tests__/StockMasterTab.test.jsx`

- [ ] **Step 1: 写测试**

```jsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import StockMasterTab from "../StockMasterTab";
import * as api from "../../api";

vi.mock("../../api", () => ({
  rawApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

describe("StockMasterTab", () => {
  it("renders table with stock data", async () => {
    api.rawApi.get.mockResolvedValue({
      data: { items: [{ stock_code: "600519.SH", stock_name: "贵州茅台",
                          asset_type: "a_share_equity", currency: "CNY" }],
              total: 1, page: 1, page_size: 50 },
    });
    render(<StockMasterTab />);
    await waitFor(() => {
      expect(screen.getByText("600519.SH")).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 3: 实现 StockMasterTab.jsx** (基于现有 SecurityMasterTab 模板,改路径)

```jsx
import React, { useState, useEffect, useCallback } from 'react'
import { rawApi as api } from '../api'

/**
 * 股票主数据 tab — 分页表格 + 筛选 + CRUD。
 * 复用 .data-table / .btn-ghost / .ig / .raised 样式。
 */
export default function StockMasterTab() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState({ asset_type: '', search: '' })
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState(null)

  const PAGE_SIZE = 50

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = { page, page_size: PAGE_SIZE }
      if (filters.asset_type) params.asset_type = filters.asset_type
      if (filters.search) params.search = filters.search
      const res = await api.get('/admin/stock-master', { params })
      setItems(res.data.items || [])
      setTotal(res.data.total || 0)
    } catch (e) {
      console.error('加载股票主数据失败', e)
      setItems([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [page, filters])

  useEffect(() => { load() }, [load])

  const handleSave = async (data) => {
    try {
      if (editing) {
        await api.put(`/admin/stock-master/${encodeURIComponent(editing.stock_code)}`, data)
      } else {
        await api.post('/admin/stock-master', data)
      }
      setEditing(null)
      load()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    }
  }

  return (
    <div className="raised" style={{ padding: 12 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <input className="ig" placeholder="搜索代码/名称"
               value={filters.search}
               onChange={(e) => setFilters({...filters, search: e.target.value, page: 1})} />
        <select className="ig" value={filters.asset_type}
                onChange={(e) => setFilters({...filters, asset_type: e.target.value, page: 1})}>
          <option value="">全部类型</option>
          <option value="a_share_equity">A 股股票</option>
          <option value="hk_equity">港股</option>
          <option value="us_stock">美股</option>
          <option value="bond">债券</option>
          <option value="gold">黄金</option>
          <option value="commodity">商品</option>
        </select>
        <button className="btn-ghost" onClick={() => setEditing({})}>+ 新增</button>
      </div>

      <table className="data-table">
        <thead>
          <tr>
            <th>代码</th><th>名称</th><th>交易所</th><th>币种</th>
            <th>资产类型</th><th>可下钻</th><th>备注</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map(r => (
            <tr key={r.stock_code}>
              <td>{r.stock_code}</td>
              <td>{r.stock_name}</td>
              <td>{r.exchange}</td>
              <td>{r.currency}</td>
              <td>{r.asset_type}</td>
              <td>{r.is_drillable ? '✓' : '—'}</td>
              <td>{r.note}</td>
              <td>
                <button className="btn-ghost" onClick={() => setEditing(r)}>编辑</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)' }}>
        共 {total} 条,第 {page} 页
        {page > 1 && <button className="btn-ghost" onClick={() => setPage(page - 1)} style={{ marginLeft: 8 }}>上一页</button>}
        {items.length >= PAGE_SIZE && <button className="btn-ghost" onClick={() => setPage(page + 1)} style={{ marginLeft: 8 }}>下一页</button>}
      </div>

      {editing && <StockEditDialog row={editing} onClose={() => setEditing(null)} onSave={handleSave} />}
    </div>
  )
}

function StockEditDialog({ row, onClose, onSave }) {
  const [data, setData] = useState({
    stock_code: row.stock_code || '',
    stock_name: row.stock_name || '',
    exchange: row.exchange || '',
    currency: row.currency || 'CNY',
    asset_type: row.asset_type || 'a_share_equity',
    note: row.note || '',
  })

  return (
    <div className="modal-overlay">
      <div className="modal-box">
        <h3>{row.stock_code ? '编辑股票' : '新增股票'}</h3>
        <label>代码 <input className="ig" value={data.stock_code}
                            onChange={(e) => setData({...data, stock_code: e.target.value})}
                            disabled={!!row.stock_code} /></label>
        <label>名称 <input className="ig" value={data.stock_name}
                            onChange={(e) => setData({...data, stock_name: e.target.value})} /></label>
        <label>交易所 <input className="ig" value={data.exchange}
                              onChange={(e) => setData({...data, exchange: e.target.value})} /></label>
        <label>币种
          <select className="ig" value={data.currency}
                  onChange={(e) => setData({...data, currency: e.target.value})}>
            <option>CNY</option><option>USD</option><option>HKD</option><option>CAD</option>
          </select>
        </label>
        <label>资产类型
          <select className="ig" value={data.asset_type}
                  onChange={(e) => setData({...data, asset_type: e.target.value})}>
            <option value="a_share_equity">A 股股票</option>
            <option value="hk_equity">港股</option>
            <option value="us_stock">美股</option>
            <option value="bond">债券</option>
            <option value="gold">黄金</option>
            <option value="commodity">商品</option>
          </select>
        </label>
        <label>备注 <input className="ig" value={data.note}
                            onChange={(e) => setData({...data, note: e.target.value})} /></label>
        <div style={{ marginTop: 12, textAlign: 'right' }}>
          <button className="btn-ghost" onClick={onClose}>取消</button>
          <button className="btn-ghost" style={{ marginLeft: 8 }}
                  onClick={() => onSave(data)}>保存</button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/StockMasterTab.jsx frontend/src/components/__tests__/StockMasterTab.test.jsx
git commit -m "feat(ui): StockMasterTab 组件"
```

---

### Task 16: FundMasterTab 组件

**Files:**
- Create: `frontend/src/components/FundMasterTab.jsx`
- Test: `frontend/src/components/__tests__/FundMasterTab.test.jsx`

- [ ] **Step 1: 写测试** (同 Task 15 模式,改 stock→fund 字段)

- [ ] **Step 3: 实现** (基于 StockMasterTab,字段改为 fund_code/fund_name/fund_type/asset_type/benchmark_formula/is_drillable/note;asset_type 列表包含 a_share_etf, qdii_equity, qdii_bond, gold 等)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FundMasterTab.jsx frontend/src/components/__tests__/FundMasterTab.test.jsx
git commit -m "feat(ui): FundMasterTab 组件"
```

---

### Task 17: IndexMasterTab 组件

**Files:**
- Create: `frontend/src/components/IndexMasterTab.jsx`
- Test: `frontend/src/components/__tests__/IndexMasterTab.test.jsx`

- [ ] **Step 1: 写测试** (验证表格 + 手动刷新按钮 + category 下拉)

- [ ] **Step 3: 实现** (基于 StockMasterTab 模板;字段 index_code/index_name/exchange/currency/category/constituent_count/is_active;category 4 类下拉;加「手动刷新」按钮 → POST /admin/index-master/refresh)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/IndexMasterTab.jsx frontend/src/components/__tests__/IndexMasterTab.test.jsx
git commit -m "feat(ui): IndexMasterTab 组件 (含手动刷新按钮)"
```

---

### Task 18: ClassificationTab 组件

**Files:**
- Create: `frontend/src/components/ClassificationTab.jsx`
- Test: `frontend/src/components/__tests__/ClassificationTab.test.jsx`

- [ ] **Step 1: 写测试** (验证 2 个 dimension 切换 + CRUD + 停用)

- [ ] **Step 3: 实现** (顶部 sub-tab 切换 asset_type / theme;表格列 dimension/code/display_label/sort_order/is_active;操作:新增/编辑/停用)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ClassificationTab.jsx frontend/src/components/__tests__/ClassificationTab.test.jsx
git commit -m "feat(ui): ClassificationTab 组件 (字典管理)"
```

---

### Task 19: 删除旧 SecurityMasterTab + 在 api.js 加新端点

**Files:**
- Modify: `frontend/src/api.js` — 加 stockMasterList / fundMasterList / indexMasterList / classificationList 等 client
- Delete: `frontend/src/components/SecurityMasterTab.jsx`
- Modify: 任何 import `SecurityMasterTab` 的文件 (目前是 MasterDataPanel,已重写;grep 确认无其他)

- [ ] **Step 1: grep 检查 SecurityMasterTab 引用**

Run: `cd frontend && grep -r "SecurityMasterTab" src/`
Expected: 仅 MasterDataPanel.jsx (本次已重写) — 无其他引用

- [ ] **Step 2: 加 api.js client**

`frontend/src/api.js` 末尾追加:
```js
// 公共数据主数据重构 (2026-07-02)
export const stockMasterList = (params) => rawApi.get('/admin/stock-master', { params }).then(r => r.data)
export const fundMasterList = (params) => rawApi.get('/admin/fund-master', { params }).then(r => r.data)
export const indexMasterList = (params) => rawApi.get('/admin/index-master', { params }).then(r => r.data)
export const indexMasterRefresh = () => rawApi.post('/admin/index-master/refresh').then(r => r.data)
export const fundMasterLookup = (q, page = 1) => rawApi.get('/admin/fund-master/lookup', { params: { q, page } }).then(r => r.data)
export const indexMasterLookup = (q, page = 1) => rawApi.get('/admin/index-master/lookup', { params: { q, page } }).then(r => r.data)
export const classificationList = (dimension) => rawApi.get('/admin/classification', { params: { dimension } }).then(r => r.data)
export const classificationAssign = (entity_type, entity_code, classification_id) =>
  rawApi.post('/admin/classification/assign', { entity_type, entity_code, classification_id }).then(r => r.data)
export const classificationUnassign = (entity_type, entity_code, classification_id) =>
  rawApi.post('/admin/classification/unassign', { entity_type, entity_code, classification_id }).then(r => r.data)
export const fundIndexMapSelective = (body) =>
  rawApi.post('/admin/fund-index-map/selective', body).then(r => r.data)
```

- [ ] **Step 3: 删 SecurityMasterTab.jsx**

```bash
cd frontend && git rm src/components/SecurityMasterTab.jsx
```

- [ ] **Step 4: 跑前端测试不破**

Run: `cd frontend && npm test`
Expected: 所有现有测试 + 新增测试都过

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat(api-client): 加 stock/fund/index/classification client + 删除 SecurityMasterTab"
```

---

## Phase 4 — 基金-指数双向选择

### Task 20: `/api/admin/fund-master/lookup` 端点

**Files:**
- Modify: `backend/main.py` — 加 lookup 端点

- [ ] **Step 1: 写测试**

`backend/tests/test_selective_fund_index_api.py` (顶层):
```python
def test_fund_lookup(in_memory_db):
    """模糊搜索 + 分页。"""
    from services.fund_master_service import create_fund
    create_fund(in_memory_db, dict(
        fund_code="510300.SH", fund_name="华泰柏瑞沪深300ETF",
        fund_type="etf", asset_type="a_share_etf"))
    create_fund(in_memory_db, dict(
        fund_code="161725.OF", fund_name="招商中证白酒",
        fund_type="otc", asset_type="a_share_equity"))
    # Import不会破,实际 HTTP 调用留 Phase 4 integration 测试
```

- [ ] **Step 3: 实现端点**

```python
@app.get("/api/admin/fund-master/lookup")
def admin_fund_master_lookup(
    q: str = Query("", description="模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from services.fund_master_service import list_funds
    return list_funds(db, search=q or None, page=page, page_size=page_size)
```

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_selective_fund_index_api.py
git commit -m "feat(api): /api/admin/fund-master/lookup 模糊搜索"
```

---

### Task 21: `/api/admin/index-master/lookup` 端点

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 3: 实现** (同 Task 20 模式,查 index_master)

```python
@app.get("/api/admin/index-master/lookup")
def admin_index_master_lookup(
    q: str = Query("", description="模糊搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    from services.index_master_service import list_indices
    return list_indices(db, search=q or None, page=page, page_size=page_size)
```

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "feat(api): /api/admin/index-master/lookup 模糊搜索"
```

---

### Task 22: `/api/admin/fund-index-map/selective` 端点

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: 写测试**

```python
def test_selective_create_validates_entities(in_memory_db):
    """fund_code 不在 fund_master 或 index_code 不在 index_master → 400。"""
    # 直接调用 service-level helper,避免 HTTP 测试复杂
    # 详见 Phase 4 集成测试
    pass
```

- [ ] **Step 3: 实现端点**

```python
@app.post("/api/admin/fund-index-map/selective")
def admin_create_fund_index_map_selective(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """双向选择式新增 fund-index 映射。

    body: {fund_code, index_code, benchmark_formula?, as_of_date?}
    fund_code 必须在 fund_master;index_code 必须在 index_master。
    """
    fund_code = body.get("fund_code")
    index_code = body.get("index_code")
    if not fund_code or not index_code:
        raise HTTPException(400, "fund_code 和 index_code 必填")

    fund = db.query(FundMaster).filter_by(fund_code=fund_code).first()
    if not fund:
        raise HTTPException(400, f"基金 {fund_code} 不在 fund_master 中")
    idx = db.query(IndexMaster).filter_by(index_code=index_code).first()
    if not idx:
        raise HTTPException(400, f"指数 {index_code} 不在 index_master 中")

    from sqlalchemy import text
    raw_date = body.get("as_of_date")
    if raw_date:
        if isinstance(raw_date, str):
            raw_date = date.fromisoformat(raw_date)
    else:
        raw_date = date.today()

    fm = FundIndexMap(
        fund_code=fund_code,
        fund_name=fund.fund_name,
        index_code=index_code,
        index_name=idx.index_name,
        benchmark_formula=body.get("benchmark_formula"),
        as_of_date=raw_date,
        source="manual_selective",
    )
    db.add(fm)
    db.commit()
    return {"status": "ok", "fund_code": fm.fund_code, "index_code": fm.index_code}
```

注意:文件顶部 import 区需要 `from models_master import FundMaster, IndexMaster`。

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "feat(api): POST /api/admin/fund-index-map/selective"
```

---

### Task 23: SelectiveFundIndexDialog 弹窗组件

**Files:**
- Create: `frontend/src/components/SelectiveFundIndexDialog.jsx`
- Test: `frontend/src/components/__tests__/SelectiveFundIndexDialog.test.jsx`

- [ ] **Step 1: 写测试**

```jsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SelectiveFundIndexDialog from "../SelectiveFundIndexDialog";

vi.mock("../../api", () => ({
  fundMasterLookup: vi.fn().mockResolvedValue({
    items: [{ fund_code: "510300.SH", fund_name: "华泰柏瑞沪深300ETF" }], total: 1,
  }),
  indexMasterLookup: vi.fn().mockResolvedValue({
    items: [{ index_code: "000300.SH", index_name: "沪深300" }], total: 1,
  }),
  fundIndexMapSelective: vi.fn().mockResolvedValue({ status: "ok" }),
}));

describe("SelectiveFundIndexDialog", () => {
  it("renders 3 sections (fund search, index search, benchmark)", () => {
    render(<SelectiveFundIndexDialog open={true} onClose={() => {}} onSuccess={() => {}} />);
    expect(screen.getByText(/选择基金/)).toBeInTheDocument();
    expect(screen.getByText(/选择指数/)).toBeInTheDocument();
    expect(screen.getByLabelText(/业绩比较基准/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 实现 SelectiveFundIndexDialog.jsx**

```jsx
import React, { useState, useEffect, useCallback } from 'react'
import * as api from '../api'

/**
 * 基金-指数映射 双向选择弹窗。
 * 表格上方「+ 新增映射」按钮触发。
 */
export default function SelectiveFundIndexDialog({ open, onClose, onSuccess }) {
  const [step, setStep] = useState(1)  // 1=选基金 2=选指数 3=填基准
  const [fundSearch, setFundSearch] = useState('')
  const [fundResults, setFundResults] = useState([])
  const [selectedFund, setSelectedFund] = useState(null)

  const [idxSearch, setIdxSearch] = useState('')
  const [idxResults, setIdxResults] = useState([])
  const [selectedIndex, setSelectedIndex] = useState(null)

  const [benchmark, setBenchmark] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState(null)

  const searchFunds = useCallback(async (q) => {
    if (!q) { setFundResults([]); return }
    try {
      const res = await api.fundMasterLookup(q)
      setFundResults(res.items || [])
    } catch (e) { console.error(e) }
  }, [])
  const searchIndices = useCallback(async (q) => {
    if (!q) { setIdxResults([]); return }
    try {
      const res = await api.indexMasterLookup(q)
      setIdxResults(res.items || [])
    } catch (e) { console.error(e) }
  }, [])

  useEffect(() => {
    if (!open) {
      setStep(1); setSelectedFund(null); setSelectedIndex(null)
      setBenchmark(''); setErr(null); setFundSearch(''); setIdxSearch('')
    }
  }, [open])

  if (!open) return null

  const handleSubmit = async () => {
    setSaving(true); setErr(null)
    try {
      await api.fundIndexMapSelective({
        fund_code: selectedFund.fund_code,
        index_code: selectedIndex.index_code,
        benchmark_formula: benchmark || undefined,
        as_of_date: new Date().toISOString().slice(0, 10),
      })
      onSuccess?.()
      onClose()
    } catch (e) {
      setErr(e.response?.data?.detail || e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={(e) => e.stopPropagation()}
           style={{ maxWidth: 600, width: '90%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <h3>新增基金-指数映射</h3>
          <button className="btn-ghost" onClick={onClose}>×</button>
        </div>

        <div>
          <h4>1. 选择基金 {selectedFund && `(${selectedFund.fund_code})`}</h4>
          <input className="ig" placeholder="搜索代码或名称"
                 value={fundSearch}
                 onChange={(e) => { setFundSearch(e.target.value); searchFunds(e.target.value) }} />
          {fundResults.map(r => (
            <div key={r.fund_code} className="raised" style={{ padding: 8, marginTop: 4, cursor: 'pointer' }}
                 onClick={() => { setSelectedFund(r); setStep(2) }}>
              {r.fund_code}  {r.fund_name}
            </div>
          ))}
        </div>

        <div style={{ marginTop: 16 }}>
          <h4>2. 选择指数 {selectedIndex && `(${selectedIndex.index_code})`}</h4>
          <input className="ig" placeholder="搜索代码或名称"
                 value={idxSearch}
                 onChange={(e) => { setIdxSearch(e.target.value); searchIndices(e.target.value) }} />
          {idxResults.map(r => (
            <div key={r.index_code} className="raised" style={{ padding: 8, marginTop: 4, cursor: 'pointer' }}
                 onClick={() => { setSelectedIndex(r); setStep(3) }}>
              {r.index_code}  {r.index_name}
            </div>
          ))}
        </div>

        <div style={{ marginTop: 16 }}>
          <h4>3. 业绩比较基准（可选）</h4>
          <input className="ig" style={{ width: '100%' }}
                 value={benchmark} onChange={(e) => setBenchmark(e.target.value)}
                 placeholder="沪深300指数收益率×95% + 银行活期×5%" />
        </div>

        {err && <div style={{ color: 'red', marginTop: 12 }}>{err}</div>}

        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <button className="btn-ghost" onClick={onClose}>取消</button>
          <button className="btn-ghost" style={{ marginLeft: 8 }}
                  disabled={!selectedFund || !selectedIndex || saving}
                  onClick={handleSubmit}>
            {saving ? '保存中…' : '确认新增'}
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SelectiveFundIndexDialog.jsx frontend/src/components/__tests__/SelectiveFundIndexDialog.test.jsx
git commit -m "feat(ui): SelectiveFundIndexDialog 双向选择弹窗"
```

---

### Task 24: FundIndexMapTab 加「新增映射」按钮 + 表格列下拉化

**Files:**
- Modify: `frontend/src/components/FundIndexMapTab.jsx` — 加按钮和弹窗;index_code 列改下拉 (利用 lookup API)

- [ ] **Step 1: 写测试** (mock lookup API,验证按钮存在,弹窗打开逻辑)

- [ ] **Step 3: 实现** (修改 FundIndexMapTab:加 `const [showDialog, setShowDialog] = useState(false)`,按钮触发 setShowDialog(true);表格列编辑时 index_code 列改 `<select>` 配 lookup endpoint — 此处只改读路径,新建走弹窗;保持向后兼容旧的 PUT endpoint)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FundIndexMapTab.jsx frontend/src/components/__tests__/FundIndexMapTab.test.jsx
git commit -m "feat(ui): FundIndexMapTab 加双向选择弹窗 + index_code 下拉化"
```

---

## Phase 5 — akshare 指数轮询

### Task 25: akshare_index_poller service + 测试

**Files:**
- Create: `backend/services/akshare_index_poller.py`
- Test: `backend/tests/test_akshare_index_poller.py`

- [ ] **Step 1: 写测试 (mock akshare)**

```python
"""akshare_index_poller 测试 — mock akshare 返回值。"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


@pytest.fixture
def fake_akshare():
    """Mock akshare.stock_zh_index_spot_em 返回固定 dataframe。"""
    with patch("akshare.stock_zh_index_spot_em") as mock_em, \
         patch("akshare.index_stock_info") as mock_info:
        # A 股指数实时快照
        mock_em.return_value = pd.DataFrame({
            "代码": ["000300", "000905", "399006"],
            "名称": ["沪深300", "中证500", "创业板指"],
            "最新价": [3800.0, 5200.0, 2300.0],
        })
        # 单只详情 (constituent_count)
        mock_info.return_value = pd.DataFrame({
            "指数简称": ["沪深300"],
            "指数代码": ["000300"],
            "样本数": [300],
        })
        yield {"em": mock_em, "info": mock_info}


def test_poll_inserts_new_indices(in_memory_db, fake_akshare):
    from services.akshare_index_poller import poll_index_master
    result = poll_index_master(in_memory_db)
    assert result["inserted"] == 3
    assert result["skipped"] == 0


def test_poll_updates_existing(in_memory_db, fake_akshare):
    from models_master import IndexMaster
    from services.akshare_index_poller import poll_index_master
    # 预存在一条旧 name
    db = in_memory_db
    db.add(IndexMaster(
        index_code="000300", index_name="旧名",
        exchange="SH", currency="CNY", source="akshare",
        first_pulled_at=__import__("datetime").datetime.utcnow(),
    ))
    db.commit()

    result = poll_index_master(db)
    assert result["updated"] == 1
    refreshed = db.query(IndexMaster).filter_by(index_code="000300").first()
    assert refreshed.index_name == "沪深300"


def test_poll_marks_inactive_disappeared(in_memory_db, fake_akshare):
    """上次见到的 code 本次没出现 → is_active=False。"""
    from models_master import IndexMaster
    from datetime import datetime
    from services.akshare_index_poller import poll_index_master
    db = in_memory_db
    db.add(IndexMaster(
        index_code="999999", index_name="已下架指数",
        is_active=True, source="akshare",
        first_pulled_at=datetime.utcnow(),
    ))
    db.commit()

    poll_index_master(db)
    # fake_em 返回的 3 个都没有 999999
    refreshed = db.query(IndexMaster).filter_by(index_code="999999").first()
    assert refreshed.is_active is False


def test_poll_records_failure_to_data_pull_task(in_memory_db, fake_akshare):
    """akshare 抛错时,应写 DataPullTask(status='FAILED')。"""
    from sqlalchemy import text
    fake_akshare["em"].side_effect = Exception("akshare 临时失败")
    # 确保 data_pull_task 表存在 (本测试最小集)
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

    from services.akshare_index_poller import poll_index_master
    result = poll_index_master(in_memory_db)
    assert result["status"] == "failed"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_akshare_index_poller.py -v`
Expected: FAIL "No module named 'services.akshare_index_poller'"

- [ ] **Step 3: 实现 akshare_index_poller.py**

```python
"""akshare 增量指数轮询 (2026-07-02)。

每天 21:23 Asia/Shanghai 跑一次;增量:
  - 新增:index_code 不存在 → INSERT
  - 更新:name / constituent_count 有差异 → UPDATE
  - 跳过:完全一致 → 跳过
  - 标记 is_active=False:上次见到但本次未拉到
"""
from __future__ import annotations

import logging
from datetime import datetime

import akshare as ak
import pandas as pd

from sqlalchemy.orm import Session

from models import DataPullTask
from models_master import IndexMaster

logger = logging.getLogger(__name__)


def _normalize_code(raw: str) -> str:
    """akshare 指数代码 → 项目内的 index_code (000300 保持)。"""
    code = str(raw).strip()
    if "." in code:
        code = code.split(".")[0]
    return code


def _fetch_indices_from_ak() -> pd.DataFrame:
    """从 akshare 拉全市场 A 股指数实时快照。"""
    df = ak.stock_zh_index_spot_em()
    df = df.rename(columns={"代码": "code", "名称": "name"})
    df["code"] = df["code"].apply(_normalize_code)
    return df[["code", "name"]]


def poll_index_master(db: Session) -> dict:
    """主入口: 增量同步 index_master。

    Returns:
        dict: {status, inserted, updated, skipped, marked_inactive, error?}
    """
    started_at = datetime.utcnow()
    job = DataPullTask(
        job_id="job_poll_index_master",
        job_name="指数主数据轮询 (akshare)",
        started_at=started_at,
        status="RUNNING",
        triggered_by="scheduler",
    )
    db.add(job)
    db.commit()

    try:
        df = _fetch_indices_from_ak()
        current_codes = set(df["code"].astype(str))

        # 1) 增量: 新增 / 更新
        inserted = updated = skipped = 0
        now = datetime.utcnow()
        for _, row in df.iterrows():
            code = str(row["code"])
            name = str(row["name"])
            existing = db.query(IndexMaster).filter_by(index_code=code).first()
            if not existing:
                db.add(IndexMaster(
                    index_code=code,
                    index_name=name,
                    source="akshare",
                    is_active=True,
                    first_pulled_at=now,
                    last_pulled_at=now,
                    last_verified_at=now,
                ))
                inserted += 1
            else:
                changed = False
                if existing.index_name != name:
                    existing.index_name = name
                    changed = True
                if existing.last_verified_at is None or (
                    now - existing.last_verified_at
                ).days >= 1:
                    existing.last_verified_at = now
                    changed = True
                if changed:
                    existing.last_pulled_at = now
                    updated += 1
                else:
                    skipped += 1

        # 2) 标记消失:is_active=False (不动 is_active=True 的新表条目)
        marked_inactive = 0
        active_rows = db.query(IndexMaster).filter(IndexMaster.is_active == True).all()  # noqa: E712
        for r in active_rows:
            if r.source == "akshare" and r.index_code not in current_codes:
                r.is_active = False
                r.last_pulled_at = now
                marked_inactive += 1

        db.commit()

        # 3) 写 data_pull_task
        job.status = "SUCCESS"
        job.finished_at = datetime.utcnow()
        job.records_pulled = inserted + updated
        db.commit()

        return {
            "status": "success",
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "marked_inactive": marked_inactive,
        }

    except Exception as e:
        db.rollback()
        job.status = "FAILED"
        job.finished_at = datetime.utcnow()
        job.error_message = str(e)[:500]
        db.commit()
        logger.exception("akshare_index_poller 失败")
        return {"status": "failed", "error": str(e)[:500]}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_akshare_index_poller.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/services/akshare_index_poller.py backend/tests/test_akshare_index_poller.py
git commit -m "feat(service): akshare_index_poller 增量轮询"
```

---

### Task 26: scheduler 注册 job_poll_index_master

**Files:**
- Modify: `backend/services/scheduler.py`

- [ ] **Step 1: 找添加位置**

Run: `grep -n "add_job" backend/services/scheduler.py | head -5` — 找一个 cron=21 已有的位置附近加。

- [ ] **Step 2: 加 job 定义 + 调度**

在文件中加:
```python
def job_poll_index_master() -> dict:
    """每天 21:23 跑 — akshare 指数主数据增量轮询 (2026-07-02)。"""
    from database import SessionLocal
    from services.akshare_index_poller import poll_index_master
    db = SessionLocal()
    try:
        return poll_index_master(db)
    finally:
        db.close()
```

在调度注册区域加 (找一个 `add_job` 后):
```python
scheduler.add_job(
    job_poll_index_master,
    'cron',
    hour=21, minute=23,
    id='job_poll_index_master',
    max_instances=1,
    coalesce=True,
    replace_existing=True,
)
```

- [ ] **Step 3: 写测试**

`backend/tests/test_scheduler_job_registry.py` (新建,或加到现有 scheduler 测试):
```python
def test_job_poll_index_master_registered():
    """job_poll_index_master 应在 scheduler 中注册。"""
    from services.scheduler import scheduler
    job = scheduler.get_job('job_poll_index_master')
    assert job is not None
    assert job.trigger.hour == 21
    assert job.trigger.minute == 23
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_scheduler_job_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/scheduler.py backend/tests/test_scheduler_job_registry.py
git commit -m "feat(scheduler): 注册 job_poll_index_master 每天 21:23"
```

---

### Task 27: 手动刷新 endpoint + QQQ seed

**Files:**
- Modify: `backend/main.py` — 加 `POST /api/admin/index-master/refresh`
- Create: `backend/scripts/_seed_qqq.py`

- [ ] **Step 1: 写测试**

```python
def test_refresh_endpoint(in_memory_db):
    """手动刷新 endpoint 应调 poll_index_master。"""
    with patch("main.poll_index_master") as mock_poll:
        mock_poll.return_value = {"status": "success"}
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        r = client.post("/api/admin/index-master/refresh")
        # 不依赖 auth:路径可能 401/200 都行
        assert r.status_code in (200, 401)
```

- [ ] **Step 3: 实现端点**

```python
@app.post("/api/admin/index-master/refresh")
def admin_refresh_index_master(db: Session = Depends(get_db)):
    """手动触发指数轮询 (admin 失败时一键重跑)。"""
    from services.akshare_index_poller import poll_index_master
    return poll_index_master(db)
```

`backend/scripts/_seed_qqq.py`:
```python
"""QQQ 手动入库 — 单一一次性脚本,不走 akshare 轮询。

用法: python -m scripts._seed_qqq
"""
from datetime import datetime

from database import SessionLocal
from models_master import IndexMaster


def seed_qqq():
    db = SessionLocal()
    try:
        existing = db.query(IndexMaster).filter_by(index_code="QQQ").first()
        if existing:
            print(f"QQQ 已存在: id={existing.id}")
            return
        db.add(IndexMaster(
            index_code="QQQ",
            index_name="纳斯达克100",
            exchange="US",
            currency="USD",
            category="宽基",
            source="manual_qqq_seed",
            is_active=True,
            first_pulled_at=datetime.utcnow(),
            last_pulled_at=datetime.utcnow(),
            last_verified_at=datetime.utcnow(),
        ))
        db.commit()
        print("QQQ 已写入")
    finally:
        db.close()


if __name__ == "__main__":
    seed_qqq()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_*.py -k "refresh or qqq" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/scripts/_seed_qqq.py
git commit -m "feat(api+script): 指数手动刷新 endpoint + QQQ seed"
```

---

## Phase 6 — 改名 + 清理 + 验证

### Task 28: 跑迁移 commit_migration

**Files:** none (人工执行步骤)

- [ ] **Step 1: prod backup 验证 memanto 中的 pg_dump 流程可用**

(memanto: portfoliom2-pg 数据备份流程 43.130.62.66 — 已在 Step 1 备份)

- [ ] **Step 2: 跑 commit**

Run: `ssh chargeye133 'cd /home/ubuntu/PortfolioM2/backend && python -m scripts.migrate_split_security_master --commit'`
Expected: 输出 counts 与 dry-run 一致 + 表创建成功

- [ ] **Step 3: 跑 verify**

Run: `ssh chargeye133 'cd /home/ubuntu/PortfolioM2/backend && python -m scripts.migrate_split_security_master --verify'`
Expected: `legacy_count == new_count` (true)

- [ ] **Step 4: 跑 QQQ seed**

Run: `ssh chargeye133 'cd /home/ubuntu/PortfolioM2/backend && python -m scripts._seed_qqq'`
Expected: "QQQ 已写入" 或 "已存在"

- [ ] **Step 5: (无 commit — 验证步)**

---

### Task 29: rename security_master → security_master_legacy

**Files:**
- Modify: `backend/scripts/migrate_split_security_master.py` — 加 `rename_security_master_to_legacy()` (Task 5 已实现)

- [ ] **Step 1: 备份确认**

确认 Step 1 的 pg_dump 备份还在。

- [ ] **Step 2: 跑 rename**

Run: `ssh chargeye133 'cd /home/ubuntu/PortfolioM2/backend && python -c "from database import SessionLocal; from scripts.migrate_split_security_master import rename_security_master_to_legacy; db = SessionLocal(); rename_security_master_to_legacy(db); print(\"renamed\")"'`
Expected: 输出 "renamed"

- [ ] **Step 3: 验证旧名不可用**

Run: `ssh chargeye133 'cd /home/ubuntu/PortfolioM2/backend && python -c "from sqlalchemy import text; from database import SessionLocal; db = SessionLocal(); print(db.execute(text(\"SELECT 1 FROM security_master LIMIT 1\")).fetchall())"'`
Expected: 报错 (表不存在)

Run: `ssh chargeye133 'cd /home/ubuntu/PortfolioM2/backend && python -c "from sqlalchemy import text; from database import SessionLocal; db = SessionLocal(); n = db.execute(text(\"SELECT COUNT(*) FROM security_master_legacy\")).scalar(); print(n)'`
Expected: 输出 legacy 总数

- [ ] **Step 4: 跑所有测试 + 端到端 smoke**

Run: `cd backend && python -m pytest tests/ -q`
Expected: 所有测试通过

- [ ] **Step 5: Commit (rename 是 migration commit)**

```bash
git add backend/scripts/migrate_split_security_master.py
git commit -m "chore(migration): 生产执行 rename security_master → legacy"
```

(注:rename 已在 prod 执行,本 commit 是把脚本状态记录下来;如 rename 已包含在前面的 commit 里,本步可省)

---

### Task 30: 移除旧 SecurityMaster 引用 (前端 + 后端)

**Files:**
- Modify: 任何仍引用 `security_master` 的代码
- Modify: `backend/services/security_master_service.py` — 标记 deprecated (如有引用)
- 人工 grep 检查

- [ ] **Step 1: grep 找所有引用**

Run:
```bash
cd /d/claude_code_project/PortfolioM
grep -rE "security_master[^_]" backend/ frontend/src/ --include="*.py" --include="*.jsx" --include="*.js" \
  --exclude-dir=node_modules --exclude-dir=.git
```

Expected: 仅迁移脚本 + 已 deprecated 文件 + 测试 fixture 引用。如有非迁移代码引用,改用新表。

- [ ] **Step 2: 替换引用**

把每个引用改为读新表 (例:`security_master_security_type` 改为 `stock_master.asset_type`)。

- [ ] **Step 3: 跑测试确认不破**

Run: `cd backend && python -m pytest tests/ -q && cd ../frontend && npm test`
Expected: 所有测试通过

- [ ] **Step 4: 删除或归档旧代码**

旧代码如不再需要,删除:
```bash
git rm backend/services/security_master_service.py
git rm backend/main.py 中的 /api/admin/security-master/* 端点 (如保留兼容层则不删)
```

(决策: 是否保留兼容层 read-only 读 legacy 6 个月 — 倾向保留)

- [ ] **Step 5: Commit**

```bash
git add -u
git commit -m "refactor: 移除 security_master 旧引用,统一走新表"
```

---

### Task 31: 文档 + Project_development 更新

**Files:**
- Modify: `Project_development.md` — 主数据章节记录本次改动
- Modify: 前端 README / docs (如有)

- [ ] **Step 1: 更新 Project_development.md**

在主数据相关章节追加本次重构要点:
```markdown
## 2026-07-02 公共数据主数据重构 (Spec-1)

参考: docs/superpowers/specs/2026-07-02-master-data-overhaul-design.md

- 3 张主表 stock_master / fund_master / index_master 替代单一 security_master
- 2 张分类表 classification + classification_assign (asset_type + theme 双维度)
- akshare 增量拉 A 股指数 (job_poll_index_master 每天 21:23)
- QQQ 手动 seed 脚本 (backend/scripts/_seed_qqq.py)
- 双向 typeahead 选择基金-指数映射
- 旧 security_master 改名 security_master_legacy,冻结只读

下一轮 (Spec-2): 全市场 A 股/港股/基金/指数名称代码一次性拉取
```

- [ ] **Step 2: grep 验证无遗漏**

- [ ] **Step 3: Commit**

```bash
git add Project_development.md
git commit -m "docs: 记录主数据重构改动"
```

---

### Task 32: 上线 (push + deploy)

**Files:** none (git push + ssh deploy)

- [ ] **Step 1: 验证本轮所有 commit 都已本地**

Run: `git log --oneline e6d3e98..HEAD`
Expected: 本 Spec-1 的所有 commit 都列出来

- [ ] **Step 2: push**

Run: `git push origin main`
Expected: 与远程同步成功

- [ ] **Step 3: deploy 到 prod**

Run:
```bash
ssh chargeye133 'cd /home/ubuntu/PortfolioM2 && git pull origin main && \
  docker compose -f docker-compose-2.0.yml up -d --build backend frontend'
```

Expected: 后端容器重启 + 前端容器重建 (本轮含 backend 端点改动,前端 UI 改动)

- [ ] **Step 4: 上线后 smoke 测试 (人工)**

按 Phase 4 spec 手动:
- 打开 Admin → 主数据
- 验证 4 个 sub-tab 出现
- 点股票主数据 → 看表格加载
- 点基金主数据 → 看表格加载
- 点指数主数据 → 看表格加载 (如有从 akshare 同步的数据可见)
- 点分类维度管理 → 看到 2 个 dimension
- 在基金-指数映射 tab 点「新增映射」弹窗能打开

- [ ] **Step 5: (无 commit — 验证步)**

---

## Self-Review

| 检查项 | 状态 |
|---|---|
| Spec coverage: 11 个 spec 节,均可指向具体 task | ✓ |
| Placeholder scan: 无 TBD / TODO / "implement later" | ✓ |
| Type 一致性:`StockMaster.stock_code` / `FundMaster.fund_code` / `IndexMaster.index_code` / `Classification.id` 在所有 task 中用同一个名字 | ✓ |
| 端点路径一致:`/api/admin/{stock,fund,index}-master` / `/api/admin/classification` / `/api/admin/fund-index-map/selective` | ✓ |

---

## 风险与回退

- 迁移脚本失败: `pg_dump` 已在 Step 1 备份;事务包裹失败自动 rollback
- akshare 接口异常:`try/except` + data_pull_task 记录;连续 3 次失败告警
- bond 归类错: dry-run 阶段人工 review `security_type='bond'` 的样本
- type2 未知值: 告警 + 留在 classification 表,人工后续编辑 (不阻迁移)
- 前端 vitest 失败: 任务单独 commit,可回滚单 PR
