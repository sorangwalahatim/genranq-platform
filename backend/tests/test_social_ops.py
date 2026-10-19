"""Report automation, Microsoft Teams, white-label domains, Chrome extension tokens + API, and cross-org isolation."""
import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from app import models as m
from app.db import tenant_session
from app.services import social_domains as dom
from app.services import social_teams as teams
from tests.test_social_helpers import drain as run_once
from tests.test_social_helpers import account, connect, content, fake_http

HOOK = "https://acme.webhook.office.com/webhookb2/abc@def/IncomingWebhook/123/456"


def now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ 46 Teams
def test_teams_url_validation():
    ok = [HOOK, "https://outlook.office.com/webhook/abc", "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?sig=x",
          "https://default1234.ab.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/x"]
    bad = ["http://acme.webhook.office.com/webhookb2/x", "https://hooks.slack.com/services/T/B/x", "https://evil.com/webhook.office.com/",
           "https://acme.webhook.office.com.evil.com/x", "https://user:pw@acme.webhook.office.com/x", "https://127.0.0.1/workflows/x",
           "javascript:alert(1)", "", "https://acme.webhook.office.com/ x"]
    assert all(teams.valid_teams_url(u) for u in ok)
    assert not any(teams.valid_teams_url(u) for u in bad)


def test_teams_channels_routing_test_send_and_sweep(org, monkeypatch):
    fake = fake_http(monkeypatch)
    fake.on("POST", r"webhook\.office\.com", status=200, json_body={})
    p1, p2 = org.project("Acme")["id"], org.project("Beta")["id"]
    assert org.post("/social/teams/channels", {"name": "Bad", "webhook_url": "https://hooks.slack.com/services/x"}).status_code == 400
    assert org.post("/social/teams/channels", {"name": "Bad", "webhook_url": HOOK, "events": ["nope"]}).status_code == 400
    assert org.post("/social/teams/channels", {"name": "Hub", "use_hub": True}).status_code == 409  # hub NOT CONNECTED
    org_ch = org.post("/social/teams/channels", {"name": "Agency ops", "webhook_url": HOOK}).json()
    assert "webhook_ref" not in org_ch and org_ch["webhook_hint"].startswith("acme.webhook.office.com")
    p2_ch = org.post("/social/teams/channels", {"name": "Beta client", "project_id": p2, "webhook_url": HOOK + "b",
                                                "events": ["publish_failed"]}).json()
    t = org.post(f"/social/teams/channels/{org_ch['id']}/test").json()
    assert t["status"] == "sent" and t["http_status"] == 200
    card = fake.calls[-1]["json"]
    assert card["@type"] == "MessageCard" and "Test message" in card["title"]
    staff = org.invite_user("staff")
    assert org.c.post(f"/api/social/teams/channels/{org_ch['id']}/test", headers=staff).status_code == 403

    # publish failure in project 1 → org-wide channel only; failure in project 2 → both
    for pid in (p1, p2):
        a = account(org, pid, "linkedin", "77", "LI")
        it = content(org, pid, channel="linkedin")
        org.post("/social/schedules", {"content_item_id": it, "account_ids": [a["id"]], "scheduled_at": now().isoformat()})
    n0 = len(fake.calls)
    run_once()
    log = org.get("/social/teams/log").json()
    pf = [d for d in log if d["event"] == "publish_failed"]
    assert sorted(d["channel"] for d in pf) == ["Agency ops", "Agency ops", "Beta client"] and all(d["status"] == "sent" for d in pf)
    assert len(fake.calls) - n0 == 3 and "NOT CONNECTED" in fake.calls[-1]["json"]["text"]

    # sweep forwards approvals requested + critical monitoring issues once each
    from app import models_monitor as mm
    oid = uuid.UUID(org.org_id)
    with tenant_session(oid) as db:
        db.add(m.Approval(organisation_id=oid, entity_type="task", title="Disavow 12 domains", chain=["manager"]))
        db.add(mm.MonitorIssue(organisation_id=oid, project_id=uuid.UUID(p1), kind="indexing_drop", category="seo", severity="critical",
                               title="Homepage noindexed", fingerprint="fp1", url="https://acme.test/", recommendation="Remove noindex"))
        db.add(mm.MonitorIssue(organisation_id=oid, project_id=uuid.UUID(p1), kind="x", category="seo", severity="low", title="Minor",
                               fingerprint="fp2"))
    with tenant_session(oid) as db:
        sent = teams.sweep(db, oid)
        assert sent == {"approval_requested": 1, "monitor_critical": 1}
        assert teams.sweep(db, oid) == {"approval_requested": 0, "monitor_critical": 0}
    # disabling a channel stops delivery; delete removes it
    assert org.patch(f"/social/teams/channels/{org_ch['id']}", {"enabled": False}).json()["enabled"] is False
    assert org.c.delete(f"/api/social/teams/channels/{p2_ch['id']}", headers=org.h).status_code == 200
    ov = org.get("/social/teams").json()
    assert [c["name"] for c in ov["channels"]] == ["Agency ops"] and ov["hub"]["status"] == "NOT_CONNECTED"
    # hub reuse: ms_teams connected with a webhook URL in the Integrations Hub
    connect(org, "ms_teams", {"webhook_url": HOOK + "hub"}, webhook_only=True)
    hub = org.post("/social/teams/channels", {"name": "Via hub", "use_hub": True, "events": ["report_ready"]}).json()
    assert org.post(f"/social/teams/channels/{hub['id']}/test").json()["status"] == "sent"
    assert fake.calls[-1]["url"] == HOOK + "hub"


