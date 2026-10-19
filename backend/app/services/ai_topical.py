"""Topical map (module 61) and content plan builder (module 62).

Topical map: keywords from seeds + Search Console queries + tracked/imported keywords are grouped into
pillars (a seed, or the most frequent head term) and clusters (keywords.cluster inside each pillar).
Every node gets demand (impressions/clicks/position) and coverage: which of the project's own pages
already covers it (crawl titles/H1/URLs, or a page ranking top-20 for it in Search Console).

Content plan: one brief per node (pillar = hub page, cluster = supporting article), an internal-link map
(every cluster links up to its pillar, the pillar links down to each cluster, siblings cross-link) and a
publishing order (pillars and highest-demand gaps first). Plan items convert into content drafts + tasks.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select

from .. import models as m
from .. import models_ai as ma
from .. import models_monitor as mm
from . import ai_content as ac, keywords as kwsvc

MAX_KEYWORDS = 3000


def _pool(db, project, seeds: list[str], imported: list[str]) -> tuple[list[dict], dict]:
    kws = {k["keyword"].lower(): k for k in kwsvc.project_keywords(db, project, days=90)}
    sources = Counter(k.get("source") or "gsc" for k in kws.values())
    brand = project.client_name or project.name
    for s in seeds + imported:
        s = s.strip().lower()
        if s and s not in kws:
            kws[s] = {"keyword": s, "source": "seed" if s in [x.lower() for x in seeds] else "import", "intent": kwsvc.intent(s, brand)}
            sources[kws[s]["source"]] += 1
    out = [k for k in kws.values() if k.get("intent") != "navigational"][:MAX_KEYWORDS]
    return out, dict(sources)


def _ranking_pages(db, project) -> dict[str, str]:
    S = mm.SearchMetric
    anchor = db.scalar(select(func.max(S.date)).where(S.project_id == project.id))
    if not anchor:
        return {}
    best: dict[str, tuple[int, str]] = {}
    for q, pg, wp, i in db.execute(select(S.query, S.page, func.sum(S.position * S.impressions), func.sum(S.impressions))
                                   .where(S.project_id == project.id, S.grain == "detail", S.date > anchor - timedelta(days=90))
                                   .group_by(S.query, S.page)):
        if q and pg and i and wp is not None and float(wp) / i <= 20 and i > best.get(q, (0, ""))[0]:
            best[q] = (int(i), pg)
    return {q: pg for q, (_, pg) in best.items()}


def build(db, project, name: str, seeds: list[str], imported: list[str], user_id=None, min_cluster: int = 2) -> m.TopicalMap:
    pool, sources = _pool(db, project, seeds, imported)
    if not pool:
        raise ValueError("No keywords to map — add seed keywords, import a keyword list or connect/import Search Console")
    stems = {k["keyword"]: {kwsvc.stem(t) for t in kwsvc.tokens(k["keyword"])} for k in pool}
    groups: dict[str, list[dict]] = defaultdict(list)
    seed_list = [s.strip().lower() for s in seeds if s.strip()]
    if seed_list:
        seed_st = {s: {kwsvc.stem(t) for t in kwsvc.tokens(s)} for s in seed_list}
        for k in pool:
            best = max(seed_list, key=lambda s: (len(stems[k["keyword"]] & seed_st[s]) / max(1, len(seed_st[s])), -len(s)))
            if stems[k["keyword"]] & seed_st[best]:
                groups[best].append(k)
            else:
                groups["(other topics)"].append(k)
        for s in seed_list:
            groups.setdefault(s, [])
    else:
        freq = Counter(t for st in stems.values() for t in st)
        assigned = set()
        for t, n in freq.most_common():
            if n < 3:
                break
            members = [k for k in pool if k["keyword"] not in assigned and t in stems[k["keyword"]]]
            if len(members) >= 3:
                pillar_kw = min((k["keyword"] for k in members), key=lambda x: (len(x.split()), -len(x)))
                groups[pillar_kw] = members
                assigned |= {k["keyword"] for k in members}
        rest = [k for k in pool if k["keyword"] not in assigned]
        if rest:
            groups["(other topics)"] = rest
    tm = m.TopicalMap(organisation_id=project.organisation_id, project_id=project.id, name=name[:200])
    db.add(tm)
    db.flush()
    pages = kwsvc.own_pages(db, project)
    ranking = _ranking_pages(db, project)
    order = 0
    n_nodes = 0
    for pillar_kw, members in sorted(groups.items(), key=lambda g: (g[0] == "(other topics)", -_demand(g[1]))):
        if not members and not seed_list:
            continue
        intents = Counter(k["intent"] for k in members)
        pn = m.TopicNode(organisation_id=project.organisation_id, topical_map_id=tm.id, parent_id=None, keyword=pillar_kw,
                         intent=intents.most_common(1)[0][0] if members else kwsvc.intent(pillar_kw), volume=_vol(members))
        db.add(pn)
        db.flush()
        order += 1
        n_nodes += 1
        pcov = _coverage(pillar_kw, members, pages, ranking)
        db.add(ma.TopicNodeMeta(topic_node_id=pn.id, organisation_id=project.organisation_id, kind="pillar", keywords=_slim(members[:200]),
                                impressions=sum(k.get("impressions") or 0 for k in members), clicks=sum(k.get("clicks") or 0 for k in members),
                                position=_pos(members), coverage_status=pcov[0], coverage_url=pcov[1], position_order=order))
        clusters = kwsvc.cluster([k for k in members if k["keyword"] != pillar_kw], min_size=min_cluster) if members else []
        for c in clusters:
            if c.get("unclustered"):
                parts = [kwsvc._mk_cluster([k], k["keyword"]) for k in c["keywords"][:25]]
            else:
                parts = [c]
            for p in parts:
                mem = p["keywords"]
                cn = m.TopicNode(organisation_id=project.organisation_id, topical_map_id=tm.id, parent_id=pn.id, keyword=p["pillar"],
                                 intent=p["intent"], volume=_vol(mem))
                db.add(cn)
                db.flush()
                order += 1
                n_nodes += 1
                cov = _coverage(p["pillar"], mem, pages, ranking)
                db.add(ma.TopicNodeMeta(topic_node_id=cn.id, organisation_id=project.organisation_id, kind="cluster", keywords=_slim(mem),
                                        impressions=sum(k.get("impressions") or 0 for k in mem), clicks=sum(k.get("clicks") or 0 for k in mem),
                                        position=_pos(mem), coverage_status=cov[0], coverage_url=cov[1], position_order=order))
    db.flush()
    stats = coverage_stats(db, tm.id)
    db.add(ma.TopicalMapMeta(topical_map_id=tm.id, organisation_id=project.organisation_id, seeds=seed_list, sources=sources,
                             stats={**stats, "keywords": len(pool), "nodes": n_nodes, "pages_checked": len(pages)}, created_by=user_id))
    return tm


def _demand(members):
    return sum((k.get("impressions") or 0) + (k.get("volume") or 0) for k in members)


def _vol(members):
    v = [k.get("volume") for k in members if k.get("volume")]
    return int(sum(v)) if v else None


def _pos(members):
    num = sum((k.get("position") or 0) * (k.get("impressions") or 0) for k in members if k.get("position"))
    den = sum(k.get("impressions") or 0 for k in members if k.get("position"))
    return round(num / den, 1) if den else None


def _slim(members):
    return [{"keyword": k["keyword"], "intent": k.get("intent"), "impressions": k.get("impressions"), "clicks": k.get("clicks"),
             "position": k.get("position"), "volume": k.get("volume"), "source": k.get("source")} for k in members]


def _coverage(head: str, members: list[dict], pages: dict, ranking: dict) -> tuple[str, str | None]:
    url = kwsvc.covered(head, pages, ranking)
    if url:
        return "covered", url
    hits = [kwsvc.covered(k["keyword"], pages, ranking) for k in members]
    hits = [h for h in hits if h]
    if hits:
        return "partial", Counter(hits).most_common(1)[0][0]
    return "gap", None


def coverage_stats(db, map_id) -> dict:
    rows = db.execute(select(ma.TopicNodeMeta.kind, ma.TopicNodeMeta.coverage_status, func.count())
                      .join(m.TopicNode, m.TopicNode.id == ma.TopicNodeMeta.topic_node_id)
                      .where(m.TopicNode.topical_map_id == map_id).group_by(ma.TopicNodeMeta.kind, ma.TopicNodeMeta.coverage_status)).all()
    out = {"pillars": 0, "clusters": 0, "covered": 0, "partial": 0, "gap": 0}
    for kind, cov, n in rows:
        out["pillars" if kind == "pillar" else "clusters"] += n
        out[cov] += n
    total = out["pillars"] + out["clusters"]
    out["coverage_pct"] = round(100 * (out["covered"] + 0.5 * out["partial"]) / total) if total else 0
    return out


def tree(db, map_id) -> list[dict]:
    nodes = db.execute(select(m.TopicNode, ma.TopicNodeMeta).join(ma.TopicNodeMeta, ma.TopicNodeMeta.topic_node_id == m.TopicNode.id)
                       .where(m.TopicNode.topical_map_id == map_id).order_by(ma.TopicNodeMeta.position_order)).all()
    by = {}
    roots = []
    for n, mt in nodes:
        d = {"id": str(n.id), "keyword": n.keyword, "intent": n.intent, "volume": n.volume, "kind": mt.kind, "impressions": mt.impressions,
             "clicks": mt.clicks, "position": float(mt.position) if mt.position is not None else None, "coverage_status": mt.coverage_status,
             "coverage_url": mt.coverage_url, "keywords": mt.keywords, "children": []}
        by[n.id] = d
        if n.parent_id is None:
            roots.append(d)
    for n, _ in nodes:
        if n.parent_id and n.parent_id in by:
            by[n.parent_id]["children"].append(by[n.id])
    return roots


# ------------------------------------------------------------------ content plan
def build_plan(db, project, tm: m.TopicalMap, name: str, per_week: int = 2, start: date | None = None, user_id=None) -> m.ContentPlan:
    roots = tree(db, tm.id)
    if not roots:
        raise ValueError("The topical map has no nodes")
    site = (db.scalar(select(m.Site.domain).where(m.Site.project_id == project.id).limit(1)) or "").rstrip("/")
    base = site if "://" in site else (f"https://{site}" if site else "")
    plan = m.ContentPlan(organisation_id=project.organisation_id, topical_map_id=tm.id, internal_link_map={})
    db.add(plan)
    db.flush()
    db.add(ma.ContentPlanMeta(content_plan_id=plan.id, organisation_id=project.organisation_id, project_id=project.id, name=name[:200],
                              settings={"per_week": per_week, "start": (start or date.today()).isoformat()}, created_by=user_id))
    items, links = [], []
    year = date.today().year

    def url_for(node, pillar_slug=None):
        if node["coverage_url"] and node["coverage_status"] == "covered":
            return node["coverage_url"]
        slug = ac.slugify(node["keyword"])
        return f"{base}/{pillar_slug}/{slug}" if pillar_slug else f"{base}/{slug}"
    for p in roots:
        p_slug = ac.slugify(p["keyword"])
        p_url = url_for(p)
        items.append((p, None, p_url))
        for c in p["children"]:
            c_url = url_for(c, p_slug)
            items.append((c, p, c_url))
            links.append({"from": c_url, "to": p_url, "anchor": p["keyword"], "type": "cluster→pillar"})
            links.append({"from": p_url, "to": c_url, "anchor": c["keyword"], "type": "pillar→cluster"})
        kids = p["children"]
        for i, c in enumerate(kids):  # each cluster links to its next sibling (ring) so siblings are crawlable
            if len(kids) > 1:
                nxt = kids[(i + 1) % len(kids)]
                links.append({"from": url_for(c, p_slug), "to": url_for(nxt, p_slug), "anchor": nxt["keyword"], "type": "sibling"})
    # publishing order: pillars first, then gaps by demand, then partial refreshes
    rank = {"gap": 0, "partial": 1, "covered": 2}
    ordered = sorted(items, key=lambda x: (x[1] is not None, rank[x[0]["coverage_status"]], -(x[0]["impressions"] or x[0]["volume"] or 0)))
    start = start or date.today()
    per_week = max(1, min(14, per_week))
    for i, (node, parent, url) in enumerate(ordered):
        action = {"covered": "keep / optimise existing page", "partial": "refresh existing page to cover the cluster", "gap": "create new page"}[node["coverage_status"]]
        brief = {"role": "pillar" if parent is None else "cluster", "title": kwsvc.title_for(node["keyword"], node["intent"] or "informational", year),
                 "primary_keyword": node["keyword"], "supporting_keywords": [k["keyword"] for k in node["keywords"] if k["keyword"] != node["keyword"]][:15],
                 "intent": node["intent"], "target_url": url, "existing_url": node["coverage_url"], "coverage_status": node["coverage_status"],
                 "action": action, "content_type": "landing" if node["intent"] in ("transactional", "local") else "blog",
                 "demand": node["impressions"] or node["volume"] or 0, "pillar": parent["keyword"] if parent else None,
                 "links_to": [lk["to"] for lk in links if lk["from"] == url], "links_from": [lk["from"] for lk in links if lk["to"] == url],
                 "week": i // per_week + 1, "publish_by": (start + timedelta(weeks=i // per_week)).isoformat(), "status": "planned",
                 "requirements": ["Primary keyword in title, H1 and first paragraph",
                                  f"Link to the pillar page ({parent['keyword']})" if parent else "Link to every cluster article in this topic",
                                  "Answer the main question in the first 2 sentences"]}
        db.add(m.ContentBrief(organisation_id=project.organisation_id, content_plan_id=plan.id, topic_node_id=node["id"], brief=brief))
    plan.internal_link_map = {"links": links, "count": len(links)}
    db.flush()
    return plan
