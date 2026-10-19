"""AI Studio generators (modules 24–30, 60): blog, news, whitepaper, publication, playbook, website /
landing pages and social posts.

Every generator has two halves:
  * a data half that runs without AI — briefs, outlines, FAQs, titles, meta, schema, page plans and charts
    built from the project's own data (profile, Search Console, keywords, crawl, skills);
  * an AI half (``generate``) that renders the prompt from the prompt library and calls ``agents.llm``.
    With no provider connected it records NOT CONNECTED (and the exact prompt) and changes nothing else.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select

from .. import models as m
from .. import models_ai as ma
from .. import models_monitor as mm
from . import agents, ai_content as ac, ai_engine as eng, ai_prompts as prompts, keywords as kwsvc, skills

CONTENT_TYPES = {
    "blog": {"label": "Blog post", "prompt": "blog.draft", "schema": "BlogPosting", "channel": "website", "max_tokens": 5000},
    "news": {"label": "News article", "prompt": "news.draft", "schema": "NewsArticle", "channel": "website", "max_tokens": 3000},
    "whitepaper": {"label": "Whitepaper", "prompt": "whitepaper.draft", "schema": "Report", "channel": "download", "max_tokens": 8000},
    "publication": {"label": "Thought-leadership article", "prompt": "publication.draft", "schema": "Article", "channel": "website", "max_tokens": 5000},
    "playbook": {"label": "Playbook / SOP", "prompt": "playbook.draft", "schema": "HowTo", "channel": "internal", "max_tokens": 4000},
    "web_page": {"label": "Website page", "prompt": "web_page.draft", "schema": "Service", "channel": "website", "max_tokens": 4000},
    "landing": {"label": "Landing page", "prompt": "web_page.draft", "schema": "Service", "channel": "website", "max_tokens": 4000},
    "social": {"label": "Social post", "prompt": "social.post", "schema": None, "channel": None, "max_tokens": 800},
}

PLATFORM_LIMITS = {"x": 280, "linkedin": 3000, "instagram": 2200, "facebook": 63206}
URL_RE = re.compile(r"https?://\S+")


def utc_today():
    return date.today()


# ------------------------------------------------------------------ shared context
def voice(db, project) -> dict:
    p = skills.get_profile(db, project)
    lst = lambda v: skills._list(v)  # noqa: E731
    return {"brand": p.get("brand"), "tone": p.get("tone"), "audience": p.get("audience"), "usp": p.get("usp"),
            "industry": p.get("industry"), "product": p.get("product"), "location": p.get("location"), "persona": p.get("persona"),
            "website": p.get("website"), "proof_points": p.get("proof_points"), "pain_points": lst(p.get("pain_points")),
            "primary_keywords": lst(p.get("primary_keywords")), "secondary_keywords": lst(p.get("secondary_keywords")),
            "brand_promise": p.get("brand_promise"), "phone": p.get("phone")}


def question_queries(db, project, about: str | None = None, limit: int = 8) -> list[dict]:
    """Real question-style Search Console queries (optionally sharing a stem with ``about``)."""
    qs = skills.gsc_queries(db, project, days=90, limit=1000)
    want = {kwsvc.stem(t) for t in kwsvc.tokens(about or "")}
    out = []
    for q in qs:
        if not re.match(r"^(how|what|why|when|where|who|which|is|are|can|does|do|should)\b", q["query"]):
            continue
        if want and not (want & {kwsvc.stem(t) for t in kwsvc.tokens(q["query"])}):
            continue
        out.append({"question": q["query"][:1].upper() + q["query"][1:].rstrip("?") + "?", "impressions": q["impressions"], "answer": None})
    return out[:limit]


def new_item(db, user_id, project, ctype: str, title: str, data: dict, channel: str | None = None) -> m.ContentItem:
    it = m.ContentItem(organisation_id=project.organisation_id, project_id=project.id, type=ctype, state="draft",
                       channel=channel or CONTENT_TYPES[ctype]["channel"], title=title[:300])
    db.add(it)
    db.flush()
    db.add(ma.ContentMeta(content_item_id=it.id, organisation_id=project.organisation_id, generator=ctype, data=data,
                          current_version=0, created_by=user_id))
    db.add(ma.ContentTransition(organisation_id=project.organisation_id, content_item_id=it.id, from_state="draft", to_state="draft",
                                action="create", by_user=user_id))
    db.flush()
    return it


# ------------------------------------------------------------------ 24 blog
OUTLINES = {
    "informational": ["What is {k}?", "Why {k} matters", "How to approach {k}: step by step", "Common mistakes to avoid", "Expert tips"],
    "commercial": ["What to look for in {k}", "Options compared", "Costs and pricing factors", "How to choose", "Our recommendation"],
    "transactional": ["{K}: what's included", "Prices and what affects them", "How it works", "Why choose {brand}", "Next steps"],
    "local": ["{K}: local overview", "Services available", "Prices and response times", "Why choose {brand}", "How to book"],
    "navigational": ["About {brand}", "What we offer", "How to get started"],
}


def blog_sources(db, project) -> dict:
    return {"ideas": kwsvc.blog_ideas(db, project, limit=30), "queries": skills.gsc_queries(db, project, days=90, limit=60),
            "voice": voice(db, project)}


def build_outline(keyword: str, intent: str, supporting: list[str], faqs: list[dict], brand: str | None, extra: list[str] | None = None) -> list[dict]:
    k = keyword
    heads = [h.format(k=k, K=k[:1].upper() + k[1:], brand=brand or "us") for h in OUTLINES.get(intent, OUTLINES["informational"])]
    out = [{"h2": h, "h3": []} for h in heads]
    sup = [s for s in supporting if s.lower() != keyword.lower()][:6]
    for i, s in enumerate(sup):  # supporting keywords become H3s spread over the body sections
        out[min(i % max(1, len(out) - 1) + 1, len(out) - 1)]["h3"].append(kwsvc._title_case(s))
    for h in (extra or [])[:4]:
        if h and all(h.lower() != o["h2"].lower() for o in out):
            out.insert(len(out) - 1, {"h2": h, "h3": [], "source": "competitor outline"})
    if faqs:
        out.append({"h2": "FAQ", "h3": [f["question"] for f in faqs]})
    return out


def outline_text(outline: list[dict]) -> str:
    lines = []
    for o in outline:
        lines.append(f"## {o['h2']}")
        lines += [f"   ### {h}" for h in o.get("h3") or []]
    return "\n".join(lines)


def create_blog(db, user_id, project, keyword: str, title: str | None = None, supporting: list[str] | None = None,
                word_count: int = 1500, notes: str | None = None, use_agent_brief: bool = True) -> m.ContentItem:
    v = voice(db, project)
    keyword = keyword.strip().lower()
    intent = kwsvc.intent(keyword, v.get("brand"))
    idea = next((i for i in kwsvc.blog_ideas(db, project, limit=100) if i["primary_keyword"] == keyword), None)
    supporting = supporting if supporting is not None else (idea or {}).get("supporting_keywords", [])
    title = title or (idea or {}).get("title") or kwsvc.title_for(keyword, intent, utc_today().year)
    faqs = question_queries(db, project, keyword)
    agent_brief = {}
    if use_agent_brief:
        with eng.usage_scope(project_id=project.id, user_id=user_id, task_type="brief"):
            status, summary, out = agents.run_agent(db, project, "content_brief", {"keyword": keyword})
        if status == "COMPLETED":
            agent_brief = {"summary": summary, "competitor_urls": out.get("competitor_urls", []), "competitor_outline": out.get("competitor_outline", []),
                           "requirements": out.get("requirements", []), "ai_outline": out.get("ai_outline")}
    outline = build_outline(keyword, intent, supporting, faqs, v.get("brand"), agent_brief.get("competitor_outline"))
    data = {"primary_keyword": keyword, "supporting_keywords": supporting, "intent": intent, "word_count": word_count,
            "outline": outline, "faqs": faqs, "brief_notes": notes, "voice": v, "agent_brief": agent_brief,
            "idea": idea, "seo": {"meta_title": title[:60], "meta_description": None, "slug": ac.slugify(keyword)},
            "requirements": agent_brief.get("requirements") or ["Answer the main question in the first 2 sentences",
                                                                "Use the primary keyword in title, H1 and first paragraph", "Add an FAQ section"]}
    return new_item(db, user_id, project, "blog", title, data)


# ------------------------------------------------------------------ 25 news
def news_topics(db, project, days_new: int = 14) -> list[dict]:
    """Queries that first appeared in the last ``days_new`` days of Search Console data, plus rising queries."""
    anchor = db.scalar(select(func.max(mm.SearchMetric.date)).where(mm.SearchMetric.project_id == project.id))
    if not anchor:
        return []
    S = mm.SearchMetric
    rows = db.execute(select(S.query, func.min(S.date), func.sum(S.impressions), func.sum(S.clicks))
                      .where(S.project_id == project.id, S.grain == "detail", S.query.is_not(None), S.date > anchor - timedelta(days=90))
                      .group_by(S.query)).all()
    recent = dict(db.execute(select(S.query, func.sum(S.impressions)).where(S.project_id == project.id, S.grain == "detail",
                                                                          S.date > anchor - timedelta(days=7)).group_by(S.query)).all())
    prior = dict(db.execute(select(S.query, func.sum(S.impressions)).where(S.project_id == project.id, S.grain == "detail",
                                                                         S.date > anchor - timedelta(days=14), S.date <= anchor - timedelta(days=7))
                            .group_by(S.query)).all())
    out = []
    for q, first, imp, clk in rows:
        if first > anchor - timedelta(days=days_new):
            out.append({"query": q, "kind": "new", "first_seen": first.isoformat(), "impressions": int(imp or 0), "clicks": int(clk or 0),
                        "headline": kwsvc.title_for(q, kwsvc.intent(q), anchor.year)})
        else:
            r, p = int(recent.get(q) or 0), int(prior.get(q) or 0)
            if r >= 20 and r >= 2 * max(p, 1):
                out.append({"query": q, "kind": "rising", "first_seen": first.isoformat(), "impressions": int(imp or 0), "clicks": int(clk or 0),
                            "growth_pct": round(100 * (r - p) / max(p, 1)), "headline": kwsvc.title_for(q, kwsvc.intent(q), anchor.year)})
    out.sort(key=lambda x: (x["kind"] != "new", -x["impressions"]))
    return out[:50]


def source_headlines(url: str, limit: int = 30) -> dict:
    """Fetch a user-entered news source through the SSRF-safe crawler and list its article headlines."""
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    from .crawler import CrawlBlocked, Crawler
    c = Crawler(url, max_pages=1, render_sample=0, mobile_sample=0, external_check_limit=0, delay=0)
    try:
        r = c.fetch(url)
    except CrawlBlocked as e:
        return {"url": url, "error": str(e), "headlines": []}
    except Exception as e:  # noqa: BLE001 — network errors are reported, not raised
        return {"url": url, "error": f"{type(e).__name__}: {e}"[:200], "headlines": []}
    if r.get("status") != 200 or not r.get("body"):
        return {"url": url, "error": f"HTTP {r.get('status')}", "headlines": []}
    soup = BeautifulSoup(r["body"], "lxml")
    seen, out = set(), []
    for h in soup.find_all(["h1", "h2", "h3"]):
        a = h.find("a") or h.find_parent("a")
        t = h.get_text(" ", strip=True)
        if len(t.split()) >= 3 and t.lower() not in seen:
            seen.add(t.lower())
            out.append({"title": t[:200], "url": urljoin(url, a["href"]) if a and a.get("href") else url})
    for a in soup.select("article a[href], main a[href]"):
        t = a.get_text(" ", strip=True)
        if len(t.split()) >= 4 and t.lower() not in seen:
            seen.add(t.lower())
            out.append({"title": t[:200], "url": urljoin(url, a["href"])})
    return {"url": url, "error": None, "headlines": out[:limit]}


def create_news(db, user_id, project, headline: str, angle: str | None, source_urls: list[str], query: str | None) -> m.ContentItem:
    notes = []
    for u in source_urls[:5]:
        f = skills.page_facts(u)
        if f.get("status") == 200:
            notes.append({"url": u, "title": f.get("title"), "description": f.get("meta_description"), "date": f.get("date_modified"),
                          "excerpt": " ".join(f.get("sample_paragraphs") or [])[:1500]})
        else:
            notes.append({"url": u, "error": f.get("error") or f"HTTP {f.get('status')}"})
    qd = None
    if query:
        qd = next((t for t in news_topics(db, project) if t["query"] == query), None)
    data = {"headline": headline, "angle": angle, "sources": notes, "query": query, "query_data": qd, "voice": voice(db, project),
            "primary_keyword": query, "seo": {"meta_title": headline[:60], "slug": ac.slugify(headline)}}
    return new_item(db, user_id, project, "news", headline, data)


# ------------------------------------------------------------------ 26 whitepaper
WP_SECTIONS = ["Executive summary", "The challenge", "What our data shows", "Approach and methodology", "Recommendations", "Conclusion"]


def project_charts(db, project) -> dict:
    """Chart datasets from the project's real metrics (empty when there is no data)."""
    charts = {}
    S = mm.SearchMetric
    anchor = db.scalar(select(func.max(S.date)).where(S.project_id == project.id))
    if anchor:
        grain = "total" if db.scalar(select(S.id).where(S.project_id == project.id, S.grain == "total").limit(1)) else "detail"
        rows = db.execute(select(func.date_trunc("week", S.date), func.sum(S.clicks), func.sum(S.impressions))
                          .where(S.project_id == project.id, S.grain == grain, S.date > anchor - timedelta(days=182))
                          .group_by(func.date_trunc("week", S.date)).order_by(func.date_trunc("week", S.date))).all()
        if rows:
            charts["search_trend"] = {"title": "Weekly organic clicks and impressions (Search Console)", "type": "line",
                                      "series": ["clicks", "impressions"],
                                      "data": [{"label": w.date().isoformat(), "clicks": int(c or 0), "impressions": int(i or 0)} for w, c, i in rows]}
        q = skills.gsc_queries(db, project, days=90, limit=8)
        if q:
            charts["top_queries"] = {"title": "Top search queries by clicks (last 90 days)", "type": "bar", "series": ["clicks"],
                                     "data": [{"label": x["query"][:40], "clicks": x["clicks"]} for x in sorted(q, key=lambda x: -x["clicks"])]}
    kws = kwsvc.project_keywords(db, project)
    if kws:
        mix = Counter(k["intent"] for k in kws)
        charts["intent_mix"] = {"title": "Keyword portfolio by search intent", "type": "bar", "series": ["keywords"],
                                "data": [{"label": k, "keywords": n} for k, n in mix.most_common()]}
    return charts


