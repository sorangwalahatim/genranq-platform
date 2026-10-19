"""Project workstreams: SEO strategies → activities → who does the work (person, team or AI agent).

* Built-in strategy templates (Foundation, Topical authority, Content refresh, Skyscraper, Local,
  AI visibility AIO/AEO/GEO/SXO, Link building, Social media, Paid social) — each is a list of
  activities across workstreams (on-page, technical, off-page, content, AIO, AEO, GEO, SXO, local,
  analytics, social, paid social).
* A project applies one or more strategies and customises every activity: cadence, assignee
  (a single user, several users, a team, or an AI agent), approval, instructions, config.
* The activity scheduler runs due activities: AI agents execute and attach their output to a
  review task; human/team activities become tasks. next_due advances by cadence.
* Content watch: every new blog/article is checked at 7/14/30/60/90 days. If it underperforms,
  an issue is raised for the content team and keyword/content/structure gap analysis runs
  automatically (GapReport attached to the task).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from .. import models as m
from .. import models_monitor as mm
from .ingest import path_of

WORKSTREAM_LABEL = {
    "strategy": "Strategy", "on_page": "On-page SEO", "technical": "Technical SEO", "off_page": "Off-page SEO",
    "content": "Content", "aio": "AIO (AI Overviews)", "aeo": "AEO (answer engines)", "geo": "GEO (generative engines)",
    "sxo": "SXO (search experience)", "local": "Local SEO", "analytics": "Analytics & reporting", "social": "Social media",
    "paid_social": "Paid social (Meta)",
}
CADENCE = {"once": None, "daily": timedelta(days=1), "weekly": timedelta(weeks=1), "biweekly": timedelta(weeks=2),
           "monthly": timedelta(days=30), "quarterly": timedelta(days=91)}


def utcnow():
    return datetime.now(timezone.utc)


def act(key, ws, title, cadence="monthly", who="ai_agent", agent=None, team=None, instructions=None, approval=True):
    return {"key": key, "workstream": ws, "title": title, "cadence": cadence, "assignee_type": who,
            "agent_key": agent if who == "ai_agent" else agent, "team": team or _team_for(ws),
            "instructions": instructions, "approval_required": approval}


def _team_for(ws):
    return {"technical": "developer", "content": "content", "off_page": "backlink", "analytics": "analytics",
            "social": "content", "paid_social": "content"}.get(ws, "seo")


STRATEGIES = [
    {"key": "foundation", "name": "Technical foundation", "description": "Crawlability, indexation, speed and on-page hygiene — the base every other strategy needs.",
     "activities": [
         act("tech_indexation", "technical", "Indexation audit", "weekly", agent="indexation_audit"),
         act("tech_redirects", "technical", "Redirect chains & broken links", "weekly", agent="redirect_chains"),
         act("tech_canonicals", "technical", "Canonical review", "monthly", agent="canonicals"),
         act("tech_robots", "technical", "Robots.txt & sitemap check", "weekly", agent="robots_sitemap"),
         act("tech_cwv", "technical", "Core Web Vitals review", "monthly", agent="core_web_vitals"),
         act("tech_schema", "technical", "Structured data inventory", "monthly", agent="structured_data"),
         act("tech_js", "technical", "JavaScript rendering check", "monthly", agent="javascript_rendering"),
         act("onpage_titles", "on_page", "Titles & meta descriptions", "monthly", agent="titles_metas"),
         act("onpage_thin", "on_page", "Thin content review", "monthly", agent="thin_content"),
         act("onpage_internal", "on_page", "Internal link suggestions", "monthly", agent="internal_links"),
         act("onpage_orphans", "technical", "Orphan pages", "monthly", agent="orphan_pages"),
         act("monitor", "analytics", "Search Console & GA4 anomaly monitoring", "daily", agent="gsc_anomalies", approval=False),
     ]},
    {"key": "topical_authority", "name": "Topical authority", "description": "Own a topic: clusters, pillar pages, briefs and articles from real queries.",
     "activities": [
         act("kw_mining", "on_page", "Keyword mining from Search Console", "monthly", agent="keyword_mining"),
         act("kw_intent", "on_page", "Intent-based keyword research", "monthly", agent="skill:advanced_keyword_discovery"),
         act("topical_map", "content", "Topical map & clusters", "quarterly", agent="topical_map"),
         act("blog_ideas", "content", "Blog titles from search queries", "weekly", agent="blog_ideas"),
         act("content_brief", "content", "Content briefs for new topics", "weekly", agent="content_brief"),
         act("article_drafting", "content", "Article drafting (AI, human review)", "weekly", agent="article_drafting"),
         act("semantic_seo", "content", "Semantic SEO map (entities, PASF, clusters)", "quarterly", agent="skill:semantic_seo"),
         act("plan_90", "content", "90-day content plan", "quarterly", agent="skill:content_plan_90"),
         act("cannibal", "on_page", "Keyword cannibalisation check", "monthly", agent="cannibalization"),
     ]},
    {"key": "content_refresh", "name": "Content refresh", "description": "Recover decaying pages and lift low-CTR results.",
     "activities": [
         act("refresh_finder", "content", "Pages losing clicks", "monthly", agent="content_refresh"),
         act("ctr_rewrites", "on_page", "Low-CTR title rewrites", "monthly", agent="titles_metas"),
         act("meta_gap", "on_page", "Meta title & description gap analysis", "quarterly", agent="skill:meta_gap_analysis"),
         act("usp_heading_gap", "content", "USP & heading-structure gap analysis", "quarterly", agent="skill:usp_heading_gap"),
         act("eeat_review", "content", "E-E-A-T review of top pages", "quarterly", agent="eeat"),
         act("slop_check", "content", "AI-slop check on recent content", "monthly", agent="ai_slop_detector"),
     ]},
    {"key": "skyscraper", "name": "Competitor skyscraper", "description": "Beat the pages that outrank you: competitor analysis, gaps and better pages.",
     "activities": [
         act("comp_analysis", "content", "Competitor page analysis", "quarterly", agent="skill:competitor_page_analysis"),
         act("kw_gap", "content", "Keyword gap vs competitors", "monthly", agent="keyword_gap"),
         act("seo_llm_gap", "geo", "SEO & LLM gap analysis", "quarterly", agent="skill:seo_llm_gap"),
         act("bl_gap", "off_page", "Backlink gap", "monthly", agent="backlink_gap"),
     ]},
    {"key": "local", "name": "Local SEO", "description": "Local pack visibility: GBP, NAP consistency, local pages and reviews.",
     "activities": [
         act("gbp_audit", "local", "Google Business Profile audit", "monthly", agent="google_business_profile"),
         act("nap", "local", "NAP consistency", "monthly", agent="nap_citations"),
         act("gbp_posts", "local", "GBP posts", "weekly", who="team", team="content"),
         act("reviews", "local", "Review responses", "weekly", who="team", team="seo"),
     ]},
    {"key": "ai_visibility", "name": "AI visibility (AIO · AEO · GEO · SXO)", "description": "Be cited by AI Overviews and answer engines and give searchers a better experience.",
     "activities": [
         act("ai_citations", "geo", "AI answer-engine citation check", "monthly", agent="chatgpt_citations"),
         act("geo_dataset", "geo", "GEO / AI-search query dataset", "quarterly", agent="skill:geo_query_dataset"),
         act("quotable", "aeo", "Quotable answers & Q&A structure", "monthly", agent="quotable_answers"),
         act("faq", "aeo", "FAQ blocks from question queries", "monthly", agent="faq_blocks"),
         act("aio_tracking", "aio", "AI Overviews tracking", "weekly", agent="ai_overviews"),
         act("sxo", "sxo", "SXO landing-page review", "monthly", agent="sxo_review"),
         act("index_it", "technical", "Submit new/updated URLs for indexing", "weekly", agent="index_it"),
     ]},
    {"key": "link_building", "name": "Link building", "description": "Earn authority: prospects, outreach, digital PR and link hygiene.",
     "activities": [
         act("prospects", "off_page", "Link prospecting", "monthly", agent="link_prospecting"),
         act("outreach", "off_page", "Outreach", "weekly", who="team", team="backlink"),
         act("pr", "off_page", "Digital PR angles", "monthly", agent="digital_pr"),
         act("toxic", "off_page", "Toxic link review", "monthly", agent="toxic_links"),
         act("eeat_titles", "content", "E-E-A-T white paper & case study titles", "quarterly", agent="skill:eeat_titles"),
     ]},
    {"key": "brand_strategy", "name": "Brand & audience research", "description": "ICP, domain vocabulary and tone of voice — the inputs every content decision uses.",
     "activities": [
         act("icp", "strategy", "ICP deep dive", "quarterly", agent="skill:icp_deep_dive"),
         act("domain_terms", "strategy", "Domain terms extraction", "quarterly", agent="skill:domain_terms"),
         act("tone", "content", "Tone audit & style guide", "quarterly", agent="skill:tone_style_guide"),
         act("strategy_plan", "strategy", "Quarterly SEO plan", "quarterly", agent="seo_strategy"),
     ]},
    {"key": "social_media", "name": "Social media", "description": "Turn content into a posting calendar and platform-specific drafts.",
     "activities": [
         act("social_calendar", "social", "Weekly social calendar", "weekly", agent="social_calendar"),
         act("social_posts", "social", "Post drafts per platform", "weekly", agent="social_posts"),
         act("social_publish", "social", "Publish & engage", "weekly", who="team", team="content"),
         act("social_report", "social", "Social performance review", "monthly", who="team", team="analytics"),
     ]},
    {"key": "paid_social", "name": "Paid social (Meta)", "description": "Meta ads audits, creative and landing-page quality, budget and testing.",
     "activities": [
         act("meta_landing", "paid_social", "Ad landing-page quality", "monthly", agent="sxo_review"),
         act("meta_audit", "paid_social", "Meta ads account audit", "monthly", who="team", team="content",
             instructions="Needs the Meta Ads API connected; until then run the audit manually in Ads Manager."),
         act("meta_tests", "paid_social", "A/B test design & read-out", "biweekly", who="team", team="analytics"),
     ]},
    {"key": "reporting", "name": "Reporting", "description": "Client-ready weekly reporting and forecasting.",
     "activities": [
         act("weekly_report", "analytics", "Weekly client report", "weekly", agent="client_reporting"),
         act("forecast", "analytics", "Traffic forecast", "monthly", agent="traffic_forecast"),
         act("prioritise", "strategy", "Re-prioritise backlog", "weekly", agent="prioritization"),
     ]},
]
STRATEGY_BY_KEY = {s["key"]: s for s in STRATEGIES}


def templates(db) -> list[dict]:
    out = [{**s, "builtin": True} for s in STRATEGIES]
    for t in db.scalars(select(mm.StrategyTemplate).where(mm.StrategyTemplate.organisation_id.is_not(None))):
        out.append({"key": t.key, "name": t.name, "description": t.description, "activities": t.activities, "builtin": False, "id": str(t.id)})
    return out


def apply_strategy(db, project: m.Project, key: str, overrides: dict | None = None, start: date | None = None) -> dict:
    """Add the strategy's activities to the project. Existing activity keys are kept (customisations are never overwritten)."""
    tpl = STRATEGY_BY_KEY.get(key)
    if not tpl:
        t = db.scalar(select(mm.StrategyTemplate).where(mm.StrategyTemplate.key == key))
        if not t:
            raise KeyError(key)
        tpl = {"key": t.key, "name": t.name, "activities": t.activities}
    overrides = overrides or {}
    ps = db.scalar(select(mm.ProjectStrategy).where(mm.ProjectStrategy.project_id == project.id, mm.ProjectStrategy.template_key == tpl["key"]))
    if not ps:
        ps = mm.ProjectStrategy(organisation_id=project.organisation_id, project_id=project.id, template_key=tpl["key"], name=tpl["name"],
                                goals=overrides.get("goals") or {})
        db.add(ps)
        db.flush()
    existing = {a.key for a in db.scalars(select(mm.ProjectActivity).where(mm.ProjectActivity.project_id == project.id))}
    added = []
    start = start or date.today()
    for a in tpl["activities"]:
        if a["key"] in existing:
            continue
        o = (overrides.get("activities") or {}).get(a["key"], {})
        pa = mm.ProjectActivity(organisation_id=project.organisation_id, project_id=project.id, strategy_id=ps.id,
                                workstream=a["workstream"], key=a["key"], title=o.get("title", a["title"]),
                                instructions=o.get("instructions", a.get("instructions")), cadence=o.get("cadence", a["cadence"]),
                                assignee_type=o.get("assignee_type", a["assignee_type"]), team=o.get("team", a["team"]),
                                agent_key=o.get("agent_key", a.get("agent_key")), approval_required=o.get("approval_required", a["approval_required"]),
                                assignee_user_ids=o.get("assignee_user_ids", []), next_due=start, config=o.get("config", {}))
        db.add(pa)
        added.append(a["key"])
    db.flush()
    return {"strategy_id": str(ps.id), "added": added, "kept_existing": sorted(existing & {a["key"] for a in tpl["activities"]})}


