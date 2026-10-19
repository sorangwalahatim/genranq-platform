"""Autonomous project monitor.

    collect → detect change → analyse root cause → classify → route to team →
    create tasks → notify → verify fix → resolve / reopen

Everything is computed per project from that project's own data (search_metric,
analytics_metric, refdomain snapshots, rank history, crawls). Windows:
  cur  = last 7 days ending at the latest date with data
  prev = the 7 days before that
  base = 28 days before cur (weekly average) — the project baseline
"""
from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_monitor as mm
from .ingest import path_of

DEFAULTS = {
    "traffic_drop_pct": 20, "traffic_drop_critical_pct": 40, "min_clicks": 30,
    "page_drop_pct": 30, "page_min_clicks": 10, "device_drop_pct": 25, "country_drop_pct": 30,
    "ctr_drop_pct": 30, "rank_drop_positions": 5, "rank_up_positions": 5, "rank_min_impr": 50,
    "new_query_min_impr": 100, "lost_query_min_clicks": 5, "striking_min_impr": 200, "low_ctr_min_impr": 500,
    "tracking_zero_days": 2, "sessions_drop_pct": 25, "conversions_drop_pct": 40,
    "refdomain_loss_min": 5, "refdomain_loss_pct": 5, "verify_window_days": 7, "max_opportunity_tasks": 10,
    "verify_delay_sec": 120,  # wait for the deploy / CDN cache before re-fetching a fixed URL
}
ROUTING = {"developer": "developer", "seo": "seo", "content": "content", "backlink": "backlink", "analytics": "analytics"}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
# expected CTR by position (industry curve, used for 'low CTR for its position')
CTR_CURVE = {1: .28, 2: .15, 3: .10, 4: .07, 5: .05, 6: .04, 7: .03, 8: .025, 9: .02, 10: .018}


@dataclass
class Finding:
    kind: str
    category: str
    severity: str
    title: str
    recommendation: str
    url: str | None = None
    query: str | None = None
    dimension: str | None = None
    evidence: dict = field(default_factory=dict)
    root_cause: dict = field(default_factory=dict)
    categories: list = field(default_factory=list)
    verify: dict = field(default_factory=dict)
    is_opportunity: bool = False
    task_notes: dict = field(default_factory=dict)  # category -> team-specific instruction

    @property
    def fingerprint(self):
        return hashlib.sha1("|".join(str(x or "") for x in (self.kind, self.url, self.query, self.dimension)).encode()).hexdigest()


def pct(new, old):
    if not old:
        return None
    return round(100.0 * (new - old) / old, 1)


def utcnow():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ config
def get_config(db: Session, project: m.Project) -> mm.MonitorConfig:
    c = db.get(mm.MonitorConfig, project.id)
    if not c:
        c = mm.MonitorConfig(project_id=project.id, organisation_id=project.organisation_id, thresholds={}, severity_overrides={},
                             routing={}, channels={}, important_urls=[])
        db.add(c)
        db.flush()
    return c


def thresholds(cfg: mm.MonitorConfig) -> dict:
    return {**DEFAULTS, **(cfg.thresholds or {})}


# ------------------------------------------------------------------ data access
class Windows:
    def __init__(self, anchor: date):
        self.anchor = anchor
        self.cur = (anchor - timedelta(days=6), anchor)
        self.prev = (anchor - timedelta(days=13), anchor - timedelta(days=7))
        self.base = (anchor - timedelta(days=34), anchor - timedelta(days=7))


def _grain_present(db, pid, grain):
    return db.scalar(select(func.count()).select_from(mm.SearchMetric).where(mm.SearchMetric.project_id == pid,
                                                                            mm.SearchMetric.grain == grain).limit(1)) > 0


def search_agg(db, pid, dims: tuple, rng: tuple, grains: tuple, where=None) -> dict:
    """Sum clicks/impressions and impression-weighted position grouped by dims, from the first grain present."""
    for g in grains:
        if not _grain_present(db, pid, g):
            continue
        cols = [getattr(mm.SearchMetric, d) for d in dims]
        q = select(*cols, func.sum(mm.SearchMetric.clicks), func.sum(mm.SearchMetric.impressions),
                   func.sum(mm.SearchMetric.position * mm.SearchMetric.impressions)) \
            .where(mm.SearchMetric.project_id == pid, mm.SearchMetric.grain == g,
                   mm.SearchMetric.date >= rng[0], mm.SearchMetric.date <= rng[1])
        if where is not None:
            q = q.where(where)
        if cols:
            q = q.group_by(*cols)
        out = {}
        for row in db.execute(q):
            key = tuple(row[:len(dims)])
            c, i, wp = row[len(dims)], row[len(dims) + 1], row[len(dims) + 2]
            out[key] = {"clicks": int(c or 0), "impressions": int(i or 0),
                        "position": round(float(wp) / i, 1) if wp is not None and i else None}
        return out
    return {}


def ga_agg(db, pid, dims: tuple, rng: tuple, where=None) -> dict:
    cols = [getattr(mm.AnalyticsMetric, d) for d in dims]
    q = select(*cols, func.sum(mm.AnalyticsMetric.sessions), func.sum(mm.AnalyticsMetric.conversions),
               func.sum(mm.AnalyticsMetric.engaged_sessions)) \
        .where(mm.AnalyticsMetric.project_id == pid, mm.AnalyticsMetric.date >= rng[0], mm.AnalyticsMetric.date <= rng[1])
    if where is not None:
        q = q.where(where)
    if cols:
        q = q.group_by(*cols)
    return {tuple(r[:len(dims)]): {"sessions": int(r[len(dims)] or 0), "conversions": float(r[len(dims) + 1] or 0),
                                   "engaged": int(r[len(dims) + 2] or 0)} for r in db.execute(q)}


def important_urls(db, project, cfg, w: Windows | None) -> set[str]:
    imp = set(cfg.important_urls or [])
    if w:
        pages = search_agg(db, project.id, ("page",), (w.anchor - timedelta(days=27), w.anchor), ("page", "detail"))
        imp |= {k[0] for k, v in pages.items() if v["clicks"] >= 5 and k[0]}
    return imp


