# 子项目 3：yfinance 集成 — 非中港市场 PE/PB/PS 自动补足

**日期**：2026-06-24
**状态**：设计确认，待实施
**前置依赖**：子项目 1（管理员数据运维管理重构）、子项目 2（内容上传套件）

## 1. 概述

当前系统对 A 股和港股有完整的财务数据模型（`AShareFinancialSnapshot` / `HKShareFinancialSnapshot`），但非中港市场（US、韩国、日本、欧洲等）的财务数据仅存储在 `StockInfoCache.data_json`（JSON 字段）中，无法被穿透分析使用。

本子项目通过新建 `OverseasShareFinancialSnapshot` 表，将 yfinance 获取的海外市场财务数据结构化存储，并集成到穿透分析链路中，实现 PE/PB/PS 的自动补足。

## 2. 目标

1. **结构化存储**：新建 `OverseasShareFinancialSnapshot` 表，存储所有非中港市场的 PE/PB/PS/dividend_yield/market_cap
2. **yfinance 增强**：补全 PB（priceToBook）和 PS（priceToSalesTrailing12Months）字段获取
3. **自动补足**：scheduler 定时从 yfinance 获取海外持仓财务数据并写入新表
4. **穿透集成**：修改 `resolve_dynamic_metrics_for_stock`，使海外股票的 PE/PB/PS 能参与穿透分析
5. **市场覆盖**：支持所有非 A 股、非港股的海外市场（US、韩国、日本、欧洲等）

## 3. 现状分析

### 3.1 现有 yfinance 使用

- `crawlers/price_data.py` 的 `fetch_yfinance_info(ticker)` 获取 PE、市值、营收、净利润、增长率、股息率、行业
- **缺失**：PB（priceToBook）和 PS（priceToSalesTrailing12Months）未获取
- `scheduler.py` 的 `job_update_financial_fundamentals()` 每日抓取美股持仓财务数据，写入 `StockInfoCache.data_json`

### 3.2 数据缺口

- `aggregation.py` 的 `resolve_dynamic_metrics_for_stock` 只查 `HKShareFinancialSnapshot` 和 `AShareFinancialSnapshot`
- 不查 `StockInfoCache`，因此 US/韩国等海外股票的 PE/PB/PS 无法参与穿透分析
- 即使 yfinance 数据已抓取到 `StockInfoCache`，也无法被使用

### 3.3 海外持仓识别

- `Holding.asset_type` 为 `US_STOCK` / `US_ETF` 的为美股持仓
- `SecurityMaster.market` 为 `US` / `KR` / `JP` / `EU` 等的为海外证券
- 当前只有 US 市场的持仓被 scheduler 抓取，其他海外市场未覆盖

## 4. 数据模型设计

### 4.1 新建 `OverseasShareFinancialSnapshot` 表

```python
class OverseasShareFinancialSnapshot(Base):
    """海外市场（非 A 股、非港股）估值快照。"""
    __tablename__ = "overseas_share_financial_snapshot"
    __table_args__ = (
        UniqueConstraint("as_of_date", "stock_code",
                         name="ux_osfs_asof_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)  # 多用户隔离
    as_of_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(20), nullable=False, index=True)  # yfinance ticker，如 AAPL、005930.KS
    stock_name = Column(String(80))
    market = Column(String(8), nullable=False, index=True)  # US / KR / JP / GB / DE / FR 等
    # 核心估值指标
    pe_ttm = Column(Float)
    pb_mrq = Column(Float)
    ps_ttm = Column(Float)
    dividend_yield = Column(Float)
    market_cap = Column(Float)  # 亿元（yfinance marketCap / 1e8）
    eps_fy1 = Column(Float)    # forwardEPS
    eps_fy2 = Column(Float)    # 暂留空，yfinance 不提供
    # 行业（yfinance 提供，替代申万/中证分级）
    sector = Column(String(60))
    industry = Column(String(80))
    # Dynamic 指标（与 AShare/HKShare 对齐）
    baseline_price = Column(Float)      # as_of_date 当日收盘价
    current_price = Column(Float)       # 上一交易日收盘价
    current_price_date = Column(Date)
    pe_ttm_dynamic = Column(Float)
    pb_mrq_dynamic = Column(Float)
    ps_ttm_dynamic = Column(Float)
    # 元数据
    source = Column(String(40))         # "yfinance"
    created_at = Column(DateTime, default=datetime.utcnow)
```

