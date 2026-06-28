# PortfolioM 用户权限 / 数据隔离升级 — 设计

**作者**: Chargeye (mini-claude)
**日期**: 2026-06-23
**范围**: 本地开发 + 本地测试；**云端不在本次升级范围**
**状态**: 设计稿（待 review）

---

## Context（为何做这件事）

PortfolioM 当前是**单密码全局访问**：所有登录者看到同一份 holdings / watchlist / analyst report。`grep user_id` 在整个 `backend/` 命中 0 次。

产品定位已经明确（用户本次重新表述）：

- **用户（User）**：在自家交易终端做交易维护；本项目对其实现**持仓聚合 / 持仓分析**。导入 / 维护自己的持仓。
- **顾问（Advisor）**：帮助用户分析持仓；可访问客户的视图、可在客户的关注列表里加入建议股票，但**看不到交易明细**，**不能代客户导入**。
- **管理员（Admin）**：维护分析所需要的基础数据（指数构成、指数分类、补价、运维）；不维护交易。

本次升级把这三类角色落地为数据库 + 鉴权 + UI 三层结构，并补足 4 类「数据补足」检测 + 顾问-客户双向关联。

---

## 10 项关键决策（brainstorming 阶段已确认）

| # | 主题 | 决策 |
|---|------|------|
| 1 | 账户模型 | 单表 + 角色字段 (`users.is_advisor`, `users.is_admin`) |
| 2 | 切换账户 | **视图代理**（前端 UI 切换，后端 session 仍是顾问/admin 自身） |
| 3 | TradingPanel | 预留「交易维护」placeholder；现有运维按钮迁移到 Admin 「运维」面板 |
| 4 | 持仓导入 | **用户自导入**（顾问/admin 不可代） |
| 5 | 顾问加关注 | 限于其代理下的客户（watchlist.owner_user_id = 客户 id） |
| 6 | 0.8% 阈值 | 分母 = 组合总市值 |
| 7 | 顾问-用户关联 | **双向预占** (PENDING → ACTIVE / CANCELLED) |
| 8 | 云端 | 本期**仅本地**，云端不动 |
| 9 | 现有数据迁移 | holdings/watchlist 归到新 admin 账户；分析师表共享不拆 |
| 10 | 密码 | **bcrypt** |

---

## Section 1 — 后端：数据模型新增

### 1.1 新增 5 张表

```python
# models.py 新增

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)  # bcrypt
    display_name = Column(String(64), nullable=True)
    is_advisor = Column(Boolean, nullable=False, default=False, index=True)
    is_admin = Column(Boolean, nullable=False, default=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserRelation(Base):
    __tablename__ = "user_relations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    advisor_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    client_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="PENDING")  # PENDING|ACTIVE|CANCELLED
    initiator_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("advisor_user_id", "client_user_id", name="uq_relation"),)


class IndexClassification(Base):
    __tablename__ = "index_classification"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    index_code = Column(String(32), unique=True, nullable=False, index=True)
    index_name = Column(String(128), nullable=True)
    category = Column(String(64), nullable=True)   # 宽基/行业/主题/策略
    theme = Column(String(64), nullable=True)      # 新兴产业/us_tech/红利/...
    benchmark_formula = Column(Text, nullable=True)
    source = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DataGapReport(Base):
    __tablename__ = "data_gap_report"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    gap_type = Column(String(32), nullable=False, index=True)
        # stock_report | index_constituent | index_classification
    stock_code = Column(String(32), nullable=True, index=True)
    index_code = Column(String(32), nullable=True, index=True)
    as_of_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="OPEN")  # OPEN|FIXED
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)


class HoldingImportLog(Base):
    __tablename__ = "holding_import_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    import_source = Column(String(16), nullable=False)  # user_upload|admin_upload
    file_name = Column(String(255), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    imported_at = Column(DateTime, default=datetime.utcnow, index=True)
```

### 1.2 修改 4 张已有表

| 表 | 改动 |
|---|------|
| `holdings` | + `user_id BIGINT NOT NULL DEFAULT 1` (FK users.id) |
| `watchlist` | PK 改为 `(user_id, code)`；+ `user_id BIGINT NOT NULL DEFAULT 1` |
| `access_sessions` | + `user_id BIGINT NULL` (FK users.id)；保留 ip 列 |
| `access_attempts` | 保留不变（按 IP 限流） |

### 1.3 用户隔离表（写入/查询必须带 user_id）

- holdings, watchlist, penetration_results, penetration_snapshot, full_holding_snapshot, aggregation_cache, aggregation_timeseries, holding_import_log

### 1.4 共享表（保持原状，不带 user_id）

