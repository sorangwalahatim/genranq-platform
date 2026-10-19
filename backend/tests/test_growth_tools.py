"""Phase 6 growth: free tools (authenticated + public with email gating and per-IP rate limits) against
fixture pages, domain age with mocked RDAP, public backlink monitor, service orders fulfilled by the platform."""
import uuid
from datetime import timedelta

import httpx

from app import models_growth as g
from app.db import tenant_session
from app.services import growth_tools
from app.services.growth_common import utcnow
from app.worker import run_once
from tests.test_growth_market import TARGET, fund, ledger_ok, make_platform_admin

RDAP = {"ldhName": "EXAMPLE.COM", "status": ["client transfer prohibited"],
        "events": [{"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
                   {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"},
                   {"eventAction": "last changed", "eventDate": "2025-08-14T07:01:38Z"}],
        "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", "RESERVED-IANA"]]]}],
        "nameservers": [{"ldhName": "A.IANA-SERVERS.NET"}]}


def test_domain_age_mocked_rdap_and_unreachable(org, monkeypatch):
    seen = []

    def ok(method, url, **kw):
        seen.append(url)
        return httpx.Response(200, json=RDAP) if "example.com" in url else httpx.Response(404)
    monkeypatch.setattr(growth_tools, "http_call", ok)
    r = org.post("/tools/domain-age/run", {"domain": "https://www.Example.com/path"}).json()
    assert r["status"] == "COMPLETED", r
    res = r["result"]
    assert seen[0] == "https://rdap.org/domain/example.com"
    assert res["registered"].startswith("1995") and res["registrar"] == "RESERVED-IANA" and res["age_years"] > 30
    assert res["nameservers"] == ["a.iana-servers.net"]
    r = org.post("/tools/domain-age/run", {"domain": "unregistered-zzz.com"}).json()
    assert r["status"] == "COMPLETED" and r["result"]["found"] is False
    r = org.post("/tools/domain-age/run", {"domain": "not a domain"}).json()
    assert r["status"] == "FAILED" and "valid domain" in r["error"]

    def down(method, url, **kw):
        raise growth_tools.IntegrationError("network error: connection refused")
    monkeypatch.setattr(growth_tools, "http_call", down)
    r = org.post("/tools/domain-age/run", {"domain": "example.com"}).json()
    assert r["status"] == "FAILED" and "could not be reached" in r["error"] and r["result"] == {}


def test_broken_links_dofollow_link_analyzer_on_fixtures(org, site_server):
    r = org.post("/tools/broken-links/run", {"url": site_server + "/growth/site/index.html", "max_pages": 10})
    assert r.status_code == 200 and r.json()["status"] == "QUEUED"
    run_once()
    res = org.get(f"/tools/runs/{r.json()['id']}").json()
    assert res["status"] == "COMPLETED", res
    out = res["result"]
    broken = {b["href"].rsplit("/", 1)[-1]: b for b in out["broken"]}
    assert {"missing.html", "gone.html", "dead-external"} <= set(broken)
    assert broken["missing.html"]["status"] == 404 and broken["missing.html"]["internal"]
    assert broken["gone.html"]["found_on"][0].endswith("/growth/site/a.html")
    assert not broken["dead-external"]["internal"]
    assert out["pages_crawled"] == 3 and out["broken_internal"] == 2 and out["broken_external"] == 1
    # dofollow / nofollow checker
    d = org.post("/tools/dofollow/run", {"url": site_server + "/growth/rel.html"}).json()["result"]
    assert d["total"] == 5 and d["dofollow"] == 2 and d["nofollow"] == 3 and d["sponsored"] == 1 and d["ugc"] == 1
    rels = {l["href"]: l["follow"] for l in d["links"]}
    assert rels["https://example.com/a"] == "dofollow" and rels["https://example.org/c"] == "nofollow"
    # link analyzer
    a = org.post("/tools/link-analyzer/run", {"url": site_server + "/growth/site/index.html"}).json()["result"]
    assert a["internal"] == 3 and a["external"] == 1 and a["nofollow"] == 1 and a["rel_distribution"]["nofollow"] == 1
    assert {b["href"].rsplit("/", 1)[-1] for b in a["broken"]} == {"missing.html", "dead-external"}
    # invalid input / SSRF guard stays in place for non-URLs
    assert org.post("/tools/broken-links/run", {"url": "ftp://x"}).status_code == 400
    assert org.post("/tools/dofollow/run", {"url": site_server + "/growth/nothing-here.html"}).json()["status"] == "FAILED"
    runs = org.get("/tools/runs").json()
    assert len(runs) >= 4


