﻿﻿﻿﻿﻿# PortfolioM 项目开发文档

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

### 2026-06-25 认证架构改造：localStorage → HttpOnly Cookie

**影响范围**：大（前后端认证架构全面改造，安全级别提升）

**问题**：生产环境部署在即，但前端使用 localStorage 存储 session token + 用户信息。XSS 攻击可直接 `localStorage.getItem('portfoliom_session')` 窃取 token，在过期前以受害者身份调用所有 API。token 一旦泄露无法撤销。

**改造方案**（用户确认三项推荐方案）：
1. token → HttpOnly + Secure + SameSite=Lax cookie（JS 不可读）
2. 用户信息 → 不持久化，每次刷新调 `/auth/me` 实时获取
3. UI 状态（activeRole / viewAs）→ React state，不持久化

**TDD 流程**：

| 阶段 | 测试 | 实现 |
|------|------|------|
| RED | 5 个后端 cookie 测试失败 | - |
| GREEN | 15 个后端测试全部通过 | 登录 Set-Cookie + 中间件读 cookie + 登出清 cookie |
| RED | 前端测试依赖 localStorage | - |
| GREEN | 13 个前端测试全部通过 | api.js withCredentials + App.jsx 移除 localStorage |

**后端改动**（`backend/main.py`）：
1. 新增 `_extract_token(request)` — 统一从 header > cookie > query 读取 token（header 优先兼容旧前端）
2. `auth_login` — 成功后 `response.set_cookie(session_token, httponly=True, samesite="lax")`
3. `auth_logout` — `response.delete_cookie(session_token)`
4. `auth_middleware` + `require_auth` — 改用 `_extract_token`
5. CORS — `allow_credentials=True`，`_json_error` 添加 `Access-Control-Allow-Credentials: true`
6. `COOKIE_SECURE` 环境变量控制 Secure 标志（本地 http 不启用，生产 https 启用）

**前端改动**：
1. `frontend/src/api.js` — `withCredentials: true`，移除 localStorage token 注入，view_as 改内存变量，401 改回调通知
2. `frontend/src/App.jsx` — 移除 sessionToken state + 所有 localStorage 持久化，启动时总是调 `/auth/me`，onLogout 调 `api.logout()`
3. `frontend/src/components/AuthGate.jsx` — 移除 `localStorage.removeItem('portfoliom_admin_token')`

**安全性提升**：
- token：XSS 无法读取（HttpOnly）
- 用户信息：不暴露到 JS 可读存储
- CSRF 防护：SameSite=Lax
- 生产环境：Secure 标志确保 cookie 只通过 HTTPS 传输

**兼容性**：
- `x-session-token` header 仍工作（平滑迁移期，内部脚本可用）
- 后端登录接口仍返回 token（前端仅用于判断登录成功，不持久化）

**测试结果**：
- 后端：29 个测试全部通过（15 auth_login + 14 user_relations/isolation）
- 前端：13 个测试全部通过（10 viewAsCandidates 纯函数 + 3 App 组件）
- E2E 验证：curl 登录 → Set-Cookie 正确 → cookie 调 /auth/me 成功 → cookie 调 /auth/users 成功

### 2026-06-25 Postgres 数据结构迁移

**影响范围**：大（Postgres 生产库结构优化 + 多用户隔离补全 + 冗余清理 + 索引优化）

**迁移范围**：
1. 多用户隔离补全 — `penetration_snapshot` 补 `user_id` 列 + 重建唯一约束含 `user_id`；`csi300_constituent_snapshot` 补 `user_id` 列
2. 冗余字段清理 — `HKShareFinancialSnapshot` 删除重复的 `se_l1/l2/l3/l4`；`FundDailyNav` 删除重复的 `source`/`created_at`
3. 索引/类型优化 — `aggregation_cache`/`aggregation_timeseries` 的 `user_id` default 改 NULL；4 张表补 `user_id` 索引；models.py 所有 `user_id` 统一为 `BigInteger`
4. Snapshot 体系评估 — 10 张 snapshot 表全部保留（均有明确用途，无删除）

**迁移策略**：备份 + 迁移脚本（幂等可重跑）

**迁移脚本**：
- `backend/scripts/migrate_pg_structure.py` — 主迁移脚本（幂等，`--dry-run` 预览模式）
- `backend/scripts/migrate_snapshot_user_id.py` — 辅助脚本，覆盖 9 张表的 `user_id` 列添加（已扩充从 5 张到 9 张）

**备份**：`pg_dump` 备份 158 MB（Step 0，迁移前完成）

**执行结果**：
- `penetration_snapshot`: user_id 列已存在（之前添加），UK `ux_pnsnap` 重建为 `(as_of_date, user_id, holding_code, stock_code)`
- `aggregation_cache`/`aggregation_timeseries`: `user_id` default 改为 NULL
- `csi300_constituent_snapshot`: ADD COLUMN `user_id BIGINT`，UPDATE 300 行（沪深300成分股）→ user_id=2，SET NOT NULL，CREATE INDEX
- `overseas_share_financial_snapshot`/`aggregation_cache`/`aggregation_timeseries`: 索引创建/确认

**修改文件**：
- `backend/models.py` — 6 处 `user_id = Column(Integer, ...)` 改为 `Column(BigInteger, ...)`；`PenetrationSnapshot` 添加 user_id + UK 改造；`HKShareFinancialSnapshot` 删除重复 se_l1-l4；`FundDailyNav` 删除重复 source/created_at；`AggregationCache`/`AggregationTimeseries` user_id default 改 None
- `backend/database.py` — `_MIGRATIONS` 列表追加 `penetration_snapshot.user_id` + `csi300_constituent_snapshot.user_id`
- `backend/scripts/migrate_pg_structure.py` — 新建主迁移脚本（含 `_column_exists`/`_constraint_exists`/`_index_exists` 幂等检查）
- `backend/scripts/migrate_snapshot_user_id.py` — TABLES 列表扩充为 9 张表，ADD COLUMN 类型改 BIGINT
- `backend/tests/test_pg_migration.py` — 新建 14 项测试（8 模型元数据层 + 6 Postgres 实际结构层）

**测试结果**：14 项测试全部通过

