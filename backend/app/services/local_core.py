"""Local SEO computations (pure functions, no DB / network): NAP normalisation, geo-grid maths,
rank metrics (ARP / ATRP / SoLV), review sentiment + topics, review & post statistics,
GBP profile audit scoring, category matching, local score and website ↔ GBP comparison.

Everything here is deterministic so it can be unit-tested and so results never depend on
an external service being connected.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

EARTH_KM = 6371.0088
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def utcnow():
    return datetime.now(timezone.utc)


def _aware(d):
    if d is None:
        return None
    if isinstance(d, str):
        d = parse_date(d)
        if d is None:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def parse_date(s) -> datetime | None:
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return _aware(s)
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return _aware(datetime.strptime(s, fmt))
        except ValueError:
            continue
    try:
        return _aware(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError:
        return None


# =============================================================== NAP normalisation
LEGAL = {"llc", "ltd", "limited", "inc", "incorporated", "co", "corp", "corporation", "pty", "plc", "gmbh", "llp", "company"}
ABBR = {
    "street": "st", "str": "st", "road": "rd", "avenue": "ave", "av": "ave", "boulevard": "blvd", "drive": "dr",
    "lane": "ln", "court": "ct", "place": "pl", "square": "sq", "terrace": "ter", "highway": "hwy", "parkway": "pkwy",
    "circle": "cir", "crescent": "cres", "close": "cl", "suite": "ste", "unit": "ste", "apartment": "apt", "floor": "fl",
    "building": "bldg", "north": "n", "south": "s", "east": "e", "west": "w", "northeast": "ne", "northwest": "nw",
    "southeast": "se", "southwest": "sw", "mount": "mt", "saint": "st", "fort": "ft", "route": "rte", "way": "way",
    "first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th",
}
COUNTRY_WORDS = {"usa", "us", "united", "states", "america", "uk", "kingdom", "gb", "great", "britain", "england",
                 "australia", "au", "canada", "ca", "nz", "zealand", "ireland", "ie", "of"}


def norm_name(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ").replace("'", "")
    toks = [t for t in re.findall(r"[a-z0-9]+", s) if t not in LEGAL]
    return " ".join(toks)


def norm_phone(s: str | None) -> str:
    """Digits only, with a leading international '00' or '+' dropped."""
    d = re.sub(r"\D", "", s or "")
    return d[2:] if d.startswith("00") else d


def phone_key(s: str | None) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-9:] if len(d) >= 9 else d  # last 9 digits survive every national/international formatting


def norm_address(s: str | None) -> str:
    s = (s or "").lower().replace("#", " ste ")
    toks = re.findall(r"[a-z0-9]+", s)
    out = [ABBR.get(t, t) for t in toks]
    return " ".join(t for t in out if t not in COUNTRY_WORDS)


def address_string(nap: dict) -> str:
    parts = [nap.get("address"), nap.get("address2"), nap.get("city"), nap.get("region"), nap.get("postcode")]
    return ", ".join(str(p) for p in parts if p)


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def compare_name(a, b) -> str:
    x, y = norm_name(a), norm_name(b)
    if not x or not y:
        return "missing"
    if x == y:
        return "match"
    if x in y or y in x or _jaccard(set(x.split()), set(y.split())) >= 0.6:
        return "partial"
    return "mismatch"


def compare_phone(a, b) -> str:
    x, y = phone_key(a), phone_key(b)
    if not x or not y:
        return "missing"
    return "match" if x == y else "mismatch"


def compare_address(a, b) -> str:
    x, y = norm_address(a), norm_address(b)
    if not x or not y:
        return "missing"
    if x == y:
        return "match"
    tx, ty = set(x.split()), set(y.split())
    small, big = (tx, ty) if len(tx) <= len(ty) else (ty, tx)
    first_x = next((t for t in x.split() if t.isdigit()), None)
    first_y = next((t for t in y.split() if t.isdigit()), None)
    if first_x and first_y and first_x != first_y:  # different street numbers
        return "mismatch"
    if small <= big or _jaccard(tx, ty) >= 0.7:
        return "partial" if small != big else "match"
    return "mismatch"


def address_key(nap: dict) -> str:
    """Key for 'same address' detection: street line + postcode/city, normalised (suite numbers dropped)."""
    street = norm_address(nap.get("address"))
    street = re.sub(r"\b(ste|apt|fl|bldg)\s+\w+\b", "", street).strip()
    loc = norm_address(nap.get("postcode") or nap.get("city") or "")
    return f"{street}|{loc}" if street else ""


# =============================================================== hours
DAY_ALIASES = {
    "mo": "mon", "mon": "mon", "monday": "mon", "tu": "tue", "tue": "tue", "tues": "tue", "tuesday": "tue",
    "we": "wed", "wed": "wed", "wednesday": "wed", "th": "thu", "thu": "thu", "thur": "thu", "thurs": "thu", "thursday": "thu",
    "fr": "fri", "fri": "fri", "friday": "fri", "sa": "sat", "sat": "sat", "saturday": "sat", "su": "sun", "sun": "sun", "sunday": "sun",
}


def _hhmm(t: str) -> str:
    t = t.strip().lower()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", t)
    if not m:
        return t
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def _day_range(expr: str) -> list[str]:
    out = []
    for part in re.split(r"\s*,\s*", expr.strip().lower()):
        if "-" in part:
            a, b = [DAY_ALIASES.get(x.strip()) for x in part.split("-", 1)]
            if a and b:
                i, j = DAYS.index(a), DAYS.index(b)
                rng = DAYS[i:j + 1] if i <= j else DAYS[i:] + DAYS[:j + 1]
                out += rng
        elif DAY_ALIASES.get(part):
            out.append(DAY_ALIASES[part])
    return out


HOURS_RX = re.compile(r"((?:[A-Za-z]{2,9})(?:\s*[-,]\s*[A-Za-z]{2,9})*)\s*:?\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*[-–]\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", re.I)


def parse_hours(v) -> dict:
    """Canonical hours {day: [[open, close], ...]} from a dict, GBP regularHours, schema.org openingHours(Specification)
    or a free string like 'Mo-Fr 09:00-17:00; Sa 10:00-14:00'."""
    out: dict[str, list] = {}
    if not v:
        return out
    if isinstance(v, dict) and "periods" in v:  # GBP regularHours
        for p in v.get("periods") or []:
            d = DAY_ALIASES.get(str(p.get("openDay", "")).lower()[:3])
            ot, ct = p.get("openTime") or {}, p.get("closeTime") or {}
            if d:
                out.setdefault(d, []).append([f"{int(ot.get('hours', 0)):02d}:{int(ot.get('minutes', 0)):02d}",
                                              f"{int(ct.get('hours', 24 if not ct else 0)):02d}:{int(ct.get('minutes', 0)):02d}"])
        return _sort_hours(out)
    if isinstance(v, dict):
        for k, slots in v.items():
            d = DAY_ALIASES.get(str(k).lower())
            if d and isinstance(slots, list):
                for s in slots:
                    if isinstance(s, (list, tuple)) and len(s) == 2:
                        out.setdefault(d, []).append([_hhmm(str(s[0])), _hhmm(str(s[1]))])
                    elif isinstance(s, str) and "-" in s:
                        a, b = s.split("-", 1)
                        out.setdefault(d, []).append([_hhmm(a), _hhmm(b)])
            elif d and isinstance(slots, str) and "-" in slots:
                a, b = slots.split("-", 1)
                out.setdefault(d, []).append([_hhmm(a), _hhmm(b)])
        return _sort_hours(out)
    if isinstance(v, list):
        for item in v:
            if isinstance(item, dict):  # OpeningHoursSpecification
                days = item.get("dayOfWeek")
                days = days if isinstance(days, list) else [days]
                for dd in days:
                    d = DAY_ALIASES.get(str(dd or "").rsplit("/", 1)[-1].lower())
                    if d and item.get("opens") and item.get("closes"):
                        out.setdefault(d, []).append([_hhmm(str(item["opens"])[:5]), _hhmm(str(item["closes"])[:5])])
            elif isinstance(item, str):
                for d, slots in parse_hours(item).items():
                    out.setdefault(d, []).extend(slots)
        return _sort_hours(out)
    if isinstance(v, str):
        for m in HOURS_RX.finditer(v):
            for d in _day_range(m.group(1)):
                out.setdefault(d, []).append([_hhmm(m.group(2)), _hhmm(m.group(3))])
    return _sort_hours(out)


def _sort_hours(h: dict) -> dict:
    return {d: [list(x) for x in sorted({tuple(x) for x in h[d]})] for d in DAYS if h.get(d)}


def compare_hours(a: dict, b: dict) -> dict:
    a, b = parse_hours(a), parse_hours(b)
    if not a or not b:
        return {"status": "missing", "differences": []}
    diffs = [{"day": d, "gbp": a.get(d, []), "website": b.get(d, [])} for d in DAYS if a.get(d, []) != b.get(d, [])]
    return {"status": "match" if not diffs else ("partial" if len(diffs) <= 2 else "mismatch"), "differences": diffs}


# =============================================================== geo / grid
def haversine_km(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp, dl = p2 - p1, math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def destination(lat, lng, bearing_deg, dist_km) -> tuple[float, float]:
    """Great-circle destination point from (lat, lng) after dist_km on the initial bearing (inverse haversine)."""
    if dist_km == 0:
        return float(lat), float(lng)
    d = dist_km / EARTH_KM
    th, p1, l1 = math.radians(bearing_deg), math.radians(float(lat)), math.radians(float(lng))
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(th))
    l2 = l1 + math.atan2(math.sin(th) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def grid_points(lat, lng, size: int, radius_km: float) -> list[dict]:
    """size×size grid centred on (lat, lng); the outermost ring sits radius_km north/south/east/west of the centre.
    Points are row-major from the north-west corner: idx = row*size + col. Each point is placed on the great circle
    at the bearing/distance of its planar (east, north) offset, so its haversine distance from the centre is exact."""
    if not (2 <= size <= 15):
        raise ValueError("grid size must be between 2 and 15")
    if radius_km <= 0:
        raise ValueError("radius must be positive")
    if not (-85 <= float(lat) <= 85 and -180 <= float(lng) <= 180):
        raise ValueError("centre coordinates out of range")
    step = 2 * radius_km / (size - 1)
    pts = []
    for r in range(size):
        for c in range(size):
            north = radius_km - r * step
            east = -radius_km + c * step
            dist = math.hypot(north, east)
            bearing = math.degrees(math.atan2(east, north)) % 360
            plat, plng = destination(lat, lng, bearing, dist)
            pts.append({"idx": r * size + c, "row": r, "col": c, "lat": round(plat, 6), "lng": round(plng, 6),
                        "offset_km": {"north": round(north, 4), "east": round(east, 4)}, "distance_km": round(dist, 4)})
    return pts


def grid_metrics(positions: list, depth: int = 20) -> dict:
    """positions: one entry per grid point — int rank (1..depth), 0 = checked but not found in the top `depth`,
    None = not observed yet.
      ARP  = average rank over points where the business was found.
      ATRP = average rank over all observed points, counting not-found as depth+1.
      SoLV = share of observed points where the business ranks in the top 3 (Map Pack), in %."""
    observed = [p for p in positions if p is not None]
    found = [p for p in observed if 1 <= p <= depth]
    n = len(observed)
    return {
        "points": len(positions), "observed": n, "found": len(found),
        "arp": round(sum(found) / len(found), 2) if found else None,
        "atrp": round(sum(p if 1 <= p <= depth else depth + 1 for p in observed) / n, 2) if n else None,
        "solv": round(100 * sum(1 for p in found if p <= 3) / n, 1) if n else None,
        "top3": sum(1 for p in found if p <= 3), "top10": sum(1 for p in found if p <= 10),
        "not_found": n - len(found), "best": min(found) if found else None, "worst": max(found) if found else None,
    }


# =============================================================== sentiment + topics
POS = set("""good great excellent amazing awesome fantastic wonderful brilliant outstanding superb perfect best love loved lovely
friendly helpful professional recommend recommended efficient quick fast prompt clean tidy reliable honest fair reasonable
affordable knowledgeable courteous polite pleasant happy satisfied impressed impressive thorough careful attentive kind
caring delicious tasty fresh beautiful comfortable welcoming responsive punctual skilled expert quality exceptional
trustworthy easy smooth seamless gentle patient informative accommodating spotless fabulous terrific stellar
top-notch neat organised organized warm thanks thank grateful appreciated appreciate pleased enjoyed enjoy nice cool
glad favourite favorite worth valuable reassuring transparent ontime on-time hassle-free painless""".split())
NEG = set("""bad poor terrible awful horrible worst rude unprofessional slow late dirty expensive overpriced disappointed
disappointing disappointment never wrong broken broke unhelpful useless waste careless sloppy messy noisy cold stale
bland overcooked undercooked incompetent dishonest scam rip-off ripoff overcharged overcharge refund complaint complain
problem problems issue issues mistake mistakes ignored ignore unreliable unresponsive delay delayed delays cancelled
canceled cancel avoid annoying angry frustrating frustrated horrendous disgusting shocking appalling dreadful mediocre
pricey hidden damaged damage leak leaking leaked unsafe unfriendly arrogant dismissive confusing confused lied liar
nightmare regret worse hate hated filthy smelly chaotic disorganised disorganized lazy unacceptable pathetic rushed
painful hurt waiting""".split())
NEGATORS = {"not", "no", "never", "dont", "didnt", "doesnt", "isnt", "wasnt", "werent", "wont", "cant", "couldnt",
            "wouldnt", "shouldnt", "hardly", "without", "nothing", "nor", "aint", "havent", "hasnt"}
BOOST = {"very": 1.5, "extremely": 1.8, "really": 1.4, "super": 1.5, "so": 1.3, "incredibly": 1.8, "absolutely": 1.6,
         "highly": 1.5, "truly": 1.4, "totally": 1.4, "most": 1.3}

TOPICS = {
    "staff & service": ["staff", "team", "employee", "service", "customer service", "receptionist", "friendly", "rude", "helpful", "attitude", "manager", "owner"],
    "price & value": ["price", "pricing", "cost", "expensive", "cheap", "affordable", "value", "overpriced", "quote", "charge", "fee", "money", "bill", "invoice"],
    "quality of work": ["quality", "job", "work", "workmanship", "result", "results", "finish", "repair", "fixed", "standard", "thorough"],
    "speed & wait time": ["wait", "waiting", "waited", "quick", "fast", "slow", "late", "on time", "punctual", "delay", "prompt", "minutes", "hours", "same day"],
    "communication": ["communication", "communicate", "call", "called", "email", "explained", "explain", "respond", "response", "update", "informed", "listen"],
    "booking & appointments": ["booking", "book", "booked", "appointment", "schedule", "scheduled", "reschedule", "availability", "cancel", "cancelled"],
    "cleanliness": ["clean", "dirty", "tidy", "mess", "messy", "hygiene", "spotless", "filthy"],
    "location & parking": ["location", "parking", "park", "access", "find", "directions", "convenient"],
    "professionalism": ["professional", "unprofessional", "expert", "knowledgeable", "experienced", "skilled", "competent", "qualified"],
    "food & drink": ["food", "meal", "dish", "menu", "taste", "delicious", "coffee", "drink", "drinks", "portion", "breakfast", "dinner", "lunch"],
    "atmosphere": ["atmosphere", "ambience", "vibe", "decor", "music", "noisy", "cosy", "cozy", "comfortable"],
}


def _tokens(text: str) -> list[str]:
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"n't\b", " not", t)
    return re.findall(r"[a-z][a-z'\-]*", t.replace("'", ""))


def sentiment(text: str) -> dict:
    """Lexicon sentiment with negation (3-token window) and intensifiers. score ∈ [-1, 1]."""
    toks = _tokens(text)
    s, hits = 0.0, []
    for i, w in enumerate(toks):
        v = 1.0 if w in POS else -1.0 if w in NEG else 0.0
        if not v:
            continue
        window = toks[max(0, i - 3):i]
        if any(x in NEGATORS for x in window):
            v = -v * 0.75
        if i and toks[i - 1] in BOOST:
            v *= BOOST[toks[i - 1]]
        s += v
        hits.append(w)
    score = s / math.sqrt(s * s + 15) if s else 0.0
    label = "positive" if score >= 0.2 else "negative" if score <= -0.2 else "neutral"
    return {"score": round(score, 3), "label": label, "terms": hits}


def topics(text: str) -> list[str]:
    low = " " + " ".join(_tokens(text)) + " "
    out = []
    for topic, kws in TOPICS.items():
        if any(f" {k} " in low for k in kws):
            out.append(topic)
    return out


# =============================================================== review statistics
def review_stats(reviews: list[dict], now: datetime | None = None, totals: dict | None = None) -> dict:
    """reviews: [{rating, body, reviewed_at, reply, replied_at, sentiment?}]"""
    now = now or utcnow()
    n = len(reviews)
    rated = [int(r["rating"]) for r in reviews if r.get("rating")]
    dist = {str(k): sum(1 for x in rated if x == k) for k in range(1, 6)}
    replied = [r for r in reviews if (r.get("reply") or "").strip()]
    rt = []
    for r in replied:
        a, b = _aware(r.get("reviewed_at")), _aware(r.get("replied_at"))
        if a and b and b >= a:
            rt.append((b - a).total_seconds() / 3600)
    months: dict[str, dict] = defaultdict(lambda: {"count": 0, "rating_sum": 0, "rated": 0, "replied": 0})
    for r in reviews:
        d = _aware(r.get("reviewed_at"))
        if not d:
            continue
        mm = months[d.strftime("%Y-%m")]
        mm["count"] += 1
        if r.get("rating"):
            mm["rating_sum"] += int(r["rating"])
            mm["rated"] += 1
        if (r.get("reply") or "").strip():
            mm["replied"] += 1
    by_month = [{"month": k, "count": v["count"], "avg_rating": round(v["rating_sum"] / v["rated"], 2) if v["rated"] else None,
                 "replied": v["replied"]} for k, v in sorted(months.items())]
    # fill gaps so velocity charts show zero months
    if by_month:
        filled, cur = [], datetime.strptime(by_month[0]["month"], "%Y-%m")
        end = datetime(now.year, now.month, 1)
        have = {b["month"]: b for b in by_month}
        while cur <= end:
            k = cur.strftime("%Y-%m")
            filled.append(have.get(k, {"month": k, "count": 0, "avg_rating": None, "replied": 0}))
            cur = datetime(cur.year + (cur.month // 12), cur.month % 12 + 1, 1)
        by_month = filled[-24:]

    def window(days, offset=0):
        lo, hi = now - timedelta(days=days + offset), now - timedelta(days=offset)
        w = [r for r in reviews if _aware(r.get("reviewed_at")) and lo < _aware(r.get("reviewed_at")) <= hi]
        rr = [int(r["rating"]) for r in w if r.get("rating")]
        return {"count": len(w), "avg_rating": round(sum(rr) / len(rr), 2) if rr else None}

    sents = []
    topic_agg: dict[str, dict] = defaultdict(lambda: {"mentions": 0, "positive": 0, "negative": 0, "score_sum": 0.0})
    for r in reviews:
        se = sentiment(r.get("body") or "")
        sents.append(se)
        for t in topics(r.get("body") or ""):
            a = topic_agg[t]
            a["mentions"] += 1
            a["score_sum"] += se["score"]
            if se["label"] == "positive":
                a["positive"] += 1
            elif se["label"] == "negative":
                a["negative"] += 1
    topic_rows = sorted([{"topic": k, "mentions": v["mentions"], "positive": v["positive"], "negative": v["negative"],
                          "avg_sentiment": round(v["score_sum"] / v["mentions"], 3)} for k, v in topic_agg.items()],
                        key=lambda x: -x["mentions"])
    labels = Counter(s["label"] for s in sents)
    last90, prev90 = window(90), window(90, 90)
    spanned = [d for d in (_aware(r.get("reviewed_at")) for r in reviews) if d]
    months_span = max(1.0, (now - min(spanned)).days / 30.44) if spanned else None
    total_count = max(n, int((totals or {}).get("review_count") or 0))
    avg = round(sum(rated) / len(rated), 2) if rated else ((totals or {}).get("rating"))
    trend = None
    if last90["avg_rating"] is not None and prev90["avg_rating"] is not None:
        trend = round(last90["avg_rating"] - prev90["avg_rating"], 2)
    unanswered_neg = [r for r in reviews if r.get("rating") and int(r["rating"]) <= 3 and not (r.get("reply") or "").strip()]
    return {
        "count": n, "total_count": total_count, "avg_rating": float(avg) if avg is not None else None, "distribution": dist,
        "response_rate": round(100 * len(replied) / n, 1) if n else None,
        "avg_response_hours": round(sum(rt) / len(rt), 1) if rt else None,
        "median_response_hours": round(sorted(rt)[len(rt) // 2], 1) if rt else None,
        "per_month": by_month, "velocity_per_month": round(n / months_span, 2) if months_span else None,
        "last_90": last90, "prev_90": prev90, "rating_trend_90d": trend,
        "sentiment": {"positive": labels.get("positive", 0), "neutral": labels.get("neutral", 0), "negative": labels.get("negative", 0),
                      "avg": round(sum(s["score"] for s in sents) / n, 3) if n else None},
        "topics": topic_rows, "unanswered_negative": len(unanswered_neg),
        "last_review_at": max(spanned).isoformat() if spanned else None,
    }


def review_recommendations(st: dict) -> list[str]:
    out = []
    if st["count"] == 0 and not st["total_count"]:
        return ["No reviews recorded yet — import your reviews (CSV) or connect Google Business Profile, and start asking happy customers for reviews."]
    if st["response_rate"] is not None and st["response_rate"] < 90:
        out.append(f"Reply to more reviews: response rate is {st['response_rate']}% (aim for 90%+).")
    if st["unanswered_negative"]:
        out.append(f"{st['unanswered_negative']} review(s) rated 3★ or lower have no reply — respond to these first.")
    if st["avg_response_hours"] and st["avg_response_hours"] > 48:
        out.append(f"Average reply time is {st['avg_response_hours']} h — aim to reply within 24–48 hours.")
    if st["last_90"]["count"] < 3:
        out.append("Fewer than 3 new reviews in the last 90 days — set up a regular review-request process.")
    if st["rating_trend_90d"] is not None and st["rating_trend_90d"] < -0.2:
        out.append(f"Average rating fell by {abs(st['rating_trend_90d'])} over the last 90 days — investigate recent complaints.")
    neg = [t for t in st["topics"] if t["negative"] >= 2 and t["negative"] >= t["positive"]]
    for t in neg[:3]:
        out.append(f"Recurring complaints about {t['topic']} ({t['negative']} negative mentions).")
    return out


# =============================================================== posts audit
GBP_POST_MAX = 1500


def post_audit(posts: list[dict], now: datetime | None = None) -> dict:
    """posts: [{post_type, title, body, cta_type, image_ref, posted_at, status}] — only published posts count for cadence."""
    now = now or utcnow()
    pub = sorted([p for p in posts if p.get("status") == "published" and _aware(p.get("posted_at"))],
                 key=lambda p: _aware(p["posted_at"]))
    dates = [_aware(p["posted_at"]) for p in pub]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    since_last = (now - dates[-1]).days if dates else None
    in90 = [d for d in dates if d > now - timedelta(days=90)]
    types = Counter((p.get("post_type") or "update") for p in pub)
    lens = [len((p.get("body") or "")) for p in pub]
    with_cta = sum(1 for p in pub if p.get("cta_type"))
    with_img = sum(1 for p in pub if p.get("image_ref"))
    weeks = defaultdict(int)
    for d in dates:
        y, w, _ = d.isocalendar()
        weeks[f"{y}-W{w:02d}"] += 1
    long_gaps = []
    for a, b in zip(dates, dates[1:]):
        if (b - a).days > 14:
            long_gaps.append({"from": a.date().isoformat(), "to": b.date().isoformat(), "days": (b - a).days})
    if dates and since_last and since_last > 14:
        long_gaps.append({"from": dates[-1].date().isoformat(), "to": now.date().isoformat(), "days": since_last, "ongoing": True})
    res = {
        "published": len(pub), "drafts": sum(1 for p in posts if p.get("status") in ("draft", "review", "approved")),
        "last_90_days": len(in90), "per_week_90d": round(len(in90) / (90 / 7), 2),
        "days_since_last": since_last, "avg_gap_days": round(sum(gaps) / len(gaps), 1) if gaps else None,
        "max_gap_days": max(gaps) if gaps else None, "gaps_over_14d": long_gaps[-10:],
        "types": {t: types.get(t, 0) for t in ("update", "offer", "event")},
        "cta_pct": round(100 * with_cta / len(pub), 1) if pub else None,
        "image_pct": round(100 * with_img / len(pub), 1) if pub else None,
        "avg_length": round(sum(lens) / len(lens)) if lens else None,
        "too_short": sum(1 for x in lens if x < 100), "too_long": sum(1 for x in lens if x > GBP_POST_MAX),
        "weekly": [{"week": k, "posts": v} for k, v in sorted(weeks.items())][-26:],
    }
    rec = []
    if not pub:
        rec.append("No published posts recorded — post at least weekly (updates, offers, events). Import past posts by CSV or connect GBP.")
    else:
        if since_last is not None and since_last > 7:
            rec.append(f"Last post was {since_last} days ago — publish at least one post per week.")
        if res["per_week_90d"] < 1:
            rec.append(f"Cadence is {res['per_week_90d']} posts/week over 90 days — aim for 1–2 per week.")
        if res["max_gap_days"] and res["max_gap_days"] > 30:
            rec.append(f"Longest gap between posts was {res['max_gap_days']} days — schedule posts ahead with GMB Automation.")
        if res["cta_pct"] is not None and res["cta_pct"] < 80:
            rec.append(f"Only {res['cta_pct']}% of posts have a call-to-action button — add Book / Call / Learn more.")
        if res["image_pct"] is not None and res["image_pct"] < 90:
            rec.append(f"Only {res['image_pct']}% of posts have an image — posts with photos get more engagement.")
        if not types.get("offer"):
            rec.append("No offer posts — try a seasonal offer with a coupon code and end date.")
        if not types.get("event"):
            rec.append("No event posts — promote workshops, open days or seasonal events.")
        if res["too_short"]:
            rec.append(f"{res['too_short']} post(s) under 100 characters — aim for 150–300 words-worth of useful detail.")
        if res["too_long"]:
            rec.append(f"{res['too_long']} post(s) exceed Google's 1,500 character limit.")
    res["recommendations"] = rec
    return res


# =============================================================== GBP profile audit
def _rule(key, label, weight, status, detail, fix, group):
    earned = weight if status == "pass" else weight / 2 if status == "partial" else 0
    return {"key": key, "label": label, "weight": weight, "status": status, "earned": earned, "detail": detail,
            "fix": fix if status != "pass" else None, "group": group}


def stuffing(name: str, city: str | None, categories: list[str]) -> bool:
    n = (name or "").lower()
    if re.search(r"\s[|/]\s|\s-\s.*\b(near me|best|cheap|24/7|24 hour)\b", n):
        return True
    if city and city.lower() in n and len(n.split()) > 4:
        return True
    cat_words = {w for c in categories for w in re.findall(r"[a-z]+", c.lower()) if len(w) > 3}
    return len(n.split()) > 7 and len(cat_words & set(n.split())) >= 2


def gbp_audit(p: dict, ctx: dict) -> dict:
    """p: profile (name, nap{address,city,postcode,phone,website}, primary_category, secondary_categories, hours,
    description, photos_count, services, products, attributes, qa_questions, qa_answered, has_logo, social_links,
    place_id, cid, verified). ctx: review_stats, post_audit, same_address (list), nap_sources (list of {source, name,
    address, phone}), website_run (latest website↔GBP result or None), now."""
    nap = p.get("nap") or {}
    rs, pa = ctx.get("review_stats") or {}, ctx.get("post_audit") or {}
    cats = [c for c in [p.get("primary_category")] + list(p.get("secondary_categories") or []) if c]
    R = []
    name = p.get("name") or ""
    R.append(_rule("name", "Business name", 4, "pass" if name.strip() else "fail", name or "missing", "Add the exact real-world business name.", "Identity"))
    st = stuffing(name, nap.get("city"), cats) if name else False
    R.append(_rule("name_clean", "Name free of keyword stuffing", 3, "fail" if st else "pass",
                   "looks keyword-stuffed" if st else "ok", "Remove keywords/locations from the name — it violates Google guidelines and risks suspension.", "Identity"))
    R.append(_rule("primary_category", "Primary category", 8, "pass" if p.get("primary_category") else "fail",
                   p.get("primary_category") or "missing", "Set the most specific primary category (see Category Finder).", "Categories"))
    sec = len(p.get("secondary_categories") or [])
    R.append(_rule("secondary_categories", "Secondary categories (2+)", 4, "pass" if sec >= 2 else "partial" if sec == 1 else "fail",
                   f"{sec} set", "Add 2–9 relevant secondary categories for services you actually offer.", "Categories"))
    parts = [bool(nap.get("address")), bool(nap.get("city")), bool(nap.get("postcode"))]
    R.append(_rule("address", "Complete address", 6, "pass" if all(parts) else "partial" if any(parts) else "fail",
                   address_string(nap) or "missing", "Complete street, city and postcode (or set a service area).", "NAP"))
    ph = phone_key(nap.get("phone"))
    R.append(_rule("phone", "Phone number", 6, "pass" if len(ph) >= 9 else "partial" if ph else "fail", nap.get("phone") or "missing",
                   "Add a local phone number that is answered during business hours.", "NAP"))
    web = nap.get("website") or ""
    R.append(_rule("website", "Website", 6, "pass" if web.startswith("https://") else "partial" if web else "fail", web or "missing",
                   "Link the website (https) — ideally a location landing page.", "NAP"))
    hrs = parse_hours(p.get("hours") or {})
    R.append(_rule("hours", "Opening hours", 6, "pass" if len(hrs) >= 5 else "partial" if hrs else "fail", f"{len(hrs)} days set",
                   "Set regular hours for every day you open (and special hours for holidays).", "Details"))
    dl = len((p.get("description") or "").strip())
    R.append(_rule("description", "Description (250–750 chars)", 6,
                   "fail" if dl == 0 or dl > 750 else "pass" if dl >= 250 else "partial", f"{dl} characters",
                   "Write a 250–750 character description covering services, area served and what sets you apart." if dl <= 750
                   else "Shorten the description to Google's 750 character limit.", "Details"))
    ph_n = int(p.get("photos_count") or 0)
    R.append(_rule("photos", "Photos (10+)", 6, "pass" if ph_n >= 10 else "partial" if ph_n >= 3 else "fail", f"{ph_n} photos",
                   "Upload at least 10 recent photos (exterior, interior, team, work) plus logo and cover.", "Media"))
    sp = len(p.get("services") or []) + len(p.get("products") or [])
    R.append(_rule("services", "Services / products (3+)", 5, "pass" if sp >= 3 else "partial" if sp else "fail", f"{sp} listed",
                   "List your services/products with descriptions (and prices where possible).", "Details"))
    at = len(p.get("attributes") or [])
    R.append(_rule("attributes", "Attributes (3+)", 3, "pass" if at >= 3 else "partial" if at else "fail", f"{at} set",
                   "Set applicable attributes (accessibility, payments, amenities, identity).", "Details"))
    q, qa = int(p.get("qa_questions") or 0), int(p.get("qa_answered") or 0)
    R.append(_rule("qa", "Q&A seeded and answered", 3, "pass" if q >= 1 and qa >= q else "partial" if q else "fail",
                   f"{qa}/{q} answered", "Seed 3–5 common questions with helpful owner answers; answer every public question.", "Engagement"))
    dsl = pa.get("days_since_last")
    R.append(_rule("posts", "Posted in the last 7 days", 6, "pass" if dsl is not None and dsl <= 7 else "partial" if dsl is not None and dsl <= 30 else "fail",
                   "never" if dsl is None else f"{dsl} days ago", "Publish a GBP post every week (use GMB Posts / Automation).", "Engagement"))
    rc = int(rs.get("total_count") or 0)
    R.append(_rule("reviews_count", "Review count (25+)", 6, "pass" if rc >= 25 else "partial" if rc >= 10 else "fail", f"{rc} reviews",
                   "Ask every satisfied customer for a review (share the review link after each job).", "Reviews"))
    ar = rs.get("avg_rating")
    R.append(_rule("rating", "Average rating (4.5+)", 6, "pass" if ar is not None and ar >= 4.5 else "partial" if ar is not None and ar >= 4.0 else "fail",
                   "no rating" if ar is None else f"{ar}★", "Fix recurring complaints (see Review Audit) and request more reviews from happy customers.", "Reviews"))
    rr = rs.get("response_rate")
    R.append(_rule("response_rate", "Review response rate (90%+)", 6, "pass" if rr is not None and rr >= 90 else "partial" if rr is not None and rr >= 50 else "fail",
                   "no reviews" if rr is None else f"{rr}%", "Reply to every review — thank positives, resolve negatives.", "Reviews"))
    # NAP consistency across sources (website check + citations)
    srcs = list(ctx.get("nap_sources") or [])
    checks = []
    for s in srcs:
        for fld, fn, a, b in (("name", compare_name, name, s.get("name")), ("address", compare_address, address_string(nap), s.get("address")),
                              ("phone", compare_phone, nap.get("phone"), s.get("phone"))):
            if b:
                checks.append({"source": s.get("source"), "field": fld, "gbp": a, "value": b, "result": fn(a, b)})
    ok = sum(1 for c in checks if c["result"] == "match") + 0.5 * sum(1 for c in checks if c["result"] == "partial")
    cons = round(100 * ok / len(checks)) if checks else None
    R.append(_rule("nap_consistency", "NAP consistency across sources", 5,
                   "partial" if cons is None else "pass" if cons >= 90 else "partial" if cons >= 60 else "fail",
                   "no sources to compare — run Website ↔ GBP audit or add citations" if cons is None else f"{cons}% consistent",
                   "Make name, address and phone identical on the website and every directory listing.", "NAP"))
    same = ctx.get("same_address") or []
    R.append(_rule("same_address", "No conflicting listings at the same address", 5, "fail" if same else "pass",
                   f"{len(same)} other listing(s) at this address" if same else "none found",
                   "Check other listings at your address: merge duplicates, and differentiate suites/entrances for co-located businesses.", "NAP"))
    total = sum(r["weight"] for r in R)
    score = round(100 * sum(r["earned"] for r in R) / total, 1)
    fixes = sorted([r for r in R if r["status"] != "pass"], key=lambda r: (-(r["weight"] - r["earned"]), r["key"]))
    fixes = [{"key": r["key"], "label": r["label"], "fix": r["fix"], "points": r["weight"] - r["earned"],
              "priority": "high" if r["weight"] - r["earned"] >= 5 else "medium" if r["weight"] - r["earned"] >= 3 else "low"} for r in fixes]
    wr = ctx.get("website_run") or {}
    kp = [
        {"item": "Verified Google Business Profile (place ID / CID known)", "ok": bool(p.get("verified") or p.get("place_id") or p.get("cid"))},
        {"item": "Primary category set", "ok": bool(p.get("primary_category"))},
        {"item": "Description present", "ok": dl >= 100},
        {"item": "Logo and 10+ photos", "ok": bool(p.get("has_logo")) and ph_n >= 10},
        {"item": "10+ reviews with 4.0★ or better", "ok": rc >= 10 and (ar or 0) >= 4.0},
        {"item": "Website has LocalBusiness / Organization schema", "ok": bool(wr.get("schema_present"))},
        {"item": "Schema sameAs links to social profiles", "ok": bool(wr.get("same_as"))},
        {"item": "Social profiles recorded", "ok": bool(p.get("social_links"))},
        {"item": "NAP consistent with website", "ok": bool(wr) and (wr.get("score") or 0) >= 80},
        {"item": "No duplicate / conflicting listings at the address", "ok": not same},
    ]
    return {"score": score, "rules": R, "fixes": fixes, "nap": {"checks": checks, "consistency": cons},
            "same_address": same, "knowledge_panel": {"items": kp, "ready": sum(1 for k in kp if k["ok"]), "total": len(kp)},
            "groups": _group_scores(R)}


def _group_scores(R):
    g = defaultdict(lambda: [0.0, 0.0])
    for r in R:
        g[r["group"]][0] += r["earned"]
        g[r["group"]][1] += r["weight"]
    return {k: round(100 * a / b) for k, (a, b) in g.items()}


# =============================================================== categories
@lru_cache(maxsize=1)
def catalogue() -> list[dict]:
    return json.loads((Path(__file__).parent / "local_gbp_categories.json").read_text())


def _stem(w: str) -> str:
    for suf in ("ing", "ers", "er", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


STOPW = {"and", "the", "of", "for", "a", "an", "service", "services", "shop", "store", "company", "center", "centre", "agency",
         "contractor", "supplier", "in", "near", "me", "my", "best", "local", "to", "with", "on", "at", "&"}


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOPW and len(w) > 1}


def cat_lookup(name: str) -> dict | None:
    k = (name or "").strip().lower()
    for c in catalogue():
        if c["name"].lower() == k:
            return c
    return None


def match_categories(texts: list[str], competitor_cats: list[list[str]] | None = None, current: list[str] | None = None,
                     limit: int = 12) -> dict:
    """Rank catalogue categories against the business's services/keywords (phrase + token overlap) and how many
    competitors use each category. Returns primary + secondary recommendations with reasons."""
    phrases = [t.strip().lower() for t in texts if t and t.strip()]
    bag = set()
    for t in phrases:
        bag |= _stems(t)
    comp = competitor_cats or []
    n_comp = len(comp)
    freq = Counter()
    for cats in comp:
        for c in {x.strip() for x in cats if x}:
            freq[c.lower()] += 1
    current_l = {c.lower() for c in (current or [])}
    scored = []
    for c in catalogue():
        name_st = _stems(c["name"])
        kw_hits, matched = 0.0, []
        for k in c["keywords"]:
            kl = k.lower()
            if any(kl == p or f" {kl} " in f" {p} " for p in phrases):
                kw_hits += 2
                matched.append(k)
            elif _stems(kl) and _stems(kl) <= bag:
                kw_hits += 1
                matched.append(k)
        name_overlap = len(name_st & bag) / len(name_st) if name_st else 0
        exact = any(c["name"].lower() == p for p in phrases)
        rel = (6 if exact else 0) + 4 * name_overlap + min(kw_hits, 8)
        used = freq.get(c["name"].lower(), 0)
        comp_score = 4 * used / n_comp if n_comp else 0
        total = rel + comp_score
        if rel <= 0 and not used:
            continue
        scored.append({"name": c["name"], "group": c["group"], "relevance": round(rel, 2), "used_by": used, "competitors": n_comp,
                       "score": round(total, 2), "matched_terms": matched[:6], "current": c["name"].lower() in current_l,
                       "used_by_label": f"used by {used} of {n_comp} top competitors" if n_comp else None})
    # competitor categories not in our catalogue still count
    known = {c["name"].lower() for c in catalogue()}
    for cname, used in freq.items():
        if cname not in known:
            scored.append({"name": next(x for cats in comp for x in cats if x and x.lower() == cname), "group": "Other",
                           "relevance": 0, "used_by": used, "competitors": n_comp, "score": round(4 * used / n_comp, 2),
                           "matched_terms": [], "current": cname in current_l,
                           "used_by_label": f"used by {used} of {n_comp} top competitors"})
    scored.sort(key=lambda x: (-x["score"], -x["used_by"], x["name"]))
    relevant = [s for s in scored if s["relevance"] > 0]
    primary = None
    if relevant:
        # the primary is the most specific relevant category: best relevance, competitor usage as tie-breaker
        primary = sorted(relevant, key=lambda x: (-(x["relevance"] + 2 * x["used_by"] / max(1, n_comp)), x["name"]))[0]
    secondary = [s for s in scored if not primary or s["name"] != primary["name"]][: max(0, limit - 1)]
    missing_common = [s for s in scored if s["used_by"] and n_comp and s["used_by"] / n_comp >= 0.5 and not s["current"]]
    return {"primary": primary, "secondary": secondary, "all": scored[:60], "competitor_count": n_comp,
            "competitor_frequency": [{"category": k, "used_by": v} for k, v in freq.most_common(30)],
            "missing_common": missing_common[:10], "inputs": phrases[:50]}


# =============================================================== local score / competitors
def local_score(l: dict, keyword: str | None = None, center: tuple | None = None, now: datetime | None = None) -> dict:
    """0-100 prominence/relevance/proximity proxy from profile metrics only."""
    now = now or utcnow()
    comp = {}
    rating, rc = float(l.get("rating") or 0), int(l.get("review_count") or 0)
    conf = min(1.0, math.log10(rc + 1) / 2)  # 100 reviews → full confidence
    comp["rating"] = round(25 * (max(0.0, rating - 3) / 2) * conf if rating else 0, 2)
    comp["reviews"] = round(20 * min(1.0, math.log10(rc + 1) / math.log10(201)), 2)
    cats = [c for c in [l.get("primary_category")] + list(l.get("secondary_categories") or []) if c]
    if keyword:
        kw = _stems(keyword)
        prim = _stems(l.get("primary_category") or "")
        if prim and kw & prim:
            comp["category"] = 15.0
        elif any(_stems(c) & kw for c in cats):
            comp["category"] = 9.0
        elif kw & _stems(l.get("name") or ""):
            comp["category"] = 5.0
        else:
            comp["category"] = 0.0
    else:
        comp["category"] = 15.0 if l.get("primary_category") else 0.0
    ph = int(l.get("photos_count") or 0)
    comp["photos"] = round(10 * min(1.0, math.log10(ph + 1) / math.log10(51)), 2)
    comp["website"] = 10.0 if (l.get("website") or "") else 0.0
    dist = None
    if center and l.get("lat") is not None and l.get("lng") is not None:
        dist = haversine_km(center[0], center[1], l["lat"], l["lng"])
        comp["proximity"] = round(10 * max(0.0, 1 - dist / 15), 2)
    else:
        comp["proximity"] = 5.0
    ppm = l.get("posts_per_month")
    last = _aware(l.get("last_post_at"))
    if ppm is not None:
        comp["posts"] = round(10 * min(1.0, float(ppm) / 4), 2)
    elif last:
        comp["posts"] = 10.0 if (now - last).days <= 7 else 5.0 if (now - last).days <= 30 else 0.0
    else:
        comp["posts"] = 0.0
    return {"score": round(sum(comp.values()), 1), "components": comp, "distance_km": round(dist, 2) if dist is not None else None}


def competitor_gaps(own: dict, comps: list[dict]) -> dict:
    if not comps:
        return {"categories_missing": [], "metrics": [], "summary": []}
    own_cats = {c.lower() for c in [own.get("primary_category")] + list(own.get("secondary_categories") or []) if c}
    freq = Counter()
    for c in comps:
        for x in {y for y in [c.get("primary_category")] + list(c.get("secondary_categories") or []) if y}:
            freq[x] += 1
    missing = [{"category": k, "used_by": v, "of": len(comps)} for k, v in freq.most_common() if k.lower() not in own_cats]

    def med(vals):
        v = sorted(x for x in vals if x is not None)
        return v[len(v) // 2] if v else None
    metrics = []
    for key, label, better in (("rating", "Rating", "higher"), ("review_count", "Review count", "higher"),
                               ("photos_count", "Photos", "higher"), ("posts_per_month", "Posts per month", "higher")):
        cm = med([float(c[key]) if c.get(key) is not None else None for c in comps])
        top = max([float(c[key]) for c in comps if c.get(key) is not None], default=None)
        mine = float(own[key]) if own.get(key) is not None else None
        gap = round(cm - mine, 2) if cm is not None and mine is not None else None
        metrics.append({"metric": key, "label": label, "own": mine, "competitor_median": cm, "competitor_best": top,
                        "gap_to_median": gap, "behind": gap is not None and gap > 0})
    summary = []
    for mt in metrics:
        if mt["behind"]:
            summary.append(f"{mt['label']}: you have {mt['own']:g}, competitor median is {mt['competitor_median']:g}.")
        elif mt["own"] is None and mt["competitor_median"] is not None:
            summary.append(f"{mt['label']}: not recorded for your listing (competitor median {mt['competitor_median']:g}).")
    for mc in missing[:3]:
        summary.append(f"Category '{mc['category']}' is used by {mc['used_by']} of {mc['of']} competitors but not by you.")
    return {"categories_missing": missing, "metrics": metrics, "summary": summary}


# =============================================================== website ↔ GBP
LOCAL_TYPES = {
    "localbusiness", "organization", "professionalservice", "homeandconstructionbusiness", "plumber", "electrician",
    "roofingcontractor", "generalcontractor", "hvacbusiness", "locksmith", "housepainter", "movingcompany", "dentist",
    "medicalbusiness", "medicalclinic", "physician", "restaurant", "foodestablishment", "cafeorcoffeeshop", "bakery",
    "barorpub", "store", "autorepair", "automotivebusiness", "legalservice", "attorney", "accountingservice",
    "financialservice", "realestateagent", "beautysalon", "hairsalon", "nailsalon", "dayspa", "healthandbeautybusiness",
    "lodgingbusiness", "hotel", "veterinarycare", "childcare", "sportsactivitylocation", "exerciseGym".lower(),
    "insuranceagency", "travelagency", "emergencyservice", "entertainmentbusiness", "selfstorage", "drycleaningorlaundry",
    "homegoodsstore", "clothingstore", "electronicsstore", "furniturestore", "hardwarestore", "jewelrystore",
}


def _ld_items(soup) -> list[dict]:
    out = []
    for s in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            it = stack.pop(0)
            if isinstance(it, dict):
                if "@graph" in it and isinstance(it["@graph"], list):
                    stack.extend(it["@graph"])
                out.append(it)
            elif isinstance(it, list):
                stack.extend(it)
    return out


def _types(it) -> list[str]:
    t = it.get("@type")
    return [str(x) for x in (t if isinstance(t, list) else [t]) if x]


def _schema_address(a) -> str:
    if isinstance(a, list):
        a = a[0] if a else {}
    if isinstance(a, str):
        return a
    if isinstance(a, dict):
        return ", ".join(str(a.get(k)) for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode") if a.get(k))
    return ""


def extract_site_local(html: str, url: str) -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    items = _ld_items(soup)
    lb = None
    for it in items:
        if any(t.lower() in LOCAL_TYPES for t in _types(it)) and (it.get("address") or it.get("telephone") or it.get("name")):
            if lb is None or (it.get("address") and not lb.get("address")):
                lb = it
    for t in soup(["script", "style", "noscript"]):
        if t.name != "script" or "ld+json" not in (t.get("type") or ""):
            t.decompose()
    text = re.sub(r"\s+", " ", (soup.body or soup).get_text(" ", strip=True))
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heads = [h.get_text(" ", strip=True) for h in soup.find_all(re.compile("^h[1-3]$"))]
    tels = sorted({re.sub(r"[^\d+]", "", a["href"][4:]) for a in soup.find_all("a", href=True) if a["href"].lower().startswith("tel:")})
    maps_embed = any(re.search(r"google\.[a-z.]+/maps|maps\.google\.|/maps/embed", f.get("src") or "") for f in soup.find_all("iframe"))
    maps_link = any(re.search(r"google\.[a-z.]+/maps|maps\.google\.|goo\.gl/maps|maps\.app\.goo\.gl|g\.page/", a.get("href") or "")
                    for a in soup.find_all("a", href=True))
    addr_el = soup.find("address")
    schema = None
    if lb:
        oh = lb.get("openingHoursSpecification") or lb.get("openingHours")
        schema = {"types": _types(lb), "name": lb.get("name"), "telephone": lb.get("telephone"), "address": _schema_address(lb.get("address")),
                  "url": lb.get("url"), "hours": parse_hours(oh if isinstance(oh, (list, dict)) else [oh] if oh else []),
                  "geo": lb.get("geo"), "same_as": lb.get("sameAs") if isinstance(lb.get("sameAs"), list) else [lb["sameAs"]] if lb.get("sameAs") else [],
                  "has_map": bool(lb.get("hasMap")), "price_range": lb.get("priceRange")}
    return {"url": url, "title": title, "headings": heads[:40], "text": text[:20000], "tel_links": tels,
            "address_block": addr_el.get_text(" ", strip=True) if addr_el else None, "maps_embed": maps_embed, "maps_link": maps_link,
            "schema": schema, "schema_types": sorted({t for it in items for t in _types(it)}), "hours_text": parse_hours(text)}


def _text_has_phone(text: str, phone: str) -> bool:
    k = phone_key(phone)
    if len(k) < 9:
        return False
    digits = re.sub(r"\D", "", text)
    return k in digits


def _text_has_address(text: str, nap: dict) -> str:
    street = norm_address(nap.get("address"))
    if not street:
        return "missing"
    t = norm_address(text)
    pc = norm_address(nap.get("postcode") or "")
    if f" {street} " in f" {t} ":
        return "match" if (not pc or pc in t) else "partial"
    toks = street.split()
    num = [x for x in toks if x.isdigit()]
    if num and all(f" {n} " in f" {t} " for n in num) and sum(1 for x in toks if f" {x} " in f" {t} ") >= len(toks) - 1:
        return "partial"
    return "mismatch"


FIELD_WEIGHTS = {"name": 15, "address": 20, "phone": 20, "hours": 10, "website": 5, "categories": 10, "schema": 10, "schema_fields": 5, "maps": 5}


def compare_website_gbp(site: dict, g: dict) -> dict:
    """site: extract_site_local output (+ final_url). g: {name, nap{...}, hours, primary_category, secondary_categories}."""
    nap = g.get("nap") or {}
    sch = site.get("schema") or {}
    text = site.get("text") or ""
    gaddr = address_string(nap)
    F = []

    def add(field, status, gbp, website, note=None):
        F.append({"field": field, "status": status, "gbp": gbp, "website": website, "note": note, "weight": FIELD_WEIGHTS[field]})

    # name: schema name, else title/h1 containment
    nm = compare_name(g.get("name"), sch.get("name")) if sch.get("name") else None
    if nm is None:
        in_text = norm_name(g.get("name")) and norm_name(g.get("name")) in norm_name(site.get("title") + " " + " ".join(site.get("headings") or []) + " " + text[:3000])
        nm = "match" if in_text else ("missing" if not g.get("name") else "mismatch")
        add("name", nm, g.get("name"), site.get("title"), "compared with page title/headings (no schema name)")
    else:
        add("name", nm, g.get("name"), sch.get("name"), "schema.org name")
    # address
    if sch.get("address"):
        st = compare_address(gaddr, sch["address"])
        add("address", st, gaddr, sch["address"], "schema.org address")
    else:
        st = _text_has_address((site.get("address_block") or "") + " " + text, nap)
        add("address", st, gaddr, site.get("address_block") or ("found in page text" if st != "mismatch" else None), "visible page text")
    # phone
    site_phones = [sch.get("telephone")] if sch.get("telephone") else []
    site_phones += site.get("tel_links") or []
    if not nap.get("phone"):
        add("phone", "missing", None, ", ".join(p for p in site_phones if p) or None)
    elif any(compare_phone(nap.get("phone"), p) == "match" for p in site_phones if p):
        add("phone", "match", nap.get("phone"), next(p for p in site_phones if p and compare_phone(nap.get("phone"), p) == "match"))
    elif _text_has_phone(text, nap.get("phone")):
        add("phone", "partial" if site_phones else "match", nap.get("phone"), "found in page text",
            "phone in text but schema/tel: link differs" if site_phones else "visible page text")
    else:
        add("phone", "mismatch" if (site_phones or text) else "missing", nap.get("phone"), ", ".join(p for p in site_phones if p) or None)
    # hours
    site_hours = sch.get("hours") or site.get("hours_text") or {}
    hc = compare_hours(g.get("hours") or {}, site_hours)
    add("hours", hc["status"], parse_hours(g.get("hours") or {}), site_hours, f"{len(hc['differences'])} day(s) differ" if hc["differences"] else None)
    # website URL
    gw, fu = (nap.get("website") or ""), (site.get("final_url") or site.get("url") or "")
    if not gw:
        add("website", "missing", None, fu, "GBP has no website")
    else:
        h1, h2 = urlparse(gw if "://" in gw else "https://" + gw).netloc.lower().removeprefix("www."), urlparse(fu).netloc.lower().removeprefix("www.")
        add("website", "match" if h1 == h2 else "mismatch", gw, fu)
    # categories vs page topics
    cats = [c for c in [g.get("primary_category")] + list(g.get("secondary_categories") or []) if c]
    page_bag = _stems((site.get("title") or "") + " " + " ".join(site.get("headings") or []) + " " + text[:8000])
    cov = []
    for c in cats:
        cat = cat_lookup(c)
        terms = _stems(c) | ({_stem(w) for k in cat["keywords"][:6] for w in re.findall(r"[a-z]+", k.lower())} if cat else set())
        cov.append({"category": c, "on_page": bool(terms & page_bag)})
    if not cats:
        add("categories", "missing", [], None, "GBP has no categories")
    else:
        hit = sum(1 for c in cov if c["on_page"])
        add("categories", "match" if hit == len(cov) else "partial" if hit else "mismatch", cats, cov,
            f"{hit}/{len(cov)} categories reflected in page topics")
    # schema presence + field match
    add("schema", "match" if sch else "missing", "LocalBusiness", ", ".join(sch.get("types") or []) if sch else None,
        None if sch else "no LocalBusiness/Organization JSON-LD found")
    if sch:
        sub = [x for x in F if x["field"] in ("name", "address", "phone") and x.get("note", "") and "schema" in (x.get("note") or "")]
        fields_present = [k for k in ("name", "address", "telephone", "url", "hours") if sch.get(k)]
        st = "match" if len(fields_present) >= 5 and all(s["status"] == "match" for s in sub) else "partial" if fields_present else "mismatch"
        add("schema_fields", st, None, fields_present, "schema has name/address/telephone/url/hours and matches GBP" if st == "match" else
            f"schema fields present: {', '.join(fields_present) or 'none'}")
    else:
        add("schema_fields", "missing", None, None)
    add("maps", "match" if site.get("maps_embed") else "partial" if site.get("maps_link") or sch.get("has_map") else "missing", None,
        "embedded Google Map" if site.get("maps_embed") else "link to Google Maps" if site.get("maps_link") else None)
    pts = {"match": 1.0, "partial": 0.5}
    total = sum(f["weight"] for f in F)
    score = round(100 * sum(f["weight"] * pts.get(f["status"], 0) for f in F) / total, 1)
    fixes = []
    for f in F:
        if f["status"] in ("match",):
            continue
        fixes.append({"field": f["field"], "status": f["status"], "fix": {
            "name": "Use the exact GBP business name on the site (title/H1 and schema name).",
            "address": "Show the same address as GBP on the site (footer/contact) and in schema.org address.",
            "phone": "Use the GBP phone number on the site as a tel: link and in schema telephone.",
            "hours": "Make opening hours on the site (and openingHoursSpecification) match GBP.",
            "website": "Point the GBP website field at this site's domain.",
            "categories": "Add page content (headings/sections) for each GBP category you use.",
            "schema": "Add LocalBusiness (or a specific subtype) JSON-LD with name, address, telephone, url, openingHours.",
            "schema_fields": "Complete the LocalBusiness schema fields so they match GBP exactly.",
            "maps": "Embed a Google Map of the location on the contact/location page.",
        }[f["field"]]})
    return {"score": score, "fields": F, "fixes": fixes, "schema_present": bool(sch), "schema": sch or None,
            "same_as": (sch or {}).get("same_as") or [], "categories_coverage": cov,
            "site_nap": {"name": sch.get("name") or site.get("title"), "address": sch.get("address") or site.get("address_block"),
                         "phone": (sch.get("telephone") or (site.get("tel_links") or [None])[0])}}
