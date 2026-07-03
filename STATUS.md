# 公共数据主数据重构 — 状态摘要 (2026-07-02)

> 给 compact 后下一个 session 看的快照。

## 关键位置

- **Worktree**: `D:\claude_code_project\PortfolioM\.worktrees\master-data-overhaul`
- **分支**: `feature/master-data-overhaul` (已 push origin, 28+ commits)
- **Plan**: `docs/superpowers/plans/2026-07-02-master-data-overhaul.md` (32 tasks, 6 phases)
- **Spec**: `docs/superpowers/specs/2026-07-02-master-data-overhaul-design.md`
- **Docs**: `Project_development.md` 含完整本轮总结 + 部署方案

## 已完成 (Tasks 1-29, 31)

- ✅ Phase 1 (Tasks 1-5): DB schema + migration script + sanity test on local PG
- ✅ Phase 2 (Tasks 6-13): 4 services + 4 API endpoint sets, 22 tests pass
- ✅ Phase 3 (Tasks 14-19): UI 4 sub-tabs (MasterDataPanel + 4 components) + SecurityMasterTab 删除
- ✅ Phase 4 (Tasks 20-24): lookup endpoints + SelectiveFundIndexDialog + FundIndexMapTab
- ✅ Phase 5 (Tasks 25-27): akshare_index_poller + scheduler 21:23 + QQQ seed
- ✅ Task 30 (part 1): type2_classifier refactored → uses classification + classification_assign

## 已完成部分 - Task 30 (剩余)

- ✅ `backend/services/type2_classifier.py` (done in commit `e34446c`)
- ⏳ `backend/services/security_onboarding_service.py` — 6 refs 待改 (主路径: on_board 写新表 stock_master/fund_master 替代 SecurityMaster)
- ⏳ `backend/main.py` — 18 refs 待改 (大多是注释 + 重复表名引用 + admin 端点移到新端点)
- ⏳ `backend/migrate_admin_columns.py` — 4 refs (历史迁移脚本,可不改)

## 未开始 - Task 32 (部署)

- 用户部署方案: 部署新容器 `portfoliom3.0`,nginx 路由,本地 PG 全量复制云端
- 需要: 构建 backend 镜像 + frontend dist (已 build 成功 1.47 MB) + 写 docker-compose-3.0.yml
- 不动现有 prod portfoliom2.0 容器

## 本地 PG 现状 (sanity test 后)

| Table | Rows | 来源 |
|---|---|---|
| security_master_legacy | 36 | 改名自原 security_master |
| stock_master | 7 | security_type='stock' 迁移 (含 bond-as-stock) |
| fund_master | 29 | security_type='fund' + qdii_bond + .OF bond |
| index_master | 12 | 从 index_code 提取 (含 QQQ 待手动 seed) |
| classification | 14 | asset_type (9 unique) + theme (5 unique,含 mojibake) |
| classification_assign | 0 | ⚠️ 留待 admin 手动 assign 或 Task 30 backfill |

## 已知遗留 (out of scope 当前 spec)

1. **10 mojibake Chinese labels** in `classification.dimension='theme'`.display_label (security_master_legacy GBK 编码问题)。Admin 用新 ClassificationTab 编辑修复。
2. **classification_assign 表为空** — 迁移脚本未自动灌,等 Task 30 后续 backfill 或 admin UI assign。
3. **`datetime.utcnow()` deprecation warnings** — Python 3.12+ 推荐 `datetime.now(timezone.utc)`,项目一致性保留旧 API。
4. **`backend/scripts/_*.py` gitignore** — 已加 `!backend/scripts/_seed_qqq.py` 例外,后续 seed 脚本需类似处理。
5. **akshare 在 Python 3.14 导入失败** (py_mini_racer 循环导入) — 本地开发不能直接 import;测试用 fetcher 注入绕过;prod 部署在 Linux Python 3.x 无此问题。

## 用户最终部署计划 (portfoliom3.0)

1. 在云端起新容器 `portfoliom3.0`,跑 `feature/master-data-overhaul` 代码
2. nginx 切换路由: `https://chargeye133.duckdns.org/portfoliom3.0/` → 新容器
3. 本地 PG 已迁移,全量 `pg_dump` 后 scp 到云端,新容器连这个 PG
4. 现有 `portfoliom2.0` 不下线,作为回滚

## Compact 后如何继续

- 说 "继续 master-data plan" — 我会读 STATUS.md + plan 文件 + Project_development.md 恢复上下文
- 接下来: Task 30 (security_onboarding_service + main.py 18 refs) → Task 32 (部署 docker-compose-3.0.yml)
