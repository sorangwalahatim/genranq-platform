"""Fix playbooks: for every issue type, the solution, step-by-step fix per team, who is responsible /
accountable / consulted (RACI), the deadline, how the fix is verified and the prevention strategy.

Used by the monitoring engine (issue → tasks → emails) and by any task that comes from an issue,
so nobody receives just an error: they receive what to do, why, who owns it and how it will be checked.
"""
from __future__ import annotations

from datetime import timedelta

SLA_DAYS = {"critical": 1, "high": 3, "medium": 7, "low": 21}
TEAM_LABEL = {"developer": "Developer", "seo": "SEO", "content": "Content", "backlink": "Backlink", "analytics": "Analytics", "pm": "Project manager"}


def P(responsible, solution, steps, strategy, consulted=(), informed=("pm",)):
    return {"responsible": responsible, "accountable": "pm", "consulted": list(consulted), "informed": list(informed),
            "solution": solution, "steps": steps, "strategy": strategy}


PLAYBOOKS: dict[str, dict] = {
    # ---------------------------------------------------------------- technical (developer-owned)
    "noindex_important": P("developer",
        "Remove the noindex directive from this important page and get it re-indexed.",
        {"developer": ["Open the page source and the HTTP response headers for the URL.",
                       "Remove <meta name=\"robots\" content=\"noindex\"> and any X-Robots-Tag: noindex header (check the CMS page setting, SEO plugin and server/CDN rules).",
                       "Check the template/staging flag that added it — fix it at the source so other pages aren't affected.",
                       "Deploy, purge the CDN cache, then mark the task done (the page is re-fetched automatically to verify)."],
         "seo": ["After the developer's fix, inspect the URL in Search Console and click Request re-indexing.",
                 "Check the XML sitemap still lists the URL and internal links point to it.",
                 "Watch impressions for the page over the next 7 days."]},
        ["Add a release checklist item: 'no noindex on production templates'.",
         "Block deploys that ship noindex to production (CI check on rendered HTML of key URLs).",
         "Keep this URL in the project's important URLs so monitoring flags it within a day."],
        consulted=("seo",)),
    "http_5xx": P("developer",
        "Fix the server error so the page returns 200 again.",
        {"developer": ["Reproduce: request the URL and read the server/application error logs at the time of the crawl.",
                       "Fix the failing code path, dependency or resource limit (timeouts, memory, database).",
                       "Add a health check / alert for this route; deploy and mark done (automatic re-check follows)."],
         "seo": ["If the error lasted more than a day, inspect the URL in Search Console and request re-indexing.",
                 "Check Crawl stats in Search Console for server-error spikes."]},
        ["Uptime monitoring on key templates with alerting to the developer on call.",
         "Load-test before campaigns; cache heavy pages."],
        consulted=("seo",)),
    "http_404": P("developer",
        "Restore the page or 301-redirect it to the closest relevant live page.",
        {"seo": ["Decide the target: restore the page, or choose the most relevant live URL for a 301 redirect.",
                 "List internal links pointing to the broken URL (crawl inlinks) for update."],
         "developer": ["Implement the 301 redirect (or restore the page) exactly as agreed with SEO.",
                       "Update internal links to point straight to the final URL; deploy and mark done."]},
        ["Require a redirect map whenever URLs are removed or renamed.",
         "Run a crawl after every release and compare against the previous crawl."],
        consulted=("seo",)),
    "canonical_changed": P("developer",
        "Point the canonical tag back to the correct (self-referencing) URL unless the change was intended.",
        {"seo": ["Confirm whether the new canonical was intentional (consolidation) or a mistake."],
         "developer": ["If a mistake: fix the canonical in the template/CMS so it self-references the page URL.",
                       "Deploy and mark done — the page is re-fetched automatically to verify."]},
        ["Canonical rules live in one template helper with unit tests.", "Change log review of SEO-critical tags on each release."],
        consulted=("seo",)),
    "missing_title": P("developer",
        "Add a unique, descriptive <title> to the page.",
        {"seo": ["Write the title (≤ 60 characters, primary keyword first, brand last)."],
         "developer": ["Make sure the template outputs the title field; publish SEO's title; mark done."]},
        ["Template must fall back to the page name if the SEO title field is empty."], consulted=("seo",)),
    "missing_h1": P("developer",
        "Add one H1 that states the page topic.",
        {"seo": ["Write the H1 (matches search intent, includes the main keyword naturally)."],
         "developer": ["Render the H1 in the template (exactly one per page); deploy; mark done."]},
        ["Design system: the page title component renders as H1 once."], consulted=("seo",)),
    "redirect_chain": P("developer",
        "Collapse the redirect chain into one 301 to the final URL.",
        {"developer": ["Update the first redirect to point directly to the final destination.",
                       "Update internal links to the final URL; deploy; mark done (re-checked automatically)."]},
        ["Keep a single redirect map; check new redirects against existing ones before adding."], consulted=("seo",)),
    "page_removed": P("seo",
        "Confirm whether the page removal was intended; restore or redirect it if it had traffic or links.",
        {"seo": ["Check the page's clicks, rankings and backlinks before removal.",
                 "Decide: restore, 301 to the closest page, or accept removal (410)."],
         "developer": ["Implement the decision (restore / 301 / 410) and mark done."]},
        ["Content removal needs SEO sign-off with a redirect plan."], consulted=("developer",)),
    # ---------------------------------------------------------------- search performance
    "traffic_drop": P("seo",
        "Find what caused the site-wide click drop and reverse it.",
        {"seo": ["Read the root cause below (where the loss is concentrated: pages, devices, countries, queries).",
                 "Check Search Console for manual actions, indexing drops and algorithm-update timing.",
                 "Compare top losing pages before/after: rankings, titles, content changes, internal links.",
                 "Agree the fix list with the developer/content leads and track it in these tasks."],
         "developer": ["Check releases and incidents in the drop window (robots, noindex, canonicals, speed, rendering).",
                       "Fix any technical regression found and mark done."],
         "content": ["Refresh the top losing pages against the current top-ranking pages (gap report)."]},
        ["Weekly monitoring review with the team leads.", "Change log for every SEO-relevant deploy.",
         "Diversify: grow pages for several query clusters so one loss can't sink the site."],
        consulted=("developer", "content")),
    "traffic_spike": P("seo", "Understand the spike and capitalise on it (or rule out bot traffic).",
        {"seo": ["Identify the queries/pages driving the spike.", "If genuine: strengthen the page (internal links, CTA, freshness).",
                 "If spam/bot: check analytics filters."]},
        ["Track which content types produce spikes and make more of them."]),
    "device_drop": P("developer", "Fix the device-specific regression (rendering, speed or usability).",
        {"developer": ["Test the top affected pages on the device (real device + Lighthouse/PSI).",
                       "Check JS errors, layout shifts, blocked resources and interstitials on that device.",
                       "Fix, deploy and mark done; recovery is measured on the next runs."],
         "seo": ["Check the device's indexing and Core Web Vitals report in Search Console.",
                 "Confirm rankings recover for the top affected queries."]},
        ["Mobile/desktop visual and performance checks in CI for key templates."], consulted=("seo",)),
    "country_drop": P("seo", "Recover the market: rankings, hreflang/geo-targeting and local competition.",
        {"seo": ["Check rankings in that country for the top queries.", "Validate hreflang and geo-targeting.",
                 "Review local competitors that gained."]},
        ["Per-market keyword tracking and hreflang validation after each release."], consulted=("developer",)),
    "page_traffic_drop": P("seo", "Recover the page's clicks by fixing the cause shown in the evidence.",
        {"seo": ["Compare the page's queries before/after (rank, CTR, impressions).",
                 "Check what changed on the page (change log) and on the competing pages.",
                 "Decide: refresh content, fix title/meta, add internal links, or fix a technical issue."],
         "content": ["Refresh the content using the gap analysis (missing sections, questions, freshness)."]},
        ["Quarterly refresh cycle for the top pages."], consulted=("content",)),
    "ctr_drop": P("seo", "Win back the click-through rate with better titles/snippets.",
        {"seo": ["Look at the SERP: new features (AI Overview, ads, snippets) or competitors' titles.",
                 "Rewrite title and meta description for the affected queries; add structured data where eligible."]},
        ["Test titles systematically and track CTR per page."], consulted=("content",)),
    "keyword_down": P("seo", "Recover the keyword's ranking.",
        {"seo": ["Compare the ranking page with the pages now above it (content depth, intent match, links).",
                 "Check for cannibalisation (another of our pages competing).",
                 "Plan the fix: on-page improvements, internal links, or link building."],
         "content": ["Update the page with the missing topics/questions from the gap report."]},
        ["Track priority keywords weekly; refresh pages before they slip."], consulted=("content", "backlink")),
    "tracked_keyword_down": P("seo", "Recover the tracked keyword's ranking.",
        {"seo": ["Check the target URL is still the ranking URL and is indexable.",
                 "Compare against the pages that overtook it and plan on-page / link fixes."]},
        ["Keep target URLs mapped for every tracked keyword."], consulted=("content",)),
    "lost_query": P("seo", "Find out why the query stopped showing and recover it if valuable.",
        {"seo": ["Check whether the page still exists and is indexable.", "Search the query: has intent or the SERP changed?",
                 "If valuable, re-optimise or create content for it."]}, ["Monitor high-value queries weekly."], consulted=("content",)),
    "new_query": P("content", "Capture the new demand: optimise an existing page or create a new one.",
        {"seo": ["Decide: optimise the page already getting impressions or brief a new page."],
         "content": ["Write/extend the content to fully answer the query (use the brief)."]},
        ["Review new queries weekly and feed them into the content plan."], consulted=("seo",), informed=("pm",)),
    "opp_striking_distance": P("seo", "Push this page-two keyword onto page one.",
        {"seo": ["Improve on-page relevance (title, H1, headings, intent).", "Add 3+ internal links with descriptive anchors."],
         "content": ["Expand the content with missing subtopics and FAQs."]},
        ["Run the striking-distance list every month."], consulted=("content",)),
    "opp_low_ctr": P("seo", "Lift CTR to the expected level for its position.",
        {"seo": ["Rewrite title/meta to match intent and add a clear benefit.", "Add eligible structured data (FAQ, product, review)."]},
        ["Quarterly CTR review against the expected CTR curve."]),
    "keyword_up": P("seo", "Consolidate the gain.", {"seo": ["Note what changed; replicate it on similar pages."]}, ["Document wins in the strategy log."]),
    "tracked_keyword_up": P("seo", "Consolidate the gain.", {"seo": ["Note what changed; replicate it on similar pages."]}, ["Document wins in the strategy log."]),
    "change_negative_impact": P("seo", "Revert or improve the change that was followed by a click drop.",
        {"seo": ["Review the before/after values of the change and the click impact.",
                 "Decide: revert, or test an improved version."],
         "developer": ["Implement the revert/alternative and mark done."]},
        ["SEO review before changing titles/H1/canonicals on important pages."], consulted=("developer", "content")),
    "blog_underperforming": P("content", "Rework the post with the automatic gap analysis.",
        {"content": ["Open the attached gap report: missing sections, questions, word count and entities vs ranking pages.",
                     "Rewrite/extend the post, add FAQ, examples and first-hand experience.",
                     "Update the publish date only if the content materially changed."],
         "seo": ["Confirm the target keyword matches the intent; check indexing.",
                 "Add 3+ internal links from related pages; request re-indexing."]},
        ["Brief every post from real queries and the competitor structure before writing.",
         "Checkpoints at 7/14/30/60/90 days are automatic — act at the first missed checkpoint."],
        consulted=("seo",)),
    # ---------------------------------------------------------------- analytics
    "tracking_stopped": P("analytics", "Restore analytics tracking immediately (data is being lost).",
        {"analytics": ["Check the GA4 tag/GTM container is loading on the site (Tag Assistant / network tab).",
                       "Check consent mode / cookie banner changes and recent deploys."],
         "developer": ["Restore the tag in the template / GTM snippet, deploy and mark done."]},
        ["Tag presence check in CI for key templates.", "Daily data-freshness alert (this monitor)."],
        consulted=("developer",)),
    "conversion_tracking": P("analytics", "Fix conversion tracking.",
        {"analytics": ["Check key events in GA4 DebugView; verify triggers and event names.", "Fix the GTM trigger/tag."],
         "developer": ["Restore form/checkout events in the code if they were removed."]},
        ["Test conversions after every release of forms/checkout."], consulted=("developer",)),
    "sessions_drop": P("analytics", "Explain and reverse the drop in sessions.",
        {"analytics": ["Split by channel, landing page and device to localise the drop.", "Rule out tracking issues first."],
         "seo": ["If organic: follow the search drop root cause."]},
        ["Weekly analytics review."], consulted=("seo",)),
    "conversions_drop": P("analytics", "Find why conversions fell (tracking, traffic quality or the page).",
        {"analytics": ["Check tracking first, then conversion rate by landing page/device/channel."],
         "content": ["Review the top converting pages for broken CTAs/forms or messaging changes."]},
        ["Conversion-rate monitoring per key landing page."], consulted=("developer", "content")),
    "landing_sessions_drop": P("seo", "Recover sessions on this landing page.",
        {"seo": ["Check rankings/queries for the page.", "Check page changes and technical status."]},
        ["Monitor the top landing pages weekly."], consulted=("analytics",)),
    "device_sessions_drop": P("developer", "Fix the device-specific session drop.",
        {"developer": ["Test the site on the device; check errors, speed, layout."], "analytics": ["Confirm tracking works on that device."]},
        ["Cross-device QA before releases."], consulted=("analytics",)),
    # ---------------------------------------------------------------- links
    "refdomain_loss": P("backlink", "Recover lost referring domains.",
        {"backlink": ["List the lost domains and the pages they linked to.",
                      "Contact the site owners for the most valuable ones (reclaim / fix broken targets).",
                      "Make sure the linked pages still exist on our site (no 404s)."]},
        ["Monthly link-loss review; keep relationships with key linking sites."], consulted=("seo",)),
    "backlink_lost": P("backlink", "Reclaim the lost backlink.",
        {"backlink": ["Check the source page: link removed, page removed or nofollowed?",
                      "Contact the publisher/supplier to restore it (check the CRM agreement)."]},
        ["Verify paid/placed links on a schedule (Backlink CRM)."], consulted=("seo",)),
}


