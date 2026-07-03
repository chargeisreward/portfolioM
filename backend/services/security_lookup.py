"""统一证券主数据查询服务 (2026-07-02)。

替代旧 `security_master` 直读，作为新代码读证券基础信息的统一入口。

数据源（按优先级）：
  1. `stock_master`    — 股票主数据 (A 股 / 港股 / 美股)
  2. `fund_master`     — 基金主数据 (场内 ETF / 场外 / QDII)
  3. `index_master`    — 指数主数据
  4. `security_master_legacy` — 旧表 (兼容层,只读,6 个月冻结期)

返回字段统一为 SecurityView TypedDict,字段语义对齐旧 SecurityMaster:
  security_code / security_name / asset_type / currency / is_drillable /
  security_type (= 'stock'|'fund'|'index') / market / exchange /
  fund_type / type2 (legacy only) / benchmark_formula / source

用途:
  - main.py 中所有读 SecurityMaster 的位置改用本服务
  - 新代码 (onboard / api.js 前端 lookup) 通过本服务读
"""
from __future__ import annotations

from typing import TypedDict

from sqlalchemy.orm import Session

from models_master import StockMaster, FundMaster, IndexMaster


class SecurityView(TypedDict, total=False):
    """统一证券视图(读侧)。"""
    security_code: str
    security_name: str
    security_type: str          # 'stock' / 'fund' / 'index'
    asset_type: str
    currency: str
    market: str                 # 旧表字段(legacy)
    exchange: str
    fund_type: str              # 'etf' / 'otc' (fund_master 字段)
    is_drillable: bool
    is_listed: bool             # stock_master
    is_active: bool             # index_master
    benchmark_formula: str      # fund_master
    category: str               # index_master: 宽基/行业/主题/策略
    type2: str                  # legacy only (theme)
    source: str                 # index_master source
    note: str


# ============== 内部辅助 ==============

def _stock_to_view(s: StockMaster) -> SecurityView:
    return SecurityView(
        security_code=s.stock_code,
        security_name=s.stock_name,
        security_type="stock",
        asset_type=s.asset_type,
        currency=s.currency or "CNY",
        exchange=s.exchange,
        is_drillable=bool(s.is_drillable),
        is_listed=bool(s.is_listed) if s.is_listed is not None else True,
        note=s.note,
    )


def _fund_to_view(f: FundMaster) -> SecurityView:
    return SecurityView(
        security_code=f.fund_code,
        security_name=f.fund_name,
        security_type="fund",
        asset_type=f.asset_type,
        currency=f.currency or "CNY",
        fund_type=f.fund_type,
        is_drillable=bool(f.is_drillable),
        benchmark_formula=f.benchmark_formula,
        note=f.note,
    )


def _index_to_view(i: IndexMaster) -> SecurityView:
    return SecurityView(
        security_code=i.index_code,
        security_name=i.index_name,
        security_type="index",
        asset_type="index",
        currency=i.currency or "CNY",
        exchange=i.exchange,
        category=i.category,
        source=i.source or "akshare",
        is_active=bool(i.is_active) if i.is_active is not None else True,
        note=i.source,
    )


def _legacy_to_view(s) -> SecurityView:
    """旧 security_master_legacy → SecurityView 适配。"""
    return SecurityView(
        security_code=s.security_code,
        security_name=s.security_name,
        security_type=s.security_type or "fund",
        asset_type=s.asset_type,
        currency=s.currency or "CNY",
        market=s.market,
        exchange=s.exchange,
        fund_type=s.fund_type,
        is_drillable=bool(s.is_drillable),
        type2=s.type2,
        benchmark_formula=s.benchmark_formula,
        note=s.note,
    )


# ============== 公共 API ==============

def get_security_view(db: Session, code: str) -> SecurityView | None:
    """查单只证券,新表优先,legacy 兜底。

    Args:
        db: SQLAlchemy Session
        code: 证券代码 (e.g. 'AAPL', '510300.SH', '161039.OF')

    Returns:
        SecurityView dict 或 None (代码不存在)
    """
    if not code:
        return None

    # 1. stock_master
    s = db.query(StockMaster).filter(StockMaster.stock_code == code).first()
    if s:
        return _stock_to_view(s)

    # 2. fund_master
    f = db.query(FundMaster).filter(FundMaster.fund_code == code).first()
    if f:
        return _fund_to_view(f)

    # 3. index_master
    i = db.query(IndexMaster).filter(IndexMaster.index_code == code).first()
    if i:
        return _index_to_view(i)

    # 4. legacy 兜底 (compat layer,只读)
    try:
        from models import SecurityMaster
        sm = db.query(SecurityMaster).filter(SecurityMaster.security_code == code).first()
        if sm:
            return _legacy_to_view(sm)
    except Exception:
        # 旧表可能不存在 (全新环境),静默返回 None
        pass

    return None


def get_currency_asset_type(db: Session, code: str) -> tuple[str, str | None]:
    """查代码的 (currency, asset_type)。便捷方法用于价格/汇率 JOIN。

    Returns:
        (currency, asset_type) — currency 默认 'CNY',asset_type 可能为 None
    """
    v = get_security_view(db, code)
    if v:
        return v.get("currency", "CNY"), v.get("asset_type")
    return "CNY", None


def get_security_name(db: Session, code: str) -> str | None:
    """查代码的名称。"""
    v = get_security_view(db, code)
    return v.get("security_name") if v else None