**关键设计决策**：
1. 所有 10 张 snapshot 表保留（评估结论：均有明确用途）
2. `csi300_constituent_snapshot` 现有 300 行数据归 advisor（user_id=2），与 `migrate_snapshot_user_id.py` 默认值一致
3. `aggregation_cache`/`aggregation_timeseries` 的 default 改 NULL（不再写死 advisor id=2），由应用层写入时指定
4. models.py Integer → BigInteger 统一仅改代码（Postgres 中两者 DB 层都是 integer 类型，无需 ALTER）
5. 用户每日持仓快照表 + 交易记录表仅记录 TODO，不在本次实施

**TODO（未来实施）**：
- `HoldingDailySnapshot` — 每日持仓快照（记录用户每天的 holdings 历史状态）
- `TradeRecord` — 用户交易记录（记录买入/卖出，记录在交易日而非录入日）

### 2026-06-25 下钻估值数据补全（P0 已完成，Phase B 待实施）

**影响范围**：大（下钻卡片 PE/PB/PS/股息率/金额/占比/偏差数据全部丢失修复 + 估值表 user_id 公共化 + 基准日可变动态计算）

**问题**：下钻页面（DrillableFundsPage.jsx）的 11 个卡片 PE/PB/PS/股息率数据全部丢失，卡片显示空白；用户层金额/占比/偏差也无数据。根因是三层下钻架构（public → user → orchestration）的公共数据层面处理不正确：
1. `FundDrillSnapshot` 表无估值字段（只有 weight_pct/baseline_price/current_price/shares_equivalent）
2. `drill_snapshot.py` 生成 snapshot 时未 join 估值表
3. `drill_public_service` 不返回 weighted_pe 等
4. 估值数据源 A/H/Overseas 三表带 `user_id`（历史设计错误——PE/PB/PS 是市场公共数据，与持仓交易无关），代码本身矛盾（部分按 user_id 过滤，部分不当 user_id 用）

**修复方案（P0，本次实施）** — 9 阶段：

| 阶段 | 内容 | 文件 |
|------|------|------|
| 1 | DB 迁移脚本：三估值表 user_id 改 nullable + FundDrillSnapshot 加 4 估值字段 + 回填 | `scripts/migrate_drill_valuation.py` 新建、`database.py` _MIGRATIONS 追加 |
| 2 | models.py：FundDrillSnapshot 追加 pe_ttm/pb_mrq/ps_ttm/dividend_yield；三估值表 user_id 改 nullable | `models.py` |
| 3 | drill_snapshot.py 生成时 join 估值：新增 `_load_valuation_snapshots(db, as_of_date)` 预加载 A/H 估值表（取 ≤ as_of_date 最新批次，不按 user_id 过滤），写入 4 字段 | `services/drill_snapshot.py` |
| 4 | drill_public_service 计算 weighted 估值：动态调整公式 pe_dyn = pe_ttm × (current/baseline)、dy_dyn = dy × (baseline/current)（反向）；调和平均 PE/PB/PS、算术平均股息率，按 shares_eq × baseline_price 加权 | `services/drill_public_service.py` |
| 5 | drill_orchestration_service 补用户层字段：static_amount_cny / est_market_value_cny / est_deviation_pct / weight_pct | `services/drill_orchestration_service.py` |
| 6 | 清理 6 处 user_id 过滤（main.py）+ analyst_service.py + drillable_funds.py | `main.py`、`services/analyst_service.py`、`services/drillable_funds.py` |
| 7 | 导入脚本清理：overseas_financial_service.py 删 user_id=1 硬编码 | `services/overseas_financial_service.py` |
| 8 | 测试：更新 3 个现有测试 + 新建 E2E 测试 | `tests/test_drill_*.py` 4 文件 |
| 9 | 文档更新 | `Project_development.md` |

**关键公式**：
- 加权 PE（调和平均）：`weighted_pe = Σ(weight_basis) / Σ(weight_basis / pe_dyn)`，weight_basis = shares_equivalent × baseline_price
- 加权股息率（算术平均，反向调整）：`weighted_dy = Σ(weight_basis × dy_dyn) / Σ(weight_basis)`，dy_dyn = dividend_yield × (baseline_price / current_price)
- 估算偏差：`est_deviation_pct = ((card_est + card_cash) / card_fund_value - 1) × 100`，card_cash = 5% × card_fund_value
- 基准日可变：`_load_valuation_snapshots` 取 ≤ as_of_date 的最新估值批次（非硬编码），未来导入新基准日数据自动切换

**测试结果**：25 项测试全部通过（5 E2E + 4 公共层 + 4 编排层 + 10 API 集成 + 2 其他）

**Phase B 方向（待实施，单独建 plan）** — 基准日版本管理 + 批量重算：
- 数据模型：公共 snapshot 表追加 `baseline_date` + `version` + `superseded_at` 列；唯一约束追加 baseline_date 维度（partial unique index WHERE superseded_at IS NULL）
- 重算服务：新增 `recompute_from_baseline(db, new_baseline_date)` — 新业务日期导入后，重算自该日起的两类派生数据：(1) 非下钻估值数据（总览）PenetrationResult；(2) 下钻估值数据（下钻-全持仓）FundDrillSnapshot；并重算指数构成 PE/PB/PS/股息率
- 覆盖语义：重算结果持久化**覆盖**原持久化数据（前台视角值被替换），通过**小版本号**保留历史可回溯（新增更高 version 行，旧行 superseded_at 标记，不物理删除）
- 前台显示替换：查询历史估值时前台显示最新基准下的覆盖值，老基准值通过 tooltip/下钻可见；API 返回 `effective_baseline_date` 字段
- 核心原则：使用**最新的导入基础日**（数据日期本身，非导入操作日期），结合该基准日的相对价格变化动态计算
- 详见 plan 文档 `.trae/documents/drill-valuation-fix.md` Phase B 章节

