# PortfolioM 项目开发文档

## 项目名称

PortfolioM — 多用户投资组合管理系统

## 项目目的

为投资顾问和个人投资者提供组合管理、下钻分析、数据运维的一体化平台。支持多用户隔离、角色权限（user/advisor/admin）、基金下钻分析、证券主数据管理、数据源监控等功能。

## 工作区文件结构

```
PortfolioM/
├── backend/                    # FastAPI 后端
│   ├── models.py               # SQLAlchemy ORM 模型
│   ├── main.py                 # FastAPI 应用 + API 端点
│   ├── database.py             # 数据库连接
│   ├── migrate_admin_columns.py # 管理员扩展列迁移脚本
│   ├── services/               # 业务服务层
│   │   ├── security_master_service.py    # 证券主数据 CRUD + 同步
│   │   ├── data_readiness_service.py     # 数据就绪检查
│   │   ├── data_pull_task_service.py     # 任务执行记录
│   │   ├── drill_public_service.py       # 下钻公共层
│   │   ├── drill_user_service.py         # 下钻用户层
│   │   ├── drill_orchestration_service.py # 下钻编排层
│   │   └── scheduler.py                  # 定时任务调度
│   └── tests/                  # pytest 测试
├── frontend/                   # React 前端
│   └── src/
│       ├── App.jsx             # 主应用 + 侧边栏
│       ├── api.js              # axios 实例
│       └── components/         # UI 组件
│           ├── MasterDataPanel.jsx       # 主数据页
│           ├── SecurityMasterTab.jsx     # 证券主数据 tab
│           ├── FundIndexMapTab.jsx       # 基金-指数映射 tab
│           ├── DataSourcePanel.jsx       # 数据源页
│           ├── DataReadinessTab.jsx      # 数据就绪 tab
│           ├── TaskHistoryTab.jsx        # 任务历史 tab
│           ├── ApiStrategyTab.jsx        # API策略 tab
│           └── ContentUploadPanel.jsx    # 内容上传占位（子项目2）
├── docs/superpowers/           # 设计文档和实施计划
│   ├── specs/                  # 设计 spec
│   └── plans/                  # 实施计划
└── .worktrees/                 # Git worktree 工作区
    └── auth-upgrade/           # 当前开发分支
```

## 技术栈

- **后端**：FastAPI + SQLAlchemy + SQLite(dev)/PostgreSQL(prod)
- **前端**：React + Vite
- **测试**：pytest（后端）+ npm build（前端）
- **调度**：APScheduler
- **认证**：Session Token + 角色权限（user/advisor/admin）

## API 描述