# ------------------------------------------------------------------ 23 extension + 37 reports (uses a real audit)
def _pat(org, scopes=None, headers=None):
    r = org.c.post("/api/social/extension/tokens", headers=headers or org.h, json={"name": "Chrome", **({"scopes": scopes} if scopes else {})})
    assert r.status_code == 200, r.text
    return r.json()


def test_extension_tokens_scopes_and_audit_queue(org, org_factory, site_server, monkeypatch):
    pid = org.project("Acme")["id"]
    tok = _pat(org)
    raw = tok["token"]
    assert raw.startswith("gqx_") and tok["prefix"] == raw[:12]
    with tenant_session(uuid.UUID(org.org_id)) as db:
        from app import models_social as sm
        row = db.get(sm.ExtensionToken, uuid.UUID(tok["id"]))
        assert row.token_hash != raw and len(row.token_hash) == 64  # only the hash is stored
    lst = org.get("/social/extension/tokens").json()
    assert lst["tokens"][0]["active"] is True and "token" not in lst["tokens"][0] and "token_hash" not in lst["tokens"][0]
    H = {"Authorization": f"Bearer {raw}"}
    me = org.c.get("/api/social/ext/me", headers=H).json()
    assert [p["name"] for p in me["projects"]] == ["Acme"]
    # a PAT is not a session token for the rest of the API
    assert org.c.get("/api/projects", headers=H).status_code == 401
    assert org.c.get("/api/social/ext/me", headers={"Authorization": "Bearer gqx_nope"}).status_code == 401
    assert org.c.get("/api/social/ext/me", headers=org.h).status_code == 401  # JWTs don't work on the extension API

    # audit this site: creates the site in the project and queues the existing audit job (entitlement consumed)
    r = org.c.post("/api/social/ext/audit", headers=H, json={"project_id": pid, "url": site_server + "/index.html", "max_pages": 5})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["site_created"] is True and a["pages_granted"] == 5 and a["domain"] == site_server
    assert org.c.post("/api/social/ext/audit", headers=H, json={"project_id": pid, "url": site_server, "max_pages": 5}).status_code == 409
    usage = {x["key"]: x["used"] for x in org.get("/billing/usage").json()["meters"]}
    assert usage["audit_pages"] == 5
    run_once()
    aud = org.get(f"/audits/{a['audit_run_id']}").json()
    assert aud["status"] == "COMPLETED"
    assert org.c.post("/api/social/ext/audit", headers=H, json={"project_id": pid, "url": "notaurl", "max_pages": 5}).status_code == 400

    # track keyword + save competitor (with and without website)
    k = org.c.post("/api/social/ext/keyword", headers=H, json={"project_id": pid, "phrase": "Emergency Plumber Springfield"}).json()
    assert k["added"] == 1 and k["phrase"] == "emergency plumber springfield"
    assert org.c.post("/api/social/ext/keyword", headers=H, json={"project_id": pid, "phrase": "emergency plumber springfield"}).json()["added"] == 0
    assert any(x["phrase"] == "emergency plumber springfield" and "extension" in x["tags"] for x in org.get(f"/keywords?project_id={pid}").json())
    c = org.c.post("/api/social/ext/competitor", headers=H, json={"project_id": pid, "name": "Rival Plumbing", "website": "https://www.rival-plumbing.com/contact",
                                                                  "rating": 4.6, "reviews": 120, "address": "1 Main St",
                                                                  "maps_url": "https://www.google.com/maps/place/Rival"}).json()
    assert c["domain"] == "rival-plumbing.com" and c["competitor_id"]
    c2 = org.c.post("/api/social/ext/competitor", headers=H, json={"project_id": pid, "name": "No Site Co"}).json()
    assert c2["competitor_id"] is None
    saved = org.get(f"/social/saved-businesses?project_id={pid}").json()
    assert {s["name"] for s in saved} == {"Rival Plumbing", "No Site Co"}
    assert org.c.post("/api/social/ext/competitor", headers=H, json={"project_id": pid, "name": "x", "rating": 9}).status_code == 422

    # scope enforcement
    ro = _pat(org, ["read"])["token"]
    RO = {"Authorization": f"Bearer {ro}"}
    assert org.c.get("/api/social/ext/me", headers=RO).status_code == 200
    assert org.c.post("/api/social/ext/keyword", headers=RO, json={"project_id": pid, "phrase": "x"}).status_code == 403
    assert org.c.post("/api/social/ext/audit", headers=RO, json={"project_id": pid, "url": site_server}).status_code == 403
    assert org.c.post("/api/social/extension/tokens", headers=org.h, json={"name": "x", "scopes": ["admin"]}).status_code == 400
    # role enforcement: a staff token can create; a client cannot hold tokens
    staff = org.invite_user("staff")
    st = _pat(org, headers=staff)
    assert org.c.post("/api/social/ext/keyword", headers={"Authorization": f"Bearer {st['token']}"},
                      json={"project_id": pid, "phrase": "drain repair"}).status_code == 200
    assert len(org.get("/social/extension/tokens").json()["tokens"]) == 3  # owner sees all tokens in the org
    assert len(org.c.get("/api/social/extension/tokens", headers=staff).json()["tokens"]) == 1
    assert org.c.delete(f"/api/social/extension/tokens/{tok['id']}", headers=staff).status_code == 404  # not theirs
    # revoke
    assert org.c.delete(f"/api/social/extension/tokens/{tok['id']}", headers=org.h).status_code == 200
    assert org.c.get("/api/social/ext/me", headers=H).status_code == 401
    # deactivated user → token stops working immediately
    sid = org.c.get("/api/auth/me", headers=staff).json()["user"]["id"]
    org.patch(f"/team/{sid}", {"is_active": False})
    assert org.c.get("/api/social/ext/me", headers={"Authorization": f"Bearer {st['token']}"}).status_code == 401

    # other org's projects are invisible through a PAT
    other = org_factory()
    op = other.project("Other")["id"]
    assert org.c.post("/api/social/ext/keyword", headers=RO, json={"project_id": op, "phrase": "x"}).status_code == 403
    full = _pat(org)["token"]
    F = {"Authorization": f"Bearer {full}"}
    assert org.c.post("/api/social/ext/keyword", headers=F, json={"project_id": op, "phrase": "x"}).status_code == 404
    assert org.c.post("/api/social/ext/audit", headers=F, json={"project_id": op, "url": site_server}).status_code == 404

    # zip download built at request time
    z = org.c.get("/api/social/extension/download", headers=org.h)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "genranq-extension/manifest.json" in names and "genranq-extension/config.json" in names
    assert any(n.endswith("popup.html") for n in names) and any(n.endswith("background.js") for n in names)

    # ---- report automation on the same project (audit is completed now)
    _report_automation(org, pid, monkeypatch)


