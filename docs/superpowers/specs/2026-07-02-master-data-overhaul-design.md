# 公共数据主数据重构设计 — Spec-1

> **日期**: 2026-07-02
> **范围**: 公共数据主数据（股票/基金/指数）三表拆分 + 分类维度独立管理 + 指数轮询 + 基金-指数双向选择
> **状态**: 设计已确认，待写实施计划
> **前置**: [2026-06-24-admin-master-data-design.md](./2026-06-24-admin-master-data-design.md)（创建 SecurityMaster 的奠基 spec）
> **后续 Spec**: Spec-2 — 全市场 A 股/港股/基金/指数名称代码一次性拉取 + 增量轮询

---

## 1. 背景与目标

### 1.1 问题

当前 `SecurityMaster` 表混合管理 4 种实体（股票/ETF场内基金/场外基金/QDII等），并在 `asset_type`、`type2` 等字段上承担了过多职责：

1. **数据模型过载**：股票与基金共表，指数根本没有专属主数据表（仅在 `FundIndexMap.index_code` 字段中以字符串形式出现）
2. **分类维度是自由文本**：`type2` 字段值如 `"emerging"`/`"红利"` 是用户手输字符串，无字典、无约束、无中文标签统一
3. **指数数据无来源**：今天需要指数代码+名称时，只能从持仓的 `FundIndexMap` 提取，缺指数无任何来源
4. **基金-指数关联 UX 差**：`FundIndexMapTab` 是单表格 + 文本输入 `index_code`，无 typeahead，无校验

### 1.2 目标

1. **3 张主表拆分**：`StockMaster` / `FundMaster` / `IndexMaster`，每张表只管一类证券
2. **2 张分类表**：`Classification`（维度字典）+ `ClassificationAssign`（多对多关联），一个实体可同时有「类型」+「主题」两维度
3. **指数轮询**：akshare 增量拉取 A 股指数（每天 21:23），写入 `IndexMaster`；QQQ 单独手动入库
4. **双向选择**：基金-指数新增映射时，从对应主表 typeahead 模糊搜索选择，不再 key-in 文本
5. **现有 SecurityMaster**：迁移完成后改名为 `security_master_legacy`，冻结只读，新代码绝不读

### 1.3 不在范围内（Out of Scope）

- **Spec-2 留待**：全市场 A 股/港股/基金/指数的名称+代码一次性拉取 + 增量轮询（这是更大的探索性项目）
- **港股/美股指数轮询**：本轮只拉 A 股指数，QQQ 单独手动入库
- **折溢价率**（premium_discount）：这是行情数据，不属于主数据；后续可作行情字段扩展
- **BondMaster 单独建表**：本轮 bond 暂归 StockMaster 留作 future work
- **新 Admin 角色/权限模型**：沿用现有 `require_admin` 模式

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                     Frontend (React)                         │
│  MasterDataPanel                                             │
│   ├─ Tab 1: 股票主数据  (StockMasterTab)                     │
│   ├─ Tab 2: 基金主数据  (FundMasterTab)                      │
│   ├─ Tab 3: 指数主数据  (IndexMasterTab)                     │
│   └─ Tab 4: 分类维度管理 (ClassificationTab)                 │
│                                                              │
│  FundIndexMapTab（在 IndexDrillBaseTab 上方）                  │
│   - 表格保留 + 表格上方「+ 新增映射」按钮（typeahead 弹窗）  │
└─────────────────────────────────────────────────────────────┘
                          │ axios
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                     Backend (FastAPI)                        │
│  /api/admin/stock-master       CRUD + 同步                    │
│  /api/admin/fund-master        CRUD + 同步                    │
│  /api/admin/index-master       CRUD + 手动刷新                │
│  /api/admin/classification     CRUD (维度+分类值)             │
│  /api/admin/fund-index-map     保留旧端点 + 新增 selective     │
│  /api/admin/fund-master/lookup typeahead 搜索                 │
│  /api/admin/index-master/lookup typeahead 搜索                │
│                                                              │
│  Services:                                                   │
│   - stock_master_service        (新)                          │
│   - fund_master_service         (新)                          │
│   - index_master_service        (新)                          │
│   - classification_service      (新)                          │
│   - akshare_index_poller        (新)                          │
│   - security_master_legacy_service (新,只读,迁移期回退用)    │
│                                                              │
│  Scheduler:                                                  │
│   - job_poll_index_master       每天 21:23 Asia/Shanghai     │
│   - job_alert_poll_failures     连续 3 次失败触发 (复用现有)  │
└─────────────────────────────────────────────────────────────┘
                          │ SQLAlchemy
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                     PostgreSQL                               │
│  新表:                                                       │
│   stock_master    (security_type='stock' 数据迁过来)         │
│   fund_master     (security_type='fund' 数据迁过来)          │
│   index_master    (新;从 FundIndexMap 提取 index_code + 轮询)│
│   classification  (新;两维度: asset_type + theme)            │
│   classification_assign  (新;多对多)                         │
│                                                              │
│  改名:                                                       │
│   security_master → security_master_legacy (只读,新代码不读) │
│                                                              │
│  保留:                                                       │
│   fund_index_map  (保留,增强为双向选择)                      │
│   fund_drill_snapshot, index_constituent (不变)              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 数据模型

