"""Local SEO data access + workflows shared by the router and job handlers: listings (own + competitor),
CSV imports, review / post workflows with approvals, geo-grid scans, GBP / Places sync, automation runs."""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_local as ml
from . import integrations as ih
from . import local_core as L

MAX_ROWS = 5000


def utcnow():
    return datetime.now(timezone.utc)


def not_connected(provider: str, message: str | None = None):
    names = {"gbp": "Google Business Profile", "google_maps": "Google Maps / Places", "ai": "AI provider"}
    link = "/integrations" if provider == "ai" else f"/integrations/{provider}"
    return HTTPException(409, detail={"code": "not_connected", "provider": provider, "link": link,
                                      "message": message or f"{names.get(provider, provider)} is NOT CONNECTED"})


def connection_status(db: Session) -> dict:
    conn = ih.connected_map(db)
    ai = any(conn.get(p) == "CONNECTED" for p in ("claude", "openai", "gemini", "perplexity"))
    out = {}
    for k in ("gbp", "google_maps"):
        st = conn.get(k) or "NOT_CONNECTED"
        out[k] = {"status": st, "connected": st in ("CONNECTED", "RATE_LIMITED"), "link": f"/integrations/{k}",
                  "name": ih.BY_KEY[k].spec_name}
    out["ai"] = {"status": "CONNECTED" if ai else "NOT_CONNECTED", "connected": ai, "link": "/integrations", "name": "AI provider"}
    return out


# ================================================================== listings
def ensure_listing(db: Session, loc: m.GbpLocation, kind: str = "own") -> ml.LocalListing:
    lst = db.scalar(select(ml.LocalListing).where(ml.LocalListing.location_id == loc.id))
    if not lst:
        lst = ml.LocalListing(organisation_id=loc.organisation_id, location_id=loc.id, project_id=loc.project_id, kind=kind,
                              secondary_categories=[], hours={}, services=[], products=[], attributes=[], social_links=[],
                              citations=[], keywords=[])
        db.add(lst)
        db.flush()
    return lst


def review_rows(db: Session, location_id) -> list[dict]:
    q = select(m.GbpReview, ml.LocalReviewExt).outerjoin(ml.LocalReviewExt, ml.LocalReviewExt.review_id == m.GbpReview.id) \
        .where(m.GbpReview.location_id == location_id).order_by(m.GbpReview.reviewed_at.desc().nullslast())
    out = []
    for r, x in db.execute(q).all():
        out.append({"id": str(r.id), "rating": r.rating, "body": r.body, "sentiment": float(r.sentiment) if r.sentiment is not None else None,
                    "reply": r.reply, "reviewed_at": r.reviewed_at, "replied_at": x.replied_at if x else None,
                    "author": x.author if x else None, "reply_status": x.reply_status if x else ("published" if r.reply else "none"),
                    "reply_draft": x.reply_draft if x else None, "reply_source": x.reply_source if x else None,
                    "topics": x.topics if x else [], "source": x.source if x else "manual", "external_id": x.external_id if x else None,
                    "publish_error": x.publish_error if x else None, "approval_id": str(x.approval_id) if x and x.approval_id else None})
    return out


def effective_totals(lst: ml.LocalListing, reviews: list[dict]) -> dict:
    rated = [r["rating"] for r in reviews if r.get("rating")]
    n = len(reviews)
    lc = int(lst.review_count or 0)
    if lc > n and lst.rating is not None:
        return {"rating": float(lst.rating), "review_count": lc}
    if rated:
        return {"rating": round(sum(rated) / len(rated), 2), "review_count": max(n, lc)}
    return {"rating": float(lst.rating) if lst.rating is not None else None, "review_count": max(n, lc) or (lst.review_count)}


def listing_dict(db: Session, loc: m.GbpLocation, lst: ml.LocalListing | None = None, with_counts: bool = True) -> dict:
    lst = lst or ensure_listing(db, loc)
    nap = loc.nap or {}
    d = {"id": str(loc.id), "project_id": str(loc.project_id), "name": loc.name, "place_id": loc.place_id, "cid": loc.cid, "nap": nap,
         "kind": lst.kind, "source": lst.source, "gbp_resource": lst.gbp_resource, "primary_category": lst.primary_category,
         "secondary_categories": lst.secondary_categories or [], "hours": lst.hours or {}, "description": lst.description,
         "photos_count": lst.photos_count or 0, "has_logo": lst.has_logo, "services": lst.services or [], "products": lst.products or [],
         "attributes": lst.attributes or [], "qa_questions": lst.qa_questions or 0, "qa_answered": lst.qa_answered or 0,
         "social_links": lst.social_links or [], "citations": lst.citations or [], "keywords": lst.keywords or [],
         "rating": float(lst.rating) if lst.rating is not None else None, "review_count": lst.review_count,
         "posts_per_month": float(lst.posts_per_month) if lst.posts_per_month is not None else None,
         "last_post_at": lst.last_post_at, "verified": lst.verified, "discovered_for": lst.discovered_for,
         "competitor_id": str(lst.competitor_id) if lst.competitor_id else None, "synced_at": lst.synced_at,
         "website": nap.get("website"), "lat": nap.get("lat"), "lng": nap.get("lng"), "updated_at": lst.updated_at}
    if with_counts and lst.kind == "own":
        revs = review_rows(db, loc.id)
        tot = effective_totals(lst, revs)
        d["rating"], d["review_count"] = tot["rating"], tot["review_count"]
        d["reviews_recorded"] = len(revs)
        last = db.scalar(select(func.max(m.GbpPost.posted_at)).where(m.GbpPost.location_id == loc.id, m.GbpPost.status == "published"))
        if last and (not d["last_post_at"] or last > d["last_post_at"]):
            d["last_post_at"] = last
        n90 = db.scalar(select(func.count()).select_from(m.GbpPost).where(m.GbpPost.location_id == loc.id, m.GbpPost.status == "published",
                                                                          m.GbpPost.posted_at > utcnow() - timedelta(days=90))) or 0
        if n90 or d["posts_per_month"] is None:
            d["posts_per_month"] = round(n90 / 3, 2)
    return d


