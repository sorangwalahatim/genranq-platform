"""Executable detectors for every row of the *Audit Checklist* sheet (CHK-01 … CHK-62).

Each detector receives an ``AuditInput`` (crawl output + whatever external sources are
connected) and returns a ``Outcome``. Grades:
  pass / fail / partial  – evaluated (counted in the score)
  not_run                – the data source it needs is NOT CONNECTED
  manual                 – cannot be decided automatically; needs a human review
  na                     – not applicable to this site (e.g. no hreflang in use)
A detector is looked up by code; checklist rows added later in the sheet with no detector
yet are reported as ``manual`` so they are never silently dropped.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse


@dataclass
class Outcome:
    grade: str
    count: int = 0
    affected: list = field(default_factory=list)
    message: str = ""
    evidence: dict = field(default_factory=dict)
    total: int | None = None  # population size for prevalence
    site_level: bool = False


@dataclass
class AuditInput:
    pages: dict  # url -> page facts (from crawler)
    site: dict  # site-level probes
    sources: dict  # provider_key -> status
    psi: list = field(default_factory=list)  # pagespeed_result dicts
    gsc: list = field(default_factory=list)  # gsc_issue dicts
    ranks: list = field(default_factory=list)  # {keyword, url, checked_at}
    backlinks: list = field(default_factory=list)  # CRM rows
    ahrefs: dict | None = None
    gbp: list = field(default_factory=list)
    keywords: list = field(default_factory=list)  # {phrase, target_url}
    gsc_sitemaps: list | None = None

    # -------------------------------------------------- helpers
    @property
    def ok(self):
        return {u: p for u, p in self.pages.items() if p.get("status") == 200 and "html" in (p.get("content_type") or "")}

    @property
    def indexable(self):
        return {u: p for u, p in self.ok.items() if not p.get("noindex") and (not p.get("canonical") or p.get("canonical") == u)}

    def connected(self, key):
        return self.sources.get(key) in ("CONNECTED", "RATE_LIMITED")

    @property
    def home(self):
        h = self.site.get("home", {}).get("final_url")
        return self.pages.get(h) or next(iter(self.pages.values()), {})


REG: dict[str, Callable[[AuditInput], Outcome]] = {}
RECS: dict[str, str] = {}


def check(code, rec):
    def deco(f):
        REG[code] = f
        RECS[code] = rec
        return f
    return deco


def _list(urls, n=100):
    return sorted(urls)[:n]


def _page_check(inp: AuditInput, pop: dict, bad: list, msg_fail: str, msg_pass: str, **ev) -> Outcome:
    if not pop:
        return Outcome("na", message="No crawlable pages in scope")
    if bad:
        return Outcome("fail", len(bad), _list(bad), msg_fail.format(n=len(bad), total=len(pop)), ev, total=len(pop))
    return Outcome("pass", 0, [], msg_pass.format(total=len(pop)), ev, total=len(pop))


def _important(inp):
    sm = set(inp.site.get("sitemap", {}).get("urls", []))
    return {u: p for u, p in inp.pages.items() if u in sm or (p.get("depth") is not None and p["depth"] <= 2)}


# ===================================================================== Indexation
@check("CHK-01", "Make sure key pages return 200, are not noindexed or canonicalised elsewhere, and request indexing in Search Console.")
def chk01(inp):
    imp = _important(inp)
    bad = [u for u, p in imp.items() if p.get("status") != 200 or p.get("noindex") or (p.get("canonical") and p["canonical"] != u)]
    gsc_missing = [g for g in inp.gsc if g["type"] in ("crawled-not-indexed", "discovered-not-indexed", "excluded")]
    if gsc_missing:
        bad += [u for g in gsc_missing for u in g.get("sample_urls", [])]
    o = _page_check(inp, imp, bad, "{n} of {total} important pages are not indexable", "All {total} important pages are indexable")
    if not inp.connected("gsc"):
        o.message += " (crawler evidence only — Google Search Console NOT CONNECTED, index status unconfirmed)"
        if o.grade == "pass":
            o.grade = "partial"
    return o


@check("CHK-02", "Noindex or canonicalise parameter, filter and thin archive URLs; remove them from sitemaps.")
def chk02(inp):
    idx = inp.indexable
    param = [u for u in idx if urlparse(u).query]
    ev = {"indexable": len(idx), "parameter_urls": len(param)}
    if inp.connected("gsc") and inp.gsc:
        indexed = sum(g["count"] for g in inp.gsc if g["type"] == "indexed")
        ev["gsc_indexed"] = indexed
        if indexed and indexed > 1.5 * max(1, len(idx)):
            return Outcome("fail", indexed - len(idx), [], f"Google indexes {indexed} URLs but only {len(idx)} indexable pages were found", ev)
    if idx and len(param) / len(idx) > 0.1:
        return Outcome("fail", len(param), _list(param), f"{len(param)} indexable URLs carry query parameters (possible index bloat)", ev, total=len(idx))
    return Outcome("pass" if inp.connected("gsc") else "partial", 0, [], "No index bloat signals in crawl" +
                   ("" if inp.connected("gsc") else " (GSC NOT CONNECTED — indexed count unavailable)"), ev, total=len(idx))


@check("CHK-03", "Remove the noindex directive (meta robots or X-Robots-Tag) from pages that should rank.")
def chk03(inp):
    sm = set(inp.site.get("sitemap", {}).get("urls", []))
    bad = [u for u, p in inp.ok.items() if p.get("noindex") and (u in sm or p.get("inlinks", 0) >= 3 or (p["depth"] if p.get("depth") is not None else 9) <= 1)]
    return _page_check(inp, inp.ok, bad, "{n} linked/sitemap pages carry noindex", "No important page is noindexed")


@check("CHK-04", "Update robots.txt so pages you want indexed are not disallowed.")
def chk04(inp):
    blocked = inp.site.get("robots_blocked", [])
    sm = set(inp.site.get("sitemap", {}).get("urls", []))
    bad = [u for u in blocked if u in sm] or blocked
    if bad:
        return Outcome("fail", len(bad), _list(bad), f"{len(bad)} linked or sitemap URLs are blocked by robots.txt", {"blocked": len(blocked)})
    return Outcome("pass", message="No linked or sitemap URL is blocked by robots.txt")


@check("CHK-05", "Publish /robots.txt and add a 'Sitemap:' line pointing to your XML sitemap.")
def chk05(inp):
    r = inp.site.get("robots", {})
    if not r.get("exists"):
        return Outcome("fail", 1, [r.get("url")], "robots.txt is missing", {"status": r.get("status")}, site_level=True)
    if not r.get("sitemaps"):
        return Outcome("fail", 1, [r.get("url")], "robots.txt does not reference a sitemap", site_level=True)
    return Outcome("pass", message=f"robots.txt found, references {len(r['sitemaps'])} sitemap(s)", evidence={"sitemaps": r["sitemaps"]})


@check("CHK-06", "Generate an XML sitemap and an HTML sitemap page, and submit the XML sitemap in Search Console.")
def chk06(inp):
    xml = inp.site.get("sitemap", {}).get("exists", False)
    html = any("sitemap" in l["href"].lower() and not l["href"].lower().endswith(".xml")
               for p in inp.pages.values() for l in p.get("internal_links", []))
    ev = {"xml_sitemap": xml, "html_sitemap": html}
    if inp.gsc_sitemaps is not None:
        ev["submitted_to_gsc"] = len(inp.gsc_sitemaps)
    problems = [k for k, v in ev.items() if not v]
    if problems:
        return Outcome("fail", len(problems), [], "Missing: " + ", ".join(problems), ev, site_level=True)
    if inp.gsc_sitemaps is None:
        return Outcome("partial", 0, [], "XML and HTML sitemaps exist (GSC NOT CONNECTED — submission unverified)", ev)
    return Outcome("pass", 0, [], "Sitemaps exist and are submitted to Search Console", ev)


@check("CHK-07", "Keep only canonical, indexable, 200-status URLs in the XML sitemap.")
def chk07(inp):
    sm = inp.site.get("sitemap", {}).get("urls", [])
    if not sm:
        return Outcome("na", message="No XML sitemap URLs to validate")
    crawled = {u: inp.pages[u] for u in sm if u in inp.pages}
    bad = [u for u, p in crawled.items() if p.get("status") != 200 or p.get("noindex") or (p.get("canonical") and p["canonical"] != u) or p.get("chain")]
    o = _page_check(inp, crawled, bad, "{n} of {total} checked sitemap URLs are not valid indexable URLs", "All {total} checked sitemap URLs are valid")
    o.evidence = {"sitemap_urls": len(sm), "checked": len(crawled)}
    return o


@check("CHK-08", "Return a real 404/410 status for missing pages instead of a 200 error page.")
def chk08(inp):
    probe = inp.site.get("soft404_probe", {})
    soft = [u for u, p in inp.ok.items() if re.search(r"\b(404|not found|page cannot be found)\b", (p.get("title") or ""), re.I)]
    soft += [u for g in inp.gsc if g["type"] == "soft404" for u in g.get("sample_urls", [])]
    if probe.get("status") == 200:
        return Outcome("fail", 1 + len(soft), [probe.get("url")] + _list(soft), "Non-existent URLs return HTTP 200 (soft 404)", {"probe": probe}, site_level=True)
    if soft:
        return Outcome("fail", len(soft), _list(soft), f"{len(soft)} pages look like error pages but return 200")
    return Outcome("pass", message=f"Missing URLs return {probe.get('status')}", evidence={"probe": probe})


@check("CHK-09", "Improve content quality and internal links for URLs Google crawled or discovered but did not index.")
def chk09(inp):
    if not inp.connected("gsc"):
        return Outcome("not_run", message="Requires Google Search Console (NOT CONNECTED)")
    rows = [g for g in inp.gsc if g["type"] in ("crawled-not-indexed", "discovered-not-indexed")]
    n = sum(g["count"] for g in rows)
    if n:
        return Outcome("fail", n, [u for g in rows for u in g.get("sample_urls", [])][:100], f"{n} URLs crawled/discovered but not indexed")
    return Outcome("partial" if not inp.gsc else "pass", message="No crawled/discovered-not-indexed URLs reported by GSC")


# ===================================================================== Crawlability
@check("CHK-10", "Flatten navigation and add internal links so important pages are within 4 clicks of the homepage.")
def chk10(inp):
    deep = [u for u, p in inp.ok.items() if p.get("depth") is not None and p["depth"] > 4]
    return _page_check(inp, inp.ok, deep, "{n} pages are more than 4 clicks from the homepage", "All linked pages are within 4 clicks")


@check("CHK-11", "Reduce links on pages with more than 300 outlinks (mega-menus, tag clouds, footers).")
def chk11(inp):
    bad = [u for u, p in inp.ok.items() if p.get("outlinks", 0) > 300]
    return _page_check(inp, inp.ok, bad, "{n} pages have more than 300 outlinks", "No page exceeds 300 outlinks")


@check("CHK-12", "Update or remove internal links that point to 4XX/5XX URLs.")
def chk12(inp):
    bad = [u for u, p in inp.pages.items() if p.get("broken_internal")]
    targets = sorted({t for p in inp.pages.values() for t in p.get("broken_internal", [])})
    o = _page_check(inp, inp.ok, bad, "{n} pages link to broken internal URLs", "No internal links to error pages")
    o.evidence = {"broken_targets": targets[:50]}
    return o


@check("CHK-13", "Link to orphan pages from relevant hub/category pages, or remove them if obsolete.")
def chk13(inp):
    home = inp.site.get("home", {}).get("final_url")
    orphans = [u for u, p in inp.ok.items() if p.get("source") == "sitemap" and p.get("inlinks", 0) == 0 and u != home]
    if not inp.site.get("sitemap", {}).get("urls"):
        return Outcome("partial", message="No XML sitemap — orphan detection limited to link graph")
    return _page_check(inp, inp.ok, orphans, "{n} sitemap pages have no internal links pointing to them", "No orphan pages found")


@check("CHK-14", "Fix hosting/uptime: the homepage must respond with 200 quickly and reliably.")
def chk14(inp):
    h = inp.site.get("home", {})
    if h.get("status") != 200:
        return Outcome("fail", 1, [inp.site.get("start_url")], f"Site not reachable: status={h.get('status')} error={h.get('error')}", {"home": h}, site_level=True)
    if (h.get("elapsed") or 0) > 10:
        return Outcome("fail", 1, [inp.site.get("start_url")], f"Homepage took {h['elapsed']:.1f}s to respond", site_level=True)
    return Outcome("pass", message=f"Homepage reachable ({h.get('elapsed', 0):.2f}s)")


# ===================================================================== On-page
def _dupes(pages, key):
    groups = defaultdict(list)
    for u, p in pages.items():
        v = (p.get(key) or "").strip().lower()
        if v:
            groups[v].append(u)
    return {k: v for k, v in groups.items() if len(v) > 1}


@check("CHK-15", "Add a unique, descriptive <title> to every page.")
def chk15(inp):
    bad = [u for u, p in inp.indexable.items() if not (p.get("title") or "").strip()]
    return _page_check(inp, inp.indexable, bad, "{n} pages have no title", "All {total} pages have a title")


@check("CHK-16", "Rewrite duplicate titles so each page targets its own query.")
def chk16(inp):
    d = _dupes(inp.indexable, "title")
    bad = [u for us in d.values() for u in us]
    o = _page_check(inp, inp.indexable, bad, "{n} pages share a title with another page", "No duplicate titles")
    o.evidence = {"groups": [{"title": k, "urls": v[:10]} for k, v in list(d.items())[:20]]}
    return o


@check("CHK-17", "Shorten titles to ~60 characters so they are not truncated in results.")
def chk17(inp):
    bad = [u for u, p in inp.indexable.items() if len(p.get("title") or "") > 65]
    return _page_check(inp, inp.indexable, bad, "{n} titles exceed 65 characters", "All titles within length")


@check("CHK-18", "Write a compelling meta description (~150 chars) for every indexable page.")
def chk18(inp):
    bad = [u for u, p in inp.indexable.items() if not (p.get("meta_description") or "").strip()]
    return _page_check(inp, inp.indexable, bad, "{n} pages lack a meta description", "All pages have a meta description")


@check("CHK-19", "Make meta descriptions unique per page.")
def chk19(inp):
    d = _dupes(inp.indexable, "meta_description")
    bad = [u for us in d.values() for u in us]
    o = _page_check(inp, inp.indexable, bad, "{n} pages share a meta description", "No duplicate meta descriptions")
    o.evidence = {"groups": [{"meta": k[:80], "urls": v[:10]} for k, v in list(d.items())[:20]]}
    return o


@check("CHK-20", "Add one H1 that states the page topic.")
def chk20(inp):
    bad = [u for u, p in inp.indexable.items() if not p.get("h1")]
    return _page_check(inp, inp.indexable, bad, "{n} pages have no H1", "Every page has an H1")


@check("CHK-21", "Use exactly one H1 per page and keep H1s unique across pages.")
def chk21(inp):
    multi = [u for u, p in inp.indexable.items() if len(p.get("h1") or []) > 1]
    groups = defaultdict(list)
    for u, p in inp.indexable.items():
        if p.get("h1"):
            groups[p["h1"][0].strip().lower()].append(u)
    dup = [u for us in groups.values() if len(us) > 1 for u in us]
    bad = sorted(set(multi + dup))
    o = _page_check(inp, inp.indexable, bad, "{n} pages have multiple or duplicate H1s", "H1s are single and unique")
    o.evidence = {"multiple": len(multi), "duplicate": len(dup)}
    return o


@check("CHK-22", "Structure headings in order (H1 → H2 → H3) without skipping levels.")
def chk22(inp):
    bad = []
    for u, p in inp.indexable.items():
        hs = [int(h[1]) for h in p.get("headings", [])]
        if not hs:
            continue
        if hs[0] != 1 or any(b - a > 1 for a, b in zip(hs, hs[1:])):
            bad.append(u)
    o = _page_check(inp, inp.indexable, bad, "{n} pages skip heading levels or don't start with H1", "Heading hierarchy is correct")
    if o.grade == "fail" and len(bad) / max(1, len(inp.indexable)) < 0.3:
        o.grade = "partial"
    return o


@check("CHK-23", "Add descriptive ALT text to meaningful images (empty alt only for decorative ones).")
def chk23(inp):
    bad = [u for u, p in inp.indexable.items() if p.get("images_missing_alt")]
    n_imgs = sum(len(p.get("images_missing_alt", [])) for p in inp.indexable.values())
    o = _page_check(inp, inp.indexable, bad, "{n} pages have images without ALT (" + str(n_imgs) + " images)", "All images have ALT text")
    return o


@check("CHK-24", "Use short, lowercase, hyphenated, readable URLs without session IDs or excessive parameters.")
def chk24(inp):
    bad = []
    for u in inp.indexable:
        p = urlparse(u)
        if (re.search(r"[A-Z]", p.path) or "_" in p.path or len(u) > 115 or p.query.count("&") >= 3
                or re.search(r"(sessionid|sid|phpsessid|jsessionid)=", p.query, re.I) or re.search(r"[^\x00-\x7f]", p.path)):
            bad.append(u)
    o = _page_check(inp, inp.indexable, bad, "{n} URLs are not SEO-friendly", "URL structure is clean")
    if o.grade == "fail":
        o.grade = "partial"  # sheet: partly automated; human judgement on naming
    return o


@check("CHK-25", "Link phone numbers with tel: and email addresses with mailto: (contact page, header/footer).")
def chk25(inp):
    tel = any(p.get("tel") for p in inp.pages.values())
    mail = any(p.get("mailto") for p in inp.pages.values())
    if tel or mail:
        return Outcome("pass", message=f"tel: {'yes' if tel else 'no'}, mailto: {'yes' if mail else 'no'}", evidence={"tel": tel, "mailto": mail})
    return Outcome("fail", 1, [], "No tel: or mailto: links found anywhere on the site", site_level=True)


# ===================================================================== Content
@check("CHK-26", "Consolidate internally duplicated pages (canonical, 301 or rewrite).")
def chk26(inp):
    groups = defaultdict(list)
    for u, p in inp.indexable.items():
        if p.get("body_hash") and p.get("word_count", 0) >= 20:
            groups[p["body_hash"]].append(u)
    d = [v for v in groups.values() if len(v) > 1]
    bad = [u for v in d for u in v]
    o = _page_check(inp, inp.indexable, bad, "{n} pages have identical body content", "No internal duplicate content")
    o.evidence = {"groups": d[:20]}
    return o


@check("CHK-27", "Rewrite or claim content that is duplicated on other domains; file DMCA for scrapers if needed.")
def chk27(inp):
    return Outcome("manual", message="External duplicate detection needs a plagiarism source; Semrush/Ahrefs APIs expose no such report. "
                                     "Review manually or with an AI provider once connected.")


@check("CHK-28", "Expand thin pages with useful content, merge them, or noindex them.")
def chk28(inp):
    bad = [u for u, p in inp.indexable.items() if p.get("word_count", 0) < 300]
    o = _page_check(inp, inp.indexable, bad, "{n} indexable pages have fewer than 300 words", "No thin pages")
    o.message += " (word-count heuristic; AI quality review not run)"
    return o


@check("CHK-29", "Assign one target page per keyword; merge or differentiate pages competing for the same query.")
def chk29(inp):
    if not inp.ranks:
        return Outcome("not_run", message="Requires ranking data (rank tracker or GSC) — none available yet")
    since = datetime.now(timezone.utc) - timedelta(days=30)
    urls = defaultdict(set)
    for r in inp.ranks:
        if r["url"] and r["checked_at"] >= since:
            urls[r["keyword"]].add(r["url"])
    bad = {k: sorted(v) for k, v in urls.items() if len(v) > 1}
    if bad:
        return Outcome("fail", len(bad), [u for v in bad.values() for u in v][:100],
                       f"{len(bad)} keywords rank with more than one URL in the last 30 days", {"keywords": bad})
    return Outcome("pass", message=f"No cannibalisation across {len(urls)} ranking keywords")


@check("CHK-30", "Use the target keyword naturally; avoid stuffing a single term above ~5% of words.")
def chk30(inp):
    stuffed = []
    for u, p in inp.indexable.items():
        wc = p.get("word_count", 0)
        if wc >= 100 and p.get("top_terms"):
            term, n = p["top_terms"][0]
            if n / wc > 0.05:
                stuffed.append(u)
    missing = []
    for k in inp.keywords:
        t = k.get("target_url")
        if t and t in inp.pages:
            pg = inp.pages[t]
            hay = " ".join([pg.get("title") or "", " ".join(pg.get("h1") or [])]).lower()
            if k["phrase"].lower() not in hay:
                missing.append(t)
    bad = sorted(set(stuffed + missing))
    o = _page_check(inp, inp.indexable, bad, "{n} pages over-use a term or miss their target keyword in title/H1", "Keyword usage looks natural")
    o.evidence = {"stuffed": stuffed[:20], "target_missing": missing[:20]}
    if o.grade == "fail":
        o.grade = "partial"
    return o


# ===================================================================== Technical
@check("CHK-31", "Serve every page over HTTPS with a valid certificate; remove mixed (http://) resources.")
def chk31(inp):
    pr = inp.site.get("protocol", {})
    http_pages = [u for u in inp.ok if u.startswith("http://")]
    mixed = [u for u, p in inp.ok.items() if p.get("mixed_content")]
    if not pr.get("https_ok"):
        return Outcome("fail", 1, [], "HTTPS is not available on this host", {"protocol": pr}, site_level=True)
    bad = http_pages + mixed
    if bad:
        return Outcome("fail", len(bad), _list(bad), f"{len(http_pages)} pages on http://, {len(mixed)} with mixed content", {"protocol": pr}, total=len(inp.ok))
    return Outcome("pass", message="HTTPS everywhere, no mixed content")


@check("CHK-32", "301-redirect all http:// URLs to their https:// equivalent.")
def chk32(inp):
    pr = inp.site.get("protocol", {})
    if pr.get("http_status") == 200:
        return Outcome("fail", 1, ["http://" + inp.site.get("host", "") + "/"], "Both HTTP and HTTPS serve content (duplicate protocol)", {"protocol": pr}, site_level=True)
    if pr.get("http_redirects_to_https"):
        return Outcome("pass", message="HTTP redirects to HTTPS", evidence={"protocol": pr})
    return Outcome("partial", message="HTTP does not serve content but does not redirect to HTTPS either", evidence={"protocol": pr})


@check("CHK-33", "Add a self-referencing canonical to indexable pages; point canonicals only at 200, indexable URLs.")
def chk33(inp):
    missing = [u for u, p in inp.ok.items() if not p.get("noindex") and not p.get("canonical")]
    wrong = [u for u, p in inp.ok.items() if p.get("canonical") and p["canonical"] != u and
             (inp.pages.get(p["canonical"], {}).get("status") not in (200, None) or inp.pages.get(p["canonical"], {}).get("noindex"))]
    bad = sorted(set(missing + wrong))
    o = _page_check(inp, inp.ok, bad, "{n} pages have missing or invalid canonicals", "Canonical tags present and valid")
    o.evidence = {"missing": len(missing), "invalid_target": len(wrong)}
    return o


@check("CHK-34", "Collapse redirect chains to a single hop and break redirect loops; update links to the final URL.")
def chk34(inp):
    bad = [u for u, p in inp.pages.items() if len(p.get("chain") or []) > 1 or p.get("error") in ("redirect_loop", "too_many_redirects")]
    return _page_check(inp, inp.pages, bad, "{n} URLs go through redirect chains or loops", "No redirect chains or loops")


@check("CHK-35", "Use permanent 301 redirects instead of 302/307, JavaScript or meta-refresh redirects.")
def chk35(inp):
    bad = [u for u, p in inp.pages.items() if any(h["status"] not in (301, 308) for h in (p.get("chain") or []))
           or p.get("meta_refresh") or p.get("js_redirect")]
    return _page_check(inp, inp.pages, bad, "{n} URLs use temporary, JS or meta-refresh redirects", "All redirects are permanent")


@check("CHK-36", "Fix or remove outbound links that return errors.")
def chk36(inp):
    st = inp.site.get("external_status", {}).get("status", {})
    broken = [u for u, s in st.items() if s == "dns_error" or (isinstance(s, int) and s >= 400 and s not in (401, 403, 429, 999))]
    unverified = [u for u, s in st.items() if s in (None, "error", "blocked")]
    ev = {"checked": len(st), "total_external": inp.site.get("external_status", {}).get("total"), "unverified": unverified[:50]}
    if broken:
        src = [u for u, p in inp.pages.items() if any(l["href"] in broken for l in p.get("external_links", []))]
        return Outcome("fail", len(broken), broken[:100], f"{len(broken)} outbound links are broken (on {len(src)} pages)", {**ev, "pages": src[:50]}, total=len(st))
    return Outcome("pass", message=f"{len(st)} outbound links checked, none broken", evidence=ev)


@check("CHK-37", "Restore or 301-redirect URLs returning 404, and fix links pointing to them.")
def chk37(inp):
    bad = [u for u, p in inp.pages.items() if p.get("status") == 404]
    gsc404 = sum(g["count"] for g in inp.gsc if g["type"] == "404")
    o = _page_check(inp, inp.pages, bad, "{n} crawled URLs return 404", "No 404s in crawl")
    if gsc404:
        o.grade, o.count = "fail", o.count + gsc404
        o.message += f"; GSC reports {gsc404} 404s"
    return o


@check("CHK-38", "Remove or delay intrusive pop-ups/interstitials, especially on mobile landing pages.")
def chk38(inp):
    bad = [u for u, p in inp.ok.items() if p.get("interstitial_markup")]
    if bad:
        return Outcome("partial", len(bad), _list(bad), f"Modal/pop-up markup detected on {len(bad)} pages — review manually", total=len(inp.ok))
    return Outcome("pass", message="No pop-up/interstitial markup detected (heuristic)")


# ===================================================================== Rendering
@check("CHK-39", "Server-render or pre-render primary content so it is visible without relying on client-side JS.")
def chk39(inp):
    r = inp.site.get("render", {})
    if r.get("available") and r.get("pages"):
        js_only = [p["url"] for p in r["pages"] if "rendered_words" in p and p["raw_words"] < 50 and p["rendered_words"] > 150]
        empty = [p["url"] for p in r["pages"] if "rendered_words" in p and p["raw_words"] >= 30
                 and p["rendered_words"] < 0.5 * p["raw_words"]]
        bad = js_only + empty
        ev = {"sample": r["pages"]}
        if bad:
            return Outcome("fail", len(bad), bad, f"{len(js_only)} pages only show content after JS; {len(empty)} lose content when rendered", ev, total=len(r["pages"]))
        return Outcome("pass", message=f"Content visible in raw and rendered HTML on {len(r['pages'])} sampled pages", evidence=ev)
    spa = [u for u, p in inp.ok.items() if p.get("spa_root") and p.get("word_count", 0) < 50]
    if spa:
        return Outcome("fail", len(spa), _list(spa), f"{len(spa)} pages look like client-rendered shells", total=len(inp.ok))
    return Outcome("partial", message="Renderer unavailable — raw HTML has content (no JS render comparison)")


@check("CHK-40", "Move crucial content out of iframes and remove Flash.")
def chk40(inp):
    bad = [u for u, p in inp.ok.items() if p.get("flash") or (p.get("content_iframes", p.get("iframes", 0)) > 0 and p.get("word_count", 0) < 100)]
    return _page_check(inp, inp.ok, bad, "{n} pages rely on iframe/Flash for main content", "No crucial content in iframes/Flash")


@check("CHK-41", "Set proper Cache-Control headers and fix per-URL rendering errors.")
def chk41(inp):
    nocache = [u for u, p in inp.ok.items() if not p.get("cache_control")]
    psi_cache = [r for r in inp.psi if (r.get("audits", {}).get("uses-long-cache-ttl", {}) or {}).get("score", 1) < 0.5]
    bad = nocache + [r["url"] for r in psi_cache]
    o = _page_check(inp, inp.ok, bad, "{n} URLs lack Cache-Control or have poor cache TTLs", "Caching headers present")
    o.grade = "partial" if o.grade == "fail" else o.grade
    return o


# ===================================================================== Speed & CWV
def _psi_latest(inp):
    latest = {}
    for r in sorted(inp.psi, key=lambda x: x.get("checked_at") or 0):
        latest[(r["url"], r["device"])] = r
    return list(latest.values())


@check("CHK-42", "Reduce server response time, render-blocking resources and payload so pages load in under 3s.")
def chk42(inp):
    psi = _psi_latest(inp)
    if psi:
        slow = [f"{r['url']} ({r['device']})" for r in psi if (r.get("lcp") or 0) > 3]
        if slow:
            return Outcome("fail", len(slow), slow, f"{len(slow)} URL/device combinations take over 3s (LCP)", {"psi": psi}, total=len(psi))
        return Outcome("pass", message="All measured pages load under 3s (LCP)", evidence={"psi": psi})
    slow = [u for u, p in inp.ok.items() if (p.get("elapsed") or 0) > 3]
    o = _page_check(inp, inp.ok, slow, "{n} pages took over 3s to download (server timing)", "All pages downloaded under 3s")
    o.grade = "partial" if o.grade == "pass" else o.grade
    o.message += " — PageSpeed Insights NOT CONNECTED, browser load time not measured" if not inp.connected("pagespeed") else ""
    return o


@check("CHK-43", "Improve LCP (<2.5s), CLS (<0.1) and INP (<200ms) on mobile and desktop.")
def chk43(inp):
    psi = _psi_latest(inp)
    if not psi:
        return Outcome("not_run", message="Requires Google PageSpeed Insights (NOT CONNECTED or not yet run)")
    bad = [f"{r['url']} ({r['device']})" for r in psi if (r.get("lcp") or 0) > 2.5 or (r.get("cls") or 0) > 0.1 or (r.get("inp") or 0) > 200]
    if bad:
        return Outcome("fail", len(bad), bad, f"{len(bad)} URL/device combinations fail Core Web Vitals", {"psi": psi}, total=len(psi))
    return Outcome("pass", message="Core Web Vitals pass on all measured URLs", evidence={"psi": psi})


@check("CHK-44", "Enable GZIP or Brotli compression for HTML, CSS and JS.")
def chk44(inp):
    bad = [u for u, p in inp.ok.items() if not p.get("content_encoding")]
    assets = [a["url"] for a in inp.site.get("assets", {}).get("checked", []) if not a.get("encoding") and a["bytes"] > 1500]
    o = _page_check(inp, inp.ok, bad + assets, "{n} HTML pages/assets are served uncompressed", "Compression enabled")
    return o


@check("CHK-45", "Minify CSS and JavaScript in your build pipeline.")
def chk45(inp):
    psi = _psi_latest(inp)
    psi_bad = [r["url"] for r in psi if any((r.get("audits", {}).get(a) or {}).get("score", 1) < 0.9 for a in ("unminified-css", "unminified-javascript"))]
    assets = inp.site.get("assets", {}).get("checked", [])
    bad = [a["url"] for a in assets if not a["minified"]] + psi_bad
    if not assets and not psi:
        return Outcome("not_run", message="No CSS/JS assets fetched and PageSpeed Insights not run")
    if bad:
        return Outcome("fail", len(bad), bad, f"{len(bad)} CSS/JS files are not minified", {"assets": assets}, total=len(assets) or None)
    return Outcome("pass", message=f"{len(assets)} sampled assets are minified", evidence={"assets": assets})


@check("CHK-46", "Compress images and serve WebP/AVIF with correct dimensions.")
def chk46(inp):
    psi = _psi_latest(inp)
    if psi:
        bad = [r["url"] for r in psi if any((r.get("audits", {}).get(a) or {}).get("score", 1) < 0.9 for a in ("modern-image-formats", "uses-optimized-images"))]
        if bad:
            return Outcome("fail", len(bad), bad, f"{len(bad)} URLs have uncompressed or legacy-format images", total=len(psi))
        return Outcome("pass", message="Images optimised on measured URLs")
    legacy = [u for u, p in inp.ok.items() if set(p.get("img_formats", [])) & {"jpg", "jpeg", "png", "gif"} and not set(p.get("img_formats", [])) & {"webp", "avif"}]
    o = _page_check(inp, inp.ok, legacy, "{n} pages use only legacy image formats", "Next-gen image formats in use")
    o.grade = "partial" if o.grade in ("fail", "pass") else o.grade
    o.message += " (PSI NOT CONNECTED — compression not measured)"
    return o


# ===================================================================== Mobile
@check("CHK-47", "Add a responsive viewport meta tag and test layouts on mobile breakpoints.")
def chk47(inp):
    bad = [u for u, p in inp.ok.items() if not p.get("viewport")]
    psi_m = [r for r in _psi_latest(inp) if r["device"] == "mobile" and (r.get("score") or 100) < 50]
    o = _page_check(inp, inp.ok, bad + [r["url"] for r in psi_m], "{n} pages lack a viewport tag or score poorly on mobile", "Pages are mobile-ready")
    return o


@check("CHK-48", "Use ≥16px base fonts and ≥48px tap targets with spacing.")
def chk48(inp):
    psi = [r for r in _psi_latest(inp) if r["device"] == "mobile"]
    if not psi:
        return Outcome("not_run", message="Requires PageSpeed Insights mobile audits (NOT CONNECTED or not yet run)")
    bad = [r["url"] for r in psi if any((r.get("audits", {}).get(a) or {}).get("score", 1) < 0.9 for a in ("font-size", "tap-targets", "target-size"))]
    if bad:
        return Outcome("fail", len(bad), bad, f"{len(bad)} URLs have illegible fonts or small tap targets", total=len(psi))
    return Outcome("pass", message="Fonts and tap targets pass on mobile")


@check("CHK-49", "Serve the same content and links to mobile and desktop users.")
def chk49(inp):
    mp = inp.site.get("mobile_parity", [])
    if not mp:
        return Outcome("not_run", message="No mobile sample collected")
    bad = [x["url"] for x in mp if x["desktop_words"] and abs(x["mobile_words"] - x["desktop_words"]) / x["desktop_words"] > 0.2
           or x["desktop_links"] and abs(x["mobile_links"] - x["desktop_links"]) / x["desktop_links"] > 0.2]
    if bad:
        return Outcome("fail", len(bad), bad, f"{len(bad)} of {len(mp)} sampled pages differ >20% between mobile and desktop", {"sample": mp}, total=len(mp))
    return Outcome("pass", message=f"Mobile parity OK on {len(mp)} sampled pages", evidence={"sample": mp})


# ===================================================================== Structured data
@check("CHK-50", "Add schema.org markup (Organization/LocalBusiness, Article, Product, BreadcrumbList…) to key templates.")
def chk50(inp):
    key = {u: p for u, p in inp.indexable.items() if (p.get("depth") if p.get("depth") is not None else 9) <= 1}
    bad = [u for u, p in key.items() if not p.get("schema_types")]
    o = _page_check(inp, key, bad, "{n} of {total} key template pages have no structured data", "Structured data present on key pages")
    o.evidence = {"types": sorted({t for p in inp.pages.values() for t in p.get("schema_types", [])})}
    return o


@check("CHK-51", "Fix invalid JSON-LD and keep markup consistent with visible content.")
def chk51(inp):
    bad = [u for u, p in inp.ok.items() if p.get("schema_errors")]
    if not any(p.get("schema_types") for p in inp.ok.values()) and not bad:
        return Outcome("na", message="No structured data on site")
    o = _page_check(inp, inp.ok, bad, "{n} pages have structured-data errors", "Structured data parses without errors")
    o.evidence = {"errors": {u: inp.pages[u]["schema_errors"] for u in bad[:20]}}
    return o


# ===================================================================== International
@check("CHK-52", "Make hreflang annotations reciprocal, self-referencing and pointing at 200 URLs.")
def chk52(inp):
    pages = {u: p for u, p in inp.ok.items() if p.get("hreflang")}
    if not pages:
        return Outcome("na", message="hreflang not used")
    bad = []
    for u, p in pages.items():
        for alt in p["hreflang"]:
            if alt["href"] == u:
                continue
            tgt = inp.pages.get(alt["href"])
            if tgt is None:
                continue
            if not any(x["href"] == u for x in tgt.get("hreflang", [])):
                bad.append(u)
                break
    return _page_check(inp, pages, bad, "{n} pages have non-reciprocal hreflang", "hreflang reciprocal on {total} pages")


@check("CHK-53", "Confirm international targeting in Search Console (country/language) matches your markets.")
def chk53(inp):
    return Outcome("manual", message="Search Console no longer exposes international targeting via API — confirm in the GSC UI")


# ===================================================================== Off-page
@check("CHK-54", "Grow referring domains with relevant, authoritative links; monitor profile trends.")
def chk54(inp):
    if inp.ahrefs:
        rd = inp.ahrefs.get("refdomains") or 0
        return Outcome("pass" if rd >= 50 else "partial" if rd >= 10 else "fail", 0, [], f"{rd} referring domains (Ahrefs)", inp.ahrefs)
    if inp.backlinks:
        live = [b for b in inp.backlinks if b["status"] == "live"]
        rd = len({urlparse(b["source_url"]).netloc for b in live})
        return Outcome("partial", 0, [], f"{rd} referring domains in Backlink CRM (Ahrefs/Semrush NOT CONNECTED)", {"crm_live": len(live)})
    return Outcome("not_run", message="Requires Ahrefs or Semrush (NOT CONNECTED) or Backlink CRM data")


@check("CHK-55", "Review flagged toxic links and, after human approval, add them to a disavow file.")
def chk55(inp):
    scored = [b for b in inp.backlinks if b.get("toxicity_score") is not None]
    if not scored:
        return Outcome("not_run", message="No toxicity scores yet — requires Semrush (NOT CONNECTED) or Backlink CRM scoring")
    toxic = [b["source_url"] for b in scored if float(b["toxicity_score"]) >= 60]
    if toxic:
        return Outcome("fail", len(toxic), toxic[:100], f"{len(toxic)} backlinks scored toxic (≥60) — advisory, human disavow gate", total=len(scored))
    return Outcome("pass", message=f"No toxic links among {len(scored)} scored backlinks")


@check("CHK-56", "Reclaim lost links: contact the site owner or supplier, or replace the link.")
def chk56(inp):
    if not inp.backlinks and not inp.ahrefs:
        return Outcome("not_run", message="Requires Ahrefs (NOT CONNECTED) or Backlink CRM data")
    lost = [b["source_url"] for b in inp.backlinks if b["status"] in ("removed", "changed", "noindex")]
    if lost:
        return Outcome("fail", len(lost), lost[:100], f"{len(lost)} tracked backlinks were lost or changed", total=len(inp.backlinks))
    return Outcome("pass", message=f"No lost links among {len(inp.backlinks)} tracked backlinks")


@check("CHK-57", "Target domains linking to 2+ competitors but not to you.")
def chk57(inp):
    if not inp.connected("ahrefs"):
        return Outcome("not_run", message="Requires Ahrefs link intersect (NOT CONNECTED)")
    return Outcome("manual", message="Ahrefs connected — run link intersect against tracked competitors and review gaps")


# ===================================================================== Setup
@check("CHK-58", "Install the GA4 tag (gtag.js or GTM) on every page.")
def chk58(inp):
    bad = [u for u, p in inp.ok.items() if not p.get("ga4")]
    o = _page_check(inp, inp.ok, bad, "{n} of {total} pages have no GA4/GTM tag", "GA4/GTM present on all pages")
    if o.grade == "fail" and len(bad) == len(inp.ok):
        o.site_level = True
    return o


@check("CHK-59", "Verify the property in Search Console and connect it in Genranq Integrations.")
def chk59(inp):
    ev = {"gsc_connected": inp.connected("gsc"), "verification_meta": bool(inp.home.get("gsc_verification"))}
    if inp.connected("gsc"):
        return Outcome("pass", message="Search Console connected", evidence=ev)
    return Outcome("fail", 1, [], "Google Search Console is NOT CONNECTED" + (" (verification meta tag found on homepage)" if ev["verification_meta"] else ""), ev, site_level=True)


@check("CHK-60", "Link your official social profiles from the header/footer (and sameAs in schema).")
def chk60(inp):
    s = sorted({x for p in inp.pages.values() for x in p.get("social_links", [])})
    if s:
        return Outcome("pass", message=f"Social profiles linked: {', '.join(s)}", evidence={"profiles": s})
    return Outcome("fail", 1, [], "No social profile links found", site_level=True)


@check("CHK-61", "Embed or link your Google Maps listing on the contact page or footer.")
def chk61(inp):
    pages = [u for u, p in inp.pages.items() if p.get("map_link")]
    if pages:
        return Outcome("pass", message=f"Google Maps link/embed on {len(pages)} pages", evidence={"pages": pages[:10]})
    return Outcome("fail", 1, [], "No Google Maps link or embed found", site_level=True)


# ===================================================================== Local
@check("CHK-62", "Complete every GBP field and keep name, address and phone identical on site and profile.")
def chk62(inp):
    if not inp.gbp:
        return Outcome("not_run", message="Requires Google Business Profile (NOT CONNECTED) or a GBP location on the project")
    site_phones = {ph[-10:] for p in inp.pages.values() for ph in p.get("phones", [])}
    bad, ev = [], []
    for loc in inp.gbp:
        nap = loc.get("nap") or {}
        phone = re.sub(r"\D", "", nap.get("phone", ""))[-10:]
        missing = [k for k in ("name", "address", "phone", "website", "category", "hours") if not nap.get(k)]
        mismatch = bool(phone and site_phones and phone not in site_phones)
        ev.append({"location": loc.get("name"), "missing_fields": missing, "phone_on_site": phone in site_phones if phone else None})
        if missing or mismatch:
            bad.append(loc.get("name") or str(loc.get("id")))
    if bad:
        return Outcome("fail", len(bad), bad, f"{len(bad)} GBP locations incomplete or NAP-inconsistent", {"locations": ev})
    return Outcome("partial" if not inp.connected("gbp") else "pass", message="GBP complete and phone consistent with site", evidence={"locations": ev})


def run_check(code: str, inp: AuditInput) -> Outcome:
    f = REG.get(code)
    if not f:
        return Outcome("manual", message="No automated detector yet for this checklist item — review manually")
    try:
        return f(inp)
    except Exception as e:  # a detector bug must not sink the whole audit
        return Outcome("manual", message=f"Detector error: {type(e).__name__}: {str(e)[:200]}")
