"""Phase 6 growth: leads, forms (public flow + spam guard), connectors (HMAC), attribution math, HubSpot,
Calendly, onboarding scoring and tenant isolation."""
import base64
import hashlib
import hmac
import json
import time
import uuid

import pytest

from app.services import growth_crm, growth_leads as L
from app.services.growth_common import sign


def old_token(form_token, age=10):
    ts = str(int(time.time()) - age)
    return f"{ts}.{sign(form_token + ':' + ts)}"


def mk_form(org, pid, **settings):
    s = {"spam": {"min_seconds": 0, "rate_limit": 100, "rate_window_min": 10, "honeypot": True, "blocked_words": []}}
    s["spam"].update(settings.pop("spam", {}))
    s.update(settings)
    r = org.post("/forms", {"project_id": pid, "name": "Contact", "kind": "contact", "settings": s})
    assert r.status_code == 200, r.text
    return r.json()


def submit(client, token, data, rt=None, **kw):
    body = {**data, "_gq_t": rt or old_token(token)}
    return client.post(f"/api/public/forms/{token}/submit", content=json.dumps(body), headers={"Content-Type": "text/plain"}, **kw)


BASE = {"name": "Ada Lovelace", "email": "Ada.Lovelace+web@Gmail.com", "message": "Need an SEO audit please", "consent": True}


# ------------------------------------------------------------------ pure functions
def test_normalisation_and_similarity():
    assert L.norm_email(" A.d.a+x@GoogleMail.com ") == "ada@gmail.com"
    assert L.norm_email("bob+tag@acme.io") == "bob@acme.io"
    assert L.norm_email("nope") is None
    assert L.phone_key("+44 7700 900123") == L.phone_key("07700900123") == L.phone_key("0044 7700-900-123")
    assert L.phone_key("123") is None
    assert L.similarity("Jon Smith", "Acme Ltd", "John Smith", "ACME Limited") >= L.FUZZY_THRESHOLD
    assert L.similarity("Jon Smith", "Acme", "Mary Jones", "Globex") < 0.6


def test_classify_touch():
    assert L.classify({"utm_source": "Newsletter", "utm_medium": "Email"})["source"] == "newsletter"
    c = L.classify({"referrer": "https://www.google.com/search?q=x", "page_url": "https://acme.io/contact"})
    assert (c["source"], c["medium"]) == ("google", "organic")
    c = L.classify({"referrer": "https://www.linkedin.com/feed"})
    assert c["medium"] == "social"
    c = L.classify({"page_url": "https://acme.io/?utm_source=bing&utm_medium=cpc&utm_campaign=x"})
    assert (c["source"], c["medium"], c["campaign"]) == ("bing", "cpc", "x")
    assert L.classify({})["source"] == "(direct)"
    assert L.classify({"page_url": "https://acme.io/?gclid=abc"})["medium"] == "cpc"


def test_attribution_model_math():
    leads = [{"id": 1, "status": "won", "value": 900}, {"id": 2, "status": "new", "value": 0}, {"id": 3, "status": "won", "value": 100}]
    tps = {1: [{"source": "google"}, {"source": "newsletter"}, {"source": "direct"}],
           2: [{"source": "google"}],
           3: [{"source": "newsletter"}]}
    first = {r["key"]: r for r in L.attribute(leads, tps, "first_touch", "source")}
    assert first["google"]["leads"] == 2 and first["google"]["conversions"] == 1 and first["google"]["value"] == 900
    assert first["newsletter"]["value"] == 100
    last = {r["key"]: r for r in L.attribute(leads, tps, "last_touch", "source")}
    assert last["direct"]["value"] == 900 and last["google"]["conversions"] == 0
    lin = {r["key"]: r for r in L.attribute(leads, tps, "linear", "source")}
    assert lin["google"]["value"] == 300 and lin["newsletter"]["value"] == 400 and lin["direct"]["value"] == 300
    assert abs(lin["google"]["leads"] - (1 / 3 + 1)) < 1e-3
    assert abs(sum(r["leads"] for r in lin.values()) - 3) < 1e-3  # credit always sums to the lead count
    assert abs(sum(r["value"] for r in lin.values()) - 1000) < 0.02
    assert L.weights(4, "linear") == [0.25] * 4 and L.weights(3, "first_touch") == [1, 0, 0]


