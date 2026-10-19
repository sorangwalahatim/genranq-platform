"""Requirement map: Module Catalog row → Feature Backlog IDs → Database tables →
API Integrations → UI route → background jobs → permission → roadmap phase → build status.

The module/feature/integration rows themselves always come from the live spec; this
file holds only the cross-references that the workbook implies but does not state
as columns (the sheets have no explicit FK between modules and features).
"""
from __future__ import annotations

# Module # → backlog feature IDs
MODULE_FEATURES = {
    1: ["F-005"], 2: ["F-003"], 3: ["F-003"], 4: ["F-003"], 5: ["F-001", "F-002"],
    6: ["F-011", "F-012"], 7: ["F-006", "F-007", "F-008", "F-009", "F-010"], 8: ["F-006"], 9: ["F-044"], 10: ["F-016", "F-044"],
    11: ["F-050"], 12: ["F-011"], 13: ["F-013", "F-014", "F-015", "F-016", "F-017", "F-019"], 14: ["F-059", "F-060"],
    15: ["F-021"], 16: ["F-023"], 17: ["F-022"], 18: ["F-021"], 19: ["F-021"], 20: ["F-020"], 21: ["F-023"], 22: ["F-024"], 23: [],
    24: ["F-025", "F-026"], 25: ["F-026"], 26: ["F-026"], 27: ["F-026"], 28: ["F-026"], 29: ["F-052"], 30: ["F-026"],
    31: ["F-026"], 32: ["F-027"], 33: ["F-025", "F-028"], 34: ["F-004"], 35: ["F-030"], 36: ["F-030"], 37: ["F-010", "F-036"],
    38: ["F-029"], 39: ["F-030", "F-028"], 40: ["F-031"], 41: ["F-032"], 42: ["F-033"], 43: ["F-034"], 44: ["F-035"],
    45: ["F-028"], 46: ["F-037"], 47: ["F-010"], 48: ["F-018"], 49: ["F-038"], 50: ["F-039"], 51: ["F-040"], 52: ["F-041"],
    53: ["F-042"], 54: ["F-043"], 55: ["F-046"], 56: ["F-047"], 57: ["F-044", "F-045"], 58: ["F-048"], 59: ["F-049"],
    60: ["F-052"], 61: ["F-050"], 62: ["F-051"], 63: ["F-053"], 64: ["F-054"], 65: ["F-055"], 66: ["F-056"],
    67: ["F-057"], 68: ["F-057"], 69: ["F-057"], 70: ["F-058"], 71: ["F-057"], 72: ["F-057"],
    73: ["F-062"], 74: ["F-059"], 75: ["F-060"], 76: ["F-061"],
}
# Platform-wide features not owned by a single catalog module
PLATFORM_FEATURES = ["F-001", "F-002", "F-004", "F-005"]

MODULE_TABLES = {
    1: ["activity_log", "notification"], 2: ["task"], 3: ["time_entry"], 4: ["project"], 5: ["user", "organisation", "invite"],
    6: ["keyword", "rank_history"], 7: ["audit_run", "audit_issue", "audit_result", "audit_checklist_item", "report"],
    8: ["crawl_run", "crawl_page"], 9: ["backlink"], 10: ["backlink"], 11: ["keyword"], 12: ["rank_history"],
    13: ["supplier", "backlink", "backlink_check_history", "backlink_file", "credential"], 14: ["marketplace_listing", "order"],
    15: ["gbp_location"], 16: ["gbp_location"], 17: ["gbp_review"], 18: ["gbp_post"], 19: ["gbp_location", "competitor"],
    20: ["grid_scan", "grid_point"], 21: ["competitor"], 22: ["gbp_location", "site"], 23: [],
    24: ["content_item", "content_version"], 25: ["content_item", "content_version"], 26: ["content_item", "content_version"],
    27: ["content_item"], 28: ["content_item", "content_version"], 29: ["content_item"], 30: ["content_item"], 31: ["content_item", "gbp_post"],
    32: ["content_item"], 33: ["content_item", "schedule", "publish_job"], 34: ["job_schedule", "job"], 35: ["gbp_post", "schedule"],
    36: ["schedule", "publish_job", "campaign"], 37: ["report", "job_schedule"], 38: ["integration_connection"], 39: ["schedule", "content_item"],
    40: [], 41: ["lead", "form"], 42: ["lead", "campaign"], 43: ["lead"], 44: ["lead"], 45: ["approval"], 46: ["integration_connection"],
    47: ["organisation"], 48: ["credential", "vault.secret"], 49: ["integration_connection", "rate_limit_bucket"],
    50: ["ai_provider"], 51: ["prompt"], 52: ["skill"], 53: ["agent", "agent_team", "agent_run"], 54: ["ai_usage"],
    55: ["pagespeed_result"], 56: ["gsc_issue"], 57: ["audit_checklist_item", "audit_result"], 58: ["competitor", "audit_run"],
    59: ["seo_plan", "task"], 60: ["content_item"], 61: ["topical_map", "topic_node"], 62: ["content_plan", "content_brief"],
    63: [], 64: ["form", "form_submission"], 65: ["form", "form_submission"], 66: ["lead"],
    67: ["free_tool_lead"], 68: ["free_tool_lead"], 69: ["free_tool_lead"], 70: ["free_tool_lead"], 71: ["free_tool_lead"], 72: ["free_tool_lead"],
    73: ["service"], 74: ["marketplace_listing"], 75: ["order"], 76: ["wallet", "wallet_txn"],
}