### 4.2 设计决策

1. **不含申万/中证/战新行业分级**：海外市场不适用中国行业分级标准
2. **sector/industry 字段**：yfinance 提供的 GICS 行业分类，替代中国行业分级
3. **market 字段**：根据 yfinance ticker 后缀推断（见 4.3）
4. **eps_fy2 留空**：yfinance 不提供 FY2 预期 EPS，仅 `eps_fy1`（forwardEPS）有值
5. **dynamic 字段**：与 AShare/HKShare 对齐，支持穿透分析中的动态 PE/PB/PS 计算

### 4.3 market 推断规则

| ticker 后缀 | market | 示例 |
|-------------|--------|------|
| 无后缀 | US | AAPL、MSFT |
| .KS / .KQ | KR | 005930.KS（三星电子） |
| .T | JP | 7203.T（丰田） |
| .L | GB | SHEL.L（壳牌） |
| .DE | DE | SAP.DE（SAP） |
| .PA | FR | MC.PA（LVMH） |
| .HK | HK | 00700.HK — **排除**，走 HKShareFinancialSnapshot |
| .SH / .SZ | CN | 600519.SH — **排除**，走 AShareFinancialSnapshot |
| 其他 | 按后缀取国家代码 | |

## 5. yfinance 增强

### 5.1 增强 `fetch_yfinance_info`

在 `crawlers/price_data.py` 中增强现有函数：

```python
def fetch_yfinance_info(ticker: str) -> dict | None:
    """yfinance 财务信息补充（增强版：含 PB/PS）"""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "code": ticker,
            "name": info.get("shortName", ""),
            "market": _infer_market_from_ticker(ticker),
            "pe_ttm": info.get("trailingPE"),
            "pb_mrq": info.get("priceToBook"),
            "ps_ttm": info.get("priceToSalesTrailing12Months"),
            "market_cap_b": info.get("marketCap", 0) / 1e8,  # 亿
            "revenue_b": info.get("totalRevenue", 0) / 1e8,
            "net_income_b": info.get("netIncomeToCommon", 0) / 1e8,
            "profit_growth": info.get("earningsGrowth"),
            "revenue_growth": info.get("revenueGrowth"),
            "dividend_yield": info.get("dividendYield"),
            "eps_fy1": info.get("forwardEPS"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "source": "yfinance",
        }
    except Exception:
        return None


def _infer_market_from_ticker(ticker: str) -> str:
    """根据 yfinance ticker 后缀推断市场代码。"""
    if "." not in ticker:
        return "US"  # 无后缀默认美股
    suffix = ticker.rsplit(".", 1)[-1].upper()
    market_map = {
        "KS": "KR", "KQ": "KR",  # 韩国
        "T": "JP",               # 日本
        "L": "GB",               # 英国
        "DE": "DE",              # 德国
        "PA": "FR",              # 法国
        "AS": "NL",              # 荷兰
        "MI": "IT",              # 意大利
        "SW": "CH",              # 瑞士
        "AX": "AU",              # 澳大利亚
        "TO": "CA",              # 加拿大
    }
    return market_map.get(suffix, suffix)
```

### 5.2 注意事项

- yfinance `info` 字段可能为空或缺失（部分小盘股），service 层需处理 None 值
- yfinance 限流：3s/req，易 429，scheduler 批量抓取需加延迟
- `priceToBook` 和 `priceToSalesTrailing12Months` 可能返回 None（部分 ETF 无此数据）

## 6. Service 层设计

### 6.1 新建 `backend/services/overseas_financial_service.py`

