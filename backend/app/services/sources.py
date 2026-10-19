"""Data-source clients used by the audit engine and rank tracker.

All calls go through the Integrations Hub: credentials come from the vault, a
rate-limit token is taken per call, and failures are recorded on the connection.
"""
from __future__ import annotations

import os
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import integrations as ih
from .integrations import IntegrationError, NotConnected, _ok, http_call


# ------------------------------------------------------------------ PageSpeed Insights (F-046)
def pagespeed(db: Session, org_id, url: str, device: str) -> dict:
    cr = ih.credentials(db, org_id, "pagespeed")
    ih.take_token(db, org_id, "pagespeed")
    params = [("url", url), ("key", cr["secret"]), ("strategy", device)]
    params += [("category", c) for c in ("performance", "accessibility", "best-practices", "seo")]
    r = _ok(http_call("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params, timeout=120, retries=2), "PSI")
    j = r.json()
    lh = j.get("lighthouseResult", {})
    au = lh.get("audits", {})
    field = j.get("loadingExperience", {}).get("metrics", {})
    lcp_ms = (field.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("percentile") or (au.get("largest-contentful-paint") or {}).get("numericValue")
    cls = (field.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("percentile")
    cls = cls / 100 if cls is not None else (au.get("cumulative-layout-shift") or {}).get("numericValue")
    inp = (field.get("INTERACTION_TO_NEXT_PAINT") or {}).get("percentile")
    keep = ("uses-long-cache-ttl", "unminified-css", "unminified-javascript", "modern-image-formats", "uses-optimized-images",
            "font-size", "tap-targets", "target-size", "render-blocking-resources", "server-response-time", "uses-text-compression",
            "speed-index", "total-blocking-time")
    return {
        "url": url, "device": device,
        "lcp": round(lcp_ms / 1000, 3) if lcp_ms else None, "cls": round(cls, 4) if cls is not None else None,
        "inp": inp, "score": round((lh.get("categories", {}).get("performance", {}).get("score") or 0) * 100, 1),
        "audits": {k: {"score": au[k].get("score"), "displayValue": au[k].get("displayValue")} for k in keep if k in au},
        "field_data": bool(field),
    }


# ------------------------------------------------------------------ Google Search Console (F-047)
def _gsc_headers(db, org_id):
    return {"Authorization": f"Bearer {ih.oauth_access_token(db, org_id, 'gsc')}"}


def gsc_search_analytics(db: Session, org_id, prop: str, days: int = 7, row_limit: int = 5000) -> list[dict]:
    ih.take_token(db, org_id, "gsc")
    end = date.today() - timedelta(days=2)
    body = {"startDate": str(end - timedelta(days=days)), "endDate": str(end), "dimensions": ["query", "page"], "rowLimit": row_limit}
    r = _ok(http_call("POST", f"https://www.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(prop, safe='')}/searchAnalytics/query",
                      headers=_gsc_headers(db, org_id), json=body), "GSC searchAnalytics")
    return [{"query": x["keys"][0], "page": x["keys"][1], "clicks": x["clicks"], "impressions": x["impressions"],
             "position": x["position"]} for x in r.json().get("rows", [])]


def gsc_sitemaps(db: Session, org_id, prop: str) -> list[dict]:
    ih.take_token(db, org_id, "gsc")
    r = _ok(http_call("GET", f"https://www.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(prop, safe='')}/sitemaps",
                      headers=_gsc_headers(db, org_id)), "GSC sitemaps")
    return r.json().get("sitemap", [])


COVERAGE_MAP = {
    "Submitted and indexed": "indexed", "Indexed, not submitted in sitemap": "indexed",
    "Crawled - currently not indexed": "crawled-not-indexed", "Discovered - currently not indexed": "discovered-not-indexed",
    "Excluded by ‘noindex’ tag": "noindex", "Not found (404)": "404", "Soft 404": "soft404",
    "Page with redirect": "redirect", "Blocked by robots.txt": "robots-blocked",
}


def gsc_inspect(db: Session, org_id, prop: str, urls: list[str]) -> dict[str, list[str]]:
    """URL Inspection API (2,000/day/property) on a sample of crawled URLs → coverage buckets."""
    buckets: dict[str, list[str]] = {}
    for u in urls:
        ih.take_token(db, org_id, "gsc")
        r = http_call("POST", "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                      headers=_gsc_headers(db, org_id), json={"inspectionUrl": u, "siteUrl": prop})
        if r.status_code != 200:
            continue
        st = r.json().get("inspectionResult", {}).get("indexStatusResult", {}).get("coverageState", "unknown")
        buckets.setdefault(COVERAGE_MAP.get(st, st), []).append(u)
    return buckets


# ------------------------------------------------------------------ Semrush (positions)
def semrush_positions(db: Session, org_id, domain: str, phrases: list[str], database: str = "us") -> dict[str, dict]:
    cr = ih.credentials(db, org_id, "semrush")
    out = {}
    for ph in phrases:
        ih.take_token(db, org_id, "semrush")
        r = _ok(http_call("GET", "https://api.semrush.com/", params={
            "type": "domain_organic", "key": cr["secret"], "domain": domain, "database": database,
            "export_columns": "Ph,Po,Ur", "display_filter": f"+|Ph|Eq|{ph}", "display_limit": 5}), "Semrush")
        lines = [l for l in r.text.strip().splitlines()[1:] if ";" in l]
        if lines:
            p, pos, url = lines[0].split(";")[:3]
            out[ph] = {"position": int(pos), "url": url}
        else:
            out[ph] = {"position": None, "url": None}
    return out


# ------------------------------------------------------------------ Ahrefs (domain metrics)
def ahrefs_metrics(db: Session, org_id, domain: str) -> dict:
    cr = ih.credentials(db, org_id, "ahrefs")
    ih.take_token(db, org_id, "ahrefs")
    h = {"Authorization": f"Bearer {cr['secret']}"}
    d = str(date.today())
    dr = _ok(http_call("GET", "https://api.ahrefs.com/v3/site-explorer/domain-rating", headers=h,
                       params={"target": domain, "date": d}), "Ahrefs DR").json()
    bl = _ok(http_call("GET", "https://api.ahrefs.com/v3/site-explorer/backlinks-stats", headers=h,
                       params={"target": domain, "date": d, "mode": "subdomains"}), "Ahrefs backlinks").json()
    m = bl.get("metrics", {})
    return {"domain_rating": dr.get("domain_rating", {}).get("domain_rating"),
            "refdomains": m.get("live_refdomains"), "backlinks": m.get("live")}


# ------------------------------------------------------------------ Claude (F-009 AI recommendations)
def claude_complete(db: Session, org_id, prompt: str, max_tokens: int = 1200) -> tuple[str, dict]:
    cr = ih.credentials(db, org_id, "claude")
    ih.take_token(db, org_id, "claude")
    model = (cr.get("config") or {}).get("model") or os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
    r = _ok(http_call("POST", "https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": cr["secret"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}), "Claude")
    j = r.json()
    text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
    return text, {"model": model, **(j.get("usage") or {})}
