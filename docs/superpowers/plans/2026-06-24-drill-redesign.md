# 下钻架构重新设计 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 `drillable_funds.py` 中耦合的公共层/用户层拆分为三层 service（public/user/orchestration），保留现有 API 端点，前端无感知。

**Architecture:** 公共层只读 `fund_drill_snapshot`，用户层只读 `Holding`，orchestration 层调两者 + 估值快照 join 后返回。API 端点保留 `/drillable-indices` 和 `/index-drill`，内部改为调 orchestration。

**Tech Stack:** Python + SQLAlchemy + FastAPI + pytest

**Spec:** `docs/superpowers/specs/2026-06-24-drill-redesign-design.md`

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `backend/services/drill_public_service.py` | 公共层：只读 fund_drill_snapshot + fund_index_map |
| 新建 | `backend/services/drill_user_service.py` | 用户层：只读 Holding |
| 新建 | `backend/services/drill_orchestration_service.py` | join 层：调 public + user + 估值快照 |
| 新建 | `backend/tests/test_drill_public_service.py` | 公共层单元测试 |
| 新建 | `backend/tests/test_drill_user_service.py` | 用户层单元测试 |
| 新建 | `backend/tests/test_drill_orchestration.py` | join 逻辑单元测试 |
| 修改 | `backend/main.py` | 2 个端点改为调 orchestration |
| 修改 | `backend/services/drillable_funds.py` | 标记 deprecated，保留兼容 |

---

## Task 1: 创建公共层 service

### Step 1.1: 写公共层失败测试

**文件：** `backend/tests/test_drill_public_service.py`

```python
"""公共层 service 单元测试 — 只读 fund_drill_snapshot，无 user_id。"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch
from services.drill_public_service import get_public_cards, get_public_detail


class TestGetPublicCards:
    """测试 get_public_cards — 返回所有公共下钻卡片。"""

    def test_returns_cards_grouped_by_index(self):
        """正常情况：按 index_code 分组返回卡片。"""
        db = MagicMock()
        # mock fund_drill_snapshot 查询
        mock_snapshot = MagicMock()
        mock_snapshot.fund_code = "510300.SH"
        mock_snapshot.index_code = "000300"
        mock_snapshot.index_name = "沪深300"
        mock_snapshot.stock_code = "600519.SH"
        mock_snapshot.stock_name = "贵州茅台"
        mock_snapshot.shares_equivalent = 0.001
        mock_snapshot.weight_pct = 5.23
        mock_snapshot.baseline_price = 1500.0
        mock_snapshot.current_price = 1600.0

        db.query.return_value.filter.return_value.filter.return_value.all.return_value = [mock_snapshot]

        cards = get_public_cards(db, date(2026, 6, 24))
        assert len(cards) >= 1
        assert cards[0]["index_code"] == "000300"
        assert "510300.SH" in cards[0]["fund_codes"]
        assert cards[0]["stock_count"] >= 1

    def test_returns_empty_when_no_snapshot(self):
        """无 snapshot 时返回空列表。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        cards = get_public_cards(db, date(2026, 6, 24))
        assert cards == []


class TestGetPublicDetail:
    """测试 get_public_detail — 返回某指数的公共下钻明细。"""

    def test_returns_detail_with_constituents_and_funds(self):
        """正常情况：返回成分股 + 基金列表。"""
        db = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.fund_code = "510300.SH"
        mock_snapshot.index_code = "000300"
        mock_snapshot.index_name = "沪深300"
        mock_snapshot.stock_code = "600519.SH"
        mock_snapshot.stock_name = "贵州茅台"
        mock_snapshot.shares_equivalent = 0.001
        mock_snapshot.weight_pct = 5.23
        mock_snapshot.baseline_price = 1500.0
        mock_snapshot.current_price = 1600.0

        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.all.return_value = [mock_snapshot]
        detail = get_public_detail(db, date(2026, 6, 24), "000300")
        assert detail is not None
        assert detail["index_code"] == "000300"
        assert len(detail["constituents"]) >= 1
        assert len(detail["funds"]) >= 1

    def test_returns_none_when_index_not_found(self):
        """index_code 不存在时返回 None。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.all.return_value = []
        detail = get_public_detail(db, date(2026, 6, 24), "999999")
        assert detail is None
```