def data_summary(charts: dict) -> str:
    lines = []
    st = charts.get("search_trend")
    if st and st["data"]:
        first, last = st["data"][0], st["data"][-1]
        lines.append(f"Weekly organic clicks went from {first['clicks']} (week of {first['label']}) to {last['clicks']} (week of {last['label']}); "
                     f"impressions from {first['impressions']} to {last['impressions']}.")
    tq = charts.get("top_queries")
    if tq:
        lines.append("Top queries by clicks: " + "; ".join(f"{d['label']} ({d['clicks']})" for d in tq["data"][:5]) + ".")
    im = charts.get("intent_mix")
    if im:
        lines.append("Keyword intent mix: " + ", ".join(f"{d['label']} {d['keywords']}" for d in im["data"]) + ".")
    return "\n".join(lines) or "(no analytics data available — do not cite numbers)"


def create_whitepaper(db, user_id, project, title: str, audience: str | None, sections: list[str] | None, include_data: bool = True) -> m.ContentItem:
    charts = project_charts(db, project) if include_data else {}
    secs = [s.strip() for s in (sections or WP_SECTIONS) if s.strip()][:15]
    chart_section = next((s for s in secs if re.search(r"data|finding|result|evidence", s, re.I)), secs[min(2, len(secs) - 1)])
    data = {"title": title, "audience": audience or voice(db, project).get("audience"), "sections": secs, "charts": charts,
            "chart_section": chart_section, "voice": voice(db, project), "seo": {"meta_title": title[:60], "slug": ac.slugify(title)}}
    return new_item(db, user_id, project, "whitepaper", title, data)