### 3.1 新建 StockMaster 表

```sql
CREATE TABLE stock_master (
  stock_code      VARCHAR(20)  PRIMARY KEY,   -- e.g. "600519.SH"
  stock_name      VARCHAR(100) NOT NULL,
  exchange        VARCHAR(10),                -- "SH" / "SZ"
  currency        VARCHAR(10)  DEFAULT 'CNY',
  asset_type      VARCHAR(20)  NOT NULL,      -- "a_share_equity" / "us_stock" / "bond" / "gold" / "commodity"
  is_listed       BOOLEAN      DEFAULT TRUE,
  is_drillable    BOOLEAN      DEFAULT FALSE, -- 股票恒 false
  note            VARCHAR(200),
  created_at      TIMESTAMP    DEFAULT NOW(),
  updated_at      TIMESTAMP    DEFAULT NOW() ON UPDATE NOW(),
  updated_by      INTEGER
);
```

### 3.2 新建 FundMaster 表

```sql
CREATE TABLE fund_master (
  fund_code        VARCHAR(20)  PRIMARY KEY,  -- "510300.SH" / "161725.OF"
  fund_name        VARCHAR(100) NOT NULL,
  fund_type        VARCHAR(20)  NOT NULL,     -- "etf" / "otc"
  currency         VARCHAR(10)  DEFAULT 'CNY',
  asset_type       VARCHAR(20)  NOT NULL,     -- 包含 "a_share_etf" / "qdii_equity" / "qdii_bond" / ...
  benchmark_formula VARCHAR(500),
  is_drillable     BOOLEAN      DEFAULT FALSE,
  note             VARCHAR(200),
  created_at       TIMESTAMP    DEFAULT NOW(),
  updated_at       TIMESTAMP    DEFAULT NOW() ON UPDATE NOW(),
  updated_by       INTEGER
);
```

> **注 1**: `fund_master.asset_type` 是 `security_master.asset_type` 直接迁移，含义不变（保持 `a_share_etf`/`qdii_equity`/`qdii_bond` 等枚举值）。分类通过 `classification_assign` 表达。
>
> **注 2**: `premium_discount`（折溢价率）**不放入**本表 — 这是 ETF 的行情数据，不属于主数据。后续可作 `fund_quote` 行情表字段扩展。

### 3.3 新建 IndexMaster 表

```sql
CREATE TABLE index_master (
  index_code      VARCHAR(20)  PRIMARY KEY,   -- "000300.SH" / "HSI" / "QQQ"
  index_name      VARCHAR(100) NOT NULL,
  exchange        VARCHAR(20),                -- "SH" / "SZ" / "HK" / "US" / ...
  currency        VARCHAR(10)  DEFAULT 'CNY',
  category        VARCHAR(50),                -- "宽基" / "行业" / "主题" / "策略" (4 类)
  constituent_count INTEGER,
  source          VARCHAR(40)  DEFAULT 'akshare',
  is_active       BOOLEAN      DEFAULT TRUE,  -- 标记下架: 上次见到但本次未拉到 → FALSE
  first_pulled_at TIMESTAMP,                  -- 首次入库时间
  last_pulled_at  TIMESTAMP,                  -- 最近一次轮询入库
  last_verified_at TIMESTAMP,                 -- 最近一次 name 验证一致
  created_at      TIMESTAMP    DEFAULT NOW(),
  updated_at      TIMESTAMP    DEFAULT NOW() ON UPDATE NOW(),
  updated_by      INTEGER
);
```