### Step 1.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_drill_public_service.py -v
```

**预期：** ImportError — `No module named 'services.drill_public_service'`

### Step 1.3: 实现公共层 service

**文件：** `backend/services/drill_public_service.py`

```python
"""公共下钻 service — 只读 fund_drill_snapshot + fund_index_map 表。
不知道 user_id，不读 Holding 表。可独立复用。

数据来源：scheduler 每日生成的 fund_drill_snapshot 预计算表。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date

from sqlalchemy.orm import Session

from models import FundDrillSnapshot, FundIndexMap

logger = logging.getLogger(__name__)


def get_public_cards(db: Session, as_of: _date) -> list[dict]:
    """返回所有公共下钻卡片（按指数分组）。

    只读 fund_drill_snapshot + fund_index_map，不含任何用户数据。

    返回结构：
    [
        {
            "index_code": "000300",
            "index_name": "沪深300",
            "as_of": "2026-06-24",
            "fund_codes": ["510300.SH", ...],
            "stock_count": 300,
            "total_weight": 1.0,
        },
    ]
    """
    rows = db.query(FundDrillSnapshot).filter(
        FundDrillSnapshot.as_of_date == as_of
    ).all()

    if not rows:
        return []

    by_index: dict[str, dict] = {}
    for r in rows:
        idx_code = (r.index_code or "").split(".")[0]
        if not idx_code:
            continue
        if idx_code not in by_index:
            by_index[idx_code] = {
                "index_code": idx_code,
                "index_name": r.index_name or idx_code,
                "as_of": as_of.isoformat(),
                "fund_codes": set(),
                "stock_set": set(),
                "total_weight": 0.0,
            }
        bucket = by_index[idx_code]
        bucket["fund_codes"].add(r.fund_code)
        bucket["stock_set"].add(r.stock_code)
        bucket["total_weight"] += (r.weight_pct or 0.0) / 100.0

    cards = []
    for bucket in by_index.values():
        cards.append({
            "index_code": bucket["index_code"],
            "index_name": bucket["index_name"],
            "as_of": bucket["as_of"],
            "fund_codes": sorted(bucket["fund_codes"]),
            "stock_count": len(bucket["stock_set"]),
            "total_weight": round(bucket["total_weight"], 4),
        })
    cards.sort(key=lambda c: c["stock_count"], reverse=True)
    return cards


def get_public_detail(db: Session, as_of: _date, index_code: str) -> dict | None:
    """返回某指数的公共下钻明细（成分股 + 基金穿透关系）。

    只读 fund_drill_snapshot + fund_index_map，不含任何用户数据。
    无数据返回 None。

    返回结构：
    {
        "index_code": "000300",
        "index_name": "沪深300",
        "as_of": "2026-06-24",
        "constituents": [
            {"stock_code": "600519.SH", "stock_name": "贵州茅台", "weight_pct": 5.23,
             "baseline_price": 1500.0, "current_price": 1600.0, "shares_equivalent": 0.001},
        ],
        "funds": [
            {"fund_code": "510300.SH", "fund_name": "华泰柏瑞沪深300ETF",
             "shares_equivalent": 1234567.0},
        ],
    }
    """
    idx_code = index_code.split(".")[0]
    rows = db.query(FundDrillSnapshot).filter(
        FundDrillSnapshot.as_of_date == as_of,
        FundDrillSnapshot.index_code.startswith(idx_code),
    ).all()

    if not rows:
        return None

    # 获取基金名称
    fund_codes = list(set(r.fund_code for r in rows))
    fund_maps = db.query(FundIndexMap).filter(
        FundIndexMap.fund_code.in_(fund_codes)
    ).all()
    fund_name_map = {fm.fund_code: fm.index_name or "" for fm in fund_maps}
    index_name = fund_maps[0].index_name if fund_maps else idx_code

    constituents_by_code: dict[str, dict] = {}
    funds_by_code: dict[str, dict] = {}

    for r in rows:
        # 成分股
        if r.stock_code not in constituents_by_code:
            constituents_by_code[r.stock_code] = {
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "weight_pct": r.weight_pct,
                "baseline_price": r.baseline_price,
                "current_price": r.current_price,
                "shares_equivalent": 0.0,
            }
        constituents_by_code[r.stock_code]["shares_equivalent"] += (r.shares_equivalent or 0.0)

        # 基金
        if r.fund_code not in funds_by_code:
            funds_by_code[r.fund_code] = {
                "fund_code": r.fund_code,
                "fund_name": fund_name_map.get(r.fund_code, ""),
                "shares_equivalent": 0.0,
            }
        funds_by_code[r.fund_code]["shares_equivalent"] += (r.shares_equivalent or 0.0)

    constituents = list(constituents_by_code.values())
    constituents.sort(key=lambda c: c.get("weight_pct", 0) or 0, reverse=True)

    funds = list(funds_by_code.values())
    funds.sort(key=lambda f: f["shares_equivalent"], reverse=True)

    return {
        "index_code": idx_code,
        "index_name": index_name,
        "as_of": as_of.isoformat(),
        "constituents": constituents,
        "funds": funds,
    }
```

### Step 1.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_drill_public_service.py -v
```

**预期：** 4 tests passed

### Step 1.5: commit

```bash
git add backend/services/drill_public_service.py backend/tests/test_drill_public_service.py
git commit -m "feat(drill): add public service layer + tests (Task 1)"
```

---

## Task 2: 创建用户层 service

### Step 2.1: 写用户层失败测试

**文件：** `backend/tests/test_drill_user_service.py`

```python
"""用户层 service 单元测试 — 只读 Holding，无下钻结构。"""
import pytest
from unittest.mock import MagicMock
from services.drill_user_service import get_user_fund_codes, get_user_fund_holdings


class TestGetUserFundCodes:
    """测试 get_user_fund_codes — 返回用户可下钻基金代码集合。"""

    def test_returns_fund_codes_for_drillable_asset_types(self):
        """正常情况：返回可下钻 asset_type 的基金代码。"""
        db = MagicMock()
        mock_holding_1 = MagicMock()
        mock_holding_1.security_code = "510300.SH"
        mock_holding_1.asset_type = "a_share_etf"
        mock_holding_1.quantity = 10000.0
        mock_holding_1.amount_cny = 45000.0

        mock_holding_2 = MagicMock()
        mock_holding_2.security_code = "600519.SH"
        mock_holding_2.asset_type = "a_share_equity"
        mock_holding_2.quantity = 100.0
        mock_holding_2.amount_cny = 160000.0

        mock_holding_3 = MagicMock()
        mock_holding_3.security_code = "BOND001"
        mock_holding_3.asset_type = "bond"
        mock_holding_3.quantity = 1000.0
        mock_holding_3.amount_cny = 100000.0

        db.query.return_value.filter.return_value.all.return_value = [
            mock_holding_1, mock_holding_2, mock_holding_3
        ]

        codes = get_user_fund_codes(db, user_id=2)
        assert "510300.SH" in codes
        assert "600519.SH" in codes
        assert "BOND001" not in codes  # bond 不可下钻

    def test_returns_empty_set_when_no_holdings(self):
        """无持仓时返回空集合。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        codes = get_user_fund_codes(db, user_id=999)
        assert codes == set()


