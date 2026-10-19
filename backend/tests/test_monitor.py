"""Autonomous monitoring end-to-end on realistic synthetic Search Console / GA4 data, Screaming Frog
imports and a live fixture site: detection → root cause → team routing → tasks → notification →
verification → reopen / resolve; blog checkpoints → automatic gap analysis; dashboards."""
import csv
import io
import random
from datetime import date, timedelta

from app.worker import run_once
from tests.fixtures import serve

ANCHOR = date.today() - timedelta(days=3)


def gsc_csv(base: str) -> bytes:
    """56 days of query × page × device rows. In the last 7 days mobile clicks on the drains page collapse,
    a new query appears, one query is lost, one keyword drops from ~4 to ~12, and a new blog post barely gets impressions."""
    rnd = random.Random(7)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Query", "Page", "Device", "Country", "Clicks", "Impressions", "Position"])
    for i in range(56):
        d = ANCHOR - timedelta(days=55 - i)
        cur = d > ANCHOR - timedelta(days=7)

        def row(q, page, dev, clicks, impr, pos, country="usa"):
            w.writerow([d.isoformat(), q, base + page, dev, country, max(0, clicks + rnd.randint(-1, 1)), impr + rnd.randint(-3, 3), pos])

        row("drain unblocking springfield", "/services/drains.html", "DESKTOP", 20, 400, 2.1)
        row("drain unblocking springfield", "/services/drains.html", "MOBILE", 8 if cur else 30, 600, 2.3)
        row("plumber springfield", "/", "DESKTOP", 6, 300, 5.0)
        row("plumber springfield", "/", "MOBILE", 6, 300, 5.2)
        row("water heater repair", "/services/water-heaters.html", "DESKTOP", 1 if cur else 4, 120, 12.0 if cur else 4.0)
        if not cur:
            row("drain snake rental", "/services/drains.html", "DESKTOP", 2, 60, 8.0)
        if cur:
            row("emergency plumber springfield", "/", "MOBILE", 2, 300, 14.0)
        if d >= ANCHOR - timedelta(days=35):
            row("how to unclog a drain", "/blog/new-guide.html", "DESKTOP", 0, 1, 48.0)
    return buf.getvalue().encode()


def ga4_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Landing page", "Device category", "Session default channel group", "Sessions", "Total users", "Key events"])
    for i in range(56):
        d = ANCHOR - timedelta(days=55 - i)
        cur = d > ANCHOR - timedelta(days=7)
        w.writerow([d.strftime("%Y%m%d"), "/services/drains.html", "mobile", "Organic Search", 15 if cur else 45, 14 if cur else 40, 1 if cur else 4])
        w.writerow([d.strftime("%Y%m%d"), "/", "desktop", "Organic Search", 30, 28, 2])
    return buf.getvalue().encode()


def sf_csv(base: str, dyn_noindex: bool) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Address", "Content Type", "Status Code", "Indexability", "Indexability Status", "Title 1", "Meta Description 1", "H1-1",
                "Canonical Link Element 1", "Meta Robots 1", "Word Count", "Crawl Depth", "Inlinks"])
    w.writerow([base + "/", "text/html", 200, "Indexable", "", "Acme Plumbing", "Plumbers", "Acme", base + "/", "", 400, 0, 12])
    w.writerow([base + "/services/drains.html", "text/html", 200, "Indexable", "", "Drains", "Drains", "Drains", base + "/services/drains.html", "", 300, 1, 8])
    w.writerow([base + "/dyn/landing.html", "text/html", 200, "Non-Indexable" if dyn_noindex else "Indexable", "noindex" if dyn_noindex else "",
                "Landing", "", "Landing", "", "noindex" if dyn_noindex else "", 200, 1, 6])
    return buf.getvalue().encode()


def _setup(org, site_server):
    p = org.project("Acme Plumbing")
    org.post("/sites", {"project_id": p["id"], "domain": site_server})
    dev_h = org.invite_user("staff")
    content_h = org.invite_user("staff")
    me = org.c.get("/api/auth/me", headers=dev_h).json()["user"]
    cw = org.c.get("/api/auth/me", headers=content_h).json()["user"]
    owner = org.get("/auth/me").json()["user"]
    r = org.c.put(f"/api/projects/{p['id']}/members", headers=org.h, json=[
        {"user_id": me["id"], "team": "developer"}, {"user_id": cw["id"], "team": "content"},
        {"user_id": owner["id"], "team": "seo"}, {"user_id": owner["id"], "team": "pm", "is_lead": True}])
    assert r.status_code == 200, r.text
    assert org.c.put(f"/api/projects/{p['id']}/monitor/config", headers=org.h, json={"thresholds": {"verify_delay_sec": 0}}).status_code == 200
    return p, me, cw, owner, dev_h


def _upload(org, pid, kind, content, **form):
    r = org.c.post(f"/api/projects/{pid}/import/{kind}", headers=org.h, files={"file": (f"{kind}.csv", content, "text/csv")},
                   data={k: str(v) for k, v in form.items()})
    assert r.status_code == 200, r.text
    return r.json()


