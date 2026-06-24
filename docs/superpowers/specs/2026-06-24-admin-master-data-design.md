# 管理员数据运维管理重构 — 子项目 1 设计文档

> **日期**: 2026-06-24
> **范围**: 子项目 1（侧边栏重构 + 证券主数据 + 数据源管理）
> **状态**: 设计已确认，待写实施计划
> **后续子项目**: 子项目 2（内容上传套件）、子项目 3（yfinance 集成）

## 1. 背景与目标

### 1.1 问题

当前管理员功能散落在 5 个侧边栏项（数据/运维/数据补足/API策略/管理员设置）中，存在以下问题：

- **AdminSettingsPanel 功能简陋**：仅 4 个按钮 + 硬编码数据状态卡片
- **证券主数据缺失**：基金属性（asset_type、是否可下钻）散落在 Holding 和 FundIndexMap 中，无统一管理入口
- **drill flag 硬编码**：`DRILLABLE_ASSET_TYPES` 在 `drill_user_service.py` 中硬编码，无法通过 UI 配置
- **任务监控缺失**：scheduler 有 JOB_DISPATCH 但无执行历史记录，无法查看任务完成度
- **数据就绪不可见**：管理员无法一眼看出当前业务日期下哪些数据已就绪、哪些缺失

### 1.2 目标

- 重构侧边栏为"用户区 + 分割线 + 管理员区"结构，高内聚低耦合
- 新建 `SecurityMaster` 表统一管理证券主数据（含基金/ETF/股票/债券）
- `is_drillable` 字段替代硬编码 `DRILLABLE_ASSET_TYPES`
- 新建 `DataPullTask` 表记录任务执行历史
- 数据就绪仪表盘 + 任务历史 + API策略 + 交易日历整合为"数据源"页

### 1.3 不在范围内

- 内容上传（指数构成 PDF、股票报告、产业链报告、财务数据手动上传）→ 子项目 2
- yfinance 集成（非中港 PE/PB/PS）→ 子项目 3
- 前端样式大改（保持现有设计语言）

## 2. 侧边栏重构

### 2.1 新菜单结构

```
┌─────────────────────┐
│  总览               │  user/advisor/admin
│  分析               │  user/advisor/admin
│  分析师             │  user/advisor/admin
│  关注               │  user/advisor/admin
│  交易               │  user
│  关联               │  user/advisor
│  设置               │  user/advisor/admin
├─────────────────────┤  灰色分割线（仅 admin 可见时显示）
│  主数据             │  admin
│  数据源             │  admin
│  内容上传           │  admin（子项目2占位，显示"即将上线"）
└─────────────────────┘
```

### 2.2 实现要点

1. **TABS 数组重排**：按上述顺序排列，移除旧的 `data`/`ops`/`dataGap`/`strategies`/`adminSettings` 5 项，替换为 `masterData`/`dataSource`/`contentUpload` 3 项
2. **分割线**：在 `visibleTabs` 渲染时，如果 `effectiveRole === 'admin'`，在"设置"和"主数据"之间插入 `<div className="sidebar-divider" />`
3. **权限不变**：非 admin 看不到分割线以下的任何项

### 2.3 旧组件处理

| 旧组件 | 处理方式 |
|---|---|
| `AdminSettingsPanel.jsx` | 拆分到主数据/数据源页，删除 |
| `DataBrowser.jsx` | 移入数据源页"数据浏览"tab |
| `DataGapPanel.jsx` | 并入数据源页"数据就绪"tab |
| `OpsPanel.jsx` | 并入数据源页"任务历史"tab |
| `StrategiesPanel.jsx` | 并入数据源页"API策略"tab |
| `TradingCalendarView.jsx` | 移入数据源页"交易日历"tab |

## 3. 数据模型

### 3.1 新建 SecurityMaster 表

```python
class SecurityMaster(Base):
    """证券主数据 — 涵盖基金（含ETF）、股票、债券等所有持仓及下钻证券。"""
    __tablename__ = "security_master"

    security_code = Column(String(20), primary_key=True)   # "510300.SH" / "600519.SH" / "NVDA"
    security_name = Column(String(80))
    security_type = Column(String(20), nullable=False)     # fund / stock / bond
    fund_type = Column(String(20))                         # 仅 fund: etf(场内) / otc(场外)
    asset_type = Column(String(30))                        # a_share_equity / a_share_etf / hk_equity / qdii_equity / us_etf / us_equity
    market = Column(String(8))                             # CN / HK / US / OF
    is_drillable = Column(Boolean, default=False)          # 仅 fund 可下钻；stock 恒 False
    index_code = Column(String(20))                        # 仅 fund 有：跟踪指数
    index_name = Column(String(80))
    benchmark_formula = Column(String(500))                # 仅 fund 有：业绩比较基准
    premium_discount = Column(Float)                       # 仅 ETF：折溢价率（预留接口，暂不计算）
    note = Column(String(200))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(Integer)                           # 最后修改人 user_id
```