def sections_from_markdown(md: str) -> list[dict]:
    out, cur = [], None
    for line in (md or "").splitlines():
        h = re.match(r"^##\s+(.+)$", line)
        if h and not line.startswith("###"):
            cur = {"title": h.group(1).strip(), "body": []}
            out.append(cur)
        elif cur is not None:
            cur["body"].append(line)
    return [{"title": s["title"], "body": "\n".join(s["body"]).strip()} for s in out]


# ------------------------------------------------------------------ 27 publication
def eeat_titles(db, project, topic: str | None, metric: str | None = None, count: int = 15) -> dict:
    v = skills.merged_inputs(skills.get_profile(db, project), {"topic": topic, "metric": metric, "count": count})
    res = skills.a_eeat_titles(v, {})
    rows = next(iter(res.get("tables", {}).values()), [])
    C = skills.EEAT_COLUMNS
    return {"titles": [{"id": r[C[0]], "title": r[C[1]], "subtitle": r[C[2]], "intent": r[C[3]], "eeat_focus": r[C[4]], "persona": r[C[5]],
                        "pain": r[C[6]], "metric": r[C[7]], "keyword": r[C[8]], "trend": r[C[9]], "experience_proof": r[C[10]],
                        "expertise": r[C[11]], "authority": r[C[12]], "trust": r[C[13]], "seo_variant": r[C[14]], "social_hook": r[C[15]],
                        "email_subject": r[C[16]]} for r in rows], "notes": res.get("notes", []), "skill": "eeat_titles"}


