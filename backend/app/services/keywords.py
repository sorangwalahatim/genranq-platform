"""Keyword research from the project's own data: intent classification, clustering,
long-tail, cannibalisation, and blog title ideas from real search queries.

Deterministic (no LLM needed). When an AI provider is connected, titles/briefs can be
refined by an agent, but every output here is already usable on its own.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select

from .. import models as m
from .. import models_monitor as mm
from .ingest import path_of

STOP = set("""a an the and or of for to in on at by with from is are was be how what why when where which who whom does do can
should vs versus your you my our best top 2024 2025 2026 near me i it its this that these those about into than""".split())

INTENT_RULES = [
    ("transactional", r"\b(buy|price|prices|pricing|cost|costs|cheap|deal|discount|coupon|order|hire|book|booking|quote|subscription|for sale|shop)\b"),
    ("local", r"\b(near me|nearby|in [a-z]+$|open now|directions|closest)\b"),
    ("commercial", r"\b(best|top|review|reviews|vs|versus|compare|comparison|alternative|alternatives|agency|company|companies|services?|software|tool|tools|provider)\b"),
    ("informational", r"^(how|what|why|when|where|who|which|can|does|do|is|are|should)\b|\b(guide|tutorial|tips|ideas|examples?|meaning|definition|learn|steps?|checklist|template)\b"),
]


def intent(q: str, brand: str | None = None) -> str:
    s = q.lower().strip()
    if brand and brand.lower() in s:
        return "navigational"
    for name, rx in INTENT_RULES:
        if re.search(rx, s):
            return name
    return "informational" if len(s.split()) >= 4 else "commercial"


def tokens(q: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", q.lower()) if t not in STOP and len(t) > 1]


def stem(t: str) -> str:
    for suf in ("ing", "ers", "er", "es", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def cluster(keywords: list[dict], min_size: int = 2) -> list[dict]:
    """Group keywords by their most frequent shared head term pair (stemmed). Greedy, stable, explainable."""
    stems = {k["keyword"]: [stem(t) for t in tokens(k["keyword"])] for k in keywords}
    pair_count = Counter()
    for st in stems.values():
        s = sorted(set(st))
        for i in range(len(s)):
            for j in range(i + 1, len(s)):
                pair_count[(s[i], s[j])] += 1
    single = Counter(t for st in stems.values() for t in set(st))
    assigned, clusters = set(), []
    for (a, b), n in pair_count.most_common():
        if n < min_size:
            break
        members = [k for k in keywords if k["keyword"] not in assigned and a in stems[k["keyword"]] and b in stems[k["keyword"]]]
        if len(members) >= min_size:
            clusters.append(_mk_cluster(members, f"{a} {b}"))
            assigned |= {k["keyword"] for k in members}
    for t, n in single.most_common():
        if n < min_size:
            break
        members = [k for k in keywords if k["keyword"] not in assigned and t in stems[k["keyword"]]]
        if len(members) >= min_size:
            clusters.append(_mk_cluster(members, t))
            assigned |= {k["keyword"] for k in members}
    rest = [k for k in keywords if k["keyword"] not in assigned]
    if rest:
        clusters.append({**_mk_cluster(rest, "unclustered"), "unclustered": True})
    return clusters


def _mk_cluster(members, head):
    members = sorted(members, key=lambda k: -(k.get("impressions") or k.get("volume") or 0))
    intents = Counter(k["intent"] for k in members)
    return {"head": head, "pillar": members[0]["keyword"], "size": len(members), "intent": intents.most_common(1)[0][0],
            "impressions": sum(k.get("impressions") or 0 for k in members), "volume": sum(k.get("volume") or 0 for k in members),
            "keywords": members[:50]}


def project_keywords(db, project, days: int = 28) -> list[dict]:
    """Union of GSC queries, tracked keywords and imported own/competitor keywords for the project."""
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    out: dict[str, dict] = {}
    brand = project.client_name or project.name
    if anchor:
        rows = db.execute(select(mm.SearchMetric.query, func.sum(mm.SearchMetric.clicks), func.sum(mm.SearchMetric.impressions),
                                 func.sum(mm.SearchMetric.position * mm.SearchMetric.impressions))
                          .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.grain == "detail",
                                 mm.SearchMetric.date > anchor - timedelta(days=days)).group_by(mm.SearchMetric.query)).all()
        for q, c, i, wp in rows:
            if q:
                out[q] = {"keyword": q, "clicks": int(c or 0), "impressions": int(i or 0),
                          "position": round(float(wp) / i, 1) if wp and i else None, "source": "gsc"}
    for k in db.scalars(select(m.Keyword).where(m.Keyword.project_id == project.id)):
        out.setdefault(k.phrase, {"keyword": k.phrase, "source": "tracked", "volume": k.search_volume})
    for ck in db.scalars(select(mm.CompetitorKeyword).where(mm.CompetitorKeyword.project_id == project.id, mm.CompetitorKeyword.is_own)):
        e = out.setdefault(ck.keyword, {"keyword": ck.keyword, "source": ck.source})
        e["volume"] = e.get("volume") or ck.volume
        if e.get("position") is None:
            e["position"] = ck.position
    for e in out.values():
        e["intent"] = intent(e["keyword"], brand)
        e["words"] = len(e["keyword"].split())
        e["long_tail"] = e["words"] >= 4
    return list(out.values())


def research(db, project) -> dict:
    kws = project_keywords(db, project)
    by_intent = Counter(k["intent"] for k in kws)
    clusters = cluster([k for k in kws if k["intent"] != "navigational"])
    return {"total": len(kws), "by_intent": dict(by_intent), "long_tail": sum(1 for k in kws if k["long_tail"]),
            "clusters": clusters[:60], "keywords": sorted(kws, key=lambda k: -(k.get("impressions") or k.get("volume") or 0))[:500]}


def cannibalisation(db, project, days: int = 28, min_share: float = 0.2) -> list[dict]:
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    if not anchor:
        return []
    rows = db.execute(select(mm.SearchMetric.query, mm.SearchMetric.page, func.sum(mm.SearchMetric.impressions), func.sum(mm.SearchMetric.clicks))
                      .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.grain == "detail",
                             mm.SearchMetric.date > anchor - timedelta(days=days)).group_by(mm.SearchMetric.query, mm.SearchMetric.page)).all()
    byq = defaultdict(list)
    for q, pg, i, c in rows:
        if q and pg:
            byq[q].append({"page": pg, "impressions": int(i or 0), "clicks": int(c or 0)})
    out = []
    for q, pages in byq.items():
        tot = sum(p["impressions"] for p in pages)
        comp = [p for p in pages if tot and p["impressions"] / tot >= min_share]
        if len(comp) >= 2 and tot >= 100:
            out.append({"query": q, "impressions": tot, "pages": sorted(comp, key=lambda p: -p["impressions"])})
    return sorted(out, key=lambda x: -x["impressions"])


# ------------------------------------------------------------------ blog title ideas from real queries
def _title_case(s):
    small = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "vs", "with"}
    w = s.split()
    return " ".join(x if (i and x in small) else x[:1].upper() + x[1:] for i, x in enumerate(w))


def title_for(q: str, it: str, year: int) -> str:
    qs = q.strip().rstrip("?")
    if re.match(r"^(how|what|why|when|where|who|which|can|does|do|is|are|should)\b", qs):
        return _title_case(qs) + "?"
    if it == "commercial" and re.search(r"\bbest|top\b", qs):
        return f"{_title_case(qs)} ({year}): Compared and Reviewed"
    if re.search(r"\bvs\b|\bversus\b", qs):
        return f"{_title_case(qs)}: Which Should You Choose?"
    if it == "transactional":
        return f"{_title_case(qs)}: Prices, Options and What to Expect"
    if it == "local":
        return f"{_title_case(qs)}: A Local Guide"
    return f"{_title_case(qs)}: The Complete Guide"


def own_pages(db, project) -> dict[str, dict]:
    """Latest crawl of each of the project's sites: url → {title, h1}."""
    from .monitor import _own_crawls, _pages
    out = {}
    for site in db.scalars(select(m.Site).where(m.Site.project_id == project.id)):
        crawls = _own_crawls(db, site)
        if crawls:
            for u, p in _pages(db, crawls[0].id).items():
                if p.get("status") == 200:
                    out[u] = {"title": p.get("title") or "", "h1": " ".join(p.get("h1") or []), "word_count": p.get("word_count")}
    return out