class TestGetUserFundHoldings:
    """测试 get_user_fund_holdings — 返回用户在指定基金上的持仓。"""

    def test_returns_holdings_for_specified_funds(self):
        """正常情况：返回指定基金的持仓明细。"""
        db = MagicMock()
        mock_holding = MagicMock()
        mock_holding.security_code = "510300.SH"
        mock_holding.quantity = 10000.0
        mock_holding.amount_cny = 45000.0
        mock_holding.price = 4.5

        db.query.return_value.filter.return_value.filter.return_value.all.return_value = [mock_holding]

        holdings = get_user_fund_holdings(db, user_id=2, fund_codes=["510300.SH"])
        assert "510300.SH" in holdings
        assert holdings["510300.SH"]["quantity"] == 10000.0
        assert holdings["510300.SH"]["amount_cny"] == 45000.0

    def test_returns_empty_dict_when_no_holdings(self):
        """无持仓时返回空字典。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        holdings = get_user_fund_holdings(db, user_id=999, fund_codes=["510300.SH"])
        assert holdings == {}
```

### Step 2.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_drill_user_service.py -v
```

**预期：** ImportError — `No module named 'services.drill_user_service'`

### Step 2.3: 实现用户层 service

**文件：** `backend/services/drill_user_service.py`

```python
"""用户下钻 service — 只读 Holding 表。
不知道下钻结构，不读 fund_drill_snapshot。可独立复用。

