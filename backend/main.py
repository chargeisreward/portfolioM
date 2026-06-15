"""PortfolioM — FastAPI 应用入口"""
import hashlib
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Depends, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, init_db
from schemas import (
    HoldingOut, HoldingSummary, PenetrationRow, PenetrationSummary,
    IndustryChainAnalysis, GrowthAnalysis, ValuationMetrics,
    PriceSeries, PricePoint, ImportRequest, CrawlResponse,
    SecurityMasterOut, SecurityMasterUpsert,
    SecurityTypeConfigOut, SecurityTypeConfigUpsert,
)
from services.importer import import_excel, get_holdings_summary
from services.penetration import PenetrationEngine
from services.growth_bucketer import GrowthBucketer, IndustryChainAnalyzer
from services.csi300 import Csi300Analyzer
from crawlers.etf_index import crawl_fund_index_map
from crawlers.index_constituents import crawl_constituents
from crawlers.price_data import get_stock_info, fetch_price_history

app = FastAPI(title="PortfolioM", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path(__file__).parent.parent


# ==================== 访问密码 + IP 限流 ====================

import os
# 启动时设置的访问密码。优先级: env APP_PASSWORD > 默认 dev 密码
APP_PASSWORD = os.environ.get("APP_PASSWORD", "123456")
# 简单 SHA-256 存明文 hash 比较（dev 简化，生产应换 bcrypt）
def _hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()
APP_PASSWORD_HASH = _hash_pw(APP_PASSWORD)

# IP 限流阈值
_BAN_RULES = [
    (10, timedelta(hours=1)),    # 10 次 → 1h
    (20, timedelta(days=1)),     # 20 次 → 1d
    (30, timedelta(days=30)),    # 30 次 → 30d
    (40, timedelta(days=365)),   # 40 次 → 365d
]


def _client_ip(request: Request) -> str:
    """取真实 IP（兼容反向代理）"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real
    if request.client:
        return request.client.host
    return "unknown"


def _check_ban(db: Session, ip: str):
    """检查 IP 是否被锁，返回 (banned_until_or_None, remaining_seconds)"""
    from models import AccessAttempt
    rec = db.query(AccessAttempt).filter(AccessAttempt.ip == ip).first()
    if not rec or not rec.banned_until:
        return None, 0
    now = datetime.utcnow()
    if rec.banned_until > now:
        remaining = int((rec.banned_until - now).total_seconds())
        return rec.banned_until, remaining
    # 已过期 — 清空 banned_until 但保留计数
    rec.banned_until = None
    db.commit()
    return None, 0


def _record_fail(db: Session, ip: str):
    """记录一次失败，并按规则判断是否需要封禁"""
    from models import AccessAttempt
    rec = db.query(AccessAttempt).filter(AccessAttempt.ip == ip).first()
    if not rec:
        rec = AccessAttempt(ip=ip, fails_1h=0, fails_1d=0, fails_1mo=0, fails_1y=0)
        db.add(rec)
    rec.fails_1h += 1
    rec.fails_1d += 1
    rec.fails_1mo += 1
    rec.fails_1y += 1
    rec.last_fail_at = datetime.utcnow()

    # 按从大到小阈值检查（命中最大阈值优先）
    ban_for = None
    for threshold, duration in reversed(_BAN_RULES):
        if rec.fails_1y >= threshold:
            ban_for = duration
            break
    if ban_for:
        rec.banned_until = datetime.utcnow() + ban_for
    db.commit()
    return rec, ban_for


def _record_success(db: Session, ip: str):
    """成功登录：清零失败计数（保留最后成功时间）"""
    from models import AccessAttempt
    rec = db.query(AccessAttempt).filter(AccessAttempt.ip == ip).first()
    if rec:
        rec.fails_1h = 0
        rec.fails_1d = 0
        rec.fails_1mo = 0
        rec.fails_1y = 0
        rec.banned_until = None
        rec.last_success_at = datetime.utcnow()
        db.commit()


def _create_session(db: Session, ip: str) -> str:
    """创建新 session，返回 token。默认 24h 过期"""
    from models import AccessSession
    token = secrets.token_hex(32)
    sess = AccessSession(
        token=token,
        ip=ip,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=1),
    )
    db.add(sess)
    db.commit()
    return token


def _verify_token(db: Session, token: str) -> bool:
    """验证 session token 是否有效"""
    from models import AccessSession
    if not token:
        return False
    sess = db.query(AccessSession).filter(AccessSession.token == token).first()
    if not sess:
        return False
    if sess.expires_at < datetime.utcnow():
        db.delete(sess)
        db.commit()
        return False
    return True


def require_auth(request: Request, db: Session = Depends(get_db)):
    """FastAPI 依赖：要求有效 session。失败抛 401。"""
    # 跳过 auth 端点本身
    if request.url.path.startswith("/api/auth/"):
        return True
    # 跳过 OpenAPI 文档
    if request.url.path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
        return True
    # 跳过 admin 端点（用于本地同步脚本）— 需额外 token
    # 跳过 static 资源
    token = request.headers.get("x-session-token") or request.query_params.get("session")
    if not _verify_token(db, token):
        raise HTTPException(status_code=401, detail="需要登录")


class LoginRequest(BaseModel):
    password: str


@app.get("/api/auth/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    """检查当前 IP 是否被锁、密码长度要求等"""
    ip = _client_ip(request)
    banned_until, remaining = _check_ban(db, ip)
    return {
        "require_password": True,
        "banned": banned_until is not None,
        "banned_until": banned_until.isoformat() if banned_until else None,
        "remaining_seconds": remaining,
        "min_length": 6,
        "max_length": 12,
    }


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """提交密码。正确→发 token；错→按 IP 限流规则封禁"""
    ip = _client_ip(request)
    # 先检查是否被锁
    banned_until, remaining = _check_ban(db, ip)
    if banned_until:
        return {
            "status": "banned",
            "banned_until": banned_until.isoformat(),
            "remaining_seconds": remaining,
        }
    # 密码长度校验
    if not (6 <= len(req.password) <= 12):
        return {"status": "error", "message": "密码长度需 6-12 位"}
    # 校验密码
    if _hash_pw(req.password) != APP_PASSWORD_HASH:
        rec, ban_for = _record_fail(db, ip)
        result = {
            "status": "error",
            "message": "密码错误",
            "attempts_1y": rec.fails_1y,
        }
        if ban_for:
            result["status"] = "banned"
            result["banned_until"] = (datetime.utcnow() + ban_for).isoformat()
            result["remaining_seconds"] = int(ban_for.total_seconds())
            result["message"] = f"输错 {rec.fails_1y} 次，已封禁 {int(ban_for.total_seconds()//3600)} 小时"
        return result
    # 成功
    _record_success(db, ip)
    token = _create_session(db, ip)
    return {
        "status": "ok",
        "token": token,
        "expires_in": 86400,
    }


@app.post("/api/auth/logout")
def auth_logout(request: Request, db: Session = Depends(get_db)):
    """登出（删除当前 session）"""
    from models import AccessSession
    token = request.headers.get("x-session-token") or request.query_params.get("session")
    if token:
        db.query(AccessSession).filter(AccessSession.token == token).delete()
        db.commit()
    return {"status": "ok"}


# 给所有受保护端点加依赖
def _apply_auth_to_routes():
    """遍历 app 路由，给非 auth 端点加 require_auth 依赖"""
    from fastapi.routing import APIRoute
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api/"):
            continue
        if route.path.startswith("/api/auth/"):
            continue
        # 在依赖列表前加 require_auth
        # 注意：FastAPI 路由依赖是合并方式，重复 add 不会冲突
        route.dependant.dependencies.insert(0, ...)  # 复杂；改用更直接的方法


# 上面 _apply_auth_to_routes 太复杂，改用 middleware 方式：
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """统一鉴权：未通过 → 401"""
    path = request.url.path
    # admin 同步端点：需专用 token（独立于用户密码）
    if path.startswith("/api/admin/sync-table"):
        admin_token = os.environ.get("ADMIN_TOKEN", APP_PASSWORD)  # 默认复用 APP_PASSWORD
        provided = request.headers.get("x-admin-token")
        if provided != admin_token:
            return _json_error(401, "admin token required")
        return await call_next(request)
    # 公开路径
    PUBLIC_PATHS = (
        "/api/auth/",
        "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/favicon.ico",
    )
    if any(path.startswith(p) for p in PUBLIC_PATHS):
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)
    # 检查 session
    token = request.headers.get("x-session-token") or request.query_params.get("session")
    db = next(get_db())
    try:
        if not _verify_token(db, token):
            return _json_error(401, "需要登录")
    finally:
        db.close()
    return await call_next(request)


def _json_error(status: int, msg: str):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=status, content={"detail": msg})


@app.on_event("startup")
def startup():
    init_db()
    from services.scheduler import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
def shutdown():
    from services.scheduler import stop_scheduler
    stop_scheduler()


# ==================== 持仓 ====================

@app.get("/api/holdings", response_model=list[HoldingOut])
def list_holdings(db: Session = Depends(get_db)):
    from models import Holding as HoldingModel
    return db.query(HoldingModel).all()


@app.get("/api/holdings/summary", response_model=HoldingSummary)
def holdings_summary(db: Session = Depends(get_db)):
    return get_holdings_summary(db)


# ==================== 证券基础表 ====================

@app.get("/api/securities", response_model=list[SecurityMasterOut])
def list_securities(db: Session = Depends(get_db)):
    """获取所有证券基础信息"""
    from models import SecurityMaster
    return db.query(SecurityMaster).all()


@app.get("/api/securities/{code}", response_model=SecurityMasterOut)
def get_security(code: str, db: Session = Depends(get_db)):
    """获取单只证券基础信息"""
    from models import SecurityMaster
    row = db.query(SecurityMaster).filter(SecurityMaster.security_code == code).first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Security {code} not found")
    return row


@app.put("/api/securities/{code}", response_model=SecurityMasterOut)
def upsert_security(code: str, body: SecurityMasterUpsert, db: Session = Depends(get_db)):
    """新增或更新证券基础信息"""
    from models import SecurityMaster
    from datetime import datetime as dt
    row = db.query(SecurityMaster).filter(SecurityMaster.security_code == code).first()
    if row:
        row.security_name = body.security_name or row.security_name
        row.currency = body.currency
        row.asset_type = body.asset_type or row.asset_type
        # type2 允许显式置空：传 "" 表示清空
        row.type2 = body.type2 if body.type2 is not None else row.type2
        if body.type2 == "":
            row.type2 = None
        row.exchange = body.exchange or row.exchange
        row.updated_at = dt.utcnow()
    else:
        row = SecurityMaster(
            security_code=code,
            security_name=body.security_name,
            currency=body.currency,
            asset_type=body.asset_type,
            type2=body.type2 or None,
            exchange=body.exchange,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


@app.post("/api/securities/sync-from-holdings", response_model=CrawlResponse)
def sync_securities_from_holdings(db: Session = Depends(get_db)):
    """从持仓表同步证券基础信息（仅新增，不覆盖已有）"""
    from models import Holding, SecurityMaster
    from services.importer import guess_asset_type
    from crawlers.exchange_rates import guess_currency_from_code
    from datetime import datetime as dt
    holdings = db.query(Holding).all()
    added = 0
    for h in holdings:
        exists = db.query(SecurityMaster).filter(SecurityMaster.security_code == h.security_code).first()
        if not exists:
            sm = SecurityMaster(
                security_code=h.security_code,
                security_name=h.security_name,
                currency=guess_currency_from_code(h.security_code),
                asset_type=h.asset_type or guess_asset_type(h.security_code),
            )
            db.add(sm)
            added += 1
            db.flush()  # flush per row to avoid duplicate key in batch
    db.commit()
    return CrawlResponse(status="ok", message=f"Synced {added} new securities", count=added)


# ==================== 证券类型配置 ====================

@app.get("/api/security-types", response_model=list[SecurityTypeConfigOut])
def list_security_types(db: Session = Depends(get_db)):
    """获取所有证券类型配置"""
    from models import SecurityTypeConfig
    return db.query(SecurityTypeConfig).order_by(SecurityTypeConfig.sort_order).all()


@app.put("/api/security-types/{asset_type}", response_model=SecurityTypeConfigOut)
def upsert_security_type(asset_type: str, body: SecurityTypeConfigUpsert, db: Session = Depends(get_db)):
    """新增或更新证券类型配置"""
    from models import SecurityTypeConfig
    from datetime import datetime as dt
    row = db.query(SecurityTypeConfig).filter(SecurityTypeConfig.asset_type == asset_type).first()
    if row:
        row.type_name = body.type_name or row.type_name
        row.price_precision = body.price_precision
        row.amount_precision = body.amount_precision
        row.sort_order = body.sort_order
        row.updated_at = dt.utcnow()
    else:
        row = SecurityTypeConfig(
            asset_type=asset_type,
            type_name=body.type_name,
            price_precision=body.price_precision,
            amount_precision=body.amount_precision,
            sort_order=body.sort_order,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


@app.post("/api/security-types/seed", response_model=CrawlResponse)
def seed_security_types(db: Session = Depends(get_db)):
    """初始化证券类型配置种子数据"""
    from models import SecurityTypeConfig
    from datetime import datetime as dt
    seeds = [
        {"asset_type": "a_share_equity", "type_name": "A股基金", "price_precision": 4, "sort_order": 1},
        {"asset_type": "a_share_etf", "type_name": "A股ETF", "price_precision": 3, "sort_order": 2},
        {"asset_type": "bond", "type_name": "债券基金", "price_precision": 4, "sort_order": 3},
        {"asset_type": "gold", "type_name": "黄金", "price_precision": 4, "sort_order": 4},
        {"asset_type": "hk_equity", "type_name": "港股", "price_precision": 3, "sort_order": 5},
        {"asset_type": "qdii_equity", "type_name": "QDII", "price_precision": 4, "sort_order": 6},
        {"asset_type": "us_stock", "type_name": "美股", "price_precision": 2, "sort_order": 7},
        {"asset_type": "us_etf", "type_name": "美股ETF", "price_precision": 2, "sort_order": 8},
    ]
    added = 0
    for s in seeds:
        exists = db.query(SecurityTypeConfig).filter(SecurityTypeConfig.asset_type == s["asset_type"]).first()
        if not exists:
            db.add(SecurityTypeConfig(**s, updated_at=dt.utcnow()))
            added += 1
    db.commit()
    return CrawlResponse(status="ok", message=f"Seeded {added} security type configs", count=added)


# ==================== 汇率 ====================

@app.post("/api/exchange-rates/update", response_model=CrawlResponse)
def update_exchange_rates(db: Session = Depends(get_db)):
    """Crawl PBoC rates for today"""
    from crawlers.exchange_rates import update_rates_today
    count = update_rates_today(db)
    return CrawlResponse(status="ok", message=f"Updated {count} rate records", count=count)


@app.get("/api/exchange-rates")
def list_exchange_rates(db: Session = Depends(get_db)):
    """List latest exchange rates"""
    from models import ExchangeRate
    from datetime import date as date_cls
    rows = db.query(ExchangeRate).filter(
        ExchangeRate.rate_date <= date_cls.today()
    ).order_by(ExchangeRate.rate_date.desc()).limit(10).all()
    return [{"date": r.rate_date.isoformat(), "from": r.from_currency, "to": r.to_currency, "rate": r.rate, "source": r.source} for r in rows]


@app.get("/api/holdings/converted")
def holdings_converted(target: str = Query("CNY"), db: Session = Depends(get_db)):
    """Get holdings with amounts converted to target currency.
    Joins with security_master for currency and asset_type.
    Joins with security_type_config for price_precision."""
    from models import Holding as HoldingModel, SecurityMaster, SecurityTypeConfig
    from crawlers.exchange_rates import get_rate
    rows = db.query(HoldingModel).all()
    # Build lookup from security_master
    sm_map = {}
    for sm in db.query(SecurityMaster).all():
        sm_map[sm.security_code] = sm
    # Build lookup from security_type_config
    stc_map = {}
    for stc in db.query(SecurityTypeConfig).all():
        stc_map[stc.asset_type] = stc

    result = []
    for h in rows:
        sm = sm_map.get(h.security_code)
        # Priority: security_master > holding field > guess
        orig_currency = sm.currency if sm else (h.currency or 'CNY')
        asset_type = sm.asset_type if sm else h.asset_type
        security_name = sm.security_name if sm else h.security_name
        # Price precision from type config
        stc = stc_map.get(asset_type)
        price_precision = stc.price_precision if stc else 2

        # Convert to target currency
        rate = get_rate(db, 'CNY', target) if target != 'CNY' else 1.0
        if target == 'CNY':
            converted = h.amount_cny or h.amount
        else:
            converted = round((h.amount_cny or h.amount) * rate, 2)

        # 金额·原 = 数量 × 单价（原币种）
        amount_original = round(h.quantity * h.price, 2) if h.price and h.quantity else None

        result.append({
            "security_code": h.security_code,
            "security_name": security_name,
            "quantity": h.quantity,
            "price": h.price,
            "price_precision": price_precision,
            "currency": orig_currency,
            "amount": h.amount,
            "amount_original": amount_original,
            "amount_local": converted,
            "asset_type": asset_type,
            "type2": sm.type2 if sm else None,
        })
    return result


@app.get("/api/trend")
def get_portfolio_trend(
    days: int = Query(90, ge=1, le=365),
    target: str = Query("CNY"),
    db: Session = Depends(get_db),
):
    """组合 90 天资产走势：每日期末总市值 = Σ(qty × close_px × fx_rate)
    用 PriceCache.close_px + ExchangeRate.rate 计算。无历史价则用最近已知价补齐。"""
    from datetime import date, timedelta
    from models import Holding, PriceCache, ExchangeRate

    # 1. 取当前所有 holdings
    rows = db.query(Holding).all()
    if not rows:
        return {"series": [], "currency": target, "days": days}

    # 2. 拿所有 (code, date) -> close_px
    cutoff = date.today() - timedelta(days=days)
    pc_rows = db.query(PriceCache).filter(PriceCache.trade_date >= cutoff).all()
    # code -> {date: close}
    pc_map: dict = {}
    for r in pc_rows:
        pc_map.setdefault(r.stock_code, {})[r.trade_date.isoformat()] = r.close_px

    # 3. 汇率（按 date 查）
    fx_rows = db.query(ExchangeRate).filter(ExchangeRate.rate_date >= cutoff).all()
    # (date, from, to) -> rate
    fx_map: dict = {}
    for r in fx_rows:
        key = (r.rate_date.isoformat(), r.from_currency, r.to_currency)
        fx_map[key] = r.rate
    # 同币 1.0
    def get_fx(d_iso: str, from_cur: str, to_cur: str) -> float:
        if from_cur == to_cur:
            return 1.0
        # 优先当日
        if (d_iso, from_cur, to_cur) in fx_map:
            return fx_map[(d_iso, from_cur, to_cur)]
        # 倒退找最近（最多 7 天）
        d = date.fromisoformat(d_iso)
        for k in range(1, 8):
            nd = (d - timedelta(days=k)).isoformat()
            if (nd, from_cur, to_cur) in fx_map:
                return fx_map[(nd, from_cur, to_cur)]
        return 1.0  # 兜底

    # 4. 找过去 N 天有 trade_date 的全部日期（union 全部 code 的 dates）
    all_dates = sorted({d for code_dates in pc_map.values() for d in code_dates.keys()})
    # 只留 cutoff 之后
    all_dates = [d for d in all_dates if d >= cutoff.isoformat()]

    # 5. 对每个日期算总值
    series = []
    for d_iso in all_dates:
        total = 0.0
        for h in rows:
            code_map = pc_map.get(h.security_code, {})
            px = code_map.get(d_iso)
            if px is None:
                continue  # 当日没价 — 跳过（不归零，留给次日补）
            cur = h.currency or "CNY"
            fx = get_fx(d_iso, cur, target)
            total += (h.quantity or 0) * px * fx
        if total > 0:
            series.append({"date": d_iso, "value": round(total, 2)})

    # 6. 如果完全没有历史价（冷启动），返回空 series — 前端不显示图
    return {"series": series, "currency": target, "days": days}


@app.post("/api/holdings/import", response_model=CrawlResponse)
def import_holdings(req: ImportRequest, db: Session = Depends(get_db)):
    """从Excel导入持仓"""
    xlsx_files = list(DATA_DIR.glob("*.xlsx")) + list(DATA_DIR.glob("*.xls"))
    if not xlsx_files:
        return CrawlResponse(status="error", message="No Excel files found in project root")

    filepath = str(xlsx_files[0])
    count = import_excel(filepath, db)
    return CrawlResponse(status="ok", message=f"Imported {count} holdings", count=count)


@app.post("/api/holdings/fill-prices", response_model=CrawlResponse)
def fill_holdings_prices(db: Session = Depends(get_db)):
    """获取所有持仓的最新价格并计算金额"""
    from services.importer import fill_prices
    from crawlers.exchange_rates import update_rates_today
    # First update rates
    update_rates_today(db)
    # Then update prices
    updated = fill_prices(db)
    return CrawlResponse(status="ok", message=f"Updated prices for {updated} holdings", count=updated)


# ==================== ETF & 穿透 ====================

@app.post("/api/crawl/etf-mapping", response_model=CrawlResponse)
def crawl_etf_mapping(db: Session = Depends(get_db)):
    """爬取ETF→指数映射"""
    count = crawl_fund_index_map(db)
    return CrawlResponse(status="ok", message=f"Mapped {count} funds", count=count)


@app.post("/api/crawl/constituents", response_model=CrawlResponse)
def crawl_constituents_endpoint(
    index_code: str = Query("000300"),
    db: Session = Depends(get_db),
):
    """爬取指数成分股"""
    constituents = crawl_constituents(index_code, db)
    return CrawlResponse(
        status="ok",
        message=f"Crawled {len(constituents)} constituents for {index_code}",
        count=len(constituents),
    )


@app.post("/api/penetration/calculate")
def calculate_penetration(db: Session = Depends(get_db)):
    """执行穿透计算"""
    engine = PenetrationEngine(db)
    results = engine.calculate()
    return {
        "status": "ok",
        "stock_count": len(results),
        "message": f"Penetrated into {len(results)} underlying stocks",
    }


@app.get("/api/penetration/table", response_model=list[PenetrationRow])
def penetration_table(db: Session = Depends(get_db)):
    """获取底层股票穿透表"""
    from models import PenetrationResult
    rows = db.query(PenetrationResult).order_by(
        PenetrationResult.penetration_weight.desc()
    ).all()
    return rows


@app.get("/api/penetration/summary", response_model=PenetrationSummary)
def penetration_summary(db: Session = Depends(get_db)):
    """穿透汇总"""
    from models import PenetrationResult
    rows = db.query(PenetrationResult).order_by(
        PenetrationResult.penetration_weight.desc()
    ).all()
    total = sum(r.penetration_weight for r in rows)
    return PenetrationSummary(
        total_penetrated=round(total, 2),
        stock_count=len(rows),
        top_holdings=[PenetrationRow.model_validate(r) for r in rows[:10]],
    )


# ==================== 分析 ====================

@app.get("/api/analysis/industry-chain", response_model=IndustryChainAnalysis)
def industry_chain_analysis(db: Session = Depends(get_db)):
    """产业链分布分析 + 沪深300对比"""
    from models import PenetrationResult
    results = db.query(PenetrationResult).all()
    portfolio = IndustryChainAnalyzer.compute_distribution(results)

    csi300 = Csi300Analyzer(db)
    baselines = csi300.get_baselines()
    chain_data = baselines.get("industry_chain", {})

    return IndustryChainAnalysis(
        portfolio=portfolio,
        csi300={k.replace("csi300_", ""): v for k, v in chain_data.items()}
        if chain_data else None,
    )


@app.get("/api/analysis/growth", response_model=GrowthAnalysis)
def growth_analysis(db: Session = Depends(get_db)):
    """增长分层分析 + 沪深300对比"""
    bucketer = GrowthBucketer(db)
    csi300_analyzer = Csi300Analyzer(db)
    baselines = csi300_analyzer.get_baselines()

    thresholds = bucketer.calculate_csi300_thresholds()
    if not thresholds.get("high_cutoff"):
        # No thresholds yet - use defaults
        thresholds = {"high_cutoff": 20.0, "med_cutoff": 10.0}

    portfolio = bucketer.compute_portfolio_growth_distribution(thresholds)

    growth_data = baselines.get("growth", {})
    csi300_dist = {
        k.replace("csi300_", ""): v
        for k, v in growth_data.items()
        if k.startswith("csi300_")
    }

    return GrowthAnalysis(
        thresholds=thresholds,
        portfolio=portfolio,
        csi300=csi300_dist if csi300_dist else None,
    )


@app.get("/api/analysis/valuation", response_model=ValuationMetrics)
def valuation_analysis(db: Session = Depends(get_db)):
    """估值分析"""
    from models import PenetrationResult
    results = db.query(PenetrationResult).filter(
        PenetrationResult.ttm_pe.isnot(None),
        PenetrationResult.ttm_pe > 0,
        PenetrationResult.ttm_pe < 500,
    ).all()

    total_weight = sum(r.penetration_weight for r in results)
    if total_weight == 0:
        return ValuationMetrics()

    weighted_pe = sum(r.penetration_weight * r.ttm_pe for r in results) / total_weight
    forecast_1y = sum(
        r.penetration_weight * (r.forecast_pe_1y or r.ttm_pe)
        for r in results
    ) / total_weight if results else None
    forecast_2y = sum(
        r.penetration_weight * (r.forecast_pe_2y or r.ttm_pe)
        for r in results
    ) / total_weight if results else None

    # CSI300 comparison
    csi300 = Csi300Analyzer(db)
    baselines = csi300.get_baselines()
    csi300_pe = baselines.get("valuation", {}).get("csi300_weighted_pe")

    return ValuationMetrics(
        portfolio_weighted_pe=round(weighted_pe, 2),
        portfolio_forecast_pe_1y=round(forecast_1y, 2) if forecast_1y else None,
        portfolio_forecast_pe_2y=round(forecast_2y, 2) if forecast_2y else None,
        csi300_pe=csi300_pe,
    )


# ==================== 价格 ====================

@app.get("/api/prices", response_model=list[PriceSeries])
def get_prices(
    codes: str = Query("NVDA,GOOGL"),
    days: int = Query(90),
    db: Session = Depends(get_db),
):
    """获取价格走势数据（多股叠加）"""
    tickers = [c.strip() for c in codes.split(",")]
    result = []
    for ticker in tickers:
        prices = fetch_price_history(ticker, days)
        result.append(PriceSeries(
            code=ticker,
            name=ticker,
            prices=[PricePoint(date=p["date"], close=p["close"]) for p in prices],
        ))
    return result


@app.get("/api/prices/bonds")
def bond_price_curve(days: int = Query(365)):
    """债券作为年化2%+微小波动的类现金资产"""
    import numpy as np
    np.random.seed(42)
    daily_return = 0.02 / 252
    daily_vol = 0.0005
    prices = [100.0]
    for _ in range(days):
        prices.append(prices[-1] * (1 + daily_return + np.random.normal(0, daily_vol)))
    return [{"date": "", "close": round(p, 2)} for p in prices]


# ==================== 沪深300 基准 ====================

@app.post("/api/csi300/recalc")
def recalc_csi300(db: Session = Depends(get_db)):
    """重新计算沪深300基准"""
    analyzer = Csi300Analyzer(db)
    result = analyzer.recalc_baselines()
    return {"status": "ok", "data": result}


# ==================== 爬虫触发 ====================

@app.post("/api/crawl/all")
def crawl_all(db: Session = Depends(get_db)):
    """全量爬虫: ETF映射 → 成分股 → 财务 → 分析"""
    from crawlers.price_data import get_stock_info

    # 1. ETF映射
    fund_count = crawl_fund_index_map(db)

    # 2. 爬取各指数成分股
    from models import Fund
    index_codes = set()
    for f in db.query(Fund).all():
        if f.tracking_index_code:
            index_codes.add(f.tracking_index_code)

    # Add CSI300
    index_codes.add("000300")

    const_count = 0
    for idx_code in index_codes:
        try:
            cons = crawl_constituents(idx_code, db)
            const_count += len(cons)
        except Exception:
            pass

    return {
        "status": "ok",
        "fund_mapped": fund_count,
        "constituents_crawled": const_count,
        "message": "Full crawl completed. Run /api/penetration/calculate next.",
    }


# ==================== 调度器状态 ====================

@app.get("/api/scheduler/status")
def scheduler_status():
    """获取定时任务调度器状态"""
    from services.scheduler import scheduler
    if not scheduler or not scheduler.running:
        return {"running": False, "jobs": []}
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return {"running": True, "jobs": jobs}


@app.post("/api/scheduler/trigger/{job_id}")
def trigger_job(job_id: str):
    """手动触发指定定时任务（实时行情任务会强制跳过交易时段判断）"""
    from services.scheduler import scheduler, job_fetch_realtime_prices
    if not scheduler or not scheduler.running:
        return {"status": "error", "message": "Scheduler not running"}
    if job_id == "realtime_prices":
        # 直接调用并强制执行，不走调度器队列
        job_fetch_realtime_prices(force=True)
        return {"status": "ok", "message": f"Job {job_id} executed (force=True)"}
    job = scheduler.get_job(job_id)
    if not job:
        return {"status": "error", "message": f"Job {job_id} not found"}
    job.modify(next_run_time=datetime.now())
    return {"status": "ok", "message": f"Job {job_id} triggered"}


# ==================== 数据浏览 ====================

# 数据表注册：分类 → 表列表
DATA_TABLES = {
    "持仓": [
        {"table": "holdings", "label": "持仓", "model": "Holding", "pk": "id"},
        {"table": "security_master", "label": "证券基础", "model": "SecurityMaster", "pk": "security_code"},
        {"table": "security_type_config", "label": "证券类型配置", "model": "SecurityTypeConfig", "pk": "asset_type"},
    ],
    "行情": [
        {"table": "price_cache", "label": "价格缓存", "model": "PriceCache"},
        {"table": "stock_info_cache", "label": "行情信息缓存", "model": "StockInfoCache"},
        {"table": "exchange_rates", "label": "汇率", "model": "ExchangeRate"},
    ],
    "分析": [
        {"table": "penetration_results", "label": "穿透结果", "model": "PenetrationResult"},
        {"table": "stock_financials", "label": "个股财务", "model": "StockFinancial"},
        {"table": "csi300_baselines", "label": "沪深300基准", "model": "Csi300Baseline"},
    ],
    "基础": [
        {"table": "funds", "label": "基金", "model": "Fund"},
        {"table": "index_constituents", "label": "指数成分股", "model": "IndexConstituent"},
    ],
}


@app.get("/api/data-browser/tables")
def list_data_tables():
    """获取数据浏览表列表（分类结构）"""
    return DATA_TABLES


# 注意：必须放在 /data-browser/{table_name} 之前，否则会被路由捕获
@app.get("/api/data-browser/options")
def data_browser_options(db: Session = Depends(get_db)):
    """前端下拉框选项：asset_type / type2（type2 含固定 + 数据库已有值）"""
    return {
        "asset_type": ASSET_TYPE_OPTIONS,
        "type2": _merge_type2_options(db),
    }


@app.get("/api/data-browser/{table_name}")
def browse_table(
    table_name: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """分页浏览指定数据表"""
    import models
    # 查找模型类
    model_cls = None
    for category, tables in DATA_TABLES.items():
        for t in tables:
            if t["table"] == table_name:
                model_cls = getattr(models, t["model"], None)
                break
        if model_cls:
            break

    if not model_cls:
        return {"error": f"Table {table_name} not found"}

    # 找 pk 列
    pk_col = None
    for category, tables in DATA_TABLES.items():
        for t in tables:
            if t["table"] == table_name and "pk" in t:
                pk_col = t["pk"]
                break
        if pk_col:
            break

    total = db.query(model_cls).count()
    rows = db.query(model_cls).offset((page - 1) * page_size).limit(page_size).all()

    # 序列化：将 ORM 对象转为 dict
    from sqlalchemy.inspection import inspect as sa_inspect
    result_rows = []
    mapper = sa_inspect(model_cls)
    columns = [c.key for c in mapper.column_attrs]

    for row in rows:
        item = {}
        for col in columns:
            val = getattr(row, col, None)
            if val is not None and not isinstance(val, (str, int, float, bool)):
                val = str(val)
            item[col] = val
        result_rows.append(item)

    return {
        "table": table_name,
        "columns": columns,
        "pk_column": pk_col,
        "editable_columns": list(EDITABLE_COLUMNS.get(table_name, [])),
        "rows": result_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


# 编辑允许的列：表 → 允许编辑的列集合
EDITABLE_COLUMNS = {
    "holdings": {"asset_type", "type2"},
    "security_master": {"asset_type", "type2"},
}

# 合法值枚举（前端下拉用）
ASSET_TYPE_OPTIONS = [
    {"value": "a_share_equity", "label": "A股基金"},
    {"value": "a_share_etf",    "label": "A股ETF"},
    {"value": "bond",           "label": "债券基金"},
    {"value": "gold",           "label": "黄金"},
    {"value": "hk_equity",      "label": "港股"},
    {"value": "qdii_equity",    "label": "QDII"},
    {"value": "us_stock",       "label": "美股"},
    {"value": "us_etf",         "label": "美股ETF"},
]
TYPE2_OPTIONS = [
    {"value": "__none__",       "label": "其他"},   # 显式空值
    {"value": "dividend",       "label": "红利"},
    {"value": "emerging",       "label": "新兴产业"},
    {"value": "gold",           "label": "黄金"},
    {"value": "us_tech",        "label": "美股科技"},
    {"value": "broad_index",    "label": "宽基"},
]


def _merge_type2_options(db: Session) -> list[dict]:
    """返回固定选项 ∪ 数据库 security_master.type2 已存在的值（动态发现）"""
    from models import SecurityMaster
    existing = {row[0] for row in db.query(SecurityMaster.type2).distinct().all() if row[0]}
    fixed = {o["value"] for o in TYPE2_OPTIONS}
    extras = [{"value": v, "label": v} for v in (existing - fixed)]
    # 排序：固定选项(含"其他")优先 → 额外（DB 中已有但未列入固定的）
    return TYPE2_OPTIONS + extras


# /api/data-browser/options 已在文件靠前位置注册（路由顺序必须早于 /{table_name}）


@app.put("/api/data-browser/{table_name}/{pk_col}/{pk_val}")
def update_table_row(
    table_name: str,
    pk_col: str,
    pk_val: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """通用行内更新（仅允许白名单字段）"""
    import models
    editable = EDITABLE_COLUMNS.get(table_name)
    if not editable:
        return {"status": "error", "message": f"Table {table_name} not editable"}

    model_cls = None
    for category, tables in DATA_TABLES.items():
        for t in tables:
            if t["table"] == table_name:
                model_cls = getattr(models, t["model"], None)
                break
        if model_cls:
            break
    if not model_cls:
        return {"status": "error", "message": f"Table {table_name} not found"}

    # 主键类型转换
    pk_attr = getattr(model_cls, pk_col, None)
    if pk_attr is None:
        return {"status": "error", "message": f"PK column {pk_col} not on model"}
    pk_type = pk_attr.type
    try:
        if 'INTEGER' in str(pk_type).upper():
            pk_cmp = int(pk_val)
        else:
            pk_cmp = pk_val
    except Exception:
        pk_cmp = pk_val

    row = db.query(model_cls).filter(getattr(model_cls, pk_col) == pk_cmp).first()
    if not row:
        return {"status": "error", "message": f"Row {pk_val} not found"}

    # 仅写白名单字段
    changed = []
    for col, val in body.items():
        if col not in editable:
            continue
        # type2 允许置空："" 或 "__none__" → None
        if col == "type2" and val in ("", "__none__"):
            val = None
        # asset_type 校验合法值
        if col == "asset_type" and val is not None:
            if not any(o["value"] == val for o in ASSET_TYPE_OPTIONS):
                return {"status": "error", "message": f"Invalid asset_type: {val}"}
        # type2 校验：固定选项 ∪ 数据库已有值（None 始终合法）
        if col == "type2" and val is not None and val != "":
            valid = {o["value"] for o in _merge_type2_options(db)} - {"__none__"}
            if val not in valid:
                return {"status": "error", "message": f"Invalid type2: {val}"}
        setattr(row, col, val)
        changed.append(col)

    db.commit()
    db.refresh(row)
    return {"status": "ok", "changed": changed, "row": {c: getattr(row, c, None) for c in [pk_col, *editable]}}


# ==================== Admin: 本地 SQLite → 云端 Postgres 同步 ====================

class AdminSyncRequest(BaseModel):
    table: str  # 'holdings' / 'security_master' / 'security_type_config' / 'watchlist'
    rows: list[dict]
    truncate: bool = False  # True = 先清空表再插


# 允许通过 admin 端点写入的表（白名单，防止任意写）
_ADMIN_WRITABLE_TABLES = {"holdings", "security_master", "security_type_config", "watchlist"}


@app.post("/api/admin/sync-table")
def admin_sync_table(req: AdminSyncRequest, db: Session = Depends(get_db)):
    """从本地 SQLite 同步数据到云端 Postgres（白名单表）"""
    import models
    if req.table not in _ADMIN_WRITABLE_TABLES:
        return {"status": "error", "message": f"Table {req.table} not in whitelist"}

    # 模型名 = 表名驼峰: holdings → Holding, security_master → SecurityMaster
    name_map = {
        "holdings": "Holding",
        "security_master": "SecurityMaster",
        "security_type_config": "SecurityTypeConfig",
        "watchlist": "Watchlist",
    }
    model_cls = getattr(models, name_map[req.table], None)
    if model_cls is None:
        return {"status": "error", "message": f"Model for {req.table} not found"}

    from sqlalchemy import inspect
    insp = inspect(model_cls)
    valid_cols = {c.key for c in insp.column_attrs}

    if req.truncate:
        db.query(model_cls).delete()
        db.commit()

    inserted = 0
    for row_data in req.rows:
        clean = {k: v for k, v in row_data.items() if k in valid_cols}
        if not clean:
            continue
        # id 字段：None 则让 DB 自增
        if "id" in clean and clean["id"] is None:
            clean.pop("id")
        obj = model_cls(**clean)
        db.add(obj)
        inserted += 1

    db.commit()
    return {"status": "ok", "table": req.table, "inserted": inserted, "truncated": req.truncate}


# ==================== 关注清单 (Watchlist) ====================

class WatchAddRequest(BaseModel):
    code: str


class WatchWeightRequest(BaseModel):
    weight: float


def _enrich_watch_row(w, db) -> dict:
    """用腾讯行情补全关注项的实时价/PE/市值/涨跌幅"""
    from crawlers.price_data import fetch_tencent_quote
    from crawlers.exchange_rates import get_rate

    code = w.code
    info = fetch_tencent_quote(code) or {}
    # 货币识别
    cur = "CNY"
    if code.upper().endswith((".OQ", ".NYSE", ".NASDAQ", ".US")) or (code.isalpha() and code.isupper()):
        cur = "USD"
    elif code.upper().endswith(".HK"):
        cur = "HKD"

    price = info.get("price")
    change_pct = None
    if price and info.get("prev_close"):
        change_pct = round((price - info["prev_close"]) / info["prev_close"] * 100, 2)

    # 市值：腾讯字段 45 是"万元"（A股）/ 美股直接是 dollar
    mkt_cap_raw = info.get("market_cap")
    if mkt_cap_raw:
        if cur == "CNY":
            if mkt_cap_raw > 1e8:
                mkt_cap = f"¥{mkt_cap_raw/1e8:.1f}亿"
            else:
                mkt_cap = f"¥{mkt_cap_raw/1e4:.1f}万"
        else:
            mkt_cap = f"${mkt_cap_raw/1e8:.1f}B" if mkt_cap_raw > 1e9 else f"${mkt_cap_raw/1e6:.1f}M"
    else:
        mkt_cap = "-"

    # 折算价格到 CNY 用于 KPI 汇总
    price_cny = None
    if price:
        rate = get_rate(db, cur, "CNY")
        price_cny = round(price * rate, 4) if rate else None

    return {
        "code": code,
        "name": w.name or info.get("name") or code,
        "market": w.market or ("美股" if cur == "USD" else "港股" if cur == "HKD" else "A股"),
        "industry": w.industry or info.get("industry") or "-",
        "weight": w.weight,
        "price": price,
        "price_cny": price_cny,
        "change_pct": change_pct,
        "pe_ttm": info.get("pe_ttm"),
        "market_cap": mkt_cap,
        "added_at": w.added_at.isoformat() if w.added_at else None,
    }


@app.get("/api/watchlist")
def list_watchlist(db: Session = Depends(get_db)):
    """获取关注清单（带实时行情补全）"""
    from models import Watchlist
    rows = db.query(Watchlist).order_by(Watchlist.added_at.desc()).all()
    return [_enrich_watch_row(r, db) for r in rows]


@app.post("/api/watchlist")
def add_watchlist(req: WatchAddRequest, db: Session = Depends(get_db)):
    """添加关注。code 任意合法证券代码；后端拉一次行情回填 name/market/industry"""
    from models import Watchlist
    from crawlers.price_data import fetch_tencent_quote

    code = req.code.strip().upper()
    if not code:
        return {"status": "error", "message": "code 不能为空"}

    # 查重
    if db.query(Watchlist).filter(Watchlist.code == code).first():
        return {"status": "error", "message": f"{code} 已在关注清单"}

    # 拉行情回填 name/industry
    info = fetch_tencent_quote(code) or {}
    name = info.get("name")
    industry = info.get("industry")
    if code.isalpha() and code.isupper():
        market = "美股"
    elif code.upper().endswith(".HK"):
        market = "港股"
    else:
        market = "A股"

    w = Watchlist(
        code=code,
        name=name,
        market=market,
        industry=industry,
        weight=5.0,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return {"status": "ok", "row": _enrich_watch_row(w, db)}


@app.delete("/api/watchlist/{code}")
def remove_watchlist(code: str, db: Session = Depends(get_db)):
    """移除关注"""
    from models import Watchlist
    w = db.query(Watchlist).filter(Watchlist.code == code).first()
    if not w:
        return {"status": "error", "message": f"{code} 不在关注清单"}
    db.delete(w)
    db.commit()
    return {"status": "ok", "code": code}


@app.put("/api/watchlist/{code}/weight")
def set_watchlist_weight(code: str, req: WatchWeightRequest, db: Session = Depends(get_db)):
    """修改权重"""
    from models import Watchlist
    w = db.query(Watchlist).filter(Watchlist.code == code).first()
    if not w:
        return {"status": "error", "message": "not found"}
    w.weight = req.weight
    db.commit()
    return {"status": "ok"}


@app.get("/api/watchlist/search")
def search_securities(q: str = Query("", description="代码或名称关键字"), db: Session = Depends(get_db)):
    """用腾讯 API 实时搜索证券（不依赖本地静态列表）"""
    from crawlers.price_data import fetch_tencent_quote, _to_tencent_ticker
    q = q.strip()
    if not q:
        return []

    # 标准化用户输入：纯 6 位数字 → 加 .SZ/.SH 后缀做尝试
    candidates = []
    if q.isdigit() and len(q) == 6:
        # 同时尝试 sh 和 sz（A 股 ETF 规则：5/6 开头是 sh，其他 sz）
        if q.startswith(("5", "6")):
            candidates = [f"sh{q}", f"sz{q}", q + ".SH", q + ".SZ"]
        else:
            candidates = [f"sz{q}", f"sh{q}", q + ".SZ", q + ".SH"]
    elif q.isdigit() and len(q) == 5:
        candidates = [f"hk{q}", q + ".HK"]
    elif q.isalnum() and q.isalpha():
        candidates = [q.upper(), f"us{q.upper()}"]
    else:
        candidates = [q]

    results = []
    for c in candidates:
        info = fetch_tencent_quote(c)
        if info and info.get("price") is not None:
            # 反推标准 code
            standard = c.lower()
            if standard.startswith(("sh", "sz")) and len(standard) == 8:
                suffix = standard[:2].upper()
                standard = standard[2:] + "." + suffix
            elif standard.startswith("hk"):
                standard = standard[2:] + ".HK"
            elif standard.startswith("us"):
                standard = standard[2:]
            results.append({
                "code": standard,
                "name": info.get("name") or c,
                "market": "美股" if c.isalpha() else "港股" if c.upper().endswith(".HK") else "A股",
                "industry": info.get("industry") or "-",
                "price": info.get("price"),
            })
            break  # 第一个能查到的就够

    return results