def test_monitoring_detects_routes_and_verifies(org, site_server):
    p, dev, content_user, owner, dev_h = _setup(org, site_server)
    pid = p["id"]
    serve.STATE["dyn_noindex"] = True
    assert _upload(org, pid, "screaming_frog", sf_csv(site_server, False), run_monitor="false")["pages"] == 3
    res = _upload(org, pid, "gsc", gsc_csv(site_server), run_monitor="false")
    assert res["rows"] > 300 and res["grains"] == ["detail"]
    _upload(org, pid, "ga4", ga4_csv(), run_monitor="false")
    # second crawl: a developer deploy added noindex to a landing page
    _upload(org, pid, "screaming_frog", sf_csv(site_server, True), run_monitor="true")
    run_once()

    issues = org.get(f"/issues?project_id={pid}").json()
    kinds = {i["kind"]: i for i in issues}
    for k in ("traffic_drop", "device_drop", "page_traffic_drop", "keyword_down", "new_query", "lost_query", "noindex_important",
              "sessions_drop", "landing_sessions_drop", "blog_underperforming"):
        assert k in kinds, (k, sorted(kinds))
    td = kinds["traffic_drop"]
    assert td["root_cause"]["where"] and "mobile" in str(td["root_cause"]).lower()
    assert kinds["device_drop"]["dimension"] == "device=mobile" and kinds["device_drop"]["category"] == "developer"
    assert kinds["keyword_down"]["query"] == "water heater repair" and kinds["keyword_down"]["severity"] == "high"
    assert kinds["new_query"]["query"] == "emergency plumber springfield" and kinds["new_query"]["is_opportunity"]
    ni = kinds["noindex_important"]
    assert ni["severity"] == "critical" and ni["url"].endswith("/dyn/landing.html") and set(ni["categories"]) == {"developer", "seo"}

    # routing: developer task → developer team member; SEO note → SEO team; notifications sent
    tasks = {t["id"]: t for t in org.get(f"/tasks?project_id={pid}").json()}
    ni_tasks = [tasks[t] for t in ni["task_ids"]]
    dev_task = next(t for t in ni_tasks if t["title"].startswith("[Developer]"))
    seo_task = next(t for t in ni_tasks if t["title"].startswith("[SEO]"))
    assert dev_task["assignee_ids"] == [dev["id"]] and seo_task["assignee_ids"] == [owner["id"]]
    assert dev_task["priority"] == "critical" and "monitor" in dev_task["labels"]
    assert "Request re-indexing" in seo_task["description"]
    # every task carries the fix steps, who is responsible/accountable, the deadline and the prevention strategy
    assert "Steps for Developer:" in dev_task["description"] and "Responsible: Developer" in dev_task["description"]
    assert "Accountable:" in dev_task["description"] and "Prevention strategy:" in dev_task["description"]
    fix = ni["root_cause"]["fix"]
    assert fix["responsible_team"] == "developer" and fix["responsible"]["developer"][0]["user_id"] == dev["id"]
    assert fix["steps"]["developer"] and fix["strategy"] and fix["due"]
    # the developer is emailed (SMTP isn't configured here → recorded honestly as not_connected)
    assert org.get("/email/status").json()["status"] == "NOT_CONNECTED"
    outbox = org.get("/email/outbox").json()
    dev_mail = [e for e in outbox if e["to_email"] == dev["email"] and e["entity"] == f"task:{dev_task['id']}"]
    assert dev_mail and dev_mail[0]["status"] == "not_connected"
    assert any(e["to_email"] == content_user["email"] for e in outbox)  # content team got the blog task email
    blog = kinds["blog_underperforming"]
    blog_task = next(tasks[t] for t in blog["task_ids"] if tasks[t]["title"].startswith("[Content]"))
    assert blog_task["assignee_ids"] == [content_user["id"]]
    notes = org.c.get("/api/notifications", headers=dev_h).json()["items"]
    assert any("noindex" in n["title"] for n in notes)

    # blog underperformance queued a gap analysis that ran and attached its plan to the content task
    gaps = org.get(f"/projects/{pid}/gaps").json()
    g = next(x for x in gaps if x["target_url"].endswith("/blog/new-guide.html"))
    assert g["status"] == "COMPLETED", g
    full = org.get(f"/gaps/{g['id']}").json()
    assert full["result"]["root_causes"] and full["result"]["plan"]
    watch = org.get(f"/projects/{pid}/content-watch").json()["items"]
    cw = next(x for x in watch if x["url"].endswith("/blog/new-guide.html"))
    assert cw["source"] == "auto_gsc" and cw["status"] == "underperforming" and cw["checkpoints"]["30"]["passed"] is False
    blog_task = next(t for t in org.get(f"/tasks?project_id={pid}").json() if t["id"] == blog_task["id"])
    assert "Gap analysis plan:" in blog_task["description"] and blog_task["output"].get("gap_report_id") == g["id"]

    # developer marks the fix done but the page still has noindex → verification re-fetches → reopened
    for t in ni_tasks:
        assert org.patch(f"/tasks/{t['id']}", {"status": "done"}).status_code == 200
    assert org.get(f"/issues/{ni['id']}").json()["status"] == "verifying"
    run_once()
    i2 = org.get(f"/issues/{ni['id']}").json()
    assert i2["status"] == "reopened", i2["history"]
    assert any(h["event"] == "verification_failed" for h in i2["history"])
    assert all(t["status"] == "todo" for t in i2["tasks"])

    # now actually fixed → verified → resolved, tasks stay done
    serve.STATE["dyn_noindex"] = False
    for t in ni_tasks:
        org.patch(f"/tasks/{t['id']}", {"status": "done"})
    run_once()
    i3 = org.get(f"/issues/{ni['id']}").json()
    assert i3["status"] == "resolved", i3["history"]
    serve.STATE["dyn_noindex"] = True

    # re-running the monitor doesn't duplicate issues or tasks
    n_tasks = len(org.get(f"/tasks?project_id={pid}").json())
    org.post(f"/projects/{pid}/monitor/run")
    run_once()
    assert len(org.get(f"/tasks?project_id={pid}").json()) == n_tasks
    assert len([i for i in org.get(f"/issues?project_id={pid}").json() if i["kind"] == "traffic_drop"]) == 1

    # dashboards
    ov = org.get(f"/projects/{pid}/monitor/overview?days=7").json()
    assert ov["search"]["clicks"]["change_pct"] < -20 and ov["analytics"]["sessions"]["change_pct"] < 0
    assert {d["device"] for d in ov["devices"]} == {"desktop", "mobile"} and ov["last_run"]["summary"]
    q = org.get(f"/projects/{pid}/monitor/queries?days=7").json()
    st = {r["query"]: r for r in q["rows"]}
    assert st["emergency plumber springfield"]["status"] == "new" and st["drain snake rental"]["status"] == "lost"
    assert st["water heater repair"]["position_change"] < -5
    pg = org.get(f"/projects/{pid}/monitor/pages?days=7").json()["rows"]
    drains = next(r for r in pg if r["path"] == "/services/drains.html")
    assert drains["clicks_change_pct"] < -30 and drains["sessions"] is not None
    detail = org.get(f"/projects/{pid}/monitor/page", params={"url": site_server + "/services/drains.html", "days": 7}).json()
    assert detail["queries"]["rows"] and detail["issues"]
    ex = org.get("/dashboards/executive").json()
    assert ex["projects"][0]["issues"].get("critical", 0) + ex["projects"][0]["issues"].get("high", 0) >= 1
    td_ = org.get("/dashboards/team/developer").json()
    assert td_["issues"] and any(m_["user_id"] == dev["id"] for m_ in td_["members"])
    assert org.get(f"/projects/{pid}/monitor/changes").json()  # the noindex change was logged


