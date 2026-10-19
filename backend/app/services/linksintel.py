"""Links Intel: anchor classification, CSV imports, us-vs-them summaries, orphan detection."""
from __future__ import annotations

import csv
import io
import re
import uuid

from sqlalchemy import func, select

from .. import models_linksintel as li

DR_BUCKETS = ["0-20", "20-40", "40-60", "60+"]
GENERIC_ANCHORS = {
    "click here", "read more", "website", "here", "link", "this page", "this site",
    "learn more", "visit website", "official site", "official website", "homepage",
    "home page", "site", "web page", "source", "more", "info", "details",
}
STOP_WORDS = {"com", "org", "net", "io", "www", "http", "https"}


def dr_bucket(dr) -> str:
    """DR number -> bucket label."""
    try:
        v = float(dr)
    except (TypeError, ValueError):
        return "0-20"
    if v < 20:
        return "0-20"
    if v < 40:
        return "20-40"
    if v < 60:
        return "40-60"
    return "60+"


def _brand_words(brand) -> list[str]:
    """Brand string (or list) -> lowercase word fragments, e.g. 'BrightDental' -> ['brightdental']."""
    if not brand:
        return []
    if isinstance(brand, str):
        brand = [brand]
    words: list[str] = []
    for b in brand:
        for w in re.split(r"[^a-z0-9]+", str(b).lower()):
            if w and w not in STOP_WORDS:
                words.append(w)
    return words


def classify_anchor(anchor: str | None, brand=None, keywords: list[str] | None = None) -> str:
    """Anchor text -> branded / exact / partial / generic (plain rules, plain output)."""
    a = (anchor or "").strip().lower()
    if not a:
        return "generic"
    if a in GENERIC_ANCHORS:
        return "generic"
    kw = {k.strip().lower() for k in (keywords or []) if k.strip()}
    if a in kw:
        return "exact"
    for w in _brand_words(brand):
        if w in a:
            return "branded"
    return "partial"


def _norm_url(u: str) -> str:
    return (u or "").strip().rstrip("/").lower()


def infer_target_type(url: str) -> str:
    u = (url or "").lower()
    if "/blog/" in u or u.rstrip("/").endswith("/blog") or "/news/" in u:
        return "blog"
    if "/product" in u or "/shop/" in u or "/p/" in u:
        return "product"
    if "/service" in u:
        return "service"
    return "other"


def import_backlinks_csv(db, org_id: uuid.UUID, project_id: uuid.UUID, site_label: str,
                         csv_text: str, brand=None, keywords: list[str] | None = None) -> dict:
    """Import a backlink CSV (columns: anchor_text, backlink_url, dr) for one site.

    Replaces this site's previous import (delete + fresh insert) so re-uploads
    never double-count. Rows are aggregated per (anchor_text, dr_bucket).
    """
    rdr = csv.DictReader(io.StringIO(csv_text or ""))
    cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
    if "anchor_text" not in cols:
        raise ValueError("CSV needs an 'anchor_text' column")
    agg: dict[tuple[str, str], int] = {}
    rows = 0
    for r in rdr:
        anchor = (r[cols["anchor_text"]] or "").strip()
        if not anchor:
            continue
        bucket = dr_bucket((r[cols["dr"]] if "dr" in cols else None) or "")
        agg[(anchor, bucket)] = agg.get((anchor, bucket), 0) + 1
        rows += 1
    db.query(li.AnchorStat).filter(
        li.AnchorStat.project_id == project_id,
        li.AnchorStat.site_label == site_label,
    ).delete()
    for (anchor, bucket), count in agg.items():
        db.add(li.AnchorStat(
            organisation_id=org_id, project_id=project_id, site_label=site_label,
            anchor_text=anchor, backlink_count=count, dr_bucket=bucket,
            anchor_type=classify_anchor(anchor, brand, keywords),
        ))
    db.flush()
    return {"imported_rows": rows, "anchors": len(agg)}


