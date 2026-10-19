"""Social publishing core: account mapping & health (38), recurring rules / campaigns / publisher (36),
calendar queries (39) and engagement analytics (40).

Publishing guarantees
* Only content items in state ``approved`` are scheduled or sent (an evergreen rule may re-send an
  item that was approved and already published).
* Every scheduled post owns exactly one ``publish_job`` whose idempotency key is derived from the
  schedule row (``social:<schedule_id>``). The publisher marks it SENDING in its own committed
  transaction *before* calling the platform; a retry that finds SENDING (worker crashed mid-call) or
  PUBLISHED never posts again. The platform call itself is made with ``retries=0`` so a 5xx after
  the post was accepted cannot cause a second post.
* No connection → the publish_job is FAILED with error_code ``not_connected``, a notification is
  raised, and nothing is ever marked published.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_social as sm
from ..db import tenant_session
from . import integrations as ih
from .jobs import enqueue

GRAPH = "https://graph.facebook.com/v19.0"
LINKEDIN = "https://api.linkedin.com/rest"
LINKEDIN_VERSION = "202405"
X_API = "https://api.twitter.com/2"

CHANNELS = {"facebook": "meta", "instagram": "meta", "linkedin": "linkedin", "x": "x"}
CHANNEL_LABEL = {"facebook": "Facebook page", "instagram": "Instagram business", "linkedin": "LinkedIn page", "x": "X account"}
PROVIDER_LABEL = {"meta": "Meta (Facebook/Instagram)", "linkedin": "LinkedIn", "x": "X (Twitter)"}
MAX_LEN = {"x": 280, "linkedin": 3000, "facebook": 63206, "instagram": 2200}
SENDING_LEASE = timedelta(minutes=10)
# "scheduled" is the AI content workflow's state for an approved item that has a publish date; it is
# treated exactly like approved here (both mean a human approved the content).
APPROVED = ("approved", "scheduled")
APPROVED_OR_PUBLISHED = ("approved", "scheduled", "published")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ content state (AI group contract)
class WorkflowUnavailable(Exception):
    pass


def transition(db: Session, actor, item: m.ContentItem, to_state: str, note: str | None = None):
    """Move a content item through the AI group's state machine
    (``services.ai_content.transition(db, ctx_or_user, item, to_state, note)``).
    When that module is not installed only approved→published is applied directly, because the
    publisher must be able to record a real post; every other step belongs to the content workflow."""
    try:
        from . import ai_content  # type: ignore
    except ImportError:
        ai_content = None
    fn = getattr(ai_content, "transition", None) if ai_content else None
    if fn is not None:
        return fn(db, actor, item, to_state, note)
    if item.state in APPROVED and to_state == "published":
        item.state = "published"
        db.flush()
        return item
    raise WorkflowUnavailable(f"Content workflow service is not available to move '{item.state}' → '{to_state}'")


def item_body(db: Session, item: m.ContentItem) -> str:
    v = db.scalar(select(m.ContentVersion).where(m.ContentVersion.content_item_id == item.id)
                  .order_by(desc(m.ContentVersion.version), desc(m.ContentVersion.created_at)).limit(1))
    return ((v.body if v and v.body else None) or item.title or "").strip()


def post_meta(db: Session, item_id) -> sm.SocialPostMeta | None:
    return db.scalar(select(sm.SocialPostMeta).where(sm.SocialPostMeta.content_item_id == item_id))


def validate_for_channel(db: Session, item: m.ContentItem, channel: str) -> str | None:
    """Returns a human-readable problem, or None when the item can be posted to the channel."""
    body = item_body(db, item)
    meta = post_meta(db, item.id)
    if not body:
        return "The content has no text to post"
    extra = len(meta.link_url) + 1 if (channel == "x" and meta and meta.link_url and meta.link_url not in body) else 0
    if len(body) + extra > MAX_LEN[channel]:
        return f"Text is {len(body) + extra} characters; {CHANNEL_LABEL[channel]} allows {MAX_LEN[channel]}"
    if channel == "instagram" and not (meta and meta.media_url):
        return "Instagram posts need an image URL (media_url)"
    return None


# ------------------------------------------------------------------ provider access
class PlatformError(Exception):
    def __init__(self, msg: str, code: str = "platform_error"):
        super().__init__(msg)
        self.code = code


def access_token(db: Session, org_id, provider: str) -> str:
    """Raises integrations.NotConnected when the org-level connection is missing."""
    cr = ih.credentials(db, org_id, provider)
    secret = cr.get("secret") or ""
    if secret.startswith("{"):
        return ih.oauth_access_token(db, org_id, provider)
    if not secret:
        raise ih.NotConnected(f"{PROVIDER_LABEL[provider]} is NOT CONNECTED")
    return secret


def provider_status(db: Session) -> dict:
    conn = ih.connected_map(db)
    return {p: ("CONNECTED" if conn.get(p) in ("CONNECTED", "RATE_LIMITED") else (conn.get(p) or "NOT_CONNECTED")) for p in PROVIDER_LABEL}


def _check(r, what: str):
    if r.status_code in (401, 403):
        raise PlatformError(f"{what}: credentials rejected ({r.status_code}) — reconnect in Integrations", "auth")
    if r.status_code >= 400:
        try:
            j = r.json()
            msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else (j.get("detail") or j.get("message") or j.get("title"))
        except Exception:
            msg = None
        raise PlatformError(f"{what}: HTTP {r.status_code} {msg or r.text[:200]}")
    return r


def _li_headers(tok):
    return {"Authorization": f"Bearer {tok}", "LinkedIn-Version": LINKEDIN_VERSION, "X-Restli-Protocol-Version": "2.0.0"}


def _li_org(ext: str) -> str:
    return ext if ext.startswith("urn:li:") else f"urn:li:organization:{ext}"


def discover(db: Session, org_id, provider: str) -> list[dict]:
    """List the pages / profiles the org-level connection can manage (for mapping to projects)."""
    tok = access_token(db, org_id, provider)
    out = []
    if provider == "meta":
        r = _check(ih.http_call("GET", f"{GRAPH}/me/accounts", params={
            "fields": "id,name,instagram_business_account{id,username,name}", "limit": 100, "access_token": tok}, retries=1), "Meta")
        for p in r.json().get("data", []):
            out.append({"channel": "facebook", "external_id": p["id"], "name": p.get("name") or p["id"], "handle": None})
            ig = p.get("instagram_business_account")
            if ig:
                out.append({"channel": "instagram", "external_id": ig["id"], "name": ig.get("name") or ig.get("username") or ig["id"],
                            "handle": ig.get("username")})
    elif provider == "linkedin":
        r = _check(ih.http_call("GET", f"{LINKEDIN}/organizationAcls", params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"},
                                headers=_li_headers(tok), retries=1), "LinkedIn")
        for e in r.json().get("elements", []):
            urn = e.get("organization") or e.get("organizationTarget") or ""
            oid = urn.rsplit(":", 1)[-1]
            name = oid
            try:
                o = ih.http_call("GET", f"{LINKEDIN}/organizations/{oid}", headers=_li_headers(tok), retries=0)
                if o.status_code < 400:
                    name = o.json().get("localizedName") or name
            except ih.IntegrationError:
                pass
            out.append({"channel": "linkedin", "external_id": oid, "name": name, "handle": None})
    elif provider == "x":
        r = _check(ih.http_call("GET", f"{X_API}/users/me", headers={"Authorization": f"Bearer {tok}"}, retries=1), "X")
        u = r.json().get("data", {})
        out.append({"channel": "x", "external_id": u.get("id"), "name": u.get("name") or u.get("username"), "handle": u.get("username")})
    ih.mark_synced(db, org_id, provider)
    return out


def account_health(db: Session, org_id, a: sm.SocialAccount) -> sm.SocialAccount:
    a.last_health_at = utcnow()
    try:
        tok = access_token(db, org_id, a.provider)
        if a.channel == "facebook":
            _check(ih.http_call("GET", f"{GRAPH}/{a.external_id}", params={"fields": "id,name", "access_token": tok}, retries=1), "Facebook page")
        elif a.channel == "instagram":
            _check(ih.http_call("GET", f"{GRAPH}/{a.external_id}", params={"fields": "id,username", "access_token": tok}, retries=1), "Instagram")
        elif a.channel == "linkedin":
            oid = a.external_id.rsplit(":", 1)[-1]
            _check(ih.http_call("GET", f"{LINKEDIN}/organizations/{oid}", headers=_li_headers(tok), retries=1), "LinkedIn page")
        elif a.channel == "x":
            _check(ih.http_call("GET", f"{X_API}/users/{a.external_id}", headers={"Authorization": f"Bearer {tok}"}, retries=1), "X account")
        a.status, a.last_health_ok, a.last_error = "active", True, None
    except ih.NotConnected as e:
        a.last_health_ok, a.last_error = False, str(e)
    except (PlatformError, ih.IntegrationError) as e:
        a.status, a.last_health_ok, a.last_error = "error", False, str(e)[:500]
    return a


# ------------------------------------------------------------------ platform calls (publish)
@dataclass
class Outgoing:
    channel: str
    external_account: str
    token: str
    body: str
    title: str | None
    link: str | None
    media: str | None


def send_post(o: Outgoing) -> tuple[str, str | None]:
    """Make the platform call. Returns (external_post_id, url). Never retried internally."""
    if o.channel == "facebook":
        r = _check(ih.http_call("GET", f"{GRAPH}/{o.external_account}", params={"fields": "access_token", "access_token": o.token},
                                retries=1), "Facebook page token")
        page_tok = r.json().get("access_token") or o.token
        data = {"message": o.body, "access_token": page_tok}
        if o.link:
            data["link"] = o.link
        r = _check(ih.http_call("POST", f"{GRAPH}/{o.external_account}/feed", data=data, retries=0), "Facebook")
        pid = str(r.json()["id"])
        return pid, f"https://www.facebook.com/{pid}"
    if o.channel == "instagram":
        r = _check(ih.http_call("POST", f"{GRAPH}/{o.external_account}/media",
                                data={"image_url": o.media, "caption": o.body, "access_token": o.token}, retries=0), "Instagram media")
        creation = r.json()["id"]
        r = _check(ih.http_call("POST", f"{GRAPH}/{o.external_account}/media_publish",
                                data={"creation_id": creation, "access_token": o.token}, retries=0), "Instagram publish")
        return str(r.json()["id"]), None
    if o.channel == "linkedin":
        payload = {"author": _li_org(o.external_account), "commentary": o.body, "visibility": "PUBLIC",
                   "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                   "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        if o.link:
            payload["content"] = {"article": {"source": o.link, "title": (o.title or o.link)[:200]}}
        r = _check(ih.http_call("POST", f"{LINKEDIN}/posts", json=payload, headers=_li_headers(o.token), retries=0), "LinkedIn")
        pid = r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or (r.json().get("id") if r.content else None)
        if not pid:
            raise PlatformError("LinkedIn accepted the post but returned no id", "outcome_unknown")
        return pid, f"https://www.linkedin.com/feed/update/{pid}"
    if o.channel == "x":
        text = o.body if not o.link or o.link in o.body else f"{o.body} {o.link}"
        r = _check(ih.http_call("POST", f"{X_API}/tweets", json={"text": text}, headers={"Authorization": f"Bearer {o.token}"},
                                retries=0), "X")
        pid = str(r.json()["data"]["id"])
        return pid, f"https://x.com/i/web/status/{pid}"
    raise PlatformError(f"Unsupported channel {o.channel}", "invalid")


# ------------------------------------------------------------------ scheduling
def job_key(schedule_id, at: datetime) -> str:
    return f"socialpub:{schedule_id}:{at.astimezone(timezone.utc).isoformat()}"


def enqueue_publish(db: Session, org_id, sched: m.Schedule, user_id=None, force: bool = False, suffix: str = ""):
    key = job_key(sched.id, sched.scheduled_at) + suffix
    return enqueue(db, org_id, "social_publish", {"schedule_id": str(sched.id), "expected_at": sched.scheduled_at.isoformat(),
                                                   "force": force}, run_at=sched.scheduled_at if not force else utcnow(),
                   idempotency_key=key, created_by=user_id, max_attempts=3)


def schedule_item(db: Session, org_id, item: m.ContentItem, account: sm.SocialAccount, at: datetime, *, user_id=None,
                  campaign_id=None, rule: sm.SocialRule | None = None, slot: datetime | None = None,
                  allow_published: bool = False) -> sm.SocialSchedule:
    ok_states = APPROVED_OR_PUBLISHED if allow_published else APPROVED
    if item.state not in ok_states:
        raise HTTPException(409, detail={"code": "not_approved", "message": f"Only approved content can be scheduled (this item is '{item.state}')"})
    if account.project_id != item.project_id:
        raise HTTPException(400, "The account belongs to a different project")
    if account.status == "disabled":
        raise HTTPException(400, "This account is disabled")
    problem = validate_for_channel(db, item, account.channel)
    if problem:
        raise HTTPException(400, problem)
    s = m.Schedule(organisation_id=org_id, content_item_id=item.id, scheduled_at=at,
                   recurrence=f"rule:{rule.id}" if rule else None)
    db.add(s)
    db.flush()
    ss = sm.SocialSchedule(organisation_id=org_id, schedule_id=s.id, project_id=item.project_id, content_item_id=item.id,
                           social_account_id=account.id, campaign_id=campaign_id or (rule.campaign_id if rule else None),
                           rule_id=rule.id if rule else None, slot_at=slot, created_by=user_id)
    db.add(ss)
    db.flush()
    enqueue_publish(db, org_id, s, user_id)
    return ss


def reschedule(db: Session, org_id, ss: sm.SocialSchedule, at: datetime, user_id=None):
    if ss.status != "scheduled":
        raise HTTPException(409, f"Only scheduled posts can be moved (this one is {ss.status})")
    item = db.get(m.ContentItem, ss.content_item_id)
    rule = db.get(sm.SocialRule, ss.rule_id) if ss.rule_id else None
    allowed = APPROVED_OR_PUBLISHED if (rule and rule.mode == "evergreen") else APPROVED
    if item.state not in allowed:
        raise HTTPException(409, detail={"code": "not_approved", "message": f"The content is '{item.state}', not approved — it cannot be rescheduled"})
    if at < utcnow() - timedelta(minutes=1):
        raise HTTPException(400, "Cannot move a post into the past")
    camp = db.scalar(select(sm.SocialCampaign).where(sm.SocialCampaign.campaign_id == ss.campaign_id)) if ss.campaign_id else None
    check_campaign_window(camp, at)
    s = db.get(m.Schedule, ss.schedule_id)
    s.scheduled_at = at
    db.flush()
    enqueue_publish(db, org_id, s, user_id)
    return s


def check_campaign_window(camp: sm.SocialCampaign | None, at: datetime):
    if not camp:
        return
    d = at.astimezone(timezone.utc).date()
    if (camp.start_date and d < camp.start_date) or (camp.end_date and d > camp.end_date):
        raise HTTPException(400, f"The date is outside the campaign window ({camp.start_date or '…'} – {camp.end_date or '…'})")


# ------------------------------------------------------------------ recurring rules (timezone-aware)
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(400, f"Unknown timezone '{tz}' (use an IANA name like Europe/London)")


def expand(weekdays: list[int], times: list[str], tz: str, start: datetime, end: datetime,
           start_date: date | None = None, end_date: date | None = None) -> list[datetime]:
    """All slot instants (UTC) in [start, end) for weekly days (0=Mon) and local wall-clock times.
    DST: a local time skipped by spring-forward maps to the same offset as before the gap (e.g. 02:30 →
    03:30 local); an ambiguous fall-back time uses its first occurrence."""
    z = zone(tz)
    parsed = []
    for t in times:
        mt = TIME_RE.match(t)
        if not mt:
            raise HTTPException(400, f"Time '{t}' must be HH:MM (24h)")
        parsed.append(time(int(mt.group(1)), int(mt.group(2))))
    out = set()
    d, last = start.astimezone(z).date(), end.astimezone(z).date()
    while d <= last:
        if d.weekday() in weekdays and (not start_date or d >= start_date) and (not end_date or d <= end_date):
            for t in parsed:
                u = datetime.combine(d, t).replace(tzinfo=z).astimezone(timezone.utc)
                if start <= u < end:
                    out.add(u)
        d += timedelta(days=1)
    return sorted(out)


def rule_accounts(db: Session, rule: sm.SocialRule) -> list[sm.SocialAccount]:
    if not rule.account_ids:
        return []
    return list(db.scalars(select(sm.SocialAccount).where(sm.SocialAccount.id.in_(rule.account_ids),
                                                         sm.SocialAccount.project_id == rule.project_id,
                                                         sm.SocialAccount.status != "disabled")))


def materialize(db: Session, rule: sm.SocialRule, now: datetime | None = None, user_id=None) -> dict:
    """Create schedule rows for the rule's slots inside its horizon. Idempotent per (rule, slot, account)."""
    now = now or utcnow()
    if not rule.enabled:
        return {"created": 0, "reason": "disabled"}
    accounts = rule_accounts(db, rule)
    if not accounts:
        return {"created": 0, "reason": "no accounts"}
    slots = expand(rule.weekdays or [], rule.times or [], rule.timezone, now, now + timedelta(days=rule.horizon_days or 14),
                   rule.start_date, rule.end_date)
    existing = db.execute(select(sm.SocialSchedule.slot_at, sm.SocialSchedule.social_account_id, sm.SocialSchedule.content_item_id,
                                 sm.SocialSchedule.status).where(sm.SocialSchedule.rule_id == rule.id)).all()
    taken = {(s, a) for s, a, _, st in existing if st != "cancelled"}
    used_items = {i for _, _, i, st in existing if st != "cancelled"}
    items = {i.id: i for i in db.scalars(select(m.ContentItem).where(m.ContentItem.id.in_([uuid.UUID(x) for x in rule.item_ids or []])))}
    order = [items[uuid.UUID(x)] for x in rule.item_ids or [] if uuid.UUID(x) in items]
    created, skipped = 0, []
    if rule.mode == "evergreen":
        pool = [i for i in order if i.state in APPROVED_OR_PUBLISHED]
        n_slots_done = len({s for s, _ in taken})
    else:
        pool = [i for i in order if i.state in APPROVED and i.id not in used_items]
    for slot in slots:
        free = [a for a in accounts if (slot, a.id) not in taken]
        if not free:
            continue
        if not pool:
            break
        if rule.mode == "evergreen":
            item = pool[n_slots_done % len(pool)]
            n_slots_done += 1
        else:
            item = pool.pop(0)
        for a in free:
            try:
                with db.begin_nested():
                    schedule_item(db, rule.organisation_id, item, a, slot, user_id=user_id, rule=rule, slot=slot,
                                  allow_published=rule.mode == "evergreen")
                created += 1
            except HTTPException as e:
                skipped.append({"item": str(item.id), "account": a.name, "reason": e.detail if isinstance(e.detail, str) else e.detail.get("message")})
    rule.last_materialized_at = now
    return {"created": created, "skipped": skipped, "remaining_queue": len(pool) if rule.mode == "queue" else None}