def create_publication(db, user_id, project, t: dict) -> m.ContentItem:
    keep = ("title", "subtitle", "eeat_focus", "intent", "persona", "pain", "metric", "keyword", "experience_proof", "expertise", "authority", "trust", "seo_variant")
    data = {k: t.get(k) for k in keep}
    data.update({"primary_keyword": t.get("keyword"), "voice": voice(db, project),
                 "seo": {"meta_title": (t.get("seo_variant") or t["title"])[:60], "slug": ac.slugify(t["title"])}})
    return new_item(db, user_id, project, "publication", t["title"], data)


# ------------------------------------------------------------------ 28 playbook
def clean_playbook(pb: dict, roles: list[str]) -> dict:
    steps = []
    for s in (pb.get("steps") or [])[:60]:
        if not isinstance(s, dict) or not str(s.get("title") or "").strip():
            continue
        cl = [str(c.get("text") if isinstance(c, dict) else c).strip()[:300] for c in (s.get("checklist") or [])][:20]
        steps.append({"title": str(s["title"]).strip()[:200], "owner": (str(s.get("owner") or "").strip() or (roles[0] if roles else "Owner"))[:80],
                      "details": str(s.get("details") or "").strip()[:4000], "checklist": [c for c in cl if c]})
    return {"purpose": str(pb.get("purpose") or "").strip()[:2000], "scope": str(pb.get("scope") or "").strip()[:2000],
            "steps": steps, "kpis": [str(k).strip()[:200] for k in (pb.get("kpis") or []) if str(k).strip()][:20]}


def playbook_markdown(title: str, pb: dict, roles: list[str]) -> str:
    out = [f"# {title}", ""]
    if pb.get("purpose"):
        out += ["## Purpose", pb["purpose"], ""]
    if pb.get("scope"):
        out += ["## Scope", pb["scope"], ""]
    if roles:
        out += ["## Roles", *[f"- {r}" for r in roles], ""]
    out += ["## Procedure", ""]
    for i, s in enumerate(pb.get("steps") or [], 1):
        out += [f"### {i}. {s['title']}", f"**Owner:** {s['owner']}", ""]
        if s.get("details"):
            out += [s["details"], ""]
        out += [f"- [ ] {c}" for c in s.get("checklist") or []] + [""]
    if pb.get("kpis"):
        out += ["## KPIs", *[f"- {k}" for k in pb["kpis"]], ""]
    return "\n".join(out).strip() + "\n"


