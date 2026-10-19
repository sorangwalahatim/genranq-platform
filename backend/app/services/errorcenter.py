"""Unified Error Center logic: ingest (with dedupe), auto-classify into
SEO vs Developer buckets, plain-words solution templates, and smart assignment
to the team member whose role/department best fits the bucket."""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, select

from .. import models as m
from ..security import now
from .. import models_errorcenter as me

SOURCES = me.SOURCES

# Categories that need a developer (code / server), everything else goes to SEO.
DEVELOPER_CATEGORIES = {
    "broken_link", "server_error", "redirect_chain", "redirect_loop",
    "robots_txt", "sitemap", "page_speed", "render_issue", "mobile_usability",
    "https", "crawl_error", "js_content", "core_web_vitals",
}

SKILL_FOR_CATEGORY = {
    "broken_link": "seo-technical-audit",
    "server_error": "seo-technical-audit",
    "redirect_chain": "seo-technical-audit",
    "redirect_loop": "seo-technical-audit",
    "robots_txt": "seo-technical-audit",
    "sitemap": "seo-sitemap",
    "page_speed": "seo-technical-audit",
    "core_web_vitals": "seo-technical-audit",
    "render_issue": "seo-technical-audit",
    "mobile_usability": "seo-technical-audit",
    "https": "seo-technical-audit",
    "crawl_error": "seo-technical-audit",
    "js_content": "seo-technical-audit",
    "missing_title": "seo-page",
    "missing_meta": "seo-page",
    "duplicate_title": "seo-page",
    "thin_content": "seo-content-brief",
    "missing_alt": "seo-images",
    "large_image": "seo-images",
    "missing_h1": "seo-page",
    "heading_hierarchy": "seo-page",
    "canonical": "seo-technical-audit",
    "hreflang": "seo-hreflang",
    "schema": "seo-schema",
    "pagination": "seo-technical-audit",
    "internal_link": "seo-technical-audit",
    "orphan_page": "seo-technical-audit",
    "keyword_cannibalization": "seo-keyword-cluster",
    "coverage": "seo-google",
}

