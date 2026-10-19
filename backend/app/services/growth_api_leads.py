"""API: Leads (41), Form Builder (64), Form Connectors (65), Attribution (42), HubSpot (43), Calendly (44),
Onboarding/Profiling (66). Mounted by routers/growth.py under /api."""
from __future__ import annotations

import csv
import io
import json
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select

from .. import models as m
from .. import models_growth as g
from ..db import tenant_session
from ..deps import Ctx, deny_client, get_db, log, project_guard, require, visible_project_ids
from ..util import to_dict
from . import growth_crm as crm
from . import growth_forms as F
from . import growth_leads as L
from . import vault
from .growth_common import check_url, new_token, utcnow
from .integrations import NotConnected, connected_map

router = APIRouter()


def nc(e: Exception, provider: str):
    raise HTTPException(409, detail={"code": "not_connected", "provider": provider, "message": str(e)})


def base_url(request: Request) -> str:
    return (os.environ.get("APP_BASE_URL") or str(request.base_url)).rstrip("/")


def _lead(db, ctx: Ctx, lid) -> tuple[m.Lead, g.GrowthLead]:
    lead = db.get(m.Lead, lid)
    if not lead:
        raise HTTPException(404, "Lead not found")
    project_guard(db, ctx, lead.project_id)
    return lead, L.ext_of(db, lead)


# ================================================================== leads
class LeadIn(BaseModel):
    project_id: uuid.UUID
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=40)
    name: str | None = Field(None, max_length=200)
    company: str | None = Field(None, max_length=200)
    value: float | None = Field(None, ge=0, le=100_000_000)
    status: str = "new"
    notes: str | None = Field(None, max_length=5000)
    utm_source: str | None = Field(None, max_length=200)
    utm_medium: str | None = Field(None, max_length=200)
    utm_campaign: str | None = Field(None, max_length=200)
    referrer: str | None = Field(None, max_length=2000)
    assignee_id: uuid.UUID | None = None


class LeadPatch(BaseModel):
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=40)
    name: str | None = Field(None, max_length=200)
    company: str | None = Field(None, max_length=200)
    value: float | None = Field(None, ge=0, le=100_000_000)
    status: str | None = None
    lost_reason: str | None = Field(None, max_length=500)
    assignee_id: uuid.UUID | None = None
    segment: str | None = Field(None, max_length=100)