# ------------------------------------------------------------------ detectors: search (GSC)
def detect_search(db, project, cfg, th, w: Windows, ctx: dict) -> list[Finding]:
    F: list[Finding] = []
    pid = project.id
    tot_c = search_agg(db, pid, (), w.cur, ("total", "page", "detail")).get((), {"clicks": 0, "impressions": 0})
    tot_p = search_agg(db, pid, (), w.prev, ("total", "page", "detail")).get((), {"clicks": 0, "impressions": 0})
    ctx["search"] = {"cur": tot_c, "prev": tot_p}
    pages_c = search_agg(db, pid, ("page",), w.cur, ("page", "detail"))
    pages_p = search_agg(db, pid, ("page",), w.prev, ("page", "detail"))
    dev_c = search_agg(db, pid, ("device",), w.cur, ("total", "page", "detail"))
    dev_p = search_agg(db, pid, ("device",), w.prev, ("total", "page", "detail"))
    q_cur = search_agg(db, pid, ("query", "page"), w.cur, ("detail",))
    q_prev = search_agg(db, pid, ("query", "page"), w.prev, ("detail",))
    qq_cur = search_agg(db, pid, ("query",), w.cur, ("detail",))
    qq_prev = search_agg(db, pid, ("query",), w.prev, ("detail",))
    qq_base = search_agg(db, pid, ("query",), w.base, ("detail",))

    # ---- page-level drops (also the root-cause contributors of any site drop)
    page_drops = []
    for (pg,), pv in pages_p.items():
        cv = pages_c.get((pg,), {"clicks": 0, "impressions": 0, "position": None})
        ch = pct(cv["clicks"], pv["clicks"])
        if pv["clicks"] >= th["page_min_clicks"] and ch is not None and ch <= -th["page_drop_pct"]:
            page_drops.append({"page": pg, "clicks_prev": pv["clicks"], "clicks_cur": cv["clicks"], "change_pct": ch,
                               "lost": pv["clicks"] - cv["clicks"], "pos_prev": pv["position"], "pos_cur": cv["position"]})
    page_drops.sort(key=lambda x: -x["lost"])
    ctx["page_drops"] = page_drops

    # ---- query position moves (for root cause + keyword issues)
    q_moves = []
    for (q,), cv in qq_cur.items():
        pv = qq_prev.get((q,))
        if pv and cv["position"] and pv["position"] and max(cv["impressions"], pv["impressions"]) >= th["rank_min_impr"]:
            q_moves.append({"query": q, "pos_prev": pv["position"], "pos_cur": cv["position"],
                            "delta": round(pv["position"] - cv["position"], 1), "impr": cv["impressions"], "clicks": cv["clicks"]})
    ctx["query_moves"] = q_moves

    # ---- device split
    dev_changes = {d[0]: pct(dev_c.get(d, {"clicks": 0})["clicks"], v["clicks"]) for d, v in dev_p.items() if d[0] and v["clicks"] >= th["page_min_clicks"]}
    ctx["device_changes"] = dev_changes

    # ---- site-level traffic drop
    ch = pct(tot_c["clicks"], tot_p["clicks"])
    if tot_p["clicks"] >= th["min_clicks"] and ch is not None and ch <= -th["traffic_drop_pct"]:
        F.append(_site_drop_finding(db, project, th, w, ctx, tot_c, tot_p, ch))
    elif tot_p["clicks"] >= th["min_clicks"] and ch is not None and ch >= 40:
        F.append(Finding("traffic_spike", "seo", "low", f"Organic clicks up {ch}% week over week",
                         "Find which pages and queries drove the increase and expand on them (related keywords, supporting articles).",
                         evidence={"clicks_cur": tot_c["clicks"], "clicks_prev": tot_p["clicks"], "change_pct": ch}, is_opportunity=True))

    # ---- device-specific drop (others stable)
    for d, c in dev_changes.items():
        others = [v for k, v in dev_changes.items() if k != d and v is not None]
        if c is not None and c <= -th["device_drop_pct"] and others and all(o > c / 2 for o in others):  # others fell < half as much
            F.append(Finding("device_drop", "developer", "high", f"{d.title()} clicks down {abs(c)}% — far more than other devices",
                             f"The drop is specific to {d}. Check {d} rendering, page speed / Core Web Vitals, {d} usability, JavaScript errors "
                             f"and whether {d} content matches desktop. Then confirm {d} indexing in Search Console.",
                             dimension=f"device={d}", categories=["developer", "seo"],
                             evidence={"device_changes": dev_changes},
                             task_notes={"developer": f"Investigate {d}-specific rendering, speed and UX regressions.",
                                         "seo": f"Check {d} indexing and {d} SERP changes for the top affected queries."},
                             verify={"type": "metric", "metric": f"device_clicks:{d}"}))

    # ---- country drop
    cty_c = search_agg(db, pid, ("country",), w.cur, ("total", "page", "detail"))
    cty_p = search_agg(db, pid, ("country",), w.prev, ("total", "page", "detail"))
    for (cty,), pv in cty_p.items():
        if cty and pv["clicks"] >= th["min_clicks"]:
            c = pct(cty_c.get((cty,), {"clicks": 0})["clicks"], pv["clicks"])
            if c is not None and c <= -th["country_drop_pct"] and (ch is None or c < ch - 15):
                F.append(Finding("country_drop", "seo", "medium", f"Clicks from {cty.upper()} down {abs(c)}%",
                                 f"Check rankings in the {cty.upper()} market, hreflang/geo-targeting and local competitors.",
                                 dimension=f"country={cty}", evidence={"clicks_prev": pv["clicks"], "change_pct": c}))

    # ---- individual landing pages (only when not already explained as the whole site)
    for d in page_drops[:15]:
        sev = "high" if d["change_pct"] <= -50 and d["clicks_prev"] >= 50 else "medium"
        F.append(Finding("page_traffic_drop", "seo", sev, f"Clicks to {path_of(d['page'])} down {abs(d['change_pct'])}%",
                         "Compare the page's queries and positions week over week, check for technical changes on the URL, and review "
                         "competitor pages that overtook it.", url=d["page"], categories=["seo", "content"], evidence=d,
                         verify={"type": "metric", "metric": "page_clicks"}))

    # ---- CTR drop with stable position (title/meta problem or SERP feature change)
    for (pg,), pv in pages_p.items():
        cv = pages_c.get((pg,))
        if not cv or pv["impressions"] < th["low_ctr_min_impr"] or not pv["impressions"] or not cv["impressions"]:
            continue
        ctr_p, ctr_c = pv["clicks"] / pv["impressions"], cv["clicks"] / cv["impressions"]
        c = pct(ctr_c, ctr_p)
        if c is not None and c <= -th["ctr_drop_pct"] and pv["position"] and cv["position"] and abs(cv["position"] - pv["position"]) < 1.5:
            F.append(Finding("ctr_drop", "content", "medium", f"CTR on {path_of(pg)} fell {abs(c)}% at a stable position",
                             "Position held but fewer searchers click: rewrite the title and meta description, check whether a SERP "
                             "feature or AI Overview now sits above the result, and add rich-result markup.", url=pg,
                             categories=["content", "seo"], evidence={"ctr_prev": round(ctr_p * 100, 2), "ctr_cur": round(ctr_c * 100, 2),
                                                                      "position": cv["position"]}))

    # ---- ranking down / up per query
    pages_for_q = defaultdict(list)
    for (q, pg), v in q_cur.items():
        pages_for_q[q].append((pg, v["impressions"]))
    for mv in q_moves:
        top_page = max(pages_for_q.get(mv["query"], [(None, 0)]), key=lambda x: x[1])[0]
        if mv["delta"] <= -th["rank_drop_positions"]:
            sev = "high" if mv["pos_prev"] <= 10 else "medium"
            F.append(Finding("keyword_down", "seo", sev, f"“{mv['query']}” dropped {abs(mv['delta'])} positions ({mv['pos_prev']} → {mv['pos_cur']})",
                             "Check the SERP for new competitors or intent change, compare the ranking page's content depth and freshness, "
                             "and look for technical or internal-link changes on the page.", query=mv["query"], url=top_page,
                             categories=["seo", "content"], evidence=mv, verify={"type": "metric", "metric": "query_position"}))
        elif mv["delta"] >= th["rank_up_positions"]:
            F.append(Finding("keyword_up", "seo", "low", f"“{mv['query']}” improved {mv['delta']} positions ({mv['pos_prev']} → {mv['pos_cur']})",
                             "Record what changed, then expand: target related queries on the same page and add a supporting article "
                             "linking to it.", query=mv["query"], url=top_page, evidence=mv, is_opportunity=True))

    # ---- new queries (in cur, absent from prev+base)
    for (q,), cv in qq_cur.items():
        if cv["impressions"] >= th["new_query_min_impr"] and (q,) not in qq_prev and (q,) not in qq_base:
            best = max(pages_for_q.get(q, [(None, 0)]), key=lambda x: x[1])[0]
            existing = cv["position"] is not None and cv["position"] <= 30 and best
            F.append(Finding("new_query", "content" if not existing else "seo", "low",
                             f"New query “{q}”: {cv['impressions']} impressions at position {cv['position']}",
                             (f"Optimise the existing page {path_of(best)} for this query (title, H2, FAQ)." if existing else
                              "No page targets this query well: create a new piece of content for it."),
                             query=q, url=best, evidence={**cv, "action": "optimise_existing" if existing else "create_content"},
                             is_opportunity=True))

    # ---- lost queries
    for (q,), pv in {**qq_base, **qq_prev}.items():
        clicks_before = (qq_prev.get((q,), {"clicks": 0})["clicks"] + qq_base.get((q,), {"clicks": 0})["clicks"])
        if clicks_before >= th["lost_query_min_clicks"] and (q,) not in qq_cur:
            F.append(Finding("lost_query", "seo", "medium", f"Query “{q}” no longer gets impressions",
                             "Check whether the ranking page changed, was de-indexed or was replaced by a competitor; restore or "
                             "re-target the content.", query=q, evidence={"clicks_before": clicks_before}))

    # ---- opportunities: striking distance & low CTR for position
    for (q,), cv in qq_cur.items():
        if cv["position"] and 10 < cv["position"] <= 20 and cv["impressions"] >= th["striking_min_impr"]:
            best = max(pages_for_q.get(q, [(None, 0)]), key=lambda x: x[1])[0]
            F.append(Finding("opp_striking_distance", "seo", "low", f"“{q}” sits at position {cv['position']} with {cv['impressions']} impressions",
                             "Page-two keyword with real demand: strengthen the page (depth, FAQ, internal links from strong pages) to push "
                             "it onto page one.", query=q, url=best, evidence=cv, is_opportunity=True))
        elif cv["position"] and cv["position"] <= 10 and cv["impressions"] >= th["low_ctr_min_impr"]:
            exp = CTR_CURVE.get(max(1, round(cv["position"])), .02)
            ctr = cv["clicks"] / cv["impressions"]
            if ctr < exp * 0.5:
                best = max(pages_for_q.get(q, [(None, 0)]), key=lambda x: x[1])[0]
                F.append(Finding("opp_low_ctr", "content", "low", f"“{q}” CTR {round(ctr * 100, 1)}% at position {cv['position']} (expected ≈{round(exp * 100)}%)",
                                 "Rewrite the title and meta description to match the query intent; add rich-result markup.",
                                 query=q, url=best, evidence={**cv, "ctr": round(ctr * 100, 2), "expected_ctr": round(exp * 100, 1)},
                                 is_opportunity=True))
    return F


