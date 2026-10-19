"""Per-user access rights: which projects and which modules each team member can use."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import access
from .. import models as m
from ..deps import Ctx, get_ctx, get_db, log, require
from ..models_access import UserAccess
from ..rbac import RANK

router = APIRouter(tags=["access"])


def _catalog():
    from ..spec.store import active_spec
    return access.catalog(active_spec())


@router.get("/access/me")
def my_access(db=Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    """Effective access for the signed-in user (the sidebar hides modules that aren't allowed)."""
    return {"role": ctx.role, "all_projects": ctx.project_scope is None, "project_ids": [str(p) for p in ctx.project_scope or []],
            "modules": sorted(ctx.modules) if ctx.modules is not None else None}


@router.get("/access/modules")
def modules(ctx: Ctx = Depends(require("manage_users"))):
    return _catalog()


@router.get("/access/users")
def users(db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    rows = {a.user_id: a for a in db.scalars(select(UserAccess))}
    out = []
    for u in db.scalars(select(m.User).order_by(m.User.created_at)):
        a = rows.get(u.id)
        out.append({"user_id": str(u.id), "name": u.name, "email": u.email, "role": u.role, "is_active": u.is_active,
                    "restricted": bool(a and (not a.all_projects or a.modules is not None)), "editable": u.role != "owner" and RANK[u.role] <= RANK[ctx.role],
                    "all_projects": a.all_projects if a else True, "project_ids": [str(p) for p in (a.project_ids if a else [])],
                    "modules": a.modules if a else None})
    return out


class AccessIn(BaseModel):
    all_projects: bool = True
    project_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    modules: list[str] | None = Field(None, max_length=200)


@router.put("/access/users/{uid}")
def set_access(uid: uuid.UUID, body: AccessIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    u = db.get(m.User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    if u.role == "owner":
        raise HTTPException(400, "Owners always have full access")
    if RANK[u.role] > RANK[ctx.role] or (u.role == ctx.role and ctx.role != "owner"):
        raise HTTPException(403, "You can only change access for roles below your own")
    if u.id == ctx.user_id:
        raise HTTPException(400, "You can't change your own access")
    keys = {x["key"] for x in _catalog()}
    if body.modules is not None:
        bad = [k for k in body.modules if k not in keys]
        if bad:
            raise HTTPException(400, f"Unknown modules: {bad[:5]}")
    pids = list(dict.fromkeys(body.project_ids))
    if pids:
        found = {p for (p,) in db.execute(select(m.Project.id).where(m.Project.id.in_(pids)))}
        if len(found) != len(pids):
            raise HTTPException(400, "Unknown project id")
    a = db.get(UserAccess, uid) or UserAccess(user_id=uid, organisation_id=ctx.org_id)
    db.add(a)
    a.all_projects, a.project_ids = body.all_projects, ([] if body.all_projects else pids)
    a.modules = sorted(set(body.modules)) if body.modules is not None else None
    a.updated_by = ctx.user_id
    log(db, ctx, "user.access_changed", f"user:{uid}", {"all_projects": a.all_projects, "projects": len(a.project_ids),
                                                         "modules": None if a.modules is None else len(a.modules)})
    return {"ok": True}