```python
"""海外市场财务数据 service — yfinance 获取 + upsert。"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from models import OverseasShareFinancialSnapshot

logger = logging.getLogger(__name__)


def upsert_overseas_financial(db: Session, data: dict) -> dict:
    """单条写入海外财务数据（upsert）。

    Args:
        db: 数据库会话
        data: {stock_code, stock_name, market, pe_ttm, pb_mrq, ps_ttm,
               dividend_yield, market_cap, eps_fy1, sector, industry, as_of_date}

    Returns: {status, market}
    """
    stock_code = data.get("stock_code", "")
    if not stock_code:
        raise ValueError("stock_code 不能为空")

    market = data.get("market")
    if not market:
        from crawlers.price_data import _infer_market_from_ticker
        market = _infer_market_from_ticker(stock_code)
    as_of = data.get("as_of_date")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)

    # 查找已存在记录（同 stock_code + as_of_date）
    existing = db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == stock_code,
        OverseasShareFinancialSnapshot.as_of_date == as_of,
    ).first()

    fields = (
        "stock_name", "market", "pe_ttm", "pb_mrq", "ps_ttm",
        "dividend_yield", "market_cap", "eps_fy1",
        "sector", "industry",
    )

    if existing:
        for f in fields:
            if f in data:
                setattr(existing, f, data[f])
    else:
        kwargs = {"stock_code": stock_code, "as_of_date": as_of, "user_id": 1, "market": market}
        for f in fields:
            if f in data:
                kwargs[f] = data[f]
        snap = OverseasShareFinancialSnapshot(**kwargs)
        db.add(snap)

    db.commit()
    return {"status": "ok", "market": market}


def fetch_and_store_overseas_financials(db: Session, stock_codes: list[str], as_of_date: date) -> dict:
    """批量从 yfinance 获取海外财务数据并存储。

    Args:
        db: 数据库会话
        stock_codes: yfinance ticker 列表
        as_of_date: 截止日期

    Returns: {status, fetched, stored, errors}
    """
    from crawlers.price_data import fetch_yfinance_info
    import time

    fetched = 0
    stored = 0
    errors = []

    for code in stock_codes:
        try:
            yf_info = fetch_yfinance_info(code)
            if not yf_info:
                errors.append(f"{code}: yfinance 返回空")
                continue

            fetched += 1

            data = {
                "stock_code": code,
                "stock_name": yf_info.get("name", ""),
                "market": yf_info.get("market", "US"),
                "pe_ttm": yf_info.get("pe_ttm"),
                "pb_mrq": yf_info.get("pb_mrq"),
                "ps_ttm": yf_info.get("ps_ttm"),
                "dividend_yield": yf_info.get("dividend_yield"),
                "market_cap": yf_info.get("market_cap_b"),
                "eps_fy1": yf_info.get("eps_fy1"),
                "sector": yf_info.get("sector"),
                "industry": yf_info.get("industry"),
                "as_of_date": as_of_date,
            }

            upsert_overseas_financial(db, data)
            stored += 1

            # yfinance 限流：3s/req
            time.sleep(3)

        except Exception as e:
            errors.append(f"{code}: {str(e)}")
            logger.warning("获取海外财务数据失败 [%s]: %s", code, e)
            continue

    return {"status": "ok", "fetched": fetched, "stored": stored, "errors": errors}
```

> **注意**：`_infer_market_from_ticker` 函数在 `crawlers/price_data.py` 中定义（见 5.1 节），service 层通过 `from crawlers.price_data import _infer_market_from_ticker` 复用。

### 6.2 设计决策

1. **延迟 import**：`fetch_and_store_overseas_financials` 内部 `from crawlers.price_data import fetch_yfinance_info`，避免顶部 import 污染
2. **3s 限流**：yfinance 易 429，每次请求后 sleep 3s
3. **`_infer_market_from_ticker` 复用**：此函数在 `crawlers/price_data.py` 中定义（见 5.1 节），service 层通过 `from crawlers.price_data import _infer_market_from_ticker` 复用，不重复定义

## 7. Scheduler 集成

### 7.1 修改 `job_update_financial_fundamentals`

在现有 `job_update_financial_fundamentals` 函数中，增加将海外持仓数据写入 `OverseasShareFinancialSnapshot` 的逻辑：