# ------------------------------------------------------------------ activity execution
def _assignees(db, project, a: mm.ProjectActivity) -> list:
    from .monitor import fallback_assignees, team_members
    if a.assignee_user_ids:
        return list(a.assignee_user_ids)
    if a.team:
        t = team_members(db, project.id, a.team)
        if t:
            return t
    return fallback_assignees(db, project.id)


def _advance(a: mm.ProjectActivity, today: date):
    step = CADENCE.get(a.cadence)
    if step is None:
        a.next_due, a.enabled = None, False
        return
    nxt = a.next_due or today
    while nxt <= today:
        nxt += step
    a.next_due = nxt


def _out_text(output: dict, limit=3500) -> str:
    import json
    s = json.dumps(output, default=str, indent=1)
    return s if len(s) <= limit else s[:limit] + "\n… (full output attached to the task)"


def run_activity(db, project: m.Project, a: mm.ProjectActivity, triggered_by="schedule", user_id=None) -> mm.ActivityRun:
    """Execute one activity now. AI agents run and hand a review task to the team; people get a task."""
    from . import agents
    run = mm.ActivityRun(organisation_id=project.organisation_id, project_id=project.id, activity_id=a.id, agent_key=a.agent_key,
                         triggered_by=triggered_by)
    db.add(run)
    db.flush()
    assignees = _assignees(db, project, a)
    label = WORKSTREAM_LABEL.get(a.workstream, a.workstream)
    due = utcnow() + timedelta(days=3 if a.cadence in ("daily", "weekly") else 7)
    if a.assignee_type == "ai_agent" and a.agent_key:
        try:
            if a.agent_key.startswith("skill:"):
                from . import skills
                sr = skills.run_skill(db, project, a.agent_key.split(":", 1)[1], inputs=a.config or {}, user_id=user_id)
                status = {"COMPLETED": "COMPLETED", "NEEDS_DATA": "NEEDS_DATA"}.get(sr.status, sr.status)
                summary, output = sr.summary, {"skill_run_id": str(sr.id), "mode": sr.mode, "tables": {k: len(v) for k, v in (sr.output or {}).get("tables", {}).items()}}
            else:
                status, summary, output = agents.run_agent(db, project, a.agent_key, a.config or {})
        except agents.NoAI as e:
            status, summary, output = "NEEDS_CONNECTION", str(e), {}
        except Exception as e:  # an agent failure becomes a task for a person, never a silent drop
            status, summary, output = "FAILED", f"{type(e).__name__}: {e}"[:500], {}
        run.status, run.summary, run.output = status, summary, output
        if status == "COMPLETED":
            title = f"[{label}] Review AI output: {a.title}" if a.approval_required else f"[{label}] {a.title} — AI completed"
            t = m.Task(organisation_id=project.organisation_id, project_id=project.id, title=title[:300],
                       description=f"{summary}\n\nAgent: {a.agent_key}. " + ("Approve or edit the output, then mark done." if a.approval_required else ""),
                       status="review" if a.approval_required else "done", priority="medium", assignee_ids=assignees,
                       source=f"activity:{a.id}", workstream=a.workstream, assignee_type="ai_agent", agent_key=a.agent_key,
                       output=output, labels=["activity", a.workstream, "ai"], due_date=due)
        else:
            reason = {"NEEDS_CONNECTION": "the AI agent needs a connection", "NEEDS_DATA": "the AI agent needs data",
                      "HANDED_TO_HUMAN": "this step is done by a person", "FAILED": "the AI agent failed"}.get(status, status)
            t = m.Task(organisation_id=project.organisation_id, project_id=project.id, title=f"[{label}] {a.title}"[:300],
                       description=f"Assigned to the team because {reason}: {summary}\n\n{a.instructions or ''}".strip(),
                       priority="medium", assignee_ids=assignees, source=f"activity:{a.id}", workstream=a.workstream,
                       assignee_type="team", agent_key=a.agent_key, output=output, labels=["activity", a.workstream, status.lower()], due_date=due)
    else:
        run.status, run.summary = "HANDED_TO_HUMAN", "Task created for " + ("the assigned people" if a.assignee_user_ids else f"the {a.team or 'project'} team")
        t = m.Task(organisation_id=project.organisation_id, project_id=project.id, title=f"[{label}] {a.title}"[:300],
                   description=a.instructions, priority="medium", assignee_ids=assignees, source=f"activity:{a.id}",
                   workstream=a.workstream, assignee_type=a.assignee_type, labels=["activity", a.workstream], due_date=due)
    t.description = ((t.description or "") + "\n\n" + responsibility_text(db, project, a, assignees)).strip()
    db.add(t)
    db.flush()
    from . import mailer
    for u in assignees:
        mailer.task_email(db, t, u)
    run.task_id, run.finished_at = t.id, utcnow()
    a.last_run_at = utcnow()
    _advance(a, date.today())
    return run


