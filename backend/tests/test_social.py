"""Social accounts, calendar, recurring rules, campaigns, publisher idempotency, NOT CONNECTED handling,
content automation and analytics."""
import uuid
from datetime import date, datetime, timedelta, timezone

from app import models as m
from app import models_social as sm
from app.db import tenant_session
from app.services import social as soc
from tests.test_social_helpers import drain as run_once
from sqlalchemy import select
from tests.test_social_helpers import account, connect, content, fake_http, set_state, state_of


def now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ 38 accounts
def test_account_mapping_health_and_not_connected(org_factory, monkeypatch):
    org = org_factory(plan="enterprise")  # client-user access is an Enterprise feature
    p1, p2 = org.project("Acme")["id"], org.project("Beta")["id"]
    lst = org.get(f"/social/accounts?project_id={p1}").json()
    assert {p["provider"]: p["status"] for p in lst["providers"]} == {"meta": "NOT_CONNECTED", "linkedin": "NOT_CONNECTED", "x": "NOT_CONNECTED"}
    d = org.get("/social/accounts/discover?provider=meta").json()
    assert d["connected"] is False and "NOT CONNECTED" in d["message"]

    a1 = account(org, p1, "facebook", "1001", "Acme Page")
    assert a1["is_default"] is True and a1["state"] == "NOT CONNECTED" and a1["publish_ready"] is False
    a2 = account(org, p1, "facebook", "1002", "Acme Offers")
    assert a2["is_default"] is False
    li = account(org, p1, "linkedin", "urn:li:organization:777", "Acme LinkedIn")
    assert li["external_id"] == "777"
    account(org, p2, "facebook", "1001", "Acme Page (shared)")  # same page can serve two projects
    assert org.post("/social/accounts", {"project_id": p1, "channel": "facebook", "external_id": "1001", "name": "dup"}).status_code == 409
    assert org.post("/social/accounts", {"project_id": p1, "channel": "tiktok", "external_id": "1", "name": "x"}).status_code == 400
    assert org.post("/social/accounts", {"project_id": p1, "channel": "x", "external_id": "bad id!", "name": "x"}).status_code == 400
    # switching default within (project, channel)
    r = org.patch(f"/social/accounts/{a2['id']}", {"is_default": True})
    assert r.json()["is_default"] is True
    accts = {a["id"]: a for a in org.get(f"/social/accounts?project_id={p1}").json()["accounts"]}
    assert accts[a1["id"]]["is_default"] is False and len(accts) == 3
    assert len(org.get("/social/accounts").json()["accounts"]) == 4

    # health without connection → NOT CONNECTED, account not marked broken
    h = org.post(f"/social/accounts/{a1['id']}/health").json()
    assert h["last_health_ok"] is False and "NOT CONNECTED" in h["last_error"] and h["status"] == "active"

    # connect Meta → discovery lists pages + linked IG business accounts; health ok
    fake = fake_http(monkeypatch)
    fake.on("GET", r"graph\.facebook\.com/v19\.0/me/accounts", json_body={"data": [
        {"id": "1001", "name": "Acme Page", "instagram_business_account": {"id": "17841", "username": "acme"}},
        {"id": "1003", "name": "Other Page"}]})
    fake.on("GET", r"graph\.facebook\.com/v19\.0/1001$", json_body={"id": "1001", "name": "Acme Page"})
    connect(org, "meta")
    d = org.get("/social/accounts/discover?provider=meta").json()
    assert d["connected"] is True
    by = {(x["channel"], x["external_id"]): x for x in d["accounts"]}
    assert by[("instagram", "17841")]["handle"] == "acme" and set(by[("facebook", "1001")]["mapped_project_ids"]) == {p1, p2}
    h = org.post(f"/social/accounts/{a1['id']}/health").json()
    assert h["last_health_ok"] is True and h["state"] == "ready" and h["publish_ready"] is True
    fake.on("GET", r"graph\.facebook\.com/v19\.0/1002$", status=400, json_body={"error": {"message": "Page not found"}})
    h = org.post(f"/social/accounts/{a2['id']}/health").json()
    assert h["status"] == "error" and "Page not found" in h["last_error"]

    # client sees only their project's accounts and cannot map accounts
    ch = org.invite_user("client")
    me = org.c.get("/api/auth/me", headers=ch).json()["user"]["id"]
    assert org.patch(f"/projects/{p2}", {"client_user_ids": [me]}).status_code == 200
    r = org.c.get("/api/social/accounts", headers=ch).json()
    assert [a["project_id"] for a in r["accounts"]] == [p2]
    assert org.c.get(f"/api/social/accounts?project_id={p1}", headers=ch).status_code == 404
    assert org.c.post("/api/social/accounts", headers=ch, json={"project_id": p2, "channel": "x", "external_id": "9", "name": "n"}).status_code == 403

    # delete cancels scheduled posts on that account
    it = content(org, p1, channel="facebook")
    s = org.post("/social/schedules", {"content_item_id": it, "account_ids": [a1["id"]], "scheduled_at": (now() + timedelta(days=2)).isoformat()})
    assert s.status_code == 200, s.text
    r = org.c.delete(f"/api/social/accounts/{a1['id']}", headers=org.h).json()
    assert r["cancelled_posts"] == 1