- security_master, security_type_config, funds, index_constituents, index_constituent_snapshot, fund_index_map, fund_daily_nav
- a_share_financial_snapshot, hk_share_financial_snapshot, csi300_constituent_snapshot, csi300_baseline
- price_cache, stock_info_cache, stock_financials, exchange_rates, trading_calendar
- api_code_map, data_version
- 所有资讯类（global_flash_news, stock_news, announcements, research_reports, hot_stock_signals）
- **analyst_company_report**（共享不拆；见 §6）
- **analyst_industry_chain** / **analyst_industry_chain_company**（产业链公共知识）
- index_classification（新增；admin 维护，全部 user 共享）

### 1.5 init_db 迁移策略

`backend/database.py::init_db()` 现有 `_MIGRATIONS` 列表追加：

```python
# 自动 ALTER / CREATE（启动时执行）
_MIGRATIONS.append("ALTER TABLE holdings ADD COLUMN IF NOT EXISTS user_id BIGINT NOT NULL DEFAULT 1")
_MIGRATIONS.append("ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS user_id BIGINT NOT NULL DEFAULT 1")
_MIGRATIONS.append("ALTER TABLE access_sessions ADD COLUMN IF NOT EXISTS user_id BIGINT")
# CREATE TABLE users / user_relations / index_classification / data_gap_report / holding_import_log
# 由 Base.metadata.create_all 自动处理（init_db 已调用）
```

### 1.6 数据迁移（新启动时执行一次）

`init_db()` 检测到 `users` 表为空时：

1. 创建 admin 账户：`username='admin' / password='admin123'`（bcrypt），`is_admin=True, is_advisor=False, display_name='系统管理员'`
2. 把所有现有 `holdings` / `watchlist` 行的 `user_id` 设为 admin.id
3. **不做 analyst_company_report 迁移**（共享）
4. 在 startup log 打印 `[MIGRATION] 创建 admin 用户 / 已分配 holdings/watchlist 到 admin`

### 1.7 启动 seed（测试用，本地）

`scripts/seed_users.py` 创建 4 个测试账户：

| username | password | is_advisor | is_admin | display_name |
|----------|----------|------------|----------|--------------|
| admin | admin123 | false | true | 系统管理员 |
| advisor_x | advisor123 | true | false | 张顾问 |
| user_a | user123 | false | false | 王先生 |
| user_b | user123 | false | false | 李女士 |

---

## Section 2 — 后端：认证 / 中间件改造

### 2.1 `/api/auth/login` 改为多用户

```python
@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    # body: {username: str, password: str}
    user = db.query(User).filter(User.username == body.username, User.is_active == True).first()
    if not user or not bcrypt.checkpw(body.password.encode(), user.password_hash.encode()):
        # IP 限流 + AccessAttempt 计数（同现有逻辑）
        ...
        raise HTTPException(401, "用户名或密码错误")
    # 创建 session
    token = secrets.token_hex(32)
    session = AccessSession(token=token, ip=request.client.host, user_id=user.id,
                            expires_at=datetime.utcnow() + timedelta(hours=24))
    db.add(session); db.commit()
    user.last_login_at = datetime.utcnow(); db.commit()
    return {
        "token": token,
        "expires_in": 86400,
        "user": {
            "id": user.id, "username": user.username,
            "display_name": user.display_name,
            "is_advisor": user.is_advisor, "is_admin": user.is_admin,
        },
    }
```

### 2.2 新增 `/api/auth/me`

```python
@app.get("/api/auth/me")
def auth_me(request: Request, db: Session = Depends(get_db)):
    user = _current_user(request, db)
    return {"user": _user_to_dict(user)}
```

### 2.3 auth_middleware 注入 user_id

```python
@app.middleware("http")
def auth_middleware(request: Request, call_next):
    # ... 现有 token 校验 ...
    # 解析 token → 查 AccessSession → 注入 user
    sess = db.query(AccessSession).filter(AccessSession.token == token).first()
    if sess and sess.user_id:
        user = db.query(User).filter(User.id == sess.user_id).first()
        request.state.user = user
        request.state.user_id = user.id
        request.state.is_advisor = user.is_advisor
        request.state.is_admin = user.is_admin
    # ... 否则继续（公开路径或 401）...
```

### 2.4 角色依赖

```python
def require_user():
    """user / advisor / admin 都可以"""
    def dep(request: Request):
        u = getattr(request.state, "user", None)
        if not u: raise HTTPException(401, "请登录")
        return u
    return dep

def require_advisor():
    def dep(request: Request):
        u = getattr(request.state, "user", None)
        if not u: raise HTTPException(401, "请登录")
        if not (u.is_advisor or u.is_admin): raise HTTPException(403, "需要顾问或管理员权限")
        return u
    return dep

def require_admin():
    def dep(request: Request):
        u = getattr(request.state, "user", None)
        if not u: raise HTTPException(401, "请登录")
        if not u.is_admin: raise HTTPException(403, "需要管理员权限")
        return u
    return dep
```

