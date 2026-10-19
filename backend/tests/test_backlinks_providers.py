"""Ahrefs (9) / Semrush (10): NOT CONNECTED state, CSV imports (incl. UTF-16 Ahrefs exports), add-to-CRM,
a connected Ahrefs sync against a faked API; keyword research (11) with intent, clusters, gap, difficulty, tracking."""
import httpx

from app.worker import run_once
from tests.test_monitor import gsc_csv


def _proj(org, site_server):
    pid = org.project("Acme")["id"]
    org.post("/sites", {"project_id": pid, "domain": site_server})
    return pid


def _imp(org, pid, provider, kind, content, name="x.csv", **data):
    return org.c.post(f"/api/projects/{pid}/providers/{provider}/import/{kind}", headers=org.h,
                      files={"file": (name, content, "text/csv")}, data={k: str(v).lower() if isinstance(v, bool) else v for k, v in data.items()})


def test_ahrefs_semrush_not_connected_and_csv(org, site_server):
    pid = _proj(org, site_server)
    tgt = site_server + "/services/drains.html"
    for prov in ("ahrefs", "semrush"):
        s = org.get(f"/projects/{pid}/providers/{prov}").json()
        assert s["connected"] is False and "NOT CONNECTED" in s["message"] and s["backlinks"] == [] and s["metrics"] == []
        r = org.post(f"/projects/{pid}/providers/{prov}/sync")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected"
    assert org.get(f"/projects/{pid}/providers/moz").status_code == 404
    assert org.c.put(f"/api/projects/{pid}/providers/ahrefs/config", headers=org.h, json={"domain": "acme-plumbing.com"}).json()["domain"] == "acme-plumbing.com"
    assert org.c.put(f"/api/projects/{pid}/providers/ahrefs/config", headers=org.h, json={"domain": "bad domain"}).status_code == 400

    # Ahrefs backlinks export: UTF-16, tab separated (Excel format)
    ah = ("Referring page URL\tReferring page title\tDomain rating\tTarget URL\tAnchor\tType\tFirst seen\tLast seen\n"
          f"https://homeblog.example/autumn\tAutumn\t45\t{tgt}\tdrain unblocking\tdofollow\t2026-05-01\t2026-09-30\n"
          f"https://forum.example/t/9\tForum\t30\t{tgt}\tclick\tnofollow, ugc\t2026-06-01\t2026-09-30\n").encode("utf-16")
    r = _imp(org, pid, "ahrefs", "backlinks", ah)
    assert r.status_code == 200 and r.json() == {"rows": 2, "domains": 2}, r.text
    rd = "Domain,Domain rating,Dofollow links\nhomeblog.example,45,3\nforum.example,30,1\nnews.example,70,2\n"
    r = _imp(org, pid, "ahrefs", "refdomains", rd.encode())
    assert r.status_code == 200 and r.json()["rows"] == 3
    kw = ("Keyword,Volume,KD,CPC,Current position,Current URL\n"
          f"emergency plumber springfield,900,34,4.5,7,{site_server}/\n"
          f"drain unblocking springfield,300,12,3.1,2,{tgt}\n")
    r = _imp(org, pid, "ahrefs", "keywords", kw.encode(), is_own=True)
    assert r.status_code == 200 and r.json()["rows"] == 2 and r.json()["difficulty_rows"] == 2
    s = org.get(f"/projects/{pid}/providers/ahrefs").json()
    assert {b["source_domain"]: b["rel"] for b in s["backlinks"]} == {"homeblog.example": "dofollow", "forum.example": "ugc"}
    assert s["refdomains"][0] == {"domain": "news.example", "domain_rating": 70.0} and len(s["refdomains"]) == 3
    assert {k["keyword"]: k["difficulty"] for k in s["own_keywords"]} == {"emergency plumber springfield": 34.0, "drain unblocking springfield": 12.0}
    assert s["source"]["last_status"] == "ok" and s["source"]["rows_last_sync"] == 2
    assert _imp(org, pid, "ahrefs", "keywords", kw.encode()).status_code == 200  # domain from the configured source
    assert _imp(org, pid, "ahrefs", "toxic", b"x").status_code == 400
    assert _imp(org, pid, "ahrefs", "backlinks", b"foo,bar\n1,2\n").status_code == 400

    # add provider-discovered links to the CRM (deduped)
    ids = [b["id"] for b in s["backlinks"]]
    r = org.post(f"/projects/{pid}/ext-backlinks/to-crm", {"ids": ids})
    assert r.json() == {"added": 2, "skipped": 0}
    assert org.post(f"/projects/{pid}/ext-backlinks/to-crm", {"ids": ids}).json() == {"added": 0, "skipped": 2}
    crm = {b["source_domain"]: b for b in org.get(f"/backlinks?project_id={pid}").json()["items"]}
    assert crm["homeblog.example"]["dr"] == 45 and crm["homeblog.example"]["source"] == "ahrefs" and crm["forum.example"]["link_type"] == "ugc"
    assert all(b["in_crm"] for b in org.get(f"/projects/{pid}/providers/ahrefs").json()["backlinks"])

    # Semrush: backlinks export, positions (organic keywords), Backlink Audit toxic scores
    sb = "Page ascore;Source title;Source url;Target url;Anchor;Nofollow;First seen;Last seen\n" \
         f"22;Spam;https://spammy.example/p;{tgt};casino;true;2026-01-01;2026-09-01\n"
    assert _imp(org, pid, "semrush", "backlinks", sb.encode()).json()["rows"] == 1
    pos = "Keyword;Position;Search Volume;Keyword Difficulty;URL\nplumber springfield;5;1200;41;" + site_server + "/\n"
    r = _imp(org, pid, "semrush", "keywords", pos.encode(), domain=site_server, is_own=True)
    assert r.status_code == 200 and r.json()["difficulty_rows"] == 1
    tox = f"Source url,Target url,Toxicity Score\nhttps://spammy.example/p,{tgt},77\n"
    assert _imp(org, pid, "semrush", "toxic", tox.encode()).json()["rows"] == 1
    s = org.get(f"/projects/{pid}/providers/semrush").json()
    assert s["connected"] is False and s["backlinks"][0]["rel"] == "nofollow" and s["toxic"][0]["score"] == 77
    assert "Backlink Audit" in s["toxic_note"] and s["own_keywords"][0]["difficulty"] == 41