> **注 1**: QQQ（纳斯达克 100）**手动入库**（admin "新增指数" 按钮），不走 akshare 轮询。
>
> **注 2**: `category` 4 类（宽基/行业/主题/策略）由 admin 手动维护，akshare 拉到的初始值为 `NULL`，人工填。

### 3.4 新建 Classification 表（维度字典）

```sql
CREATE TABLE classification (
  id              SERIAL       PRIMARY KEY,
  dimension       VARCHAR(20)  NOT NULL,      -- "asset_type" 或 "theme"
  code            VARCHAR(50)  NOT NULL,      -- "a_share_etf" / "dividend"
  display_label   VARCHAR(100) NOT NULL,      -- "A股ETF" / "红利"
  sort_order      INTEGER      DEFAULT 0,
  is_active       BOOLEAN      DEFAULT TRUE,
  created_at      TIMESTAMP    DEFAULT NOW(),
  UNIQUE (dimension, code)
);
```

> **注**: `display_label` 一律中文（"a_share_etf" → "A股ETF"），`code` 保留原值便于代码引用。

### 3.5 新建 ClassificationAssign 表（多对多关联）

```sql
CREATE TABLE classification_assign (
  id                BIGSERIAL    PRIMARY KEY,
  entity_type       VARCHAR(20)  NOT NULL,    -- "stock" / "fund" / "index"
  entity_code       VARCHAR(20)  NOT NULL,    -- FK 弱引用 (entity_type, entity_code)
  classification_id INTEGER      NOT NULL REFERENCES classification(id) ON DELETE CASCADE,
  created_at        TIMESTAMP    DEFAULT NOW(),
  UNIQUE (entity_type, entity_code, classification_id),
  INDEX (entity_type, entity_code),
  INDEX (classification_id)
);
```

> **注**: 一个实体可同时有「asset_type」+「theme」两维度的多条 assign 记录，互不冲突。后续可加新维度（如「策略」「地区」）不需改表结构。

### 3.6 SecurityMaster 改名

```sql
ALTER TABLE security_master RENAME TO security_master_legacy;
COMMENT ON TABLE security_master_legacy IS
  'DEPRECATED 2026-07-02: 数据已迁到 stock_master + fund_master;本表冻结只读,禁止新写入';
```

新代码不读此表；前端不显示此表；只允许迁移期/审计期查询。

---

## 4. 数据迁移

### 4.1 迁移脚本

```python
# backend/scripts/migrate_split_security_master.py
# 一次性脚本,可幂等重跑;带 dry-run 模式

def migrate_security_master_legacy_to_3_tables(db, dry_run: bool = True):
    """
    流程:
      1) 读 security_master_legacy
      2) 分流（按 security_type + asset_type + code 后缀鉴别）:
         - 'stock' → stock_master
         - 'fund'  → fund_master
         - 'bond' + ('qdii_bond' asset_type OR code ends with .OF) → fund_master
         - 'bond' 其他                                          → stock_master
      3) 提取 index_code 集合 → 写 index_master (source='manual_legacy')
      4) 提取 type2 集合 → 写 classification(dimension='theme') 字典 (中文标签)
      5) 写 classification_assign (entity_type + entity_code + classification_id)
      6) 同步 asset_type → classification(dimension='asset_type') 字典
      7) 输出 counts / samples / unmapped warnings
    """
```

### 4.2 鉴别规则详解