MODULE_INTEGRATIONS = {
    6: ["Google Search Console", "Semrush"], 7: ["Screaming Frog", "Ahrefs", "Semrush", "Google PageSpeed Insights", "Google Search Console", "Claude (Anthropic)"],
    8: ["Screaming Frog"], 9: ["Ahrefs"], 10: ["Semrush"], 11: ["Ahrefs", "Semrush"], 12: ["Google Search Console"], 13: ["Semrush", "Ahrefs"],
    14: ["Payment gateway (Stripe)"], 15: ["Google Business Profile"], 16: ["Google Business Profile", "Google Maps / Places"],
    17: ["Google Business Profile", "Claude (Anthropic)"], 18: ["Google Business Profile"], 19: ["Google Maps / Places"],
    20: ["Google Maps / Places"], 21: ["Google Maps / Places"], 22: ["Google Business Profile"],
    **{n: ["Claude (Anthropic)", "OpenAI (ChatGPT)", "Google Gemini", "Perplexity"] for n in (24, 25, 26, 27, 28, 29, 30, 60, 61, 62, 63)},
    31: ["Google Business Profile", "Claude (Anthropic)"], 32: ["Google Gemini", "OpenAI (ChatGPT)"], 33: ["Claude (Anthropic)"],
    34: ["Screaming Frog", "Google Search Console", "Google PageSpeed Insights"], 35: ["Google Business Profile"],
    36: ["Meta (Facebook/Instagram)", "LinkedIn", "X (Twitter)"], 37: ["Looker Studio"],
    38: ["Meta (Facebook/Instagram)", "LinkedIn", "X (Twitter)"], 40: ["Meta (Facebook/Instagram)", "LinkedIn", "X (Twitter)"],
    41: ["Meta (Facebook/Instagram)", "LinkedIn", "Google Ads"], 42: ["Google Analytics 4", "Google Ads", "Microsoft Ads / Bing WMT"],
    43: ["HubSpot"], 44: ["Calendly"], 46: ["Microsoft Teams"], 49: ["Xero / MYOB / QuickBooks"],
    50: ["Claude (Anthropic)", "OpenAI (ChatGPT)", "Perplexity", "Google Gemini"], 54: ["Claude (Anthropic)", "OpenAI (ChatGPT)", "Perplexity", "Google Gemini"],
    55: ["Google PageSpeed Insights"], 56: ["Google Search Console"],
    57: ["Screaming Frog", "Ahrefs", "Semrush", "Google PageSpeed Insights", "Google Search Console", "Google Analytics 4"],
    58: ["Ahrefs", "Semrush"], 76: ["Payment gateway (Stripe)"],
}

MODULE_JOBS = {6: ["rank_check", "scheduled_rank_check"], 7: ["audit", "report"], 8: ["audit"], 34: ["scheduled_audit", "scheduled_rank_check"],
               37: ["report"], 49: ["integration_health"], 55: ["pagespeed"], 56: ["audit"], 57: ["audit"], 58: ["audit"], 59: ["audit"]}

# Build status in this release. live = working end-to-end; partial = working core with noted gaps;
# planned = data model + traceability in place, functionality scheduled per Roadmap.
STATUS = {n: "live" for n in range(1, 77)}
STATUS.update({
    23: "partial",  # Chrome extension: works unpacked; Chrome Web Store publishing is an operator step
    47: "partial",  # white-label domains verify via DNS; TLS certificates are provisioned by the operator
})
GAPS = {
    8: "Built-in crawler is live; Screaming Frog CLI engine activates when connected in Integrations, or import its CSV exports.",
    10: "Semrush's API has no toxicity field — toxic scores come from the Backlink Audit CSV export.",
    23: "Load-unpacked install from the Extension page; Chrome Web Store listing is an operator step.",
    47: "Custom domains are verified by DNS; TLS certificates for them are issued by the platform operator.",
}

