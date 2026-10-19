"""ReleVink placement finder (63) on fixture pages and the page-to-page competitor audit (58)."""
from app.worker import run_once


def _proj(org, site_server):
    pid = org.project("Acme")["id"]
    site = org.post("/sites", {"project_id": pid, "domain": site_server}).json()
    return pid, site


def test_relevink_internal_and_external(org, site_server):
    pid, site = _proj(org, site_server)
    target = site_server + "/contact.html"
    # no crawl yet → internal placements explain what's missing
    r = org.post(f"/projects/{pid}/relevink", {"target_url": target, "keyword": "drain unblocking", "mode": "internal"}).json()
    run_once()
    res = org.get(f"/relevink/{r['run_id']}").json()
    assert res["status"] == "COMPLETED" and res["result"]["internal"]["available"] is False and "audit" in res["result"]["internal"]["message"]

    assert org.post(f"/sites/{site['id']}/audits", {"max_pages": 30}).status_code == 200
    run_once()
    b = site_server + "/backlinks/"
    r = org.post(f"/projects/{pid}/relevink", {"target_url": target, "keyword": "drain unblocking", "anchor": "drain unblocking",
                                              "mode": "both", "candidates": [b + "garden.html", b + "drain-guide.html", b + "missing.html"]})
    assert r.status_code == 200, r.text
    run_once()
    res = org.get(f"/relevink/{r.json()['run_id']}").json()
    assert res["status"] == "COMPLETED", res
    out = res["result"]
    assert out["query_terms"] == ["drain", "unblock"]
    inter = out["internal"]["placements"]
    assert inter[0]["url"] == site_server + "/services/drains.html" and inter[0]["already_links"] is False and inter[0]["score"] > 0
    assert inter[0]["sentence"] and inter[0]["suggested_anchor"] == "drain unblocking"
    assert all(p["url"] != target for p in inter)
    linking = [p for p in inter if p["already_links"]]
    assert all(inter.index(p) > 0 for p in linking)  # pages that already link to the target are pushed down
    ext = out["external"]["placements"]
    assert ext[0]["url"] == b + "drain-guide.html" and ext[0]["score"] > 40
    assert "drain unblocking" in ext[0]["sentence"].lower() and ext[0]["how"] == "Link the existing words in this sentence."
    garden = next(p for p in ext if p["url"].endswith("garden.html"))
    assert garden["score"] < ext[0]["score"] / 3
    assert next(p for p in ext if p["url"].endswith("missing.html"))["fetched"] is False
    assert out["ai"]["used"] is False and "Deterministic" in out["ai"]["note"]
    # AI requested but no provider connected → said honestly, deterministic results kept
    r = org.post(f"/projects/{pid}/relevink", {"target_url": target, "keyword": "drain unblocking", "mode": "external", "use_ai": True,
                                              "candidates": [b + "drain-guide.html"]}).json()
    run_once()
    res = org.get(f"/relevink/{r['run_id']}").json()
    assert res["result"]["ai"]["used"] is False and "No AI provider" in res["result"]["ai"]["note"] and res["ai_connected"] is False
    assert res["result"]["external"]["placements"][0]["score"] > 0
    # external candidates from the backlink / supplier DB
    s = org.post("/suppliers", {"name": "S"}).json()
    org.post(f"/suppliers/{s['id']}/prices", {"domain": "127.0.0.1.nip.invalid", "price": 10})
    r = org.post(f"/projects/{pid}/relevink", {"target_url": target, "keyword": "drain", "mode": "external", "include_db": True}).json()
    run_once()
    ext = org.get(f"/relevink/{r['run_id']}").json()["result"]["external"]
    assert ext["available"] and any(p["domain"] == "127.0.0.1.nip.invalid" and not p["fetched"] for p in ext["placements"])
    runs = org.get(f"/projects/{pid}/relevink").json()
    assert len(runs) == 4 and "result" not in runs[0]
    # validation
    assert org.post(f"/projects/{pid}/relevink", {"target_url": "nope", "keyword": "x y"}).status_code == 400
    assert org.post(f"/projects/{pid}/relevink", {"target_url": target, "keyword": "drain", "mode": "all"}).status_code == 400
    assert org.post(f"/projects/{pid}/relevink", {"target_url": target, "keyword": "drain", "candidates": ["bad candidate!"]}).status_code == 400


def test_page_to_page_comparison(org, site_server):
    pid, _ = _proj(org, site_server)
    ours = site_server + "/landing.html"
    r = org.post(f"/projects/{pid}/page-compare", {"our_url": ours, "competitor_urls": [site_server + "/comp/a.html", site_server + "/comp/b.html",
                                                                                     site_server + "/comp/missing.html"],
                                                   "keyword": "emergency plumber springfield"})
    assert r.status_code == 200, r.text
    run_once()
    c = org.get(f"/page-compare/{r.json()['comparison_id']}").json()
    assert c["status"] == "COMPLETED", c
    res = c["result"]
    assert res["available"] and res["page_count"] == 3 and len(res["failed"]) == 1
    pages = {p["url"]: p for p in res["pages"]}
    o, a = pages[ours], pages[site_server + "/comp/a.html"]
    assert o["ours"] is True and set(o["score"]["dimensions"]) == set(res["weights"])
    assert a["score"]["total"] > o["score"]["total"] and res["our_rank"] >= 2
    assert a["metrics"]["faq_questions"] >= 3 and "FAQPage" in a["metrics"]["schema_types"] and o["metrics"]["faq_questions"] == 0
    assert a["score"]["dimensions"]["faq"] > o["score"]["dimensions"]["faq"] and a["score"]["dimensions"]["eeat"] > o["score"]["dimensions"]["eeat"]
    assert o["metrics"]["fetch_seconds"] is not None
    dims = {x["dimension"] for x in res["recommendations"]}
    assert {"faq", "schema", "eeat"} <= dims
    assert any(x["dimension"] == "structure" for x in res["recommendations"])
    assert res["leaders"]["faq"] == site_server + "/comp/a.html"
    lst = org.get(f"/projects/{pid}/page-compare").json()
    assert lst[0]["our_score"] == res["our_score"] and "result" not in lst[0]
    # our page unreachable → failed run with the reason
    r = org.post(f"/projects/{pid}/page-compare", {"our_url": site_server + "/nope.html", "competitor_urls": [site_server + "/comp/a.html"]}).json()
    run_once()
    c = org.get(f"/page-compare/{r['comparison_id']}").json()
    assert c["status"] == "FAILED" and "Could not fetch" in c["result"]["message"]
    # validation: max 5 competitors, distinct from ours
    assert org.post(f"/projects/{pid}/page-compare", {"our_url": ours, "competitor_urls": [ours]}).status_code == 400
    assert org.post(f"/projects/{pid}/page-compare", {"our_url": ours, "competitor_urls": [f"https://c{i}.example/" for i in range(6)]}).status_code == 422