def responsibility_text(db, project, a: mm.ProjectActivity, assignees) -> str:
    """Who owns an activity task and which strategy it serves (shown on every activity task)."""
    from .monitor import TEAM_LABEL, team_members
    names = []
    for u in assignees:
        usr = db.get(m.User, u)
        names.append((usr.name or usr.email) if usr else str(u))
    pm = [db.get(m.User, u) for u in team_members(db, project.id, "pm")]
    strat = db.get(mm.ProjectStrategy, a.strategy_id) if a.strategy_id else None
    tpl = STRATEGY_BY_KEY.get(strat.template_key) if strat and strat.template_key else None
    lines = ["Responsibility:",
             f"  Responsible: {', '.join(names) or 'unassigned'}" + (f" ({TEAM_LABEL.get(a.team, a.team)} team)" if a.team else ""),
             f"  Accountable: {', '.join((u.name or u.email) for u in pm if u) or 'project owner / manager'}",
             f"  Done by: {'AI agent ' + a.agent_key + ' (a person reviews)' if a.assignee_type == 'ai_agent' else 'a person'} · cadence {a.cadence}"]
    if strat:
        lines += ["", f"Strategy: {strat.name}" + (f" — {tpl['description']}" if tpl else "")]
    return "\n".join(lines)


def due_activities(db, today: date | None = None) -> list[mm.ProjectActivity]:
    today = today or date.today()
    return list(db.scalars(select(mm.ProjectActivity).join(m.Project, m.Project.id == mm.ProjectActivity.project_id)
                           .where(mm.ProjectActivity.enabled, mm.ProjectActivity.next_due.is_not(None), mm.ProjectActivity.next_due <= today,
                                  m.Project.status == "active")))


