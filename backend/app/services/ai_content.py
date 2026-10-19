"""Content items: state machine, versions, approvals, JSON-LD schema and publish-ready exports.

STATE MACHINE (shared contract — the Social group imports :func:`transition`)::

    draft ──generate──▶ generated ──submit──▶ review ──approve──▶ approved ──publish──▶ published
      ▲  ◀──revise──────────┘     draft ─submit─▶ review     │                 └─schedule─▶ scheduled ─publish─▶ published
      └──────────────reject (notes required)───────────────────┘                                   (unschedule ▶ approved)

``rejected`` is an action, not a stored state: it returns the item to ``draft`` with the reviewer's notes
(kept on content_meta.notes and in the approval row). The same transitions are enforced by the
``content_state_guard`` trigger in Postgres; this module adds permissions, approvals and the audit trail.

Every generated or edited body is a new ``content_version`` row (versions are immutable; restore = new version).
"""
from __future__ import annotations

import difflib
import html
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from .. import models as m
from .. import models_ai as ma
from ..rbac import RANK, can

STATES = ["draft", "generated", "review", "approved", "scheduled", "published"]
# (from, to) -> (action, permission)
TRANSITIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("draft", "generated"): ("generate", "edit"),
    ("generated", "draft"): ("revise", "edit"),
    ("generated", "review"): ("submit", "edit"),
    ("draft", "review"): ("submit", "edit"),
    ("review", "approved"): ("approve", "approve"),
    ("review", "draft"): ("reject", "approve"),
    ("approved", "published"): ("publish", "publish"),
    ("approved", "scheduled"): ("schedule", "publish"),
    ("scheduled", "published"): ("publish", "publish"),
    ("scheduled", "approved"): ("unschedule", "publish"),
}
SYSTEM_ALLOWED = {"publish", "schedule", "unschedule", "generate"}  # actions a background job may take without a user
EDITABLE = {"draft", "generated"}


class TransitionError(HTTPException):
    def __init__(self, status: int, message: str):
        super().__init__(status, message)


def utcnow():
    return datetime.now(timezone.utc)


def _actor(ctx_or_user):
    """(user_id, role) from a deps.Ctx, a models.User, a (user_id, role) tuple, or None (system job)."""
    if ctx_or_user is None:
        return None, None
    if isinstance(ctx_or_user, tuple):
        return ctx_or_user
    if isinstance(ctx_or_user, m.User):
        return ctx_or_user.id, ctx_or_user.role
    return ctx_or_user.user_id, ctx_or_user.role


def meta_of(db, item: m.ContentItem) -> ma.ContentMeta:
    mt = db.get(ma.ContentMeta, item.id)
    if not mt:
        mt = ma.ContentMeta(content_item_id=item.id, organisation_id=item.organisation_id, data={}, current_version=0)
        db.add(mt)
        db.flush()
        last = db.scalar(select(func.max(m.ContentVersion.version)).where(m.ContentVersion.content_item_id == item.id))
        mt.current_version = last or 0
    return mt


def latest_version(db, item) -> m.ContentVersion | None:
    return db.scalar(select(m.ContentVersion).where(m.ContentVersion.content_item_id == item.id)
                     .order_by(m.ContentVersion.version.desc()).limit(1))


