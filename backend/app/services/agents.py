"""AI agent catalog and executors.

Catalog = the 50 SEO agents (strategy, keywords, technical, content, internal linking, link
building, GEO & AI, local & measurement), the 26 SEO skills and the Meta Ads skills from the
reference material. Each entry has an executor type:

  data   — runs now on the project's own data (crawl, GSC, GA4, imports); no LLM needed
  llm    — needs an AI provider (Claude / OpenAI / Gemini / Perplexity) connected
  source — needs a specific external connection (GBP, Meta, SERP data, server logs…)
  human  — work a person does (e.g. outreach emails); the agent prepares the task

An executor returns (status, summary, output). Nothing is simulated: an agent that
lacks its data or connection returns NEEDS_DATA / NEEDS_CONNECTION and hands the work to a person.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import desc, func, select

from .. import models as m
from .. import models_monitor as mm
from . import gaps, integrations as ih, keywords as kwsvc
from .ingest import path_of

A = lambda num, key, name, group, ws, kind, desc, needs=None: {  # noqa: E731
    "num": num, "key": key, "name": name, "group": group, "workstream": ws, "executor": kind, "description": desc, "needs": needs}

AGENTS = [
    A(1, "seo_strategy", "SEO strategy", "Strategy", "strategy", "data", "Builds a prioritised quarterly plan from audit, monitoring and gap data."),
    A(2, "topical_map", "Topical map", "Strategy", "content", "data", "Clusters the project's keywords into pillars and supporting topics."),
    A(3, "prioritization", "Prioritization", "Strategy", "strategy", "data", "Ranks open issues and opportunities by expected impact."),
    A(4, "search_intent", "Search intent", "Strategy", "on_page", "data", "Classifies every keyword by intent and flags pages targeting the wrong intent."),
    A(5, "traffic_forecast", "Traffic forecast", "Strategy", "analytics", "data", "Projects clicks for the next 4 weeks and the upside from page-two keywords."),
    A(6, "keyword_mining", "Keyword mining", "Keywords", "on_page", "data", "Collects keywords from GSC queries, tracked keywords and tool exports."),
    A(7, "clustering", "Clustering", "Keywords", "content", "data", "Groups keywords into clusters with a pillar keyword each."),
    A(8, "long_tail", "Long tail", "Keywords", "content", "data", "Finds long-tail (4+ word) queries with demand and the page that should own them."),
    A(9, "cannibalization", "Cannibalization", "Keywords", "on_page", "data", "Finds queries where two or more of your pages compete."),
    A(10, "seasonality", "Seasonality", "Keywords", "analytics", "data", "Detects monthly seasonality once a year of data exists."),
    A(11, "serp_features", "SERP features", "Keywords", "aeo", "source", "Tracks featured snippets, PAA and AI Overviews per keyword.", "SERP data provider"),
    A(12, "indexation_audit", "Indexation audit", "Technical", "technical", "data", "Finds important pages that are noindexed, not 200, or get no impressions."),
    A(13, "crawl_budget", "Crawl budget", "Technical", "technical", "data", "Measures wasted crawl on redirects, errors, parameters and deep pages."),
    A(14, "robots_sitemap", "Robots & sitemap", "Technical", "technical", "data", "Checks robots.txt, sitemap presence and sitemap URL validity."),
    A(15, "redirect_chains", "Redirect chains", "Technical", "technical", "data", "Lists redirect chains/loops and temporary redirects."),
    A(16, "canonicals", "Canonicals", "Technical", "technical", "data", "Finds missing, non-self and broken canonicals."),
    A(17, "core_web_vitals", "Core Web Vitals", "Technical", "technical", "data", "Summarises LCP/CLS/INP from PageSpeed results.", "Google PageSpeed Insights"),
    A(18, "javascript_rendering", "JavaScript rendering", "Technical", "technical", "data", "Compares raw vs rendered content on sampled pages."),
    A(19, "server_logs", "Server logs", "Technical", "technical", "source", "Analyses Googlebot hits from server logs.", "Server log upload"),
    A(20, "hreflang", "Hreflang", "Technical", "technical", "data", "Validates hreflang reciprocity and targets."),
    A(21, "structured_data", "Structured data", "Technical", "technical", "data", "Inventories schema types and errors per template."),
    A(22, "content_brief", "Content brief", "Content", "content", "data", "Builds a brief from real queries and competitor page structure (LLM refines it if connected)."),
    A(23, "article_drafting", "Article drafting", "Content", "content", "llm", "Drafts an article from the brief in the brand voice."),
    A(24, "content_refresh", "Content refresh", "Content", "content", "data", "Finds pages losing clicks over 90 days that need refreshing."),
    A(25, "thin_content", "Thin content", "Content", "on_page", "data", "Lists indexable pages under 300 words."),
    A(26, "eeat", "E-E-A-T", "Content", "content", "llm", "Reviews experience, expertise, authority and trust signals of a page."),
    A(27, "titles_metas", "Titles & metas", "Content", "on_page", "data", "Finds missing/duplicate/long titles and low-CTR results; LLM rewrites if connected."),
    A(28, "faq_blocks", "FAQ blocks", "Content", "aeo", "data", "Collects question queries per page for FAQ sections; LLM drafts answers if connected."),
    A(29, "ai_slop_detector", "AI slop detector", "Content", "content", "llm", "Flags generic, repetitive or unsupported AI-sounding passages."),
    A(30, "translation", "Translation & localization", "Content", "content", "llm", "Translates and localises content for target markets."),
    A(31, "internal_links", "Internal links", "Internal linking", "on_page", "data", "Suggests source → target internal links with anchors from query data."),
    A(32, "anchor_text", "Anchor text", "Internal linking", "on_page", "data", "Finds generic or over-optimised internal anchors."),
    A(33, "site_architecture", "Site architecture", "Internal linking", "technical", "data", "Click-depth distribution and section structure."),
    A(34, "orphan_pages", "Orphan pages", "Internal linking", "technical", "data", "Pages in the sitemap with no internal links."),
    A(35, "link_prospecting", "Link prospecting", "Link building", "off_page", "data", "Domains linking to competitors but not to you, ranked by authority."),
    A(36, "outreach", "Outreach", "Link building", "off_page", "human", "Prepares outreach tasks for prospects; emails are sent by a person."),
    A(37, "toxic_links", "Toxic links", "Link building", "off_page", "data", "Lists backlinks with high toxicity scores (advisory; disavow needs approval)."),
    A(38, "broken_link_building", "Broken link building", "Link building", "off_page", "source", "Finds broken pages on prospect sites to replace.", "Backlink data provider"),
    A(39, "backlink_gap", "Backlink gap", "Link building", "off_page", "data", "Referring domains competitors have that you don't."),
    A(40, "digital_pr", "Digital PR", "Link building", "off_page", "llm", "Generates data-led PR angles and pitch drafts."),
    A(41, "ai_overviews", "AI Overviews", "GEO & AI", "aio", "source", "Tracks whether AI Overviews appear and cite you.", "SERP data provider"),
    A(42, "chatgpt_citations", "ChatGPT citations", "GEO & AI", "geo", "llm", "Asks answer engines your target questions and checks if your domain is cited (Perplexity returns citations)."),
    A(43, "index_it", "Index it", "GEO & AI", "technical", "data", "Lists new/updated URLs to submit for indexing (GSC / IndexNow)."),
    A(44, "brand_mentions", "Brand mentions", "GEO & AI", "geo", "source", "Finds unlinked brand mentions across the web.", "Web mention monitoring API"),
    A(45, "reddit_ugc", "Reddit & UGC", "GEO & AI", "geo", "source", "Finds forum threads where your answer would help.", "Reddit API"),
    A(46, "quotable_answers", "Quotable answers", "GEO & AI", "aeo", "data", "Finds pages lacking concise, quotable answers and Q&A structure."),
    A(47, "google_business_profile", "Google Business Profile", "Local & measurement", "local", "source", "Audits GBP completeness, posts and reviews.", "Google Business Profile"),
    A(48, "nap_citations", "NAP & citations", "Local & measurement", "local", "data", "Checks name/address/phone consistency between site and GBP data."),
    A(49, "gsc_anomalies", "Search Console anomalies", "Local & measurement", "analytics", "data", "The monitoring engine: drops, spikes, new/lost queries, device/country anomalies."),
    A(50, "client_reporting", "Client reporting", "Local & measurement", "analytics", "data", "Builds the weekly project report from monitoring and task data."),
    # SXO + social (requested workstreams)
    A(51, "sxo_review", "SXO page review", "GEO & AI", "sxo", "data", "Checks search-experience signals: speed, mobile, CTR vs position, engagement per landing page."),
    A(52, "social_posts", "Social post drafts", "Social", "social", "llm", "Drafts platform-specific posts from new/updated content (publishing needs Meta/LinkedIn/X)."),
    A(53, "social_calendar", "Social calendar", "Social", "social", "data", "Builds the week's posting plan from published and refreshed content."),
    A(55, "keyword_gap", "Keyword gap", "Keywords", "content", "data", "Keywords competitors rank for in the top 20 where you don't."),
    A(56, "content_gap", "Content gap", "Content", "content", "data", "Topics competitors cover that have no page on your site."),
    A(57, "change_log", "SEO change log (drift)", "Technical", "technical", "data", "Lists SEO-relevant changes between crawls (titles, canonicals, robots, status) with their measured impact."),
    A(54, "blog_ideas", "Blog ideas from queries", "Content", "content", "data", "Turns real search queries and content gaps into blog titles with keywords and format."),
]

SKILLS_26 = [
    ("seo-technical-audit", "Crawlability, indexing, security and mobile", "indexation_audit"), ("seo-sitemap", "Sitemap against the latest crawl", "robots_sitemap"),
    ("seo-schema", "Detect, validate and generate JSON-LD", "structured_data"), ("seo-images", "Alt text, WebP, srcset and CLS", "thin_content"),
    ("seo-hreflang", "Language codes and return tags", "hreflang"), ("seo-content-brief", "Writer-ready briefs from SERP analysis", "content_brief"),
    ("seo-content-audit", "E-E-A-T and CITE, with a publish verdict", "eeat"), ("seo-keyword-cluster", "Intent clusters, pillar plus spokes", "clustering"),
    ("seo-keyword-niche", "Longtail and question opportunities", "long_tail"), ("seo-sxo", "Page-type mismatch, scored from four personas", "sxo_review"),
    ("seo-competitor-gap-analysis", "Keywords they rank for, you do not", "keyword_gap"), ("seo-competitor-pages", "X versus Y and alternatives-to pages", "content_gap"),
    ("seo-subdomain", "Ownership map and fragmentation flags", "site_architecture"), ("seo-ads", "Paid footprint, bidding and copy patterns", None),
    ("seo-backlink-gap", "Domains linking to rivals but not you", "backlink_gap"), ("seo-backlinks-profile", "Authority spread and toxic flags", "toxic_links"),
    ("seo-ai-search-share-of-voice", "Visibility across five AI engines", "chatgpt_citations"), ("seo-geo", "Passage-level fixes to improve citability", "quotable_answers"),
    ("seo-page", "Keep, refresh, consolidate or kill per URL", "content_refresh"), ("seo-local", "GBP, NAP consistency and local-pack scoring", "nap_citations"),
    ("seo-agency-landing-page", "Demand-gen page with a free audit", None), ("seo-drift", "Git for SEO, with severity coding", "change_log"),
    ("seo-plan", "Phased quarterly roadmap", "seo_strategy"), ("seo-google", "GSC, PageSpeed, CrUX and GA4", "gsc_anomalies"),
    ("seo-api", "The full 195-tool surface, cost forecast", None), ("seo-firecrawl", "Raw HTML and JSON-LD WebFetch cannot reach", "javascript_rendering"),
]
META_SKILLS = [
    ("meta-ads", "Meta paid advertising audit and optimisation"), ("meta-analyze", "Deep analysis across Facebook, Instagram and Threads ads"),
    ("meta-attribution", "Attribution settings health audit"), ("meta-audit", "Full paid advertising audit"), ("meta-budget", "Budget allocation and bidding review"),
    ("meta-capi", "Conversions API / server-side tracking audit"), ("meta-competitor", "Competitor ad intelligence (Ad Library)"),
    ("meta-create", "Campaign concept and copy brief generator"), ("meta-creative", "Creative quality audit"), ("meta-dna", "Brand DNA extractor from a website"),
    ("meta-generate", "AI image generation for ad creatives"), ("meta-landing", "Landing page quality for ad campaigns"),
    ("meta-math", "CPA / ROAS / CPL calculator and break-even modelling"), ("meta-photoshoot", "Product photography enhancement"),
    ("meta-plan", "Strategic Meta advertising plan"), ("meta-test", "A/B test design and significance"),
]
META_DATA_READY = {"meta-landing", "meta-math", "meta-dna"}  # runnable without the Meta API

BY_KEY = {a["key"]: a for a in AGENTS}
EXECUTORS = {}


def executor(key):
    def deco(f):
        EXECUTORS[key] = f
        return f
    return deco


def catalog(db) -> dict:
    conn = ih.connected_map(db)
    ai = any(conn.get(k) == "CONNECTED" for k in ("claude", "openai", "gemini", "perplexity"))
    out = []
    for a in AGENTS:
        if a["executor"] == "data":
            state = "live" if a["key"] in EXECUTORS else "planned"
            if a["key"] == "core_web_vitals" and conn.get("pagespeed") != "CONNECTED":
                state = "live (needs PageSpeed data)"
        elif a["executor"] == "llm":
            state = ("live" if ai else "needs AI provider") if a["key"] in EXECUTORS else "planned"
        elif a["executor"] == "human":
            state = "prepares human task"
        else:
            state = f"needs {a['needs']}"
        out.append({**a, "state": state, "runnable": a["key"] in EXECUTORS})
    skills = [{"key": k, "description": d, "agent": ag, "state": (BY_KEY.get(ag) and next(x["state"] for x in out if x["key"] == ag))
               or ("live" if ag in EXECUTORS else "planned")} for k, d, ag in SKILLS_26]
    meta = [{"key": k, "description": d, "state": ("live" if k in META_DATA_READY else
                                                    ("needs Meta Ads API" if conn.get("meta") != "CONNECTED" else "planned"))} for k, d in META_SKILLS]
    return {"agents": out, "seo_skills": skills, "meta_skills": meta, "ai_provider_connected": ai}


# ------------------------------------------------------------------ LLM gateway (multi-provider, fallback order)
class NoAI(Exception):
    pass


class BudgetExceeded(NoAI):
    """The organisation's monthly AI budget cap is reached (set in AI Provider Hub)."""


