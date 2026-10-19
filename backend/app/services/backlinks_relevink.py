"""ReleVink AI (module 63): find the most relevant pages to place a link to a target URL.

  (a) internal placements — the project's own crawled pages (latest crawl), pre-ranked by TF-IDF over
      title / H1 / H2 / URL / top terms, then the best candidates are fetched to find the sentence where
      the link fits;
  (b) external placements — candidate pages/domains the user supplies and, optionally, domains from the
      backlink / supplier database; domains are explored from their homepage (the most on-topic internal links).

Scoring is deterministic (TF-IDF cosine + sentence term coverage). When an AI provider is connected and
requested, agents.llm refines the insertion sentence for the top placements; otherwise the run says so.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select

from .. import models as m
from .. import models_backlinks as mb
from .backlinks import host_of, url_key
from .crawler import Crawler, CrawlBlocked
from .keywords import stem, tokens

MAX_FETCH = 40
INTERNAL_FETCH = 8


def terms(text: str) -> list[str]:
    return [stem(t) for t in tokens(text or "") if not t.isdigit()]


def tfidf(docs: dict[str, Counter]) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    n = len(docs)
    df = Counter(t for c in docs.values() for t in c)
    idf = {t: math.log((n + 1) / (d + 1)) + 1 for t, d in df.items()}
    vecs = {}
    for k, c in docs.items():
        tot = sum(c.values()) or 1
        v = {t: (cnt / tot) * idf[t] for t, cnt in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1
        vecs[k] = {t: x / norm for t, x in v.items()}
    return vecs, idf


def cosine_query(q: list[str], vec: dict[str, float], idf: dict[str, float]) -> float:
    qc = Counter(q)
    qv = {t: c * idf.get(t, 1.0) for t, c in qc.items()}
    qn = math.sqrt(sum(x * x for x in qv.values())) or 1
    return sum(w / qn * vec.get(t, 0.0) for t, w in qv.items())


def fetch_page(url: str) -> dict:
    out = {"url": url, "status": None}
    try:
        c = Crawler(url, max_pages=1, render_sample=0, mobile_sample=0, external_check_limit=0, delay=0, timeout=15)
        r = c.fetch(url)
    except CrawlBlocked as e:
        out["error"] = str(e)
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:200]
        return out
    out["status"], out["final_url"] = r.get("status"), r.get("final_url") or url
    if r.get("status") != 200:
        out["error"] = r.get("error") or f"HTTP {r.get('status')}"
        return out
    soup = BeautifulSoup(r.get("body") or "", "lxml")
    mr = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    out["noindex"] = "noindex" in ((mr.get("content") or "").lower() if mr else "")
    out["title"] = soup.title.get_text(" ", strip=True) if soup.title else ""
    out["h1"] = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    out["h2"] = [h.get_text(" ", strip=True) for h in soup.find_all("h2")][:40]
    base = out["final_url"]
    links = []
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if h.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        try:
            links.append({"href": urljoin(base, h), "anchor": a.get_text(" ", strip=True)[:120]})
        except ValueError:
            continue
    out["links"] = links[:800]
    for t in soup(["script", "style", "noscript", "nav", "footer", "header", "form"]):
        t.decompose()
    blocks = [re.sub(r"\s+", " ", x.get_text(" ", strip=True)) for x in soup.find_all(["p", "li", "td", "blockquote"])]
    sents = []
    for b in blocks:
        for s in re.split(r"(?<=[.!?])\s+", b):
            s = s.strip()
            if 4 <= len(s.split()) <= 80:
                sents.append(s)
    out["sentences"] = list(dict.fromkeys(sents))[:600]
    out["text"] = re.sub(r"\s+", " ", (soup.body or soup).get_text(" ", strip=True))[:60000]
    return out


def best_sentence(sentences: list[str], q: list[str], phrase: str) -> tuple[str | None, float]:
    qs = set(q)
    best, score = None, 0.0
    for s in sentences:
        st = set(terms(s))
        cov = len(qs & st) / len(qs) if qs else 0
        bonus = 0.5 if phrase and phrase.lower() in s.lower() else 0
        sc = cov + bonus - (0.002 * max(0, len(s.split()) - 35))
        if sc > score:
            best, score = s, sc
    return best, round(min(1.0, score), 3)


def page_doc(p: dict) -> Counter:
    c = Counter()
    c.update(terms(p.get("title") or "") * 3)
    c.update(terms(" ".join(p.get("h1") or [])) * 3)
    c.update(terms(" ".join(p.get("h2") or [])) * 2)
    c.update(terms((urlparse(p.get("url") or "").path or "").replace("-", " ").replace("/", " ").replace("_", " ")) * 2)
    for t, n in p.get("top_terms") or []:
        c.update({stem(t): min(int(n), 20)})
    if p.get("text"):
        c.update(terms(p["text"][:30000]))
    return c


def suggest_anchor(anchor: str | None, keyword: str, sentence: str | None) -> tuple[str, str]:
    a = (anchor or "").strip() or keyword.strip()
    if sentence and a.lower() in sentence.lower():
        return a, "Link the existing words in this sentence."
    if sentence and keyword.lower() in (sentence or "").lower():
        return keyword, "Link the existing words in this sentence."
    return a, "Add the anchor to this sentence (or a short sentence after it)."


def _links_to(p: dict, target: str) -> bool:
    t = url_key(target)
    return any(url_key(l["href"]) == t for l in p.get("links") or p.get("internal_links") or [])


def internal(db, project: m.Project, target: str, keyword: str, anchor: str | None, q: list[str]) -> dict:
    from .monitor import _own_crawls, _pages
    pages = {}
    for site in db.scalars(select(m.Site).where(m.Site.project_id == project.id)):
        cr = _own_crawls(db, site)
        if cr:
            for u, p in _pages(db, cr[0].id).items():
                if p.get("status") == 200 and not p.get("noindex") and url_key(u) != url_key(target):
                    pages[u] = {**p, "url": u}
    if not pages:
        return {"available": False, "message": "No crawl of this project's site yet — run an SEO audit first to find internal placements.",
                "placements": []}
    docs = {u: page_doc(p) for u, p in pages.items()}
    vecs, idf = tfidf(docs)
    pre = sorted(((cosine_query(q, vecs[u], idf), u) for u in pages), reverse=True)
    pre = [(s, u) for s, u in pre if s > 0][:INTERNAL_FETCH]
    out = []
    fetched = {u: fetch_page(u) for _, u in pre}
    fdocs = {u: page_doc({**pages[u], **f}) for u, f in fetched.items() if f.get("status") == 200}
    fvecs, fidf = tfidf({**docs, **fdocs})
    for s0, u in pre:
        f = fetched[u]
        sent, cov = best_sentence(f.get("sentences") or [], q, keyword)
        sc = cosine_query(q, fvecs[u], fidf)
        score = round(100 * min(1.0, 0.6 * sc + 0.4 * cov), 1)
        a, how = suggest_anchor(anchor, keyword, sent)
        already = _links_to(pages[u], target) or _links_to(f, target)
        out.append({"url": u, "title": f.get("title") or pages[u].get("title"), "score": score, "term_similarity": round(sc, 3),
                    "sentence_match": cov, "sentence": sent, "suggested_anchor": a, "how": how, "already_links": already,
                    "fetched": f.get("status") == 200, "error": f.get("error")})
    out.sort(key=lambda r: (r["already_links"], -r["score"]))
    return {"available": True, "pages_considered": len(pages), "pages_fetched": len(fetched), "placements": out}


def _domain_pages(home: dict, q: list[str], limit: int = 3) -> list[str]:
    host = urlparse(home.get("final_url") or home["url"]).netloc
    scored = []
    for l in home.get("links") or []:
        p = urlparse(l["href"])
        if p.netloc != host or p.scheme not in ("http", "https") or url_key(l["href"]) == url_key(home["url"]):
            continue
        st = set(terms(l["anchor"] + " " + p.path.replace("-", " ").replace("/", " ")))
        ov = len(st & set(q))
        if ov:
            scored.append((ov, l["href"]))
    return list(dict.fromkeys(u for _, u in sorted(scored, key=lambda x: -x[0])))[:limit]


def db_candidates(db, project: m.Project, limit: int = 15) -> list[str]:
    doms = []
    for (d,) in db.execute(select(mb.SupplierPrice.domain).distinct()):
        doms.append(d)
    for (u,) in db.execute(select(m.Backlink.source_url).where(m.Backlink.status.in_(("live", "changed", "pending")))):
        doms.append(host_of(u))
    own = {host_of(s.domain) for s in db.scalars(select(m.Site).where(m.Site.project_id == project.id))}
    out = []
    for d in doms:
        d = host_of(d)
        if d and d not in own and d not in out:
            out.append(d)
    return out[:limit]


def external(db, project: m.Project, target: str, keyword: str, anchor: str | None, q: list[str], candidates: list[str],
             include_db: bool) -> dict:
    cands = list(candidates)
    if include_db:
        cands += [c for c in db_candidates(db, project) if c not in cands]
    if not cands:
        return {"available": False, "message": "Add candidate domains or URLs (or include domains from your backlink/supplier database).",
                "placements": []}
    pages, fetches, skipped = {}, 0, []
    for c in cands:
        if fetches >= MAX_FETCH:
            skipped.append(c)
            continue
        url = c if "://" in c else "https://" + c.strip("/") + "/"
        f = fetch_page(url)
        fetches += 1
        if f.get("status") != 200:
            pages[url] = f
            continue
        pages[url] = f
        if (urlparse(url).path or "/") == "/":
            for u in _domain_pages(f, q):
                if fetches >= MAX_FETCH:
                    break
                if u not in pages:
                    pages[u] = fetch_page(u)
                    fetches += 1
    ok = {u: p for u, p in pages.items() if p.get("status") == 200}
    vecs, idf = tfidf({u: page_doc(p) for u, p in ok.items()}) if ok else ({}, {})
    out = []
    for u, p in pages.items():
        if p.get("status") != 200:
            out.append({"url": u, "domain": host_of(u), "score": 0, "fetched": False, "error": p.get("error") or f"HTTP {p.get('status')}"})
            continue
        sent, cov = best_sentence(p.get("sentences") or [], q, keyword)
        sc = cosine_query(q, vecs[u], idf)
        score = round(100 * min(1.0, 0.6 * sc + 0.4 * cov), 1)
        a, how = suggest_anchor(anchor, keyword, sent)
        out.append({"url": u, "domain": host_of(u), "title": p.get("title"), "score": score, "term_similarity": round(sc, 3),
                    "sentence_match": cov, "sentence": sent, "suggested_anchor": a, "how": how, "already_links": _links_to(p, target),
                    "noindex": p.get("noindex"), "fetched": True})
    out.sort(key=lambda r: (not r["fetched"], r.get("already_links", False), bool(r.get("noindex")), -r["score"]))
    return {"available": True, "candidates": len(cands), "pages_fetched": fetches, "skipped_over_limit": skipped, "placements": out}


def ai_refine(db, org_id, keyword: str, target: str, placements: list[dict]) -> dict:
    from .agents import NoAI, llm
    top = [p for p in placements if p.get("sentence")][:5]
    if not top:
        return {"used": False, "note": "No sentences to refine."}
    prompt = ("You place contextual links. For each page below, rewrite the given sentence so it naturally includes a link "
              f"with the anchor text to {target} (topic: “{keyword}”). Keep the meaning and tone; do not invent facts.\n"
              "Answer ONLY a JSON array of objects {\"url\":..., \"sentence\":..., \"anchor\":...}.\n\n" +
              "\n".join(f"- url: {p['url']}\n  anchor: {p['suggested_anchor']}\n  sentence: {p['sentence']}" for p in top))
    try:
        txt, meta = llm(db, org_id, prompt, max_tokens=900)
    except NoAI as e:
        return {"used": False, "note": f"{e} — results use deterministic scoring only."}
    m_ = re.search(r"\[.*\]", txt, re.S)
    try:
        items = json.loads(m_.group(0)) if m_ else []
    except ValueError:
        items = []
    by = {i.get("url"): i for i in items if isinstance(i, dict)}
    for p in placements:
        if p["url"] in by:
            p["ai_sentence"] = str(by[p["url"]].get("sentence") or "")[:600]
    return {"used": True, "model": meta.get("model"), "refined": len(by)}


def run(db, project: m.Project, r: mb.RelevinkRun) -> dict:
    q = terms(r.keyword) + terms(r.anchor or "")
    if not q:
        return {"error": "The keyword has no meaningful terms."}
    res: dict = {"query_terms": sorted(set(q))}
    if r.mode in ("internal", "both"):
        res["internal"] = internal(db, project, r.target_url, r.keyword, r.anchor, q)
    if r.mode in ("external", "both"):
        res["external"] = external(db, project, r.target_url, r.keyword, r.anchor, q, r.candidates or [], r.include_db)
    if r.use_ai:
        allp = (res.get("internal", {}).get("placements") or []) + (res.get("external", {}).get("placements") or [])
        allp = sorted([p for p in allp if p.get("fetched") and not p.get("already_links")], key=lambda p: -p["score"])
        res["ai"] = ai_refine(db, project.organisation_id, r.keyword, r.target_url, allp)
    else:
        res["ai"] = {"used": False, "note": "Deterministic scoring (TF-IDF term similarity + sentence match). Enable AI refinement "
                                            "with a connected AI provider to rewrite insertion sentences."}
    return res