# Plain-words step-by-step fixes (non-technical reader).
SOLUTIONS = {
    "broken_link": (
        "A link on your site leads to a page that no longer exists.\n"
        "1. Open the page listed under 'Affected pages'.\n"
        "2. Find the broken link and either point it to the correct page, or remove it.\n"
        "3. If many pages link to it, set up a redirect from the old address to the right one.\n"
        "4. Re-check the page to confirm the link works."
    ),
    "server_error": (
        "Some pages show a server error (5xx) instead of loading.\n"
        "1. Open one of the listed pages to confirm the error.\n"
        "2. Ask your developer / hosting provider to check the server error log.\n"
        "3. Fix the underlying cause (often a plugin, code error or overloaded server).\n"
        "4. Re-check until the page loads normally."
    ),
    "redirect_chain": (
        "A page redirects through several hops before loading (A -> B -> C).\n"
        "1. Open the chain listed under 'Affected pages'.\n"
        "2. Change the first redirect to point straight to the final page.\n"
        "3. Keep chains to one hop; chains slow the site and waste crawl budget."
    ),
    "redirect_loop": (
        "Pages redirect in a circle and never load.\n"
        "1. Open the loop listed under 'Affected pages'.\n"
        "2. Break the circle: one address must be the final page with no further redirect.\n"
        "3. Re-check the page loads."
    ),
    "robots_txt": (
        "Your robots.txt file blocks something it shouldn't (or has an error).\n"
        "1. Open yoursite.com/robots.txt and read the flagged rule.\n"
        "2. Remove or fix the rule that blocks important pages or resources.\n"
        "3. Test the file in Google Search Console's robots.txt tester."
    ),
    "sitemap": (
        "Your sitemap has a problem (broken, missing pages, or wrong addresses).\n"
        "1. Open your sitemap.xml and find the flagged entries.\n"
        "2. Include only real, working pages with correct addresses.\n"
        "3. Re-submit the sitemap in Google Search Console."
    ),
    "page_speed": (
        "The page loads too slowly.\n"
        "1. Open the page and note what feels slow (images, banners, scripts).\n"
        "2. Compress images over 100 KB and use modern formats.\n"
        "3. Remove or delay scripts and popups you don't need.\n"
        "4. Re-run the speed test to confirm the improvement."
    ),
    "core_web_vitals": (
        "Google measured a poor loading experience (LCP / CLS / INP).\n"
        "1. Open the PageSpeed report for the page to see which metric fails.\n"
        "2. LCP slow: compress the main image and preload it. Layout shifting: set image sizes.\n"
        "3. Ask your developer for the specific failing element.\n"
        "4. Re-test until all three metrics are green."
    ),
    "render_issue": (
        "Google cannot properly see the page content (often JavaScript-heavy pages).\n"
        "1. Use Search Console's URL inspection to see what Google sees.\n"
        "2. Make sure key text and links appear without JavaScript.\n"
        "3. Ask your developer to fix server-side rendering for the page."
    ),
    "mobile_usability": (
        "The page is hard to use on a phone.\n"
        "1. Open the page on a phone: check text size, button spacing and sideways scrolling.\n"
        "2. Make tap targets at least 48px and text readable without zooming.\n"
        "3. Re-check with Search Console's mobile usability report."
    ),
    "missing_title": (
        "The page has no title, or it is empty.\n"
        "1. Open the page and write a clear title (under 60 characters) with the main keyword near the start.\n"
        "2. Keep every page's title unique.\n"
        "3. The title is what shows in Google results, so make it inviting."
    ),
    "missing_meta": (
        "The page has no meta description.\n"
        "1. Write a 120-155 character summary of the page with the main keyword.\n"
        "2. Make it read like an advert: say what the visitor gets.\n"
        "3. Keep every page's description unique."
    ),
    "duplicate_title": (
        "Two or more pages share the same title.\n"
        "1. List the pages shown under 'Affected pages'.\n"
        "2. Give each page its own unique, descriptive title.\n"
        "3. If pages are near-duplicates, consider merging them."
    ),
    "thin_content": (
        "The page has very little useful content.\n"
        "1. Open the page and read it as a customer would.\n"
        "2. Add genuinely helpful sections: details, FAQs, examples, images.\n"
        "3. Aim to answer the visitor's question better than competing pages."
    ),
    "missing_alt": (
        "Images on the page have no alt text.\n"
        "1. Open the page and look at each image.\n"
        "2. Add short alt text describing what the image shows (helps Google and blind visitors).\n"
        "3. Skip alt text only for purely decorative images."
    ),
    "large_image": (
        "Images on the page are over 100 KB and slow it down.\n"
        "1. Download each listed image.\n"
        "2. Compress it (tools like TinyPNG) and resize to the display size.\n"
        "3. Re-upload and re-check the page speed."
    ),
    "missing_h1": (
        "The page has no main heading (H1).\n"
        "1. Add one H1 to the page with the main topic/keyword.\n"
        "2. Keep it to a single H1; sub-sections use H2, H3 in order."
    ),
    "heading_hierarchy": (
        "Headings on the page are out of order (e.g. H3 before H2, or several H1s).\n"
        "1. Use one H1 for the page title.\n"
        "2. Nest sections logically: H2, then H3 under it, never skipping levels.\n"
        "3. Fix the order on the listed pages."
    ),
    "canonical": (
        "The page's canonical tag is missing or wrong.\n"
        "1. Decide which address is the 'main' version of the page.\n"
        "2. Point the canonical tag at that main address.\n"
        "3. Avoid canonicals that point to a different page's content."
    ),
    "hreflang": (
        "Language/region tags (hreflang) are missing or wrong.\n"
        "1. List each language version of the page.\n"
        "2. Tag each version with its language code and link them to each other.\n"
        "3. Include a self-referencing tag on every version."
    ),
    "schema": (
        "Structured data (schema) is missing or has errors.\n"
        "1. Pick the right schema type for the page (Article, Product, FAQ, LocalBusiness...).\n"
        "2. Add the JSON-LD markup with the page's real details.\n"
        "3. Test it in Google's Rich Results Test."
    ),
    "pagination": (
        "Paginated pages (page 2, 3...) have SEO issues.\n"
        "1. Make sure each paginated page has a self-referencing canonical.\n"
        "2. Keep titles unique across the series ('Page 2 of ...').\n"
        "3. Don't put paginated URLs in the sitemap."
    ),
    "internal_link": (
        "Important pages get too few internal links.\n"
        "1. Find related pages and blog posts.\n"
        "2. Add natural links with descriptive anchor text to the important page.\n"
        "3. Pages with more internal links get crawled and ranked better."
    ),
    "orphan_page": (
        "The page has no internal links pointing to it (orphan).\n"
        "1. Find 3-5 related pages.\n"
        "2. Add links to the orphan page with descriptive anchor text.\n"
        "3. Orphan pages are barely crawled, so this matters."
    ),
    "keyword_cannibalization": (
        "Several of your pages compete for the same keyword.\n"
        "1. Pick the strongest page as the main one for this keyword.\n"
        "2. Merge weaker pages into it, or re-target them at different keywords.\n"
        "3. Redirect removed pages to the main one."
    ),
    "coverage": (
        "Google Search Console reports a coverage problem for these pages.\n"
        "1. Open the coverage report in Search Console for the exact reason.\n"
        "2. Fix per the reason (submit for indexing, fix the error, or remove the page).\n"
        "3. Ask Google to re-crawl with URL inspection."
    ),
}