**修改文件清单**：
- `backend/models.py` — FundDrillSnapshot +4 估值字段，三估值表 user_id nullable
- `backend/database.py` — _MIGRATIONS 追加 4 项
- `backend/scripts/migrate_drill_valuation.py` — 新建迁移脚本（跨库兼容 PG/SQLite，幂等 + --dry-run）
- `backend/services/drill_snapshot.py` — 新增 `_load_valuation_snapshots` + join 估值写入
- `backend/services/drill_public_service.py` — weighted PE/PB/PS/dy 计算 + 明细层估值字段 + weight_at_baseline_pct
- `backend/services/drill_orchestration_service.py` — 用户层 static/est/deviation/weight_pct
- `backend/main.py` — 清理 6 处 user_id 过滤
- `backend/services/analyst_service.py`、`backend/services/drillable_funds.py` — 清理 user_id 过滤
- `backend/services/overseas_financial_service.py` — 删 user_id=1 硬编码
- `backend/tests/test_drill_public_service.py`、`test_drill_orchestration.py`、`test_drill_api_integration.py`、`test_drill_valuation_e2e.py` — 测试更新/新建
- `.trae/documents/drill-valuation-fix.md` — plan 文档（P0 实施 + Phase B 方向）

### 2026-06-25 下钻估值改用持久化动态财务指标 + 下钻-现金行

**影响范围**：中（P0 估值补全的修正与增强 — 从"存基准日值 + 实时算动态值"改为"直接用已持久化的动态值"）

**背景与问题**：P0 阶段最初的动态估值方案是在查询时实时计算 `pe_dyn = pe_ttm × (current_price / baseline_price)`。但用户指出：动态财务指标（基于最新导入的财务数据 + 最新收盘价相对涨跌的调整）已由导入流程在公共数据下每日持久化保存，下钻应直接使用下钻日期对应的这些动态财务指标，而非查询时实时算。实时算无法反映"最新财务数据"的变化（如季报更新后 PE 基准值本身已变），只能反映价格相对变化。

**修正方案**：

1. **FundDrillSnapshot 新增 3 个动态字段**（`models.py` + `database.py` _MIGRATIONS）：
   - `pe_ttm_dynamic` / `pb_mrq_dynamic` / `ps_ttm_dynamic` — 来自 A/H 估值表的 `*_dynamic` 列
   - 股息率无 dynamic 字段（仍用 `dividend_yield / price_ratio` 实时算）

2. **drill_snapshot.py 加载并写入动态字段**：
   - `_load_valuation_snapshots` 从 AShareFinancialSnapshot/HKShareFinancialSnapshot 加载 `pe_ttm_dynamic` 等
   - FundDrillSnapshot 构造时写入 3 个动态字段
   - INSERT 策略从 `on_conflict_do_nothing` 改为 `on_conflict_do_update`（全量刷新，确保补字段后重跑能写入新增字段 — 修复了此前 6831 行 pe_ttm 全为 NULL 的根因）

3. **drill_public_service.py 加权计算用动态值**（with fallback）：
   - PE/PB/PS 优先用 `r.pe_ttm_dynamic`，为空时 fallback 到 `r.pe_ttm × price_ratio` 实时算（覆盖海外股无 dynamic 字段的场景）
   - 股息率仍用 `r.dividend_yield / price_ratio` 实时算（无 dynamic 字段）
   - `get_public_detail` constituents 返回 3 个动态字段供前端显示

4. **下钻-现金行**（`drill_orchestration_service.py`）：
   - `get_drill_detail` 在 constituents 末尾追加"下钻-现金"行（基金有 5% 现金部分，现金也是资产需计入合计）
   - `cash_value = Σ(user_quantity[f] × fund_price[f] × 0.05)`
   - 前端现金行特殊渲染（斜体 + 背景色），合计行过滤 is_cash

5. **前端显示动态值**（`DrillableFundsPage.jsx`）：
   - PE/PB/PS 列改为 `fmtNum(r.pe_ttm_dynamic ?? r.pe_ttm)`（优先动态值，fallback 基准日值）

**关键公式（修正后）**：
- 加权 PE（调和平均）：`weighted_pe = Σ(weight_basis) / Σ(weight_basis / pe_dyn)`
  - `pe_dyn = pe_ttm_dynamic`（优先持久化动态值）
  - fallback：`pe_dyn = pe_ttm × (current_price / baseline_price)`（实时算，仅当 dynamic 为空）
- 加权股息率（算术平均）：`weighted_dy = Σ(weight_basis × dy_dyn) / Σ(weight_basis)`
  - `dy_dyn = dividend_yield / price_ratio`（无 dynamic 字段，仍实时算）
- weight_basis = shares_equivalent × baseline_price

**Postgres 回填**：
- ALTER TABLE fund_drill_snapshot ADD 3 个动态字段
- 从 A/H 估值表动态查最新批次（非硬编码日期）回填，结果 6828/6831 行有 pe_ttm_dynamic

**真实数据验证**：
- FundDrillSnapshot 动态字段覆盖率 99%（6828/6831 行）
- 最新 snapshot 日期 2026-06-25
- 325 行动态值 ≠ 实时算（`pe_ttm × price_ratio`），证明动态值是基于"最新财务数据 + 价格相对涨跌"独立计算，非简单价格调整
- 源头 AShareFinancialSnapshot 94% 覆盖率（5206/5528 行）

**测试结果**：15 个下钻相关测试全部通过
- test_drill_public_service.py 4 个（mock 动态字段设 None 走 fallback 路径）
- test_drill_orchestration.py 6 个（mock 加动态字段 + 现金行断言）
- test_drill_valuation_e2e.py 5 个（种子数据加动态字段 + 手算公式用动态值 + 动态字段透传断言）

**修改文件清单**：
- `backend/models.py` — FundDrillSnapshot +3 动态字段
- `backend/database.py` — _MIGRATIONS 追加 3 项
- `backend/services/drill_snapshot.py` — `_load_valuation_snapshots` 加载动态字段 + 构造写入 + on_conflict_do_update 全量刷新
- `backend/services/drill_public_service.py` — 加权计算用动态值（with fallback）+ 明细返回动态字段
- `backend/services/drill_orchestration_service.py` — get_drill_detail 追加"下钻-现金"行
- `frontend/src/components/DrillableFundsPage.jsx` — PE/PB/PS 显示动态值 + 现金行特殊渲染
- `backend/tests/test_drill_public_service.py` — mock 动态字段设 None
- `backend/tests/test_drill_orchestration.py` — mock 加动态字段 + 现金行断言
- `backend/tests/test_drill_valuation_e2e.py` — 种子数据加动态字段 + 手算公式用动态值