def transition(db, ctx_or_user, item: m.ContentItem, to_state: str, note: str | None = None) -> m.ContentItem:
    """Move ``item`` to ``to_state`` (or "rejected" = back to draft with notes). Enforces the allowed
    transitions, the role permission for the action, the approval chain and records the audit trail.

    ``ctx_or_user`` may be a deps.Ctx, a models.User, a (user_id, role) tuple or None for a background job
    (system actors may only publish/schedule/unschedule/generate). Raises TransitionError (an HTTPException:
    403 for permission, 409 for invalid transitions) on failure."""
    user_id, role = _actor(ctx_or_user)
    src = item.state or "draft"
    target = "draft" if to_state == "rejected" else to_state
    if to_state == "rejected" and src != "review":
        raise TransitionError(409, f"Only content in review can be rejected (it is {src})")
    if target not in STATES:
        raise TransitionError(400, f"Unknown state '{to_state}'")
    key = (src, target)
    if key not in TRANSITIONS:
        raise TransitionError(409, f"Invalid content state transition {src} → {target}")
    action, perm = TRANSITIONS[key]
    if role is None:
        if action not in SYSTEM_ALLOWED:
            raise TransitionError(403, f"'{action}' needs a person with the '{perm}' permission")
    elif not can(role, perm):
        raise TransitionError(403, f"Your role ({role}) lacks the '{perm}' permission")
    mt = meta_of(db, item)
    if action == "submit" and not (latest_version(db, item) and (latest_version(db, item).body or "").strip()):
        raise TransitionError(409, "Nothing to review yet — generate or write the content first")
    if action == "reject" and not (note or "").strip():
        raise TransitionError(400, "Rejection requires notes for the writer")
    appr = db.get(m.Approval, mt.approval_id) if mt.approval_id else None
    if action in ("approve", "reject"):
        if appr and appr.status == "pending":
            need = appr.chain[appr.step] if appr.step < len(appr.chain) else "manager"
            if RANK.get(role, 0) < RANK.get(need, 9):
                raise TransitionError(403, f"This approval step needs a {need} or above")
            if appr.requested_by == user_id and role != "owner":
                raise TransitionError(403, "You cannot approve your own submission")
            appr.decisions = (appr.decisions or []) + [{"by": str(user_id), "role": role, "decision": action, "notes": note,
                                                        "at": utcnow().isoformat(), "step": appr.step}]
            if action == "reject":
                appr.status = "rejected"
            else:
                appr.step += 1
                if appr.step < len(appr.chain):
                    _record(db, item, src, src, "approve_step", user_id, note)
                    return item  # more approvers needed; stays in review
                appr.status = "approved"
    if action == "submit":
        appr = m.Approval(organisation_id=item.organisation_id, entity_type="content_item", entity_id=item.id,
                          title=f"Content: {item.title or item.type}"[:300], chain=["manager"], requested_by=user_id, notes=note)
        db.add(appr)
        db.flush()
        mt.approval_id = appr.id
        db.add(m.Notification(organisation_id=item.organisation_id, kind="approval_requested",
                              title=f"Content ready for review: {item.title or item.type}"[:300], body=note, link="/approvals"))
    if action == "reject":
        mt.notes = note
        if appr and appr.requested_by:
            db.add(m.Notification(organisation_id=item.organisation_id, user_id=appr.requested_by, kind="content_rejected",
                                  title=f"Changes requested: {item.title or item.type}"[:300], body=note))
    if action == "publish":
        mt.data = {**(mt.data or {}), "published_at": utcnow().isoformat()}
    item.state = target
    db.flush()  # the DB trigger re-validates the transition here
    _record(db, item, src, target, action, user_id, note)
    return item


def _record(db, item, src, dst, action, user_id, note):
    db.add(ma.ContentTransition(organisation_id=item.organisation_id, content_item_id=item.id, from_state=src, to_state=dst,
                                action=action, by_user=user_id, note=note))
    db.add(m.ActivityLog(organisation_id=item.organisation_id, actor_id=user_id, action=f"content.{action}",
                         target=f"content_item:{item.id}", meta={"from": src, "to": dst, "note": note}))


def sync_approval(db, item: m.ContentItem):
    """Apply a decision taken on the generic Approvals screen to the content item it is about."""
    if item.state != "review":
        return
    mt = db.get(ma.ContentMeta, item.id)
    appr = db.get(m.Approval, mt.approval_id) if mt and mt.approval_id else None
    if not appr or appr.status == "pending":
        return
    last = (appr.decisions or [{}])[-1]
    try:
        by = uuid.UUID(last.get("by")) if last.get("by") else None
    except ValueError:
        by = None
    if appr.status == "approved":
        item.state = "approved"
        db.flush()
        _record(db, item, "review", "approved", "approve", by, last.get("notes"))
    elif appr.status == "rejected":
        item.state = "draft"
        mt.notes = last.get("notes") or appr.notes
        db.flush()
        _record(db, item, "review", "draft", "reject", by, mt.notes)