挂在路由：`/api/admin/*` 改用 `Depends(require_admin())`；admin 路由同时**保留** `x-admin-token` 双轨（向后兼容）。

### 2.5 视图代理：effective_user_id

涉及**持仓 / 关注**读类接口，新增依赖：

```python
def get_effective_user_id(request: Request, view_as_user_id: int | None = None):
    u = request.state.user
    if not view_as_user_id or view_as_user_id == u.id:
        return u.id  # 看自己
    # advisor/admin 才能代理
    if not (u.is_advisor or u.is_admin):
        raise HTTPException(403, "无权查看其他用户")
    # 校验 advisor 只能看 ACTIVE 关联的客户
    if u.is_advisor and not u.is_admin:
        rel = db.query(UserRelation).filter(
            UserRelation.advisor_user_id == u.id,
            UserRelation.client_user_id == view_as_user_id,
            UserRelation.status == "ACTIVE",
        ).first()
        if not rel:
            raise HTTPException(403, "未与该客户建立关联")
    target = db.query(User).filter(User.id == view_as_user_id, User.is_active == True).first()
    if not target: raise HTTPException(404, "用户不存在")
    return target.id
```

读取端点加 `?view_as=<user_id>` 参数，写入端点**不接受** view_as（写只对自己）。

### 2.6 401 vs 403 区分

- 401：未登录或 session 失效 → 前端清 token 跳登录
- 403：已登录但权限不足 → 前端弹 toast「权限不足」**不清 token**

`api.js` response interceptor 已处理 401 自动 reload；新增 403 处理：

```js
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) { /* 清 token + reload */ }
    if (err?.response?.status === 403) {
      // 弹 toast; 不清 token
      window.dispatchEvent(new CustomEvent('portfoliom-403', { detail: err.response.data }))
    }
    return Promise.reject(err)
  }
)
```

---

## Section 3 — 权限矩阵（按端点）

| 端点 | user | advisor | admin |
|------|------|---------|-------|
| `GET /api/auth/me`、`POST /api/auth/login`、`POST /api/auth/logout` | ✓ | ✓ | ✓ |
| `GET /api/auth/relations`、`POST /api/auth/relations`、`POST .../confirm`、`POST .../cancel` | ✓ 自己 | ✓ 自己 | ✓ |
| `GET /api/auth/users`、`POST /api/auth/users`、`PUT /api/auth/users/{id}` | ✗ | ✗ | ✓ |
| `GET /api/holdings*`、`GET /api/penetration/*`、`GET /api/analysis/*`、`GET /api/analyst/*`、`GET /api/watchlist`（仅自己或代理） | ✓ 自己 | ✓ 代理客户 | ✓ 任意 |
| `POST /api/holdings/import`（user_upload）、`POST /api/watchlist` | ✓ | ✗ | ✗ |
| `POST /api/crawl/*`、`POST /api/penetration/calculate`、`POST /api/csi300/recalc`、`POST /api/holdings/fill-prices` | ✗ | ✗ | ✓ |
| `POST /api/scheduler/trigger/*` | ✗ | ✗ | ✓ |
| `POST /api/info/crawl/*` | ✗ | ✗ | ✓ |
| `POST /api/admin/*`、`POST /api/admin/import-source-data`、`POST /api/admin/recalc-aggregation`、`POST /api/admin/fill-prices-tencent`、`POST /api/admin/analyst/ingest` | ✗ | ✗ | ✓ |
| `GET /api/admin/gap-report`、`POST /api/admin/fix-gap/{id}` | ✗ | ✗ | ✓ |
| `GET /api/data-browser/*`（只读浏览） | ✓ | ✓ | ✓ |
| `PUT /api/data-browser/{table}/...` | ✗ | ✗ | ✓ |
| `GET /api/code-map`、`GET /api/strategies`（公开） | ✓ | ✓ | ✓ |
| `POST /api/code-map`、`DELETE /api/code-map/{code_in}/{api_strategy}` | ✗ | ✗ | ✓ |
| `GET /api/calendar/*`、`GET /api/exchange-rates/*` | ✓ | ✓ | ✓ |
| `POST /api/exchange-rates/update` | ✗ | ✗ | ✓ |

---

## Section 4 — 前端：菜单 / 导航 / 登出改造

### 4.1 菜单最终列表