def test_website_finder(org, site_server):
    org.post("/marketplace/my-listings", {"domain": "gardenfinder.example", "category": "Gardening", "guest_post_price": 50,
                                          "description": "gardening tips blog"})
    r = org.post("/tools/website-finder/run", {"keyword": "gardening", "extra": ["submit an article"]}).json()
    assert r["status"] == "COMPLETED"
    res = r["result"]
    assert any(q["query"] == '"gardening" "write for us"' for q in res["queries"])
    assert any('"submit an article"' in q["query"] for q in res["queries"])
    assert res["queries"][0]["google"].startswith("https://www.google.com/search?q=")
    assert res["serp"]["connected"] is False and "NOT CONNECTED" in res["serp"]["message"]
    assert any(x["domain"] == "gardenfinder.example" for x in res["marketplace"])


def test_public_free_tools_gating_rate_limit_and_isolation(org, org_factory, client, site_server):
    pid = org.project("Tool leads")["id"]
    home = org.get("/tools").json()
    key = home["public_key"]
    assert "/free/dofollow?key=" in next(t for t in home["tools"] if t["key"] == "dofollow")["public_url"]
    assert org.c.put("/api/tools/public-settings", headers=org.h, json={"project_id": pid}).status_code == 200
    info = client.get(f"/api/public/tools/info?key={key}")
    assert info.status_code == 200 and info.headers["access-control-allow-origin"] == "*"
    assert client.get("/api/public/tools/info?key=bad-key-123").status_code == 404
    # email gate
    r = client.post(f"/api/public/tools/dofollow?key={key}", json={"url": site_server + "/growth/rel.html"})
    assert r.status_code == 400 and r.json()["code"] == "email_required"
    r = client.post(f"/api/public/tools/dofollow?key={key}", json={"url": site_server + "/growth/rel.html", "email": "seo@prospect.io",
                                                                   "utm_source": "blog"})
    assert r.status_code == 200 and r.json()["result"]["nofollow"] == 3
    tl = org.get("/tools/leads").json()
    assert tl[0]["email"] == "seo@prospect.io" and tl[0]["tool"] == "dofollow"
    lead = org.get(f"/leads?project_id={pid}").json()["items"][0]
    assert lead["email"] == "seo@prospect.io" and lead["source"] == "free_tool:dofollow" and lead["utm_source"] == "blog"
    # per-IP rate limit (broken-links: 5 per hour)
    codes = [client.post(f"/api/public/tools/broken-links?key={key}", json={"url": "notaurl", "email": "a@b.io"}).status_code for _ in range(6)]
    assert codes[:5] == [422] * 5 and codes[5] == 429
    # another org never sees these
    other = org_factory()
    assert other.get("/tools/leads").json() == []
    assert all(x["email"] != "seo@prospect.io" for x in other.get("/leads").json()["items"])
    # disabling free tools turns the public key off
    org.c.put("/api/tools/public-settings", headers=org.h, json={"free_tools_enabled": False})
    assert client.get(f"/api/public/tools/info?key={key}").status_code == 404