可下钻 asset_type：a_share_equity, a_share_etf, hk_equity, qdii_equity, us_etf
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import Holding

logger = logging.getLogger(__name__)

# 可下钻的 asset_type 集合
DRILLABLE_ASSET_TYPES = frozenset({
    "a_share_equity",
    "a_share_etf",
    "hk_equity",
    "qdii_equity",
    "us_etf",
})


def get_user_fund_codes(db: Session, user_id: int) -> set[str]:
    """返回用户持有的所有可下钻基金代码集合。

    过滤 asset_type in DRILLABLE_ASSET_TYPES 且 quantity > 0。

    返回：{"510300.SH", "159919.SZ", ...}
    """
    rows = db.query(Holding).filter(
        Holding.user_id == user_id,
    ).all()

    codes: set[str] = set()
    for h in rows:
        asset_type = (h.asset_type or "").lower()
        if asset_type in DRILLABLE_ASSET_TYPES and (h.quantity or 0) > 0:
            codes.add(h.security_code)
    return codes


def get_user_fund_holdings(db: Session, user_id: int, fund_codes: list[str]) -> dict[str, dict]:
    """返回用户在指定基金上的持仓明细。

    跨买入批次聚合（同一基金多笔买入求和）。

    返回结构：
    {
        "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
    }
    """
    if not fund_codes:
        return {}

    rows = db.query(Holding).filter(
        Holding.user_id == user_id,
        Holding.security_code.in_(fund_codes),
    ).all()

    out: dict[str, dict] = {}
    for h in rows:
        code = h.security_code
        if code not in out:
            out[code] = {
                "quantity": 0.0,
                "amount_cny": 0.0,
                "price": h.price,
            }
        out[code]["quantity"] += (h.quantity or 0.0)
        out[code]["amount_cny"] += (h.amount_cny or 0.0)

    # 计算平均价格
    for code, info in out.items():
        if info["quantity"] > 0:
            info["price"] = info["amount_cny"] / info["quantity"]

    return out
```

### Step 2.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_drill_user_service.py -v
```

**预期：** 4 tests passed

### Step 2.5: commit

```bash
git add backend/services/drill_user_service.py backend/tests/test_drill_user_service.py
git commit -m "feat(drill): add user service layer + tests (Task 2)"
```

---

## Task 3: 创建 orchestration service

### Step 3.1: 写 orchestration 失败测试

**文件：** `backend/tests/test_drill_orchestration.py`