NAP_KEYS = ("address", "address2", "city", "region", "postcode", "country", "phone", "website", "lat", "lng")
LIST_FIELDS = ("primary_category", "secondary_categories", "hours", "description", "photos_count", "has_logo", "services", "products",
               "attributes", "qa_questions", "qa_answered", "social_links", "citations", "keywords", "rating", "review_count",
               "posts_per_month", "last_post_at", "verified", "discovered_for")


def _clean_url(u):
    if not u:
        return None
    u = str(u).strip()
    if not re.match(r"^https?://", u):
        u = "https://" + u
    p = urlparse(u)
    if not p.netloc or "." not in p.netloc:
        raise ValueError(f"invalid website URL: {u}")
    return u


def apply_fields(db: Session, loc: m.GbpLocation, lst: ml.LocalListing, data: dict):
    if "name" in data and data["name"] is not None:
        loc.name = str(data["name"]).strip()[:300]
    for k in ("place_id", "cid"):
        if data.get(k) is not None:
            setattr(loc, k, str(data[k]).strip() or None)
    nap = dict(loc.nap or {})
    src = data.get("nap") or {}
    for k in NAP_KEYS:
        v = src.get(k, data.get(k)) if (k in src or k in data) else "__absent__"
        if v == "__absent__":
            continue
        if k == "website":
            v = _clean_url(v)
        elif k in ("lat", "lng"):
            v = float(v) if v not in (None, "") else None
            if v is not None and ((k == "lat" and not -90 <= v <= 90) or (k == "lng" and not -180 <= v <= 180)):
                raise ValueError(f"{k} out of range")
        else:
            v = str(v).strip()[:300] if v not in (None, "") else None
        nap[k] = v
    loc.nap = nap
    for k in LIST_FIELDS:
        if k not in data or data[k] is None and k not in ("rating", "review_count", "posts_per_month", "last_post_at", "primary_category"):
            continue
        v = data[k]
        if k in ("secondary_categories", "services", "products", "attributes", "social_links", "keywords"):
            v = [str(x).strip()[:200] for x in (v if isinstance(v, list) else str(v).split(";")) if str(x).strip()][:100]
        elif k == "hours":
            v = L.parse_hours(v)
        elif k in ("photos_count", "qa_questions", "qa_answered", "review_count"):
            v = int(float(v)) if v not in (None, "") else (None if k == "review_count" else 0)
            if v is not None and v < 0:
                raise ValueError(f"{k} must be ≥ 0")
        elif k in ("rating",):
            v = float(v) if v not in (None, "") else None
            if v is not None and not 0 <= v <= 5:
                raise ValueError("rating must be between 0 and 5")
        elif k == "posts_per_month":
            v = float(v) if v not in (None, "") else None
        elif k == "last_post_at":
            v = L.parse_date(v)
        elif k in ("has_logo", "verified"):
            v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "y")
        elif k == "description":
            v = str(v)[:5000] if v else None
        elif k == "citations":
            v = [{kk: str(c.get(kk) or "")[:300] for kk in ("source", "name", "address", "phone", "url")} for c in (v or []) if isinstance(c, dict)][:100]
        elif k == "primary_category":
            v = str(v).strip()[:200] if v else None
        setattr(lst, k, v)
    if lst.kind == "competitor":
        link_competitor(db, loc, lst)


def link_competitor(db: Session, loc: m.GbpLocation, lst: ml.LocalListing):
    web = (loc.nap or {}).get("website")
    if not web:
        return
    dom = urlparse(web).netloc.lower().removeprefix("www.")
    if not dom:
        return
    c = db.scalar(select(m.Competitor).where(m.Competitor.project_id == loc.project_id, m.Competitor.domain == dom))
    if not c:
        c = m.Competitor(organisation_id=loc.organisation_id, project_id=loc.project_id, domain=dom)
        db.add(c)
        db.flush()
    lst.competitor_id = c.id