# ------------------------------------------------------------------ publisher
def _fail(org_id, schedule_id, code: str, msg: str):
    """Record a failed publish in its own transaction + notify (in-app and Teams)."""
    from . import social_teams
    with tenant_session(org_id) as db:
        ss = db.scalar(select(sm.SocialSchedule).where(sm.SocialSchedule.schedule_id == schedule_id).with_for_update())
        pj = db.get(m.PublishJob, ss.publish_job_id) if ss and ss.publish_job_id else None
        sp = db.scalar(select(sm.SocialPublish).where(sm.SocialPublish.publish_job_id == pj.id)) if pj else None
        if pj:
            pj.status = "FAILED"
        if sp:
            sp.error, sp.error_code = msg[:1000], code
        if ss:
            ss.status = "failed"
        item = db.get(m.ContentItem, ss.content_item_id) if ss else None
        acct = db.get(sm.SocialAccount, ss.social_account_id) if ss and ss.social_account_id else None
        title = f"Publish failed: {(item.title if item else None) or 'social post'}" + (f" → {acct.name}" if acct else "")
        db.add(m.Notification(organisation_id=org_id, kind="publish_failed", title=title[:200], body=msg[:300], link="/social/automation"))
        db.add(m.ActivityLog(organisation_id=org_id, action="social.publish_failed", target=f"publish_job:{pj.id}" if pj else None,
                             meta={"code": code, "error": msg[:300]}))
        social_teams.dispatch(db, org_id, "publish_failed", title, msg, link="/social/automation",
                              project_id=ss.project_id if ss else None, dedupe_key=f"publish_failed:{pj.id if pj else schedule_id}")
    return {"status": "FAILED", "code": code, "error": msg}