def playbook(kind: str) -> dict | None:
    return PLAYBOOKS.get(kind)


def resolve(db, project, kind: str, severity: str, categories: list[str], routing: dict | None = None) -> dict:
    """The playbook with real people: responsible names per team, accountable lead, consulted teams, due date."""
    from .. import models as m
    from .monitor import ROUTING, fallback_assignees, team_members, utcnow
    pb = PLAYBOOKS.get(kind) or P(categories[0] if categories else "seo", "Investigate and fix the issue described.",
                                  {c: ["Review the evidence and recommendation.", "Fix, then mark the task done."] for c in (categories or ["seo"])},
                                  ["Review recurring issues of this kind in the weekly meeting."])
    routing = {**ROUTING, **(routing or {})}
    names = {}

    def people(team):
        ids = team_members(db, project.id, routing.get(team, team))
        out = []
        for u in ids:
            if u not in names:
                usr = db.get(m.User, u)
                names[u] = (usr.name or usr.email) if usr else str(u)
            out.append({"user_id": str(u), "name": names[u]})
        return out

    teams = list(dict.fromkeys([pb["responsible"]] + [c for c in categories if c in pb["steps"] or c == pb["responsible"]]))
    responsible = {t: people(t) for t in teams}
    acc = people("pm")
    if not acc:
        acc = [{"user_id": str(u), "name": (db.get(m.User, u).name or db.get(m.User, u).email)} for u in fallback_assignees(db, project.id)[:1]]
    due = (utcnow() + timedelta(days=SLA_DAYS.get(severity, 7))).date().isoformat()
    return {"solution": pb["solution"], "steps": pb["steps"], "strategy": pb["strategy"],
            "responsible_team": pb["responsible"], "responsible": responsible, "accountable": acc,
            "consulted": pb["consulted"], "informed": pb["informed"], "due": due, "sla_days": SLA_DAYS.get(severity, 7)}


