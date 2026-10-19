"""Local SEO (Phase 3) end-to-end: manual + CSV data without connections (NOT CONNECTED, no fake data),
GBP audit, category finder, review audit + reply approval gating, posts workflow + post audit, geo-grid
(import and live via a mocked Places API), local scan / competitors, website ↔ GBP, automation and
tenant isolation."""
import io
import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app import models as m
from app import models_local as ml
from app.db import tenant_session
from app.services import integrations as ih
from app.services import local_core as L
from app.worker import run_once

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------- helpers
def _csv(rows: list[list]) -> bytes:
    import csv
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


def upload(org, path, content: bytes, **form):
    return org.c.post("/api" + path, headers=org.h, files={"file": ("data.csv", content, "text/csv")}, data=form)


def setup_business(org, name="Acme Plumbing", lat=39.7817, lng=-89.6501, **extra):
    p = org.project("Local " + name)
    nap_extra = extra.pop("nap", {})
    body = {"project_id": p["id"], "kind": "own", "name": name,
            "nap": {"address": "123 Main St", "city": "Springfield", "region": "IL", "postcode": "62704", "phone": "(217) 555-0142",
                    "website": "https://acmeplumbing.example", "lat": lat, "lng": lng, **nap_extra},
            "primary_category": "Plumber", "secondary_categories": ["Drainage service"], "hours": "Mo-Fr 08:00-18:00",
            "description": "Family-run plumbers serving Springfield since 1998. " * 6, "photos_count": 12,
            "services": ["Emergency plumbing", "Drain unblocking", "Water heater repair"], "attributes": ["Wheelchair accessible"],
            "keywords": ["plumber springfield", "blocked drain"], **extra}
    r = org.post("/local/locations", body)
    assert r.status_code == 200, r.text
    return p, r.json()


def connect(org_id, key, secret):
    with tenant_session(org_id) as db:
        ih.connect_api_key(db, org_id, None, key, secret, {})
        ih.get_conn(db, org_id, key).status = "CONNECTED"


class FakeResp:
    def __init__(self, payload, status=200):
        self.status_code, self._p = status, payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()
        self.headers = {}

    def json(self):
        return self._p


def review_rows():
    d = lambda n: (NOW - timedelta(days=n)).strftime("%Y-%m-%d")  # noqa: E731
    return [["location", "author", "rating", "text", "date", "reply", "reply_date"],
            ["Acme Plumbing", "Ann", "5", "Excellent service, very friendly staff and fair price", d(5), "Thanks Ann!", d(4)],
            ["Acme Plumbing", "Bob", "4", "Good work, arrived on time", d(20), "", ""],
            ["Acme Plumbing", "Cat", "1", "Rude technician, terrible and overpriced", d(35), "", ""],
            ["Acme Plumbing", "Dan", "5", "Brilliant, fixed my blocked drain quickly", d(70), "Cheers Dan", d(69)],
            ["Acme Plumbing", "Eve", "2", "Late and messy, disappointed", d(150), "", ""],
            ["Nope Unknown", "Zed", "5", "x", d(3), "", ""]]