def test_monitor_config_validation_and_isolation(org, org_factory, site_server):
    p = org.project("X")
    r = org.c.put(f"/api/projects/{p['id']}/monitor/config", headers=org.h, json={"channels": {"slack_webhook": "https://evil.example/hook"}})
    assert r.status_code == 400
    r = org.c.put(f"/api/projects/{p['id']}/monitor/config", headers=org.h,
                  json={"channels": {"slack_webhook": "https://hooks.slack.com/services/T0/B0/x"}, "thresholds": {"traffic_drop_pct": 15}})
    assert r.status_code == 200
    cfg = org.get(f"/projects/{p['id']}/monitor/config").json()
    assert cfg["channels"]["slack_webhook"] == "•••• configured" and cfg["thresholds"]["traffic_drop_pct"] == 15
    assert org.c.put(f"/api/projects/{p['id']}/monitor/config", headers=org.h, json={"thresholds": {"nope": 1}}).status_code == 400
    other = org_factory()
    assert other.get(f"/projects/{p['id']}/monitor/overview").status_code == 404
    assert other.get(f"/issues?project_id={p['id']}").json() == []
    r = other.c.post(f"/api/projects/{p['id']}/import/gsc", headers=other.h, files={"file": ("g.csv", b"Date,Clicks,Impressions\n2026-01-01,1,2\n", "text/csv")})
    assert r.status_code == 404
    # sources show NOT CONNECTED until the org connects the provider
    src = {s["provider"]: s for s in org.get(f"/projects/{p['id']}/sources").json()}
    assert src["gsc"]["org_connection"] == "NOT CONNECTED" and src["screaming_frog"]["org_connection"] == "IMPORT"
    assert org.c.put(f"/api/projects/{p['id']}/sources/screaming_frog", headers=org.h, json={"mode": "api"}).status_code == 400
    org.c.put(f"/api/projects/{p['id']}/sources/gsc", headers=org.h, json={"property": "sc-domain:example.com"})
    org.post(f"/projects/{p['id']}/sources/gsc/sync")
    run_once()
    s = {x["provider"]: x for x in org.get(f"/projects/{p['id']}/sources").json()}["gsc"]
    assert s["last_status"] == "not_connected" and "connect" in (s["last_error"] or "").lower()
