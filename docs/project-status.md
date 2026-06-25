# PortfolioM 项目状态与任务清单

**更新日期：** 2026-06-25  
**当前分支：** main（存在大量未提交实现）  
**最近提交：** `0838ea8 spec: fund penetration + industry aggregation design`

---

## 已完成功能

### 1. 数据模型（后端）

| 表 / 模块 | 状态 | 位置 |
|-----------|------|------|
| 证券基础表、持仓表、汇率表、交易日历、API 代码映射 | 已完成 | `backend/models.py` |
| 基金→指数映射快照 `fund_index_map` | 已完成 | `backend/models.py` |
| 指数成分股快照 `index_constituent_snapshot` | 已完成 | `backend/models.py` |
| A 股估值快照 `a_share_financial_snapshot` | 已完成 | `backend/models.py` |
| 港股估值快照 `hk_share_financial_snapshot` | 已完成 | `backend/models.py` |
| 穿透结果快照 `penetration_snapshot` | 已完成 | `backend/models.py` |
| 全持仓快照 `full_holding_snapshot` | 已完成 | `backend/models.py` |
| 聚合缓存 `aggregation_cache` | 已完成 | `backend/models.py` |
| CSI300 成分股快照 `csi300_constituent_snapshot` | 已完成 | `backend/models.py` |
| 估值时序 `aggregation_timeseries` | 已完成 | `backend/models.py` |

### 2. 数据导入

| 功能 | 状态 | 位置 |
|------|------|------|
| 数据版本管理 `data_version.csv` | 已完成 | `backend/services/data_version.py` |
| 基金→指数映射导入 | 已完成 | `backend/scripts/import_fund_index_map.py` |
| 指数成分股导入 | 已完成 | `backend/scripts/import_index_constituents.py` |
| A 股估值快照导入 | 已完成 | `backend/scripts/import_a_share_financials.py` |
| 港股估值快照导入 | 已完成 | `backend/scripts/import_hk_share_financials.py` |
| 399673 创业板 50 官方权重导入 | 已完成 | `backend/scripts/import_399673_cons.py` |
| 导入通用工具（价格解析、动态指标计算） | 已完成 | `backend/scripts/import_common.py` |
| 一键导入 + 穿透 + 聚合 API | 已完成 | `POST /api/admin/import-source-data` |
| 启动时自动导入最新快照 | 已完成 | `backend/main.py::startup` |

### 3. 穿透与聚合引擎

| 功能 | 状态 | 位置 |
|------|------|------|
| 基金可下钻性判断 | 已完成 | `backend/services/drillable_funds.py` |
| 权重不变重算穿透（weight-invariant recompute） | 已完成 | `backend/services/penetration_v2.py` |
| 全持仓快照合并 | 已完成 | `backend/services/penetration_v2.py` |
| 多维度聚合（申万 L1-L4 / 中证 L1-L4 / 战略新兴 L1-L4 / 产业链 / 增长 / 竞争） | 已完成 | `backend/services/aggregation.py` |
| 虚拟盈利法加权 PE/PB/PS | 已完成 | `backend/services/aggregation.py` |
| CSI300 基准对比 | 已完成 | `backend/services/aggregation.py` + `/api/penetration/portfolio-vs-csi300` |
| 估值时序生成 | 已完成 | `backend/services/aggregation.py::write_timeseries_for_day` |

### 4. 交易日历与价格系统

| 功能 | 状态 | 位置 |
|------|------|------|
| CN / HK / US / OF 交易日历 | 已完成 | `backend/services/trading_calendar.py` |
| 日历惰性持久化 | 已完成 | `backend/services/trading_calendar.py` |
| 价格缓存 `price_cache` | 已完成 | `backend/models.py` |
| 腾讯实时行情 + K 线 | 已完成 | `backend/crawlers/price_data.py` |
| API 代码映射表 | 已完成 | `backend/services/code_map.py` |
| A+H 底层证券 6 个月历史价拉取 | 已完成 | `backend/scripts/pull_history_prices.py` |
| OF 基金历史净值拉取 | 已完成 | `backend/scripts/pull_fund_nav.py` |
| 缺失 current_price 补全 | 已完成 | `backend/services/price_filler.py` |
| 持仓历史价回补 API | 已完成 | `POST /api/admin/backfill-prices` |
| 价格缺口检查任务 | 已完成 | `backend/services/scheduler.py::job_backfill_gaps` |