# ------------------------------------------------------------------ 36 recurring rule expansion
def test_rule_expansion_timezones(org):
    ny = "America/New_York"
    # 2026 DST in New York starts Sun 8 Mar and ends Sun 1 Nov
    start = datetime(2026, 3, 5, tzinfo=timezone.utc)
    s = soc.expand([0, 4], ["09:00"], ny, start, start + timedelta(days=7))  # Fri 6 Mar (EST) + Mon 9 Mar (EDT)
    assert s == [datetime(2026, 3, 6, 14, 0, tzinfo=timezone.utc), datetime(2026, 3, 9, 13, 0, tzinfo=timezone.utc)]
    # a wall-clock time skipped by spring-forward lands after the gap (02:30 EST offset → 03:30 EDT)
    s = soc.expand([6], ["02:30"], ny, datetime(2026, 3, 8, tzinfo=timezone.utc), datetime(2026, 3, 9, tzinfo=timezone.utc))
    assert s == [datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)]
    # an ambiguous fall-back time fires once, at its first occurrence (EDT)
    s = soc.expand([6], ["01:30"], ny, datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 2, tzinfo=timezone.utc))
    assert s == [datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)]
    # southern hemisphere, multiple times, date window respected
    syd = soc.expand([0, 1, 2, 3, 4, 5, 6], ["08:00", "18:30"], "Australia/Sydney", datetime(2026, 1, 1, tzinfo=timezone.utc),
                     datetime(2026, 1, 10, tzinfo=timezone.utc), start_date=date(2026, 1, 3), end_date=date(2026, 1, 4))
    assert syd == [datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc), datetime(2026, 1, 3, 7, 30, tzinfo=timezone.utc),
                   datetime(2026, 1, 3, 21, 0, tzinfo=timezone.utc), datetime(2026, 1, 4, 7, 30, tzinfo=timezone.utc)]
    # API preview + validation
    r = org.post("/social/rules/preview", {"weekdays": [0, 2, 4], "times": ["09:00"], "timezone": "Europe/London", "days": 14})
    assert r.status_code == 200 and 5 <= len(r.json()["slots"]) <= 7
    assert r.json()["slots"][0]["local"].split()[-1] in ("GMT", "BST")
    assert org.post("/social/rules/preview", {"weekdays": [0], "times": ["9am"], "timezone": "UTC"}).status_code == 400
    assert org.post("/social/rules/preview", {"weekdays": [0], "times": ["09:00"], "timezone": "Mars/Base"}).status_code == 400
    assert org.post("/social/rules/preview", {"weekdays": [7], "times": ["09:00"], "timezone": "UTC"}).status_code == 400


