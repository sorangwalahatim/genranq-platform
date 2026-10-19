"""Backlink CRM core (module 13): classification, CSV import/export, KPIs, toxic-score import and the
disavow workflow. Verification lives in backlinks_verify.py, provider ingest in backlinks_providers.py.

Everything here works on data the organisation has (manual entry, CSV) — no external service is needed.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_backlinks as mb
from .crawler import normalise

URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.I)
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.I)
CHECKED = ("live", "removed", "changed", "noindex")  # statuses that come from a conclusive check
TOXIC_THRESHOLD = 60  # Semrush: 60-100 = toxic, 45-59 = potentially toxic
FREQ = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1), "monthly": timedelta(days=30)}


def utcnow():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ url helpers
def clean_url(u: str | None, field: str = "URL") -> str:
    u = (u or "").strip()
    if u and "://" not in u:
        u = "https://" + u
    host = (urlparse(u).hostname or "") if URL_RE.match(u) else ""
    if not host or len(u) > 2000 or not ("." in host or host == "localhost"):
        raise HTTPException(400, f"{field} must be a full http(s) URL")
    try:
        return normalise(u)
    except Exception:
        raise HTTPException(400, f"{field} is not a valid URL")


def host_of(u: str) -> str:
    h = (urlparse(u if "://" in u else "https://" + u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def url_key(u: str) -> str:
    """Scheme/www/trailing-slash-insensitive identity of a URL (used to match links to targets)."""
    p = urlparse(u if "://" in u else "https://" + u)
    path = (p.path or "/").rstrip("/") or "/"
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    return f"{host_of(u)}{port}{path}" + (f"?{p.query}" if p.query else "")


def link_type_from_rel(rel: str | None) -> str:
    toks = set((rel or "").lower().split())
    if "sponsored" in toks:
        return "sponsored"
    if "ugc" in toks:
        return "ugc"
    if "nofollow" in toks:
        return "nofollow"
    return "dofollow"


def num(v) -> float | None:
    if v is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(v).replace(",", ""))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def fnum(v):
    return float(v) if isinstance(v, Decimal) else v


# ------------------------------------------------------------------ CRM helpers
def site_for(db: Session, org_id, project_id, target_url: str, site_id=None) -> m.Site:
    """The project's site the target URL belongs to; created when the project has none for that host."""
    if site_id:
        s = db.get(m.Site, site_id)
        if not s or s.project_id != project_id:
            raise HTTPException(400, "target_site_id is not a site of this project")
        return s
    h = host_of(target_url)
    for s in db.scalars(select(m.Site).where(m.Site.project_id == project_id)):
        if host_of(s.domain) == h:
            return s
    p = urlparse(target_url)
    s = m.Site(organisation_id=org_id, project_id=project_id, domain=f"{p.scheme}://{p.netloc}")
    db.add(s)
    db.flush()
    return s


def ensure_meta(db: Session, bl: m.Backlink) -> mb.BacklinkMeta:
    meta = db.get(mb.BacklinkMeta, bl.id)
    if not meta:
        meta = mb.BacklinkMeta(backlink_id=bl.id, organisation_id=bl.organisation_id, link_scope="project", verify_frequency="weekly",
                               next_check_at=utcnow(), tags=[])
        db.add(meta)
        db.flush()
    return meta


def schedule_next(meta: mb.BacklinkMeta, base: datetime | None = None):
    f = FREQ.get(meta.verify_frequency)
    meta.next_check_at = (base or utcnow()) + f if f else None


