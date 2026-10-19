"""Services catalog (module 73): package definitions for the seven spec services.

The `service` table (global, seeded from the spec) carries id/name/type/tags; the package detail —
description, deliverables, turnaround, price tiers and add-ons — is versioned here with the code,
like other seed data. Prices are list prices in USD charged from the organisation wallet.
"""
from __future__ import annotations

CATALOG: dict[str, dict] = {
    "Link Building": {
        "description": "Manual outreach link building on relevant, real-traffic sites. Every placement is verified live before completion.",
        "deliverables": ["Prospect list approved by you before outreach", "Contextual dofollow links on DR 30+ sites",
                         "Live-link report with anchor text and metrics", "Replacement guarantee for 6 months"],
        "tat_days": 30, "tags": ["most-popular"],
        "tiers": [{"key": "starter", "name": "Starter", "price": 450, "includes": "3 links, DR 30+"},
                  {"key": "growth", "name": "Growth", "price": 1200, "includes": "8 links, DR 40+"},
                  {"key": "authority", "name": "Authority", "price": 2600, "includes": "15 links, DR 50+"}],
        "addons": [{"key": "anchor_plan", "name": "Anchor text strategy", "price": 150},
                   {"key": "rush", "name": "Rush delivery (−10 days)", "price": 300}],
    },
    "Guest Post": {
        "description": "Written and placed guest articles on niche-relevant blogs, with your link in the body copy.",
        "deliverables": ["Topic proposal", "1,000+ word article written by a niche writer", "Placement on a real blog with organic traffic",
                         "Live URL and indexation check"],
        "tat_days": 14, "tags": [],
        "tiers": [{"key": "standard", "name": "Standard", "price": 180, "includes": "DR 30–49"},
                  {"key": "premium", "name": "Premium", "price": 390, "includes": "DR 50–69"},
                  {"key": "elite", "name": "Elite", "price": 790, "includes": "DR 70+"}],
        "addons": [{"key": "extra_words", "name": "+500 words", "price": 60}, {"key": "image", "name": "Custom featured image", "price": 40}],
    },
    "Digital PR": {
        "description": "Data-led or reactive PR campaigns pitched to journalists for editorial links and brand coverage.",
        "deliverables": ["Campaign ideation workshop", "Press release + assets", "Journalist outreach", "Coverage report"],
        "tat_days": 45, "tags": ["premium"],
        "tiers": [{"key": "reactive", "name": "Reactive PR", "price": 1500, "includes": "Newsjacking, 5+ pitches"},
                  {"key": "campaign", "name": "Campaign", "price": 4500, "includes": "1 data campaign, 10+ links target"}],
        "addons": [{"key": "survey", "name": "Commissioned survey (1,000 respondents)", "price": 1200}],
    },
    "Niche Edits": {
        "description": "Links inserted into existing, already-indexed articles that rank and get traffic.",
        "deliverables": ["Relevant aged article", "Contextual link insertion", "Live URL report"],
        "tat_days": 7, "tags": [],
        "tiers": [{"key": "standard", "name": "Standard", "price": 120, "includes": "DR 25+"},
                  {"key": "premium", "name": "Premium", "price": 290, "includes": "DR 45+ with traffic 1k+"}],
        "addons": [{"key": "content_refresh", "name": "Paragraph rewrite around the link", "price": 35}],
    },
    "Content": {
        "description": "SEO content written from a brief: articles, landing pages and product copy, optimised for your target queries.",
        "deliverables": ["Keyword-mapped brief", "Draft for review", "One revision round", "On-page optimisation notes"],
        "tat_days": 10, "tags": [],
        "tiers": [{"key": "article", "name": "Article (1,500 words)", "price": 220, "includes": "1 article"},
                  {"key": "pack", "name": "Content pack", "price": 950, "includes": "5 articles"}],
        "addons": [{"key": "upload", "name": "CMS upload & formatting", "price": 30}],
    },
    "Brand Mentions": {
        "description": "Unlinked and linked brand mentions in listicles, roundups and community content to strengthen entity signals.",
        "deliverables": ["Mention opportunities list", "Placement of brand mentions", "Report with URLs"],
        "tat_days": 21, "tags": [],
        "tiers": [{"key": "five", "name": "5 mentions", "price": 350, "includes": "5 placements"},
                  {"key": "fifteen", "name": "15 mentions", "price": 900, "includes": "15 placements"}],
        "addons": [{"key": "ai_visibility", "name": "AI-assistant visibility check", "price": 90}],
    },
    "SEO Reseller": {
        "description": "White-label SEO fulfilment you resell to your clients under your brand.",
        "deliverables": ["White-label monthly report", "On-page fixes", "Monthly link package", "Dedicated account manager"],
        "tat_days": 30, "tags": ["premium"],
        "tiers": [{"key": "local", "name": "Local SEO", "price": 399, "includes": "per client / month"},
                  {"key": "national", "name": "National SEO", "price": 899, "includes": "per client / month"}],
        "addons": [{"key": "gbp", "name": "Google Business Profile management", "price": 99}],
    },
}


def detail(name: str) -> dict | None:
    return CATALOG.get(name)


def price(name: str, tier: str | None, addons: list[str]) -> tuple[float, dict, list[dict]]:
    d = CATALOG.get(name)
    if not d:
        raise ValueError(f"Service '{name}' has no orderable package")
    t = next((x for x in d["tiers"] if x["key"] == tier), None)
    if not t:
        raise ValueError(f"tier must be one of {[x['key'] for x in d['tiers']]}")
    by = {a["key"]: a for a in d["addons"]}
    bad = [a for a in addons if a not in by]
    if bad:
        raise ValueError(f"unknown add-ons {bad}; available {list(by)}")
    picked = [by[a] for a in dict.fromkeys(addons)]
    return float(t["price"]) + sum(a["price"] for a in picked), t, picked