def _site_drop_finding(db, project, th, w, ctx, tot_c, tot_p, ch) -> Finding:
    """Root-cause analysis for a site-wide drop: where, which pages, which devices, which queries, what changed."""
    lost = tot_p["clicks"] - tot_c["clicks"]
    drops = ctx["page_drops"]
    contrib = sum(d["lost"] for d in drops)
    share = round(100 * contrib / lost) if lost > 0 else None
    changes = db.scalars(select(mm.SiteChange).where(mm.SiteChange.project_id == project.id,
                                                     mm.SiteChange.detected_at >= datetime.combine(w.prev[0], datetime.min.time(), timezone.utc))
                         .order_by(desc(mm.SiteChange.detected_at))).all()
    drop_paths = {d["page"] for d in drops}
    tech = [c for c in changes if c.url in drop_paths and c.field in ("indexable", "status", "canonical", "robots", "page_removed")]
    devc = ctx["device_changes"]
    worst_dev = min(devc.items(), key=lambda kv: kv[1] if kv[1] is not None else 0) if devc else None
    falling = sorted([q for q in ctx["query_moves"] if q["delta"] <= -th["rank_drop_positions"]], key=lambda q: q["delta"])[:10]
    lost_rd = ctx.get("lost_refdomains") or []
    cats, notes, causes = ["seo"], {}, []
    if tech:
        cats.insert(0, "developer")
        causes.append(f"{len(tech)} technical change(s) on affected pages ({', '.join(sorted({c.field for c in tech}))})")
        notes["developer"] = "Check recent deployment and these changes on affected URLs: " + "; ".join(
            f"{path_of(c.url)} {c.field}: {c.old_value} → {c.new_value}" for c in tech[:8])
    if worst_dev and worst_dev[1] is not None and worst_dev[1] <= ch - 15:
        if "developer" not in cats:
            cats.append("developer")
        causes.append(f"{worst_dev[0]} disproportionately affected ({worst_dev[1]}% vs {ch}% overall)")
        notes.setdefault("developer", f"Investigate {worst_dev[0]}-specific rendering/speed/UX regressions.")
    if falling:
        causes.append(f"{len(falling)} queries fell ≥{th['rank_drop_positions']} positions")
        notes["seo"] = "Investigate ranking decline for: " + ", ".join(f"“{q['query']}” ({q['pos_prev']}→{q['pos_cur']})" for q in falling[:6])
    if drops and not tech:
        cats.append("content")
        notes["content"] = "Review content of affected landing pages vs the pages now outranking them: " + ", ".join(path_of(d["page"]) for d in drops[:6])
    if lost_rd:
        cats.append("backlink")
        causes.append(f"{len(lost_rd)} referring domains lost")
        notes["backlink"] = "Check lost referring domains: " + ", ".join(lost_rd[:10])
    sev = "critical" if ch <= -th["traffic_drop_critical_pct"] or tech else "high"
    root = {
        "what": f"Organic clicks {ch}% week over week ({tot_p['clicks']} → {tot_c['clicks']}).",
        "when": f"{w.cur[0]} to {w.cur[1]} compared with {w.prev[0]} to {w.prev[1]}.",
        "where": (f"{share}% of the loss comes from {len(drops)} landing page(s)" if share is not None and drops else "Spread across the site")
                 + (f"; {worst_dev[0]} {worst_dev[1]}%" if worst_dev else ""),
        "why": "; ".join(causes) or "No single cause found in the connected data — check SERP changes and algorithm updates.",
        "evidence": {"top_pages": drops[:10], "device_changes": devc, "falling_queries": falling, "changes": [
            {"url": c.url, "field": c.field, "old": c.old_value, "new": c.new_value, "at": c.detected_at.isoformat()} for c in tech[:10]],
            "lost_refdomains": lost_rd[:20]},
        "who": cats, "urgency": sev,
        "verify": "Clicks return to within 10% of the previous week, and affected pages are indexable and returning 200.",
    }
    return Finding("traffic_drop", cats[0], sev, f"Organic traffic down {abs(ch)}% ({tot_p['clicks']} → {tot_c['clicks']} clicks/week)",
                   "Follow the team-specific tasks: they are built from the pages, queries, devices and changes behind the drop.",
                   evidence={"clicks_prev": tot_p["clicks"], "clicks_cur": tot_c["clicks"], "change_pct": ch,
                             "impressions_prev": tot_p["impressions"], "impressions_cur": tot_c["impressions"]},
                   root_cause=root, categories=list(dict.fromkeys(cats)), task_notes=notes,
                   verify={"type": "metric", "metric": "site_clicks"})