**字段适用矩阵：**

| 字段 | fund(otc) | fund(etf) | stock | bond |
|---|---|---|---|---|
| fund_type | otc | etf | - | - |
| is_drillable | ✅ 可改 | ✅ 可改 | False | False |
| index_code | ✅ | ✅ | - | - |
| benchmark_formula | ✅ | ✅ | - | - |
| premium_discount | - | 预留 | - | - |

### 3.2 新建 DataPullTask 表

```python
class DataPullTask(Base):
    """数据拉取任务执行记录。"""
    __tablename__ = "data_pull_task"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(60), nullable=False, index=True)    # "crawl_cn_prices"
    job_name = Column(String(100))                              # "拉取A股价格"
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime)
    status = Column(String(20), nullable=False)                 # SUCCESS / FAILED / RUNNING / SKIPPED
    records_pulled = Column(Integer, default=0)
    error_message = Column(Text)
    triggered_by = Column(String(40))                           # scheduler / manual:<user_id>
    created_at = Column(DateTime, default=datetime.utcnow)
```

### 3.3 数据就绪检查（服务层，无新表）

```python
# services/data_readiness_service.py
def get_data_readiness(db, as_of: date) -> list[dict]:
    """检查当前业务日期下各数据源是否就绪。
    返回 [{source, expected, actual, status, last_updated}, ...]
    """
```

检查项：
1. CN价格 — 持仓中 CN 股票/基金数 vs 当日有价格的 CN 证券数（查 FundDailyNav / AShareDailyPrice 等）
2. HK价格 — 持仓中 HK 股票数 vs 当日有价格的 HK 股票数
3. US价格 — 持仓中 US 股票数 vs 当日有价格的 US 股票数
4. 财务数据 — 持仓股票数 vs 有财务快照的股票数（查 AShareFinancialSnapshot + HKShareFinancialSnapshot）
5. 成分股 — IndexConstituentSnapshot 当日记录数
6. 下钻snapshot — FundDrillSnapshot 当日记录数

## 4. 主数据页 (MasterDataPanel)

### 4.1 页面结构

两个 tab：
1. **证券主数据** — SecurityMaster 表的 CRUD
2. **基金-指数映射** — FundIndexMap 表的 CRUD

### 4.2 证券主数据 tab

**功能：**
- 分页列表 + 筛选（类型/市场/可下钻）+ 搜索
- 行内编辑或抽屉编辑
- 新增证券
- 从持仓同步（扫描 Holding 表，为不在 SecurityMaster 中的证券创建记录，已存在的不覆盖）
- 从下钻同步（扫描 FundDrillSnapshot 表，为不在 SecurityMaster 中的下钻股票创建记录）
- 初始化（一次性从 FundIndexMap + Holding + FundDrillSnapshot 批量导入，已存在的不覆盖）

**API：**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/security-master?type=&market=&drillable=&search=&page=` | 分页+筛选 |
| POST | `/api/admin/security-master` | 新增 |
| PUT | `/api/admin/security-master/{security_code}` | 编辑 |
| DELETE | `/api/admin/security-master/{security_code}` | 删除（有持仓时禁止） |
| POST | `/api/admin/security-master/sync-from-holdings` | 从持仓同步 |
| POST | `/api/admin/security-master/sync-from-drill` | 从下钻同步 |
| POST | `/api/admin/security-master/init` | 初始化 |

### 4.3 基金-指数映射 tab

复用现有 `FundIndexMap` 模型，提供 CRUD：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/fund-index-map?search=` | 列表 |
| POST | `/api/admin/fund-index-map` | 新增 |
| PUT | `/api/admin/fund-index-map/{fund_code}/{as_of_date}` | 编辑 |
| DELETE | `/api/admin/fund-index-map/{fund_code}/{as_of_date}` | 删除 |

### 4.4 is_drillable 联动

`drill_user_service.get_user_fund_codes()` 改为 join SecurityMaster：

```python
def get_user_fund_codes(db, user_id):
    return set(
        r[0] for r in db.query(Holding.security_code)
        .join(SecurityMaster, Holding.security_code == SecurityMaster.security_code)
        .filter(Holding.user_id == user_id)
        .filter(SecurityMaster.is_drillable == True)
        .all()
    )
```

`DRILLABLE_ASSET_TYPES` 常量标记 deprecated，保留作为 fallback（SecurityMaster 表为空时使用）。

## 5. 数据源页 (DataSourcePanel)

### 5.1 页面结构

5 个 tab：
1. **数据就绪** — 仪表盘显示各数据源就绪状态
2. **任务历史** — DataPullTask 记录查询
3. **API策略** — 策略列表 + 代码映射 CRUD
4. **交易日历** — 复用 TradingCalendarView
5. **数据浏览** — 复用 DataBrowser

### 5.2 数据就绪 tab

**后端服务**: `services/data_readiness_service.py`