| security_type | asset_type | code 后缀 | 目标表 | 备注 |
|---|---|---|---|---|
| `stock` | any | any | `stock_master` | 包括 A 股/港股/美股股票 |
| `fund` | any | any | `fund_master` | 包括 ETF/OF/QDII |
| `bond` | `qdii_bond` | any | `fund_master` | QDII 债基是基金 |
| `bond` | any | `.OF` | `fund_master` | 场外债基（防误归类） |
| `bond` | `bond` | `.SH`/`.SZ`/`.HK`/无后缀 | `stock_master` | 实际债券证券 |

迁移脚本在 dry-run 阶段把所有 `security_type='bond'` 的样本打出来供人工 review。

### 4.3 type2 英文 → 中文映射

```python
_TYPE2_CODE_TO_LABEL = {
    "emerging": "新兴产业",
    "dividend": "红利",
    "gold":     "黄金",
    # 后续如发现新值,加在这里
}

def _normalize_type2_label(raw: str | None) -> tuple[str, str] | None:
    """
    返回 (code, display_label) 或 None。
    未知值: code = 原值, label = 原值 (人工后续编辑)。
    """
    if not raw:
        return None
    code = raw.lower()
    label = _TYPE2_CODE_TO_LABEL.get(code, raw)
    return (code, label)
```

### 4.4 幂等性

- 所有 INSERT 用 `INSERT ... ON CONFLICT DO NOTHING`（PostgreSQL 语法）
- 重跑前先清空 staging 临时表（不污染正式表）
- 输出 `unmapped warnings` 让 admin 知道哪些值需要人工处理

### 4.5 备份与回退

- **跑前备份**：`pg_dump -U portfoliom -d portfoliom -Fc > /path/to/backup.dump`（脚本内自动调用，路径 `~/portfoliom-migration-backups/`）
- **跑中失败**：整个流程包在一个 PG transaction 中，任意步骤失败自动 rollback
- **跑后回退**：
  ```sql
  DROP TABLE stock_master, fund_master, index_master, classification, classification_assign;
  ALTER TABLE security_master_legacy RENAME TO security_master;
  ```

### 4.6 干运行报告 (dry-run 输出示例)

```
== Dry Run Report ==
  security_master_legacy 总数:  142
  → stock_master (type=stock):   98
  → stock_master (type=bond):     2     ← 人工 review: 这 2 条是真正的债券?
  → fund_master  (type=fund):    39
  → fund_master  (type=bond+qdii_bond):  3
  index_master (新增):           27     ← source=manual_legacy
  classification (新增):         14     (asset_type=9 + theme=5)
  classification_assign:        182
  type2 映射:
    emerging  → 新兴产业      (15 records)
    dividend  → 红利          (8 records)
    gold      → 黄金          (3 records)
  警告: 3 条 type2 未知值,需要人工编辑:
    "hybrid"        (1 record)
    "balanced"      (2 records)
== End ==
```

### 4.7 执行命令

```bash
# 1. dry-run (默认,只读)
cd backend && python scripts/migrate_split_security_master.py

# 2. 人工 review 输出

# 3. 真跑 (写入)
cd backend && python scripts/migrate_split_security_master.py --commit

# 4. 验证 (对比 counts)
cd backend && python scripts/migrate_split_security_master.py --verify
```

---

## 5. 指数轮询 (akshare 增量)

### 5.1 轮询服务

```python
# services/akshare_index_poller.py

def poll_index_master(db, dry_run: bool = False) -> dict:
    """
    增量轮询 akshare → 写 index_master。
    
    流程:
      1) ak.stock_zh_index_spot_em()      拉全市场 A 股指数实时快照
      2) ak.index_stock_info(symbol)       拉单指数详情 (constituent_count 等)
      3) 增量逻辑:
         - 新增: index_code 不存在 → INSERT
         - 更新: name / exchange / constituent_count / source 有差异 → UPDATE
         - 跳过: 完全一致 → 跳过
         - 标记 is_active=False: 上次见到但本次未拉到
      4) last_pulled_at = NOW(); last_verified_at = NOW()
    """
```

### 5.2 定时任务

```python
# 在 services/scheduler.py 注册
scheduler.add_job(
    job_poll_index_master,
    'cron',
    hour=21, minute=23,                          # 21:23 (与现有 21:00 价格任务错开)
    id='job_poll_index_master',
    max_instances=1,                             # 防并发
    coalesce=True,                               # 错过的运行合并
    replace_existing=True,
)
```

