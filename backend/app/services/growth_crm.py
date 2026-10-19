"""HubSpot two-way contact sync (43) and Calendly booking tracking (44).

HubSpot: pull contacts modified since the last sync, then push leads changed since their last sync.
Conflict rule: last-modified wins — a HubSpot contact modified after the lead's last local change
overwrites the mapped lead fields; a lead modified after the contact's lastmodifieddate is pushed.
Calendly: webhook receiver (invitee.created / invitee.canceled, signature verified when a signing key is
configured) + API pull of scheduled events when connected; bookings are matched to leads by e-mail.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_growth as g
from . import growth_leads as L
from .growth_common import utcnow
from .integrations import IntegrationError, NotConnected, credentials, http_call, mark_synced, oauth_access_token, take_token

HS = "https://api.hubapi.com"
DEFAULT_MAP = {"email": "email", "first_name": "firstname", "last_name": "lastname", "company": "company", "phone": "phone",
               "status": "hs_lead_status"}
MAPPABLE = ["email", "first_name", "last_name", "name", "company", "phone", "status", "value", "utm_source", "utm_medium", "utm_campaign"]
STATUS_TO_HS = {"new": "NEW", "contacted": "ATTEMPTED_TO_CONTACT", "qualified": "OPEN_DEAL", "won": "CONNECTED", "lost": "UNQUALIFIED"}
HS_TO_STATUS = {"NEW": "new", "OPEN": "new", "IN_PROGRESS": "contacted", "ATTEMPTED_TO_CONTACT": "contacted", "CONNECTED": "won",
                "OPEN_DEAL": "qualified", "UNQUALIFIED": "lost", "BAD_TIMING": "lost"}
PROP_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


def validate_map(fm: dict) -> dict:
    out = {}
    for k, v in (fm or {}).items():
        if k not in MAPPABLE:
            raise HTTPException(400, f"Unknown lead field '{k}'. Allowed: {MAPPABLE}")
        if not isinstance(v, str) or not PROP_RE.match(v):
            raise HTTPException(400, f"HubSpot property for '{k}' must be an internal property name (a-z0-9_)")
        out[k] = v
    if "email" not in out:
        raise HTTPException(400, "email must be mapped (it is HubSpot's contact identity)")
    return out


def settings(db: Session, org_id) -> g.GrowthHubspotSettings:
    s = db.scalar(select(g.GrowthHubspotSettings))
    if not s:
        s = g.GrowthHubspotSettings(organisation_id=org_id, field_map=dict(DEFAULT_MAP))
        db.add(s)
        db.flush()
    return s


def hs_token(db: Session, org_id) -> str:
    cr = credentials(db, org_id, "hubspot")  # NotConnected when not connected
    s = cr["secret"] or ""
    return oauth_access_token(db, org_id, "hubspot") if s.startswith("{") else s


def _hs(db, org_id, token: str, method: str, path: str, **kw) -> dict:
    take_token(db, org_id, "hubspot")
    r = http_call(method, HS + path, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, **kw)
    if r.status_code in (401, 403):
        raise IntegrationError(f"HubSpot rejected the credentials ({r.status_code})")
    if r.status_code >= 400:
        raise IntegrationError(f"HubSpot {method} {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if r.content else {}


def _ts(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        return datetime.fromtimestamp(int(v) / 1000, timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def lead_props(lead: m.Lead, x: g.GrowthLead, fm: dict) -> dict:
    first, _, last = (x.name or "").partition(" ")
    vals = {"email": lead.email, "phone": lead.phone, "company": x.company, "name": x.name, "first_name": first or None,
            "last_name": last or None, "status": STATUS_TO_HS.get(lead.status), "value": str(x.value) if x.value is not None else None,
            "utm_source": lead.utm_source, "utm_medium": lead.utm_medium, "utm_campaign": lead.utm_campaign}
    return {prop: vals[f] for f, prop in fm.items() if vals.get(f) not in (None, "")}


def apply_props(lead: m.Lead, x: g.GrowthLead, props: dict, fm: dict) -> list[str]:
    changed = []
    inv = {prop: f for f, prop in fm.items()}
    first = last = None
    for prop, f in inv.items():
        v = props.get(prop)
        if v in (None, ""):
            continue
        if f == "email" and v != lead.email:
            lead.email, x.email_norm = v, L.norm_email(v)
            changed.append("email")
        elif f == "phone" and v != lead.phone:
            lead.phone, x.phone_key = v, L.phone_key(v)
            changed.append("phone")
        elif f == "company" and v != x.company:
            x.company = v
            changed.append("company")
        elif f == "name" and v != x.name:
            x.name = v
            changed.append("name")
        elif f == "first_name":
            first = v
        elif f == "last_name":
            last = v
        elif f == "status" and HS_TO_STATUS.get(v) and HS_TO_STATUS[v] != lead.status:
            lead.status = HS_TO_STATUS[v]
            changed.append("status")
    if first or last:
        nm = " ".join(p for p in (first, last) if p)
        if nm != x.name:
            x.name = nm
            changed.append("name")
    return changed


def sync_hubspot(db: Session, org_id, user_id=None) -> g.GrowthSyncLog:
    s = settings(db, org_id)
    log = g.GrowthSyncLog(organisation_id=org_id, provider="hubspot", direction="two_way", status="ok", created_by=user_id,
                          counts={"pulled": 0, "created": 0, "updated_from_hubspot": 0, "pushed_created": 0, "pushed_updated": 0,
                                  "conflicts_hubspot_won": 0, "conflicts_lead_won": 0, "skipped_no_project": 0})
    db.add(log)
    try:
        token = hs_token(db, org_id)
    except NotConnected as e:
        log.status, log.error = "not_connected", str(e)
        db.flush()
        raise
    c, details, fm = log.counts, [], s.field_map or dict(DEFAULT_MAP)
    started = utcnow()
    hs_seen: dict[str, datetime] = {}
    try:
        # ---------------- pull
        props = sorted(set(fm.values()) | {"email", "lastmodifieddate"})
        after, pages = None, 0
        while pages < 20:
            body = {"properties": props, "limit": 100, "sorts": [{"propertyName": "lastmodifieddate", "direction": "ASCENDING"}]}
            if s.last_pull_at:
                body["filterGroups"] = [{"filters": [{"propertyName": "lastmodifieddate", "operator": "GTE",
                                                      "value": str(int(s.last_pull_at.timestamp() * 1000))}]}]
            if after:
                body["after"] = after
            res = _hs(db, org_id, token, "POST", "/crm/v3/objects/contacts/search", json=body)
            for ct in res.get("results", []):
                c["pulled"] += 1
                cid, p = str(ct["id"]), ct.get("properties") or {}
                hs_mod = _ts(p.get("lastmodifieddate") or ct.get("updatedAt")) or utcnow()
                hs_seen[cid] = hs_mod
                lead = db.scalar(select(m.Lead).where(m.Lead.crm_contact_id == cid))
                if not lead and p.get("email"):
                    en = L.norm_email(p["email"])
                    q = select(m.Lead).join(g.GrowthLead, g.GrowthLead.lead_id == m.Lead.id).where(g.GrowthLead.email_norm == en)
                    if s.project_id:
                        q = q.where(m.Lead.project_id == s.project_id)
                    lead = db.scalars(q.order_by(m.Lead.created_at)).first()
                if lead:
                    x = L.ext_of(db, lead)
                    lead.crm_contact_id = cid
                    x.hubspot_modified_at = hs_mod
                    if hs_mod >= x.updated_at:
                        ch = apply_props(lead, x, p, fm)
                        x.updated_at = x.crm_synced_at = utcnow()
                        if ch:
                            c["updated_from_hubspot"] += 1
                            L.activity(db, org_id, lead.id, "hubspot_sync", f"Updated from HubSpot: {', '.join(ch)}", {"contact_id": cid})
                            details.append({"lead_id": str(lead.id), "action": "updated_from_hubspot", "fields": ch})
                    else:
                        c["conflicts_lead_won"] += 1
                        details.append({"lead_id": str(lead.id), "action": "conflict_lead_newer"})
                elif s.project_id:
                    first, last = p.get(fm.get("first_name", "firstname")), p.get(fm.get("last_name", "lastname"))
                    data = {"email": p.get("email"), "phone": p.get(fm.get("phone", "phone")), "company": p.get(fm.get("company", "company")),
                            "name": " ".join(x for x in (first, last) if x) or None}
                    if not any(data.values()):
                        continue
                    lead, created = L.capture(db, org_id, s.project_id, data, source="hubspot", kind="hubspot",
                                              touch={"utm_source": "hubspot", "utm_medium": "crm"}, ref=cid)
                    lead.crm_contact_id = cid
                    x = L.ext_of(db, lead)
                    st = HS_TO_STATUS.get(p.get(fm.get("status", "hs_lead_status")) or "")
                    if st and created:
                        lead.status = st
                    x.hubspot_modified_at = hs_mod
                    x.updated_at = x.crm_synced_at = utcnow()
                    c["created"] += int(created)
                    details.append({"lead_id": str(lead.id), "action": "created_from_hubspot"})
                else:
                    c["skipped_no_project"] += 1
            after = ((res.get("paging") or {}).get("next") or {}).get("after")
            pages += 1
            if not after:
                break
        s.last_pull_at = started
        db.flush()
        # ---------------- push
        q = (select(m.Lead, g.GrowthLead).join(g.GrowthLead, g.GrowthLead.lead_id == m.Lead.id)
             .where((g.GrowthLead.crm_synced_at.is_(None)) | (g.GrowthLead.updated_at > g.GrowthLead.crm_synced_at)).limit(500))
        if s.project_id:
            q = q.where(m.Lead.project_id == s.project_id)
        for lead, x in db.execute(q).all():
            props_out = lead_props(lead, x, fm)
            if not props_out.get(fm["email"]) and not lead.crm_contact_id:
                continue  # HubSpot de-duplicates contacts on e-mail; skip leads without one
            if lead.crm_contact_id:
                hs_mod = hs_seen.get(lead.crm_contact_id) or x.hubspot_modified_at
                if hs_mod and hs_mod > x.updated_at:
                    c["conflicts_hubspot_won"] += 1
                    details.append({"lead_id": str(lead.id), "action": "conflict_hubspot_newer"})
                    continue
                _hs(db, org_id, token, "PATCH", f"/crm/v3/objects/contacts/{lead.crm_contact_id}", json={"properties": props_out})
                c["pushed_updated"] += 1
                details.append({"lead_id": str(lead.id), "action": "pushed_update"})
            else:
                found = _hs(db, org_id, token, "POST", "/crm/v3/objects/contacts/search", json={
                    "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": lead.email}]}],
                    "properties": sorted(set(fm.values()) | {"lastmodifieddate"}), "limit": 1}).get("results") or []
                if found:
                    ct = found[0]
                    lead.crm_contact_id = str(ct["id"])
                    hs_mod = _ts((ct.get("properties") or {}).get("lastmodifieddate") or ct.get("updatedAt"))
                    x.hubspot_modified_at = hs_mod
                    if hs_mod and hs_mod > x.updated_at:
                        apply_props(lead, x, ct.get("properties") or {}, fm)
                        c["conflicts_hubspot_won"] += 1
                        details.append({"lead_id": str(lead.id), "action": "linked_hubspot_newer"})
                    else:
                        _hs(db, org_id, token, "PATCH", f"/crm/v3/objects/contacts/{ct['id']}", json={"properties": props_out})
                        c["pushed_updated"] += 1
                        details.append({"lead_id": str(lead.id), "action": "linked_and_pushed"})
                else:
                    res = _hs(db, org_id, token, "POST", "/crm/v3/objects/contacts", json={"properties": props_out})
                    lead.crm_contact_id = str(res["id"])
                    c["pushed_created"] += 1
                    details.append({"lead_id": str(lead.id), "action": "pushed_create"})
                L.activity(db, org_id, lead.id, "hubspot_sync", "Linked to HubSpot contact", {"contact_id": lead.crm_contact_id})
            x.crm_synced_at = max(utcnow(), x.updated_at)
        s.last_push_at = utcnow()
        mark_synced(db, org_id, "hubspot")
    except IntegrationError as e:
        log.status, log.error = "error", str(e)[:500]
        mark_synced(db, org_id, "hubspot", str(e))
    log.counts = dict(c)
    log.details = details[:100]
    db.flush()
    return log


# ================================================================== Calendly
CAL = "https://api.calendly.com"


def cal_settings(db: Session, org_id) -> g.GrowthCalendlySettings:
    s = db.scalar(select(g.GrowthCalendlySettings))
    if not s:
        import secrets
        s = g.GrowthCalendlySettings(organisation_id=org_id, webhook_token=secrets.token_urlsafe(18))
        db.add(s)
        db.flush()
    return s


def verify_calendly(key: str, header: str | None, body: bytes, tolerance: int = 180) -> bool:
    parts = dict(p.strip().split("=", 1) for p in (header or "").split(",") if "=" in p)
    t, sig = parts.get("t", ""), parts.get("v1", "")
    if not t.isdigit() or not sig or abs(time.time() - int(t)) > tolerance:
        return False
    exp = hmac.new(key.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, sig)


def record_booking(db: Session, org_id, s: g.GrowthCalendlySettings, event: str, p: dict, *, verified: bool, source: str = "webhook") -> g.GrowthBooking:
    inv_uri = p.get("uri") or ""
    if not inv_uri:
        raise HTTPException(400, "payload.uri (invitee) missing")
    se = p.get("scheduled_event") or {}
    tracking = {k: v for k, v in (p.get("tracking") or {}).items() if v}
    b = db.scalar(select(g.GrowthBooking).where(g.GrowthBooking.invitee_uri == inv_uri))
    if not b:
        b = g.GrowthBooking(organisation_id=org_id, invitee_uri=inv_uri, source=source)
        db.add(b)
    b.event_uri = se.get("uri") or p.get("event")
    b.event_name = se.get("name")
    b.email, b.name = p.get("email"), p.get("name")
    b.start_time, b.end_time = _ts(se.get("start_time")), _ts(se.get("end_time"))
    b.tracking = tracking
    b.signature_verified = b.signature_verified or verified
    canceled = event == "invitee.canceled" or p.get("status") == "canceled"
    if canceled:
        b.status, b.canceled_at = "canceled", utcnow()
    # booking link: tracked links carry utm_content=gq_<link id prefix>
    uc = tracking.get("utm_content") or ""
    if uc.startswith("gq_") and not b.booking_link_id:
        for bl in db.scalars(select(g.GrowthBookingLink)):
            if str(bl.id).startswith(uc[3:]) and len(uc) > 6:
                b.booking_link_id = bl.id
                break
    db.flush()
    # lead match by normalised e-mail
    en = L.norm_email(b.email)
    lead = None
    if en:
        lead = db.scalars(select(m.Lead).join(g.GrowthLead, g.GrowthLead.lead_id == m.Lead.id)
                          .where(g.GrowthLead.email_norm == en, g.GrowthLead.merged_into.is_(None)).order_by(m.Lead.created_at.desc())).first()
    first_time = b.lead_id is None
    if not lead and not canceled and s.create_leads and en:
        pid = s.project_id
        if b.booking_link_id:
            bl = db.get(g.GrowthBookingLink, b.booking_link_id)
            pid = (bl.project_id if bl else None) or pid
        if pid:
            lead, _ = L.capture(db, org_id, pid, {"email": b.email, "name": b.name}, source="calendly", kind="booking",
                                touch={**tracking}, ref=inv_uri)
            first_time = False
    if lead:
        b.lead_id = lead.id
        if first_time and not canceled and any(k.startswith("utm_") for k in tracking):
            L.add_touchpoint(db, lead, "booking", tracking, ref=inv_uri)
        when = b.start_time.strftime("%d %b %Y %H:%M UTC") if b.start_time else "unscheduled"
        L.activity(db, org_id, lead.id, "booking", f"Calendly booking {'canceled' if canceled else 'scheduled'}: {b.event_name or 'meeting'} — {when}",
                   {"booking_id": str(b.id), "event": event})
        if not canceled and lead.status == "new":
            L.set_status(db, lead, "contacted")
        if b.booking_link_id:
            bl = db.get(g.GrowthBookingLink, b.booking_link_id)
            x = L.ext_of(db, lead)
            if bl and bl.user_id and not x.assignee_id:
                L.assign(db, lead, bl.user_id)
    db.flush()
    return b


def cal_token(db: Session, org_id) -> str:
    cr = credentials(db, org_id, "calendly")
    s = cr["secret"] or ""
    return oauth_access_token(db, org_id, "calendly") if s.startswith("{") else s


def _cal(db, org_id, token, method, path, **kw) -> dict:
    take_token(db, org_id, "calendly")
    url = path if path.startswith("http") else CAL + path
    if not url.startswith(CAL):
        raise IntegrationError("Refusing non-Calendly URL")
    r = http_call(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)
    if r.status_code >= 400:
        raise IntegrationError(f"Calendly {method} {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if r.content else {}


def sync_calendly(db: Session, org_id, user_id=None, days: int = 30) -> g.GrowthSyncLog:
    s = cal_settings(db, org_id)
    log = g.GrowthSyncLog(organisation_id=org_id, provider="calendly", direction="pull", status="ok", created_by=user_id,
                          counts={"events": 0, "invitees": 0, "matched": 0})
    db.add(log)
    try:
        token = cal_token(db, org_id)
    except NotConnected as e:
        log.status, log.error = "not_connected", str(e)
        db.flush()
        raise
    try:
        me = _cal(db, org_id, token, "GET", "/users/me")["resource"]
        since = (s.last_pull_at or utcnow() - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        res = _cal(db, org_id, token, "GET", "/scheduled_events", params={"user": me["uri"], "min_start_time": since, "count": 100})
        for ev in res.get("collection", []):
            log.counts["events"] += 1
            uuid_ = ev["uri"].rstrip("/").rsplit("/", 1)[-1]
            invs = _cal(db, org_id, token, "GET", f"/scheduled_events/{uuid_}/invitees", params={"count": 100})
            for inv in invs.get("collection", []):
                log.counts["invitees"] += 1
                b = record_booking(db, org_id, s, "invitee.canceled" if inv.get("status") == "canceled" else "invitee.created",
                                   {**inv, "scheduled_event": ev}, verified=True, source="api")
                log.counts["matched"] += int(bool(b.lead_id))
        s.last_pull_at = utcnow()
        mark_synced(db, org_id, "calendly")
    except IntegrationError as e:
        log.status, log.error = "error", str(e)[:500]
        mark_synced(db, org_id, "calendly", str(e))
    log.counts = dict(log.counts)
    db.flush()
    return log


def register_webhook(db: Session, org_id, callback_url: str) -> dict:
    import secrets
    from . import vault
    s = cal_settings(db, org_id)
    token = cal_token(db, org_id)
    me = _cal(db, org_id, token, "GET", "/users/me")["resource"]
    key = secrets.token_urlsafe(32)
    res = _cal(db, org_id, token, "POST", "/webhook_subscriptions", json={
        "url": callback_url, "events": ["invitee.created", "invitee.canceled"], "organization": me["current_organization"],
        "user": me["uri"], "scope": "user", "signing_key": key})
    if s.signing_ref:
        vault.remove(org_id, s.signing_ref)
    s.signing_ref = vault.put(org_id, key)
    s.subscription_uri = (res.get("resource") or {}).get("uri")
    return {"subscription_uri": s.subscription_uri}
