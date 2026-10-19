"""Free tools (67–72): domain age (RDAP), broken link checker, link analyzer, dofollow/nofollow checker,
backlink monitor checks and the guest-post website finder. All page fetches go through the SSRF-safe crawler."""
from __future__ import annotations

import re
from collections import Counter, deque
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from .crawler import SKIP_EXT, CrawlBlocked, Crawler, normalise
from .integrations import IntegrationError, http_call

TOOLS = {
    "domain-age": "Domain Age Checker", "broken-links": "Broken Link Checker", "link-analyzer": "Link Analyzer",
    "backlink-monitor": "Backlink Monitoring Tool", "dofollow": "Dofollow/Nofollow Checker", "website-finder": "Website Finder",
}
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class ToolError(Exception):
    pass


def clean_domain(d: str) -> str:
    d = (d or "").strip().lower()
    if "://" in d:
        d = urlparse(d).hostname or ""
    d = d.split("/")[0].split(":")[0]
    if d.startswith("www."):
        d = d[4:]
    if not DOMAIN_RE.match(d):
        raise ToolError("Enter a valid domain name, e.g. example.com")
    return d


def clean_url(u: str) -> str:
    u = (u or "").strip()
    if re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I) and not re.match(r"^https?://", u, re.I):
        raise ToolError("Only http(s) URLs can be checked")
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname or "." not in p.hostname and p.hostname != "localhost" or len(u) > 2000:
        raise ToolError("Enter a valid http(s) URL")
    return normalise(u)


# ------------------------------------------------------------------ 67 domain age (RDAP)
def _vcard_name(ent: dict) -> str | None:
    for item in (ent.get("vcardArray") or [None, []])[1]:
        if item and item[0] == "fn":
            return item[3]
    return None


def domain_age(domain: str) -> dict:
    d = clean_domain(domain)
    try:
        r = http_call("GET", f"https://rdap.org/domain/{d}", retries=1, timeout=20, headers={"Accept": "application/rdap+json"})
    except IntegrationError as e:
        raise ToolError(f"The RDAP registry service could not be reached ({e}). No result is shown rather than a guess.")
    if r.status_code == 404:
        return {"domain": d, "found": False, "message": "No RDAP record — the domain may be unregistered or its TLD has no RDAP service."}
    if r.status_code >= 400:
        raise ToolError(f"RDAP lookup failed: HTTP {r.status_code}")
    try:
        js = r.json()
    except ValueError:
        raise ToolError("RDAP returned an invalid response")
    ev = {e.get("eventAction"): e.get("eventDate") for e in js.get("events") or [] if e.get("eventAction")}
    reg = ev.get("registration")
    registrar = next((_vcard_name(e) for e in js.get("entities") or [] if "registrar" in (e.get("roles") or [])), None)
    out = {"domain": d, "found": True, "registered": reg, "expires": ev.get("expiration"), "updated": ev.get("last changed"),
           "registrar": registrar, "status": js.get("status") or [],
           "nameservers": [n.get("ldhName", "").lower() for n in js.get("nameservers") or [] if n.get("ldhName")]}
    if reg:
        try:
            dt = datetime.fromisoformat(reg.replace("Z", "+00:00"))
            days = (datetime.now(timezone.utc) - dt).days
            out.update(age_days=days, age_years=round(days / 365.25, 1))
        except ValueError:
            pass
    return out


# ------------------------------------------------------------------ link extraction
def _crawler(url: str) -> Crawler:
    return Crawler(url, max_pages=1, render_sample=0, mobile_sample=0, external_check_limit=0, delay=None, timeout=15)


