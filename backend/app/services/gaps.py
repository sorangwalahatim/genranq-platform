"""Gap analysis: keyword gap, content gap, competitor gap, structure gap, and the
underperforming-page diagnosis that chains them (blog performance automation)."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import func, select

from .. import models as m
from .. import models_monitor as mm
from .crawler import Crawler, CrawlBlocked, normalise
from .ingest import path_of
from .keywords import cluster, covered, intent, own_pages, stem, title_for, tokens


def _own_ranking(db, project) -> dict[str, tuple]:
    """keyword → (best position, url) from GSC (28d), own keyword imports and the rank tracker."""
    own: dict[str, tuple] = {}
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    if anchor:
        for q, pg, wp, i in db.execute(select(mm.SearchMetric.query, mm.SearchMetric.page, func.sum(mm.SearchMetric.position * mm.SearchMetric.impressions),
                                              func.sum(mm.SearchMetric.impressions))
                                       .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.grain == "detail",
                                              mm.SearchMetric.date > anchor - timedelta(days=28)).group_by(mm.SearchMetric.query, mm.SearchMetric.page)):
            if q and i:
                pos = round(float(wp) / i, 1)
                if q not in own or pos < own[q][0]:
                    own[q] = (pos, pg)
    for ck in db.scalars(select(mm.CompetitorKeyword).where(mm.CompetitorKeyword.project_id == project.id, mm.CompetitorKeyword.is_own)):
        if ck.position and (ck.keyword not in own or ck.position < own[ck.keyword][0]):
            own[ck.keyword] = (ck.position, ck.url)
    return own


def keyword_gap(db, project, topic_tokens: set | None = None, top: int = 20) -> dict:
    own = _own_ranking(db, project)
    comp = defaultdict(list)
    for ck in db.scalars(select(mm.CompetitorKeyword).where(mm.CompetitorKeyword.project_id == project.id,
                                                            mm.CompetitorKeyword.is_own.is_(False))):
        if ck.position and ck.position <= top:
            comp[ck.keyword].append({"domain": ck.domain, "position": ck.position, "url": ck.url, "volume": ck.volume})
    if not comp:
        return {"available": False, "message": "No competitor keyword data yet — connect Semrush/Ahrefs or import competitors' organic keyword exports."}
    rows = []
    for kw, cs in comp.items():
        if topic_tokens and not (set(stem(t) for t in tokens(kw)) & topic_tokens):
            continue
        mine = own.get(kw)
        if mine and mine[0] <= top:
            continue
        vol = max((c["volume"] or 0) for c in cs)
        rows.append({"keyword": kw, "volume": vol, "intent": intent(kw), "competitors": sorted(cs, key=lambda c: c["position"]),
                     "competitor_count": len(cs), "our_position": mine[0] if mine else None, "our_url": mine[1] if mine else None,
                     "action": "improve existing page" if mine else "create content"})
    rows.sort(key=lambda r: (-r["competitor_count"], -(r["volume"] or 0)))
    return {"available": True, "total": len(rows), "keywords": rows[:300],
            "by_intent": dict(Counter(r["intent"] for r in rows))}


def content_gap(db, project, kg: dict | None = None) -> dict:
    kg = kg or keyword_gap(db, project)
    if not kg.get("available"):
        return kg
    pages = own_pages(db, project)
    missing = [{"keyword": r["keyword"], "volume": r["volume"], "intent": r["intent"]} for r in kg["keywords"]
               if r["action"] == "create content" and not covered(r["keyword"], pages, {})]
    topics = []
    from datetime import date
    for c in cluster(missing, min_size=1):
        if c.get("unclustered"):
            continue
        topics.append({"topic": c["head"], "pillar_keyword": c["pillar"], "keywords": [k["keyword"] for k in c["keywords"][:15]],
                       "volume": c["volume"], "intent": c["intent"], "suggested_title": title_for(c["pillar"], c["intent"], date.today().year),
                       "suggested_format": "landing page" if c["intent"] in ("transactional", "local") else
                       "comparison page" if c["intent"] == "commercial" else "blog / guide"})
    topics.sort(key=lambda t: -(t["volume"] or 0))
    return {"available": True, "topics": topics[:60], "uncovered_keywords": len(missing)}


def competitor_gap(db, project) -> dict:
    own = set(_own_ranking(db, project))
    by = defaultdict(set)
    for ck in db.scalars(select(mm.CompetitorKeyword).where(mm.CompetitorKeyword.project_id == project.id, mm.CompetitorKeyword.is_own.is_(False))):
        by[ck.domain].add(ck.keyword)
    return {"competitors": [{"domain": d, "keywords": len(ks), "shared": len(ks & own), "only_them": len(ks - own),
                             "only_us": len(own - ks), "top_missing": sorted(ks - own)[:20]} for d, ks in sorted(by.items())]}


def backlink_gap(db, project) -> dict:
    snaps = db.execute(select(mm.RefdomainSnapshot.target, func.max(mm.RefdomainSnapshot.snapshot_date))
                       .where(mm.RefdomainSnapshot.project_id == project.id).group_by(mm.RefdomainSnapshot.target)).all()
    latest = {t: d for t, d in snaps}
    if "own" not in latest or len(latest) < 2:
        return {"available": False, "message": "Needs your referring domains and at least one competitor's (Ahrefs/Semrush export or API)."}
    def doms(t):
        return {r.domain: r.domain_rating for r in db.scalars(select(mm.RefdomainSnapshot).where(
            mm.RefdomainSnapshot.project_id == project.id, mm.RefdomainSnapshot.target == t, mm.RefdomainSnapshot.snapshot_date == latest[t]))}
    mine = doms("own")
    counts, dr = Counter(), {}
    for t in latest:
        if t == "own":
            continue
        for d, r in doms(t).items():
            if d not in mine:
                counts[d] += 1
                dr[d] = r
    rows = [{"domain": d, "linking_competitors": n, "domain_rating": float(dr[d]) if dr[d] is not None else None} for d, n in counts.items()]
    rows.sort(key=lambda r: (-r["linking_competitors"], -(r["domain_rating"] or 0)))
    return {"available": True, "prospects": rows[:300], "competitors": [t for t in latest if t != "own"]}


# ------------------------------------------------------------------ structure gap (live page comparison)
def _fetch(url):
    c = Crawler(url, max_pages=1, render_sample=0, mobile_sample=0, external_check_limit=0, delay=0)
    try:
        r = c.fetch(url)
    except CrawlBlocked as e:
        return {"url": url, "error": str(e)}
    p = c.parse(url, r)
    p["url"] = url
    return p


def structure_gap(our_url: str, competitor_urls: list[str]) -> dict:
    ours = _fetch(our_url)
    comps = [_fetch(u) for u in competitor_urls[:5]]
    comps = [c for c in comps if c.get("status") == 200]
    if ours.get("status") != 200:
        return {"available": False, "message": f"Could not fetch {our_url} (status {ours.get('status')}, {ours.get('error')})"}
    if not comps:
        return {"available": False, "message": "No competitor page could be fetched for comparison."}

    def med(key):
        v = sorted(c.get(key) or 0 for c in comps)
        return v[len(v) // 2]

    # H2 topic terms used by at least half of competitors but absent from ours
    def terms(p):
        return {stem(t) for h in (p.get("h2") or []) for t in tokens(h)}
    our_terms = terms(ours) | {stem(t) for t in tokens(" ".join(ours.get("h1") or []) + " " + (ours.get("title") or ""))}
    tc = Counter(t for c in comps for t in terms(c))
    missing_terms = [t for t, n in tc.most_common() if n >= max(1, len(comps) / 2) and t not in our_terms][:25]
    missing_h2 = []
    for c in comps:
        for h in c.get("h2") or []:
            if {stem(t) for t in tokens(h)} & set(missing_terms) and h not in missing_h2:
                missing_h2.append(h)
    metrics = {k: {"ours": ours.get(k) if not isinstance(ours.get(k), list) else len(ours.get(k)),
                   "competitor_median": med(k) if k not in ("h2", "questions", "schema_types") else sorted(len(c.get(k) or []) for c in comps)[len(comps) // 2]}
               for k in ("word_count", "h2", "h3_count", "images", "tables", "lists", "questions", "schema_types")}
    recs = []
    wc, cwc = ours.get("word_count") or 0, metrics["word_count"]["competitor_median"]
    if cwc and wc < 0.7 * cwc:
        recs.append(f"Expand the content: {wc} words vs a competitor median of {cwc}.")
    if missing_h2:
        recs.append("Add sections competitors cover: " + "; ".join(missing_h2[:8]) + ".")
    if metrics["questions"]["competitor_median"] > metrics["questions"]["ours"]:
        recs.append("Add an FAQ block answering the questions searchers ask (competitors average "
                    f"{metrics['questions']['competitor_median']} question headings).")
    comp_schema = Counter(t for c in comps for t in c.get("schema_types") or [])
    missing_schema = [t for t, n in comp_schema.items() if n >= max(1, len(comps) / 2) and t not in (ours.get("schema_types") or [])]
    if missing_schema:
        recs.append("Add structured data used by competitors: " + ", ".join(missing_schema) + ".")
    if metrics["tables"]["competitor_median"] > metrics["tables"]["ours"]:
        recs.append("Add a comparison table — most competing pages include one.")
    if metrics["images"]["competitor_median"] > (metrics["images"]["ours"] or 0) + 2:
        recs.append("Add more original images/diagrams.")
    if not ours.get("author") and sum(1 for c in comps if c.get("author")) >= len(comps) / 2:
        recs.append("Show the author and their expertise (E-E-A-T); competitors do.")
    return {"available": True, "our_url": our_url, "competitors": [{"url": c["url"], "word_count": c.get("word_count"), "h2": c.get("h2", [])[:20],
                                                                     "schema_types": c.get("schema_types")} for c in comps],
            "metrics": metrics, "missing_topics": missing_terms, "missing_sections": missing_h2[:20], "missing_schema": missing_schema,
            "our_h2": ours.get("h2", []), "recommendations": recs}


# ------------------------------------------------------------------ underperforming page diagnosis
def diagnose_page(db, project, url: str, target_keyword: str | None = None, competitor_urls: list | None = None) -> dict:
    """Keyword → competitor → content gap → keyword gap → structure gap → intent → SERP → backlinks →
    internal links → technical → AI search → root cause → optimisation plan."""
    steps, causes, plan = {}, [], []
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    q = []
    if anchor:
        q = [{"query": r[0], "impressions": int(r[1] or 0), "clicks": int(r[2] or 0), "position": round(float(r[3]) / r[1], 1) if r[1] else None}
             for r in db.execute(select(mm.SearchMetric.query, func.sum(mm.SearchMetric.impressions), func.sum(mm.SearchMetric.clicks),
                                        func.sum(mm.SearchMetric.position * mm.SearchMetric.impressions))
                                 .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.page == url, mm.SearchMetric.grain == "detail",
                                        mm.SearchMetric.date > anchor - timedelta(days=28)).group_by(mm.SearchMetric.query)) if r[0]]
        q.sort(key=lambda x: -x["impressions"])
    kw = target_keyword or (q[0]["query"] if q else None)
    steps["keyword_analysis"] = {"target_keyword": kw, "queries": q[:25],
                                 "target_ranking": next((x for x in q if x["query"] == kw), None) if kw else None}
    if not q:
        causes.append("The page has no search impressions yet — it may not be indexed or not match any searched query.")
        plan.append("Request indexing in Search Console and link to the page from 2–3 relevant, strong pages.")
    it = intent(kw) if kw else None
    steps["search_intent"] = {"keyword_intent": it, "page_path": path_of(url)}
    if it == "transactional" and "/blog" in url:
        causes.append(f"Intent mismatch: “{kw}” is transactional but the page is a blog post.")
        plan.append("Target an informational variant of the keyword with this post, and point the transactional keyword at a service/product page.")
    topic = {stem(t) for t in tokens(kw)} if kw else set()
    kg = keyword_gap(db, project, topic_tokens=topic) if topic else {"available": False}
    steps["keyword_gap"] = {k: v for k, v in kg.items() if k != "keywords"} | {"keywords": kg.get("keywords", [])[:30]}
    if kg.get("available") and kg.get("total"):
        plan.append(f"Cover {min(kg['total'], 10)} related keywords competitors rank for: " + ", ".join(r["keyword"] for r in kg["keywords"][:10]) + ".")
    comps = list(competitor_urls or [])
    if not comps and kw:
        comps = [u for (u,) in db.execute(select(mm.CompetitorKeyword.url).where(
            mm.CompetitorKeyword.project_id == project.id, mm.CompetitorKeyword.is_own.is_(False),
            mm.CompetitorKeyword.keyword == kw, mm.CompetitorKeyword.url.isnot(None)).order_by(mm.CompetitorKeyword.position).limit(5))]
    steps["competitor_analysis"] = {"competitor_urls": comps}
    sg = structure_gap(url, comps) if comps else {"available": False, "message": "No competitor URLs for this keyword yet — add them on the watch or import competitor keywords with URLs."}
    steps["structure_gap"] = sg
    if sg.get("available"):
        plan += sg["recommendations"]
        if sg["recommendations"]:
            causes.append("Content is thinner / less complete than the pages that rank.")
    # internal links + technical (from latest crawl)
    from .monitor import _own_crawls, _pages
    page = None
    for site in db.scalars(select(m.Site).where(m.Site.project_id == project.id)):
        cr = _own_crawls(db, site)
        if cr:
            page = _pages(db, cr[0].id).get(url) or page
    if page:
        steps["internal_links"] = {"inlinks": page.get("inlinks"), "depth": page.get("depth")}
        if (page.get("inlinks") or 0) < 3:
            causes.append(f"Weak internal linking ({page.get('inlinks') or 0} internal links point to it).")
            plan.append("Add contextual internal links from 3–5 related pages using descriptive anchors.")
        tech = {"status": page.get("status"), "noindex": page.get("noindex"), "canonical": page.get("canonical")}
        steps["technical"] = tech
        if page.get("noindex") or page.get("status") != 200 or (page.get("canonical") and page.get("canonical") != url):
            causes.append(f"Technical blocker: {tech}.")
            plan.insert(0, "Fix the technical blocker first (status/noindex/canonical).")
        steps["ai_search"] = {"question_headings": len(page.get("questions") or []), "schema": page.get("schema_types"),
                              "author": page.get("author")}
        if not page.get("questions"):
            plan.append("Add concise question-and-answer sections so AI Overviews and answer engines can quote the page.")
    else:
        steps["internal_links"] = steps["technical"] = {"available": False, "message": "Page not in the latest crawl — run an audit."}
    bl = db.scalar(select(func.count()).select_from(m.Backlink).where(m.Backlink.project_id == project.id, m.Backlink.target_url == url,
                                                                    m.Backlink.status == "live"))
    steps["backlinks"] = {"live_backlinks_in_crm": bl}
    if not bl:
        plan.append("Build 2–3 relevant backlinks to this page (it has none tracked in the Backlink CRM).")
    if steps["keyword_analysis"]["target_ranking"] and steps["keyword_analysis"]["target_ranking"]["position"] and \
            steps["keyword_analysis"]["target_ranking"]["position"] <= 10 and q and q[0]["clicks"] / max(1, q[0]["impressions"]) < 0.02:
        causes.append("Ranks on page one but gets few clicks.")
        plan.insert(0, "Rewrite the title and meta description to improve CTR.")
    return {"url": url, "target_keyword": kw, "steps": steps, "root_causes": causes or ["No single blocker found — the page needs more depth and authority than competitors."],
            "plan": list(dict.fromkeys(plan))}