```python
@track_run("financial_fundamentals")
def job_update_financial_fundamentals():
    """每日7:00/19:00执行：增量抓取财务基本面数据并运行穿透计算"""
    db: Session = SessionLocal()
    try:
        from crawlers.price_data import get_stock_info, fetch_yfinance_info
        from services.overseas_financial_service import fetch_and_store_overseas_financials
        from services.penetration import PenetrationEngine

        today = date.today()

        # === 原有逻辑：US 持仓写入 StockInfoCache（保留兼容） ===
        # ...（现有代码不变）

        # === 新增逻辑：海外持仓写入 OverseasShareFinancialSnapshot ===
        # 获取所有海外持仓（通过 asset_type 过滤 US_STOCK/US_ETF）
        # 未来扩展：可通过 SecurityMaster.market 过滤 KR/JP/EU 等
        overseas_holdings = db.query(Holding).filter(
            Holding.asset_type.in_([
                AssetType.US_STOCK.value,
                AssetType.US_ETF.value,
            ])
        ).all()
        overseas_codes = list(set(h.security_code for h in overseas_holdings))

        if overseas_codes:
            result = fetch_and_store_overseas_financials(db, overseas_codes, today)
            logger.info("海外财务数据更新：fetched=%d, stored=%d, errors=%d",
                       result["fetched"], result["stored"], len(result["errors"]))

        # === 原有逻辑：穿透计算 ===
        # ...
```

### 7.2 设计决策

1. **保留 StockInfoCache 写入**：现有逻辑不删除，保持向后兼容（前端可能依赖 StockInfoCache）
2. **新增 OverseasShareFinancialSnapshot 写入**：并行写入新表
3. **持仓过滤**：当前只过滤 `US_STOCK`/`US_ETF`，未来可通过 `SecurityMaster.market` 扩展到韩国等市场
4. **不新建独立 job**：复用现有 `job_update_financial_fundamentals`，避免 scheduler 配置膨胀

## 8. 穿透分析集成

### 8.1 修改 `resolve_dynamic_metrics_for_stock`

在 `backend/services/aggregation.py` 中修改：

```python
def resolve_dynamic_metrics_for_stock(db: Session, stock_code: str):
    code_norm = stock_code.split(".")[0]
    # 1. 先查港股
    h_snap = db.query(HKShareFinancialSnapshot).filter(
        HKShareFinancialSnapshot.stock_code.like(f"{code_norm}%"),
    ).first()
    if h_snap:
        return h_snap.pe_ttm_dynamic, h_snap.pb_mrq_dynamic, h_snap.ps_ttm_dynamic
    # 2. 再查 A 股
    padded = _pad_csi_code(stock_code)
    for suffix in (".SZ", ".SH"):
        candidate = f"{padded}{suffix}"
        snap = db.query(AShareFinancialSnapshot).filter_by(stock_code=candidate).first()
        if snap:
            return snap.pe_ttm_dynamic, snap.pb_mrq_dynamic, snap.ps_ttm_dynamic
    # 3. 新增：查海外市场
    o_snap = db.query(OverseasShareFinancialSnapshot).filter(
        OverseasShareFinancialSnapshot.stock_code == stock_code,
    ).order_by(OverseasShareFinancialSnapshot.as_of_date.desc()).first()
    if o_snap:
        return o_snap.pe_ttm_dynamic, o_snap.pb_mrq_dynamic, o_snap.ps_ttm_dynamic
    return None, None, None
```

### 8.2 设计决策

1. **查询顺序**：HK → CN → Overseas（海外优先级最低，因为 yfinance 数据可能不如本地数据准确）
2. **精确匹配**：海外股票用 `stock_code == stock_code` 精确匹配（不像 A股用 like，因为 yfinance ticker 格式统一）
3. **取最新**：`order_by(as_of_date.desc())` 取最新快照
4. **dynamic 字段**：需要 scheduler 或手动触发计算 dynamic 值（见 8.3）

### 8.3 Dynamic 指标计算

dynamic PE/PB/PS = baseline 指标 × (current_price / baseline_price)

**计算时机**：在 `fetch_and_store_overseas_financials` 中，upsert 完成后立即计算 dynamic 值：
1. `baseline_price` = yfinance 获取时的当前股价（`stock.info.get("currentPrice")` 或 `stock.history(period="1d")`）
2. `current_price` = baseline_price（首次写入时两者相同）
3. `pe_ttm_dynamic` = `pe_ttm`（首次写入时 dynamic = baseline，因为 current = baseline）
4. 后续 scheduler 运行时更新 `current_price`，并重算 dynamic