def publish(org_id, schedule_id, expected_at: str | None = None, force: bool = False) -> dict:
    schedule_id = uuid.UUID(str(schedule_id))
    # ---- phase 1: checks + claim (committed before any platform call)
    with tenant_session(org_id) as db:
        ss = db.scalar(select(sm.SocialSchedule).where(sm.SocialSchedule.schedule_id == schedule_id).with_for_update())
        s = db.get(m.Schedule, schedule_id)
        if not ss or not s:
            return {"status": "skipped", "reason": "schedule removed"}
        if ss.status == "published":
            return {"status": "PUBLISHED", "idempotent": True}
        if ss.status == "cancelled":
            return {"status": "skipped", "reason": "cancelled"}
        if not force:
            if expected_at and s.scheduled_at != datetime.fromisoformat(expected_at):
                return {"status": "skipped", "reason": "rescheduled (stale job)"}
            if s.scheduled_at > utcnow() + timedelta(seconds=60):
                return {"status": "skipped", "reason": "not due yet"}
        if ss.status == "failed" and not force:
            return {"status": "skipped", "reason": "failed — use retry"}
        item = db.get(m.ContentItem, ss.content_item_id)
        acct = db.get(sm.SocialAccount, ss.social_account_id) if ss.social_account_id else None
        rule = db.get(sm.SocialRule, ss.rule_id) if ss.rule_id else None
        pj = db.get(m.PublishJob, ss.publish_job_id) if ss.publish_job_id else None
        if not pj:
            key = f"social:{schedule_id}"
            pj = db.scalar(select(m.PublishJob).where(m.PublishJob.idempotency_key == key))
            if not pj:
                pj = m.PublishJob(organisation_id=org_id, content_item_id=item.id, idempotency_key=key, status="QUEUED",
                                  channel=acct.channel if acct else None)
                db.add(pj)
                db.flush()
            ss.publish_job_id = pj.id
        sp = db.scalar(select(sm.SocialPublish).where(sm.SocialPublish.publish_job_id == pj.id))
        if not sp:
            sp = sm.SocialPublish(organisation_id=org_id, publish_job_id=pj.id, schedule_id=schedule_id, project_id=ss.project_id,
                                  social_account_id=acct.id if acct else None, provider=acct.provider if acct else None)
            db.add(sp)
            db.flush()
        if pj.status == "PUBLISHED" or sp.external_post_id:
            pj.status, ss.status = "PUBLISHED", "published"
            return {"status": "PUBLISHED", "external_post_id": sp.external_post_id, "idempotent": True}
        if pj.status == "SENDING":
            if sp.sending_started_at and sp.sending_started_at > utcnow() - SENDING_LEASE:
                return {"status": "skipped", "reason": "another worker is sending this post"}
            fail = ("outcome_unknown", "A previous attempt was interrupted while sending; check the platform before retrying "
                                       "(retrying requires confirming the post did not go out)")
        else:
            fail = None
            allowed = APPROVED_OR_PUBLISHED if (rule and rule.mode == "evergreen") else APPROVED
            if item.state not in allowed:
                fail = ("not_approved", f"Content is '{item.state}', not approved — not published")
            elif not acct or acct.status == "disabled":
                fail = ("invalid", "The target social account was removed or disabled")
            else:
                problem = validate_for_channel(db, item, acct.channel)
                if problem:
                    fail = ("invalid", problem)
        if not fail:
            try:
                tok = access_token(db, org_id, acct.provider)
            except ih.NotConnected:
                fail = ("not_connected", f"{PROVIDER_LABEL[acct.provider]} is NOT CONNECTED — connect it in Integrations; "
                                         f"the post was not published")
            except ih.IntegrationError as e:
                fail = ("not_connected", f"{PROVIDER_LABEL[acct.provider]} token unusable: {e}")
        if not fail:
            meta = post_meta(db, item.id)
            out = Outgoing(acct.channel, acct.external_id, tok, item_body(db, item), item.title,
                           meta.link_url if meta else None, meta.media_url if meta else None)
            pj.status, ss.status = "SENDING", "publishing"
            sp.sending_started_at, sp.attempts, sp.body_sent, sp.error, sp.error_code = utcnow(), (sp.attempts or 0) + 1, out.body, None, None
            pj_id, provider, acct_id = pj.id, acct.provider, acct.id
    if fail:
        return _fail(org_id, schedule_id, *fail)

    # ---- phase 2: the platform call (no DB transaction open)
    try:
        ext_id, url = send_post(out)
    except ih.RateLimited:
        with tenant_session(org_id) as db:
            pj = db.get(m.PublishJob, pj_id)
            pj.status = "QUEUED"
            ss = db.scalar(select(sm.SocialSchedule).where(sm.SocialSchedule.schedule_id == schedule_id))
            ss.status = "scheduled"
        raise
    except PlatformError as e:
        if e.code == "auth":
            with tenant_session(org_id) as db:
                ih.mark_synced(db, org_id, provider, str(e))
        return _fail(org_id, schedule_id, "outcome_unknown" if e.code == "outcome_unknown" else "platform_error", str(e))
    except ih.IntegrationError as e:  # network error: the request may or may not have reached the platform
        return _fail(org_id, schedule_id, "outcome_unknown", f"{e} — check the platform before retrying")

    # ---- phase 3: record the real post
    with tenant_session(org_id) as db:
        pj = db.get(m.PublishJob, pj_id)
        sp = db.scalar(select(sm.SocialPublish).where(sm.SocialPublish.publish_job_id == pj_id))
        ss = db.scalar(select(sm.SocialSchedule).where(sm.SocialSchedule.schedule_id == schedule_id))
        pj.status, ss.status = "PUBLISHED", "published"
        sp.external_post_id, sp.external_url, sp.published_at = ext_id, url, utcnow()
        item = db.get(m.ContentItem, ss.content_item_id)
        if item.state in APPROVED:
            transition(db, None, item, "published", f"Published to {out.channel} ({ext_id})")
        ih.mark_synced(db, org_id, provider)
        db.add(m.ActivityLog(organisation_id=org_id, action="social.published", target=f"publish_job:{pj_id}",
                             meta={"channel": out.channel, "external_post_id": ext_id, "account_id": str(acct_id)}))
    return {"status": "PUBLISHED", "external_post_id": ext_id, "url": url}