### 5. 后端 API（新增）

全部已实现并通过 `backend/main.py` 暴露：

- `GET /api/data-version`
- `GET /api/penetration/full-holding`
- `GET /api/penetration/dimension`
- `GET /api/penetration/dimension-detail`
- `GET /api/penetration/timeseries`
- `GET /api/penetration/kpi`
- `GET /api/penetration/portfolio-vs-csi300`
- `GET /api/penetration/hk-concepts`
- `GET /api/penetration/drillable-indices`
- `GET /api/penetration/index-drill`
- `POST /api/admin/import-source-data`
- `POST /api/admin/recalc-aggregation`
- `POST /api/admin/fill-prices-tencent`
- `POST /api/admin/backfill-prices`

### 6. 前端组件

| 组件 | 状态 | 位置 |
|------|------|------|
| 数据版本状态栏 | 已完成 | `frontend/src/components/DataVersionBar.jsx` |
| 行业分解面板 | 已完成 | `frontend/src/components/IndustryBreakdownPanel.jsx` |
| 行业下钻明细表 | 已完成 | `frontend/src/components/IndustryDrilldownTable.jsx` |
| 估值时序图 | 已完成 | `frontend/src/components/MetricTimeseriesChart.jsx` |
| 全持仓表 | 已完成 | `frontend/src/components/FullHoldingTable.jsx` |
| 可下钻基金卡片页 | 已完成 | `frontend/src/components/DrillableFundsPage.jsx` |
| 组合 vs CSI300 卡片 | 已完成 | `frontend/src/components/PortfolioVsCsi300Card.jsx` |
| 交易日历视图 | 已完成 | `frontend/src/components/TradingCalendarView.jsx` |
| API 客户端新增接口 | 已完成 | `frontend/src/api.js` |

---

## 待进行任务

### 高优先级

1. **提交当前实现**
   - 当前工作区有大量未提交新增文件和修改，需要整理成若干清晰 commit 后提交。
   - 涉及文件：`backend/models.py`、`backend/main.py`、`backend/database.py`，以及新增的服务 / 脚本 / 前端组件。

2. **端到端验证**
   - 运行 `POST /api/admin/import-source-data?source_folder=202605数据`
   - 验证 `full_holding_snapshot`、`aggregation_cache`、`aggregation_timeseries` 行数与预期一致
   - 检查 `/api/penetration/kpi`、`/api/penetration/dimension`、`/api/penetration/timeseries` 返回真实数值

3. **价格完整性再确认**
   - 运行 `python scripts/pull_history_prices.py --days 180 --market AH`
   - 运行 `python scripts/pull_fund_nav.py --days 180`
   - 检查 `/api/penetration/timeseries?window=180` 的 `missing_dates`
   - 对仍缺价格的证券，调用 `POST /api/admin/fill-prices-tencent`

4. **前端集成收尾**
   - 确认 `AnalysisPanel.jsx` 的 tab 切换、下钻展开、趋势图展开全部使用真实 API
   - 确认 `OverviewPanel.jsx` 的 6 张 KPI 卡片来自 `/api/penetration/kpi`
   - 移除任何残留硬编码数字或 mock 数据

### 中优先级

5. **官方指数成分股爬虫硬化**
   - 当前 `backend/scripts/crawl_index_official.py` 已创建，但只覆盖部分来源
   - 需要为 CSI、国证、深交所、恒生分别实现稳定的 xlsx/json 下载和解析
   - 支持 `--as-of-date` 参数以便回溯历史快照

6. **2026-06 月度数据准备**
   - 6 月底需要准备 `sourceData/202606数据/`
   - 文件清单：`基金-指数.xlsx`、`指数构成.xlsx`、`全部A股.xlsx`、`全部港股.xlsx`
   - 更新 `sourceData/data_version.csv`