# ------------------------------------------------------------------ detectors: analytics (GA4)
def detect_analytics(db, project, cfg, th, ctx) -> list[Finding]:
    F = []
    pid = project.id
    anchor = db.scalar(select(func.max(mm.AnalyticsMetric.date)).where(mm.AnalyticsMetric.project_id == pid))
    if not anchor:
        return F
    w = Windows(anchor)
    ctx["ga_anchor"] = anchor
    cur = ga_agg(db, pid, (), w.cur).get((), {"sessions": 0, "conversions": 0})
    prev = ga_agg(db, pid, (), w.prev).get((), {"sessions": 0, "conversions": 0})
    base = ga_agg(db, pid, (), w.base).get((), {"sessions": 0, "conversions": 0})
    ctx["analytics"] = {"cur": cur, "prev": prev}
    lastn = ga_agg(db, pid, (), (anchor - timedelta(days=th["tracking_zero_days"] - 1), anchor)).get((), {"sessions": 0, "conversions": 0})
    if lastn["sessions"] == 0 and base["sessions"] > 0:
        F.append(Finding("tracking_stopped", "analytics", "critical", f"GA4 recorded zero sessions for the last {th['tracking_zero_days']} days",
                         "Tracking has almost certainly broken: check the GA4 tag / GTM container on all templates, consent mode, "
                         "and recent deployments that may have removed the tag.", categories=["analytics", "developer"],
                         evidence={"baseline_weekly_sessions": round(base["sessions"] / 4)},
                         task_notes={"developer": "Confirm the GA4 / GTM snippet is still present on every template after the latest deploy."},
                         verify={"type": "metric", "metric": "ga_sessions"}))
        return F
    last3 = ga_agg(db, pid, (), (anchor - timedelta(days=2), anchor)).get((), {"sessions": 0, "conversions": 0})
    if base["conversions"] > 0 and last3["conversions"] == 0 and last3["sessions"] > 0.5 * base["sessions"] / 28 * 3:
        F.append(Finding("conversion_tracking", "analytics", "high", "Key events (conversions) stopped while traffic continues",
                         "Check the conversion / key-event configuration, form submission events and thank-you page tags.",
                         evidence={"sessions_last3d": last3["sessions"], "baseline_conversions_28d": base["conversions"]},
                         verify={"type": "metric", "metric": "ga_conversions"}))
    c = pct(cur["sessions"], prev["sessions"])
    if prev["sessions"] >= th["min_clicks"] and c is not None and c <= -th["sessions_drop_pct"]:
        F.append(Finding("sessions_drop", "analytics", "high" if c <= -40 else "medium", f"Sessions down {abs(c)}% week over week",
                         "Break the drop down by channel and landing page (see evidence); organic drops are investigated with Search "
                         "Console data, other channels with their owners.", categories=["analytics", "seo"],
                         evidence={"sessions_cur": cur["sessions"], "sessions_prev": prev["sessions"], "change_pct": c,
                                   "by_channel": {k[0]: pct(ga_agg(db, pid, ("channel",), w.cur).get(k, {"sessions": 0})["sessions"], v["sessions"])
                                                  for k, v in ga_agg(db, pid, ("channel",), w.prev).items() if k[0]}}))
    cc = pct(cur["conversions"], prev["conversions"])
    if prev["conversions"] >= 10 and cc is not None and cc <= -th["conversions_drop_pct"]:
        F.append(Finding("conversions_drop", "analytics", "high", f"Conversions down {abs(cc)}% week over week",
                         "Check whether the drop follows a traffic drop (see sessions) or a funnel / form problem on landing pages.",
                         evidence={"conversions_cur": cur["conversions"], "conversions_prev": prev["conversions"]}))
    # landing pages & devices
    lp_c, lp_p = ga_agg(db, pid, ("landing_page",), w.cur), ga_agg(db, pid, ("landing_page",), w.prev)
    for (lp,), pv in lp_p.items():
        if not lp or pv["sessions"] < th["page_min_clicks"] * 2:
            continue
        x = pct(lp_c.get((lp,), {"sessions": 0})["sessions"], pv["sessions"])
        if x is not None and x <= -40:
            F.append(Finding("landing_sessions_drop", "analytics", "medium", f"Sessions on {lp} down {abs(x)}%",
                             "Check the page's organic queries, referral sources and any recent change to the page.",
                             url=lp, categories=["analytics", "seo"], evidence={"sessions_prev": pv["sessions"], "change_pct": x}))
    dv_c, dv_p = ga_agg(db, pid, ("device",), w.cur), ga_agg(db, pid, ("device",), w.prev)
    dch = {k[0]: pct(dv_c.get(k, {"sessions": 0})["sessions"], v["sessions"]) for k, v in dv_p.items() if k[0] and v["sessions"] >= 20}
    for d, x in dch.items():
        others = [v for k, v in dch.items() if k != d and v is not None]
        if x is not None and x <= -th["device_drop_pct"] and others and all(o > x / 2 for o in others):
            F.append(Finding("device_sessions_drop", "developer", "high", f"{d.title()} sessions down {abs(x)}% — far more than other devices",
                             f"Investigate {d} UX, speed and rendering; check {d} conversion funnel.", dimension=f"ga_device={d}",
                             categories=["developer", "analytics"], evidence={"device_changes": dch}))
    return F


# ------------------------------------------------------------------ detectors: links
def detect_links(db, project, cfg, th, ctx) -> list[Finding]:
    F = []
    own = and_(mm.RefdomainSnapshot.project_id == project.id, mm.RefdomainSnapshot.target == "own")
    dates = [d for (d,) in db.execute(select(mm.RefdomainSnapshot.snapshot_date).where(own)
                                      .distinct().order_by(desc(mm.RefdomainSnapshot.snapshot_date)).limit(2))]
    if len(dates) == 2:
        new = set(db.scalars(select(mm.RefdomainSnapshot.domain).where(own, mm.RefdomainSnapshot.snapshot_date == dates[0])))
        old = set(db.scalars(select(mm.RefdomainSnapshot.domain).where(own, mm.RefdomainSnapshot.snapshot_date == dates[1])))
        lost, gained = sorted(old - new), sorted(new - old)
        ctx["lost_refdomains"], ctx["new_refdomains"] = lost, gained
        if len(lost) >= th["refdomain_loss_min"] and old and 100 * len(lost) / len(old) >= th["refdomain_loss_pct"]:
            F.append(Finding("refdomain_loss", "backlink", "high" if len(lost) / len(old) >= .15 else "medium",
                             f"{len(lost)} referring domains lost since {dates[1]} ({len(old)} → {len(new)})",
                             "Check each lost domain: was the link removed, the page deleted, or switched to nofollow? Contact the site owner "
                             "or supplier to reclaim paid links; replace links that can't be recovered.",
                             evidence={"lost": lost[:100], "gained": gained[:50], "from": str(dates[1]), "to": str(dates[0])}))
    for b in db.scalars(select(m.Backlink).where(m.Backlink.project_id == project.id, m.Backlink.status.in_(("removed", "changed", "noindex")))):
        F.append(Finding("backlink_lost", "backlink", "high" if b.paid_or_free == "paid" else "medium",
                         f"Backlink from {urlparse(b.source_url).netloc} is {b.status}", "Re-check the link and contact the supplier/site owner.",
                         url=b.source_url, evidence={"target": b.target_url, "status": b.status, "paid": b.paid_or_free}))
    return F