# ------------------------------------------------------------------ analytics
METRIC_COLS = ("likes", "comments", "shares", "impressions", "clicks")


def fetch_metrics(db: Session, org_id, sp: sm.SocialPublish, acct: sm.SocialAccount) -> dict:
    tok = access_token(db, org_id, acct.provider)
    pid = sp.external_post_id
    if acct.channel == "facebook":
        r = _check(ih.http_call("GET", f"{GRAPH}/{acct.external_id}", params={"fields": "access_token", "access_token": tok}, retries=1), "Facebook")
        ptok = r.json().get("access_token") or tok
        j = _check(ih.http_call("GET", f"{GRAPH}/{pid}", params={
            "fields": "shares,comments.summary(true).limit(0),reactions.summary(true).limit(0),insights.metric(post_impressions,post_clicks)",
            "access_token": ptok}, retries=1), "Facebook").json()
        ins = {d["name"]: (d.get("values") or [{}])[0].get("value", 0) for d in (j.get("insights") or {}).get("data", [])}
        return {"likes": ((j.get("reactions") or {}).get("summary") or {}).get("total_count", 0),
                "comments": ((j.get("comments") or {}).get("summary") or {}).get("total_count", 0),
                "shares": (j.get("shares") or {}).get("count", 0), "impressions": ins.get("post_impressions", 0),
                "clicks": ins.get("post_clicks", 0)}
    if acct.channel == "instagram":
        j = _check(ih.http_call("GET", f"{GRAPH}/{pid}", params={"fields": "like_count,comments_count", "access_token": tok}, retries=1), "Instagram").json()
        out = {"likes": j.get("like_count", 0), "comments": j.get("comments_count", 0), "shares": 0, "impressions": 0, "clicks": 0}
        r = ih.http_call("GET", f"{GRAPH}/{pid}/insights", params={"metric": "views,shares", "access_token": tok}, retries=1)
        if r.status_code < 400:
            vals = {d["name"]: (d.get("values") or [{}])[0].get("value", 0) for d in r.json().get("data", [])}
            out["impressions"], out["shares"] = vals.get("views", 0), vals.get("shares", 0)
        return out
    if acct.channel == "linkedin":
        h = _li_headers(tok)
        key = "ugcPosts" if ":ugcPost:" in pid else "shares"
        r = ih.http_call("GET", f"{LINKEDIN}/organizationalEntityShareStatistics", headers=h, retries=1,
                         params={"q": "organizationalEntity", "organizationalEntity": _li_org(acct.external_id), f"{key}[0]": pid})
        if r.status_code < 400 and r.json().get("elements"):
            st = r.json()["elements"][0].get("totalShareStatistics", {})
            return {"likes": st.get("likeCount", 0), "comments": st.get("commentCount", 0), "shares": st.get("shareCount", 0),
                    "impressions": st.get("impressionCount", 0), "clicks": st.get("clickCount", 0)}
        j = _check(ih.http_call("GET", f"{LINKEDIN}/socialActions/{quote(pid, safe='')}", headers=h, retries=1), "LinkedIn").json()
        return {"likes": (j.get("likesSummary") or {}).get("totalLikes", 0),
                "comments": (j.get("commentsSummary") or {}).get("aggregatedTotalComments", 0), "shares": 0, "impressions": 0, "clicks": 0}
    if acct.channel == "x":
        j = _check(ih.http_call("GET", f"{X_API}/tweets/{pid}", params={"tweet.fields": "public_metrics"},
                                headers={"Authorization": f"Bearer {tok}"}, retries=1), "X").json()
        pm = (j.get("data") or {}).get("public_metrics", {})
        return {"likes": pm.get("like_count", 0), "comments": pm.get("reply_count", 0),
                "shares": pm.get("retweet_count", 0) + pm.get("quote_count", 0), "impressions": pm.get("impression_count", 0), "clicks": 0}
    raise PlatformError("unsupported channel", "invalid")