def find_existing(db: Session, project_id, kind: str, data: dict) -> m.GbpLocation | None:
    q = select(m.GbpLocation).join(ml.LocalListing, ml.LocalListing.location_id == m.GbpLocation.id) \
        .where(m.GbpLocation.project_id == project_id, ml.LocalListing.kind == kind)
    if data.get("place_id"):
        hit = db.scalar(q.where(m.GbpLocation.place_id == data["place_id"]))
        if hit:
            return hit
    if data.get("gbp_resource"):
        hit = db.scalar(q.where(ml.LocalListing.gbp_resource == data["gbp_resource"]))
        if hit:
            return hit
    nn = L.norm_name(data.get("name"))
    nap = data.get("nap") or {k: data.get(k) for k in NAP_KEYS}
    ak = L.address_key(nap)
    for loc in db.scalars(q):
        if nn and L.norm_name(loc.name) == nn and (not ak or not L.address_key(loc.nap or {}) or L.address_key(loc.nap or {}) == ak):
            return loc
    return None


def upsert_location(db: Session, org_id, project_id, kind: str, data: dict, source: str) -> tuple[m.GbpLocation, bool]:
    if kind not in ml.LISTING_KINDS:
        raise ValueError("kind must be own or competitor")
    if not (data.get("name") or "").strip():
        raise ValueError("name is required")
    loc = find_existing(db, project_id, kind, data)
    created = loc is None
    if created:
        loc = m.GbpLocation(organisation_id=org_id, project_id=project_id, name=data["name"].strip(), nap={})
        db.add(loc)
        db.flush()
    lst = ensure_listing(db, loc, kind)
    lst.kind = kind
    if created or source != "manual":
        lst.source = source
    if data.get("gbp_resource"):
        lst.gbp_resource = data["gbp_resource"]
    apply_fields(db, loc, lst, data)
    if source in ("gbp", "places"):
        lst.synced_at = utcnow()
    db.flush()
    return loc, created


def get_location(db: Session, ctx, lid, kind: str | None = None) -> tuple[m.GbpLocation, ml.LocalListing]:
    from ..deps import project_guard
    loc = db.get(m.GbpLocation, lid)
    if not loc:
        raise HTTPException(404, "Location not found")
    project_guard(db, ctx, loc.project_id)
    lst = ensure_listing(db, loc)
    if kind and lst.kind != kind:
        raise HTTPException(400, f"This action needs a {'business location' if kind == 'own' else 'competitor listing'}")
    return loc, lst


def project_listings(db: Session, project_id, kind: str | None = None) -> list[tuple[m.GbpLocation, ml.LocalListing]]:
    q = select(m.GbpLocation, ml.LocalListing).join(ml.LocalListing, ml.LocalListing.location_id == m.GbpLocation.id) \
        .where(m.GbpLocation.project_id == project_id).order_by(m.GbpLocation.name)
    if kind:
        q = q.where(ml.LocalListing.kind == kind)
    # legacy rows without an extension (e.g. created by other modules) are adopted as own locations
    for loc in db.scalars(select(m.GbpLocation).where(m.GbpLocation.project_id == project_id,
                                                       ~m.GbpLocation.id.in_(select(ml.LocalListing.location_id)))):
        ensure_listing(db, loc)
    return list(db.execute(q).all())


# ================================================================== CSV
def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def read_csv(content: bytes) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rd = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not rd.fieldnames:
        raise ValueError("CSV has no header row")
    rows = []
    for i, r in enumerate(rd):
        if i >= MAX_ROWS:
            raise ValueError(f"CSV has more than {MAX_ROWS} rows")
        rows.append({_norm_header(k): (v or "").strip() for k, v in r.items() if k})
    return rows


ALIASES = {
    "name": ("name", "business_name", "title", "location", "location_name", "competitor"),
    "address": ("address", "street", "street_address", "address_line_1", "address1"),
    "city": ("city", "town", "locality"), "region": ("region", "state", "county", "province"),
    "postcode": ("postcode", "postal_code", "zip", "zip_code"), "country": ("country", "country_code"),
    "phone": ("phone", "telephone", "phone_number", "primary_phone"), "website": ("website", "url", "website_url", "site"),
    "primary_category": ("primary_category", "category", "main_category"),
    "secondary_categories": ("additional_categories", "secondary_categories", "categories"),
    "lat": ("lat", "latitude"), "lng": ("lng", "lon", "long", "longitude"), "place_id": ("place_id",), "cid": ("cid",),
    "description": ("description",), "photos_count": ("photos", "photos_count", "photo_count"),
    "rating": ("rating", "average_rating", "stars"), "review_count": ("review_count", "reviews", "total_reviews"),
    "posts_per_month": ("posts_per_month",), "services": ("services",), "products": ("products",), "attributes": ("attributes",),
    "hours": ("hours", "opening_hours"), "keywords": ("keywords",),
}


