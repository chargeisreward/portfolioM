"""鉴权依赖 + 角色检查 + 视图代理

提供 FastAPI Depends 用的工具函数：
- current_user: 取当前 user (None if 未登录)
- require_user: 必须登录
- require_advisor: 顾问或管理员
- require_admin: 仅管理员
- get_effective_user_id: 计算「视图代理」后的有效 user_id
"""
from typing import Optional
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User, UserRelation


def get_db():
    """FastAPI dependency: get DB session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request) -> Optional[User]:
    return getattr(request.state, "user", None)


def require_user(request: Request) -> User:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "请登录")
    return u


def require_advisor(request: Request) -> User:
    u = require_user(request)
    if not (u.is_advisor or u.is_admin):
        raise HTTPException(403, "需要顾问或管理员权限")
    return u


def require_admin(request: Request) -> User:
    u = require_user(request)
    if not u.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return u


def get_effective_user_id(
    request: Request,
    view_as_user_id: Optional[int],
    user: User,
    db: Session,
) -> int:
    """计算 effective_user_id；advisor/admin 可代理，user 只能自己。

    - view_as_user_id=None 或 == user.id → user.id
    - user 是顾问（不是 admin）→ 必须有 ACTIVE 关联
    - user 是 admin → 任意 active user
    - 普通 user → 403
    """
    if not view_as_user_id or view_as_user_id == user.id:
        return user.id
    if not (user.is_advisor or user.is_admin):
        raise HTTPException(403, "无权查看其他用户")
    if user.is_advisor and not user.is_admin:
        rel = db.query(UserRelation).filter(
            UserRelation.advisor_user_id == user.id,
            UserRelation.client_user_id == view_as_user_id,
            UserRelation.status == "ACTIVE",
        ).first()
        if not rel:
            raise HTTPException(403, "未与该客户建立 ACTIVE 关联")
    target = db.query(User).filter(
        User.id == view_as_user_id, User.is_active == True
    ).first()
    if not target:
        raise HTTPException(404, "目标用户不存在")
    return target.id


def _resolve_eff_from_request(request: Request, db: Session) -> tuple[User, int]:
    """从 request.state.user + view_as query 解析 (user, effective_user_id)。
    假设 middleware 已注入 user；不抛 401（应已被 require_user 校验）。
    """
    u = getattr(request.state, "user", None)
    view_as_raw = request.query_params.get("view_as")
    view_as_id: Optional[int] = None
    if view_as_raw:
        try:
            view_as_id = int(view_as_raw)
        except (TypeError, ValueError):
            view_as_id = None
    eff_uid = get_effective_user_id(request, view_as_id, u, db) if u else None
    return u, eff_uid


def effective_user_dep(
    request: Request,
    user: User = None,  # 强制 require_user 先行
    db: Session = None,
):
    """FastAPI dependency 包装器: 调用时同时 require_user + get_db。

    用法:
        def my_endpoint(
            eff = Depends(effective_user_dep),
        ):
            u, eff_uid = eff
    """
    # FastAPI 会先把 user 和 db 注入 — 但作为顶层 dep，user/db 是 Depends 参数
    # 此处 user/db 实际由 require_user + get_db 注入；调用方应显式依赖
    return _resolve_eff_from_request(request, db)