| 菜单 | user | advisor | admin |
|------|------|---------|-------|
| 总览 | ✓ | ✓ | ✓ |
| 分析 | ✓ | ✓ | ✓ |
| 分析师 | ✓ | ✓ | ✓ |
| 交易（placeholder） | ✓ | ✗ | ✗ |
| 关注 | ✓ | ✓ | ✓ |
| 关联 | ✓ | ✓ | ✗ |
| 运维 | ✗ | ✗ | ✓ |
| 数据补足 | ✗ | ✗ | ✓ |
| 数据 | ✗ | ✓ | ✓ |
| API策略 | ✗ | ✗ | ✓ |
| 设置（仅改密） | ✓ | ✓ | ✓ |

### 4.2 App.jsx 改造点

```jsx
// 1. 加 currentUser state
const [currentUser, setCurrentUser] = useState(null)
const [viewAsUser, setViewAsUser] = useState(null)  // advisor/admin 切换的客户

useEffect(() => {
  if (sessionToken) {
    api.getAuthMe().then(r => setCurrentUser(r.user))
  }
}, [sessionToken])

// 2. TABS 增加 visibility 字段
const TABS = [
  { id: 'overview',   label: '总览',     visibility: ['user','advisor','admin'] },
  { id: 'analysis',   label: '分析',     visibility: ['user','advisor','admin'] },
  { id: 'analyst',    label: '分析师',   visibility: ['user','advisor','admin'] },
  { id: 'trading',    label: '交易',     visibility: ['user'] },
  { id: 'watch',      label: '关注',     visibility: ['user','advisor','admin'] },
  { id: 'relation',   label: '关联',     visibility: ['user','advisor'] },
  { id: 'ops',        label: '运维',     visibility: ['admin'] },
  { id: 'dataGap',    label: '数据补足', visibility: ['admin'] },
  { id: 'data',       label: '数据',     visibility: ['advisor','admin'] },
  { id: 'strategies', label: 'API策略',  visibility: ['admin'] },
  { id: 'settings',   label: '设置',     visibility: ['user','advisor','admin'] },
]

const visibleTabs = TABS.filter(t => {
  if (!currentUser) return false
  if (currentUser.is_admin) return true  // admin 看全部
  if (currentUser.is_advisor) return t.visibility.includes('advisor')
  return t.visibility.includes('user')
})

// 3. 视图代理状态条
{viewAsUser && (
  <div className="view-as-banner">
    正在查看: <strong>{viewAsUser.display_name}</strong>
    <button onClick={() => setViewAsUser(null)}>切回自己</button>
  </div>
)}

// 4. 账户切换下拉
{(currentUser?.is_advisor || currentUser?.is_admin) && (
  <select onChange={e => setViewAsUser(allUsers.find(u => u.id === +e.target.value))}>
    <option value="">切换查看...</option>
    {availableUsers.map(u => <option key={u.id} value={u.id}>{u.display_name}</option>)}
  </select>
)}
```

### 4.3 onLogout 修复

```jsx
const onLogout = async () => {
  try { await api.logout() } catch {}
  localStorage.removeItem('portfoliom_session')
  setSessionToken('')
  setCurrentUser(null)
  setViewAsUser(null)
  window.location.reload()
}
```

### 4.4 所有持仓 / 分析 / 分析师读取端点携带 view_as

```js
const viewAsParam = viewAsUser?.id ? { view_as: viewAsUser.id } : {}
export const getHoldingsSummary = () =>
  api.get('/holdings/summary', { params: viewAsParam }).then(r => r.data)
// ... 所有 read 端点同样 ...
```

### 4.5 AuthGate UI 改造

- 登录表单增加 `username` 输入（替代原 password 单字段）
- 启动时若已登录但未拿到 currentUser → 调 `/api/auth/me` 重新获取
- 错误处理：401 错误密码 / 403 用户被禁用 / 429 限流

### 4.6 TradingPanel 占位

```jsx
export default function TradingPanel() {
  return (
    <div className="empty">
      <h3>交易维护</h3>
      <p>本功能将在下一版本上线。请在外部交易终端完成交易记录后，使用「导入」功能上传 Excel。</p>
      <button onClick={() => alert('导入功能即将上线')}>导入持仓</button>
    </div>
  )
}
```

---

## Section 5 — 持仓 / 关注 改造细节

### 5.1 `Holding` 写入路径（importer.py）

```python
def import_excel(db: Session, file_path: str, user_id: int, import_source: str = "user_upload"):
    # 1. 仅删自己
    db.query(Holding).filter(Holding.user_id == user_id).delete()
    db.commit()
    # 2. 读 Excel → 行
    df = pd.read_excel(file_path)
    rows = []
    for _, r in df.iterrows():
        rows.append(Holding(
            user_id=user_id, security_code=r['code'], ...
        ))
    db.bulk_save_objects(rows); db.commit()
    # 3. 写 holding_import_log
    db.add(HoldingImportLog(user_id=user_id, import_source=import_source,
                            file_name=os.path.basename(file_path), row_count=len(rows)))
    db.commit()
```

