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
    Boolean, Column, DateTime, ForeignKey, Index,
    Integer, String, UniqueConstraint,
)

from database import Base


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

    id = Column(Integer, primary_key=True, autoincrement=True)
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
