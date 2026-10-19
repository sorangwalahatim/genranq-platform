"""Microsoft Teams delivery (module 46): per-org / per-project incoming-webhook channels, event routing,
delivery log, and a sweep that forwards approvals requested and critical monitoring issues."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_monitor as mm
from .. import models_social as sm
from . import integrations as ih
from . import vault
from .jobs import enqueue
from .monitor import valid_hook

EVENTS = {
    "approval_requested": "Approvals requested",
    "monitor_critical": "Monitoring critical issues",
    "report_ready": "Reports ready",
    "publish_failed": "Publish failures",
}
SWEEP_EVENTS = ("approval_requested", "monitor_critical")
SWEEP_EVERY = timedelta(minutes=5)

# Classic Office 365 connector URLs (same patterns monitoring accepts) plus Teams "Workflows" webhooks
# (Power Automate / Logic Apps), which replace connectors.
WORKFLOW_HOOKS = (r"^https://[a-z0-9-]+\.[a-z0-9-]+\.logic\.azure\.com(:443)?/workflows/",
                  r"^https://[a-z0-9.-]+\.environment\.api\.powerplatform\.com(:443)?/")


def valid_teams_url(url: str) -> bool:
    if not url or len(url) > 2048 or any(c.isspace() for c in url):
        return False
    p = urlparse(url)
    if p.scheme != "https" or "@" in p.netloc or not p.hostname:
        return False
    if re.match(r"^https://hooks\.slack\.com/", url):
        return False  # monitoring accepts Slack too; this module is Teams-only
    return valid_hook(url) or any(re.match(rx, url, re.I) for rx in WORKFLOW_HOOKS)


def _is_workflow(url: str) -> bool:
    return any(re.match(rx, url, re.I) for rx in WORKFLOW_HOOKS)


def card(url: str, title: str, text: str, link: str | None = None, facts: dict | None = None) -> dict:
    if _is_workflow(url):
        body = [{"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": title, "wrap": True},
                {"type": "TextBlock", "text": text or "", "wrap": True}]
        if facts:
            body.append({"type": "FactSet", "facts": [{"title": k, "value": str(v)} for k, v in facts.items()]})
        content = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": body}
        if link:
            content["actions"] = [{"type": "Action.OpenUrl", "title": "Open in Genranq", "url": link}]
        return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": content}]}
    c = {"@type": "MessageCard", "@context": "http://schema.org/extensions", "summary": title[:150], "themeColor": "1F3A5F",
         "title": title, "text": text or ""}
    if facts:
        c["sections"] = [{"facts": [{"name": k, "value": str(v)} for k, v in facts.items()]}]
    if link:
        c["potentialAction"] = [{"@type": "OpenUri", "name": "Open in Genranq", "targets": [{"os": "default", "uri": link}]}]
    return c


def channel_url(db: Session, org_id, ch: sm.TeamsChannel) -> str:
    """Raises integrations.NotConnected when the channel relies on a hub connection that is missing."""
    if ch.use_hub:
        cfg = ih.credentials(db, org_id, "ms_teams").get("config") or {}
        url = cfg.get("webhook_url")
        if not url:
            raise ih.NotConnected("Microsoft Teams in the Integrations Hub has no incoming-webhook URL (OAuth-only connection)")
        return url
    if not ch.webhook_ref:
        raise ih.NotConnected("No webhook URL saved for this channel")
    return vault.get(org_id, ch.webhook_ref)


def _abs(link: str | None) -> str | None:
    if not link:
        return None
    import os
    base = os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
    return f"{base}{link}" if base and link.startswith("/") else (link if link.startswith("http") else None)


def send(db: Session, org_id, ch: sm.TeamsChannel, event: str, title: str, text: str, link: str | None = None,
         facts: dict | None = None, dedupe_key: str | None = None) -> sm.TeamsDelivery:
    d = sm.TeamsDelivery(organisation_id=org_id, channel_id=ch.id, event=event, title=title[:300], status="failed",
                         dedupe_key=dedupe_key)
    try:
        url = channel_url(db, org_id, ch)
        if not valid_teams_url(url):
            raise ih.IntegrationError("Saved webhook URL is not a Microsoft Teams webhook")
        r = ih.http_call("POST", url, json=card(url, title, text, _abs(link), facts), retries=1, timeout=10)
        d.http_status = r.status_code
        if 200 <= r.status_code < 300:
            d.status = "sent"
        else:
            d.error = f"Teams responded HTTP {r.status_code}: {r.text[:200]}"
    except ih.NotConnected as e:
        d.status, d.error = "not_connected", f"NOT CONNECTED: {e}"
    except ih.IntegrationError as e:
        d.error = str(e)[:500]
    except KeyError:
        d.error = "Webhook secret missing from the vault — re-save the channel"
    db.add(d)
    db.flush()
    return d


def dispatch(db: Session, org_id, event: str, title: str, text: str, link: str | None = None, project_id=None,
             facts: dict | None = None, dedupe_key: str | None = None) -> list[dict]:
    """Send an event to every enabled channel subscribed to it: org-wide channels always, project channels
    only for their project. A dedupe key is sent at most once per channel."""
    out = []
    chans = db.scalars(select(sm.TeamsChannel).where(sm.TeamsChannel.enabled)).all()
    for ch in chans:
        if event not in (ch.events or []):
            continue
        if ch.project_id and (not project_id or str(ch.project_id) != str(project_id)):
            continue
        if dedupe_key and db.scalar(select(sm.TeamsDelivery.id).where(sm.TeamsDelivery.channel_id == ch.id,
                                                                     sm.TeamsDelivery.dedupe_key == dedupe_key,
                                                                     sm.TeamsDelivery.status == "sent").limit(1)):
            continue
        d = send(db, org_id, ch, event, title, text, link, facts, dedupe_key)
        out.append({"channel": ch.name, "status": d.status, "error": d.error})
    return out


def state(db: Session, org_id) -> sm.SocialOrgState:
    st = db.get(sm.SocialOrgState, org_id)
    if not st:
        st = sm.SocialOrgState(organisation_id=org_id)
        db.add(st)
        db.flush()
    return st


def wants_sweep(db: Session) -> bool:
    for ch in db.scalars(select(sm.TeamsChannel).where(sm.TeamsChannel.enabled)):
        if any(e in (ch.events or []) for e in SWEEP_EVENTS):
            return True
    return False


def ensure_sweep(db: Session, org_id):
    """Keep exactly one pending sweep per 5-minute slot (self-rescheduling chain) plus a daily backstop."""
    if not wants_sweep(db):
        return None
    st = state(db, org_id)
    if st.teams_cursor is None:
        st.teams_cursor = datetime.now(timezone.utc)  # never flood a new channel with history
    if not db.scalar(select(m.JobSchedule.id).where(m.JobSchedule.kind == "social_teams_sweep")):
        db.add(m.JobSchedule(organisation_id=org_id, kind="social_teams_sweep", payload={}, frequency="daily",
                             next_run_at=datetime.now(timezone.utc) + timedelta(days=1)))
    nxt = datetime.now(timezone.utc) + SWEEP_EVERY
    slot = int(nxt.timestamp() // SWEEP_EVERY.total_seconds())
    return enqueue(db, org_id, "social_teams_sweep", {}, run_at=nxt, idempotency_key=f"teams_sweep:{org_id}:{slot}")


def sweep(db: Session, org_id) -> dict:
    st = state(db, org_id)
    now = datetime.now(timezone.utc)
    since = st.teams_cursor or now
    sent = {"approval_requested": 0, "monitor_critical": 0}
    for a in db.scalars(select(m.Approval).where(m.Approval.created_at > since, m.Approval.created_at <= now,
                                                 m.Approval.status == "pending").order_by(m.Approval.created_at)):
        pid = None
        if a.entity_type == "content_item" and a.entity_id:
            it = db.get(m.ContentItem, a.entity_id)
            pid = it.project_id if it else None
        r = dispatch(db, org_id, "approval_requested", f"Approval requested: {a.title}",
                     f"Awaiting {a.chain[a.step] if a.step < len(a.chain or []) else 'review'}" + (f" — {a.notes}" if a.notes else ""),
                     link="/approvals", project_id=pid, dedupe_key=f"approval:{a.id}")
        sent["approval_requested"] += sum(1 for x in r if x["status"] == "sent")
    projects = {p.id: p.name for p in db.scalars(select(m.Project))}
    for i in db.scalars(select(mm.MonitorIssue).where(mm.MonitorIssue.severity == "critical", mm.MonitorIssue.last_seen > since,
                                                      mm.MonitorIssue.status.in_(("open", "reopened")))):
        r = dispatch(db, org_id, "monitor_critical", f"Critical issue — {projects.get(i.project_id, 'project')}: {i.title}",
                     (i.recommendation or "")[:600], link=f"/monitor?project={i.project_id}", project_id=i.project_id,
                     facts={"URL": i.url} if i.url else None, dedupe_key=f"issue:{i.id}:{i.occurrences}")
        sent["monitor_critical"] += sum(1 for x in r if x["status"] == "sent")
    st.teams_cursor = now
    return sent