DEFAULT_PREFER = ("claude", "openai", "gemini", "perplexity")


def _call_provider(p: str, cr: dict, model: str, prompt: str, max_tokens: int) -> tuple[str, dict]:
    """One HTTP call to a provider. Returns (text, raw json). Raises IntegrationError on failure."""
    from .integrations import _ok, http_call
    if p == "claude":
        j = _ok(http_call("POST", "https://api.anthropic.com/v1/messages", timeout=120, headers={
            "x-api-key": cr["secret"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}), "Claude").json()
        return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text"), j
    if p == "openai":
        j = _ok(http_call("POST", "https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {cr['secret']}"},
                          json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}, timeout=120), "OpenAI").json()
        return j["choices"][0]["message"]["content"], j
    if p == "gemini":
        j = _ok(http_call("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          params={"key": cr["secret"]}, json={"contents": [{"parts": [{"text": prompt}]}],
                                                             "generationConfig": {"maxOutputTokens": max_tokens}}, timeout=120), "Gemini").json()
        return "".join(x.get("text", "") for x in j["candidates"][0]["content"]["parts"]), j
    if p == "perplexity":
        j = _ok(http_call("POST", "https://api.perplexity.ai/chat/completions", headers={"Authorization": f"Bearer {cr['secret']}"},
                          json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}, timeout=120), "Perplexity").json()
        return j["choices"][0]["message"]["content"], j
    raise ih.IntegrationError(f"Unknown AI provider {p}")


