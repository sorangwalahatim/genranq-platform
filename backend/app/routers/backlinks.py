"""Backlinks group API: Backlink CRM (13) + supplier CRM, verification, toxic/disavow, files and KPIs;
Ahrefs (9) and Semrush (10) project ingest; ReleVink placement finder (63); keyword research (11);
page-to-page competitor audit (58)."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select

from .. import models as m
from .. import models_backlinks as mb
from ..deps import Ctx, deny_client, get_db, log, project_guard, require, visible_project_ids
from ..rbac import can
from ..services import backlinks as bs
from ..services import backlinks_keywords as bk
from ..services import backlinks_providers as bp
from ..services import entitlements as ent
from ..services import integrations as ih
from ..services import reports as rep
from ..services.handlers_backlinks import ensure_verify_schedule
from ..services.jobs import enqueue
from ..util import to_dict

router = APIRouter(tags=["backlinks"])
MAX_UPLOAD = 25 * 1024 * 1024
MAX_CSV = 50 * 1024 * 1024
ALLOWED_FILE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif", "text/csv", "text/plain",
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel",
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword",
                      "application/zip", "application/octet-stream"}


def _now():
    return datetime.now(timezone.utc)


def _bl(db, ctx: Ctx, bid) -> m.Backlink:
    b = db.get(m.Backlink, bid)
    if not b:
        raise HTTPException(404, "Backlink not found")
    project_guard(db, ctx, b.project_id)
    return b


def _sup(db, sid) -> m.Supplier:
    s = db.get(m.Supplier, sid)
    if not s:
        raise HTTPException(404, "Supplier not found")
    return s


async def _read(file: UploadFile, limit: int) -> bytes:
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(413, f"File too large ({limit // (1024 * 1024)} MB max)")
    if not content:
        raise HTTPException(400, "The file is empty")
    return content


def _strip_for_client(ctx: Ctx, d: dict) -> dict:
    if ctx.role == "client":  # agency costs and suppliers are internal
        for k in ("cost", "currency", "supplier", "supplier_id", "notes"):
            d[k] = None
    return d


# ================================================================== enums
@router.get("/backlinks/options")
def options(ctx: Ctx = Depends(require("view"))):
    return {"link_types": mb.LINK_TYPES, "paid": mb.PAID, "scopes": mb.SCOPES, "statuses": mb.STATUSES,
            "frequencies": mb.FREQUENCIES, "file_kinds": mb.FILE_KINDS, "toxic_threshold": bs.TOXIC_THRESHOLD}


# ================================================================== backlinks CRUD
class BacklinkIn(BaseModel):
    project_id: uuid.UUID
    source_url: str = Field(..., max_length=2000)
    target_url: str = Field(..., max_length=2000)
    anchor_text: str | None = Field(None, max_length=500)
    link_type: str = "dofollow"
    paid_or_free: str | None = None
    link_scope: str = "project"
    cost: float | None = Field(None, ge=0, le=10_000_000)
    currency: str | None = Field(None, max_length=3)
    placement_date: date | None = None
    supplier_id: uuid.UUID | None = None
    target_site_id: uuid.UUID | None = None
    dr: float | None = Field(None, ge=0, le=100)
    da: float | None = Field(None, ge=0, le=100)
    notes: str | None = Field(None, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    verify_frequency: str = "weekly"
    status: str = "pending"
    verify_now: bool = False


class BacklinkPatch(BaseModel):
    source_url: str | None = Field(None, max_length=2000)
    target_url: str | None = Field(None, max_length=2000)
    anchor_text: str | None = Field(None, max_length=500)
    link_type: str | None = None
    paid_or_free: str | None = None
    link_scope: str | None = None
    cost: float | None = Field(None, ge=0, le=10_000_000)
    currency: str | None = Field(None, max_length=3)
    placement_date: date | None = None
    supplier_id: uuid.UUID | None = None
    dr: float | None = Field(None, ge=0, le=100)
    da: float | None = Field(None, ge=0, le=100)
    notes: str | None = Field(None, max_length=5000)
    tags: list[str] | None = Field(None, max_length=30)
    verify_frequency: str | None = None
    status: str | None = None


def _rows(db, ctx, bls: list[m.Backlink]) -> list[dict]:
    if not bls:
        return []
    ids = [b.id for b in bls]
    metas = {x.backlink_id: x for x in db.scalars(select(mb.BacklinkMeta).where(mb.BacklinkMeta.backlink_id.in_(ids)))}
    sups = {s.id: s.name for s in db.scalars(select(m.Supplier))} if ctx.role != "client" else {}
    projects = {p.id: p.name for p in db.scalars(select(m.Project).where(m.Project.id.in_({b.project_id for b in bls})))}
    last = {}
    for e in db.scalars(select(mb.BacklinkCheckEvidence).where(mb.BacklinkCheckEvidence.backlink_id.in_(ids))
                        .order_by(mb.BacklinkCheckEvidence.checked_at.desc())):
        last.setdefault(e.backlink_id, {"status": e.status, "checked_at": e.checked_at, "reason": e.reason, "rel": e.rel_found})
    return [_strip_for_client(ctx, bs.serialize(b, metas.get(b.id), sups, last.get(b.id), projects)) for b in bls]


@router.get("/backlinks")
def list_backlinks(project_id: uuid.UUID | None = None, status: str | None = None, supplier_id: uuid.UUID | None = None,
                   link_type: str | None = None, paid_or_free: str | None = None, q: str | None = Query(None, max_length=200),
                   min_toxicity: float | None = None, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
                   db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    if project_id:
        project_guard(db, ctx, project_id)
        qq = select(m.Backlink).where(m.Backlink.project_id == project_id)
    else:
        qq = select(m.Backlink).where(m.Backlink.project_id.in_(visible_project_ids(db, ctx)))
    if status:
        qq = qq.where(m.Backlink.status == status)
    if supplier_id and ctx.role != "client":
        qq = qq.where(m.Backlink.supplier_id == supplier_id)
    if link_type:
        qq = qq.where(m.Backlink.link_type == link_type)
    if paid_or_free:
        qq = qq.where(m.Backlink.paid_or_free == paid_or_free)
    if min_toxicity is not None:
        qq = qq.where(m.Backlink.toxicity_score >= min_toxicity)
    if q:
        like = f"%{q}%"
        qq = qq.where(or_(m.Backlink.source_url.ilike(like), m.Backlink.target_url.ilike(like), m.Backlink.anchor_text.ilike(like)))
    total = db.scalar(select(func.count()).select_from(qq.subquery()))
    bls = db.scalars(qq.order_by(desc(m.Backlink.last_verified).nullslast(), m.Backlink.source_url).limit(limit).offset(offset)).all()
    return {"total": total, "items": _rows(db, ctx, bls)}


@router.post("/backlinks")
def create_backlink(body: BacklinkIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    d = body.model_dump(exclude={"project_id", "verify_now"})
    if d.get("currency"):
        d["currency"] = d["currency"].upper()
    bl = bs.create_backlink(db, ctx.org_id, ctx.user_id, body.project_id, d)
    ensure_verify_schedule(db, ctx.org_id)
    log(db, ctx, "backlink.created", f"backlink:{bl.id}", {"source": bl.source_url, "target": bl.target_url})
    job = enqueue(db, ctx.org_id, "backlinks_verify", {"backlink_ids": [str(bl.id)]}, created_by=ctx.user_id) if body.verify_now else None
    return {**_rows(db, ctx, [bl])[0], "job_id": str(job.id) if job else None}


@router.get("/backlinks/dashboard")
def dashboard(project_id: uuid.UUID | None = None, toxic_threshold: float = Query(bs.TOXIC_THRESHOLD, ge=0, le=100),
              months: int = Query(12, ge=1, le=36), db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    if project_id:
        project_guard(db, ctx, project_id)
        pids = [project_id]
    else:
        pids = list(db.scalars(visible_project_ids(db, ctx)))
    k = bs.kpis(db, pids, toxic_threshold, months)
    if ctx.role == "client":
        k["spend_by_supplier"], k["cost_per_live_link"] = [], []
    return k


@router.post("/backlinks/verify")
def verify(body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    ids = body.get("backlink_ids") or []
    if body.get("project_id"):
        pid = bs.uuid_or_none(body["project_id"])
        project_guard(db, ctx, pid)
        ids = [str(x) for x in db.scalars(select(m.Backlink.id).where(m.Backlink.project_id == pid))]
    if not ids or len(ids) > 2000:
        raise HTTPException(400, "Give backlink_ids (1–2000) or a project_id")
    ok = []
    for i in ids:
        ok.append(str(_bl(db, ctx, bs.uuid_or_none(i)).id))
    jobs = [str(enqueue(db, ctx.org_id, "backlinks_verify", {"backlink_ids": ok[i:i + 25]}, created_by=ctx.user_id).id)
            for i in range(0, len(ok), 25)]
    log(db, ctx, "backlink.verify_requested", None, {"count": len(ok)})
    return {"queued": len(ok), "job_ids": jobs}


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=2000)
    action: str
    value: str | None = Field(None, max_length=200)
    confirm: bool = False


BULK_FIELDS = {"set_status": "status", "set_link_type": "link_type", "set_scope": "link_scope", "set_frequency": "verify_frequency",
               "set_paid": "paid_or_free"}


@router.post("/backlinks/bulk")
def bulk(body: BulkIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    bls = [_bl(db, ctx, i) for i in body.ids]
    if body.action == "delete":
        if not can(ctx.role, "delete"):
            raise HTTPException(403, f"Your role ({ctx.role}) lacks the 'delete' permission")
        if not body.confirm:
            raise HTTPException(400, detail={"code": "confirm_required", "message": f"Deleting {len(bls)} backlinks can't be undone — confirm to proceed."})
        for b in bls:
            db.delete(b)
        log(db, ctx, "backlink.bulk_deleted", None, {"count": len(bls)})
        return {"deleted": len(bls)}
    if body.action == "verify":
        ids = [str(b.id) for b in bls]
        jobs = [str(enqueue(db, ctx.org_id, "backlinks_verify", {"backlink_ids": ids[i:i + 25]}, created_by=ctx.user_id).id)
                for i in range(0, len(ids), 25)]
        return {"queued": len(ids), "job_ids": jobs}
    if body.action == "set_supplier":
        sid = bs.uuid_or_none(body.value)
        if sid:
            _sup(db, sid)
        for b in bls:
            b.supplier_id = sid
    elif body.action in BULK_FIELDS:
        for b in bls:
            bs.apply_patch(db, b, {BULK_FIELDS[body.action]: body.value})
    elif body.action == "add_tag":
        if not body.value:
            raise HTTPException(400, "value (tag) required")
        for b in bls:
            meta = bs.ensure_meta(db, b)
            meta.tags = list(dict.fromkeys((meta.tags or []) + [body.value.strip()[:40]]))
    else:
        raise HTTPException(400, "action must be verify, delete, set_supplier, add_tag or one of " + ", ".join(BULK_FIELDS))
    log(db, ctx, "backlink.bulk_updated", None, {"count": len(bls), "action": body.action, "value": body.value})
    return {"updated": len(bls)}


@router.get("/projects/{pid}/backlinks/export.csv")
def export_csv(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("export"))):
    p = project_guard(db, ctx, pid)
    bls = db.scalars(select(m.Backlink).where(m.Backlink.project_id == pid).order_by(m.Backlink.source_url)).all()
    data = bs.export_csv(_rows(db, ctx, bls))
    log(db, ctx, "backlink.exported", f"project:{pid}", {"rows": len(bls)})
    fn = f"backlinks-{re.sub(r'[^a-z0-9]+', '-', p.name.lower()).strip('-') or 'project'}-{date.today():%Y%m%d}.csv"
    return Response(data, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@router.post("/projects/{pid}/backlinks/import")
async def import_csv(pid: uuid.UUID, file: UploadFile = File(...), db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    content = await _read(file, MAX_CSV)
    res = bs.import_csv(db, ctx.org_id, ctx.user_id, pid, content)
    ensure_verify_schedule(db, ctx.org_id)
    log(db, ctx, "backlink.imported", f"project:{pid}", {k: res[k] for k in ("created", "updated", "error_count")})
    return res


@router.get("/backlinks/{bid}")
def get_backlink(bid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    b = _bl(db, ctx, bid)
    ev = db.scalars(select(mb.BacklinkCheckEvidence).where(mb.BacklinkCheckEvidence.backlink_id == bid)
                    .order_by(mb.BacklinkCheckEvidence.checked_at.desc()).limit(200)).all()
    hist = {h.id: h for h in db.scalars(select(m.BacklinkCheckHistory).where(m.BacklinkCheckHistory.backlink_id == bid))}
    checks = [{**to_dict(e, exclude=("organisation_id",)), "http_status": hist[e.history_id].http_status if e.history_id in hist else None,
               "is_indexed": hist[e.history_id].is_indexed if e.history_id in hist else None} for e in ev]
    toxic = db.scalars(select(mb.BacklinkToxic).where(mb.BacklinkToxic.backlink_id == bid).order_by(mb.BacklinkToxic.imported_at.desc())).all()
    return {**_rows(db, ctx, [b])[0], "checks": checks,
            "status_changes": [{"at": c["checked_at"], "from": c["previous_status"], "to": c["status"], "reason": c["reason"]}
                               for c in checks if c["previous_status"] != c["status"] and c["status"] != "could_not_verify"],
            "toxic_history": [{"score": float(t.score), "source": t.source, "imported_at": t.imported_at, "markers": t.markers} for t in toxic],
            "files": _files(db, ctx, backlink_id=bid)}


@router.patch("/backlinks/{bid}")
def patch_backlink(bid: uuid.UUID, body: BacklinkPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    b = _bl(db, ctx, bid)
    d = body.model_dump(exclude_unset=True)
    if d.get("currency"):
        d["currency"] = d["currency"].upper()
    bs.apply_patch(db, b, d)
    log(db, ctx, "backlink.updated", f"backlink:{bid}", {"fields": list(d)})
    db.flush()
    return _rows(db, ctx, [b])[0]


@router.delete("/backlinks/{bid}")
def delete_backlink(bid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    b = _bl(db, ctx, bid)
    log(db, ctx, "backlink.deleted", f"backlink:{bid}", {"source": b.source_url, "target": b.target_url})
    db.delete(b)
    return {"ok": True}


# ================================================================== suppliers (F-014)
class SupplierIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str | None = Field(None, max_length=320)
    phone: str | None = Field(None, max_length=60)
    country: str | None = Field(None, max_length=80)
    niche: str | None = Field(None, max_length=200)
    rating: float | None = Field(None, ge=0, le=5)
    contact_name: str | None = Field(None, max_length=200)
    website: str | None = Field(None, max_length=500)
    payment_terms: str | None = Field(None, max_length=500)
    payment_method: str | None = Field(None, max_length=200)
    status: str | None = None
    tags: list[str] | None = Field(None, max_length=30)
    notes: str | None = Field(None, max_length=5000)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SUP_STATUS = ("active", "paused", "blacklisted")


def _apply_supplier(db, s: m.Supplier, d: dict, org_id):
    if d.get("email") and not EMAIL_RE.match(d["email"]):
        raise HTTPException(400, "Invalid email")
    if d.get("status") and d["status"] not in SUP_STATUS:
        raise HTTPException(400, f"status must be one of {SUP_STATUS}")
    if d.get("website"):
        d["website"] = bs.clean_url(d["website"], "website")
    for k in ("name", "email", "phone", "country", "niche", "rating"):
        if k in d:
            setattr(s, k, d[k])
    prof = db.get(mb.SupplierProfile, s.id)
    if not prof:
        prof = mb.SupplierProfile(supplier_id=s.id, organisation_id=org_id, tags=[])
        db.add(prof)
    for k in ("contact_name", "website", "payment_terms", "payment_method", "status", "tags", "notes"):
        if k in d and (d[k] is not None or k not in ("status", "tags")):
            setattr(prof, k, d[k])


def _supplier_stats(db, ids) -> dict:
    out = {}
    if not ids:
        return out
    for sid, st, paid, cur, cost in db.execute(select(m.Backlink.supplier_id, m.Backlink.status, m.Backlink.paid_or_free, m.Backlink.currency,
                                                      m.Backlink.cost).where(m.Backlink.supplier_id.in_(ids))):
        o = out.setdefault(sid, {"links": 0, "live": 0, "removed": 0, "spend": {}})
        o["links"] += 1
        o["live"] += st == "live"
        o["removed"] += st == "removed"
        if paid == "paid" and cost is not None:
            o["spend"][cur or "USD"] = round(o["spend"].get(cur or "USD", 0) + float(cost), 2)
    for o in out.values():
        chk = o["live"] + o["removed"]
        o["retention"] = round(o["live"] / chk, 4) if chk else None
    return out


@router.get("/suppliers")
def list_suppliers(q: str | None = Query(None, max_length=200), status: str | None = None, db=Depends(get_db),
                   ctx: Ctx = Depends(require("view")), _c: Ctx = Depends(deny_client)):
    qq = select(m.Supplier)
    if q:
        qq = qq.where(or_(m.Supplier.name.ilike(f"%{q}%"), m.Supplier.niche.ilike(f"%{q}%"), m.Supplier.email.ilike(f"%{q}%")))
    sups = db.scalars(qq.order_by(m.Supplier.name)).all()
    profs = {p.supplier_id: p for p in db.scalars(select(mb.SupplierProfile))}
    stats = _supplier_stats(db, [s.id for s in sups])
    last = dict(db.execute(select(mb.SupplierNote.supplier_id, func.max(mb.SupplierNote.occurred_at)).group_by(mb.SupplierNote.supplier_id)).all())
    prices = dict(db.execute(select(mb.SupplierPrice.supplier_id, func.count()).group_by(mb.SupplierPrice.supplier_id)).all())
    out = []
    for s in sups:
        p = profs.get(s.id)
        if status and (p.status if p else "active") != status:
            continue
        out.append({**to_dict(s), "contact_name": p.contact_name if p else None, "website": p.website if p else None,
                    "status": p.status if p else "active", "tags": p.tags if p else [], "stats": stats.get(s.id, {"links": 0, "live": 0, "removed": 0, "spend": {}, "retention": None}),
                    "last_contact": last.get(s.id), "price_rows": prices.get(s.id, 0)})
    return out


@router.post("/suppliers")
def create_supplier(body: SupplierIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    s = m.Supplier(organisation_id=ctx.org_id, name=body.name.strip(), price_list={})
    db.add(s)
    db.flush()
    _apply_supplier(db, s, body.model_dump(), ctx.org_id)
    log(db, ctx, "supplier.created", f"supplier:{s.id}", {"name": s.name})
    db.flush()
    return to_dict(s)


@router.get("/suppliers/{sid}")
def get_supplier(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c: Ctx = Depends(deny_client)):
    s = _sup(db, sid)
    p = db.get(mb.SupplierProfile, sid)
    bls = db.scalars(select(m.Backlink).where(m.Backlink.supplier_id == sid, m.Backlink.project_id.in_(visible_project_ids(db, ctx)))
                     .order_by(m.Backlink.source_url).limit(500)).all()
    creds = db.scalars(select(m.Credential).where(m.Credential.supplier_id == sid).order_by(desc(m.Credential.created_at))).all()
    show_creds = can(ctx.role, "access_credentials")
    return {**to_dict(s), "profile": to_dict(p, exclude=("organisation_id",)) if p else {"status": "active", "tags": []},
            "stats": _supplier_stats(db, [sid]).get(sid, {"links": 0, "live": 0, "removed": 0, "spend": {}, "retention": None}),
            "prices": [to_dict(x, exclude=("organisation_id",)) for x in db.scalars(select(mb.SupplierPrice).where(mb.SupplierPrice.supplier_id == sid)
                                                                                     .order_by(mb.SupplierPrice.domain))],
            "notes": [to_dict(x, exclude=("organisation_id",)) for x in db.scalars(select(mb.SupplierNote).where(mb.SupplierNote.supplier_id == sid)
                                                                                    .order_by(desc(mb.SupplierNote.occurred_at)).limit(300))],
            "backlinks": _rows(db, ctx, bls),
            "files": _files(db, ctx, supplier_id=sid),
            # credentials are linked through the vault (Settings → Vault); secrets are never returned here
            "credentials": [{"id": str(c.id), "label": c.label, "username": c.username, "masked_hint": c.masked_hint, "created_at": c.created_at,
                             "rotated_at": c.rotated_at} for c in creds] if show_creds else None,
            "credential_count": len(creds), "can_access_credentials": show_creds}


class SupplierPatch(SupplierIn):
    name: str | None = Field(None, min_length=1, max_length=200)


@router.patch("/suppliers/{sid}")
def patch_supplier(sid: uuid.UUID, body: SupplierPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    s = _sup(db, sid)
    d = body.model_dump(exclude_unset=True)
    if "name" in d and not (d["name"] or "").strip():
        raise HTTPException(400, "name can't be empty")
    _apply_supplier(db, s, d, ctx.org_id)
    log(db, ctx, "supplier.updated", f"supplier:{sid}", {"fields": list(d)})
    return to_dict(s)


@router.delete("/suppliers/{sid}")
def delete_supplier(sid: uuid.UUID, confirm: bool = False, db=Depends(get_db), ctx: Ctx = Depends(require("delete")),
                    _c: Ctx = Depends(deny_client)):
    s = _sup(db, sid)
    if not confirm:
        raise HTTPException(400, detail={"code": "confirm_required", "message": "Deleting a supplier removes its price list, notes and files "
                                                                                "and unlinks its backlinks — confirm to proceed."})
    for f in db.scalars(select(m.BacklinkFile).where(m.BacklinkFile.supplier_id == sid, m.BacklinkFile.backlink_id.is_(None))):
        db.delete(f)
    for c in db.scalars(select(m.Credential).where(m.Credential.supplier_id == sid)):
        c.supplier_id = None  # credential stays in the vault, unlinked
    log(db, ctx, "supplier.deleted", f"supplier:{sid}", {"name": s.name})
    db.delete(s)
    return {"ok": True}


class PriceIn(BaseModel):
    domain: str = Field(..., max_length=253)
    service: str = "guest_post"
    price: float = Field(..., ge=0, le=10_000_000)
    currency: str = Field("USD", min_length=3, max_length=3)
    link_type: str | None = None
    dr: float | None = Field(None, ge=0, le=100)
    turnaround_days: int | None = Field(None, ge=0, le=365)
    notes: str | None = Field(None, max_length=2000)


@router.post("/suppliers/{sid}/prices")
def add_price(sid: uuid.UUID, body: PriceIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    _sup(db, sid)
    dom = bs.host_of(body.domain) if ("/" in body.domain or ":" in body.domain) else body.domain.strip().lower().removeprefix("www.")
    if not bs.DOMAIN_RE.match(dom):
        raise HTTPException(400, "domain must look like example.com")
    if body.service not in ("guest_post", "link_insertion", "niche_edit", "other"):
        raise HTTPException(400, "service must be guest_post, link_insertion, niche_edit or other")
    if body.link_type and body.link_type not in mb.LINK_TYPES:
        raise HTTPException(400, f"link_type must be one of {mb.LINK_TYPES}")
    if not re.match(r"^[A-Za-z]{3}$", body.currency):
        raise HTTPException(400, "currency must be a 3-letter code")
    p = mb.SupplierPrice(organisation_id=ctx.org_id, supplier_id=sid, **{**body.model_dump(), "domain": dom, "currency": body.currency.upper()})
    db.add(p)
    db.flush()
    log(db, ctx, "supplier.price_added", f"supplier:{sid}", {"domain": dom, "price": body.price})
    return to_dict(p, exclude=("organisation_id",))


@router.delete("/supplier-prices/{prid}")
def del_price(prid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    p = db.get(mb.SupplierPrice, prid)
    if not p:
        raise HTTPException(404)
    db.delete(p)
    return {"ok": True}


class NoteIn(BaseModel):
    kind: str = "note"
    direction: str | None = None
    subject: str | None = Field(None, max_length=300)
    body: str = Field(..., min_length=1, max_length=20000)
    occurred_at: datetime | None = None


@router.post("/suppliers/{sid}/notes")
def add_note(sid: uuid.UUID, body: NoteIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    _sup(db, sid)
    if body.kind not in ("note", "email", "call", "meeting", "chat"):
        raise HTTPException(400, "kind must be note, email, call, meeting or chat")
    if body.direction and body.direction not in ("inbound", "outbound"):
        raise HTTPException(400, "direction must be inbound or outbound")
    n = mb.SupplierNote(organisation_id=ctx.org_id, supplier_id=sid, kind=body.kind, direction=body.direction, subject=body.subject,
                        body=body.body, occurred_at=body.occurred_at or _now(), created_by=ctx.user_id)
    db.add(n)
    db.flush()
    return to_dict(n, exclude=("organisation_id",))


@router.delete("/supplier-notes/{nid}")
def del_note(nid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete")), _c: Ctx = Depends(deny_client)):
    n = db.get(mb.SupplierNote, nid)
    if not n:
        raise HTTPException(404)
    db.delete(n)
    return {"ok": True}


# ================================================================== files (F-017)
def _files(db, ctx: Ctx, backlink_id=None, supplier_id=None, project_id=None) -> list[dict]:
    q = select(m.BacklinkFile, m.StoredFile, mb.BacklinkFileMeta).join(m.StoredFile, m.StoredFile.id == m.BacklinkFile.stored_file_id) \
        .outerjoin(mb.BacklinkFileMeta, mb.BacklinkFileMeta.backlink_file_id == m.BacklinkFile.id)
    if backlink_id:
        q = q.where(m.BacklinkFile.backlink_id == backlink_id)
    if supplier_id:
        q = q.where(m.BacklinkFile.supplier_id == supplier_id)
    if project_id:
        q = q.where(or_(mb.BacklinkFileMeta.project_id == project_id,
                        m.BacklinkFile.backlink_id.in_(select(m.Backlink.id).where(m.Backlink.project_id == project_id))))
    out = []
    for bf, sf, meta in db.execute(q.order_by(desc(m.StoredFile.created_at)).limit(500)):
        if not _file_visible(db, ctx, bf, meta):
            continue
        out.append({"id": str(bf.id), "kind": bf.kind, "filename": sf.filename, "content_type": sf.content_type, "size_bytes": sf.size_bytes,
                    "created_at": sf.created_at, "backlink_id": str(bf.backlink_id) if bf.backlink_id else None,
                    "supplier_id": str(bf.supplier_id) if bf.supplier_id else None,
                    "project_id": str(meta.project_id) if meta and meta.project_id else None, "note": meta.note if meta else None})
    return out


def _file_visible(db, ctx: Ctx, bf: m.BacklinkFile, meta) -> bool:
    if ctx.role != "client":
        return True
    pids = set(db.scalars(visible_project_ids(db, ctx)))
    if bf.backlink_id:
        b = db.get(m.Backlink, bf.backlink_id)
        return bool(b and b.project_id in pids)
    if meta and meta.project_id:
        return meta.project_id in pids
    return False  # supplier-only files are internal


@router.post("/backlink-files")
async def upload_file(file: UploadFile = File(...), kind: str = Form("invoice"), backlink_id: str | None = Form(None),
                      supplier_id: str | None = Form(None), project_id: str | None = Form(None), note: str | None = Form(None),
                      db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    if kind not in mb.FILE_KINDS:
        raise HTTPException(400, f"kind must be one of {mb.FILE_KINDS}")
    bid, sid, pid = bs.uuid_or_none(backlink_id), bs.uuid_or_none(supplier_id), bs.uuid_or_none(project_id)
    if not (bid or sid or pid):
        raise HTTPException(400, "Attach the file to a backlink, a supplier or a project")
    if bid:
        b = _bl(db, ctx, bid)
        pid = pid or b.project_id
    if pid:
        project_guard(db, ctx, pid)
    if sid:
        if ctx.role == "client":
            raise HTTPException(403, "Not available to client users")
        _sup(db, sid)
    ctype = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if ctype not in ALLOWED_FILE_TYPES:
        raise HTTPException(415, f"File type {ctype} is not allowed (PDF, images, CSV/XLSX, DOCX, TXT, ZIP)")
    content = await _read(file, MAX_UPLOAD)
    used = db.scalar(select(func.coalesce(func.sum(m.StoredFile.size_bytes), 0))) or 0
    ent.check_count_limit(db, ctx.org_id, "storage_bytes", int(used), len(content))
    sf = rep.store(db, ctx.org_id, bs.safe_filename(file.filename or "file"), content, ctype, user_id=ctx.user_id, kind="upload")
    bf = m.BacklinkFile(organisation_id=ctx.org_id, backlink_id=bid, supplier_id=sid, kind=kind, stored_file_id=sf.id)
    db.add(bf)
    db.flush()
    db.add(mb.BacklinkFileMeta(backlink_file_id=bf.id, organisation_id=ctx.org_id, project_id=pid, note=(note or "")[:1000] or None,
                               uploaded_by=ctx.user_id))
    log(db, ctx, "backlink.file_uploaded", f"backlink_file:{bf.id}", {"kind": kind, "filename": sf.filename, "bytes": len(content)})
    return {"id": str(bf.id), "filename": sf.filename, "size_bytes": sf.size_bytes, "kind": kind}


@router.get("/backlink-files")
def list_files(backlink_id: uuid.UUID | None = None, supplier_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None,
               db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    if backlink_id:
        _bl(db, ctx, backlink_id)
    if project_id:
        project_guard(db, ctx, project_id)
    if supplier_id and ctx.role == "client":
        raise HTTPException(403, "Not available to client users")
    return _files(db, ctx, backlink_id, supplier_id, project_id)


def _file(db, ctx: Ctx, fid) -> tuple[m.BacklinkFile, m.StoredFile]:
    bf = db.get(m.BacklinkFile, fid)  # RLS: other organisations' files are invisible
    if not bf:
        raise HTTPException(404, "File not found")
    meta = db.get(mb.BacklinkFileMeta, fid)
    if not _file_visible(db, ctx, bf, meta):
        raise HTTPException(404, "File not found")
    if bf.backlink_id:
        _bl(db, ctx, bf.backlink_id)
    sf = db.get(m.StoredFile, bf.stored_file_id)
    if not sf or sf.organisation_id != ctx.org_id:
        raise HTTPException(404, "File not found")
    return bf, sf


@router.get("/backlink-files/{fid}/download")
def download_file(fid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    bf, sf = _file(db, ctx, fid)
    path = rep.path_of(sf)
    if not path.is_file():
        raise HTTPException(410, "The stored file is missing")
    log(db, ctx, "backlink.file_downloaded", f"backlink_file:{fid}", {"filename": sf.filename})
    return FileResponse(path, media_type=sf.content_type or "application/octet-stream", filename=sf.filename)


@router.delete("/backlink-files/{fid}")
def delete_file(fid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    bf, sf = _file(db, ctx, fid)
    path = rep.path_of(sf)
    db.delete(bf)
    db.flush()
    db.delete(sf)
    log(db, ctx, "backlink.file_deleted", f"backlink_file:{fid}", {"filename": sf.filename})
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}


# ================================================================== toxic scores + disavow (F-016)
@router.post("/projects/{pid}/toxic/import")
async def import_toxic(pid: uuid.UUID, file: UploadFile = File(...), db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, pid)
    res = bs.import_toxic_csv(db, ctx.org_id, pid, await _read(file, MAX_CSV))
    bp.mark_import(db, ctx.org_id, pid, "semrush", res["rows"])
    log(db, ctx, "backlink.toxic_imported", f"project:{pid}", {k: res[k] for k in ("rows", "matched_backlinks")})
    return res


@router.get("/projects/{pid}/toxic")
def list_toxic(pid: uuid.UUID, min_score: float = Query(0, ge=0, le=100), db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    rows = db.scalars(select(mb.BacklinkToxic).where(mb.BacklinkToxic.project_id == pid, mb.BacklinkToxic.score >= min_score)
                      .order_by(desc(mb.BacklinkToxic.score)).limit(2000)).all()
    listed = set()
    for dl in db.scalars(select(mb.DisavowList).where(mb.DisavowList.project_id == pid, mb.DisavowList.status != "rejected")):
        listed |= {e["value"] for e in dl.entries}
    return {"advisory": "Toxic scores are advisory (Semrush Backlink Audit). Disavowing needs a manager's approval.",
            "threshold": bs.TOXIC_THRESHOLD,
            "items": [{**to_dict(t, exclude=("organisation_id",)), "in_disavow": t.source_domain in listed or t.source_url in listed} for t in rows]}


class DisavowIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    entries: list[dict] = Field(default_factory=list, max_length=5000)
    toxic_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    as_domains: bool = True


def _dl(db, ctx, did) -> mb.DisavowList:
    d = db.get(mb.DisavowList, did)
    if not d:
        raise HTTPException(404, "Disavow list not found")
    project_guard(db, ctx, d.project_id)
    _sync_approval(db, d)
    return d


def _sync_approval(db, d: mb.DisavowList):
    if d.approval_id and d.status in ("pending_approval",):
        a = db.get(m.Approval, d.approval_id)
        if a and a.status == "approved":
            d.status = "approved"
        elif a and a.status == "rejected":
            d.status = "rejected"


def _dl_out(db, d: mb.DisavowList) -> dict:
    a = db.get(m.Approval, d.approval_id) if d.approval_id else None
    return {**to_dict(d, exclude=("organisation_id",)), "domains": sum(1 for e in d.entries if e["type"] == "domain"),
            "urls": sum(1 for e in d.entries if e["type"] == "url"),
            "approval": {"id": str(a.id), "status": a.status, "chain": a.chain, "step": a.step, "decisions": a.decisions} if a else None}


@router.get("/projects/{pid}/disavow")
def list_disavow(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c: Ctx = Depends(deny_client)):
    project_guard(db, ctx, pid)
    out = []
    for d in db.scalars(select(mb.DisavowList).where(mb.DisavowList.project_id == pid).order_by(desc(mb.DisavowList.created_at))):
        _sync_approval(db, d)
        out.append(_dl_out(db, d))
    return out


@router.post("/projects/{pid}/disavow")
def create_disavow(pid: uuid.UUID, body: DisavowIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    project_guard(db, ctx, pid)
    entries = [bs.clean_entry(e) for e in body.entries]
    for tid in body.toxic_ids:
        t = db.get(mb.BacklinkToxic, tid)
        if not t or t.project_id != pid:
            raise HTTPException(400, f"Toxic row {tid} is not in this project")
        entries.append(bs.clean_entry({"type": "domain" if body.as_domains else "url", "value": t.source_domain if body.as_domains else t.source_url,
                                       "reason": f"Semrush toxicity {float(t.score):g}" + (f" ({t.markers})" if t.markers else ""), "score": t.score}))
    uniq = {}
    for e in entries:
        uniq.setdefault((e["type"], e["value"]), e)
    if not uniq:
        raise HTTPException(400, "Add at least one domain or URL")
    d = mb.DisavowList(organisation_id=ctx.org_id, project_id=pid, title=body.title, entries=list(uniq.values()), created_by=ctx.user_id)
    db.add(d)
    db.flush()
    log(db, ctx, "disavow.created", f"disavow:{d.id}", {"entries": len(uniq)})
    return _dl_out(db, d)


@router.get("/disavow/{did}")
def get_disavow(did: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c: Ctx = Depends(deny_client)):
    return _dl_out(db, _dl(db, ctx, did))


@router.patch("/disavow/{did}")
def patch_disavow(did: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    d = _dl(db, ctx, did)
    if d.status not in ("draft", "rejected"):
        raise HTTPException(409, f"A {d.status.replace('_', ' ')} list can't be edited — create a new list")
    if "title" in body:
        d.title = str(body["title"])[:200] or d.title
    if "entries" in body:
        if not isinstance(body["entries"], list) or len(body["entries"]) > 5000:
            raise HTTPException(400, "entries must be a list (max 5000)")
        d.entries = [bs.clean_entry(e) for e in body["entries"]]
    d.status, d.approval_id = "draft", None if d.status == "rejected" else d.approval_id
    return _dl_out(db, d)


@router.post("/disavow/{did}/submit")
def submit_disavow(did: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    d = _dl(db, ctx, did)
    if d.status not in ("draft", "rejected"):
        raise HTTPException(409, f"List is already {d.status.replace('_', ' ')}")
    if not d.entries:
        raise HTTPException(400, "The list is empty")
    p = db.get(m.Project, d.project_id)
    a = m.Approval(organisation_id=ctx.org_id, entity_type="disavow_list", entity_id=d.id, chain=["manager"], requested_by=ctx.user_id,
                   title=f"Disavow {len(d.entries)} entries for {p.name}: {d.title}",
                   notes="Disavowing links is irreversible in effect — review the domains and URLs before approving.")
    db.add(a)
    db.flush()
    d.approval_id, d.status = a.id, "pending_approval"
    from ..deps import notify
    notify(db, ctx.org_id, "approval_requested", f"Approval requested: {a.title}", "Awaiting manager", "/approvals")
    log(db, ctx, "disavow.submitted", f"disavow:{d.id}", {"approval_id": str(a.id)})
    return _dl_out(db, d)


@router.post("/disavow/{did}/generate")
def generate_disavow(did: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    d = _dl(db, ctx, did)
    if d.status not in ("approved", "generated"):
        raise HTTPException(409, detail={"code": "approval_required",
                                         "message": "A manager must approve this disavow list (Approvals) before the file can be generated."})
    a = db.get(m.Approval, d.approval_id) if d.approval_id else None
    if not a or a.status != "approved":
        raise HTTPException(409, detail={"code": "approval_required", "message": "No approved approval for this list."})
    approver = None
    if a.decisions:
        u = db.get(m.User, uuid.UUID(a.decisions[-1]["by"]))
        approver = (u.name or u.email) if u else None
    p = db.get(m.Project, d.project_id)
    content = bs.disavow_text(d, p, approver)
    used = db.scalar(select(func.coalesce(func.sum(m.StoredFile.size_bytes), 0))) or 0
    ent.check_count_limit(db, ctx.org_id, "storage_bytes", int(used), len(content))
    sf = rep.store(db, ctx.org_id, bs.safe_filename(f"disavow-{p.name}-{date.today():%Y%m%d}.txt"), content, "text/plain",
                   user_id=ctx.user_id, kind="disavow")
    d.stored_file_id, d.status, d.generated_at = sf.id, "generated", _now()
    log(db, ctx, "disavow.generated", f"disavow:{d.id}", {"entries": len(d.entries), "file": str(sf.id)})
    return _dl_out(db, d)


@router.get("/disavow/{did}/download")
def download_disavow(did: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("export")), _c: Ctx = Depends(deny_client)):
    d = _dl(db, ctx, did)
    if d.status != "generated" or not d.stored_file_id:
        raise HTTPException(409, "Generate the file after approval first")
    sf = db.get(m.StoredFile, d.stored_file_id)
    if not sf:
        raise HTTPException(404)
    return FileResponse(rep.path_of(sf), media_type="text/plain", filename=sf.filename)


# ================================================================== Ahrefs / Semrush (modules 9, 10)
def _prov(provider: str) -> str:
    if provider not in bp.NAMES:
        raise HTTPException(404, "provider must be ahrefs or semrush")
    return provider


@router.get("/projects/{pid}/providers/{provider}")
def provider_summary(pid: uuid.UUID, provider: str, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, pid)
    return bp.summary(db, p, _prov(provider))


class ProviderCfg(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    country: str | None = Field(None, max_length=2)
    database: str | None = Field(None, max_length=10)


@router.put("/projects/{pid}/providers/{provider}/config")
def provider_config(pid: uuid.UUID, provider: str, body: ProviderCfg, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, pid)
    dom = bs.host_of(body.domain)
    if not bs.DOMAIN_RE.match(dom):
        raise HTTPException(400, "Enter the project's domain, e.g. example.com")
    cfg = {"country": (body.country or "").lower() or None, "database": (body.database or "").lower() or None}
    s = bp.set_source(db, ctx.org_id, pid, _prov(provider), dom, cfg)
    log(db, ctx, "provider.configured", f"project:{pid}", {"provider": provider, "domain": dom})
    return {"domain": s.property, "config": s.config}


@router.post("/projects/{pid}/providers/{provider}/sync")
def provider_sync(pid: uuid.UUID, provider: str, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, pid)
    _prov(provider)
    if not bp.connected(db, provider):
        raise HTTPException(409, detail={"code": "not_connected", "message": bp.not_connected_msg(provider)})
    s = bp.get_source(db, pid, provider)
    if not s or not s.property:
        raise HTTPException(400, f"Set the project's domain for {bp.NAMES[provider]} first")
    s.mode = "api"
    j = enqueue(db, ctx.org_id, "backlinks_provider_sync", {"source_id": str(s.id)}, created_by=ctx.user_id, max_attempts=2)
    log(db, ctx, "provider.sync_requested", f"project:{pid}", {"provider": provider})
    return {"job_id": str(j.id)}


@router.post("/projects/{pid}/providers/{provider}/import/{kind}")
async def provider_import(pid: uuid.UUID, provider: str, kind: str, file: UploadFile = File(...), domain: str | None = Form(None),
                          is_own: bool = Form(True), target: str | None = Form(None), db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    """kind: backlinks | refdomains | keywords (organic keywords / positions) | toxic (Semrush Backlink Audit)."""
    project_guard(db, ctx, pid)
    _prov(provider)
    content = await _read(file, MAX_CSV)
    src = bp.get_source(db, pid, provider)
    dom = bs.host_of(domain) if domain else (src.property if src else None)
    if kind == "backlinks":
        res = bp.import_ext_backlinks(db, ctx.org_id, pid, content, provider)
    elif kind == "refdomains":
        if not dom:
            raise HTTPException(400, "domain is required (the site these referring domains link to)")
        tgt = "own" if is_own else bs.host_of(target or dom)
        res = bp.import_refdomains(db, ctx.org_id, pid, content, dom, provider, target=tgt)
    elif kind == "keywords":
        if not dom:
            raise HTTPException(400, "domain is required (whose organic keywords these are)")
        res = bp.import_keywords(db, ctx.org_id, pid, content, dom, is_own, provider)
    elif kind == "toxic" and provider == "semrush":
        res = bs.import_toxic_csv(db, ctx.org_id, pid, content)
    else:
        raise HTTPException(400, "kind must be backlinks, refdomains, keywords" + (" or toxic" if provider == "semrush" else ""))
    bp.mark_import(db, ctx.org_id, pid, provider, int(res.get("rows") or 0))
    log(db, ctx, "provider.imported", f"project:{pid}", {"provider": provider, "kind": kind, "rows": res.get("rows")})
    return res


class ToCrmIn(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=1000)


@router.post("/projects/{pid}/ext-backlinks/to-crm")
def ext_to_crm(pid: uuid.UUID, body: ToCrmIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    have = {(bs.url_key(b.source_url), bs.url_key(b.target_url)) for b in db.scalars(select(m.Backlink).where(m.Backlink.project_id == pid))}
    added, skipped = 0, 0
    for x in db.scalars(select(mb.ExtBacklink).where(mb.ExtBacklink.id.in_(body.ids), mb.ExtBacklink.project_id == pid)):
        if not x.target_url or (bs.url_key(x.source_url), bs.url_key(x.target_url)) in have:
            skipped += 1
            continue
        try:
            with db.begin_nested():
                bs.create_backlink(db, ctx.org_id, ctx.user_id, pid, {"source_url": x.source_url, "target_url": x.target_url,
                                                                       "anchor_text": (x.anchor or None), "link_type": x.rel or "dofollow",
                                                                       "paid_or_free": "free", "dr": float(x.authority) if x.authority is not None and x.provider == "ahrefs" else None,
                                                                       "notes": f"Discovered by {bp.NAMES[x.provider]}"}, source=x.provider)
            have.add((bs.url_key(x.source_url), bs.url_key(x.target_url)))
            added += 1
        except HTTPException:
            skipped += 1
    ensure_verify_schedule(db, ctx.org_id)
    log(db, ctx, "backlink.added_from_provider", f"project:{pid}", {"added": added})
    return {"added": added, "skipped": skipped}


# ================================================================== keyword research (module 11)
@router.get("/projects/{pid}/keyword-research")
def keyword_research(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, pid)
    res = bk.research(db, p)
    res["connections"] = {k: bp.connected(db, k) for k in ("ahrefs", "semrush", "gsc")}
    return res


@router.get("/projects/{pid}/keyword-research/gap")
def keyword_gap(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return bk.gap(db, project_guard(db, ctx, pid))


class TrackIn(BaseModel):
    phrases: list[str] = Field(..., min_length=1, max_length=500)
    country: str = Field("us", min_length=2, max_length=2)
    lang: str = Field("en", min_length=2, max_length=5)
    tags: list[str] = Field(default_factory=list, max_length=10)


@router.post("/projects/{pid}/keyword-research/track")
def track_keywords(pid: uuid.UUID, body: TrackIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    from .seo import KeywordsIn, add_keywords  # the rank tracker's own endpoint logic (plan limits, dedupe, log)
    phrases = [x.strip()[:200] for x in body.phrases if x and x.strip()]
    if not phrases:
        raise HTTPException(400, "No keywords given")
    return add_keywords(KeywordsIn(project_id=pid, phrases=phrases, country=body.country.lower(), lang=body.lang.lower(),
                                   tags=body.tags or ["keyword-research"]), db=db, ctx=ctx)


@router.post("/projects/{pid}/keyword-research/difficulty")
def refresh_difficulty(pid: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, pid)
    if not (bp.connected(db, "ahrefs") or bp.connected(db, "semrush")):
        raise HTTPException(409, detail={"code": "not_connected", "message": "Keyword difficulty needs Ahrefs or Semrush — both are NOT "
                                                                            "CONNECTED. Import a keyword export with a KD column instead."})
    phrases = [str(x)[:200] for x in (body.get("phrases") or [])][:100]
    if not phrases:
        phrases = [k["keyword"] for k in bk.research(db, db.get(m.Project, pid))["keywords"] if k.get("difficulty") is None][:100]
    j = enqueue(db, ctx.org_id, "backlinks_kd_refresh", {"project_id": str(pid), "phrases": phrases}, created_by=ctx.user_id, max_attempts=2)
    return {"job_id": str(j.id), "phrases": len(phrases)}


# ================================================================== ReleVink (module 63)
class RelevinkIn(BaseModel):
    target_url: str = Field(..., max_length=2000)
    keyword: str = Field(..., min_length=2, max_length=200)
    anchor: str | None = Field(None, max_length=200)
    mode: str = "both"
    candidates: list[str] = Field(default_factory=list, max_length=30)
    include_db: bool = False
    use_ai: bool = False


@router.post("/projects/{pid}/relevink")
def relevink_run(pid: uuid.UUID, body: RelevinkIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    if body.mode not in ("internal", "external", "both"):
        raise HTTPException(400, "mode must be internal, external or both")
    tgt = bs.clean_url(body.target_url, "target_url")
    cands = []
    for c in body.candidates:
        c = c.strip()
        if not c:
            continue
        if "://" in c:
            cands.append(bs.clean_url(c, "candidate URL"))
        else:
            d = c.strip("/").lower()
            if not (bs.DOMAIN_RE.match(d.split("/")[0].split(":")[0]) or d.split(":")[0] in ("localhost", "127.0.0.1")):
                raise HTTPException(400, f"Invalid candidate: {c}")
            cands.append(d)
    r = mb.RelevinkRun(organisation_id=ctx.org_id, project_id=pid, target_url=tgt, keyword=body.keyword.strip(), anchor=(body.anchor or "").strip() or None,
                       mode=body.mode, candidates=cands, include_db=body.include_db and ctx.role != "client", use_ai=body.use_ai, created_by=ctx.user_id)
    db.add(r)
    db.flush()
    j = enqueue(db, ctx.org_id, "backlinks_relevink", {"run_id": str(r.id)}, created_by=ctx.user_id, max_attempts=1)
    log(db, ctx, "relevink.run", f"project:{pid}", {"keyword": r.keyword, "mode": r.mode})
    return {"run_id": str(r.id), "job_id": str(j.id)}


@router.get("/projects/{pid}/relevink")
def relevink_runs(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    return [to_dict(r, exclude=("organisation_id", "result")) for r in db.scalars(select(mb.RelevinkRun).where(mb.RelevinkRun.project_id == pid)
                                                                                  .order_by(desc(mb.RelevinkRun.created_at)).limit(100))]


@router.get("/relevink/{rid}")
def relevink_get(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    r = db.get(mb.RelevinkRun, rid)
    if not r:
        raise HTTPException(404)
    project_guard(db, ctx, r.project_id)
    return {**to_dict(r, exclude=("organisation_id",)), "ai_connected": any(ih.is_connected(db, k) for k in ("claude", "openai", "gemini", "perplexity"))}


# ================================================================== page-to-page competitor audit (module 58)
class CompareIn(BaseModel):
    our_url: str = Field(..., max_length=2000)
    competitor_urls: list[str] = Field(..., min_length=1, max_length=5)
    keyword: str | None = Field(None, max_length=200)


@router.post("/projects/{pid}/page-compare")
def page_compare(pid: uuid.UUID, body: CompareIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    our = bs.clean_url(body.our_url, "our_url")
    comps = list(dict.fromkeys(bs.clean_url(u, "competitor URL") for u in body.competitor_urls if u.strip()))
    comps = [c for c in comps if bs.url_key(c) != bs.url_key(our)]
    if not comps:
        raise HTTPException(400, "Add at least one competitor URL different from yours")
    c = mb.PageComparison(organisation_id=ctx.org_id, project_id=pid, our_url=our, competitor_urls=comps,
                          keyword=(body.keyword or "").strip() or None, created_by=ctx.user_id)
    db.add(c)
    db.flush()
    j = enqueue(db, ctx.org_id, "backlinks_page_compare", {"comparison_id": str(c.id)}, created_by=ctx.user_id, max_attempts=1)
    log(db, ctx, "page_compare.run", f"project:{pid}", {"our_url": our, "competitors": len(comps)})
    return {"comparison_id": str(c.id), "job_id": str(j.id)}


@router.get("/projects/{pid}/page-compare")
def page_compares(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    out = []
    for c in db.scalars(select(mb.PageComparison).where(mb.PageComparison.project_id == pid).order_by(desc(mb.PageComparison.created_at)).limit(100)):
        d = to_dict(c, exclude=("organisation_id", "result"))
        d["our_score"], d["competitor_avg"], d["our_rank"] = (c.result or {}).get("our_score"), (c.result or {}).get("competitor_avg"), (c.result or {}).get("our_rank")
        out.append(d)
    return out


@router.get("/page-compare/{cid}")
def page_compare_get(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    c = db.get(mb.PageComparison, cid)
    if not c:
        raise HTTPException(404)
    project_guard(db, ctx, c.project_id)
    return to_dict(c, exclude=("organisation_id",))