# ------------------------------------------------------------------ forms: public flow + spam guard
def test_public_form_flow_spam_guard_and_dedupe(org, client):
    pid = org.project("Leads")["id"]
    f = mk_form(org, pid, spam={"min_seconds": 3, "rate_limit": 4, "blocked_words": ["casino"]})
    tok = f["public_token"]
    assert "/api/public/forms/" in f["embed"]["script"] and f["embed"]["iframe"].startswith("<iframe")
    js = client.get(f"/api/public/forms/{tok}/embed.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and tok in js.text
    pub = client.get(f"/api/public/forms/{tok}").json()
    assert pub["schema"]["fields"][0]["key"] == "name" and pub["render_token"] and pub["honeypot_field"]
    # too fast (fresh render token) → rejected
    r = submit(client, tok, BASE, rt=pub["render_token"])
    assert r.status_code == 400 and r.json()["code"] == "too_fast"
    # forged/expired token
    assert submit(client, tok, BASE, rt="123.abc").json()["code"] == "bad_token"
    # honeypot → silently OK, nothing stored
    r = submit(client, tok, {**BASE, "_gq_website": "http://spam"})
    assert r.status_code == 200 and r.json()["ok"]
    assert org.get(f"/leads?project_id={pid}").json()["total"] == 0
    # rate limit: 4 per window per IP (3 used above) → 1 more allowed, then 429
    r = submit(client, tok, {**BASE, "utm_source": "newsletter", "utm_medium": "email", "utm_campaign": "spring"},
               )
    assert r.status_code == 200 and r.json()["ok"], r.text
    r = submit(client, tok, BASE)
    assert r.status_code == 429
    spam = org.get(f"/forms/{f['id']}/spam").json()["by_reason"]
    assert spam["too_fast"] == 1 and spam["honeypot"] == 1 and spam["bad_token"] == 1 and spam["rate_limited"] >= 1
    leads = org.get(f"/leads?project_id={pid}").json()
    assert leads["total"] == 1
    lead = leads["items"][0]
    assert lead["utm_source"] == "newsletter" and lead["name"] == "Ada Lovelace" and lead["source"] == "form"


def test_form_validation_blocked_words_and_assignment(org, client):
    pid = org.project("Leads")["id"]
    staff_h = org.invite_user("staff")
    staff = client.get("/api/auth/me", headers=staff_h).json()["user"]
    f = mk_form(org, pid, assignee_ids=[staff["id"]], spam={"blocked_words": ["casino"]})
    tok = f["public_token"]
    r = submit(client, tok, {"email": "bad-email", "message": "x"})
    assert r.status_code == 422
    errs = r.json()["errors"]
    assert {"name", "email", "message", "consent"} <= set(errs)
    r = submit(client, tok, {**BASE, "message": "Visit my CASINO now"})
    assert r.status_code == 400 and r.json()["code"] == "blocked"
    r = submit(client, tok, {**BASE, "phone": "+44 7700 900123", "_gq_referrer": "https://www.google.com/", "_gq_page": "https://acme.io/contact"})
    assert r.json()["ok"], r.text
    # same person, different email formatting + phone formatting → deduplicated into the same lead
    r = submit(client, tok, {**BASE, "email": "adalovelace@gmail.com", "phone": "07700 900123"})
    assert r.json()["ok"]
    data = org.get(f"/leads?project_id={pid}").json()
    assert data["total"] == 1
    lead = org.get(f"/leads/{data['items'][0]['id']}").json()
    assert lead["assignee_id"] == staff["id"]
    assert len(lead["submissions"]) == 2 and len(lead["touchpoints"]) == 2
    assert lead["touchpoints"][0]["source"] == "google" and lead["touchpoints"][0]["medium"] == "organic"
    assert any(a["kind"] == "deduplicated" for a in lead["activity"])
    notes = client.get("/api/notifications", headers=staff_h).json()["items"]
    assert any(n["kind"] == "lead_assigned" for n in notes)
    # paused form
    assert org.patch(f"/forms/{f['id']}", {"status": "paused"}).status_code == 200
    assert client.get(f"/api/public/forms/{tok}").status_code == 410
    # schema validation
    bad = org.post("/forms", {"project_id": pid, "name": "x", "kind": "custom", "schema": {"fields": [{"key": "A b", "type": "text"}]}})
    assert bad.status_code == 400
    bad = org.post("/forms", {"project_id": pid, "name": "x", "kind": "custom", "schema": {"fields": [{"key": "c", "type": "select"}]}})
    assert bad.status_code == 400


# ------------------------------------------------------------------ leads CRUD / pipeline / csv / fuzzy / merge
def test_lead_pipeline_notes_csv_fuzzy_merge(org, client):
    pid = org.project("CRM")["id"]
    a = org.post("/leads", {"project_id": pid, "name": "Jon Smith", "company": "Acme Ltd", "email": "jon@acme.io", "value": 1200}).json()
    assert a["created"] and a["status"] == "new"
    b = org.post("/leads", {"project_id": pid, "name": "John Smith", "company": "ACME Limited", "email": "john.smith@other.io"}).json()
    assert b["created"] and b["possible_duplicate_of"] == a["id"]
    dups = org.get(f"/leads/duplicates?project_id={pid}").json()
    assert dups and dups[0]["candidate"]["id"] == a["id"]
    same = org.post("/leads", {"project_id": pid, "email": "JON@acme.io", "phone": "555 123 4567"}).json()
    assert same["created"] is False and same["id"] == a["id"] and same["phone"] == "555 123 4567"
    for st in ("contacted", "qualified", "won"):
        r = org.patch(f"/leads/{a['id']}", {"status": st})
        assert r.status_code == 200 and r.json()["status"] == st
    assert org.patch(f"/leads/{a['id']}", {"status": "bogus"}).status_code == 400
    assert org.post(f"/leads/{a['id']}/notes", {"body": "Called, very interested"}).status_code == 200
    me = org.get("/auth/me").json()["user"]
    assert org.patch(f"/leads/{a['id']}", {"assignee_id": me["id"]}).json()["assignee_id"] == me["id"]
    d = org.get(f"/leads/{a['id']}").json()
    kinds = [x["kind"] for x in d["activity"]]
    assert kinds.count("status") == 3 and "note" in kinds and "deduplicated" in kinds and d["notes"][0]["body"].startswith("Called")
    # merge b into a
    r = org.post(f"/leads/{b['id']}/merge", {"into_id": a["id"]})
    assert r.status_code == 200
    assert org.get(f"/leads/{b['id']}").status_code == 404
    assert any(x["kind"] == "merged" for x in org.get(f"/leads/{a['id']}").json()["activity"])
    # email collision on edit
    c = org.post("/leads", {"project_id": pid, "email": "c@c.io"}).json()
    assert org.patch(f"/leads/{c['id']}", {"email": "jon@acme.io"}).status_code == 409
    # CSV import with dedupe + export
    csv_body = "Full Name,E-mail,Phone,Company,Status,Source\nJane Roe,jane@roe.io,,Roe Inc,qualified,linkedin\n" \
               "Jon S,jon@acme.io,,,,\n,,,,,\nBob,bob@x.io,,,,\n"
    r = org.c.post("/api/leads/import", headers=org.h, data={"project_id": pid}, files={"file": ("l.csv", csv_body, "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2 and r.json()["deduplicated"] == 1 and len(r.json()["errors"]) == 1
    leads = org.get(f"/leads?project_id={pid}").json()
    jane = next(x for x in leads["items"] if x["email"] == "jane@roe.io")
    assert jane["status"] == "qualified" and jane["utm_source"] == "linkedin"
    assert leads["counts"]["won"] == 1
    csv_out = org.get(f"/leads/export.csv?project_id={pid}")
    assert csv_out.status_code == 200 and "jane@roe.io" in csv_out.text
    # attribution report on these leads
    rep = org.get(f"/attribution/report?model=first_touch&dimension=source&project_id={pid}").json()
    assert rep["totals"]["conversions"] == 1 and rep["totals"]["value"] == 1200
    assert org.get("/attribution/report?model=nope").status_code == 400
    cmp_ = org.get(f"/attribution/compare?dimension=medium&project_id={pid}").json()
    assert {"first_touch", "last_touch", "linear"} <= set(cmp_["rows"][0])


# ------------------------------------------------------------------ connectors (HMAC)
def test_connectors_hmac_mapping_and_delivery_log(org, client):
    pid = org.project("Hooks")["id"]
    presets = {p["key"] for p in org.get("/forms/connectors/presets").json()}
    assert presets == {"generic", "typeform", "gravity_forms", "webflow", "facebook_lead_ads"}
    c = org.post("/forms/connectors", {"name": "Site", "preset": "generic", "project_id": pid}).json()
    secret, ep = c["secret"], c["endpoint"]
    path = ep[ep.index("/api/public"):]
    body = json.dumps({"name": "Grace", "email": "grace@navy.mil", "utm_source": "partner", "utm_medium": "referral"}).encode()
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(path, content=body).status_code == 401
    assert client.post(path, content=body, headers={"X-Genranq-Signature": "sha256=" + "0" * 64}).status_code == 401
    r = client.post(path, content=body, headers={"X-Genranq-Signature": good, "Content-Type": "application/json"})
    assert r.status_code == 200 and r.json()["accepted"] == 1
    tampered = body.replace(b"grace", b"evil")
    assert client.post(path, content=tampered, headers={"X-Genranq-Signature": good}).status_code == 401
    dl = org.get(f"/forms/connectors/{c['id']}/deliveries").json()
    assert [d["status"] for d in dl].count("rejected") == 3 and [d["status"] for d in dl].count("accepted") == 1
    lead = org.get(f"/leads?project_id={pid}").json()["items"][0]
    assert lead["email"] == "grace@navy.mil" and lead["utm_source"] == "partner" and lead["source"] == "webhook:generic"
    # typeform (base64 signature) + sample tester
    t = org.post("/forms/connectors", {"name": "TF", "preset": "typeform", "project_id": pid}).json()
    test = org.post(f"/forms/connectors/{t['id']}/test", {}).json()
    assert test["valid"] and test["lead"]["email"] == "grace@example.com" and test["touch"]["utm_source"] == "google"
    assert "service" in test["unmapped_fields"]
    raw = test["example_body"].encode()
    sig = "sha256=" + base64.b64encode(hmac.new(t["secret"].encode(), raw, hashlib.sha256).digest()).decode()
    tpath = t["endpoint"][t["endpoint"].index("/api/public"):]
    assert client.post(tpath, content=raw, headers={"Typeform-Signature": sig}).json()["accepted"] == 1
    # webflow (timestamped)
    w = org.post("/forms/connectors", {"name": "WF", "preset": "webflow", "project_id": pid}).json()
    wraw = json.dumps({"payload": {"pageUrl": "https://x.webflow.io/c?utm_source=webflowads", "data": {"Name": "D V", "Email": "dv@x.io"}}}).encode()
    ts = str(int(time.time() * 1000))
    wsig = hmac.new(w["secret"].encode(), f"{ts}:".encode() + wraw, hashlib.sha256).hexdigest()
    wpath = w["endpoint"][w["endpoint"].index("/api/public"):]
    assert client.post(wpath, content=wraw, headers={"x-webflow-timestamp": ts, "x-webflow-signature": wsig}).json()["accepted"] == 1
    old = str(int(time.time() * 1000) - 3600_000)
    osig = hmac.new(w["secret"].encode(), f"{old}:".encode() + wraw, hashlib.sha256).hexdigest()
    assert client.post(wpath, content=wraw, headers={"x-webflow-timestamp": old, "x-webflow-signature": osig}).status_code == 401
    # facebook lead ads: verified challenge, NOT CONNECTED Meta → logged, no fake lead
    fb = org.post("/forms/connectors", {"name": "FB", "preset": "facebook_lead_ads", "project_id": pid}).json()
    fpath = fb["endpoint"][fb["endpoint"].index("/api/public"):]
    ch = client.get(fpath, params={"hub.mode": "subscribe", "hub.verify_token": fb["verify_token"], "hub.challenge": "42"})
    assert ch.status_code == 200 and ch.text == "42"
    assert client.get(fpath, params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"}).status_code == 403
    fraw = json.dumps({"entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": "123"}}]}]}).encode()
    fsig = "sha256=" + hmac.new(fb["secret"].encode(), fraw, hashlib.sha256).hexdigest()
    r = client.post(fpath, content=fraw, headers={"X-Hub-Signature-256": fsig})
    assert r.status_code == 202 and r.json()["accepted"] == 0
    fdl = org.get(f"/forms/connectors/{fb['id']}/deliveries").json()
    assert fdl[0]["status"] == "not_connected" and "NOT CONNECTED" in fdl[0]["error"]
    assert org.get(f"/leads?project_id={pid}").json()["total"] == 3
    # mapping validation + disabled + rotate
    assert org.patch(f"/forms/connectors/{c['id']}", {"mapping": {"nope": "x"}}).status_code == 400
    org.patch(f"/forms/connectors/{c['id']}", {"enabled": False})
    assert client.post(path, content=body, headers={"X-Genranq-Signature": good}).status_code == 410
    org.patch(f"/forms/connectors/{c['id']}", {"enabled": True})
    rot = org.post(f"/forms/connectors/{c['id']}/rotate-secret").json()
    assert rot["secret"] != secret
    assert client.post(path, content=body, headers={"X-Genranq-Signature": good}).status_code == 401
    assert "secret" not in org.get("/forms/connectors").json()["items"][0]