def llm(db, org_id, prompt: str, max_tokens: int = 1500, prefer: tuple = DEFAULT_PREFER, task_type: str | None = None,
        project_id=None, user_id=None) -> tuple[str, dict]:
    """Multi-provider AI gateway.

    Honours the organisation's routing (AI Provider Hub: order, enabled, model per task type, provider budgets),
    blocks the call when the org's monthly budget cap is reached, falls back to the next provider on errors,
    and records tokens + cost for every successful call (AI Cost & Usage). ``prefer`` is the order used when
    the organisation has not configured routing. Raises NoAI when nothing is connected or every provider failed.
    """
    from . import ai_engine as eng
    task_type = task_type or eng.current_scope().get("task_type") or "general"
    candidates = eng.route(db, org_id, task_type, prefer)
    if not candidates:
        raise NoAI("No AI provider connected (Claude, OpenAI, Gemini or Perplexity) — NOT CONNECTED")
    eng.check_budget(db, org_id)
    errors, rate_limited = [], None
    for c in candidates:
        p = c["key"]
        if c["over_budget"]:
            errors.append(f"{p}: provider monthly budget reached")
            continue
        try:
            cr = ih.credentials(db, org_id, p)
            ih.take_token(db, org_id, p)
            model = eng.model_for(c, task_type, cr.get("config"))
            text, j = _call_provider(p, cr, model, prompt, max_tokens)
        except ih.RateLimited as e:
            rate_limited = e
            errors.append(f"{p}: {e}")
            continue
        except ih.IntegrationError as e:
            errors.append(f"{p}: {e}")
            continue  # fall back to the next provider in the routing order
        tin, tout, est = eng.extract_tokens(p, j, prompt, text)
        rec = eng.record_usage(org_id, p, model, tin, tout, task_type=task_type, project_id=project_id, user_id=user_id, estimated=est)
        meta = {"model": model, "provider": p, "tokens_in": tin, "tokens_out": tout, "cost": rec["cost"], "estimated_tokens": est,
                **(j.get("usage") or {})}
        if p == "perplexity":
            meta["citations"] = j.get("citations", [])
        if errors:
            meta["fallback_from"] = errors
        return text, meta
    if rate_limited and len(errors) == 1:
        raise rate_limited
    if all("budget" in e for e in errors):
        raise BudgetExceeded("Every enabled AI provider has reached its monthly budget — " + "; ".join(errors))
    raise NoAI("All connected AI providers failed: " + "; ".join(errors))


# ------------------------------------------------------------------ helpers
def _latest_pages(db, project):
    from .monitor import _own_crawls, _pages
    out = {}
    info = {}
    for site in db.scalars(select(m.Site).where(m.Site.project_id == project.id)):
        cr = _own_crawls(db, site)
        if cr:
            out.update(_pages(db, cr[0].id))
            info[site.domain] = cr[0].site_info or {}
    return out, info


def _need_crawl():
    return "NEEDS_DATA", "No crawl yet for this project — run an SEO audit or import a Screaming Frog crawl.", {}


def _need_gsc():
    return "NEEDS_DATA", "No Search Console data yet — connect GSC for this project or import a GSC export.", {}


# ------------------------------------------------------------------ data executors
@executor("keyword_mining")
@executor("search_intent")
def ex_keywords(db, project, cfg):
    r = kwsvc.research(db, project)
    if not r["total"]:
        return "NEEDS_DATA", "No keywords yet — connect GSC, add tracked keywords or import an Ahrefs/Semrush export.", {}
    s = f"{r['total']} keywords: " + ", ".join(f"{v} {k}" for k, v in sorted(r["by_intent"].items(), key=lambda x: -x[1])) + \
        f"; {r['long_tail']} long-tail; {len(r['clusters'])} clusters."
    return "COMPLETED", s, r


@executor("clustering")
@executor("topical_map")
def ex_clusters(db, project, cfg):
    r = kwsvc.research(db, project)
    if not r["total"]:
        return ex_keywords(db, project, cfg)
    pillars = [{"pillar": c["pillar"], "intent": c["intent"], "size": c["size"], "children": [k["keyword"] for k in c["keywords"][1:15]]}
               for c in r["clusters"] if not c.get("unclustered")]
    return "COMPLETED", f"{len(pillars)} topic clusters (pillar + supporting keywords).", {"topical_map": pillars}


