"""Project checklists (pre-launch / post-launch / tech audit) + 301 redirect plans."""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import models as m
from .. import models_checklists as mc
from ..deps import Ctx, get_db, log, project_guard, require
from ..services import checklist_seed
from ..services.redirects import find_chains
from ..util import rows, to_dict

router = APIRouter(tags=["checklists"])


def _checklist(db, ctx, cid) -> mc.Checklist:
    c = db.get(mc.Checklist, cid)
    if not c:
        raise HTTPException(404, "Checklist not found")
    project_guard(db, ctx, c.project_id)
    return c


def _progress(db, c: mc.Checklist) -> dict:
    counts = dict(db.execute(
        select(mc.ChecklistItem.status, func.count()).where(mc.ChecklistItem.checklist_id == c.id).group_by(mc.ChecklistItem.status)).all())
    total = sum(counts.values())
    return {"total": total, "pending": counts.get("pending", 0), "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0), "partial": counts.get("partial", 0),
            "done": counts.get("pass", 0) + counts.get("partial", 0)}


# ------------------------------------------------------------------ seed & list
class SeedIn(BaseModel):
    project_id: uuid.UUID


@router.post("/checklists/seed")
def seed_checklists(body: SeedIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    """Idempotent: creates the 3 checklists (pre/post launch, tech audit) for a project."""
    project_guard(db, ctx, body.project_id)
    try:
        data = checklist_seed.load_seed_data()
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    created = 0
    for kind, items in data.items():
        exists = db.scalar(select(mc.Checklist.id).where(mc.Checklist.project_id == body.project_id, mc.Checklist.kind == kind))
        if exists:
            continue
        c = mc.Checklist(organisation_id=ctx.org_id, project_id=body.project_id, kind=kind,
                         name=mc.KIND_LABELS.get(kind, kind))
        db.add(c)
        db.flush()
        for it in items:
            db.add(mc.ChecklistItem(organisation_id=ctx.org_id, checklist_id=c.id, project_id=body.project_id,
                                    title=it["title"], detail=it.get("detail"), owner_role=it.get("owner_role"),
                                    priority=it.get("priority"), status=it.get("status") or "pending",
                                    sort_order=it.get("sort_order") or 0))
        created += 1
    log(db, ctx, "checklist.seeded", f"project:{body.project_id}", {"created": created})
    return {"created": created, "skipped": len(data) - created}


@router.get("/checklists")
def list_checklists(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    out = []
    for c in db.scalars(select(mc.Checklist).where(mc.Checklist.project_id == project_id).order_by(mc.Checklist.kind)):
        out.append({**to_dict(c), "label": mc.KIND_LABELS.get(c.kind, c.kind), "progress": _progress(db, c)})
    return out


@router.get("/checklists/{cid}")
def get_checklist(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    c = _checklist(db, ctx, cid)
    items = db.scalars(select(mc.ChecklistItem).where(mc.ChecklistItem.checklist_id == cid)
                       .order_by(mc.ChecklistItem.sort_order, mc.ChecklistItem.title)).all()
    return {**to_dict(c), "label": mc.KIND_LABELS.get(c.kind, c.kind), "progress": _progress(db, c), "items": rows(items)}


# ------------------------------------------------------------------ items
@router.patch("/checklist-items/{iid}")
def patch_item(iid: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = db.get(mc.ChecklistItem, iid)
    if not it:
        raise HTTPException(404, "Item not found")
    project_guard(db, ctx, it.project_id)
    if "status" in body:
        if body["status"] not in mc.ITEM_STATUSES:
            raise HTTPException(400, f"status must be one of {mc.ITEM_STATUSES}")
        it.status = body["status"]
    if "evidence" in body:
        it.evidence = (body["evidence"] or None)
    log(db, ctx, "checklist.item_updated", f"checklist_item:{iid}", {"status": it.status})
    return to_dict(it)


@router.post("/checklist-items/{iid}/task")
def item_to_task(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    """Turn a failed checklist item into a task for the team."""
    it = db.get(mc.ChecklistItem, iid)
    if not it:
        raise HTTPException(404, "Item not found")
    project_guard(db, ctx, it.project_id)
    if it.task_id:
        return {"task_id": str(it.task_id), "already": True}
    t = m.Task(organisation_id=ctx.org_id, project_id=it.project_id,
               title=f"[Checklist] {it.title}",
               description=(it.detail or "") + (f"\n\nEvidence: {it.evidence}" if it.evidence else ""),
               priority=it.priority if it.priority in ("low", "medium", "high", "critical") else "medium",
               workstream="technical", source=f"checklist_item:{iid}", created_by=ctx.user_id)
    db.add(t)
    db.flush()
    it.task_id = t.id
    log(db, ctx, "checklist.task_created", f"checklist_item:{iid}", {"task_id": str(t.id)})
    return {"task_id": str(t.id), "already": False}


# ------------------------------------------------------------------ redirects
@router.get("/redirects")
def list_redirects(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    rules = db.scalars(select(mc.RedirectRule).where(mc.RedirectRule.project_id == project_id).order_by(mc.RedirectRule.old_url)).all()
    return {"rules": rows(rules), "chains": find_chains([to_dict(r) for r in rules])}


class RedirectIn(BaseModel):
    project_id: uuid.UUID
    old_url: str
    new_url: str
    status: str = "pending"
    note: str | None = None


@router.post("/redirects")
def upsert_redirect(body: RedirectIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    old, new = body.old_url.strip(), body.new_url.strip()
    if not old or not new:
        raise HTTPException(400, "old_url and new_url are required")
    if body.status not in mc.REDIRECT_STATUSES:
        raise HTTPException(400, f"status must be one of {mc.REDIRECT_STATUSES}")
    r = db.scalar(select(mc.RedirectRule).where(mc.RedirectRule.project_id == body.project_id, mc.RedirectRule.old_url == old))
    if r:
        r.new_url, r.status, r.note = new, body.status, body.note
    else:
        r = mc.RedirectRule(organisation_id=ctx.org_id, project_id=body.project_id, old_url=old, new_url=new,
                            status=body.status, note=body.note)
        db.add(r)
    db.flush()
    log(db, ctx, "redirect.saved", f"project:{body.project_id}", {"old_url": old})
    return to_dict(r)


@router.post("/redirects/import")
async def import_redirects(project_id: uuid.UUID, file: UploadFile = File(...),
                           db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    """CSV columns: old_url, new_url (optional: status, note). Existing old_urls are updated."""
    project_guard(db, ctx, project_id)
    text = (await file.read()).decode("utf-8-sig")
    rdr = csv.DictReader(io.StringIO(text))
    cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
    if "old_url" not in cols or "new_url" not in cols:
        raise HTTPException(400, "CSV needs 'old_url' and 'new_url' columns")
    existing = {r.old_url: r for r in db.scalars(select(mc.RedirectRule).where(mc.RedirectRule.project_id == project_id))}
    n = 0
    for i, row in enumerate(rdr, 2):
        old, new = (row[cols["old_url"]] or "").strip(), (row[cols["new_url"]] or "").strip()
        if not old or not new:
            continue
        status = (row.get(cols.get("status", "")) or "pending").strip().lower() if cols.get("status") else "pending"
        if status not in mc.REDIRECT_STATUSES:
            raise HTTPException(400, f"Row {i}: status must be one of {mc.REDIRECT_STATUSES}")
        note = (row.get(cols["note"]) or "").strip() or None if "note" in cols else None
        r = existing.get(old)
        if r:
            r.new_url, r.status, r.note = new, status, note
        else:
            r = mc.RedirectRule(organisation_id=ctx.org_id, project_id=project_id, old_url=old, new_url=new, status=status, note=note)
            db.add(r)
            existing[old] = r
        n += 1
    log(db, ctx, "redirect.imported", f"project:{project_id}", {"rows": n})
    return {"rows": n}


@router.patch("/redirects/{rid}")
def patch_redirect(rid: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    r = db.get(mc.RedirectRule, rid)
    if not r:
        raise HTTPException(404, "Redirect not found")
    project_guard(db, ctx, r.project_id)
    if "new_url" in body and body["new_url"] and body["new_url"].strip():
        r.new_url = body["new_url"].strip()
    if "status" in body:
        if body["status"] not in mc.REDIRECT_STATUSES:
            raise HTTPException(400, f"status must be one of {mc.REDIRECT_STATUSES}")
        r.status = body["status"]
    if "note" in body:
        r.note = body["note"] or None
    return to_dict(r)


@router.delete("/redirects/{rid}")
def delete_redirect(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    r = db.get(mc.RedirectRule, rid)
    if r:
        project_guard(db, ctx, r.project_id)
        db.delete(r)
    return {"ok": True}


@router.get("/redirects/chains")
def redirect_chains(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    """Flag redirect chains (A->B->C) and loops so they can be flattened."""
    project_guard(db, ctx, project_id)
    rules = db.scalars(select(mc.RedirectRule).where(mc.RedirectRule.project_id == project_id)).all()
    return {"problems": find_chains([to_dict(r) for r in rules])}