### 用户区 API

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/auth/login` | POST | 登录 |
| `/api/holdings` | GET | 持仓查询 |
| `/api/penetration/*` | GET | 下钻分析 |
| `/api/watchlist/*` | GET/POST/DELETE | 关注清单 |

### 管理员区 API（需 admin 角色）

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/admin/security-master` | GET/POST | 证券主数据列表/新增 |
| `/api/admin/security-master/{code}` | PUT/DELETE | 编辑/删除证券 |
| `/api/admin/security-master/sync-from-holdings` | POST | 从持仓同步 |
| `/api/admin/security-master/sync-from-drill` | POST | 从下钻同步 |
| `/api/admin/security-master/init` | POST | 初始化主数据 |
| `/api/admin/fund-index-map` | GET/POST | 基金-指数映射列表/新增 |
| `/api/admin/fund-index-map/{code}/{date}` | PUT/DELETE | 编辑/删除映射 |
| `/api/admin/data-readiness` | GET | 数据就绪状态 |
| `/api/admin/data-pull-tasks` | GET | 任务历史查询 |
| `/api/admin/data-pull-tasks/trigger/{job_id}` | POST | 手动触发任务 |

## 数据库设计

### security_master（证券主数据表）

| 字段 | 类型 | 说明 |
|------|------|------|
| security_code | VARCHAR(20) PK | 证券代码 |
| security_name | VARCHAR(100) | 证券名称 |
| currency | VARCHAR(10) | 原币种 |
| asset_type | VARCHAR(20) | 资产类型 |
| type2 | VARCHAR(20) | 主题类型 |
| exchange | VARCHAR(20) | 交易所 |
| security_type | VARCHAR(20) | fund/stock/bond |
| fund_type | VARCHAR(20) | etf(场内)/otc(场外) |
| market | VARCHAR(8) | CN/HK/US/OF |
| is_drillable | BOOLEAN | 是否可下钻 |
| index_code | VARCHAR(20) | 跟踪指数代码 |
| index_name | VARCHAR(80) | 跟踪指数名称 |
| benchmark_formula | VARCHAR(500) | 业绩比较基准 |
| premium_discount | FLOAT | 折溢价率（预留） |
| note | VARCHAR(200) | 备注 |
| updated_by | INTEGER | 最后修改人 |
| updated_at | DATETIME | 更新时间 |

### data_pull_task（数据拉取任务表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增ID |
| job_id | VARCHAR(60) | 任务标识 |
| job_name | VARCHAR(100) | 任务名称 |
| started_at | DATETIME | 开始时间 |
| finished_at | DATETIME | 结束时间 |
| status | VARCHAR(20) | SUCCESS/FAILED/RUNNING/SKIPPED |
| records_pulled | INTEGER | 拉取记录数 |
| error_message | TEXT | 错误信息 |
| triggered_by | VARCHAR(40) | scheduler/manual:<uid> |

## 流程图

### 下钻分析流程（三层 service 架构）

```
用户请求 → API 端点
         → drill_orchestration_service
            → drill_public_service（读 fund_drill_snapshot + fund_index_map）
            → drill_user_service（读 Holding，join SecurityMaster.is_drillable）
            ← 合并返回卡片数据
```

### 证券主数据初始化流程

```
管理员点击"初始化"
  → POST /api/admin/security-master/init
  → security_master_service.init_from_existing()
    → sync_from_holdings()  # 从 Holding 表导入缺失证券
    → sync_from_drill()     # 从 FundDrillSnapshot 导入下钻股票
    → 从 FundIndexMap 补充 index_code/index_name
  ← 返回初始化条数
```

### 数据拉取任务记录流程

```
APScheduler 触发 job
  → track_run 装饰器
    → record_task_start(RUNNING)
    → 执行 job 函数
    → 成功: record_task_finish(SUCCESS)
    → 失败: record_task_finish(FAILED)
  
管理员手动触发
  → POST /api/admin/data-pull-tasks/trigger/{job_id}
  → trigger_job()
    → 设置 contextvar = "manual"
    → record_task_start(RUNNING)
    → 执行 job（track_run 检测到 manual 跳过记录）
    → record_task_finish(SUCCESS/FAILED)
```

## 状态机

### is_drillable 配置状态

```
SecurityMaster.is_drillable:
  False (默认) → 管理员编辑 → True (可下钻)
  
drill_user_service.get_user_fund_codes():
  SecurityMaster 表有数据 → join is_drillable == True
  SecurityMaster 表为空   → fallback 到 DRILLABLE_ASSET_TYPES 硬编码
```

### 数据拉取任务状态

```
RUNNING → SUCCESS (成功完成)
RUNNING → FAILED   (执行异常)
RUNNING → SKIPPED  (跳过执行)
```

## 项目修复

### 2026-06-24 子项目 1：管理员数据运维管理重构

**影响范围**：大（11 个 Task，后端 4 模型 + 3 service + 13 API + scheduler 集成，前端侧边栏重构 + 2 个域页面）

**完成内容**：

| Task | 内容 | Commit |
|------|------|--------|
| Task 1 | SecurityMaster 扩展 + DataPullTask 模型 | 4f9a9e4 |
| Task 2 | security_master_service（CRUD + 同步） | bcf36dc |
| Task 3 | data_readiness_service | 47b15be |
| Task 4 | data_pull_task_service | 5f06efa |
| Task 5 | drill_user_service join SecurityMaster | d7b8680 |
| Task 6 | 13 个 admin API 端点 | 72dbde9 |
| Task 7 | scheduler 集成 record_task | cdfe0c6 |
| Task 8 | 侧边栏重构 + 分割线 + 占位组件 | edfaff6 |
| Task 9 | MasterDataPanel（SecurityMaster + FundIndexMap） | f35915e |
| Task 10 | DataSourcePanel（5 tabs）+ 删除旧组件 | 2df8357 |
| Task 11 | 集成测试 + 迁移 + 最终验证 | 4db26dd |

**测试结果**：54 个后端测试全部通过，前端 build 成功

**关键设计决策**：
1. SecurityMaster 扩展而非新建表（已有 32 行数据）
2. is_drillable 字段替代硬编码 DRILLABLE_ASSET_TYPES，保留 fallback
3. 侧边栏"用户区 + 分割线 + 管理员区"结构，高内聚低耦合
4. 3 个域页面（主数据/数据源/内容上传）+ 内部 tab
5. scheduler 用 contextvar 避免手动触发时重复记录

**后续子项目**：
- 子项目 2：内容上传套件（指数构成 PDF、股票报告、产业链报告、财务数据手动上传）
- 子项目 3：yfinance 集成（非中港市场 PE/PB/PS 自动补足）

### 2026-06-24 子项目 2：内容上传套件

**影响范围**：大（9 个 Task，后端 4 service + 8 API 端点，前端 ContentUploadPanel 4 tab + admin token 拦截器修复）

**完成内容**：

| Task | 内容 | Commit |
|------|------|--------|
| Task 1 | upload_service + StaticFiles 挂载 + 依赖 | aa92f46 |
| Task 2 | llm_service（AI 辅助解析层） | 05c95db |
| Task 3 | pdf_parser_service（三层解析策略） | 672cfc9 |
| Task 4 | 指数构成 PDF 上传 + 确认端点 | 83dd39d |
| Task 5 | 股票分析报告上传端点 | b7e6362 |
| Task 6 | 产业链报告上传端点 | 355bfb9 |
| Task 7 | 财务数据上传（单条 + Excel 批量）端点 | ea37341 |
| Task 8 | ContentUploadPanel 4 tab + admin token 拦截器 | 1b08706 |
| Task 9 | 集成测试 + 最终验证 | - |

**测试结果**：26 个后端测试全部通过（4 upload_service + 4 llm_service + 5 pdf_parser + 4 financial_upload + 9 upload_api），前端 build 成功

**关键设计决策**：
1. PDF 三层解析策略：pdfplumber → OCR（pytesseract）→ AI 辅助（LLM API），逐层降级
2. task_id 内存缓存 + 1 小时 TTL + `secrets.token_urlsafe(8)` 生成（指数 PDF 两步上传：预览 → 确认）
3. admin 端点通过 `x-admin-token` 头鉴权（独立于用户 session），前端 axios 拦截器自动注入
4. 财务数据上传复用现有 `import_a_share`/`import_hk_share` 脚本，单条写入支持 upsert
5. `.OF` 后缀不支持单条财务上传，返回 400 错误
6. 前端 admin token = 登录密码（与后端 `APP_PASSWORD`/`ADMIN_TOKEN` 一致），登录时存储到 localStorage

**新增文件**：
- `backend/services/upload_service.py` — 文件保存 + 路径管理
- `backend/services/llm_service.py` — LLM API 调用（AI 辅助层）
- `backend/services/pdf_parser_service.py` — PDF 三层解析
- `backend/services/financial_upload_service.py` — 财务数据 upsert + Excel 批量导入
- `frontend/src/components/IndexPdfUploadTab.jsx` — 指数构成 PDF 上传
- `frontend/src/components/AnalystReportTab.jsx` — 股票分析报告上传
- `frontend/src/components/IndustryChainTab.jsx` — 产业链报告上传
- `frontend/src/components/FinancialUploadTab.jsx` — 财务数据上传（Excel + 单条）

**新增依赖**：
- pdfplumber, pytesseract, Pillow, pdf2image, python-multipart

**系统依赖**：
- tesseract-ocr（OCR 引擎，Windows 安装 Tesseract-OCR，Linux: `apt install tesseract-ocr`）
- poppler（pdf2image 依赖，Windows 安装 poppler 并加入 PATH，Linux: `apt install poppler-utils`）

### 2026-06-24 子项目 3：yfinance 集成 — 非中港市场 PE/PB/PS 自动补足

**影响范围**：中（7 个 Task，后端 1 模型 + 1 service + 2 API 端点 + scheduler 集成 + 穿透分析集成）

**完成内容**：

| Task | 内容 | Commit |
|------|------|--------|
| Task 1 | OverseasShareFinancialSnapshot 模型 | 133de7f |
| Task 2 | yfinance 增强（PB/PS + market 推断） | f65220a |
| Task 3 | overseas_financial_service | 631f4d7 |
| Task 4 | resolve_dynamic_metrics_for_stock 集成海外查询 | 5ae9963 |
| Task 5 | scheduler 集成 | a7b1800 |
| Task 6 | API 端点（列表 + 手动触发） | cd2e796 |
| Task 7 | 集成测试 + 最终验证 | - |

**测试结果**：19 个新测试全部通过（累计 45 个测试）

**关键设计决策**：
1. 新建 OverseasShareFinancialSnapshot 通用表，market 字段区分 US/KR/JP/EU 等
2. yfinance 增强：补全 PB（priceToBook）和 PS（priceToSalesTrailing12Months）
3. 穿透分析查询顺序：HK → CN → Overseas
4. 复用现有 job_update_financial_fundamentals，不新建独立 job
5. 无新增依赖（yfinance 已安装）

**新增文件**：
- `backend/services/overseas_financial_service.py`
- `backend/tests/test_yfinance_enhanced.py`
- `backend/tests/test_overseas_financial_service.py`
- `backend/tests/test_aggregation_overseas.py`
- `backend/tests/test_overseas_financial_api.py`

**修改文件**：
- `backend/models.py`（新增 OverseasShareFinancialSnapshot 类）
- `backend/crawlers/price_data.py`（增强 fetch_yfinance_info + 新增 _infer_market_from_ticker）
- `backend/services/aggregation.py`（resolve_dynamic_metrics_for_stock 添加海外查询）
- `backend/services/scheduler.py`（job_update_financial_fundamentals 添加海外写入）
- `backend/main.py`（2 个 API 端点 + import 补充）

### 2026-06-24 下钻架构重构（已完成）

**影响范围**：中（三层 service 架构）

- drill_public_service.py — 公共层（只读 fund_drill_snapshot + fund_index_map）
- drill_user_service.py — 用户层（只读 Holding）
- drill_orchestration_service.py — join 层
- drillable_funds.py — 已标记 deprecated
- 24 个测试全部通过

### 2026-06-25 下钻 E2E HTTP 测试 + FundIndexMap 回退修复

**影响范围**：中（发现并修复生产数据场景 bug）

**问题**：E2E HTTP 测试发现 advisor（44 行持仓）的下钻卡片返回空。根因：`SecurityMaster` 表有 32 行数据但 `is_drillable` 全部为 `False`（admin 从未设置），`get_user_fund_codes` 在 `SecurityMaster` 有数据时只依赖 `is_drillable` 标志，不检查 `FundIndexMap`，导致返回空集合。

**修复**：`drill_user_service.get_user_fund_codes` 增加 FundIndexMap 回退逻辑 — 当 `SecurityMaster` 有数据但 `is_drillable=True` 的查询结果为空时，回退到 `Holding JOIN FundIndexMap` 查找可下钻基金。

**TDD 流程**：
1. 写失败测试 `test_get_user_fund_codes_falls_back_to_fund_index_map`
2. 修改 `get_user_fund_codes` 添加回退逻辑
3. 26 个测试全部通过（25 原有 + 1 新增）

**E2E HTTP 测试结果**（admin/advisor/user 账号，密码 123456）：

| 测试 | 预期 | 实际 | 状态 |
|------|------|------|------|
| admin → drillable-indices | 0 卡片（无持仓） | 0 卡片 | ✅ |
| advisor → drillable-indices | 有卡片 | 12 卡片 | ✅ |
| advisor → index-drill | 有明细 | 50 成分股 + 1 基金 | ✅ |
| user → drillable-indices | 0 卡片（无持仓） | 0 卡片 | ✅ |
| admin view_as=advisor | advisor 的卡片 | 12 卡片 | ✅ |

**Commit**：`e5add81`
