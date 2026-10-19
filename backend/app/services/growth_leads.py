"""Leads (41), attribution (42) and profiling (66) domain logic.

* capture(): single entry point for every lead source (forms, webhooks, CSV, bookings, HubSpot, free tools)
  with dedupe on normalised e-mail and phone; fuzzy name/company similarity flags possible duplicates.
* touchpoints are stored per lead; attribution() applies first-touch / last-touch / linear models.
* score_answers(): questionnaire scoring rules → lead score + segment.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_growth as g
from ..deps import notify
from .growth_common import domain_of, money, utcnow

STATUSES = ["new", "contacted", "qualified", "won", "lost"]
LEAD_FIELDS = ["email", "phone", "name", "first_name", "last_name", "company", "value", "status", "notes",
               "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "referrer", "page_url"]
SEARCH_HOSTS = ("google.", "bing.com", "duckduckgo.com", "yahoo.", "baidu.com", "yandex.", "ecosia.org")
SOCIAL_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "t.co", "twitter.com", "x.com", "youtube.com",
                "tiktok.com", "pinterest.com", "reddit.com", "lnkd.in")


# ------------------------------------------------------------------ normalisation
def norm_email(e: str | None) -> str | None:
    if not e:
        return None
    e = e.strip().lower()
    if "@" not in e:
        return None
    local, _, dom = e.rpartition("@")
    local = local.split("+", 1)[0]
    if dom in ("gmail.com", "googlemail.com"):
        local, dom = local.replace(".", ""), "gmail.com"
    return f"{local}@{dom}" if local and dom else None


def phone_key(p: str | None) -> str | None:
    """Digits only; international prefix '00' and a national trunk '0' are dropped; the last 9 digits
    form the match key so '+44 7700 900123', '07700900123' and '0044 7700-900-123' collide."""
    if not p:
        return None
    d = re.sub(r"\D", "", p)
    if d.startswith("00"):
        d = d[2:]
    if len(d) < 7:
        return None
    return d[-9:]


def _norm_text(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(inc|ltd|llc|limited|pty|gmbh|co|corp|corporation|plc)\b\.?", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def similarity(a_name, a_company, b_name, b_company) -> float:
    an, bn = _norm_text(a_name), _norm_text(b_name)
    if not an or not bn:
        return 0.0
    ns = SequenceMatcher(None, an, bn).ratio()
    ac, bc = _norm_text(a_company), _norm_text(b_company)
    if ac and bc:
        cs = SequenceMatcher(None, ac, bc).ratio()
        return round(0.6 * ns + 0.4 * cs, 3)
    return round(ns * 0.9, 3)  # name only is weaker evidence


FUZZY_THRESHOLD = 0.85


# ------------------------------------------------------------------ touch classification
def classify(touch: dict) -> dict:
    """UTM parameters win; otherwise infer source/medium from the referrer (search → organic, social → social,
    other host → referral, none → direct)."""
    t = {k: (str(v).strip()[:300] if v not in (None, "") else None) for k, v in (touch or {}).items()}
    landing = t.get("page_url") or t.get("landing_url")
    if landing and not t.get("utm_source"):
        q = parse_qs(urlparse(landing).query)
        for k in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
            if q.get(k) and not t.get(k):
                t[k] = q[k][0][:300]
        if q.get("gclid") and not t.get("utm_source"):
            t["utm_source"], t["utm_medium"] = "google", "cpc"
        if q.get("fbclid") and not t.get("utm_source"):
            t["utm_source"], t["utm_medium"] = "facebook", "paid_social"
    ref = t.get("referrer")
    src, med = t.get("utm_source"), t.get("utm_medium")
    if not src:
        host = domain_of(ref) if ref else ""
        own = domain_of(landing) if landing else ""
        if not host or host == own:
            src, med = "(direct)", med or "(none)"
        elif any(h in host for h in SEARCH_HOSTS):
            src, med = host.split(".")[0] if not host.startswith("google.") else "google", med or "organic"
        elif any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS):
            src, med = host, med or "social"
        else:
            src, med = host, med or "referral"
    return {"source": src.lower(), "medium": (med or "(none)").lower(), "campaign": t.get("utm_campaign"),
            "term": t.get("utm_term"), "content": t.get("utm_content"), "referrer": ref, "landing_url": landing}


# ------------------------------------------------------------------ capture with dedupe
def activity(db: Session, org_id, lead_id, kind: str, summary: str, data: dict | None = None, actor_id=None):
    db.add(g.GrowthLeadActivity(organisation_id=org_id, lead_id=lead_id, kind=kind, summary=summary[:500],
                                data=data or {}, actor_id=actor_id))


def ext_of(db: Session, lead: m.Lead) -> g.GrowthLead:
    x = db.scalar(select(g.GrowthLead).where(g.GrowthLead.lead_id == lead.id))
    if not x:
        x = g.GrowthLead(organisation_id=lead.organisation_id, lead_id=lead.id, email_norm=norm_email(lead.email),
                         phone_key=phone_key(lead.phone))
        db.add(x)
        db.flush()
    return x


def find_duplicate(db: Session, project_id, email_n: str | None, phone_k: str | None, exclude=None) -> m.Lead | None:
    for col, val in ((g.GrowthLead.email_norm, email_n), (g.GrowthLead.phone_key, phone_k)):
        if not val:
            continue
        q = (select(m.Lead).join(g.GrowthLead, g.GrowthLead.lead_id == m.Lead.id)
             .where(m.Lead.project_id == project_id, col == val, g.GrowthLead.merged_into.is_(None))
             .order_by(m.Lead.created_at))
        if exclude:
            q = q.where(m.Lead.id != exclude)
        hit = db.scalars(q.limit(1)).first()
        if hit:
            return hit
    return None


def fuzzy_match(db: Session, project_id, name, company, exclude=None, limit: int = 2000):
    if not _norm_text(name):
        return None, 0.0
    q = (select(m.Lead.id, g.GrowthLead.name, g.GrowthLead.company).join(g.GrowthLead, g.GrowthLead.lead_id == m.Lead.id)
         .where(m.Lead.project_id == project_id, g.GrowthLead.merged_into.is_(None)).order_by(m.Lead.created_at.desc()).limit(limit))
    best, best_s = None, 0.0
    for lid, n, c in db.execute(q):
        if lid == exclude:
            continue
        s = similarity(name, company, n, c)
        if s > best_s:
            best, best_s = lid, s
    return (best, best_s) if best_s >= FUZZY_THRESHOLD else (None, best_s)


def _clean(data: dict) -> dict:
    d = {k: (v.strip() if isinstance(v, str) else v) for k, v in (data or {}).items() if v not in (None, "")}
    if not d.get("name") and (d.get("first_name") or d.get("last_name")):
        d["name"] = " ".join(x for x in (d.get("first_name"), d.get("last_name")) if x)
    for k in ("email", "phone", "name", "company"):
        if k in d:
            d[k] = str(d[k])[:300]
    if "value" in d:
        try:
            d["value"] = money(d["value"])
        except Exception:  # noqa: BLE001
            d.pop("value")
    return d


def capture(db: Session, org_id, project_id, data: dict, *, source: str, kind: str, form_id=None,
            touch: dict | None = None, actor_id=None, ref: str | None = None, occurred_at=None) -> tuple[m.Lead, bool]:
    """Create or update a lead. Returns (lead, created). Exact dedupe on email/phone within the project."""
    d = _clean(data)
    en, pk = norm_email(d.get("email")), phone_key(d.get("phone"))
    if not en and not pk and not d.get("name"):
        raise HTTPException(400, "A lead needs at least an email, phone or name")
    lead = find_duplicate(db, project_id, en, pk)
    created = lead is None
    if created:
        lead = m.Lead(organisation_id=org_id, project_id=project_id, form_id=form_id, email=d.get("email"),
                      phone=d.get("phone"), status=d.get("status") if d.get("status") in STATUSES else "new")
        db.add(lead)
        db.flush()
        x = g.GrowthLead(organisation_id=org_id, lead_id=lead.id, name=d.get("name"), company=d.get("company"),
                         email_norm=en, phone_key=pk, value=d.get("value"), source=source,
                         custom={k: v for k, v in d.items() if k not in LEAD_FIELDS and isinstance(v, (str, int, float, bool))})
        db.add(x)
        db.flush()
        dup, s = fuzzy_match(db, project_id, d.get("name"), d.get("company"), exclude=lead.id)
        if dup:
            x.possible_duplicate_of = dup
        activity(db, org_id, lead.id, "created", f"Lead captured via {source}", {"kind": kind, "ref": ref}, actor_id)
        if dup:
            activity(db, org_id, lead.id, "possible_duplicate", f"Possible duplicate (similarity {s:.2f})", {"of": str(dup)}, actor_id)
    else:
        x = ext_of(db, lead)
        filled = []
        for k in ("email", "phone"):
            if d.get(k) and not getattr(lead, k):
                setattr(lead, k, d[k])
                filled.append(k)
        for k in ("name", "company", "value"):
            if d.get(k) is not None and not getattr(x, k):
                setattr(x, k, d[k])
                filled.append(k)
        if en and not x.email_norm:
            x.email_norm = en
        if pk and not x.phone_key:
            x.phone_key = pk
        extra = {k: v for k, v in d.items() if k not in LEAD_FIELDS and isinstance(v, (str, int, float, bool))}
        if extra:
            x.custom = {**(x.custom or {}), **extra}
        if form_id and not lead.form_id:
            lead.form_id = form_id
        x.updated_at = utcnow()
        activity(db, org_id, lead.id, "deduplicated", f"Matched existing lead from {source}",
                 {"kind": kind, "filled": filled, "ref": ref}, actor_id)
    if touch is not None:
        add_touchpoint(db, lead, kind, touch, ref=ref, occurred_at=occurred_at)
    if d.get("notes"):
        db.add(g.GrowthLeadNote(organisation_id=org_id, lead_id=lead.id, author_id=actor_id, body=str(d["notes"])[:5000]))
    return lead, created


def add_touchpoint(db: Session, lead: m.Lead, kind: str, touch: dict, ref: str | None = None, occurred_at=None) -> g.GrowthTouchpoint:
    c = classify(touch)
    tp = g.GrowthTouchpoint(organisation_id=lead.organisation_id, lead_id=lead.id, project_id=lead.project_id, kind=kind,
                            source=c["source"], medium=c["medium"], campaign=c["campaign"], term=c["term"], content=c["content"],
                            referrer=c["referrer"], landing_url=c["landing_url"], ref=ref, occurred_at=occurred_at or utcnow())
    db.add(tp)
    x = ext_of(db, lead)
    if not lead.utm_source:  # spec columns hold first-touch attribution
        lead.utm_source, lead.utm_medium, lead.utm_campaign = c["source"], c["medium"], c["campaign"]
        x.referrer, x.landing_url = c["referrer"], c["landing_url"]
    return tp


def set_status(db: Session, lead: m.Lead, status: str, actor_id=None, lost_reason: str | None = None):
    if status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    x = ext_of(db, lead)
    old = lead.status
    if old == status:
        return
    lead.status = status
    x.updated_at = utcnow()
    if status == "won":
        x.won_at = utcnow()
    elif old == "won":
        x.won_at = None
    if status == "lost":
        x.lost_reason = lost_reason
    activity(db, lead.organisation_id, lead.id, "status", f"Status {old} → {status}",
             {"from": old, "to": status, "lost_reason": lost_reason}, actor_id)


def assign(db: Session, lead: m.Lead, user_id, actor_id=None):
    x = ext_of(db, lead)
    if user_id:
        u = db.get(m.User, user_id)
        if not u or u.role == "client" or not u.is_active:
            raise HTTPException(400, "Assignee must be an active, non-client user in your organisation")
        x.assignee_id = u.id
        activity(db, lead.organisation_id, lead.id, "assigned", f"Assigned to {u.name or u.email}", {"user_id": str(u.id)}, actor_id)
        if u.id != actor_id:
            notify(db, lead.organisation_id, "lead_assigned", f"New lead assigned: {lead_label(db, lead, x)}", None,
                   f"/leads?lead={lead.id}", user_id=u.id)
    else:
        x.assignee_id = None
        activity(db, lead.organisation_id, lead.id, "assigned", "Unassigned", {}, actor_id)
    x.updated_at = utcnow()


def lead_label(db, lead: m.Lead, x: g.GrowthLead | None = None) -> str:
    x = x or ext_of(db, lead)
    return x.name or lead.email or lead.phone or str(lead.id)[:8]


def merge(db: Session, src: m.Lead, dst: m.Lead, actor_id=None):
    if src.id == dst.id:
        raise HTTPException(400, "Cannot merge a lead into itself")
    if src.project_id != dst.project_id:
        raise HTTPException(400, "Leads must be in the same project to merge")
    sx, dx = ext_of(db, src), ext_of(db, dst)
    for k in ("email", "phone", "utm_source", "utm_medium", "utm_campaign", "crm_contact_id"):
        if getattr(src, k) and not getattr(dst, k):
            setattr(dst, k, getattr(src, k))
    for k in ("name", "company", "value", "assignee_id", "referrer", "landing_url", "segment"):
        if getattr(sx, k) and not getattr(dx, k):
            setattr(dx, k, getattr(sx, k))
    dx.score = max(dx.score or 0, sx.score or 0)
    dx.custom = {**(sx.custom or {}), **(dx.custom or {})}
    dx.email_norm = dx.email_norm or sx.email_norm
    dx.phone_key = dx.phone_key or sx.phone_key
    for model in (g.GrowthTouchpoint, g.GrowthLeadNote, g.GrowthLeadActivity, m.FormSubmission, g.GrowthBooking,
                  g.GrowthQuestionnaireResponse, g.GrowthConnectorDelivery):
        db.execute(update(model).where(model.lead_id == src.id).values(lead_id=dst.id))
    db.execute(update(g.GrowthLead).where(g.GrowthLead.possible_duplicate_of == src.id).values(possible_duplicate_of=None))
    if dx.possible_duplicate_of == src.id:
        dx.possible_duplicate_of = None
    dx.updated_at = utcnow()
    activity(db, dst.organisation_id, dst.id, "merged", f"Merged lead {lead_label(db, src, sx)} into this lead",
             {"merged_lead_id": str(src.id)}, actor_id)
    db.flush()
    db.delete(src)


def lead_dict(lead: m.Lead, x: g.GrowthLead | None, users: dict | None = None) -> dict:
    users = users or {}
    return {"id": str(lead.id), "project_id": str(lead.project_id), "form_id": str(lead.form_id) if lead.form_id else None,
            "email": lead.email, "phone": lead.phone, "status": lead.status, "utm_source": lead.utm_source,
            "utm_medium": lead.utm_medium, "utm_campaign": lead.utm_campaign, "crm_contact_id": lead.crm_contact_id,
            "created_at": lead.created_at,
            "name": x.name if x else None, "company": x.company if x else None,
            "assignee_id": str(x.assignee_id) if x and x.assignee_id else None,
            "assignee": users.get(x.assignee_id) if x and x.assignee_id else None,
            "score": x.score if x else 0, "segment": x.segment if x else None,
            "value": float(x.value) if x and x.value is not None else None, "source": x.source if x else None,
            "referrer": x.referrer if x else None, "landing_url": x.landing_url if x else None,
            "custom": x.custom if x else {}, "possible_duplicate_of": str(x.possible_duplicate_of) if x and x.possible_duplicate_of else None,
            "lost_reason": x.lost_reason if x else None, "won_at": x.won_at if x else None,
            "updated_at": x.updated_at if x else lead.created_at, "crm_synced_at": x.crm_synced_at if x else None}


# ------------------------------------------------------------------ attribution models
MODELS = ["first_touch", "last_touch", "linear"]
DIMENSIONS = {"source": ("source",), "medium": ("medium",), "campaign": ("campaign",),
              "source_medium": ("source", "medium"), "channel": ("kind",)}


def weights(n: int, model: str) -> list[float]:
    if n <= 0:
        return []
    if model == "first_touch":
        return [1.0] + [0.0] * (n - 1)
    if model == "last_touch":
        return [0.0] * (n - 1) + [1.0]
    if model == "linear":
        return [1.0 / n] * n
    raise HTTPException(400, f"model must be one of {MODELS}")


def attribute(leads: list[dict], touchpoints: dict, model: str, dimension: str) -> list[dict]:
    """Pure attribution math. leads: [{id, status, value}], touchpoints: lead_id → [tp dict sorted by time]."""
    if dimension not in DIMENSIONS:
        raise HTTPException(400, f"dimension must be one of {list(DIMENSIONS)}")
    keys = DIMENSIONS[dimension]
    agg: dict[str, dict] = defaultdict(lambda: {"leads": 0.0, "conversions": 0.0, "value": 0.0, "touchpoints": 0})
    for ld in leads:
        tps = touchpoints.get(ld["id"]) or []
        if not tps:
            tps = [{"source": "(unattributed)", "medium": "(none)", "campaign": None, "kind": "none"}]
        w = weights(len(tps), model)
        won = ld["status"] == "won"
        val = float(ld.get("value") or 0) if won else 0.0
        for tp, wt in zip(tps, w):
            key = " / ".join(str(tp.get(k) or "(not set)") for k in keys)
            a = agg[key]
            a["touchpoints"] += 1
            a["leads"] += wt
            if won:
                a["conversions"] += wt
                a["value"] += wt * val
    out = []
    for k, a in agg.items():
        out.append({"key": k, "leads": round(a["leads"], 4), "conversions": round(a["conversions"], 4),
                    "value": round(a["value"], 2), "touchpoints": a["touchpoints"],
                    "conversion_rate": round(a["conversions"] / a["leads"], 4) if a["leads"] else None})
    return sorted(out, key=lambda r: (-r["value"], -r["conversions"], -r["leads"], r["key"]))


def attribution(db: Session, project_ids, model: str, dimension: str, since=None, until=None) -> dict:
    q = select(m.Lead, g.GrowthLead).outerjoin(g.GrowthLead, g.GrowthLead.lead_id == m.Lead.id).where(m.Lead.project_id.in_(project_ids))
    if since:
        q = q.where(m.Lead.created_at >= since)
    if until:
        q = q.where(m.Lead.created_at < until)
    leads = [{"id": l.id, "status": l.status, "value": float(x.value) if x and x.value is not None else 0} for l, x in db.execute(q)]
    tps: dict = defaultdict(list)
    if leads:
        ids = [l["id"] for l in leads]
        for i in range(0, len(ids), 5000):
            for tp in db.scalars(select(g.GrowthTouchpoint).where(g.GrowthTouchpoint.lead_id.in_(ids[i:i + 5000]))
                                 .order_by(g.GrowthTouchpoint.occurred_at, g.GrowthTouchpoint.id)):
                tps[tp.lead_id].append({"source": tp.source, "medium": tp.medium, "campaign": tp.campaign, "kind": tp.kind})
    rows = attribute(leads, tps, model, dimension)
    won = [l for l in leads if l["status"] == "won"]
    return {"model": model, "dimension": dimension, "rows": rows,
            "totals": {"leads": len(leads), "conversions": len(won), "value": round(sum(l["value"] for l in won), 2),
                       "touchpoints": sum(len(v) for v in tps.values())}}


# ------------------------------------------------------------------ questionnaire scoring (66)
Q_TYPES = ["single", "multi", "text", "number", "email"]
Q_CATEGORIES = ["who", "what", "source", "budget", "timeline", "other"]
RULE_OPS = ["eq", "neq", "in", "contains", "gte", "lte", "answered"]


def validate_questionnaire(questions: list, rules: list, segments: list):
    keys = set()
    for q in questions:
        k = str(q.get("key", ""))
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", k) or k in keys:
            raise HTTPException(400, f"question key '{k}' must be unique, lowercase a-z0-9_")
        keys.add(k)
        if q.get("type") not in Q_TYPES:
            raise HTTPException(400, f"question type must be one of {Q_TYPES}")
        if q.get("category", "other") not in Q_CATEGORIES:
            raise HTTPException(400, f"question category must be one of {Q_CATEGORIES}")
        if not str(q.get("label", "")).strip():
            raise HTTPException(400, "every question needs a label")
        if q["type"] in ("single", "multi"):
            opts = q.get("options") or []
            if not opts:
                raise HTTPException(400, f"question '{k}' needs options")
            for o in opts:
                if not str(o.get("value", "")).strip():
                    raise HTTPException(400, f"question '{k}' has an option without a value")
                if not isinstance(o.get("points", 0), (int, float)) or abs(o.get("points", 0)) > 1000:
                    raise HTTPException(400, "option points must be a number between -1000 and 1000")
    for r in rules:
        if r.get("question") not in keys:
            raise HTTPException(400, f"rule references unknown question '{r.get('question')}'")
        if r.get("op") not in RULE_OPS:
            raise HTTPException(400, f"rule op must be one of {RULE_OPS}")
        if not isinstance(r.get("points"), (int, float)) or abs(r["points"]) > 1000:
            raise HTTPException(400, "rule points must be a number between -1000 and 1000")
    for s in segments:
        if not str(s.get("name", "")).strip() or not isinstance(s.get("min_score"), (int, float)):
            raise HTTPException(400, "segments need a name and a numeric min_score")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rule_hit(op: str, ans, val) -> bool:
    if op == "answered":
        return ans not in (None, "", [])
    if ans in (None, "", []):
        return False
    vals = ans if isinstance(ans, list) else [ans]
    if op == "eq":
        return any(str(a).lower() == str(val).lower() for a in vals)
    if op == "neq":
        return all(str(a).lower() != str(val).lower() for a in vals)
    if op == "in":
        opts = {str(x).lower() for x in (val if isinstance(val, list) else str(val).split(","))}
        return any(str(a).lower().strip() in opts for a in vals)
    if op == "contains":
        return any(str(val).lower() in str(a).lower() for a in vals)
    if op in ("gte", "lte"):
        a, v = _num(vals[0]), _num(val)
        if a is None or v is None:
            return False
        return a >= v if op == "gte" else a <= v
    return False


def score_answers(qn: g.GrowthQuestionnaire, answers: dict) -> dict:
    total, breakdown, missing = 0, [], []
    for q in qn.questions:
        ans = answers.get(q["key"])
        if q.get("required") and ans in (None, "", []):
            missing.append(q["key"])
        if q["type"] in ("single", "multi") and ans not in (None, "", []):
            chosen = ans if isinstance(ans, list) else [ans]
            if q["type"] == "single" and len(chosen) > 1:
                raise HTTPException(400, f"'{q['key']}' accepts a single answer")
            valid = {str(o["value"]): o for o in q["options"]}
            for c in chosen:
                o = valid.get(str(c))
                if not o:
                    raise HTTPException(400, f"'{c}' is not an option of '{q['key']}'")
                pts = o.get("points", 0) or 0
                if pts:
                    total += pts
                    breakdown.append({"question": q["key"], "reason": f"answered '{o.get('label') or o['value']}'", "points": pts})
    for r in qn.rules:
        if _rule_hit(r["op"], answers.get(r["question"]), r.get("value")):
            total += r["points"]
            breakdown.append({"question": r["question"], "reason": f"rule {r['op']} {r.get('value', '')}".strip(), "points": r["points"]})
    total = int(round(total))
    segment = None
    for s in sorted(qn.segments, key=lambda s: -s["min_score"]):
        if total >= s["min_score"]:
            segment = s["name"]
            break
    return {"score": total, "segment": segment, "breakdown": breakdown, "missing": missing}


def apply_profile(db: Session, qn: g.GrowthQuestionnaire, answers: dict, lead: m.Lead | None = None, user_id=None,
                  enforce_required: bool = True) -> g.GrowthQuestionnaireResponse:
    res = score_answers(qn, answers)
    if enforce_required and res["missing"]:
        raise HTTPException(400, f"Required answers missing: {', '.join(res['missing'])}")
    keys = {q["key"] for q in qn.questions}
    clean = {k: v for k, v in answers.items() if k in keys}
    r = g.GrowthQuestionnaireResponse(organisation_id=qn.organisation_id, questionnaire_id=qn.id, lead_id=lead.id if lead else None,
                                      user_id=user_id, answers=clean, score=res["score"], segment=res["segment"],
                                      breakdown=res["breakdown"])
    db.add(r)
    if lead:
        x = ext_of(db, lead)
        x.score, x.segment, x.updated_at = res["score"], res["segment"], utcnow()
        x.custom = {**(x.custom or {}), **{f"q_{k}": (", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in clean.items()}}
        activity(db, lead.organisation_id, lead.id, "scored", f"Profiled with '{qn.name}': score {res['score']}"
                 + (f", segment {res['segment']}" if res["segment"] else ""), {"questionnaire_id": str(qn.id)})
    db.flush()
    return r


def users_map(db: Session) -> dict:
    return {u.id: (u.name or u.email) for u in db.scalars(select(m.User))}


def uuid_or_400(v, field="id") -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        raise HTTPException(400, f"{field} must be a UUID")