### 2026-06-25 现金-下钻概念纠正：从编排层临时计算改为公共数据层分解

**影响范围**：中（现金-下钻 CASH 行的正确归属层修正 — 从编排层/全持仓层临时追加改为公共数据层生成，流经所有下游层）

**问题**：此前实现中，"下钻-现金"行（代表基金 5% 现金部分）是在编排层（`drill_orchestration_service.get_drill_detail`）和全持仓层（`main.py full-holding-table`、`drillable_funds.py`）临时计算并追加的。这违反了三层解耦架构原则——现金-下钻本质上是公共数据的分解，不应在各下游层重复计算。

**用户纠正**：
> "现金-下钻是在公共数据处理指数时，进行的分解，指数和基金的对应关系，是基金 95% 配置指数，5% 配置现金，所以，虽然指数中股票权重合计为 100%，但是基金中，权重股票的合计为 95%，其他 5% 分配给现金-下钻"

**正确理解**：
- 基金 = 95% 指数 + 5% 现金
- 指数中股票权重合计 100%，但基金中股票合计 95%，其余 5% = 现金-下钻
- CASH 行应从公共数据层（`drill_snapshot.py`）生成，流经所有下游层，而非在各层临时计算

**修复方案**（7 个 Task）：

| Task | 内容 | 文件 |
|------|------|------|
| 14 | `drill_snapshot.py` 生成时为每只基金追加 CASH 行（shares_eq = fund_price × 0.05, price = 1.0, 估值 = None） | `services/drill_snapshot.py` |
| 15 | `drill_public_service` 跳过 CASH 行的 stock_set / total_weight / 估值聚合；get_public_detail 添加 is_cash 标记，CASH 排序末尾 | `services/drill_public_service.py` |
| 16 | `drill_orchestration_service` 移除所有临时 CASH 逻辑；est_deviation_pct 公式从 `((est+cash)/fund_value - 1)` 简化为 `(est/fund_value - 1)`（per_fund_est 已含 CASH） | `services/drill_orchestration_service.py` |
| 17 | `drillable_funds.py` 移除临时 cash_value_cny 计算；处理来自 snapshot 的 CASH 行（is_cash 标记 + 排序末尾 + 跳过 PE 查找） | `services/drillable_funds.py` |
| 18 | `main.py full-holding-table` 修复 drillable_codes（移除 FundIndexMap 日期过滤）+ 移除临时 CASH 追加 + 传播 is_cash | `main.py` |
| 19 | 测试更新：E2E 种子数据加 CASH 行 + est_deviation_pct 公式断言更新 + constituents 数量断言更新 | `tests/test_drill_valuation_e2e.py`、`tests/test_drill_orchestration.py` |
| 20 | 重新生成 drill snapshots（15 只基金 × 1 CASH 行 = 15 行）+ 端到端验证 | `scripts/regenerate_drill_snapshot_with_cash.py`、`scripts/verify_cash_drill_down.py` |

**CASH 行数据结构**：
```python
FundDrillSnapshot(
    fund_code=<基金代码>,
    as_of_date=<日期>,
    stock_code="CASH",
    stock_name="下钻-现金",
    weight_pct=5.0,              # CASH_RATIO × 100
    baseline_price=1.0,          # 现金基准 = 1.0
    current_price=1.0,           # 现金现价 = 1.0（无价格变动）
    shares_equivalent=fund_price × 0.05,  # 每份基金含现金金额
    pe_ttm=None, pb_mrq=None, ps_ttm=None, dividend_yield=None,  # 现金无估值
    pe_ttm_dynamic=None, pb_mrq_dynamic=None, ps_ttm_dynamic=None,
)
```

**CASH 行传播路径**：
```
drill_snapshot.py（生成）
  → FundDrillSnapshot 表
  → drill_public_service（读取，跳过估值聚合，返回 is_cash 标记）
  → drill_orchestration_service（join 用户数据，CASH 自然流过）
  → drillable_funds（旧模块，full-holding-table 用，处理 CASH 行）
  → main.py full-holding-table（传播 is_cash）
  → 前端 FullHoldingTable.jsx（特殊渲染：💵 图标 + 斜体 + 约当数量显示 '-'）
```

**est_deviation_pct 公式变更**：
- 旧公式：`est_deviation_pct = ((card_est + card_cash) / card_fund_value - 1) × 100`
  - `card_cash = 0.05 × card_fund_value`（编排层临时计算）
- 新公式：`est_deviation_pct = (card_est / card_fund_value - 1) × 100`
  - `card_est` 已含 CASH（`per_fund_est` 聚合查询包含 FundDrillSnapshot 所有行，含 CASH）
  - 理论上 `card_est ≈ card_fund_value` → `deviation ≈ 0`

**FundIndexMap 日期过滤修复**（Task 18 附带）：
- 问题：`FundIndexMap.as_of_date == as_of_date` 过滤导致查询日期无数据时 `drillable_codes` 为空
- 修复：移除日期过滤，`drillable_codes = {m.fund_code for m in db.query(FundIndexMap).all()}`
- 原因：FundIndexMap 是静态映射表（fund_code → index_code），不应按日期过滤

**测试结果**：
- 32 个下钻相关测试全部通过（5 E2E + 7 编排层 + 4 公共层 + 10 API 集成 + 6 用户层）
- 3 个预先存在的失败与 CASH 改动无关（2 个 401 认证问题 + 1 个测试逻辑检查错误字段 fund_codes 而非 user_fund_codes）

**端到端验证**（真实数据，as_of=2026-06-25）：
1. ✅ CASH 行数据正确：15 行，price=1.0, weight=5.0, 估值=None
2. ✅ PE/PB/PS 不被现金稀释：CASH 行 `continue` 跳过估值聚合，weighted_pe 正常计算
3. ✅ `est_deviation_pct ≈ 0`（0.0009, 0.001）— 验证 card_est 含 CASH ≈ card_fund_value
4. ✅ `get_drill_detail` constituents 含 CASH 行，user_hold_value > 0（用户约当现金金额）
5. ✅ `get_public_detail` CASH 行排末尾，is_cash=True
6. ✅ main.py full-holding-table 传播 is_cash
7. ✅ 前端 FullHoldingTable.jsx CASH 行特殊渲染（💵 + 斜体 + '-' 约当数量）