def _pick(r: dict, key: str):
    for a in ALIASES.get(key, (key,)):
        if r.get(a) not in (None, ""):
            return r[a]
    return None


def import_locations(db: Session, org_id, project_id, content: bytes, kind: str) -> dict:
    rows = read_csv(content)
    created = updated = 0
    errors = []
    for i, r in enumerate(rows, start=2):
        data = {k: _pick(r, k) for k in ALIASES}
        data = {k: v for k, v in data.items() if v is not None}
        try:
            with db.begin_nested():
                _, c = upsert_location(db, org_id, project_id, kind, data, "import")
            created += c
            updated += not c
        except (ValueError, TypeError) as e:
            errors.append({"row": i, "error": str(e)})
    return {"rows": len(rows), "created": created, "updated": updated, "errors": errors[:50]}


def _resolve_location(db: Session, project_id, r: dict, cache: dict, default_location=None) -> m.GbpLocation | None:
    ref = r.get("location_id") or r.get("location") or r.get("location_name") or r.get("business")
    if not ref:
        return default_location
    if ref in cache:
        return cache[ref]
    loc = None
    try:
        loc = db.get(m.GbpLocation, uuid.UUID(ref))
        if loc and loc.project_id != project_id:
            loc = None
    except ValueError:
        nn = L.norm_name(ref)
        for x in db.scalars(select(m.GbpLocation).where(m.GbpLocation.project_id == project_id)):
            if L.norm_name(x.name) == nn:
                loc = x
                break
    cache[ref] = loc
    return loc


def add_review(db: Session, loc: m.GbpLocation, data: dict, source: str) -> tuple[m.GbpReview, bool]:
    rating = data.get("rating")
    if rating in (None, ""):
        rating = None
    else:
        rating = int(round(float(str(rating).split("/")[0])))
        if not 1 <= rating <= 5:
            raise ValueError("rating must be 1–5")
    body = (data.get("body") or "").strip()[:10000] or None
    rat = L.parse_date(data.get("reviewed_at"))
    if data.get("reviewed_at") and rat is None:
        raise ValueError(f"unrecognised date: {data.get('reviewed_at')}")
    ext_id = data.get("external_id")
    existing = None
    if ext_id:
        existing = db.execute(select(m.GbpReview).join(ml.LocalReviewExt, ml.LocalReviewExt.review_id == m.GbpReview.id)
                              .where(m.GbpReview.location_id == loc.id, ml.LocalReviewExt.external_id == ext_id)).scalar()
    if not existing:  # de-duplicate re-imports on (author, date, text)
        for r, x in db.execute(select(m.GbpReview, ml.LocalReviewExt).outerjoin(ml.LocalReviewExt, ml.LocalReviewExt.review_id == m.GbpReview.id)
                               .where(m.GbpReview.location_id == loc.id, m.GbpReview.reviewed_at == rat, m.GbpReview.body == body)):
            if (x.author if x else None) == (data.get("author") or None):
                existing = r
                break
    se = L.sentiment(body or "")
    rv = existing or m.GbpReview(organisation_id=loc.organisation_id, location_id=loc.id)
    rv.rating, rv.body, rv.reviewed_at, rv.sentiment = rating, body, rat, se["score"]
    reply = (data.get("reply") or "").strip() or None
    if reply:
        rv.reply = reply[:4096]
    if not existing:
        db.add(rv)
    db.flush()
    x = db.scalar(select(ml.LocalReviewExt).where(ml.LocalReviewExt.review_id == rv.id))
    if not x:
        x = ml.LocalReviewExt(organisation_id=loc.organisation_id, review_id=rv.id, source=source)
        db.add(x)
    x.author = (data.get("author") or "").strip()[:200] or x.author
    x.external_id = ext_id or x.external_id
    x.topics = L.topics(body or "")
    if reply:
        x.reply_status = "published"
        x.replied_at = L.parse_date(data.get("replied_at")) or x.replied_at
    db.flush()
    return rv, existing is None


def import_reviews(db: Session, project_id, content: bytes, default_location=None) -> dict:
    rows = read_csv(content)
    cache: dict = {}
    created = updated = 0
    errors = []
    for i, r in enumerate(rows, start=2):
        loc = _resolve_location(db, project_id, r, cache, default_location)
        if not loc:
            errors.append({"row": i, "error": "unknown location (use a location column matching a location name or id)"})
            continue
        data = {"rating": r.get("rating") or r.get("stars") or r.get("star_rating"),
                "body": r.get("text") or r.get("review") or r.get("comment") or r.get("body"),
                "reviewed_at": r.get("date") or r.get("reviewed_at") or r.get("review_date") or r.get("created"),
                "reply": r.get("reply") or r.get("response") or r.get("owner_response"),
                "replied_at": r.get("reply_date") or r.get("replied_at") or r.get("response_date"),
                "author": r.get("author") or r.get("reviewer") or r.get("name")}
        try:
            with db.begin_nested():
                _, c = add_review(db, loc, data, "import")
            created += c
            updated += not c
        except (ValueError, TypeError) as e:
            errors.append({"row": i, "error": str(e)})
    return {"rows": len(rows), "created": created, "updated": updated, "errors": errors[:50]}


