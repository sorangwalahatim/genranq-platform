"""Comprehensive audit (F-044…F-049, F-006…F-009).

crawl → gather connected sources (PSI, GSC, Ahrefs, Semrush, Backlink CRM, GBP,
rankings) → run every active checklist item → weighted score + category scores →
prioritised issues (impact × prevalence × effort-inverse) → optional Claude
recommendations → auto-generated SEO Action Plan.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select

from .. import models as m
from ..db import system_session
from . import entitlements as ent
from . import integrations as ih
from . import sources
from .checks import RECS, AuditInput, Outcome, run_check
from .crawler import Crawler, normalise, root_url
from .jobs import FAIL_HOOKS, JobCtx, PermanentError, handler

SEV_WEIGHT = {"High": 10, "Med": 5, "Medium": 5, "Low": 2}
IMPACT = {"critical": 10, "high": 7, "medium": 4, "low": 1.5}
EFFORT = {"SEO Tech": 1.0, "SEO": 1.5, "Content": 2.5, "Web Dev": 2.5, "UX": 2.0}
GRADE_SCORE = {"pass": 1.0, "partial": 0.5}
EVALUATED = {"pass", "fail", "partial"}


def utcnow():
    return datetime.now(timezone.utc)


def severity_of(item_sev: str, out: Outcome) -> str:
    """Sheet severities are High/Med/Low. 'critical' = a High check that fails site-wide
    or on ≥25% of the population (documented in ASSUMPTIONS.md)."""
    base = {"High": "high", "Med": "medium", "Medium": "medium", "Low": "low"}.get(item_sev, "medium")
    if base == "high" and out.grade == "fail":
        prev = (out.count / out.total) if out.total else 1.0
        if out.site_level or prev >= 0.25:
            return "critical"
    return base


def prevalence(out: Outcome) -> float:
    if out.site_level or not out.total:
        return 1.0
    return max(0.0, min(1.0, out.count / out.total))


def check_score(out: Outcome) -> float:
    if out.grade == "pass":
        return 1.0
    if out.grade == "partial":
        return 0.5
    if out.site_level or not out.total:
        return 0.0
    return 0.5 * (1 - prevalence(out))


def priority(sev: str, out: Outcome, owner: str) -> float:
    p = 0.2 + 0.8 * prevalence(out)
    return round(IMPACT[sev] * p * (1 / EFFORT.get(owner, 2.0)) * 10, 2)


def score_results(items: list[m.AuditChecklistItem], outs: dict[str, Outcome]) -> tuple[float | None, dict, dict]:
    cats: dict[str, list[tuple[float, float]]] = {}
    for it in items:
        o = outs[it.code]
        if o.grade in EVALUATED:
            cats.setdefault(it.category, []).append((SEV_WEIGHT.get(it.severity, 5), check_score(o)))
    cat_scores = {c: round(100 * sum(w * s for w, s in v) / sum(w for w, _ in v), 1) for c, v in cats.items()}
    allv = [x for v in cats.values() for x in v]
    overall = round(100 * sum(w * s for w, s in allv) / sum(w for w, _ in allv), 1) if allv else None
    counts = {g: sum(1 for o in outs.values() if o.grade == g) for g in ("pass", "fail", "partial", "not_run", "manual", "na")}
    counts["evaluated"] = counts["pass"] + counts["fail"] + counts["partial"]
    counts["coverage_pct"] = round(100 * counts["evaluated"] / max(1, len(items)), 1)
    return overall, cat_scores, counts


# ------------------------------------------------------------------ Screaming Frog engine (module 8)
def crawl_with_screaming_frog(cli: str, start: str, max_pages: int) -> dict:
    out = tempfile.mkdtemp(prefix="sf-")
    subprocess.run([cli, "--crawl", start, "--headless", "--output-folder", out, "--export-tabs", "Internal:All",
                    "--overwrite"], check=True, timeout=3600, capture_output=True)
    pages = {}
    f = Path(out) / "internal_all.csv"
    with open(f, newline="", encoding="utf-8-sig") as fh:
        for row in list(csv.DictReader(fh))[:max_pages]:
            u = normalise(row["Address"])
            h1 = [row[k] for k in ("H1-1", "H1-2") if row.get(k)]
            pages[u] = {"status": int(row.get("Status Code") or 0) or None, "content_type": row.get("Content Type", ""),
                        "title": row.get("Title 1") or None, "meta_description": row.get("Meta Description 1") or None,
                        "h1": h1, "noindex": "noindex" in (row.get("Indexability Status") or "").lower(),
                        "canonical": normalise(row["Canonical Link Element 1"]) if row.get("Canonical Link Element 1") else None,
                        "word_count": int(row.get("Word Count") or 0), "depth": int(row.get("Crawl Depth") or 0),
                        "inlinks": int(row.get("Inlinks") or 0), "outlinks": int(row.get("Outlinks") or 0),
                        "elapsed": float(row.get("Response Time") or 0), "body_hash": row.get("Hash"),
                        "source": "screaming_frog"}
    return pages


# ------------------------------------------------------------------ the job
def _fail_audit(job, err):
    with system_session() as db:
        ar = db.get(m.AuditRun, job.payload.get("audit_run_id"))
        if ar and ar.status != "COMPLETED":
            ar.status, ar.finished_at = "FAILED", utcnow()
            ar.summary = {**(ar.summary or {}), "error": err[:500]}
            reserved = (ar.summary or {}).get("reserved_pages", 0)
            if reserved:
                ent.refund(db, ar.organisation_id, "audit_pages", reserved)
                ar.summary = {**ar.summary, "reserved_pages": 0}


FAIL_HOOKS["audit"] = _fail_audit


@handler("audit")
def run_audit_job(ctx: JobCtx) -> dict:
    run_id = ctx.payload["audit_run_id"]
    with ctx.session() as db:
        ar = db.get(m.AuditRun, run_id)
        if not ar:
            raise PermanentError("audit run not found")
        site = db.get(m.Site, ar.site_id)
        target = site.domain
        if ar.competitor_id:
            target = db.get(m.Competitor, ar.competitor_id).domain
        sources_map = ih.connected_map(db)
        ar.status, ar.started_at = "RUNNING", utcnow()
        reserved = (ar.summary or {}).get("reserved_pages", 0)
        max_pages = ar.max_pages
        project_id = site.project_id
        sf_cli = None
        if sources_map.get("screaming_frog") == "CONNECTED":
            sf_cli = os.environ.get("SCREAMING_FROG_CLI", "screamingfrogseospidercli")  # operator-controlled, never tenant input
        cr = m.CrawlRun(organisation_id=ctx.org_id, site_id=site.id, start_url=root_url(target), max_pages=max_pages,
                        engine="screaming_frog" if sf_cli else "builtin", status="RUNNING", started_at=utcnow())
        db.add(cr)
        db.flush()
        ar.crawl_run_id, crawl_id = cr.id, cr.id

    # ---------- crawl (outside a DB transaction; can take minutes)
    def prog(n, total):
        ctx.progress(5 + int(60 * n / max(1, total)))

    crawler = Crawler(target, max_pages=max_pages, progress=prog)
    try:
        if sf_cli:
            res_site = crawler.probe_site()
            pages = crawl_with_screaming_frog(sf_cli, crawler.start, max_pages)
        else:
            res = crawler.run()
            res_site, pages = res.site, res.pages
    except Exception as e:
        with ctx.session() as db:
            c = db.get(m.CrawlRun, crawl_id)
            c.status, c.error, c.finished_at = "FAILED", str(e)[:500], utcnow()
        raise

    with ctx.session() as db:
        c = db.get(m.CrawlRun, crawl_id)
        c.status, c.pages_crawled, c.finished_at = "COMPLETED", len(pages), utcnow()
        site_info = {k: v for k, v in res_site.items() if k != "robots"}
        site_info["robots"] = {k: v for k, v in res_site.get("robots", {}).items() if k != "body"}
        c.site_info = json.loads(json.dumps(site_info, default=str))
        for u, p in pages.items():
            db.add(m.CrawlPage(organisation_id=ctx.org_id, crawl_run_id=crawl_id, url=u[:2000], status_code=p.get("status"),
                               depth=p.get("depth") if p.get("depth") is not None else -1, content_type=(p.get("content_type") or "")[:200],
                               data=json.loads(json.dumps(p, default=str))))
        # refund unused reserved audit pages
        unused = max(0, reserved - len(pages))
        ent.refund(db, ctx.org_id, "audit_pages", unused)
        ar_ = db.get(m.AuditRun, run_id)
        ar_.summary = {**(ar_.summary or {}), "reserved_pages": min(reserved, len(pages))}
    ctx.progress(70)

    # ---------- gather connected sources
    psi_rows, gsc_rows, gsc_sm, ahrefs, warnings = [], [], None, None, []
    with ctx.session() as db:
        site = db.get(m.Site, ar.site_id)
        if sources_map.get("pagespeed") == "CONNECTED":
            top = [u for u, p in pages.items() if p.get("status") == 200][:3]
            for u in top:
                for dev in ("mobile", "desktop"):
                    try:
                        r = sources.pagespeed(db, ctx.org_id, u, dev)
                        db.add(m.PagespeedResult(organisation_id=ctx.org_id, site_id=site.id, url=u, device=dev, lcp=r["lcp"],
                                                 cls=r["cls"], inp=r["inp"], score=r["score"], audits=r["audits"]))
                        psi_rows.append({**r, "checked_at": utcnow()})
                    except ih.IntegrationError as e:
                        warnings.append(f"PSI {u} {dev}: {e}")
                        ih.mark_synced(db, ctx.org_id, "pagespeed", str(e))
            if psi_rows:
                ih.mark_synced(db, ctx.org_id, "pagespeed")
        if sources_map.get("gsc") == "CONNECTED" and site.gsc_property and not ar.competitor_id:
            try:
                buckets = sources.gsc_inspect(db, ctx.org_id, site.gsc_property, list(pages)[:50])
                db.execute(delete(m.GscIssue).where(m.GscIssue.site_id == site.id))
                for t, us in buckets.items():
                    db.add(m.GscIssue(organisation_id=ctx.org_id, site_id=site.id, type=t, count=len(us), sample_urls=us[:50]))
                    gsc_rows.append({"type": t, "count": len(us), "sample_urls": us})
                gsc_sm = sources.gsc_sitemaps(db, ctx.org_id, site.gsc_property)
                ih.mark_synced(db, ctx.org_id, "gsc")
            except ih.IntegrationError as e:
                warnings.append(f"GSC: {e}")
                ih.mark_synced(db, ctx.org_id, "gsc", str(e))
        elif not psi_rows:
            psi_rows = [{"url": r.url, "device": r.device, "lcp": float(r.lcp) if r.lcp is not None else None,
                         "cls": float(r.cls) if r.cls is not None else None, "inp": float(r.inp) if r.inp is not None else None,
                         "score": float(r.score) if r.score is not None else None, "audits": r.audits, "checked_at": r.checked_at}
                        for r in db.scalars(select(m.PagespeedResult).where(m.PagespeedResult.site_id == site.id,
                                                                           m.PagespeedResult.checked_at > utcnow() - timedelta(days=30)))]
        if not gsc_rows and not ar.competitor_id:
            gsc_rows = [{"type": g.type, "count": g.count, "sample_urls": g.sample_urls}
                        for g in db.scalars(select(m.GscIssue).where(m.GscIssue.site_id == site.id))]
        if sources_map.get("ahrefs") == "CONNECTED":
            try:
                ahrefs = sources.ahrefs_metrics(db, ctx.org_id, target)
                ih.mark_synced(db, ctx.org_id, "ahrefs")
            except ih.IntegrationError as e:
                warnings.append(f"Ahrefs: {e}")
        kws = db.scalars(select(m.Keyword).where(m.Keyword.project_id == project_id)).all()
        ranks = [{"keyword": k.phrase, "url": r.url, "checked_at": r.checked_at}
                 for k in kws for r in db.scalars(select(m.RankHistory).where(m.RankHistory.keyword_id == k.id,
                                                                            m.RankHistory.checked_at > utcnow() - timedelta(days=30)))]
        backlinks = [] if ar.competitor_id else [
            {"source_url": b.source_url, "status": b.status, "toxicity_score": b.toxicity_score}
            for b in db.scalars(select(m.Backlink).where(m.Backlink.target_site_id == site.id))]
        gbp = [] if ar.competitor_id else [{"id": str(g.id), "name": g.name, "nap": g.nap}
                                           for g in db.scalars(select(m.GbpLocation).where(m.GbpLocation.project_id == project_id))]
        items = db.scalars(select(m.AuditChecklistItem).where(m.AuditChecklistItem.active).order_by(m.AuditChecklistItem.code)).all()
        keywords = [{"phrase": k.phrase, "target_url": normalise(k.target_url) if k.target_url else None} for k in kws]

    inp = AuditInput(pages=pages, site=res_site, sources=sources_map, psi=psi_rows, gsc=gsc_rows, ranks=ranks,
                     backlinks=backlinks, ahrefs=ahrefs, gbp=gbp, keywords=keywords, gsc_sitemaps=gsc_sm)
    outs = {it.code: run_check(it.code, inp) for it in items}
    ctx.progress(85)
    overall, cat_scores, counts = score_results(items, outs)

    with ctx.session() as db:
        ar = db.get(m.AuditRun, run_id)
        db.execute(delete(m.AuditResult).where(m.AuditResult.audit_run_id == run_id))
        db.execute(delete(m.AuditIssue).where(m.AuditIssue.audit_run_id == run_id))
        sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for it in items:
            o = outs[it.code]
            db.add(m.AuditResult(organisation_id=ctx.org_id, audit_run_id=run_id, checklist_item_id=it.id, grade=o.grade,
                                 count=o.count, affected=o.affected[:200], message=o.message,
                                 source_payload=json.loads(json.dumps(o.evidence, default=str))))
            if o.grade in ("fail", "partial"):
                sev = severity_of(it.severity, o)
                if o.grade == "partial" and sev == "critical":
                    sev = "high"
                sev_counts[sev] += 1
                db.add(m.AuditIssue(organisation_id=ctx.org_id, audit_run_id=run_id, rule=it.code, category=it.category,
                                    severity=sev, priority_score=priority(sev, o, it.default_owner) * (1 if o.grade == "fail" else 0.5),
                                    title=f"{it.check}", affected_count=o.count, affected_urls=o.affected[:200],
                                    evidence={"message": o.message, "grade": o.grade, **json.loads(json.dumps(o.evidence, default=str))},
                                    recommendation=it.recommendation or RECS.get(it.code), owner=it.default_owner))
        ar.score, ar.category_scores = overall, cat_scores
        ar.summary = {**counts, "issues": sev_counts, "pages_crawled": len(pages), "warnings": warnings,
                      "passed_checks": counts["pass"], "reserved_pages": 0}
        ar.sources = sources_map
        ar.status, ar.finished_at = "COMPLETED", utcnow()
        db.flush()
        ai = None
        if sources_map.get("claude") == "CONNECTED":
            try:
                ai = ai_recommendations(db, ctx.org_id, run_id)
            except ih.IntegrationError as e:
                ar.summary = {**ar.summary, "warnings": warnings + [f"Claude: {e}"]}
        plan = None
        if not ar.competitor_id:
            plan = build_action_plan(db, ctx.org_id, run_id)
        db.add(m.Notification(organisation_id=ctx.org_id, kind="audit_completed",
                              title=f"Audit completed: {target} scored {overall if overall is not None else '—'}",
                              body=f"{sev_counts['critical']} critical, {sev_counts['high']} high issues", link=f"/audits/{run_id}"))
        db.add(m.ActivityLog(organisation_id=ctx.org_id, action="audit.completed", target=f"audit_run:{run_id}",
                             meta={"score": overall, "pages": len(pages)}))
    return {"score": overall, "pages": len(pages), "issues": sev_counts, "ai_recommendations": bool(ai),
            "plan_id": str(plan.id) if plan else None}


def ai_recommendations(db, org_id, run_id, top: int = 5) -> int:
    issues = db.scalars(select(m.AuditIssue).where(m.AuditIssue.audit_run_id == run_id)
                        .order_by(m.AuditIssue.priority_score.desc()).limit(top)).all()
    if not issues:
        return 0
    prompt = ("You are a senior technical SEO. For each issue below give 3-5 concrete fix steps for a web team. "
              "Reply as JSON: {\"<code>\": \"<markdown steps>\"}.\n\n" +
              "\n".join(f"{i.rule} [{i.severity}] {i.title} — {i.evidence.get('message')} — sample: {i.affected_urls[:3]}" for i in issues))
    text, usage = sources.claude_complete(db, org_id, prompt)
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        recs = json.loads(text[start:end])
    except ValueError:
        recs = {}
    for i in issues:
        if recs.get(i.rule):
            i.evidence = {**i.evidence, "ai_recommendation": recs[i.rule], "ai_model": usage.get("model")}
    return len(recs)


# ------------------------------------------------------------------ SEO Action Plan (F-049)
def build_action_plan(db, org_id, run_id, create_tasks: bool = False) -> m.SeoPlan:
    ar = db.get(m.AuditRun, run_id)
    site = db.get(m.Site, ar.site_id)
    issues = db.scalars(select(m.AuditIssue).where(m.AuditIssue.audit_run_id == run_id)
                        .order_by(m.AuditIssue.priority_score.desc())).all()
    items = []
    for rank, i in enumerate(issues, 1):
        bucket = ("Now (week 1-2)" if i.severity in ("critical", "high") and EFFORT.get(i.owner or "", 2) <= 1.5
                  else "Next (week 3-6)" if i.severity in ("critical", "high", "medium") else "Later")
        items.append({"rank": rank, "issue_id": str(i.id), "code": i.rule, "title": i.title, "severity": i.severity,
                      "priority_score": float(i.priority_score), "owner": i.owner, "affected": i.affected_count,
                      "bucket": bucket, "recommendation": i.evidence.get("ai_recommendation") or i.recommendation})
    db.execute(delete(m.SeoPlan).where(m.SeoPlan.audit_run_id == run_id, m.SeoPlan.tasks_created.is_(False)))
    plan = m.SeoPlan(organisation_id=org_id, project_id=site.project_id, audit_run_id=run_id, items=items)
    db.add(plan)
    db.flush()
    return plan


def plan_to_tasks(db, org_id, plan: m.SeoPlan, user_id=None, limit: int | None = None) -> int:
    pri = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
    n = 0
    for it in plan.items[:limit] if limit else plan.items:
        db.add(m.Task(organisation_id=org_id, project_id=plan.project_id, title=f"[{it['code']}] {it['title']}",
                      description=(it.get("recommendation") or "") + f"\n\nAffected: {it['affected']} · Owner: {it['owner']} · {it['bucket']}",
                      priority=pri.get(it["severity"], "medium"), source=f"seo_plan:{plan.id}", position=it["rank"],
                      labels=[it["owner"], it["bucket"]], created_by=user_id))
        n += 1
    plan.tasks_created = True
    return n