### 5.2 `Watchlist` PK 迁移

旧 PK = `code`；新 PK = `(user_id, code)`。`init_db()` 内做迁移：

```python
# 把所有现有 watchlist 行 user_id 设为 1（admin 账户，init 时已建）
db.execute("UPDATE watchlist SET user_id = 1 WHERE user_id IS NULL")
db.execute("ALTER TABLE watchlist DROP CONSTRAINT IF EXISTS watchlist_pkey")
db.execute("ALTER TABLE watchlist ADD PRIMARY KEY (user_id, code)")
```

### 5.3 顾问加关注端点

```python
@app.post("/api/watchlist")
def add_watchlist(body: WatchlistAddIn, request: Request, db: Session = Depends(get_db)):
    u = require_user()(request)  # 任意角色
    # 决定 owner_user_id
    if u.is_admin or u.is_advisor:
        # 必须在 view_as 模式下加关注到客户
        if not request.state.view_as_user_id:
            raise HTTPException(400, "顾问/管理员加关注时必须先切换到客户视图")
        owner_id = request.state.view_as_user_id
    else:
        owner_id = u.id
    # 校验：advisor 必须是该客户的 ACTIVE 关联
    if u.is_advisor:
        rel = db.query(UserRelation).filter(
            UserRelation.advisor_user_id == u.id,
            UserRelation.client_user_id == owner_id,
            UserRelation.status == "ACTIVE",
        ).first()
        if not rel and not u.is_admin:
            raise HTTPException(403, "未与该客户建立关联")
    db.add(Watchlist(user_id=owner_id, code=body.code, ...))
    db.commit()
    return {"status": "ok", "owner_user_id": owner_id}
```

### 5.4 scheduler 改造

`services/scheduler.py` 中遍历 `Holding` 的 5 个 job：

```python
def _all_user_holdings_query(db: Session):
    """返回所有 user 的 holdings（用于 scheduler 等公共 job）"""
    return db.query(Holding)

def _user_holdings_query(db: Session, user_id: int):
    return db.query(Holding).filter(Holding.user_id == user_id)
```

改写：
- `realtime_prices`：按 user 遍历
- `fill_snapshot_gaps_smart`：按 user 遍历
- `financial_fundamentals`：按 user 遍历（合并所有）
- `backfill_gaps`：按 user 遍历
- `info_stock_news` / `info_announcements_research`：按 user 计算穿透后股票池后合并去重

---

## Section 6 — 数据补足页面

### 6.1 新增 scheduler job `detect_data_gaps`

每天 06:50 跑（其他 job 是 06:00/06:05/06:10/06:15/06:20/06:25）。

```python
def job_detect_data_gaps():
    """扫 3 类缺口，写入 data_gap_report"""
    db = SessionLocal()
    try:
        gaps = []
        # 1. stock_report_gap：每个 user 持仓的「下钻后 ≥ 0.8%」股票，缺报告的
        users = db.query(User).filter(User.is_active == True).all()
        for u in users:
            holdings = db.query(Holding).filter(Holding.user_id == u.id).all()
            # 调 PenetrationEngine.calculate → full_holding_snapshot 含 is_drill=true
            # 过滤 est_market_value_cny / 组合总市值 ≥ 0.008
            total_est = db.query(func.sum(Holding.amount_cny)).filter(
                Holding.user_id == u.id).scalar() or 0
            for h in holdings:
                # ... 调 penetration_v2 计算该 holding 的下钻成分股 ...
                for stock_code, est_cny in drilled_stocks_for_h.items():
                    weight = est_cny / total_est if total_est > 0 else 0
                    if weight < 0.008: continue
                    # 查报告是否存在
                    has = db.query(AnalystCompanyReport).filter(
                        AnalystCompanyReport.stock_code == stock_code).first()
                    if not has:
                        gaps.append(DataGapReport(
                            user_id=u.id, gap_type="stock_report",
                            stock_code=stock_code,
                            description=f"{h.security_name} 下钻后占比 {weight:.2%}，无报告"
                        ))

        # 2. index_constituent_gap：当前是 N 月，但缺 N-1 月底快照
        today = date.today()
        last_month_end = ...  # 上月最后一天（交易日内）
        for fmap in db.query(FundIndexMap).filter(FundIndexMap.index_code.isnot(None)).distinct(FundIndexMap.index_code):
            has_snapshot = db.query(IndexConstituentSnapshot).filter(
                IndexConstituentSnapshot.index_code == fmap.index_code,
                IndexConstituentSnapshot.as_of_date == last_month_end
            ).first()
            if not has_snapshot:
                gaps.append(DataGapReport(
                    gap_type="index_constituent",
                    index_code=fmap.index_code,
                    as_of_date=last_month_end,
                    description=f"{fmap.index_name} 缺 {last_month_end} 月底快照"
                ))

        # 3. index_classification_gap
        for fmap in db.query(FundIndexMap).filter(FundIndexMap.index_code.isnot(None)).distinct(FundIndexMap.index_code):
            has_cls = db.query(IndexClassification).filter(
                IndexClassification.index_code == fmap.index_code).first()
            if not has_cls:
                gaps.append(DataGapReport(
                    gap_type="index_classification",
                    index_code=fmap.index_code,
                    description=f"{fmap.index_name or fmap.index_code} 无分类"
                ))

        # 去重：同 (user_id, stock_code, gap_type) 只保留最新 OPEN
        # 实际用 INSERT ... ON CONFLICT DO NOTHING（PostgreSQL）或者查重后再插
        ...
        # 标记旧的 OPEN 为已解决（如果现在不缺了）
        ...
    finally:
        db.close()
```