GENERAL_SOLUTION = (
    "An issue was found on your site.\n"
    "1. Read the details above to understand what is wrong.\n"
    "2. Fix it on the listed pages.\n"
    "3. Re-check to confirm it is gone."
)


def fingerprint(source: str, category: str, title: str) -> str:
    base = f"{source}|{category}|{(title or '').strip().lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:40]


def classify(category: str) -> tuple[str, str, str]:
    """Return (bucket, skill_key, solution) for a category."""
    bucket = "developer" if category in DEVELOPER_CATEGORIES else "seo"
    return bucket, SKILL_FOR_CATEGORY.get(category, "seo-technical-audit"), SOLUTIONS.get(category, GENERAL_SOLUTION)


def _project_users(db, project_id) -> list:
    """Team members that can be assigned: active non-client users in this project.
    Project is agency-scoped; clients never get internal error tasks."""
    p = db.get(m.Project, project_id)
    client_ids = set(p.client_user_ids or []) if p else set()
    return [u for u in db.scalars(select(m.User).where(m.User.is_active.is_(True)).order_by(m.User.created_at))
            if u.id not in client_ids and u.role != "client"]


def smart_assign(db, project_id, bucket: str) -> tuple[uuid.UUID | None, str]:
    """Pick the best-fit team member for a bucket. Returns (user_id, reason)."""
    users = _project_users(db, project_id)
    if not users:
        return None, "No team members yet — add your team first."
    dev_words = ("dev", "tech", "engineer", "web")
    seo_words = ("seo", "market", "content", "link", "social")
    words = dev_words if bucket == "developer" else seo_words

    def score(u) -> int:
        dept = (u.department or "").lower()
        s = 0
        if any(w in dept for w in words):
            s += 10
        s += {"manager": 3, "staff": 2, "admin": 1, "owner": 0}.get(u.role, 0)
        return s

    best = max(users, key=score)
    if score(best) == 0:
        return None, "Nobody on the team matches this work yet — assign manually."
    dept = f" ({best.department})" if best.department else ""
    why = "developer" if bucket == "developer" else "SEO"
    return best.id, f"{best.name or best.email}{dept} — {why} work, {best.role}."


def ingest(db, ctx, project_id, source: str, errors: list[dict]) -> dict:
    """Insert new errors or bump times_seen on re-seen ones. Never duplicates."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    created, updated = 0, 0
    for e in errors:
        category = (e.get("category") or "general").strip().lower()
        title = (e.get("title") or "").strip()
        if not title:
            continue
        fp = fingerprint(source, category, title)
        row = db.scalar(select(me.ErrorItem).where(me.ErrorItem.project_id == project_id, me.ErrorItem.fingerprint == fp))
        if row:
            row.times_seen = (row.times_seen or 0) + 1
            row.last_seen = now()
            if row.status in ("fixed", "verified"):
                row.status = "open"  # it came back — reopen
                row.ignore_reason = None
            urls = e.get("urls") or []
            if urls:
                merged = list(dict.fromkeys((row.urls or []) + [u for u in urls if u]))
                row.urls = merged
            updated += 1
            continue
        bucket, skill_key, solution = classify(category)
        assignee, _reason = smart_assign(db, project_id, bucket)
        row = me.ErrorItem(
            organisation_id=ctx.org_id, project_id=project_id, source=source, fingerprint=fp,
            category=category, severity=e.get("severity") if e.get("severity") in me.SEVERITIES else "warning",
            title=title, detail=e.get("detail"), urls=e.get("urls") or [],
            solution=e.get("solution") or solution, status="open",
            assignee=assignee, skill_key=e.get("skill_key") or skill_key, bucket=bucket,
            times_seen=1, last_seen=now(),
        )
        db.add(row)
        created += 1
    db.flush()
    return {"created": created, "updated": updated}


def ignore(db, item: me.ErrorItem, reason: str, by: uuid.UUID):
    if not (reason or "").strip():
        raise ValueError("A reason is required to ignore an error.")
    item.status = "ignored"
    item.ignore_reason = reason.strip()
    item.ignored_by = by
    item.ignored_at = now()


def unignore(db, item: me.ErrorItem):
    item.status = "open"
    item.ignore_reason = None
    item.ignored_by = None
    item.ignored_at = None