# ------------------------------------------------------------------ detectors: rank tracker
def detect_rank_tracker(db, project, cfg, th, ctx) -> list[Finding]:
    F = []
    for k in db.scalars(select(m.Keyword).where(m.Keyword.project_id == project.id)):
        h = db.scalars(select(m.RankHistory).where(m.RankHistory.keyword_id == k.id).order_by(desc(m.RankHistory.checked_at)).limit(2)).all()
        if len(h) < 2:
            continue
        new, old = h[0].position or 101, h[1].position or 101
        d = old - new
        if d <= -th["rank_drop_positions"]:
            F.append(Finding("tracked_keyword_down", "seo", "high" if old <= 10 else "medium",
                             f"Tracked keyword “{k.phrase}” dropped {old if old < 101 else 'unranked'} → {new if new < 101 else 'not ranking'}",
                             "Check the SERP, competitors and the target page for changes.", query=k.phrase, url=h[0].url or k.target_url,
                             categories=["seo", "content"], evidence={"from": old, "to": new, "source": h[0].source},
                             verify={"type": "metric", "metric": "tracked_position"}))
        elif d >= th["rank_up_positions"]:
            F.append(Finding("tracked_keyword_up", "seo", "low", f"Tracked keyword “{k.phrase}” up {old if old < 101 else 'unranked'} → {new}",
                             "Expand the winning page to related keywords.", query=k.phrase, evidence={"from": old, "to": new}, is_opportunity=True))
    return F


# ------------------------------------------------------------------ detectors: crawl / technical (+ change detection)
FIELDS = {"status": lambda p: p.get("status"), "title": lambda p: p.get("title"), "meta": lambda p: p.get("meta_description"),
          "h1": lambda p: " | ".join(p.get("h1") or []), "canonical": lambda p: p.get("canonical"),
          "indexable": lambda p: "noindex" if p.get("noindex") else "index", "robots": lambda p: p.get("meta_robots") or ""}


def _own_crawls(db, site: m.Site):
    host = urlparse(site.domain if "://" in site.domain else "https://" + site.domain).netloc
    runs = db.scalars(select(m.CrawlRun).where(m.CrawlRun.site_id == site.id, m.CrawlRun.status == "COMPLETED")
                      .order_by(desc(m.CrawlRun.finished_at)).limit(10)).all()
    return [r for r in runs if urlparse(r.start_url if "://" in r.start_url else "https://" + r.start_url).netloc == host]


def _pages(db, crawl_id) -> dict:
    return {p.url: (p.data or {}) for p in db.scalars(select(m.CrawlPage).where(m.CrawlPage.crawl_run_id == crawl_id))}


def detect_changes(db, project, site, cur, prev) -> list[mm.SiteChange]:
    """Diff two crawls of the same site; store each changed field once per crawl pair."""
    if db.scalar(select(func.count()).select_from(mm.SiteChange).where(mm.SiteChange.to_crawl_id == cur.id)):
        return []
    a, b = _pages(db, prev.id), _pages(db, cur.id)
    out = []
    for url, pb in b.items():
        pa = a.get(url)
        if pa is None:
            if prev.pages_crawled and len(a) >= 0.8 * len(b):  # only meaningful if crawls are comparable
                out.append(mm.SiteChange(organisation_id=project.organisation_id, project_id=project.id, site_id=site.id, url=url,
                                         field="page_added", new_value=str(pb.get("status")), from_crawl_id=prev.id, to_crawl_id=cur.id, changed_by="crawl"))
            continue
        for f, get in FIELDS.items():
            ov, nv = get(pa), get(pb)
            if (ov or "") != (nv or "") and not (f == "status" and ov is None):
                out.append(mm.SiteChange(organisation_id=project.organisation_id, project_id=project.id, site_id=site.id, url=url,
                                         field=f, old_value=None if ov is None else str(ov)[:500], new_value=None if nv is None else str(nv)[:500],
                                         from_crawl_id=prev.id, to_crawl_id=cur.id, changed_by="crawl"))
    if len(b) >= 0.8 * len(a):
        for url in set(a) - set(b):
            if a[url].get("status") == 200:
                out.append(mm.SiteChange(organisation_id=project.organisation_id, project_id=project.id, site_id=site.id, url=url,
                                         field="page_removed", old_value="200", from_crawl_id=prev.id, to_crawl_id=cur.id, changed_by="crawl"))
    for c in out:
        db.add(c)
    db.flush()
    return out


def detect_technical(db, project, cfg, th, w, ctx) -> list[Finding]:
    F = []
    imp = important_urls(db, project, cfg, w)
    for site in db.scalars(select(m.Site).where(m.Site.project_id == project.id)):
        crawls = _own_crawls(db, site)
        if not crawls:
            continue
        cur = crawls[0]
        ctx.setdefault("crawls", []).append({"site": site.domain, "crawl_id": str(cur.id), "at": cur.finished_at.isoformat() if cur.finished_at else None,
                                             "pages": cur.pages_crawled, "engine": cur.engine})
        changes = detect_changes(db, project, site, cur, crawls[1]) if len(crawls) > 1 else []
        ch_by_url = defaultdict(list)
        for c in changes:
            ch_by_url[c.url].append(c)
        pages = _pages(db, cur.id)
        for url, p in pages.items():
            st = p.get("status")
            important = url in imp or (p.get("inlinks") or 0) >= 3 or p.get("source") == "sitemap" or (p.get("depth") in (0, 1))
            new_change = {c.field: c for c in ch_by_url.get(url, [])}
            if st and st >= 500:
                F.append(Finding("http_5xx", "developer", "critical", f"{path_of(url)} returns HTTP {st}",
                                 "Server error: check application logs for this route, recent deploys and upstream dependencies.",
                                 url=url, evidence={"status": st, "crawl": str(cur.id)},
                                 verify={"type": "url_check", "expect": {"status": 200}}))
            elif st == 404 and (important or "status" in new_change):
                F.append(Finding("http_404", "developer", "critical" if url in imp else "high", f"{path_of(url)} returns 404",
                                 "Restore the page or 301-redirect it to the closest equivalent, and update internal links pointing to it.",
                                 url=url, categories=["developer", "seo"], evidence={"status": st, "was": new_change.get("status").old_value if "status" in new_change else None,
                                                                                    "had_traffic": url in imp},
                                 verify={"type": "url_check", "expect": {"status_in": [200, 301, 308]}}))
            if st == 200 and p.get("noindex") and (important or "indexable" in new_change):
                F.append(Finding("noindex_important", "developer", "critical", f"noindex on {path_of(url)} (expected index, follow)",
                                 "Remove the noindex directive (meta robots or X-Robots-Tag) from this page, then request indexing in "
                                 "Search Console.", url=url, categories=["developer", "seo"],
                                 evidence={"expected": "index, follow", "detected": p.get("meta_robots") or "noindex",
                                           "changed_in_crawl": "indexable" in new_change, "had_traffic": url in imp},
                                 task_notes={"seo": "Request re-indexing once the developer has removed noindex; watch impressions recover."},
                                 verify={"type": "url_check", "expect": {"indexable": True}}))
            if "canonical" in new_change and st == 200 and p.get("canonical") and p.get("canonical") != url:
                c = new_change["canonical"]
                F.append(Finding("canonical_changed", "developer", "high" if important else "medium",
                                 f"Canonical on {path_of(url)} now points elsewhere", f"Canonical changed from {c.old_value} to {c.new_value}. "
                                 "Confirm this was intended; otherwise restore the self-referencing canonical.", url=url,
                                 categories=["developer", "seo"], evidence={"old": c.old_value, "new": c.new_value},
                                 verify={"type": "url_check", "expect": {"canonical_self": True}}))
            if st == 200 and important and not (p.get("title") or "").strip():
                F.append(Finding("missing_title", "seo", "medium", f"{path_of(url)} has no <title>", "Add a unique descriptive title.",
                                 url=url, verify={"type": "url_check", "expect": {"title": True}}))
            if st == 200 and important and not p.get("h1"):
                F.append(Finding("missing_h1", "seo", "low", f"{path_of(url)} has no H1", "Add one H1 stating the page topic.", url=url,
                                 verify={"type": "url_check", "expect": {"h1": True}}))
            if p.get("chain") and len(p["chain"]) > 1:
                F.append(Finding("redirect_chain", "developer", "medium", f"Redirect chain on {path_of(url)} ({len(p['chain'])} hops)",
                                 "Point the first URL straight at the final destination and update internal links.", url=url,
                                 evidence={"chain": p["chain"]}, verify={"type": "url_check", "expect": {"max_hops": 1}}))
        # removed pages with traffic
        for c in changes:
            if c.field == "page_removed" and c.url in imp:
                F.append(Finding("page_removed", "developer", "high", f"{path_of(c.url)} disappeared from the crawl",
                                 "The page had organic traffic and is no longer linked or reachable. Check whether it was deleted, unlinked "
                                 "or redirected.", url=c.url, categories=["developer", "seo"], evidence={"crawl": str(cur.id)}))
    return F


