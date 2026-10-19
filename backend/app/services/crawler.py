"""Built-in crawler (F-006 crawl orchestration).

* politeness: per-request delay, single connection per host, honest User-Agent
* robots.txt respected (configurable), sitemap discovery (robots + /sitemap.xml)
* URL normalisation + dedupe, BFS click depth, internal link graph (inlinks/outlinks)
* redirects followed manually so chains, loops and non-301 hops are recorded
* SSRF guard: private/loopback/link-local targets refused unless ALLOW_PRIVATE_CRAWL=1
* optional JS rendering (Playwright) for a sample of pages, mobile-UA parity sample

Screaming Frog (module 8) plugs in as an alternative engine when its CLI is connected.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings

S = get_settings()
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Mobile Safari/537.36 GenranqBot/1.0")
SOCIAL_HOSTS = ("facebook.com", "linkedin.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "tiktok.com", "pinterest.com")
SKIP_EXT = re.compile(r"\.(jpg|jpeg|png|gif|webp|avif|svg|ico|pdf|zip|gz|mp4|mp3|woff2?|ttf|css|js|xml|json)(\?|$)", re.I)


class CrawlBlocked(Exception):
    pass


def assert_public(url: str):
    if S.allow_private_crawl:
        return
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise CrawlBlocked(f"DNS lookup failed for {host}: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _ip_ok(ip):
            raise CrawlBlocked(f"Refusing to crawl non-public address {ip} ({host})")


MAX_BODY = 5 * 1024 * 1024


def _ip_ok(ip) -> bool:
    if S.allow_private_crawl:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def normalise(url: str) -> str:
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    scheme, netloc = p.scheme.lower(), p.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    path = p.path or "/"
    return urlunparse((scheme, netloc, path, "", p.query, ""))


def root_url(domain: str) -> str:
    d = domain.strip()
    if not re.match(r"^https?://", d):
        d = "https://" + d
    p = urlparse(d)
    return f"{p.scheme}://{p.netloc}/"


@dataclass
class CrawlResult:
    start_url: str
    pages: dict[str, dict] = field(default_factory=dict)
    site: dict = field(default_factory=dict)


class Crawler:
    def __init__(self, start: str, *, max_pages=250, max_depth=5, respect_robots=True, delay=None,
                 render_sample=5, mobile_sample=5, external_check_limit=100,
                 progress: Callable[[int, int], None] | None = None, timeout=15.0):
        self.start = normalise(root_url(start) if "://" not in start else start)
        self.host = urlparse(self.start).netloc
        self.max_pages, self.max_depth, self.respect_robots = max_pages, max_depth, respect_robots
        self.delay = S.crawl_delay_sec if delay is None else delay
        self.render_sample, self.mobile_sample, self.external_check_limit = render_sample, mobile_sample, external_check_limit
        self.progress = progress
        self.client = httpx.Client(timeout=timeout, follow_redirects=False,
                                   headers={"User-Agent": S.crawl_user_agent, "Accept-Encoding": "gzip, deflate, br"},
                                   trust_env=not S.allow_private_crawl)
        self.robots: urllib.robotparser.RobotFileParser | None = None
        self._last = 0.0

    # -------------------------------------------------------------- fetching
    def _polite(self):
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def fetch(self, url: str, *, ua: str | None = None, method: str = "GET", max_hops: int = 10) -> dict:
        chain, seen, cur = [], set(), url
        t0 = time.monotonic()
        for _ in range(max_hops + 1):
            assert_public(cur)
            self._polite()
            try:
                with self.client.stream(method, cur, headers={"User-Agent": ua} if ua else None) as r:
                    # Re-check the address actually connected to (defends against DNS rebinding).
                    ns = r.extensions.get("network_stream")
                    peer = ns.get_extra_info("server_addr") if ns else None
                    if peer and not _ip_ok(ipaddress.ip_address(peer[0])):
                        raise CrawlBlocked(f"Connected address {peer[0]} is not public ({urlparse(cur).hostname})")
                    content = b""
                    if method == "GET" and not r.is_redirect:
                        for chunk in r.iter_bytes():
                            content += chunk
                            if len(content) > MAX_BODY:
                                break
                r._content = content[:MAX_BODY]
            except httpx.TimeoutException:
                return {"url": url, "final_url": cur, "status": None, "error": "timeout", "chain": chain,
                        "elapsed": time.monotonic() - t0}
            except httpx.HTTPError as e:
                return {"url": url, "final_url": cur, "status": None, "error": str(e)[:200], "chain": chain,
                        "elapsed": time.monotonic() - t0}
            if r.is_redirect and r.headers.get("location"):
                nxt = normalise(urljoin(cur, r.headers["location"]))
                chain.append({"from": cur, "to": nxt, "status": r.status_code})
                if nxt in seen or nxt == cur:
                    return {"url": url, "final_url": nxt, "status": r.status_code, "error": "redirect_loop",
                            "chain": chain, "elapsed": time.monotonic() - t0}
                seen.add(cur)
                cur = nxt
                continue
            return {"url": url, "final_url": cur, "status": r.status_code, "headers": dict(r.headers),
                    "body": r.content.decode(r.encoding or "utf-8", errors="replace") if method == "GET" else "",
                    "chain": chain, "elapsed": time.monotonic() - t0, "bytes": len(r.content)}
        return {"url": url, "final_url": cur, "status": None, "error": "too_many_redirects", "chain": chain,
                "elapsed": time.monotonic() - t0}

    # -------------------------------------------------------------- site-level probes
    def probe_site(self) -> dict:
        info: dict = {"host": self.host, "start_url": self.start}
        home = self.fetch(self.start)
        info["home"] = {"status": home.get("status"), "error": home.get("error"), "elapsed": home.get("elapsed"),
                        "final_url": home.get("final_url"), "chain": home.get("chain")}
        info["assets"] = self._asset_check(home) if home.get("status") == 200 else {}
        # robots.txt
        robots_url = f"{urlparse(self.start).scheme}://{self.host}/robots.txt"
        r = self.fetch(robots_url)
        info["robots"] = {"url": robots_url, "status": r.get("status"), "exists": r.get("status") == 200,
                          "body": (r.get("body") or "")[:20000] if r.get("status") == 200 else ""}
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(info["robots"]["body"].splitlines() if info["robots"]["exists"] else [])
        self.robots = rp
        sitemaps = [l.split(":", 1)[1].strip() for l in info["robots"]["body"].splitlines()
                    if l.lower().startswith("sitemap:")]
        info["robots"]["sitemaps"] = sitemaps
        # sitemaps
        candidates = sitemaps or [f"{urlparse(self.start).scheme}://{self.host}/sitemap.xml"]
        info["sitemap"] = self._read_sitemaps(candidates)
        # protocol duplication / https
        http_url = "http://" + self.host + "/"
        https_url = "https://" + self.host + "/"
        h = self.fetch(http_url, max_hops=0)
        s = self.fetch(https_url, max_hops=0)
        info["protocol"] = {
            "http_status": h.get("status"), "http_location": (h.get("chain") or [{}])[0].get("to") if h.get("chain") else None,
            "http_redirects_to_https": bool(h.get("chain") and h["chain"][0]["to"].startswith("https://")),
            "https_status": s.get("status"), "https_ok": s.get("status") is not None and s.get("error") is None,
        }
        # soft 404 probe
        junk = f"{urlparse(self.start).scheme}://{self.host}/genranq-404-probe-{hashlib.md5(self.host.encode()).hexdigest()[:10]}"
        j = self.fetch(junk)
        info["soft404_probe"] = {"url": junk, "status": j.get("status")}
        return info

    def _asset_check(self, home: dict, limit: int = 6) -> dict:
        """Fetch a few same-host CSS/JS assets from the homepage and test for minification/compression."""
        page = self.parse(home.get("final_url") or self.start, home)
        out = []
        for a in [x for x in page.get("assets", []) if urlparse(x).netloc == urlparse(home.get("final_url") or self.start).netloc][:limit]:
            r = self.fetch(a)
            body = r.get("body") or ""
            if r.get("status") != 200 or not body:
                continue
            lines = body.count("\n") + 1
            avg_line = len(body) / lines
            ws_ratio = sum(1 for ch in body if ch in " \t\n") / max(1, len(body))
            hdr = {k.lower(): v for k, v in (r.get("headers") or {}).items()}
            out.append({"url": a, "bytes": len(body), "avg_line": round(avg_line, 1), "ws_ratio": round(ws_ratio, 3),
                        "minified": avg_line > 200 or ws_ratio < 0.12 or len(body) < 1500,
                        "encoding": hdr.get("content-encoding")})
        return {"checked": out}

    def _read_sitemaps(self, urls: list[str], limit: int = 5000) -> dict:
        out = {"files": [], "urls": [], "errors": []}
        queue, seen = list(urls), set()
        while queue and len(out["urls"]) < limit and len(seen) < 50:
            u = queue.pop(0)
            if u in seen:
                continue
            seen.add(u)
            try:
                r = self.fetch(u)
            except CrawlBlocked as e:
                out["errors"].append(str(e))
                continue
            out["files"].append({"url": u, "status": r.get("status")})
            if r.get("status") != 200:
                continue
            soup = BeautifulSoup(r.get("body") or "", "xml")
            if soup.find("sitemapindex"):
                queue += [loc.text.strip() for loc in soup.select("sitemap > loc")]
            else:
                out["urls"] += [normalise(loc.text.strip()) for loc in soup.select("url > loc")][: limit - len(out["urls"])]
        out["exists"] = any(f["status"] == 200 for f in out["files"])
        return out

    # -------------------------------------------------------------- page parsing
    def parse(self, url: str, resp: dict) -> dict:
        d: dict = {"status": resp.get("status"), "final_url": resp.get("final_url"), "chain": resp.get("chain", []),
                   "error": resp.get("error"), "elapsed": round(resp.get("elapsed") or 0, 3), "bytes": resp.get("bytes")}
        hdr = {k.lower(): v for k, v in (resp.get("headers") or {}).items()}
        d["content_type"] = hdr.get("content-type", "")
        d["content_encoding"] = hdr.get("content-encoding")
        d["x_robots"] = hdr.get("x-robots-tag", "")
        d["cache_control"] = hdr.get("cache-control")
        body = resp.get("body") or ""
        if resp.get("status") != 200 or "html" not in d["content_type"]:
            return d
        soup = BeautifulSoup(body, "lxml") if _has_lxml() else BeautifulSoup(body, "html.parser")
        head = soup.head or soup
        titles = [t.get_text(strip=True) for t in soup.find_all("title")]
        d["title"] = titles[0] if titles else None
        d["title_count"] = len(titles)
        md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        d["meta_description"] = (md.get("content") or "").strip() if md else None
        mr = soup.find("meta", attrs={"name": re.compile("^(robots|googlebot)$", re.I)})
        d["meta_robots"] = (mr.get("content") or "").lower() if mr else ""
        d["noindex"] = "noindex" in d["meta_robots"] or "noindex" in d["x_robots"].lower()
        can = soup.find("link", rel=lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])])
        d["canonical"] = normalise(urljoin(url, can["href"])) if can and can.get("href") else None
        d["headings"] = [h.name for h in soup.find_all(re.compile("^h[1-6]$"))]
        d["h1"] = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
        d["h2"] = [h.get_text(" ", strip=True)[:150] for h in soup.find_all("h2")][:60]
        d["h3_count"] = len(soup.find_all("h3"))
        d["tables"] = len(soup.find_all("table"))
        d["lists"] = len(soup.find_all(["ul", "ol"]))
        d["questions"] = [h.get_text(" ", strip=True)[:150] for h in soup.find_all(re.compile("^h[2-4]$"))
                          if h.get_text(strip=True).endswith("?")][:30]
        d["author"] = bool(soup.find(attrs={"rel": "author"}) or soup.find(attrs={"itemprop": "author"}) or
                           re.search(r'"author"\s*:', body))
        d["date_modified"] = bool(re.search(r'"dateModified"|article:modified_time', body))
        imgs = soup.find_all("img")
        d["images"] = len(imgs)
        d["images_missing_alt"] = [urljoin(url, i.get("src") or "") for i in imgs if not (i.get("alt") or "").strip()][:50]
        d["img_formats"] = sorted({(urlparse(i.get("src") or "").path.rsplit(".", 1)[-1] or "").lower()[:5] for i in imgs if i.get("src")})
        mrf = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
        d["meta_refresh"] = (mrf.get("content") or "") if mrf else None
        scripts_text = " ".join(s.get_text() for s in soup.find_all("script") if not s.get("src"))
        d["js_redirect"] = bool(re.search(r"(window|document)\.location(\.href)?\s*=|location\.replace\(", scripts_text))
        d["ga4"] = bool(re.search(r"gtag\(['\"]config['\"],\s*['\"]G-|googletagmanager\.com/gtag/js\?id=G-|googletagmanager\.com/gtm\.js", body))
        d["gsc_verification"] = bool(soup.find("meta", attrs={"name": "google-site-verification"}))
        d["phones"] = sorted({re.sub(r"\D", "", a["href"][4:]) for a in soup.find_all("a", href=True)
                              if a["href"].lower().startswith("tel:")})[:10]
        d["assets"] = [urljoin(url, t.get("src") or t.get("href")) for t in soup.find_all(["script", "link"])
                       if (t.name == "script" and t.get("src")) or
                       (t.name == "link" and "stylesheet" in (t.get("rel") or []) and t.get("href"))][:30]
        d["viewport"] = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))
        ifr = [f.get("src") or "" for f in soup.find_all("iframe")]
        d["iframes"] = len(ifr)
        d["content_iframes"] = sum(1 for x in ifr if not re.search(r"google\.[a-z.]+/maps|youtube|vimeo|recaptcha|googletagmanager", x))
        d["flash"] = bool(soup.find(["object", "embed"], attrs={"type": re.compile("shockwave|flash", re.I)})) or ".swf" in body.lower()
        d["hreflang"] = [{"lang": l.get("hreflang"), "href": normalise(urljoin(url, l.get("href", "")))}
                         for l in soup.find_all("link", hreflang=True) if l.get("href")]
        # structured data
        schema_types, schema_errors = [], []
        for s in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
            try:
                data = json.loads(s.string or s.get_text() or "")
                items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
                for it in items:
                    if isinstance(it, dict):
                        t = it.get("@type")
                        schema_types += t if isinstance(t, list) else [t] if t else []
                        if not it.get("@type"):
                            schema_errors.append("JSON-LD item without @type")
                if isinstance(data, dict) and "@context" not in data and "@graph" not in data:
                    schema_errors.append("JSON-LD without @context")
            except (ValueError, AttributeError) as e:
                schema_errors.append(f"Invalid JSON-LD: {str(e)[:80]}")
        if soup.find(attrs={"itemtype": True}):
            schema_types += [x.get("itemtype").rsplit("/", 1)[-1] for x in soup.find_all(attrs={"itemtype": True})]
        d["schema_types"] = sorted({str(t) for t in schema_types})
        d["schema_errors"] = schema_errors
        # links
        internal, external, tel, mailto, social, maps = [], [], False, False, set(), False
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            low = href.lower()
            if low.startswith("tel:"):
                tel = True
                continue
            if low.startswith("mailto:"):
                mailto = True
                continue
            if low.startswith(("javascript:", "#", "data:")):
                continue
            absu = normalise(urljoin(url, href))
            p = urlparse(absu)
            if p.scheme not in ("http", "https"):
                continue
            rel = " ".join(a.get("rel", [])) if isinstance(a.get("rel"), list) else (a.get("rel") or "")
            if p.netloc == self.host:
                internal.append({"href": absu, "rel": rel, "anchor": a.get_text(" ", strip=True)[:100]})
            else:
                external.append({"href": absu, "rel": rel})
                if any(p.netloc.endswith(s) for s in SOCIAL_HOSTS):
                    social.add(p.netloc)
                if ("google." in p.netloc and "/maps" in p.path) or p.netloc in ("maps.app.goo.gl", "goo.gl"):
                    maps = True
        if any("google.com/maps" in (f.get("src") or "") for f in soup.find_all("iframe")):
            maps = True
        d["internal_links"] = internal[:1000]
        d["external_links"] = external[:500]
        d["outlinks"] = len(internal) + len(external)
        d["tel"], d["mailto"], d["social_links"], d["map_link"] = tel, mailto, sorted(social), maps
        # mixed content
        if url.startswith("https://"):
            insecure = [t.get("src") for t in soup.find_all(["img", "script", "iframe", "link"], src=True)
                        if (t.get("src") or "").startswith("http://")]
            insecure += [t.get("href") for t in soup.find_all("link", href=True)
                         if (t.get("href") or "").startswith("http://") and "stylesheet" in (t.get("rel") or [])]
            d["mixed_content"] = insecure[:20]
        # content
        for t in soup(["script", "style", "noscript", "template"]):
            t.decompose()
        txt = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        words = re.findall(r"[A-Za-z0-9À-ɏ']+", txt)
        d["word_count"] = len(words)
        d["body_hash"] = hashlib.sha1(" ".join(w.lower() for w in words).encode()).hexdigest() if words else None
        top = {}
        for w in (w.lower() for w in words if len(w) > 3):
            top[w] = top.get(w, 0) + 1
        d["top_terms"] = sorted(top.items(), key=lambda kv: -kv[1])[:10]
        d["spa_root"] = bool(re.search(r'<div id="(root|app|__next)"\s*>\s*</div>', body))
        d["interstitial_markup"] = bool(re.search(r'class="[^"]*(modal|popup|newsletter-overlay|interstitial)[^"]*"', body, re.I))
        return d

    def allowed(self, url: str) -> bool:
        return not self.respect_robots or not self.robots or self.robots.can_fetch(S.crawl_user_agent, url)

    # -------------------------------------------------------------- crawl
    def run(self) -> CrawlResult:
        res = CrawlResult(self.start)
        res.site = self.probe_site()
        home_final = res.site["home"].get("final_url") or self.start
        if urlparse(home_final).netloc and urlparse(home_final).netloc != self.host:
            # Homepage redirects to another host (e.g. www): crawl that host instead.
            self.host = urlparse(home_final).netloc
        q = deque([(normalise(home_final), 0, "link")])
        seen = {normalise(home_final)}
        blocked = []
        sitemap_urls = [u for u in res.site["sitemap"]["urls"] if urlparse(u).netloc == self.host]
        sitemap_queued = False
        while len(res.pages) < self.max_pages:
            if not q:
                if sitemap_queued:
                    break
                sitemap_queued = True
                for u in sitemap_urls:
                    if u not in seen:
                        seen.add(u)
                        q.append((u, None, "sitemap"))
                if not q:
                    break
            url, depth, source = q.popleft()
            if not self.allowed(url):
                blocked.append(url)
                continue
            if SKIP_EXT.search(urlparse(url).path):
                continue
            resp = self.fetch(url)
            page = self.parse(url, resp)
            page.update({"depth": depth, "source": source})
            res.pages[url] = page
            if self.progress:
                self.progress(len(res.pages), self.max_pages)
            if depth is not None and depth >= self.max_depth:
                continue
            for l in page.get("internal_links", []):
                h = l["href"]
                if h not in seen and not SKIP_EXT.search(urlparse(h).path):
                    seen.add(h)
                    q.append((h, (depth or 0) + 1, "link"))
        res.site["robots_blocked"] = blocked[:500]
        res.site["sitemap_in_crawl"] = len([u for u in sitemap_urls if u in res.pages])
        # link graph: inlinks
        inl: dict[str, int] = {}
        for u, p in res.pages.items():
            for l in {x["href"] for x in p.get("internal_links", [])}:
                if l != u:
                    inl[l] = inl.get(l, 0) + 1
        for u, p in res.pages.items():
            p["inlinks"] = inl.get(u, 0)
        # internal links pointing at error URLs (statuses we know)
        status_of = {u: p.get("status") for u, p in res.pages.items()}
        for u, p in res.pages.items():
            p["broken_internal"] = sorted({l["href"] for l in p.get("internal_links", [])
                                           if (status_of.get(l["href"]) or 0) >= 400 or
                                           (l["href"] in status_of and status_of[l["href"]] is None)})[:50]
        res.site["external_status"] = self._check_external(res)
        res.site["mobile_parity"] = self._mobile_parity(res)
        res.site["render"] = self._render_sample(res)
        return res

    def _check_external(self, res: CrawlResult) -> dict:
        urls = []
        for p in res.pages.values():
            for l in p.get("external_links", []):
                if l["href"] not in urls:
                    urls.append(l["href"])
        out = {}
        for u in urls[: self.external_check_limit]:
            try:
                r = self.fetch(u, method="HEAD", max_hops=5)
                if r.get("status") in (405, 403, 501):
                    r = self.fetch(u, max_hops=5)
                err = (r.get("error") or "").lower()
                out[u] = r.get("status") if r.get("status") is not None else (
                    "dns_error" if any(x in err for x in ("name or service not known", "nodename", "getaddrinfo", "name resolution", "no address")) else "error")
            except CrawlBlocked as e:
                out[u] = "dns_error" if "DNS lookup failed" in str(e) else "blocked"
        return {"checked": len(out), "total": len(urls), "status": out}

    def _mobile_parity(self, res: CrawlResult) -> list[dict]:
        out = []
        for u, p in list(res.pages.items())[: self.mobile_sample]:
            if p.get("status") != 200:
                continue
            m = self.parse(u, self.fetch(u, ua=MOBILE_UA))
            out.append({"url": u, "desktop_words": p.get("word_count", 0), "mobile_words": m.get("word_count", 0),
                        "desktop_links": len(p.get("internal_links", [])), "mobile_links": len(m.get("internal_links", []))})
        return out

    def _render_sample(self, res: CrawlResult) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return {"available": False}
        sample = [u for u, p in res.pages.items() if p.get("status") == 200][: self.render_sample]
        out = {"available": True, "pages": []}
        try:
            with sync_playwright() as pw:
                b = pw.chromium.launch(args=["--no-sandbox"])
                pg = b.new_page(user_agent=S.crawl_user_agent)

                def _guard(route):
                    try:
                        assert_public(route.request.url)
                        route.continue_()
                    except CrawlBlocked:
                        route.abort()
                pg.route("**/*", _guard)
                for u in sample:
                    assert_public(u)
                    try:
                        pg.goto(u, wait_until="networkidle", timeout=20000)
                        txt = pg.evaluate("() => document.body ? document.body.innerText : ''")
                        rendered = len(re.findall(r"[A-Za-z0-9']+", txt or ""))
                        out["pages"].append({"url": u, "raw_words": res.pages[u].get("word_count", 0), "rendered_words": rendered})
                    except Exception as e:
                        out["pages"].append({"url": u, "error": str(e)[:120]})
                b.close()
        except Exception as e:
            return {"available": False, "error": str(e)[:200]}
        return out


_LXML = None


def _has_lxml():
    global _LXML
    if _LXML is None:
        try:
            import lxml  # noqa
            _LXML = True
        except ImportError:
            _LXML = False
    return _LXML