def serialize(bl: m.Backlink, meta: mb.BacklinkMeta | None, suppliers: dict, last_check: dict | None = None,
              projects: dict | None = None) -> dict:
    return {
        "id": str(bl.id), "project_id": str(bl.project_id), "project": (projects or {}).get(bl.project_id),
        "target_site_id": str(bl.target_site_id), "supplier_id": str(bl.supplier_id) if bl.supplier_id else None,
        "supplier": suppliers.get(bl.supplier_id), "source_url": bl.source_url, "source_domain": host_of(bl.source_url),
        "target_url": bl.target_url, "anchor_text": bl.anchor_text, "link_type": bl.link_type, "paid_or_free": bl.paid_or_free,
        "status": bl.status, "cost": fnum(bl.cost), "currency": bl.currency, "dr": fnum(bl.dr), "da": fnum(bl.da),
        "authority": fnum(bl.authority), "toxicity_score": fnum(bl.toxicity_score), "first_detected": bl.first_detected,
        "last_verified": bl.last_verified,
        "link_scope": meta.link_scope if meta else "project", "placement_date": meta.placement_date if meta else None,
        "notes": meta.notes if meta else None, "tags": meta.tags if meta else [], "verify_frequency": meta.verify_frequency if meta else "weekly",
        "next_check_at": meta.next_check_at if meta else None, "last_check_note": meta.last_check_note if meta else None,
        "source": meta.source if meta else "manual", "created_at": meta.created_at if meta else None,
        "last_check": last_check,
    }


def validate_fields(d: dict):
    if d.get("link_type") is not None and d["link_type"] not in mb.LINK_TYPES:
        raise HTTPException(400, f"link_type must be one of {mb.LINK_TYPES}")
    if d.get("paid_or_free") is not None and d["paid_or_free"] not in mb.PAID:
        raise HTTPException(400, "paid_or_free must be paid or free")
    if d.get("link_scope") is not None and d["link_scope"] not in mb.SCOPES:
        raise HTTPException(400, f"link_scope must be one of {mb.SCOPES}")
    if d.get("verify_frequency") is not None and d["verify_frequency"] not in mb.FREQUENCIES:
        raise HTTPException(400, f"verify_frequency must be one of {mb.FREQUENCIES}")
    if d.get("status") is not None and d["status"] not in mb.STATUSES:
        raise HTTPException(400, f"status must be one of {mb.STATUSES}")
    if d.get("currency") is not None and d["currency"] and not re.match(r"^[A-Z]{3}$", d["currency"]):
        raise HTTPException(400, "currency must be a 3-letter ISO code (e.g. USD)")
    if d.get("cost") is not None and (d["cost"] < 0 or d["cost"] > 10_000_000):
        raise HTTPException(400, "cost out of range")
    if d.get("paid_or_free") == "free" and d.get("cost"):
        raise HTTPException(400, "A free link can't have a cost")


def create_backlink(db: Session, org_id, user_id, project_id, d: dict, source: str = "manual") -> m.Backlink:
    validate_fields(d)
    src = clean_url(d["source_url"], "source_url")
    tgt = clean_url(d["target_url"], "target_url")
    if url_key(src) == url_key(tgt):
        raise HTTPException(400, "source_url and target_url are the same page")
    site = site_for(db, org_id, project_id, tgt, d.get("target_site_id"))
    if d.get("supplier_id") and not db.get(m.Supplier, d["supplier_id"]):
        raise HTTPException(400, "Unknown supplier")
    paid = d.get("paid_or_free") or ("paid" if d.get("cost") else "free")
    bl = m.Backlink(organisation_id=org_id, project_id=project_id, target_site_id=site.id, supplier_id=d.get("supplier_id"),
                    source_url=src, target_url=tgt, anchor_text=(d.get("anchor_text") or None), link_type=d.get("link_type") or "dofollow",
                    paid_or_free=paid, status=d.get("status") or "pending", cost=d.get("cost"),
                    currency=(d.get("currency") or ("USD" if d.get("cost") else None)), dr=d.get("dr"), da=d.get("da"))
    db.add(bl)
    db.flush()
    meta = mb.BacklinkMeta(backlink_id=bl.id, organisation_id=org_id, link_scope=d.get("link_scope") or "project",
                           placement_date=d.get("placement_date"), notes=d.get("notes"), tags=d.get("tags") or [],
                           verify_frequency=d.get("verify_frequency") or "weekly", next_check_at=utcnow(), source=source, created_by=user_id)
    db.add(meta)
    db.flush()
    return bl


