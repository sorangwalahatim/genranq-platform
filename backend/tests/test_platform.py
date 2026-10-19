"""Entitlements from the Pricing Matrix, job queue semantics, integrations hub."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import models as m
from app.db import tenant_session
from app.services import entitlements as ent
from app.services import jobs as jq
from app.services.integrations import RateLimited
from app.worker import run_once


def test_plans_come_from_pricing_matrix():
    p = ent.plans()
    assert p["essentials"]["features"]["rank_keywords"]["limit"] == 250
    assert p["advanced"]["features"]["audit_pages"]["limit"] == 5000
    assert p["essentials"]["features"]["storage_bytes"]["limit"] == 1024 ** 3
    assert p["enterprise"]["features"]["rank_keywords"].get("custom")
    assert not p["advanced"]["features"]["api_access"]["enabled"]
    assert p["enterprise"]["features"]["client_users"]["enabled"]
    assert not p["essentials"]["features"]["scheduled_audits"]["enabled"]
    assert p["advanced"]["features"]["scheduled_audits"]["enabled"]


def test_keyword_limit_and_white_label_gates(org_factory):
    o = org_factory(plan="essentials")
    p = o.project()
    r = o.post("/keywords", {"project_id": p["id"], "phrases": [f"kw {i}" for i in range(251)]})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "quota_exceeded"
    assert o.post("/keywords", {"project_id": p["id"], "phrases": [f"kw {i}" for i in range(250)]}).json()["added"] == 250
    assert o.post("/keywords", {"project_id": p["id"], "phrases": ["one more"]}).status_code == 402
    assert o.patch("/org", {"branding": {"white_label": True}}).status_code == 402
    site = o.post("/sites", {"project_id": p["id"], "domain": "example.com"}).json()
    r = o.post("/automation/schedules", {"kind": "scheduled_audit", "frequency": "weekly", "site_id": site["id"]})
    assert r.status_code == 402  # Essentials = real-time audits only


def test_audit_page_quota_consumed_and_capped(org_factory):
    o = org_factory(plan="essentials")
    p = o.project()
    site = o.post("/sites", {"project_id": p["id"], "domain": "example.com"}).json()
    r = o.post(f"/sites/{site['id']}/audits", {"max_pages": 400}).json()
    assert r["pages_granted"] == 250  # capped at the monthly allowance
    o.post(f"/jobs/{r['job_id']}/cancel")
    usage = {x["key"]: x for x in o.get("/billing/usage").json()["meters"]}
    assert usage["audit_pages"]["used"] == 0  # refunded on cancel


def test_job_retry_backoff_and_failure(org):
    calls = []

    @jq.handler("_test_flaky")
    def flaky(ctx):
        calls.append(ctx.attempt)
        raise RuntimeError("boom")

    @jq.handler("_test_ratelimited")
    def rl(ctx):
        raise RateLimited(30)

    with tenant_session(org.org_id) as db:
        j = jq.enqueue(db, org.org_id, "_test_flaky", max_attempts=2)
        j2 = jq.enqueue(db, org.org_id, "_test_ratelimited")
        jid, jid2 = j.id, j2.id
    run_once()
    with tenant_session(org.org_id) as db:
        j = db.get(m.Job, jid)
        assert j.status == "QUEUED" and j.attempts == 1 and j.run_at > datetime.now(timezone.utc)
        j.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        r = db.get(m.Job, jid2)
        assert r.status == "QUEUED" and r.attempts == 0  # rate limit doesn't burn an attempt
        r.run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    run_once()
    with tenant_session(org.org_id) as db:
        j = db.get(m.Job, jid)
        assert j.status == "FAILED" and "boom" in j.error
        assert db.scalar(select(m.Notification).where(m.Notification.kind == "job_failed"))
    assert org.post(f"/jobs/{jid}/retry").json()["status"] == "QUEUED"


def test_scheduler_enqueues_due_schedules_once(org):
    with tenant_session(org.org_id) as db:
        s = m.JobSchedule(organisation_id=org.org_id, kind="_noop", frequency="daily",
                          next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        db.add(s)
        db.flush()
        sid = s.id
    assert jq.scheduler_tick() >= 1
    jq.scheduler_tick()
    with tenant_session(org.org_id) as db:
        assert db.get(m.JobSchedule, sid).next_run_at > datetime.now(timezone.utc)
        assert len(db.scalars(select(m.Job).where(m.Job.kind == "_noop")).all()) == 1


def test_integrations_hub_lists_every_spec_provider(org):
    items = org.get("/integrations").json()
    assert len(items) == 23 and all(i["has_connector"] for i in items)
    assert all(i["status"] == "NOT_CONNECTED" for i in items)
    # rank check without a source is refused with an explicit NOT CONNECTED message
    p = org.project()
    r = org.post(f"/projects/{p['id']}/rank-check")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected"
    # api key is stored encrypted, never returned
    r = org.post("/integrations/semrush/connect", {"secret": "fake-semrush-key-1234"})
    assert r.status_code == 200 and r.json()["status"] in ("ERROR", "CONNECTED", "RATE_LIMITED")
    row = next(i for i in org.get("/integrations").json() if i["key"] == "semrush")
    assert row["credential_hint"].endswith("1234") and "fake-semrush" not in str(row)
    assert org.post("/integrations/semrush/disconnect").json()["status"] == "NOT_CONNECTED"
    # staff cannot manage integrations
    staff = org.invite_user("staff")
    assert org.c.post("/api/integrations/claude/connect", json={"secret": "x"}, headers=staff).status_code == 403


def test_schedule_rejects_negative_pages_and_consume_rejects_nonpositive(org):
    p = org.project()
    site = org.post("/sites", {"project_id": p["id"], "domain": "example.com"}).json()
    assert org.post("/automation/schedules", {"kind": "scheduled_audit", "frequency": "weekly", "site_id": site["id"], "max_pages": -1000000}).status_code == 422
    import pytest
    from fastapi import HTTPException
    with tenant_session(org.org_id) as db:
        with pytest.raises(HTTPException):
            ent.consume(db, org.org_id, "audit_pages", -5)