@router.get("/leads/meta")
def leads_meta(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    users = [{"id": str(u.id), "name": u.name or u.email, "role": u.role} for u in db.scalars(select(m.User).where(m.User.is_active))
             if u.role != "client"]
    return {"statuses": L.STATUSES, "users": [] if ctx.role == "client" else users, "lead_fields": L.LEAD_FIELDS}


@router.get("/leads")
def list_leads(project_id: uuid.UUID | None = None, status: str | None = None, assignee: str | None = None,
               q: str | None = Query(None, max_length=200), source: str | None = None, segment: str | None = None,
               duplicates: bool = False, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
               db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    base = select(m.Lead, g.GrowthLead).outerjoin(g.GrowthLead, g.GrowthLead.lead_id == m.Lead.id) \
        .where(m.Lead.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        project_guard(db, ctx, project_id)
        base = base.where(m.Lead.project_id == project_id)
    if source:
        base = base.where(g.GrowthLead.source == source)
    if segment:
        base = base.where(g.GrowthLead.segment == segment)
    if assignee == "me":
        base = base.where(g.GrowthLead.assignee_id == ctx.user_id)
    elif assignee == "none":
        base = base.where(g.GrowthLead.assignee_id.is_(None))
    elif assignee:
        base = base.where(g.GrowthLead.assignee_id == L.uuid_or_400(assignee, "assignee"))
    if duplicates:
        base = base.where(g.GrowthLead.possible_duplicate_of.isnot(None))
    if q:
        like = f"%{q}%"
        base = base.where(or_(m.Lead.email.ilike(like), m.Lead.phone.ilike(like), g.GrowthLead.name.ilike(like), g.GrowthLead.company.ilike(like)))
    sub = base.with_only_columns(m.Lead.status).subquery()
    counts = {st: 0 for st in L.STATUSES}
    for st, n in db.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all():
        counts[st] = n
    if status:
        if status not in L.STATUSES:
            raise HTTPException(400, f"status must be one of {L.STATUSES}")
        base = base.where(m.Lead.status == status)
    total = db.scalar(select(func.count()).select_from(base.with_only_columns(m.Lead.id).subquery()))
    users = L.users_map(db)
    items = [L.lead_dict(l, x, users) for l, x in db.execute(base.order_by(desc(m.Lead.created_at)).limit(limit).offset(offset))]
    pipeline_value = db.scalar(select(func.coalesce(func.sum(g.GrowthLead.value), 0)).join(m.Lead, m.Lead.id == g.GrowthLead.lead_id)
                               .where(m.Lead.project_id.in_(visible_project_ids(db, ctx)), m.Lead.status.in_(["new", "contacted", "qualified"]),
                                      *( [m.Lead.project_id == project_id] if project_id else [])))
    return {"items": items, "total": total, "counts": counts, "pipeline_value": float(pipeline_value or 0)}


@router.post("/leads")
def create_lead(body: LeadIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    if body.status not in L.STATUSES:
        raise HTTPException(400, f"status must be one of {L.STATUSES}")
    data = body.model_dump(exclude={"project_id", "utm_source", "utm_medium", "utm_campaign", "referrer", "assignee_id"})
    touch = {k: getattr(body, k) for k in ("utm_source", "utm_medium", "utm_campaign", "referrer") if getattr(body, k)}
    lead, created = L.capture(db, ctx.org_id, body.project_id, data, source="manual", kind="manual", touch=touch or {"utm_source": "(manual)", "utm_medium": "(none)"},
                              actor_id=ctx.user_id)
    if body.assignee_id:
        L.assign(db, lead, body.assignee_id, ctx.user_id)
    log(db, ctx, "lead.created" if created else "lead.deduplicated", f"lead:{lead.id}")
    db.flush()
    return {**L.lead_dict(lead, L.ext_of(db, lead), L.users_map(db)), "created": created}


@router.get("/leads/duplicates")
def duplicates(project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(m.Lead, g.GrowthLead).join(g.GrowthLead, g.GrowthLead.lead_id == m.Lead.id) \
        .where(g.GrowthLead.possible_duplicate_of.isnot(None), m.Lead.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        q = q.where(m.Lead.project_id == project_id)
    out = []
    for l, x in db.execute(q.limit(500)):
        other = db.get(m.Lead, x.possible_duplicate_of)
        if not other:
            continue
        ox = L.ext_of(db, other)
        out.append({"lead": L.lead_dict(l, x), "candidate": L.lead_dict(other, ox),
                    "similarity": L.similarity(x.name, x.company, ox.name, ox.company)})
    return out


@router.get("/leads/export.csv")
def export_leads(project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("export"))):
    data = list_leads(project_id, None, None, None, None, None, False, 1000, 0, db, ctx)["items"]
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["id", "created_at", "status", "name", "email", "phone", "company", "value", "score", "segment", "source",
            "utm_source", "utm_medium", "utm_campaign", "assignee", "crm_contact_id"]
    w.writerow(cols)
    for d in data:
        w.writerow([d.get(c) if d.get(c) is not None else "" for c in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=leads.csv"})


CSV_ALIASES = {"e-mail": "email", "email address": "email", "mobile": "phone", "telephone": "phone", "phone number": "phone",
               "full name": "name", "first name": "first_name", "last name": "last_name", "organisation": "company",
               "organization": "company", "deal value": "value", "source": "utm_source", "medium": "utm_medium", "campaign": "utm_campaign"}


@router.post("/leads/import")
async def import_leads(project_id: uuid.UUID = Form(...), file: UploadFile = File(...), db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, project_id)
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "CSV must be under 5 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rdr = csv.DictReader(io.StringIO(text))
    if not rdr.fieldnames:
        raise HTTPException(400, "CSV has no header row")
    keymap = {}
    for h in rdr.fieldnames:
        k = (h or "").strip().lower()
        k = CSV_ALIASES.get(k, k.replace(" ", "_"))
        keymap[h] = k
    if not {"email", "phone", "name", "first_name"} & set(keymap.values()):
        raise HTTPException(400, "CSV needs at least one of the columns: email, phone, name")
    created = deduped = 0
    errors = []
    for i, row in enumerate(rdr, start=2):
        if i > 10001:
            errors.append({"row": i, "error": "row limit (10,000) reached"})
            break
        d = {keymap[k]: (v or "").strip() for k, v in row.items() if k in keymap}
        touch = {k: d.pop(k) for k in ("utm_source", "utm_medium", "utm_campaign", "referrer") if d.get(k)}
        status = d.pop("status", "").lower() or None
        try:
            with db.begin_nested():
                lead, c = L.capture(db, ctx.org_id, project_id, d, source="import", kind="import",
                                    touch=touch or {"utm_source": "(import)", "utm_medium": "(none)"}, actor_id=ctx.user_id, ref=f"csv:{file.filename}:{i}")
                if status in L.STATUSES and c:
                    L.set_status(db, lead, status, ctx.user_id)
            created += int(c)
            deduped += int(not c)
        except HTTPException as e:
            errors.append({"row": i, "error": e.detail})
    log(db, ctx, "lead.imported", f"project:{project_id}", {"created": created, "deduplicated": deduped, "errors": len(errors)})
    return {"created": created, "deduplicated": deduped, "errors": errors[:100]}


# ---------- HubSpot (module 43) — declared before /leads/{lid}
class HubspotSettingsIn(BaseModel):
    project_id: uuid.UUID | None = None
    field_map: dict[str, str] | None = None
    auto_sync: bool | None = None


@router.get("/leads/hubspot")
def hubspot_overview(db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    s = crm.settings(db, ctx.org_id)
    conn = connected_map(db).get("hubspot", "NOT_CONNECTED")
    logs = db.scalars(select(g.GrowthSyncLog).where(g.GrowthSyncLog.provider == "hubspot").order_by(desc(g.GrowthSyncLog.created_at)).limit(30))
    linked = db.scalar(select(func.count()).select_from(m.Lead).where(m.Lead.crm_contact_id.isnot(None)))
    pending = db.scalar(select(func.count()).select_from(g.GrowthLead).where(
        (g.GrowthLead.crm_synced_at.is_(None)) | (g.GrowthLead.updated_at > g.GrowthLead.crm_synced_at)))
    return {"connected": conn in ("CONNECTED", "RATE_LIMITED"), "status": conn,
            "message": None if conn in ("CONNECTED", "RATE_LIMITED") else "HubSpot is NOT CONNECTED — connect it in Integrations to sync contacts both ways.",
            "settings": {**to_dict(s), "field_map": s.field_map or crm.DEFAULT_MAP}, "mappable": crm.MAPPABLE,
            "default_map": crm.DEFAULT_MAP, "status_map": crm.STATUS_TO_HS, "linked_leads": linked, "pending_push": pending,
            "conflict_rule": "Last modified wins: the side changed most recently overwrites the mapped fields.",
            "logs": [to_dict(x) for x in logs]}


@router.put("/leads/hubspot/settings")
def hubspot_settings(body: HubspotSettingsIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    s = crm.settings(db, ctx.org_id)
    if body.project_id:
        project_guard(db, ctx, body.project_id)
        s.project_id = body.project_id
    if body.field_map is not None:
        s.field_map = crm.validate_map(body.field_map)
    if body.auto_sync is not None:
        s.auto_sync = body.auto_sync
        sched = db.scalar(select(m.JobSchedule).where(m.JobSchedule.kind == "growth_hubspot_sync"))
        if body.auto_sync and not sched:
            db.add(m.JobSchedule(organisation_id=ctx.org_id, kind="growth_hubspot_sync", payload={}, frequency="daily", next_run_at=utcnow()))
        elif sched:
            sched.enabled = body.auto_sync
    log(db, ctx, "hubspot.settings_updated", None, body.model_dump(mode="json", exclude_none=True))
    db.flush()
    return {**to_dict(s)}


def _nc_log(org_id, user_id, provider, direction, msg):
    with tenant_session(org_id, user_id) as db2:
        db2.add(g.GrowthSyncLog(organisation_id=org_id, provider=provider, direction=direction, status="not_connected", error=msg,
                                created_by=user_id))


@router.post("/leads/hubspot/sync")
def hubspot_sync(db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    try:
        lg = crm.sync_hubspot(db, ctx.org_id, ctx.user_id)
    except NotConnected as e:
        _nc_log(ctx.org_id, ctx.user_id, "hubspot", "two_way", str(e))
        nc(e, "hubspot")
    log(db, ctx, "hubspot.synced", None, {"status": lg.status, **lg.counts})
    return to_dict(lg)


# ---------- Calendly (module 44)
class CalendlySettingsIn(BaseModel):
    project_id: uuid.UUID | None = None
    create_leads: bool | None = None
    signing_key: str | None = Field(None, min_length=16, max_length=200)
    clear_signing_key: bool = False


class BookingLinkIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., max_length=500)
    user_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


def _tracked(bl: g.GrowthBookingLink) -> str:
    sep = "&" if "?" in bl.url else "?"
    return f"{bl.url}{sep}utm_source=calendly&utm_medium=booking_link&utm_content=gq_{str(bl.id)[:8]}"


@router.get("/leads/calendly")
def calendly_overview(request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    s = crm.cal_settings(db, ctx.org_id)
    conn = connected_map(db).get("calendly", "NOT_CONNECTED")
    links = db.scalars(select(g.GrowthBookingLink).order_by(g.GrowthBookingLink.created_at)).all()
    users = L.users_map(db)
    bookings = db.scalars(select(g.GrowthBooking).order_by(desc(g.GrowthBooking.created_at)).limit(200)).all()
    total = db.scalar(select(func.count()).select_from(g.GrowthBooking)) or 0
    matched = db.scalar(select(func.count()).select_from(g.GrowthBooking).where(g.GrowthBooking.lead_id.isnot(None))) or 0
    logs = db.scalars(select(g.GrowthSyncLog).where(g.GrowthSyncLog.provider == "calendly").order_by(desc(g.GrowthSyncLog.created_at)).limit(20))
    return {"connected": conn in ("CONNECTED", "RATE_LIMITED"), "status": conn,
            "message": None if conn in ("CONNECTED", "RATE_LIMITED") else
            "Calendly API is NOT CONNECTED — the webhook receiver works without it (add the URL in Calendly or connect to register it automatically); API pulls need a connection.",
            "webhook_url": f"{base_url(request)}/api/public/calendly/{s.webhook_token}", "signing_key_configured": bool(s.signing_ref),
            "settings": {"project_id": str(s.project_id) if s.project_id else None, "create_leads": s.create_leads,
                         "subscription_uri": s.subscription_uri, "last_pull_at": s.last_pull_at},
            "links": [{**to_dict(bl), "tracked_url": _tracked(bl), "user": users.get(bl.user_id),
                       "bookings": sum(1 for b in bookings if b.booking_link_id == bl.id)} for bl in links],
            "bookings": [to_dict(b, exclude=("tracking",)) | {"tracking": b.tracking} for b in bookings],
            "stats": {"total": total, "matched": matched, "match_rate": round(matched / total, 3) if total else None},
            "logs": [to_dict(x) for x in logs]}


@router.put("/leads/calendly/settings")
def calendly_settings(body: CalendlySettingsIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    s = crm.cal_settings(db, ctx.org_id)
    if body.project_id:
        project_guard(db, ctx, body.project_id)
        s.project_id = body.project_id
    if body.create_leads is not None:
        s.create_leads = body.create_leads
    if body.signing_key or body.clear_signing_key:
        if s.signing_ref:
            vault.remove(ctx.org_id, s.signing_ref)
            s.signing_ref = None
        if body.signing_key:
            s.signing_ref = vault.put(ctx.org_id, body.signing_key)
    log(db, ctx, "calendly.settings_updated", None, {"signing_key_set": bool(s.signing_ref)})
    return {"ok": True, "signing_key_configured": bool(s.signing_ref)}


@router.post("/leads/calendly/rotate-url")
def calendly_rotate(db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    s = crm.cal_settings(db, ctx.org_id)
    s.webhook_token = new_token(18)
    return {"ok": True}


@router.post("/leads/calendly/links")
def add_booking_link(body: BookingLinkIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    url = check_url(body.url)
    if "calendly.com/" not in url:
        raise HTTPException(400, "Use a Calendly scheduling link (https://calendly.com/...)")
    if body.project_id:
        project_guard(db, ctx, body.project_id)
    if body.user_id:
        u = db.get(m.User, body.user_id)
        if not u or u.role == "client":
            raise HTTPException(400, "user_id must be a team member")
    bl = g.GrowthBookingLink(organisation_id=ctx.org_id, name=body.name, url=url, user_id=body.user_id, project_id=body.project_id)
    db.add(bl)
    db.flush()
    return {**to_dict(bl), "tracked_url": _tracked(bl)}


@router.delete("/leads/calendly/links/{bid}")
def del_booking_link(bid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    bl = db.get(g.GrowthBookingLink, bid)
    if not bl:
        raise HTTPException(404)
    db.delete(bl)
    return {"ok": True}


@router.post("/leads/calendly/sync")
def calendly_sync(db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    try:
        lg = crm.sync_calendly(db, ctx.org_id, ctx.user_id)
    except NotConnected as e:
        _nc_log(ctx.org_id, ctx.user_id, "calendly", "pull", str(e))
        nc(e, "calendly")
    return to_dict(lg)


@router.post("/leads/calendly/register-webhook")
def calendly_register(request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    s = crm.cal_settings(db, ctx.org_id)
    try:
        r = crm.register_webhook(db, ctx.org_id, f"{base_url(request)}/api/public/calendly/{s.webhook_token}")
    except NotConnected as e:
        nc(e, "calendly")
    except crm.IntegrationError as e:
        raise HTTPException(502, str(e))
    return r


# ---------- single lead
@router.get("/leads/{lid}")
def get_lead(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    lead, x = _lead(db, ctx, lid)
    users = L.users_map(db)
    d = L.lead_dict(lead, x, users)
    d["notes"] = [{**to_dict(n), "author": users.get(n.author_id)} for n in
                  db.scalars(select(g.GrowthLeadNote).where(g.GrowthLeadNote.lead_id == lid).order_by(desc(g.GrowthLeadNote.created_at)))]
    d["activity"] = [{**to_dict(a), "actor": users.get(a.actor_id, "system")} for a in
                     db.scalars(select(g.GrowthLeadActivity).where(g.GrowthLeadActivity.lead_id == lid).order_by(desc(g.GrowthLeadActivity.created_at)).limit(200))]
    d["touchpoints"] = [to_dict(t) for t in db.scalars(select(g.GrowthTouchpoint).where(g.GrowthTouchpoint.lead_id == lid).order_by(g.GrowthTouchpoint.occurred_at))]
    d["submissions"] = [to_dict(s) for s in db.scalars(select(m.FormSubmission).where(m.FormSubmission.lead_id == lid).order_by(desc(m.FormSubmission.created_at)))]
    d["bookings"] = [to_dict(b) for b in db.scalars(select(g.GrowthBooking).where(g.GrowthBooking.lead_id == lid))]
    d["profiles"] = [to_dict(r) for r in db.scalars(select(g.GrowthQuestionnaireResponse).where(g.GrowthQuestionnaireResponse.lead_id == lid))]
    if x.possible_duplicate_of:
        o = db.get(m.Lead, x.possible_duplicate_of)
        d["duplicate_candidate"] = L.lead_dict(o, L.ext_of(db, o)) if o else None
    return d


@router.patch("/leads/{lid}")
def patch_lead(lid: uuid.UUID, body: LeadPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    lead, x = _lead(db, ctx, lid)
    data = body.model_dump(exclude_unset=True)
    changed = []
    if "email" in data and data["email"] != lead.email:
        en = L.norm_email(data["email"]) if data["email"] else None
        if data["email"] and not en:
            raise HTTPException(400, "Invalid email")
        if en and L.find_duplicate(db, lead.project_id, en, None, exclude=lead.id):
            raise HTTPException(409, "Another lead in this project already has this email — merge them instead")
        lead.email, x.email_norm = data["email"], en
        changed.append("email")
    if "phone" in data and data["phone"] != lead.phone:
        pk = L.phone_key(data["phone"])
        if pk and L.find_duplicate(db, lead.project_id, None, pk, exclude=lead.id):
            raise HTTPException(409, "Another lead in this project already has this phone number — merge them instead")
        lead.phone, x.phone_key = data["phone"], pk
        changed.append("phone")
    for k in ("name", "company", "value", "segment"):
        if k in data and data[k] != getattr(x, k):
            setattr(x, k, data[k])
            changed.append(k)
    if changed:
        L.activity(db, ctx.org_id, lead.id, "edited", f"Edited {', '.join(changed)}", {"fields": changed}, ctx.user_id)
        x.updated_at = utcnow()
    if data.get("status"):
        L.set_status(db, lead, data["status"], ctx.user_id, data.get("lost_reason"))
    if "assignee_id" in data and data["assignee_id"] != x.assignee_id:
        if ctx.role == "client":
            raise HTTPException(403, "Clients cannot assign leads")
        L.assign(db, lead, data["assignee_id"], ctx.user_id)
    log(db, ctx, "lead.updated", f"lead:{lead.id}", {k: str(v) for k, v in data.items()})
    db.flush()
    return L.lead_dict(lead, x, L.users_map(db))


@router.delete("/leads/{lid}")
def delete_lead(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    lead, x = _lead(db, ctx, lid)
    log(db, ctx, "lead.deleted", f"lead:{lead.id}", {"email": lead.email})
    for model in (g.GrowthTouchpoint, g.GrowthLeadNote, g.GrowthLeadActivity):
        for r in db.scalars(select(model).where(model.lead_id == lead.id)):
            db.delete(r)
    db.execute(m.FormSubmission.__table__.update().where(m.FormSubmission.lead_id == lead.id).values(lead_id=None))
    db.flush()
    db.delete(lead)
    return {"ok": True}


class NoteIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


@router.post("/leads/{lid}/notes")
def add_note(lid: uuid.UUID, body: NoteIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    lead, x = _lead(db, ctx, lid)
    n = g.GrowthLeadNote(organisation_id=ctx.org_id, lead_id=lead.id, author_id=ctx.user_id, body=body.body)
    db.add(n)
    L.activity(db, ctx.org_id, lead.id, "note", "Note added", {}, ctx.user_id)
    x.updated_at = utcnow()
    db.flush()
    return to_dict(n)


class TouchIn(BaseModel):
    utm_source: str | None = Field(None, max_length=200)
    utm_medium: str | None = Field(None, max_length=200)
    utm_campaign: str | None = Field(None, max_length=200)
    utm_term: str | None = Field(None, max_length=200)
    utm_content: str | None = Field(None, max_length=200)
    referrer: str | None = Field(None, max_length=2000)
    page_url: str | None = Field(None, max_length=2000)
    occurred_at: datetime | None = None


@router.post("/leads/{lid}/touchpoints")
def add_touch(lid: uuid.UUID, body: TouchIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    lead, _ = _lead(db, ctx, lid)
    tp = L.add_touchpoint(db, lead, "manual", body.model_dump(exclude={"occurred_at"}), occurred_at=body.occurred_at)
    L.activity(db, ctx.org_id, lead.id, "touchpoint", f"Touchpoint added: {tp.source} / {tp.medium}", {}, ctx.user_id)
    db.flush()
    return to_dict(tp)


class MergeIn(BaseModel):
    into_id: uuid.UUID


@router.post("/leads/{lid}/merge")
def merge_lead(lid: uuid.UUID, body: MergeIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    src, _ = _lead(db, ctx, lid)
    dst, dx = _lead(db, ctx, body.into_id)
    L.merge(db, src, dst, ctx.user_id)
    log(db, ctx, "lead.merged", f"lead:{dst.id}", {"merged": str(lid)})
    db.flush()
    return L.lead_dict(dst, dx, L.users_map(db))


@router.post("/leads/{lid}/dismiss-duplicate")
def dismiss_dup(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    lead, x = _lead(db, ctx, lid)
    x.possible_duplicate_of = None
    L.activity(db, ctx.org_id, lead.id, "duplicate_dismissed", "Marked as not a duplicate", {}, ctx.user_id)
    return {"ok": True}


# ================================================================== connectors (module 65) — before /forms/{fid}
class ConnectorIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    preset: str
    project_id: uuid.UUID
    form_id: uuid.UUID | None = None
    mapping: dict[str, str] | None = None


class ConnectorPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    mapping: dict[str, str] | None = None
    enabled: bool | None = None
    form_id: uuid.UUID | None = None


def _connector_dict(c: g.GrowthConnector, request: Request, db=None, secret: str | None = None) -> dict:
    d = to_dict(c, exclude=("secret_ref", "token"))
    d["endpoint"] = f"{base_url(request)}/api/public/hooks/{c.token}"
    d["preset_info"] = {k: v for k, v in F.PRESETS[c.preset].items() if k != "sample"}
    if secret:
        d["secret"] = secret
    if db is not None:
        d["deliveries"] = db.scalar(select(func.count()).select_from(g.GrowthConnectorDelivery).where(g.GrowthConnectorDelivery.connector_id == c.id))
        d["accepted"] = db.scalar(select(func.count()).select_from(g.GrowthConnectorDelivery).where(
            g.GrowthConnectorDelivery.connector_id == c.id, g.GrowthConnectorDelivery.status == "accepted"))
    return d


@router.get("/forms/connectors/presets")
def connector_presets(ctx: Ctx = Depends(require("view"))):
    return [{"key": k, **v} for k, v in F.PRESETS.items()]


@router.get("/forms/connectors")
def list_connectors(request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    meta_conn = connected_map(db).get("meta", "NOT_CONNECTED")
    return {"items": [_connector_dict(c, request, db) for c in db.scalars(select(g.GrowthConnector).order_by(g.GrowthConnector.created_at))],
            "meta_connected": meta_conn in ("CONNECTED", "RATE_LIMITED")}


@router.post("/forms/connectors")
def create_connector(body: ConnectorIn, request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    if body.preset not in F.PRESETS:
        raise HTTPException(400, f"preset must be one of {list(F.PRESETS)}")
    project_guard(db, ctx, body.project_id)
    if body.form_id and not db.get(m.Form, body.form_id):
        raise HTTPException(400, "form_id not found")
    mapping = F.validate_mapping(body.mapping if body.mapping is not None else F.PRESETS[body.preset]["mapping"])
    secret = new_token(32)
    c = g.GrowthConnector(organisation_id=ctx.org_id, name=body.name, preset=body.preset, token=new_token(18),
                          secret_ref=vault.put(ctx.org_id, secret), secret_hint=vault.mask(secret),
                          verify_token=new_token(12) if body.preset == "facebook_lead_ads" else None,
                          project_id=body.project_id, form_id=body.form_id, mapping=mapping, created_by=ctx.user_id)
    db.add(c)
    db.flush()
    log(db, ctx, "connector.created", f"connector:{c.id}", {"preset": body.preset})
    return _connector_dict(c, request, db, secret)


@router.patch("/forms/connectors/{cid}")
def patch_connector(cid: uuid.UUID, body: ConnectorPatch, request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    c = db.get(g.GrowthConnector, cid)
    if not c:
        raise HTTPException(404)
    if body.name:
        c.name = body.name
    if body.mapping is not None:
        c.mapping = F.validate_mapping(body.mapping)
    if body.enabled is not None:
        c.enabled = body.enabled
    if "form_id" in body.model_fields_set:
        if body.form_id and not db.get(m.Form, body.form_id):
            raise HTTPException(400, "form_id not found")
        c.form_id = body.form_id
    log(db, ctx, "connector.updated", f"connector:{c.id}")
    return _connector_dict(c, request, db)


@router.post("/forms/connectors/{cid}/rotate-secret")
def rotate_connector(cid: uuid.UUID, request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    c = db.get(g.GrowthConnector, cid)
    if not c:
        raise HTTPException(404)
    secret = new_token(32)
    vault.remove(ctx.org_id, c.secret_ref)
    c.secret_ref, c.secret_hint = vault.put(ctx.org_id, secret), vault.mask(secret)
    log(db, ctx, "connector.secret_rotated", f"connector:{c.id}")
    return _connector_dict(c, request, db, secret)


@router.delete("/forms/connectors/{cid}")
def delete_connector(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    c = db.get(g.GrowthConnector, cid)
    if not c:
        raise HTTPException(404)
    vault.remove(ctx.org_id, c.secret_ref)
    for d in db.scalars(select(g.GrowthConnectorDelivery).where(g.GrowthConnectorDelivery.connector_id == c.id)):
        db.delete(d)
    db.flush()
    db.delete(c)
    log(db, ctx, "connector.deleted", f"connector:{cid}")
    return {"ok": True}


class TestIn(BaseModel):
    payload: dict | None = None
    create_lead: bool = False


@router.post("/forms/connectors/{cid}/test")
def test_connector(cid: uuid.UUID, body: TestIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    c = db.get(g.GrowthConnector, cid)
    if not c:
        raise HTTPException(404)
    payload = body.payload if body.payload is not None else F.PRESETS[c.preset]["sample"]
    raw = json.dumps(payload).encode()
    if c.preset == "facebook_lead_ads":
        ids = F.facebook_lead_ids(payload)
        flat, data, touch = {}, {}, {}
        note = f"{len(ids)} lead ID(s) found; field data is fetched from the Meta Graph API on delivery " + \
               ("(Meta connected)." if connected_map(db).get("meta") == "CONNECTED" else "— Meta is NOT CONNECTED, so live deliveries are logged but cannot be resolved into leads.")
    else:
        flat = F.flatten(c.preset, payload)
        data, touch = F.apply_mapping(c.mapping, flat)
        note = None
    unmapped = sorted(k for k in flat if k not in set(c.mapping.values()))
    secret = vault.get(ctx.org_id, c.secret_ref)
    lead_id = None
    if body.create_lead and data:
        lead, _ = L.capture(db, ctx.org_id, c.project_id, data, source=f"webhook:{c.preset}", kind="webhook", form_id=c.form_id,
                            touch=touch, actor_id=ctx.user_id, ref=f"test:{c.id}")
        lead_id = lead.id
    db.add(g.GrowthConnectorDelivery(organisation_id=ctx.org_id, connector_id=c.id, status="test", signature_ok=None,
                                     payload=payload, mapped={"lead": data, "touch": touch}, lead_id=lead_id))
    return {"flat": flat, "lead": data, "touch": touch, "unmapped_fields": unmapped, "note": note,
            "valid": bool(set(data) & {"email", "phone", "name", "first_name"}), "lead_id": str(lead_id) if lead_id else None,
            "example_headers": F.sign_for_preset(c.preset, secret, raw), "example_body": raw.decode()}


@router.get("/forms/connectors/{cid}/deliveries")
def connector_deliveries(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    if not db.get(g.GrowthConnector, cid):
        raise HTTPException(404)
    return [to_dict(d) for d in db.scalars(select(g.GrowthConnectorDelivery).where(g.GrowthConnectorDelivery.connector_id == cid)
                                           .order_by(desc(g.GrowthConnectorDelivery.created_at)).limit(200))]


# ================================================================== forms (module 64)
class FormIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    kind: str = "contact"
    schema_: dict | None = Field(None, alias="schema")
    settings: dict | None = None


class FormPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    schema_: dict | None = Field(None, alias="schema")
    settings: dict | None = None
    status: str | None = None


def _check_settings(db, settings: dict) -> dict:
    s = F.validate_settings(settings or {})
    for uid_ in s["assignee_ids"]:
        u = db.get(m.User, L.uuid_or_400(uid_, "assignee_ids"))
        if not u or u.role == "client":
            raise HTTPException(400, "assignee_ids must be team members of your organisation")
    if s["questionnaire_id"] and not db.get(g.GrowthQuestionnaire, L.uuid_or_400(s["questionnaire_id"], "questionnaire_id")):
        raise HTTPException(400, "questionnaire_id not found")
    return s


def embed_codes(request: Request, token: str) -> dict:
    b = base_url(request)
    return {"page_url": f"{b}/f/{token}",
            "iframe": f'<iframe src="{b}/f/{token}?embed=1" style="width:100%;min-height:560px;border:0" title="Contact form" loading="lazy"></iframe>',
            "script": f'<div id="gq-form-{token[:8]}"></div>\n<script src="{b}/api/public/forms/{token}/embed.js" data-target="gq-form-{token[:8]}" async></script>',
            "endpoint": f"{b}/api/public/forms/{token}/submit"}


def _form_dict(db, f: m.Form, meta: g.GrowthFormMeta, request: Request) -> dict:
    subs = db.scalar(select(func.count()).select_from(m.FormSubmission).where(m.FormSubmission.form_id == f.id))
    spam = db.scalar(select(func.count()).select_from(g.GrowthSpamEvent).where(g.GrowthSpamEvent.form_id == f.id))
    leads = db.scalar(select(func.count(func.distinct(m.FormSubmission.lead_id))).where(m.FormSubmission.form_id == f.id))
    return {"id": str(f.id), "project_id": str(f.project_id) if f.project_id else None, "name": f.name, "schema": f.schema,
            "settings": meta.settings, "status": meta.status, "kind": meta.kind, "public_token": meta.public_token,
            "embed": embed_codes(request, meta.public_token), "submissions": subs, "spam_blocked": spam, "leads": leads,
            "created_at": meta.created_at, "updated_at": meta.updated_at}


def _form(db, ctx, fid) -> tuple[m.Form, g.GrowthFormMeta]:
    f = db.get(m.Form, fid)
    if not f:
        raise HTTPException(404, "Form not found")
    if f.project_id:
        project_guard(db, ctx, f.project_id)
    meta = db.scalar(select(g.GrowthFormMeta).where(g.GrowthFormMeta.form_id == f.id))
    if not meta:
        meta = g.GrowthFormMeta(organisation_id=f.organisation_id, form_id=f.id, public_token=new_token(16), settings=F.validate_settings({}))
        db.add(meta)
        db.flush()
    return f, meta


@router.get("/forms/templates")
def form_templates(ctx: Ctx = Depends(require("view"))):
    return {"contact": F.CONTACT_TEMPLATE, "field_types": F.FIELD_TYPES, "maps_to": F.MAPPABLE, "utm_keys": F.UTM_KEYS,
            "default_spam": F.DEFAULT_SPAM}


@router.get("/forms")
def list_forms(request: Request, project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(m.Form).where(m.Form.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        project_guard(db, ctx, project_id)
        q = q.where(m.Form.project_id == project_id)
    out = []
    for f in db.scalars(q):
        _, meta = _form(db, ctx, f.id)
        out.append(_form_dict(db, f, meta, request))
    return sorted(out, key=lambda d: d["created_at"], reverse=True)


@router.post("/forms")
def create_form(body: FormIn, request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    if body.kind not in ("contact", "custom"):
        raise HTTPException(400, "kind must be contact or custom")
    schema = F.validate_schema(body.schema_ or (F.CONTACT_TEMPLATE if body.kind == "contact" else
                                                {"fields": [{"key": "email", "label": "Email", "type": "email", "required": True}]}))
    settings = _check_settings(db, body.settings or {})
    f = m.Form(organisation_id=ctx.org_id, project_id=body.project_id, name=body.name, schema=schema)
    db.add(f)
    db.flush()
    meta = g.GrowthFormMeta(organisation_id=ctx.org_id, form_id=f.id, public_token=new_token(16), kind=body.kind, settings=settings,
                            created_by=ctx.user_id)
    db.add(meta)
    db.flush()
    f.embed_code = embed_codes(request, meta.public_token)
    log(db, ctx, "form.created", f"form:{f.id}", {"name": f.name})
    return _form_dict(db, f, meta, request)


@router.get("/forms/{fid}")
def get_form(fid: uuid.UUID, request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    f, meta = _form(db, ctx, fid)
    return _form_dict(db, f, meta, request)


@router.patch("/forms/{fid}")
def patch_form(fid: uuid.UUID, body: FormPatch, request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    f, meta = _form(db, ctx, fid)
    if body.name:
        f.name = body.name
    if body.schema_ is not None:
        f.schema = F.validate_schema(body.schema_)
    if body.settings is not None:
        meta.settings = _check_settings(db, body.settings)
    if body.status:
        if body.status not in ("active", "paused"):
            raise HTTPException(400, "status must be active or paused")
        meta.status = body.status
    meta.updated_at = utcnow()
    log(db, ctx, "form.updated", f"form:{f.id}")
    return _form_dict(db, f, meta, request)


@router.post("/forms/{fid}/rotate-token")
def rotate_form(fid: uuid.UUID, request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    f, meta = _form(db, ctx, fid)
    meta.public_token = new_token(16)
    f.embed_code = embed_codes(request, meta.public_token)
    log(db, ctx, "form.token_rotated", f"form:{f.id}")
    return _form_dict(db, f, meta, request)


@router.delete("/forms/{fid}")
def delete_form(fid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    f, meta = _form(db, ctx, fid)
    db.execute(m.Lead.__table__.update().where(m.Lead.form_id == f.id).values(form_id=None))
    for model in (m.FormSubmission, g.GrowthSpamEvent):
        for r in db.scalars(select(model).where(model.form_id == f.id)):
            db.delete(r)
    db.flush()
    db.delete(f)
    log(db, ctx, "form.deleted", f"form:{fid}")
    return {"ok": True}


@router.get("/forms/{fid}/submissions")
def form_submissions(fid: uuid.UUID, limit: int = Query(200, le=1000), db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    _form(db, ctx, fid)
    return [to_dict(s) for s in db.scalars(select(m.FormSubmission).where(m.FormSubmission.form_id == fid)
                                           .order_by(desc(m.FormSubmission.created_at)).limit(limit))]


@router.get("/forms/{fid}/spam")
def form_spam(fid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    _form(db, ctx, fid)
    rows = db.execute(select(g.GrowthSpamEvent.reason, func.count()).where(g.GrowthSpamEvent.form_id == fid).group_by(g.GrowthSpamEvent.reason)).all()
    recent = db.scalars(select(g.GrowthSpamEvent).where(g.GrowthSpamEvent.form_id == fid).order_by(desc(g.GrowthSpamEvent.created_at)).limit(50))
    return {"by_reason": dict(rows), "recent": [to_dict(e, exclude=("ip_hash",)) for e in recent]}


# ================================================================== attribution (module 42)
@router.get("/attribution/report")
def attribution_report(model: str = "linear", dimension: str = "source", project_id: uuid.UUID | None = None,
                       since: datetime | None = None, until: datetime | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    if model not in L.MODELS:
        raise HTTPException(400, f"model must be one of {L.MODELS}")
    pids = visible_project_ids(db, ctx)
    if project_id:
        project_guard(db, ctx, project_id)
        pids = [project_id]
    return L.attribution(db, pids, model, dimension, since, until)


@router.get("/attribution/compare")
def attribution_compare(dimension: str = "source", project_id: uuid.UUID | None = None, since: datetime | None = None,
                        until: datetime | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    pids = visible_project_ids(db, ctx)
    if project_id:
        project_guard(db, ctx, project_id)
        pids = [project_id]
    reps = {mo: L.attribution(db, pids, mo, dimension, since, until) for mo in L.MODELS}
    keys = sorted({r["key"] for rep in reps.values() for r in rep["rows"]})
    rows = []
    for k in keys:
        row = {"key": k}
        for mo, rep in reps.items():
            r = next((x for x in rep["rows"] if x["key"] == k), None)
            row[mo] = {"leads": r["leads"] if r else 0, "conversions": r["conversions"] if r else 0, "value": r["value"] if r else 0}
        rows.append(row)
    rows.sort(key=lambda r: -r["linear"]["value"] - r["linear"]["leads"] / 1e6)
    return {"dimension": dimension, "rows": rows, "totals": reps["linear"]["totals"]}


@router.get("/attribution/touchpoints")
def attribution_touchpoints(project_id: uuid.UUID | None = None, limit: int = Query(200, le=1000), db=Depends(get_db),
                            ctx: Ctx = Depends(require("view"))):
    q = select(g.GrowthTouchpoint, m.Lead.email, m.Lead.status).join(m.Lead, m.Lead.id == g.GrowthTouchpoint.lead_id) \
        .where(g.GrowthTouchpoint.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        q = q.where(g.GrowthTouchpoint.project_id == project_id)
    return [{**to_dict(t), "lead_email": e, "lead_status": s} for t, e, s in db.execute(q.order_by(desc(g.GrowthTouchpoint.occurred_at)).limit(limit))]


# ================================================================== onboarding / profiling (module 66)
class QuestionnaireIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    questions: list[dict] = Field(..., max_length=50)
    rules: list[dict] = Field([], max_length=200)
    segments: list[dict] = Field([], max_length=20)
    attach_signup: bool = False
    active: bool = True


QUAL_TEMPLATE = {
    "name": "Lead qualification", "description": "Who / what / source / budget / timeline",
    "questions": [
        {"key": "role", "label": "Which best describes you?", "type": "single", "category": "who", "required": True,
         "options": [{"value": "business_owner", "label": "Business owner", "points": 20}, {"value": "marketing_manager", "label": "Marketing manager", "points": 15},
                     {"value": "agency", "label": "Agency", "points": 10}, {"value": "student", "label": "Student / just browsing", "points": -20}]},
        {"key": "need", "label": "What do you need help with?", "type": "multi", "category": "what", "required": True,
         "options": [{"value": "seo", "label": "SEO", "points": 10}, {"value": "local", "label": "Local SEO", "points": 10},
                     {"value": "links", "label": "Link building", "points": 10}, {"value": "content", "label": "Content", "points": 5}]},
        {"key": "heard", "label": "How did you hear about us?", "type": "single", "category": "source",
         "options": [{"value": "search", "label": "Search engine"}, {"value": "referral", "label": "Referral", "points": 10},
                     {"value": "social", "label": "Social media"}, {"value": "ads", "label": "Ads"}]},
        {"key": "budget", "label": "Monthly budget (USD)", "type": "number", "category": "budget", "required": True},
        {"key": "timeline", "label": "When do you want to start?", "type": "single", "category": "timeline", "required": True,
         "options": [{"value": "now", "label": "Immediately", "points": 25}, {"value": "quarter", "label": "This quarter", "points": 10},
                     {"value": "later", "label": "Later / not sure", "points": 0}]}],
    "rules": [{"question": "budget", "op": "gte", "value": 2000, "points": 30}, {"question": "budget", "op": "gte", "value": 500, "points": 10},
              {"question": "budget", "op": "lte", "value": 200, "points": -15}],
    "segments": [{"name": "Hot", "min_score": 70}, {"name": "Warm", "min_score": 35}, {"name": "Cold", "min_score": -1000}],
}


@router.get("/onboarding/template")
def onboarding_template(ctx: Ctx = Depends(require("view"))):
    return {"template": QUAL_TEMPLATE, "types": L.Q_TYPES, "categories": L.Q_CATEGORIES, "ops": L.RULE_OPS}


@router.get("/onboarding/questionnaires")
def list_q(db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    out = []
    for q in db.scalars(select(g.GrowthQuestionnaire).order_by(g.GrowthQuestionnaire.created_at)):
        n = db.scalar(select(func.count()).select_from(g.GrowthQuestionnaireResponse).where(g.GrowthQuestionnaireResponse.questionnaire_id == q.id))
        segs = dict(db.execute(select(g.GrowthQuestionnaireResponse.segment, func.count()).where(
            g.GrowthQuestionnaireResponse.questionnaire_id == q.id).group_by(g.GrowthQuestionnaireResponse.segment)).all())
        forms = [str(mf.form_id) for mf in db.scalars(select(g.GrowthFormMeta)) if (mf.settings or {}).get("questionnaire_id") == str(q.id)]
        out.append({**to_dict(q), "responses": n, "segments_count": {str(k): v for k, v in segs.items()}, "attached_forms": forms})
    return out


def _save_q(q: g.GrowthQuestionnaire, body: QuestionnaireIn, db, ctx):
    L.validate_questionnaire(body.questions, body.rules, body.segments)
    for k in ("name", "description", "questions", "rules", "segments", "attach_signup", "active"):
        setattr(q, k, getattr(body, k))
    q.updated_at = utcnow()
    if body.attach_signup:
        for other in db.scalars(select(g.GrowthQuestionnaire).where(g.GrowthQuestionnaire.attach_signup.is_(True))):
            if other.id != q.id:
                other.attach_signup = False


@router.post("/onboarding/questionnaires")
def create_q(body: QuestionnaireIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    q = g.GrowthQuestionnaire(organisation_id=ctx.org_id, name=body.name)
    _save_q(q, body, db, ctx)
    db.add(q)
    db.flush()
    log(db, ctx, "questionnaire.created", f"questionnaire:{q.id}")
    return to_dict(q)


@router.put("/onboarding/questionnaires/{qid}")
def update_q(qid: uuid.UUID, body: QuestionnaireIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    q = db.get(g.GrowthQuestionnaire, qid)
    if not q:
        raise HTTPException(404)
    _save_q(q, body, db, ctx)
    return to_dict(q)


@router.delete("/onboarding/questionnaires/{qid}")
def delete_q(qid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    q = db.get(g.GrowthQuestionnaire, qid)
    if not q:
        raise HTTPException(404)
    for r in db.scalars(select(g.GrowthQuestionnaireResponse).where(g.GrowthQuestionnaireResponse.questionnaire_id == qid)):
        db.delete(r)
    db.flush()
    db.delete(q)
    return {"ok": True}


class AnswersIn(BaseModel):
    answers: dict
    lead_id: uuid.UUID | None = None


@router.post("/onboarding/questionnaires/{qid}/score")
def score_q(qid: uuid.UUID, body: AnswersIn, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = db.get(g.GrowthQuestionnaire, qid)
    if not q:
        raise HTTPException(404)
    return L.score_answers(q, body.answers)


@router.post("/onboarding/questionnaires/{qid}/responses")
def respond_q(qid: uuid.UUID, body: AnswersIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    q = db.get(g.GrowthQuestionnaire, qid)
    if not q:
        raise HTTPException(404)
    lead = _lead(db, ctx, body.lead_id)[0] if body.lead_id else None
    r = L.apply_profile(db, q, body.answers, lead, ctx.user_id)
    return to_dict(r)


@router.get("/onboarding/responses")
def list_responses(questionnaire_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    q = select(g.GrowthQuestionnaireResponse).order_by(desc(g.GrowthQuestionnaireResponse.created_at)).limit(300)
    if questionnaire_id:
        q = q.where(g.GrowthQuestionnaireResponse.questionnaire_id == questionnaire_id)
    users = L.users_map(db)
    out = []
    for r in db.scalars(q):
        d = to_dict(r)
        d["user"] = users.get(r.user_id)
        if r.lead_id:
            ld = db.get(m.Lead, r.lead_id)
            d["lead_email"] = ld.email if ld else None
        out.append(d)
    return out


@router.get("/onboarding/signup")
def signup_q(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = db.scalar(select(g.GrowthQuestionnaire).where(g.GrowthQuestionnaire.attach_signup.is_(True), g.GrowthQuestionnaire.active.is_(True)))
    if not q:
        return {"questionnaire": None, "response": None}
    r = db.scalar(select(g.GrowthQuestionnaireResponse).where(g.GrowthQuestionnaireResponse.questionnaire_id == q.id,
                                                              g.GrowthQuestionnaireResponse.user_id == ctx.user_id)
                  .order_by(desc(g.GrowthQuestionnaireResponse.created_at)))
    return {"questionnaire": {k: v for k, v in to_dict(q).items() if k not in ("rules",)},
            "response": to_dict(r) if r else None}


@router.post("/onboarding/signup")
def signup_submit(body: AnswersIn, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = db.scalar(select(g.GrowthQuestionnaire).where(g.GrowthQuestionnaire.attach_signup.is_(True), g.GrowthQuestionnaire.active.is_(True)))
    if not q:
        raise HTTPException(404, "No signup questionnaire is active")
    r = L.apply_profile(db, q, body.answers, None, ctx.user_id)
    return {"score": r.score, "segment": r.segment}
