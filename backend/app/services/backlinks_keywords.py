"""Keyword research (module 11): one keyword list per project combining tracked keywords, Search Console
queries and imported own/competitor keywords, with intent, clusters, gap vs competitors and — only when
Ahrefs/Semrush data exists — keyword difficulty. Built on services/keywords.py and services/gaps.py."""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select

from .. import models as m
from .. import models_backlinks as mb
from .. import models_monitor as mm
from . import gaps
from . import keywords as kw


def difficulty_map(db, project) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in db.scalars(select(mb.KeywordMetric).where(mb.KeywordMetric.project_id == project.id)):
        cur = out.get(r.keyword)
        # Ahrefs and Semrush both use 0–100; prefer Ahrefs when both exist (stable ordering)
        if r.difficulty is not None and (not cur or (cur["source"] != "ahrefs" and r.source == "ahrefs")):
            out[r.keyword] = {"difficulty": float(r.difficulty), "source": r.source, "volume": r.volume, "cpc": float(r.cpc) if r.cpc is not None else None}
        elif r.keyword not in out and r.volume is not None:
            out[r.keyword] = {"difficulty": None, "source": r.source, "volume": r.volume, "cpc": float(r.cpc) if r.cpc is not None else None}
    for k in db.scalars(select(m.Keyword).where(m.Keyword.project_id == project.id, m.Keyword.difficulty.isnot(None))):
        out.setdefault(k.phrase, {"difficulty": float(k.difficulty), "source": "rank_tracker", "volume": k.search_volume, "cpc": None})
    return out


def research(db, project) -> dict:
    brand = project.client_name or project.name
    own = {k["keyword"]: k for k in kw.project_keywords(db, project)}
    comp = defaultdict(list)
    for ck in db.scalars(select(mm.CompetitorKeyword).where(mm.CompetitorKeyword.project_id == project.id, mm.CompetitorKeyword.is_own.is_(False))):
        comp[ck.keyword].append({"domain": ck.domain, "position": ck.position, "volume": ck.volume, "url": ck.url})
    for k, cs in comp.items():
        if k not in own:
            own[k] = {"keyword": k, "source": "competitor", "volume": max((c["volume"] or 0) for c in cs) or None,
                      "intent": kw.intent(k, brand), "words": len(k.split()), "long_tail": len(k.split()) >= 4, "competitor_only": True}
    tracked = {k.phrase for k in db.scalars(select(m.Keyword).where(m.Keyword.project_id == project.id))}
    diff = difficulty_map(db, project)
    for k, e in own.items():
        d = diff.get(k) or {}
        e["difficulty"] = d.get("difficulty")
        e["difficulty_source"] = d.get("source") if d.get("difficulty") is not None else None
        e["volume"] = e.get("volume") or d.get("volume")
        e["cpc"] = d.get("cpc")
        e["tracked"] = k in tracked
        cs = comp.get(k) or []
        e["competitors"] = sorted(cs, key=lambda c: c["position"] or 999)[:5]
        e["best_competitor_position"] = min((c["position"] for c in cs if c["position"]), default=None)
    kws = list(own.values())
    clusters = kw.cluster([k for k in kws if k["intent"] != "navigational"])
    for c in clusters:
        ds = [k["difficulty"] for k in c["keywords"] if k.get("difficulty") is not None]
        c["avg_difficulty"] = round(sum(ds) / len(ds), 1) if ds else None
    has_diff = any(k.get("difficulty") is not None for k in kws)
    src = Counter(k.get("source") or "unknown" for k in kws)
    out = {
        "total": len(kws), "by_intent": dict(Counter(k["intent"] for k in kws)), "by_source": dict(src),
        "long_tail": sum(1 for k in kws if k.get("long_tail")), "tracked": sum(1 for k in kws if k["tracked"]),
        "competitor_domains": sorted({c["domain"] for cs in comp.values() for c in cs}),
        "difficulty_available": has_diff,
        "clusters": clusters[:60],
        "keywords": sorted(kws, key=lambda k: -(k.get("impressions") or k.get("volume") or 0))[:1000],
        "has_gsc": any(k.get("source") == "gsc" for k in kws),
    }
    if not has_diff:
        out["difficulty_message"] = ("Keyword difficulty needs Ahrefs or Semrush data — connect one in Integrations or import a keyword "
                                     "export that includes a KD column.")
    return out


def gap(db, project) -> dict:
    g = gaps.keyword_gap(db, project)
    if g.get("available"):
        diff = difficulty_map(db, project)
        tracked = {k.phrase for k in db.scalars(select(m.Keyword).where(m.Keyword.project_id == project.id))}
        for r in g["keywords"]:
            r["difficulty"] = (diff.get(r["keyword"]) or {}).get("difficulty")
            r["tracked"] = r["keyword"] in tracked
        g["content_gap"] = gaps.content_gap(db, project, g).get("topics", [])[:30]
    return g
