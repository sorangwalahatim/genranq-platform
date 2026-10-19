"""Unified Error Center API: one deduplicated inbox for errors from every audit
source (GSC, PageSpeed, Screaming Frog, Ahrefs, Semrush, site audit).

NOTE (wiring): register this router in backend/app/main.py:
    from .routers import ..., errorcenter
    ... errorcenter.router ...
and make sure backend/app/models_errorcenter.py is imported before migrations run
(e.g. add it to the models_* import line at the end of backend/app/models.py).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from .. import models as m
from ..deps import Ctx, get_db, log, project_guard, require
from ..security import now
from ..services import errorcenter as ec
from .. import models_errorcenter as me
from ..util import to_dict

router = APIRouter(tags=["errorcenter"])

SOURCES = me.SOURCES
PRIORITY = {"critical": "critical", "warning": "high", "info": "medium"}


def _item(db, ctx, iid: uuid.UUID) -> me.ErrorItem:
    it = db.get(me.ErrorItem, iid)
    if not it:
        raise HTTPException(404, "Error not found")
    project_guard(db, ctx, it.project_id)
    return it


def _with_names(db, items):
    ids = {i.assignee for i in items if i.assignee}
    names = {u.id: (u.name or u.email) for u in db.scalars(select(m.User).where(m.User.id.in_(ids)))} if ids else {}
    out = []
    for i in items:
        d = to_dict(i)
        d["assignee_name"] = names.get(i.assignee)
        d["url_count"] = len(i.urls or [])
        out.append(d)
    return out


class IngestError(BaseModel):
    category: str = "general"
    severity: str = "warning"
    title: str
    detail: str | None = None
    urls: list[str] = Field(default_factory=list, max_length=2000)
    solution: str | None = None
    skill_key: str | None = None


class IngestIn(BaseModel):
    project_id: uuid.UUID
    source: str
    errors: list[IngestError] = Field(min_length=1, max_length=2000)


@router.post("/errorcenter/ingest")
def ingest_errors(body: IngestIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    """Bulk-import errors from an audit source. Dedupe is automatic (fingerprint)."""
    project_guard(db, ctx, body.project_id)
    try:
        res = ec.ingest(db, ctx, body.project_id, body.source, [e.model_dump() for e in body.errors])
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "errorcenter.ingested", f"project:{body.project_id}",
        {"source": body.source, **res})
    return res


@router.get("/errorcenter")
def list_errors(project_id: uuid.UUID, status: str | None = None, bucket: str | None = None,
                source: str | None = None, q: str | None = None,
                limit: int = Query(200, le=1000), offset: int = 0,
                db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    """Filterable error inbox. Ignored items are hidden unless status=ignored."""
    project_guard(db, ctx, project_id)
    qq = select(me.ErrorItem).where(me.ErrorItem.project_id == project_id)
    if status:
        if status not in me.STATUSES:
            raise HTTPException(400, f"status must be one of {me.STATUSES}")
        qq = qq.where(me.ErrorItem.status == status)
    else:
        qq = qq.where(me.ErrorItem.status != "ignored")
    if bucket:
        qq = qq.where(me.ErrorItem.bucket == bucket)
    if source:
        qq = qq.where(me.ErrorItem.source == source)
    if q:
        qq = qq.where(me.ErrorItem.title.ilike(f"%{q}%"))
    sev_rank = func.array_position(["critical", "warning", "info"], me.ErrorItem.severity)
    total = db.scalar(select(func.count()).select_from(qq.subquery()))
    items = db.scalars(qq.order_by(sev_rank, desc(me.ErrorItem.last_seen)).limit(limit).offset(offset)).all()
    return {"total": total, "items": _with_names(db, items)}


class PatchIn(BaseModel):
    status: str | None = None
    assignee: uuid.UUID | None = None
    ignore_reason: str | None = None


@router.patch("/errorcenter/{iid}")
def patch_error(iid: uuid.UUID, body: PatchIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    if body.status:
        if body.status not in me.STATUSES:
            raise HTTPException(400, f"status must be one of {me.STATUSES}")
        if body.status == "ignored":
            reason = body.ignore_reason or it.ignore_reason
            try:
                ec.ignore(db, it, reason or "", ctx.user_id)
            except ValueError as e:
                raise HTTPException(400, str(e))
        else:
            if it.status == "ignored":
                ec.unignore(db, it)
            it.status = body.status
    if body.assignee is not None:
        if body.assignee:
            u = db.get(m.User, body.assignee)
            if not u or u.role == "client":
                raise HTTPException(400, "Assignee must be a team member")
        it.assignee = body.assignee
    if body.ignore_reason and it.status != "ignored":
        raise HTTPException(400, "ignore_reason needs status=ignored")
    log(db, ctx, "errorcenter.updated", f"error_item:{iid}", {"status": it.status})
    return _with_names(db, [it])[0]


@router.post("/errorcenter/{iid}/task")
def error_to_task(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    """Create a Task from the error (solution included), move error to in_progress."""
    it = _item(db, ctx, iid)
    desc_text = (it.detail or "") + "\n\n--- How to fix (step by step) ---\n" + (it.solution or "")
    t = m.Task(
        organisation_id=ctx.org_id, project_id=it.project_id,
        title=f"Fix: {it.title}",
        description=desc_text.strip(),
        priority=PRIORITY.get(it.severity, "medium"),
        workstream="technical" if it.bucket == "developer" else "on_page",
        source=f"errorcenter:{it.id}",
        labels=[it.source, it.category, it.bucket],
        assignee_ids=[it.assignee] if it.assignee else [],
        assignee_type="user" if it.assignee else "team",
        created_by=ctx.user_id,
        due_date=now() + timedelta(days=2 if it.severity == "critical" else 7),
    )
    db.add(t)
    db.flush()
    if it.status == "open":
        it.status = "in_progress"
    log(db, ctx, "errorcenter.task_created", f"error_item:{iid}", {"task_id": str(t.id)})
    return {"task_id": str(t.id), **to_dict(t)}


@router.get("/errorcenter/summary")
def summary(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    """Counts by source / bucket / status for the project dashboard."""
    project_guard(db, ctx, project_id)
    base = [me.ErrorItem.project_id == project_id]

    def counts(col):
        return {k: n for k, n in db.execute(
            select(col, func.count()).where(*base).group_by(col)).all()}

    by_source = counts(me.ErrorItem.source)
    by_bucket = counts(me.ErrorItem.bucket)
    by_status = counts(me.ErrorItem.status)
    open_total = sum(n for s, n in by_status.items() if s not in ("ignored", "verified"))
    return {
        "open_total": open_total,
        "by_source": by_source,
        "by_bucket": by_bucket,
        "by_status": by_status,
        "ignored": by_status.get("ignored", 0),
    }


@router.get("/errorcenter/ignored")
def ignored_list(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    """Who ignored what and when, with restore."""
    project_guard(db, ctx, project_id)
    items = db.scalars(select(me.ErrorItem).where(me.ErrorItem.project_id == project_id,
                                                 me.ErrorItem.status == "ignored")
                       .order_by(desc(me.ErrorItem.ignored_at)).limit(200)).all()
    ids = {i.ignored_by for i in items if i.ignored_by}
    names = {u.id: (u.name or u.email) for u in db.scalars(select(m.User).where(m.User.id.in_(ids)))} if ids else {}
    out = []
    for i in items:
        d = to_dict(i)
        d["ignored_by_name"] = names.get(i.ignored_by)
        d["url_count"] = len(i.urls or [])
        out.append(d)
    return {"total": len(out), "items": out}


@router.post("/errorcenter/{iid}/restore")
def restore_error(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    if it.status != "ignored":
        raise HTTPException(400, "Only ignored errors can be restored")
    ec.unignore(db, it)
    log(db, ctx, "errorcenter.restored", f"error_item:{iid}")
    return _with_names(db, [it])[0]


@router.post("/errorcenter/{iid}/smart-assign")
def smart_assign_error(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    """Re-run smart assignment for one error."""
    it = _item(db, ctx, iid)
    uid, reason = ec.smart_assign(db, it.project_id, it.bucket)
    it.assignee = uid
    return {"assignee": str(uid) if uid else None, "reason": reason, **{k: v for k, v in to_dict(it).items() if k != "assignee"}}