```python
"""orchestration service 单元测试 — join 逻辑。"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from services.drill_orchestration_service import (
    list_drillable_cards,
    get_drill_detail,
)


class TestListDrillableCards:
    """测试 list_drillable_cards — join public + user。"""

    @patch("services.drill_orchestration_service.user_service")
    @patch("services.drill_orchestration_service.public_service")
    def test_returns_filtered_cards_with_est_value(self, mock_public, mock_user):
        """正常情况：过滤公共卡片，只保留用户持有的基金。"""
        db = MagicMock()
        mock_public.get_public_cards.return_value = [
            {
                "index_code": "000300",
                "index_name": "沪深300",
                "as_of": "2026-06-24",
                "fund_codes": ["510300.SH", "159919.SZ"],
                "stock_count": 300,
                "total_weight": 1.0,
            },
            {
                "index_code": "000905",
                "index_name": "中证500",
                "as_of": "2026-06-24",
                "fund_codes": ["510500.SH"],
                "stock_count": 500,
                "total_weight": 1.0,
            },
        ]
        mock_user.get_user_fund_codes.return_value = {"510300.SH"}
        mock_user.get_user_fund_holdings.return_value = {
            "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
        }

        cards = list_drillable_cards(db, date(2026, 6, 24), user_id=2)
        assert len(cards) == 1
        assert cards[0]["index_code"] == "000300"
        assert cards[0]["est_market_value_cny"] == 45000.0
        assert "510300.SH" in cards[0]["user_fund_codes"]

    @patch("services.drill_orchestration_service.user_service")
    @patch("services.drill_orchestration_service.public_service")
    def test_returns_empty_when_no_holdings(self, mock_public, mock_user):
        """用户无持仓时返回空列表。"""
        db = MagicMock()
        mock_public.get_public_cards.return_value = [
            {"index_code": "000300", "fund_codes": ["510300.SH"], "stock_count": 300},
        ]
        mock_user.get_user_fund_codes.return_value = set()

        cards = list_drillable_cards(db, date(2026, 6, 24), user_id=999)
        assert cards == []

    @patch("services.drill_orchestration_service.user_service")
    @patch("services.drill_orchestration_service.public_service")
    def test_returns_empty_when_no_snapshot(self, mock_public, mock_user):
        """无 snapshot 时返回空列表。"""
        db = MagicMock()
        mock_public.get_public_cards.return_value = []
        mock_user.get_user_fund_codes.return_value = {"510300.SH"}

        cards = list_drillable_cards(db, date(2026, 6, 24), user_id=2)
        assert cards == []


class TestGetDrillDetail:
    """测试 get_drill_detail — join public + user，计算 user_drill_shares。"""

    @patch("services.drill_orchestration_service.user_service")
    @patch("services.drill_orchestration_service.public_service")
    def test_returns_detail_with_user_drill_shares(self, mock_public, mock_user):
        """正常情况：返回含 user_drill_shares 的明细。"""
        db = MagicMock()
        mock_public.get_public_detail.return_value = {
            "index_code": "000300",
            "index_name": "沪深300",
            "as_of": "2026-06-24",
            "constituents": [
                {"stock_code": "600519.SH", "stock_name": "贵州茅台",
                 "weight_pct": 5.23, "baseline_price": 1500.0,
                 "current_price": 1600.0, "shares_equivalent": 0.001},
            ],
            "funds": [
                {"fund_code": "510300.SH", "fund_name": "沪深300ETF",
                 "shares_equivalent": 0.001},
            ],
        }
        mock_user.get_user_fund_holdings.return_value = {
            "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
        }

        detail = get_drill_detail(db, date(2026, 6, 24), "000300", user_id=2)
        assert detail is not None
        assert detail["funds"][0]["user_drill_shares"] == 10.0  # 10000 * 0.001
        assert "user_hold_shares" in detail["constituents"][0]

    @patch("services.drill_orchestration_service.user_service")
    @patch("services.drill_orchestration_service.public_service")
    def test_returns_none_when_no_public_detail(self, mock_public, mock_user):
        """公共层无数据时返回 None。"""
        db = MagicMock()
        mock_public.get_public_detail.return_value = None

        detail = get_drill_detail(db, date(2026, 6, 24), "999999", user_id=2)
        assert detail is None

    @patch("services.drill_orchestration_service.user_service")
    @patch("services.drill_orchestration_service.public_service")
    def test_returns_none_when_no_holdings(self, mock_public, mock_user):
        """用户无持仓时返回 None。"""
        db = MagicMock()
        mock_public.get_public_detail.return_value = {
            "index_code": "000300",
            "constituents": [{"stock_code": "600519.SH"}],
            "funds": [{"fund_code": "510300.SH"}],
        }
        mock_user.get_user_fund_holdings.return_value = {}

        detail = get_drill_detail(db, date(2026, 6, 24), "000300", user_id=999)
        assert detail is None
```

### Step 3.2: 运行测试确认失败

```bash
cd backend
python -m pytest tests/test_drill_orchestration.py -v
```

**预期：** ImportError — `No module named 'services.drill_orchestration_service'`