# ------------------------------------------------------------------ content watch (blog performance)
CHECKPOINTS = {  # days since publish → minimum expected performance (cumulative, page grain)
    7: {"impressions": 20},
    14: {"impressions": 100},
    30: {"clicks": 10, "position": 30},
    60: {"clicks": 30, "position": 20},
    90: {"clicks": 60, "position": 15},
}
CONTENT_PATHS = ("/blog/", "/news/", "/articles/", "/article/", "/guides/", "/guide/", "/insights/", "/resources/", "/posts/")


def _page_perf(db, project_id, url, since: date, until: date) -> dict:
    grain = "page" if db.scalar(select(func.count()).select_from(mm.SearchMetric).where(
        mm.SearchMetric.project_id == project_id, mm.SearchMetric.grain == "page")) else "detail"
    r = db.execute(select(func.coalesce(func.sum(mm.SearchMetric.clicks), 0), func.coalesce(func.sum(mm.SearchMetric.impressions), 0),
                          func.sum(mm.SearchMetric.position * mm.SearchMetric.impressions))
                   .where(mm.SearchMetric.project_id == project_id, mm.SearchMetric.grain == grain, mm.SearchMetric.page == url,
                          mm.SearchMetric.date >= since, mm.SearchMetric.date <= until)).one()
    clicks, impr, wpos = int(r[0]), int(r[1]), r[2]
    return {"clicks": clicks, "impressions": impr, "position": round(float(wpos) / impr, 1) if impr and wpos is not None else None}