def create_playbook(db, user_id, project, title: str, goal: str | None, roles: list[str], steps: list[dict] | None) -> m.ContentItem:
    roles = [r.strip()[:80] for r in roles if r.strip()][:20] or ["Owner"]
    pb = clean_playbook({"steps": steps or [], "purpose": goal}, roles)
    it = new_item(db, user_id, project, "playbook", title, {"goal": goal, "roles": roles, "playbook": pb, "voice": voice(db, project)})
    if pb["steps"]:
        ac.add_version(db, it, playbook_markdown(title, pb, roles), author_id=user_id, origin="manual", note="Initial steps")
    return it


# ------------------------------------------------------------------ 29 / 60 website + landing pages
PAGE_SECTIONS = {
    "landing": ["hero", "problem", "solution", "benefits", "proof", "process", "faq", "cta"],
    "service": ["hero", "overview", "whats_included", "process", "pricing", "proof", "faq", "cta"],
    "location": ["hero", "local_intro", "services_in_area", "local_proof", "faq", "cta"],
}
SECTION_LABEL = {"hero": "Hero", "problem": "The problem", "solution": "Our solution", "benefits": "Benefits", "proof": "Proof",
                 "process": "How it works", "faq": "FAQ", "cta": "Call to action", "overview": "Overview", "whats_included": "What's included",
                 "pricing": "Pricing", "local_intro": "Local introduction", "services_in_area": "Services in the area", "local_proof": "Local proof"}


def page_seo(page_type: str, keyword: str, service: str | None, location: str | None, v: dict) -> dict:
    brand = v.get("brand") or ""
    base = (service or keyword).strip()
    h1 = f"{base[:1].upper()}{base[1:]}" + (f" in {location}" if location else "")
    title = f"{h1} | {brand}".strip(" |")
    if len(title) > 60:
        title = h1[:60]
    desc = None
    if v.get("usp"):
        desc = f"{h1} from {brand}. {v['usp']}".strip()
        if len(desc) > 155:
            desc = desc[:152].rsplit(" ", 1)[0] + "…"
    slug = "/" + ac.slugify(f"{base}-{location}" if location else base)
    return {"h1": h1, "seo": {"meta_title": title, "meta_description": desc, "slug": slug}}


def create_page(db, user_id, project, ctype: str, page_type: str, keyword: str, service: str | None, location: str | None,
                cta: str | None = None) -> m.ContentItem:
    v = voice(db, project)
    keyword = keyword.strip().lower()
    intent = kwsvc.intent(keyword, v.get("brand"))
    sd = page_seo(page_type, keyword, service, location, v)
    faqs = question_queries(db, project, f"{keyword} {service or ''}", limit=6)
    data = {"page_type": page_type, "primary_keyword": keyword, "intent": intent, "service": service, "location": location or None,
            "h1": sd["h1"], "seo": sd["seo"], "cta": cta or "Get a free quote", "voice": v, "faqs": faqs,
            "sections": [{"key": k, "heading": SECTION_LABEL[k], "body": ""} for k in PAGE_SECTIONS[page_type]],
            "url": (v.get("website") or "").rstrip("/") + sd["seo"]["slug"] if v.get("website") else None}
    return new_item(db, user_id, project, ctype, sd["h1"], data)


def plan_website(db, project, services: list[str], locations: list[str]) -> list[dict]:
    """Service pages, location pages and service × location pages with Search Console demand where it exists."""
    v = voice(db, project)
    services = services or (v.get("primary_keywords") or []) + (v.get("secondary_keywords") or [])
    locations = locations or skills._list(v.get("location"))
    demand = {q["query"]: q["impressions"] for q in skills.gsc_queries(db, project, days=90, limit=2000)}
    existing = {(i.title or "").lower() for i in db.scalars(select(m.ContentItem).where(m.ContentItem.project_id == project.id,
                                                                                       m.ContentItem.type.in_(("web_page", "landing"))))}
    pages = []

    def add(page_type, kw, svc, loc):
        sd = page_seo(page_type, kw, svc, loc, v)
        toks = {kwsvc.stem(t) for t in kwsvc.tokens(kw)}
        imp = sum(i for q, i in demand.items() if toks and toks <= {kwsvc.stem(t) for t in kwsvc.tokens(q)})
        pages.append({"page_type": page_type, "keyword": kw, "service": svc, "location": loc, "h1": sd["h1"], "slug": sd["seo"]["slug"],
                      "meta_title": sd["seo"]["meta_title"], "impressions": imp, "exists": sd["h1"].lower() in existing})
    for s in services[:15]:
        add("service", s.lower(), s, None)
    for loc in locations[:25]:
        for s in services[:10]:
            add("location", f"{s} in {loc}".lower(), s, loc)
    pages.sort(key=lambda p: (p["page_type"] != "service", -p["impressions"]))
    return pages