def apply_patch(db: Session, bl: m.Backlink, d: dict):
    validate_fields({**d, "paid_or_free": d.get("paid_or_free", bl.paid_or_free), "cost": d.get("cost", fnum(bl.cost))})
    meta = ensure_meta(db, bl)
    if "source_url" in d:
        bl.source_url = clean_url(d["source_url"], "source_url")
    if "target_url" in d:
        bl.target_url = clean_url(d["target_url"], "target_url")
        bl.target_site_id = site_for(db, bl.organisation_id, bl.project_id, bl.target_url).id
    if "supplier_id" in d and d["supplier_id"] and not db.get(m.Supplier, d["supplier_id"]):
        raise HTTPException(400, "Unknown supplier")
    for k in ("supplier_id", "anchor_text", "link_type", "paid_or_free", "cost", "currency", "dr", "da", "status"):
        if k in d:
            setattr(bl, k, d[k])
    for k in ("link_scope", "placement_date", "notes", "tags", "verify_frequency"):
        if k in d:
            setattr(meta, k, d[k])
    if "verify_frequency" in d:
        schedule_next(meta)
    if "status" in d:
        meta.last_status_change_at = utcnow()


# ------------------------------------------------------------------ CSV import / export
COLS = {
    "source_url": ["sourceurl", "source", "referringpageurl", "referringpage", "urlfrom", "linkingpage", "placementurl", "liveurl", "url"],
    "target_url": ["targeturl", "target", "urlto", "linkurl", "landingpage"],
    "anchor_text": ["anchor", "anchortext", "anchortextused"],
    "link_type": ["linktype", "type", "rel", "follow"],
    "paid_or_free": ["paidorfree", "paidfree", "paid"],
    "cost": ["cost", "price", "amount", "fee"],
    "currency": ["currency", "cur"],
    "placement_date": ["placementdate", "placed", "dateplaced", "publishdate", "date", "firstseen"],
    "supplier": ["supplier", "vendor", "seller", "publisher"],
    "link_scope": ["scope", "linkscope", "category", "linkcategory"],
    "status": ["status"],
    "notes": ["notes", "note", "comments"],
    "dr": ["dr", "domainrating"],
    "da": ["da", "domainauthority"],
}


def _norm(h):
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def decode_csv(content: bytes) -> str:
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return content.decode("utf-16")
    return content.decode("utf-8-sig", errors="replace")


def to_utf8(content: bytes) -> bytes:
    """Ahrefs offers UTF-16 (tab separated) exports for Excel — normalise to UTF-8 for the shared importers."""
    return decode_csv(content).encode("utf-8")


