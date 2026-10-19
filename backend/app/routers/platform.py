"""Organisation, team & RBAC, approvals, activity log, notifications, dashboard."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr
from sqlalchemy import and_, desc, func, or_, select

from .. import models as m
from ..deps import Ctx, deny_client, get_ctx, get_db, log, notify, require, visible_project_ids
from ..rbac import RANK, ROLES, can, matrix
from ..security import new_opaque_token, now
from ..services import entitlements as ent
from ..util import rows, to_dict

router = APIRouter(tags=["platform"])


# ------------------------------------------------------------------ organisation
class OrgPatch(BaseModel):
    name: str | None = None
    branding: dict | None = None


@router.get("/org")
def get_org(db=Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    o = db.get(m.Organisation, ctx.org_id)
    d = to_dict(o)
    d["branding"] = {k: v for k, v in (o.branding or {}).items() if not k.startswith("_")}
    return d


@router.patch("/org")
def patch_org(body: OrgPatch, db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    o = db.get(m.Organisation, ctx.org_id)
    if body.name:
        o.name = body.name
    if body.branding is not None:
        allowed = {"company_name", "logo_url", "primary_color", "white_label", "custom_domain", "report_footer"}
        b = {k: v for k, v in body.branding.items() if k in allowed}
        if b.get("white_label") or b.get("custom_domain"):
            ent.require_feature(db, ctx.org_id, "white_label")
        if b.get("logo_url"):
            ent.require_feature(db, ctx.org_id, "report_branding")
        o.branding = {**(o.branding or {}), **b}
    log(db, ctx, "org.updated", f"organisation:{o.id}", body.model_dump(exclude_none=True))
    return get_org(db, ctx)


# ------------------------------------------------------------------ team
class InviteIn(BaseModel):
    email: EmailStr
    role: str
    project_ids: list[uuid.UUID] = []


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    department: str | None = None
    name: str | None = None


@router.get("/rbac/matrix")
def rbac_matrix(ctx: Ctx = Depends(get_ctx)):
    return {"roles": ROLES, "matrix": matrix()}


@router.get("/team")
def team(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    users = db.scalars(select(m.User).order_by(m.User.created_at)).all()
    if ctx.role == "client":  # clients get names only (for assignee display), no emails or invites
        return {"users": [{"id": u.id, "name": u.name or "Team member", "role": u.role, "is_active": u.is_active} for u in users],
                "invites": []}
    invites = db.scalars(select(m.Invite).where(m.Invite.accepted_at.is_(None), m.Invite.expires_at > now())).all()
    return {"users": rows(users, exclude=("password_hash", "mfa_secret_ref")),
            "invites": rows(invites, exclude=("token_hash",))}


@router.post("/team/invites")
def invite(body: InviteIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    if body.role not in ROLES:
        raise HTTPException(400, "Unknown role")
    if body.role == "owner" and ctx.role != "owner":
        raise HTTPException(403, "Only an owner can invite another owner")
    if RANK[body.role] > RANK[ctx.role]:
        raise HTTPException(403, "Cannot grant a role above your own")
    if body.role == "client":
        ent.require_feature(db, ctx.org_id, "client_users")
    else:
        n = db.scalar(select(func.count()).select_from(m.User).where(m.User.role != "client", m.User.is_active))
        ent.check_count_limit(db, ctx.org_id, "staff_users", n)
    if db.scalar(select(m.User.id).where(m.User.email == body.email)):
        raise HTTPException(409, "That email is already a member")
    raw, h = new_opaque_token()
    inv = m.Invite(organisation_id=ctx.org_id, email=body.email, role=body.role, token_hash=h, invited_by=ctx.user_id,
                   expires_at=now() + timedelta(days=7))
    db.add(inv)
    log(db, ctx, "team.invited", f"invite:{body.email}", {"role": body.role})
    # Email delivery is not wired in this release: the accept link is returned to the inviter to share.
    return {"invite_id": str(inv.id), "accept_token": raw, "accept_url": f"/accept-invite?token={raw}",
            "delivery": "manual (email provider NOT CONNECTED)"}


@router.patch("/team/{user_id}")
def patch_user(user_id: uuid.UUID, body: UserPatch, db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    u = db.get(m.User, user_id)
    if not u:
        raise HTTPException(404)
    if RANK[u.role] > RANK[ctx.role]:
        raise HTTPException(403, "Cannot modify a user above your role")
    if body.role:
        if body.role not in ROLES or RANK[body.role] > RANK[ctx.role]:
            raise HTTPException(403, "Cannot grant that role")
        if u.role == "owner" and body.role != "owner":
            owners = db.scalar(select(func.count()).select_from(m.User).where(m.User.role == "owner", m.User.is_active))
            if owners <= 1:
                raise HTTPException(400, "Organisation must keep at least one owner")
        if body.role == "client":
            ent.require_feature(db, ctx.org_id, "client_users")
        u.role = body.role
    if body.is_active is not None:
        if u.id == ctx.user_id and not body.is_active:
            raise HTTPException(400, "You cannot deactivate yourself")
        u.is_active = body.is_active
    if body.department is not None:
        u.department = body.department
    if body.name is not None:
        u.name = body.name
    log(db, ctx, "team.user_updated", f"user:{u.id}", body.model_dump(exclude_none=True))
    return to_dict(u, exclude=("password_hash", "mfa_secret_ref"))


# ------------------------------------------------------------------ approvals (module 45)
class ApprovalIn(BaseModel):
    entity_type: str
    entity_id: uuid.UUID | None = None
    title: str
    chain: list[str] = ["manager"]
    notes: str | None = None


class Decision(BaseModel):
    decision: str  # approve/reject
    notes: str | None = None


@router.get("/approvals")
def list_approvals(status: str | None = None, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    q = select(m.Approval).order_by(desc(m.Approval.created_at))
    if status:
        q = q.where(m.Approval.status == status)
    out = []
    for a in db.scalars(q.limit(200)):
        d = to_dict(a)
        d["awaiting_role"] = a.chain[a.step] if a.status == "pending" and a.step < len(a.chain) else None
        d["can_decide"] = bool(d["awaiting_role"]) and can(ctx.role, "approve") and RANK[ctx.role] >= RANK.get(d["awaiting_role"], 9)
        out.append(d)
    return out


@router.post("/approvals")
def create_approval(body: ApprovalIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    if not body.chain or any(r not in ROLES for r in body.chain):
        raise HTTPException(400, "chain must be a list of roles")
    a = m.Approval(organisation_id=ctx.org_id, entity_type=body.entity_type, entity_id=body.entity_id, title=body.title,
                   chain=body.chain, requested_by=ctx.user_id, notes=body.notes)
    db.add(a)
    db.flush()
    notify(db, ctx.org_id, "approval_requested", f"Approval requested: {body.title}", f"Awaiting {body.chain[0]}", "/approvals")
    log(db, ctx, "approval.requested", f"approval:{a.id}")
    return to_dict(a)


@router.post("/approvals/{aid}/decide")
def decide(aid: uuid.UUID, body: Decision, db=Depends(get_db), ctx: Ctx = Depends(require("approve"))):
    a = db.get(m.Approval, aid)
    if not a or a.status != "pending":
        raise HTTPException(404, "No pending approval")
    need = a.chain[a.step]
    if RANK[ctx.role] < RANK[need]:
        raise HTTPException(403, f"This step needs a {need} or above")
    if a.requested_by == ctx.user_id and ctx.role != "owner":
        raise HTTPException(403, "You cannot approve your own request")
    if body.decision == "reject" and not body.notes:
        raise HTTPException(400, "Rejection requires notes")
    a.decisions = a.decisions + [{"by": str(ctx.user_id), "role": ctx.role, "decision": body.decision, "notes": body.notes,
                                  "at": now().isoformat(), "step": a.step}]
    if body.decision == "reject":
        a.status = "rejected"
    elif body.decision == "approve":
        a.step += 1
        if a.step >= len(a.chain):
            a.status = "approved"
    else:
        raise HTTPException(400, "decision must be approve or reject")
    if a.requested_by:
        notify(db, ctx.org_id, "approval_decided", f"{a.title}: {a.status}", body.notes, "/approvals", user_id=a.requested_by)
    log(db, ctx, f"approval.{body.decision}", f"approval:{a.id}", {"notes": body.notes})
    return to_dict(a)


# ------------------------------------------------------------------ activity (F-005)
@router.get("/activity")
def activity(action: str | None = None, actor_id: uuid.UUID | None = None, q: str | None = None,
             since: datetime | None = None, limit: int = Query(100, le=500), offset: int = 0,
             db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    if ctx.role == "client":
        raise HTTPException(403, "Clients cannot view the activity log")
    ent.require_feature(db, ctx.org_id, "activity_logs")
    qq = select(m.ActivityLog).order_by(desc(m.ActivityLog.created_at))
    if action:
        qq = qq.where(m.ActivityLog.action.like(f"{action}%"))
    if actor_id:
        qq = qq.where(m.ActivityLog.actor_id == actor_id)
    if q:
        qq = qq.where(or_(m.ActivityLog.target.ilike(f"%{q}%"), m.ActivityLog.action.ilike(f"%{q}%")))
    if since:
        qq = qq.where(m.ActivityLog.created_at >= since)
    items = db.scalars(qq.limit(limit).offset(offset)).all()
    names = {u.id: (u.name or u.email) for u in db.scalars(select(m.User))}
    return [{**to_dict(a), "actor": names.get(a.actor_id, "system")} for a in items]


# ------------------------------------------------------------------ notifications
@router.get("/notifications")
def notifications(db=Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    mine = (m.Notification.user_id == ctx.user_id) if ctx.role == "client" else \
        or_(m.Notification.user_id.is_(None), m.Notification.user_id == ctx.user_id)
    q = select(m.Notification).where(mine) \
        .order_by(desc(m.Notification.created_at)).limit(50)
    items = db.scalars(q).all()
    return {"items": rows(items), "unread": sum(1 for n in items if not n.read_at)}


class ReadIn(BaseModel):
    ids: list[uuid.UUID] | None = None


@router.post("/notifications/read")
def mark_read(body: ReadIn, db=Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    ids = body.ids
    q = select(m.Notification).where(m.Notification.read_at.is_(None),
                                     or_(m.Notification.user_id.is_(None), m.Notification.user_id == ctx.user_id))
    if ids:
        q = q.where(m.Notification.id.in_(ids))
    for n in db.scalars(q):
        n.read_at = now()
    return {"ok": True}


# ------------------------------------------------------------------ dashboard (module 1)
@router.get("/dashboard")
def dashboard(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    pids = visible_project_ids(db, ctx)
    week = now() - timedelta(days=7)
    tasks = db.execute(select(m.Task.status, func.count()).where(m.Task.project_id.in_(pids)).group_by(m.Task.status)).all()
    overdue = db.scalar(select(func.count()).select_from(m.Task).where(m.Task.project_id.in_(pids), m.Task.status != "done",
                                                                        m.Task.due_date < now()))
    site_ids = select(m.Site.id).where(m.Site.project_id.in_(pids))
    latest = db.execute(select(m.AuditRun, m.Site.domain).join(m.Site).where(m.AuditRun.site_id.in_(site_ids), m.AuditRun.status == "COMPLETED",
                                                                             m.AuditRun.competitor_id.is_(None))
                        .order_by(desc(m.AuditRun.finished_at)).limit(8)).all()
    hours = db.scalar(select(func.coalesce(func.sum(func.extract("epoch", func.coalesce(m.TimeEntry.ended_at, func.now()) - m.TimeEntry.started_at)), 0))
                      .where(m.TimeEntry.started_at >= week)) or 0
    billable = db.scalar(select(func.coalesce(func.sum(func.extract("epoch", func.coalesce(m.TimeEntry.ended_at, func.now()) - m.TimeEntry.started_at)), 0))
                         .where(m.TimeEntry.started_at >= week, m.TimeEntry.billable)) or 0
    kw = db.scalar(select(func.count()).select_from(m.Keyword).where(m.Keyword.project_id.in_(pids)))
    # average latest position across tracked keywords
    sub = select(m.RankHistory.keyword_id, func.max(m.RankHistory.checked_at).label("t")).group_by(m.RankHistory.keyword_id).subquery()
    avgpos = db.scalar(select(func.avg(m.RankHistory.position)).join(sub, and_(sub.c.keyword_id == m.RankHistory.keyword_id,
                                                                               sub.c.t == m.RankHistory.checked_at))
                      .where(m.RankHistory.keyword_id.in_(select(m.Keyword.id).where(m.Keyword.project_id.in_(pids)))))
    acts = [] if ctx.role == "client" else db.scalars(select(m.ActivityLog).order_by(desc(m.ActivityLog.created_at)).limit(12)).all()
    names = {u.id: (u.name or u.email) for u in db.scalars(select(m.User))}
    jobs = db.execute(select(m.Job.status, func.count()).where(m.Job.created_at >= week).group_by(m.Job.status)).all()
    if ctx.role == "client":
        hours = billable = 0
        jobs = []
    return {
        "projects": db.scalar(select(func.count()).select_from(m.Project).where(m.Project.id.in_(pids))),
        "tasks": dict(tasks), "overdue_tasks": overdue,
        "hours_7d": round(float(hours) / 3600, 1), "billable_hours_7d": round(float(billable) / 3600, 1),
        "keywords": kw, "avg_position": round(float(avgpos), 1) if avgpos else None,
        "audits": [{"id": str(a.id), "domain": d, "score": float(a.score) if a.score is not None else None,
                    "issues": a.summary.get("issues", {}), "finished_at": a.finished_at} for a, d in latest],
        "pending_approvals": 0 if ctx.role == "client" else db.scalar(select(func.count()).select_from(m.Approval).where(m.Approval.status == "pending")),
        "jobs_7d": dict(jobs),
        "activity": [{**to_dict(a), "actor": names.get(a.actor_id, "system")} for a in acts],
    }