def page_links(cr: Crawler, url: str) -> tuple[dict, list[dict]]:
    try:
        resp = cr.fetch(url)
    except CrawlBlocked as e:
        raise ToolError(str(e))
    if resp.get("status") != 200:
        raise ToolError(f"The page returned {resp.get('status') or resp.get('error')}")
    ct = str((resp.get("headers") or {}).get("content-type", ""))
    if "html" not in ct.lower():
        raise ToolError("The URL is not an HTML page")
    base = resp.get("final_url") or url
    host = urlparse(base).netloc
    soup = BeautifulSoup(resp.get("body") or "", "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            continue
        absu = normalise(urljoin(base, href))
        if urlparse(absu).scheme not in ("http", "https"):
            continue
        rel = a.get("rel") or []
        rel = [x.lower() for x in (rel if isinstance(rel, list) else str(rel).split())]
        img = a.find("img")
        anchor = a.get_text(" ", strip=True)[:200] or (f"[img] {img.get('alt') or ''}".strip() if img else "")
        links.append({"href": absu, "anchor": anchor, "rel": " ".join(rel), "internal": urlparse(absu).netloc == host,
                      "follow": "dofollow" if not set(rel) & {"nofollow", "sponsored", "ugc"} else "nofollow",
                      "sponsored": "sponsored" in rel, "ugc": "ugc" in rel})
    return {"url": url, "final_url": base, "status": resp.get("status")}, links


def link_status(cr: Crawler, u: str) -> int | str:
    try:
        r = cr.fetch(u, method="HEAD", max_hops=5)
        if r.get("status") in (405, 403, 501):
            r = cr.fetch(u, max_hops=5)
    except CrawlBlocked as e:
        return "dns_error" if "DNS" in str(e) else "blocked"
    if r.get("status") is None:
        return r.get("error") or "error"
    return r["status"]


def is_broken(st) -> bool:
    return not isinstance(st, int) or st >= 400


# ------------------------------------------------------------------ 71 dofollow / nofollow
def dofollow(url: str) -> dict:
    u = clean_url(url)
    page, links = page_links(_crawler(u), u)
    c = Counter(l["follow"] for l in links)
    return {**page, "total": len(links), "dofollow": c.get("dofollow", 0), "nofollow": c.get("nofollow", 0),
            "sponsored": sum(l["sponsored"] for l in links), "ugc": sum(l["ugc"] for l in links), "links": links[:500]}


# ------------------------------------------------------------------ 69 link analyzer
def link_analyzer(url: str, check_limit: int = 60) -> dict:
    u = clean_url(url)
    cr = _crawler(u)
    page, links = page_links(cr, u)
    rels = Counter()
    for l in links:
        for r in (l["rel"].split() or ["(none)"]):
            rels[r] += 1
    anchors = Counter(l["anchor"] or "(empty)" for l in links)
    ext_domains = Counter(urlparse(l["href"]).netloc for l in links if not l["internal"])
    statuses, checked = {}, 0
    for h in dict.fromkeys(l["href"] for l in links):
        if checked >= check_limit:
            break
        statuses[h] = link_status(cr, h)
        checked += 1
    broken = [{"href": h, "status": s, "anchor": next((l["anchor"] for l in links if l["href"] == h), "")}
              for h, s in statuses.items() if is_broken(s)]
    for l in links:
        l["status"] = statuses.get(l["href"])
    return {**page, "total": len(links), "internal": sum(l["internal"] for l in links),
            "external": sum(not l["internal"] for l in links),
            "dofollow": sum(l["follow"] == "dofollow" for l in links), "nofollow": sum(l["follow"] == "nofollow" for l in links),
            "rel_distribution": dict(rels), "top_anchors": anchors.most_common(20),
            "external_domains": ext_domains.most_common(30), "checked": checked, "unique_links": len(set(l["href"] for l in links)),
            "broken": broken, "links": links[:500]}


# ------------------------------------------------------------------ 68 broken link checker
def broken_links(url: str, max_pages: int = 10, check_limit: int = 150, progress=None) -> dict:
    u = clean_url(url)
    cr = _crawler(u)
    host = urlparse(u).netloc
    q, seen, pages = deque([u]), {u}, {}
    sources: dict[str, set] = {}
    anchors: dict[str, str] = {}
    page_status: dict[str, int | str] = {}
    while q and len(pages) < max_pages:
        cur = q.popleft()
        try:
            resp = cr.fetch(cur)
        except CrawlBlocked as e:
            if cur == u:
                raise ToolError(str(e))
            page_status[cur] = "blocked"
            continue
        page_status[cur] = resp.get("status") if resp.get("status") is not None else (resp.get("error") or "error")
        if resp.get("status") != 200 or "html" not in str((resp.get("headers") or {}).get("content-type", "")).lower():
            if cur == u:
                raise ToolError(f"The start page returned {page_status[cur]}")
            continue
        try:
            _, links = page_links_from(resp, cur)
        except Exception:  # noqa: BLE001
            links = []
        pages[cur] = len(links)
        for l in links:
            sources.setdefault(l["href"], set()).add(cur)
            anchors.setdefault(l["href"], l["anchor"])
            if urlparse(l["href"]).netloc == host and l["href"] not in seen and not SKIP_EXT.search(urlparse(l["href"]).path):
                seen.add(l["href"])
                q.append(l["href"])
        if progress:
            progress(len(pages), max_pages)
    statuses = dict(page_status)
    n = 0
    for h in sources:
        if h in statuses:
            continue
        if n >= check_limit:
            break
        statuses[h] = link_status(cr, h)
        n += 1
    broken = []
    for h, srcs in sources.items():
        st = statuses.get(h)
        if st is not None and is_broken(st):
            broken.append({"href": h, "status": st, "internal": urlparse(h).netloc == host, "anchor": anchors.get(h, ""),
                           "found_on": sorted(srcs)[:20]})
    broken.sort(key=lambda b: (not b["internal"], b["href"]))
    return {"url": u, "pages_crawled": len(pages), "links_found": len(sources),
            "links_checked": sum(1 for h in sources if h in statuses), "unchecked": sum(1 for h in sources if h not in statuses),
            "broken_internal": sum(b["internal"] for b in broken), "broken_external": sum(not b["internal"] for b in broken),
            "broken": broken, "pages": [{"url": p, "links": c, "status": page_status.get(p)} for p, c in pages.items()]}


def page_links_from(resp: dict, url: str) -> tuple[dict, list[dict]]:
    """Same as page_links but from an already-fetched response."""
    class _One:
        def fetch(self, _u):
            return resp
    return page_links(_One(), url)  # type: ignore[arg-type]


# ------------------------------------------------------------------ 70 backlink monitor check
def monitor_check(source_url: str, target_url: str) -> dict:
    from .growth_market import verify_link
    r = verify_link(source_url, target_url, None, require_dofollow=False)
    if r.get("status") != 200:
        st = "error"
    elif not r.get("found"):
        st = "missing"
    elif not r.get("dofollow"):
        st = "nofollow"
    else:
        st = "live"
    return {"status": st, "http_status": r.get("status"), "rel": r.get("rel"), "anchor": r.get("anchor"), "noindex": r.get("noindex"),
            "issues": r.get("issues"), "checked_at": r.get("checked_at")}


# ------------------------------------------------------------------ 72 website finder
FOOTPRINTS = ['"write for us"', '"guest post"', '"submit a guest post"', '"become a contributor"', '"accepting guest posts"',
              'inurl:write-for-us', 'intitle:"write for us"', '"guest post guidelines"', '"contribute to"', '"sponsored post"']


def footprint_queries(keyword: str, extra: list[str] | None = None) -> list[dict]:
    kw = re.sub(r"\s+", " ", (keyword or "").strip())[:80]
    if not kw:
        raise ToolError("Enter a keyword or niche")
    out = []
    for fp in FOOTPRINTS + [f'"{e.strip()}"' for e in (extra or []) if e.strip()][:10]:
        q = f'"{kw}" {fp}'
        out.append({"query": q, "google": f"https://www.google.com/search?q={quote_plus(q)}",
                    "bing": f"https://www.bing.com/search?q={quote_plus(q)}"})
    return out