### 6.2 admin 端点

```python
@app.get("/api/admin/gap-report")
def list_gap_reports(gap_type: str | None = None, status: str = "OPEN",
                     db: Session = Depends(get_db), user = Depends(require_admin())):
    q = db.query(DataGapReport)
    if gap_type: q = q.filter(DataGapReport.gap_type == gap_type)
    if status: q = q.filter(DataGapReport.status == status)
    return {"items": [...], "counts": {"OPEN": ..., "FIXED": ...}}

@app.post("/api/admin/fix-gap/{gap_id}")
def fix_gap(gap_id: int, db: Session = Depends(get_db), user = Depends(require_admin())):
    """按 gap_type 触发对应修复逻辑"""
    gap = db.query(DataGapReport).filter(DataGapReport.id == gap_id).first()
    if gap.gap_type == "stock_report":
        # 触发 analyst ingest 或手动标 FIXED
        ...
    elif gap.gap_type == "index_constituent":
        # 调 crawl_constituents(gap.index_code, gap.as_of_date)
        ...
    elif gap.gap_type == "index_classification":
        # 手动录入或调分类器
        ...
    gap.status = "FIXED"; gap.resolved_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}
```

### 6.3 前端 `DataGapPanel.jsx`

3 个 tab：

```
[个股报告] [指数构成] [指数分类]

Tab "个股报告":
- 表格列: 客户 | 股票代码 | 当前占比 | 描述 | 检测时间 | 操作
- 顶部显示「OPEN 总数」徽章
- 「立即修复」按钮：调 /api/admin/fix-gap/{id}

Tab "指数构成":
- 表格列: 指数代码 | 指数名称 | 缺失快照月份 | 描述 | 操作

Tab "指数分类":
- 表格列: 指数代码 | 指数名称 | 当前分类 | 操作
- 「编辑分类」按钮：弹窗选 category/theme 后 POST /api/admin/index-classification
```

挂在 admin 「数据补足」菜单下。

---

## Section 7 — 顾问-客户双向关联

### 7.1 端点

```python
@app.get("/api/auth/relations")
def list_relations(db = Depends(get_db), user = Depends(require_user())):
    """返回我作为 advisor 的所有关系 + 作为 client 的所有关系"""
    as_advisor = db.query(UserRelation, User.username, User.display_name).join(
        User, User.id == UserRelation.client_user_id
    ).filter(UserRelation.advisor_user_id == user.id).all()
    as_client = db.query(UserRelation, User.username, User.display_name).join(
        User, User.id == UserRelation.advisor_user_id
    ).filter(UserRelation.client_user_id == user.id).all()
    return {
        "as_advisor": [...],
        "as_client": [...],
    }

@app.post("/api/auth/relations")
def create_relation(body: RelationCreateIn, db = Depends(get_db), user = Depends(require_user())):
    """body: { advisor_username: str, client_username: str }"""
    if user.is_advisor:
        advisor_id = user.id
        client = db.query(User).filter(User.username == body.client_username).first()
        if not client: raise HTTPException(404, "用户不存在")
        client_id = client.id
    elif user.is_admin or not user.is_advisor:
        # 普通用户
        client_id = user.id
        advisor = db.query(User).filter(User.username == body.advisor_username).first()
        if not advisor or not advisor.is_advisor:
            raise HTTPException(404, "顾问不存在")
        advisor_id = advisor.id
    else:
        raise HTTPException(403, "仅顾问或用户可发起关联")
    # 检查是否已存在
    existing = db.query(UserRelation).filter(
        UserRelation.advisor_user_id == advisor_id,
        UserRelation.client_user_id == client_id
    ).first()
    if existing and existing.status != "CANCELLED":
        return {"status": "exists", "relation_id": existing.id}
    rel = UserRelation(
        advisor_user_id=advisor_id, client_user_id=client_id,
        status="PENDING", initiator_user_id=user.id
    )
    db.add(rel); db.commit()
    return {"status": "created", "relation_id": rel.id}

@app.post("/api/auth/relations/{rel_id}/confirm")
def confirm_relation(rel_id: int, db = Depends(get_db), user = Depends(require_user())):
    rel = db.query(UserRelation).filter(UserRelation.id == rel_id).first()
    if not rel: raise HTTPException(404)
    if user.id not in (rel.advisor_user_id, rel.client_user_id):
        raise HTTPException(403, "无权操作")
    if rel.initiator_user_id == user.id:
        raise HTTPException(400, "不能确认自己发起的关联，需对方确认")
    rel.status = "ACTIVE"; rel.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "active"}

@app.post("/api/auth/relations/{rel_id}/cancel")
def cancel_relation(rel_id: int, db = Depends(get_db), user = Depends(require_user())):
    rel = db.query(UserRelation).filter(UserRelation.id == rel_id).first()
    if not rel: raise HTTPException(404)
    if user.id not in (rel.advisor_user_id, rel.client_user_id) and not user.is_admin:
        raise HTTPException(403, "无权操作")
    rel.status = "CANCELLED"; rel.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "cancelled"}
```