**修改文件清单**：
- `backend/services/drill_snapshot.py` — 生成 CASH 行（公共数据层分解）
- `backend/services/drill_public_service.py` — 跳过 CASH 估值聚合 + is_cash 标记 + 排序
- `backend/services/drill_orchestration_service.py` — 移除临时 CASH + 简化 est_deviation_pct 公式

- `backend/services/drillable_funds.py` — 移除临时 cash_value_cny + 处理 snapshot CASH 行
- `backend/main.py` — 修复 drillable_codes + 移除临时 CASH + 传播 is_cash
- `backend/tests/test_drill_valuation_e2e.py` — 种子数据加 CASH 行 + 公式断言更新
- `backend/tests/test_drill_orchestration.py` — mock 加 CASH 行 + 公式断言更新
- `frontend/src/components/FullHoldingTable.jsx` — CASH 行合并逻辑 + is_cash 传播 + 特殊渲染
- `backend/scripts/regenerate_drill_snapshot_with_cash.py` — 新建，重新生成快照脚本
- `backend/scripts/verify_cash_drill_down.py` — 新建，端到端验证脚本

**关键设计决策**：
1. CASH 行的 `shares_equivalent = fund_price × 0.05`（每份基金含现金金额），`current_price = 1.0`（现金无价格变动），估算市值 = `shares_eq × price = fund_price × 0.05`
2. CASH 行不参与 PE/PB/PS/股息率加权（现金无盈利/净资产/营收/分红），但计入合计金额
3. CASH 行的 `weight_pct = 5.0`（CASH_RATIO × 100），但 `total_weight` 只累加股票权重（CASH 被 `is_cash` 跳过）
4. CASH 行排序到 constituents 末尾（`sort(key=lambda c: (c.get('is_cash', False), -weight_pct))`）
5. 前端 CASH 行约当数量显示 `-`（现金无股数概念），估算市值正常显示

### 2026-06-25 港股通下钻汇率量纲修正 + 双币种规则

**影响范围**：大（港股通基金估算市值偏差 8.26% 修复 + 全项目单价双币种规则确立）

**问题**：港股通基金（018388.OF、021142.OF）的 `est_deviation_pct` 显示 +8.26%（应接近 0%）。
- 根因：`shares_equivalent` 用 CNY 价算（`fund_price × 0.95 × weight / price_cny`），但下游 `get_drill_detail` / `per_fund` 聚合用 `current_price`（HKD 原币）与之相乘，量纲混乱（CNY 股数 × HKD 价），导致港股通基金估算市值虚高 1/fx_rate - 1 ≈ 8.26%。
- A 股基金因 fx_rate=1.0（原币=本币）未暴露此 bug。

**双币种规则（2026-06-25 确立，全项目通用）**：
> 对于所有出现单价的地方，都同时存「单价×原币」和「单价×本币(CNY)」两个值。
> 「×本币」不是下游临时计算，而是在公共数据层一次性算好存入表，下游层直接取公共数据。

- 原币 = 上市地交易币种（A 股 CNY、港股 HKD、美股 USD）
- 本币 = CNY（人民币）
- 本币值 = 原币值 × fx_rate，在公共数据层算好落库，下游取字段不临时算
- A 股 fx_rate=1.0，原币=本币；港股/美股经 fx_rate 折算

**FundDrillSnapshot 双币种字段**：
- `current_price`（原币）+ `current_price_cny`（本币）= `current_price × fx_rate`
- `baseline_price`（原币）+ `baseline_price_cny`（本币，新增）= `baseline_price × fx_rate`
- `currency`（原币币种）+ `cny_currency`（本币币种 CNY）+ `fx_rate` + `fx_date`

**修复内容**：
1. `models.py`：新增 `baseline_price_cny` 字段 + docstring 更新双币种规则
2. `database.py`：init_db 自动迁移 `baseline_price_cny` 列
3. `services/drill_snapshot.py`（公共数据层）：生成 `baseline_price_cny`（成分股行 + CASH 行 + INSERT/upsert）
4. `services/drill_public_service.py`：`weight_basis` 改用 `baseline_price_cny`（本币）；`get_public_detail` 返回本币字段供前端
5. `services/drill_orchestration_service.py`：`per_fund` 聚合 + `get_drill_detail` 改用 `current_price_cny`（本币）

**币种验证（2026-06-25 实证，非名字推断）**：
1. 港股成分股价 = HKD 原币：
   - `crawlers/price_data.py` 中 `float(item[2])` 原样入库，grep `HKD|CNY|currency|折算|汇率` 无匹配 — 无折算逻辑
   - 腾讯 API `qt.gtimg.cn/q=hk00005` 实测返回 `100~汇丰控股~00005~148.300~148.200...`（100=港股市场标识），现价 148.3 HKD
   - DB `PriceCache.close_px=148.2` = 腾讯昨收价，确认是 HKD
2. 港股通基金价 = CNY：`Holding.currency="CNY"`，`amount == amount_cny`（018388.OF price=1.2591, 021142.OF price=1.3386）
3. fx_rate=0.92 合理（HKD→CNY 区间 0.91-0.93）

**测试结果**：19 个下钻相关测试全部通过（含 3 个新增港股通汇率回归测试 `TestHKDrillFxRate`）
**端到端验证**：`verify_hk_drill_fx.py` 显示正确公式 deviation=-0.00%（错误公式 +8.26%）

**修改文件清单**：
- `backend/models.py` — 新增 `baseline_price_cny` + docstring 双币种规则
- `backend/database.py` — init_db 迁移 `baseline_price_cny`
- `backend/services/drill_snapshot.py` — 公共层生成 `baseline_price_cny`
- `backend/services/drill_public_service.py` — `weight_basis` 用本币 + 透传本币字段
- `backend/services/drill_orchestration_service.py` — 聚合 + detail 用 `current_price_cny`
- `backend/tests/test_drill_valuation_e2e.py` — 港股通 seed 数据 + 3 个汇率回归测试
- `backend/tests/test_drill_public_service.py` — mock 加本币字段
- `backend/tests/test_drill_orchestration.py` — mock 加 `current_price_cny`
- `backend/scripts/verify_hk_drill_fx.py` — 偏差验证脚本
- `backend/scripts/verify_hk_currency.py` — 币种实证验证脚本（新建）