def reader(content: bytes, aliases: dict) -> tuple[csv.DictReader, dict]:
    text = decode_csv(content)
    sample = "\n".join(text.splitlines()[:5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rdr = csv.DictReader(io.StringIO(text), dialect=dialect)
    cols = {}
    for h in rdr.fieldnames or []:
        n = _norm(h)
        for k, al in aliases.items():
            if n in al and k not in cols:
                cols[k] = h
    return rdr, cols


def _date(v) -> date | None:
    s = (v or "").strip()[:19]
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def import_csv(db: Session, org_id, user_id, project_id, content: bytes) -> dict:
    rdr, cols = reader(content, COLS)
    if "source_url" not in cols or "target_url" not in cols:
        raise HTTPException(400, "CSV needs source_url and target_url columns (anchor, link_type, cost, currency, placement_date, supplier optional)")
    existing = {(url_key(b.source_url), url_key(b.target_url)): b for b in
                db.scalars(select(m.Backlink).where(m.Backlink.project_id == project_id))}
    sup_by_name = {s.name.strip().lower(): s for s in db.scalars(select(m.Supplier))}
    created, updated, errors, new_suppliers = 0, 0, [], 0
    for i, r in enumerate(rdr, start=2):
        if i > 20001:
            errors.append({"row": i, "error": "Stopped after 20,000 rows"})
            break
        g = lambda k: (r.get(cols[k]) or "").strip() if k in cols else ""  # noqa: E731
        try:
            d: dict = {"source_url": g("source_url"), "target_url": g("target_url"), "anchor_text": g("anchor_text")[:500] or None,
                       "notes": g("notes")[:2000] or None}
            lt = g("link_type").lower()
            if lt:
                d["link_type"] = lt if lt in mb.LINK_TYPES else ("nofollow" if "no" in lt else "dofollow" if lt in ("follow", "do follow", "true") else None)
                if d["link_type"] is None:
                    raise HTTPException(400, f"unknown link type '{lt}'")
            pf = g("paid_or_free").lower()
            if pf:
                d["paid_or_free"] = "paid" if pf in ("paid", "yes", "true", "1") else "free" if pf in ("free", "no", "false", "0") else None
                if d["paid_or_free"] is None:
                    raise HTTPException(400, f"paid_or_free must be paid/free, got '{pf}'")
            if g("cost"):
                d["cost"] = num(g("cost"))
                if d["cost"] is None:
                    raise HTTPException(400, f"cost '{g('cost')}' is not a number")
            if g("currency"):
                d["currency"] = g("currency").upper()
            if g("placement_date"):
                d["placement_date"] = _date(g("placement_date"))
                if not d["placement_date"]:
                    raise HTTPException(400, f"placement_date '{g('placement_date')}' not understood (use YYYY-MM-DD)")
            if g("link_scope"):
                d["link_scope"] = g("link_scope").lower()
            if g("status"):
                d["status"] = g("status").lower()
            for k in ("dr", "da"):
                if g(k):
                    d[k] = num(g(k))
            sname = g("supplier")
            if sname:
                s = sup_by_name.get(sname.lower())
                if not s:
                    s = m.Supplier(organisation_id=org_id, name=sname[:200], price_list={})
                    db.add(s)
                    db.flush()
                    sup_by_name[sname.lower()] = s
                    new_suppliers += 1
                d["supplier_id"] = s.id
            key = (url_key(clean_url(d["source_url"], "source_url")), url_key(clean_url(d["target_url"], "target_url")))
            with db.begin_nested():
                if key in existing:
                    patch = {k: v for k, v in d.items() if v not in (None, "") and k not in ("source_url", "target_url")}
                    apply_patch(db, existing[key], patch)
                    updated += 1
                else:
                    if d.get("cost") and not d.get("paid_or_free"):
                        d["paid_or_free"] = "paid"
                    existing[key] = create_backlink(db, org_id, user_id, project_id, {k: v for k, v in d.items() if v not in (None, "")}, source="csv")
                    created += 1
        except HTTPException as e:
            errors.append({"row": i, "error": str(e.detail)})
    return {"created": created, "updated": updated, "errors": errors[:200], "error_count": len(errors), "new_suppliers": new_suppliers}


EXPORT_FIELDS = ["source_url", "target_url", "anchor_text", "link_type", "paid_or_free", "link_scope", "status", "cost", "currency",
                 "placement_date", "supplier", "dr", "da", "toxicity_score", "first_detected", "last_verified", "verify_frequency", "notes"]


def export_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_FIELDS)
    for r in rows:
        w.writerow(["" if r.get(k) is None else (r[k].isoformat() if hasattr(r[k], "isoformat") else r[k]) for k in EXPORT_FIELDS])
    return buf.getvalue().encode("utf-8")


# ------------------------------------------------------------------ KPIs (F-019)
def _month_ends(n: int) -> list[date]:
    """Today, then the last day of each previous month (oldest first)."""
    today = date.today()
    out, first = [today], today.replace(day=1)
    for _ in range(n - 1):
        last = first - timedelta(days=1)
        out.append(last)
        first = last.replace(day=1)
    return list(reversed(out))


def kpis(db: Session, project_ids: list, toxic_threshold: float = TOXIC_THRESHOLD, months: int = 12) -> dict:
    bls = db.scalars(select(m.Backlink).where(m.Backlink.project_id.in_(project_ids))).all() if project_ids else []
    metas = {x.backlink_id: x for x in db.scalars(select(mb.BacklinkMeta).where(mb.BacklinkMeta.backlink_id.in_([b.id for b in bls])))} if bls else {}
    sups = {s.id: s.name for s in db.scalars(select(m.Supplier))}
    projects = {p.id: p.name for p in db.scalars(select(m.Project).where(m.Project.id.in_(project_ids)))} if project_ids else {}
    status = Counter(b.status for b in bls)
    checked = sum(status[s] for s in CHECKED)
    live = status["live"]
    toxic = sum(1 for b in bls if b.toxicity_score is not None and float(b.toxicity_score) >= toxic_threshold)
    # spend by supplier (per currency — amounts in different currencies are never summed together)
    spend: dict = defaultdict(lambda: {"spend": 0.0, "links": 0, "live": 0})
    for b in bls:
        if b.paid_or_free == "paid" and b.cost is not None:
            k = (sups.get(b.supplier_id) or "No supplier", b.currency or "USD")
            spend[k]["spend"] += float(b.cost)
            spend[k]["links"] += 1
            spend[k]["live"] += 1 if b.status == "live" else 0
    by_supplier = [{"supplier": s, "currency": c, "spend": round(v["spend"], 2), "links": v["links"], "live_links": v["live"],
                    "cost_per_live_link": round(v["spend"] / v["live"], 2) if v["live"] else None}
                   for (s, c), v in sorted(spend.items(), key=lambda kv: -kv[1]["spend"])]
    cur: dict = defaultdict(lambda: {"spend": 0.0, "live": 0, "paid": 0})
    for b in bls:
        if b.paid_or_free == "paid" and b.cost is not None:
            c = cur[b.currency or "USD"]
            c["spend"] += float(b.cost)
            c["paid"] += 1
            c["live"] += 1 if b.status == "live" else 0
    cost_per_live = [{"currency": k, "spend": round(v["spend"], 2), "paid_links": v["paid"], "live_paid_links": v["live"],
                      "cost_per_live_link": round(v["spend"] / v["live"], 2) if v["live"] else None} for k, v in sorted(cur.items())]
    per_project = Counter(b.project_id for b in bls)
    live_per_project = Counter(b.project_id for b in bls if b.status == "live")
    links_per_project = [{"project_id": str(p), "project": projects.get(p), "links": n, "live": live_per_project[p]}
                         for p, n in per_project.most_common()]
    return {
        "total": len(bls), "by_status": dict(status), "live": live, "removed": status["removed"], "toxic": toxic,
        "checked": checked, "live_rate": round(live / checked, 4) if checked else None,
        "by_link_type": dict(Counter(b.link_type or "unknown" for b in bls)),
        "by_paid": dict(Counter(b.paid_or_free or "unknown" for b in bls)),
        "by_scope": dict(Counter((metas.get(b.id).link_scope if metas.get(b.id) else "project") for b in bls)),
        "spend_by_supplier": by_supplier, "cost_per_live_link": cost_per_live, "links_per_project": links_per_project,
        "retention": retention(db, bls, metas, months), "toxic_threshold": toxic_threshold,
    }


def retention(db: Session, bls: list, metas: dict, months: int = 12) -> list[dict]:
    """At each month end: of the links placed by then that had a conclusive check, the share whose most recent
    check on or before that day found them live."""
    if not bls:
        return []
    ids = [b.id for b in bls]
    hist = defaultdict(list)
    for e in db.scalars(select(mb.BacklinkCheckEvidence).where(mb.BacklinkCheckEvidence.backlink_id.in_(ids),
                                                               mb.BacklinkCheckEvidence.status.in_(CHECKED))
                        .order_by(mb.BacklinkCheckEvidence.checked_at)):
        hist[e.backlink_id].append((e.checked_at.date(), e.status))
    out = []
    for end in _month_ends(months):
        placed = checked = live = 0
        for b in bls:
            meta = metas.get(b.id)
            start = (meta.placement_date if meta and meta.placement_date else None) or (meta.created_at.date() if meta and meta.created_at else None)
            if start and start > end:
                continue
            placed += 1
            last = None
            for d, st in hist.get(b.id, []):
                if d <= end:
                    last = st
                else:
                    break
            if last:
                checked += 1
                live += last == "live"
        out.append({"month": end.strftime("%Y-%m"), "placed": placed, "checked": checked, "live": live,
                    "retention": round(live / checked, 4) if checked else None})
    return out


# ------------------------------------------------------------------ toxic scores (F-016) — advisory only
TOXIC_COLS = {
    "source_url": ["sourceurl", "source", "referringpageurl", "url", "sourcepage", "fromurl"],
    "target_url": ["targeturl", "target", "tourl"],
    "anchor": ["anchor", "anchortext"],
    "score": ["toxicityscore", "toxicscore", "tox", "toxicity", "toxscore", "score"],
    "markers": ["toxicmarkers", "markers"],
}


def import_toxic_csv(db: Session, org_id, project_id, content: bytes, source: str = "semrush_csv") -> dict:
    rdr, cols = reader(content, TOXIC_COLS)
    if "source_url" not in cols or "score" not in cols:
        raise HTTPException(400, "Toxic export needs Source url and Toxicity Score columns (Semrush Backlink Audit → Export)")
    by_src = defaultdict(list)
    for b in db.scalars(select(m.Backlink).where(m.Backlink.project_id == project_id)):
        by_src[url_key(b.source_url)].append(b)
    db.execute(mb.BacklinkToxic.__table__.delete().where(mb.BacklinkToxic.project_id == project_id, mb.BacklinkToxic.source == source))
    n, matched, skipped = 0, 0, 0
    for r in rdr:
        src = (r.get(cols["source_url"]) or "").strip()
        score = num(r.get(cols["score"]))
        if not src or score is None or not (0 <= score <= 100):
            skipped += 1
            continue
        if "://" not in src:
            src = "http://" + src
        tgt = (r.get(cols["target_url"]) or "").strip() if "target_url" in cols else None
        cands = by_src.get(url_key(src), [])
        if tgt and len(cands) > 1:
            cands = [b for b in cands if url_key(b.target_url) == url_key(tgt)] or cands
        bl = cands[0] if cands else None
        for b in cands:
            b.toxicity_score = score
        matched += 1 if cands else 0
        db.add(mb.BacklinkToxic(organisation_id=org_id, project_id=project_id, backlink_id=bl.id if bl else None, source_url=src[:2000],
                                source_domain=host_of(src), target_url=(tgt or None), score=score,
                                anchor=((r.get(cols["anchor"]) or "").strip()[:500] or None) if "anchor" in cols else None,
                                markers=((r.get(cols["markers"]) or "").strip()[:1000] or None) if "markers" in cols else None, source=source))
        n += 1
    if not n:
        raise HTTPException(400, "No usable rows (need a source URL and a 0-100 toxicity score per row)")
    return {"rows": n, "matched_backlinks": matched, "unmatched": n - matched, "skipped": skipped,
            "advisory": "Toxic scores are advisory. Nothing is disavowed without a manager's approval."}


# ------------------------------------------------------------------ disavow
def clean_entry(e: dict) -> dict:
    t, v = (e.get("type") or "domain").strip(), (e.get("value") or "").strip().lower()
    if t == "domain":
        v = v.removeprefix("domain:")
        v = host_of(v) if "/" in v or ":" in v else (v[4:] if v.startswith("www.") else v)
        if not DOMAIN_RE.match(v):
            raise HTTPException(400, f"Invalid domain for disavow: {e.get('value')}")
    elif t == "url":
        v = clean_url(e.get("value"), "disavow URL")
    else:
        raise HTTPException(400, "entry type must be domain or url")
    return {"type": t, "value": v, "reason": (e.get("reason") or "")[:300] or None, "score": num(e.get("score"))}


def disavow_text(dl: mb.DisavowList, project: m.Project, approver: str | None) -> bytes:
    lines = [f"# Disavow file — {project.name}", f"# {dl.title}",
             f"# Generated by Genranq on {date.today().isoformat()} after approval{(' by ' + approver) if approver else ''}.",
             "# Upload at https://search.google.com/search-console/disavow-links — Google treats this as a suggestion.", ""]
    doms = sorted({e["value"] for e in dl.entries if e["type"] == "domain"})
    urls = sorted({e["value"] for e in dl.entries if e["type"] == "url" and host_of(e["value"]) not in doms})
    if doms:
        lines.append("# Domains")
        lines += [f"domain:{d}" for d in doms]
    if urls:
        lines.append("# Individual URLs")
        lines += urls
    return ("\n".join(lines) + "\n").encode("utf-8")


def safe_filename(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "file").strip())[:120].strip("._") or "file"
    return n


def uuid_or_none(v):
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        raise HTTPException(400, f"Invalid id: {v}")