def impact_analysis(db, project) -> list[Finding]:
    """For changes ≥7 days old, compare the page's clicks in the 7 days before vs after the change."""
    F = []
    cutoff = utcnow() - timedelta(days=7)
    for c in db.scalars(select(mm.SiteChange).where(mm.SiteChange.project_id == project.id, mm.SiteChange.detected_at <= cutoff,
                                                    mm.SiteChange.field.in_(("title", "meta", "h1", "canonical", "indexable", "status", "robots")))):
        if c.impact:
            continue
        d0 = c.detected_at.date()
        before = search_agg(db, project.id, (), (d0 - timedelta(days=7), d0 - timedelta(days=1)), ("page", "detail"), mm.SearchMetric.page == c.url).get((), {"clicks": 0})
        after = search_agg(db, project.id, (), (d0, d0 + timedelta(days=6)), ("page", "detail"), mm.SearchMetric.page == c.url).get((), {"clicks": 0})
        ch = pct(after["clicks"], before["clicks"])
        verdict = "positive" if ch is not None and ch >= 15 else "negative" if ch is not None and ch <= -15 else "neutral"
        c.impact = {"clicks_before": before["clicks"], "clicks_after": after["clicks"], "change_pct": ch, "verdict": verdict}
        if verdict == "negative" and before["clicks"] >= 10:
            F.append(Finding("change_negative_impact", "seo", "high", f"{c.field} change on {path_of(c.url)} was followed by a {abs(ch)}% click drop",
                             f"“{c.old_value}” → “{c.new_value}”. Consider reverting the change or testing an alternative.",
                             url=c.url, dimension=f"change={c.id}", categories=["seo", "content"], evidence=c.impact))
    return F


# ------------------------------------------------------------------ issue lifecycle
def team_members(db, project_id, team) -> list[uuid.UUID]:
    return [x for (x,) in db.execute(select(mm.ProjectMember.user_id).join(m.User, m.User.id == mm.ProjectMember.user_id)
                                     .where(mm.ProjectMember.project_id == project_id, mm.ProjectMember.team == team, m.User.is_active))]


def fallback_assignees(db, project_id) -> list:
    pm = team_members(db, project_id, "pm")
    if pm:
        return pm
    return [u for (u,) in db.execute(select(m.User.id).where(m.User.role.in_(("owner", "admin", "manager")), m.User.is_active))]


TEAM_LABEL = {"developer": "Developer", "seo": "SEO", "content": "Content", "backlink": "Backlink", "analytics": "Analytics", "pm": "Project manager"}
PRIORITY = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


def _create_tasks(db, project, issue: mm.MonitorIssue, f: Finding, cfg) -> list:
    """One task per team involved, each with the fix steps for that team, who is responsible / accountable /
    consulted, the deadline and the prevention strategy. Assignees get an in-app notification and an email."""
    from . import mailer, playbooks
    routing = {**ROUTING, **(cfg.routing or {})}
    cats = f.categories or [f.category]
    fix = playbooks.resolve(db, project, f.kind, issue.severity, cats, cfg.routing)
    rc = dict(f.root_cause or {})
    rc.setdefault("what", f.title)
    rc.setdefault("why", f.recommendation)
    rc.setdefault("who", ", ".join(TEAM_LABEL.get(c, c) for c in cats))
    rc["fix"] = fix
    issue.root_cause = _json(rc)
    ids = []
    for cat in cats:
        team = routing.get(cat, cat)
        assignees = team_members(db, project.id, team)
        unassigned = not assignees
        if unassigned:
            assignees = fallback_assignees(db, project.id)
        note = f.task_notes.get(cat) or f.recommendation
        desc_lines = [note, "", f"Why: {rc.get('why')}" if rc.get("why") and rc.get("why") != note else "",
                      f"Where: {rc.get('where')}" if rc.get("where") else "",
                      f"URL: {f.url}" if f.url else "", f"Query: {f.query}" if f.query else "",
                      "", playbooks.task_text(fix, cat if cat in fix["steps"] else team), "",
                      f"How we verify: {rc.get('verify') or _verify_text(f.verify)}",
                      "(No one is on this project's {} team — assigned to the project manager.)".format(TEAM_LABEL.get(team, team)) if unassigned else ""]
        t = m.Task(organisation_id=project.organisation_id, project_id=project.id,
                   title=f"[{TEAM_LABEL.get(team, team)}] {f.title}"[:300], description="\n".join(x for x in desc_lines if x is not None).strip(),
                   priority=PRIORITY[issue.severity], assignee_ids=assignees, source=f"monitor_issue:{issue.id}",
                   labels=["monitor", team, issue.severity] + (["opportunity"] if f.is_opportunity else []) + (["backlog"] if issue.severity == "low" else []),
                   due_date=utcnow() + timedelta(days=playbooks.SLA_DAYS[issue.severity]))
        db.add(t)
        db.flush()
        ids.append(t.id)
        if not f.is_opportunity or issue.severity in ("critical", "high"):
            for u in assignees:
                mailer.task_email(db, t, u)
    return ids


def _verify_text(v):
    if not v:
        return "the condition is no longer detected on the next monitoring run"
    if v.get("type") == "url_check":
        return "the URL is re-fetched automatically when the task is completed: " + ", ".join(f"{k}={v2}" for k, v2 in v.get("expect", {}).items())
    return f"the metric ({v.get('metric')}) recovers on the next monitoring runs"


def _notify(db, project, issue: mm.MonitorIssue, f: Finding, cfg, event="new"):
    """Severity policy: critical → team + PM immediately (+ chat channels); high → team; medium/low → task only."""
    if issue.severity in ("medium", "low") and event == "new":
        return []
    routing = {**ROUTING, **(cfg.routing or {})}
    users = set()
    for cat in (f.categories or [f.category]) if f else (issue.categories or [issue.category]):
        users |= set(team_members(db, project.id, routing.get(cat, cat)))
    if issue.severity == "critical" or not users:
        users |= set(fallback_assignees(db, project.id))
    verb = {"new": "", "reopened": "Reopened — ", "resolved": "Resolved — ", "verifying": "Verifying fix — "}[event]
    title = f"{project.name} — {verb}{issue.severity.title()} {TEAM_LABEL.get(issue.category, issue.category)} issue: {issue.title}"[:300]
    body = (issue.root_cause or {}).get("why") or issue.recommendation
    for u in users:
        db.add(m.Notification(organisation_id=project.organisation_id, user_id=u, kind=f"monitor_{event}", title=title,
                              body=(body or "")[:500], link=f"/issues?id={issue.id}"))
    sent = []
    if issue.severity in ("critical", "high") or event in ("reopened",):
        sent = send_channels(cfg, title, f"{body or ''}\nURL: {issue.url or '-'}\nAssigned: {', '.join(TEAM_LABEL.get(c, c) for c in issue.categories or [issue.category])}")
    return sent