**已知遗留（记入 docs/project-status.md 待进行任务）**：
- `drillable_funds.py` 旧模块 + `main.py full-holding-table` 端点 + 前端 `FullHoldingTable.jsx` 的 `toCNY` 临时折算 — 同类汇率问题暂不修改
- `ExchangeRate` 表 HKD→CNY 恒定 0.92（应每日波动）、`PriceCache` 表无 currency 字段、`_guess_currency` 后缀推断缺防御性

### 2026-06-25 全持仓下钻迁移到三层架构 + 4 口径指标统一算法

**影响范围**：大（修复全持仓页面港股通量纲混乱 + 4 口径估值卡片算法与下钻卡片统一）

**问题背景**：
1. **全持仓页面下钻段量纲混乱**：`/api/penetration/full-holding-table` 端点仍在用 deprecated 的 `drillable_funds.py` 旧模块，其 `get_index_drill_detail` 用 `current_price`（原币）算 `est_market_value_cny`（命名误导，实际是原币），前端 `FullHoldingTable.jsx` 再用 `toCNY` 折算 → 港股通基金估算市值虚高 8.26%（与 645 节"港股通下钻汇率量纲修正"同一类 bug）。
2. **4 口径指标算法与下钻卡片不一致**：`/api/penetration/full-holding-summary` 端点用旧算法 `_compute_drill_virtual_earnings`：
   - 用 `est_market_value_cny × fx_rate` 折算 CNY（量纲错误，因为 `est_market_value_cny` 在旧逻辑下已经是原币命名混乱）
   - 不读持久化的 `pe_ttm_dynamic` / `pb_mrq_dynamic` / `ps_ttm_dynamic`，而是用 `pe_ttm × price_ratio` 现算
   - 与下钻卡片（`drill_public_service.get_public_cards`）用的算法不一致

**修复方案**：迁移到三层 service 架构（与下钻页面一致），4 口径指标复用与下钻卡片完全相同的算法。

**新增 service 函数（`backend/services/drill_orchestration_service.py`）**：

1. `get_all_drill_constituents(db, as_of, user_id) -> dict | None`（line 244-375）
   - 跨所有可下钻指数聚合成分股，按 `stock_code` 合并（跨 fund / 跨指数）
   - 含 CASH 行（现金-下钻，来自公共数据层 `drill_snapshot.py`）
   - 用与 `get_drill_detail` 相同的双币种算法：`est_market_value_cny = user_hold_shares × current_price_cny`（本币 CNY）
   - 字段齐全：`stock_code` / `stock_name` / `is_cash` / `shares_equivalent` / `baseline_price` / `current_price` / `baseline_price_cny` / `current_price_cny` / `est_market_value_cny` / `pe_ttm` / `pb_mrq` / `ps_ttm` / `dividend_yield` / `pe_ttm_dynamic` / `pb_mrq_dynamic` / `ps_ttm_dynamic` / `currency` / `fx_rate` / `indices`
   - 输出按估算市值降序，现金-下钻行排末尾

2. `compute_scope_metrics(stocks) -> dict`（line 378-462）
   - 与 `drill_public_service.get_public_cards` 用**完全一致**的算法计算口径指标
   - 调和平均 PE/PB/PS + 算术平均 DY
   - `weight_basis = shares_eq × baseline_price_cny`（本币）
   - `price_ratio = current_price_cny / baseline_price_cny`
   - 动态估值优先级：`pe_dyn = pe_ttm_dynamic if pe_ttm_dynamic else (pe_ttm × price_ratio)`，优先用持久化 dynamic 字段
   - 返回 `stock_count` / `total_amount_cny` / `weighted_pe` / `weighted_pb` / `weighted_ps` / `weighted_dividend_yield`

**端点迁移（`backend/main.py`）**：

1. `/api/penetration/full-holding-table` 端点（line ~2909-3032）
   - import 从 `from services.drillable_funds import list_drillable_indices, get_index_drill_detail` 改为 `from services.drill_orchestration_service import get_all_drill_constituents`
   - 删除 `FundDailyNav` import + `fund_navs_map` 加载逻辑（~20 行）
   - 删除 `list_drillable_indices` + `get_index_drill_detail` 循环聚合逻辑（~40 行）
   - 替换为单次调用 `get_all_drill_constituents(db, as_of_date, eff_uid)`
   - `drilled_map` 字段补齐本币字段 + 动态估值字段（`current_price_cny` / `baseline_price_cny` / `pe_ttm_dynamic` / `pb_mrq_dynamic` / `ps_ttm_dynamic` / `currency` / `fx_rate`）

2. `/api/penetration/full-holding-summary` 端点（line ~4037-4125）
   - import 从 `drillable_funds` 改为 `drill_orchestration_service` + `drill_public_service.get_public_cards`
   - 删除 `ExchangeRate` import + `fx_rates` 加载逻辑（新算法用本币字段，不需要临时折算）
   - 删除 `list_drillable_indices` + `get_all_drilled_stocks` 调用
   - `_compute_drill_virtual_earnings` → `compute_scope_metrics`
   - CSI300 卡片从 `list_drillable_indices` 返回的 000300 卡片改为 `get_public_cards` 返回的 000300 卡片（与下钻页面一致）

**前端修改（`frontend/src/components/FullHoldingTable.jsx`）**：

drilled 段（line 157-177）去掉 `toCNY` 双重折算：
- `est_market_value_cny` 直接累加（后端已返回本币 CNY，不再 `toCNY` 折算）
- `current_price` 优先用 `current_price_cny`（确保与 `est_market_value_cny` 口径一致），fallback 到 `toCNY` 折算兼容旧后端
- 估值字段优先用动态值：`peV = s.pe_ttm_dynamic ?? s.pe_ttm`（与下钻页面一致）

undrilled 段（line 116-141）保持不变（仍用 `toCNY` 折算，因后端返回原币种）。