# ------------------------------------------------------------------ versions
def add_version(db, item: m.ContentItem, body: str, *, author_id=None, origin: str = "manual", model: str | None = None,
                note: str | None = None, data: dict | None = None) -> m.ContentVersion:
    mt = meta_of(db, item)
    n = (db.scalar(select(func.max(m.ContentVersion.version)).where(m.ContentVersion.content_item_id == item.id)) or 0) + 1
    v = m.ContentVersion(organisation_id=item.organisation_id, content_item_id=item.id, version=n, body=body)
    db.add(v)
    db.flush()
    db.add(ma.ContentVersionMeta(content_version_id=v.id, organisation_id=item.organisation_id, author_id=author_id, origin=origin,
                                 model=model, note=note, data=json.loads(json.dumps(data if data is not None else (mt.data or {}), default=str))))
    mt.current_version = n
    return v


def versions(db, item) -> list[dict]:
    out = []
    for v, vm in db.execute(select(m.ContentVersion, ma.ContentVersionMeta).join(ma.ContentVersionMeta, ma.ContentVersionMeta.content_version_id == m.ContentVersion.id, isouter=True)
                            .where(m.ContentVersion.content_item_id == item.id).order_by(m.ContentVersion.version.desc())):
        out.append({"id": str(v.id), "version": v.version, "created_at": v.created_at, "words": len(re.findall(r"\w+", v.body or "")),
                    "chars": len(v.body or ""), "origin": vm.origin if vm else None, "model": vm.model if vm else None,
                    "note": vm.note if vm else None, "author_id": str(vm.author_id) if vm and vm.author_id else None})
    return out


def get_version(db, item, n: int) -> tuple[m.ContentVersion, ma.ContentVersionMeta | None]:
    v = db.scalar(select(m.ContentVersion).where(m.ContentVersion.content_item_id == item.id, m.ContentVersion.version == n))
    if not v:
        raise HTTPException(404, f"Version {n} not found")
    return v, db.get(ma.ContentVersionMeta, v.id)


def diff(a: str, b: str, a_label="a", b_label="b") -> dict:
    al, bl = (a or "").splitlines(), (b or "").splitlines()
    unified = "\n".join(difflib.unified_diff(al, bl, a_label, b_label, lineterm=""))
    ops = []
    sm = difflib.SequenceMatcher(None, al, bl)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops += [{"op": "same", "text": t} for t in al[i1:i2]]
        else:
            ops += [{"op": "del", "text": t} for t in al[i1:i2]]
            ops += [{"op": "add", "text": t} for t in bl[j1:j2]]
    return {"unified": unified, "lines": ops, "added": sum(1 for o in ops if o["op"] == "add"),
            "removed": sum(1 for o in ops if o["op"] == "del"), "similarity": round(sm.ratio(), 3)}


# ------------------------------------------------------------------ parsing AI output
def split_meta(text: str) -> tuple[dict, str]:
    """Pull META_TITLE / META_DESCRIPTION lines off the top of an AI response."""
    seo, rest = {}, []
    for line in (text or "").strip().splitlines():
        mt = re.match(r"^\s*\**(META_TITLE|META_DESCRIPTION)\**\s*:\s*(.+)$", line)
        if mt and len(rest) < 3 and not any(r.strip() for r in rest):
            seo["meta_title" if mt.group(1) == "META_TITLE" else "meta_description"] = mt.group(2).strip().strip('"')
            continue
        rest.append(line)
    body = "\n".join(rest).strip()
    body = re.sub(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", r"\1", body, flags=re.S)
    return seo, body


def extract_json(text: str):
    mt = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text or "", re.S)
    raw = mt.group(1) if mt else None
    if raw is None:
        s, e = (text or "").find("{"), (text or "").rfind("}")
        raw = text[s:e + 1] if s >= 0 and e > s else None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def faqs_from_markdown(md: str) -> list[dict]:
    """Questions/answers under an '## FAQ…' heading (### question + answer paragraphs)."""
    out, in_faq, q, buf = [], False, None, []
    for line in (md or "").splitlines() + ["## END"]:
        h2 = re.match(r"^##\s+(.+)$", line)
        h3 = re.match(r"^###\s+(.+)$", line)
        if h2 and not line.startswith("###"):
            if q and buf:
                out.append({"question": q, "answer": " ".join(buf).strip()})
            q, buf = None, []
            in_faq = bool(re.search(r"\b(faq|frequently asked|questions)\b", h2.group(1), re.I))
            continue
        if in_faq and h3:
            if q and buf:
                out.append({"question": q, "answer": " ".join(buf).strip()})
            q, buf = h3.group(1).strip().strip("*"), []
            continue
        if in_faq and q and line.strip():
            buf.append(re.sub(r"^\s*[-*]\s+", "", line.strip()))
    return [f for f in out if f["answer"]]