def covered(q: str, pages: dict, ranking_pages: dict) -> str | None:
    """A page 'covers' a query if it already ranks top-20 for it or its title/H1/URL contains all its main tokens."""
    if ranking_pages.get(q):
        return ranking_pages[q]
    tk = set(stem(t) for t in tokens(q))
    if not tk:
        return None
    for u, p in pages.items():
        hay = set(stem(t) for t in tokens(p["title"] + " " + p["h1"] + " " + path_of(u).replace("-", " ").replace("/", " ")))
        if tk <= hay:
            return u
    return None


def blog_ideas(db, project, limit: int = 30) -> list[dict]:
    """Informational/commercial query clusters with demand but no page that targets them → blog ideas with titles."""
    from datetime import date
    kws = project_keywords(db, project)
    pages = own_pages(db, project)
    ranking = {k["keyword"]: None for k in kws}
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    if anchor:
        for q, pg, wp, i in db.execute(select(mm.SearchMetric.query, mm.SearchMetric.page, func.sum(mm.SearchMetric.position * mm.SearchMetric.impressions),
                                              func.sum(mm.SearchMetric.impressions))
                                       .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.grain == "detail",
                                              mm.SearchMetric.date > anchor - timedelta(days=28)).group_by(mm.SearchMetric.query, mm.SearchMetric.page)):
            if i and wp and wp / i <= 20:
                ranking[q] = pg
    gaps = [k for k in db.scalars(select(mm.CompetitorKeyword).where(mm.CompetitorKeyword.project_id == project.id,
                                                                     mm.CompetitorKeyword.is_own.is_(False),
                                                                     mm.CompetitorKeyword.position <= 20))]
    pool = {k["keyword"]: k for k in kws if k["intent"] in ("informational", "commercial")}
    for g in gaps:
        if g.keyword not in pool:
            pool[g.keyword] = {"keyword": g.keyword, "volume": g.volume, "source": f"gap:{g.domain}", "intent": intent(g.keyword)}
    candidates = [k for k in pool.values() if not covered(k["keyword"], pages, {q: p for q, p in ranking.items() if p})]
    ideas = []
    for c in cluster(candidates, min_size=1):
        if c.get("unclustered") and c["size"] > 1:
            groups = [_mk_cluster([k], k["keyword"]) for k in c["keywords"]]
        else:
            groups = [c]
        for g in groups:
            demand = g["impressions"] or g["volume"]
            ideas.append({"title": title_for(g["pillar"], g["intent"], date.today().year), "primary_keyword": g["pillar"],
                          "supporting_keywords": [k["keyword"] for k in g["keywords"][1:12]], "intent": g["intent"], "demand": demand,
                          "demand_type": "impressions" if g["impressions"] else "volume",
                          "format": "comparison" if re.search(r"\bvs\b|versus|compare|best|top", g["pillar"]) else
                          "how-to guide" if g["pillar"].startswith("how") else "FAQ / explainer" if g["pillar"].split()[0] in
                          ("what", "why", "when", "is", "are", "can", "does") else "guide",
                          "sources": sorted({k.get("source", "") for k in g["keywords"]})})
    ideas.sort(key=lambda x: -(x["demand"] or 0))
    return ideas[:limit]