### 7.2 前端 `RelationPanel.jsx`

```
[作为顾问]  [作为客户]

作为顾问视角（is_advisor or is_admin）:
- 「邀请新客户」：下拉选 user，POST /api/auth/relations {client_username}
- 关系列表：状态 tab (PENDING / ACTIVE / CANCELLED)
  - PENDING：显示「确认」/「取消」按钮（仅对方发起时显示确认）
  - ACTIVE：显示「解除关联」按钮

作为客户视角（!is_advisor and !is_admin）:
- 「邀请顾问」：下拉选 advisor，POST
- 关系列表同上
```

---

## Section 8 — 验证策略

### 8.1 本地启动

```bash
cd D:/claude_code_project/PortfolioM
cd backend
# 1. 安装新依赖
echo "bcrypt==4.1.2" >> requirements.txt
pip install -r requirements.txt
# 2. 跑数据迁移：删 db 重建（本地）；保留 db 时 init_db 自动迁移
rm -rf data/portfolio.db  # SQLite 时
# 3. seed 用户
python scripts/seed_users.py
# 4. 启动
uvicorn main:app --reload --port 8001

cd frontend
npm run dev
```

### 8.2 测试剧本（必须 9 步全过）

| # | 步骤 | 期望 |
|---|------|------|
| 1 | admin 登录（admin/admin123） | 看到 9 个菜单：总览/分析/分析师/关注/运维/数据补足/数据/API策略/设置 |
| 2 | user_a 登录（user_a/user123） | 看到 7 个菜单：总览/分析/分析师/交易/关注/关联/设置；无 数据/API策略/运维/数据补足 |
| 3 | advisor_x 登录（advisor_x/advisor123） | 看到 7 个菜单：总览/分析/分析师/关注/关联/数据/设置；无 交易/运维/API策略/数据补足 |
| 4 | advisor_x 「切换到 user_a」 | sidebar 顶部出现「正在查看：王先生」banner；总览/分析页是 user_a 的数据 |
| 5 | advisor_x 在 user_a 视图下 `POST /api/watchlist {code: NVDA}` | 成功；`watchlist` 表新增 `(user_id=user_a.id, code=NVDA)` |
| 6 | user_a 发起关联 advisor_x（PENDING）；advisor_x 确认 → ACTIVE | user_a 「关联」tab 看到 ACTIVE；advisor_x 「关联」tab 看到 ACTIVE |
| 7 | 触发 scheduler `detect_data_gaps` job | `data_gap_report` 插入 3 类缺口的行 |
| 8 | admin 触发 fix-gap (index_classification 缺一个) | 弹窗录入 category；data_gap_report.status = FIXED |
| 9 | 退出 admin | localStorage token 清空；AccessSession 表该行被删除；reload 跳登录页 |

### 8.3 回归测试

- 现有 holdings 已被 admin 账户接管；admin 登录后总览页面正常
- 现有 watchlist 同上
- penetration / aggregation 缓存带 user_id 维度重算后正常（首次进入 user/admin 时会重算）
- 旧 `APP_PASSWORD` 仍然有效（如设置过）：admin 账户未存在时降级到旧的单密码登录（保持向后兼容过渡）

### 8.4 自动化测试

