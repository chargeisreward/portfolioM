# 下钻架构重新设计

**日期：** 2026-06-24
**分支：** feature/auth-upgrade
**状态：** 已定稿，待实施

---

## 1. 背景与痛点

### 当前架构

```
API (main.py)
  └─ drillable_funds.py
       ├─ 读 fund_drill_snapshot（公共表）
       ├─ 读 Holding（用户表）
       └─ 实时 join + 计算
```

`drillable_funds.py` 同时读公共表和用户表，在同一个函数内完成 join，职责耦合严重。

### 核心痛点

**公共层/用户层耦合**：
- `list_drillable_indices` 同时查 `fund_drill_snapshot`（公共）和 `Holding`（用户），在内存中 join
- `get_index_drill_detail` 同样混合查公共 snapshot 和用户 Holding
- 公共层无法独立复用（如给 admin 看公共穿透结构、给数据管理页验证 snapshot 完整性）
- 测试困难：无法单独测公共层或用户层

### 当前数据状态

| 表 | 行数 | 说明 |
|----|------|------|
| `fund_drill_snapshot` | 5123 | 公共下钻截面，3 个日期（6/22, 6/23, 6/24） |
| `fund_index_map` | 15 | 基金→指数映射 |
| `Holding` | 44 | 用户持仓，全部属于 user_id=2 |
| `index_constituent_snapshot` | 1359 | 指数成分股 |

---

## 2. 设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 核心方向 | 重新设计下钻架构 | 根治耦合问题 |
| 解耦方式 | API 分离 + service join | 公共层和用户层独立，join 集中 |
| join 位置 | 后端 service join | 前端简单，后端分层清晰 |
| 公共层数据源 | 保留 fund_drill_snapshot 预计算表 | scheduler 每日生成，查询快 |
| API 端点 | 保留现有端点 + 内部重构 | 前端无感知 |
| 无持仓体验 | 返回空，通过 view_as 切换 | 已有用户切换设计，无需兜底 |
| service 层方案 | 三层 service（public/user/orchestration） | 职责最清晰，彻底解耦 |

---

## 3. 架构设计

### 3.1 三层 service 分层

```
┌─────────────────────────────────────────────────────────┐
│  API 层 (main.py)                                        │
│  GET /api/penetration/drillable-indices                  │
│  GET /api/penetration/index-drill                        │
│  （保留端点，内部改为调 orchestration service）            │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  orchestration service (drill_orchestration_service.py)  │
│  - list_drillable_cards(as_of, user_id)                  │
│  - get_drill_detail(as_of, index_code, user_id)          │
│  职责：调 public + user，join，返回完整结果               │
└────────┬─────────────────────────┬──────────────────────┘
         │                         │
┌────────▼───────────┐  ┌─────────▼──────────────────────┐
│ public service      │  │ user service                    │
│ (drill_public_      │  │ (drill_user_service.py)         │
│  service.py)        │  │                                 │
│                     │  │ - get_user_fund_codes(user_id)  │
│ - get_public_cards  │  │   → {fund_code, ...}            │
│   (as_of)           │  │ - get_user_fund_holdings(       │
│ - get_public_detail │  │     user_id, fund_codes)        │
│   (as_of, idx_code) │  │   → {fund_code: {qty, amount}}  │
│                     │  │                                 │
│ 只读 fund_drill_    │  │ 只读 Holding 表                  │
│ snapshot 表         │  │                                 │
└─────────────────────┘  └─────────────────────────────────┘
```

### 3.2 数据流

**list_drillable_cards 流程：**

```
1. API 收到请求 → _resolve_eff_from_request → eff_uid
2. orchestration.list_drillable_cards(as_of, eff_uid)
3.   ├─ public.get_public_cards(as_of) → 所有公共卡片（无用户数据）
4.   ├─ user.get_user_fund_codes(eff_uid) → 用户持有的基金代码集合
5.   ├─ if user_fund_codes 为空 → return []
6.   └─ join：过滤公共卡片，只保留 fund_codes ∩ user_fund_codes 非空的卡片
7. API 返回 JSON
```

**get_drill_detail 流程：**

