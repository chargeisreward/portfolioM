# PortfolioM 3.0 部署指南 (portfoliom3.0 新容器)

> 部署 `feature/master-data-overhaul` 分支,与 portfoliom2.0 并行运行。
> 旧 `portfoliom2.0` 保留作为快速回滚路径。

## 1. 部署前检查

### 1.1 代码状态
- 分支 `feature/master-data-overhaul` 已 push 到 origin (commit `094a982` 之后)
- 本地所有测试通过 (27 onboarding + 32 service tests)
- 前端 dist 已 build 成功 (`frontend/dist/`,1.47 MB)

### 1.2 数据库
- 本地 PG (`portfoliom-pg`) 已迁移完毕:
  - 36 行 security_master_legacy (旧表只读)
  - 7 行 stock_master + 29 行 fund_master (新主表)
  - 12 行 index_master + 14 行 classification (新表)
- `classification_assign` 表为空 (等 admin 用 ClassificationTab UI 手动 assign)
- 10 条 mojibake Chinese label 在 `classification.theme.display_label`(admin UI 修复)

### 1.3 已知问题(部署前心里有数)
1. `datetime.utcnow()` deprecation warnings (Python 3.12+) — 运行时 warning,不影响功能
2. `backend/scripts/_*.py` gitignore — 已加 `!backend/scripts/_seed_qqq.py` 例外
3. akshare 在 Python 3.14 导入失败 (py_mini_racer 循环导入) — **prod 用 Python 3.12 镜像,无此问题**
4. 10 条 mojibake Chinese label 在 ClassificationTab UI 中显示为乱码 — admin 修复

## 2. 数据库复制 (本地 PG → 云端 PG)

### 2.1 本地 dump
```bash
docker exec portfoliom-pg pg_dump -U portfoliom -d portfoliom \
    --no-owner --clean --if-exists \
    -f /tmp/portfoliom_3.0_init.sql

docker cp portfoliom-pg:/tmp/portfoliom_3.0_init.sql \
    /tmp/portfoliom_3.0_init.sql
```

### 2.2 上传到云端
```bash
scp /tmp/portfoliom_3.0_init.sql chargeye133:/tmp/
```

### 2.3 云端恢复
云端的 `portfoliom2-pg` 容器已被 portfoliom2.0 用过,需停 backend 防止写入冲突:
```bash
# 1. 停 portfoliom2.0 backend (留 PG 容器运行)
ssh chargeye133 "docker stop portfoliom2-backend"

# 2. 在云端 PG 上 restore
ssh chargeye133 "docker exec -i portfoliom2-pg psql -U portfoliom -d portfoliom" < /tmp/portfoliom_3.0_init.sql

# 3. 重启 portfoliom2.0 backend
ssh chargeye133 "docker start portfoliom2-backend"
```

> 注:本地 PG 容器名是 `portfoliom-pg`,云端是 `portfoliom2-pg`,根据环境替换。

## 3. 部署 portfoliom3.0

### 3.1 在云端拉新代码
```bash
ssh chargeye133
cd /path/to/PortfolioM  # 假设已 clone
git fetch origin
git checkout feature/master-data-overhaul
git pull  # 拉到本地最新 (commit 094a982)
```

### 3.2 创建 .env 文件
```bash
cd /path/to/PortfolioM
cat > .env <<'EOF'
PG_PASSWORD=portfoliom2_prod
LLM_API_KEY=sk-xxxxxx
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
EOF
chmod 600 .env
```

### 3.3 构建 + 启动
```bash
docker compose -f docker-compose-3.0.yml build --no-cache
docker compose -f docker-compose-3.0.yml up -d
```

### 3.4 验证启动
```bash
# 检查容器状态
docker ps | grep portfoliom3

# 检查 backend 日志 (是否有 import error)
docker logs portfoliom3-backend --tail 50

# 健康检查
docker exec portfoliom3-backend curl -fsS http://localhost:8000/api/auth/status
```

## 4. nginx 反代配置

### 4.1 在云端 nginx 添加 /portfoliom3.0/ 路由
编辑云端 nginx 配置 (路径因部署而异,通常在 `/etc/nginx/conf.d/portfoliom.conf`):

```nginx
# /portfoliom3.0/ → portfoliom3-frontend:80
location ^~ /portfoliom3.0/ {
    proxy_pass http://portfoliom3-frontend:80;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# 静态资源缓存 (Vite 产物带 hash)
location ^~ /portfoliom3.0/assets/ {
    proxy_pass http://portfoliom3-frontend:80;
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

### 4.2 重载 nginx
```bash
ssh chargeye133 "nginx -t && nginx -s reload"
```

## 5. 验证

### 5.1 浏览器访问
- 主页: https://chargeye133.duckdns.org/portfoliom3.0/
- 应看到 MasterDataPanel 4 sub-tab (stocks / funds / indices / classification)

### 5.2 数据一致性检查
对比新旧两边的相同指标 (持仓总市值、估值快照、tech_weight_pct),应一致:
- portfoliom2.0: https://chargeye133.duckdns.org/portfoliom2.0/
- portfoliom3.0: https://chargeye133.duckdns.org/portfoliom3.0/

### 5.3 验证 cron 任务注册
```bash
# portfoliom3.0 应自动注册 akshare index poller (每天 21:23)
docker exec portfoliom3-backend python -c "
from services.scheduler import scheduler
for job in scheduler.get_jobs():
    print(f'{job.id}: {job.name} - {job.trigger}')
"
```

应看到 `job_poll_index_master: 拉取 A 股指数 ... - cron[hour='21', minute='23']`

## 6. 回滚

如遇严重问题,快速回滚到 portfoliom2.0:
```bash
# 1. 停 portfoliom3.0 容器
ssh chargeye133 "docker compose -f /path/to/PortfolioM/docker-compose-3.0.yml down"

# 2. nginx 路由切回 (或直接禁用 portfoliom3.0/ 路由)
# 3. portfoliom2.0 仍运行,继续服务用户
```

> 注:`docker compose ... down` 默认**保留** volume 和 image,**不删 PG 数据**,
> 旧数据 (security_master_legacy + 新表) 全部保留。

## 7. 部署后清理(可选)

部署稳定后(1 周),可以清理遗留脚本:
- 本地:`rm -rf /tmp/mdo-scripts/` (sanity test 残留)
- 云端:`rm /tmp/portfoliom_3.0_init.sql` (dump 文件)
- 代码侧:`backend/scripts/migrate_split_security_master.py` 可保留作为参考

## 8. 参考文件

- `docker-compose-3.0.yml` — 新容器编排
- `backend/Dockerfile` — 后端镜像 (Python 3.12-slim)
- `frontend/Dockerfile` + `frontend/nginx.conf` — 前端镜像 + nginx 路由
- `Project_development.md` — 完整本轮总结
- `docs/superpowers/specs/2026-07-02-master-data-overhaul-design.md` — 设计文档
- `docs/superpowers/plans/2026-07-02-master-data-overhaul.md` — 32-task 实施计划