def test_ahrefs_connected_sync_with_faked_api(org, site_server, monkeypatch):
    from app.db import tenant_session
    from app.services import ingest, integrations as ih, sources, backlinks_providers
    pid = _proj(org, site_server)
    with tenant_session(org.org_id) as db:
        c = ih.connect_api_key(db, org.org_id, None, "ahrefs", "ahrefs-test-key", {})
        c.status = "CONNECTED"
    calls = []

    def fake(method, url, **kw):
        calls.append(url)
        assert kw["headers"]["Authorization"] == "Bearer ahrefs-test-key"
        if url.endswith("/domain-rating"):
            j = {"domain_rating": {"domain_rating": 38}}
        elif url.endswith("/backlinks-stats"):
            j = {"metrics": {"live": 420, "live_refdomains": 61}}
        elif url.endswith("/refdomains"):
            j = {"refdomains": [{"domain": "homeblog.example", "domain_rating": 45}, {"domain": "news.example", "domain_rating": 70}]}
        elif url.endswith("/all-backlinks"):
            j = {"backlinks": [{"url_from": "https://homeblog.example/a", "url_to": site_server + "/", "anchor": "acme", "domain_rating_source": 45,
                                "first_seen": "2026-05-01T00:00:00Z", "last_visited": "2026-09-20T00:00:00Z", "is_dofollow": True}]}
        elif url.endswith("/organic-keywords"):
            j = {"keywords": [{"keyword": "plumber springfield", "best_position": 4, "best_position_url": site_server + "/", "volume": 1200,
                               "keyword_difficulty": 29}]}
        else:
            raise AssertionError(url)
        return httpx.Response(200, json=j, request=httpx.Request(method, url))

    for mod in (ingest, sources, backlinks_providers):
        monkeypatch.setattr(mod, "http_call", fake)
    assert org.post(f"/projects/{pid}/providers/ahrefs/sync").status_code == 400  # domain not configured yet
    org.c.put(f"/api/projects/{pid}/providers/ahrefs/config", headers=org.h, json={"domain": "https://www.acme-plumbing.com/"})
    r = org.post(f"/projects/{pid}/providers/ahrefs/sync")
    assert r.status_code == 200, r.text
    run_once()
    job = org.get(f"/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "COMPLETED", job
    s = org.get(f"/projects/{pid}/providers/ahrefs").json()
    assert s["connected"] is True and "message" not in s
    assert s["metrics"][-1]["domain_rating"] == 38 and s["metrics"][-1]["refdomains"] == 61 and s["metrics"][-1]["domain"] == "acme-plumbing.com"
    assert len(s["refdomains"]) == 2 and s["backlinks"][0]["via"] == "api" and s["backlinks"][0]["first_seen"] == "2026-05-01"
    assert s["own_keywords"] == [{"keyword": "plumber springfield", "position": 4, "url": site_server + "/", "volume": 1200, "difficulty": 29.0}]
    assert s["source"]["last_status"] == "ok"
    assert any(u.endswith("/all-backlinks") for u in calls)


def test_keyword_research(org, site_server):
    pid = _proj(org, site_server)
    r = org.c.post(f"/api/projects/{pid}/import/gsc", headers=org.h, params={"run_monitor": "false"},
                   files={"file": ("gsc.csv", gsc_csv(site_server), "text/csv")})
    assert r.status_code == 200, r.text
    org.post("/keywords", {"project_id": pid, "phrases": ["blocked drain cost"]})
    k = org.get(f"/projects/{pid}/keyword-research").json()
    kws = {x["keyword"]: x for x in k["keywords"]}
    assert kws["drain unblocking springfield"]["source"] == "gsc" and kws["drain unblocking springfield"]["impressions"] > 0
    assert kws["blocked drain cost"]["tracked"] is True and kws["blocked drain cost"]["intent"] == "transactional"
    assert kws["how to unclog a drain"]["intent"] == "informational"
    assert k["difficulty_available"] is False and "Ahrefs or Semrush" in k["difficulty_message"]
    assert k["clusters"] and k["connections"] == {"ahrefs": False, "semrush": False, "gsc": False}
    g = org.get(f"/projects/{pid}/keyword-research/gap").json()
    assert g["available"] is False
    r = org.post(f"/projects/{pid}/keyword-research/difficulty", {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected"

    comp = ("Keyword,Position,Volume,KD,URL\nemergency plumber springfield,2,900,38,https://comp.example/emergency\n"
            "drain unblocking cost,4,200,22,https://comp.example/drains\nwater heater installation price,6,150,,https://comp.example/wh\n")
    r = _imp(org, pid, "semrush", "keywords", comp.encode(), domain="comp.example", is_own=False)
    assert r.status_code == 200, r.text
    k = org.get(f"/projects/{pid}/keyword-research").json()
    kws = {x["keyword"]: x for x in k["keywords"]}
    assert k["difficulty_available"] is True and "difficulty_message" not in k
    assert kws["drain unblocking cost"]["competitor_only"] is True and kws["drain unblocking cost"]["difficulty"] == 22
    assert kws["drain unblocking cost"]["difficulty_source"] == "semrush" and kws["drain unblocking cost"]["best_competitor_position"] == 4
    assert kws["water heater installation price"]["difficulty"] is None
    assert k["competitor_domains"] == ["comp.example"] and k["by_source"]["competitor"] == 2  # the third is already a GSC query
    drain = [c for c in k["clusters"] if any(x["keyword"] == "drain unblocking cost" for x in c["keywords"])][0]
    assert drain["size"] >= 2 and drain["avg_difficulty"] is not None
    g = org.get(f"/projects/{pid}/keyword-research/gap").json()
    gk = {x["keyword"]: x for x in g["keywords"]}
    assert g["available"] and gk["drain unblocking cost"]["difficulty"] == 22 and gk["drain unblocking cost"]["action"] == "create content"
    assert "content_gap" in g

    r = org.post(f"/projects/{pid}/keyword-research/track", {"phrases": ["drain unblocking cost", "Blocked Drain Cost"]})
    assert r.status_code == 200 and r.json() == {"added": 1, "skipped_duplicates": 1}
    tracked = {x["phrase"]: x for x in org.get(f"/keywords?project_id={pid}").json()}
    assert "drain unblocking cost" in tracked and tracked["drain unblocking cost"]["tags"] == ["keyword-research"]
    assert org.get(f"/projects/{pid}/keyword-research").json()["tracked"] == 2
    assert org.post(f"/projects/{pid}/keyword-research/track", {"phrases": []}).status_code == 422
