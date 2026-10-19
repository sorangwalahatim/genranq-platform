"""Live / removed backlink verification (F-015).

For each backlink the source page is fetched through the SSRF-guarded Crawler and inspected:
HTTP status, whether an <a> pointing at the target exists, its rel attribute and anchor text, and
whether the source page is indexable (meta robots / X-Robots-Tag noindex, robots.txt disallow).
Every check is stored (backlink_check_history + backlink_check_evidence). Status changes are
recorded, re-checks are scheduled from the link's frequency, and newly removed links notify the project.

Pages that only render their content with JavaScript are NOT rendered headlessly here: such a check is
recorded as "could_not_verify" with the reason, never guessed.
"""
from __future__ import annotations

import re
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select

from .. import models as m
from .. import models_backlinks as mb
from .. import models_monitor as mm
from ..deps import log, notify
from .backlinks import ensure_meta, host_of, link_type_from_rel, schedule_next, url_key
from .crawler import Crawler, CrawlBlocked

GOOGLEBOT = "Googlebot"


def utcnow():
    return datetime.now(timezone.utc)


class Verifier:
    def __init__(self):
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def robots_allows(self, c: Crawler, url: str) -> bool | None:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            try:
                r = c.fetch(base + "/robots.txt")
            except CrawlBlocked:
                r = {}
            rp = None
            if r.get("status") == 200:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse((r.get("body") or "").splitlines())
            elif r.get("status") in (401, 403):
                rp = urllib.robotparser.RobotFileParser()
                rp.disallow_all = True
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(GOOGLEBOT, url)

    def inspect(self, bl: m.Backlink) -> dict:
        """Fetch + analyse; pure (no DB writes)."""
        out = {"status": "could_not_verify", "http_status": None, "link_found": None, "rel_found": None, "anchor_found": None,
               "anchor_matches": None, "noindex": None, "robots_blocked": None, "final_url": None, "reason": None, "evidence": {}}
        try:
            c = Crawler(bl.source_url, max_pages=1, render_sample=0, mobile_sample=0, external_check_limit=0, delay=0, timeout=20)
            r = c.fetch(bl.source_url)
        except CrawlBlocked as e:
            out["reason"] = f"Fetch refused: {e}"
            return out
        except Exception as e:  # never let one bad page fail the batch
            out["reason"] = f"Fetch failed: {type(e).__name__}: {e}"[:300]
            return out
        out["http_status"], out["final_url"] = r.get("status"), r.get("final_url")
        out["evidence"] = {"elapsed_s": round(r.get("elapsed") or 0, 3), "redirect_chain": r.get("chain") or [], "bytes": r.get("bytes")}
        st = r.get("status")
        if st is None:
            out["reason"] = f"Source page could not be fetched ({r.get('error')}) — status left unchanged"
            return out
        if st in (404, 410):
            out.update(status="removed", link_found=False, reason=f"Source page returns HTTP {st}")
            return out
        if st >= 400:
            out["reason"] = f"Source page returned HTTP {st} (blocked or unavailable) — could not verify"
            return out
        hdr = {k.lower(): v for k, v in (r.get("headers") or {}).items()}
        if "html" not in hdr.get("content-type", "html"):
            out["reason"] = f"Source is not an HTML page ({hdr.get('content-type')})"
            return out
        body = r.get("body") or ""
        soup = BeautifulSoup(body, "lxml")
        base = r.get("final_url") or bl.source_url
        mr = soup.find("meta", attrs={"name": re.compile("^(robots|googlebot)$", re.I)})
        robots_meta = (mr.get("content") or "").lower() if mr else ""
        out["noindex"] = "noindex" in robots_meta or "noindex" in hdr.get("x-robots-tag", "").lower()
        out["robots_blocked"] = not self.robots_allows(c, base)
        target = url_key(bl.target_url)
        hits = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            try:
                absu = urljoin(base, href)
            except ValueError:
                continue
            if url_key(absu) == target:
                rel = a.get("rel")
                rel = " ".join(rel) if isinstance(rel, list) else (rel or "")
                anchor = a.get_text(" ", strip=True)[:300]
                if not anchor and a.find("img"):
                    anchor = (a.find("img").get("alt") or "")[:300]
                hits.append({"href": absu, "rel": rel.lower(), "anchor": anchor, "snippet": str(a)[:400]})
        out["evidence"]["links_to_target"] = len(hits)
        if not hits:
            for t in soup(["script", "style", "noscript"]):
                t.decompose()
            words = len(re.findall(r"[A-Za-z0-9']+", soup.get_text(" ")))
            js_app = bool(re.search(r'<div id="(root|app|__next|__nuxt)"[^>]*>\s*</div>', body)) or (words < 40 and "<script" in body.lower())
            out["evidence"]["words"] = words
            if js_app:
                out.update(link_found=None, reason="Page renders its content with JavaScript; headless rendering is not run, so the link "
                                                   "could not be verified")
                return out
            out.update(status="removed", link_found=False, reason="No link to the target URL on the source page")
            return out
        # prefer a followed link when the page links more than once
        hit = sorted(hits, key=lambda h: link_type_from_rel(h["rel"]) != "dofollow")[0]
        found_type = link_type_from_rel(hit["rel"])
        out.update(link_found=True, rel_found=hit["rel"] or "", anchor_found=hit["anchor"])
        out["evidence"]["snippet"] = hit["snippet"]
        out["evidence"]["link_type_found"] = found_type
        if bl.anchor_text:
            out["anchor_matches"] = (hit["anchor"] or "").strip().lower() == bl.anchor_text.strip().lower() or \
                any((h["anchor"] or "").strip().lower() == bl.anchor_text.strip().lower() for h in hits)
        reasons = []
        if out["noindex"] or out["robots_blocked"]:
            status = "noindex"
            reasons.append("Link present but the source page is " + ("noindex" if out["noindex"] else "blocked by robots.txt"))
        elif bl.link_type and found_type != bl.link_type:
            status = "changed"
            reasons.append(f"Link is {found_type}, recorded as {bl.link_type}")
        elif out["anchor_matches"] is False:
            status = "changed"
            reasons.append(f"Anchor is “{hit['anchor']}”, expected “{bl.anchor_text}”")
        else:
            status = "live"
            reasons.append(f"Live {found_type} link found")
        out["status"], out["reason"] = status, "; ".join(reasons)
        return out