CTA_ALIASES = {"book": "BOOK", "order": "ORDER", "shop": "SHOP", "buy": "SHOP", "learn_more": "LEARN_MORE", "learn more": "LEARN_MORE",
               "sign_up": "SIGN_UP", "sign up": "SIGN_UP", "call": "CALL", "call now": "CALL", "get offer": "LEARN_MORE"}


def norm_cta(v) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    if s.upper() in ml.CTA_TYPES:
        return s.upper() or None
    return CTA_ALIASES.get(s.lower(), "LEARN_MORE")


def create_post(db: Session, loc: m.GbpLocation, data: dict, *, status: str, source: str, user_id=None, automation_id=None) -> tuple[m.GbpPost, ml.LocalPostExt]:
    pt = (data.get("post_type") or "update").lower()
    if pt not in ml.POST_TYPES:
        raise ValueError("post_type must be update, offer or event")
    body = (data.get("body") or "").strip()
    if not body:
        raise ValueError("post body is required")
    if len(body) > L.GBP_POST_MAX and status != "published":
        raise ValueError(f"post body exceeds Google's {L.GBP_POST_MAX} character limit")
    if pt == "event" and status != "published" and not (data.get("title") and data.get("start_at")):
        raise ValueError("event posts need a title and a start date")
    p = m.GbpPost(organisation_id=loc.organisation_id, location_id=loc.id, post_type=pt, body=body, status=status,
                  posted_at=L.parse_date(data.get("posted_at")) if status == "published" else None)
    db.add(p)
    db.flush()
    cta_url = data.get("cta_url")
    if cta_url:
        cta_url = _clean_url(cta_url)
    x = ml.LocalPostExt(organisation_id=loc.organisation_id, post_id=p.id, project_id=loc.project_id,
                        title=(data.get("title") or "").strip()[:300] or None, cta_type=norm_cta(data.get("cta_type")), cta_url=cta_url,
                        start_at=L.parse_date(data.get("start_at")), end_at=L.parse_date(data.get("end_at")),
                        offer_code=(data.get("offer_code") or "").strip()[:100] or None, image_ref=(data.get("image_ref") or "").strip()[:1000] or None,
                        source=source, automation_id=automation_id, created_by=user_id, external_name=data.get("external_name"))
    if x.start_at and x.end_at and x.end_at < x.start_at:
        raise ValueError("end date is before start date")
    db.add(x)
    db.flush()
    return p, x


def import_posts(db: Session, project_id, content: bytes, default_location=None) -> dict:
    rows = read_csv(content)
    cache: dict = {}
    created = 0
    errors = []
    for i, r in enumerate(rows, start=2):
        loc = _resolve_location(db, project_id, r, cache, default_location)
        if not loc:
            errors.append({"row": i, "error": "unknown location"})
            continue
        img = r.get("image") or r.get("image_url") or r.get("photo") or r.get("media")
        if img and img.lower() in ("yes", "y", "true", "1"):
            img = "imported:yes"
        elif img and img.lower() in ("no", "n", "false", "0"):
            img = None
        data = {"post_type": (r.get("type") or r.get("post_type") or "update").lower(), "title": r.get("title"),
                "body": r.get("body") or r.get("text") or r.get("summary") or r.get("content"),
                "cta_type": r.get("cta") or r.get("cta_type") or r.get("button"), "cta_url": r.get("url") or r.get("cta_url") or r.get("link"),
                "posted_at": r.get("date") or r.get("posted_at") or r.get("published"), "image_ref": img,
                "start_at": r.get("start") or r.get("start_date"), "end_at": r.get("end") or r.get("end_date")}
        try:
            if not L.parse_date(data["posted_at"]):
                raise ValueError("date (posted date) is required for imported posts")
            with db.begin_nested():
                create_post(db, loc, data, status="published", source="import")
            created += 1
        except (ValueError, TypeError) as e:
            errors.append({"row": i, "error": str(e)})
    return {"rows": len(rows), "created": created, "errors": errors[:50]}


# ================================================================== approvals
def request_approval(db: Session, ctx, entity_type: str, entity_id, title: str) -> m.Approval:
    from ..deps import notify
    a = m.Approval(organisation_id=ctx.org_id, entity_type=entity_type, entity_id=entity_id, title=title[:300],
                   chain=["manager"], requested_by=ctx.user_id if ctx else None)
    db.add(a)
    db.flush()
    notify(db, a.organisation_id, "approval_requested", f"Approval requested: {a.title}", "Awaiting manager", "/approvals")
    return a