```
1. API 收到请求 → _resolve_eff_from_request → eff_uid
2. orchestration.get_drill_detail(as_of, index_code, eff_uid)
3.   ├─ public.get_public_detail(as_of, index_code) → 公共明细（无用户数据）
4.   ├─ if public_detail is None → return None → API 404
5.   ├─ user.get_user_fund_holdings(eff_uid, fund_codes) → 用户持仓
6.   ├─ if user_holdings 为空 → return None → API 404
7.   └─ join：
8.        ├─ 每只基金：user_drill_shares = quantity × shares_equivalent
9.        ├─ 每个成分股：user_hold_shares = total_drill_shares × weight
10.       └─ user_hold_value = user_hold_shares × current_price
11. API 返回 JSON
```

### 3.3 关键原则

- **public service 不知道 user_id**：纯公共数据，可独立复用
- **user service 不知道下钻结构**：纯持仓数据，可独立复用
- **orchestration 是唯一耦合点**：所有 join 逻辑集中在这里
- **无持仓 → 空列表/None**：orchestration 发现 user 持仓为空时直接返回

---

## 4. 接口定义

### 4.1 公共层 service

**文件：** `backend/services/drill_public_service.py`

```python
"""公共下钻 service — 只读 fund_drill_snapshot 表。不知道 user_id，不读 Holding 表。"""

def get_public_cards(db, as_of: date) -> list[dict]:
    """返回所有公共下钻卡片（按指数分组）。
    
    返回结构：
    [
        {
            "index_code": "000300.SH",
            "index_name": "沪深300",
            "as_of": "2026-06-24",
            "fund_codes": ["510300.SH", "159919.SZ", ...],
            "stock_count": 300,
            "total_weight": 1.0,
        },
    ]
    """

def get_public_detail(db, as_of: date, index_code: str) -> dict | None:
    """返回某指数的公共下钻明细（成分股 + 基金穿透关系）。
    
    返回结构：
    {
        "index_code": "000300.SH",
        "index_name": "沪深300",
        "as_of": "2026-06-24",
        "constituents": [
            {"stock_code": "600519.SH", "stock_name": "贵州茅台", "weight": 0.0523, "sw_l1": "食品饮料"},
        ],
        "funds": [
            {"fund_code": "510300.SH", "fund_name": "华泰柏瑞沪深300ETF", "shares_equivalent": 1234567.0},
        ],
    }
    无数据返回 None。
    """
```

### 4.2 用户层 service

**文件：** `backend/services/drill_user_service.py`

```python
"""用户下钻 service — 只读 Holding 表。不知道下钻结构，不读 fund_drill_snapshot。"""

def get_user_fund_codes(db, user_id: int) -> set[str]:
    """返回用户持有的所有可下钻基金代码集合。
    
    过滤 asset_type in ("a_share_equity", "a_share_etf", "hk_equity", "qdii_equity", "us_etf")
    返回：{"510300.SH", "159919.SZ", ...}
    """

def get_user_fund_holdings(db, user_id: int, fund_codes: list[str]) -> dict[str, dict]:
    """返回用户在指定基金上的持仓明细。
    
    返回结构：
    {
        "510300.SH": {"quantity": 10000.0, "amount_cny": 45000.0, "price": 4.5},
    }
    """
```

### 4.3 orchestration service

**文件：** `backend/services/drill_orchestration_service.py`

```python
"""下钻编排 service — 唯一耦合点。调 public + user，join 后返回完整结果。"""

def list_drillable_cards(db, as_of: date, user_id: int) -> list[dict]:
    """返回用户可见的下钻卡片列表。
    
    join 逻辑：
    1. public.get_public_cards(as_of) → 所有公共卡片
    2. user.get_user_fund_codes(user_id) → 用户基金代码集合
    3. if not user_fund_codes → return []
    4. 过滤：只保留 fund_codes ∩ user_fund_codes 非空的卡片
    5. 计算 est_market_value_cny
    """

def get_drill_detail(db, as_of: date, index_code: str, user_id: int) -> dict | None:
    """返回用户可见的下钻明细。
    
    join 逻辑：
    1. public.get_public_detail(as_of, index_code) → 公共明细
    2. if not public_detail → return None
    3. user.get_user_fund_holdings(user_id, fund_codes) → 用户持仓
    4. if not user_holdings → return None
    5. join：计算 user_drill_shares / user_hold_shares / user_hold_value
    """
```

---

## 5. API 端点重构

**文件：** `backend/main.py`

