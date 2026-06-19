"""PortfolioM — FastAPI 应用入口"""
import hashlib
import os
import re as _re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Depends, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func
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

# ---- CORS: 显式白名单 (避免通配符在某些浏览器/代理场景下被拒) ----
# 生产前端固定在 portfoliom.zeabur.app; 加上 localhost 让 vite dev/preview 可直连
# 任何 *.zeabur.app 子域 (Zeabur 临时预览域名) 也放行
_DEFAULT_ALLOWED_ORIGINS = [
    "https://portfoliom.zeabur.app",
    "http://localhost:5173",   # vite dev
    "http://localhost:4173",   # vite preview
    "http://127.0.0.1:5173",
    "http://127.0.0.1:4173",
]
_EXTRA_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
_ALLOWED_ORIGINS = _DEFAULT_ALLOWED_ORIGINS + _EXTRA_ORIGINS
_ZEABUR_ORIGIN_RE = _re.compile(r"^https://[a-z0-9-]+\.zeabur\.app$")


def _is_allowed_origin(origin: str | None) -> bool:
    if not origin:
        return False
    if origin in _ALLOWED_ORIGINS:
        return True
    if _ZEABUR_ORIGIN_RE.match(origin):
        return True
    return False


app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,   # 显式列表, 不再 "*"
    allow_origin_regex=r"^https://[a-z0-9-]+\.zeabur\.app$",
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    allow_credentials=False,           # 前端 axios 不发 cookie, 显式 False 避免 wildcard 冲突
    max_age=600,
)

DATA_DIR = Path(__file__).parent.parent