ALLOWED_HOOKS = (r"^https://hooks\.slack\.com/services/", r"^https://[a-z0-9.-]+\.webhook\.office\.com/", r"^https://outlook\.office\.com/webhook/")


def valid_hook(url: str) -> bool:
    return any(re.match(p, url or "") for p in ALLOWED_HOOKS)


def send_channels(cfg, title, text) -> list:
    from .integrations import http_call
    sent = []
    ch = cfg.channels or {}
    for key in ("slack_webhook", "teams_webhook"):
        url = ch.get(key)
        if url and valid_hook(url):
            try:
                payload = {"text": f"*{title}*\n{text}"} if key == "slack_webhook" else {"text": f"**{title}**\n\n{text}"}
                r = http_call("POST", url, json=payload, retries=1, timeout=10)
                sent.append({key: r.status_code})
            except Exception as e:  # never fail monitoring because chat delivery failed
                sent.append({key: f"error: {e}"[:120]})
    return sent


def process_findings(db, project, cfg, findings: list[Finding], ran: set[str]) -> dict:
    """Upsert issues by fingerprint; create tasks + notify for new/reopened; auto-resolve recovered ones."""
    stats = defaultdict(int)
    sev_over = cfg.severity_overrides or {}
    th = thresholds(cfg)
    seen = set()
    opp_tasks = 0
    findings.sort(key=lambda f: (SEVERITY_ORDER[sev_over.get(f.kind, f.severity)], f.is_opportunity))
    for f in findings:
        f.severity = sev_over.get(f.kind, f.severity)
        if f.fingerprint in seen:
            continue
        seen.add(f.fingerprint)
        if not f.categories:
            f.categories = [f.category]
        issue = db.scalar(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id == project.id, mm.MonitorIssue.fingerprint == f.fingerprint)
                          .order_by(desc(mm.MonitorIssue.first_seen)).limit(1))
        now = utcnow()
        if issue and issue.status in ("open", "in_progress", "reopened"):
            issue.last_seen, issue.occurrences, issue.evidence = now, issue.occurrences + 1, f.evidence
            if f.root_cause:
                issue.root_cause = f.root_cause
            stats["ongoing"] += 1
            continue
        if issue and issue.status == "ignored":
            continue
        if issue and issue.status == "verifying":
            if issue.verify.get("type") == "url_check":
                continue  # the verify_issue job re-fetches the URL itself; older crawl data must not reopen it
            since = datetime.fromisoformat(issue.verify.get("since")) if issue.verify.get("since") else now
            if now - since >= timedelta(days=th["verify_window_days"]):
                _reopen(db, project, issue, cfg, "Still detected after the fix was marked done")
                stats["reopened"] += 1
            continue
        if issue and issue.status == "resolved":
            _reopen(db, project, issue, cfg, "Detected again after it was resolved")
            issue.evidence = f.evidence
            stats["reopened"] += 1
            continue
        issue = mm.MonitorIssue(organisation_id=project.organisation_id, project_id=project.id, kind=f.kind, category=f.category,
                                categories=f.categories, severity=f.severity, is_opportunity=f.is_opportunity, title=f.title[:500],
                                url=f.url, query=f.query, dimension=f.dimension, evidence=_json(f.evidence), root_cause=_json(f.root_cause),
                                recommendation=f.recommendation, verify=f.verify, fingerprint=f.fingerprint,
                                history=[{"at": now.isoformat(), "event": "detected"}])
        db.add(issue)
        db.flush()
        if not f.is_opportunity or opp_tasks < th["max_opportunity_tasks"]:
            issue.task_ids = _create_tasks(db, project, issue, f, cfg)
            opp_tasks += 1 if f.is_opportunity else 0
        _notify(db, project, issue, f, cfg)
        stats["new"] += 1
        stats[f"new_{f.severity}"] += 1
    # auto-resolve issues whose detector ran and no longer fires
    for issue in db.scalars(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id == project.id,
                                                          mm.MonitorIssue.status.in_(("open", "in_progress", "reopened", "verifying")))):
        if issue.fingerprint in seen or issue.kind not in ran:
            continue
        issue.status, issue.resolved_at = "resolved", utcnow()
        issue.history = issue.history + [{"at": utcnow().isoformat(), "event": "resolved", "note": "No longer detected in the latest data"}]
        _close_tasks(db, issue)
        _notify(db, project, issue, None, cfg, "resolved")
        stats["resolved"] += 1
    return dict(stats)


def _json(x):
    import json
    return json.loads(json.dumps(x, default=str))


def _close_tasks(db, issue):
    for tid in issue.task_ids or []:
        t = db.get(m.Task, tid)
        if t and t.status != "done":
            t.status = "done"


def _reopen(db, project, issue, cfg, note):
    issue.status, issue.resolved_at, issue.last_seen = "reopened", None, utcnow()
    issue.history = issue.history + [{"at": utcnow().isoformat(), "event": "reopened", "note": note}]
    from . import mailer
    for tid in issue.task_ids or []:
        t = db.get(m.Task, tid)
        if t:
            t.status = "todo"
            t.description = (t.description or "") + f"\n\nReopened {utcnow():%Y-%m-%d}: {note}."
            for u in t.assignee_ids or []:
                mailer.task_email(db, t, u, reason="reopened")
    _notify(db, project, issue, None, cfg, "reopened")


KIND_SOURCES = {
    "search": {"traffic_drop", "traffic_spike", "device_drop", "country_drop", "page_traffic_drop", "ctr_drop", "keyword_down", "keyword_up",
               "new_query", "lost_query", "opp_striking_distance", "opp_low_ctr"},
    "analytics": {"tracking_stopped", "conversion_tracking", "sessions_drop", "conversions_drop", "landing_sessions_drop", "device_sessions_drop"},
    "links": {"refdomain_loss", "backlink_lost"},
    "rank": {"tracked_keyword_down", "tracked_keyword_up"},
    "technical": {"http_5xx", "http_404", "noindex_important", "canonical_changed", "missing_title", "missing_h1", "redirect_chain", "page_removed"},
}


# ------------------------------------------------------------------ task status → issue lifecycle
def on_task_status(db, task: m.Task, old_status: str | None) -> str | None:
    """Called when a task changes status. When every task of a monitor issue is done the issue moves to
    'verifying': URL issues are re-fetched straight away, metric issues are confirmed by the next runs."""
    from .jobs import enqueue
    if not (task.source or "").startswith("monitor_issue:") or task.status == old_status:
        return None
    try:
        issue = db.get(mm.MonitorIssue, uuid.UUID(task.source.split(":", 1)[1]))
    except ValueError:
        return None
    if not issue or issue.status in ("resolved", "ignored"):
        return None
    now = utcnow()
    if task.status in ("in_progress", "review") and issue.status in ("open", "reopened"):
        issue.status = "in_progress"
        issue.history = issue.history + [{"at": now.isoformat(), "event": "in_progress"}]
        return issue.status
    if task.status == "done":
        others = [db.get(m.Task, t) for t in issue.task_ids or [] if t != task.id]
        if any(o and o.status != "done" for o in others):
            return issue.status
        issue.status = "verifying"
        issue.verify = {**(issue.verify or {}), "since": now.isoformat()}
        issue.history = issue.history + [{"at": now.isoformat(), "event": "fix_marked_done", "task": str(task.id)}]
        if (issue.verify or {}).get("type") == "url_check" and issue.url:
            proj = db.get(m.Project, issue.project_id)
            delay = thresholds(get_config(db, proj))["verify_delay_sec"] if proj else 120
            enqueue(db, issue.organisation_id, "verify_issue", {"issue_id": str(issue.id)}, run_at=now + timedelta(seconds=max(0, int(delay))))
        return issue.status
    if old_status == "done" and issue.status == "verifying":
        issue.status = "in_progress"
        issue.history = issue.history + [{"at": now.isoformat(), "event": "task_reopened_by_user"}]
    return issue.status


