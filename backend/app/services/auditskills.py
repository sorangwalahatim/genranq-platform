"""Audit skill runners: each skill runs per project and stores findings.

Honest by design: real HTTP calls where feasible (PageSpeed API, page HTML,
robots.txt / sitemap.xml / llms.txt); CSV imports for Screaming Frog, Ahrefs
and Semrush exports; clear error messages when a required input (API key,
file, domain) is missing. No fake data is ever generated.
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from sqlalchemy import func, select

from .. import models_auditskills as sm

UA = {"User-Agent": "GenranqAuditSkill/1.0"}
HTTP_TIMEOUT = 25
MAX_URLS = 10

DEVICE_PROFILES = [
    "Android phone (small 360px)",
    "Android phone (large 412px)",
    "iPhone SE (375px)",
    "iPhone 14/15 (390px)",
    "iPhone Pro Max (430px)",
    "iPad (768px)",
    "Android tablet (800px)",
    "Desktop (1440px)",
]


def utcnow():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ helpers
def _fetch(url: str, timeout: int = HTTP_TIMEOUT) -> tuple[int | None, str, str]:
    """Fetch a URL. Returns (status_code, final_url, text); status None on
    connection failure (never raises for HTTP error statuses)."""
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, r.url, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, url, body
    except Exception:
        return None, url, ""


def _add(db, org_id, project_id, run_id, category, severity, url, title,
         detail=None, recommendation=None, data=None):
    db.add(sm.SkillFinding(
        organisation_id=org_id, project_id=project_id, run_id=run_id,
        category=category, severity=severity, url=url, title=title,
        detail=detail, recommendation=recommendation, data=data or {}))


def _clean_url(u: str) -> str:
    u = (u or "").strip()
    if u and not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


def _norm_domain(d: str) -> str:
    d = (d or "").strip().rstrip("/")
    d = re.sub(r"^https?://", "", d).split("/")[0]
    if not d or "." not in d:
        raise ValueError("Enter a domain like example.com")
    return "https://" + d


# ------------------------------------------------------------------ pagespeed
def _psi_call(url: str, strategy: str, api_key: str) -> dict:
    q = urllib.parse.urlencode({"url": url, "strategy": strategy, "category": "performance", "key": api_key})
    endpoint = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?{q}"
    req = urllib.request.Request(endpoint, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise RuntimeError(f"PageSpeed API error ({e.code}): {msg or 'check the API key and URL'}")
    except Exception as e:
        raise RuntimeError(f"PageSpeed request failed: {e}")
    lh = payload.get("lighthouseResult") or {}
    audits = lh.get("audits") or {}

    def ms(key):
        v = (audits.get(key) or {}).get("numericValue")
        return round(v / 1000, 2) if isinstance(v, (int, float)) else None

    score = (lh.get("categories") or {}).get("performance", {}).get("score")
    score = round(score * 100) if isinstance(score, (int, float)) else None
    opps = []
    for key, a in audits.items():
        d = a.get("details") or {}
        if d.get("type") == "opportunity":
            savings = a.get("numericValue")
            if isinstance(savings, (int, float)) and savings >= 300:
                opps.append({"key": key, "title": a.get("title"), "savings_s": round(savings / 1000, 2)})
    opps.sort(key=lambda o: -o["savings_s"])
    return {"lcp": ms("largest-contentful-paint"), "fcp": ms("first-contentful-paint"),
            "tti": ms("interactive"), "score": score, "opportunities": opps[:8]}


def run_pagespeed(db, org_id, project_id, run_id, params: dict) -> dict:
    api_key = (params.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("A Google PageSpeed API key is needed. Get a free key in Google Cloud Console "
                         "(enable the PageSpeed Insights API) and paste it here.")
    urls = [_clean_url(u) for u in (params.get("urls") or []) if (u or "").strip()][:MAX_URLS]
    if not urls:
        raise ValueError("Add at least one page URL to test.")
    strategies = params.get("strategies") or ["mobile", "desktop"]
    n = 0
    for url in urls:
        for strat in strategies:
            res = _psi_call(url, strat, api_key)
            score = res["score"]
            sev = "info" if score is None else ("critical" if score < 50 else "high" if score < 75 else "medium" if score < 90 else "low")
            opp_lines = [f"- {o['title']} (saves ~{o['savings_s']}s)" for o in res["opportunities"]]
            detail = (f"Score {score}/100 on {strat}. LCP {res['lcp']}s, FCP {res['fcp']}s, TTI {res['tti']}s."
                      + ("\nTop fixes:\n" + "\n".join(opp_lines) if opp_lines else "\nNo major opportunities found."))
            rec = "Work through the fixes above, starting with the biggest time saving." if opp_lines else "Page is fast; re-test after major site changes."
            if res["lcp"] is not None and res["lcp"] > 3:
                rec = f"LCP is over 3s ({res['lcp']}s) — this hurts rankings. " + rec
            _add(db, org_id, project_id, run_id, "speed", sev, url,
                 f"PageSpeed {strat}: {score}/100", detail, rec,
                 {"device": strat, "lcp": res["lcp"], "fcp": res["fcp"], "tti": res["tti"],
                  "score": score, "opportunities": res["opportunities"]})
            n += 1
    return {"urls": len(urls), "strategies": strategies, "measurements": n,
            "note": "Scores from the live Google PageSpeed Insights API."}


# ------------------------------------------------------------------ screaming frog (CSV)
_SF_SEV = {"error": "high", "warning": "medium", "notice": "low"}


def _sf_checks(db, org_id, project_id, run_id, cols: dict, row: dict, rownum: int):
    url = row.get(cols.get("address", "")) or row.get(cols.get("url", "")) or ""
    url = url.strip()
    status = (row.get(cols.get("status code", "")) or "").strip()
    if status and status[0] in "45":
        _add(db, org_id, project_id, run_id, "developer", "high", url or None,
             f"Broken page ({status})",
             f"Row {rownum}: this URL returns HTTP {status}.",
             "Fix the link or add a 301 redirect to the right page.")
    title = (row.get(cols.get("title 1", "")) or "").strip()
    if "title 1" in cols:
        if not title:
            _add(db, org_id, project_id, run_id, "seo", "medium", url or None, "Missing page title",
                 f"Row {rownum}: the page has no <title>.",
                 "Write a unique title under 60 characters with the main keyword near the start.")
        elif len(title) > 60:
            _add(db, org_id, project_id, run_id, "seo", "low", url or None, "Title too long",
                 f"Row {rownum}: title is {len(title)} characters.",
                 "Shorten the title to under 60 characters so it is not cut off in Google.")
    if "meta description 1" in cols and not (row.get(cols["meta description 1"]) or "").strip():
        _add(db, org_id, project_id, run_id, "seo", "medium", url or None, "Missing meta description",
             f"Row {rownum}: no meta description set.",
             "Write a 120-155 character description that makes people want to click.")
    if "h1-1" in cols and not (row.get(cols["h1-1"]) or "").strip():
        _add(db, org_id, project_id, run_id, "seo", "medium", url or None, "Missing H1 heading",
             f"Row {rownum}: the page has no H1.",
             "Add one clear H1 per page describing what the page is about.")


def run_screaming_frog(db, org_id, project_id, run_id, params: dict) -> dict:
    text_in = params.get("csv_text") or ""
    if not text_in.strip():
        raise ValueError("Upload a Screaming Frog crawl export (CSV). In Screaming Frog: Bulk Export or the Internal tab → Export.")
    rdr = csv.DictReader(io.StringIO(text_in))
    cols = {(c or "").lower().strip(): c for c in (rdr.fieldnames or [])}
    rows = list(rdr)
    if not rows:
        raise ValueError("The CSV has no data rows.")
    if {"address", "status code", "title 1", "meta description 1", "h1-1"} & set(cols):
        for i, row in enumerate(rows, 2):
            _sf_checks(db, org_id, project_id, run_id, cols, row, i)
    elif {"url", "issue"} <= set(cols):
        # generic fallback: url, issue, severity columns
        for i, row in enumerate(rows, 2):
            sev = (row.get(cols["severity"]) or "medium").strip().lower() if "severity" in cols else "medium"
            sev = {"error": "high", "warning": "medium", "notice": "low"}.get(sev, sev)
            if sev not in ("critical", "high", "medium", "low", "info"):
                sev = "medium"
            _add(db, org_id, project_id, run_id, "seo", sev, (row.get(cols["url"]) or "").strip() or None,
                 (row.get(cols["issue"]) or f"Issue on row {i}").strip(),
                 f"Row {i} from the Screaming Frog export.",
                 "Fix as described by the crawl issue, then re-crawl to verify.")
    else:
        raise ValueError("CSV not recognised. Use a Screaming Frog export (Address, Status Code, Title 1, …) "
                         "or columns: url, issue, severity.")
    made = db.scalar(select(func.count()).select_from(sm.SkillFinding).where(sm.SkillFinding.run_id == run_id)) or 0
    return {"rows_read": len(rows), "findings": made}


# ------------------------------------------------------------------ ahrefs / semrush (CSV)
def run_provider_csv(db, org_id, project_id, run_id, params: dict, provider: str) -> dict:
    text_in = params.get("csv_text") or ""
    if not text_in.strip():
        raise ValueError(f"Upload an {provider.title()} site-audit export (CSV).")
    rdr = csv.DictReader(io.StringIO(text_in))
    cols = {(c or "").lower().strip(): c for c in (rdr.fieldnames or [])}
    if "issue" not in cols:
        raise ValueError("CSV needs at least an 'issue' column (accepted columns: url, issue, severity).")
    n = 0
    for i, row in enumerate(list(rdr), 2):
        issue = (row.get(cols["issue"]) or "").strip()
        if not issue:
            continue
        sev = (row.get(cols["severity"]) or "medium").strip().lower() if "severity" in cols else "medium"
        sev = {"error": "high", "warning": "medium", "notice": "low", "critical": "critical"}.get(sev, sev)
        if sev not in ("critical", "high", "medium", "low", "info"):
            sev = "medium"
        cat = "developer" if re.search(r"broken|4\d\d|5\d\d|redirect|https|server|robots|sitemap|speed", issue, re.I) else "seo"
        _add(db, org_id, project_id, run_id, cat, sev,
             (row.get(cols["url"]) or "").strip() or None if "url" in cols else None,
             issue, f"Row {i} from the {provider.title()} export.",
             "Fix following the tool's recommendation, then re-run the audit to verify.")
        n += 1
    if not n:
        raise ValueError("No usable issue rows found in the CSV.")
    return {"provider": provider, "findings": n}


# ------------------------------------------------------------------ schema
SCHEMA_WANTED = {
    "product": ("Product", ["price", "add to cart", "buy now", "sku"]),
    "blog": ("Article", ["blog", "article", "news"]),
    "faq": ("FAQPage", ["faq", "frequently asked"]),
    "service": ("Service", ["service", "services"]),
    "contact": ("ContactPage", ["contact"]),
}


def _detect_page_type(url: str, html: str) -> str:
    u = url.lower()
    text_low = re.sub(r"<[^>]+>", " ", html[:20000]).lower()
    if re.search(r"/product|/shop|/p/|/buy", u) or any(k in text_low[:4000] for k in ("add to cart", "add to basket")):
        return "product"
    if re.search(r"/blog|/news|/article|/post", u):
        return "blog"
    if "faq" in u:
        return "faq"
    if re.search(r"/service", u):
        return "service"
    if "contact" in u:
        return "contact"
    for ptype, (_, keys) in SCHEMA_WANTED.items():
        if any(k in text_low[:4000] for k in keys):
            return ptype
    return "page"


def _jsonld_types(html: str) -> tuple[set, bool]:
    found, broken = set(), False
    for m_ in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            data = json.loads(m_.group(1).strip())
        except Exception:
            broken = True
            continue
        blocks = data if isinstance(data, list) else [data]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            for g in (b.get("@graph") if isinstance(b.get("@graph"), list) else [b]):
                t = (g or {}).get("@type")
                if isinstance(t, list):
                    found.update(t)
                elif t:
                    found.add(t)
    return found, broken


def run_schema(db, org_id, project_id, run_id, params: dict) -> dict:
    urls = [_clean_url(u) for u in (params.get("urls") or []) if (u or "").strip()][:MAX_URLS]
    if not urls:
        raise ValueError("Add at least one page URL to check.")
    checked = missing = invalid = 0
    for url in urls:
        status, final, html = _fetch(url)
        if status is None:
            _add(db, org_id, project_id, run_id, "schema", "high", url, "Page could not be opened",
                 "The page did not respond, so schema could not be checked.",
                 "Make sure the URL is public and reachable, then run again.")
            continue
        if status >= 400 or not html:
            _add(db, org_id, project_id, run_id, "schema", "high", url, f"Page returned HTTP {status}",
                 "Schema cannot be checked on an error page.", "Fix the page status first.")
            continue
        checked += 1
        ptype = _detect_page_type(url, html)
        wanted = SCHEMA_WANTED.get(ptype, (None,))[0]
        types, broken = _jsonld_types(html)
        if broken:
            invalid += 1
            _add(db, org_id, project_id, run_id, "schema", "high", url, "Broken JSON-LD schema",
                 "A schema block on the page is not valid JSON, so Google ignores it.",
                 "Fix the JSON syntax (a developer can validate it at schema.org's validator).")
        if wanted and wanted not in types and not any(wanted in t for t in types):
            missing += 1
            others = ", ".join(sorted(types)) or "none"
            _add(db, org_id, project_id, run_id, "schema", "medium", url,
                 f"Missing {wanted} schema (page looks like: {ptype})",
                 f"Found schema types: {others}. This page type should carry {wanted} markup.",
                 f"Ask the developer to add {wanted} JSON-LD matching the page content.")
    return {"pages_checked": checked, "missing_schema": missing, "invalid_schema": invalid}


# ------------------------------------------------------------------ crawler files
def run_crawler_files(db, org_id, project_id, run_id, params: dict) -> dict:
    base = _norm_domain(params.get("domain") or "")
    checks = []

    def check(name, ok, sev, detail, rec):
        checks.append(name)
        _add(db, org_id, project_id, run_id, "crawler", sev if not ok else "info",
             base + {"robots.txt": "/robots.txt", "sitemap.xml": "/sitemap.xml", "llms.txt": "/llms.txt"}[name],
             ("PASS: " if ok else "FAIL: ") + name, detail, rec if not ok else None)

    status, _, robots = _fetch(base + "/robots.txt")
    if status != 200 or not robots.strip():
        check("robots.txt", False, "critical",
              "robots.txt is missing or empty — search engines get no crawl rules.",
              "Publish a robots.txt at the site root (a developer can add it in minutes).")
        robots = ""
    else:
        check("robots.txt", True, "info", "robots.txt exists and is readable.", None)
        if not re.search(r"(?im)^user-agent\s*:", robots):
            check("robots.txt", False, "high", "No User-agent line found — the file may be malformed.",
                  "Add at least:\nUser-agent: *\nAllow: /")
        if not re.search(r"(?im)^sitemap\s*:", robots):
            check("robots.txt", False, "low", "No Sitemap: line — crawlers must discover the sitemap themselves.",
                  "Add a line like: Sitemap: https://yourdomain.com/sitemap.xml")

    status, _, smap = _fetch(base + "/sitemap.xml")
    if status != 200 or not smap.strip():
        check("sitemap.xml", False, "high", "sitemap.xml is missing or empty.",
              "Generate an XML sitemap (most CMS platforms do this automatically) and submit it in Search Console.")
    else:
        try:
            root = ET.fromstring(smap.encode("utf-8", errors="replace")[:2_000_000])
            tag = root.tag.lower()
            if "sitemapindex" in tag:
                n = len(root)
                check("sitemap.xml", True, "info", f"Sitemap index found with {n} sitemaps.", None)
            elif "urlset" in tag:
                n = len(root)
                check("sitemap.xml", True, "info", f"Sitemap lists {n} URLs.", None)
                if n == 0:
                    check("sitemap.xml", False, "medium", "Sitemap is valid XML but contains no URLs.",
                          "Regenerate the sitemap so it lists the site's pages.")
            else:
                check("sitemap.xml", False, "high", "sitemap.xml is not a recognised sitemap format.",
                      "Regenerate it in the standard XML sitemap format.")
        except ET.ParseError:
            check("sitemap.xml", False, "high", "sitemap.xml is not valid XML.",
                  "Fix the XML errors (often a stray & or <) and re-validate.")

    status, _, llms = _fetch(base + "/llms.txt")
    if status == 200 and llms.strip():
        check("llms.txt", True, "info", "llms.txt exists — helps AI search tools understand the site.", None)
    else:
        check("llms.txt", False, "low",
              "llms.txt not found. It is a new standard file that tells AI assistants (ChatGPT, Perplexity…) what the site offers.",
              "Optional but recommended: publish a short llms.txt describing key pages and services.")
    return {"domain": base, "checks": len(checks)}


# ------------------------------------------------------------------ responsive
def run_responsive(db, org_id, project_id, run_id, params: dict) -> dict:
    results = params.get("results") or []
    if not results:
        raise ValueError("Add at least one page × device check result.")
    tested = failed = 0
    for r in results[:200]:
        url = _clean_url(r.get("url") or "")
        device = (r.get("device") or "").strip()
        if not url or device not in DEVICE_PROFILES:
            continue
        passed = bool(r.get("passed"))
        notes = (r.get("notes") or "").strip()
        tested += 1
        if passed:
            _add(db, org_id, project_id, run_id, "responsive", "info", url,
                 f"OK on {device}", notes or "Layout, tap targets and text size look fine.",
                 None, {"device": device, "passed": True})
        else:
            failed += 1
            _add(db, org_id, project_id, run_id, "responsive", "high", url,
                 f"Layout problem on {device}", notes or "The page does not display correctly on this device.",
                 "Ask the developer to fix the layout at this screen size (check for horizontal scroll, overlapping text and tiny tap targets).",
                 {"device": device, "passed": False})
    if not tested:
        raise ValueError("No valid rows. Each row needs a URL and a device from the standard list.")
    return {"tested": tested, "failed": failed,
            "devices": DEVICE_PROFILES,
            "note": "Enter results from real device or browser-devtools testing — nothing is guessed."}


RUNNERS = {
    "pagespeed": run_pagespeed,
    "screaming_frog": run_screaming_frog,
    "ahrefs": lambda db, o, p, r, params: run_provider_csv(db, o, p, r, params, "ahrefs"),
    "semrush": lambda db, o, p, r, params: run_provider_csv(db, o, p, r, params, "semrush"),
    "schema": run_schema,
    "crawler_files": run_crawler_files,
    "responsive": run_responsive,
}