def decide_approval(db: Session, ctx, approval_id, decision: str, notes: str | None):
    if not approval_id:
        return
    a = db.get(m.Approval, approval_id)
    if not a or a.status != "pending":
        return
    if a.requested_by == ctx.user_id and ctx.role != "owner":
        raise HTTPException(403, "You cannot approve your own request")
    a.decisions = (a.decisions or []) + [{"by": str(ctx.user_id), "role": ctx.role, "decision": decision, "notes": notes,
                                          "at": utcnow().isoformat(), "step": a.step}]
    if decision == "approve":
        a.step = len(a.chain or [])
        a.status = "approved"
    else:
        a.status = "rejected"
    a.notes = notes or a.notes


def sync_from_approval(db: Session, x) -> None:
    """Reflect decisions made in the generic Approvals inbox onto the reply/post workflow state."""
    if not x.approval_id:
        return
    a = db.get(m.Approval, x.approval_id)
    if not a:
        return
    attr = "reply_status" if isinstance(x, ml.LocalReviewExt) else None
    if attr:
        if x.reply_status == "review" and a.status in ("approved", "rejected"):
            x.reply_status = a.status
    else:
        p = db.get(m.GbpPost, x.post_id)
        if p and p.status == "review" and a.status in ("approved", "rejected"):
            p.status = a.status


# ================================================================== grid
def get_or_create_keyword(db: Session, org_id, project_id, phrase: str) -> m.Keyword:
    from . import entitlements as ent
    phrase = re.sub(r"\s+", " ", phrase.strip().lower())
    if not phrase or len(phrase) > 200:
        raise HTTPException(400, "keyword must be 1–200 characters")
    k = db.scalar(select(m.Keyword).where(m.Keyword.project_id == project_id, m.Keyword.phrase == phrase).limit(1))
    if k:
        return k
    ent.check_count_limit(db, org_id, "rank_keywords", db.scalar(select(func.count()).select_from(m.Keyword)) or 0)
    k = m.Keyword(organisation_id=org_id, project_id=project_id, phrase=phrase, tags=["local-grid"])
    db.add(k)
    db.flush()
    return k


def scan_points(db: Session, scan_id) -> list[m.GridPoint]:
    return list(db.scalars(select(m.GridPoint).where(m.GridPoint.scan_id == scan_id).order_by(m.GridPoint.id)))


def recompute_scan(db: Session, scan: m.GridScan, x: ml.LocalGridScanExt) -> dict:
    pts = scan_points(db, scan.id)
    met = L.grid_metrics([p.position for p in pts], depth=x.depth or 20)
    x.metrics = met
    if met["observed"] == met["points"] and met["points"]:
        scan.status = "COMPLETED"
        x.finished_at = x.finished_at or utcnow()
    elif met["observed"]:
        scan.status = "PARTIAL"
    return met


def parse_rank(v) -> int | None:
    s = str(v if v is not None else "").strip().lower()
    if s == "":
        return None
    if s in ("0", "-", "x", "n/a", "na", "none", "not found", "nf", "20+", "21", ">20", "unranked"):
        return 0
    m_ = re.match(r"^#?(\d+)", s)
    if not m_:
        raise ValueError(f"unrecognised rank: {v}")
    n = int(m_.group(1))
    return n if 1 <= n <= 20 else 0


def import_grid_ranks(db: Session, scan: m.GridScan, x: ml.LocalGridScanExt, rows: list[dict]) -> dict:
    pts = scan_points(db, scan.id)
    n = scan.grid_size
    spacing = float(scan.spacing_km)
    set_, errors = 0, []
    for i, r in enumerate(rows, start=2):
        try:
            rank = parse_rank(r.get("rank") if "rank" in r else r.get("position"))
            target = None
            if r.get("row") not in (None, "") and r.get("col") not in (None, ""):
                rr, cc = int(r["row"]), int(r["col"])
                if not (0 <= rr < n and 0 <= cc < n):
                    raise ValueError(f"row/col outside the {n}×{n} grid (0-based)")
                target = pts[rr * n + cc]
            elif r.get("idx") not in (None, "") or r.get("point") not in (None, ""):
                idx = int(r.get("idx") or r.get("point"))
                if not 0 <= idx < len(pts):
                    raise ValueError("point index outside the grid")
                target = pts[idx]
            elif r.get("lat") not in (None, "") and r.get("lng") not in (None, ""):
                la, lo = float(r["lat"]), float(r["lng"])
                best = min(pts, key=lambda p: L.haversine_km(la, lo, p.lat, p.lng))
                if L.haversine_km(la, lo, best.lat, best.lng) > max(0.05, spacing / 2):
                    raise ValueError("lat/lng is not near any grid point")
                target = best
            else:
                raise ValueError("each row needs row+col, idx, or lat+lng")
            target.position = rank
            set_ += 1
        except (ValueError, TypeError, KeyError) as e:
            errors.append({"row": i, "error": str(e)})
    met = recompute_scan(db, scan, x)
    return {"rows": len(rows), "updated": set_, "errors": errors[:50], "metrics": met}


