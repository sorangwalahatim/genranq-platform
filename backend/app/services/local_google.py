"""Google Business Profile ("gbp", OAuth2) and Google Maps / Places ("google_maps", API key) clients for the
Local SEO modules. Every call goes through the Integrations Hub: credentials from the vault, token-bucket rate
limiting, NotConnected when the org has not connected the provider. Nothing here fabricates data."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import integrations as ih
from .integrations import IntegrationError, NotConnected, http_call  # noqa: F401  (http_call patched in tests)
from .local_core import cat_lookup, parse_date

GBP_ACCT = "https://mybusinessaccountmanagement.googleapis.com/v1"
GBP_INFO = "https://mybusinessbusinessinformation.googleapis.com/v1"
GBP_V4 = "https://mybusiness.googleapis.com/v4"
PLACES = "https://maps.googleapis.com/maps/api/place"
STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
TOPIC = {"update": "STANDARD", "offer": "OFFER", "event": "EVENT"}
TOPIC_BACK = {v: k for k, v in TOPIC.items()}
GENERIC_TYPES = {"point_of_interest", "establishment", "premise", "store", "food", "health", "finance", "general_contractor_x",
                 "political", "locality", "geocode", "route", "street_address"}
TYPE_MAP = {"car_repair": "Auto repair shop", "hair_care": "Hair salon", "beauty_salon": "Beauty salon", "lawyer": "Lawyer",
            "real_estate_agency": "Real estate agency", "general_contractor": "General contractor", "roofing_contractor": "Roofing contractor",
            "insurance_agency": "Insurance agency", "travel_agency": "Travel agency", "car_dealer": "Car dealer", "car_wash": "Car wash",
            "car_rental": "Car rental agency", "moving_company": "Moving company", "veterinary_care": "Veterinarian",
            "physiotherapist": "Physical therapist", "accounting": "Accountant", "lodging": "Hotel", "gym": "Gym", "spa": "Spa",
            "bar": "Bar", "cafe": "Cafe", "bakery": "Bakery", "meal_takeaway": "Restaurant", "meal_delivery": "Meal delivery",
            "laundry": "Laundry service", "storage": "Self-storage facility", "locksmith": "Locksmith", "painter": "Painter",
            "plumber": "Plumber", "electrician": "Electrician", "dentist": "Dentist", "doctor": "Doctor", "florist": "Florist",
            "pharmacy": "Pharmacy", "restaurant": "Restaurant", "hospital": "Hospital", "funeral_home": "Funeral home"}


def _ok(r, what):
    return ih._ok(r, what)


# ------------------------------------------------------------------ Places (google_maps)
def _maps_key(db: Session, org_id) -> str:
    cr = ih.credentials(db, org_id, "google_maps")
    return cr["secret"]


def types_to_categories(types: list[str]) -> list[str]:
    out = []
    for t in types or []:
        if t in GENERIC_TYPES:
            continue
        name = TYPE_MAP.get(t) or t.replace("_", " ").capitalize()
        c = cat_lookup(name)
        if c and c["name"] not in out:
            out.append(c["name"])
    return out


def places_text_search(db: Session, org_id, query: str, lat=None, lng=None, radius_m: int = 5000) -> list[dict]:
    key = _maps_key(db, org_id)
    ih.take_token(db, org_id, "google_maps")
    params = {"query": query, "key": key}
    if lat is not None and lng is not None:
        params.update(location=f"{float(lat)},{float(lng)}", radius=str(int(max(100, min(50000, radius_m)))))
    r = _ok(http_call("GET", f"{PLACES}/textsearch/json", params=params), "Places text search")
    j = r.json()
    if j.get("status") not in ("OK", "ZERO_RESULTS"):
        raise IntegrationError(f"Places: {j.get('status')} {j.get('error_message', '')}")
    out = []
    for i, x in enumerate(j.get("results") or []):
        loc = (x.get("geometry") or {}).get("location") or {}
        out.append({"rank": i + 1, "place_id": x.get("place_id"), "name": x.get("name"), "rating": x.get("rating"),
                    "review_count": x.get("user_ratings_total"), "types": x.get("types") or [], "address": x.get("formatted_address"),
                    "lat": loc.get("lat"), "lng": loc.get("lng"), "photos_count": len(x.get("photos") or [])})
    return out


def place_details(db: Session, org_id, place_id: str) -> dict:
    key = _maps_key(db, org_id)
    ih.take_token(db, org_id, "google_maps")
    fields = "name,formatted_address,formatted_phone_number,international_phone_number,website,opening_hours,types,rating," \
             "user_ratings_total,photos,geometry,address_components,url,editorial_summary"
    r = _ok(http_call("GET", f"{PLACES}/details/json", params={"place_id": place_id, "fields": fields, "key": key}), "Place details")
    j = r.json()
    if j.get("status") != "OK":
        raise IntegrationError(f"Places details: {j.get('status')} {j.get('error_message', '')}")
    x = j.get("result") or {}
    comps = {c["types"][0]: c for c in x.get("address_components") or [] if c.get("types")}

    def comp(k, short=False):
        c = comps.get(k)
        return (c.get("short_name") if short else c.get("long_name")) if c else None
    street = " ".join(p for p in (comp("street_number"), comp("route")) if p) or None
    loc = (x.get("geometry") or {}).get("location") or {}
    hours = {}
    for p in ((x.get("opening_hours") or {}).get("periods") or []):
        o, c = p.get("open") or {}, p.get("close") or {}
        day = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"][int(o.get("day", 0))]
        if o.get("time") and c.get("time"):
            hours.setdefault(day, []).append([f"{o['time'][:2]}:{o['time'][2:]}", f"{c['time'][:2]}:{c['time'][2:]}"])
    return {"place_id": place_id, "name": x.get("name"), "phone": x.get("international_phone_number") or x.get("formatted_phone_number"),
            "website": x.get("website"), "rating": x.get("rating"), "review_count": x.get("user_ratings_total"),
            "types": x.get("types") or [], "categories": types_to_categories(x.get("types") or []),
            "photos_count": len(x.get("photos") or []), "lat": loc.get("lat"), "lng": loc.get("lng"),
            "address": street, "city": comp("postal_town") or comp("locality"), "region": comp("administrative_area_level_1", True),
            "postcode": comp("postal_code"), "country": comp("country", True), "formatted_address": x.get("formatted_address"),
            "hours": hours, "description": (x.get("editorial_summary") or {}).get("overview"), "maps_url": x.get("url")}


def find_rank(results: list[dict], place_id: str | None, name: str | None) -> int:
    """1-based rank of the business in Places results; 0 when absent."""
    from .local_core import norm_name
    for r in results:
        if place_id and r.get("place_id") == place_id:
            return r["rank"]
    nn = norm_name(name)
    for r in results:
        if nn and norm_name(r.get("name")) == nn:
            return r["rank"]
    return 0


# ------------------------------------------------------------------ Business Profile (gbp)
def _gbp_headers(db: Session, org_id) -> dict:
    tok = ih.oauth_access_token(db, org_id, "gbp")
    ih.take_token(db, org_id, "gbp")
    return {"Authorization": f"Bearer {tok}"}


def gbp_accounts(db: Session, org_id) -> list[dict]:
    r = _ok(http_call("GET", f"{GBP_ACCT}/accounts", headers=_gbp_headers(db, org_id)), "GBP accounts")
    return r.json().get("accounts") or []


def gbp_locations(db: Session, org_id, account: str) -> list[dict]:
    mask = "name,title,storefrontAddress,phoneNumbers,websiteUri,categories,regularHours,profile,latlng,serviceItems,metadata"
    out, token = [], None
    for _ in range(20):
        params = {"readMask": mask, "pageSize": 100}
        if token:
            params["pageToken"] = token
        j = _ok(http_call("GET", f"{GBP_INFO}/{account}/locations", headers=_gbp_headers(db, org_id), params=params), "GBP locations").json()
        out += j.get("locations") or []
        token = j.get("nextPageToken")
        if not token:
            break
    return out


def map_gbp_location(account: str, x: dict) -> dict:
    a = x.get("storefrontAddress") or {}
    lines = a.get("addressLines") or []
    cats = x.get("categories") or {}
    prim = (cats.get("primaryCategory") or {}).get("displayName")
    sec = [c.get("displayName") for c in cats.get("additionalCategories") or [] if c.get("displayName")]
    services = []
    for s in x.get("serviceItems") or []:
        lbl = ((s.get("freeFormServiceItem") or {}).get("label") or {}).get("displayName") or \
              (s.get("structuredServiceItem") or {}).get("serviceTypeId")
        if lbl:
            services.append(lbl)
    md = x.get("metadata") or {}
    ll = x.get("latlng") or {}
    return {"gbp_resource": f"{account}/{x.get('name')}", "name": x.get("title"), "place_id": md.get("placeId"),
            "cid": (re.search(r"cid=(\d+)", md.get("mapsUri") or "") or [None, None])[1],
            "nap": {"address": ", ".join(lines) or None, "city": a.get("locality"), "region": a.get("administrativeArea"),
                    "postcode": a.get("postalCode"), "country": a.get("regionCode"),
                    "phone": (x.get("phoneNumbers") or {}).get("primaryPhone"), "website": x.get("websiteUri"),
                    "lat": ll.get("latitude"), "lng": ll.get("longitude")},
            "primary_category": prim, "secondary_categories": sec, "hours": x.get("regularHours") or {},
            "description": (x.get("profile") or {}).get("description"), "services": services,
            "verified": bool(md.get("hasVoiceOfMerchant"))}


def gbp_reviews(db: Session, org_id, resource: str) -> tuple[list[dict], dict]:
    out, token, meta = [], None, {}
    for _ in range(40):
        params = {"pageSize": 50}
        if token:
            params["pageToken"] = token
        j = _ok(http_call("GET", f"{GBP_V4}/{resource}/reviews", headers=_gbp_headers(db, org_id), params=params), "GBP reviews").json()
        meta = {"rating": j.get("averageRating"), "review_count": j.get("totalReviewCount")}
        for rv in j.get("reviews") or []:
            rep = rv.get("reviewReply") or {}
            out.append({"external_id": rv.get("name"), "author": (rv.get("reviewer") or {}).get("displayName"),
                        "rating": STARS.get(rv.get("starRating")), "body": rv.get("comment"), "reviewed_at": parse_date(rv.get("createTime")),
                        "reply": rep.get("comment"), "replied_at": parse_date(rep.get("updateTime"))})
        token = j.get("nextPageToken")
        if not token:
            break
    return out, meta


def gbp_posts(db: Session, org_id, resource: str) -> list[dict]:
    j = _ok(http_call("GET", f"{GBP_V4}/{resource}/localPosts", headers=_gbp_headers(db, org_id), params={"pageSize": 100}), "GBP posts").json()
    out = []
    for p in j.get("localPosts") or []:
        cta = p.get("callToAction") or {}
        out.append({"external_name": p.get("name"), "post_type": TOPIC_BACK.get(p.get("topicType"), "update"),
                    "title": (p.get("event") or {}).get("title"), "body": p.get("summary"), "cta_type": cta.get("actionType"),
                    "cta_url": cta.get("url"), "image_ref": ((p.get("media") or [{}])[0] or {}).get("googleUrl"),
                    "posted_at": parse_date(p.get("createTime")), "status": "published" if p.get("state") in (None, "LIVE") else "draft"})
    return out


def gbp_reply(db: Session, org_id, review_name: str, comment: str) -> dict:
    r = _ok(http_call("PUT", f"{GBP_V4}/{review_name}/reply", headers=_gbp_headers(db, org_id), json={"comment": comment}), "GBP reply")
    return r.json() if r.content else {}


def _gdate(d: datetime | None):
    return {"year": d.year, "month": d.month, "day": d.day} if d else None


def gbp_create_post(db: Session, org_id, resource: str, post: dict) -> dict:
    body: dict = {"languageCode": "en", "summary": post["body"], "topicType": TOPIC.get(post.get("post_type") or "update", "STANDARD")}
    if post.get("cta_type"):
        body["callToAction"] = {"actionType": post["cta_type"], **({"url": post["cta_url"]} if post.get("cta_url") and post["cta_type"] != "CALL" else {})}
    if post.get("post_type") in ("event", "offer"):
        sched = {"startDate": _gdate(post.get("start_at")), "endDate": _gdate(post.get("end_at") or post.get("start_at"))}
        body["event"] = {"title": post.get("title") or "Offer", "schedule": {k: v for k, v in sched.items() if v}}
    if post.get("post_type") == "offer" and post.get("offer_code"):
        body["offer"] = {"couponCode": post["offer_code"], **({"redeemOnlineUrl": post["cta_url"]} if post.get("cta_url") else {})}
    if post.get("image_ref") and str(post["image_ref"]).startswith("https://"):
        body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": post["image_ref"]}]
    r = _ok(http_call("POST", f"{GBP_V4}/{resource}/localPosts", headers=_gbp_headers(db, org_id), json=body), "GBP create post")
    return r.json()


def now():
    return datetime.now(timezone.utc)
