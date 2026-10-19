"""Competitor audit — page to page (module 58).

Our URL vs up to 5 competitor URLs, side by side: title/meta, headings, word count, schema, internal and
external links, images/alt text, a speed proxy (measured fetch time), FAQ and E-E-A-T trust signals.
Facts come from skills.page_facts (SSRF-guarded fetch); topic/section gaps from gaps.structure_gap.
Each page gets a 0–100 score from nine weighted dimensions; recommendations name the competitor that
does better and by how much.
"""
from __future__ import annotations

import time

from . import gaps, skills
from .keywords import stem, tokens

WEIGHTS = {"title_meta": 10, "headings": 10, "content": 15, "schema": 10, "links": 10, "images": 10, "speed": 10, "faq": 10, "eeat": 15}
LABELS = {"title_meta": "Title & meta", "headings": "Headings", "content": "Content depth", "schema": "Structured data",
          "links": "Internal & external links", "images": "Images & alt text", "speed": "Speed (fetch time)", "faq": "FAQ",
          "eeat": "E-E-A-T signals"}


def facts(url: str) -> dict:
    t0 = time.monotonic()
    f = skills.page_facts(url)
    f["fetch_seconds"] = round(time.monotonic() - t0, 3)
    f.pop("text", None)
    f.pop("sample_paragraphs", None)
    return f


def _has_kw(text: str, kw: str | None) -> bool | None:
    if not kw:
        return None
    k = {stem(t) for t in tokens(kw)}
    return bool(k) and k <= {stem(t) for t in tokens(text or "")}


def score(f: dict, best_words: int, kw: str | None) -> dict:
    s = {}
    tl, ml = f.get("title_length") or 0, f.get("meta_length") or 0
    t_ok = 30 <= tl <= 65
    m_ok = 70 <= ml <= 160
    if kw:
        s["title_meta"] = 3 * t_ok + 3 * m_ok + 2 * bool(_has_kw(f.get("title"), kw)) + 2 * bool(_has_kw(" ".join(f.get("headings", {}).get("h1") or []), kw))
    else:
        s["title_meta"] = 5 * t_ok + 5 * m_ok
    hc = f.get("heading_counts") or {}
    s["headings"] = 4 * (hc.get("h1") == 1) + 3 * min(1, (hc.get("h2") or 0) / 2) + 3 * (not f.get("heading_level_skips"))
    s["content"] = 15 * min(1.0, (f.get("word_count") or 0) / max(600, best_words or 0))
    st = f.get("schema_types") or []
    s["schema"] = (5 if st else 0) + 5 * min(1, max(0, len(st) - 1) / 2) - (2 if f.get("schema_errors") else 0)
    s["links"] = 5 * min(1, (f.get("internal_links") or 0) / 5) + 5 * min(1, (f.get("external_links") or 0) / 2)
    imgs = f.get("images") or 0
    s["images"] = (4 * min(1, imgs / 2) + 6 * (1 - (f.get("images_missing_alt") or 0) / imgs)) if imgs else 0
    fs = f.get("fetch_seconds") or 99
    s["speed"] = 10 if fs <= 0.5 else 8 if fs <= 1 else 5 if fs <= 2 else 2 if fs <= 4 else 0
    fq = len(f.get("faq_questions") or [])
    s["faq"] = min(10, 2.5 * fq + (3 if f.get("faq_schema") else 0))
    s["eeat"] = min(15, 2.5 * (f.get("trust_score") or 0) + (2 if f.get("date_modified") else 0))
    s = {k: round(max(0, min(WEIGHTS[k], v)), 1) for k, v in s.items()}
    return {"dimensions": s, "total": round(sum(s.values()), 1)}


def metrics_row(f: dict) -> dict:
    hc = f.get("heading_counts") or {}
    return {"title": f.get("title"), "title_length": f.get("title_length"), "meta_description": f.get("meta_description"),
            "meta_length": f.get("meta_length"), "h1": (f.get("headings") or {}).get("h1", []), "h2_count": hc.get("h2", 0),
            "h3_count": hc.get("h3", 0), "heading_level_skips": f.get("heading_level_skips"), "word_count": f.get("word_count"),
            "schema_types": f.get("schema_types") or [], "internal_links": f.get("internal_links"), "external_links": f.get("external_links"),
            "images": f.get("images"), "images_missing_alt": f.get("images_missing_alt"), "fetch_seconds": f.get("fetch_seconds"),
            "faq_questions": len(f.get("faq_questions") or []), "faq_schema": f.get("faq_schema"), "trust_score": f.get("trust_score"),
            "trust_signals": f.get("trust_signals") or {}, "date_modified": f.get("date_modified"), "tables": f.get("tables"),
            "videos": f.get("videos"), "ctas": f.get("cta_count"), "noindex": f.get("noindex"), "canonical": f.get("canonical")}