def task_text(fix: dict, team: str) -> str:
    """The responsibility + steps + strategy block appended to a team's task."""
    lines = [f"Solution: {fix['solution']}", "", f"Steps for {TEAM_LABEL.get(team, team)}:"]
    steps = fix["steps"].get(team) or fix["steps"].get(fix["responsible_team"]) or []
    lines += [f"  {i}. {s}" for i, s in enumerate(steps, 1)]
    others = [t for t in fix["steps"] if t != team]
    for t in others:
        lines += ["", f"{TEAM_LABEL.get(t, t)} will:"] + [f"  - {s}" for s in fix["steps"][t]]
    who = lambda lst: ", ".join(p["name"] for p in lst) or "unassigned (project manager will assign)"  # noqa: E731
    lines += ["", "Responsibility:",
              f"  Responsible: {TEAM_LABEL.get(team, team)} — {who(fix['responsible'].get(team, []))}",
              f"  Accountable: {who(fix['accountable'])}",
              f"  Consulted: {', '.join(TEAM_LABEL.get(c, c) for c in fix['consulted']) or '—'}",
              f"  Due: {fix['due']} ({fix['sla_days']} day SLA)",
              "", "Prevention strategy:"] + [f"  - {s}" for s in fix["strategy"]]
    return "\n".join(lines)
