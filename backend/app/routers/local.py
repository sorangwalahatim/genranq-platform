"""Local SEO API (Roadmap Phase 3): GBP audit (15), category finder (16), review audit (17), post audit (18),
local scan (19), geo-grid rank tracking (20), competitor research (21), website ↔ GBP audit (22),
GMB posts (31) and GMB automation (35).

Data comes from Google Business Profile / Google Maps when connected; without them everything works on
manually entered and CSV-imported data, and external actions report NOT CONNECTED instead of pretending."""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .. import models as m
from .. import models_local as ml
from ..deps import Ctx, get_db, log, project_guard, require
from ..services import entitlements as ent
from ..services import integrations as ih
from ..services import local_core as L
from ..services import local_svc as S
from ..services.jobs import FREQ, enqueue
from ..util import to_dict

router = APIRouter(tags=["local"])
MAX_UPLOAD = 10 * 1024 * 1024


def _f(v):
    return float(v) if v is not None else None


# ================================================================== status / overview
@router.get("/local/status")
def status(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return S.connection_status(db)


@router.get("/local/overview")
def overview(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    rows = S.project_listings(db, project_id)
    own = [loc for loc, lst in rows if lst.kind == "own"]
    ids = [loc.id for loc in own]
    reviews = db.scalar(select(func.count()).select_from(m.GbpReview).where(m.GbpReview.location_id.in_(ids))) if ids else 0
    pending = db.scalar(select(func.count()).select_from(m.GbpPost).where(m.GbpPost.location_id.in_(ids), m.GbpPost.status == "review")) if ids else 0
    scans = db.scalar(select(func.count()).select_from(ml.LocalGridScanExt).where(ml.LocalGridScanExt.project_id == project_id))
    last = {}
    for kind in ml.RUN_KINDS:
        r = db.scalar(select(ml.LocalRun).where(ml.LocalRun.project_id == project_id, ml.LocalRun.kind == kind)
                      .order_by(ml.LocalRun.created_at.desc()).limit(1))
        last[kind] = {"id": str(r.id), "score": _f(r.score), "created_at": r.created_at} if r else None
    return {"locations": len(own), "competitors": len(rows) - len(own), "reviews": reviews or 0, "posts_in_review": pending or 0,
            "grid_scans": scans or 0, "last_runs": last, "connections": S.connection_status(db)}


# ================================================================== locations
class LocationIn(BaseModel):
    project_id: uuid.UUID | None = None
    kind: str = "own"
    name: str | None = Field(None, max_length=300)
    place_id: str | None = Field(None, max_length=300)
    cid: str | None = Field(None, max_length=100)
    nap: dict | None = None
    primary_category: str | None = Field(None, max_length=200)
    secondary_categories: list[str] | None = Field(None, max_length=20)
    hours: dict | list | str | None = None
    description: str | None = Field(None, max_length=5000)
    photos_count: int | None = Field(None, ge=0, le=100000)
    has_logo: bool | None = None
    services: list[str] | None = Field(None, max_length=100)
    products: list[str] | None = Field(None, max_length=100)
    attributes: list[str] | None = Field(None, max_length=100)
    qa_questions: int | None = Field(None, ge=0, le=10000)
    qa_answered: int | None = Field(None, ge=0, le=10000)
    social_links: list[str] | None = Field(None, max_length=30)
    citations: list[dict] | None = Field(None, max_length=100)
    keywords: list[str] | None = Field(None, max_length=100)
    rating: float | None = Field(None, ge=0, le=5)
    review_count: int | None = Field(None, ge=0, le=10_000_000)
    posts_per_month: float | None = Field(None, ge=0, le=1000)
    last_post_at: datetime | None = None
    verified: bool | None = None


@router.get("/local/locations")
def list_locations(project_id: uuid.UUID, kind: str | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    if kind and kind not in ml.LISTING_KINDS:
        raise HTTPException(400, "kind must be own or competitor")
    return [S.listing_dict(db, loc, lst) for loc, lst in S.project_listings(db, project_id, kind)]


@router.post("/local/locations")
def create_location(body: LocationIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    if not body.project_id:
        raise HTTPException(400, "project_id is required")
    project_guard(db, ctx, body.project_id)
    if not (body.name or "").strip():
        raise HTTPException(400, "name is required")
    try:
        loc, created = S.upsert_location(db, ctx.org_id, body.project_id, body.kind, body.model_dump(exclude_none=True, exclude={"project_id", "kind"}),
                                         "manual")
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "local.location_saved", f"gbp_location:{loc.id}", {"kind": body.kind, "created": created})
    return S.listing_dict(db, loc)


@router.get("/local/locations/{lid}")
def get_location(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    loc, lst = S.get_location(db, ctx, lid)
    return S.listing_dict(db, loc, lst)


@router.patch("/local/locations/{lid}")
def update_location(lid: uuid.UUID, body: LocationIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    loc, lst = S.get_location(db, ctx, lid)
    data = body.model_dump(exclude_unset=True, exclude={"project_id", "kind"})
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(400, "name cannot be empty")
    try:
        S.apply_fields(db, loc, lst, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.flush()
    log(db, ctx, "local.location_updated", f"gbp_location:{loc.id}", {"fields": sorted(data)})
    return S.listing_dict(db, loc, lst)


@router.delete("/local/locations/{lid}")
def delete_location(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    loc, lst = S.get_location(db, ctx, lid)
    scans = db.scalars(select(ml.LocalGridScanExt.scan_id).where(ml.LocalGridScanExt.location_id == lid)).all()
    for sid in scans:
        db.execute(delete(m.GridScan).where(m.GridScan.id == sid))
    db.execute(delete(ml.LocalRun).where(ml.LocalRun.location_id == lid))
    db.delete(loc)
    log(db, ctx, "local.location_deleted", f"gbp_location:{lid}", {"name": loc.name})
    return {"ok": True}


@router.post("/local/projects/{pid}/import/{kind}")
async def import_csv(pid: uuid.UUID, kind: str, file: UploadFile = File(...), location_id: uuid.UUID | None = Form(None),
                     db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    """kind: locations | competitors | reviews | posts (CSV)."""
    project_guard(db, ctx, pid)
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (10 MB max)")
    default_loc = None
    if location_id:
        default_loc, _ = S.get_location(db, ctx, location_id)
        if default_loc.project_id != pid:
            raise HTTPException(400, "location belongs to another project")
    try:
        if kind in ("locations", "competitors"):
            res = S.import_locations(db, ctx.org_id, pid, content, "own" if kind == "locations" else "competitor")
        elif kind == "reviews":
            res = S.import_reviews(db, pid, content, default_loc)
        elif kind == "posts":
            res = S.import_posts(db, pid, content, default_loc)
        else:
            raise HTTPException(400, "kind must be locations, competitors, reviews or posts")
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "local.import", f"project:{pid}", {"kind": kind, "rows": res.get("rows"), "errors": len(res.get("errors") or [])})
    return res


class SyncIn(BaseModel):
    resources: list[str] = Field(default_factory=list, max_length=50)


@router.post("/local/projects/{pid}/gbp-sync")
def gbp_sync(pid: uuid.UUID, body: SyncIn | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, pid)
    if not ih.is_connected(db, "gbp"):
        raise S.not_connected("gbp", "Google Business Profile is NOT CONNECTED — connect it to sync locations, reviews and posts, "
                                     "or import them by CSV.")
    j = enqueue(db, ctx.org_id, "local_gbp_sync", {"project_id": str(pid), "resources": (body.resources if body else [])}, created_by=ctx.user_id)
    log(db, ctx, "local.gbp_sync_queued", f"project:{pid}")
    return {"job_id": str(j.id)}


# ================================================================== runs
def _save_run(db, ctx, project_id, location_id, kind, score, params, result) -> ml.LocalRun:
    r = ml.LocalRun(organisation_id=ctx.org_id, project_id=project_id, location_id=location_id, kind=kind, score=score,
                    params=json.loads(json.dumps(params, default=str)), result=json.loads(json.dumps(result, default=str)), created_by=ctx.user_id)
    db.add(r)
    db.flush()
    return r


def _run_out(r: ml.LocalRun, full=True) -> dict:
    d = {"id": str(r.id), "project_id": str(r.project_id), "location_id": str(r.location_id) if r.location_id else None, "kind": r.kind,
         "score": _f(r.score), "params": r.params, "created_at": r.created_at}
    if full:
        d["result"] = r.result
    return d


@router.get("/local/runs")
def list_runs(project_id: uuid.UUID, kind: str | None = None, location_id: uuid.UUID | None = None, limit: int = Query(50, le=200),
              db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    q = select(ml.LocalRun).where(ml.LocalRun.project_id == project_id).order_by(ml.LocalRun.created_at.desc()).limit(limit)
    if kind:
        q = q.where(ml.LocalRun.kind == kind)
    if location_id:
        q = q.where(ml.LocalRun.location_id == location_id)
    return [_run_out(r, full=False) for r in db.scalars(q)]


@router.get("/local/runs/{rid}")
def get_run(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    r = db.get(ml.LocalRun, rid)
    if not r:
        raise HTTPException(404, "Run not found")
    project_guard(db, ctx, r.project_id)
    return _run_out(r)


def _latest_run(db, location_id, kind):
    return db.scalar(select(ml.LocalRun).where(ml.LocalRun.location_id == location_id, ml.LocalRun.kind == kind)
                     .order_by(ml.LocalRun.created_at.desc()).limit(1))


# ================================================================== 15 GBP audit
class AuditIn(BaseModel):
    check_maps: bool = True


def _post_rows(db, location_id) -> list[dict]:
    q = select(m.GbpPost, ml.LocalPostExt).outerjoin(ml.LocalPostExt, ml.LocalPostExt.post_id == m.GbpPost.id) \
        .where(m.GbpPost.location_id == location_id)
    return [{"post_type": p.post_type, "body": p.body, "status": p.status, "posted_at": p.posted_at,
             "title": x.title if x else None, "cta_type": x.cta_type if x else None, "image_ref": x.image_ref if x else None}
            for p, x in db.execute(q).all()]


def _same_address(db, ctx, loc, lst, check_maps: bool) -> tuple[list[dict], bool]:
    key = L.address_key(loc.nap or {})
    out = []
    if not key:
        return out, False
    for other, olst in S.project_listings(db, loc.project_id):
        if other.id != loc.id and L.address_key(other.nap or {}) == key:
            out.append({"name": other.name, "kind": olst.kind, "source": "project listings", "id": str(other.id)})
    maps_checked = False
    if check_maps and ih.is_connected(db, "google_maps"):
        from ..services import local_google as G
        try:
            res = G.places_text_search(db, ctx.org_id, L.address_string(loc.nap or {}))
            maps_checked = True
            for r in res:
                if (loc.place_id and r["place_id"] == loc.place_id) or L.norm_name(r["name"]) == L.norm_name(loc.name):
                    continue
                if L.compare_address(L.address_string(loc.nap or {}), r.get("address")) in ("match", "partial") and \
                        L.address_key({"address": (r.get("address") or "").split(",")[0], "postcode": (loc.nap or {}).get("postcode")}) == key:
                    out.append({"name": r["name"], "kind": "google_maps", "source": "Google Maps", "place_id": r["place_id"]})
        except ih.IntegrationError:
            maps_checked = False
    return out, maps_checked


@router.post("/local/locations/{lid}/gbp-audit")
def gbp_audit(lid: uuid.UUID, body: AuditIn | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    loc, lst = S.get_location(db, ctx, lid, "own")
    prof = S.listing_dict(db, loc, lst)
    revs = S.review_rows(db, loc.id)
    rs = L.review_stats(revs, totals=S.effective_totals(lst, revs))
    pa = L.post_audit(_post_rows(db, loc.id))
    if pa["days_since_last"] is None and lst.last_post_at:
        pa["days_since_last"] = (S.utcnow() - lst.last_post_at).days
    same, maps_checked = _same_address(db, ctx, loc, lst, body.check_maps if body else True)
    wr = _latest_run(db, loc.id, "website_gbp")
    sources = [dict(c) for c in (lst.citations or [])]
    if wr:
        sn = (wr.result or {}).get("site_nap") or {}
        sources.insert(0, {"source": "website", **{k: sn.get(k) for k in ("name", "address", "phone")}})
    res = L.gbp_audit(prof, {"review_stats": rs, "post_audit": pa, "same_address": same, "nap_sources": sources,
                             "website_run": {**(wr.result or {}), "score": _f(wr.score)} if wr else None})
    res["maps_checked"] = maps_checked
    res["inputs"] = {"reviews": rs["total_count"], "rating": rs["avg_rating"], "response_rate": rs["response_rate"],
                     "days_since_last_post": pa["days_since_last"], "website_audit_run": str(wr.id) if wr else None}
    run = _save_run(db, ctx, loc.project_id, loc.id, "gbp_audit", res["score"], {"check_maps": maps_checked}, res)
    log(db, ctx, "local.gbp_audit", f"gbp_location:{loc.id}", {"score": res["score"]})
    return _run_out(run)


# ================================================================== 16 categories
@router.get("/local/categories/catalogue")
def catalogue(q: str | None = Query(None, max_length=100), limit: int = Query(50, le=500), ctx: Ctx = Depends(require("view"))):
    items = L.catalogue()
    if q:
        ql = q.lower()
        items = [c for c in items if ql in c["name"].lower() or any(ql in k for k in c["keywords"])]
        items.sort(key=lambda c: (not c["name"].lower().startswith(ql), c["name"]))
    return {"total": len(L.catalogue()), "items": items[:limit]}


class CategoryIn(BaseModel):
    terms: list[str] = Field(default_factory=list, max_length=100)
    top_competitors: int = Field(10, ge=1, le=50)


@router.post("/local/locations/{lid}/categories")
def category_finder(lid: uuid.UUID, body: CategoryIn | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    body = body or CategoryIn()
    loc, lst = S.get_location(db, ctx, lid, "own")
    texts = [t for t in (body.terms or []) if t and len(t) <= 200]
    texts += list(lst.services or []) + list(lst.products or []) + list(lst.keywords or [])
    if lst.description:
        texts.append(lst.description[:500])
    if not texts:
        raise HTTPException(400, "Add services, keywords or terms describing the business first")
    own = S.listing_dict(db, loc, lst)
    comps = []
    for c, cl in S.project_listings(db, loc.project_id, "competitor"):
        d = S.listing_dict(db, c, cl, with_counts=False)
        d["_score"] = L.local_score(d, (lst.keywords or [None])[0])["score"]
        comps.append(d)
    comps.sort(key=lambda d: -d["_score"])
    comps = comps[: body.top_competitors]
    comp_cats = [[x for x in [d["primary_category"]] + d["secondary_categories"] if x] for d in comps]
    comp_cats = [c for c in comp_cats if c]
    res = L.match_categories(texts, comp_cats, [x for x in [own["primary_category"]] + own["secondary_categories"] if x])
    res["current"] = {"primary": own["primary_category"], "secondary": own["secondary_categories"]}
    res["competitors_used"] = [{"name": d["name"], "primary": d["primary_category"], "secondary": d["secondary_categories"]} for d in comps]
    run = _save_run(db, ctx, loc.project_id, loc.id, "categories", None, {"terms": body.terms}, res)
    log(db, ctx, "local.category_finder", f"gbp_location:{loc.id}")
    return _run_out(run)


class ApplyCats(BaseModel):
    primary: str | None = Field(None, max_length=200)
    secondary: list[str] = Field(default_factory=list, max_length=9)


@router.post("/local/locations/{lid}/categories/apply")
def apply_categories(lid: uuid.UUID, body: ApplyCats, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    loc, lst = S.get_location(db, ctx, lid, "own")
    if body.primary:
        lst.primary_category = body.primary.strip()
    lst.secondary_categories = [s.strip() for s in body.secondary if s.strip() and s.strip() != lst.primary_category][:9]
    log(db, ctx, "local.categories_applied", f"gbp_location:{loc.id}", body.model_dump())
    return S.listing_dict(db, loc, lst)


# ================================================================== 17 reviews
class ReviewIn(BaseModel):
    rating: int | None = Field(None, ge=1, le=5)
    body: str | None = Field(None, max_length=10000)
    reviewed_at: datetime | None = None
    author: str | None = Field(None, max_length=200)
    reply: str | None = Field(None, max_length=4096)
    replied_at: datetime | None = None


@router.get("/local/locations/{lid}/reviews")
def list_reviews(lid: uuid.UUID, status: str | None = None, sentiment: str | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    loc, _ = S.get_location(db, ctx, lid)
    for x in db.scalars(select(ml.LocalReviewExt).join(m.GbpReview, m.GbpReview.id == ml.LocalReviewExt.review_id)
                        .where(m.GbpReview.location_id == lid, ml.LocalReviewExt.reply_status == "review")):
        S.sync_from_approval(db, x)
    out = S.review_rows(db, loc.id)
    for r in out:
        r["sentiment_label"] = L.sentiment(r["body"] or "")["label"]
    if status:
        out = [r for r in out if r["reply_status"] == status]
    if sentiment:
        out = [r for r in out if r["sentiment_label"] == sentiment]
    return out


@router.post("/local/locations/{lid}/reviews")
def add_review(lid: uuid.UUID, body: ReviewIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    loc, _ = S.get_location(db, ctx, lid)
    if not body.rating and not body.body:
        raise HTTPException(400, "rating or review text is required")
    try:
        rv, _c = S.add_review(db, loc, body.model_dump(), "manual")
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "local.review_added", f"gbp_review:{rv.id}")
    return next(r for r in S.review_rows(db, loc.id) if r["id"] == str(rv.id))


def _review(db, ctx, rid) -> tuple[m.GbpReview, ml.LocalReviewExt, m.GbpLocation]:
    rv = db.get(m.GbpReview, rid)
    if not rv:
        raise HTTPException(404, "Review not found")
    loc, _ = S.get_location(db, ctx, rv.location_id)
    x = db.scalar(select(ml.LocalReviewExt).where(ml.LocalReviewExt.review_id == rv.id))
    if not x:
        x = ml.LocalReviewExt(organisation_id=rv.organisation_id, review_id=rv.id, reply_status="published" if rv.reply else "none")
        db.add(x)
        db.flush()
    S.sync_from_approval(db, x)
    return rv, x, loc


def _review_out(db, rv, x, loc):
    return next(r for r in S.review_rows(db, loc.id) if r["id"] == str(rv.id))


@router.delete("/local/reviews/{rid}")
def delete_review(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    rv, x, loc = _review(db, ctx, rid)
    db.delete(rv)
    log(db, ctx, "local.review_deleted", f"gbp_review:{rid}")
    return {"ok": True}


@router.get("/local/locations/{lid}/review-audit")
def review_audit(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    loc, lst = S.get_location(db, ctx, lid, "own")
    revs = S.review_rows(db, loc.id)
    st = L.review_stats(revs, totals=S.effective_totals(lst, revs))
    comp = [{"name": loc.name, "own": True, "rating": st["avg_rating"], "review_count": st["total_count"]}]
    for c, cl in S.project_listings(db, loc.project_id, "competitor"):
        comp.append({"name": c.name, "own": False, "rating": _f(cl.rating), "review_count": cl.review_count})
    comp.sort(key=lambda r: -(r["review_count"] or 0))
    crs = [c for c in comp if not c["own"] and c["review_count"] is not None]
    return {"stats": st, "recommendations": L.review_recommendations(st), "competitors": comp,
            "competitor_median_reviews": sorted(c["review_count"] for c in crs)[len(crs) // 2] if crs else None,
            "connections": S.connection_status(db)}


class DraftIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)


class ToneIn(BaseModel):
    tone: str = Field("warm and professional", max_length=100)
    instructions: str | None = Field(None, max_length=1000)


@router.post("/local/reviews/{rid}/ai-draft")
def ai_draft_reply(rid: uuid.UUID, body: ToneIn | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    from ..services import agents
    rv, x, loc = _review(db, ctx, rid)
    if x.reply_status in ("approved", "publishing", "published"):
        raise HTTPException(409, f"Reply is already {x.reply_status}")
    body = body or ToneIn()
    prompt = (f"You are the owner of the local business \"{loc.name}\" ({(loc.nap or {}).get('city') or ''}). Write a reply to this "
              f"Google review. Tone: {body.tone}. Keep it under 120 words, personal and specific to what the reviewer said; thank them; "
              f"for criticism apologise without admitting liability and invite them to contact the business directly"
              f"{' on ' + (loc.nap or {}).get('phone') if (loc.nap or {}).get('phone') else ''}. Do not invent facts, offers or names. "
              f"{body.instructions or ''}\nOutput only the reply text.\n\nRating: {rv.rating or 'n/a'} stars\nReview: {rv.body or '(no text)'}")
    try:
        text, meta = agents.llm(db, ctx.org_id, prompt, max_tokens=400)
    except agents.NoAI:
        raise S.not_connected("ai", "AI drafting is NOT CONNECTED — no AI provider (Claude, OpenAI, Gemini or Perplexity) is connected. "
                                    "Write the reply manually, or connect a provider in Integrations.")
    x.reply_draft, x.reply_status, x.reply_source, x.publish_error = text.strip()[:4096], "draft", "ai", None
    log(db, ctx, "local.reply_ai_drafted", f"gbp_review:{rid}", {"model": meta.get("model")})
    return _review_out(db, rv, x, loc)


@router.put("/local/reviews/{rid}/draft")
def save_reply_draft(rid: uuid.UUID, body: DraftIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    rv, x, loc = _review(db, ctx, rid)
    if x.reply_status in ("publishing", "published"):
        raise HTTPException(409, "This reply is already published")
    changed = x.reply_draft != body.text.strip()
    x.reply_draft = body.text.strip()
    if changed or x.reply_status in ("none", "rejected"):
        if x.reply_status in ("review", "approved") and x.approval_id:
            a = db.get(m.Approval, x.approval_id)
            if a and a.status == "pending":
                a.status = "rejected"
                a.notes = "superseded by an edited draft"
        x.reply_status, x.reply_source = "draft", x.reply_source if not changed else "manual"
    return _review_out(db, rv, x, loc)


@router.post("/local/reviews/{rid}/submit")
def submit_reply(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    rv, x, loc = _review(db, ctx, rid)
    if x.reply_status not in ("draft", "rejected") or not (x.reply_draft or "").strip():
        raise HTTPException(409, "Only a saved draft reply can be submitted for approval")
    a = S.request_approval(db, ctx, "gbp_review_reply", rv.id, f"Review reply for {loc.name} ({rv.rating or '?'}★)")
    x.approval_id, x.reply_status = a.id, "review"
    log(db, ctx, "local.reply_submitted", f"gbp_review:{rid}")
    return _review_out(db, rv, x, loc)


class DecisionIn(BaseModel):
    notes: str | None = Field(None, max_length=2000)


@router.post("/local/reviews/{rid}/approve")
def approve_reply(rid: uuid.UUID, body: DecisionIn | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("approve"))):
    rv, x, loc = _review(db, ctx, rid)
    if x.reply_status != "review":
        raise HTTPException(409, "Reply is not awaiting approval")
    S.decide_approval(db, ctx, x.approval_id, "approve", body.notes if body else None)
    x.reply_status, x.approved_by = "approved", ctx.user_id
    log(db, ctx, "local.reply_approved", f"gbp_review:{rid}")
    return _review_out(db, rv, x, loc)


@router.post("/local/reviews/{rid}/reject")
def reject_reply(rid: uuid.UUID, body: DecisionIn, db=Depends(get_db), ctx: Ctx = Depends(require("approve"))):
    rv, x, loc = _review(db, ctx, rid)
    if x.reply_status != "review":
        raise HTTPException(409, "Reply is not awaiting approval")
    if not body.notes:
        raise HTTPException(400, "Rejection requires notes")
    S.decide_approval(db, ctx, x.approval_id, "reject", body.notes)
    x.reply_status = "rejected"
    log(db, ctx, "local.reply_rejected", f"gbp_review:{rid}", {"notes": body.notes})
    return _review_out(db, rv, x, loc)


@router.post("/local/reviews/{rid}/publish")
def publish_reply(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    rv, x, loc = _review(db, ctx, rid)
    if x.reply_status != "approved":
        raise HTTPException(409, "Only an approved reply can be published (submit it for approval first)")
    lst = S.ensure_listing(db, loc)
    if not ih.is_connected(db, "gbp") or not x.external_id or not lst.gbp_resource:
        why = "Google Business Profile is NOT CONNECTED" if not ih.is_connected(db, "gbp") else \
            "this review was not synced from Google Business Profile"
        return {"published": False, "connected": ih.is_connected(db, "gbp"), "status": x.reply_status,
                "message": f"NOT CONNECTED — publish manually: {why}. Copy the approved reply, post it on Google, then mark it as published.",
                "text": x.reply_draft, "link": "/integrations/gbp"}
    x.reply_status = "publishing"
    j = enqueue(db, ctx.org_id, "local_gbp_publish", {"kind": "reply", "id": str(rv.id)}, created_by=ctx.user_id)
    log(db, ctx, "local.reply_publish_queued", f"gbp_review:{rid}")
    return {"published": False, "queued": True, "connected": True, "status": "publishing", "job_id": str(j.id)}


@router.post("/local/reviews/{rid}/mark-published")
def mark_reply_published(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    rv, x, loc = _review(db, ctx, rid)
    if x.reply_status != "approved":
        raise HTTPException(409, "Only an approved reply can be marked as published")
    rv.reply, x.replied_at, x.reply_status, x.publish_error = x.reply_draft, S.utcnow(), "published", None
    log(db, ctx, "local.reply_marked_published", f"gbp_review:{rid}")
    return _review_out(db, rv, x, loc)


# ================================================================== 18 post audit
@router.get("/local/locations/{lid}/post-audit")
def post_audit(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    loc, lst = S.get_location(db, ctx, lid, "own")
    res = L.post_audit(_post_rows(db, loc.id))
    comps = [{"name": c.name, "posts_per_month": _f(cl.posts_per_month), "last_post_at": cl.last_post_at}
             for c, cl in S.project_listings(db, loc.project_id, "competitor") if cl.posts_per_month is not None or cl.last_post_at]
    res["own_posts_per_month"] = round(res["last_90_days"] / 3, 2)
    res["competitors"] = comps
    return res


# ================================================================== 31 GMB posts
class PostIn(BaseModel):
    location_id: uuid.UUID | None = None
    post_type: str = "update"
    title: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=L.GBP_POST_MAX)
    cta_type: str | None = Field(None, max_length=20)
    cta_url: str | None = Field(None, max_length=1000)
    start_at: datetime | None = None
    end_at: datetime | None = None
    offer_code: str | None = Field(None, max_length=100)
    image_ref: str | None = Field(None, max_length=1000)


def _post(db, ctx, pid_) -> tuple[m.GbpPost, ml.LocalPostExt, m.GbpLocation]:
    p = db.get(m.GbpPost, pid_)
    if not p:
        raise HTTPException(404, "Post not found")
    loc, _ = S.get_location(db, ctx, p.location_id)
    x = db.scalar(select(ml.LocalPostExt).where(ml.LocalPostExt.post_id == p.id))
    if not x:
        x = ml.LocalPostExt(organisation_id=p.organisation_id, post_id=p.id, project_id=loc.project_id)
        db.add(x)
        db.flush()
    S.sync_from_approval(db, x)
    return p, x, loc


def _post_out(p, x, loc) -> dict:
    return {"id": str(p.id), "location_id": str(p.location_id), "location_name": loc.name, "project_id": str(loc.project_id),
            "post_type": p.post_type, "body": p.body, "status": p.status, "posted_at": p.posted_at,
            "title": x.title, "cta_type": x.cta_type, "cta_url": x.cta_url, "start_at": x.start_at, "end_at": x.end_at,
            "offer_code": x.offer_code, "image_ref": x.image_ref, "source": x.source, "publish_error": x.publish_error,
            "automation_id": str(x.automation_id) if x.automation_id else None, "approval_id": str(x.approval_id) if x.approval_id else None,
            "external_name": x.external_name, "created_at": x.created_at, "updated_at": x.updated_at, "length": len(p.body or "")}


def _validate_image(ref):
    if ref and not (ref.startswith("https://") or re.match(r"^[0-9a-f-]{36}$", ref)):
        raise HTTPException(400, "image_ref must be an https URL or a stored file id")


@router.get("/local/posts")
def list_posts(project_id: uuid.UUID, location_id: uuid.UUID | None = None, status: str | None = None, limit: int = Query(200, le=1000),
               db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    q = select(m.GbpPost, ml.LocalPostExt, m.GbpLocation).join(m.GbpLocation, m.GbpLocation.id == m.GbpPost.location_id) \
        .outerjoin(ml.LocalPostExt, ml.LocalPostExt.post_id == m.GbpPost.id).where(m.GbpLocation.project_id == project_id) \
        .order_by(func.coalesce(m.GbpPost.posted_at, ml.LocalPostExt.created_at).desc().nullslast()).limit(limit)
    if location_id:
        q = q.where(m.GbpPost.location_id == location_id)
    if status:
        if status not in ml.POST_STATES:
            raise HTTPException(400, f"status must be one of {ml.POST_STATES}")
        q = q.where(m.GbpPost.status == status)
    out = []
    for p, x, loc in db.execute(q).all():
        if x is None:
            x = ml.LocalPostExt(organisation_id=p.organisation_id, post_id=p.id, project_id=loc.project_id)
            db.add(x)
            db.flush()
        if p.status == "review":
            S.sync_from_approval(db, x)
        out.append(_post_out(p, x, loc))
    return out


@router.post("/local/posts")
def create_post(body: PostIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    if not body.location_id:
        raise HTTPException(400, "location_id is required")
    loc, _ = S.get_location(db, ctx, body.location_id, "own")
    _validate_image(body.image_ref)
    try:
        p, x = S.create_post(db, loc, body.model_dump(), status="draft", source="manual", user_id=ctx.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "local.post_created", f"gbp_post:{p.id}")
    return _post_out(p, x, loc)


@router.get("/local/posts/{post_id}")
def get_post(post_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return _post_out(*_post(db, ctx, post_id))


@router.patch("/local/posts/{post_id}")
def update_post(post_id: uuid.UUID, body: PostIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    p, x, loc = _post(db, ctx, post_id)
    if p.status in ("publishing", "published"):
        raise HTTPException(409, "Published posts cannot be edited")
    data = body.model_dump(exclude_unset=True, exclude={"location_id"})
    if "post_type" in data:
        if data["post_type"] not in ml.POST_TYPES:
            raise HTTPException(400, "post_type must be update, offer or event")
        p.post_type = data["post_type"]
    if "body" in data:
        if not (data["body"] or "").strip():
            raise HTTPException(400, "post body is required")
        p.body = data["body"].strip()
    if "image_ref" in data:
        _validate_image(data["image_ref"])
    for k in ("title", "cta_url", "start_at", "end_at", "offer_code", "image_ref"):
        if k in data:
            v = data[k]
            if k == "cta_url" and v:
                try:
                    v = S._clean_url(v)
                except ValueError as e:
                    raise HTTPException(400, str(e))
            setattr(x, k, v or None)
    if "cta_type" in data:
        x.cta_type = S.norm_cta(data["cta_type"])
    if x.start_at and x.end_at and x.end_at < x.start_at:
        raise HTTPException(400, "end date is before start date")
    if p.status in ("review", "approved", "rejected"):  # any edit needs a fresh approval
        if x.approval_id:
            a = db.get(m.Approval, x.approval_id)
            if a and a.status == "pending":
                a.status, a.notes = "rejected", "superseded by an edit"
        p.status = "draft"
    log(db, ctx, "local.post_updated", f"gbp_post:{p.id}")
    return _post_out(p, x, loc)


@router.delete("/local/posts/{post_id}")
def delete_post(post_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    p, x, loc = _post(db, ctx, post_id)
    if p.status == "publishing":
        raise HTTPException(409, "Post is being published")
    db.delete(p)
    log(db, ctx, "local.post_deleted", f"gbp_post:{post_id}")
    return {"ok": True}


class AiPostIn(BaseModel):
    location_id: uuid.UUID
    post_type: str = "update"
    brief: str = Field(..., min_length=3, max_length=2000)
    tone: str = Field("friendly and professional", max_length=100)


@router.post("/local/posts/ai-draft")
def ai_draft_post(body: AiPostIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    from ..services import agents
    loc, lst = S.get_location(db, ctx, body.location_id, "own")
    if body.post_type not in ml.POST_TYPES:
        raise HTTPException(400, "post_type must be update, offer or event")
    nap = loc.nap or {}
    prompt = (f"Write a Google Business Profile {body.post_type} post for \"{loc.name}\", a {lst.primary_category or 'local business'} in "
              f"{nap.get('city') or 'the local area'}. Brief: {body.brief}\nTone: {body.tone}. 80–250 words, under 1,400 characters, no hashtags, "
              f"no phone numbers in the text, no invented prices or claims beyond the brief. Respond ONLY with JSON: "
              f'{{"title": "...", "body": "...", "cta_type": "BOOK|ORDER|SHOP|LEARN_MORE|SIGN_UP|CALL"}}')
    try:
        text, meta = agents.llm(db, ctx.org_id, prompt, max_tokens=700)
    except agents.NoAI:
        raise S.not_connected("ai", "AI drafting is NOT CONNECTED — no AI provider is connected. Write the post manually, or connect "
                                    "Claude, OpenAI, Gemini or Perplexity in Integrations.")
    m_ = re.search(r"\{.*\}", text, re.S)
    try:
        j = json.loads(m_.group(0)) if m_ else {}
    except ValueError:
        j = {}
    data = {"post_type": body.post_type, "title": j.get("title"), "body": (j.get("body") or text).strip()[: L.GBP_POST_MAX],
            "cta_type": j.get("cta_type") if j.get("cta_type") in ml.CTA_TYPES else None, "cta_url": nap.get("website")}
    if body.post_type == "event":
        data["title"] = data["title"] or body.brief[:80]
        data["start_at"] = (S.utcnow() + timedelta(days=7)).isoformat()
    try:
        p, x = S.create_post(db, loc, data, status="draft", source="ai", user_id=ctx.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "local.post_ai_drafted", f"gbp_post:{p.id}", {"model": meta.get("model")})
    return _post_out(p, x, loc)


@router.post("/local/posts/{post_id}/submit")
def submit_post(post_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    p, x, loc = _post(db, ctx, post_id)
    if p.status not in ("draft", "rejected"):
        raise HTTPException(409, "Only draft posts can be submitted for review")
    a = S.request_approval(db, ctx, "gbp_post", p.id, f"GBP {p.post_type} post for {loc.name}: {(x.title or p.body or '')[:60]}")
    x.approval_id, p.status = a.id, "review"
    log(db, ctx, "local.post_submitted", f"gbp_post:{p.id}")
    return _post_out(p, x, loc)


@router.post("/local/posts/{post_id}/approve")
def approve_post(post_id: uuid.UUID, body: DecisionIn | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("approve"))):
    p, x, loc = _post(db, ctx, post_id)
    if p.status != "review":
        raise HTTPException(409, "Post is not awaiting review")
    S.decide_approval(db, ctx, x.approval_id, "approve", body.notes if body else None)
    p.status, x.approved_by = "approved", ctx.user_id
    log(db, ctx, "local.post_approved", f"gbp_post:{p.id}")
    return _post_out(p, x, loc)


@router.post("/local/posts/{post_id}/reject")
def reject_post(post_id: uuid.UUID, body: DecisionIn, db=Depends(get_db), ctx: Ctx = Depends(require("approve"))):
    p, x, loc = _post(db, ctx, post_id)
    if p.status != "review":
        raise HTTPException(409, "Post is not awaiting review")
    if not body.notes:
        raise HTTPException(400, "Rejection requires notes")
    S.decide_approval(db, ctx, x.approval_id, "reject", body.notes)
    p.status = "rejected"
    log(db, ctx, "local.post_rejected", f"gbp_post:{p.id}", {"notes": body.notes})
    return _post_out(p, x, loc)


@router.post("/local/posts/{post_id}/publish")
def publish_post(post_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    p, x, loc = _post(db, ctx, post_id)
    if p.status != "approved":
        raise HTTPException(409, "Only approved posts can be published (submit for review and get approval first)")
    lst = S.ensure_listing(db, loc)
    connected = ih.is_connected(db, "gbp")
    if not connected or not lst.gbp_resource:
        why = "Google Business Profile is NOT CONNECTED" if not connected else "this location is not linked to a GBP location (sync it first)"
        x.publish_error = f"NOT CONNECTED — publish manually: {why}."
        return {"published": False, "connected": connected, "status": p.status, "message": x.publish_error + " Copy the post, publish it "
                "in Google Business Profile, then mark it as published.", "text": p.body, "post": _post_out(p, x, loc), "link": "/integrations/gbp"}
    p.status = "publishing"
    x.publish_error = None
    j = enqueue(db, ctx.org_id, "local_gbp_publish", {"kind": "post", "id": str(p.id)}, created_by=ctx.user_id)
    log(db, ctx, "local.post_publish_queued", f"gbp_post:{p.id}")
    return {"published": False, "queued": True, "connected": True, "status": "publishing", "job_id": str(j.id), "post": _post_out(p, x, loc)}


@router.post("/local/posts/{post_id}/mark-published")
def mark_post_published(post_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    p, x, loc = _post(db, ctx, post_id)
    if p.status != "approved":
        raise HTTPException(409, "Only approved posts can be marked as published")
    p.status, p.posted_at, x.publish_error = "published", S.utcnow(), None
    log(db, ctx, "local.post_marked_published", f"gbp_post:{p.id}")
    return _post_out(p, x, loc)


# ================================================================== 20 geo-grid
@router.get("/local/grid/preview")
def grid_preview(lat: float = Query(..., ge=-85, le=85), lng: float = Query(..., ge=-180, le=180), size: int = Query(5, ge=3, le=9),
                 radius_km: float = Query(2, gt=0, le=50), ctx: Ctx = Depends(require("view"))):
    return {"points": L.grid_points(lat, lng, size, radius_km), "spacing_km": round(2 * radius_km / (size - 1), 4)}


class ScanIn(BaseModel):
    project_id: uuid.UUID
    location_id: uuid.UUID
    keyword: str = Field(..., min_length=1, max_length=200)
    center_lat: float | None = Field(None, ge=-85, le=85)
    center_lng: float | None = Field(None, ge=-180, le=180)
    grid_size: int = Field(5, ge=3, le=9)
    radius_km: float = Field(2, gt=0, le=50)
    mode: str = "import"  # live | import


@router.post("/local/grid/scans")
def create_scan(body: ScanIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    loc, lst = S.get_location(db, ctx, body.location_id, "own")
    if loc.project_id != body.project_id:
        raise HTTPException(400, "location belongs to another project")
    if body.mode not in ("live", "import"):
        raise HTTPException(400, "mode must be live or import")
    lat = body.center_lat if body.center_lat is not None else (loc.nap or {}).get("lat")
    lng = body.center_lng if body.center_lng is not None else (loc.nap or {}).get("lng")
    if lat is None or lng is None:
        raise HTTPException(400, "Set the location's latitude/longitude or pass center_lat/center_lng")
    if body.mode == "live" and not ih.is_connected(db, "google_maps"):
        raise S.not_connected("google_maps", "Google Maps / Places is NOT CONNECTED — live grid scans need it. Create the scan in import "
                                             "mode and upload rank observations per point (CSV), or connect Google Maps.")
    if body.mode == "live" and not (loc.place_id or loc.name):
        raise HTTPException(400, "The location needs a Google place ID or name to be matched in Maps results")
    kw = S.get_or_create_keyword(db, ctx.org_id, body.project_id, body.keyword)
    pts = L.grid_points(lat, lng, body.grid_size, body.radius_km)
    credits = 0
    if body.mode == "live":
        credits = ent.consume(db, ctx.org_id, "local_rank_credits", len(pts))
    scan = m.GridScan(organisation_id=ctx.org_id, keyword_id=kw.id, center_lat=lat, center_lng=lng, grid_size=body.grid_size,
                      spacing_km=round(2 * body.radius_km / (body.grid_size - 1), 2), status="QUEUED" if body.mode == "live" else "AWAITING_DATA")
    db.add(scan)
    db.flush()
    for p in pts:
        db.add(m.GridPoint(organisation_id=ctx.org_id, scan_id=scan.id, lat=p["lat"], lng=p["lng"], position=None))
        db.flush()  # keep insertion order == idx (row-major)
    x = ml.LocalGridScanExt(organisation_id=ctx.org_id, scan_id=scan.id, project_id=body.project_id, location_id=loc.id,
                            radius_km=body.radius_km, mode=body.mode, credits=credits, created_by=ctx.user_id, metrics=L.grid_metrics([None] * len(pts)))
    db.add(x)
    db.flush()
    if body.mode == "live":
        j = enqueue(db, ctx.org_id, "local_grid_scan", {"scan_id": str(scan.id)}, created_by=ctx.user_id)
        x.job_id = j.id
    log(db, ctx, "local.grid_scan_created", f"grid_scan:{scan.id}", {"mode": body.mode, "size": body.grid_size, "keyword": kw.phrase})
    return S.scan_dict(db, scan, x)


def _scan(db, ctx, sid) -> tuple[m.GridScan, ml.LocalGridScanExt]:
    scan = db.get(m.GridScan, sid)
    x = db.scalar(select(ml.LocalGridScanExt).where(ml.LocalGridScanExt.scan_id == sid)) if scan else None
    if not scan or not x:
        raise HTTPException(404, "Scan not found")
    project_guard(db, ctx, x.project_id)
    return scan, x


@router.get("/local/grid/scans")
def list_scans(project_id: uuid.UUID, location_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    q = select(m.GridScan, ml.LocalGridScanExt).join(ml.LocalGridScanExt, ml.LocalGridScanExt.scan_id == m.GridScan.id) \
        .where(ml.LocalGridScanExt.project_id == project_id).order_by(m.GridScan.created_at.desc()).limit(200)
    if location_id:
        q = q.where(ml.LocalGridScanExt.location_id == location_id)
    return [S.scan_dict(db, s, x, points=False) for s, x in db.execute(q).all()]


@router.get("/local/grid/scans/{sid}")
def get_scan(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    scan, x = _scan(db, ctx, sid)
    return S.scan_dict(db, scan, x)


@router.delete("/local/grid/scans/{sid}")
def delete_scan(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    scan, x = _scan(db, ctx, sid)
    if scan.status == "RUNNING":
        raise HTTPException(409, "Scan is running")
    db.delete(scan)
    log(db, ctx, "local.grid_scan_deleted", f"grid_scan:{sid}")
    return {"ok": True}


@router.get("/local/grid/scans/{sid}/template.csv")
def scan_template(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    scan, x = _scan(db, ctx, sid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["row", "col", "lat", "lng", "rank"])
    n = scan.grid_size
    for i, p in enumerate(S.scan_points(db, scan.id)):
        w.writerow([i // n, i % n, float(p.lat), float(p.lng), "" if p.position is None else (p.position or "20+")])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="grid-{sid}.csv"'})


@router.post("/local/grid/scans/{sid}/import")
async def import_ranks(sid: uuid.UUID, file: UploadFile = File(...), db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    scan, x = _scan(db, ctx, sid)
    if scan.status in ("RUNNING", "QUEUED"):
        raise HTTPException(409, "A live scan is in progress")
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File too large")
    try:
        rows = S.read_csv(content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    res = S.import_grid_ranks(db, scan, x, rows)
    log(db, ctx, "local.grid_ranks_imported", f"grid_scan:{sid}", {"updated": res["updated"]})
    return {**res, "scan": S.scan_dict(db, scan, x)}


class RankObs(BaseModel):
    row: int | None = Field(None, ge=0, le=8)
    col: int | None = Field(None, ge=0, le=8)
    idx: int | None = Field(None, ge=0, le=80)
    lat: float | None = None
    lng: float | None = None
    rank: int | str | None = None


@router.post("/local/grid/scans/{sid}/ranks")
def set_ranks(sid: uuid.UUID, body: list[RankObs], db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    scan, x = _scan(db, ctx, sid)
    if scan.status in ("RUNNING", "QUEUED"):
        raise HTTPException(409, "A live scan is in progress")
    if len(body) > 81:
        raise HTTPException(400, "Too many observations")
    rows = [{k: ("" if v is None else str(v)) for k, v in o.model_dump().items()} for o in body]
    res = S.import_grid_ranks(db, scan, x, rows)
    return {**res, "scan": S.scan_dict(db, scan, x)}


@router.get("/local/grid/compare")
def compare_scans(a: uuid.UUID, b: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    sa, xa = _scan(db, ctx, a)
    sb, xb = _scan(db, ctx, b)
    da, dbb = S.scan_dict(db, sa, xa), S.scan_dict(db, sb, xb)
    deltas = {}
    for k in ("arp", "atrp", "solv", "top3", "found"):
        va, vb = da["metrics"].get(k), dbb["metrics"].get(k)
        deltas[k] = round(vb - va, 2) if va is not None and vb is not None else None
    per_point = None
    if sa.grid_size == sb.grid_size:
        per_point = []
        for pa, pb in zip(da["points"], dbb["points"]):
            ra = pa["position"] if pa["position"] else (None if pa["position"] is None else (xa.depth or 20) + 1)
            rb = pb["position"] if pb["position"] else (None if pb["position"] is None else (xb.depth or 20) + 1)
            per_point.append({"idx": pa["idx"], "a": pa["position"], "b": pb["position"],
                              "improvement": (ra - rb) if ra is not None and rb is not None else None})
    return {"a": da, "b": dbb, "deltas": deltas, "per_point": per_point,
            "same_center": abs(da["center_lat"] - dbb["center_lat"]) < 1e-4 and abs(da["center_lng"] - dbb["center_lng"]) < 1e-4}


# ================================================================== 19 local scan + 21 competitors
class LocalScanIn(BaseModel):
    location_id: uuid.UUID
    keyword: str = Field(..., min_length=1, max_length=200)
    lat: float | None = Field(None, ge=-85, le=85)
    lng: float | None = Field(None, ge=-180, le=180)
    radius_km: float = Field(5, gt=0, le=50)
    discover: bool = False
    limit: int = Field(10, ge=1, le=20)


def _center(loc, lat, lng):
    lat = lat if lat is not None else (loc.nap or {}).get("lat")
    lng = lng if lng is not None else (loc.nap or {}).get("lng")
    return (float(lat), float(lng)) if lat is not None and lng is not None else None


def _discover(db, ctx, pid, loc, keyword, center, radius_km, limit):
    if not ih.is_connected(db, "google_maps"):
        raise S.not_connected("google_maps", "Google Maps / Places is NOT CONNECTED — competitor discovery needs it. Add competitors "
                                             "manually or import them by CSV.")
    if not center:
        raise HTTPException(400, "Set the location's latitude/longitude (or pass lat/lng) to discover nearby competitors")
    try:
        return S.discover_competitors(db, ctx.org_id, pid, loc, keyword, center[0], center[1], radius_km, limit)
    except ih.NotConnected as e:
        raise S.not_connected("google_maps", str(e))
    except ih.IntegrationError as e:
        raise HTTPException(502, f"Google Maps error: {e}")


@router.post("/local/projects/{pid}/scan")
def local_scan(pid: uuid.UUID, body: LocalScanIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    loc, lst = S.get_location(db, ctx, body.location_id, "own")
    if loc.project_id != pid:
        raise HTTPException(400, "location belongs to another project")
    center = _center(loc, body.lat, body.lng)
    disc = _discover(db, ctx, pid, loc, body.keyword, center, body.radius_km, body.limit) if body.discover else None
    kw = body.keyword.strip().lower()
    rows = []
    comps = S.project_listings(db, pid, "competitor")
    tagged = [(c, cl) for c, cl in comps if (cl.discovered_for or "").lower() == kw]
    for c, cl in [(loc, lst)] + (tagged or comps):
        d = S.listing_dict(db, c, cl)
        sc = L.local_score(d, kw, center)
        rows.append({"id": d["id"], "name": d["name"], "own": c.id == loc.id, "rating": d["rating"], "review_count": d["review_count"],
                     "primary_category": d["primary_category"], "categories": [x for x in [d["primary_category"]] + d["secondary_categories"] if x],
                     "photos_count": d["photos_count"], "website": d["website"], "posts_per_month": d["posts_per_month"],
                     "distance_km": sc["distance_km"], "local_score": sc["score"], "components": sc["components"]})
    rows.sort(key=lambda r: -r["local_score"])
    for i, r in enumerate(rows):
        r["position"] = i + 1
    own = next(r for r in rows if r["own"])
    res = {"keyword": kw, "center": center, "radius_km": body.radius_km, "rows": rows, "own_position": own["position"],
           "own_score": own["local_score"], "discovery": disc, "competitors_compared": len(rows) - 1,
           "note": None if len(rows) > 1 else "No competitors recorded for this project — add them manually, import a CSV, or connect Google Maps and run discovery."}
    run = _save_run(db, ctx, pid, loc.id, "local_scan", own["local_score"], body.model_dump(), res)
    log(db, ctx, "local.scan", f"project:{pid}", {"keyword": kw, "discover": body.discover})
    return _run_out(run)


@router.get("/local/projects/{pid}/competitors")
def competitors(pid: uuid.UUID, location_id: uuid.UUID | None = None, keyword: str | None = Query(None, max_length=200),
                db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    owns = S.project_listings(db, pid, "own")
    own = next(((l_, s_) for l_, s_ in owns if not location_id or l_.id == location_id), None)
    center = _center(own[0], None, None) if own else None
    kw = keyword or (own[1].keywords[0] if own and own[1].keywords else None)
    own_d = S.listing_dict(db, *own) if own else None
    comps = []
    for c, cl in S.project_listings(db, pid, "competitor"):
        d = S.listing_dict(db, c, cl)
        d["local"] = L.local_score(d, kw, center)
        comps.append(d)
    comps.sort(key=lambda d: -d["local"]["score"])
    if own_d:
        own_d["local"] = L.local_score(own_d, kw, center)
    ranking = sorted(([{"id": own_d["id"], "name": own_d["name"], "own": True, "score": own_d["local"]["score"]}] if own_d else []) +
                     [{"id": d["id"], "name": d["name"], "own": False, "score": d["local"]["score"]} for d in comps], key=lambda r: -r["score"])
    return {"own": own_d, "competitors": comps, "keyword": kw, "ranking": ranking,
            "gaps": L.competitor_gaps(own_d, comps) if own_d else None, "connections": S.connection_status(db)}


class DiscoverIn(BaseModel):
    location_id: uuid.UUID
    keyword: str = Field(..., min_length=1, max_length=200)
    radius_km: float = Field(5, gt=0, le=50)
    limit: int = Field(10, ge=1, le=20)


@router.post("/local/projects/{pid}/competitors/discover")
def discover(pid: uuid.UUID, body: DiscoverIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    loc, _ = S.get_location(db, ctx, body.location_id, "own")
    res = _discover(db, ctx, pid, loc, body.keyword.strip().lower(), _center(loc, None, None), body.radius_km, body.limit)
    log(db, ctx, "local.competitors_discovered", f"project:{pid}", {"keyword": body.keyword, "added": len(res["competitors"])})
    return res


# ================================================================== 22 website ↔ GBP
class WebsiteIn(BaseModel):
    url: str | None = Field(None, max_length=2000)
    extra_urls: list[str] = Field(default_factory=list, max_length=3)


def _fetch_site(url: str) -> dict:
    from ..services.crawler import CrawlBlocked, Crawler
    url = (url or "").strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I) and not re.match(r"^https?://", url, re.I):
        raise HTTPException(400, "Only http(s) URLs can be audited")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    if not urlparse_ok(url) or "." not in (urlparse_host(url)) and not urlparse_host(url).startswith("127."):
        raise HTTPException(400, "Invalid URL")
    c = Crawler(url, max_pages=1, render_sample=0, mobile_sample=0, external_check_limit=0, delay=0)
    try:
        r = c.fetch(url)
    except CrawlBlocked as e:
        raise HTTPException(400, f"URL not allowed: {e}")
    if r.get("status") != 200 or not r.get("body"):
        raise HTTPException(422, f"Could not fetch {url}: {r.get('error') or 'HTTP ' + str(r.get('status'))}")
    site = L.extract_site_local(r["body"], url)
    site["final_url"] = r.get("final_url") or url
    return site


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or ""


def urlparse_ok(url: str) -> bool:
    from urllib.parse import urlparse
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.hostname)


@router.post("/local/locations/{lid}/website-audit")
def website_audit(lid: uuid.UUID, body: WebsiteIn | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    body = body or WebsiteIn()
    loc, lst = S.get_location(db, ctx, lid)
    url = body.url or (loc.nap or {}).get("website")
    if not url:
        raise HTTPException(400, "The location has no website — add one or pass a URL")
    site = _fetch_site(url)
    for extra in body.extra_urls:
        try:
            more = _fetch_site(extra)
        except HTTPException:
            continue
        site["text"] = (site["text"] + " " + more["text"])[:40000]
        site["headings"] = (site["headings"] + more["headings"])[:60]
        site["tel_links"] = sorted(set(site["tel_links"]) | set(more["tel_links"]))
        site["maps_embed"] = site["maps_embed"] or more["maps_embed"]
        site["maps_link"] = site["maps_link"] or more["maps_link"]
        site["schema"] = site["schema"] or more["schema"]
        site["address_block"] = site["address_block"] or more["address_block"]
        site["hours_text"] = site["hours_text"] or more["hours_text"]
    g = {"name": loc.name, "nap": loc.nap or {}, "hours": lst.hours or {}, "primary_category": lst.primary_category,
         "secondary_categories": lst.secondary_categories or []}
    res = L.compare_website_gbp(site, g)
    res["url"] = site["final_url"]
    res["page"] = {"title": site["title"], "schema_types": site["schema_types"], "maps_embed": site["maps_embed"], "tel_links": site["tel_links"]}
    run = _save_run(db, ctx, loc.project_id, loc.id, "website_gbp", res["score"], {"url": url, "extra_urls": body.extra_urls}, res)
    log(db, ctx, "local.website_gbp_audit", f"gbp_location:{loc.id}", {"score": res["score"]})
    return _run_out(run)


# ================================================================== 35 automation
class AutomationIn(BaseModel):
    project_id: uuid.UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    post_type: str | None = None
    title_template: str | None = Field(None, max_length=300)
    body_template: str | None = Field(None, min_length=1, max_length=L.GBP_POST_MAX)
    cta_type: str | None = Field(None, max_length=20)
    cta_url_template: str | None = Field(None, max_length=1000)
    image_ref: str | None = Field(None, max_length=1000)
    location_ids: list[uuid.UUID] | None = Field(None, max_length=200)
    variables: dict[str, dict[str, str]] | None = None
    frequency: str | None = None  # once / weekly / monthly
    start_at: datetime | None = None
    enabled: bool | None = None


def _auto_out(db, a: ml.LocalAutomation) -> dict:
    d = to_dict(a)
    d["location_ids"] = [str(x) for x in a.location_ids or []]
    sch = db.get(m.JobSchedule, a.schedule_id) if a.schedule_id else None
    d["next_run_at"] = sch.next_run_at if sch and sch.enabled else None
    d["posts_created"] = db.scalar(select(func.count()).select_from(ml.LocalPostExt).where(ml.LocalPostExt.automation_id == a.id)) or 0
    d["posts_pending"] = db.scalar(select(func.count()).select_from(ml.LocalPostExt).join(m.GbpPost, m.GbpPost.id == ml.LocalPostExt.post_id)
                                   .where(ml.LocalPostExt.automation_id == a.id, m.GbpPost.status == "review")) or 0
    return d


def _validate_automation(db, ctx, a: ml.LocalAutomation):
    if a.post_type not in ml.POST_TYPES:
        raise HTTPException(400, "post_type must be update, offer or event")
    if a.frequency not in ("once", "weekly", "monthly"):
        raise HTTPException(400, "frequency must be once, weekly or monthly")
    if not a.location_ids:
        raise HTTPException(400, "Choose at least one location")
    for lid in a.location_ids:
        loc, lst = S.get_location(db, ctx, uuid.UUID(str(lid)), "own")
        if loc.project_id != a.project_id:
            raise HTTPException(400, "All locations must belong to the automation's project")
    if a.cta_type:
        a.cta_type = S.norm_cta(a.cta_type)
    _validate_image(a.image_ref)
    unknown = set()
    for tpl in (a.title_template, a.body_template, a.cta_url_template):
        for v in S.TEMPLATE_VAR.findall(tpl or ""):
            unknown.add(v)
    known = set(S.location_vars(db.get(m.GbpLocation, uuid.UUID(str(a.location_ids[0]))), S.ensure_listing(db, db.get(m.GbpLocation, uuid.UUID(str(a.location_ids[0])))), {}))
    custom = {k for v in (a.variables or {}).values() for k in v}
    bad = unknown - known - custom
    if bad:
        raise HTTPException(400, f"Unknown template variables: {', '.join(sorted(bad))} (define them per location under variables)")


def _sync_schedule(db, ctx, a: ml.LocalAutomation, start_at: datetime | None):
    sch = db.get(m.JobSchedule, a.schedule_id) if a.schedule_id else None
    if a.frequency == "once":
        if sch:
            sch.enabled = False
        return
    if not sch:
        sch = m.JobSchedule(organisation_id=ctx.org_id, kind="local_automation_run", payload={"automation_id": str(a.id)},
                            frequency=a.frequency, next_run_at=start_at or S.utcnow(), created_by=ctx.user_id)
        db.add(sch)
        db.flush()
        a.schedule_id = sch.id
    sch.frequency, sch.enabled = a.frequency, a.enabled
    if start_at:
        sch.next_run_at = start_at
    assert a.frequency in FREQ


@router.get("/local/automations")
def list_automations(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    return [_auto_out(db, a) for a in db.scalars(select(ml.LocalAutomation).where(ml.LocalAutomation.project_id == project_id)
                                                 .order_by(ml.LocalAutomation.created_at.desc()))]


def _build_automation(db, ctx, body: AutomationIn) -> ml.LocalAutomation:
    if not body.project_id or not body.name or not body.body_template:
        raise HTTPException(400, "project_id, name and body_template are required")
    project_guard(db, ctx, body.project_id)
    a = ml.LocalAutomation(organisation_id=ctx.org_id, project_id=body.project_id, name=body.name.strip(), post_type=body.post_type or "update",
                           title_template=body.title_template, body_template=body.body_template, cta_type=body.cta_type,
                           cta_url_template=body.cta_url_template, image_ref=body.image_ref,
                           location_ids=[str(x) for x in body.location_ids or []], variables=body.variables or {},
                           frequency=body.frequency or "once", enabled=True if body.enabled is None else body.enabled, created_by=ctx.user_id)
    _validate_automation(db, ctx, a)
    return a


@router.post("/local/automations/preview")
def preview_automation(body: AutomationIn, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    a = _build_automation(db, ctx, body)
    return S.automation_preview(db, a)


@router.post("/local/automations")
def create_automation(body: AutomationIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    a = _build_automation(db, ctx, body)
    db.add(a)
    db.flush()
    _sync_schedule(db, ctx, a, body.start_at)
    job = None
    if a.frequency == "once" and a.enabled:
        job = enqueue(db, ctx.org_id, "local_automation_run", {"automation_id": str(a.id)}, run_at=body.start_at, created_by=ctx.user_id)
    log(db, ctx, "local.automation_created", f"local_automation:{a.id}", {"frequency": a.frequency, "locations": len(a.location_ids)})
    return {**_auto_out(db, a), "job_id": str(job.id) if job else None}


def _automation(db, ctx, aid) -> ml.LocalAutomation:
    a = db.get(ml.LocalAutomation, aid)
    if not a:
        raise HTTPException(404, "Automation not found")
    project_guard(db, ctx, a.project_id)
    return a


@router.get("/local/automations/{aid}")
def get_automation(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    a = _automation(db, ctx, aid)
    return {**_auto_out(db, a), "preview": S.automation_preview(db, a)}


@router.patch("/local/automations/{aid}")
def update_automation(aid: uuid.UUID, body: AutomationIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    a = _automation(db, ctx, aid)
    data = body.model_dump(exclude_unset=True, exclude={"project_id", "start_at"})
    for k, v in data.items():
        if k == "location_ids":
            v = [str(x) for x in v or []]
        if k in ("name", "body_template") and not v:
            raise HTTPException(400, f"{k} cannot be empty")
        setattr(a, k, v)
    _validate_automation(db, ctx, a)
    _sync_schedule(db, ctx, a, body.start_at)
    log(db, ctx, "local.automation_updated", f"local_automation:{a.id}", {"fields": sorted(data)})
    return _auto_out(db, a)


@router.delete("/local/automations/{aid}")
def delete_automation(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    a = _automation(db, ctx, aid)
    if a.schedule_id:
        sch = db.get(m.JobSchedule, a.schedule_id)
        a.schedule_id = None
        db.flush()
        if sch:
            db.delete(sch)
    db.delete(a)
    log(db, ctx, "local.automation_deleted", f"local_automation:{aid}")
    return {"ok": True}


@router.post("/local/automations/{aid}/run")
def run_automation_now(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    a = _automation(db, ctx, aid)
    if not a.enabled:
        raise HTTPException(409, "Automation is paused")
    j = enqueue(db, ctx.org_id, "local_automation_run", {"automation_id": str(a.id), "triggered_by": "manual"}, created_by=ctx.user_id)
    log(db, ctx, "local.automation_run", f"local_automation:{a.id}")
    return {"job_id": str(j.id)}