@executor("long_tail")
def ex_long_tail(db, project, cfg):
    kws = [k for k in kwsvc.project_keywords(db, project) if k["long_tail"]]
    if not kws:
        return _need_gsc()
    kws.sort(key=lambda k: -(k.get("impressions") or k.get("volume") or 0))
    return "COMPLETED", f"{len(kws)} long-tail queries (4+ words); top: " + ", ".join(k["keyword"] for k in kws[:5]), {"keywords": kws[:200]}


@executor("cannibalization")
def ex_cannibal(db, project, cfg):
    r = kwsvc.cannibalisation(db, project)
    if r == [] and not db.scalar(select(func.count()).select_from(mm.SearchMetric).where(mm.SearchMetric.project_id == project.id)):
        return _need_gsc()
    return "COMPLETED", f"{len(r)} queries where two or more of your pages compete.", {"queries": r[:100]}


@executor("seasonality")
def ex_season(db, project, cfg):
    rows = db.execute(select(func.date_trunc("month", mm.SearchMetric.date), func.sum(mm.SearchMetric.clicks))
                      .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.grain.in_(("total",)))
                      .group_by(func.date_trunc("month", mm.SearchMetric.date)).order_by(func.date_trunc("month", mm.SearchMetric.date))).all()
    if len(rows) < 13:
        return "NEEDS_DATA", f"Seasonality needs 13+ months of Search Console totals; {len(rows)} available.", {"months": len(rows)}
    vals = [int(v) for _, v in rows]
    avg = sum(vals) / len(vals)
    peaks = [str(d.date())[:7] for d, v in rows if v > avg * 1.2]
    return "COMPLETED", f"Peak months: {', '.join(peaks) or 'none above +20%'}.", {"monthly": [{"month": str(d.date())[:7], "clicks": int(v)} for d, v in rows]}


@executor("prioritization")
def ex_prior(db, project, cfg):
    iss = db.scalars(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id == project.id, mm.MonitorIssue.status.in_(("open", "reopened", "in_progress")))).all()
    sev = {"critical": 100, "high": 40, "medium": 10, "low": 3}
    ranked = sorted(iss, key=lambda i: -(sev[i.severity] * (1.5 if not i.is_opportunity else 1) + (i.evidence or {}).get("impressions", 0) / 1000))
    return "COMPLETED", f"{len(ranked)} open items ranked.", {"items": [{"title": i.title, "severity": i.severity, "team": i.category,
                                                                         "opportunity": i.is_opportunity, "id": str(i.id)} for i in ranked[:50]]}


@executor("traffic_forecast")
def ex_forecast(db, project, cfg):
    rows = db.execute(select(mm.SearchMetric.date, func.sum(mm.SearchMetric.clicks)).where(mm.SearchMetric.project_id == project.id,
                                                                                         mm.SearchMetric.grain == "total")
                      .group_by(mm.SearchMetric.date).order_by(mm.SearchMetric.date)).all()
    if len(rows) < 28:
        return "NEEDS_DATA", "Forecast needs 28+ days of Search Console totals.", {}
    weeks = []
    for i in range(0, len(rows) - len(rows) % 7, 7):
        weeks.append(sum(int(v) for _, v in rows[i:i + 7]))
    n = len(weeks)
    xs = range(n)
    mx, my = sum(xs) / n, sum(weeks) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, weeks)) / max(1, sum((x - mx) ** 2 for x in xs))
    fc = [max(0, round(my + slope * (n + k - mx))) for k in range(4)]
    from .monitor import CTR_CURVE
    upside = 0
    for k in kwsvc.project_keywords(db, project):
        if k.get("position") and 10 < k["position"] <= 20 and k.get("impressions"):
            upside += k["impressions"] / 4 * CTR_CURVE[8]
    msg = (f"Next 4 weeks: {fc} clicks/week (trend {'+' if slope >= 0 else ''}{round(slope, 1)}/week); "
           f"≈{round(upside)} extra clicks/week if page-two keywords reach position 8.")
    return "COMPLETED", msg, {"weekly_history": weeks, "forecast": fc, "striking_distance_upside": round(upside)}