def record(db, bl: m.Backlink, res: dict, now: datetime | None = None) -> dict:
    """Persist a check, update the backlink status/schedule and notify on newly removed links."""
    now = now or utcnow()
    meta = ensure_meta(db, bl)
    prev = bl.status
    fetched = res["http_status"] is not None and res["status"] != "could_not_verify"
    h = m.BacklinkCheckHistory(organisation_id=bl.organisation_id, backlink_id=bl.id, checked_at=now, http_status=res["http_status"],
                               rel=res["rel_found"], is_indexed=(not (res["noindex"] or res["robots_blocked"])) if res["noindex"] is not None else None)
    db.add(h)
    db.flush()
    db.add(mb.BacklinkCheckEvidence(organisation_id=bl.organisation_id, history_id=h.id, backlink_id=bl.id, checked_at=now, status=res["status"],
                                    previous_status=prev, link_found=res["link_found"], anchor_found=res["anchor_found"],
                                    anchor_matches=res["anchor_matches"], rel_found=res["rel_found"], noindex=res["noindex"],
                                    robots_blocked=res["robots_blocked"], final_url=res["final_url"], reason=res["reason"],
                                    evidence=res["evidence"]))
    meta.last_check_note = res["reason"]
    changed = False
    if res["status"] == "could_not_verify":
        # an inconclusive check never overwrites a known status; a never-verified link says so honestly
        if prev == "pending":
            bl.status, changed = "could_not_verify", True
    else:
        if res["status"] != prev:
            bl.status, changed = res["status"], True
        bl.last_verified = now
        if res["status"] in ("live", "changed", "noindex") and not bl.first_detected:
            bl.first_detected = now
    if changed:
        meta.last_status_change_at = now
    schedule_next(meta, now)
    newly_removed = bl.status == "removed" and prev != "removed" and fetched
    if newly_removed:
        _notify_removed(db, bl, res)
    if changed:
        log(db, None, "backlink.status_changed", f"backlink:{bl.id}", {"from": prev, "to": bl.status, "reason": res["reason"]},
            org_id=bl.organisation_id)
    return {"backlink_id": str(bl.id), "previous": prev, "status": bl.status, "check_status": res["status"], "reason": res["reason"],
            "newly_removed": newly_removed}


def _notify_removed(db, bl: m.Backlink, res: dict):
    p = db.get(m.Project, bl.project_id)
    title = f"Backlink removed: {host_of(bl.source_url)} → {p.name if p else 'project'}"
    body = f"{bl.source_url} no longer links to {bl.target_url}. {res.get('reason') or ''}".strip()
    link = f"/backlinks?project={bl.project_id}&backlink={bl.id}"
    members = db.scalars(select(mm.ProjectMember.user_id).where(mm.ProjectMember.project_id == bl.project_id,
                                                                mm.ProjectMember.team.in_(("backlink", "pm")))).all()
    if members:
        for uid in set(members):
            notify(db, bl.organisation_id, "backlink_removed", title, body, link, user_id=uid)
    else:
        notify(db, bl.organisation_id, "backlink_removed", title, body, link)


def verify(db, backlinks: list[m.Backlink]) -> list[dict]:
    v = Verifier()
    out = []
    for bl in backlinks:
        out.append(record(db, bl, v.inspect(bl)))
    return out