`backend/tests/` 新增：
- `test_auth.py`：登录失败 / 401 / 403
- `test_user_relations.py`：PENDING → ACTIVE → CANCELLED 流程
- `test_data_isolation.py`：user_a 看不到 user_b 的 holdings（即使猜对 URL）
- `test_view_as.py`：advisor 切换到客户 A 视图时 read 端点返回 A 的数据，写入端点仍然只对自身
- `test_data_gap_detect.py`：scheduler job 跑完后 data_gap_report 行数正确

---

## 关键文件清单

### 修改
- `backend/models.py` — 加 5 张表 + 改 4 张表
- `backend/database.py` — `_MIGRATIONS` 加 3 条 ALTER；启动检测 + 自动 seed admin
- `backend/main.py` — 改 `/api/auth/login`、加 `/api/auth/me`、`/api/auth/users`、`/api/auth/relations*`、`/api/admin/gap-report`、`/api/admin/fix-gap/{id}`；80+ 路由改 `Depends(require_*)`
- `backend/services/importer.py` — `import_excel(db, file, user_id, import_source)`
- `backend/services/penetration.py` + `penetration_v2.py` — 持仓查询带 user_id
- `backend/services/scheduler.py` — 11 个 job 中 5 个改按 user 遍历；新增 `job_detect_data_gaps`
- `backend/services/analyst_service.py` — 「核心公司」按 user 持仓动态计算；不读 `analyst_company_report` 静态列表
- `backend/services/info_service.py` — 资讯抓取按 user 穿透股票池合并去重
- `backend/services/drillable_funds.py` — 不变
- `backend/requirements.txt` — + bcrypt
- `backend/scripts/seed_users.py` — **新**
- `backend/scripts/check_code_map_coverage.py` — 已存在；保持

### 新增
- `backend/scripts/migrate_to_multi_user.py` — 一次性迁移脚本（如需手动控制）
- `frontend/src/components/RelationPanel.jsx`
- `frontend/src/components/DataGapPanel.jsx`
- `frontend/src/components/OpsPanel.jsx` — 容纳原 TradingPanel 的 4 个运维按钮 + SettingsPanel「数据管理」
- `frontend/src/components/TradingPanel.jsx` — 改写为 placeholder
- `frontend/src/components/AuthGate.jsx` — 增加 username 输入
- `frontend/src/api.js` — 加 `getAuthMe` / `getUsers` / `listRelations` / `createRelation` 等

### 不改
- 所有 crawlers/*（保持按全表 / 按 user 都不影响）
- 所有资讯类表（共享）
- `analyst_company_report` / `analyst_industry_chain*`（共享不拆）
- `cloud/` 相关：本次升级仅本地，云端不动

---

## 范围外（本次不做）

- **不部署云端**：用户明确要求本地测试通过后再单独部署
- **不建 transactions 表**：交易维护是下期，本次 TradingPanel 留 placeholder
- **不改 Zeabur 部署流程**
- **不做 admin 双因子认证**
- **不做用户配额 / 资源限制**
- **不做审计日志表**（admin 操作记录暂不入库）
- **不做密码找回流程**（admin 手动重置）

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| `Holding.user_id` 列加 NOT NULL 时旧数据怎么办 | `init_db` 内 ALTER 显式 `DEFAULT 1`；先 UPDATE 现有行；再加 NOT NULL |
| Watchlist PK 改复合时旧 PK 冲突 | `init_db` 检测冲突行 → 合并 user_id=admin.id；保留最早 added_at |
| admin 视图代理时误写客户数据 | 写入端点全部 `Depends(require_user())` + 忽略 `view_as` 参数；只读端点带 `view_as` |
| scheduler 11 个 job 中有的不能简单按 user 遍历（如 `info_global_news` 是全局的） | 区分"按 user" 和"全局" 两类；保持全局 job 不动 |
| bcrypt 安装失败 | 备选：保持 SHA-256 + pepper，spec 标为「推荐但不强求」 |
| 0.8% 阈值计算需重算 `full_holding_snapshot` | 仅在 `detect_data_gaps` job 内**临时**调 penetration_v2.calculate()；不写回快照表 |
| analyst 报告共享导致 user A 看到 user B 写的报告 | spec 明确：分析师是研究输出，全员共享；user 看到的是「这份报告存在」，不是「我拥有」 |
| 顾问-客户 PENDING 时一方能看到对方的持仓吗 | **不能**：仅 ACTIVE 状态可代理；PENDING 期间双方各自只能看自己 |

---

## 相关 memory

- [[teck-dashboard-cloud-deploy]] — 云端部署上下文（本次不动）
- [[portfoliom-deploy-rule]] — 部署规则（本次不部署）
- [[portfolio-data-flow]] — 持仓数据流（importer / penetration / snapshot）
- [[cloud-fastapi-pending-rollback]] — 注意事项