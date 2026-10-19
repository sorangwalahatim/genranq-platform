"""Unit tests for Local SEO computations (no DB / network)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import local_core as L

NOW = datetime(2026, 6, 15, 12, tzinfo=timezone.utc)


# ---------------------------------------------------------------- NAP normalisation
@pytest.mark.parametrize("a,b,exp", [
    ("+1 (217) 555-0142", "217.555.0142", "match"),
    ("+44 20 7946 0958", "020 7946 0958", "match"),
    ("0044 20 7946 0958", "+442079460958", "match"),
    ("217-555-0142", "217-555-9999", "mismatch"),
    ("", "217-555-0142", "missing"),
])
def test_phone_compare(a, b, exp):
    assert L.compare_phone(a, b) == exp


@pytest.mark.parametrize("a,b,exp", [
    ("123 Main Street, Springfield, IL 62704", "123 Main St., Springfield IL 62704 USA", "match"),
    ("123 North Main Avenue Suite 4, Springfield", "123 N Main Ave #4, Springfield", "match"),
    ("123 Main Street, Springfield, IL 62704", "125 Main Street, Springfield, IL 62704", "mismatch"),
    ("123 Main Street, Springfield", "123 Main Street, Springfield, IL 62704", "partial"),
    ("77 Oak Avenue, Shelbyville", "123 Main Street, Springfield", "mismatch"),
])
def test_address_compare(a, b, exp):
    assert L.compare_address(a, b) == exp


def test_name_compare_and_key():
    assert L.compare_name("Acme Plumbing LLC", "acme plumbing") == "match"
    assert L.compare_name("Acme Plumbing & Heating", "Acme Plumbing and Heating Ltd") == "match"
    assert L.compare_name("Acme Plumbing", "Acme Plumbing Springfield") == "partial"
    assert L.compare_name("Acme Plumbing", "Best Pipes") == "mismatch"
    assert L.address_key({"address": "123 Main Street Suite 4", "postcode": "62704"}) == \
        L.address_key({"address": "123 Main St", "postcode": "62704"})


def test_hours_parsing_formats():
    want = {d: [["09:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}
    assert L.parse_hours("Mo-Fr 09:00-17:00") == want
    assert L.parse_hours({"mon": ["9am-5pm"], "tue": [["09:00", "17:00"]]}) == {"mon": [["09:00", "17:00"]], "tue": [["09:00", "17:00"]]}
    gbp = {"periods": [{"openDay": "MONDAY", "openTime": {"hours": 9}, "closeDay": "MONDAY", "closeTime": {"hours": 17, "minutes": 30}}]}
    assert L.parse_hours(gbp) == {"mon": [["09:00", "17:30"]]}
    spec = [{"dayOfWeek": ["https://schema.org/Saturday"], "opens": "10:00:00", "closes": "14:00:00"}]
    assert L.parse_hours(spec) == {"sat": [["10:00", "14:00"]]}
    assert L.parse_hours("Fr-Mo 08:00-12:00").keys() == {"fri", "sat", "sun", "mon"}
    assert L.compare_hours("Mo-Fr 09:00-17:00", want)["status"] == "match"
    assert L.compare_hours("Mo-Fr 09:00-17:00", "Mo-Fr 09:00-16:00")["status"] == "mismatch"


# ---------------------------------------------------------------- grid
@pytest.mark.parametrize("size,radius", [(3, 1.0), (5, 2.0), (7, 3.5), (9, 10.0)])
def test_grid_coordinates_are_exact(size, radius):
    lat, lng = 39.7817, -89.6501
    pts = L.grid_points(lat, lng, size, radius)
    assert len(pts) == size * size
    step = 2 * radius / (size - 1)
    centre = pts[(size * size) // 2]
    assert (centre["lat"], centre["lng"]) == (lat, lng)
    for p in pts:
        n, e = radius - p["row"] * step, -radius + p["col"] * step
        # haversine distance from the centre equals the planar offset length (within rounding of 6 dp ≈ 0.1 m)
        assert abs(L.haversine_km(lat, lng, p["lat"], p["lng"]) - (n * n + e * e) ** 0.5) < 0.001
        assert p["idx"] == p["row"] * size + p["col"]
    # north row has larger latitude, east column larger longitude
    assert pts[0]["lat"] > centre["lat"] > pts[-1]["lat"]
    assert pts[size - 1]["lng"] > centre["lng"] > pts[0]["lng"]
    # adjacent points along the middle row are `step` km apart
    mid = [p for p in pts if p["row"] == size // 2]
    for a, b in zip(mid, mid[1:]):
        assert abs(L.haversine_km(a["lat"], a["lng"], b["lat"], b["lng"]) - step) < 0.002


def test_grid_validation():
    with pytest.raises(ValueError):
        L.grid_points(0, 0, 1, 1)
    with pytest.raises(ValueError):
        L.grid_points(0, 0, 3, 0)
    with pytest.raises(ValueError):
        L.grid_points(91, 0, 3, 1)


def test_destination_known_value():
    lat, lng = L.destination(0, 0, 90, 111.195)  # ~1 degree of longitude at the equator
    assert abs(lat) < 1e-6 and abs(lng - 1.0) < 0.001


def test_grid_metrics():
    # 9 points: ranks 1,2,3 (top3), 5, 12, two not found (0), one unobserved (None), 4
    met = L.grid_metrics([1, 2, 3, 0, None, 5, 12, 0, 4], depth=20)
    assert met["observed"] == 8 and met["found"] == 6 and met["not_found"] == 2
    assert met["arp"] == round((1 + 2 + 3 + 5 + 12 + 4) / 6, 2)            # 4.5
    assert met["atrp"] == round((1 + 2 + 3 + 21 + 5 + 12 + 21 + 4) / 8, 2)  # 8.62
    assert met["solv"] == 37.5  # 3 of 8 observed points in the top 3
    assert met["top3"] == 3 and met["best"] == 1 and met["worst"] == 12
    empty = L.grid_metrics([None] * 9)
    assert empty["arp"] is None and empty["atrp"] is None and empty["solv"] is None
    none_found = L.grid_metrics([0, 0, 0])
    assert none_found["arp"] is None and none_found["atrp"] == 21 and none_found["solv"] == 0


# ---------------------------------------------------------------- sentiment / topics
def test_sentiment_lexicon():
    assert L.sentiment("Excellent service, highly recommend!")["label"] == "positive"
    assert L.sentiment("Rude staff and they overcharged me. Terrible.")["label"] == "negative"
    assert L.sentiment("The plumber came on Tuesday.")["label"] == "neutral"
    # negation flips polarity
    assert L.sentiment("not good")["score"] < 0
    assert L.sentiment("wasn't bad at all")["score"] > 0
    # intensifier strengthens
    assert L.sentiment("very good")["score"] > L.sentiment("good")["score"]
    s = L.sentiment("great great great great great")["score"]
    assert -1 <= s <= 1


def test_topics():
    t = L.topics("Great price, the staff were friendly and they arrived on time")
    assert {"price & value", "staff & service", "speed & wait time"} <= set(t)
    assert L.topics("") == []


# ---------------------------------------------------------------- review stats
def _rv(days, rating, body="", reply=None, reply_after_h=None):
    d = NOW - timedelta(days=days)
    return {"rating": rating, "body": body, "reviewed_at": d, "reply": reply,
            "replied_at": d + timedelta(hours=reply_after_h) if reply_after_h is not None else None}


def test_review_stats_math():
    reviews = [_rv(10, 5, "Excellent friendly staff", "Thanks!", 2), _rv(20, 4, "Good work", "Thank you", 10),
               _rv(40, 1, "Rude and late, terrible"), _rv(100, 5, "Brilliant", "Cheers", 48), _rv(130, 3, "ok")]
    st = L.review_stats(reviews, now=NOW)
    assert st["count"] == 5 and st["avg_rating"] == 3.6
    assert st["distribution"] == {"1": 1, "2": 0, "3": 1, "4": 1, "5": 2}
    assert st["response_rate"] == 60.0
    assert st["avg_response_hours"] == 20.0 and st["median_response_hours"] == 10.0
    assert st["last_90"] == {"count": 3, "avg_rating": round(10 / 3, 2)}
    assert st["prev_90"] == {"count": 2, "avg_rating": 4.0}
    assert st["rating_trend_90d"] == round(10 / 3 - 4.0, 2)
    assert st["unanswered_negative"] == 2
    assert st["sentiment"]["negative"] == 1 and st["sentiment"]["positive"] >= 3
    months = {m["month"]: m["count"] for m in st["per_month"]}
    assert months["2026-06"] == 1 and months["2026-05"] == 2 and sum(months.values()) == 5
    assert st["per_month"][-1]["month"] == "2026-06"
    recs = L.review_recommendations(st)
    assert any("response rate" in r for r in recs) and any("3★ or lower" in r for r in recs)


def test_review_stats_totals_fallback():
    st = L.review_stats([], now=NOW, totals={"rating": 4.7, "review_count": 88})
    assert st["count"] == 0 and st["total_count"] == 88 and st["avg_rating"] == 4.7 and st["response_rate"] is None


# ---------------------------------------------------------------- post audit
def test_post_audit():
    posts = [{"status": "published", "post_type": "update", "body": "x" * 200, "cta_type": "BOOK", "image_ref": "https://i/1.jpg",
              "posted_at": NOW - timedelta(days=d)} for d in (3, 10, 17, 60)]
    posts.append({"status": "published", "post_type": "offer", "body": "short", "cta_type": None, "image_ref": None, "posted_at": NOW - timedelta(days=95)})
    posts.append({"status": "draft", "post_type": "event", "body": "d", "posted_at": None})
    pa = L.post_audit(posts, now=NOW)
    assert pa["published"] == 5 and pa["drafts"] == 1 and pa["days_since_last"] == 3
    assert pa["last_90_days"] == 4 and pa["max_gap_days"] == 43
    assert pa["types"] == {"update": 4, "offer": 1, "event": 0}
    assert pa["cta_pct"] == 80.0 and pa["image_pct"] == 80.0 and pa["too_short"] == 1
    assert any("event" in r for r in pa["recommendations"])
    assert any(g["days"] == 43 for g in pa["gaps_over_14d"])
    assert L.post_audit([], now=NOW)["recommendations"][0].startswith("No published posts")


# ---------------------------------------------------------------- GBP audit scoring
FULL = {"name": "Acme Plumbing", "nap": {"address": "123 Main St", "city": "Springfield", "postcode": "62704", "phone": "+1 217 555 0142",
                                          "website": "https://acme.example"},
        "primary_category": "Plumber", "secondary_categories": ["Drainage service", "Water heater installation service"],
        "hours": "Mo-Fr 08:00-18:00", "description": "d" * 400, "photos_count": 25, "services": ["a", "b", "c"], "attributes": ["x", "y", "z"],
        "qa_questions": 2, "qa_answered": 2, "has_logo": True, "place_id": "p1", "social_links": ["https://fb.com/a"]}
GOOD_CTX = {"review_stats": {"total_count": 40, "avg_rating": 4.8, "response_rate": 95.0}, "post_audit": {"days_since_last": 2},
            "same_address": [], "nap_sources": [{"source": "website", "name": "Acme Plumbing", "address": "123 Main Street, Springfield, 62704",
                                                 "phone": "(217) 555-0142"}],
            "website_run": {"schema_present": True, "same_as": ["x"], "score": 95}}


def test_gbp_audit_perfect_profile():
    r = L.gbp_audit(FULL, GOOD_CTX)
    assert sum(x["weight"] for x in r["rules"]) == 100
    assert r["score"] == 100.0 and r["fixes"] == []
    assert r["nap"]["consistency"] == 100
    assert r["knowledge_panel"]["ready"] == r["knowledge_panel"]["total"]


def test_gbp_audit_weighted_partial_and_priorities():
    p = dict(FULL, secondary_categories=["Drainage service"], photos_count=4, description="", name="Acme Plumbing | Best Plumber Springfield 24/7")
    ctx = dict(GOOD_CTX, review_stats={"total_count": 12, "avg_rating": 4.2, "response_rate": 40.0},
               same_address=[{"name": "Other Co"}])
    r = L.gbp_audit(p, ctx)
    by = {x["key"]: x for x in r["rules"]}
    assert by["secondary_categories"]["status"] == "partial" and by["secondary_categories"]["earned"] == 2
    assert by["photos"]["status"] == "partial" and by["description"]["status"] == "fail"
    assert by["name_clean"]["status"] == "fail" and by["same_address"]["status"] == "fail"
    assert by["reviews_count"]["earned"] == 3 and by["rating"]["earned"] == 3 and by["response_rate"]["earned"] == 0
    # stuffed name only partially matches the website NAP source → nap_consistency partial (-2.5)
    lost = 2 + 3 + 6 + 3 + 5 + 3 + 3 + 6 + 2.5
    assert r["score"] == 100 - lost
    pts = [f["points"] for f in r["fixes"]]
    assert pts == sorted(pts, reverse=True) and r["fixes"][0]["priority"] == "high"


def test_gbp_audit_empty_profile():
    r = L.gbp_audit({"name": "X"}, {})
    assert r["score"] < 15
    assert r["nap"]["consistency"] is None
    assert len(r["fixes"]) >= 15


def test_description_over_limit_fails():
    r = L.gbp_audit(dict(FULL, description="d" * 800), GOOD_CTX)
    assert next(x for x in r["rules"] if x["key"] == "description")["status"] == "fail"


# ---------------------------------------------------------------- categories
def test_catalogue_size_and_uniqueness():
    cat = L.catalogue()
    assert len(cat) >= 300
    assert len({c["name"] for c in cat}) == len(cat)


def test_category_matching_with_competitors():
    comp = [["Plumber", "Drainage service"], ["Plumber", "Heating contractor"], ["Plumber"], ["Plumber", "Drainage service"]]
    r = L.match_categories(["blocked drain", "burst pipe", "boiler repair", "water heater", "emergency plumber"], comp, current=["Plumber"])
    assert r["primary"]["name"] in ("Plumber", "Emergency plumber")
    names = {s["name"]: s for s in r["all"]}
    assert names["Plumber"]["used_by"] == 4 and names["Plumber"]["used_by_label"] == "used by 4 of 4 top competitors"
    assert names["Drainage service"]["used_by"] == 2
    assert "Drainage service" in [m["name"] for m in r["missing_common"]]
    assert r["competitor_count"] == 4


def test_category_matching_dentist():
    r = L.match_categories(["teeth whitening", "veneers", "dental implants", "dentist"], [])
    assert r["primary"]["name"] in ("Dentist", "Cosmetic dentist")
    top = [s["name"] for s in r["secondary"][:6]]
    assert "Cosmetic dentist" in top or r["primary"]["name"] == "Cosmetic dentist"
    assert all("Plumber" != s for s in top)


# ---------------------------------------------------------------- local score
def test_local_score_ordering():
    strong = {"name": "A", "rating": 4.9, "review_count": 300, "primary_category": "Plumber", "photos_count": 60,
              "website": "https://a", "lat": 39.78, "lng": -89.65, "posts_per_month": 4}
    weak = {"name": "B", "rating": 3.8, "review_count": 4, "primary_category": "Handyman", "photos_count": 1, "lat": 39.9, "lng": -89.9}
    a, b = L.local_score(strong, "plumber", (39.78, -89.65)), L.local_score(weak, "plumber", (39.78, -89.65))
    assert a["score"] > 90 and b["score"] < 30 and a["distance_km"] == 0.0
    assert a["components"]["category"] == 15 and b["components"]["category"] == 0


def test_competitor_gaps():
    own = {"primary_category": "Plumber", "secondary_categories": [], "rating": 4.2, "review_count": 10, "photos_count": 5, "posts_per_month": 0}
    comps = [{"primary_category": "Plumber", "secondary_categories": ["Drainage service"], "rating": 4.8, "review_count": 120, "photos_count": 40, "posts_per_month": 4},
             {"primary_category": "Plumber", "secondary_categories": ["Drainage service"], "rating": 4.5, "review_count": 60, "photos_count": 20, "posts_per_month": 2}]
    g = L.competitor_gaps(own, comps)
    assert g["categories_missing"][0] == {"category": "Drainage service", "used_by": 2, "of": 2}
    rc = next(m for m in g["metrics"] if m["metric"] == "review_count")
    assert rc["behind"] and rc["competitor_best"] == 120


# ---------------------------------------------------------------- website extraction
def test_extract_site_local_schema_and_text():
    html = open("tests/fixtures/site/local/match.html").read()
    s = L.extract_site_local(html, "https://acme.example/")
    assert s["schema"]["name"] == "Acme Plumbing" and "Plumber" in s["schema"]["types"]
    assert s["schema"]["hours"]["sat"] == [["09:00", "13:00"]]
    assert s["maps_embed"] and "+12175550142" in s["tel_links"]
    g = {"name": "Acme Plumbing", "nap": {"address": "123 Main St", "city": "Springfield", "region": "IL", "postcode": "62704",
                                          "phone": "217-555-0142", "website": "https://acme.example"},
         "hours": "Mo-Fr 08:00-18:00; Sa 09:00-13:00", "primary_category": "Plumber", "secondary_categories": ["Drainage service"]}
    s["final_url"] = "https://acme.example/"
    r = L.compare_website_gbp(s, g)
    assert r["score"] == 100.0, r["fields"]