### 5.3 手动触发

```python
@app.post("/api/admin/index-master/refresh")
def admin_refresh_index_master(db: Session = Depends(get_db)):
    """手动触发指数轮询 (admin 看到失败时一键重跑)"""
    from services.akshare_index_poller import poll_index_master
    result = poll_index_master(db, dry_run=False)
    return result
```

### 5.4 错误处理

```python
try:
    df = ak.stock_zh_index_spot_em()
except Exception as e:
    DataPullTask(
        job_id='job_poll_index_master',
        job_name='指数主数据轮询',
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
        status='FAILED',
        error_message=str(e)[:500],
        triggered_by='scheduler',
    ).save()
    # 第一次失败: log warning
    # 连续 3 次失败: 触发 alert-monitor-beta
    return {"status": "failed", "error": str(e)}
```

### 5.5 数据规模预估

- A 股指数 ~200-300 条
- 每次全量拉取 ~3-5 秒
- 一天一次完全 OK

---

## 6. 基金-指数双向选择

### 6.1 UX 流程

`FundIndexMapTab` 表格保留; 表格上方新增「+ 新增映射」按钮，点击打开全屏 dialog。

```
┌─────────────────────────────────────────────────┐
│  新增基金-指数映射                            [×]│
├─────────────────────────────────────────────────┤
│                                                  │
│  1. 选择基金                                     │
│     🔍 [搜索基金代码或名称......]                 │
│     ┌────────────────────────────────────────┐  │
│     │ 510300.SH  华泰柏瑞沪深300ETF       ☐  │  │
│     │ 510500.SH  南方中证500ETF            ☐  │  │
│     │ 161725.OF  招商中证白酒指数          ☐  │  │
│     │ ... (滚动加载)                          │  │
│     └────────────────────────────────────────┘  │
│                                                  │
│  2. 选择指数                                     │
│     🔍 [搜索指数代码或名称......]                 │
│     ┌────────────────────────────────────────┐  │
│     │ 000300.SH  沪深300                  ☐  │  │
│     │ 000905.SH  中证500                  ☐  │  │
│     │ QQQ        纳斯达克100              ☐  │  │
│     │ ... (滚动加载)                          │  │
│     └────────────────────────────────────────┘  │
│                                                  │
│  3. 业绩比较基准（可选, 文本）                    │
│     [沪深300指数收益率×95% + 银行活期×5%     ]   │
│                                                  │
│  as_of_date: [2026-07-02 ▼]  (默认今天)          │
│                                                  │
│           [取消]      [确认新增]                  │
└─────────────────────────────────────────────────┘
```

### 6.2 后端端点

```python
# 保留旧端点 (兼容旧调用)
@app.get("/api/admin/fund-index-map")
@app.post("/api/admin/fund-index-map")
@app.put("/api/admin/fund-index-map/{fund_code}/{as_of_date}")
@app.delete("/api/admin/fund-index-map/{fund_code}/{as_of_date}")

# 新增: typeahead 搜索
@app.get("/api/admin/fund-master/lookup")
def lookup_funds_for_select(
    q: str = Query("", description="模糊搜索"),
    page: int = 1, page_size: int = 30,
    db: Session = Depends(get_db),
):
    """基金选择器: 按 code/name 模糊搜索 + 分页"""
    qry = db.query(FundMaster)
    if q:
        like = f"%{q}%"
        qry = qry.filter(FundMaster.fund_code.ilike(like) | FundMaster.fund_name.ilike(like))
    total = qry.count()
    items = qry.order_by(FundMaster.fund_code).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [...], "total": total}


@app.get("/api/admin/index-master/lookup")
def lookup_indices_for_select(...):
    """指数选择器: 同上结构,查 index_master"""
    ...

# 新增: 双向选择式新增
@app.post("/api/admin/fund-index-map/selective")
def create_fund_index_mapping_selective(
    body: dict,  # {fund_code, index_code, benchmark_formula?, as_of_date?}
    db: Session = Depends(get_db),
):
    fund = db.query(FundMaster).filter_by(fund_code=body["fund_code"]).first()
    if not fund:
        raise HTTPException(400, f"基金 {body['fund_code']} 不在 fund_master 中")
    idx = db.query(IndexMaster).filter_by(index_code=body["index_code"]).first()
    if not idx:
        raise HTTPException(400, f"指数 {body['index_code']} 不在 index_master 中")
    fm = FundIndexMap(
        fund_code=body["fund_code"],
        fund_name=fund.fund_name,
        index_code=body["index_code"],
        index_name=idx.index_name,
        benchmark_formula=body.get("benchmark_formula"),
        as_of_date=date.fromisoformat(body["as_of_date"]) if body.get("as_of_date") else date.today(),
        source="manual_selective",
    )
    db.add(fm)
    db.commit()
    return {"status": "ok", "fund_code": fm.fund_code}
```