### Step 3.3: 实现 orchestration service

**文件：** `backend/services/drill_orchestration_service.py`

```python
"""下钻编排 service — 唯一耦合点。
调 public service + user service，join 后返回完整结果。

join 公式：
  user_drill_shares = user_quantity × fund.shares_equivalent
  user_hold_shares = total_drill_shares × constituent.weight
  user_hold_value = user_hold_shares × constituent.current_price
"""
from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy.orm import Session

from services import drill_public_service as public_service
from services import drill_user_service as user_service

logger = logging.getLogger(__name__)


def list_drillable_cards(db: Session, as_of: _date, user_id: int) -> list[dict]:
    """返回用户可见的下钻卡片列表。

    join 逻辑：
    1. public.get_public_cards(as_of) → 所有公共卡片
    2. user.get_user_fund_codes(user_id) → 用户基金代码集合
    3. if not user_fund_codes → return []
    4. 过滤：只保留 fund_codes ∩ user_fund_codes 非空的卡片
    5. 计算 est_market_value_cny
    """
    public_cards = public_service.get_public_cards(db, as_of)
    user_fund_codes = user_service.get_user_fund_codes(db, user_id)

    if not user_fund_codes:
        return []

    if not public_cards:
        return []

    # 获取用户持仓明细（用于计算 est_market_value）
    user_holdings = user_service.get_user_fund_holdings(
        db, user_id, list(user_fund_codes)
    )

    result = []
    for card in public_cards:
        overlap = set(card["fund_codes"]) & user_fund_codes
        if not overlap:
            continue
        est_value = sum(
            user_holdings[f]["amount_cny"]
            for f in overlap
            if f in user_holdings
        )
        result.append({
            **card,
            "user_fund_codes": sorted(overlap),
            "est_market_value_cny": round(est_value, 4),
        })

    result.sort(key=lambda c: c.get("est_market_value_cny", 0), reverse=True)
    return result


def get_drill_detail(
    db: Session, as_of: _date, index_code: str, user_id: int
) -> dict | None:
    """返回用户可见的下钻明细。

    join 逻辑：
    1. public.get_public_detail(as_of, index_code) → 公共明细
    2. if not public_detail → return None
    3. user.get_user_fund_holdings(user_id, fund_codes) → 用户持仓
    4. if not user_holdings → return None
    5. join：计算 user_drill_shares / user_hold_shares / user_hold_value
    """
    public_detail = public_service.get_public_detail(db, as_of, index_code)
    if not public_detail:
        return None

    fund_codes = [f["fund_code"] for f in public_detail.get("funds", [])]
    user_holdings = user_service.get_user_fund_holdings(db, user_id, fund_codes)

    if not user_holdings:
        return None

    # join：计算每只基金的 user_drill_shares
    funds_joined = []
    total_drill_shares = 0.0
    for f in public_detail["funds"]:
        h = user_holdings.get(f["fund_code"])
        if not h:
            continue
        user_drill_shares = h["quantity"] * (f.get("shares_equivalent") or 0.0)
        funds_joined.append({
            **f,
            "user_quantity": h["quantity"],
            "user_drill_shares": round(user_drill_shares, 4),
        })
        total_drill_shares += user_drill_shares

    # join：计算每个成分股的 user_hold_shares / user_hold_value
    constituents_joined = []
    for c in public_detail["constituents"]:
        weight = (c.get("weight_pct") or 0.0) / 100.0
        user_hold_shares = total_drill_shares * weight
        current_price = c.get("current_price") or 0.0
        user_hold_value = user_hold_shares * current_price
        constituents_joined.append({
            **c,
            "user_hold_shares": round(user_hold_shares, 4),
            "user_hold_value": round(user_hold_value, 4),
        })

    return {
        **public_detail,
        "funds": funds_joined,
        "constituents": constituents_joined,
        "total_user_drill_shares": round(total_drill_shares, 4),
    }
```

### Step 3.4: 运行测试确认通过

```bash
cd backend
python -m pytest tests/test_drill_orchestration.py -v
```

**预期：** 6 tests passed

### Step 3.5: commit

