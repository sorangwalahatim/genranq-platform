"""Projects (module 4), Tasks (module 2), Time Tracking (module 3) — F-003."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import any_, desc, func, or_, select

from .. import models as m
from ..deps import Ctx, get_ctx, get_db, log, notify, project_guard, require, visible_project_ids
from ..security import now
from ..services import entitlements as ent
from ..util import rows, to_dict

router = APIRouter(tags=["work"])


# ------------------------------------------------------------------ projects
class ProjectIn(BaseModel):
    name: str
    client_name: str | None = None
    description: str | None = None
    is_template: bool = False
    template_id: uuid.UUID | None = None


class ProjectPatch(BaseModel):
    name: str | None = None
    client_name: str | None = None
    description: str | None = None
    status: str | None = None
    client_user_ids: list[uuid.UUID] | None = None


@router.get("/projects")
def list_projects(include_templates: bool = False, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(m.Project).where(m.Project.id.in_(visible_project_ids(db, ctx))).order_by(m.Project.created_at.desc())
    if not include_templates:
        q = q.where(m.Project.is_template.is_(False))
    out = []
    for p in db.scalars(q):
        d = to_dict(p)
        d["open_tasks"] = db.scalar(select(func.count()).select_from(m.Task).where(m.Task.project_id == p.id, m.Task.status != "done"))
        d["sites"] = rows(db.scalars(select(m.Site).where(m.Site.project_id == p.id)))
        d["keywords"] = db.scalar(select(func.count()).select_from(m.Keyword).where(m.Keyword.project_id == p.id))
        out.append(d)
    return out


@router.post("/projects")
def create_project(body: ProjectIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    n = db.scalar(select(func.count()).select_from(m.Project))
    ent.check_count_limit(db, ctx.org_id, "projects", n)
    p = m.Project(organisation_id=ctx.org_id, name=body.name, client_name=body.client_name, description=body.description,
                  is_template=body.is_template)
    db.add(p)
    db.flush()
    if body.template_id:
        tpl = db.get(m.Project, body.template_id)
        if not tpl or not tpl.is_template:
            raise HTTPException(400, "template_id is not a template project")
        for t in db.scalars(select(m.Task).where(m.Task.project_id == tpl.id)):
            db.add(m.Task(organisation_id=ctx.org_id, project_id=p.id, title=t.title, description=t.description,
                          priority=t.priority, position=t.position, labels=t.labels, created_by=ctx.user_id))
    log(db, ctx, "project.created", f"project:{p.id}", {"name": p.name})
    return to_dict(p)


@router.get("/projects/{pid}")
def get_project(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, pid)
    d = to_dict(p)
    d["sites"] = rows(db.scalars(select(m.Site).where(m.Site.project_id == p.id)))
    d["competitors"] = rows(db.scalars(select(m.Competitor).where(m.Competitor.project_id == p.id)))
    return d


@router.patch("/projects/{pid}")
def patch_project(pid: uuid.UUID, body: ProjectPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    p = project_guard(db, ctx, pid)
    data = body.model_dump(exclude_none=True)
    if "client_user_ids" in data:
        ent.require_feature(db, ctx.org_id, "client_users")
        users = {u.id: u.role for u in db.scalars(select(m.User).where(m.User.id.in_(data["client_user_ids"])))}
        if any(users.get(x) != "client" for x in data["client_user_ids"]):
            raise HTTPException(400, "client_user_ids must reference users with the client role")
    for k, v in data.items():
        setattr(p, k, v)
    log(db, ctx, "project.updated", f"project:{p.id}", {k: str(v) for k, v in data.items()})
    return to_dict(p)


@router.delete("/projects/{pid}")
def delete_project(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    p = project_guard(db, ctx, pid)
    log(db, ctx, "project.deleted", f"project:{p.id}", {"name": p.name})
    db.delete(p)
    return {"ok": True}


# ------------------------------------------------------------------ tasks
class TaskIn(BaseModel):
    project_id: uuid.UUID
    title: str
    description: str | None = None
    status: str = "todo"
    priority: str = "medium"
    assignee_ids: list[uuid.UUID] = []
    due_date: datetime | None = None
    labels: list[str] = []


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assignee_ids: list[uuid.UUID] | None = None
    due_date: datetime | None = None
    position: int | None = None
    labels: list[str] | None = None


class BulkTasks(BaseModel):
    ids: list[uuid.UUID]
    status: str | None = None
    priority: str | None = None
    assignee_ids: list[uuid.UUID] | None = None
    delete: bool = False


STATUSES = ["todo", "in_progress", "review", "done"]
PRIORITIES = ["low", "medium", "high", "critical"]


def _validate(status=None, priority=None):
    if status and status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    if priority and priority not in PRIORITIES:
        raise HTTPException(400, f"priority must be one of {PRIORITIES}")


@router.get("/tasks")
def list_tasks(project_id: uuid.UUID | None = None, status: str | None = None, assignee: uuid.UUID | None = None,
               priority: str | None = None, q: str | None = None, mine: bool = False,
               db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    qq = select(m.Task).where(m.Task.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        qq = qq.where(m.Task.project_id == project_id)
    if status:
        qq = qq.where(m.Task.status == status)
    if priority:
        qq = qq.where(m.Task.priority == priority)
    if assignee or mine:
        qq = qq.where(any_(m.Task.assignee_ids) == (assignee or ctx.user_id))
    if q:
        qq = qq.where(or_(m.Task.title.ilike(f"%{q}%"), m.Task.description.ilike(f"%{q}%")))
    items = db.scalars(qq.order_by(m.Task.status, m.Task.position, m.Task.created_at).limit(1000)).all()
    projects = {p.id: p.name for p in db.scalars(select(m.Project))}
    return [{**to_dict(t), "project_name": projects.get(t.project_id)} for t in items]


def _assigned(db, ctx, t: m.Task, new_ids):
    """Newly assigned people get an in-app notification and an email (email shows NOT CONNECTED without SMTP)."""
    from ..services import mailer
    for uid in new_ids or []:
        if uid != ctx.user_id:
            notify(db, ctx.org_id, "task_assigned", f"New task: {t.title}", (t.description or "")[:300] or None, f"/tasks?task={t.id}", user_id=uid)
            mailer.task_email(db, t, uid)


@router.post("/tasks")
def create_task(body: TaskIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    _validate(body.status, body.priority)
    if len(body.assignee_ids) > 1:
        ent.require_feature(db, ctx.org_id, "multi_assignee")
    pos = db.scalar(select(func.coalesce(func.max(m.Task.position), 0)).where(m.Task.project_id == body.project_id, m.Task.status == body.status))
    t = m.Task(organisation_id=ctx.org_id, created_by=ctx.user_id, position=pos + 1, **body.model_dump())
    db.add(t)
    db.flush()
    _assigned(db, ctx, t, body.assignee_ids)
    log(db, ctx, "task.created", f"task:{t.id}", {"title": t.title})
    return to_dict(t)


@router.patch("/tasks/{tid}")
def patch_task(tid: uuid.UUID, body: TaskPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    t = db.get(m.Task, tid)
    if not t:
        raise HTTPException(404)
    project_guard(db, ctx, t.project_id)
    data = body.model_dump(exclude_unset=True)
    _validate(data.get("status"), data.get("priority"))
    if data.get("assignee_ids") and len(data["assignee_ids"]) > 1:
        ent.require_feature(db, ctx.org_id, "multi_assignee")
    before = {k: getattr(t, k) for k in data}
    for k, v in data.items():
        setattr(t, k, v)
    if "status" in data:
        from ..services.monitor import on_task_status
        on_task_status(db, t, before["status"])
    if "assignee_ids" in data:
        _assigned(db, ctx, t, [u for u in data["assignee_ids"] or [] if u not in (before["assignee_ids"] or [])])
    log(db, ctx, "task.updated", f"task:{t.id}", {k: {"from": str(before[k]), "to": str(v)} for k, v in data.items() if k != "position"})
    return to_dict(t)


@router.post("/tasks/bulk")
def bulk_tasks(body: BulkTasks, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    ent.require_feature(db, ctx.org_id, "multi_assignee")  # "Multiple assignee / filters / bulk"
    _validate(body.status, body.priority)
    if body.delete and not __import__("app.rbac", fromlist=["can"]).can(ctx.role, "delete"):
        raise HTTPException(403, "Bulk delete needs the delete permission")
    vis = visible_project_ids(db, ctx)
    ts = db.scalars(select(m.Task).where(m.Task.id.in_(body.ids), m.Task.project_id.in_(vis))).all()
    for t in ts:
        if body.delete:
            db.delete(t)
            continue
        if body.status:
            old, t.status = t.status, body.status
            from ..services.monitor import on_task_status
            on_task_status(db, t, old)
        if body.priority:
            t.priority = body.priority
        if body.assignee_ids is not None:
            new = [u for u in body.assignee_ids if u not in (t.assignee_ids or [])]
            t.assignee_ids = body.assignee_ids
            _assigned(db, ctx, t, new)
    log(db, ctx, "task.bulk", None, {"count": len(ts), "delete": body.delete, "status": body.status})
    return {"updated": len(ts)}


@router.delete("/tasks/{tid}")
def delete_task(tid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    t = db.get(m.Task, tid)
    if not t:
        raise HTTPException(404)
    project_guard(db, ctx, t.project_id)
    log(db, ctx, "task.deleted", f"task:{t.id}", {"title": t.title})
    db.delete(t)
    return {"ok": True}


# ------------------------------------------------------------------ time tracking
class TimerStart(BaseModel):
    project_id: uuid.UUID
    task_id: uuid.UUID | None = None
    description: str | None = None
    billable: bool = True


class TimeIn(TimerStart):
    started_at: datetime
    ended_at: datetime
    hourly_rate: float | None = Field(None, ge=0, le=100000)


def _secs(e: m.TimeEntry) -> float:
    return ((e.ended_at or now()) - e.started_at).total_seconds()


@router.get("/time/running")
def running(db=Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    e = db.scalar(select(m.TimeEntry).where(m.TimeEntry.user_id == ctx.user_id, m.TimeEntry.ended_at.is_(None)))
    return to_dict(e) if e else None


def _task_in_project(db, task_id, project_id):
    if task_id:
        t = db.get(m.Task, task_id)
        if not t or t.project_id != project_id:
            raise HTTPException(400, "task_id is not in this project")


@router.post("/time/start")
def start_timer(body: TimerStart, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    _task_in_project(db, body.task_id, body.project_id)
    for e in db.scalars(select(m.TimeEntry).where(m.TimeEntry.user_id == ctx.user_id, m.TimeEntry.ended_at.is_(None))):
        e.ended_at = now()  # only one running timer per user
    e = m.TimeEntry(organisation_id=ctx.org_id, user_id=ctx.user_id, started_at=now(), **body.model_dump())
    db.add(e)
    db.flush()
    return to_dict(e)


@router.post("/time/stop")
def stop_timer(db=Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    e = db.scalar(select(m.TimeEntry).where(m.TimeEntry.user_id == ctx.user_id, m.TimeEntry.ended_at.is_(None)))
    if not e:
        raise HTTPException(400, "No running timer")
    e.ended_at = now()
    return to_dict(e)


@router.post("/time")
def add_time(body: TimeIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    _task_in_project(db, body.task_id, body.project_id)
    if body.ended_at <= body.started_at:
        raise HTTPException(400, "ended_at must be after started_at")
    e = m.TimeEntry(organisation_id=ctx.org_id, user_id=ctx.user_id, **body.model_dump())
    db.add(e)
    db.flush()
    return to_dict(e)


@router.delete("/time/{eid}")
def delete_time(eid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    e = db.get(m.TimeEntry, eid)
    if not e or (e.user_id != ctx.user_id and ctx.role not in ("owner", "admin", "manager")):
        raise HTTPException(404)
    db.delete(e)
    return {"ok": True}


@router.get("/time")
def list_time(start: date | None = None, end: date | None = None, user_id: uuid.UUID | None = None,
              project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    start = start or (date.today() - timedelta(days=date.today().weekday()))
    end = end or (start + timedelta(days=7))
    q = select(m.TimeEntry).where(m.TimeEntry.started_at >= datetime.combine(start, datetime.min.time(), timezone.utc),
                                  m.TimeEntry.started_at < datetime.combine(end, datetime.min.time(), timezone.utc))
    if ctx.role in ("staff", "client"):
        q = q.where(m.TimeEntry.user_id == ctx.user_id)
    elif user_id:
        q = q.where(m.TimeEntry.user_id == user_id)
    if project_id:
        q = q.where(m.TimeEntry.project_id == project_id)
    entries = db.scalars(q.order_by(desc(m.TimeEntry.started_at))).all()
    users = {u.id: (u.name or u.email) for u in db.scalars(select(m.User))}
    projects = {p.id: p.name for p in db.scalars(select(m.Project))}
    by_user, by_project, by_day = {}, {}, {}
    total = billable = amount = 0.0
    for e in entries:
        s = _secs(e) / 3600
        total += s
        if e.billable:
            billable += s
            amount += s * float(e.hourly_rate or 0)
        by_user[users.get(e.user_id, "?")] = by_user.get(users.get(e.user_id, "?"), 0) + s
        by_project[projects.get(e.project_id, "?")] = by_project.get(projects.get(e.project_id, "?"), 0) + s
        d = e.started_at.date().isoformat()
        by_day[d] = by_day.get(d, 0) + s
    return {"start": start, "end": end,
            "entries": [{**to_dict(e), "hours": round(_secs(e) / 3600, 2), "user": users.get(e.user_id), "project": projects.get(e.project_id)} for e in entries],
            "totals": {"hours": round(total, 2), "billable_hours": round(billable, 2), "billable_amount": round(amount, 2),
                       "utilisation_pct": round(100 * billable / total, 1) if total else None},
            "by_user": {k: round(v, 2) for k, v in by_user.items()}, "by_project": {k: round(v, 2) for k, v in by_project.items()},
            "by_day": {k: round(v, 2) for k, v in sorted(by_day.items())}}


@router.get("/time/export.csv")
def export_time(start: date | None = None, end: date | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("export"))):
    data = list_time(start, end, None, None, db, ctx)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "user", "project", "description", "hours", "billable", "rate"])
    for e in data["entries"]:
        w.writerow([e["started_at"].date(), e["user"], e["project"], e["description"] or "", e["hours"], e["billable"], e["hourly_rate"] or ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=timesheet-{data['start']}.csv"})