def _report_automation(org, pid, monkeypatch):
    fake = fake_http(monkeypatch)
    fake.on("POST", r"webhook\.office\.com", status=200, json_body={})
    st = org.get("/social/report-schedules").json()
    assert st["email"]["configured"] is False and "NOT CONNECTED" in st["email"]["message"] and st["plan"]["scheduled_reports"] is True
    ch = org.post("/social/teams/channels", {"name": "Reports", "project_id": pid, "webhook_url": HOOK, "events": ["report_ready"]}).json()
    assert ch["id"]
    staff = org.invite_user("staff")
    staff_id = org.c.get("/api/auth/me", headers=staff).json()["user"]["id"]
    bad = {"project_id": pid, "name": "x", "contents": ["audit_pdf"], "frequency": "daily"}
    assert org.post("/social/report-schedules", bad).status_code == 400
    assert org.post("/social/report-schedules", {**bad, "frequency": "weekly", "contents": ["nope"]}).status_code == 400
    assert org.post("/social/report-schedules", {**bad, "frequency": "weekly", "recipient_emails": ["not-an-email"]}).status_code == 400
    r = org.post("/social/report-schedules", {"project_id": pid, "name": "Weekly client pack", "frequency": "weekly",
                                              "contents": ["audit_pdf", "audit_xlsx", "monitoring", "rank_changes", "social"],
                                              "recipient_user_ids": [staff_id], "recipient_emails": ["client@example.com"],
                                              "channels": {"teams": True, "email": True}, "white_label": True})
    assert r.status_code == 200, r.text
    rs = r.json()
    assert rs["next_run_at"] and rs["job_schedule_id"]
    with tenant_session(uuid.UUID(org.org_id)) as db:
        js = db.get(m.JobSchedule, uuid.UUID(rs["job_schedule_id"]))
        assert js.kind == "social_report_run" and js.frequency == "weekly"
    assert org.post(f"/social/report-schedules/{rs['id']}/run").status_code == 200
    run_once()
    d = org.get(f"/social/report-deliveries?schedule_id={rs['id']}").json()[0]
    assert d["status"] == "partial"  # email NOT CONNECTED, everything else delivered
    assert d["channels"]["in_app"]["status"] == "sent" and d["channels"]["teams"]["status"] == "sent"
    assert d["channels"]["email"]["status"] == "not_connected" and "SMTP" in d["channels"]["email"]["error"]
    kinds = sorted(f["kind"] for f in d["files"])
    assert kinds == ["audit_pdf", "audit_xlsx", "summary"]
    for f in d["files"]:
        dl = org.c.get(f"/api/social/report-deliveries/{d['id']}/files/{f['stored_file_id']}", headers=org.h)
        assert dl.status_code == 200 and len(dl.content) == f["size_bytes"]
        if f["kind"] == "summary":
            assert dl.content[:4] == b"%PDF"
    assert org.c.get(f"/api/social/report-deliveries/{d['id']}/files/{uuid.uuid4()}", headers=org.h).status_code == 404
    notes = org.c.get("/api/notifications", headers=staff).json()["items"]
    assert any(n["kind"] == "report_ready" and "Weekly client pack" in n["title"] for n in notes)
    tcall = next(c for c in reversed(fake.calls) if "webhook.office.com" in c["url"])
    assert "Report ready" in tcall["json"]["title"]
    assert len([x for x in org.get("/reports").json() if x["white_label"]]) == 2  # audit files also listed with on-demand reports

    # SMTP configured → email sent with attachments (SMTP mocked)
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append({"host": host})

        def starttls(self, context=None):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            sent[-1]["to"] = msg["To"]
            sent[-1]["attachments"] = [p.get_filename() for p in msg.iter_attachments()]

        def quit(self):
            pass

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "reports@agency.test")
    org.post(f"/social/report-schedules/{rs['id']}/run")
    run_once()
    d = org.get(f"/social/report-deliveries?schedule_id={rs['id']}").json()[0]
    assert d["status"] == "delivered" and d["channels"]["email"]["status"] == "sent"
    assert "client@example.com" in sent[0]["to"] and len(sent[0]["attachments"]) == 3
    # disable + delete
    assert org.patch(f"/social/report-schedules/{rs['id']}", {"enabled": False}).json()["next_run_at"] is None
    assert org.c.delete(f"/api/social/report-schedules/{rs['id']}", headers=org.h).status_code == 200