**4 口径估值卡片（`frontend/src/components/PortfolioVsCsi300Card.jsx`）**：
- 2×2 网格布局：drilled（红）/ a_only（蓝）/ h_only（绿）/ csi300（黄）
- 显示格式不动（`fmtNum` 1 位小数 / `fmtAmount` 千分位整数 / `fmtPctRaw` 1 位小数）
- 占比 = card 金额 / 表格估算市值合计（`totalEstCNY` 由父组件 `AnalysisPanel.jsx` 传入）

**验证结果（`backend/scripts/verify_full_holding_migration.py`）**：
1. ✓ `get_all_drill_constituents` 返回 836 只股票（含 1 行 CASH），19 个必要字段全部存在
2. ✓ `est_market_value_cny = shares × current_price_cny`（量纲一致，部分 ✗ 是浮点精度差异 < 0.1 元）
3. ✓ 港股通双币种生效：60 只港股 `current_price_cny = current_price × 0.92`，差异 -8.00%
4. ✓ CASH 行存在（"下钻-现金"，198021.54 CNY）
5. ✓ 三口径指标合理：
   - drilled（全部 835 只）：PE=16.32, PB=1.65, PS=1.58, DY=2.60%
   - a_only（A股 775 只）：PE=20.14, PB=2.21, PS=2.17, DY=1.90%
   - h_only（港股 60 只）：PE=7.42, PB=0.63, PS=0.58, DY=7.05%
   - 合理：A 股 PE/PB/PS > 港股（A 股溢价），港股 DY > A 股（港股高股息）
6. ✓ 服务器日志显示前端调用 `/api/penetration/full-holding-summary` + `/api/penetration/full-holding-table` 都返回 200 OK

**修改文件清单**：
- `backend/services/drill_orchestration_service.py` — 新增 `get_all_drill_constituents` + `compute_scope_metrics` 两个函数
- `backend/main.py` — 迁移 `/api/penetration/full-holding-table` + `/api/penetration/full-holding-summary` 两个端点
- `frontend/src/components/FullHoldingTable.jsx` — drilled 段去掉 `toCNY` 双重折算，估值字段优先用动态值
- `backend/scripts/verify_full_holding_migration.py` — 新建验证脚本

**未触动**：
- `backend/services/drillable_funds.py`（DEPRECATED，仍存在但不再被 full-holding-table / full-holding-summary 端点使用）
- `backend/main.py::_compute_drill_virtual_earnings`（旧函数保留，但不再被 full-holding-summary 端点调用；可能其他地方还用，未删）
- 前端 `PortfolioVsCsi300Card.jsx` 显示格式不动（用户明确要求复用）

---

## 2026-06-26: PortfolioM 2.0 云端独立部署 + User import 修复

### 背景

用户要求在 chargeye133.duckdns.org 部署独立 2.0 版本（前端映射 `/portfoliom2.0/`），与原版 `/portfoliom/` 完全隔离；本地 PG 全量同步云端；LLM key 写入；用户名密码修改；云端测试通过。

### 2.0 云端部署架构

**隔离策略**（3 层隔离）：
1. **容器名隔离**：`portfoliom2-pg` / `portfoliom2-backend` / `portfoliom2-frontend`（原版为 `portfoliom-pg/backend/frontend`）
2. **端口隔离**：backend `127.0.0.1:8011`（原版 8010）；frontend `127.0.0.1:3001`（原版 3000）
3. **PG volume 隔离**：`pg2_data`（原版 `pg_data`），物理隔离
4. **Docker compose project name 隔离**：`portfoliom2_default` 网络（目录名 PortfolioM2 决定）

**docker-compose-2.0.yml 关键配置**：
- frontend build arg `VITE_BASE: "/portfoliom2.0/"` → Vite 打包时 `base: env.VITE_BASE` 生效 → 资源路径 `/portfoliom2.0/assets/...`
- backend 环境变量 `LLM_API_KEY` / `LLM_API_BASE` / `LLM_MODEL`（从 .env 注入）
- PG 密码通过 `${PG_PASSWORD:-portfoliom2_prod}` 注入

**nginx 反代配置**（`/etc/nginx/sites-enabled/portfoliom`）：
```nginx
# 2.0 API — rewrite strip /portfoliom2.0 前缀
location /portfoliom2.0/api/ {
    rewrite ^/portfoliom2.0/api/(.*)$ /api/$1 break;
    proxy_pass http://127.0.0.1:8011;
    proxy_read_timeout 120s;
}
# 2.0 Frontend — proxy_pass 带 trailing slash 会 strip 前缀
location /portfoliom2.0/ {
    proxy_pass http://127.0.0.1:3001/;
}
```

**数据库同步**：本地 `pg_dump -Fc`（316MB → 15MB）→ scp → 云端 `pg_restore --clean --if-exists` → 验证 5 users / 96 holdings / 16554 snapshots 一致

**用户密码修改**（云端 portfoliom2-pg，bcrypt rounds=10）：
- admin（id=1）密码 → `Wishmegoodluck!620`
- user2（id=2）username → `yjn`，display_name → `叶(顾)问`，密码 → `EmmaYe`
- user3/4/5（id=3,4,5）密码 → `1234qwerasdf`

### User import bug 修复

**Bug**：`backend/main.py` line 18 `from models import FundIndexMap, Holding, AssetType, OverseasShareFinancialSnapshot` 漏 import `User`。多处端点用 `user: User = Depends(require_admin/require_advisor)` 类型注解 → `NameError: name 'User' is not defined` at startup。

**根因**：无 `from __future__ import annotations` 时，Python 在函数定义时求值类型注解，若类型未 import 则 NameError。本地未暴露是因为 `python -m uvicorn main:app` 启动时这些端点还没加，之后改代码没重启。

**修复**：line 18 加 `, User`。commit `12de386`，merge 到 main `0ba1cad`。

**教训**：类型注解的 import 必须完整；建议加 `from __future__ import annotations`（PEP 563）使注解延迟求值。

### 管理员价格刷新入口

**后端端点**：
- `POST /api/admin/fill-prices-all` — 全用户持仓并集最新价刷新（15min TTL 增量，GROUP BY 去重避免请求爆炸）
- `POST /api/admin/refresh-analysis-prices` — 分析页全持仓收盘价刷新（支持 as_of_date / days / max_codes 参数）

**前端 UI**：`frontend/src/components/PriceRefreshTab.jsx` — 两张卡片（持仓最新价 + 分析页收盘价），集成在 `DataSourcePanel.jsx` 的 `priceRefresh` tab。

