"""Module access control: which API paths belong to which module, and each user's effective access.

Module keys are "m<num>" for the 76 Module Catalog modules plus a few platform areas that span modules
(monitoring, workstreams, agents). A user with modules=None can use everything their role allows; otherwise
only the listed modules. Paths not tied to a module (auth, notifications, the project list, spec) are always open.
Owners are never restricted.
"""
from __future__ import annotations

import re

EXTRA_MODULES = [
    {"key": "monitoring", "name": "Monitoring, issues & dashboards", "area": "Automation & monitoring"},
    {"key": "workstreams", "name": "Strategies & activities (workstreams)", "area": "Automation & monitoring"},
    {"key": "agents", "name": "AI agents & prompt skills", "area": "AI Engine"},
]


def _r(*nums):
    return [f"m{n}" for n in nums]


LOCAL_ALL = _r(15, 16, 17, 18, 19, 20, 21, 22, 31, 35)
STUDIO_ALL = _r(24, 25, 26, 27, 28, 29, 30, 32, 33, 39, 60)

# (regex on the path after /api, module keys — access is allowed if the user has ANY of them). First match wins.
RULES: list[tuple[re.Pattern, list[str]]] = [(re.compile(p), k) for p, k in [
    # always open (no module): auth, profile, notifications, project list/detail, spec, rbac, health, email status, public endpoints
    (r"^/(auth|notifications|spec|rbac|health|access/me|email/status|public)(/|$)", []),
    (r"^/projects/?$", []), (r"^/projects/[^/]+/?$", []),
    (r"^/dashboard(/|$)", _r(1)), (r"^/activity(/|$)", _r(1)),
    (r"^/tasks(/|$)", _r(2)), (r"^/time(/|$)", _r(3)),
    (r"^/team/?$", []), (r"^/org(/|$)", []),  # people pickers and org branding are used everywhere; role rights still apply
    (r"^/(team|access)(/|$)", _r(5)),
    (r"^/(keywords|projects/[^/]+/rank-)", _r(6, 12)),
    (r"^/(audits|sites)(/|$)", _r(7, 8, 56, 57)), (r"^/checklist(/|$)", _r(57)),
    (r"^/projects/[^/]+/providers/ahrefs", _r(9)), (r"^/projects/[^/]+/providers/semrush", _r(10)),
    (r"^/projects/[^/]+/(providers|ext-backlinks)", _r(9, 10)),
    (r"^/projects/[^/]+/keyword-research", _r(11)),
    (r"^/(backlinks|backlink-files|disavow|suppliers|supplier-notes|supplier-prices)(/|$)", _r(13)),
    (r"^/projects/[^/]+/(backlinks|disavow|toxic)", _r(13)),
    (r"^/(relevink|projects/[^/]+/relevink)", _r(63)),
    (r"^/(page-compare|projects/[^/]+/page-compare|competitors)", _r(58)),
    (r"^/local/grid", _r(20)), (r"^/local/reviews", _r(17)), (r"^/local/automations", _r(35)),
    (r"^/local/categories", _r(16)), (r"^/local/posts", _r(18, 31, 35)), (r"^/local/", LOCAL_ALL),
    (r"^/studio/blog", _r(24)), (r"^/studio/news", _r(25)), (r"^/studio/whitepaper", _r(26)), (r"^/studio/publication", _r(27)),
    (r"^/studio/playbook", _r(28)), (r"^/studio/website", _r(29)), (r"^/studio/landing", _r(60)), (r"^/studio/social", _r(30)),
    (r"^/studio/images", _r(32)), (r"^/studio/", STUDIO_ALL),
    (r"^/ai/(providers|settings|prices|status)", _r(50) + STUDIO_ALL + _r(51, 52, 53, 54)),
    (r"^/ai/prompts", _r(51)), (r"^/ai/(agent-teams|team-runs|agents|agent-catalog)", _r(53)), (r"^/ai/usage", _r(54)),
    (r"^/topical-maps", _r(61)), (r"^/content-plans", _r(62)),
    (r"^/(skills|skill-runs|projects/[^/]+/(skills|skill-runs|profile))", _r(52) + ["agents"]),
    (r"^/(agents|projects/[^/]+/agents)", ["agents"]),
    (r"^/(strategies|project-strategies|activities|workload|projects/[^/]+/(strategies|activities|activity-runs))", ["workstreams"]),
    (r"^/(issues|gaps|content-watch|dashboards|playbooks|email/outbox|projects/[^/]+/(monitor|sources|import|members|content-watch|gaps))", ["monitoring"]),
    (r"^/social/(batches|content)", _r(33)), (r"^/social/(rules|campaigns|schedules|publish-jobs|items)", _r(36, 39)),
    (r"^/social/calendar", _r(39)), (r"^/social/(accounts|providers)", _r(38)), (r"^/social/analytics", _r(40)),
    (r"^/social/report-", _r(37)), (r"^/social/teams", _r(46)), (r"^/social/white-label", _r(47)),
    (r"^/social/(ext|extension|saved-businesses)", _r(23)),
    (r"^/(reports)(/|$)", _r(37)), (r"^/automation(/|$)", _r(34)), (r"^/action-plans", _r(59)),
    (r"^/approvals(/|$)", _r(45)), (r"^/vault(/|$)", _r(48)), (r"^/(integrations|jobs)(/|$)", _r(49)), (r"^/billing(/|$)", []),
    (r"^/leads/hubspot", _r(43)), (r"^/(leads/calendly|calendly)", _r(44)), (r"^/leads", _r(41)), (r"^/attribution", _r(42)),
    (r"^/forms/connectors", _r(65)), (r"^/forms", _r(64)), (r"^/onboarding", _r(66)), (r"^/services", _r(73)),
    (r"^/marketplace/orders", _r(75)), (r"^/marketplace", _r(14, 74, 75)), (r"^/wallet", _r(76)),
    (r"^/tools/(domain-age)", _r(67)), (r"^/tools/(broken-links)", _r(68)), (r"^/tools/(link-analyzer)", _r(69)),
    (r"^/(tools/monitors|monitors)", _r(70)), (r"^/tools/(dofollow)", _r(71)), (r"^/tools/(website-finder)", _r(72)),
    (r"^/tools", _r(67, 68, 69, 70, 71, 72)),
]]


def modules_for(path: str) -> list[str] | None:
    """Module keys guarding an /api path (without the /api prefix). [] = open to everyone; None = unmapped (open)."""
    for rx, keys in RULES:
        if rx.search(path):
            return keys
    return None


def catalog(spec: dict) -> list[dict]:
    from .spec.traceability import build_map
    mods = [{"key": f"m{m['num']}", "num": m["num"], "name": m["name"], "area": m["area"], "phase": m["phase"], "status": m["status"],
             "route": m["route"]} for m in build_map(spec)]
    return mods + [{**e, "num": None, "phase": None, "status": "live", "route": None} for e in EXTRA_MODULES]


def allowed(modules: set[str] | None, path: str) -> bool:
    if modules is None:
        return True
    keys = modules_for(path)
    return not keys or bool(set(keys) & modules)