**API**: `GET /api/admin/data-readiness?as_of_date=`

**展示**：
- 表格：数据源 | 期望记录数 | 实际记录数 | 状态(✅/❌/⚠️) | 最后更新时间
- 手动触发拉取按钮

### 5.3 任务历史 tab

**后端服务**: `services/data_pull_task_service.py`

**API**:
- `GET /api/admin/data-pull-tasks?status=&date_from=&date_to=&page=`
- `POST /api/admin/data-pull-tasks/trigger/{job_id}` — 手动触发

**scheduler 集成**: 在 `services/scheduler.py` 的 `JOB_DISPATCH` 执行器中，每次执行前后调用 `record_task()` 记录开始/结束时间、状态、记录数、错误信息。

### 5.4 API策略 tab

整合现有 `StrategiesPanel` + `ApiCodeMap` CRUD：
- **策略列表子 tab**: 显示所有 API 策略 + 描述 + 覆盖证券数
- **代码映射子 tab**: 现有 `ApiCodeMap` 的 CRUD（code_in → code_out per strategy）

### 5.5 交易日历 tab

复用现有 `TradingCalendarView` 组件，无大改动。

### 5.6 数据浏览 tab

复用现有 `DataBrowser` 组件，无改动。

## 6. 后端服务层

```
backend/services/
├── security_master_service.py    ← 新建：证券主数据 CRUD + 同步
├── data_readiness_service.py     ← 新建：数据就绪检查
├── data_pull_task_service.py     ← 新建：任务执行记录
├── drill_user_service.py         ← 修改：join SecurityMaster
└── scheduler.py                  ← 修改：执行前后调 record_task()
```

**服务职责：**

| 服务 | 职责 | 依赖 |
|---|---|---|
| `security_master_service` | CRUD + 从 Holding/Drill 同步 + 初始化 | SecurityMaster, Holding, FundDrillSnapshot, FundIndexMap |
| `data_readiness_service` | 检查 6 类数据源的就绪状态 | 各数据表 |
| `data_pull_task_service` | 记录/查询任务执行历史 | DataPullTask |
| `drill_user_service` (改) | join SecurityMaster 过滤 is_drillable | Holding, SecurityMaster |

## 7. 前端组件树

```
App.jsx
├── MasterDataPanel.jsx          ← 新建
│   ├── SecurityMasterTab.jsx    ← 新建
│   └── FundIndexMapTab.jsx      ← 新建
├── DataSourcePanel.jsx          ← 新建
│   ├── DataReadinessTab.jsx     ← 新建
│   ├── TaskHistoryTab.jsx       ← 新建
│   ├── ApiStrategyTab.jsx       ← 新建（整合 StrategiesPanel）
│   ├── TradingCalendarView.jsx  ← 复用
│   └── DataBrowser.jsx          ← 复用
└── ContentUploadPanel.jsx       ← 新建（占位，子项目2实现）
```

**移除的旧组件**：`AdminSettingsPanel.jsx`、`OpsPanel.jsx`、`DataGapPanel.jsx`（功能拆分到上述新组件）。

## 8. 测试策略

| 层级 | 测试文件 | 测试数 | 覆盖 |
|---|---|---|---|
| security_master_service | `test_security_master_service.py` | ~8 | CRUD + 同步 + 初始化 |
| data_readiness_service | `test_data_readiness_service.py` | ~6 | 各数据源检查 |
| data_pull_task_service | `test_data_pull_task_service.py` | ~5 | 记录 + 查询 |
| drill_user_service (改) | 修改 `test_drill_user_service.py` | +2 | join SecurityMaster |
| API 集成 | `test_admin_master_data_api.py` | ~8 | 端到端 |
| API 集成 | `test_admin_data_source_api.py` | ~6 | 端到端 |

## 9. 迁移计划

1. 创建 `SecurityMaster` 表 + `DataPullTask` 表（SQLAlchemy 自动建表）
2. 运行 `/api/admin/security-master/init` 从现有数据初始化
3. 修改 `drill_user_service` join SecurityMaster
4. 修改 `scheduler.py` 集成 `record_task()`
5. 运行全部测试确认无回归
6. `DRILLABLE_ASSET_TYPES` 常量标记 deprecated，保留作为 fallback

## 10. 鉴权

所有新端点走 `/api/admin/` 前缀，复用现有 `X-Admin-Token` 请求头鉴权机制（`main.py` 第 597-602 行）。前端 admin 页面的 axios 请求自动注入 `X-Admin-Token`。

## 11. 后续子项目

### 子项目 2：内容上传套件

- 指数构成 PDF 上传 + 自动解析
- 股票分析报告上传
- 产业链分析报告上传
- 财务数据手动上传（API 缺口补足）
- 共享文件上传基础设施

### 子项目 3：yfinance 集成

- 后端 yfinance service
- 非中港市场（US 等）PE/PB/PS 自动补足
- scheduler 定时任务
- 仅用于此用途，节省限流额度