def scan_dict(db: Session, scan: m.GridScan, x: ml.LocalGridScanExt, points: bool = True) -> dict:
    kw = db.get(m.Keyword, scan.keyword_id)
    loc = db.get(m.GbpLocation, x.location_id)
    d = {"id": str(scan.id), "project_id": str(x.project_id), "location_id": str(x.location_id), "location_name": loc.name if loc else None,
         "keyword": kw.phrase if kw else None, "keyword_id": str(scan.keyword_id), "center_lat": float(scan.center_lat), "center_lng": float(scan.center_lng),
         "grid_size": scan.grid_size, "spacing_km": float(scan.spacing_km), "radius_km": float(x.radius_km), "status": scan.status,
         "mode": x.mode, "depth": x.depth, "metrics": x.metrics or {}, "error": x.error, "credits": x.credits,
         "job_id": str(x.job_id) if x.job_id else None, "created_at": scan.created_at, "finished_at": x.finished_at}
    if points:
        n = scan.grid_size
        d["points"] = [{"idx": i, "row": i // n, "col": i % n, "lat": float(p.lat), "lng": float(p.lng), "position": p.position,
                        "top": (x.point_top or {}).get(str(i), [])} for i, p in enumerate(scan_points(db, scan.id))]
    return d


def live_point(db: Session, org_id, scan: m.GridScan, x: ml.LocalGridScanExt, idx: int) -> int | None:
    """Check one grid point live (Places Text Search biased to the point). Returns the rank (0 = not in results)."""
    from . import local_google as G
    pts = scan_points(db, scan.id)
    p = pts[idx]
    if p.position is not None:
        return p.position
    kw = db.get(m.Keyword, scan.keyword_id)
    loc = db.get(m.GbpLocation, x.location_id)
    radius_m = int(max(500, float(scan.spacing_km) * 1000))
    res = G.places_text_search(db, org_id, kw.phrase, p.lat, p.lng, radius_m)
    p.position = G.find_rank(res[: x.depth or 20], loc.place_id, loc.name)
    top = dict(x.point_top or {})
    top[str(idx)] = [r["name"] for r in res[:3]]
    x.point_top = top
    return p.position


# ================================================================== discovery (Places)
def discover_competitors(db: Session, org_id, project_id, own: m.GbpLocation, keyword: str, lat, lng, radius_km: float, limit: int = 10) -> dict:
    from . import local_google as G
    res = G.places_text_search(db, org_id, keyword, lat, lng, int(radius_km * 1000))
    own_rank = G.find_rank(res, own.place_id, own.name)
    added = []
    for r in res:
        if len(added) >= limit:
            break
        if (own.place_id and r["place_id"] == own.place_id) or L.norm_name(r["name"]) == L.norm_name(own.name):
            continue
        try:
            d = G.place_details(db, org_id, r["place_id"])
        except ih.IntegrationError:
            d = {}
        data = {"name": r["name"], "place_id": r["place_id"], "address": d.get("address") or r.get("address"), "city": d.get("city"),
                "region": d.get("region"), "postcode": d.get("postcode"), "country": d.get("country"), "phone": d.get("phone"),
                "website": d.get("website"), "lat": r.get("lat"), "lng": r.get("lng"), "rating": r.get("rating"),
                "review_count": r.get("review_count"), "photos_count": max(r.get("photos_count") or 0, d.get("photos_count") or 0),
                "primary_category": (d.get("categories") or G.types_to_categories(r.get("types")) or [None])[0],
                "secondary_categories": (d.get("categories") or G.types_to_categories(r.get("types")))[1:],
                "hours": d.get("hours") or {}, "discovered_for": keyword}
        try:
            loc, _ = upsert_location(db, org_id, project_id, "competitor", {k: v for k, v in data.items() if v is not None}, "places")
            added.append({"id": str(loc.id), "name": loc.name, "rank": r["rank"]})
        except ValueError:
            continue
    ih.mark_synced(db, org_id, "google_maps")
    return {"results": len(res), "own_rank": own_rank, "competitors": added}


# ================================================================== GBP sync
def gbp_sync(db: Session, org_id, project_id, resources: list[str] | None = None, progress=None) -> dict:
    from . import local_google as G
    accounts = G.gbp_accounts(db, org_id)
    found = []
    for a in accounts:
        for x in G.gbp_locations(db, org_id, a["name"]):
            mp = G.map_gbp_location(a["name"], x)
            if resources and mp["gbp_resource"] not in resources:
                continue
            found.append(mp)
    found = found[:50]
    out = {"locations": 0, "reviews": 0, "posts": 0}
    for i, mp in enumerate(found):
        data = {k: v for k, v in mp.items() if v not in (None, [], {})}
        data["nap"] = {k: v for k, v in (mp.get("nap") or {}).items() if v is not None}
        loc, _ = upsert_location(db, org_id, project_id, "own", data, "gbp")
        lst = ensure_listing(db, loc)
        out["locations"] += 1
        revs, meta = G.gbp_reviews(db, org_id, mp["gbp_resource"])
        if meta.get("review_count") is not None:
            lst.review_count = int(meta["review_count"])
        if meta.get("rating") is not None:
            lst.rating = float(meta["rating"])
        for r in revs:
            add_review(db, loc, r, "gbp")
            out["reviews"] += 1
        known = {x for x in db.scalars(select(ml.LocalPostExt.external_name).join(m.GbpPost, m.GbpPost.id == ml.LocalPostExt.post_id)
                                       .where(m.GbpPost.location_id == loc.id, ml.LocalPostExt.external_name.isnot(None)))}
        for p in G.gbp_posts(db, org_id, mp["gbp_resource"]):
            if p["external_name"] in known or not p.get("body"):
                continue
            try:
                with db.begin_nested():
                    create_post(db, loc, p, status="published", source="gbp")
                out["posts"] += 1
            except ValueError:
                continue
        if progress:
            progress(int(100 * (i + 1) / max(1, len(found))))
    ih.mark_synced(db, org_id, "gbp")
    return out