def test_rules_materialize_queue_and_evergreen(org):
    pid = org.project()["id"]
    a = account(org, pid)
    b = account(org, pid, ext="5555", name="Acme 2")
    i1, i2 = content(org, pid, "One"), content(org, pid, "Two")
    draft = content(org, pid, "Draft", state="draft")
    assert {i["title"] for i in org.get(f"/social/content/items?project_id={pid}").json()} == {"One", "Two"}
    assert [i["title"] for i in org.get(f"/social/content/items?project_id={pid}&states=draft").json()] == ["Draft"]
    base = {"project_id": pid, "name": "Daily", "account_ids": [a["id"], b["id"]], "weekdays": list(range(7)),
            "times": ["09:00", "15:00"], "timezone": "Europe/Berlin", "horizon_days": 7}
    assert org.post("/social/rules", {**base, "item_ids": [i1, draft]}).status_code == 409  # only approved can be queued
    r = org.post("/social/rules", {**base, "item_ids": [i1, i2]})
    assert r.status_code == 200, r.text
    rule = r.json()
    assert rule["materialized"]["created"] == 4  # 2 items × 2 accounts, one slot each
    assert rule["counts"] == {"scheduled": 4}
    again = org.post(f"/social/rules/{rule['id']}/materialize").json()
    assert again["created"] == 0  # idempotent
    i3 = content(org, pid, "Three")
    assert org.post(f"/social/rules/{rule['id']}/items", {"content_item_ids": [i3]}).json()["materialized"]["created"] == 2
    # each slot carries one item to both accounts; items are used once in queue mode
    with tenant_session(uuid.UUID(org.org_id)) as db:
        rows = db.execute(select(sm.SocialSchedule.content_item_id, sm.SocialSchedule.slot_at).where(sm.SocialSchedule.rule_id == uuid.UUID(rule["id"]))).all()
        slots = {}
        for iid, slot in rows:
            slots.setdefault(slot, set()).add(iid)
        assert len(slots) == 3 and all(len(v) == 1 for v in slots.values())
        assert all(s.astimezone(soc.zone("Europe/Berlin")).strftime("%H:%M") in ("09:00", "15:00") for s in slots)
    # timing change cancels future posts and re-materialises
    r = org.patch(f"/social/rules/{rule['id']}", {"times": ["10:00"]}).json()
    assert r["cancelled"] == 6 and r["materialized"]["created"] == 6
    # evergreen cycles approved/published items
    pub = content(org, pid, "Evergreen", state="published")
    ev = org.post("/social/rules", {**base, "name": "Evergreen", "mode": "evergreen", "account_ids": [a["id"]], "times": ["12:00"],
                                    "item_ids": [pub, i1]}).json()
    assert ev["materialized"]["created"] == 7  # one post per day for the 7-day horizon, items alternating
    assert org.c.delete(f"/api/social/rules/{ev['id']}", headers=org.h).json()["cancelled"] == 7
    # staff cannot create publishing rules
    staff = org.invite_user("staff")
    assert org.c.post("/api/social/rules", headers=staff, json={**base, "item_ids": []}).status_code == 403


# ------------------------------------------------------------------ 39 calendar + approval rule
def test_only_approved_can_be_scheduled_and_calendar(org):
    pid = org.project()["id"]
    a = account(org, pid)
    draft = content(org, pid, "Draft post", state="draft")
    review = content(org, pid, "In review", state="review")
    ok = content(org, pid, "Approved post")
    when = now() + timedelta(days=3)
    for it in (draft, review):
        r = org.post("/social/schedules", {"content_item_id": it, "account_ids": [a["id"]], "scheduled_at": when.isoformat()})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_approved"
    assert org.post("/social/schedules", {"content_item_id": ok, "account_ids": [a["id"]],
                                          "scheduled_at": (now() - timedelta(hours=2)).isoformat()}).status_code == 400
    long = content(org, pid, "Too long", body="x" * 300)
    assert org.post("/social/schedules", {"content_item_id": long, "account_ids": [a["id"]], "scheduled_at": when.isoformat()}).status_code == 400
    staff = org.invite_user("staff")
    assert org.c.post("/api/social/schedules", headers=staff, json={"content_item_id": ok, "account_ids": [a["id"]],
                                                                    "scheduled_at": when.isoformat()}).status_code == 403
    r = org.post("/social/schedules", {"content_item_id": ok, "account_ids": [a["id"]], "scheduled_at": when.isoformat()})
    assert r.status_code == 200
    sid = r.json()["scheduled"][0]["schedule_id"]
    # GBP post appears read-only
    with tenant_session(uuid.UUID(org.org_id)) as db:
        loc = m.GbpLocation(organisation_id=uuid.UUID(org.org_id), project_id=uuid.UUID(pid), name="Acme Springfield")
        db.add(loc)
        db.flush()
        db.add(m.GbpPost(organisation_id=uuid.UUID(org.org_id), location_id=loc.id, post_type="offer", body="10% off", status="posted",
                         posted_at=when + timedelta(hours=1)))
    rng = f"start={(now() - timedelta(days=1)).isoformat()}&end={(now() + timedelta(days=10)).isoformat()}".replace("+", "%2B")
    cal = org.get(f"/social/calendar?{rng}&project_id={pid}").json()
    kinds = [(e["kind"], e["title"]) for e in cal["events"]]
    assert ("post", "Approved post") in kinds and any(k == "gbp_post" for k, _ in kinds)
    post = next(e for e in cal["events"] if e["kind"] == "post")
    assert post["draggable"] is True and post["channel"] == "x" and post["status"] == "scheduled"
    assert {u["title"] for u in cal["unscheduled"]} == {"Too long"}
    assert [x["title"] for x in cal["awaiting_approval"]] == ["In review"]
    assert org.get(f"/social/calendar?{rng}&channel=linkedin").json()["events"] == []
    assert [e["kind"] for e in org.get(f"/social/calendar?{rng}&channel=gbp").json()["events"]] == ["gbp_post"]
    assert len(org.get(f"/social/calendar?{rng}&state=approved").json()["events"]) == 1
    assert org.get("/social/calendar?start=2026-01-01T00:00:00Z&end=2026-06-01T00:00:00Z").status_code == 400
    # drag to reschedule
    new = now() + timedelta(days=5)
    r = org.patch(f"/social/schedules/{sid}", {"scheduled_at": new.isoformat()})
    assert r.status_code == 200
    assert org.patch(f"/social/schedules/{sid}", {"scheduled_at": (now() - timedelta(days=1)).isoformat()}).status_code == 400
    staff_move = org.c.patch(f"/api/social/schedules/{sid}", headers=staff, json={"scheduled_at": new.isoformat()})
    assert staff_move.status_code == 403
    assert org.c.delete(f"/api/social/schedules/{sid}", headers=org.h).status_code == 200
    assert org.patch(f"/social/schedules/{sid}", {"scheduled_at": new.isoformat()}).status_code == 409