def sync_metrics(db: Session, org_id, project_id=None, days: int = 90) -> dict:
    q = select(sm.SocialPublish, sm.SocialAccount, m.PublishJob).join(sm.SocialAccount, sm.SocialAccount.id == sm.SocialPublish.social_account_id) \
        .join(m.PublishJob, m.PublishJob.id == sm.SocialPublish.publish_job_id) \
        .where(sm.SocialPublish.external_post_id.isnot(None), sm.SocialPublish.published_at >= utcnow() - timedelta(days=days))
    if project_id:
        q = q.where(sm.SocialPublish.project_id == project_id)
    status = provider_status(db)
    res = {"fetched": 0, "not_connected": {}, "errors": []}
    for sp, acct, pj in db.execute(q).all():
        if status.get(acct.provider) != "CONNECTED":
            res["not_connected"][acct.provider] = res["not_connected"].get(acct.provider, 0) + 1
            continue
        try:
            vals = fetch_metrics(db, org_id, sp, acct)
        except ih.RateLimited:
            raise
        except (PlatformError, ih.IntegrationError) as e:
            res["errors"].append({"post": sp.external_post_id, "error": str(e)[:200]})
            continue
        item = db.get(m.ContentItem, pj.content_item_id)
        db.add(sm.SocialMetric(organisation_id=org_id, project_id=sp.project_id, social_account_id=acct.id, publish_job_id=pj.id,
                               channel=acct.channel, external_post_id=sp.external_post_id, post_title=item.title if item else None,
                               posted_at=sp.published_at, captured_at=utcnow(), source="api",
                               **{k: max(0, int(vals.get(k) or 0)) for k in METRIC_COLS}))
        res["fetched"] += 1
    return res