def test_report_automation_plan_gate(org_factory):
    ess = org_factory(plan="essentials")
    pid = ess.project()["id"]
    r = ess.post("/social/report-schedules", {"project_id": pid, "name": "x", "contents": ["monitoring"]})
    assert r.status_code == 402
    assert ess.post("/social/white-label/domains", {"domain": "portal.agency.com"}).status_code == 402


# ------------------------------------------------------------------ 47 white-label domains
def test_domain_verification_flow(org, org_factory, monkeypatch):
    for bad in ("not a domain", "localhost", "192.168.0.1", "app.genranq.com", "-bad-.com"):
        assert org.post("/social/white-label/domains", {"domain": bad}).status_code == 400
    r = org.post("/social/white-label/domains", {"domain": "https://Portal.Agency.com/"})
    assert r.status_code == 200
    d = r.json()
    assert d["domain"] == "portal.agency.com" and d["status"] == "pending" and d["txt_name"] == "_genranq-verify.portal.agency.com"
    assert d["txt_value"] == f"genranq-verify={d['verification_token']}"
    assert org.post("/social/white-label/domains", {"domain": "portal.agency.com"}).status_code == 409
    dns = {"txt": {}, "cname": {}}
    monkeypatch.setattr(dom, "lookup_txt", lambda name: dns["txt"].get(name, []))
    monkeypatch.setattr(dom, "lookup_cname", lambda name: dns["cname"].get(name))
    monkeypatch.setattr(dom, "HAVE_DNSPYTHON", True)
    v = org.post(f"/social/white-label/domains/{d['id']}/verify").json()
    assert v["status"] == "failed" and v["txt_ok"] is False and "not found" in v["last_error"]  # nothing resolves yet
    dns["cname"]["portal.agency.com"] = dom.target()
    v = org.post(f"/social/white-label/domains/{d['id']}/verify").json()
    assert v["status"] == "failed" and v["cname_ok"] is True  # routing alone does not prove ownership
    dns["txt"]["_genranq-verify.portal.agency.com"] = ["something-else", d["txt_value"]]
    v = org.post(f"/social/white-label/domains/{d['id']}/verify").json()
    assert v["status"] == "verified" and v["txt_ok"] and v["verified_at"] and v["tls_status"] == "awaiting_operator"
    wl = org.get("/social/white-label").json()
    assert wl["branding"]["custom_domain"] == "portal.agency.com" and wl["operator_steps"] and wl["cname_target"] == dom.target()
    # another org cannot verify the same hostname even with its own TXT token
    other = org_factory()
    od = other.post("/social/white-label/domains", {"domain": "portal.agency.com"}).json()
    dns["txt"]["_genranq-verify.portal.agency.com"].append(od["txt_value"])
    ov = other.post(f"/social/white-label/domains/{od['id']}/verify").json()
    assert ov["status"] == "failed" and "another organisation" in ov["last_error"]
    # record removed → verification lapses
    dns["txt"]["_genranq-verify.portal.agency.com"] = []
    v = org.post(f"/social/white-label/domains/{d['id']}/verify").json()
    assert v["status"] == "failed" and v["verified_at"] is None
    # without dnspython, a correct CNAME is the verification method
    monkeypatch.setattr(dom, "HAVE_DNSPYTHON", False)
    v = org.post(f"/social/white-label/domains/{d['id']}/verify").json()
    assert v["status"] == "verified" and v["method"] == "cname"
    # portal title / support email
    r = org.c.put("/api/social/white-label", headers=org.h, json={"portal_title": "Acme Client Portal", "support_email": "help@agency.com"})
    assert r.status_code == 200 and r.json()["portal_title"] == "Acme Client Portal"
    assert org.c.put("/api/social/white-label", headers=org.h, json={"support_email": "nope"}).status_code == 400
    staff = org.invite_user("staff")
    assert org.c.post("/api/social/white-label/domains", headers=staff, json={"domain": "x.agency.com"}).status_code == 403
    assert org.c.delete(f"/api/social/white-label/domains/{d['id']}", headers=org.h).status_code == 200
    assert "custom_domain" not in org.get("/social/white-label").json()["branding"]