# ------------------------------------------------------------------ HubSpot
class FakeHubSpot:
    def __init__(self):
        self.contacts = {}
        self.n = 100
        self.calls = []

    def __call__(self, method, url, **kw):
        import httpx
        path = url.replace("https://api.hubapi.com", "")
        body = kw.get("json") or {}
        self.calls.append((method, path))
        if path == "/crm/v3/objects/contacts/search":
            res = list(self.contacts.values())
            for fg in body.get("filterGroups") or []:
                for f in fg["filters"]:
                    if f["propertyName"] == "email":
                        res = [c for c in res if c["properties"].get("email") == f["value"]]
                    if f["propertyName"] == "lastmodifieddate":
                        res = [c for c in res if int(c["properties"]["lastmodifieddate"]) >= int(f["value"])]
            return httpx.Response(200, json={"results": res})
        if path == "/crm/v3/objects/contacts" and method == "POST":
            self.n += 1
            cid = str(self.n)
            self.contacts[cid] = {"id": cid, "properties": {**body["properties"], "lastmodifieddate": str(int(time.time() * 1000))}}
            return httpx.Response(201, json=self.contacts[cid])
        if path.startswith("/crm/v3/objects/contacts/") and method == "PATCH":
            cid = path.rsplit("/", 1)[1]
            self.contacts[cid]["properties"].update(body["properties"])
            self.contacts[cid]["properties"]["lastmodifieddate"] = str(int(time.time() * 1000))
            return httpx.Response(200, json=self.contacts[cid])
        return httpx.Response(404, json={})