# ------------------------------------------------------------------ 30 social
def x_length(text: str) -> int:
    """X counts every URL as 23 characters (t.co wrapping)."""
    return len(URL_RE.sub("x" * 23, text or ""))


def full_post(platform: str, post: str, hashtags: list[str], link: str | None) -> str:
    parts = [post.strip()]
    if link:
        parts.append(link)
    if hashtags:
        parts.append(" ".join(hashtags))
    return "\n\n".join(p for p in parts if p)


def post_length(platform: str, text: str) -> int:
    return x_length(text) if platform == "x" else len(text or "")


def clean_hashtags(tags) -> list[str]:
    out = []
    for t in tags if isinstance(tags, list) else re.split(r"[\s,]+", str(tags or "")):
        w = re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if w and len(w) <= 60:
            out.append("#" + w)
    return list(dict.fromkeys(out))[:30]


def fit(platform: str, post: str, hashtags: list[str], link: str | None) -> tuple[str, list[str], bool]:
    """Trim a post (then hashtags) so the full text fits the platform limit. Returns (post, hashtags, trimmed)."""
    lim = PLATFORM_LIMITS[platform]
    trimmed = False
    tags = list(hashtags)
    while tags and post_length(platform, full_post(platform, post, tags, link)) > lim and len(tags) > 1:
        tags.pop()
        trimmed = True
    while post_length(platform, full_post(platform, post, tags, link)) > lim:
        over = post_length(platform, full_post(platform, post, tags, link)) - lim
        cut = post[: max(0, len(post) - over - 1)]
        sent = re.match(r"^(.*[.!?])\s", cut + " ", re.S)
        post = (sent.group(1) if sent and len(sent.group(1)) > len(cut) * 0.6 else cut.rsplit(" ", 1)[0] + "…").strip()
        trimmed = True
        if not post:
            tags = []
            break
    return post, tags, trimmed


def social_hashtags(source_title: str, v: dict) -> list[str]:
    toks = [t for t in kwsvc.tokens(source_title) if len(t) > 3][:3]
    kws = (v.get("primary_keywords") or [])[:2]
    tags = ["".join(w.capitalize() for w in k.split()) for k in kws] + [t.capitalize() for t in toks]
    if v.get("brand"):
        tags.append(re.sub(r"\W", "", v["brand"]))
    return clean_hashtags(tags)[:5]


def social_source(db, project, source_item_id=None, url: str | None = None) -> dict:
    if source_item_id:
        it = db.get(m.ContentItem, source_item_id)
        if not it or it.project_id != project.id:
            raise ValueError("Source content item not found in this project")
        mt = db.get(ma.ContentMeta, it.id)
        lv = ac.latest_version(db, it)
        d = (mt.data if mt else {}) or {}
        return {"title": it.title or "", "summary": (d.get("seo") or {}).get("meta_description") or ac.plain(lv.body if lv else "")[:400],
                "link": d.get("url") or url, "source_item_id": str(it.id)}
    f = skills.page_facts(url)
    if f.get("status") != 200:
        raise ValueError(f"Could not read {url}: {f.get('error') or 'HTTP ' + str(f.get('status'))}")
    return {"title": f.get("title") or "", "summary": f.get("meta_description") or " ".join(f.get("sample_paragraphs") or [])[:400],
            "link": url, "source_url": url}


def create_social(db, user_id, project, platforms: list[str], src: dict) -> list[m.ContentItem]:
    v = voice(db, project)
    tags = social_hashtags(src["title"], v)
    out = []
    for pl in platforms:
        first = re.split(r"(?<=[.!?])\s", src.get("summary") or "")[0] if src.get("summary") else ""
        draft = (src["title"] + (f"\n\n{first}" if first and first.lower() != src["title"].lower() else "")).strip()
        post, ht, trimmed = fit(pl, draft, tags, src.get("link"))
        data = {"platform": pl, "limit": PLATFORM_LIMITS[pl], "post": post, "hashtags": ht, "link": src.get("link"), "source": src,
                "voice": v, "trimmed": trimmed}
        data["length"] = post_length(pl, full_post(pl, post, ht, src.get("link")))
        it = new_item(db, user_id, project, "social", f"{pl}: {src['title']}"[:300], data, channel=pl)
        ac.add_version(db, it, full_post(pl, post, ht, src.get("link")), author_id=user_id, origin="template",
                       note="Template from the source title and description (no AI)")
        out.append(it)
    return out


def set_social(db, user_id, item, post: str, hashtags, link: str | None) -> m.ContentVersion:
    mt = ac.meta_of(db, item)
    d = dict(mt.data or {})
    pl = d.get("platform") or item.channel
    tags = clean_hashtags(hashtags)
    text = full_post(pl, post, tags, link)
    n = post_length(pl, text)
    if n > PLATFORM_LIMITS[pl]:
        raise ValueError(f"{pl} posts are limited to {PLATFORM_LIMITS[pl]} characters (this one is {n})")
    d.update(post=post, hashtags=tags, link=link, length=n, trimmed=False)
    mt.data = d
    return ac.add_version(db, item, text, author_id=user_id, origin="manual")


