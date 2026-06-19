# PortfolioM 数据迁移文档

最后更新: 2026-06-19

## 背景

PortfolioM 历史上用 SQLite (`portfolio.db`) 做本地开发，2026-06 起转向 Postgres：
- 本地: Docker Postgres (`portfoliom-pg`)
- 生产: Zeabur Postgres Marketplace

迁移路径: **SQLite → 本地 Docker Postgres → Zeabur Postgres**

---

## 阶段 A: SQLite → 本地 Docker Postgres

### 何时需要

- 本地 docker postgres 数据陈旧 (例如落后几个月)
- 第一次切到 docker postgres (初始化)
- SQLite 有新数据要同步

### 步骤

```bash
# 0. 启动 docker postgres
docker start portfoliom-pg

# 1. (强烈推荐) 备份 docker postgres 现状
docker exec portfoliom-pg pg_dump -U portfoliom -d portfoliom \
  --no-owner --no-privileges \
  > backend/data/pg_backup/pre_migrate_$(date +%Y_%m_%d).sql

# 2. Dry-run: 比对行数, 不写数据
python backend/scripts/migrate_sqlite_to_pg.py --dry-run

# 3. 真迁移 (默认 APPEND, 只追加缺失行)
python backend/scripts/migrate_sqlite_to_pg.py

# 3'. (推荐首次迁移) TRUNCATE 全量替换
python backend/scripts/migrate_sqlite_to_pg.py --truncate
```

### 行为

| 模式 | 行为 |
|---|---|
| `--dry-run` | 只跑 `Base.metadata.create_all` + 比对 row counts, 不写数据 |
| (默认) APPEND | 跳过 `target_count >= source_count` 的表, 其它追加缺失行 |
| `--truncate` | 强制 TRUNCATE target + 全量重灌, 适用首次全量迁移 |

### 自动处理

脚本会自动处理以下 dialect 差异:

1. **Schema 不同步**: `Base.metadata.create_all` 自动建缺失表 (15/31 表)
2. **VARCHAR(N) 超长**: 扫描 SQLite 数据, 自动 ALTER PG 列到合适 bucket (50/100/200/500/1000)
3. **JSON 列**: SQLite TEXT 自动 json.loads + psycopg JSON adapter, 不会 double-encode
4. **Boolean 列**: SQLite INTEGER (0/1) → PG BOOLEAN (`is_trading`)
5. **PG 65535 参数上限**: 多列表自动缩小 chunk_size
6. **SERIAL 序列**: 同步 INTEGER PK 的序列到 MAX(id) (`funds.code` 这种 VARCHAR PK 自动跳过)
7. **连接泄漏**: 所有 connection 用 `with` 块, 避免 `idle in transaction` 持锁阻塞 TRUNCATE

### 回滚

```bash
# 1. 删 docker postgres 容器 (会丢数据!)
#    先确保你有了 backup
docker stop portfoliom-pg && docker rm portfoliom-pg

# 2. 重建 + 灌备份
docker run -d --name portfoliom-pg -p 5432:5432 \
  -e POSTGRES_USER=portfoliom -e POSTGRES_PASSWORD=localdev \
  -e POSTGRES_DB=portfoliom \
  postgres:16-alpine

# 3. 灌备份
cat backend/data/pg_backup/pre_migrate_2026_06_19.sql | \
  docker exec -i portfoliom-pg psql -U portfoliom -d portfoliom

# 4. 切回 SQLite: unset DATABASE_URL 即可, backend 自动 fallback
unset DATABASE_URL
```

或更轻量 — 直接回 SQLite:

```bash
# 让 backend 跳过 docker PG, 走 SQLite
unset DATABASE_URL
# backend 自动用 backend/portfolio.db
```

---

## 阶段 B: 本地 Docker Postgres → Zeabur Postgres

### 何时需要

- 本地 docker postgres 已有完整数据, 想同步到 Zeabur
- Zeabur postgres 是空的或陈旧

### 步骤

#### B.1 开启 Zeabur Postgres 公网访问 (临时)

1. Dashboard → Postgres service → Networking → 开启 **Public Access**
2. 记录 Zeabur 给的外部连接串 (例如 `postgres://user:pass@host.zeabur.com:5432/db`)

#### B.2 从本地 docker pg → Zeabur pg

```bash
# 1. 用 docker exec 导出本地 docker pg (绕过 docker 网络限制)
docker exec portfoliom-pg pg_dump -U portfoliom -d portfoliom \
  --no-owner --no-privileges --no-acl \
  > /tmp/local_dump.sql

# 2. 灌入 Zeabur (用 Zeabur 外部连接串)
PGPASSWORD='zeabur_password' psql \
  -h host.zeabur.com -p 5432 -U user -d db \
  -f /tmp/local_dump.sql
```

#### B.3 关闭公网访问

Dashboard → Postgres service → Networking → 关闭 Public Access

#### B.4 验证 backend 连 Zeabur PG

```bash
# Backend 自动读 POSTGRES_CONNECTION_STRING (Zeabur 注入)
curl https://portback.zeabur.app/api/admin/db-info \
  -H "X-Admin-Token: $ADMIN_TOKEN"

# 应看到:
#   kind: postgres
#   masked: postgresql+psycopg://user:***@<zeabur-host>.zeabur.com:5432/db
#   server: PostgreSQL 16.x
#   total_rows: 与本地 docker pg 一致
```

### 回滚 (阶段 B)

阶段 B 没有删 docker pg, 所以 docker pg 还在。
如要"回滚 Zeabur 端":

1. 重新触发 Zeabur backend deploy (会自动跑 `Base.metadata.create_all` 建空 schema)
2. 数据为空, Zeabur 端 backend 会返回空 (这是预期的, 因为 docker pg 还有完整数据)

---

## 增量同步 (开发期常用)

API 限流不稳定 → 增量数据 (新价格/新公告) 由 backend 在 Zeabur 端直接拉。
存量数据 → 一次性迁移 + 后续走增量。

日常开发:
- 本地 SQLite (默认)
- 切 docker pg: `export DATABASE_URL=postgresql+psycopg://portfoliom:localdev@localhost:5432/portfoliom`
- 切回 SQLite: `unset DATABASE_URL`

---

## 已知坑

1. **`idle in transaction` 持锁**: 如果有 Python 进程死锁, docker pg 后续 TRUNCATE 会无限挂起。
   修复: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction'`
2. **PG 65535 参数上限**: 单条 INSERT 超过 → 自动缩 chunk_size (脚本已处理)
3. **VARCHAR(20) 太短**: SQLite 数据脏 → 脚本自动拓宽 (已处理)
4. **JSON double-encode**: 不要 json.dumps 后再让 adapter 转, 直接传 dict (脚本已处理)
5. **`zeabur deploy --service-id` 会覆盖 Dockerfile**: 不要用, 详见 `~/.claude/projects/.../memory/zeabur-cli-danger.md`

---

## 相关文件

- `backend/scripts/migrate_sqlite_to_pg.py` — 主迁移脚本
- `backend/data/pg_backup/` — docker pg 备份目录 (git ignored)
- `backend/config.py` — DB URL 优先级 (POSTGRES_CONNECTION_STRING → POSTGRES_URI → DATABASE_URL → SQLite)
- `backend/main.py:782` — `/api/admin/db-info` 诊断端点