**API 函数**（`frontend/src/api.js`）：
- `adminFillPricesAll()` — timeout 120s
- `adminRefreshAnalysisPrices(asOfDate, days, maxCodes)` — timeout 120s

### 云端测试结果（2026-06-26）

| 测试项 | 结果 |
|--------|------|
| 5 用户登录（admin/yjn/user/user_b/user_c） | ✅ 全部 200，display_name 正确 |
| 持仓数据（admin 33 funds / yjn 41 funds） | ✅ cash_cny 字段存在，用户数据隔离 |
| LLM 交易解析（007789 + 510300.SH） | ✅ 返回 ParsedTradeItem[]，LLM key 生效 |
| fill-prices-all | ✅ 92/96 holdings updated, 34 codes refreshed |
| refresh-analysis-prices | ✅ remaining_null=0, hint=全部填充完成 |
| full-holding-summary | ✅ 835 stocks, weighted_pe=16.32 |
| drillable-indices | ✅ 返回指数列表含估值指标 |
| 前端 VITE_BASE | ✅ 资源路径 /portfoliom2.0/assets/... |
| 原版 /portfoliom/ 回归 | ✅ HTTP 200 + API 响应（old code c009581 未受影响） |
| backend logs | ✅ 无错误，仅 200 OK + 预期 422（缺参数） |

### 关键 Git 提交

| commit | 说明 |
|--------|------|
| `10afea6` | feat: trading rebuild + price cache + security onboarding + valuation panel + LLM trading parse |
| `7c23701` | merge: feature/auth-upgrade → main |
| `12de386` | fix: add missing User import in main.py |
| `0ba1cad` | fix: merge User import fix to main |
| `0418f35` | chore: add docker-compose-2.0.yml |

## 2026-07-02 公共数据主数据重构 (Spec-1)

参考: docs/superpowers/specs/2026-07-02-master-data-overhaul-design.md

### 改动

- 3 张主表 `stock_master` / `fund_master` / `index_master` 替代单一 `security_master`
- 2 张分类表 `classification` + `classification_assign` (asset_type + theme 双维度)
- akshare 增量拉 A 股指数 (job_poll_index_master 每天 21:23)
- QQQ 手动 seed 脚本 (backend/scripts/_seed_qqq.py)
- 双向 typeahead 选择基金-指数映射 (SelectiveFundIndexDialog)
- 旧 `security_master` 改名 `security_master_legacy`,冻结只读

### 新增 admin 端点 (4 套 CRUD + 3 个工具)

- `GET/POST/PUT/DELETE /api/admin/stock-master`
- `GET/POST/PUT/DELETE /api/admin/fund-master`
- `GET/POST/PUT/DELETE /api/admin/index-master`
- `GET/POST/PUT/DELETE /api/admin/classification` + `/assign` + `/unassign` + `/assignments`
- `GET /api/admin/fund-master/lookup` + `GET /api/admin/index-master/lookup` (typeahead)
- `POST /api/admin/fund-index-map/selective` (双向选择式新增)
- `POST /api/admin/index-master/refresh` (手动触发 akshare 轮询)
- `POST /api/admin/index-master/seed-qqq`

### 新增前端组件

- `MasterDataPanel` 改 4 sub-tab (股票 / 基金 / 指数 / 分类维度)
- `StockMasterTab` / `FundMasterTab` / `IndexMasterTab` / `ClassificationTab`
- `SelectiveFundIndexDialog` (双向选择弹窗)
- 删除 `SecurityMasterTab` (新表已替代)
- `api.js` 加 10 个 client (stockMasterList / fundMasterList / indexMasterList / classificationList / fundMasterLookup / indexMasterLookup / indexMasterRefresh / classificationAssign / classificationUnassign / fundIndexMapSelective / stockMasterLookup... 等)

### 部署方案 (portfoliom3.0)

不修改现有 prod PG 或容器。新部署方案:
1. 在云端起新容器 `portfoliom3.0`,跑这份代码
2. nginx 切换路由: `https://chargeye133.duckdns.org/portfoliom3.0/` → 新容器
3. 本地 PG 已迁移 (36 records → 7 stock + 29 fund + 12 index + 14 classification),全量 `pg_dump` 后 scp 到云端,新容器连这个 PG
4. 现有 `portfoliom2.0` 不下线,作为回滚

### 关键 Git 提交 (本轮)

| commit | 说明 |
|--------|------|
| `9ff8372` | plan(master-data): 32-task plan |
| `545a278` | spec(master-data): Spec-1 design |
| `076b7e0` + `d8c99eb` | feat + fix: 5 SQLAlchemy models |
| `3764412` | register models in models.py |
| `e45abc9` + `2c6c7a2` + `13f0c79` + `307da65` | migration script + fallback security_type + rename |
| `0677377` - `dc8fa4a` | 4 services + 4 API endpoint sets (Phase 2) |
| `05e79e4` | UI 4 sub-tabs + delete SecurityMasterTab (Phase 3) |
| `a1eb047` - `16b5e9f` | lookup endpoints + SelectiveFundIndexDialog (Phase 4) |
| `c186b36` - `a0e677a` | akshare_index_poller + scheduler 21:23 + QQQ seed (Phase 5) |

### 已知遗留

1. **10 mojibake Chinese labels** in `classification.dimension='theme'`.display_label (security_master_legacy GBK 编码问题)。Admin 用新 ClassificationTab 编辑修复。
2. **classification_assign 表为空** — 迁移脚本未自动灌关联记录,admin 通过新 UI assign 或下次迁移。
3. **`datetime.utcnow()` deprecation warnings** — Python 3.12+ 推荐 `datetime.now(timezone.utc)`,但项目一致性保留旧 API。
4. **`backend/scripts/_*.py` gitignore** — 已加 `!backend/scripts/_seed_qqq.py` 例外,后续 seed 脚本需类似处理。

### 下一轮 (Spec-2, future)

- 全市场 A 股/港股/基金/指数名称代码一次性拉取 + 增量轮询
- 跨数据源去重 (TuShare + AKShare + EastMoney + ...)
- 用户级自选主数据 (per-user watchlist 复用 index_master)