def auto_register(db, project, ctx) -> int:
    """Pages under blog-like paths that first appear in Search Console in the last 120 days are watched automatically."""
    known = {u for (u,) in db.execute(select(mm.ContentWatch.url).where(mm.ContentWatch.project_id == project.id))}
    rows = db.execute(select(mm.SearchMetric.page, func.min(mm.SearchMetric.date))
                      .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.page.is_not(None))
                      .group_by(mm.SearchMetric.page)).all()
    if not rows:
        return 0
    data_start = min(d for _, d in rows)
    anchor = max(d for _, d in rows)
    n = 0
    for url, first in rows:
        if url in known or not any(p in (path_of(url) or "") for p in CONTENT_PATHS):
            continue
        if first <= data_start + timedelta(days=3) or first < anchor - timedelta(days=120):
            continue  # existed before our data began, or too old to judge as "new"
        top = db.execute(select(mm.SearchMetric.query, func.sum(mm.SearchMetric.impressions).label("i"))
                         .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.page == url, mm.SearchMetric.query.is_not(None))
                         .group_by(mm.SearchMetric.query).order_by(func.sum(mm.SearchMetric.impressions).desc()).limit(1)).first()
        db.add(mm.ContentWatch(organisation_id=project.organisation_id, project_id=project.id, url=url, published_at=first,
                               target_keyword=top[0] if top else None, source="auto_gsc"))
        n += 1
    db.flush()
    return n