```bash
git add backend/services/drill_orchestration_service.py backend/tests/test_drill_orchestration.py
git commit -m "feat(drill): add orchestration service + tests (Task 3)"
```

---

## Task 4: 重构 API 端点

### Step 4.1: 找到现有端点位置

```bash
cd backend
grep -n "drillable.indices\|index.drill" main.py
```

**预期输出：**
```
4258: @app.get("/api/penetration/drillable-indices")
...
@app.get("/api/penetration/index-drill")
```

### Step 4.2: 修改 drillable-indices 端点

**文件：** `backend/main.py`

找到 `def get_drillable_indices` 函数（约 line 4258），替换为：

```python
@app.get("/api/penetration/drillable-indices")
def get_drillable_indices(request: Request, db: Session = Depends(get_db)):
    """下钻卡片列表 — 调 orchestration service（三层解耦架构）"""
    from middleware.auth import _resolve_eff_from_request
    from services.drill_orchestration_service import list_drillable_cards
    _u, eff_uid = _resolve_eff_from_request(request, db)
    if eff_uid is None:
        return []
    as_of = _get_as_of_date(db)
    return list_drillable_cards(db, as_of, eff_uid)
```

### Step 4.3: 修改 index-drill 端点

**文件：** `backend/main.py`

找到 `def get_index_drill` 函数，替换为：

```python
@app.get("/api/penetration/index-drill")
def get_index_drill(
    request: Request,
    index_code: str,
    db: Session = Depends(get_db),
):
    """下钻明细 — 调 orchestration service（三层解耦架构）"""
    from middleware.auth import _resolve_eff_from_request
    from services.drill_orchestration_service import get_drill_detail
    _u, eff_uid = _resolve_eff_from_request(request, db)
    if eff_uid is None:
        raise HTTPException(401, "请登录")
    as_of = _get_as_of_date(db)
    result = get_drill_detail(db, as_of, index_code, eff_uid)
    if result is None:
        raise HTTPException(404, "无下钻数据（可能无 snapshot 或无持仓）")
    return result
```

### Step 4.4: 确认 `_get_as_of_date` 存在

```bash
cd backend
grep -n "_get_as_of_date" main.py
```

如果不存在，需要从现有 `get_drillable_indices` 中提取 `as_of` 的获取逻辑。现有逻辑通常是从 `DataVersion` 或 `fund_drill_snapshot` 最新日期获取。

### Step 4.5: 手动验证 API

```bash
# 启动后端（如果未运行）
cd backend
python -m uvicorn main:app --port 8001 --reload

# 登录获取 token（用 admin 账号）
$token = (curl.exe -s -X POST http://127.0.0.1:8001/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

# 测试 drillable-indices（admin 无持仓 → 空列表）
curl.exe -s http://127.0.0.1:8001/api/penetration/drillable-indices -H "Authorization: Bearer $token"

# 测试 drillable-indices（view_as=2 → 有数据）
curl.exe -s "http://127.0.0.1:8001/api/penetration/drillable-indices?view_as=2" -H "Authorization: Bearer $token"

# 测试 index-drill（view_as=2）
curl.exe -s "http://127.0.0.1:8001/api/penetration/index-drill?index_code=000300&view_as=2" -H "Authorization: Bearer $token"
```

**预期：**
- admin 无 view_as → `[]`
- admin view_as=2 → 12 个卡片
- index-drill view_as=2 → 含 user_drill_shares 的明细

### Step 4.6: commit

```bash
git add backend/main.py
git commit -m "refactor(drill): API endpoints call orchestration service (Task 4)"
```

---

## Task 5: 集成测试

### Step 5.1: 写集成测试

**文件：** `backend/tests/test_drill_api_integration.py`

