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
from services.analyst_service import (
    ingest_analyst_data,
    get_core_companies,
    get_industry_chains,
    get_stock_detail,
)
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


def _create_session(db: Session, ip: str, user_id: int | None = None) -> str:
    """创建新 session，返回 token。默认 24h 过期。user_id 可选（多用户场景）"""
    from models import AccessSession
    token = secrets.token_hex(32)
    sess = AccessSession(
        token=token,
        ip=ip,
        user_id=user_id,
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=1),
    )
    db.add(sess)
    db.commit()
    return token


def _verify_token(db: Session, token: str):
    """验证 session token 是否有效。返回 AccessSession 或 None。"""
    from models import AccessSession
    if not token:
        return None
    sess = db.query(AccessSession).filter(AccessSession.token == token).first()
    if not sess:
        return None
    if sess.expires_at < datetime.utcnow():
        db.delete(sess)
        db.commit()
        return None
    return sess


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
    username: str | None = None  # 多用户登录；缺省走单密码兼容模式


def _user_public(u) -> dict:
    """统一序列化 user 字段到 API 响应"""
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "is_advisor": bool(u.is_advisor),
        "is_admin": bool(u.is_admin),
    }


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
    """多用户登录：username + password (bcrypt)。
    兼容：username 缺省 + 走单密码模式 (APP_PASSWORD) → 找任意 admin 用户。
    """
    ip = _client_ip(request)
    # 先检查是否被锁
    banned_until, remaining = _check_ban(db, ip)
    if banned_until:
        return {
            "status": "banned",
            "banned_until": banned_until.isoformat(),
            "remaining_seconds": remaining,
        }
    # 长度校验（兼容旧 6-12）
    if not (6 <= len(req.password) <= 128):
        return {"status": "error", "message": "密码长度需 6-128 位"}

    user = None
    if req.username:
        # === 多用户模式：按 username 查表 + bcrypt 校验 ===
        from models import User
        import bcrypt as _bcrypt
        try:
            u = db.query(User).filter(
                User.username == req.username, User.is_active == True
            ).first()
        except Exception:
            u = None
        if u:
            try:
                if _bcrypt.checkpw(req.password.encode("utf-8"), u.password_hash.encode("utf-8")):
                    user = u
            except Exception:
                user = None
    else:
        # === 兼容：旧单密码模式 → APP_PASSWORD 走 SHA-256 旧 hash ===
        if _hash_pw(req.password) == APP_PASSWORD_HASH:
            from models import User
            user = db.query(User).filter(User.is_admin == True, User.is_active == True).first()
        if user is None and APP_PASSWORD and req.password == APP_PASSWORD:
            # 旧库可能没 admin 用户（理论上 _ensure_seed_admin 已建）— 找不到就失败
            from models import User
            user = db.query(User).filter(User.is_admin == True, User.is_active == True).first()
            if not user:
                _record_fail(db, ip)
                raise HTTPException(status_code=401, detail="单密码登录要求至少存在 admin 用户")

    if user is None:
        rec, ban_for = _record_fail(db, ip)
        result = {
            "status": "error",
            "message": "用户名或密码错误",
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
    token = _create_session(db, ip, user_id=user.id)
    user.last_login_at = datetime.utcnow()
    db.commit()
    return {
        "status": "ok",
        "token": token,
        "expires_in": 86400,
        "user": _user_public(user),
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


@app.get("/api/auth/me")
def auth_me(request: Request, db: Session = Depends(get_db)):
    """返回当前登录用户信息（middleware 已注入 request.state.user）"""
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(status_code=401, detail="未登录")
    return {"user": _user_public(u)}


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
    is_public = any(path.startswith(p) for p in PUBLIC_PATHS)
    # 始终尝试注入 user（即使公开路径，也要让 /me、/auth/users 等能用 request.state.user）
    token = request.headers.get("x-session-token") or request.query_params.get("session")
    db = next(get_db())
    try:
        sess = _verify_token(db, token) if token else None
        if sess:
            try:
                from models import User
                if sess.user_id:
                    u = db.query(User).filter(User.id == sess.user_id, User.is_active == True).first()
                    if u:
                        request.state.user = u
                        request.state.user_id = u.id
                        request.state.is_advisor = bool(u.is_advisor)
                        request.state.is_admin = bool(u.is_admin)
            except Exception as _e:
                import logging
                logging.getLogger(__name__).warning("user inject failed: %s", _e)
            # view_as 解析（来自 query / header）
            view_as = (
                request.query_params.get("view_as")
                or request.headers.get("x-view-as")
            )
            if view_as:
                try:
                    request.state.view_as_user_id = int(view_as)
                except (TypeError, ValueError):
                    pass
    finally:
        db.close()
    if is_public:
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)
    if not sess:
        return _json_error(401, "需要登录", request)
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
    register_job_handlers()  # 填充 _JOB_DISPATCH 派发表
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


@app.get("/api/exchange-rates/latest")
def list_exchange_rates_latest(target: str = Query("CNY"), db: Session = Depends(get_db)):
    """返回每个 from_currency 的最新汇率 (到 target 币种, 默认 CNY).

    用于前端把 HKD/USD 等原币种价格折算为人民币:
      {USD: 7.18, HKD: 0.92, CAD: 5.20, ...}
    每个 from_currency 只返回最新一条记录 (按 rate_date desc 取首条).
    """
    from models import ExchangeRate
    from datetime import date as date_cls
    rows = db.query(ExchangeRate).filter(
        ExchangeRate.to_currency == target,
        ExchangeRate.rate_date <= date_cls.today(),
    ).order_by(ExchangeRate.rate_date.desc()).all()
    latest = {}
    for r in rows:
        if r.from_currency not in latest:
            latest[r.from_currency] = {
                "date": r.rate_date.isoformat(),
                "rate": r.rate,
                "source": r.source,
            }
    return latest


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
    import logging
    from models import Holding, PriceCache, ExchangeRate

    logger = logging.getLogger(__name__)

    # 整个函数包 try/except: Zeabur 边缘 < 30s 容易触发 502,
    # 任何异常(慢 PBoC, OOM, DB 失联)都返回空序列让前端优雅降级, 而不是挂掉 worker.
    try:
        # 兜底：仅当当日汇率缺失时才拉（update_rates_today 内部有 17s 的 PBoC 接口, 频繁调很慢）
        # Cloud: PBoC 网络常失败/超时, 直接 skip; 用 DB 里已有的 ExchangeRate 即可.
        # 重要: PBoC 失败时 update_rates_today 内部 raise 但 SQLAlchemy session
        #       会进入 PendingRollbackError, 必须 rollback 才能继续 query.
        try:
            from crawlers.exchange_rates import update_rates_today
            update_rates_today(db)
        except Exception as e:
            logger.warning("update_rates_today failed (non-fatal): %s", e)
            try:
                db.rollback()
            except Exception:
                pass

        # 1. 取当前所有 holdings
        rows = db.query(Holding).all()
        if not rows:
            return {"series": [], "currency": target, "days": days}

        # 2. 只取 holdings 关心的 stock_code 的 price_cache.
        # Cloud: price_cache 有 60 万行, 全部加载会 OOM/超时 → 500 → container crash.
        cutoff = date.today() - timedelta(days=days)
        holding_codes = list({h.security_code for h in rows if h.security_code})
        if not holding_codes:
            return {"series": [], "currency": target, "days": days}
        pc_rows = (
            db.query(PriceCache)
            .filter(
                PriceCache.trade_date >= cutoff,
                PriceCache.stock_code.in_(holding_codes),
            )
            .all()
        )
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
    except Exception as e:
        # Cloud 友好降级: 任何错误返回空序列而不是 500, 防止 edge 502 + container crash.
        logger.exception("/api/trend failed: %s", e)
        return {
            "series": [],
            "currency": target,
            "days": days,
            "eligible_holdings": 0,
            "skipped_holdings": [],
            "note": f"trend unavailable: {type(e).__name__}: {str(e)[:120]}",
            "error": True,
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


@app.get("/api/code-map/coverage")
def code_map_coverage(
    pool: str = Query("all", pattern="^(all|holdings|watchlist|drilled)$"),
    api: str | None = Query(None, description="按 api_strategy 过滤（可选）"),
    db: Session = Depends(get_db),
):
    """代码映射覆盖率检查。

    对 holdings / watchlist / drilled 三个证券池的所有 code × 所有候选 api_strategy
    跑 transform_code，返回：
      - total_codes   池里 distinct code 数
      - rows          每行 (code, market, api, code_out, status: mapped|unmapped|unsupported)
      - missing       真正需要补的 unmapped 行数 + 前 20 个示例
      - summary       三池汇总 + 健康度（missing==0 为绿，否则为红）

    可在定时拉取任务前调用，发现 missing 提前补 api_code_map，避免拉取失败。
    """
    from scripts.check_code_map_coverage import (
        DEFAULT_API_STRATEGIES, _PASSTHROUGH_APIS,
        _is_known_unsupported, _should_skip_tencent_unsupported,
        _classify, _market_of_code,
        collect_holdings, collect_watchlist, collect_drilled,
    )
    from services.code_map import transform_code

    pools_arg = ["holdings", "watchlist", "drilled"] if pool == "all" else [pool]
    api_strategies = [api] if api else list(DEFAULT_API_STRATEGIES)
    collectors = {
        "holdings": collect_holdings,
        "watchlist": collect_watchlist,
        "drilled": collect_drilled,
    }

    pools_out = []
    total_missing = 0
    for pname in pools_arg:
        codes = collectors[pname](db, api_strategies)
        rows = []
        missing_examples = []
        mapped = unsupported = 0
        for code in codes:
            market = _market_of_code(code)
            for api_s in api_strategies:
                try:
                    code_out = transform_code(code, api_s, db)
                except Exception:
                    code_out = None
                if _should_skip_tencent_unsupported(code, api_s, code_out):
                    continue
                if _is_known_unsupported(code, api_s):
                    continue
                status = _classify(code, api_s, code_out)
                if status == "mapped":
                    mapped += 1
                elif status == "unsupported":
                    unsupported += 1
                rows.append({
                    "code": code, "market": market,
                    "api": api_s, "code_out": code_out, "status": status,
                })
                if status == "unmapped" and len(missing_examples) < 20:
                    missing_examples.append({"code": code, "api": api_s, "code_out": code_out})
        missing = sum(1 for r in rows if r["status"] == "unmapped")
        total_missing += missing
        pools_out.append({
            "name": pname,
            "total_codes": len(codes),
            "rows_count": len(rows),
            "mapped": mapped,
            "unsupported": unsupported,
            "missing": missing,
            "missing_examples": missing_examples,
            # 详细 rows 仅在前端需要时返回；默认截断避免 payload 过大
            "rows": rows if len(rows) <= 500 else rows[:500],
            "rows_truncated": len(rows) > 500,
        })

    return {
        "pool": pool,
        "api_filter": api,
        "total_missing": total_missing,
        "health": "ok" if total_missing == 0 else "missing",
        "pools": pools_out,
        "checked_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


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

# Job ID → handler function 派发表（Phase 5 of data-pulling refactor）。
# 所有 8 个 job 都可以通过 /api/scheduler/trigger 手动执行，立即跑而不是等下次 cron。
_JOB_DISPATCH: dict = {}  # 延迟到 register_job_handlers() 填充（启动时执行）


def register_job_handlers() -> None:
    """把 9 个 job_* 函数注册到 _JOB_DISPATCH。FastAPI 启动时调用一次。"""
    global _JOB_DISPATCH
    if _JOB_DISPATCH:
        return  # 幂等
    from services.scheduler import (
        job_fetch_realtime_prices,
        job_fill_snapshot_gaps_smart,
        job_update_financial_fundamentals,
        job_update_industry_crawler_data,
        job_backfill_gaps,
        job_crawl_global_news,
        job_crawl_stock_news,
        job_crawl_announcements_and_research,
        job_crawl_hot_stocks,
    )
    _JOB_DISPATCH = {
        "realtime_prices": job_fetch_realtime_prices,
        "fill_snapshot_gaps_smart": job_fill_snapshot_gaps_smart,
        "industry_crawler_data": job_update_industry_crawler_data,
        "financial_fundamentals": job_update_financial_fundamentals,
        "backfill_gaps": job_backfill_gaps,
        "info_global_news": job_crawl_global_news,
        "info_stock_news": job_crawl_stock_news,
        "info_announcements_research": job_crawl_announcements_and_research,
        "info_hot_stocks": job_crawl_hot_stocks,
    }


@app.get("/api/scheduler/status")
def scheduler_status():
    """获取定时任务调度器状态 + 每个 job 最近一次执行的元数据"""
    from services.scheduler import scheduler, _JOB_LAST_RUN
    if not scheduler or not scheduler.running:
        return {"running": False, "jobs": []}
    jobs = []
    for job in scheduler.get_jobs():
        jid = job.id
        last = _JOB_LAST_RUN.get(jid, {})
        jobs.append({
            "id": jid,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "last_run_at": last.get("run_at"),
            "last_status": last.get("status"),       # "ok" / "error" / None
            "last_error": last.get("error"),
            "last_result": last.get("result"),
            "last_duration_ms": last.get("duration_ms"),
        })
    return {"running": True, "jobs": jobs}


@app.post("/api/scheduler/trigger/{job_id}")
def trigger_job(job_id: str, force: bool = False, background: bool = False):
    """手动触发指定定时任务。

    Args:
        job_id: 8 个 job 之一 (见 _JOB_DISPATCH)
        force: True 时绕过 dedup 守门（强制重拉）
        background: True 时把 handler 放到 daemon 线程跑，立即返回；否则同步等结果
    """
    import threading
    register_job_handlers()
    handler = _JOB_DISPATCH.get(job_id)
    if not handler:
        return {
            "status": "error",
            "message": f"Unknown job_id: {job_id}. "
                       f"Available: {sorted(_JOB_DISPATCH.keys())}",
        }
    if background:
        threading.Thread(
            target=handler,
            kwargs={"force": force},
            daemon=True,
        ).start()
        return {"status": "ok", "mode": "queued", "job_id": job_id, "force": force}
    try:
        result = handler(force=force)
        return {
            "status": "ok",
            "mode": "sync",
            "job_id": job_id,
            "force": force,
            "result": result if isinstance(result, dict) else {"value": result},
        }
    except Exception as e:
        return {"status": "error", "job_id": job_id, "message": str(e)[:300]}


# ==================== 数据浏览 ====================

# 数据表注册：分类 → 表列表（覆盖全部 34 张表，含 date_field 和 desc）
DATA_TABLES = {
    "持仓主数据": [
        {"table": "security_master", "label": "证券基础", "model": "SecurityMaster", "pk": "security_code", "date_field": "updated_at", "desc": "证券基础信息（原币种、类型等）"},
        {"table": "security_type_config", "label": "类型配置", "model": "SecurityTypeConfig", "pk": "asset_type", "date_field": "updated_at", "desc": "证券类型主数据（净值显示位数等）"},
        {"table": "holdings", "label": "持仓", "model": "Holding", "pk": "id", "date_field": "created_at", "desc": "组合持仓明细"},
        {"table": "watchlist", "label": "自选股", "model": "Watchlist", "pk": "code", "date_field": "added_at", "desc": "自选股清单"},
    ],
    "行情数据": [
        {"table": "price_cache", "label": "价格缓存", "model": "PriceCache", "date_field": "trade_date", "desc": "日频复权价格（开高低收量）"},
        {"table": "stock_info_cache", "label": "行情缓存", "model": "StockInfoCache", "pk": "stock_code", "date_field": "updated_at", "desc": "行情/财务 JSON 缓存"},
        {"table": "exchange_rates", "label": "汇率", "model": "ExchangeRate", "date_field": "rate_date", "desc": "PBoC 中间价汇率"},
        {"table": "fund_daily_nav", "label": "基金净值", "model": "FundDailyNav", "date_field": "trade_date", "desc": "基金每日净值/累计净值"},
    ],
    "财务快照": [
        {"table": "stock_financials", "label": "个股财务", "model": "StockFinancial", "date_field": "as_of_date", "desc": "个股财务指标（PE/增长/市值）"},
        {"table": "a_share_financial_snapshot", "label": "A股估值快照", "model": "AShareFinancialSnapshot", "date_field": "as_of_date", "desc": "A股估值+7套行业体系快照"},
        {"table": "hk_share_financial_snapshot", "label": "港股估值快照", "model": "HKShareFinancialSnapshot", "date_field": "as_of_date", "desc": "港股估值+行业体系快照"},
    ],
    "穿透分析": [
        {"table": "penetration_results", "label": "穿透结果", "model": "PenetrationResult", "date_field": "calculated_at", "desc": "底层股票穿透表"},
        {"table": "penetration_snapshot", "label": "基金下钻", "model": "PenetrationSnapshot", "date_field": "as_of_date", "desc": "基金下钻结果快照"},
        {"table": "full_holding_snapshot", "label": "全持仓快照", "model": "FullHoldingSnapshot", "date_field": "as_of_date", "desc": "全持仓快照（含行业体系）"},
        {"table": "aggregation_cache", "label": "聚合缓存", "model": "AggregationCache", "date_field": "updated_at", "desc": "组合/CSI300 聚合结果"},
        {"table": "aggregation_timeseries", "label": "估值时序", "model": "AggregationTimeseries", "date_field": "calc_date", "desc": "估值日时序数据"},
        {"table": "csi300_baselines", "label": "沪深300基准", "model": "Csi300Baseline", "date_field": "as_of_date", "desc": "沪深300分析基准"},
        {"table": "csi300_constituent_snapshot", "label": "沪深300成分", "model": "Csi300ConstituentSnapshot", "date_field": "as_of_date", "desc": "沪深300成分股快照"},
    ],
    "指数基金": [
        {"table": "funds", "label": "基金", "model": "Fund", "pk": "code", "date_field": "updated_at", "desc": "基金/ETF 基础信息"},
        {"table": "fund_index_map", "label": "基金→指数", "model": "FundIndexMap", "date_field": "as_of_date", "desc": "基金→指数追踪关系"},
        {"table": "index_constituents", "label": "指数成分股", "model": "IndexConstituent", "date_field": "as_of_date", "desc": "指数成分股+权重"},
        {"table": "index_constituent_snapshot", "label": "成分股快照", "model": "IndexConstituentSnapshot", "date_field": "as_of_date", "desc": "指数成分股快照"},
    ],
    "新闻研报": [
        {"table": "global_flash_news", "label": "全球快讯", "model": "GlobalFlashNews", "date_field": "published_at", "desc": "东财7×24全球快讯"},
        {"table": "stock_news", "label": "个股新闻", "model": "StockNews", "date_field": "published_at", "desc": "个股新闻"},
        {"table": "announcements", "label": "公告", "model": "Announcement", "date_field": "publish_date", "desc": "巨潮公告"},
        {"table": "research_reports", "label": "研报", "model": "ResearchReport", "date_field": "publish_date", "desc": "东财研报"},
        {"table": "hot_stock_signals", "label": "强势股", "model": "HotStockSignal", "date_field": "signal_date", "desc": "同花顺强势股信号"},
    ],
    "分析师报告": [
        {"table": "analyst_company_report", "label": "公司研究", "model": "AnalystCompanyReport", "date_field": "updated_at", "desc": "公司研究6段式报告"},
        {"table": "analyst_industry_chain", "label": "产业链总结", "model": "AnalystIndustryChain", "date_field": "updated_at", "desc": "产业链总结报告"},
        {"table": "analyst_industry_chain_company", "label": "产业链公司", "model": "AnalystIndustryChainCompany", "date_field": "updated_at", "desc": "产业链公司清单"},
    ],
    "基础配置": [
        {"table": "trading_calendar", "label": "交易日历", "model": "TradingCalendar", "date_field": "date", "desc": "CN/HK/US/OF 交易日历"},
        {"table": "api_code_map", "label": "API代码映射", "model": "ApiCodeMap", "date_field": "updated_at", "desc": "标准代码→各API调用代码"},
    ],
    "系统表": [
        {"table": "access_attempts", "label": "登录失败", "model": "AccessAttempt", "pk": "ip", "date_field": "last_fail_at", "desc": "登录失败累计（系统）"},
        {"table": "access_sessions", "label": "会话", "model": "AccessSession", "pk": "token", "date_field": "created_at", "desc": "会话token（系统）"},
    ],
}


def _find_model(table_name: str):
    """根据表名查找模型类和配置（公共辅助函数）"""
    import models
    for category, tables in DATA_TABLES.items():
        for t in tables:
            if t["table"] == table_name:
                model_cls = getattr(models, t["model"], None)
                return model_cls, t, category
    return None, None, None


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


@app.get("/api/data-browser/overview")
def data_browser_overview(db: Session = Depends(get_db)):
    """所有表的数据完整性概览（34张表的行数/日期范围/填充率）"""
    from sqlalchemy import func
    from sqlalchemy.inspection import inspect as sa_inspect

    result = []
    for category, tables in DATA_TABLES.items():
        for t in tables:
            model_cls, _, _ = _find_model(t["table"])
            if not model_cls:
                continue
            mapper = sa_inspect(model_cls)
            columns = [c.key for c in mapper.column_attrs]

            total = db.query(model_cls).count()
            last_update = None
            date_range = None

            # 日期字段范围
            date_field = t.get("date_field")
            if date_field and date_field in columns:
                col = getattr(model_cls, date_field)
                last_update = db.query(func.max(col)).scalar()
                dmin = db.query(func.min(col)).scalar()
                if dmin or last_update:
                    date_range = {
                        "field": date_field,
                        "min": str(dmin) if dmin else None,
                        "max": str(last_update) if last_update else None,
                    }

            # 关键字段填充率（抽样前 8 个字段）
            fill_rates = {}
            for col_name in columns[:8]:
                if col_name == date_field:
                    continue
                col = getattr(model_cls, col_name)
                non_null = db.query(func.count(col)).filter(col.isnot(None)).scalar()
                fill_rates[col_name] = round(non_null / total * 100, 1) if total > 0 else 0

            result.append({
                "table": t["table"],
                "label": t["label"],
                "category": category,
                "desc": t.get("desc", ""),
                "row_count": total,
                "column_count": len(columns),
                "date_range": date_range,
                "last_update": str(last_update) if last_update else None,
                "fill_rates": fill_rates,
            })

    # 汇总
    total_tables = len(result)
    non_empty = sum(1 for r in result if r["row_count"] > 0)
    fill_vals = [sum(r["fill_rates"].values()) / len(r["fill_rates"]) for r in result if r["fill_rates"]]
    avg_fill = sum(fill_vals) / total_tables if total_tables else 0

    return {
        "summary": {
            "total_tables": total_tables,
            "non_empty": non_empty,
            "empty": total_tables - non_empty,
            "avg_fill_rate": round(avg_fill, 1),
        },
        "tables": result,
    }


@app.get("/api/data-browser/schema")
def data_browser_schema():
    """所有表的完整结构信息（字段名/类型/可空/主键/默认值/唯一约束）"""
    from sqlalchemy.inspection import inspect as sa_inspect

    result = {}
    for category, tables in DATA_TABLES.items():
        for t in tables:
            model_cls, _, _ = _find_model(t["table"])
            if not model_cls:
                continue
            mapper = sa_inspect(model_cls)
            fields = []
            for c in mapper.columns:
                fields.append({
                    "name": c.key,
                    "type": str(c.type),
                    "nullable": c.nullable,
                    "primary_key": c.primary_key,
                    "default": str(c.default.arg) if c.default and c.default.arg else None,
                    "autoincrement": bool(c.autoincrement),
                })
            # 唯一约束
            uniques = []
            for const in mapper.tables[0].constraints:
                if hasattr(const, "columns") and const.columns:
                    uniques.append({
                        "name": const.name,
                        "columns": [col.name for col in const.columns],
                    })
            result[t["table"]] = {
                "label": t["label"],
                "category": category,
                "desc": t.get("desc", ""),
                "fields": fields,
                "uniques": uniques,
            }
    return result


@app.get("/api/data-browser/{table_name}")
def browse_table(
    table_name: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """分页浏览指定数据表"""
    model_cls, t_cfg, _ = _find_model(table_name)
    if not model_cls:
        return {"error": f"Table {table_name} not found"}

    pk_col = t_cfg.get("pk") if t_cfg else None

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


@app.get("/api/data-browser/{table_name}/stats")
def data_browser_table_stats(table_name: str, db: Session = Depends(get_db)):
    """指定表的字段级统计（宽度）：非空率/唯一值/min/max/avg/示例值"""
    from sqlalchemy import func, Integer, Float
    from sqlalchemy.inspection import inspect as sa_inspect

    model_cls, _, _ = _find_model(table_name)
    if not model_cls:
        return {"error": f"Table {table_name} not found"}

    mapper = sa_inspect(model_cls)
    total = db.query(model_cls).count()

    fields = []
    for c in mapper.columns:
        col = getattr(model_cls, c.key)
        non_null = db.query(func.count(col)).filter(col.isnot(None)).scalar() if total > 0 else 0
        distinct = db.query(func.count(func.distinct(col))).scalar() if total > 0 else 0

        stat = {
            "name": c.key,
            "type": str(c.type),
            "nullable": c.nullable,
            "primary_key": c.primary_key,
            "default": str(c.default.arg) if c.default and c.default.arg else None,
            "non_null_count": non_null,
            "fill_rate": round(non_null / total * 100, 1) if total > 0 else 0,
            "distinct_count": distinct,
        }

        # 数值字段：min/max/avg
        if isinstance(c.type, (Integer, Float)) and not c.primary_key and total > 0:
            stat["min"] = db.query(func.min(col)).scalar()
            stat["max"] = db.query(func.max(col)).scalar()
            avg_val = db.query(func.avg(col)).scalar()
            stat["avg"] = round(avg_val, 2) if avg_val is not None else None

        # 示例值（取第一条非空）
        sample = db.query(col).filter(col.isnot(None)).first()
        stat["sample"] = str(sample[0])[:100] if sample and sample[0] is not None else None

        fields.append(stat)

    return {"table": table_name, "total_rows": total, "fields": fields}


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
    """FullHoldingTable = Overview - 可下钻基金 + 下钻基金下钻/展开后的股票.

    算法 (用户口径):
      1. 拉取 Holding 全表 (Overview 的 44 笔持仓)
      2. 通过 FundIndexMap 识别 14 只可下钻基金 (12 个指数 + 多基金)
      3. 移除这些可下钻基金
      4. 加入 12 个可下钻指数的 constituents (按 drill 算法展开)
      5. 同代码的证券已合并为单行 (amount 求和)
      6. 未下钻: 估算市值 = 数量 × 收盘价 (Holding.quantity × NAV/current_price × 汇率)
      7. 下钻: 估算市值 = drill 算法 (shares_equivalent × current_price × 汇率)
      8. PE/PB/PS = *_dynamic 优先, baseline 5/29 兜底

    字段:
      source_type: 'undrilled_fund' | 'direct_stock' | 'cash' | 'drilled'
      est_market_value_cny: 在原币种下 (前端做 CNY 折算)
      currency: 原币种 (前端用此判断是否需要折算)
    """
    from models import (
        AShareFinancialSnapshot, HKShareFinancialSnapshot, Holding, FundIndexMap,
    )
    from services.drillable_funds import list_drillable_indices, get_index_drill_detail

    # 1) 拉取快照数据 (用于 PE/PB/PS)
    a_snap = {a.stock_code.split(".")[0]: a for a in
              db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap = {h.stock_code.split(".")[0]: h for h in
              db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    # 同样索引 suffix-stripped
    def _norm_keys(snap_dict):
        for k, v in list(snap_dict.items()):
            snap_dict.setdefault(v.stock_code, v)
        return snap_dict
    a_snap = _norm_keys(a_snap)
    h_snap = _norm_keys(h_snap)

    # 2) 拉 Holding 全部 + 合并同代码 (quantity/amount 累加)
    holdings = db.query(Holding).all()
    by_code: dict[str, dict] = {}
    for h in holdings:
        code = h.security_code
        if code not in by_code:
            by_code[code] = {
                "security_code": code,
                "security_name": h.security_name,
                "quantity": 0.0,
                "amount": 0.0,
                "amount_cny": 0.0,
                "currency": h.currency or "CNY",
                "asset_type": h.asset_type or "",
            }
        acc = by_code[code]
        acc["quantity"] += (h.quantity or 0.0)
        acc["amount"] += (h.amount or 0.0)
        acc["amount_cny"] += (h.amount_cny or 0.0)

    # 3) 识别可下钻基金 (FundIndexMap 中存在的 fund_code)
    drillable_codes = {
        m.fund_code for m in
        db.query(FundIndexMap).filter(FundIndexMap.as_of_date == as_of_date).all()
    }

    # 4) 同代码合并后, 移除可下钻基金, 保留其余 (非下钻) holding 行
    out: list[dict] = []
    for code, acc in by_code.items():
        if code in drillable_codes:
            continue   # 跳过可下钻基金 (会被下钻成分股取代)
        source_type = _infer_source_type_from_holding(acc)
        # PE/PB/PS 从快照 (suffixed & unsuffixed 都查)
        snap = _lookup_snap(code, a_snap, h_snap)
        pe_v = pb_v = ps_v = dy_v = None
        if snap:
            pe_v = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
            pb_v = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
            ps_v = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
            dy_v = snap.dividend_yield
        # 估算市值 = 数量 × 收盘价 (用户口径)
        est_value, shares, fallback_price = _estimate_market_value_for_holding(
            code, acc, snap, db,
        )
        baseline_price = snap.baseline_price if snap else None
        current_price = snap.current_price if snap else fallback_price
        pct_3m = _pct_change_3m(code, current_price, db)
        out.append({
            "stock_code": code,
            "stock_name": acc["security_name"],
            "source_type": source_type,
            "source_holding_code": code,
            "amount_cny": acc["amount_cny"],
            "static_amount_cny": acc["amount_cny"],
            "shares": shares,
            "baseline_price": baseline_price,
            "current_price": current_price,
            "current_price_date": snap.current_price_date.isoformat() if snap and snap.current_price_date else None,
            "est_market_value_cny": est_value,
            "est_deviation_pct": ((est_value - acc["amount_cny"]) / acc["amount_cny"] * 100)
                if acc["amount_cny"] and est_value else 0.0,
            "pct_change_3m": pct_3m,
            "pe_ttm_dynamic": pe_v,
            "pb_mrq_dynamic": pb_v,
            "ps_ttm_dynamic": ps_v,
            "dividend_yield": dy_v,
            "metric_basis": "dynamic" if (snap and snap.pe_ttm_dynamic is not None) else "baseline_5_29",
            "fund_currency": acc["currency"],
        })

    # 5) 加入 12 个可下钻指数的 constituents (drill 算法)
    indices = list_drillable_indices(db, as_of_date)
    for idx in indices:
        detail = get_index_drill_detail(db, idx["index_code"], as_of_date)
        if "constituents" not in detail:
            continue
        for c in detail["constituents"]:
            code = c["stock_code"]
            snap = _lookup_snap(code, a_snap, h_snap)
            pe_v = pb_v = ps_v = dy_v = None
            if snap:
                pe_v = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
                pb_v = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
                ps_v = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
                dy_v = snap.dividend_yield
            pct_3m = _pct_change_3m(code, c.get("current_price"), db)
            out.append({
                "stock_code": code,
                "stock_name": c.get("stock_name"),
                "source_type": "drilled",
                "source_holding_code": idx["index_code"],
                "amount_cny": None,
                "static_amount_cny": None,
                "shares": c.get("shares_equivalent"),
                "baseline_price": c.get("baseline_price"),
                "current_price": c.get("current_price"),
                "current_price_date": c.get("current_price_date"),
                "est_market_value_cny": c.get("est_market_value_cny"),  # 原币种
                "est_deviation_pct": None,
                "pct_change_3m": pct_3m,
                "pe_ttm_dynamic": pe_v,
                "pb_mrq_dynamic": pb_v,
                "ps_ttm_dynamic": ps_v,
                "dividend_yield": dy_v,
                "metric_basis": "dynamic",
                "fund_currency": _guess_currency_from_code(code),
            })

    return out


@app.get("/api/penetration/full-holding-table")
def get_full_holding_table(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """全持仓表格专用接口：一次返回 undrilled + drilled 聚合，避免重复下钻。

    与分别调用 /full-holding 和 /all-drilled-stocks 相比：
      - 只下钻一次所有指数
      - 快照 / holdings / fund_navs 只加载一次
      - drilled 部分在后端按 stock_code 聚合，避免前端重复合并
      - undrilled 部分不再包含 drilled 成分股，防止重复计算
    """
    from models import (
        AShareFinancialSnapshot, HKShareFinancialSnapshot, Holding, FundIndexMap,
        FundDailyNav,
    )
    from services.drillable_funds import list_drillable_indices, get_index_drill_detail

    # 1) 快照一次性加载
    a_snap_raw = {a.stock_code.split(".")[0]: a for a in
                  db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap_raw = {h.stock_code.split(".")[0]: h for h in
                  db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}

    def _norm_keys(snap_dict):
        for k, v in list(snap_dict.items()):
            snap_dict.setdefault(v.stock_code, v)
        return snap_dict

    a_snap = _norm_keys(a_snap_raw)
    h_snap = _norm_keys(h_snap_raw)

    # 2) Holding 聚合（按代码）
    holdings = db.query(Holding).all()
    by_code: dict[str, dict] = {}
    for h in holdings:
        code = h.security_code
        if code not in by_code:
            by_code[code] = {
                "security_code": code,
                "security_name": h.security_name,
                "quantity": 0.0,
                "amount": 0.0,
                "amount_cny": 0.0,
                "currency": h.currency or "CNY",
                "asset_type": h.asset_type or "",
            }
        acc = by_code[code]
        acc["quantity"] += (h.quantity or 0.0)
        acc["amount"] += (h.amount or 0.0)
        acc["amount_cny"] += (h.amount_cny or 0.0)

    # 3) 可下钻基金
    drillable_codes = {
        m.fund_code for m in
        db.query(FundIndexMap).filter(FundIndexMap.as_of_date == as_of_date).all()
    }

    # 4) 构建 undrilled 行（直接持股 + 未下钻基金 + 现金）
    undrilled_out: list[dict] = []
    for code, acc in by_code.items():
        if code in drillable_codes:
            continue
        source_type = _infer_source_type_from_holding(acc)
        snap = _lookup_snap(code, a_snap, h_snap)
        pe_v = pb_v = ps_v = dy_v = None
        if snap:
            pe_v = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
            pb_v = snap.pb_mrq_dynamic if snap.pb_mrq_dynamic is not None else snap.pb_mrq
            ps_v = snap.ps_ttm_dynamic if snap.ps_ttm_dynamic is not None else snap.ps_ttm
            dy_v = snap.dividend_yield
        est_value, shares, fallback_price = _estimate_market_value_for_holding(
            code, acc, snap, db,
        )
        baseline_price = snap.baseline_price if snap else None
        current_price = snap.current_price if snap else fallback_price
        undrilled_out.append({
            "stock_code": code,
            "stock_name": acc["security_name"],
            "source_type": source_type,
            "amount_cny": acc["amount_cny"],
            "shares": shares,
            "baseline_price": baseline_price,
            "current_price": current_price,
            "est_market_value_cny": est_value,
            "pe_ttm_dynamic": pe_v,
            "pb_mrq_dynamic": pb_v,
            "ps_ttm_dynamic": ps_v,
            "dividend_yield": dy_v,
            "fund_currency": acc["currency"],
        })

    # 5) 一次性加载所有相关 fund NAVs
    all_fund_codes = sorted(drillable_codes)
    fund_navs_map: dict[str, dict] = {}
    if all_fund_codes:
        nav_rows = (
            db.query(FundDailyNav)
            .filter(
                FundDailyNav.fund_code.in_(all_fund_codes),
                FundDailyNav.trade_date.in_([as_of_date, date(2026, 6, 18)]),
            )
            .all()
        )
        for fc in all_fund_codes:
            fund_navs_map[fc] = {"nav_529": None, "cumnav_529": None, "nav_618": None, "cumnav_618": None}
        for r in nav_rows:
            fc = r.fund_code
            if r.trade_date == as_of_date:
                fund_navs_map[fc]["nav_529"] = r.nav
                fund_navs_map[fc]["cumnav_529"] = r.accumulated_nav
            elif r.trade_date == date(2026, 6, 18):
                fund_navs_map[fc]["nav_618"] = r.nav
                fund_navs_map[fc]["cumnav_618"] = r.accumulated_nav

    # 6) 下钻所有指数一次，并按 stock_code 聚合
    holdings_agg = {code: info for code, info in by_code.items() if info["quantity"] > 0}
    drilled_map: dict[str, dict] = {}
    indices = list_drillable_indices(db, as_of_date)
    for idx in indices:
        detail = get_index_drill_detail(
            db, idx["index_code"], as_of_date,
            holdings_agg=holdings_agg,
            fund_navs=fund_navs_map,
            a_snap=a_snap,
            h_snap=h_snap,
        )
        if "constituents" not in detail:
            continue
        idx_code = idx["index_code"]
        for c in detail["constituents"]:
            code = c["stock_code"]
            if code not in drilled_map:
                drilled_map[code] = {
                    "stock_code": code,
                    "stock_name": c.get("stock_name"),
                    "shares_equivalent": 0,
                    "current_price": c.get("current_price"),
                    "baseline_price": c.get("baseline_price"),
                    "est_market_value_cny": 0.0,
                    "pe_ttm": c.get("pe_ttm"),
                    "pb_mrq": c.get("pb_mrq"),
                    "ps_ttm": c.get("ps_ttm"),
                    "dividend_yield": c.get("dividend_yield"),
                    "indices": set(),
                }
            acc = drilled_map[code]
            acc["shares_equivalent"] += (c.get("shares_equivalent") or 0)
            acc["est_market_value_cny"] += (c.get("est_market_value_cny") or 0)
            acc["indices"].add(idx_code)
            for k in ("current_price", "baseline_price", "pe_ttm", "pb_mrq", "ps_ttm", "dividend_yield"):
                if acc.get(k) is None and c.get(k) is not None:
                    acc[k] = c.get(k)

    for s in drilled_map.values():
        s["indices"] = sorted(s["indices"])
        s["est_market_value_cny"] = round(s["est_market_value_cny"], 4)

    return {
        "as_of_date": as_of_date.isoformat(),
        "undrilled": undrilled_out,
        "drilled": drilled_map,
    }


@app.get("/api/penetration/top10-holdings")
def get_top10_holdings(
    as_of_date: date = Query(...),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """前 N 大底层持仓（穿透 + 未穿透，仅股票，不含未穿透基金）。

    口径：
      - 合并 undrilled.direct_stock + drilled 全部行
      - 排除 undrilled_fund / cash（用户口径：「只统计股票，不统计未穿透的基金」）
      - **按前收盘口径**估值：shares × PriceCache[stock_code, prev_trade_date].close_px × fx
        — prev_trade_date = MAX(PriceCache.trade_date) WHERE trade_date < today
        — 与「当日涨幅」分母同口径，确保「前 10 大」的市值 = 分母快照的市值基线
      - 缺失 prev_close 时 fallback Holding.price（与「当日涨幅」一致）
      - 排序：est_market_value_cny DESC，取 limit
      - 返回字段：stock_code, stock_name, source (direct|drilled), est_market_value_cny,
                  weight_pct, prev_close, prev_close_date, currency, pe_ttm, pe_source
    """
    from models import (
        AShareFinancialSnapshot, HKShareFinancialSnapshot, Holding,
        FundIndexMap, FundDailyNav, PriceCache, ExchangeRate,
    )
    from services.drillable_funds import list_drillable_indices, get_index_drill_detail
    from sqlalchemy import func as _func

    # ---- 1. 拿 undrilled + drilled 全量数据（复用 full-holding-table 的核心逻辑）----
    a_snap_raw = {a.stock_code.split(".")[0]: a for a in
                  db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap_raw = {h.stock_code.split(".")[0]: h for h in
                  db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    for k, v in list(a_snap_raw.items()):
        a_snap_raw.setdefault(v.stock_code, v)
    for k, v in list(h_snap_raw.items()):
        h_snap_raw.setdefault(v.stock_code, v)

    by_code: dict[str, dict] = {}
    for h in db.query(Holding).all():
        c = h.security_code
        if c not in by_code:
            by_code[c] = {"security_code": c, "security_name": h.security_name,
                          "quantity": 0.0, "amount": 0.0, "amount_cny": 0.0,
                          "currency": h.currency or "CNY", "asset_type": h.asset_type or ""}
        a = by_code[c]
        a["quantity"] += (h.quantity or 0.0)
        a["amount"] += (h.amount or 0.0)
        a["amount_cny"] += (h.amount_cny or 0.0)

    drillable_codes = {m.fund_code for m in db.query(FundIndexMap)
                       .filter(FundIndexMap.as_of_date == as_of_date).all()}

    # ---- 2. 收集候选行 ----
    # candidate = (stock_code, stock_name, shares, currency, pe_ttm, pe_source, source_label)
    candidates: list[dict] = []

    # 2a. undrilled 直股 — _infer_source_type_from_holding 排除 undrilled_fund/cash
    for code, acc in by_code.items():
        if code in drillable_codes:
            continue
        if _infer_source_type_from_holding(acc) != "direct_stock":
            continue
        snap = _lookup_snap(code, a_snap_raw, h_snap_raw)
        pe_v, pe_src = None, None
        if snap:
            pe_v = snap.pe_ttm_dynamic if snap.pe_ttm_dynamic is not None else snap.pe_ttm
            pe_src = "snapshot"
        # shares = quantity
        candidates.append({
            "stock_code": code,
            "stock_name": acc["security_name"],
            "shares": float(acc["quantity"] or 0.0),
            "currency": acc["currency"],
            "pe_ttm": pe_v,
            "pe_source": pe_src,
            "source": "direct",
        })

    # 2b. drilled — 复用 list_drillable_indices + get_index_drill_detail 一次性聚合
    holdings_agg = {c: info for c, info in by_code.items() if info["quantity"] > 0}
    fund_navs_map: dict[str, dict] = {}
    if drillable_codes:
        nav_rows = (db.query(FundDailyNav)
                    .filter(FundDailyNav.fund_code.in_(sorted(drillable_codes)),
                            FundDailyNav.trade_date.in_([as_of_date, date(2026, 6, 18)]))
                    .all())
        for fc in drillable_codes:
            fund_navs_map[fc] = {"nav_529": None, "cumnav_529": None,
                                 "nav_618": None, "cumnav_618": None}
        for r in nav_rows:
            fc = r.fund_code
            if r.trade_date == as_of_date:
                fund_navs_map[fc]["nav_529"] = r.nav
                fund_navs_map[fc]["cumnav_529"] = r.accumulated_nav
            elif r.trade_date == date(2026, 6, 18):
                fund_navs_map[fc]["nav_618"] = r.nav
                fund_navs_map[fc]["cumnav_618"] = r.accumulated_nav

    indices = list_drillable_indices(db, as_of_date)
    drilled_acc: dict[str, dict] = {}
    for idx in indices:
        detail = get_index_drill_detail(
            db, idx["index_code"], as_of_date,
            holdings_agg=holdings_agg,
            fund_navs=fund_navs_map,
            a_snap=a_snap_raw, h_snap=h_snap_raw,
        )
        if "constituents" not in detail:
            continue
        for c in detail["constituents"]:
            code = c["stock_code"]
            if code not in drilled_acc:
                drilled_acc[code] = {
                    "stock_code": code,
                    "stock_name": c.get("stock_name"),
                    "shares": 0.0,
                    "currency": "CNY",  # default; refined by suffix below
                    "pe_ttm": c.get("pe_ttm"),
                    "pe_source": "drill",
                }
            acc = drilled_acc[code]
            acc["shares"] += (c.get("shares_equivalent") or 0.0)
            if acc["pe_ttm"] is None and c.get("pe_ttm") is not None:
                acc["pe_ttm"] = c.get("pe_ttm")
            # 推断币种
            cu = code.upper()
            if cu.endswith(".HK"):
                acc["currency"] = "HKD"
            elif cu in ("NVDA", "GOOGL", "AAPL", "MSFT", "AMZN", "TSLA", "AMD", "INTC", "SNDK", "QQQ"):
                acc["currency"] = "USD"
            # else 保持 CNY（A股 ETF / 指数成分股）
            # 名称以第一次为准
            if not acc.get("stock_name") and c.get("stock_name"):
                acc["stock_name"] = c.get("stock_name")

    for d in drilled_acc.values():
        d["source"] = "drilled"
        candidates.append(d)

    # ---- 3. 前一日（已闭环）收盘价映射 ----
    fx_to_cny = {"CNY": 1.0}
    for fc in ("USD", "HKD", "CAD"):
        r = (db.query(ExchangeRate)
             .filter(ExchangeRate.from_currency == fc, ExchangeRate.to_currency == "CNY")
             .order_by(ExchangeRate.rate_date.desc()).first())
        if r:
            fx_to_cny[fc] = r.rate

    # prev_trade_date = 最近一个已闭环交易日（PriceCache 落库即代表当天收盘价生成）
    latest_td = db.query(_func.max(PriceCache.trade_date)).scalar()
    prev_td = None
    if latest_td:
        prev_td = (db.query(_func.max(PriceCache.trade_date))
                   .filter(PriceCache.trade_date < latest_td).scalar())
    prev_px_map: dict[str, float] = {}
    if prev_td and candidates:
        codes = list({c["stock_code"] for c in candidates})
        rows = (db.query(PriceCache.stock_code, PriceCache.close_px)
                .filter(PriceCache.trade_date == prev_td,
                        PriceCache.stock_code.in_(codes)).all())
        prev_px_map = {r[0]: float(r[1]) for r in rows if r[1] is not None}

    # Holding.price 兜底（与「当日涨幅」denominator 一致）
    holding_px: dict[str, float] = {}
    for h in db.query(Holding).all():
        if h.security_code and h.price:
            holding_px.setdefault(h.security_code, float(h.price))

    # ---- 4. 估值 + 排序 ----
    rows_out = []
    for c in candidates:
        code = c["stock_code"]
        prev_close = prev_px_map.get(code) or holding_px.get(code)
        if not prev_close:
            continue  # 既无前收也无 holding 价 — 跳过
        shares = float(c["shares"] or 0.0)
        if shares <= 0:
            continue
        cur = c["currency"] or "CNY"
        est = prev_close * shares * fx_to_cny.get(cur, 1.0)
        rows_out.append({
            "stock_code": code,
            "stock_name": c.get("stock_name"),
            "source": c["source"],          # "direct" | "drilled"
            "currency": cur,
            "shares": shares,
            "prev_close": prev_close,
            "est_market_value_cny": round(est, 2),
            "pe_ttm": c["pe_ttm"],          # null = 无数据（前端展示 "-"）
            "pe_source": c["pe_source"],
        })

    rows_out.sort(key=lambda r: r["est_market_value_cny"], reverse=True)
    top = rows_out[:limit]
    # 权重分母 = 总资产（总览页面第一个卡片格的 Σ Holding.amount_cny），与「总资产」cell 同源
    # 这样 stock 估值（按前收）/ 总资产（按当前价）= 单只股票在组合中的占比（按市值）
    total_assets_cny = (db.query(_func.coalesce(_func.sum(Holding.amount_cny), 0)).scalar() or 0)
    for r in top:
        r["weight_pct"] = round((r["est_market_value_cny"] / total_assets_cny * 100), 4) if total_assets_cny else 0.0

    return {
        "as_of_date": as_of_date.isoformat(),
        "prev_close_date": prev_td.isoformat() if prev_td else None,
        "limit": limit,
        "total_assets_cny": round(total_assets_cny, 2),          # 权重分母（=总览「总资产」cell）
        "candidates_total": len(rows_out),
        "items": top,
    }


@app.get("/api/penetration/dimension-drilled")
def get_dimension_drilled(
    dim: str = Query(...),
    as_of_date: date = Query(...),
    market: str = Query("A+H", pattern="^(A\\+H|A|H)$"),
    db: Session = Depends(get_db),
):
    """下钻证券维度聚合 + CSI300 对照（仅用于需要 drill 视角的维度，如 swy1）。

    组合部分：仅对下钻证券（drilled constituents）按 dim 列聚合，金额按最新汇率折算为 CNY，
             PE 采用虚拟盈利法 Σamount / Σ(amount/PE_dynamic)。
    CSI300 部分：使用 csi300_constituent_snapshot 的最新权重作为分母，同行业算法计算 PE。
    market=A 时仅保留 A 股（.SH/.SZ），market=H 时仅保留港股（.HK）。

    返回包含 stock_details，用于前端点击行业行展开下钻证券明细。
    """
    from services.drillable_funds import get_all_drilled_stocks, list_drillable_indices
    from models import (
        AShareFinancialSnapshot, HKShareFinancialSnapshot,
        Csi300ConstituentSnapshot, ExchangeRate,
        FundIndexMap, FundDailyNav, Holding,
    )

    DIM_COL_DRILLED = {
        "swy1": "swy_l1", "swy2": "swy_l2", "swy3": "swy_l3", "swy4": "swy_l4",
        "csi1": "csi_l1", "csi2": "csi_l2", "csi3": "csi_l3", "csi4": "csi_l4",
        "se1": "se_l1", "se2": "se_l2", "se3": "se_l3", "se4": "se_l4",
        "l1": "swy_l1", "l2": "swy_l2",
        "chain": "chain_position", "growth_tier": "growth_tier", "competition": "competition",
    }
    if dim not in DIM_COL_DRILLED:
        raise HTTPException(status_code=400, detail=f"Unsupported dim: {dim}")
    col = DIM_COL_DRILLED[dim]

    # 1) 快照（用于 dim 分类 + CSI300 PE/PB/PS 补全）
    a_snap = {a.stock_code.split(".")[0]: a for a in
              db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap = {h.stock_code.split(".")[0]: h for h in
              db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}

    def _norm_keys(snap_dict):
        for k, v in list(snap_dict.items()):
            snap_dict.setdefault(v.stock_code, v)
        return snap_dict

    a_snap = _norm_keys(a_snap)
    h_snap = _norm_keys(h_snap)

    def _norm_bucket_key(k):
        if not k or k in ("--", "—", "nan", "None", "", "其他"):
            return "其他"
        return k

    def _resolve_snap(code):
        norm = code.split(".")[0]
        snap = a_snap.get(norm) or h_snap.get(norm)
        if not snap and norm.isdigit():
            snap = h_snap.get(norm.zfill(5))
        return snap

    # 2) 预加载 holdings / fund_navs / indices，避免 get_all_drilled_stocks 重复查库
    drillable_codes = {
        m.fund_code for m in
        db.query(FundIndexMap).filter(FundIndexMap.as_of_date == as_of_date).all()
    }
    holdings_agg: dict[str, dict] = {}
    for h in db.query(Holding).all():
        code = h.security_code
        if code not in holdings_agg:
            holdings_agg[code] = {
                "fund_code": code,
                "quantity": 0.0,
                "amount_cny": 0.0,
                "asset_type": (h.asset_type or "").lower(),
            }
        holdings_agg[code]["quantity"] += (h.quantity or 0.0)
        holdings_agg[code]["amount_cny"] += (h.amount_cny or 0.0)
    holdings_agg = {k: v for k, v in holdings_agg.items() if v["quantity"] > 0}

    fund_navs_map: dict[str, dict] = {}
    if drillable_codes:
        nav_rows = (
            db.query(FundDailyNav)
            .filter(
                FundDailyNav.fund_code.in_(list(drillable_codes)),
                FundDailyNav.trade_date.in_([as_of_date, date(2026, 6, 18)]),
            )
            .all()
        )
        for fc in drillable_codes:
            fund_navs_map[fc] = {"nav_529": None, "cumnav_529": None, "nav_618": None, "cumnav_618": None}
        for r in nav_rows:
            fc = r.fund_code
            if r.trade_date == as_of_date:
                fund_navs_map[fc]["nav_529"] = r.nav
                fund_navs_map[fc]["cumnav_529"] = r.accumulated_nav
            elif r.trade_date == date(2026, 6, 18):
                fund_navs_map[fc]["nav_618"] = r.nav
                fund_navs_map[fc]["cumnav_618"] = r.accumulated_nav

    indices = list_drillable_indices(db, as_of_date)

    # 3) 下钻证券聚合结果（一次性预加载）
    drilled_resp = get_all_drilled_stocks(
        db, as_of_date,
        indices=indices,
        holdings_agg=holdings_agg,
        fund_navs=fund_navs_map,
        a_snap=a_snap,
        h_snap=h_snap,
    )
    drilled_stocks = drilled_resp.get("stocks") or []

    # 4) 最新汇率（USD/HKD → CNY）
    fx_rates = {"CNY": 1.0}
    for fc in ("USD", "HKD"):
        rate_row = (
            db.query(ExchangeRate)
            .filter(ExchangeRate.from_currency == fc, ExchangeRate.to_currency == "CNY")
            .order_by(ExchangeRate.rate_date.desc())
            .first()
        )
        if rate_row:
            fx_rates[fc] = rate_row.rate

    def _to_cny(amount, code):
        cur = _guess_currency_from_code(code)
        return amount * fx_rates.get(cur, 1.0)

    def _in_market(code):
        if market == "A+H":
            return True
        c = str(code).upper()
        if market == "A":
            return c.endswith(".SH") or c.endswith(".SZ") or (c.isdigit() and len(c) == 6)
        if market == "H":
            return c.endswith(".HK") or (c.isdigit() and len(c) == 5)
        return True

    # 5) 组合：按下钻证券聚合
    portfolio_buckets: dict[str, dict] = {}
    stock_details: dict[str, list] = {}
    total_amount = 0.0

    for s in drilled_stocks:
        code = s["stock_code"]
        if not _in_market(code):
            continue
        amount_cny = _to_cny(s.get("est_market_value_cny") or 0, code)
        pe = s.get("pe_ttm")
        pb = s.get("pb_mrq")
        ps = s.get("ps_ttm")

        snap = _resolve_snap(code)
        key = _norm_bucket_key(getattr(snap, col, None) if snap else None)

        b = portfolio_buckets.setdefault(key, {"amount": 0.0, "stocks": set(), "virt_pe": 0.0, "virt_pb": 0.0, "virt_ps": 0.0})
        b["amount"] += amount_cny
        b["stocks"].add(code)
        if amount_cny > 0:
            if pe and pe > 0:
                b["virt_pe"] += amount_cny / pe
            if pb and pb > 0:
                b["virt_pb"] += amount_cny / pb
            if ps and ps > 0:
                b["virt_ps"] += amount_cny / ps
        total_amount += amount_cny

        stock_details.setdefault(key, []).append({
            "stock_code": code,
            "stock_name": s.get("stock_name"),
            "shares_equivalent": s.get("shares_equivalent"),
            "current_price": s.get("current_price"),
            "current_price_cny": round(_to_cny(s.get("current_price") or 0, code), 4) if s.get("current_price") else None,
            "currency": _guess_currency_from_code(code),
            "amount_cny": round(amount_cny, 4),
            "pe_ttm": pe,
            "pb_mrq": pb,
            "ps_ttm": ps,
        })

    # 5) CSI300：按最新指数权重聚合；行业/PE/PB/PS 从金融快照补
    csi300_buckets: dict[str, dict] = {}
    csi_total_weight = 0.0
    csi_rows = db.query(Csi300ConstituentSnapshot).filter_by(as_of_date=as_of_date).all()
    for r in csi_rows:
        if not _in_market(r.stock_code):
            continue
        snap = _resolve_snap(r.stock_code)
        key = _norm_bucket_key(getattr(snap, col, None) if snap else None)
        if key == "其他":
            key = _norm_bucket_key(getattr(r, col, None))
        weight = r.weight or 0.0
        pe = snap.pe_ttm_dynamic if snap and snap.pe_ttm_dynamic is not None else (snap.pe_ttm if snap else None)
        pb = snap.pb_mrq_dynamic if snap and snap.pb_mrq_dynamic is not None else (snap.pb_mrq if snap else None)
        ps = snap.ps_ttm_dynamic if snap and snap.ps_ttm_dynamic is not None else (snap.ps_ttm if snap else None)
        b = csi300_buckets.setdefault(key, {"weight": 0.0, "stocks": set(), "virt_pe": 0.0, "virt_pb": 0.0, "virt_ps": 0.0})
        b["weight"] += weight
        b["stocks"].add(r.stock_code)
        if weight > 0:
            if pe and pe > 0:
                b["virt_pe"] += weight / pe
            if pb and pb > 0:
                b["virt_pb"] += weight / pb
            if ps and ps > 0:
                b["virt_ps"] += weight / ps
        csi_total_weight += weight

    # 6) 构建输出行
    portfolio_rows = []
    for key, b in portfolio_buckets.items():
        portfolio_rows.append({
            "key": key,
            "stock_count": len(b["stocks"]),
            "amount_cny": round(b["amount"], 4),
            "weight_pct": round(b["amount"] / total_amount * 100, 4) if total_amount else 0.0,
            "pe_weighted": round(b["amount"] / b["virt_pe"], 4) if b["virt_pe"] else None,
            "pb_weighted": round(b["amount"] / b["virt_pb"], 4) if b["virt_pb"] else None,
            "ps_weighted": round(b["amount"] / b["virt_ps"], 4) if b["virt_ps"] else None,
        })

    csi300_rows = []
    for key, b in csi300_buckets.items():
        csi300_rows.append({
            "key": key,
            "stock_count": len(b["stocks"]),
            "weight_pct": round(b["weight"] / csi_total_weight * 100, 4) if csi_total_weight else 0.0,
            "pe_weighted": round(b["weight"] / b["virt_pe"], 4) if b["virt_pe"] else None,
            "pb_weighted": round(b["weight"] / b["virt_pb"], 4) if b["virt_pb"] else None,
            "ps_weighted": round(b["weight"] / b["virt_ps"], 4) if b["virt_ps"] else None,
        })

    # 7) 合计
    total_virt_pe = sum(b["virt_pe"] for b in portfolio_buckets.values())
    total_virt_pb = sum(b["virt_pb"] for b in portfolio_buckets.values())
    total_virt_ps = sum(b["virt_ps"] for b in portfolio_buckets.values())
    csi_total_virt_pe = sum(b["virt_pe"] for b in csi300_buckets.values())
    csi_total_virt_pb = sum(b["virt_pb"] for b in csi300_buckets.values())
    csi_total_virt_ps = sum(b["virt_ps"] for b in csi300_buckets.values())

    # 明细权重（以下钻证券合计为分母）
    for key, stocks in stock_details.items():
        for st in stocks:
            st["weight_pct"] = round(st["amount_cny"] / total_amount * 100, 4) if total_amount else 0.0

    return {
        "as_of_date": as_of_date.isoformat(),
        "dim": dim,
        "portfolio": sorted(portfolio_rows, key=lambda r: -r["amount_cny"]),
        "csi300": sorted(csi300_rows, key=lambda r: -r["weight_pct"]),
        "stock_details": stock_details,
        "totals": {
            "portfolio": {
                "stock_count": len({code for b in portfolio_buckets.values() for code in b["stocks"]}),
                "amount_cny": round(total_amount, 4),
                "pe_weighted": round(total_amount / total_virt_pe, 4) if total_virt_pe else None,
                "pb_weighted": round(total_amount / total_virt_pb, 4) if total_virt_pb else None,
                "ps_weighted": round(total_amount / total_virt_ps, 4) if total_virt_ps else None,
            },
            "csi300": {
                "stock_count": len({code for b in csi300_buckets.values() for code in b["stocks"]}),
                "amount_cny": None,
                "pe_weighted": round(csi_total_weight / csi_total_virt_pe, 4) if csi_total_virt_pe else None,
                "pb_weighted": round(csi_total_weight / csi_total_virt_pb, 4) if csi_total_virt_pb else None,
                "ps_weighted": round(csi_total_weight / csi_total_virt_ps, 4) if csi_total_virt_ps else None,
            },
        },
    }


def _infer_source_type_from_holding(acc: dict) -> str:
    """从 holding 推断 source_type (与 full_holding 兼容)."""
    cur = acc.get("currency", "CNY")
    at = (acc.get("asset_type") or "").lower()
    code = acc.get("security_code", "")
    # USD/HKD 一律视为直接持股 (含 QQQ 等美股 ETF)
    if cur in ("USD", "HKD"):
        return "direct_stock"
    # CNY 基金 / 债券 / 黄金 视为未下钻基金
    if code.endswith(".OF") or any(k in at for k in ("fund", "etf", "bond", "gold")):
        return "undrilled_fund"
    # A 股直接持股
    if at in ("a_share_equity", ""):
        return "direct_stock"
    return "undrilled_fund"


def _lookup_snap(code: str, a_snap: dict, h_snap: dict):
    """双键查找快照 (raw code 与 suffixed 都试)."""
    snap = a_snap.get(code) or h_snap.get(code)
    if snap:
        return snap
    norm = code.split(".")[0]
    snap = a_snap.get(norm) or h_snap.get(norm)
    if snap:
        return snap
    if norm.isdigit():
        for k in (norm.zfill(5), norm.zfill(6)):
            snap = a_snap.get(k) or h_snap.get(k)
            if snap:
                return snap
    return None


def _estimate_market_value_for_holding(code: str, acc: dict, snap, db) -> tuple:
    """对未下钻 holding 估算 数量 × 收盘价 (用户口径).

    返回 (est_value_in_original_currency, shares, current_price).
    est_value 仍在原币种下 (前端用 toCNY 折算).
    current_price 可能来自快照或 price_cache (用于前端展示).
    """
    from models import FundDailyNav, PriceCache
    qty = acc.get("quantity", 0.0) or 0.0
    cur = acc.get("currency", "CNY")
    if code.endswith(".OF"):
        # OF 基金: 用最新 NAV (FundDailyNav 优先, 兜底 Holding.price)
        nav = None
        nav_row = (
            db.query(FundDailyNav)
            .filter(FundDailyNav.fund_code == code, FundDailyNav.nav.isnot(None))
            .order_by(FundDailyNav.trade_date.desc())
            .first()
        )
        if nav_row:
            nav = nav_row.nav
        else:
            if qty > 0 and acc.get("amount", 0):
                nav = acc["amount"] / qty
        if qty > 0 and nav and nav > 0:
            return qty * nav, qty, nav
        return acc.get("amount_cny", 0), qty, nav

    # 股票 / ETF: 优先用快照 current_price, 缺失则从 price_cache 兜底
    if snap and snap.current_price and snap.current_price > 0:
        price = snap.current_price
    else:
        price_row = (
            db.query(PriceCache)
            .filter(PriceCache.stock_code == code, PriceCache.close_px.isnot(None))
            .order_by(PriceCache.trade_date.desc())
            .first()
        )
        price = price_row.close_px if price_row else None

    if qty > 0 and price and price > 0:
        return qty * price, qty, price
    return acc.get("amount_cny", 0), qty, price


@app.get("/api/penetration/dimension")
def get_dimension(
    dim: str = Query(..., pattern="^(swy1|swy2|swy3|swy4|csi1|csi2|csi3|csi4|se1|se2|se3|se4|l1|l2|chain|growth_tier|competition)$"),
    as_of_date: date = Query(...),
    market: str = Query("A+H", pattern="^(A\\+H|A|H)$"),
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
    dim: str = Query(..., pattern="^(swy1|swy2|swy3|swy4|csi1|csi2|csi3|csi4|se1|se2|se3|se4|l1|l2|chain|growth_tier|competition)$"),
    key: str = Query(...),
    as_of_date: date = Query(...),
    market: str = Query("A+H", pattern="^(A\\+H|A|H)$"),
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


def _compute_drill_virtual_earnings(stocks: list, fx_rates: dict | None = None) -> dict:
    """Compute virtual-earnings stats from a list of drill-page constituents.

    输入: [{stock_code, shares_equivalent, baseline_price, current_price,
            est_market_value_cny, pe_ttm, pb_mrq, ps_ttm, dividend_yield}, ...]

    算法 (以最新收盘价为口径, 全部以 CNY 为单位):
      amount = est_market_value_cny × fx_rate[原币种]   (current price × shares, 折算为 CNY)
      price_ratio = current_price / baseline_price
      pe_per_stock = pe_ttm × price_ratio
      virt_pe = Σ (amount / pe_per_stock)
      weighted_pe = Σ amount / virt_pe
      股息率 = Σ (amount × dy_per_stock) / Σ amount   where dy_per_stock = dy / price_ratio

    fx_rates: {USD: rate_to_CNY, HKD: rate_to_CNY}; 缺省默认 {USD: 7.18, HKD: 0.92}
    """
    if fx_rates is None:
        fx_rates = {"USD": 7.18, "HKD": 0.92, "CNY": 1.0}

    def to_cny(amount: float, code: str) -> float:
        cur = _guess_currency_from_code(code)
        rate = fx_rates.get(cur, 1.0)
        return amount * rate

    total_amount = 0.0
    virt_pe = virt_pb = virt_ps = 0.0
    sum_dy_weighted = 0.0
    stock_count = 0
    for s in stocks:
        amount_raw = s.get("est_market_value_cny")
        if amount_raw is None or amount_raw <= 0:
            continue
        amount = to_cny(amount_raw, s.get("stock_code", ""))
        baseline = s.get("baseline_price")
        current = s.get("current_price")
        if not (baseline and baseline > 0 and current and current > 0):
            continue
        price_ratio = current / baseline
        pe_v = s.get("pe_ttm")
        pb_v = s.get("pb_mrq")
        ps_v = s.get("ps_ttm")
        dy_v = s.get("dividend_yield")
        pe_per = (pe_v * price_ratio) if (pe_v and pe_v > 0) else None
        pb_per = (pb_v * price_ratio) if (pb_v and pb_v > 0) else None
        ps_per = (ps_v * price_ratio) if (ps_v and ps_v > 0) else None
        dy_per = (dy_v / price_ratio) if (dy_v is not None) else None
        total_amount += amount
        stock_count += 1
        if pe_per and pe_per > 0:
            virt_pe += amount / pe_per
        if pb_per and pb_per > 0:
            virt_pb += amount / pb_per
        if ps_per and ps_per > 0:
            virt_ps += amount / ps_per
        if dy_per is not None:
            sum_dy_weighted += amount * dy_per
    return {
        "stock_count": stock_count,
        "total_amount_cny": round(total_amount, 4),   # 已折算 CNY
        "weighted_pe": round(total_amount / virt_pe, 4) if virt_pe else None,
        "weighted_pb": round(total_amount / virt_pb, 4) if virt_pb else None,
        "weighted_ps": round(total_amount / virt_ps, 4) if virt_ps else None,
        "weighted_dividend_yield": round(sum_dy_weighted / total_amount, 4) if total_amount else None,
        "virtual_earnings": round(virt_pe, 4),
    }


def _guess_currency_from_code(code: str) -> str:
    """与 backend crawlers/exchange_rates.py:guess_currency_from_code 对齐."""
    if not code:
        return "CNY"
    c = str(code).upper()
    if c in {"GOOGL","NVDA","INTC","SNDK","AMD","AAPL","MSFT","AMZN","TSLA","QQQ"}:
        return "USD"
    if c.endswith(".HK") or (c.isdigit() and len(c) == 5):
        return "HKD"
    return "CNY"


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


@app.get("/api/penetration/full-holding-summary")
def get_full_holding_summary(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """全持仓 4 口径估值对比 (虚拟盈利法) — 以最新收盘价计算.

    4 个口径:
      - drilled: 全持仓-下钻部分 全部证券 (drill 页面所有指数的聚合)
      - a:       drilled 中 A 股部分
      - h:       drilled 中港股部分
      - csi300:  CSI 300 指数 (= 与下钻页面的 CSI 300 卡片数值相同, 仅作指标参照系,
                不含金额 / 占比)

    3 张卡片 (drilled / a / h) 算法 (以最新收盘价为口径):
      amount = est_market_value_cny          (current price × shares, 原始币种, 前端折算 CNY)
      price_ratio = current_price / baseline_price
      pe_per_stock = pe_ttm × price_ratio    (PE at latest closing price)
      virt_pe = Σ (amount / pe_per_stock)
      weighted_pe = Σ amount / virt_pe
      股息率 = Σ (amount × dy_per_stock) / Σ amount   where dy_per_stock = dy / price_ratio

    CSI 300 直接复用 list_drillable_indices 返回的 000300 卡片 (5/29 amount × current PE),
    但不返回 金额/占比 字段 (按用户口径: 仅作指标参照系).
    """
    from services.drillable_funds import list_drillable_indices, get_all_drilled_stocks
    from models import AShareFinancialSnapshot, HKShareFinancialSnapshot, ExchangeRate

    indices = list_drillable_indices(db, as_of_date)
    drilled_resp = get_all_drilled_stocks(db, as_of_date)
    all_stocks = drilled_resp.get("stocks") or []

    # 拉最新汇率 (按 from_currency 取最大 rate_date), 用于将 est_market_value_cny 折算 CNY
    fx_rates = {"CNY": 1.0}
    for fc in ("USD", "HKD"):
        rate_row = (
            db.query(ExchangeRate)
            .filter(ExchangeRate.from_currency == fc, ExchangeRate.to_currency == "CNY")
            .order_by(ExchangeRate.rate_date.desc())
            .first()
        )
        if rate_row:
            fx_rates[fc] = rate_row.rate

    # A / HK 检测: 按主快照判断 (drilled stocks 已含 pe_ttm/pb_mrq 等基础指标)
    a_snap_keys = {a.stock_code.split(".")[0] for a in
                   db.query(AShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    h_snap_keys = {h.stock_code.split(".")[0] for h in
                   db.query(HKShareFinancialSnapshot).filter_by(as_of_date=as_of_date).all()}
    a_stocks, h_stocks = [], []
    for s in all_stocks:
        code_norm = s["stock_code"].split(".")[0]
        if code_norm in h_snap_keys:
            h_stocks.append(s)
        elif code_norm in a_snap_keys:
            a_stocks.append(s)
        elif s["stock_code"].upper().endswith(".HK"):
            h_stocks.append(s)
        else:
            a_stocks.append(s)

    drilled_card = _compute_drill_virtual_earnings(all_stocks, fx_rates=fx_rates)
    a_card = _compute_drill_virtual_earnings(a_stocks, fx_rates=fx_rates)
    h_card = _compute_drill_virtual_earnings(h_stocks, fx_rates=fx_rates)

    # CSI 300: 仅指标 (参照系, 不含金额 / 占比)
    csi300_src = next((c for c in indices if c["index_code"] == "000300"), None)
    if csi300_src:
        csi300_card = {
            "stock_count": csi300_src.get("stock_count"),
            "weighted_pe": csi300_src.get("weighted_pe"),
            "weighted_pb": csi300_src.get("weighted_pb"),
            "weighted_ps": csi300_src.get("weighted_ps"),
            "weighted_dividend_yield": csi300_src.get("weighted_dividend_yield"),
        }
    else:
        csi300_card = {
            "stock_count": 0,
            "weighted_pe": None, "weighted_pb": None, "weighted_ps": None,
            "weighted_dividend_yield": None,
        }

    return {
        "as_of_date": as_of_date.isoformat(),
        "drilled": drilled_card,
        "a_only": a_card,
        "h_only": h_card,
        "csi300": csi300_card,
    }


@app.get("/api/penetration/timeseries")
def get_timeseries(
    scope: str = Query("portfolio", pattern="^(portfolio|csi300|both)$"),
    metric: str = Query("pe_weighted", pattern="^(pe_weighted|pb_weighted|ps_weighted|virtual_earnings|total_amount)$"),
    window: int = Query(90),
    db: Session = Depends(get_db),
):
    """序时估值时序（spec §4.6）。"""
    from datetime import timedelta
    from models import AggregationTimeseries
    if window not in (90, 180, 360):
        raise HTTPException(status_code=422, detail=f"window must be 90, 180, or 360; got {window}")
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
    """顶部 KPI bar 实时数据（替换硬编码）。

    三个近期调整（2026-06-23）：
      - csi300_pe：改用下钻页面（drillable_funds.list_drillable_indices）计算的 000300 加权 PE；
        旧实现用的是 Csi300Analyzer 的 baseline（IndexConstituent × StockFinancial.ttm_pe），
        与下钻卡片不一致。
      - daily_change_pct：当日涨幅 = 总览动态市值 / 上一交易日 FullHoldingSnapshot 静态市值 − 1。
        「上一交易日」= MAX(FullHoldingSnapshot.as_of_date)（不限制 < today）。
        — FullHoldingSnapshot 只在「美/中/港三地全部收盘、import 跑完」之后才落库，
        所以 MAX 那一行本身就是「最近一个已闭环交易日」的快照。
        例：6/23 当日（美/中/港尚未全部收盘，或 6/23 snapshot 还没生成）→ MAX=6/22 → 用 6/22 作分母。
        例：6/24 中国开盘前（6/23 snapshot 已落库）→ MAX=6/23 → 用 6/23 作分母。
      - tech_weight_pct：科技占比 = 当前 Holding × PriceCache 最新价 × 汇率（与「主题」pie 同口径），
        按 type2 ∈ {'emerging','us_tech'} 聚合的 CNY 金额 / 总额 × 100。
    """
    from models import FullHoldingSnapshot, AggregationCache, Holding, SecurityMaster, PriceCache, ExchangeRate
    from sqlalchemy import func as _func

    total_amount = db.query(_func.coalesce(_func.sum(FullHoldingSnapshot.amount_cny), 0)).filter(
        FullHoldingSnapshot.as_of_date == as_of_date,
    ).scalar() or 0
    drilled_stocks = db.query(_func.count(_func.distinct(FullHoldingSnapshot.stock_code))).filter(
        FullHoldingSnapshot.as_of_date == as_of_date,
    ).scalar() or 0

    # Read _total row from cache (must be populated first)
    p_total = db.query(AggregationCache).filter(
        AggregationCache.as_of_date == as_of_date,
        AggregationCache.scope == "portfolio",
        AggregationCache.dimension == "l1",
        AggregationCache.key == "_total",
    ).first()

    # ----- 0. 共用：当前汇率 + 当前 Holding + PriceCache 最新价 → 实时动态市值映射 -----
    # 一次拉好给「当日涨幅分子」+「科技占比」共用
    fx_to_cny = {"CNY": 1.0}
    for fc in ("USD", "HKD", "CAD"):
        r = (db.query(ExchangeRate)
             .filter(ExchangeRate.from_currency == fc, ExchangeRate.to_currency == "CNY")
             .order_by(ExchangeRate.rate_date.desc()).first())
        if r:
            fx_to_cny[fc] = r.rate

    holdings = db.query(Holding).all()
    sm_map = {m.security_code: m for m in db.query(SecurityMaster).all()}
    # PriceCache 最新 close_px（按 stock_code）
    latest_px_cache: dict[str, float] = {}
    if holdings:
        codes = [h.security_code for h in holdings if h.security_code]
        sub = (db.query(PriceCache.stock_code,
                        _func.max(PriceCache.trade_date).label("max_d"))
               .filter(PriceCache.stock_code.in_(codes))
               .group_by(PriceCache.stock_code).subquery())
        rows = (db.query(PriceCache.stock_code, PriceCache.trade_date, PriceCache.close_px)
                .join(sub, (PriceCache.stock_code == sub.c.stock_code)
                              & (PriceCache.trade_date == sub.c.max_d))
                .all())
        latest_px_cache = {r[0]: float(r[2]) for r in rows if r[2] is not None}

    def _holding_cny(h) -> float:
        """单只 Holding 的实时 CNY 市值 = quantity × (PriceCache 最新价 or Holding.price) × fx"""
        c = h.security_code
        if not c:
            return 0.0
        px = latest_px_cache.get(c) or (float(h.price) if h.price else 0.0)
        qty = float(h.quantity or 0)
        cur = h.currency or "CNY"
        return px * qty * fx_to_cny.get(cur, 1.0)

    # ----- 1. 300 PE：下钻口径 -----
    csi300_pe = None
    try:
        from services.drillable_funds import list_drillable_indices
        indices = list_drillable_indices(db, as_of_date)
        csi300_card = next((c for c in indices if c.get("index_code") == "000300"), None)
        if csi300_card:
            csi300_pe = csi300_card.get("weighted_pe")
    except Exception as e:
        logging.getLogger(__name__).warning("下钻口径 csi300_pe 计算失败: %s", e)

    # ----- 2. 当日涨幅 = 总览动态市值 / 上一交易日动态市值 − 1 -----
    # 分子：当前实时动态市值（Holding × PriceCache 最新价 × fx）
    # 分母：上一交易日动态市值（Holding × PriceCache[stock_code, prev_trading_day].close_px × fx）
    # 「上一交易日」按用户口径：美/中/港三地全部收盘的最近一个自然日
    # — 用 trading_calendar.expected_trading_dates 反查，或直接用 PriceCache 中
    #   最近一日 max(trade_date) 作为基准（因为 PriceCache 落库即代表当天收盘价已生成）。
    daily_change_pct = None
    daily_change_breakdown = {
        "numerator_cny": None, "denominator_cny": None,
        "prev_trade_date": None, "latest_trade_date": None,
        "missing_prev_codes": [],  # prev 日没有 PriceCache 的 code — 这些用 Holding.price 兜底
    }
    try:
        # 2a. 分子：当前实时动态市值（已在 _holding_cny 里实现 — latest_px_cache 是每个 code 最新一行）
        numerator = sum(_holding_cny(h) for h in holdings)
        # 同时记录「最新 trade_date」作为 today 侧参考
        latest_td_row = db.query(_func.max(PriceCache.trade_date)).scalar() if holdings else None
        daily_change_breakdown["numerator_cny"] = round(numerator, 2)
        daily_change_breakdown["latest_trade_date"] = latest_td_row.isoformat() if latest_td_row else None

        # 2b. 分母：找「最近一个已闭环交易日」的 PriceCache 行，构造上一交易日动态市值
        # 逻辑：取 MAX(PriceCache.trade_date) 中「严格早于 latest_td_row」的最大值
        # — 这就是「最近一个已闭环交易日」（PriceCache 落库即代表那天收盘价已生成）
        if latest_td_row:
            prev_td_row = (db.query(_func.max(PriceCache.trade_date))
                           .filter(PriceCache.trade_date < latest_td_row).scalar())
            if prev_td_row:
                # 取 prev_td_row 的 close_px
                codes = [h.security_code for h in holdings if h.security_code]
                prev_rows = (db.query(PriceCache.stock_code, PriceCache.close_px)
                             .filter(PriceCache.trade_date == prev_td_row,
                                     PriceCache.stock_code.in_(codes))
                             .all())
                prev_px_map = {r[0]: float(r[1]) for r in prev_rows if r[1] is not None}
                missing_codes = [c for c in codes if c not in prev_px_map]
                # 构造 prev 日动态市值（缺价时退到 Holding.price — 与「最新价缺失」一致语义）
                denominator = 0.0
                for h in holdings:
                    if not h.security_code:
                        continue
                    px = prev_px_map.get(h.security_code) or (float(h.price) if h.price else 0.0)
                    qty = float(h.quantity or 0)
                    cur = h.currency or "CNY"
                    denominator += px * qty * fx_to_cny.get(cur, 1.0)
                daily_change_breakdown["denominator_cny"] = round(denominator, 2)
                daily_change_breakdown["prev_trade_date"] = prev_td_row.isoformat()
                daily_change_breakdown["missing_prev_codes"] = missing_codes
                if denominator > 0:
                    daily_change_pct = round((numerator - denominator) / denominator * 100, 4)
    except Exception as e:
        logging.getLogger(__name__).warning("当日涨幅计算失败: %s", e, exc_info=True)

    # ----- 3. 科技占比：当前 Holding 实时 CNY 市值，按 type2 ∈ {emerging, us_tech} 聚合 -----
    # 与「主题」pie chart 完全同口径（displayHoldings.amount_local × type2 聚合）。
    # 注意 type2 编码混合：type2_classifier 写入中文 label（「新兴产业」），手动维护写入英文 key（emerging）。
    # 两者在前端 TYPE2_LABELS 下显示同名 → 视为同一桶。
    _TECH_TYPE2_KEYS = {"emerging", "新兴产业", "us_tech", "美股科技"}
    _EMERGING_KEYS = {"emerging", "新兴产业"}
    _US_TECH_KEYS = {"us_tech", "美股科技"}
    tech_weight_pct = None
    tech_weight_breakdown = {"emerging_cny": 0.0, "us_tech_cny": 0.0, "total_cny": 0.0}
    try:
        emerging_cny = us_tech_cny = total_cny = 0.0
        for h in holdings:
            v = _holding_cny(h)
            total_cny += v
            sm = sm_map.get(h.security_code)
            t2 = sm.type2 if sm else None
            if t2 in _EMERGING_KEYS:
                emerging_cny += v
            elif t2 in _US_TECH_KEYS:
                us_tech_cny += v
        tech_weight_breakdown = {
            "emerging_cny": round(emerging_cny, 2),
            "us_tech_cny": round(us_tech_cny, 2),
            "total_cny": round(total_cny, 2),
        }
        if total_cny > 0:
            tech_weight_pct = round((emerging_cny + us_tech_cny) / total_cny * 100, 4)
    except Exception as e:
        logging.getLogger(__name__).warning("科技占比计算失败: %s", e, exc_info=True)

    return {
        "as_of_date": as_of_date.isoformat(),
        "values": {
            "total_amount_cny": round(total_amount, 2),
            "drilled_stock_count": drilled_stocks,
            "portfolio_pe_weighted": p_total.pe_weighted if p_total else None,
            "portfolio_pb_weighted": p_total.pb_weighted if p_total else None,
            "portfolio_ps_weighted": p_total.ps_weighted if p_total else None,
            "csi300_pe": csi300_pe,                              # 下钻口径
            "daily_change_pct": daily_change_pct,                # 替代 high_growth_weight_pct 语义
            "tech_weight_pct": tech_weight_pct,                  # 替代 midstream_weight_pct 语义
            "tech_weight_breakdown": tech_weight_breakdown,
            "daily_change_breakdown": daily_change_breakdown,
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


# ==================== 数据新鲜度 + 数据预览 ====================

@app.get("/api/data-freshness")
def data_freshness(db: Session = Depends(get_db)):
    """各表最新落库时间 + 今日写入条数。

    用于 API 策略页面「数据新鲜度」面板，让用户一眼看出哪张表已经落后。
    Holding 没有 trade_date / as_of_date，回退到 created_at。
    """
    from models import (
        Holding, PriceCache,
        AShareFinancialSnapshot, HKShareFinancialSnapshot,
        PenetrationSnapshot, FullHoldingSnapshot,
    )

    today = date.today()

    def _stat(model, date_col: str, label: str):
        max_date_val = None
        max_created = None
        rows_today = None
        try:
            if date_col:
                max_date_val = db.query(func.max(getattr(model, date_col))).scalar()
        except Exception:
            max_date_val = None
        try:
            if hasattr(model, "created_at"):
                max_created = db.query(func.max(model.created_at)).scalar()
        except Exception:
            max_created = None
        try:
            if date_col:
                rows_today = db.query(func.count()).select_from(model).filter(
                    getattr(model, date_col) == today
                ).scalar()
        except Exception:
            rows_today = None
        return {
            "table": label,
            "max_date": str(max_date_val) if max_date_val else None,
            "max_created_at": str(max_created) if max_created else None,
            "rows_today": int(rows_today or 0) if rows_today is not None else None,
        }

    tables = [
        _stat(PriceCache, "trade_date", "price_cache"),
        _stat(AShareFinancialSnapshot, "as_of_date", "a_share_snapshot"),
        _stat(HKShareFinancialSnapshot, "as_of_date", "hk_share_snapshot"),
        _stat(PenetrationSnapshot, "as_of_date", "penetration_snapshot"),
        _stat(FullHoldingSnapshot, "as_of_date", "full_holding_snapshot"),
        # Holding 无 trade_date/as_of_date → 用 created_at 作为最后更新时间代理
        _stat(Holding, "created_at", "holding"),
    ]

    return {"as_of": today.isoformat(), "tables": tables}


# Pydantic v1 用 regex=，v2 用 pattern=。本项目 main.py 顶部用 FastAPI 0.x，
# 兼容到 v2 也都能识别；这里保留 regex 作为 v1 fallback。
@app.get("/api/data-preview")
def data_preview(
    table: str = Query(..., pattern="^(price_cache|a_share_snapshot|hk_share_snapshot|holding)$"),
    limit: int = Query(20, ge=1, le=200),
    stock_code: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """最近 N 行预览（按 created_at desc）。

    用于 API 策略页面「数据预览」面板，让用户直观看到落库数据。
    """
    from models import (
        Holding, PriceCache,
        AShareFinancialSnapshot, HKShareFinancialSnapshot,
    )

    model_map = {
        "price_cache": PriceCache,
        "a_share_snapshot": AShareFinancialSnapshot,
        "hk_share_snapshot": HKShareFinancialSnapshot,
        "holding": Holding,
    }
    model = model_map[table]

    q = db.query(model)
    if stock_code:
        if hasattr(model, "stock_code"):
            q = q.filter(model.stock_code == stock_code)
        elif hasattr(model, "security_code"):
            q = q.filter(model.security_code == stock_code)

    if hasattr(model, "created_at"):
        rows = q.order_by(model.created_at.desc()).limit(limit).all()
    else:
        rows = q.order_by(model.id.desc()).limit(limit).all()

    # 只取预设的安全字段列，避免暴露密码 / token 等敏感字段
    safe_cols = {
        "id", "stock_code", "security_code", "trade_date", "as_of_date",
        "close_px", "current_price", "current_price_date",
        "open_px", "high_px", "low_px", "volume",
        "price", "amount", "amount_cny",
        "source", "created_at", "fetched_at",
    }
    cols = [c.name for c in model.__table__.columns if c.name in safe_cols]

    total_rows = db.query(func.count()).select_from(model).scalar()

    return {
        "table": table,
        "rows": [
            {c: (str(getattr(r, c)) if getattr(r, c) is not None else None) for c in cols}
            for r in rows
        ],
        "total_rows": int(total_rows or 0),
    }



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


@app.get("/api/penetration/all-drilled-stocks")
def get_all_drilled_stocks(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """跨所有可下钻指数聚合成分股 (drill 算法同 index-drill, 同一股票求和).

    全持仓页面用：直接持股 + 此处的成分股 → 拆分「下钻 / 未下钻」两段渲染。
    """
    from services.drillable_funds import get_all_drilled_stocks
    return get_all_drilled_stocks(db, as_of_date)


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


# -----------------------------------------------------------------------------
# 分析师研究页面 (Analyst)
# -----------------------------------------------------------------------------

@app.post("/api/admin/analyst/ingest")
def admin_analyst_ingest(db: Session = Depends(get_db)):
    """解析 researcher/ 目录并写入 analyst_* 表。"""
    return ingest_analyst_data(db)


@app.get("/api/analyst/core-companies")
def analyst_core_companies(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """核心公司卡片：报告 + 组合权重 + PE/PB/PS + 最新收盘价。"""
    return get_core_companies(db, as_of_date)


@app.get("/api/analyst/stock/{code}")
def analyst_stock_detail(
    code: str,
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """单只股票详情：来源基金（约当数量）+ 报告内容。"""
    return get_stock_detail(db, code, as_of_date)


@app.get("/api/analyst/industry-chains")
def analyst_industry_chains(
    as_of_date: date = Query(...),
    db: Session = Depends(get_db),
):
    """产业链卡片：只返回当前 portfolio 持仓中的公司。"""
    return get_industry_chains(db, as_of_date)