### 6.3 前端改动

- 表格列编辑时,`index_code` 列从 `<input>` 改成 typeahead 下拉
- 新增「+ 新增映射」按钮 + 全屏 dialog
- 弹窗复用项目现有 `.modal-overlay` 样式

### 6.4 关键决策

| 决策 | 理由 |
|---|---|
| **保留旧端点 (text 输入)** | 老的 fund-index-map CRUD 不动;新端点用 `selective` 后缀区分 |
| **lookup 用 ilike 模糊搜索** | IndexMaster 会很大,需要前端 typeahead;分页 30 条 |
| **前端滚动加载** | 一次 30 条,滚到底再拉下一页;避免一次性拉 1000+ |
| **as_of_date 默认今天** | 跟旧端点行为一致 |
| **source='manual_selective'** | 跟 source='manual' 区分;后续审计/统计用 |

---

## 7. 分类维度管理

### 7.1 4 个分类 tab

MasterDataPanel 第 4 个 sub-tab: `ClassificationTab`，分两组管理：

```
┌─────────────────────────────────────────────────┐
│  分类维度管理                                     │
├─────────────────────────────────────────────────┤
│  维度: [资产类型]  [主题]    ← sub-tab 切换        │
│                                                  │
│  +-------------------+ +-------------------+     │
│  | code     | label  | | code     | label  |     │
│  | a_share_ | A股联接| | emerging | 新兴产业|     │
│  | equity   |        | | dividend | 红利    |     │
│  | a_share_ | A股ETF| | gold     | 黄金    |     │
│  | etf      |        | | ...             |     │
│  | ...             | |                   |     │
│  +-------------------+ +-------------------+     │
│                                                  │
│  [ + 新增 ] [编辑] [停用]   ← admin 操作按钮     │
└─────────────────────────────────────────────────┘
```

### 7.2 端点

```python
@app.get("/api/admin/classification")
def list_classifications(
    dimension: str = Query(...),  # "asset_type" 或 "theme"
    db: Session = Depends(get_db),
):
    return db.query(Classification).filter_by(dimension=dimension).order_by(Classification.sort_order).all()

@app.post("/api/admin/classification")
def create_classification(body: dict, db: Session = Depends(get_db)):
    """新增分类值"""
    ...

@app.put("/api/admin/classification/{id}")
def update_classification(id: int, body: dict, db: Session = Depends(get_db)):
    ...

@app.delete("/api/admin/classification/{id}")
def delete_classification(id: int, db: Session = Depends(get_db)):
    """停用 (is_active=False) 而非物理删除,避免历史数据 FK 断裂"""
    ...
```

### 7.3 主表编辑页的"分类"下拉

新增 Stock/Fund/Index 实体时，「分类」字段从下拉中选择（不再 key-in 文本）：

```
┌──────────────────────────────────────┐
│  编辑股票主数据                        │
├──────────────────────────────────────┤
│  代码:    600519.SH  (不可改)          │
│  名称:    贵州茅台                     │
│  交易所:  [SH ▼]                       │
│  币种:    [CNY ▼]                      │
│  资产类型:[A股股票      ▼] ← 维1       │
│  主题:    [红利, 价值   ▼] ← 维2 (多选)│
│  ...                                   │
│            [取消]      [保存]          │
└──────────────────────────────────────┘
```

`asset_type` 选 1 个（单选），`theme` 可选多个（多选）。