def _connect(org, key, secret):
    from app.db import tenant_session
    from app.services import integrations as ih
    with tenant_session(uuid.UUID(org.org_id)) as db:
        c = ih.connect_api_key(db, uuid.UUID(org.org_id), None, key, secret, {})
        c.status = "CONNECTED"


def test_hubspot_not_connected_then_mocked_two_way_sync(org, monkeypatch):
    pid = org.project("HS")["id"]
    ov = org.get("/leads/hubspot").json()
    assert ov["connected"] is False and "NOT CONNECTED" in ov["message"]
    r = org.post("/leads/hubspot/sync")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected"
    assert org.get("/leads/hubspot").json()["logs"][0]["status"] == "not_connected"
    assert org.c.put("/api/leads/hubspot/settings", headers=org.h, json={"field_map": {"company": "company"}}).status_code == 400
    assert org.c.put("/api/leads/hubspot/settings", headers=org.h, json={"project_id": pid}).status_code == 200

    fake = FakeHubSpot()
    monkeypatch.setattr(growth_crm, "http_call", fake)
    _connect(org, "hubspot", "pat-na1-test-token")
    old = str(int((time.time() - 86400) * 1000))
    fake.contacts["7"] = {"id": "7", "properties": {"email": "hs@crm.io", "firstname": "Hub", "lastname": "Spot", "company": "HS Inc",
                                                    "hs_lead_status": "OPEN_DEAL", "lastmodifieddate": old}}
    local = org.post("/leads", {"project_id": pid, "name": "Local Only", "email": "local@acme.io", "company": "Acme"}).json()
    r = org.post("/leads/hubspot/sync")
    assert r.status_code == 200, r.text
    c = r.json()["counts"]
    assert c["pulled"] == 1 and c["created"] == 1 and c["pushed_created"] == 1
    leads = {x["email"]: x for x in org.get(f"/leads?project_id={pid}").json()["items"]}
    assert leads["hs@crm.io"]["crm_contact_id"] == "7" and leads["hs@crm.io"]["status"] == "qualified" and leads["hs@crm.io"]["name"] == "Hub Spot"
    pushed_id = leads["local@acme.io"]["crm_contact_id"]
    assert fake.contacts[pushed_id]["properties"]["company"] == "Acme" and fake.contacts[pushed_id]["properties"]["firstname"] == "Local"
    # conflict: HubSpot edited after the lead → HubSpot wins
    time.sleep(0.01)
    fake.contacts[pushed_id]["properties"].update(company="Acme Global", lastmodifieddate=str(int(time.time() * 1000)))
    res = org.post("/leads/hubspot/sync").json()
    c = res["counts"]
    assert c["updated_from_hubspot"] >= 1, res
    assert org.get(f"/leads/{local['id']}").json()["company"] == "Acme Global"
    # conflict: lead edited after HubSpot → lead wins and is pushed
    time.sleep(1.1)
    org.patch(f"/leads/{local['id']}", {"company": "Acme Worldwide"})
    c = org.post("/leads/hubspot/sync").json()["counts"]
    assert c["pushed_updated"] >= 1
    assert fake.contacts[pushed_id]["properties"]["company"] == "Acme Worldwide"