def h1_of(md: str) -> str | None:
    mt = re.search(r"^#\s+(.+)$", md or "", re.M)
    return mt.group(1).strip() if mt else None


def plain(md: str) -> str:
    t = re.sub(r"```.*?```", " ", md or "", flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[#>*_`|-]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ------------------------------------------------------------------ JSON-LD
def _org_node(brand: str | None, website: str | None) -> dict:
    n = {"@type": "Organization", "name": brand or "Organisation"}
    if website:
        n["url"] = website
    return n


def article_schema(item: m.ContentItem, body: str, data: dict, profile: dict, kind: str = "BlogPosting") -> list[dict]:
    seo = data.get("seo") or {}
    url = data.get("url") or data.get("target_url")
    brand = profile.get("brand")
    art = {"@context": "https://schema.org", "@type": kind, "headline": (h1_of(body) or item.title or "")[:110],
           "description": seo.get("meta_description") or plain(body)[:155], "author": _org_node(brand, profile.get("website")),
           "publisher": _org_node(brand, profile.get("website")), "inLanguage": data.get("lang") or "en",
           "dateModified": utcnow().date().isoformat(), "wordCount": len(re.findall(r"\w+", plain(body)))}
    if data.get("published_at"):
        art["datePublished"] = str(data["published_at"])[:10]
    kws = [data.get("primary_keyword")] + list(data.get("supporting_keywords") or [])
    kws = [k for k in kws if k]
    if kws:
        art["keywords"] = ", ".join(kws[:12])
    if url:
        art["mainEntityOfPage"] = {"@type": "WebPage", "@id": url}
        art["url"] = url
    if data.get("image_url"):
        art["image"] = data["image_url"]
    out = [art]
    faqs = faqs_from_markdown(body) or [f for f in data.get("faqs") or [] if isinstance(f, dict) and f.get("answer")]
    if faqs:
        out.append(faq_schema(faqs))
    return out


def faq_schema(faqs: list[dict]) -> dict:
    return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": f["question"], "acceptedAnswer": {"@type": "Answer", "text": f["answer"]}}
        for f in faqs if f.get("question") and f.get("answer")]}


def page_schema(data: dict, profile: dict) -> list[dict]:
    """Service / LocalBusiness schema for website & landing pages, chosen by page type and intent."""
    brand = profile.get("brand") or "Business"
    website = profile.get("website")
    loc = data.get("location") or profile.get("location")
    ptype = data.get("page_type") or "landing"
    biz = {"@type": "LocalBusiness" if (ptype == "location" or data.get("intent") == "local" or loc) else "Organization", "name": brand}
    if website:
        biz["url"] = website
    if profile.get("phone"):
        biz["telephone"] = profile["phone"]
    if loc:
        biz["address"] = {"@type": "PostalAddress", "addressLocality": loc}
        biz["areaServed"] = {"@type": "Place", "name": loc}
    out = []
    if ptype == "location":
        lb = {"@context": "https://schema.org", **biz}
        if data.get("service"):
            lb["makesOffer"] = {"@type": "Offer", "itemOffered": {"@type": "Service", "name": data["service"]}}
        out.append(lb)
    else:
        svc = {"@context": "https://schema.org", "@type": "Service", "name": data.get("service") or data.get("h1") or data.get("primary_keyword"),
               "provider": biz, "description": (data.get("seo") or {}).get("meta_description") or data.get("h1")}
        if data.get("primary_keyword"):
            svc["serviceType"] = data["primary_keyword"]
        if loc:
            svc["areaServed"] = {"@type": "Place", "name": loc}
        out.append(svc)
    page = {"@context": "https://schema.org", "@type": "WebPage", "name": (data.get("seo") or {}).get("meta_title") or data.get("h1"),
            "description": (data.get("seo") or {}).get("meta_description")}
    if data.get("url"):
        page["url"] = data["url"]
    out.append(page)
    faqs = [f for f in data.get("faqs") or [] if isinstance(f, dict) and f.get("answer")]
    if faqs:
        out.append(faq_schema(faqs))
    return out