**实现方式**：在 `upsert_overseas_financial` 中，如果 `baseline_price` 未设置，则用当前价作为 baseline；如果已设置，则用新价作为 current_price 并计算 dynamic。

## 9. API 端点（可选）

### 9.1 查看海外财务数据

```python
@app.get("/api/admin/overseas-financials")
def admin_list_overseas_financials(
    market: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """查看海外财务数据快照。"""
    query = db.query(OverseasShareFinancialSnapshot)
    if market:
        query = query.filter(OverseasShareFinancialSnapshot.market == market)
    total = query.count()
    items = query.order_by(OverseasShareFinancialSnapshot.as_of_date.desc()) \
        .offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [{
            "stock_code": s.stock_code,
            "stock_name": s.stock_name,
            "market": s.market,
            "as_of_date": str(s.as_of_date),
            "pe_ttm": s.pe_ttm,
            "pb_mrq": s.pb_mrq,
            "ps_ttm": s.ps_ttm,
            "dividend_yield": s.dividend_yield,
            "market_cap": s.market_cap,
            "sector": s.sector,
            "industry": s.industry,
            "source": s.source,
        } for s in items],
        "total": total,
    }
```

### 9.2 手动触发更新

```python
@app.post("/api/admin/overseas-financials/refresh")
def admin_refresh_overseas_financials(db: Session = Depends(get_db)):
    """手动触发海外财务数据更新。"""
    from services.overseas_financial_service import fetch_and_store_overseas_financials
    # 获取所有海外持仓（US_STOCK + US_ETF）
    overseas_holdings = db.query(Holding).filter(
        Holding.asset_type.in_([
            AssetType.US_STOCK.value,
            AssetType.US_ETF.value,
        ])
    ).all()
    overseas_codes = list(set(h.security_code for h in overseas_holdings))
    if not overseas_codes:
        return {"status": "ok", "fetched": 0, "stored": 0, "errors": ["无海外持仓"]}
    result = fetch_and_store_overseas_financials(db, overseas_codes, date.today())
    return result
```

## 10. 前端（可选）

在数据源页面（`DataSourcePanel`）增加"海外财务数据"tab，显示：
- 海外持仓代码列表
- 每个代码的最近一次 yfinance 数据获取状态
- 手动触发更新按钮

此部分为可选项，优先级低于后端核心功能。

## 11. 测试策略

### 11.1 单元测试

- `test_overseas_financial_service.py`：
  - `test_upsert_overseas_financial_create` — 单条创建
  - `test_upsert_overseas_financial_update` — 单条更新
  - `test_upsert_overseas_financial_market_infer` — market 推断
  - `test_fetch_and_store_overseas_financials` — 批量获取（mock yfinance）
  - `test_infer_market_from_ticker` — 后缀推断

### 11.2 集成测试

- `test_overseas_financial_api.py`：
  - `test_list_overseas_financials` — 列表查询
  - `test_refresh_overseas_financials` — 手动触发（mock yfinance）

### 11.3 穿透分析测试

- 修改现有 `test_aggregation.py`（如果存在）：
  - `test_resolve_dynamic_metrics_overseas` — 海外股票 PE/PB/PS 解析

## 12. 系统依赖

- **yfinance**：已安装（requirements.txt 已有）
- **无新增依赖**：本子项目不引入新的 Python 包
- **无系统依赖**：不需要 tesseract-ocr 或 poppler 等系统级依赖

## 13. 实施任务概要

| Task | 内容 |
|------|------|
| Task 1 | OverseasShareFinancialSnapshot 模型 + 迁移 |
| Task 2 | yfinance 增强（fetch_yfinance_info 添加 PB/PS + market 推断） |
| Task 3 | overseas_financial_service（upsert + fetch_and_store） |
| Task 4 | 修改 resolve_dynamic_metrics_for_stock 集成海外查询 |
| Task 5 | scheduler 集成（job_update_financial_fundamentals 增加海外写入） |
| Task 6 | API 端点（列表 + 手动触发） |
| Task 7 | 前端数据源 tab（可选） |
| Task 8 | 集成测试 + 最终验证 |