def test_reschedule_rejected_when_content_not_approved(org):
    pid = org.project()["id"]
    a = account(org, pid)
    it = content(org, pid)
    sid = org.post("/social/schedules", {"content_item_id": it, "account_ids": [a["id"]],
                                         "scheduled_at": (now() + timedelta(days=1)).isoformat()}).json()["scheduled"][0]["schedule_id"]
    set_state(org, it, "published")  # e.g. published by hand from the content studio
    r = org.patch(f"/social/schedules/{sid}", {"scheduled_at": (now() + timedelta(days=2)).isoformat()})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_approved"


# ------------------------------------------------------------------ 36 publisher
def _due(org, pid, acct, title="Due post", body="Hello from the publisher", **kw):
    it = content(org, pid, title, body=body, **kw)
    r = org.post("/social/schedules", {"content_item_id": it, "account_ids": [acct["id"]], "scheduled_at": now().isoformat()})
    assert r.status_code == 200, r.text
    return it, r.json()["scheduled"][0]["schedule_id"]


def _pub(org, sid):
    with tenant_session(uuid.UUID(org.org_id)) as db:
        ss = db.scalar(select(sm.SocialSchedule).where(sm.SocialSchedule.schedule_id == uuid.UUID(sid)))
        pj = db.get(m.PublishJob, ss.publish_job_id) if ss.publish_job_id else None
        sp = db.scalar(select(sm.SocialPublish).where(sm.SocialPublish.publish_job_id == pj.id)) if pj else None
        return ss.status, (pj.status if pj else None), sp