---

## 8. 测试策略

### 8.1 分层

| 层 | 工具 | 覆盖什么 |
|---|---|---|
| Unit (Python) | pytest | 迁移脚本 (idempotency / counts / sample validation); akshare poller (mock akshare 返回); classification service (CRUD) |
| Integration (Python) | pytest + TestClient | 每个新端点; auth (admin role required); bond 鉴别逻辑 |
| Frontend (JS) | vitest (待确认项目是否已用) | 表格渲染;下拉选择;弹窗交互 |

### 8.2 必须测试

- 迁移脚本: idempotency（重跑 3 次结果一致）
- 迁移脚本: bond 鉴别（5 类 input 各自正确归类）
- 迁移脚本: type2 已知值映射 + 未知值告警
- akshare poller: mock akshare 返回固定 dataframe,验证 INSERT/UPDATE/skip 三种路径
- akshare poller: is_active=False 标记逻辑
- 双向选择端点: 非法 fund_code / index_code → 400
- 分类端点: dimension+code 唯一约束
- 分类端点: 停用而非物理删除（FK 完整性）

### 8.3 错误处理 (新端点通用)

```python
raise HTTPException(401, "请登录")
raise HTTPException(403, "需要 admin 权限")
raise HTTPException(404, "xxx 不存在")
raise HTTPException(400, "输入不合法: ...")
raise HTTPException(409, "已存在: ...")
```

### 8.4 admin 权限

所有 `/api/admin/*` 端点都要 `Depends(require_admin)`，复用项目现有模式。

---

## 9. Out of Scope (本轮不做)

- 港股/美股指数轮询（除 QQQ 手动入库外）
- 折溢价率（premium_discount）—— 行情字段，后续扩展
- BondMaster 单独建表
- 一次性拉取全市场 A 股/港股/基金/指数名称代码 —— 留给 Spec-2
- 现有 `security_master_legacy` 的物理删除 —— 保留 6 个月后（2026-12-31）再决定
- 前端样式大改 —— 保持现有设计语言

---

## 10. 风险与里程碑

### 10.1 风险

| 风险 | 缓解 |
|---|---|
| 迁移脚本 bug 导致数据丢失 | dry-run → 人工 review → pg_dump 备份 → transaction rollback on error |
| bond 归类错（应是基金，归到股票） | dry-run 报告把所有 `security_type='bond'` 样本打出来人工 review |
| type2 未知值未映射 | 告警 + 留在原表 + 人工编辑 classification 字典 |
| akshare 接口不稳定 | 失败重试 3 次 + 写 data_pull_task + 连续失败告警 |
| 指数下架（akshare 删了某指数） | is_active=False 标记而非物理删除 |
| 新代码误读 security_master_legacy | 改名 + COMMENT + 代码 review 检查 |

### 10.2 实施顺序（建议）

1. **Phase 1 - DB schema + 迁移脚本**：建 5 张新表 + 写迁移脚本 + 跑 dry-run
2. **Phase 2 - 新 CRUD 端点**：3 张主表 + 分类表 CRUD 端点
3. **Phase 3 - 前端 3 个子页面 + 分类管理 sub-tab**
4. **Phase 4 - 基金-指数双向选择端点 + 弹窗**
5. **Phase 5 - akshare 轮询服务 + 定时任务 + 手动触发**
6. **Phase 6 - 改名 + 清理 + 验证**

每个 Phase 完成后做：单元测试 + 集成测试 + 手动 click-through。

---

## 11. 参考

- 前置 spec: [2026-06-24-admin-master-data-design.md](./2026-06-24-admin-master-data-design.md)
- 现有 SecurityMaster: `backend/models.py:60`
- 现有 MasterDataPanel: `frontend/src/components/MasterDataPanel.jsx`
- 现有 FundIndexMapTab: `frontend/src/components/FundIndexMapTab.jsx`
- 现有 scheduler: `backend/services/scheduler.py`
- 项目规则: `rules.md` (禁止 mock 数据,前端不硬编码业务数据)
- memanto 中相关经验: `portfoliom2-pg 备份` 流程 (43.130.62.66, ubuntu)