ROUTES = {1: "/", 2: "/tasks", 3: "/time", 4: "/projects", 5: "/team", 6: "/rank-tracker", 7: "/audits", 8: "/crawls",
          9: "/backlinks/ahrefs", 10: "/backlinks/semrush", 11: "/keyword-research", 12: "/rank-tracker", 13: "/backlinks",
          14: "/marketplace", 15: "/local/gbp-audit", 16: "/local/categories", 17: "/local/reviews", 18: "/local/posts",
          19: "/local/scan", 20: "/local/grid", 21: "/local/competitors", 22: "/local/website-gbp", 23: "/extension",
          24: "/studio/blog", 25: "/studio/news", 26: "/studio/whitepaper", 27: "/studio/publication", 28: "/studio/playbook",
          29: "/studio/website", 30: "/studio/social-posts", 31: "/local/gmb-posts", 32: "/studio/images", 33: "/automation/content",
          34: "/automation", 35: "/local/automation", 36: "/social/automation", 37: "/reports/automation", 38: "/social/accounts",
          39: "/calendar", 40: "/social/analytics", 41: "/leads", 42: "/attribution", 43: "/leads/hubspot", 44: "/leads/calendly",
          45: "/approvals", 46: "/notifications/teams", 47: "/settings/white-label", 48: "/vault", 49: "/integrations",
          50: "/ai/providers", 51: "/ai/prompts", 52: "/ai/skills", 53: "/ai/agent-teams", 54: "/ai/usage", 55: "/pagespeed",
          56: "/integrations/gsc", 57: "/checklist", 58: "/competitors", 59: "/action-plans", 60: "/studio/landing-pages",
          61: "/topical-map", 62: "/content-plan", 63: "/relevink", 64: "/forms", 65: "/forms/connectors", 66: "/onboarding",
          67: "/tools/domain-age", 68: "/tools/broken-links", 69: "/tools/link-analyzer", 70: "/tools/backlink-monitor",
          71: "/tools/dofollow", 72: "/tools/website-finder", 73: "/services", 74: "/marketplace", 75: "/marketplace/orders",
          76: "/wallet"}

PERMISSION = {48: "access_credentials", 49: "manage_integrations", 5: "manage_users", 45: "approve", 37: "export"}


def build_map(spec: dict) -> list[dict]:
    feats = {f["id"]: f for f in spec["features"]}
    integ = {i["provider"] for i in spec["integrations"]}
    tables = {t["name"] for t in spec["schema"]}
    out = []
    for mod in spec["modules"]:
        n = mod["num"]
        fids = MODULE_FEATURES.get(n, [])
        out.append({
            **mod,
            "slug": mod["name"].lower().replace(" / ", "-").replace(" ", "-").replace("&", "and").replace("(", "").replace(")", "").replace("↔", "to").replace("/", "-"),
            "features": [feats[f] for f in fids if f in feats],
            "tables": MODULE_TABLES.get(n, []),
            "tables_in_spec": [t for t in MODULE_TABLES.get(n, []) if t in tables],
            "integrations": [i for i in MODULE_INTEGRATIONS.get(n, []) if i in integ],
            "jobs": MODULE_JOBS.get(n, []),
            "permission": PERMISSION.get(n, "view"),
            "status": STATUS.get(n, "planned"),
            "gap": GAPS.get(n),
            "route": ROUTES.get(n),
        })
    return out


def coverage(spec: dict) -> dict:
    mods = build_map(spec)
    mapped = {f for m in mods for f in (x["id"] for x in m["features"])} | set(PLATFORM_FEATURES)
    unmapped = [f["id"] for f in spec["features"] if f["id"] not in mapped]
    integ_mapped = {i for m in mods for i in m["integrations"]} | {"Google Ads", "Microsoft Ads / Bing WMT"}
    return {
        "modules": len(mods),
        "by_status": {s: sum(1 for m in mods if m["status"] == s) for s in ("live", "partial", "planned")},
        "features_unmapped": unmapped,
        "integrations_unmapped": [i["provider"] for i in spec["integrations"] if i["provider"] not in integ_mapped],
        "new_modules_without_mapping": [m["name"] for m in mods if m["num"] not in MODULE_FEATURES],
    }