def get_security_type(db: Session, code: str) -> str | None:
    """返回 'stock' / 'fund' / 'index'。"""
    v = get_security_view(db, code)
    return v.get("security_type") if v else None


def get_security_view_map(db: Session, codes: list[str] | None = None) -> dict[str, SecurityView]:
    """批量查多只证券,返回 {code: SecurityView}。

    用于 main.py 等价格/汇率 JOIN 时构建 sm_map 的场景。
    legacy 表只在请求某 code 且新三表没有时查(为节省内存)。

    Args:
        db: SQLAlchemy Session
        codes: 待查代码列表。None 表示查全部新三表 + legacy (供 /api/securities 用)。

    Returns:
        {security_code: SecurityView dict} — 不存在的代码不出现在 dict 中。
    """
    result: dict[str, SecurityView] = {}
    if codes is None:
        # 全量模式 (admin 列表场景)
        from models_master import StockMaster, FundMaster, IndexMaster
        for s in db.query(StockMaster).all():
            result[s.stock_code] = _stock_to_view(s)
        for f in db.query(FundMaster).all():
            result[f.fund_code] = _fund_to_view(f)
        for i in db.query(IndexMaster).all():
            result[i.index_code] = _index_to_view(i)
        try:
            from models import SecurityMaster
            for sm in db.query(SecurityMaster).all():
                if sm.security_code not in result:
                    result[sm.security_code] = _legacy_to_view(sm)
        except Exception:
            pass
        return result

    # 指定 codes:按顺序查,先查新三表,后 legacy
    legacy_codes = []
    seen = set()
    for code in codes:
        if not code or code in seen:
            continue
        seen.add(code)

        s = db.query(StockMaster).filter(StockMaster.stock_code == code).first()
        if s:
            result[code] = _stock_to_view(s)
            continue
        f = db.query(FundMaster).filter(FundMaster.fund_code == code).first()
        if f:
            result[code] = _fund_to_view(f)
            continue
        i = db.query(IndexMaster).filter(IndexMaster.index_code == code).first()
        if i:
            result[code] = _index_to_view(i)
            continue
        legacy_codes.append(code)

    # legacy 兜底(失败也不抛)
    if legacy_codes:
        try:
            from models import SecurityMaster
            rows = db.query(SecurityMaster).filter(
                SecurityMaster.security_code.in_(legacy_codes)
            ).all()
            for sm in rows:
                result[sm.security_code] = _legacy_to_view(sm)
        except Exception:
            pass

    return result


def exists_in_new_tables(db: Session, code: str) -> bool:
    """判断代码是否在新三张表之一中 (stock_master / fund_master / index_master)。

    用于 onboard 决策: 如果在新表已有,跳过 onboard;
    如果只在 legacy,认为是历史数据,不重新入库。
    """
    if not code:
        return False
    if db.query(StockMaster).filter(StockMaster.stock_code == code).first():
        return True
    if db.query(FundMaster).filter(FundMaster.fund_code == code).first():
        return True
    if db.query(IndexMaster).filter(IndexMaster.index_code == code).first():
        return True
    return False


# ============== 写侧辅助 ==============

def _derive_target_table(asset_type: str) -> tuple[type, str]:
    """根据 asset_type 判定写入哪张新表。

    Returns:
        (ORM 模型类, code 字段名)
    """
    # 股票: 美股 / A 股正股 / 港股正股
    if asset_type in ("us_stock", "hk_stock", "a_share_stock"):
        return StockMaster, "stock_code"
    # 指数 (单独类别)
    if asset_type == "index":
        return IndexMaster, "index_code"
    # 其余(基金 / ETF / QDII / 黄金 / 债券) → FundMaster
    return FundMaster, "fund_code"


def _to_target_kwargs(asset_type: str, view: dict) -> tuple[type, dict]:
    """根据 asset_type 把统一字段映射到目标表字段名。

    Returns:
        (ORM 类, kwargs dict)
    """
    model, code_field = _derive_target_table(asset_type)

    if model is StockMaster:
        kwargs = {
            "stock_code": view["security_code"],
            "stock_name": view["security_name"],
            "asset_type": view["asset_type"],
            "currency": view.get("currency", "CNY"),
            "exchange": view.get("exchange"),
            "is_drillable": view.get("is_drillable", False),
            "is_listed": view.get("is_listed", True),
            "note": view.get("note"),
        }
    elif model is IndexMaster:
        kwargs = {
            "index_code": view["security_code"],
            "index_name": view["security_name"],
            "currency": view.get("currency", "CNY"),
            "exchange": view.get("exchange"),
            "category": view.get("category"),
            "is_active": view.get("is_active", True),
            "source": view.get("source", "akshare"),
        }
    else:  # FundMaster
        kwargs = {
            "fund_code": view["security_code"],
            "fund_name": view["security_name"],
            "fund_type": view.get("fund_type") or _derive_fund_type_from_asset(asset_type),
            "asset_type": view["asset_type"],
            "currency": view.get("currency", "CNY"),
            "benchmark_formula": view.get("benchmark_formula"),
            "is_drillable": view.get("is_drillable", False),
            "note": view.get("note"),
        }
    return model, kwargs


def _derive_fund_type_from_asset(asset_type: str) -> str:
    """根据 asset_type 推断 fund_type ('etf' / 'otc')。"""
    etf_set = {"a_share_etf", "hk_etf", "us_etf", "qdii_etf"}
    return "etf" if asset_type in etf_set else "otc"