# ------------------------------------------------------------------ Calendly
def test_calendly_webhook_matching_and_signature(org, client):
    pid = org.project("Cal")["id"]
    lead = org.post("/leads", {"project_id": pid, "name": "Booker", "email": "Booker+x@acme.io"}).json()
    ov = org.get("/leads/calendly").json()
    assert ov["connected"] is False and ov["webhook_url"]
    path = ov["webhook_url"][ov["webhook_url"].index("/api/public"):]
    assert org.post("/leads/calendly/sync").status_code == 409
    me = org.get("/auth/me").json()["user"]
    bl = org.post("/leads/calendly/links", {"name": "Discovery call", "url": "https://calendly.com/acme/discovery", "user_id": me["id"], "project_id": pid}).json()
    assert "utm_content=gq_" in bl["tracked_url"]
    assert org.post("/leads/calendly/links", {"name": "x", "url": "https://evil.example/x"}).status_code == 400
    ev = {"event": "invitee.created", "payload": {
        "uri": "https://api.calendly.com/scheduled_events/E1/invitees/I1", "email": "booker@acme.io", "name": "Booker",
        "scheduled_event": {"uri": "https://api.calendly.com/scheduled_events/E1", "name": "Discovery call",
                            "start_time": "2026-11-01T10:00:00Z", "end_time": "2026-11-01T10:30:00Z"},
        "tracking": {"utm_source": "calendly", "utm_medium": "booking_link", "utm_content": f"gq_{bl['id'][:8]}"}}}
    r = client.post(path, content=json.dumps(ev))
    assert r.status_code == 200 and r.json()["lead_id"] == lead["id"] and r.json()["signature_verified"] is False
    d = org.get(f"/leads/{lead['id']}").json()
    assert d["status"] == "contacted" and d["bookings"][0]["booking_link_id"] == bl["id"] and d["assignee_id"] == me["id"]
    assert any(t["kind"] == "booking" for t in d["touchpoints"])
    # unknown invitee creates a lead in the default project only when configured
    assert org.c.put("/api/leads/calendly/settings", headers=org.h, json={"project_id": pid, "signing_key": "k" * 32}).status_code == 200
    ev2 = json.loads(json.dumps(ev))
    ev2["payload"]["uri"], ev2["payload"]["email"] = "https://api.calendly.com/scheduled_events/E2/invitees/I2", "new@person.io"
    raw = json.dumps(ev2).encode()
    assert client.post(path, content=raw).status_code == 401  # signing key configured → signature required
    t = str(int(time.time()))
    sig = hmac.new(b"k" * 32, f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    r = client.post(path, content=raw, headers={"Calendly-Webhook-Signature": f"t={t},v1={sig}"})
    assert r.status_code == 200 and r.json()["signature_verified"] and r.json()["lead_id"]
    # cancellation updates the same booking
    ev3 = {**ev, "event": "invitee.canceled"}
    raw3 = json.dumps(ev3).encode()
    sig3 = hmac.new(b"k" * 32, f"{t}.".encode() + raw3, hashlib.sha256).hexdigest()
    assert client.post(path, content=raw3, headers={"Calendly-Webhook-Signature": f"t={t},v1={sig3}"}).status_code == 200
    ov = org.get("/leads/calendly").json()
    assert ov["stats"]["total"] == 2 and ov["stats"]["matched"] == 2
    assert {b["status"] for b in ov["bookings"]} == {"active", "canceled"}


# ------------------------------------------------------------------ onboarding / profiling
def test_onboarding_scoring_and_form_attachment(org, client):
    pid = org.project("Onb")["id"]
    tpl = org.get("/onboarding/template").json()["template"]
    q = org.post("/onboarding/questionnaires", {**tpl, "attach_signup": True})
    assert q.status_code == 200, q.text
    qid = q.json()["id"]
    s = org.post(f"/onboarding/questionnaires/{qid}/score", {"answers": {"role": "business_owner", "need": ["seo", "links"],
                                                                        "budget": 2500, "timeline": "now"}}).json()
    # 20 + 10 + 10 + 25 + (budget ≥2000: 30, ≥500: 10) = 105 → Hot
    assert s["score"] == 105 and s["segment"] == "Hot" and not s["missing"]
    s = org.post(f"/onboarding/questionnaires/{qid}/score", {"answers": {"role": "student", "need": ["content"], "budget": 100, "timeline": "later"}}).json()
    assert s["score"] == -20 + 5 - 15 and s["segment"] == "Cold"
    assert org.post(f"/onboarding/questionnaires/{qid}/score", {"answers": {"role": "pirate"}}).status_code == 400
    assert org.post("/onboarding/questionnaires", {**tpl, "rules": [{"question": "ghost", "op": "eq", "value": 1, "points": 1}]}).status_code == 400
    # signup attachment
    su = org.get("/onboarding/signup").json()
    assert su["questionnaire"]["id"] == qid and su["response"] is None and "rules" not in su["questionnaire"]
    assert org.post("/onboarding/signup", {"answers": {"role": "agency", "need": ["seo"], "budget": 600, "timeline": "quarter"}}).json()["segment"] == "Warm"
    # attach to a form → public submission is scored onto the lead
    f = mk_form(org, pid, questionnaire_id=qid)
    pub = client.get(f"/api/public/forms/{f['public_token']}").json()
    assert pub["questionnaire"]["questions"][0]["key"] == "role" and "points" not in json.dumps(pub["questionnaire"])
    r = submit(client, f["public_token"], {**BASE, "_gq_answers": {"role": "business_owner", "need": ["local"], "budget": 3000, "timeline": "now"}})
    assert r.json()["ok"], r.text
    r2 = submit(client, f["public_token"], {**BASE, "email": "other@x.io", "_gq_answers": {"role": "agency"}})
    assert r2.status_code == 422 and "q_budget" in r2.json()["errors"]
    lead = org.get(f"/leads?project_id={pid}").json()["items"][0]
    assert lead["score"] == 20 + 10 + 25 + 30 + 10 and lead["segment"] == "Hot" and lead["custom"]["q_role"] == "business_owner"
    resp = org.get(f"/onboarding/responses?questionnaire_id={qid}").json()
    assert len(resp) == 2
    assert any(x["attached_forms"] for x in org.get("/onboarding/questionnaires").json())


# ------------------------------------------------------------------ isolation & roles
def test_leads_forms_crm_cross_org_isolation(org, org_factory, client):
    pid = org.project("Mine")["id"]
    lead = org.post("/leads", {"project_id": pid, "email": "secret@mine.io"}).json()
    f = mk_form(org, pid)
    c = org.post("/forms/connectors", {"name": "C", "preset": "generic", "project_id": pid}).json()
    qn = org.post("/onboarding/questionnaires", org.get("/onboarding/template").json()["template"]).json()
    other = org_factory()
    opid = other.project("Theirs")["id"]
    assert other.get(f"/leads/{lead['id']}").status_code == 404
    assert other.patch(f"/leads/{lead['id']}", {"status": "won"}).status_code == 404
    assert other.get("/leads").json()["total"] == 0
    assert other.get(f"/leads?project_id={pid}").status_code == 404
    assert other.post("/leads", {"project_id": pid, "email": "x@x.io"}).status_code == 404
    assert other.post(f"/leads/{lead['id']}/merge", {"into_id": lead["id"]}).status_code == 404
    o_lead = other.post("/leads", {"project_id": opid, "email": "theirs@x.io"}).json()
    assert org.post(f"/leads/{lead['id']}/merge", {"into_id": o_lead["id"]}).status_code == 404
    assert other.get(f"/forms/{f['id']}").status_code == 404 and other.get("/forms").json() == []
    assert other.get(f"/forms/connectors/{c['id']}/deliveries").status_code == 404
    assert other.post(f"/forms/connectors/{c['id']}/test", {}).status_code == 404
    assert other.get("/forms/connectors").json()["items"] == []
    assert other.get(f"/onboarding/questionnaires").json() == []
    assert other.post(f"/onboarding/questionnaires/{qn['id']}/score", {"answers": {}}).status_code == 404
    assert all(t["lead_id"] != lead["id"] and t["project_id"] == opid for t in other.get("/attribution/touchpoints").json())
    assert other.get("/leads/calendly").json()["bookings"] == []
    # public submission to org A's form lands only in org A
    submit(client, f["public_token"], {**BASE, "email": "pub@x.io"})
    assert [x["email"] for x in other.get("/leads").json()["items"]] == ["theirs@x.io"]
    assert org.get(f"/leads?project_id={pid}").json()["total"] == 2
    staff = org.invite_user("staff")
    assert client.post("/api/forms/connectors", headers=staff, json={"name": "x", "preset": "generic", "project_id": pid}).status_code == 403
    assert client.delete(f"/api/leads/{lead['id']}", headers=staff).status_code == 403


def test_client_role_sees_only_assigned_project_leads(org_factory, client):
    org = org_factory(plan="enterprise")  # client-user access is an Enterprise feature
    p1, p2 = org.project("Visible")["id"], org.project("Hidden")["id"]
    l1 = org.post("/leads", {"project_id": p1, "email": "one@x.io"}).json()
    l2 = org.post("/leads", {"project_id": p2, "email": "two@x.io"}).json()
    ch = org.invite_user("client")
    cu = client.get("/api/auth/me", headers=ch).json()["user"]
    assert org.patch(f"/projects/{p1}", {"client_user_ids": [cu["id"]]}).status_code == 200
    got = client.get("/api/leads", headers=ch).json()
    assert [x["id"] for x in got["items"]] == [l1["id"]]
    assert client.get(f"/api/leads/{l2['id']}", headers=ch).status_code == 404
    assert client.patch(f"/api/leads/{l1['id']}", headers=ch, json={"status": "won"}).status_code == 403
    assert client.get("/api/forms/connectors", headers=ch).status_code == 403
    assert client.get("/api/leads/hubspot", headers=ch).status_code == 403