def test_public_backlink_monitor(org, client, site_server):
    key = org.get("/tools").json()["public_key"]
    src = site_server + "/growth/article.html"
    r = client.post(f"/api/public/monitors?key={key}", json={"email": "owner@site.io", "source_url": src, "target_url": TARGET})
    assert r.status_code == 200, r.text
    tok, mon = r.json()["manage_token"], r.json()["monitor"]
    assert mon["last_status"] == "live" and mon["email"].startswith("ow***@")
    assert "NOT CONNECTED" in mon["email_alerts"]
    r = client.post(f"/api/public/monitors?key={key}", json={"email": "owner@site.io", "source_url": site_server + "/growth/nofollow.html",
                                                             "target_url": TARGET})
    assert r.json()["monitor"]["last_status"] == "nofollow"
    r = client.post(f"/api/public/monitors?key={key}", json={"email": "owner@site.io", "source_url": site_server + "/growth/nolink.html",
                                                             "target_url": TARGET})
    assert r.json()["monitor"]["last_status"] == "missing"
    r = client.post(f"/api/public/monitors?key={key}", json={"email": "OWNER@site.io", "source_url": src, "target_url": TARGET + "/x"})
    assert r.status_code == 402 and r.json()["code"] == "free_limit"
    st = client.get(f"/api/public/monitors/{tok}").json()
    assert st["last_status"] == "live" and len(st["history"]) == 1
    assert client.get("/api/public/monitors/nope").status_code == 404
    # scheduled sweep re-checks due monitors and alerts in-app on a status change
    with tenant_session(uuid.UUID(org.org_id)) as db:
        for m_ in db.query(g.GrowthPublicMonitor).all():
            m_.next_check_at = utcnow() - timedelta(minutes=1)
            if m_.manage_token == tok:
                m_.source_url = site_server + "/growth/nolink.html"
        from app.models import JobSchedule
        assert db.query(JobSchedule).filter(JobSchedule.kind == "growth_monitor_sweep").count() == 1
    from app.services.jobs import enqueue
    with tenant_session(uuid.UUID(org.org_id)) as db:
        enqueue(db, uuid.UUID(org.org_id), "growth_monitor_sweep", {})
    run_once()
    st = client.get(f"/api/public/monitors/{tok}").json()
    assert st["last_status"] == "missing" and len(st["history"]) == 2
    notes = org.get("/notifications").json()["items"]
    assert any(n["kind"] == "backlink_monitor" and "missing" in n["title"] for n in notes)
    assert client.post(f"/api/public/monitors/{tok}/stop").json()["ok"]
    assert client.get(f"/api/public/monitors/{tok}").json()["status"] == "stopped"
    # the org sees the monitors in-app (without manage tokens)
    ms = org.get("/tools/monitors").json()
    assert len(ms) == 3 and "manage_token" not in ms[0]
    # in-app monitor (not subject to the free limit) + manual re-check
    m2 = org.post("/tools/monitors", {"source_url": src, "target_url": TARGET}).json()
    assert m2["last_status"] == "live" and m2["public"] is False
    assert org.post(f"/tools/monitors/{m2['id']}/check").json()["last_checked_at"]


def test_service_order_fulfilled_by_platform(org, client):
    pa1, pa2 = make_platform_admin(client), make_platform_admin(client)
    fund(org, pa1, 1000)
    svc = next(s for s in org.get("/services").json() if s["name"] == "Content")
    org.post(f"/services/{svc['id']}/cart", {"tier": "article", "addons": ["upload"], "notes": "Topic: drain care"})
    r = org.post("/marketplace/checkout", {})
    assert r.status_code == 200, r.text
    oid = r.json()["orders"][0]["order_id"]
    assert r.json()["wallet"]["balance"] == 1000 - 250
    o = org.get(f"/marketplace/orders/{oid}").json()
    assert o["kind"] == "service" and o["status"] == "pending" and o["publisher"] == "Genranq"
    assert org.post(f"/marketplace/orders/{oid}/action", {"action": "accept"}).status_code == 403
    assert any(x["id"] == oid for x in pa1.get("/admin/growth/orders").json())
    assert pa1.post(f"/admin/growth/orders/{oid}/action", {"action": "accept"}).json()["status"] == "accepted"
    assert pa1.post(f"/admin/growth/orders/{oid}/action", {"action": "deliver", "live_url": "https://docs.example/draft"}).json()["status"] == "delivered"
    r = org.post(f"/marketplace/orders/{oid}/action", {"action": "complete"})
    assert r.status_code == 200 and r.json()["order"]["escrow"] == "released"
    assert org.get("/wallet").json()["balance"] == 750
    ledger_ok(org.org_id)