# ------------------------------------------------------------------ cross-org isolation for every endpoint family
def test_cross_org_isolation(org, org_factory, monkeypatch):
    fake = fake_http(monkeypatch)
    fake.on("POST", r"webhook\.office\.com", status=200, json_body={})
    pid = org.project("Mine")["id"]
    a = account(org, pid)
    it = content(org, pid)
    sid = org.post("/social/schedules", {"content_item_id": it, "account_ids": [a["id"]], "scheduled_at": (now() + timedelta(days=1)).isoformat()}).json()["scheduled"][0]["schedule_id"]
    camp = org.post("/social/campaigns", {"project_id": pid, "name": "Mine"}).json()
    rule = org.post("/social/rules", {"project_id": pid, "name": "R", "account_ids": [a["id"]], "weekdays": [0], "times": ["09:00"]}).json()
    batch = org.post("/social/batches", {"project_id": pid, "name": "B", "topics": ["t1"]}).json()
    org.c.post("/api/social/analytics/import", headers=org.h, data={"project_id": pid}, files={"file": ("m.csv", "channel,post_id,likes\nx,p1,5\n", "text/csv")})
    rs = org.post("/social/report-schedules", {"project_id": pid, "name": "Mine", "contents": ["monitoring"]}).json()
    org.post(f"/social/report-schedules/{rs['id']}/run")
    run_once()
    dl = org.get("/social/report-deliveries").json()[0]
    ch = org.post("/social/teams/channels", {"name": "Mine", "webhook_url": HOOK}).json()
    dm = org.post("/social/white-label/domains", {"domain": "mine.agency.com"}).json()
    tok = _pat(org)

    other = org_factory()
    op = other.project("Theirs")["id"]
    # lists are empty
    assert other.get("/social/accounts").json()["accounts"] == []
    assert other.get("/social/campaigns").json() == [] and other.get("/social/rules").json() == []
    assert other.get("/social/batches").json() == [] and other.get("/social/publish-jobs").json()["items"] == []
    assert other.get("/social/analytics").json()["totals"]["posts"] == 0
    assert other.get("/social/report-schedules").json()["schedules"] == [] and other.get("/social/report-deliveries").json() == []
    assert other.get("/social/teams").json()["channels"] == [] and other.get("/social/teams/log").json() == []
    assert other.get("/social/white-label").json()["domains"] == []
    assert other.get("/social/extension/tokens").json()["tokens"] == []
    rng = f"start={now().isoformat()}&end={(now() + timedelta(days=5)).isoformat()}".replace("+", "%2B")
    assert other.get(f"/social/calendar?{rng}").json()["events"] == []
    # direct access by id → 404
    c, h = other.c, other.h
    for method, path, body in [
        ("get", f"/api/social/accounts?project_id={pid}", None), ("patch", f"/api/social/accounts/{a['id']}", {"name": "x"}),
        ("post", f"/api/social/accounts/{a['id']}/health", None), ("delete", f"/api/social/accounts/{a['id']}", None),
        ("patch", f"/api/social/schedules/{sid}", {"scheduled_at": (now() + timedelta(days=2)).isoformat()}),
        ("delete", f"/api/social/schedules/{sid}", None), ("get", f"/api/social/campaigns/{camp['id']}", None),
        ("patch", f"/api/social/campaigns/{camp['id']}", {"name": "x"}), ("get", f"/api/social/rules/{rule['id']}/preview", None),
        ("patch", f"/api/social/rules/{rule['id']}", {"name": "x"}), ("delete", f"/api/social/rules/{rule['id']}", None),
        ("get", f"/api/social/batches/{batch['id']}", None), ("post", f"/api/social/batches/{batch['id']}/generate", None),
        ("put", f"/api/social/items/{it}/meta", {"link_url": "https://x.test"}), ("post", f"/api/social/items/{it}/request-approval", None),
        ("patch", f"/api/social/report-schedules/{rs['id']}", {"name": "x"}), ("post", f"/api/social/report-schedules/{rs['id']}/run", None),
        ("get", f"/api/social/report-deliveries/{dl['id']}/files/{dl['files'][0]['stored_file_id']}", None),
        ("post", f"/api/social/teams/channels/{ch['id']}/test", None), ("delete", f"/api/social/teams/channels/{ch['id']}", None),
        ("post", f"/api/social/white-label/domains/{dm['id']}/verify", None), ("delete", f"/api/social/white-label/domains/{dm['id']}", None),
        ("delete", f"/api/social/extension/tokens/{tok['id']}", None), ("get", f"/api/social/saved-businesses?project_id={pid}", None),
        ("get", f"/api/social/content/drafts?project_id={pid}", None), ("get", f"/api/social/analytics?project_id={pid}", None),
    ]:
        kw = {"json": body} if body is not None else {}
        r = getattr(c, method)(path, headers=h, **kw)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
    # cross-org references in bodies are rejected
    assert other.post("/social/schedules", {"content_item_id": it, "account_ids": [a["id"]], "scheduled_at": (now() + timedelta(days=1)).isoformat()}).status_code == 404
    assert other.post("/social/rules", {"project_id": op, "name": "R", "account_ids": [a["id"]], "weekdays": [0], "times": ["09:00"]}).status_code == 400
    assert other.post("/social/batches", {"project_id": op, "name": "B", "item_ids": [it]}).status_code == 400
    assert other.post("/social/accounts", {"project_id": pid, "channel": "x", "external_id": "1", "name": "n"}).status_code == 404
    assert other.post("/social/report-schedules", {"project_id": pid, "name": "x", "contents": ["monitoring"]}).status_code == 404
    # the owner's data is intact
    assert org.get(f"/social/campaigns/{camp['id']}").json()["name"] == "Mine"
    assert org.c.get("/api/social/ext/me", headers={"Authorization": f"Bearer {tok['token']}"}).status_code == 200