# ------------------------------------------------------------------ AI generation (all types)
def prompt_values(db, item: m.ContentItem, data: dict, instructions: str | None = None) -> tuple[str, dict]:
    v = data.get("voice") or {}
    base = {"brand": v.get("brand"), "tone": v.get("tone"), "audience": v.get("audience"), "usp": v.get("usp"), "industry": v.get("industry")}
    t = item.type
    if t == "blog":
        vals = {**base, "title": item.title, "primary_keyword": data.get("primary_keyword"), "intent": data.get("intent"),
                "supporting_keywords": data.get("supporting_keywords") or "(none)", "word_count": data.get("word_count") or 1500,
                "outline": outline_text(data.get("outline") or []), "faqs": [f["question"] for f in data.get("faqs") or []] or "(choose 4 common questions)",
                "brief_notes": "; ".join(filter(None, [data.get("brief_notes"), *(data.get("requirements") or [])])) or "(none)"}
    elif t == "news":
        vals = {**base, "headline": data.get("headline") or item.title, "angle": data.get("angle") or "(straight news)",
                "source_notes": data.get("sources") or "(no sources given — write only what can be verified and mark gaps [source needed])",
                "query_data": data.get("query_data") or "(none)"}
    elif t == "whitepaper":
        vals = {**base, "title": item.title, "audience": data.get("audience") or base.get("audience"),
                "sections": "\n".join(f"- {s}" for s in data.get("sections") or WP_SECTIONS), "data_summary": data_summary(data.get("charts") or {})}
    elif t == "publication":
        vals = {**base, **{k: data.get(k) for k in ("title", "subtitle", "eeat_focus", "experience_proof", "expertise", "authority", "trust", "metric")}}
        vals["audience"] = data.get("persona") or base.get("audience")
    elif t == "playbook":
        pb = data.get("playbook") or {}
        vals = {**base, "title": item.title, "goal": data.get("goal") or "(not set)", "roles": data.get("roles") or ["Owner"],
                "existing_steps": [f"{s['title']} (owner: {s['owner']})" for s in pb.get("steps") or []] or "(none)"}
    elif t in ("web_page", "landing"):
        vals = {**base, "page_type": data.get("page_type"), "h1": data.get("h1"), "primary_keyword": data.get("primary_keyword"),
                "intent": data.get("intent"), "service": data.get("service") or "(n/a)", "location": data.get("location") or "(n/a)",
                "proof_points": v.get("proof_points") or "(none given — use [proof needed] placeholders)",
                "sections": ", ".join(s["key"] for s in data.get("sections") or [])}
    elif t == "social":
        src = data.get("source") or {}
        vals = {**base, "platform": data.get("platform"), "limit": data.get("limit"), "source_title": src.get("title"),
                "source_summary": src.get("summary"), "link": data.get("link") or "(none)", "hashtags": " ".join(data.get("hashtags") or [])}
    else:
        raise ValueError(f"No generator for content type {t}")
    key = CONTENT_TYPES[t]["prompt"]
    text, info = prompts.build(db, key, vals)
    if instructions:
        text += f"\n\nAdditional instructions from the editor: {instructions}"
    return text, {**info, "key": key}


def apply_output(db, item: m.ContentItem, text: str, meta: dict, user_id) -> m.ContentVersion:
    mt = ac.meta_of(db, item)
    d = dict(mt.data or {})
    t = item.type
    body = text
    if t in ("blog", "news", "whitepaper", "publication"):
        seo, body = ac.split_meta(text)
        d["seo"] = {**(d.get("seo") or {}), **seo}
        if t == "blog":
            got = ac.faqs_from_markdown(body)
            if got:
                d["faqs"] = [{"question": f["question"], "answer": f["answer"]} for f in got]
        if t == "whitepaper":
            d["section_bodies"] = sections_from_markdown(body)
    elif t == "playbook":
        j = ac.extract_json(text)
        if not isinstance(j, dict) or not j.get("steps"):
            raise ValueError("The AI response did not contain playbook steps in the expected JSON shape")
        pb = clean_playbook(j, d.get("roles") or [])
        d["playbook"] = pb
        body = playbook_markdown(item.title, pb, d.get("roles") or [])
    elif t in ("web_page", "landing"):
        j = ac.extract_json(text)
        if not isinstance(j, dict) or not j.get("sections"):
            raise ValueError("The AI response did not contain page sections in the expected JSON shape")
        by = {s.get("key"): s for s in j["sections"] if isinstance(s, dict)}
        d["sections"] = [{"key": s["key"], "heading": str((by.get(s["key"]) or {}).get("heading") or s["heading"])[:200],
                          "body": str((by.get(s["key"]) or {}).get("body") or s.get("body") or "")[:8000]} for s in d.get("sections") or []]
        faqs = [{"question": str(f.get("question"))[:300], "answer": str(f.get("answer"))[:2000]} for f in j.get("faqs") or []
                if isinstance(f, dict) and f.get("question") and f.get("answer")]
        if faqs:
            d["faqs"] = faqs
        body = page_markdown(d)
    elif t == "social":
        pm = re.search(r"POST:\s*(.+?)(?:\n\s*HASHTAGS:|$)", text, re.S)
        hm = re.search(r"HASHTAGS:\s*(.+)$", text, re.M)
        post = (pm.group(1) if pm else text).strip()
        tags = clean_hashtags(hm.group(1)) if hm else d.get("hashtags") or []
        pl = d["platform"]
        post, tags, trimmed = fit(pl, post, tags, d.get("link"))
        body = full_post(pl, post, tags, d.get("link"))
        d.update(post=post, hashtags=tags, trimmed=trimmed, length=post_length(pl, body))
    d["generation"] = {"status": "COMPLETED", "model": meta.get("model"), "provider": meta.get("provider"),
                       "tokens_in": meta.get("tokens_in"), "tokens_out": meta.get("tokens_out"), "cost": meta.get("cost")}
    mt.data = d
    return ac.add_version(db, item, body, author_id=user_id, origin="ai", model=meta.get("model"), data=d)