@executor("indexation_audit")
def ex_index(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    with_impr = set()
    if anchor:
        with_impr = {p for (p,) in db.execute(select(mm.SearchMetric.page).where(mm.SearchMetric.project_id == project.id,
                                                                                 mm.SearchMetric.date > anchor - timedelta(days=28),
                                                                                 mm.SearchMetric.impressions > 0).distinct())}
    noindex = [u for u, p in pages.items() if p.get("noindex")]
    non200 = [u for u, p in pages.items() if p.get("status") not in (200, None)]
    no_impr = [u for u, p in pages.items() if p.get("status") == 200 and not p.get("noindex") and anchor and u not in with_impr]
    return "COMPLETED", f"{len(noindex)} noindex, {len(non200)} non-200, {len(no_impr)} indexable pages with zero impressions (likely not indexed).", \
        {"noindex": noindex[:200], "non_200": non200[:200], "no_impressions": no_impr[:200], "gsc_available": bool(anchor)}


@executor("crawl_budget")
def ex_budget(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    n = len(pages)
    red = sum(1 for p in pages.values() if p.get("chain"))
    err = sum(1 for p in pages.values() if (p.get("status") or 0) >= 400)
    params = sum(1 for u in pages if "?" in u)
    deep = sum(1 for p in pages.values() if (p.get("depth") or 0) > 4)
    waste = round(100 * (red + err + params) / n, 1)
    return "COMPLETED", f"{waste}% of crawled URLs are waste (redirects {red}, errors {err}, parameters {params}); {deep} pages deeper than 4 clicks.", \
        {"pages": n, "redirects": red, "errors": err, "parameter_urls": params, "deep_pages": deep, "waste_pct": waste}


@executor("robots_sitemap")
def ex_robots(db, project, cfg):
    _, info = _latest_pages(db, project)
    if not info:
        return _need_crawl()
    out = {d: {"robots": i.get("robots"), "sitemap": {k: v for k, v in (i.get("sitemap") or {}).items() if k != "urls"} |
               {"url_count": len((i.get("sitemap") or {}).get("urls", []))}} for d, i in info.items()}
    probs = [d for d, i in out.items() if not (i["robots"] or {}).get("exists") or not i["sitemap"].get("exists")]
    return "COMPLETED", ("All sites have robots.txt and a sitemap." if not probs else f"Missing robots.txt or sitemap on: {', '.join(probs)}"), out


@executor("redirect_chains")
def ex_redirects(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    ch = [{"url": u, "chain": p["chain"]} for u, p in pages.items() if p.get("chain")]
    temp = [c for c in ch if any(h["status"] not in (301, 308) for h in c["chain"])]
    return "COMPLETED", f"{sum(1 for c in ch if len(c['chain']) > 1)} chains, {len(temp)} temporary redirects.", {"redirects": ch[:200]}


@executor("canonicals")
def ex_canon(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    miss = [u for u, p in pages.items() if p.get("status") == 200 and not p.get("noindex") and not p.get("canonical")]
    other = [{"url": u, "canonical": p["canonical"]} for u, p in pages.items() if p.get("canonical") and p["canonical"] != u]
    return "COMPLETED", f"{len(miss)} pages without a canonical, {len(other)} canonicalised elsewhere.", {"missing": miss[:200], "non_self": other[:200]}


@executor("core_web_vitals")
def ex_cwv(db, project, cfg):
    sites = [s.id for s in db.scalars(select(m.Site).where(m.Site.project_id == project.id))]
    rs = db.scalars(select(m.PagespeedResult).where(m.PagespeedResult.site_id.in_(sites)).order_by(desc(m.PagespeedResult.checked_at)).limit(50)).all()
    if not rs:
        return "NEEDS_CONNECTION", "No PageSpeed results — connect Google PageSpeed Insights and run a test.", {}
    fail = [r for r in rs if (r.lcp or 0) > 2.5 or (r.cls or 0) > 0.1 or (r.inp or 0) > 200]
    return "COMPLETED", f"{len(fail)} of {len(rs)} measurements fail Core Web Vitals.", {"results": [
        {"url": r.url, "device": r.device, "lcp": float(r.lcp) if r.lcp else None, "cls": float(r.cls) if r.cls else None,
         "inp": float(r.inp) if r.inp else None, "score": float(r.score) if r.score else None} for r in rs]}


@executor("javascript_rendering")
def ex_js(db, project, cfg):
    _, info = _latest_pages(db, project)
    r = [i.get("render") for i in info.values() if i.get("render")]
    if not r:
        return _need_crawl()
    return "COMPLETED", "Raw vs rendered word counts on sampled pages.", {"render": r}


@executor("hreflang")
def ex_hreflang(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    hp = {u: p["hreflang"] for u, p in pages.items() if p.get("hreflang")}
    if not hp:
        return "COMPLETED", "hreflang not used on this site.", {}
    bad = [u for u, alts in hp.items() for a in alts if a["href"] in pages and not any(x["href"] == u for x in pages[a["href"]].get("hreflang", []))]
    return "COMPLETED", f"{len(hp)} pages use hreflang; {len(set(bad))} non-reciprocal.", {"non_reciprocal": sorted(set(bad))[:200]}


@executor("structured_data")
def ex_schema(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    types = Counter(t for p in pages.values() for t in p.get("schema_types") or [])
    errs = {u: p["schema_errors"] for u, p in pages.items() if p.get("schema_errors")}
    none = [u for u, p in pages.items() if p.get("status") == 200 and not p.get("schema_types")]
    return "COMPLETED", f"Schema types: {dict(types) or 'none'}; {len(errs)} pages with errors; {len(none)} without markup.", \
        {"types": dict(types), "errors": errs, "without_schema": none[:200]}


@executor("content_refresh")
def ex_refresh(db, project, cfg):
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    if not anchor:
        return _need_gsc()
    from .monitor import search_agg
    recent = search_agg(db, project.id, ("page",), (anchor - timedelta(days=27), anchor), ("page", "detail"))
    older = search_agg(db, project.id, ("page",), (anchor - timedelta(days=89), anchor - timedelta(days=62)), ("page", "detail"))
    rows = []
    for (pg,), o in older.items():
        r = recent.get((pg,), {"clicks": 0})
        if o["clicks"] >= 20 and r["clicks"] < 0.7 * o["clicks"]:
            rows.append({"page": pg, "clicks_then": o["clicks"], "clicks_now": r["clicks"], "change_pct": round(100 * (r["clicks"] - o["clicks"]) / o["clicks"])})
    rows.sort(key=lambda r: r["change_pct"])
    return "COMPLETED", f"{len(rows)} pages lost 30%+ clicks vs two months ago (refresh candidates).", {"pages": rows[:100]}


@executor("thin_content")
def ex_thin(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    thin = [{"url": u, "words": p.get("word_count")} for u, p in pages.items() if p.get("status") == 200 and not p.get("noindex") and (p.get("word_count") or 0) < 300]
    return "COMPLETED", f"{len(thin)} indexable pages under 300 words.", {"pages": thin[:200]}


@executor("titles_metas")
def ex_titles(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    ok = {u: p for u, p in pages.items() if p.get("status") == 200 and not p.get("noindex")}
    tc = Counter((p.get("title") or "").strip().lower() for p in ok.values())
    rows = []
    for u, p in ok.items():
        t = (p.get("title") or "").strip()
        probs = []
        if not t:
            probs.append("missing title")
        elif tc[t.lower()] > 1:
            probs.append("duplicate title")
        if len(t) > 65:
            probs.append("title too long")
        if not (p.get("meta_description") or "").strip():
            probs.append("missing meta description")
        if probs:
            rows.append({"url": u, "title": t, "problems": probs})
    low_ctr = [i for i in db.scalars(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id == project.id, mm.MonitorIssue.kind.in_(("opp_low_ctr", "ctr_drop")),
                                                                    mm.MonitorIssue.status != "resolved"))]
    out = {"pages": rows, "low_ctr": [{"query": i.query, "url": i.url, "evidence": i.evidence} for i in low_ctr]}
    if cfg.get("rewrite") is not False and (rows or low_ctr):
        try:
            sample = rows[:10] + [{"url": i.url, "query": i.query} for i in low_ctr[:10]]
            text, usage = llm(db, project.organisation_id, "Write an SEO title (≤60 chars) and meta description (≤155 chars) for each item. "
                              "Match search intent, include the query naturally, no clickbait. Reply as JSON list of {url,title,meta}.\n" + str(sample))
            out["ai_rewrites"], out["ai_model"] = text, usage.get("model")
        except NoAI:
            out["ai_rewrites"] = None
    return "COMPLETED", f"{len(rows)} pages with title/meta problems; {len(low_ctr)} low-CTR results." + \
        (" AI rewrites attached." if out.get("ai_rewrites") else ""), out


@executor("faq_blocks")
def ex_faq(db, project, cfg):
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    if not anchor:
        return _need_gsc()
    rx = r"^(how|what|why|when|where|who|which|can|does|do|is|are|should)\b"
    rows = db.execute(select(mm.SearchMetric.page, mm.SearchMetric.query, func.sum(mm.SearchMetric.impressions))
                      .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.grain == "detail",
                             mm.SearchMetric.date > anchor - timedelta(days=28)).group_by(mm.SearchMetric.page, mm.SearchMetric.query)).all()
    per = {}
    for pg, q, i in rows:
        if q and re.match(rx, q):
            per.setdefault(pg, []).append({"question": q, "impressions": int(i or 0)})
    out = {pg: sorted(v, key=lambda x: -x["impressions"])[:8] for pg, v in per.items()}
    return "COMPLETED", f"Question queries found for {len(out)} pages (FAQ candidates).", {"faq_candidates": out}


@executor("internal_links")
def ex_internal(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    kws = kwsvc.project_keywords(db, project)
    if not pages or not kws:
        return "NEEDS_DATA", "Needs a crawl and Search Console queries.", {}
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    best = {}
    for q, pg, i in db.execute(select(mm.SearchMetric.query, mm.SearchMetric.page, func.sum(mm.SearchMetric.impressions))
                               .where(mm.SearchMetric.project_id == project.id, mm.SearchMetric.grain == "detail",
                                      mm.SearchMetric.date > anchor - timedelta(days=28)).group_by(mm.SearchMetric.query, mm.SearchMetric.page)):
        if q and pg and (q not in best or i > best[q][1]):
            best[q] = (pg, i)
    targets = {}
    for q, (pg, i) in sorted(best.items(), key=lambda x: -x[1][1])[:200]:
        targets.setdefault(pg, q)
    sugg = []
    for tgt, q in targets.items():
        toks = set(kwsvc.tokens(q))
        for src, p in pages.items():
            if src == tgt or p.get("status") != 200:
                continue
            linked = any(l["href"] == tgt for l in p.get("internal_links", []))
            text = " ".join([p.get("title") or "", " ".join(p.get("h2") or [])]).lower()
            if not linked and toks and len(toks & set(kwsvc.tokens(text))) >= max(1, len(toks) - 1):
                sugg.append({"from": src, "to": tgt, "anchor": q})
    return "COMPLETED", f"{len(sugg)} internal-link suggestions to pages' main queries.", {"suggestions": sugg[:200]}


@executor("anchor_text")
def ex_anchor(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    generic = re.compile(r"^(click here|here|read more|more|learn more|this|link|go)$", re.I)
    bad = [{"from": u, "to": l["href"], "anchor": l.get("anchor")} for u, p in pages.items() for l in p.get("internal_links", []) if generic.match((l.get("anchor") or "").strip())]
    return "COMPLETED", f"{len(bad)} internal links with generic anchor text.", {"generic": bad[:200]}


@executor("site_architecture")
def ex_arch(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    depth = Counter(p.get("depth") for p in pages.values() if p.get("depth") is not None)
    sections = Counter((path_of(u).strip("/").split("/") or [""])[0] or "/" for u in pages)
    return "COMPLETED", f"Depth distribution {dict(sorted(depth.items()))}; {len(sections)} top-level sections.", {"depth": dict(depth), "sections": dict(sections)}


@executor("orphan_pages")
def ex_orphans(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    o = [u for u, p in pages.items() if p.get("source") == "sitemap" and not p.get("inlinks")]
    return "COMPLETED", f"{len(o)} orphan pages (in sitemap, no internal links).", {"orphans": o}


@executor("link_prospecting")
@executor("backlink_gap")
def ex_blgap(db, project, cfg):
    r = gaps.backlink_gap(db, project)
    if not r.get("available"):
        return "NEEDS_DATA", r["message"], {}
    return "COMPLETED", f"{len(r['prospects'])} domains link to competitors but not to you.", r


@executor("keyword_gap")
def ex_kwgap(db, project, cfg):
    r = gaps.keyword_gap(db, project)
    if not r.get("available"):
        return "NEEDS_DATA", r["message"], {}
    return "COMPLETED", f"{r['total']} keywords competitors rank for in the top 20 where you don't.", r


@executor("content_gap")
def ex_contentgap(db, project, cfg):
    r = gaps.content_gap(db, project)
    if not r.get("available"):
        return "NEEDS_DATA", r["message"], {}
    return "COMPLETED", f"{len(r['topics'])} topics with no page covering them ({r['uncovered_keywords']} keywords).", r


@executor("outreach")
def ex_outreach(db, project, cfg):
    r = gaps.backlink_gap(db, project)
    return "HANDED_TO_HUMAN", "Outreach is sent by a person. Prospects from the backlink gap are attached." if r.get("available") else \
        "Outreach is sent by a person; import competitor referring domains to get prospects.", {"prospects": (r.get("prospects") or [])[:50]}


@executor("toxic_links")
def ex_toxic(db, project, cfg):
    rows = db.scalars(select(m.Backlink).where(m.Backlink.project_id == project.id, m.Backlink.toxicity_score >= 50)).all()
    return "COMPLETED", f"{len(rows)} backlinks scored 50+ toxicity (advisory; disavow needs Manager approval).", \
        {"links": [{"source": b.source_url, "score": float(b.toxicity_score)} for b in rows]}


@executor("index_it")
def ex_indexit(db, project, cfg):
    since = (cfg or {}).get("since_days", 7)
    ch = db.scalars(select(mm.SiteChange).where(mm.SiteChange.project_id == project.id, mm.SiteChange.field.in_(("page_added", "title", "h1")),
                                                mm.SiteChange.detected_at >= func.now() - timedelta(days=since))).all()
    urls = sorted({c.url for c in ch})
    return "COMPLETED", f"{len(urls)} new/updated URLs to submit for indexing.", {"urls": urls}


@executor("quotable_answers")
def ex_quotable(db, project, cfg):
    pages, _ = _latest_pages(db, project)
    if not pages:
        return _need_crawl()
    weak = [{"url": u, "question_headings": len(p.get("questions") or []), "schema": p.get("schema_types")}
            for u, p in pages.items() if p.get("status") == 200 and not p.get("noindex") and (p.get("word_count") or 0) > 300 and not p.get("questions")]
    return "COMPLETED", f"{len(weak)} substantial pages have no question-and-answer sections AI engines can quote.", {"pages": weak[:200]}


@executor("nap_citations")
def ex_nap(db, project, cfg):
    locs = db.scalars(select(m.GbpLocation).where(m.GbpLocation.project_id == project.id)).all()
    pages, _ = _latest_pages(db, project)
    if not locs:
        return "NEEDS_CONNECTION", "Add the business's GBP location (or connect Google Business Profile) to compare NAP.", {}
    phones = {ph[-10:] for p in pages.values() for ph in p.get("phones", [])}
    out = [{"location": l.name, "phone": (l.nap or {}).get("phone"), "on_site": re.sub(r"\D", "", (l.nap or {}).get("phone", ""))[-10:] in phones} for l in locs]
    return "COMPLETED", f"{sum(1 for o in out if not o['on_site'])} of {len(out)} locations have a phone number not found on the site.", {"locations": out}


@executor("gsc_anomalies")
def ex_anom(db, project, cfg):
    from .monitor import run_monitor
    r = run_monitor(db, project)
    return "COMPLETED", r["summary"], r


@executor("client_reporting")
def ex_report(db, project, cfg):
    run = db.scalar(select(mm.MonitorRun).where(mm.MonitorRun.project_id == project.id).order_by(desc(mm.MonitorRun.started_at)).limit(1))
    done = db.scalar(select(func.count()).select_from(m.Task).where(m.Task.project_id == project.id, m.Task.status == "done",
                                                                   m.Task.updated_at >= func.now() - timedelta(days=7)))
    openi = db.execute(select(mm.MonitorIssue.severity, func.count()).where(mm.MonitorIssue.project_id == project.id,
                                                                           mm.MonitorIssue.status.in_(("open", "reopened")))
                       .group_by(mm.MonitorIssue.severity)).all()
    s = f"{run.summary if run else 'No monitoring run yet.'} Tasks completed this week: {done}. Open issues: {dict(openi) or 'none'}."
    return "COMPLETED", s, {"briefing": run.summary if run else None, "tasks_done_7d": done, "open_issues": dict(openi)}


@executor("blog_ideas")
def ex_blog(db, project, cfg):
    ideas = kwsvc.blog_ideas(db, project, limit=(cfg or {}).get("limit", 20))
    if not ideas:
        return "NEEDS_DATA", "No uncovered informational/commercial queries yet — needs GSC queries or competitor keyword data.", {}
    created = 0
    if (cfg or {}).get("create_drafts", True):
        existing = {t for (t,) in db.execute(select(m.ContentItem.title).where(m.ContentItem.project_id == project.id))}
        for i in ideas[: (cfg or {}).get("drafts", 5)]:
            if i["title"] not in existing:
                db.add(m.ContentItem(organisation_id=project.organisation_id, project_id=project.id, type="blog", state="draft",
                                     channel="website", title=i["title"]))
                created += 1
    return "COMPLETED", f"{len(ideas)} blog ideas from real queries and gaps; {created} added as drafts to the content pipeline.", {"ideas": ideas}


@executor("content_brief")
def ex_brief(db, project, cfg):
    kw = (cfg or {}).get("keyword")
    ideas = kwsvc.blog_ideas(db, project, limit=1) if not kw else [{"primary_keyword": kw, "supporting_keywords": [], "title": kwsvc.title_for(kw, kwsvc.intent(kw), date.today().year), "intent": kwsvc.intent(kw)}]
    if not ideas:
        return "NEEDS_DATA", "No keyword to brief — set a keyword on the activity or connect GSC.", {}
    idea = ideas[0]
    comps = [u for (u,) in db.execute(select(mm.CompetitorKeyword.url).where(mm.CompetitorKeyword.project_id == project.id, mm.CompetitorKeyword.is_own.is_(False),
                                                                              mm.CompetitorKeyword.keyword == idea["primary_keyword"], mm.CompetitorKeyword.url.isnot(None)).limit(5))]
    outline = []
    if comps:
        from collections import Counter as C
        heads = C()
        for u in comps[:3]:
            p = gaps._fetch(u)
            for h in p.get("h2") or []:
                heads[h] += 1
        outline = [h for h, _ in heads.most_common(12)]
    brief = {"title": idea["title"], "primary_keyword": idea["primary_keyword"], "supporting_keywords": idea.get("supporting_keywords", []),
             "intent": idea["intent"], "competitor_urls": comps, "competitor_outline": outline,
             "requirements": ["Answer the main question in the first 2 sentences", "Use the primary keyword in title, H1 and first paragraph",
                              "Add an FAQ section from related questions", "Link to the pillar page and 2 related articles", "Cite sources for statistics"]}
    try:
        text, usage = llm(db, project.organisation_id, "Turn this SEO brief into a writer-ready outline with H2/H3s, key points per section, "
                          "and 5 FAQs. Keep it factual.\n" + str(brief))
        brief["ai_outline"], brief["ai_model"] = text, usage.get("model")
    except NoAI:
        brief["ai_outline"] = None
    return "COMPLETED", f"Brief for “{brief['title']}”" + (" with AI outline." if brief.get("ai_outline") else " (data brief; connect an AI provider for a full outline)."), brief


@executor("social_calendar")
def ex_social_cal(db, project, cfg):
    items = db.scalars(select(m.ContentItem).where(m.ContentItem.project_id == project.id, m.ContentItem.type.in_(("blog", "landing")))
                       .order_by(desc(m.ContentItem.created_at)).limit(10)).all()
    plats = (cfg or {}).get("platforms", ["linkedin", "facebook", "x"])
    plan = [{"day": (date.today() + timedelta(days=i % 7)).isoformat(), "platform": plats[i % len(plats)], "content": it.title}
            for i, it in enumerate(items[:7])]
    for p in plan:
        db.add(m.ContentItem(organisation_id=project.organisation_id, project_id=project.id, type="social", state="draft",
                             channel=p["platform"], title=f"{p['platform']}: {p['content']}"[:300]))
    conn = ih.connected_map(db)
    pub = {pl: conn.get({"linkedin": "linkedin", "facebook": "meta", "instagram": "meta", "x": "x"}.get(pl, pl)) == "CONNECTED" for pl in plats}
    return "COMPLETED", f"{len(plan)} social posts planned as drafts. Publishing: " + ", ".join(f"{k} {'connected' if v else 'NOT CONNECTED'}" for k, v in pub.items()), \
        {"plan": plan, "publishing_connected": pub}


@executor("sxo_review")
def ex_sxo(db, project, cfg):
    from .monitor import search_agg, ga_agg, CTR_CURVE
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    if not anchor:
        return _need_gsc()
    pages = search_agg(db, project.id, ("page",), (anchor - timedelta(days=27), anchor), ("page", "detail"))
    ga = {}
    for (lp,), v in ga_agg(db, project.id, ("landing_page",), (anchor - timedelta(days=27), anchor)).items():
        ga[lp] = v
    rows = []
    for (pg,), v in pages.items():
        if v["impressions"] < 200 or not v["position"]:
            continue
        exp = CTR_CURVE.get(max(1, min(10, round(v["position"]))), .02)
        ctr = v["clicks"] / v["impressions"]
        g = ga.get(path_of(pg)) or {}
        eng = round(g["engaged"] / g["sessions"], 2) if g.get("sessions") else None
        flags = []
        if v["position"] <= 10 and ctr < exp * 0.6:
            flags.append("low CTR for position (snippet/intent mismatch)")
        if eng is not None and eng < 0.4:
            flags.append("low engagement after the click")
        if flags:
            rows.append({"page": pg, "position": v["position"], "ctr": round(ctr * 100, 2), "expected_ctr": round(exp * 100, 1),
                         "engagement_rate": eng, "flags": flags})
    return "COMPLETED", f"{len(rows)} pages with search-experience problems (click or post-click).", {"pages": rows[:100]}


# ------------------------------------------------------------------ llm-only executors
def _llm_task(prompt_fn, label):
    def run(db, project, cfg):
        try:
            text, usage = llm(db, project.organisation_id, prompt_fn(db, project, cfg))
        except NoAI as e:
            return "NEEDS_CONNECTION", f"{label} needs an AI provider. {e}. The task has been handed to the team.", {}
        return "COMPLETED", f"{label} ready for review ({usage.get('model')}).", {"text": text, "model": usage.get("model"), "citations": usage.get("citations")}
    return run


def _draft_prompt(db, project, cfg):
    it = db.scalar(select(m.ContentItem).where(m.ContentItem.project_id == project.id, m.ContentItem.type == "blog", m.ContentItem.state == "draft")
                   .order_by(m.ContentItem.created_at).limit(1))
    title = (cfg or {}).get("title") or (it.title if it else project.name)
    return (f"Write a well-structured, factual blog article titled “{title}” for {project.client_name or project.name}. Use H2/H3 headings, "
            "a direct answer in the first paragraph, an FAQ section, and no filler. Mark any statistic that needs a source with [source needed].")


EXECUTORS["article_drafting"] = _llm_task(_draft_prompt, "Article draft")
EXECUTORS["eeat"] = _llm_task(lambda db, p, c: f"Review E-E-A-T signals for the page {c.get('url', 'homepage')} of {p.name}: author, credentials, "
                              "first-hand experience, sources, trust pages. List concrete fixes.", "E-E-A-T review")
EXECUTORS["ai_slop_detector"] = _llm_task(lambda db, p, c: "Identify generic, repetitive, unsupported or AI-sounding passages in this text and "
                                          f"suggest specific rewrites:\n{c.get('text', '(paste text on the activity config)')}", "AI slop review")
EXECUTORS["translation"] = _llm_task(lambda db, p, c: f"Translate and localise for {c.get('language', 'Spanish')} ({c.get('market', 'Spain')}): "
                                     f"{c.get('text', '(set text on the activity)')}", "Translation")
EXECUTORS["digital_pr"] = _llm_task(lambda db, p, c: f"Suggest 5 data-led digital PR angles for {p.client_name or p.name} with a one-paragraph pitch each.", "Digital PR angles")
EXECUTORS["chatgpt_citations"] = lambda db, p, c: _citations(db, p, c)
EXECUTORS["social_posts"] = _llm_task(lambda db, p, c: f"Write a LinkedIn post, an X post and a Facebook post promoting the latest content of {p.name}. "
                                      "Each with a hook and 2-3 hashtags.", "Social post drafts")


def _citations(db, project, cfg):
    qs = (cfg or {}).get("questions") or [k["keyword"] for k in kwsvc.project_keywords(db, project) if k["intent"] == "informational"][:5]
    if not qs:
        return "NEEDS_DATA", "No questions to test — set questions on the activity or connect GSC.", {}
    doms = [urlparse_domain(s.domain) for s in db.scalars(select(m.Site).where(m.Site.project_id == project.id))]
    res = []
    for q in qs[:5]:
        try:
            text, usage = llm(db, project.organisation_id, q, prefer=("perplexity", "openai", "claude", "gemini"))
        except NoAI as e:
            return "NEEDS_CONNECTION", f"AI citation tracking needs an AI provider (Perplexity returns citations). {e}", {}
        cites = usage.get("citations") or []
        res.append({"question": q, "model": usage.get("model"), "cited": any(d in (" ".join(cites) + text) for d in doms), "citations": cites[:10]})
    share = round(100 * sum(r["cited"] for r in res) / len(res))
    return "COMPLETED", f"Cited in {share}% of {len(res)} answer-engine responses.", {"results": res, "share_of_voice_pct": share}


def urlparse_domain(d):
    from urllib.parse import urlparse
    return urlparse(d if "://" in d else "https://" + d).netloc


@executor("seo_strategy")
def ex_strategy(db, project, cfg):
    """Quarterly plan: fix-first (critical/high issues), then opportunities, then growth work — each item with owner,
    steps and the prevention strategy from the fix playbooks."""
    from . import playbooks
    from .monitor import TEAM_LABEL
    issues = db.scalars(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id == project.id,
                                                       mm.MonitorIssue.status.in_(("open", "reopened", "in_progress", "verifying")))).all()
    if not issues and not db.scalar(select(func.count()).select_from(mm.SearchMetric).where(mm.SearchMetric.project_id == project.id)):
        return "NEEDS_DATA", "No monitoring data yet — connect or import Search Console / run a crawl first.", {}
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues = sorted(issues, key=lambda i: (i.is_opportunity, order.get(i.severity, 9)))
    month = {1: [], 2: [], 3: []}
    for i in issues:
        pb = playbooks.playbook(i.kind) or {}
        slot = 1 if (not i.is_opportunity and i.severity in ("critical", "high")) else (2 if not i.is_opportunity or i.severity == "high" else 3)
        month[slot].append({"item": i.title, "type": "opportunity" if i.is_opportunity else "fix", "severity": i.severity,
                            "owner": TEAM_LABEL.get(pb.get("responsible", i.category), i.category),
                            "solution": pb.get("solution") or i.recommendation, "strategy": (pb.get("strategy") or [None])[0],
                            "url": i.url, "query": i.query})
    ideas = kwsvc.blog_ideas(db, project, 6) if hasattr(kwsvc, "blog_ideas") else []
    for idea in ideas or []:
        month[3].append({"item": f"Publish: {idea.get('title')}", "type": "growth", "severity": "medium", "owner": "Content",
                         "solution": f"Target {idea.get('primary_keyword')} ({idea.get('intent')})", "strategy": "Topical authority from real queries"})
    plan = [{"month": k, "focus": {1: "Fix what is losing traffic", 2: "Recover and optimise", 3: "Grow"}[k], "items": v} for k, v in month.items()]
    n = sum(len(v) for v in month.values())
    return "COMPLETED", f"Quarterly plan with {n} items: {len(month[1])} urgent fixes, {len(month[2])} optimisations, {len(month[3])} growth items.", {"plan": plan}


@executor("change_log")
def ex_change_log(db, project, cfg):
    rows = db.scalars(select(mm.SiteChange).where(mm.SiteChange.project_id == project.id).order_by(desc(mm.SiteChange.detected_at)).limit(200)).all()
    if not rows:
        return "NEEDS_DATA", "No changes recorded yet — two crawls (or Screaming Frog imports) are needed to compare.", {}
    sev = {"robots": "critical", "indexable": "critical", "status": "high", "canonical": "high", "page_removed": "high", "title": "medium",
           "h1": "medium", "meta": "low", "word_count": "low", "page_added": "low"}
    out = [{"url": r.url, "field": r.field, "from": r.old_value, "to": r.new_value, "severity": sev.get(r.field, "medium"),
            "detected_at": r.detected_at, "impact": (r.impact or {}).get("verdict")} for r in rows]
    return "COMPLETED", f"{len(out)} SEO-relevant changes ({sum(1 for o in out if o['severity'] in ('critical', 'high'))} critical/high).", {"changes": out}


def run_agent(db, project, agent_key: str, cfg: dict | None = None):
    f = EXECUTORS.get(agent_key)
    if not f:
        a = BY_KEY.get(agent_key, {})
        return ("NEEDS_CONNECTION" if a.get("executor") == "source" else "HANDED_TO_HUMAN",
                f"{a.get('name', agent_key)} needs {a.get('needs') or 'a person'} — handed to the team.", {})
    return f(db, project, cfg or {})