def recommendations(ours: dict, comps: list[dict]) -> list[dict]:
    recs = []
    if not comps:
        return recs
    om, oscore = ours["metrics"], ours["score"]["dimensions"]

    def best(key):
        return max(comps, key=lambda c: c["score"]["dimensions"][key])

    for k in WEIGHTS:
        b = best(k)
        gap = b["score"]["dimensions"][k] - oscore[k]
        if gap < 2:
            continue
        bm = b["metrics"]
        msg = {
            "title_meta": f"Title is {om['title_length']} chars and meta description {om['meta_length']} chars — aim for 30–65 and 70–160, "
                          "and put the target keyword in the title and H1.",
            "headings": f"Use one H1 and a clear H2 outline: you have {len(om['h1'])} H1 / {om['h2_count']} H2 "
                        f"(best competitor: {len(bm['h1'])} / {bm['h2_count']}).",
            "content": f"Expand the content: {om['word_count']} words vs {bm['word_count']} on {b['url']}.",
            "schema": "Add structured data: competitors use " + (", ".join(bm["schema_types"]) or "schema") +
                      (f"; you have {', '.join(om['schema_types'])}." if om["schema_types"] else "; you have none."),
            "links": f"Add contextual links: {om['internal_links']} internal / {om['external_links']} external vs "
                     f"{bm['internal_links']} / {bm['external_links']}.",
            "images": f"Add relevant images with alt text: {om['images']} images ({om['images_missing_alt']} without alt) vs {bm['images']}.",
            "speed": f"The page took {om['fetch_seconds']}s to fetch vs {bm['fetch_seconds']}s — check server response time and page weight.",
            "faq": f"Add an FAQ section: {om['faq_questions']} question headings vs {bm['faq_questions']}"
                   + (" (with FAQPage schema)." if bm["faq_schema"] else "."),
            "eeat": "Show more trust signals (" + ", ".join(sorted(k2 for k2, v in bm["trust_signals"].items() if v and not om["trust_signals"].get(k2)))
                    + ") — the best competitor shows them.",
        }[k]
        recs.append({"dimension": k, "label": LABELS[k], "gap": round(gap, 1), "best_competitor": b["url"], "recommendation": msg})
    recs.sort(key=lambda r: -r["gap"])
    return recs


def compare(our_url: str, competitor_urls: list[str], keyword: str | None = None) -> dict:
    ours = facts(our_url)
    if ours.get("status") != 200:
        return {"available": False, "message": f"Could not fetch {our_url} (status {ours.get('status')}, {ours.get('error')})"}
    cf = [facts(u) for u in competitor_urls[:5]]
    ok = [f for f in cf if f.get("status") == 200]
    failed = [{"url": f["url"], "status": f.get("status"), "error": f.get("error")} for f in cf if f.get("status") != 200]
    if not ok:
        return {"available": False, "message": "None of the competitor pages could be fetched.", "failed": failed}
    best_words = max([ours.get("word_count") or 0] + [f.get("word_count") or 0 for f in ok])
    pages = [{"url": f["url"], "ours": i == 0, "metrics": metrics_row(f), "score": score(f, best_words, keyword)}
             for i, f in enumerate([ours] + ok)]
    our, comps = pages[0], pages[1:]
    leaders = {k: max(pages, key=lambda p: p["score"]["dimensions"][k])["url"] for k in WEIGHTS}
    sg = gaps.structure_gap(our_url, [f["url"] for f in ok])
    recs = recommendations(our, comps)
    for r in (sg.get("recommendations") or []) if sg.get("available") else []:
        recs.append({"dimension": "structure", "label": "Topic coverage", "gap": None, "best_competitor": None, "recommendation": r})
    rank = sorted(pages, key=lambda p: -p["score"]["total"])
    return {"available": True, "keyword": keyword, "weights": WEIGHTS, "labels": LABELS, "pages": pages, "failed": failed,
            "leaders": leaders, "our_rank": [p["url"] for p in rank].index(our["url"]) + 1, "page_count": len(pages),
            "our_score": our["score"]["total"],
            "competitor_avg": round(sum(c["score"]["total"] for c in comps) / len(comps), 1),
            "structure_gap": {k: sg.get(k) for k in ("missing_topics", "missing_sections", "missing_schema", "metrics")} if sg.get("available") else
            {"message": sg.get("message")},
            "recommendations": recs}