7. **测试覆盖**
   - 为 `penetration_v2`、`aggregation`、`data_version` 增加单元测试
   - 至少覆盖：权重不变重算公式、虚拟盈利 PE 公式、跨市场代码匹配

8. **双币种规则推广到其他单价出现处**（2026-06-25 确立规则，下钻已修复，其他待改）
   - 规则：所有单价同时存「原币」+「本币(CNY)」，本币在公共数据层算好存表，下游取公共数据不临时算
   - 待改点 1：`backend/services/drillable_funds.py`（旧模块，deprecated）— full-holding-table 端点已迁移到三层 service，此模块不再被该端点使用，仅作为旧逻辑留存
   - ✅ 已完成 — 待改点 2：`backend/main.py` 的 `/api/penetration/full-holding-table` 端点（2026-06-25 迁移到 `drill_orchestration_service.get_all_drill_constituents`）
   - ✅ 已完成 — 待改点 3：`frontend/src/components/FullHoldingTable.jsx`（2026-06-25 drilled 段去掉 `toCNY` 双重折算，优先用后端本币字段 + 动态估值字段）
   - ✅ 已完成 — 4 口径估值卡片算法统一（2026-06-25 `/api/penetration/full-holding-summary` 迁移到 `compute_scope_metrics`，与下钻卡片算法完全一致；详见 `Project_development.md`「2026-06-25 全持仓下钻迁移到三层架构 + 4 口径指标统一算法」章节）
   - 待改点 4：`ExchangeRate` 表 HKD→CNY 恒定 0.92（2026-06-21~25 全同），真实汇率应每日波动 — 量纲正确但精度不足，需接入真实每日汇率源
   - 待改点 5：`PriceCache` 表无 `currency` 字段 — 币种完全靠 `_guess_currency` 后缀推断，目前与腾讯数据源一致（实证 .HK 返回 HKD），但若换数据源会静默出错；建议加 `currency` 列在拉取时标记
   - 待改点 6：`_guess_currency`（drill_snapshot.py line 187）按后缀推断币种缺防御性 — 应改为优先取 `SecurityMaster.currency` / `PriceCache.currency`，后缀推断仅作 fallback
   - 参考：`Project_development.md` 「2026-06-25 港股通下钻汇率量纲修正 + 双币种规则」 + 「2026-06-25 全持仓下钻迁移到三层架构 + 4 口径指标统一算法」章节
   - 验证脚本：`backend/scripts/verify_hk_drill_fx.py`（偏差）、`backend/scripts/verify_hk_currency.py`（币种实证）、`backend/scripts/verify_full_holding_migration.py`（全持仓迁移验证）

### 低优先级 / 后续迭代

9. **部署与数据同步**
   - 本地 SQLite → Zeabur Postgres 的同步脚本验证
   - `portfolio.db` 大文件不应提交到 git
   - 生产环境 `ADMIN_TOKEN`、`APP_PASSWORD` 配置

10. **性能优化**
    - `/api/trend` 当前已优化到 ~0.3s，可继续观察 180/360 天窗口表现
    - `pull_history_prices.py` 可考虑并发请求加速

11. **文档补充**
    - 补充前端组件使用说明
    - 补充部署操作手册
    - 补充数据导入失败排查指南

---

## 最近验证过的入口

1. 启动后端：`cd backend && python main.py`
2. 初始化 / 登录后访问：`http://localhost:8000`
3. 顶部状态栏应显示：业务日期、A 股价格日期、港股价格日期、美股价格日期
4. 进入“分析”页，应能看到行业、产业链、增长分层等维度表格
5. 点击行业行可展开底层股票；点击列头可展开 90/180/360 天趋势图

---

## 相关文档

- [`superpowers/specs/2026-06-17-fund-penetration-analysis-design.md`](./superpowers/specs/2026-06-17-fund-penetration-analysis-design.md) — 基金穿透与行业聚合设计
- [`reference-price-system.md`](./reference-price-system.md) — 价格与交易日历系统参考
- [`howto-backfill-6m-prices.md`](./howto-backfill-6m-prices.md) — 6 个月收盘价补全操作指南
- `../SPEC.md` — 项目整体架构与数据模型
- `../data_get.md` — 全项目数据源总览