def validate_schema(nodes: list[dict]) -> list[str]:
    """Structural checks of the JSON-LD we emit (required properties per type)."""
    req = {"BlogPosting": ["headline", "author", "publisher"], "NewsArticle": ["headline", "author", "publisher", "dateModified"],
           "Article": ["headline", "author"], "FAQPage": ["mainEntity"], "Service": ["name", "provider"],
           "LocalBusiness": ["name"], "WebPage": ["name"], "HowTo": ["name", "step"], "Report": ["name"]}
    errs = []
    for n in nodes:
        t = n.get("@type")
        if n.get("@context") != "https://schema.org":
            errs.append(f"{t}: missing @context")
        for k in req.get(t, []):
            if not n.get(k):
                errs.append(f"{t}: missing {k}")
        if t == "FAQPage":
            for q in n.get("mainEntity") or []:
                if q.get("@type") != "Question" or not q.get("name") or not (q.get("acceptedAnswer") or {}).get("text"):
                    errs.append("FAQPage: every Question needs a name and an acceptedAnswer.text")
    try:
        json.dumps(nodes)
    except (TypeError, ValueError) as e:
        errs.append(f"not serialisable: {e}")
    return errs


# ------------------------------------------------------------------ exports
def md_to_html(md: str) -> str:
    import markdown
    safe = html.escape(md or "", quote=False)  # no raw HTML from AI/user text survives into the export
    safe = re.sub(r"^&gt;", ">", safe, flags=re.M)  # keep Markdown blockquotes
    return markdown.markdown(safe, extensions=["tables", "fenced_code", "sane_lists"])


def html_document(title: str, body_html: str, seo: dict, schema: list[dict], lang: str = "en", draft: bool = False,
                  canonical: str | None = None) -> str:
    e = html.escape
    ld_parts = []
    for s in schema:
        safe_json = json.dumps(s, ensure_ascii=False).replace("</", "<\\/")
        ld_parts.append(f'<script type="application/ld+json">{safe_json}</script>')
    ld = "\n".join(ld_parts)
    head = [f'<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{e(seo.get('meta_title') or title)}</title>"]
    if seo.get("meta_description"):
        head.append(f'<meta name="description" content="{e(seo["meta_description"])}">')
    if canonical:
        head.append(f'<link rel="canonical" href="{e(canonical)}">')
    if draft:
        head.append('<meta name="robots" content="noindex">')
    banner = "<!-- DRAFT: not approved for publishing -->\n" if draft else ""
    return (f"<!doctype html>\n{banner}<html lang=\"{e(lang)}\">\n<head>\n" + "\n".join(head) + f"\n{ld}\n</head>\n<body>\n<article>\n"
            + body_html + "\n</article>\n</body>\n</html>\n")


def markdown_export(title: str, body: str, seo: dict, schema: list[dict]) -> str:
    fm = ["---", f"title: {json.dumps(seo.get('meta_title') or title, ensure_ascii=False)}"]
    if seo.get("meta_description"):
        fm.append(f"description: {json.dumps(seo['meta_description'], ensure_ascii=False)}")
    if seo.get("slug"):
        fm.append(f"slug: {seo['slug']}")
    fm.append("---")
    ld = "\n\n".join("```json\n" + json.dumps(s, indent=2, ensure_ascii=False) + "\n```" for s in schema)
    return "\n".join(fm) + "\n\n" + (body or "").strip() + ("\n\n<!-- JSON-LD structured data -->\n" + ld if schema else "") + "\n"


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:80] or "page"