```python
"""下钻 API 集成测试 — 5 用户 × 2 端点 × view_as 矩阵。"""
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


class TestDrillIsolation:
    """下钻数据隔离测试。"""

    def test_admin_no_holdings_returns_empty(self):
        """admin 无持仓 → 空列表。"""
        # 登录 admin
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/penetration/drillable-indices", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_admin_view_as_user2_returns_cards(self):
        """admin view_as=2 → 返回卡片。"""
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/penetration/drillable-indices?view_as=2", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        assert all("est_market_value_cny" in c for c in data)

    def test_user2_returns_own_cards(self):
        """user_id=2 直接登录 → 返回自己的卡片。"""
        resp = client.post("/api/auth/login", json={"username": "advisor", "password": "advisor123"})
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/penetration/drillable-indices", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0

    def test_user_cannot_view_as_others(self):
        """普通用户不能 view_as 他人 → 403。"""
        resp = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get("/api/penetration/drillable-indices?view_as=2", headers=headers)
        assert resp.status_code == 403

    def test_index_drill_returns_detail(self):
        """index-drill 返回含 user_drill_shares 的明细。"""
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 先获取卡片列表
        resp = client.get("/api/penetration/drillable-indices?view_as=2", headers=headers)
        cards = resp.json()
        if not cards:
            pytest.skip("无下钻卡片")

        index_code = cards[0]["index_code"]
        resp = client.get(
            f"/api/penetration/index-drill?index_code={index_code}&view_as=2",
            headers=headers,
        )
        assert resp.status_code == 200
        detail = resp.json()
        assert "constituents" in detail
        assert "funds" in detail
        assert "total_user_drill_shares" in detail

    def test_index_drill_404_when_no_data(self):
        """index-drill 无数据 → 404。"""
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = client.get(
            "/api/penetration/index-drill?index_code=999999&view_as=2",
            headers=headers,
        )
        assert resp.status_code == 404
```

### Step 5.2: 运行集成测试

```bash
cd backend
python -m pytest tests/test_drill_api_integration.py -v
```

**预期：** 6 tests passed（如果数据库有 user_id=2 的持仓数据）

### Step 5.3: 运行全部下钻测试

```bash
cd backend
python -m pytest tests/test_drill_*.py -v
```

**预期：** 16 tests passed（4+4+6+6... 实际数量取决于 mock 测试）

### Step 5.4: commit

```bash
git add backend/tests/test_drill_api_integration.py
git commit -m "test(drill): add API integration tests (Task 5)"
```

---

## Task 6: 废弃旧文件 + 最终验证

### Step 6.1: 标记 drillable_funds.py 为 deprecated

**文件：** `backend/services/drillable_funds.py`

在文件顶部 docstring 后添加：

```python
"""[DEPRECATED 2026-06-24] 此模块已被三层 service 替代：
- drill_public_service.py（公共层）
- drill_user_service.py（用户层）
- drill_orchestration_service.py（join 层）

保留此文件用于参考和兼容性检查。新代码请勿 import。
"""
```

### Step 6.2: 检查是否有其他地方 import drillable_funds

```bash
cd backend
grep -rn "from services.drillable_funds\|import drillable_funds" --include="*.py" .
```

**预期：** 只有 main.py 中的旧端点（已在 Task 4 中替换）。如果有其他引用，需要更新。

### Step 6.3: 运行全部测试

```bash
cd backend
python -m pytest tests/ -v
```

**预期：** 所有测试通过

### Step 6.4: 前端验证

在浏览器中打开 http://localhost:5173/，用 admin 登录：
1. 不选 view_as → 分析页下钻为空（admin 无持仓）
2. 选择 view_as=2（李顾问）→ 分析页显示 12 个下钻卡片
3. 点击卡片 → 显示下钻明细

### Step 6.5: 最终 commit

```bash
git add backend/services/drillable_funds.py
git commit -m "chore(drill): deprecate old drillable_funds.py (Task 6)"
```

---

## 自审清单

- [x] Spec coverage: 每个需求都有对应 task
  - 公共层 service → Task 1
  - 用户层 service → Task 2
  - orchestration service → Task 3
  - API 端点重构 → Task 4
  - 集成测试 → Task 5
  - 废弃旧文件 → Task 6
- [x] Placeholder scan: 无 TBD/TODO
- [x] Type consistency: 接口签名跨 task 一致
- [x] 每步包含完整代码
- [x] 每步包含预期输出
- [x] TDD: 先写测试 → 确认失败 → 实现 → 确认通过 → commit
