from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterator

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import any_, select
from sqlalchemy.orm import Session

from . import access
from . import models as m
from .db import tenant_session
from .models_access import UserAccess
from .rbac import can
from .security import decode_token, now


@dataclass
class Ctx:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    mfa: bool
    step_up_until: int | None
    platform_admin: bool
    ip: str | None = None
    project_scope: list | None = None  # None = every project the role can see; else only these project ids
    modules: set | None = None  # None = every module; else only these module keys (app/access.py)

    @property
    def stepped_up(self) -> bool:
        return bool(self.step_up_until and self.step_up_until > now().timestamp())


def get_ctx(request: Request, authorization: str | None = Header(None)) -> Ctx:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated")
    try:
        c = decode_token(authorization.split(" ", 1)[1])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    # Role, active state and platform-admin flag are re-read on every request, so demotion or
    # deactivation applies immediately rather than when the access token expires.
    from .db import system_session
    with system_session() as db:
        row = db.execute(select(m.User.role, m.User.is_active, m.User.is_platform_admin, m.User.organisation_id)
                         .where(m.User.id == uuid.UUID(c["sub"]))).first()
        acc = db.get(UserAccess, uuid.UUID(c["sub"])) if row and row.role != "owner" else None
    if not row or not row.is_active or str(row.organisation_id) != c["org"]:
        raise HTTPException(401, "User inactive")
    ctx = Ctx(uuid.UUID(c["sub"]), uuid.UUID(c["org"]), row.role, c.get("mfa", False), c.get("su"),
              bool(row.is_platform_admin), request.client.host if request.client else None)
    if acc:
        ctx.project_scope = None if acc.all_projects else list(acc.project_ids or [])
        ctx.modules = None if acc.modules is None else set(acc.modules)
    path = request.url.path
    if ctx.modules is not None and path.startswith("/api/") and not access.allowed(ctx.modules, path[4:]):
        raise HTTPException(403, detail={"code": "module_denied", "message": "Your account doesn't have access to this module — ask an owner or admin."})
    return ctx


def get_db(ctx: Ctx = Depends(get_ctx)) -> Iterator[Session]:
    with tenant_session(ctx.org_id, ctx.user_id, ctx.role) as db:
        # Deactivated users lose access immediately, even with an unexpired token.
        active = db.scalar(select(m.User.is_active).where(m.User.id == ctx.user_id))
        if not active:
            raise HTTPException(401, "User inactive")
        yield db


def require(perm: str):
    def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        if not can(ctx.role, perm):
            raise HTTPException(403, f"Your role ({ctx.role}) lacks the '{perm}' permission")
        return ctx
    return dep


def deny_client(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    """Org-wide views (team, jobs, approvals, automation) are not available to client users."""
    if ctx.role == "client":
        raise HTTPException(403, "Not available to client users")
    return ctx


def require_step_up(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if not ctx.stepped_up:
        raise HTTPException(403, detail={"code": "step_up_required",
                                         "message": "Re-authenticate (password + MFA) to access credentials"})
    return ctx


def require_platform_admin(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if not ctx.platform_admin:
        raise HTTPException(403, "Platform administrators only")
    return ctx


def project_guard(db: Session, ctx: Ctx, project_id) -> m.Project:
    p = db.get(m.Project, project_id)  # RLS already limits to this org
    if not p:
        raise HTTPException(404, "Project not found")
    if ctx.role == "client" and ctx.user_id not in (p.client_user_ids or []):
        raise HTTPException(404, "Project not found")
    if ctx.project_scope is not None and p.id not in ctx.project_scope:
        raise HTTPException(404, "Project not found")
    return p


def visible_project_ids(db: Session, ctx: Ctx):
    q = select(m.Project.id)
    if ctx.role == "client":
        q = q.where(any_(m.Project.client_user_ids) == ctx.user_id)
    if ctx.project_scope is not None:
        q = q.where(m.Project.id.in_(ctx.project_scope or [None]))
    return q


def log(db: Session, ctx: Ctx | None, action: str, target: str | None = None, meta: dict | None = None,
        org_id=None):
    db.add(m.ActivityLog(organisation_id=org_id or ctx.org_id, actor_id=ctx.user_id if ctx else None,
                         action=action, target=target, meta=meta or {}, ip=ctx.ip if ctx else None))


def notify(db: Session, org_id, kind: str, title: str, body: str | None = None, link: str | None = None,
           user_id=None):
    db.add(m.Notification(organisation_id=org_id, user_id=user_id, kind=kind, title=title, body=body, link=link))