def test_publisher_posts_once_and_is_idempotent(org, monkeypatch):
    pid = org.project()["id"]
    fake = fake_http(monkeypatch)
    fake.on("POST", r"api\.twitter\.com/2/tweets$", status=201, json_body={"data": {"id": "1111", "text": "Hello"}})
    connect(org, "x")
    a = account(org, pid)
    it, sid = _due(org, pid, a)
    run_once()
    st, pjs, sp = _pub(org, sid)
    assert (st, pjs, sp.external_post_id) == ("published", "PUBLISHED", "1111")
    assert sp.external_url.endswith("/1111") and state_of(org, it) == "published"
    assert fake.count("POST", r"/2/tweets$") == 1
    call = next(c for c in fake.calls if c["url"].endswith("/2/tweets"))
    assert call["retries"] == 0 and call["json"]["text"] == "Hello from the publisher"
    assert call["headers"]["Authorization"] == "Bearer tok-x"
    # a retried / duplicated job never posts again
    oid = uuid.UUID(org.org_id)
    assert soc.publish(oid, sid, force=True)["idempotent"] is True
    with tenant_session(oid) as db:
        s = db.get(m.Schedule, uuid.UUID(sid))
        soc.enqueue_publish(db, oid, s, force=True, suffix=":dup")
    run_once()
    assert fake.count("POST", r"/2/tweets$") == 1

    # crash after claiming (SENDING with expired lease): the retry refuses to post and flags it for a human
    it2, sid2 = _due(org, pid, a, "Crashy")
    with tenant_session(oid) as db:
        ss = db.scalar(select(sm.SocialSchedule).where(sm.SocialSchedule.schedule_id == uuid.UUID(sid2)))
        pj = m.PublishJob(organisation_id=oid, content_item_id=uuid.UUID(it2), idempotency_key=f"social:{sid2}", status="SENDING", channel="x")
        db.add(pj)
        db.flush()
        ss.publish_job_id = pj.id
        db.add(sm.SocialPublish(organisation_id=oid, publish_job_id=pj.id, schedule_id=uuid.UUID(sid2), project_id=uuid.UUID(pid),
                                social_account_id=uuid.UUID(a["id"]), provider="x", attempts=1, sending_started_at=now() - timedelta(hours=1)))
    run_once()
    st, pjs, sp = _pub(org, sid2)
    assert (st, pjs, sp.error_code) == ("failed", "FAILED", "outcome_unknown") and state_of(org, it2) == "approved"
    assert fake.count("POST", r"/2/tweets$") == 1
    pj_id = org.get(f"/social/publish-jobs?project_id={pid}&status=FAILED").json()["items"][0]["id"]
    r = org.post(f"/social/publish-jobs/{pj_id}/retry", {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirm_required"
    fake.on("POST", r"api\.twitter\.com/2/tweets$", status=201, json_body={"data": {"id": "2222"}})
    assert org.post(f"/social/publish-jobs/{pj_id}/retry", {"confirm_not_posted": True}).status_code == 200
    run_once()
    st, pjs, sp = _pub(org, sid2)
    assert (pjs, sp.external_post_id, sp.attempts) == ("PUBLISHED", "2222", 2)
    assert fake.count("POST", r"/2/tweets$") == 2
    jobs = org.get(f"/social/publish-jobs?project_id={pid}").json()
    assert jobs["counts"]["PUBLISHED"] == 2


def test_platform_error_and_rescheduled_stale_job(org, monkeypatch):
    pid = org.project()["id"]
    fake = fake_http(monkeypatch)
    fake.on("POST", r"api\.linkedin\.com/rest/posts$", status=500, json_body={"message": "Internal error"})
    connect(org, "linkedin")
    a = account(org, pid, "linkedin", "777", "Acme LI")
    it, sid = _due(org, pid, a, body="LinkedIn update")
    run_once()
    st, pjs, sp = _pub(org, sid)
    assert (st, pjs, sp.error_code) == ("failed", "FAILED", "platform_error") and "HTTP 500" in sp.error
    assert state_of(org, it) == "approved" and fake.count("POST", r"/rest/posts$") == 1
    # retry after a definite platform error needs no confirmation and succeeds once
    fake.on("POST", r"api\.linkedin\.com/rest/posts$", status=201, json_body={}, headers={"x-restli-id": "urn:li:share:99"})
    pj_id = next(j["id"] for j in org.get("/social/publish-jobs").json()["items"] if j["schedule_id"] == sid)
    assert org.post(f"/social/publish-jobs/{pj_id}/retry", {}).status_code == 200
    run_once()
    st, pjs, sp = _pub(org, sid)
    assert pjs == "PUBLISHED" and sp.external_post_id == "urn:li:share:99" and "linkedin.com/feed/update/urn:li:share:99" in sp.external_url
    body = next(c for c in fake.calls if c["url"].endswith("/rest/posts"))["json"]
    assert body["author"] == "urn:li:organization:777" and body["commentary"] == "LinkedIn update"
    # a job whose post was moved later is stale and does nothing
    it3 = content(org, pid, "Later", body="later")
    sid3 = org.post("/social/schedules", {"content_item_id": it3, "account_ids": [a["id"]], "scheduled_at": now().isoformat()}).json()["scheduled"][0]["schedule_id"]
    org.patch(f"/social/schedules/{sid3}", {"scheduled_at": (now() + timedelta(days=1)).isoformat()})
    run_once()
    assert _pub(org, sid3)[0] == "scheduled" and fake.count("POST", r"/rest/posts$") == 2


def test_not_connected_never_marks_published(org, monkeypatch):
    pid = org.project()["id"]
    fake = fake_http(monkeypatch)
    a = account(org, pid, "facebook", "1001", "Acme Page")
    it, sid = _due(org, pid, a, body="Facebook update")
    run_once()
    st, pjs, sp = _pub(org, sid)
    assert (st, pjs, sp.error_code, sp.external_post_id) == ("failed", "FAILED", "not_connected", None)
    assert "NOT CONNECTED" in sp.error and state_of(org, it) == "approved"
    assert fake.calls == []
    notes = org.get("/notifications").json()["items"]
    assert any(n["kind"] == "publish_failed" and "NOT CONNECTED" in (n["body"] or "") for n in notes)
    an = org.get(f"/social/analytics?project_id={pid}").json()
    assert an["failed_alerts"][0]["error_code"] == "not_connected" and an["publishing"] == {"FAILED": 1}
    # instagram without an image fails validation at schedule time
    ig = account(org, pid, "instagram", "17841", "Acme IG")
    it2 = content(org, pid, "IG", channel="instagram")
    r = org.post("/social/schedules", {"content_item_id": it2, "account_ids": [ig["id"]], "scheduled_at": now().isoformat()})
    assert r.status_code == 400 and "image" in r.json()["detail"]
    assert org.c.put(f"/api/social/items/{it2}/meta", headers=org.h, json={"media_url": "ftp://x"}).status_code == 400
    assert org.c.put(f"/api/social/items/{it2}/meta", headers=org.h, json={"media_url": "https://cdn.example.com/a.jpg"}).status_code == 200
    # with Meta connected the IG two-step publish runs (container → publish)
    fake.on("POST", r"/17841/media$", json_body={"id": "c1"}).on("POST", r"/17841/media_publish$", json_body={"id": "m1"})
    connect(org, "meta")
    sid2 = org.post("/social/schedules", {"content_item_id": it2, "account_ids": [ig["id"]], "scheduled_at": now().isoformat()}).json()["scheduled"][0]["schedule_id"]
    run_once()
    assert _pub(org, sid2)[2].external_post_id == "m1"
    assert next(c for c in fake.calls if c["url"].endswith("/media"))["data"]["image_url"] == "https://cdn.example.com/a.jpg"


# ------------------------------------------------------------------ 36 campaigns
def test_campaign_grouping(org):
    pid = org.project()["id"]
    a = account(org, pid)
    s, e = (now() + timedelta(days=1)).date(), (now() + timedelta(days=10)).date()
    c = org.post("/social/campaigns", {"project_id": pid, "name": "Spring launch", "start_date": str(s), "end_date": str(e), "color": "#2266aa"})
    assert c.status_code == 200
    cid = c.json()["id"]
    assert org.post("/social/campaigns", {"project_id": pid, "name": "x", "start_date": str(e), "end_date": str(s)}).status_code == 400
    assert org.post("/social/campaigns", {"project_id": pid, "name": "x", "color": "red"}).status_code == 422
    i1, i2 = content(org, pid, "Teaser"), content(org, pid, "Launch")
    inside = datetime.combine(s + timedelta(days=2), datetime.min.time(), timezone.utc) + timedelta(hours=10)
    assert org.post("/social/schedules", {"content_item_id": i1, "account_ids": [a["id"]], "campaign_id": cid,
                                          "scheduled_at": (now() + timedelta(days=20)).isoformat()}).status_code == 400
    assert org.post("/social/schedules", {"content_item_id": i1, "account_ids": [a["id"]], "campaign_id": cid,
                                          "scheduled_at": inside.isoformat()}).status_code == 200
    rule = org.post("/social/rules", {"project_id": pid, "name": "Campaign cadence", "account_ids": [a["id"]], "weekdays": list(range(7)),
                                      "times": ["11:00"], "timezone": "UTC", "campaign_id": cid, "item_ids": [i2],
                                      "start_date": str(s), "end_date": str(e)}).json()
    assert rule["materialized"]["created"] == 1
    d = org.get(f"/social/campaigns/{cid}").json()
    assert d["total"] == 2 and d["counts"] == {"scheduled": 2} and {p["title"] for p in d["posts"]} == {"Teaser", "Launch"}
    assert not any(p["outside_window"] for p in d["posts"]) and d["rules"][0]["name"] == "Campaign cadence"
    rng = f"start={now().isoformat()}&end={(now() + timedelta(days=30)).isoformat()}".replace("+", "%2B")
    cal = org.get(f"/social/calendar?{rng}&campaign_id={cid}").json()["events"]
    assert len(cal) == 2 and all(ev["campaign"] == "Spring launch" and ev["color"] == "#2266aa" for ev in cal)
    assert [x["name"] for x in org.get(f"/social/campaigns?project_id={pid}").json()] == ["Spring launch"]
    assert org.patch(f"/social/campaigns/{cid}", {"status": "archived"}).json()["status"] == "archived"


# ------------------------------------------------------------------ 33 content automation
def test_content_automation_pipeline(org, monkeypatch):
    pid = org.project()["id"]
    a = account(org, pid)
    d1 = content(org, pid, "Existing idea", body=None, state="draft")
    csv_body = "topic,channel,link_url\nWhy drains block in winter,x,https://acme.test/drains\nFixed prices explained,,\n,,\n"
    r = org.c.post("/api/social/batches/csv", headers=org.h, data={"project_id": pid, "name": "Winter", "channel": "x"},
                   files={"file": ("t.csv", csv_body, "text/csv")})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["counts"]["draft"] == 2 and b["ai_connected"] is False and b["source"] == "csv"
    bad = org.c.post("/api/social/batches/csv", headers=org.h, data={"project_id": pid, "name": "Bad"},
                     files={"file": ("t.csv", "name\nfoo\n", "text/csv")})
    assert bad.status_code == 400
    b2 = org.post("/social/batches", {"project_id": pid, "name": "Picked", "channel": "x", "item_ids": [d1]}).json()
    assert [i["title"] for i in b2["items"]] == ["Existing idea"]

    # NoAI → NOT CONNECTED, items stay draft
    g = org.post(f"/social/batches/{b['id']}/generate").json()
    assert g["connected"] is False and "NOT CONNECTED" in g["message"]
    board = org.get(f"/social/batches/{b['id']}").json()
    assert all(i["gen_status"] == "not_connected" and i["state"] == "draft" for i in board["items"])

    # with an AI provider, generation runs through the job queue
    from app.services import agents
    connect(org, "claude")
    monkeypatch.setattr(agents, "llm", lambda db, org_id, prompt, max_tokens=1500, **kw: (f"Generated: {prompt.splitlines()[1][:60]} #plumbing", {"model": "test-model"}))
    g = org.post(f"/social/batches/{b['id']}/generate").json()
    assert g == {"connected": True, "queued": 2}
    run_once()
    board = org.get(f"/social/batches/{b['id']}").json()
    assert board["counts"]["generated"] == 2 and all(i["body"].startswith("Generated: Topic / brief:") for i in board["items"])

    # approval request → review (approval rows created by the content workflow)
    r = org.post(f"/social/batches/{b['id']}/request-approval").json()
    assert r["requested"] == 2
    board = org.get(f"/social/batches/{b['id']}").json()
    assert board["counts"]["review"] == 2 and all(i["approval_status"] == "pending" for i in board["items"])
    assert any(x["entity_type"] == "content_item" for x in org.get("/approvals").json())
    ids = [i["id"] for i in board["items"]]
    # nothing approved yet → nothing to schedule
    assert org.post(f"/social/batches/{b['id']}/schedule", {"mode": "spread", "account_ids": [a["id"]],
                                                            "start_at": (now() + timedelta(hours=1)).isoformat()}).status_code == 409
    staff = org.invite_user("staff")
    assert org.c.post(f"/api/social/batches/{b['id']}/approve", headers=staff, json={"item_ids": ids}).status_code == 403
    res = org.post(f"/social/batches/{b['id']}/approve", {"item_ids": ids, "notes": "Looks good"}).json()["results"]
    assert set(res.values()) == {"approved"}
    sch = org.post(f"/social/batches/{b['id']}/schedule", {"mode": "spread", "account_ids": [a["id"]],
                                                           "start_at": (now() + timedelta(hours=1)).isoformat(), "interval_hours": 24}).json()
    assert len(sch["scheduled"]) == 2
    t0, t1 = sorted(datetime.fromisoformat(x["at"]) for x in sch["scheduled"])
    assert t1 - t0 == timedelta(hours=24)
    board = org.get(f"/social/batches/{b['id']}").json()
    assert board["counts"]["scheduled"] == 2
    lst = org.get(f"/social/batches?project_id={pid}").json()
    assert {x["name"] for x in lst} == {"Winter", "Picked"}


# ------------------------------------------------------------------ 40 analytics
def test_analytics_csv_import_and_aggregates(org, monkeypatch):
    p1, p2 = org.project("Acme")["id"], org.project("Beta")["id"]
    account(org, p1, "facebook", "1001", "Acme Page")
    csv1 = ("channel,post_id,date,likes,comments,shares,impressions,clicks,title,captured_at\n"
            f"facebook,fb_1,{(now() - timedelta(days=3)).date()},10,2,1,1000,20,Post A,{(now() - timedelta(days=2)).isoformat()}\n"
            f"facebook,fb_1,{(now() - timedelta(days=3)).date()},40,5,3,2000,50,Post A,{(now() - timedelta(days=1)).isoformat()}\n"
            f"x,tw_1,{(now() - timedelta(days=2)).date()},7,1,2,500,10,Tweet B,\n"
            f"linkedin,li_1,{(now() - timedelta(days=60)).date()},100,0,0,900,0,Old,\n")
    r = org.c.post("/api/social/analytics/import", headers=org.h, data={"project_id": p1}, files={"file": ("m.csv", csv1, "text/csv")})
    assert r.status_code == 200 and r.json()["rows"] == 4
    org.c.post("/api/social/analytics/import", headers=org.h, data={"project_id": p2},
               files={"file": ("m.csv", "channel,post_id,likes,impressions\nx,tw_9,5,100\n", "text/csv")})
    for bad in ("channel,post_id,likes\nmyspace,1,2\n", "channel,post_id,likes\nx,1,-3\n", "channel,post_id,likes\nx,1,abc\n", "foo,bar\n1,2\n",
                "channel,post_id,date\nx,1,yesterday\n"):
        assert org.c.post("/api/social/analytics/import", headers=org.h, data={"project_id": p1},
                          files={"file": ("m.csv", bad, "text/csv")}).status_code == 400
    an = org.get(f"/social/analytics?project_id={p1}&days=30").json()
    t = an["totals"]  # latest snapshot per post only (fb_1 counted once at its latest values), 60-day-old post excluded
    assert (t["posts"], t["likes"], t["comments"], t["shares"], t["impressions"], t["clicks"]) == (2, 47, 6, 5, 2500, 60)
    assert t["engagement_rate"] == round(100 * (47 + 6 + 5 + 60) / 2500, 2)
    by = {c["channel"]: c for c in an["by_channel"]}
    assert by["facebook"]["likes"] == 40 and by["x"]["impressions"] == 500
    assert an["top_posts"][0]["external_post_id"] == "fb_1" and an["sources"] == ["csv"]
    assert len(an["trend"]) == 2
    assert org.get(f"/social/analytics?project_id={p1}&days=90").json()["totals"]["posts"] == 3
    allp = org.get("/social/analytics").json()
    assert {p["project"] for p in allp["by_project"]} == {"Acme", "Beta"}
    assert org.get("/social/analytics?channel=x").json()["totals"]["posts"] == 2
    # API sync needs a connection; with X connected, metrics are pulled for published posts
    assert org.post("/social/analytics/sync", {"project_id": p1}).status_code == 409
    fake = fake_http(monkeypatch)
    fake.on("POST", r"/2/tweets$", status=201, json_body={"data": {"id": "777"}})
    fake.on("GET", r"/2/tweets/777", json_body={"data": {"public_metrics": {"like_count": 3, "reply_count": 1, "retweet_count": 2,
                                                                             "quote_count": 1, "impression_count": 90}}})
    connect(org, "x")
    xa = account(org, p1, "x", "4242", "Acme X")
    _due(org, p1, xa, body="measure me")
    run_once()
    assert org.post("/social/analytics/sync", {"project_id": p1}).status_code == 200
    run_once()
    by = {c["channel"]: c for c in org.get(f"/social/analytics?project_id={p1}").json()["by_channel"]}
    assert by["x"]["posts"] == 2 and by["x"]["shares"] == 2 + 3 and by["x"]["impressions"] == 590
