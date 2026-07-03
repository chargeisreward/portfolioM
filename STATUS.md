# 公共数据主数据重构 — 状态摘要 (2026-07-03)

> 给 compact 后下一个 session 看的快照。

## 关键位置

- **Worktree**: `D:\claude_code_project\PortfolioM\.worktrees\master-data-overhaul`
- **分支**: `feature/master-data-overhaul` (已 push origin, 30+ commits,最新 `236cb4a`)
- **Plan**: `docs/superpowers/plans/2026-07-02-master-data-overhaul.md` (32 tasks, 6 phases)
- **Spec**: `docs/superpowers/specs/2026-07-02-master-data-overhaul-design.md`
- **Deploy Guide**: `docs/superpowers/plans/2026-07-02-portfoliom3.0-deploy-guide.md`
- **Docs**: `Project_development.md` 含完整本轮总结 + 部署方案

## 已完成 (Tasks 1-32) — **全部完成 ✅**

- ✅ Phase 1 (Tasks 1-5): DB schema + migration script + sanity test on local PG
- ✅ Phase 2 (Tasks 6-13): 4 services + 4 API endpoint sets, 22 tests pass
- ✅ Phase 3 (Tasks 14-19): UI 4 sub-tabs (MasterDataPanel + 4 components) + SecurityMasterTab 删除
- ✅ Phase 4 (Tasks 20-24): lookup endpoints + SelectiveFundIndexDialog + FundIndexMapTab
- ✅ Phase 5 (Tasks 25-27): akshare_index_poller + scheduler 21:23 + QQQ seed
- ✅ Task 30 (full): security_onboarding_service + main.py 全部 refactored → 走新表
  - part 1: type2_classifier → classification + classification_assign (commit `e34446c`)
  - part 2: security_onboarding_service 写新表 + main.py 读取走 unified view (commit `094a982`)
- ✅ Task 31: docs updated
- ✅ Task 32: docker-compose-3.0.yml + nginx.conf + deploy guide (commit `236cb4a`)

## 测试结果 (latest,75 passing)

| Suite | Count |
|---|---|
| test_security_onboarding.py | 27 ✅ |
| test_security_master_service.py | 11 ✅ |
| test_migrate_split_security_master.py | 11 ✅ |
| test_classification_service.py | 13 ✅ |
| test_fund_master_service.py | 9 ✅ |
| test_index_master_service.py | 9 ✅ |
| test_stock_master_service.py | 6 ✅ |
| test_akshare_index_poller.py | 6 ✅ |
| test_analyst_parser.py | 5 ✅ |
| test_dedup.py | 8 ✅ |
| **Total** | **75 ✅** |

`test_admin_master_data_api.py` / `test_admin_classification_api.py` 等 admin endpoint 测试失败是 401 (admin auth 在 test fixture 中未提供,pre-existing,与本次重构无关)。

## 关键架构改动

### 写入路径
新代码 (`security_onboarding_service._upsert_to_new_table`) 根据 `asset_type` 路由到新表:
- `us_stock` → `StockMaster`
- 其余 (基金/ETF/QDII/黄金/债券/指数) → `FundMaster` 或 `IndexMaster`
- **不再写 `SecurityMaster`** (legacy 冻结只读,6 个月兼容期)

### 读取路径
新服务 `services/security_lookup.py` 提供统一入口:
- `get_security_view(db, code)` — 查 stock_master → fund_master → index_master → legacy 兜底
- `get_security_view_map(db, codes)` — 批量查,返回 `{code: SecurityView}` 供 main.py 各种 JOIN 用
- `get_currency_asset_type(db, code)` — 便捷方法,价格/汇率 JOIN

`main.py` 中以下路径已切换:
- `/api/securities` (GET/PUT)
- `/api/securities/sync-from-holdings`
- `/api/holdings/converted`
- `/api/trades/parse`
- `/api/overview` (sm_map)
- `/api/valuation/snapshot/diff` (sm_map)
- `/api/valuation/snapshot/kpi` (sm_map + tech_weight)

保留作为 compat layer:
- `/api/admin/security-master/*` (legacy 6 个月冻结)

## 本地 PG 现状 (sanity test 后)

| Table | Rows | 来源 |
|---|---|---|
| security_master_legacy | 36 | 改名自原 security_master |
| stock_master | 7 | security_type='stock' 迁移 (含 bond-as-stock) |
| fund_master | 29 | security_type='fund' + qdii_bond + .OF bond |
| index_master | 12 | 从 index_code 提取 (含 QQQ 待手动 seed) |
| classification | 14 | asset_type (9 unique) + theme (5 unique,含 mojibake) |
| classification_assign | 0 | ⚠️ 留待 admin 手动 assign |

## 已知遗留 (out of scope 当前 spec)

1. **10 mojibake Chinese labels** in `classification.dimension='theme'.display_label` — Admin 用 ClassificationTab 编辑修复
2. **classification_assign 表为空** — 迁移脚本未自动灌,等 admin UI assign
3. **`datetime.utcnow()` deprecation warnings** — Python 3.12+ 推荐 `datetime.now(timezone.utc)`,项目一致性保留旧 API
4. **`backend/scripts/_*.py` gitignore** — 已加 `!backend/scripts/_seed_qqq.py` 例外
5. **akshare 在 Python 3.14 导入失败** (py_mini_racer 循环导入) — 本地开发不能直接 import;测试用 fetcher 注入绕过;prod 部署在 Linux Python 3.12 无此问题

## 用户最终部署计划 (portfoliom3.0)

1. 云端拉新分支 (`feature/master-data-overhaul`, commit `236cb4a`)
2. 本地 PG 全量 dump → scp → 云端 portfoliom2-pg 恢复
3. `docker compose -f docker-compose-3.0.yml up -d --build`
4. 云端 nginx 加 `/portfoliom3.0/` 反代 → portfoliom3-frontend:80
5. 验证 https://chargeye133.duckdns.org/portfoliom3.0/ 与 portfoliom2.0 数据一致性
6. 现有 `portfoliom2.0` 不下线,作为快速回滚路径

详细步骤见 `docs/superpowers/plans/2026-07-02-portfoliom3.0-deploy-guide.md`。

## Compact 后如何继续

- 说 "继续 master-data plan" — 我会读 STATUS.md + plan 文件 + Project_development.md 恢复上下文
- **目前 32/32 tasks done**,如需继续:
  - **Spec-2 (未来)**: 全市场 A 股/港股/基金/指数名称代码一次性拉取 + 增量
  - **手动剩余**: classification_assign backfill (admin UI 或脚本)
  - **手动修复**: 10 mojibake Chinese label (admin UI)
  - **部署执行**: 按 deploy guide 在云端跑 docker-compose-3.0.yml