# ------------------------------------------------------------------ verification of a fixed URL issue
def verify_url_issue(db, project, issue: mm.MonitorIssue, cfg) -> str:
    from .crawler import Crawler
    exp = (issue.verify or {}).get("expect", {})
    cr = Crawler(issue.url, max_pages=1, render_sample=0, mobile_sample=0, external_check_limit=0)
    resp = cr.fetch(issue.url)
    p = cr.parse(issue.url, resp)
    ok = True
    reasons = []
    if "status" in exp and p.get("status") != exp["status"]:
        ok, _ = False, reasons.append(f"status is {p.get('status')}")
    if "status_in" in exp and p.get("status") not in exp["status_in"] and not (p.get("chain") and p["chain"][0]["status"] in exp["status_in"]):
        ok, _ = False, reasons.append(f"status is {p.get('status')}")
    if exp.get("indexable") and p.get("noindex"):
        ok, _ = False, reasons.append("noindex still present")
    if exp.get("canonical_self") and p.get("canonical") and p.get("canonical") != issue.url:
        ok, _ = False, reasons.append(f"canonical still {p.get('canonical')}")
    if exp.get("title") and not (p.get("title") or "").strip():
        ok, _ = False, reasons.append("title still missing")
    if exp.get("h1") and not p.get("h1"):
        ok, _ = False, reasons.append("H1 still missing")
    if "max_hops" in exp and len(p.get("chain") or []) > exp["max_hops"]:
        ok, _ = False, reasons.append(f"{len(p['chain'])} redirect hops")
    snap = {"status": p.get("status"), "noindex": p.get("noindex"), "canonical": p.get("canonical"), "title": p.get("title"),
            "checked_at": utcnow().isoformat()}
    if ok:
        issue.status, issue.resolved_at = "resolved", utcnow()
        issue.history = issue.history + [{"at": utcnow().isoformat(), "event": "verified", "after": snap}]
        _notify(db, project, issue, None, cfg, "resolved")
        return "resolved"
    issue.history = issue.history + [{"at": utcnow().isoformat(), "event": "verification_failed", "reasons": reasons, "after": snap}]
    _reopen(db, project, issue, cfg, "Verification failed: " + "; ".join(reasons))
    return "reopened"


# ------------------------------------------------------------------ briefing
def briefing(project, ctx, findings: list[Finding]) -> str:
    parts = []
    s = ctx.get("search")
    if s and s["prev"]["clicks"]:
        ch = pct(s["cur"]["clicks"], s["prev"]["clicks"])
        parts.append(f"Organic clicks {('+' if ch >= 0 else '')}{ch}% week over week ({s['prev']['clicks']} → {s['cur']['clicks']}).")
    drops = ctx.get("page_drops") or []
    if drops and s and s["prev"]["clicks"] > s["cur"]["clicks"]:
        lost = s["prev"]["clicks"] - s["cur"]["clicks"]
        share = round(100 * sum(d["lost"] for d in drops) / lost) if lost else 0
        parts.append(f"{min(share, 100)}% of the decline comes from {len(drops)} landing page(s).")
    dc = ctx.get("device_changes") or {}
    if dc:
        worst = min(dc.items(), key=lambda kv: kv[1] if kv[1] is not None else 0)
        if worst[1] is not None and worst[1] < -15:
            parts.append(f"{worst[0].title()} clicks {worst[1]}%.")
    falls = [q for q in ctx.get("query_moves", []) if q["delta"] <= -5]
    if falls:
        parts.append(f"{len(falls)} keyword(s) dropped more than five positions.")
    for kind, label in (("noindex_important", "page(s) have a noindex directive"), ("http_5xx", "page(s) return server errors"),
                        ("http_404", "important page(s) return 404")):
        n = sum(1 for f in findings if f.kind == kind)
        if n:
            parts.append(f"{n} {label}.")
    if ctx.get("lost_refdomains"):
        parts.append(f"{len(ctx['lost_refdomains'])} referring domains were lost.")
    crit = [f for f in findings if f.severity == "critical"]
    high = [f for f in findings if f.severity == "high"]
    if crit or high:
        parts.append(f"{len(crit)} critical and {len(high)} high-priority issue(s) were routed to "
                     + ", ".join(sorted({TEAM_LABEL.get(c, c) for f in crit + high for c in (f.categories or [f.category])})) + ".")
    opps = [f for f in findings if f.is_opportunity]
    if opps:
        parts.append(f"{len(opps)} opportunities found (striking-distance keywords, new queries, low-CTR results).")
    return f"{project.name}: " + (" ".join(parts) if parts else "No significant changes detected.")


# ------------------------------------------------------------------ main entry
def run_monitor(db, project: m.Project) -> dict:
    cfg = get_config(db, project)
    th = thresholds(cfg)
    run = mm.MonitorRun(organisation_id=project.organisation_id, project_id=project.id)
    db.add(run)
    db.flush()
    ctx: dict = {}
    findings: list[Finding] = []
    ran: set[str] = set()
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    w = Windows(anchor) if anchor else None
    findings += detect_links(db, project, cfg, th, ctx)  # first: lost refdomains feed the traffic root cause
    if db.scalar(select(func.count()).select_from(mm.RefdomainSnapshot).where(mm.RefdomainSnapshot.project_id == project.id)) or \
            db.scalar(select(func.count()).select_from(m.Backlink).where(m.Backlink.project_id == project.id)):
        ran |= KIND_SOURCES["links"]
    findings += detect_technical(db, project, cfg, th, w, ctx)
    if ctx.get("crawls"):
        ran |= KIND_SOURCES["technical"]
    if w:
        findings += detect_search(db, project, cfg, th, w, ctx)
        findings += impact_analysis(db, project)
        ran |= KIND_SOURCES["search"]
    a = detect_analytics(db, project, cfg, th, ctx)
    findings += a
    if ctx.get("ga_anchor"):
        ran |= KIND_SOURCES["analytics"]
    findings += detect_rank_tracker(db, project, cfg, th, ctx)
    ran |= KIND_SOURCES["rank"]
    from . import workstreams  # content watch checkpoints live with content workflows
    findings += workstreams.check_content_watch(db, project, th, ctx)
    ran.add("blog_underperforming")
    stats = process_findings(db, project, cfg, findings, ran)
    run.finished_at = utcnow()
    run.stats = _json({**stats, "findings": len(findings), "anchor": anchor, "sources_ran": sorted({k for k, v in KIND_SOURCES.items() if v & ran}),
                       "kpis": {"search": ctx.get("search"), "analytics": ctx.get("analytics"), "device_changes": ctx.get("device_changes")}})
    run.summary = briefing(project, ctx, findings)
    if stats.get("new_critical") or stats.get("new_high"):
        for u in fallback_assignees(db, project.id):
            db.add(m.Notification(organisation_id=project.organisation_id, user_id=u, kind="monitor_briefing",
                                  title=f"Monitoring briefing — {project.name}", body=run.summary[:1000], link=f"/projects/{project.id}/monitor"))
    return {"run_id": str(run.id), **run.stats, "summary": run.summary}