def check_content_watch(db, project, th, ctx) -> list:
    from .jobs import enqueue
    from .monitor import Finding
    ctx["content_watch_registered"] = auto_register(db, project, ctx)
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    if not anchor:
        return []
    F = []
    for cw in db.scalars(select(mm.ContentWatch).where(mm.ContentWatch.project_id == project.id, mm.ContentWatch.status.in_(("watching", "underperforming")))):
        age = (anchor - cw.published_at).days
        cps = dict(cw.checkpoints or {})
        failed = None
        for day, need in CHECKPOINTS.items():
            if age < day or str(day) in cps:
                continue
            perf = _page_perf(db, project.id, cw.url, cw.published_at, cw.published_at + timedelta(days=day))
            misses = []
            if "impressions" in need and perf["impressions"] < need["impressions"]:
                misses.append(f"{perf['impressions']} impressions (expected ≥{need['impressions']})")
            if "clicks" in need and perf["clicks"] < need["clicks"]:
                misses.append(f"{perf['clicks']} clicks (expected ≥{need['clicks']})")
            if "position" in need and (perf["position"] is None or perf["position"] > need["position"]):
                misses.append(f"average position {perf['position'] or '—'} (expected ≤{need['position']})")
            cps[str(day)] = {**perf, "expected": need, "passed": not misses, "misses": misses, "checked_on": anchor.isoformat()}
            if misses:
                failed = (day, perf, misses)
        cw.checkpoints = cps
        if failed:
            day, perf, misses = failed
            cw.status = "underperforming"
            sev = "high" if day >= 30 else "medium"
            F.append(Finding("blog_underperforming", "content", sev,
                             f"Blog post below its {day}-day target: {path_of(cw.url)}",
                             "Gap analysis has been run automatically against the pages ranking for the target keyword — "
                             "use the keyword, content and structure gaps in the attached report to rework the post.",
                             url=cw.url, query=cw.target_keyword, dimension=f"checkpoint={day}", categories=["content", "seo"],
                             evidence={"checkpoint_day": day, **perf, "misses": misses},
                             root_cause={"what": f"{day} days after publishing: " + "; ".join(misses),
                                         "why": "Usually one of: wrong/too-competitive target keyword, content less complete than ranking pages, "
                                                "weak structure (headings/FAQ/schema), not indexed, or no internal links pointing to it.",
                                         "verify": "next checkpoint passes"},
                             task_notes={"content": f"Rework {cw.url} using the gap report (missing sections, questions, word count, entities).",
                                         "seo": f"Check indexing, internal links and target keyword for {cw.url}; add 3+ internal links from related pages."},
                             verify={"type": "metric", "metric": "content_checkpoint"}))
            if not db.scalar(select(mm.GapReport.id).where(mm.GapReport.project_id == project.id, mm.GapReport.target_url == cw.url,
                                                           mm.GapReport.params["checkpoint"].astext == str(day))):
                gr = mm.GapReport(organisation_id=project.organisation_id, project_id=project.id, kind="full", target_url=cw.url,
                                  params={"checkpoint": str(day), "target_keyword": cw.target_keyword, "competitor_urls": cw.competitor_urls or [],
                                          "trigger": "blog_underperforming"})
                db.add(gr)
                db.flush()
                enqueue(db, project.organisation_id, "gap_analysis", {"gap_report_id": str(gr.id), "project_id": str(project.id)})
        elif age >= 90 and all(v.get("passed") for v in cps.values()):
            cw.status = "performing"
    return F