# ==================== 访问密码 + IP 限流 ====================

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
    method = request.method
    # CORS 预检：直接放行（让 CORSMiddleware 响应 OPTIONS）
    if method == "OPTIONS":
        return await call_next(request)
    # admin 端点（需 X-Admin-Token，独立于用户密码）
    if path.startswith("/api/admin/"):
        admin_token = os.environ.get("ADMIN_TOKEN", APP_PASSWORD)
        provided = request.headers.get("x-admin-token")
        if provided != admin_token:
            return _json_error(401, "admin token required", request)
        return await call_next(request)
    # 公开路径
    PUBLIC_PATHS = (
        "/api/auth/", "/api/strategies",
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
            return _json_error(401, "需要登录", request)
    finally:
        db.close()
    return await call_next(request)


def _json_error(status: int, msg: str, request: Request | None = None):
    from fastapi.responses import JSONResponse
    # 显式带 CORS 头（避免 CORSMiddleware 漏包到错误响应时浏览器拒绝跨域）
    # 关键: echo 回请求 Origin (而不是 "*"), 防止浏览器对带 credentials 的请求拒绝
    origin = request.headers.get("origin") if request is not None else None
    allowed_origin = origin if _is_allowed_origin(origin) else "null"
    return JSONResponse(
        status_code=status,
        content={"detail": msg},
        headers={
            "Access-Control-Allow-Origin": allowed_origin,
            "Vary": "Origin",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
            "Access-Control-Allow-Headers": "*",
        },
    )


@app.on_event("startup")
def startup():
    init_db()
    # Print DB URL (password masked) so we can confirm cloud-vs-local DB
    import logging
    from config import DATABASE_URL
    _url = DATABASE_URL
    if "@" in _url:
        # mask password: postgresql://user:pass@host → postgresql://user:***@host
        _scheme_user, _, _host_part = _url.partition("@")
        if ":" in _scheme_user.split("://", 1)[-1]:
            _user, _, _ = _scheme_user.rpartition(":")
            _url_masked = f"{_user}:***@{_host_part}"
        else:
            _url_masked = _url
    else:
        _url_masked = _url
    _kind = "postgres" if "postgres" in DATABASE_URL else ("sqlite" if "sqlite" in DATABASE_URL else "other")
    logging.getLogger(__name__).info(
        "DB connected: kind=%s url=%s",
        _kind, _url_masked,
    )
    from services.scheduler import start_scheduler
    start_scheduler()
    # 初始化交易日历（CN/HK/US 2020-2030），失败不阻塞启动
    try:
        from database import SessionLocal
        from services.trading_calendar import populate_market
        db = SessionLocal()
        try:
            populate_market("CN", 2020, 2030, db)
            populate_market("HK", 2020, 2030, db)
            populate_market("US", 2020, 2030, db)
        finally:
            db.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("calendar init failed (non-fatal): %s", e)
    # 初始化 API 代码映射表（默认规则 + 现有 .OF 持仓），失败不阻塞
    try:
        from database import SessionLocal
        from services.code_map import populate_default_maps
        db = SessionLocal()
        try:
            populate_default_maps(db)
        finally:
            db.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("code_map init failed (non-fatal): %s", e)

    # 启动时自动导入穿透快照（如果当前业务日期对应的 snapshot 表为空）。
    # 失败不阻塞启动（云端网络/权限可能限制 Excel 读取）。
    try:
        from database import SessionLocal as _SL
        from services.data_version import current_business_date, resolve_source_folder
        from models import AShareFinancialSnapshot
        db = _SL()
        try:
            biz = current_business_date()
            if biz:
                have = db.query(AShareFinancialSnapshot).filter(
                    AShareFinancialSnapshot.as_of_date == biz
                ).count()
                if have == 0:
                    folder = resolve_source_folder(biz)
                    if folder and folder.exists():
                        from scripts.import_fund_index_map import import_fund_index_map
                        from scripts.import_index_constituents import import_index_constituents
                        from scripts.import_a_share_financials import import_a_share
                        from scripts.import_hk_share_financials import import_hk_share
                        from services.penetration_v2 import run_penetration as run_pen
                        from services.aggregation import refresh_all_dimensions, write_timeseries_for_day
                        import_fund_index_map(db, biz, folder / "基金-指数.xlsx")
                        import_index_constituents(db, folder / "指数构成.xlsx")
                        import_a_share(db, biz, folder / "全部A股.xlsx")
                        import_hk_share(db, biz, folder / "全部港股.xlsx")
                        # Optional: 399673_cons.xlsx if present
                        cons_399673 = folder / "399673_cons.xlsx"
                        if cons_399673.exists():
                            from scripts.import_399673_cons import import_399673 as imp_399673
                            imp_399673(db, cons_399673)
                        run_pen(db, biz)
                        refresh_all_dimensions(db, biz)
                        write_timeseries_for_day(db, biz, biz)
                        import logging
                        logging.getLogger(__name__).info(
                            "auto-imported snapshots for %s from %s", biz, folder.name
                        )
        finally:
            db.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("snapshot auto-import failed (non-fatal): %s", e)


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

    # 兜底：仅当当日汇率缺失时才拉（update_rates_today 内部有 17s 的 PBoC 接口，频繁调很慢）
    try:
        from crawlers.exchange_rates import update_rates_today
        update_rates_today(db)
    except Exception:
        pass

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
    # 全局最新汇率（最近一天）
    latest_fx: dict = {}
    for r in fx_rows:
        latest_key = (r.from_currency, r.to_currency)
        cur_val = fx_map.get((r.rate_date.isoformat(), r.from_currency, r.to_currency))
        prev = latest_fx.get(latest_key)
        if prev is None or r.rate_date > prev[0]:
            latest_fx[latest_key] = (r.rate_date, cur_val)

    def get_fx(d_iso: str, from_cur: str, to_cur: str) -> float:
        if from_cur == to_cur:
            return 1.0
        # 优先用 daily 历史汇率
        if (d_iso, from_cur, to_cur) in fx_map:
            return fx_map[(d_iso, from_cur, to_cur)]
        # 倒退找最近（最多 7 天）
        try:
            d = date.fromisoformat(d_iso)
        except (ValueError, TypeError):
            d = date.today()
        for k in range(1, 8):
            nd = (d - timedelta(days=k)).isoformat()
            if (nd, from_cur, to_cur) in fx_map:
                return fx_map[(nd, from_cur, to_cur)]
        # 兜底：用最新汇率（云端只有当日 1 条）
        if (from_cur, to_cur) in latest_fx:
            return latest_fx[(from_cur, to_cur)][1]
        return 1.0

    # 4. 找过去 N 天所有有 trade_date 的全部日期（来自任一 holding 的真实价）
    all_dates = sorted({d for code_dates in pc_map.values() for d in code_dates.keys()})
    all_dates = [d for d in all_dates if d >= cutoff.isoformat()]

    # 5. 对每只 holding 构建"已知价 → 该日及以后沿用"映射（不编造：用其最后已知真实价回填未来无价日）
    # 注意：这是"backward-fill last known"，不是 forward-fill 编造。
    # 规则：某 holding 在 D 日无价，则用该 holding 在 D 之前最近的真实价代替。
    #      如果该 holding 整段 90 天都没价，则跳过（不编造）。
    def _resolve_px(code_map: dict, d_iso: str) -> float | None:
        # 真实价优先
        if d_iso in code_map:
            return code_map[d_iso]
        # 找该日之前的最近真实价
        try:
            d = date.fromisoformat(d_iso)
        except (ValueError, TypeError):
            return None
        for k in range(1, days + 5):
            nd = (d - timedelta(days=k)).isoformat()
            if nd in code_map:
                return code_map[nd]
            if (d - timedelta(days=k)) < cutoff:
                break
        return None

    # 6. 跳过整只 holding 在窗口内完全无价的情况（无法计算 → 不编造）
    # 优化：只看 pc_map 里是否有该 code 的任何价格，不再做 O(D×K) 双重 any
    eligible = []
    skipped = []
    for h in rows:
        cm = pc_map.get(h.security_code, {})
        if cm:
            eligible.append(h)
        else:
            skipped.append(h.security_code)

    # 7. 对每个日期算总值（使用 last-known backward-fill，不是 forward-fill 编造）
    from services.trading_calendar import is_trading_day
    # 预计算每个日期的 is_trading（避免 255 日 × 3 市场 = 765 次 DB 调用）
    is_td_cache: dict[str, bool] = {}
    for d_iso in all_dates:
        try:
            d_obj = date.fromisoformat(d_iso)
            is_td_cache[d_iso] = (
                is_trading_day("CN", d_obj, db)
                or is_trading_day("HK", d_obj, db)
                or is_trading_day("US", d_obj, db)
            )
        except Exception:
            is_td_cache[d_iso] = True

    series = []
    for d_iso in all_dates:
        total = 0.0
        for h in eligible:
            cm = pc_map.get(h.security_code, {})
            px = _resolve_px(cm, d_iso)
            if px is None:
                continue  # 该 holding 还未"上市"（早于其最早有价日）— 跳过
            cur = h.currency or "CNY"
            fx = get_fx(d_iso, cur, target)
            total += (h.quantity or 0) * px * fx
        series.append({"date": d_iso, "value": round(total, 2), "is_trading": is_td_cache.get(d_iso, True)})

    return {
        "series": series,
        "currency": target,
        "days": days,
        "eligible_holdings": len(eligible),
        "skipped_holdings": skipped,
        "note": "每点 = Σ(quantity × 该日或更早真实价 × 汇率)；无未来编造",
    }


@app.post("/api/admin/backfill-gaps")
def admin_backfill_gaps(days: int = 90):
    """手动触发 90 天历史价完整性检查 + 补缺任务"""
    from services.scheduler import job_backfill_gaps
    return job_backfill_gaps(days)


@app.get("/api/admin/db-info")
def admin_db_info():
    """诊断: 当前连接的 DB 类型/版本 + 主要表 row counts"""
    from sqlalchemy import text, inspect
    from config import DATABASE_URL
    from database import SessionLocal
    info = {
        "database_url_kind": "postgres" if "postgres" in DATABASE_URL else ("sqlite" if "sqlite" in DATABASE_URL else "other"),
        "database_url_masked": None,
        "server_version": None,
        "tables": {},
        "total_rows": 0,
    }
    # mask password
    if "@" in DATABASE_URL:
        scheme_user, _, host_part = DATABASE_URL.partition("@")
        if ":" in scheme_user.split("://", 1)[-1]:
            user, _, _ = scheme_user.rpartition(":")
            info["database_url_masked"] = f"{user}:***@{host_part}"
        else:
            info["database_url_masked"] = DATABASE_URL
    else:
        info["database_url_masked"] = DATABASE_URL

    try:
        db = SessionLocal()
        try:
            # server version
            if info["database_url_kind"] == "postgres":
                row = db.execute(text("SELECT version()")).fetchone()
                info["server_version"] = row[0] if row else None
            elif info["database_url_kind"] == "sqlite":
                row = db.execute(text("SELECT sqlite_version()")).fetchone()
                info["server_version"] = f"sqlite {row[0]}" if row else None

            # table row counts
            insp = inspect(db.get_bind())
            for tbl in insp.get_table_names():
                try:
                    cnt = db.execute(text(f'SELECT COUNT(*) FROM "{tbl}"')).scalar()
                    info["tables"][tbl] = cnt
                    info["total_rows"] += cnt or 0
                except Exception as e:
                    info["tables"][tbl] = f"err: {e}"
        finally:
            db.close()
    except Exception as e:
        info["error"] = str(e)
    return info


# ==================== 交易日历 ====================

@app.get("/api/calendar")
def calendar_range(
    market: str = Query("CN"),
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """区间查询某市场的开/休市状态（自动惰性补齐缺失日期）"""
    from services.trading_calendar import get_range
    from datetime import date as date_cls
    try:
        s = date_cls.fromisoformat(start)
        e = date_cls.fromisoformat(end)
    except ValueError:
        return {"error": "start/end must be YYYY-MM-DD"}
    return {"market": market, "start": start, "end": end, "days": get_range(market, s, e, db)}


@app.get("/api/calendar/is-trading")
def calendar_is_trading(
    market: str = Query("CN"),
    date: str = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """单日判断"""
    from services.trading_calendar import is_trading_day
    from datetime import date as date_cls
    try:
        d = date_cls.fromisoformat(date)
    except ValueError:
        return {"error": "date must be YYYY-MM-DD"}
    is_t = is_trading_day(market, d, db)
    return {"market": market, "date": date, "is_trading": is_t}


@app.get("/api/calendar/month")
def calendar_month(
    market: str = Query("CN"),
    year: int = Query(2026),
    month: int = Query(1, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """取整月日历（6×7 网格用）+ 汇总"""
    from services.trading_calendar import get_month
    return get_month(market, year, month, db)


@app.get("/api/calendar/summary")
def calendar_summary(
    market: str = Query("CN"),
    year: int = Query(2026),
    db: Session = Depends(get_db),
):
    """全年汇总：交易日 / 节假日 / 周末"""
    from services.trading_calendar import get_range
    from datetime import date as date_cls
    start = date_cls(year, 1, 1)
    end = date_cls(year, 12, 31)
    rows = get_range(market, start, end, db)
    trading = sum(1 for r in rows if r["is_trading"])
    holiday = sum(1 for r in rows if not r["is_trading"] and date_cls.fromisoformat(r["date"]).weekday() < 5)
    weekend = sum(1 for r in rows if date_cls.fromisoformat(r["date"]).weekday() >= 5)
    return {"market": market, "year": year, "trading": trading, "holiday": holiday, "weekend": weekend, "total": len(rows)}


# ==================== API 代码映射表 ====================

@app.get("/api/code-map")
def code_map_list(
    api: str | None = Query(None, description="按 API 策略过滤（可选）"),
    db: Session = Depends(get_db),
):
    """列出所有代码映射。可选 ?api=tencent_kline 过滤。"""
    from services.code_map import list_maps
    rows = list_maps(db, api_strategy=api)
    return {"count": len(rows), "items": rows}


class CodeMapUpsert(BaseModel):
    code_in: str
    api_strategy: str
    code_out: str
    market: str | None = None
    note: str | None = None


@app.post("/api/code-map")
def code_map_upsert(body: CodeMapUpsert, db: Session = Depends(get_db)):
    """新增 / 更新一条代码映射。"""
    from services.code_map import upsert_map
    return upsert_map(db, body.code_in, body.api_strategy, body.code_out, body.market, body.note)


@app.delete("/api/code-map/{code_in}/{api_strategy}")
def code_map_delete(code_in: str, api_strategy: str, db: Session = Depends(get_db)):
    """删除一条代码映射。"""
    from services.code_map import delete_map
    ok = delete_map(db, code_in, api_strategy)
    if not ok:
        return {"status": "error", "message": "not found"}
    return {"status": "ok"}


@app.post("/api/admin/init-code-map")
def admin_init_code_map(db: Session = Depends(get_db)):
    """手动触发：重新初始化默认代码映射（保留已有，覆盖默认集合）。"""
    from services.code_map import populate_default_maps
    n = populate_default_maps(db)
    return {"status": "ok", "new_rows": n}


@app.post("/api/admin/backfill-prices")
def admin_backfill_prices(days: int = 90, db: Session = Depends(get_db)):
    """拉所有 holding 过去 N 天 daily price，写入 price_cache。
    来源：腾讯 K 线（A 股/港股/美股 ETF）+ akshare 净值走势（OF 基金）。
    只插真实数据；不编造。"""
    from datetime import date, timedelta
    from models import Holding, PriceCache

    try:
        from crawlers.price_data import fetch_price_history
        from services.importer import fetch_fund_nav_history
    except ImportError as e:
        return {"status": "error", "message": f"import failed: {e}"}

    try:
        holdings = db.query(Holding).all()
        results = []
        cutoff = date.today() - timedelta(days=days)

        # 日历过滤：按 holding 所属市场决定是否接受该日期
        from services.trading_calendar import is_trading_day, _market_for_code

        for h in holdings:
            code = h.security_code
            is_fund = code.endswith(".OF")
            market = _market_for_code(code)

            try:
                if is_fund:
                    history = fetch_fund_nav_history(code.replace(".OF", ""), days)
                else:
                    history = fetch_price_history(code, days)
            except Exception as e:
                results.append({"code": code, "status": "fetch_error", "error": str(e)[:200]})
                continue
            if not history:
                results.append({"code": code, "status": "no_data"})
                continue
            written = 0
            for p in history:
                try:
                    d = date.fromisoformat(p["date"])
                except (ValueError, TypeError):
                    continue
                if d < cutoff:
                    continue
                # 日历过滤：非交易日不写入（OF 基金的 akshare 数据落库时通过 is_trading_day 自身惰性持久化）
                try:
                    if not is_trading_day(market, d, db):
                        continue
                except Exception:
                    pass  # 日历失败不阻塞写入
                try:
                    exists = db.query(PriceCache).filter(
                        PriceCache.stock_code == code,
                        PriceCache.trade_date == d,
                    ).first()
                except Exception:
                    exists = None
                if exists:
                    continue
                try:
                    db.add(PriceCache(
                        stock_code=code,
                        trade_date=d,
                        open_px=p.get("open"),
                        close_px=p.get("close"),
                        high_px=p.get("high"),
                        low_px=p.get("low"),
                        volume=p.get("volume"),
                        source="akshare_fund" if is_fund else "tencent",
                    ))
                    written += 1
                except Exception as e:
                    continue
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                results.append({"code": code, "status": "commit_error", "error": str(e)[:200]})
                continue
            results.append({"code": code, "status": "ok", "rows": written})

        total_pc = db.query(PriceCache).count()
        return {
            "status": "ok",
            "holdings_processed": len(holdings),
            "total_price_cache_rows": total_pc,
            "details": results,
        }
    except Exception as e:
        import traceback
        return {"status": "error", "message": str(e)[:500], "trace": traceback.format_exc()[-500:]}


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


# ==================== API 策略 ====================

def _scan_data_sources() -> dict:
    """扫描 backend/crawlers/ + services/ 中所有 fetch_/crawl_ 函数，作为策略页面 live hook。"""
    import ast
    from pathlib import Path

    sources = []
    backend_dir = Path(__file__).parent
    for sub in ("crawlers", "services"):
        d = backend_dir / sub
        if not d.exists():
            continue
        for py in d.glob("*.py"):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                name = node.name
                if not (name.startswith("fetch_") or name.startswith("crawl_") or name == "fetch_tencent_quote"):
                    continue
                # 提取 docstring 前 1 行
                doc = ast.get_docstring(node) or ""
                short = doc.split("\n")[0].strip() if doc else ""
                sources.append({
                    "function": name,
                    "file": f"backend/{sub}/{py.name}",
                    "line": node.lineno,
                    "doc": short[:120],
                })
    return {"scanned_at": datetime.utcnow().isoformat(), "total": len(sources), "sources": sources}


@app.get("/api/strategies")
def list_strategies():
    """列出所有数据源策略（从 api_strategies.json + 实时扫描代码）"""
    from pathlib import Path
    import json as _json
    p = Path(__file__).parent / "api_strategies.json"
    manifest = {"strategies": []}
    if p.exists():
        try:
            manifest = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "manifest": manifest,
        "live": _scan_data_sources(),
    }


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


# ============================================================================
# Fund Penetration & Industry Aggregation API (spec §4.1)
# ============================================================================

from services.data_version import (
    current_business_date,
    list_available_versions,
    resolve_source_folder,
)
from services.penetration_v2 import run_penetration as run_penetration_v2
from services.aggregation import (
    aggregate_dimension,
    upsert_dimension,
    write_timeseries_for_day,
)


@app.get("/api/data-version")
def get_data_version(db: Session = Depends(get_db)):
    """当前活跃业务日期 + 各市场最新股价日期 + 历史可用版本。"""
    biz = current_business_date()
    versions = list_available_versions()
    # Latest price date per market from snapshots (best-effort)
    from models import AShareFinancialSnapshot, HKShareFinancialSnapshot, PriceCache
    a_latest = db.query(func.max(AShareFinancialSnapshot.current_price_date)).scalar()
    hk_latest = db.query(func.max(HKShareFinancialSnapshot.current_price_date)).scalar()
    us_latest = db.query(func.max(PriceCache.trade_date)).filter(
        PriceCache.stock_code.like("%.OQ"),
    ).scalar() or db.query(func.max(PriceCache.trade_date)).filter(
        PriceCache.stock_code.in_(["NVDA", "GOOGL"]),
    ).scalar()
    return {
        "current_business_date": biz.isoformat() if biz else None,
        "available_versions": [
            {"as_of_date": v.as_of_date.isoformat(), "source_folder": v.source_folder,
             "imported_at": v.imported_at, "note": v.note}
            for v in versions
        ],
        "price_dates": {
            "CN": a_latest.isoformat() if a_latest else None,
            "HK": hk_latest.isoformat() if hk_latest else None,
            "US": us_latest.isoformat() if us_latest else None,
        },
    }


def _resolve_market_value(stock_code: str, amount_cny: float, snap,
                          current_price: float | None,
                          baseline_price: float | None):
    """Compute shares / est_market_value / deviation based on prices.

    For drilled_fund rows: amount_cny is already the *dynamic* amount
    (weight × original_amount × current/baseline). So:
      shares = amount_static / baseline_price
      est_market_value_at_current = shares × current_price = amount_dynamic
      deviation = amount_dynamic - amount_static

    For direct_stock rows: amount_cny is the import-time amount.
      shares = amount_cny / baseline_price (if baseline known)
      est_market_value_at_current = shares × current_price
      deviation = est_market_value_at_current - amount_cny

    For undrilled_fund / cash: no prices, return zeros.
    """
    if not snap or not baseline_price or baseline_price <= 0:
        return None, amount_cny, 0.0
    shares = amount_cny / baseline_price
    if current_price and current_price > 0:
        est_value = shares * current_price
    else:
        est_value = amount_cny
    deviation_pct = ((est_value - amount_cny) / amount_cny * 100) if amount_cny else 0.0
    return round(shares, 2), round(est_value, 4), round(deviation_pct, 4)


def _pct_change_3m(stock_code: str, current_price: float | None, db) -> float | None:
    """Compute 3-month price change using price_cache.

    current_price is the latest known close. price_3m_ago is the latest close
    on or before (today - 90 calendar days).
    """
    if not current_price:
        return None
    from datetime import timedelta
    from models import PriceCache
    target = date.today() - timedelta(days=90)
    row = (
        db.query(PriceCache)
        .filter(PriceCache.stock_code == stock_code)
        .filter(PriceCache.trade_date <= target)
        .filter(PriceCache.close_px.isnot(None))
        .order_by(PriceCache.trade_date.desc())
        .first()
    )
    if not row or not row.close_px or row.close_px <= 0:
        return None
    pct = (current_price - row.close_px) / row.close_px * 100
    if pct != pct or pct in (float('inf'), float('-inf')):
        return None
    return round(pct, 2)


@app.get("/api/penetration/full-holding")
def get_full_holding(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """返回 full_holding_snapshot 全量（下钻基金 + 直接股票 + 不下钻基金 + 现金）。

    - 同代码的证券已合并为单行（amount 求和，PE/PB/PS 取首个非空）。
    - *_dynamic 缺失时回退到 baseline (5/29)。
    - 估算市值 = 股数 × 上一交易日收盘价（shares = amount / baseline_price）。
    - 估算偏差% = (估算市值 - 持仓金额) / 持仓金额。
    - 3月涨跌% = (current_price - 90天前收盘价) / 90天前收盘价 × 100。
    """
    from models import FullHoldingSnapshot, AShareFinancialSnapshot, HKShareFinancialSnapshot
    rows = db.query(FullHoldingSnapshot).filter(FullHoldingSnapshot.as_of_date == as_of_date).all()

    # Build dual indexes: a_snap is keyed by BOTH the raw code (e.g. "2104")
    # AND the suffixed code (e.g. "002104"). Same for h_snap with HK padding.
    # This handles the case where full_holding uses raw integer codes (from
    # index constituents) while snapshots use suffixed codes (from Excel).
    def _index_a_snap(db, as_of_date):
        idx = {}
        for a in db.query(AShareFinancialSnapshot).filter(AShareFinancialSnapshot.as_of_date == as_of_date).all():
            norm = a.stock_code.split(".")[0]
            idx[norm] = a
            idx[a.stock_code] = a
            if norm.isdigit() and len(norm) == 6:
                # also index the unpadded last-N-digits so 4-digit constituent codes match
                idx[norm.lstrip("0")] = a
        return idx

    def _index_h_snap(db, as_of_date):
        idx = {}
        for h in db.query(HKShareFinancialSnapshot).filter(HKShareFinancialSnapshot.as_of_date == as_of_date).all():
            norm = h.stock_code.split(".")[0]
            idx[norm] = h
            idx[h.stock_code] = h
            if norm.isdigit():
                # Pad unpadded (4-digit → 5-digit) and vice versa
                if len(norm) <= 5:
                    idx[norm.zfill(5)] = h
                idx[norm.lstrip("0")] = h
        return idx

    a_snap = _index_a_snap(db, as_of_date)
    h_snap = _index_h_snap(db, as_of_date)

    from models import Holding
    static_by_holding = {}
    for h in db.query(Holding).all():
        static_by_holding[h.security_code] = static_by_holding.get(h.security_code, 0) + (h.amount_cny or 0)

    out = []
    for r in rows:
        # Try multiple lookup keys:
        #   1. r.stock_code (full, possibly with suffix)
        #   2. norm = suffix-stripped
        #   3. For HK: norm.zfill(5) (constituents use 4-digit, snapshot uses 5-digit)
        #   4. For A-share: norm.zfill(6) (constituents use raw 4-digit, snapshot uses 6-digit)
        norm = r.stock_code.split(".")[0]
        keys = [r.stock_code, norm]
        if norm.isdigit():
            keys.append(norm.zfill(5))
            keys.append(norm.zfill(6))
        snap = None
        for k in keys:
            snap = a_snap.get(k) or h_snap.get(k)
            if snap:
                break
        # Priority: snap dynamic (recomputed from current price) > snap baseline > null
        if snap:
            pe_v = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
            pb_v = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
            ps_v = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
            basis = "dynamic" if snap.pe_ttm_dynamic is not None else "baseline_5_29"
        else:
            pe_v = r.pe_ttm_dynamic
            pb_v = r.pb_mrq_dynamic
            ps_v = r.ps_ttm_dynamic
            basis = "dynamic"

        baseline_price = snap.baseline_price if snap else None
        current_price = snap.current_price if snap else None
        current_price_date = snap.current_price_date if snap else None

        if r.source_type == "drilled_fund":
            static_amount = static_by_holding.get(r.source_holding_code, r.amount_cny)
        else:
            static_amount = r.amount_cny
        shares, est_value, dev_pct = _resolve_market_value(
            r.stock_code, static_amount, snap, current_price, baseline_price
        )
        pct_3m = _pct_change_3m(r.stock_code, current_price, db)

        out.append({
            "stock_code": r.stock_code,
            "stock_name": r.stock_name,
            "source_type": r.source_type,
            "source_holding_code": r.source_holding_code,
            "amount_cny": r.amount_cny,
            "static_amount_cny": static_amount,
            "shares": shares,
            "baseline_price": baseline_price,
            "current_price": current_price,
            "current_price_date": current_price_date.isoformat() if current_price_date else None,
            "est_market_value_cny": est_value,
            "est_deviation_pct": dev_pct,
            "pct_change_3m": pct_3m,
            "industry_l1": r.industry_l1,
            "industry_l2": r.industry_l2,
            "chain_position": r.chain_position,
            "growth_tier": r.growth_tier,
            "competition": r.competition,
            "pe_ttm_dynamic": pe_v,
            "pb_mrq_dynamic": pb_v,
            "ps_ttm_dynamic": ps_v,
            "dividend_yield": snap.dividend_yield if snap else None,
            "eps_fy1": r.eps_fy1,
            "metric_basis": basis,
        })
    return out


@app.get("/api/penetration/dimension")
def get_dimension(
    dim: str = Query(..., regex="^(swy1|swy2|swy3|swy4|csi1|csi2|csi3|csi4|se1|se2|se3|se4|l1|l2|chain|growth_tier|competition)$"),
    as_of_date: date = Query(...),
    market: str = Query("A+H", regex="^(A\\+H|A|H)$"),
    db: Session = Depends(get_db),
):
    """统一维度聚合（组合 vs CSI300）。

    支持 9 套行业系统 (申万 L1-L4 + 中证 L1-L4 + 战略新兴 L1-L4)
    + 链位置 / 增长分层 / 竞争格局。
    market=A+H (全部) / A (A 股) / H (港股)。
    """
    from models import AggregationCache
    portfolio = aggregate_dimension(db, as_of_date, "portfolio", dim, market=market)
    csi300 = aggregate_dimension(db, as_of_date, "csi300", dim)
    return {
        "as_of_date": as_of_date.isoformat(),
        "dimension": dim,
        "market": market,
        "portfolio": [
            {
                "key": r.key,
                "stock_count": r.stock_count,
                "amount_cny": r.amount_cny,
                "weight_pct": r.weight_pct,
                "virtual_earnings": r.virtual_earnings,
                "pe_weighted": r.pe_weighted,
                "pb_weighted": r.pb_weighted,
                "ps_weighted": r.ps_weighted,
            }
            for r in portfolio if r.key != "_total"
        ],
        "csi300": [
            {
                "key": r.key,
                "stock_count": r.stock_count,
                "weight_pct": r.weight_pct,
                "pe_weighted": r.pe_weighted,
                "pb_weighted": r.pb_weighted,
                "ps_weighted": r.ps_weighted,
            }
            for r in csi300 if r.key != "_total"
        ],
        "totals": {
            "portfolio": _agg_total(portfolio),
            "csi300": _agg_total(csi300),
        },
    }


def _agg_total(rows):
    for r in rows:
        if r.key == "_total":
            return {
                "stock_count": r.stock_count,
                "amount_cny": r.amount_cny,
                "pe_weighted": r.pe_weighted,
                "pb_weighted": r.pb_weighted,
                "ps_weighted": r.ps_weighted,
            }
    return None


@app.get("/api/penetration/dimension-detail")
def get_dimension_detail(
    dim: str = Query(..., regex="^(swy1|swy2|swy3|swy4|csi1|csi2|csi3|csi4|se1|se2|se3|se4|l1|l2|chain|growth_tier|competition)$"),
    key: str = Query(...),
    as_of_date: date = Query(...),
    market: str = Query("A+H", regex="^(A\\+H|A|H)$"),
    db: Session = Depends(get_db),
):
    """下钻明细：某维度 key 下的每只股票。"""
    from models import FullHoldingSnapshot, Csi300ConstituentSnapshot
    from sqlalchemy import func
    DIM_COL = {
        "swy1": "swy_l1", "swy2": "swy_l2", "swy3": "swy_l3", "swy4": "swy_l4",
        "csi1": "csi_l1", "csi2": "csi_l2", "csi3": "csi_l3", "csi4": "csi_l4",
        "se1": "se_l1", "se2": "se_l2", "se3": "se_l3", "se4": "se_l4",
        "l1": "swy_l1", "l2": "swy_l2",
        "chain": "chain_position", "growth_tier": "growth_tier", "competition": "competition",
    }
    col = DIM_COL[dim]
    q = db.query(FullHoldingSnapshot).filter(
        FullHoldingSnapshot.as_of_date == as_of_date,
        getattr(FullHoldingSnapshot, col) == key,
    )
    if market == "A":
        q = q.filter(
            (FullHoldingSnapshot.stock_code.like("%.SH")) |
            (FullHoldingSnapshot.stock_code.like("%.SZ"))
        )
    elif market == "H":
        q = q.filter(FullHoldingSnapshot.stock_code.like("%.HK"))
    raw = q.all()
    by_stock: dict[str, dict] = {}
    for r in raw:
        s = by_stock.setdefault(r.stock_code, {
            "stock_code": r.stock_code, "stock_name": r.stock_name,
            "amount_cny": 0.0, "pe_ttm_dynamic": None,
            "pb_mrq_dynamic": None, "ps_ttm_dynamic": None,
            "industry_l2": getattr(r, "swy_l2", None) or getattr(r, "csi_l2", None) or getattr(r, "se_l2", None),
            "chain_position": r.chain_position,
            "source_funds": set(), "is_direct": False,
        })
        s["amount_cny"] += (r.amount_cny or 0.0)
        if s["pe_ttm_dynamic"] is None and r.pe_ttm_dynamic is not None:
            s["pe_ttm_dynamic"] = r.pe_ttm_dynamic
        if s["pb_mrq_dynamic"] is None and r.pb_mrq_dynamic is not None:
            s["pb_mrq_dynamic"] = r.pb_mrq_dynamic
        if s["ps_ttm_dynamic"] is None and r.ps_ttm_dynamic is not None:
            s["ps_ttm_dynamic"] = r.ps_ttm_dynamic
        if r.source_type == "direct_stock":
            s["is_direct"] = True
        elif r.source_holding_code:
            s["source_funds"].add(r.source_holding_code)
    out = []
    for s in by_stock.values():
        s["source_funds"] = sorted(s["source_funds"])
        out.append(s)
    out.sort(key=lambda x: x["amount_cny"] or 0, reverse=True)
    return {"as_of_date": as_of_date.isoformat(), "dimension": dim, "key": key, "market": market, "stocks": out}


def _is_hk_code(stock_code: str) -> bool:
    return stock_code.upper().endswith(".HK")


def _portfolio_scope_totals(db: Session, as_of_date: date, market: str):
    """Compute virtual-earnings totals for portfolio over a market scope.

    market: 'A+H' (all stocks), 'A' (A-share only), 'H' (HK only)
    Returns: { stock_count, total_amount, weighted_pe, weighted_pb, weighted_ps,
              weighted_eps_fy1, virt_pe, virt_pb, virt_ps, sum_eps_weighted }
    """
    from sqlalchemy import func as sa_func
    from models import FullHoldingSnapshot, AShareFinancialSnapshot, HKShareFinancialSnapshot

    q = db.query(
        FullHoldingSnapshot.stock_code,
        FullHoldingSnapshot.swy_l1,
        FullHoldingSnapshot.swy_l2,
        FullHoldingSnapshot.swy_l3,
        FullHoldingSnapshot.csi_l1,
        FullHoldingSnapshot.csi_l2,
        FullHoldingSnapshot.csi_l3,
        FullHoldingSnapshot.csi_l4,
        sa_func.sum(FullHoldingSnapshot.amount_cny).label("amount"),
        sa_func.max(FullHoldingSnapshot.pe_ttm_dynamic).label("pe_d"),
        sa_func.max(FullHoldingSnapshot.pb_mrq_dynamic).label("pb_d"),
        sa_func.max(FullHoldingSnapshot.ps_ttm_dynamic).label("ps_d"),
        sa_func.max(FullHoldingSnapshot.eps_fy1).label("eps"),
    ).filter(
        FullHoldingSnapshot.as_of_date == as_of_date,
        FullHoldingSnapshot.source_type.in_(("drilled_fund", "direct_stock")),
    ).group_by(
        FullHoldingSnapshot.stock_code,
        FullHoldingSnapshot.swy_l1, FullHoldingSnapshot.swy_l2, FullHoldingSnapshot.swy_l3,
        FullHoldingSnapshot.csi_l1, FullHoldingSnapshot.csi_l2, FullHoldingSnapshot.csi_l3, FullHoldingSnapshot.csi_l4,
    )

    a_snap = {a.stock_code.split(".")[0]: a for a in
              db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap = {h.stock_code.split(".")[0]: h for h in
              db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    for code, snap in list(a_snap.items()):
        if snap.stock_code.endswith(".SZ"):
            a_snap[code] = snap
        elif snap.stock_code.endswith(".SH"):
            a_snap[code] = snap

    total_amount = 0.0
    virt_pe = virt_pb = virt_ps = 0.0
    sum_dy_weighted = 0.0
    stock_count = 0
    breakdown = []  # for click-to-expand: top stocks contributing
    for r in q.all():
        code_norm = r.stock_code.split(".")[0]
        is_hk = _is_hk_code(r.stock_code) or (r.csi_l1 and r.csi_l1 != "其他")
        # More reliable HK detection: try snap lookup
        snap = None
        if code_norm in h_snap:
            snap = h_snap[code_norm]
            is_hk = True
        elif code_norm in a_snap:
            snap = a_snap[code_norm]
            is_hk = False
        # Fallback by suffix
        if snap is None:
            if r.stock_code.upper().endswith(".HK"):
                is_hk = True
            elif r.stock_code.upper().endswith((".SH", ".SZ")):
                is_hk = False
        if market == "A" and is_hk:
            continue
        if market == "H" and not is_hk:
            continue

        amt = r.amount or 0.0
        total_amount += amt
        stock_count += 1
        pe_d = r.pe_d
        pb_d = r.pb_d
        ps_d = r.ps_d
        dy = None
        # Fallback to snap if dynamic fields null
        if (pe_d is None or pb_d is None or ps_d is None or dy is None) and snap:
            if pe_d is None:
                pe_d = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
            if pb_d is None:
                pb_d = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
            if ps_d is None:
                ps_d = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
            dy = snap.dividend_yield
        if amt > 0:
            if pe_d and pe_d > 0:
                virt_pe += amt / pe_d
                breakdown.append({"stock": r.stock_code, "amount": amt, "pe": pe_d, "amt_pe": amt / pe_d})
            if pb_d and pb_d > 0:
                virt_pb += amt / pb_d
            if ps_d and ps_d > 0:
                virt_ps += amt / ps_d
            if dy is not None:
                sum_dy_weighted += amt * dy

    # Top 30 contributors to PE (for click-to-expand panel)
    breakdown.sort(key=lambda x: x["amt_pe"], reverse=True)
    breakdown = breakdown[:30]

    return {
        "stock_count": stock_count,
        "total_amount_cny": round(total_amount, 4),
        # PE = amount / (amount/PE) = total / virt_pe (per-share inversion of E/P)
        "weighted_pe": round(total_amount / virt_pe, 4) if virt_pe else None,
        "weighted_pb": round(total_amount / virt_pb, 4) if virt_pb else None,
        "weighted_ps": round(total_amount / virt_ps, 4) if virt_ps else None,
        # 股息率 is a direct amount-weighted average
        "weighted_dividend_yield": round(sum_dy_weighted / total_amount, 4) if total_amount else None,
        "virtual_earnings": round(virt_pe, 4),
        "top_pe_contributors": breakdown,
    }


def _csi300_scope_totals(db: Session, as_of_date: date):
    """Compute CSI300 virtual-earnings totals using 5/29 weight × price ratio as amount.

    Returns: { stock_count, total_amount, weighted_pe, weighted_pb, weighted_ps,
              weighted_eps_fy1 }
    """
    from sqlalchemy import func as sa_func
    from models import Csi300ConstituentSnapshot, AShareFinancialSnapshot, HKShareFinancialSnapshot

    a_snap = {a.stock_code.split(".")[0]: a for a in
              db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap = {h.stock_code.split(".")[0]: h for h in
              db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}

    rows = db.query(
        Csi300ConstituentSnapshot.stock_code,
        sa_func.max(Csi300ConstituentSnapshot.weight).label("weight"),
        sa_func.max(Csi300ConstituentSnapshot.pe_ttm_dynamic).label("pe_d"),
        sa_func.max(Csi300ConstituentSnapshot.pb_mrq_dynamic).label("pb_d"),
        sa_func.max(Csi300ConstituentSnapshot.ps_ttm_dynamic).label("ps_d"),
    ).filter(Csi300ConstituentSnapshot.as_of_date == as_of_date).group_by(
        Csi300ConstituentSnapshot.stock_code).all()

    total = 0.0
    virt_pe = virt_pb = virt_ps = 0.0
    sum_dy_weighted = 0.0
    count = 0
    breakdown = []
    for r in rows:
        code_norm = r.stock_code.split(".")[0]
        weight = r.weight or 0.0
        # Price-adjusted weight
        snap = a_snap.get(code_norm) or h_snap.get(code_norm)
        if not snap and code_norm.isdigit():
            snap = h_snap.get(code_norm.zfill(5))
        if snap and snap.baseline_price and snap.current_price and snap.baseline_price > 0:
            weight = weight * (snap.current_price / snap.baseline_price)
        pe_d = r.pe_d
        pb_d = r.pb_d
        ps_d = r.ps_d
        if (pe_d is None or pb_d is None or ps_d is None) and snap:
            if pe_d is None:
                pe_d = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
            if pb_d is None:
                pb_d = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
            if ps_d is None:
                ps_d = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
        total += weight
        count += 1
        if weight > 0:
            if pe_d and pe_d > 0:
                virt_pe += weight / pe_d
                breakdown.append({"stock": r.stock_code, "amount": weight, "pe": pe_d, "amt_pe": weight / pe_d})
            if pb_d and pb_d > 0:
                virt_pb += weight / pb_d
            if ps_d and ps_d > 0:
                virt_ps += weight / ps_d
            if snap and snap.dividend_yield is not None:
                sum_dy_weighted += weight * snap.dividend_yield

    breakdown.sort(key=lambda x: x["amt_pe"], reverse=True)
    breakdown = breakdown[:30]

    return {
        "stock_count": count,
        "total_amount_cny": round(total, 4),  # weight sum (normalized to ~100)
        "weighted_pe": round(total / virt_pe, 4) if virt_pe else None,
        "weighted_pb": round(total / virt_pb, 4) if virt_pb else None,
        "weighted_ps": round(total / virt_ps, 4) if virt_ps else None,
        "weighted_dividend_yield": round(sum_dy_weighted / total, 4) if total else None,
        "virtual_earnings": round(virt_pe, 4),
        "top_pe_contributors": breakdown,
    }


@app.get("/api/penetration/portfolio-vs-csi300")
def get_portfolio_vs_csi300(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """A+H / A / H / CSI300 四套口径的聚合指标，使用虚拟盈利法。

    portfolio 金额是 CNY；CSI300 是 5/29 weight × price-adjusted（归一化比例）。
    每个口径：股票数 / 总金额 / weighted PE / PB / PS / EPS_FY1。
    """
    return {
        "as_of_date": as_of_date.isoformat(),
        "ah": _portfolio_scope_totals(db, as_of_date, "A+H"),
        "a_only": _portfolio_scope_totals(db, as_of_date, "A"),
        "h_only": _portfolio_scope_totals(db, as_of_date, "H"),
        "csi300": _csi300_scope_totals(db, as_of_date),
    }


@app.get("/api/penetration/timeseries")
def get_timeseries(
    scope: str = Query("portfolio", regex="^(portfolio|csi300|both)$"),
    metric: str = Query("pe_weighted", regex="^(pe_weighted|pb_weighted|ps_weighted|virtual_earnings|total_amount)$"),
    window: int = Query(90, regex="^(90|180|360)$"),
    db: Session = Depends(get_db),
):
    """序时估值时序（spec §4.6）。"""
    from datetime import timedelta
    from models import AggregationTimeseries
    today_d = date.today()
    start = today_d - timedelta(days=window)
    scopes = ("portfolio", "csi300") if scope == "both" else (scope,)
    out: list[dict] = []
    seen_dates: set[date] = set()
    for s in scopes:
        rows = db.query(AggregationTimeseries).filter(
            AggregationTimeseries.scope == s,
            AggregationTimeseries.calc_date >= start,
        ).order_by(AggregationTimeseries.calc_date).all()
        for r in rows:
            val = getattr(r, metric, None)
            if val is None:
                continue
            out.append({
                "calc_date": r.calc_date.isoformat(),
                "scope": s,
                "value": val,
                "business_date": r.business_date.isoformat(),
            })
            seen_dates.add(r.calc_date)
    # Identify missing trading days in the window
    from services.trading_calendar import is_trading_day
    missing: list[str] = []
    cur = start
    while cur <= today_d:
        if cur not in seen_dates and is_trading_day("CN", cur, db):
            missing.append(cur.isoformat())
        cur += timedelta(days=1)
    return {
        "as_of_date": current_business_date().isoformat() if current_business_date() else None,
        "metric": metric,
        "window_days": window,
        "scope": scope,
        "data": out,
        "missing_dates": missing,
    }


@app.get("/api/penetration/kpi")
def get_kpi(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """顶部 KPI bar 实时数据（替换硬编码）。"""
    from models import FullHoldingSnapshot, AggregationCache
    from sqlalchemy import func

    total_amount = db.query(func.coalesce(func.sum(FullHoldingSnapshot.amount_cny), 0)).filter(
        FullHoldingSnapshot.as_of_date == as_of_date,
    ).scalar() or 0
    drilled_stocks = db.query(func.count(func.distinct(FullHoldingSnapshot.stock_code))).filter(
        FullHoldingSnapshot.as_of_date == as_of_date,
    ).scalar() or 0

    # Read _total row from cache (must be populated first)
    p_total = db.query(AggregationCache).filter(
        AggregationCache.as_of_date == as_of_date,
        AggregationCache.scope == "portfolio",
        AggregationCache.dimension == "l1",
        AggregationCache.key == "_total",
    ).first()

    high_g = db.query(func.coalesce(func.sum(AggregationCache.weight_pct), 0)).filter(
        AggregationCache.as_of_date == as_of_date,
        AggregationCache.scope == "portfolio",
        AggregationCache.dimension == "growth_tier",
        AggregationCache.key == "high",
    ).scalar() or 0
    midstream = db.query(func.coalesce(func.sum(AggregationCache.weight_pct), 0)).filter(
        AggregationCache.as_of_date == as_of_date,
        AggregationCache.scope == "portfolio",
        AggregationCache.dimension == "chain",
        AggregationCache.key == "midstream",
    ).scalar() or 0

    return {
        "as_of_date": as_of_date.isoformat(),
        "values": {
            "total_amount_cny": round(total_amount, 2),
            "drilled_stock_count": drilled_stocks,
            "portfolio_pe_weighted": p_total.pe_weighted if p_total else None,
            "portfolio_pb_weighted": p_total.pb_weighted if p_total else None,
            "portfolio_ps_weighted": p_total.ps_weighted if p_total else None,
            "high_growth_weight_pct": float(high_g or 0),
            "midstream_weight_pct": float(midstream or 0),
        },
    }


@app.post("/api/admin/import-source-data")
def admin_import_source_data(
    source_folder: str = Query(...),
    db: Session = Depends(get_db),
):
    """Trigger all 4 importers + penetration + aggregation for a given source folder."""
    folder = resolve_source_folder(_date_from_folder(source_folder))
    if folder is None or not folder.exists():
        raise HTTPException(status_code=404, detail=f"source folder not found: {source_folder}")
    from datetime import datetime as _dt
    # Parse as_of_date from folder name: YYYYMM数据
    yyyymm = source_folder.replace("数据", "")
    if len(yyyymm) != 6:
        raise HTTPException(status_code=400, detail="folder must be YYYYMM数据")
    as_of = date(int(yyyymm[:4]), int(yyyymm[4:6]), 1)
    reports = []
    # Run each importer
    from scripts.import_fund_index_map import import_fund_index_map
    rep = import_fund_index_map(db, as_of, folder / "基金-指数.xlsx")
    reports.append(rep.__dict__)
    from scripts.import_index_constituents import import_index_constituents
    rep = import_index_constituents(db, folder / "指数构成.xlsx")
    reports.append({"as_of_date": rep.as_of_date.isoformat(), "table": rep.table,
                    "rows_inserted": rep.rows_inserted, "rows_skipped": rep.rows_skipped,
                    "errors": rep.errors})
    from scripts.import_a_share_financials import import_a_share
    rep = import_a_share(db, as_of, folder / "全部A股.xlsx")
    reports.append(rep.__dict__)
    from scripts.import_hk_share_financials import import_hk_share
    rep = import_hk_share(db, as_of, folder / "全部港股.xlsx")
    reports.append(rep.__dict__)
    # Optional: 399673_cons.xlsx (深交所官方权重,优于 指数构成.xlsx 中同 sheet 的无权重数据)
    cons_399673 = folder / "399673_cons.xlsx"
    if cons_399673.exists():
        from scripts.import_399673_cons import import_399673
        rep = import_399673(db, cons_399673)
        reports.append(rep.__dict__)
    # Run penetration + aggregation
    pn = run_penetration_v2(db, as_of)
    from services.aggregation import refresh_all_dimensions
    refresh_all_dimensions(db, as_of)
    write_timeseries_for_day(db, as_of, as_of)
    return {
        "as_of_date": as_of.isoformat(),
        "source_folder": source_folder,
        "imports": reports,
        "penetration": {
            "holdings_seen": pn.holdings_seen,
            "holdings_drilled": pn.holdings_drilled,
            "rows_inserted_pnsnap": pn.rows_inserted_pnsnap,
            "rows_inserted_fhsnap": pn.rows_inserted_fhsnap,
        },
    }


def _date_from_folder(folder: str) -> date:
    yyyymm = folder.replace("数据", "")
    return date(int(yyyymm[:4]), int(yyyymm[4:6]), 1)


@app.post("/api/admin/recalc-aggregation")
def admin_recalc_aggregation(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """重算某 as_of_date 的聚合缓存 + 当日时序。"""
    refresh_all_dimensions(db, as_of_date)
    write_timeseries_for_day(db, as_of_date, as_of_date)
    return {"as_of_date": as_of_date.isoformat(), "status": "ok"}


@app.post("/api/admin/fill-prices-tencent")
def admin_fill_prices_tencent(
    as_of_date: date = Query(...),
    max_codes: int = Query(200, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """通过腾讯 API 拉取 current_price，填充 price_cache + 重算 dynamic PE/PB/PS。"""
    from services.price_filler import fill_prices_for_as_of
    return fill_prices_for_as_of(db, as_of_date, max_codes=max_codes)



@app.get("/api/penetration/hk-concepts")
def get_hk_concepts(
    as_of_date: date = Query(...),
    se_level: int = Query(1, ge=1, le=4),
    db: Session = Depends(get_db),
):
    """港股概念 (战略新兴产业 L1-L4) 表格化展示。

    数据从 HKShareFinancialSnapshot.se_l1..l4 读取，按 `se_level` 选择层级。
    单元格里的 "概念A;概念B"（如果将来 Excel 用分号分隔）会被拆分到多行，
    同一股票在多个概念下各显示一行；同一概念下所有股票聚合。
    支持对任意列（股票、概念、PE、PB 等）做聚类汇总。
    """
    from models import HKShareFinancialSnapshot, FullHoldingSnapshot
    se_col = f"se_l{se_level}"

    rows = db.query(HKShareFinancialSnapshot).filter(
        HKShareFinancialSnapshot.as_of_date == as_of_date,
    ).all()

    expanded: list[dict] = []
    for r in rows:
        raw = (getattr(r, se_col) or "").strip()
        if not raw or raw in ("--", "—", "nan", "其他", "其他", ""):
            continue
        # Split semicolon-separated values; trim each
        concepts = [c.strip() for c in raw.split(";") if c.strip() and c.strip() not in ("--", "—", "nan", "其他")]
        for concept in concepts:
            expanded.append({
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "concept": concept,
                "pe_ttm": r.pe_ttm,
                "pb_mrq": r.pb_mrq,
                "ps_ttm": r.ps_ttm,
                "dividend_yield": r.dividend_yield,
                "baseline_price": r.baseline_price,
                "current_price": r.current_price,
            })

    # Aggregate by concept for cluster summary
    by_concept: dict[str, dict] = {}
    for r in expanded:
        c = r["concept"]
        b = by_concept.setdefault(c, {
            "concept": c, "stock_count": 0, "_stocks": set(),
            "virt_pe": 0.0, "virt_pb": 0.0, "virt_ps": 0.0,
        })
        b["stock_count"] = len(b["_stocks"])
        b["_stocks"].add(r["stock_code"])
        if r["pe_ttm"] and r["pe_ttm"] > 0:
            b["virt_pe"] += 1 / r["pe_ttm"]
        if r["pb_mrq"] and r["pb_mrq"] > 0:
            b["virt_pb"] += 1 / r["pb_mrq"]
        if r["ps_ttm"] and r["ps_ttm"] > 0:
            b["virt_ps"] += 1 / r["ps_ttm"]

    # Finalize concept aggregates (harmonic mean of PE/PB/PS as simple aggregation)
    concept_summary = []
    for b in by_concept.values():
        n = b["stock_count"]
        concept_summary.append({
            "concept": b["concept"],
            "stock_count": n,
            "harmonic_pe": round(n / b["virt_pe"], 2) if b["virt_pe"] else None,
            "harmonic_pb": round(n / b["virt_pb"], 2) if b["virt_pb"] else None,
            "harmonic_ps": round(n / b["virt_ps"], 2) if b["virt_ps"] else None,
        })
    concept_summary.sort(key=lambda x: x["stock_count"], reverse=True)

    return {
        "as_of_date": as_of_date.isoformat(),
        "se_level": se_level,
        "expanded_table": expanded,           # 拆分后的明细表
        "concept_summary": concept_summary,   # 按概念聚类
    }


@app.get("/api/penetration/drillable-indices")
def get_drillable_indices(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """列出所有可下钻的基金（卡片列表）。

    每张卡片: 基金代码/名称/指数/成分股数/静态金额/估算市值/估算偏差%/组合占比/加权 PE PB PS 股息率
    """
    from services.drillable_funds import list_drillable_indices
    return {
        "as_of_date": as_of_date.isoformat(),
        "indices": list_drillable_indices(db, as_of_date),
    }


@app.get("/api/penetration/index-drill")
def get_fund_drill(
    index_code: str = Query(...),
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """单一基金下钻明细：每只成分股的约当数量 + 昨日最新股价 + 估值指标。"""
    from services.drillable_funds import get_index_drill_detail
    return get_index_drill_detail(db, index_code, as_of_date)


# ============================================================================
# 资讯数据 API (a-stock-data skill §5-7)
# ============================================================================
# 所有 endpoint 默认走 require_auth (与项目其它端点一致, 需 x-session-token)
# 手动拉取走 /api/info/crawl/*  (admin 端点, 无需额外鉴权)


@app.get("/api/info/global-news")
def get_global_flash_news(
    limit: int = Query(50, ge=1, le=200),
    hours: int | None = Query(None, description="只返回近 N 小时的快讯"),
    db: Session = Depends(get_db),
):
    """东财 7×24 全球快讯 (替代已下线财联社快讯)."""
    from services.info_service import list_global_flash_news
    rows = list_global_flash_news(db, limit=limit, hours=hours)
    return {
        "count": len(rows),
        "items": [
            {
                "title": r.title,
                "summary": r.summary,
                "source": r.source,
                "url": r.url,
                "published_at": r.published_at.isoformat() if r.published_at else None,
            }
            for r in rows
        ],
    }


@app.get("/api/info/stock-news/{code}")
def get_stock_news(
    code: str,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """个股新闻 (东财 search-api-web)."""
    from services.info_service import list_stock_news
    rows = list_stock_news(db, code=code, limit=limit)
    return {
        "code": code,
        "count": len(rows),
        "items": [
            {
                "title": r.title,
                "summary": r.summary,
                "source": r.source,
                "url": r.url,
                "published_at": r.published_at.isoformat() if r.published_at else None,
            }
            for r in rows
        ],
    }


@app.get("/api/info/announcements/{code}")
def get_announcements(
    code: str,
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """巨潮公告 (cninfo, 动态 orgId)."""
    from services.info_service import list_announcements
    rows = list_announcements(db, code=code, limit=limit)
    return {
        "code": code,
        "count": len(rows),
        "items": [
            {
                "announcement_id": r.announcement_id,
                "title": r.title,
                "type": r.announcement_type,
                "publish_date": r.publish_date.isoformat() if r.publish_date else None,
                "url": r.url,
            }
            for r in rows
        ],
    }


@app.get("/api/info/research/{code}")
def get_research_reports(
    code: str,
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """东财研报列表 (含 EPS 预测, 评级)."""
    from services.info_service import list_research_reports
    rows = list_research_reports(db, code=code, limit=limit)
    return {
        "code": code,
        "count": len(rows),
        "items": [
            {
                "info_code": r.info_code,
                "title": r.title,
                "org_name": r.org_name,
                "publish_date": r.publish_date.isoformat() if r.publish_date else None,
                "rating": r.rating,
                "predict_eps_current": r.predict_eps_current,
                "predict_eps_next": r.predict_eps_next,
                "industry": r.industry,
                "pdf_downloaded": r.pdf_path is not None,
            }
            for r in rows
        ],
    }


@app.get("/api/info/hot-stocks")
def get_hot_stocks(
    signal_date: date | None = Query(None, description="YYYY-MM-DD, 默认今天"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """同花顺当日强势股 + 题材归因."""
    from services.info_service import list_hot_stocks
    d = signal_date or date.today()
    rows = list_hot_stocks(db, signal_date=d, limit=limit)
    return {
        "signal_date": d.isoformat(),
        "count": len(rows),
        "items": [
            {
                "code": r.stock_code,
                "name": r.stock_name,
                "close": r.close,
                "change_pct": r.change_pct,
                "turnover_pct": r.turnover_pct,
                "amount": r.amount,
                "market": r.market,
                "reason_tags": r.reason_tags,
                "rank": r.rank,
            }
            for r in rows
        ],
    }


@app.get("/api/info/themes")
def get_theme_hotness(
    signal_date: date | None = Query(None, description="YYYY-MM-DD, 默认今天"),
    top_n: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """题材热度聚合 (从热点列表 reason 字段词频统计)."""
    from services.info_service import list_hot_stocks
    d = signal_date or date.today()
    rows = list_hot_stocks(db, signal_date=d, limit=200)
    items = [
        {
            "code": r.stock_code,
            "name": r.stock_name,
            "change_pct": r.change_pct,
            "reason_tags": r.reason_tags,
        }
        for r in rows
    ]
    from crawlers.signal_ths import aggregate_theme_hotness
    themes = aggregate_theme_hotness(items, top_n=top_n)
    return {"signal_date": d.isoformat(), "themes": themes}


# ---------- 主动拉取 (admin) ----------

@app.post("/api/info/crawl/global-news")
def crawl_global_news_endpoint(
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """主动拉取全球快讯并写入 DB."""
    from crawlers.news_eastmoney import fetch_global_flash_news
    from services.info_service import upsert_global_flash_news
    rows = fetch_global_flash_news(page_size=page_size)
    written = upsert_global_flash_news(db, rows)
    return {"fetched": len(rows), "written": written}


@app.post("/api/info/crawl/stock-news/{code}")
def crawl_stock_news_endpoint(
    code: str,
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """主动拉取个股新闻."""
    from crawlers.news_eastmoney import fetch_stock_news
    from services.info_service import upsert_stock_news
    rows = fetch_stock_news(code, page_size=page_size)
    written = upsert_stock_news(db, code, rows)
    return {"code": code, "fetched": len(rows), "written": written}


@app.post("/api/info/crawl/announcements/{code}")
def crawl_announcements_endpoint(
    code: str,
    page_size: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """主动拉取巨潮公告."""
    from crawlers.announcement_cninfo import fetch_announcements
    from services.info_service import upsert_announcements
    rows = fetch_announcements(code, page_size=page_size)
    written = upsert_announcements(db, code, rows)
    return {"code": code, "fetched": len(rows), "written": written}


@app.post("/api/info/crawl/research/{code}")
def crawl_research_endpoint(
    code: str,
    max_pages: int = Query(2, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """主动拉取东财研报."""
    from crawlers.research_em import fetch_reports
    from services.info_service import upsert_research_reports
    rows = fetch_reports(code, max_pages=max_pages)
    written = upsert_research_reports(db, code, rows)
    return {"code": code, "fetched": len(rows), "written": written}


@app.post("/api/info/crawl/hot-stocks")
def crawl_hot_stocks_endpoint(
    signal_date: date | None = Query(None),
    db: Session = Depends(get_db),
):
    """主动拉取同花顺当日热点."""
    from crawlers.signal_ths import fetch_hot_stocks
    from services.info_service import upsert_hot_stocks
    d = signal_date or date.today()
    rows = fetch_hot_stocks(d)
    written = upsert_hot_stocks(db, d, rows)
    return {"signal_date": d.isoformat(), "fetched": len(rows), "written": written}
