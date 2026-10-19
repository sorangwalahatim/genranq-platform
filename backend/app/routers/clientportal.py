"""Client Portal: client-facing dashboard, tasks, messages, internal space, settings.

SECURITY — enforced on every endpoint:
  * clients only ever see projects in their client_user_ids (project_guard),
  * clients need an explicit portal right (view_reports / view_tasks / comment),
  * internal notes (is_internal=true) are team-only via deny_client,
  * task payloads expose safe fields only — no costs, no internal notes,
  * dashboard data comes from portalsvc, which never invents numbers.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from .. import models as m
from .. import models_clientportal as pm
from ..deps import Ctx, deny_client, get_db, log, project_guard, require
from ..security import now
from ..services import portalsvc
from ..util import to_dict

router = APIRouter(tags=["clientportal"])

RIGHTS_DEFAULT = {"view_reports": False, "view_tasks": False, "comment": False, "approve_content": False}
SAFE_TASK_FIELDS = ("id", "title", "status", "priority", "due_date", "workstream", "labels", "created_at", "updated_at")


def _settings_row(db, project_id: uuid.UUID, user_id: uuid.UUID):
    return db.scalar(select(pm.PortalSettings).where(pm.PortalSettings.project_id == project_id,
                                                     pm.PortalSettings.client_user_id == user_id))


def _rights(db, project_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    r = dict(RIGHTS_DEFAULT)
    s = _settings_row(db, project_id, user_id)
    if s and s.rights:
        r.update({k: bool(v) for k, v in s.rights.items() if k in r})
    return r


def _need(db, ctx: Ctx, project_id: uuid.UUID, right: str) -> None:
    """Clients need an explicit portal right; team members pass through."""
    if ctx.role != "client":
        return
    if not _rights(db, project_id, ctx.user_id).get(right):
        raise HTTPException(403, f"Your portal access does not include '{right.replace('_', ' ')}' — ask your agency to enable it.")


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class ShareIn(BaseModel):
    project_id: uuid.UUID
    email: str = Field(min_length=3, max_length=320)
    period: str = "last_30_days"


class RightsIn(BaseModel):
    view_reports: bool = True
    view_tasks: bool = True
    comment: bool = True
    approve_content: bool = False


class SettingsIn(BaseModel):
    client_user_id: uuid.UUID
    rights: RightsIn


# ------------------------------------------------------------------ dashboard & tasks
@router.get("/portal/dashboard")
def portal_dashboard(project_id: uuid.UUID, days: int = Query(90, ge=7, le=365),
                     db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    _need(db, ctx, project_id, "view_reports")
    return portalsvc.get_dashboard_data(db, project_id, days)


@router.get("/portal/tasks")
def portal_tasks(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    _need(db, ctx, project_id, "view_tasks")
    tasks = db.scalars(select(m.Task).where(m.Task.project_id == project_id)
                       .order_by(m.Task.status, desc(m.Task.updated_at)).limit(500)).all()
    return [{k: getattr(t, k) for k in SAFE_TASK_FIELDS} for t in tasks]


# ------------------------------------------------------------------ client <-> agency thread
@router.get("/portal/messages")
def list_messages(project_id: uuid.UUID, limit: int = Query(200, le=500),
                  db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    _need(db, ctx, project_id, "comment")
    q = (select(pm.PortalMessage, m.User)
         .join(m.User, pm.PortalMessage.author_id == m.User.id, isouter=True)
         .where(pm.PortalMessage.project_id == project_id, pm.PortalMessage.is_internal == False)  # noqa: E712
         .order_by(pm.PortalMessage.created_at).limit(limit))
    return [{"id": str(msg.id), "body": msg.body, "created_at": msg.created_at,
             "author_name": (u.name or u.email) if u else "Unknown",
             "author_role": u.role if u else None, "mine": bool(u and u.id == ctx.user_id)}
            for msg, u in db.execute(q)]


@router.post("/portal/messages")
def post_message(project_id: uuid.UUID, body: MessageIn, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    _need(db, ctx, project_id, "comment")
    msg = pm.PortalMessage(organisation_id=ctx.org_id, project_id=project_id, author_id=ctx.user_id,
                           body=body.body.strip(), is_internal=False)
    db.add(msg)
    db.flush()
    log(db, ctx, "portal.message", f"project:{project_id}", {"message_id": str(msg.id)})
    return to_dict(msg)


# ------------------------------------------------------------------ team-only internal space
@router.get("/portal/internal-notes")
def list_internal_notes(project_id: uuid.UUID, limit: int = Query(200, le=500),
                        db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    project_guard(db, ctx, project_id)
    q = (select(pm.PortalMessage, m.User)
         .join(m.User, pm.PortalMessage.author_id == m.User.id, isouter=True)
         .where(pm.PortalMessage.project_id == project_id, pm.PortalMessage.is_internal == True)  # noqa: E712
         .order_by(desc(pm.PortalMessage.created_at)).limit(limit))
    return [{"id": str(msg.id), "body": msg.body, "created_at": msg.created_at,
             "author_name": (u.name or u.email) if u else "Unknown",
             "author_role": u.role if u else None, "mine": bool(u and u.id == ctx.user_id)}
            for msg, u in db.execute(q)]


@router.post("/portal/internal-notes")
def post_internal_note(project_id: uuid.UUID, body: MessageIn, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    project_guard(db, ctx, project_id)
    msg = pm.PortalMessage(organisation_id=ctx.org_id, project_id=project_id, author_id=ctx.user_id,
                           body=body.body.strip(), is_internal=True)
    db.add(msg)
    db.flush()
    log(db, ctx, "portal.internal_note", f"project:{project_id}", {"message_id": str(msg.id)})
    return to_dict(msg)


# ------------------------------------------------------------------ share dashboard (log only)
@router.post("/portal/share-dashboard")
def share_dashboard(body: ShareIn, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    project_guard(db, ctx, body.project_id)
    d = pm.DashboardDelivery(organisation_id=ctx.org_id, project_id=body.project_id,
                             sent_to=body.email.strip(), period=body.period, sent_at=now())
    db.add(d)
    db.flush()
    log(db, ctx, "portal.dashboard_shared", f"project:{body.project_id}",
        {"to": body.email, "period": body.period})
    return {"ok": True, "recorded": to_dict(d)}


# ------------------------------------------------------------------ agency: client rights
@router.get("/portal/settings")
def get_settings(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    project_guard(db, ctx, project_id)
    out = []
    for s in db.scalars(select(pm.PortalSettings).where(pm.PortalSettings.project_id == project_id)):
        u = db.get(m.User, s.client_user_id)
        out.append({**to_dict(s), "client_email": u.email if u else None,
                    "client_name": u.name if u else None})
    return out


@router.put("/portal/settings")
def put_settings(project_id: uuid.UUID, body: SettingsIn, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    project_guard(db, ctx, project_id)
    u = db.get(m.User, body.client_user_id)
    if not u or u.organisation_id != ctx.org_id:
        raise HTTPException(404, "User not found")
    if u.role != "client":
        raise HTTPException(400, "Portal rights can only be set for client users")
    s = _settings_row(db, project_id, body.client_user_id)
    if not s:
        s = pm.PortalSettings(organisation_id=ctx.org_id, project_id=project_id, client_user_id=body.client_user_id)
        db.add(s)
    s.rights = body.rights.model_dump()
    db.flush()
    log(db, ctx, "portal.rights_updated", f"project:{project_id}", {"client": u.email, "rights": s.rights})
    return to_dict(s)