# ================================================================== automation
TEMPLATE_VAR = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def location_vars(loc: m.GbpLocation, lst: ml.LocalListing, extra: dict | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    nap = loc.nap or {}
    v = {"location_name": loc.name or "", "business_name": loc.name or "", "city": nap.get("city") or "", "region": nap.get("region") or "",
         "postcode": nap.get("postcode") or "", "phone": nap.get("phone") or "", "website": nap.get("website") or "",
         "address": nap.get("address") or "", "primary_category": lst.primary_category or "",
         "month": now.strftime("%B"), "year": str(now.year), "date": now.date().isoformat()}
    for k, val in (extra or {}).items():
        if re.match(r"^[a-z_][a-z0-9_]*$", str(k)):
            v[k] = str(val)[:500]
    return v


def render_template(tpl: str | None, vars_: dict) -> tuple[str, list[str]]:
    if not tpl:
        return "", []
    missing = []

    def rep(mo):
        k = mo.group(1)
        if k in vars_ and vars_[k] != "":
            return vars_[k]
        missing.append(k)
        return mo.group(0)
    return TEMPLATE_VAR.sub(rep, tpl), sorted(set(missing))


def automation_preview(db: Session, a: ml.LocalAutomation) -> list[dict]:
    out = []
    for lid in a.location_ids or []:
        loc = db.get(m.GbpLocation, uuid.UUID(str(lid)))
        if not loc:
            out.append({"location_id": lid, "error": "location no longer exists"})
            continue
        lst = ensure_listing(db, loc)
        v = location_vars(loc, lst, (a.variables or {}).get(str(lid)))
        title, m1 = render_template(a.title_template, v)
        body, m2 = render_template(a.body_template, v)
        url, m3 = render_template(a.cta_url_template, v)
        out.append({"location_id": str(loc.id), "location_name": loc.name, "title": title or None, "body": body, "cta_url": url or None,
                    "missing_vars": sorted(set(m1 + m2 + m3)), "length": len(body), "too_long": len(body) > L.GBP_POST_MAX})
    return out


def run_automation(db: Session, a: ml.LocalAutomation, ctx_like=None) -> dict:
    if not a.enabled:
        return {"skipped": "automation disabled", "created": 0}
    created, skipped = [], []
    for pv in automation_preview(db, a):
        if pv.get("error") or pv["missing_vars"] or pv["too_long"] or not pv["body"].strip():
            skipped.append({"location_id": pv["location_id"], "reason": pv.get("error") or (f"missing variables: {', '.join(pv['missing_vars'])}"
                            if pv.get("missing_vars") else "post too long" if pv.get("too_long") else "empty body")})
            continue
        loc = db.get(m.GbpLocation, uuid.UUID(pv["location_id"]))
        data = {"post_type": a.post_type, "title": pv["title"], "body": pv["body"], "cta_type": a.cta_type, "cta_url": pv["cta_url"],
                "image_ref": a.image_ref}
        if a.post_type == "event" and not data["title"]:
            data["title"] = a.name
        if a.post_type in ("event", "offer"):
            data["start_at"] = utcnow().isoformat()
            data["end_at"] = (utcnow() + timedelta(days=7 if a.frequency == "weekly" else 30)).isoformat()
        try:
            with db.begin_nested():
                p, x = create_post(db, loc, data, status="review", source="automation", user_id=a.created_by, automation_id=a.id)
                ap = m.Approval(organisation_id=a.organisation_id, entity_type="gbp_post", entity_id=p.id,
                                title=f"GBP post for {loc.name}: {a.name}"[:300], chain=["manager"], requested_by=a.created_by)
                db.add(ap)
                db.flush()
                x.approval_id = ap.id
            created.append(str(p.id))
        except ValueError as e:
            skipped.append({"location_id": pv["location_id"], "reason": str(e)})
    a.runs = (a.runs or 0) + 1
    a.last_run_at = utcnow()
    if created:
        db.add(m.Notification(organisation_id=a.organisation_id, kind="approval_requested",
                              title=f"{len(created)} GBP post(s) from '{a.name}' await approval", link="/local/gmb-posts"))
    return {"created": len(created), "post_ids": created, "skipped": skipped}