```python
@app.get("/api/penetration/drillable-indices")
def get_drillable_indices(request: Request, db: Session = Depends(get_db)):
    """下钻卡片列表 — 调 orchestration service"""
    from middleware.auth import _resolve_eff_from_request
    from services.drill_orchestration_service import list_drillable_cards
    _u, eff_uid = _resolve_eff_from_request(request, db)
    as_of = _get_as_of_date(db)
    return list_drillable_cards(db, as_of, eff_uid)

@app.get("/api/penetration/index-drill")
def get_index_drill(request: Request, index_code: str, db: Session = Depends(get_db)):
    """下钻明细 — 调 orchestration service"""
    from middleware.auth import _resolve_eff_from_request
    from services.drill_orchestration_service import get_drill_detail
    _u, eff_uid = _resolve_eff_from_request(request, db)
    as_of = _get_as_of_date(db)
    result = get_drill_detail(db, as_of, index_code, eff_uid)
    if result is None:
        raise HTTPException(404, "无下钻数据（可能无 snapshot 或无持仓）")
    return result
```

---

## 6. 错误处理矩阵

| 场景 | public service | user service | orchestration | API 响应 |
|------|---------------|-------------|---------------|---------|
| 正常 | 返回公共结构 | 返回持仓 | join 成功 | 200 + 数据 |
| 无 snapshot | 返回 `[]`/`None` | — | 返回 `[]`/`None` | 200 + 空列表 / 404 |
| 用户无持仓 | 返回公共结构 | 返回 `{}` | 返回 `[]`/`None` | 200 + 空列表 / 404 |
| index_code 不存在 | 返回 `None` | — | 返回 `None` | 404 |
| view_as 无权限 | — | — | — | 403（_resolve_eff 处理） |

---

## 7. 测试策略

```
tests/
├── test_drill_public_service.py      # 公共层单元测试
│   ├── test_get_public_cards         # mock fund_drill_snapshot，验证分组
│   └── test_get_public_detail        # mock fund_drill_snapshot，验证过滤
├── test_drill_user_service.py        # 用户层单元测试
│   ├── test_get_user_fund_codes      # mock Holding，验证 asset_type 过滤
│   └── test_get_user_fund_holdings   # mock Holding，验证 fund_codes 过滤
├── test_drill_orchestration.py       # join 逻辑单元测试
│   ├── test_list_drillable_cards     # mock public + user，验证过滤+join
│   ├── test_get_drill_detail         # mock public + user，验证 shares 计算
│   ├── test_no_holdings              # 用户无持仓 → []
│   └── test_no_snapshot             # 无 snapshot → []
└── test_drill_api_integration.py     # API 集成测试
    ├── test_5_users_matrix           # 5 用户 × 2 端点 × 6 view_as
    └── test_admin_view_as            # admin view_as 切换用户
```

---

## 8. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `backend/services/drill_public_service.py` | 公共层 service |
| 新建 | `backend/services/drill_user_service.py` | 用户层 service |
| 新建 | `backend/services/drill_orchestration_service.py` | join 层 service |
| 修改 | `backend/main.py` | 2 个端点内部改为调 orchestration |
| 废弃 | `backend/services/drillable_funds.py` | 旧逻辑迁入三层 service（保留文件但标记 deprecated） |
| 新建 | `backend/tests/test_drill_public_service.py` | 公共层单元测试 |
| 新建 | `backend/tests/test_drill_user_service.py` | 用户层单元测试 |
| 新建 | `backend/tests/test_drill_orchestration.py` | join 逻辑单元测试 |
| 新建 | `backend/tests/test_drill_api_integration.py` | API 集成测试 |

---

## 9. join 公式

### 9.1 list_drillable_cards

```
对每个公共卡片 card:
    overlap = card.fund_codes ∩ user_fund_codes
    if overlap 为空 → 跳过
    est_market_value_cny = sum(user_holdings[f].amount_cny for f in overlap)
    返回 {...card, user_fund_codes: overlap, est_market_value_cny}
```

### 9.2 get_drill_detail

```
对每个基金 fund:
    user_drill_shares = user_holdings[fund.code].quantity × fund.shares_equivalent

total_drill_shares = sum(各基金的 user_drill_shares)

对每个成分股 constituent:
    user_hold_shares = total_drill_shares × constituent.weight
    user_hold_value = user_hold_shares × constituent.current_price
```

---

## 10. 不在本次范围内

- 前端改动：前端无感知，不需要修改
- 新增下钻维度（行业/概念/产业链）：本次只重构现有基金→指数→成分股下钻
- scheduler 改动：fund_drill_snapshot 生成逻辑不变
- 性能优化：本次只重构架构，不优化查询性能