def anchor_summary(db, project_id: uuid.UUID) -> dict:
    """Us-vs-them anchor data: rows per anchor (ours first), per-site summary, warnings."""
    stats = db.scalars(
        select(li.AnchorStat).where(li.AnchorStat.project_id == project_id)
    ).all()
    labels = sorted({s.site_label for s in stats}, key=lambda l: (l != "ours", l))
    per_anchor: dict[str, dict] = {}
    for s in stats:
        e = per_anchor.setdefault(s.anchor_text, {
            "anchor_text": s.anchor_text, "anchor_type": s.anchor_type,
            "counts": {}, "buckets": {b: 0 for b in DR_BUCKETS}, "total": 0,
        })
        e["counts"][s.site_label] = e["counts"].get(s.site_label, 0) + s.backlink_count
        e["buckets"][s.dr_bucket] = e["buckets"].get(s.dr_bucket, 0) + s.backlink_count
        e["total"] += s.backlink_count
        e["anchor_type"] = s.anchor_type  # same anchor classifies the same way
    rows = sorted(per_anchor.values(), key=lambda e: -e["total"])
    for e in rows:
        e["ours"] = e["counts"].get("ours", 0)
        e["competitors"] = {l: e["counts"].get(l, 0) for l in labels if l != "ours"}
    summary: dict[str, dict] = {}
    for l in labels:
        mine = [s for s in stats if s.site_label == l]
        total = sum(s.backlink_count for s in mine)
        summary[l] = {
            "total": total,
            "buckets": {b: sum(s.backlink_count for s in mine if s.dr_bucket == b) for b in DR_BUCKETS},
            "types": {t: sum(s.backlink_count for s in mine if s.anchor_type == t)
                      for t in ("branded", "exact", "partial", "generic")},
        }
    warnings = []
    for l in labels:
        tot = summary[l]["total"]
        if not tot:
            continue
        exact_n = summary[l]["types"]["exact"]
        exact_pct = round(exact_n / tot * 100, 1)
        if exact_pct > 30:
            who = "Your site" if l == "ours" else f"Competitor {l}"
            warnings.append({
                "site_label": l,
                "exact_pct": exact_pct,
                "message": (f"{who} uses the exact keyword as anchor text in {exact_pct}% of links "
                            f"({exact_n} of {tot}). Google may treat this as unnatural and penalise it. "
                            "Mix in more branded anchors (your company name) and natural words."),
            })
    return {"sites": labels, "rows": rows, "summary": summary, "warnings": warnings}


def import_internal_links(db, org_id: uuid.UUID, project_id: uuid.UUID, site_label: str,
                          csv_text: str) -> dict:
    """Import internal links CSV (columns: source_url, anchor_text, target_url) for one site."""
    rdr = csv.DictReader(io.StringIO(csv_text or ""))
    cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
    for need in ("source_url", "anchor_text", "target_url"):
        if need not in cols:
            raise ValueError(f"CSV needs a '{need}' column")
    db.query(li.InternalLink).filter(
        li.InternalLink.project_id == project_id,
        li.InternalLink.site_label == site_label,
    ).delete()
    n = 0
    for r in rdr:
        src, tgt = (r[cols["source_url"]] or "").strip(), (r[cols["target_url"]] or "").strip()
        anchor = (r[cols["anchor_text"]] or "").strip()
        if not src or not tgt:
            continue
        db.add(li.InternalLink(
            organisation_id=org_id, project_id=project_id, site_label=site_label,
            source_url=src, anchor_text=anchor, target_url=tgt, target_type=infer_target_type(tgt),
        ))
        n += 1
    db.flush()
    return {"imported_rows": n}


def orphan_pages(db, project_id: uuid.UUID, site_label: str, all_pages: list[str]) -> list[str]:
    """Pages from all_pages that have zero inbound internal links for this site."""
    targets = {t[0] for t in db.execute(
        select(li.InternalLink.target_url).where(
            li.InternalLink.project_id == project_id, li.InternalLink.site_label == site_label)
    ).all()}
    linked = {_norm_url(t) for t in targets}
    return [p for p in all_pages if p.strip() and _norm_url(p) not in linked]


def internal_summary(db, project_id: uuid.UUID, site_label: str | None = None,
                     all_pages: list[str] | None = None) -> dict:
    """Internal-link rows + orphan pages per site (ours first)."""
    q = select(li.InternalLink).where(li.InternalLink.project_id == project_id)
    if site_label:
        q = q.where(li.InternalLink.site_label == site_label)
    links = db.scalars(q.order_by(li.InternalLink.site_label, li.InternalLink.source_url)).all()
    labels = sorted({l.site_label for l in links}, key=lambda l: (l != "ours", l))
    rows = [{
        "site_label": l.site_label, "source_url": l.source_url, "anchor_text": l.anchor_text,
        "target_url": l.target_url, "target_type": l.target_type,
    } for l in links]
    counts: dict[str, dict[str, int]] = {}
    for l in links:
        c = counts.setdefault(l.target_url, {})
        c[l.site_label] = c.get(l.site_label, 0) + 1
    orphans: dict[str, list[str]] = {}
    if all_pages:
        for l in (labels if not site_label else [site_label]):
            orphans[l] = orphan_pages(db, project_id, l, all_pages)
    return {"sites": labels, "rows": rows, "inbound_counts": counts, "orphans": orphans}