def page_markdown(d: dict) -> str:
    out = [f"# {d.get('h1')}", ""]
    for s in d.get("sections") or []:
        if s["key"] == "faq":
            continue
        if s.get("body"):
            out += [f"## {s['heading']}", s["body"], ""]
    if d.get("faqs") and any(f.get("answer") for f in d["faqs"]):
        out += ["## FAQ", ""]
        for f in d["faqs"]:
            if f.get("answer"):
                out += [f"### {f['question']}", f["answer"], ""]
    return "\n".join(out).strip() + "\n"


def generate(db, item: m.ContentItem, user_id=None, role: str | None = None, instructions: str | None = None) -> dict:
    """Render the prompt and call the AI gateway. NOT CONNECTED is recorded, not faked."""
    if item.state not in ac.EDITABLE:
        raise ac.TransitionError(409, f"Content in '{item.state}' can't be regenerated — reject it back to draft first")
    mt = ac.meta_of(db, item)
    data = dict(mt.data or {})
    prompt, info = prompt_values(db, item, data, instructions)
    project = db.get(m.Project, item.project_id)
    try:
        with eng.usage_scope(project_id=project.id, user_id=user_id, task_type=item.type):
            text, meta = agents.llm(db, item.organisation_id, prompt, max_tokens=CONTENT_TYPES[item.type]["max_tokens"],
                                    task_type=item.type)
    except agents.NoAI as e:
        data["generation"] = {"status": "BUDGET_EXCEEDED" if isinstance(e, agents.BudgetExceeded) else "NOT_CONNECTED",
                              "message": str(e), "prompt": prompt, "at": ac.utcnow().isoformat()}
        mt.data = data
        return {"connected": False, "status": data["generation"]["status"], "message": str(e), "prompt": prompt}
    try:
        v = apply_output(db, item, text, meta, user_id)
    except ValueError as e:
        data["generation"] = {"status": "UNPARSEABLE", "message": str(e), "prompt": prompt, "raw": text[:20000], "model": meta.get("model")}
        mt.data = data
        return {"connected": True, "status": "UNPARSEABLE", "message": str(e)}
    mt.data = {**mt.data, "last_prompt": prompt, "prompt_info": info}
    if item.state == "draft":
        ac.transition(db, (user_id, role) if user_id and role else None, item, "generated", note=f"AI draft v{v.version}")
    return {"connected": True, "status": "COMPLETED", "version": v.version, "model": meta.get("model"), "cost": meta.get("cost")}


def schema_for(db, item: m.ContentItem) -> list[dict]:
    mt = db.get(ma.ContentMeta, item.id)
    d = (mt.data if mt else {}) or {}
    project = db.get(m.Project, item.project_id)
    profile = skills.get_profile(db, project)
    lv = ac.latest_version(db, item)
    body = lv.body if lv else ""
    t = item.type
    if t in ("web_page", "landing"):
        return ac.page_schema(d, profile)
    if t == "playbook":
        pb = d.get("playbook") or {}
        return [{"@context": "https://schema.org", "@type": "HowTo", "name": item.title, "description": pb.get("purpose") or d.get("goal"),
                 "step": [{"@type": "HowToStep", "position": i, "name": s["title"], "text": s.get("details") or s["title"]}
                          for i, s in enumerate(pb.get("steps") or [], 1)]}] if pb.get("steps") else []
    if t == "social" or not body:
        return []
    kind = {"blog": "BlogPosting", "news": "NewsArticle", "publication": "Article", "whitepaper": "Report"}.get(t, "Article")
    nodes = ac.article_schema(item, body, d, profile, "Article" if kind == "Report" else kind)
    if kind == "Report":
        nodes[0]["@type"] = "Report"
        nodes[0]["name"] = nodes[0]["headline"]
    return nodes


def by_project_counts(db, project_id) -> dict:
    out = defaultdict(lambda: defaultdict(int))
    for t, s, n in db.execute(select(m.ContentItem.type, m.ContentItem.state, func.count()).where(m.ContentItem.project_id == project_id)
                              .group_by(m.ContentItem.type, m.ContentItem.state)):
        out[t][s] = n
    return {k: dict(v) for k, v in out.items()}