# ================================================================== NOT CONNECTED behaviour
def test_not_connected_everything_still_works_manually(org):
    p, loc = setup_business(org)
    st = org.get("/local/status").json()
    assert st["gbp"]["connected"] is False and st["gbp"]["status"] == "NOT_CONNECTED" and st["gbp"]["link"] == "/integrations/gbp"
    assert st["google_maps"]["connected"] is False and st["google_maps"]["link"] == "/integrations/google_maps"
    assert st["ai"]["connected"] is False

    r = org.post(f"/local/projects/{p['id']}/gbp-sync", {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected" and r.json()["detail"]["provider"] == "gbp"
    assert "NOT CONNECTED" in r.json()["detail"]["message"]
    r = org.post(f"/local/projects/{p['id']}/competitors/discover", {"location_id": loc["id"], "keyword": "plumber"})
    assert r.status_code == 409 and r.json()["detail"]["provider"] == "google_maps"
    r = org.post(f"/local/projects/{p['id']}/scan", {"location_id": loc["id"], "keyword": "plumber", "discover": True})
    assert r.status_code == 409 and r.json()["detail"]["provider"] == "google_maps"
    r = org.post("/local/grid/scans", {"project_id": p["id"], "location_id": loc["id"], "keyword": "plumber", "mode": "live"})
    assert r.status_code == 409 and r.json()["detail"]["provider"] == "google_maps"
    r = org.post("/local/posts/ai-draft", {"location_id": loc["id"], "brief": "Spring boiler service offer"})
    assert r.status_code == 409 and r.json()["detail"]["provider"] == "ai"
    rv = org.post(f"/local/locations/{loc['id']}/reviews", {"rating": 2, "body": "Slow response"}).json()
    r = org.post(f"/local/reviews/{rv['id']}/ai-draft", {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected"
    # nothing fabricated: no competitors, no posts, no scans, review still has no draft, no credits used
    assert org.get(f"/local/locations?project_id={p['id']}&kind=competitor").json() == []
    assert org.get(f"/local/posts?project_id={p['id']}").json() == []
    assert org.get(f"/local/grid/scans?project_id={p['id']}").json() == []
    rvs = org.get(f"/local/locations/{loc['id']}/reviews").json()
    assert len(rvs) == 1 and rvs[0]["reply_draft"] is None and rvs[0]["reply_status"] == "none"
    with tenant_session(org.org_id) as db:
        assert (db.scalar(select(func.sum(m.UsageCounter.used)).where(m.UsageCounter.metric == "local_rank_credits")) or 0) == 0
    ov = org.get(f"/local/overview?project_id={p['id']}").json()
    assert ov["locations"] == 1 and ov["reviews"] == 1 and ov["connections"]["gbp"]["connected"] is False


# ================================================================== locations, imports, GBP audit (15)
def test_imports_and_gbp_audit(org):
    p, loc = setup_business(org)
    pid = p["id"]
    r = upload(org, f"/local/projects/{pid}/import/locations", _csv([
        ["name", "address", "city", "postcode", "phone", "website", "category", "additional_categories", "lat", "lng", "photos"],
        ["Acme Plumbing", "123 Main St", "Springfield", "62704", "2175550142", "acmeplumbing.example", "Plumber", "Drainage service;Water heater installation service", "39.7817", "-89.6501", "14"],
        ["Acme Plumbing East", "9 Elm Rd", "Springfield", "62702", "2175550199", "", "Plumber", "", "39.80", "-89.60", "2"],
        ["", "no name", "", "", "", "", "", "", "", "", ""]]))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 1 and res["updated"] == 1 and len(res["errors"]) == 1  # existing location matched by name+address
    locs = org.get(f"/local/locations?project_id={pid}&kind=own").json()
    assert len(locs) == 2
    main = next(x for x in locs if x["id"] == loc["id"])
    assert main["photos_count"] == 14 and main["secondary_categories"] == ["Drainage service", "Water heater installation service"]
    assert main["nap"]["website"] == "https://acmeplumbing.example"

    r = upload(org, f"/local/projects/{pid}/import/competitors", _csv([
        ["name", "address", "city", "postcode", "phone", "website", "category", "additional_categories", "rating", "reviews", "photos", "posts_per_month", "lat", "lng"],
        ["Rapid Rooter", "123 Main Street Suite 2", "Springfield", "62704", "2175550300", "https://rapidrooter.example", "Plumber", "Drainage service", "4.8", "210", "60", "4", "39.782", "-89.651"],
        ["Bright Sparks", "50 High St", "Springfield", "62701", "2175550400", "https://brightsparks.example", "Electrician", "", "4.4", "35", "8", "1", "39.79", "-89.64"]]))
    assert r.json()["created"] == 2
    comps = org.get(f"/local/locations?project_id={pid}&kind=competitor").json()
    assert {c["name"] for c in comps} == {"Rapid Rooter", "Bright Sparks"}
    with tenant_session(org.org_id) as db:  # competitor listings are linked to the spec competitor table
        assert {c.domain for c in db.scalars(select(m.Competitor).where(m.Competitor.project_id == pid))} == {"rapidrooter.example", "brightsparks.example"}

    r = upload(org, f"/local/projects/{pid}/import/reviews", _csv(review_rows()))
    assert r.json()["created"] == 5 and len(r.json()["errors"]) == 1
    again = upload(org, f"/local/projects/{pid}/import/reviews", _csv(review_rows())).json()
    assert again["created"] == 0 and again["updated"] == 5  # idempotent re-import
    r = upload(org, f"/local/projects/{pid}/import/posts", _csv([
        ["location", "type", "title", "text", "cta", "url", "date", "image"],
        ["Acme Plumbing", "update", "", "Winter is coming — book your boiler service now to avoid breakdowns. " * 3, "Book", "https://acmeplumbing.example/book", (NOW - timedelta(days=12)).date().isoformat(), "yes"],
        ["Acme Plumbing", "offer", "10% off", "Ten percent off drain cleaning this month for new customers. " * 2, "", "", (NOW - timedelta(days=40)).date().isoformat(), ""],
        ["Acme Plumbing", "update", "", "missing date", "", "", "", ""]]))
    assert r.json()["created"] == 2 and r.json()["errors"][0]["row"] == 4

    r = org.post(f"/local/locations/{loc['id']}/gbp-audit", {})
    assert r.status_code == 200, r.text
    run = r.json()
    res = run["result"]
    assert run["kind"] == "gbp_audit" and run["score"] == res["score"]
    by = {x["key"]: x for x in res["rules"]}
    assert by["reviews_count"]["detail"] == "5 reviews" and by["reviews_count"]["status"] == "fail"
    assert by["response_rate"]["detail"] == "40.0%" and by["response_rate"]["status"] == "fail"
    assert by["rating"]["detail"] == "3.4★"
    assert by["posts"]["status"] == "partial"  # last post 12 days ago
    assert by["same_address"]["status"] == "fail" and res["same_address"][0]["name"] == "Rapid Rooter"
    assert by["nap_consistency"]["status"] == "partial" and res["nap"]["consistency"] is None  # no sources yet
    assert res["maps_checked"] is False
    # score is exactly the weighted sum of the rule outcomes
    assert res["score"] == round(sum(x["earned"] for x in res["rules"]), 1)
    assert res["fixes"][0]["points"] >= res["fixes"][-1]["points"]
    assert any(k["item"].startswith("No duplicate") and not k["ok"] for k in res["knowledge_panel"]["items"])
    # citations feed NAP consistency
    org.patch(f"/local/locations/{loc['id']}", {"citations": [{"source": "Yelp", "name": "Acme Plumbing", "address": "123 Main Street, Springfield, IL 62704", "phone": "217-555-0142"}]})
    res2 = org.post(f"/local/locations/{loc['id']}/gbp-audit", {}).json()["result"]
    assert res2["nap"]["consistency"] == 100
    runs = org.get(f"/local/runs?project_id={pid}&kind=gbp_audit").json()
    assert len(runs) == 2 and org.get(f"/local/runs/{runs[0]['id']}").json()["result"]["score"] == res2["score"]
    # validation
    assert org.patch(f"/local/locations/{loc['id']}", {"rating": 7}).status_code == 422
    assert org.patch(f"/local/locations/{loc['id']}", {"nap": {"website": "not a url"}}).status_code == 400
    assert org.post(f"/local/locations/{comps[0]['id']}/gbp-audit", {}).status_code == 400  # competitor listing


# ================================================================== category finder (16)
def test_category_finder(org):
    p, loc = setup_business(org, primary_category=None, secondary_categories=[])
    for i, cats in enumerate([("Plumber", ["Drainage service"]), ("Plumber", ["Drainage service", "Water heater installation service"]),
                              ("Plumber", []), ("Heating contractor", ["Plumber"])]):
        org.post("/local/locations", {"project_id": p["id"], "kind": "competitor", "name": f"Comp {i}", "primary_category": cats[0],
                                      "secondary_categories": cats[1], "rating": 4.5, "review_count": 50})
    cat = org.get("/local/categories/catalogue?q=plumb").json()
    assert cat["total"] >= 300 and cat["items"][0]["name"].lower().startswith("plumb")
    r = org.post(f"/local/locations/{loc['id']}/categories", {"terms": ["burst pipe repair", "blocked drain", "boiler repair"]})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert res["primary"]["name"] == "Plumber"
    allc = {x["name"]: x for x in res["all"]}
    assert allc["Plumber"]["used_by_label"] == "used by 4 of 4 top competitors"
    assert allc["Drainage service"]["used_by"] == 2
    assert res["competitor_count"] == 4
    r = org.post(f"/local/locations/{loc['id']}/categories/apply", {"primary": "Plumber", "secondary": ["Drainage service", "Plumber", "Emergency plumber"]})
    assert r.json()["primary_category"] == "Plumber" and r.json()["secondary_categories"] == ["Drainage service", "Emergency plumber"]
    loc2 = org.post("/local/locations", {"project_id": p["id"], "name": "Empty Co"}).json()
    assert org.post(f"/local/locations/{loc2['id']}/categories", {}).status_code == 400


# ================================================================== reviews (17) + approval gating
def test_review_audit_and_reply_approval_gating(org, client):
    p, loc = setup_business(org)
    upload(org, f"/local/projects/{p['id']}/import/reviews", _csv(review_rows()))
    org.post("/local/locations", {"project_id": p["id"], "kind": "competitor", "name": "Rapid Rooter", "rating": 4.8, "review_count": 210})
    a = org.get(f"/local/locations/{loc['id']}/review-audit").json()
    st = a["stats"]
    assert st["count"] == 5 and st["avg_rating"] == 3.4 and st["distribution"] == {"1": 1, "2": 1, "3": 0, "4": 1, "5": 2}
    assert st["response_rate"] == 40.0 and st["avg_response_hours"] == 24.0 and st["unanswered_negative"] == 2
    assert st["sentiment"]["negative"] == 2 and st["sentiment"]["positive"] >= 2
    assert {"price & value", "staff & service"} <= {t["topic"] for t in st["topics"]}
    assert st["last_90"]["count"] == 4
    assert any("response rate" in x for x in a["recommendations"])
    assert a["competitors"][0]["name"] == "Rapid Rooter" and a["competitor_median_reviews"] == 210
    reviews = org.get(f"/local/locations/{loc['id']}/reviews").json()
    assert all(r["sentiment"] is not None for r in reviews)
    cat = next(r for r in reviews if r["author"] == "Cat")
    assert cat["sentiment_label"] == "negative" and "price & value" in cat["topics"]
    assert len(org.get(f"/local/locations/{loc['id']}/reviews?sentiment=negative").json()) == 2

    rid = cat["id"]
    # cannot submit/approve/publish without a draft
    assert org.post(f"/local/reviews/{rid}/submit").status_code == 409
    assert org.post(f"/local/reviews/{rid}/publish").status_code == 409
    staff = org.invite_user("staff")
    mgr = org.invite_user("manager")
    r = client.put(f"/api/local/reviews/{rid}/draft", headers=mgr, json={"text": "Sorry to hear this, Cat — please call us so we can put it right."})
    assert r.status_code == 200 and r.json()["reply_status"] == "draft" and r.json()["reply_source"] == "manual"
    assert client.post(f"/api/local/reviews/{rid}/submit", headers=mgr).json()["reply_status"] == "review"
    assert client.post(f"/api/local/reviews/{rid}/approve", headers=staff).status_code == 403  # staff lack 'approve'
    r = client.post(f"/api/local/reviews/{rid}/approve", headers=mgr)
    assert r.status_code == 403 and "own request" in r.text  # cannot approve own request
    assert client.post(f"/api/local/reviews/{rid}/publish", headers=mgr).status_code == 409  # not approved yet
    assert org.post(f"/local/reviews/{rid}/reject", {}).status_code == 400  # notes required
    r = org.post(f"/local/reviews/{rid}/approve", {"notes": "ok"})
    assert r.status_code == 200 and r.json()["reply_status"] == "approved"
    with tenant_session(org.org_id) as db:
        ap = db.get(m.Approval, r.json()["approval_id"])
        assert ap.status == "approved" and ap.entity_type == "gbp_review_reply"
    assert client.post(f"/api/local/reviews/{rid}/publish", headers=staff).status_code == 403  # staff lack 'publish'
    r = org.post(f"/local/reviews/{rid}/publish")
    assert r.status_code == 200
    out = r.json()
    assert out["published"] is False and out["connected"] is False and out["status"] == "approved"
    assert out["message"].startswith("NOT CONNECTED — publish manually") and out["text"].startswith("Sorry to hear")
    after = next(x for x in org.get(f"/local/locations/{loc['id']}/reviews").json() if x["id"] == rid)
    assert after["reply_status"] == "approved" and after["reply"] is None  # nothing pretended
    r = org.post(f"/local/reviews/{rid}/mark-published")
    assert r.json()["reply_status"] == "published" and r.json()["reply"].startswith("Sorry")
    assert org.get(f"/local/locations/{loc['id']}/review-audit").json()["stats"]["response_rate"] == 60.0

    # decisions made in the generic Approvals inbox flow back into the reply workflow
    eve = next(x for x in reviews if x["author"] == "Eve")
    client.put(f"/api/local/reviews/{eve['id']}/draft", headers=mgr, json={"text": "Apologies Eve."})
    sub = client.post(f"/api/local/reviews/{eve['id']}/submit", headers=mgr).json()
    r = org.post(f"/approvals/{sub['approval_id']}/decide", {"decision": "reject", "notes": "too short"})
    assert r.status_code == 200
    eve2 = next(x for x in org.get(f"/local/locations/{loc['id']}/reviews").json() if x["id"] == eve["id"])
    assert eve2["reply_status"] == "rejected"


# ================================================================== GMB posts (31) + post audit (18)
def test_posts_workflow_and_post_audit(org, client):
    p, loc = setup_business(org)
    r = org.post("/local/posts", {"location_id": loc["id"], "post_type": "offer", "title": "Spring boiler check", "body": "Book a boiler check in April and save 15%. " * 4,
                                  "cta_type": "BOOK", "cta_url": "https://acmeplumbing.example/book", "offer_code": "SPRING15",
                                  "start_at": NOW.isoformat(), "end_at": (NOW + timedelta(days=30)).isoformat(), "image_ref": "https://cdn.example/boiler.jpg"})
    assert r.status_code == 200, r.text
    post = r.json()
    assert post["status"] == "draft" and post["source"] == "manual"
    pid_ = post["id"]
    assert org.post(f"/local/posts/{pid_}/publish").status_code == 409  # not approved
    assert org.post(f"/local/posts/{pid_}/approve").status_code == 409  # not in review
    assert org.post("/local/posts", {"location_id": loc["id"], "post_type": "event", "body": "Open day"}).status_code == 400  # needs title/date
    assert org.post("/local/posts", {"location_id": loc["id"], "body": "x" * 1501}).status_code == 422
    assert org.post("/local/posts", {"location_id": loc["id"], "body": "hi", "image_ref": "ftp://x"}).status_code == 400
    mgr = org.invite_user("manager")
    assert client.post(f"/api/local/posts/{pid_}/submit", headers=mgr).json()["status"] == "review"
    assert client.post(f"/api/local/posts/{pid_}/approve", headers=mgr).status_code == 403  # own submission
    assert org.post(f"/local/posts/{pid_}/approve").json()["status"] == "approved"
    # editing an approved post sends it back to draft (needs re-approval)
    r = org.patch(f"/local/posts/{pid_}", {"body": "Book a boiler check in April and save 15% on parts and labour. " * 3})
    assert r.json()["status"] == "draft"
    org.post(f"/local/posts/{pid_}/submit")
    org.post(f"/local/posts/{pid_}/approve")
    r = org.post(f"/local/posts/{pid_}/publish").json()
    assert r["published"] is False and r["connected"] is False and r["status"] == "approved"
    assert "NOT CONNECTED — publish manually" in r["message"] and r["text"].startswith("Book a boiler")
    assert org.get(f"/local/posts/{pid_}").json()["status"] == "approved"
    assert org.post(f"/local/posts/{pid_}/mark-published").json()["status"] == "published"
    assert org.patch(f"/local/posts/{pid_}", {"body": "changed"}).status_code == 409
    # rejected path
    p2 = org.post("/local/posts", {"location_id": loc["id"], "body": "Quick update about our new van."}).json()
    org.post(f"/local/posts/{p2['id']}/submit")
    assert org.post(f"/local/posts/{p2['id']}/reject", {"notes": "too short"}).json()["status"] == "rejected"
    assert len(org.get(f"/local/posts?project_id={p['id']}&status=published").json()) == 1

    pa = org.get(f"/local/locations/{loc['id']}/post-audit").json()
    assert pa["published"] == 1 and pa["days_since_last"] == 0 and pa["types"]["offer"] == 1
    assert pa["cta_pct"] == 100.0 and pa["image_pct"] == 100.0 and pa["drafts"] == 0  # the rejected post is not a pending draft
    assert any("event" in x for x in pa["recommendations"])


# ================================================================== geo-grid (20)
def test_grid_import_metrics_and_compare(org):
    p, loc = setup_business(org)
    r = org.post("/local/grid/scans", {"project_id": p["id"], "location_id": loc["id"], "keyword": "Plumber Springfield", "grid_size": 3, "radius_km": 2})
    assert r.status_code == 200, r.text
    s1 = r.json()
    assert s1["status"] == "AWAITING_DATA" and s1["mode"] == "import" and len(s1["points"]) == 9 and s1["spacing_km"] == 2.0
    exp = L.grid_points(39.7817, -89.6501, 3, 2)
    for got, want in zip(s1["points"], exp):
        assert (got["row"], got["col"]) == (want["row"], want["col"])
        assert abs(got["lat"] - want["lat"]) < 1e-6 and abs(got["lng"] - want["lng"]) < 1e-6
    assert s1["keyword"] == "plumber springfield"
    tpl = org.get(f"/local/grid/scans/{s1['id']}/template.csv")
    assert tpl.status_code == 200 and tpl.text.splitlines()[0] == "row,col,lat,lng,rank"
    # ranks by row/col; centre by lat/lng; "20+" = not found
    rows = [["row", "col", "lat", "lng", "rank"], [0, 0, "", "", 1], [0, 1, "", "", 2], [0, 2, "", "", 3], [1, 0, "", "", "20+"],
            ["", "", exp[4]["lat"], exp[4]["lng"], 5], [1, 2, "", "", 12], [2, 0, "", "", "x"], [2, 1, "", "", 4], [9, 9, "", "", 1]]
    r = upload(org, f"/local/grid/scans/{s1['id']}/import", _csv(rows))
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["updated"] == 8 and len(res["errors"]) == 1
    met = res["metrics"]
    assert met["observed"] == 8 and met["arp"] == 4.5 and met["atrp"] == 8.62 and met["solv"] == 37.5
    assert res["scan"]["status"] == "PARTIAL"
    r = org.post(f"/local/grid/scans/{s1['id']}/ranks", [{"row": 2, "col": 2, "rank": 7}])
    assert r.json()["scan"]["status"] == "COMPLETED" and r.json()["metrics"]["observed"] == 9
    s1m = r.json()["metrics"]
    assert s1m["arp"] == round((1 + 2 + 3 + 5 + 12 + 4 + 7) / 7, 2) and s1m["solv"] == round(100 * 3 / 9, 1)

    s2 = org.post("/local/grid/scans", {"project_id": p["id"], "location_id": loc["id"], "keyword": "plumber springfield", "grid_size": 3, "radius_km": 2}).json()
    assert s2["keyword_id"] == s1["keyword_id"]
    r = org.post(f"/local/grid/scans/{s2['id']}/ranks", [{"idx": i, "rank": v} for i, v in enumerate([1, 1, 2, 3, 1, 4, 0, 2, 3])])
    assert r.json()["metrics"]["solv"] == round(100 * 7 / 9, 1)
    cmp_ = org.get(f"/local/grid/compare?a={s1['id']}&b={s2['id']}").json()
    assert cmp_["same_center"] is True and cmp_["deltas"]["solv"] == round(100 * 7 / 9, 1) - round(100 * 3 / 9, 1)
    assert cmp_["per_point"][3] == {"idx": 3, "a": 0, "b": 3, "improvement": 18}
    assert cmp_["per_point"][6]["improvement"] == 0
    assert len(org.get(f"/local/grid/scans?project_id={p['id']}").json()) == 2
    # validation
    assert org.post("/local/grid/scans", {"project_id": p["id"], "location_id": loc["id"], "keyword": "x", "grid_size": 11}).status_code == 422
    assert org.post(f"/local/grid/scans/{s2['id']}/ranks", [{"row": 0, "col": 0, "rank": "abc"}]).json()["errors"]
    prev = org.get("/local/grid/preview?lat=39.78&lng=-89.65&size=5&radius_km=2").json()
    assert len(prev["points"]) == 25 and prev["spacing_km"] == 1.0
    assert org.c.delete(f"/api/local/grid/scans/{s2['id']}", headers=org.h).status_code == 200


def test_live_grid_scan_with_connected_maps(org, monkeypatch):
    from app.services import local_google as G
    p, loc = setup_business(org, place_id="ChIJ-acme")
    connect(org.org_id, "google_maps", "maps-test-key")
    calls = []

    def fake(method, url, **kw):
        calls.append((url, kw.get("params")))
        assert kw["params"]["key"] == "maps-test-key" and "location" in kw["params"]
        lat = float(kw["params"]["location"].split(",")[0])
        others = [{"place_id": f"other{i}", "name": f"Other {i}", "types": ["plumber"], "geometry": {"location": {"lat": lat, "lng": 0}}} for i in range(5)]
        me = {"place_id": "ChIJ-acme", "name": "Acme Plumbing", "types": ["plumber"], "geometry": {"location": {"lat": lat, "lng": 0}}}
        if lat > 39.79:     # north row: rank 1
            res = [me] + others
        elif lat > 39.77:   # middle row: rank 4
            res = others[:3] + [me] + others[3:]
        else:               # south row: not found
            res = others
        return FakeResp({"status": "OK", "results": res})
    monkeypatch.setattr(G, "http_call", fake)
    r = org.post("/local/grid/scans", {"project_id": p["id"], "location_id": loc["id"], "keyword": "plumber", "grid_size": 3, "radius_km": 2, "mode": "live"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"] == "QUEUED" and s["credits"] == 9 and s["job_id"]
    for _ in range(3):
        run_once()
    s = org.get(f"/local/grid/scans/{s['id']}").json()
    assert s["status"] == "COMPLETED", s
    assert [pt["position"] for pt in s["points"]] == [1, 1, 1, 4, 4, 4, 0, 0, 0]
    assert s["metrics"]["arp"] == 2.5 and s["metrics"]["atrp"] == round((3 + 12 + 63) / 9, 2) and s["metrics"]["solv"] == round(100 / 3, 1)
    assert s["points"][0]["top"][0] == "Acme Plumbing"
    assert len(calls) == 9
    with tenant_session(org.org_id) as db:
        assert db.scalar(select(m.UsageCounter.used).where(m.UsageCounter.metric == "local_rank_credits")) == 9
        assert db.get(m.Job, s["job_id"]).status == "COMPLETED"


# ================================================================== GBP connected: sync + publish only after approval
def test_gbp_sync_and_publish_when_connected_and_approved(org, monkeypatch):
    from app.services import local_google as G
    p = org.project("GBP synced")
    connect(org.org_id, "gbp", json.dumps({"access_token": "tok", "refresh_token": "r", "expires_at": time.time() + 3600}))
    sent = []

    def fake(method, url, **kw):
        assert kw["headers"]["Authorization"] == "Bearer tok"
        sent.append((method, url, kw.get("json")))
        if url.endswith("/accounts"):
            return FakeResp({"accounts": [{"name": "accounts/1"}]})
        if url.endswith("accounts/1/locations"):
            return FakeResp({"locations": [{"name": "locations/9", "title": "Acme Plumbing", "websiteUri": "https://acmeplumbing.example",
                                            "phoneNumbers": {"primaryPhone": "(217) 555-0142"},
                                            "storefrontAddress": {"addressLines": ["123 Main St"], "locality": "Springfield", "postalCode": "62704"},
                                            "categories": {"primaryCategory": {"displayName": "Plumber"}, "additionalCategories": [{"displayName": "Drainage service"}]},
                                            "regularHours": {"periods": [{"openDay": "MONDAY", "openTime": {"hours": 8}, "closeDay": "MONDAY", "closeTime": {"hours": 18}}]},
                                            "profile": {"description": "Plumbers"}, "metadata": {"placeId": "ChIJ-acme"}, "latlng": {"latitude": 39.78, "longitude": -89.65}}]})
        if url.endswith("/reviews") and method == "GET":
            return FakeResp({"averageRating": 4.6, "totalReviewCount": 87, "reviews": [
                {"name": "accounts/1/locations/9/reviews/r1", "reviewer": {"displayName": "Ann"}, "starRating": "TWO", "comment": "Slow and rude",
                 "createTime": "2026-09-01T10:00:00Z"},
                {"name": "accounts/1/locations/9/reviews/r2", "reviewer": {"displayName": "Bo"}, "starRating": "FIVE", "comment": "Great",
                 "createTime": "2026-08-01T10:00:00Z", "reviewReply": {"comment": "Thanks", "updateTime": "2026-08-02T10:00:00Z"}}]})
        if url.endswith("/localPosts") and method == "GET":
            return FakeResp({"localPosts": [{"name": "accounts/1/locations/9/localPosts/p1", "topicType": "STANDARD", "summary": "We are open on Saturdays now.",
                                             "createTime": "2026-09-20T10:00:00Z", "state": "LIVE"}]})
        if url.endswith("/reply") and method == "PUT":
            return FakeResp({"comment": kw["json"]["comment"]})
        if url.endswith("/localPosts") and method == "POST":
            return FakeResp({"name": "accounts/1/locations/9/localPosts/new1"})
        raise AssertionError(url)
    monkeypatch.setattr(G, "http_call", fake)
    assert org.post(f"/local/projects/{p['id']}/gbp-sync", {}).status_code == 200
    run_once()
    locs = org.get(f"/local/locations?project_id={p['id']}").json()
    assert len(locs) == 1
    loc = locs[0]
    assert loc["source"] == "gbp" and loc["gbp_resource"] == "accounts/1/locations/9" and loc["place_id"] == "ChIJ-acme"
    assert loc["primary_category"] == "Plumber" and loc["hours"] == {"mon": [["08:00", "18:00"]]}
    assert loc["review_count"] == 87 and loc["rating"] == 4.6
    revs = org.get(f"/local/locations/{loc['id']}/reviews").json()
    assert {r["reply_status"] for r in revs} == {"none", "published"}
    assert len(org.get(f"/local/posts?project_id={p['id']}&status=published").json()) == 1

    ann = next(r for r in revs if r["author"] == "Ann")
    org.c.put(f"/api/local/reviews/{ann['id']}/draft", headers=org.h, json={"text": "Sorry Ann, please call us."})
    # connected but NOT approved → refused, nothing sent
    n_before = len(sent)
    assert org.post(f"/local/reviews/{ann['id']}/publish").status_code == 409
    org.post(f"/local/reviews/{ann['id']}/submit")
    assert org.post(f"/local/reviews/{ann['id']}/publish").status_code == 409
    assert len(sent) == n_before
    org.post(f"/local/reviews/{ann['id']}/approve")
    r = org.post(f"/local/reviews/{ann['id']}/publish").json()
    assert r["queued"] is True and r["status"] == "publishing"
    run_once()
    ann2 = next(x for x in org.get(f"/local/locations/{loc['id']}/reviews").json() if x["id"] == ann["id"])
    assert ann2["reply_status"] == "published" and ann2["reply"] == "Sorry Ann, please call us."
    assert ("PUT", "https://mybusiness.googleapis.com/v4/accounts/1/locations/9/reviews/r1/reply", {"comment": "Sorry Ann, please call us."}) in sent

    post = org.post("/local/posts", {"location_id": loc["id"], "post_type": "update", "body": "New: same-day drain unblocking across Springfield.",
                                     "cta_type": "CALL"}).json()
    assert org.post(f"/local/posts/{post['id']}/publish").status_code == 409
    org.post(f"/local/posts/{post['id']}/submit")
    org.post(f"/local/posts/{post['id']}/approve")
    assert org.post(f"/local/posts/{post['id']}/publish").json()["queued"] is True
    run_once()
    done = org.get(f"/local/posts/{post['id']}").json()
    assert done["status"] == "published" and done["external_name"] == "accounts/1/locations/9/localPosts/new1"
    body = next(j for meth, u, j in sent if meth == "POST")
    assert body["summary"].startswith("New: same-day") and body["callToAction"] == {"actionType": "CALL"} and body["topicType"] == "STANDARD"


# ================================================================== local scan (19) + competitors (21)
def test_local_scan_and_competitor_research(org):
    p, loc = setup_business(org, rating=4.1, review_count=18, posts_per_month=0.5)
    for c in [{"name": "Rapid Rooter", "primary_category": "Plumber", "secondary_categories": ["Drainage service", "Water heater installation service"],
               "rating": 4.9, "review_count": 320, "photos_count": 80, "posts_per_month": 6, "nap": {"website": "https://rr.example", "lat": 39.782, "lng": -89.651}},
              {"name": "Sparky", "primary_category": "Electrician", "rating": 3.9, "review_count": 6, "photos_count": 2, "nap": {"lat": 39.9, "lng": -89.9}}]:
        assert org.post("/local/locations", {"project_id": p["id"], "kind": "competitor", **c}).status_code == 200
    r = org.post(f"/local/projects/{p['id']}/scan", {"location_id": loc["id"], "keyword": "Plumber"})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    names = [x["name"] for x in res["rows"]]
    assert names[0] == "Rapid Rooter" and names[-1] == "Sparky" and res["own_position"] == 2
    own = next(x for x in res["rows"] if x["own"])
    assert own["distance_km"] == 0.0 and own["components"]["category"] == 15
    assert r.json()["score"] == own["local_score"]
    c = org.get(f"/local/projects/{p['id']}/competitors?location_id={loc['id']}&keyword=plumber").json()
    assert [x["name"] for x in c["competitors"]] == ["Rapid Rooter", "Sparky"]
    assert c["ranking"][0]["name"] == "Rapid Rooter"
    gaps = c["gaps"]
    assert any(g["category"] == "Water heater installation service" for g in gaps["categories_missing"])
    rc = next(mm for mm in gaps["metrics"] if mm["metric"] == "review_count")
    assert rc["competitor_best"] == 320 and rc["own"] == 18
    assert gaps["summary"]


# ================================================================== website ↔ GBP (22)
def test_website_gbp_comparison(org, site_server):
    p, loc = setup_business(org, hours="Mo-Fr 08:00-18:00; Sa 09:00-13:00", nap={"website": site_server + "/local/match.html"})
    r = org.post(f"/local/locations/{loc['id']}/website-audit", {})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    st = {f["field"]: f["status"] for f in res["fields"]}
    assert st == {"name": "match", "address": "match", "phone": "match", "hours": "match", "website": "match", "categories": "match",
                  "schema": "match", "schema_fields": "match", "maps": "match"}, res["fields"]
    assert res["score"] == 100.0 and res["schema_present"] and res["same_as"] == ["https://www.facebook.com/acmeplumbing"]

    r = org.post(f"/local/locations/{loc['id']}/website-audit", {"url": site_server + "/local/mismatch.html"})
    res = r.json()["result"]
    st = {f["field"]: f["status"] for f in res["fields"]}
    assert st["name"] == "mismatch" and st["address"] == "mismatch" and st["phone"] == "mismatch"
    assert st["hours"] == "mismatch" and st["maps"] == "missing"
    assert st["categories"] == "partial"  # "Pipes" in the title relates to Plumber; nothing about drainage
    assert st["schema"] == "match" and st["schema_fields"] == "partial"
    assert res["score"] < 30 and len(res["fixes"]) >= 6
    # GBP audit picks up the latest website run as a NAP source
    a = org.post(f"/local/locations/{loc['id']}/gbp-audit", {}).json()["result"]
    assert a["nap"]["checks"][0]["source"] == "website" and a["nap"]["consistency"] < 50
    assert org.post(f"/local/locations/{loc['id']}/website-audit", {"url": site_server + "/local/nope.html"}).status_code == 422
    assert org.post(f"/local/locations/{loc['id']}/website-audit", {"url": "ftp://x"}).status_code == 400


# ================================================================== automation (35)
def test_automation_bulk_posts_per_location(org):
    p, loc1 = setup_business(org)
    loc2 = org.post("/local/locations", {"project_id": p["id"], "name": "Acme Plumbing Shelbyville", "nap": {"city": "Shelbyville"}}).json()
    base = {"project_id": p["id"], "name": "Weekly tips", "post_type": "update", "title_template": "{location_name} tip",
            "body_template": "Plumbing tip from {location_name} in {city}: check your stopcock this {month}. Ask about {promo}.",
            "cta_type": "LEARN_MORE", "cta_url_template": "https://acmeplumbing.example/{city}",
            "location_ids": [loc1["id"], loc2["id"]], "frequency": "weekly",
            "variables": {loc1["id"]: {"promo": "free drain checks"}, loc2["id"]: {"promo": "10% off"}}}
    bad = dict(base, body_template="Hi {nonexistent}")
    assert org.post("/local/automations", bad).status_code == 400
    prev = org.post("/local/automations/preview", base).json()
    assert [x["missing_vars"] for x in prev] == [[], []]
    assert "Shelbyville" in prev[1]["body"] and "10% off" in prev[1]["body"]
    r = org.post("/local/automations", base)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["schedule_id"] and a["next_run_at"]
    with tenant_session(org.org_id) as db:
        sch = db.get(m.JobSchedule, a["schedule_id"])
        assert sch.kind == "local_automation_run" and sch.frequency == "weekly"
    run_once()  # scheduler tick enqueues the run, worker executes it
    posts = org.get(f"/local/posts?project_id={p['id']}&status=review").json()
    assert len(posts) == 2 and {x["source"] for x in posts} == {"automation"}
    by_loc = {x["location_id"]: x for x in posts}
    assert by_loc[loc1["id"]]["body"].startswith("Plumbing tip from Acme Plumbing in Springfield")
    assert "free drain checks" in by_loc[loc1["id"]]["body"]
    assert by_loc[loc2["id"]]["title"] == "Acme Plumbing Shelbyville tip"
    assert by_loc[loc2["id"]]["cta_url"] == "https://acmeplumbing.example/Shelbyville"
    assert all(x["approval_id"] for x in posts)
    with tenant_session(org.org_id) as db:
        assert db.scalar(select(func.count()).select_from(m.Approval).where(m.Approval.entity_type == "gbp_post", m.Approval.status == "pending")) == 2
        assert db.get(m.JobSchedule, a["schedule_id"]).next_run_at > NOW + timedelta(days=6)
    got = org.get(f"/local/automations/{a['id']}").json()
    assert got["runs"] == 1 and got["posts_created"] == 2 and got["posts_pending"] == 2
    # posts created by automation are never published without approval
    assert org.post(f"/local/posts/{posts[0]['id']}/publish").status_code == 409
    # pausing stops runs; manual run of a paused automation is refused
    assert org.patch(f"/local/automations/{a['id']}", {"enabled": False}).json()["enabled"] is False
    assert org.post(f"/local/automations/{a['id']}/run").status_code == 409
    org.patch(f"/local/automations/{a['id']}", {"enabled": True})
    assert org.post(f"/local/automations/{a['id']}/run").status_code == 200
    run_once()
    assert len(org.get(f"/local/posts?project_id={p['id']}&status=review").json()) == 4
    # a one-off automation with a missing per-location variable skips that location (no broken posts)
    once = dict(base, name="Once", frequency="once", variables={loc1["id"]: {"promo": "x"}, loc2["id"]: {}})
    r = org.post("/local/automations", once).json()
    assert r["job_id"]
    run_once()
    with tenant_session(org.org_id) as db:
        job = db.get(m.Job, r["job_id"])
        assert job.status == "COMPLETED" and job.result["created"] == 1 and "promo" in job.result["skipped"][0]["reason"]
    assert org.c.delete(f"/api/local/automations/{a['id']}", headers=org.h).status_code == 200
    with tenant_session(org.org_id) as db:
        assert db.get(m.JobSchedule, a["schedule_id"]) is None


# ================================================================== isolation / roles
def test_cross_org_isolation(org, org_factory):
    p, loc = setup_business(org)
    upload(org, f"/local/projects/{p['id']}/import/reviews", _csv(review_rows()))
    rv = org.get(f"/local/locations/{loc['id']}/reviews").json()[0]
    post = org.post("/local/posts", {"location_id": loc["id"], "body": "Hello Springfield, we are hiring."}).json()
    scan = org.post("/local/grid/scans", {"project_id": p["id"], "location_id": loc["id"], "keyword": "plumber", "grid_size": 3}).json()
    run = org.post(f"/local/locations/{loc['id']}/gbp-audit", {}).json()
    auto = org.post("/local/automations", {"project_id": p["id"], "name": "A", "body_template": "Hi {city}", "location_ids": [loc["id"]],
                                           "frequency": "monthly"}).json()
    b = org_factory()
    pid, lid = p["id"], loc["id"]
    gets = [f"/local/overview?project_id={pid}", f"/local/locations?project_id={pid}", f"/local/locations/{lid}",
            f"/local/locations/{lid}/reviews", f"/local/locations/{lid}/review-audit", f"/local/locations/{lid}/post-audit",
            f"/local/posts?project_id={pid}", f"/local/posts/{post['id']}", f"/local/grid/scans?project_id={pid}",
            f"/local/grid/scans/{scan['id']}", f"/local/grid/scans/{scan['id']}/template.csv", f"/local/grid/compare?a={scan['id']}&b={scan['id']}",
            f"/local/runs?project_id={pid}", f"/local/runs/{run['id']}", f"/local/projects/{pid}/competitors",
            f"/local/automations?project_id={pid}", f"/local/automations/{auto['id']}"]
    for g in gets:
        assert b.get(g).status_code == 404, g
    posts = [(f"/local/locations/{lid}/gbp-audit", {}), (f"/local/locations/{lid}/categories", {"terms": ["plumber"]}),
             (f"/local/locations/{lid}/website-audit", {}), (f"/local/locations/{lid}/reviews", {"rating": 5}),
             (f"/local/reviews/{rv['id']}/submit", None), (f"/local/reviews/{rv['id']}/approve", None), (f"/local/reviews/{rv['id']}/publish", None),
             (f"/local/posts/{post['id']}/submit", None), (f"/local/posts/{post['id']}/approve", None), (f"/local/posts/{post['id']}/publish", None),
             ("/local/posts", {"location_id": lid, "body": "intrusion"}), (f"/local/grid/scans/{scan['id']}/ranks", [{"idx": 0, "rank": 1}]),
             ("/local/grid/scans", {"project_id": pid, "location_id": lid, "keyword": "x"}), (f"/local/projects/{pid}/scan", {"location_id": lid, "keyword": "x"}),
             (f"/local/projects/{pid}/gbp-sync", {}), (f"/local/automations/{auto['id']}/run", None),
             ("/local/automations", {"project_id": pid, "name": "x", "body_template": "x", "location_ids": [lid]}),
             ("/local/locations", {"project_id": pid, "name": "x"})]
    for path, body in posts:
        assert b.post(path, body).status_code == 404, path
    assert upload(b, f"/local/projects/{pid}/import/reviews", _csv(review_rows())).status_code == 404
    assert b.patch(f"/local/locations/{lid}", {"name": "pwned"}).status_code == 404
    assert b.patch(f"/local/posts/{post['id']}", {"body": "pwned"}).status_code == 404
    assert b.c.put(f"/api/local/reviews/{rv['id']}/draft", headers=b.h, json={"text": "x"}).status_code == 404
    for d in (f"/local/locations/{lid}", f"/local/posts/{post['id']}", f"/local/grid/scans/{scan['id']}", f"/local/automations/{auto['id']}", f"/local/reviews/{rv['id']}"):
        assert b.c.delete("/api" + d, headers=b.h).status_code == 404, d
    # B's own data is separate and A's is unchanged
    pb, locb = setup_business(b, name="B Co")
    assert [x["name"] for x in b.get(f"/local/locations?project_id={pb['id']}").json()] == ["B Co"]
    assert org.get(f"/local/locations/{lid}").json()["name"] == "Acme Plumbing"
    assert org.get(f"/local/posts/{post['id']}").json()["body"].startswith("Hello")
    with tenant_session(b.org_id) as db:  # RLS: B's session sees none of A's rows
        assert db.scalar(select(func.count()).select_from(m.GbpLocation).where(m.GbpLocation.id == lid)) == 0
        assert db.scalar(select(func.count()).select_from(ml.LocalAutomation)) == 0


def test_client_role_scoped_to_assigned_projects(org_factory, client):
    org = org_factory(plan="enterprise")  # client user access is an Enterprise feature
    p, loc = setup_business(org)
    ch = org.invite_user("client")
    assert client.get(f"/api/local/locations/{loc['id']}", headers=ch).status_code == 404
    assert client.get(f"/api/local/locations?project_id={p['id']}", headers=ch).status_code == 404
    cu = client.get("/api/auth/me", headers=ch).json()["user"]
    r = org.patch(f"/projects/{p['id']}", {"client_user_ids": [cu["id"]]})
    if r.status_code != 200:  # fall back to direct assignment
        with tenant_session(org.org_id) as db:
            db.get(m.Project, p["id"]).client_user_ids = [cu["id"]]
    assert client.get(f"/api/local/locations/{loc['id']}", headers=ch).status_code == 200
    assert client.get(f"/api/local/locations/{loc['id']}/review-audit", headers=ch).status_code == 200
    assert client.post("/api/local/posts", headers=ch, json={"location_id": loc["id"], "body": "x"}).status_code == 403
    assert client.post(f"/api/local/locations/{loc['id']}/gbp-audit", headers=ch, json={}).status_code == 403