class CsvError(Exception):
    pass


def import_metrics_csv(db: Session, org_id, project_id, content: bytes) -> dict:
    """Columns: channel, post_id, likes, comments, shares, impressions, clicks; optional: date (posted),
    captured_at, title, account (external id or name). Metrics are cumulative per post at capture time."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvError("File must be UTF-8 CSV")
    rdr = csv.DictReader(io.StringIO(text))
    cols = {(c or "").strip().lower(): c for c in (rdr.fieldnames or [])}
    need = {"channel", "post_id"}
    if not need <= set(cols):
        raise CsvError("CSV needs at least 'channel' and 'post_id' columns (plus likes, comments, shares, impressions, clicks)")
    accounts = list(db.scalars(select(sm.SocialAccount).where(sm.SocialAccount.project_id == project_id)))
    pubs = {(sp.external_post_id): sp for sp in db.scalars(select(sm.SocialPublish).where(sm.SocialPublish.project_id == project_id,
                                                                                         sm.SocialPublish.external_post_id.isnot(None)))}
    rows = []
    for i, r in enumerate(rdr, 2):
        g = lambda k: (r.get(cols[k]) or "").strip() if k in cols else ""  # noqa: E731
        ch = g("channel").lower()
        if ch in ("twitter",):
            ch = "x"
        if ch not in CHANNELS:
            raise CsvError(f"Row {i}: channel must be one of {', '.join(CHANNELS)}")
        pid = g("post_id")
        if not pid:
            raise CsvError(f"Row {i}: post_id is required")
        vals = {}
        for k in METRIC_COLS:
            v = g(k).replace(",", "")
            try:
                n = int(float(v)) if v else 0
            except ValueError:
                raise CsvError(f"Row {i}: {k} must be a number")
            if n < 0:
                raise CsvError(f"Row {i}: {k} cannot be negative")
            vals[k] = n
        try:
            posted = _parse_dt(g("date")) if g("date") else None
            captured = _parse_dt(g("captured_at")) if g("captured_at") else utcnow()
        except ValueError:
            raise CsvError(f"Row {i}: dates must be ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM)")
        acct_ref = g("account")
        sp = pubs.get(pid)
        acct = next((a for a in accounts if sp and a.id == sp.social_account_id), None) or \
            next((a for a in accounts if acct_ref and a.channel == ch and acct_ref in (a.external_id, a.name, a.handle)), None)
        rows.append(sm.SocialMetric(organisation_id=org_id, project_id=project_id, social_account_id=acct.id if acct else None,
                                    publish_job_id=sp.publish_job_id if sp else None, channel=ch, external_post_id=pid,
                                    post_title=g("title") or None, posted_at=posted or (sp.published_at if sp else None),
                                    captured_at=captured, source="csv", **vals))
    if not rows:
        raise CsvError("No data rows found")
    if len(rows) > 50000:
        raise CsvError("Too many rows (50,000 max per file)")
    db.add_all(rows)
    return {"rows": len(rows), "matched_published": sum(1 for r in rows if r.publish_job_id)}


def _parse_dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def latest_snapshots(db: Session, project_ids, channel: str | None = None, since: datetime | None = None) -> list[sm.SocialMetric]:
    q = select(sm.SocialMetric).where(sm.SocialMetric.project_id.in_(project_ids))
    if channel:
        q = q.where(sm.SocialMetric.channel == channel)
    q = q.distinct(sm.SocialMetric.channel, sm.SocialMetric.external_post_id) \
        .order_by(sm.SocialMetric.channel, sm.SocialMetric.external_post_id, desc(sm.SocialMetric.captured_at))
    snaps = list(db.scalars(q))
    if since:
        snaps = [s for s in snaps if (s.posted_at or s.captured_at) >= since]
    return snaps


def _totals(snaps) -> dict:
    t = {k: sum(getattr(s, k) for s in snaps) for k in METRIC_COLS}
    t["posts"] = len(snaps)
    eng = t["likes"] + t["comments"] + t["shares"] + t["clicks"]
    t["engagements"] = eng
    t["engagement_rate"] = round(100 * eng / t["impressions"], 2) if t["impressions"] else None
    return t


def aggregate(snaps: list[sm.SocialMetric], project_names: dict, account_names: dict) -> dict:
    by_ch, by_proj, by_day = {}, {}, {}
    for s in snaps:
        by_ch.setdefault(s.channel, []).append(s)
        by_proj.setdefault(s.project_id, []).append(s)
        by_day.setdefault((s.posted_at or s.captured_at).date().isoformat(), []).append(s)
    top = sorted(snaps, key=lambda s: -(s.likes + s.comments + s.shares + s.clicks))[:10]
    return {
        "totals": _totals(snaps),
        "by_channel": [{"channel": c, **_totals(v)} for c, v in sorted(by_ch.items())],
        "by_project": [{"project_id": str(p), "project": project_names.get(p), **_totals(v)} for p, v in by_proj.items()],
        "trend": [{"day": d, **{k: sum(getattr(s, k) for s in v) for k in METRIC_COLS}, "posts": len(v)} for d, v in sorted(by_day.items())],
        "top_posts": [{"channel": s.channel, "external_post_id": s.external_post_id, "title": s.post_title,
                       "account": account_names.get(s.social_account_id), "posted_at": s.posted_at, "source": s.source,
                       **{k: getattr(s, k) for k in METRIC_COLS},
                       "engagement_rate": round(100 * (s.likes + s.comments + s.shares + s.clicks) / s.impressions, 2) if s.impressions else None}
                      for s in top],
    }
