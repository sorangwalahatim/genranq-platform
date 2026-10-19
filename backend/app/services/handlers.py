"""Background job handlers other than the audit (which lives in audit_engine)."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import func, select

from .. import models as m
from . import entitlements as ent
from . import integrations as ih
from . import reports, sources
from .jobs import FAIL_HOOKS, JobCtx, PermanentError, handler


def utcnow():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ rank tracker (F-011)
@handler("rank_check")
def rank_check(ctx: JobCtx) -> dict:
    with ctx.session() as db:
        q = select(m.Keyword).where(m.Keyword.project_id == ctx.payload["project_id"])
        if ctx.payload.get("keyword_ids"):
            q = q.where(m.Keyword.id.in_(ctx.payload["keyword_ids"]))
        kws = db.scalars(q).all()
        if not kws:
            return {"checked": 0}
        sites = {s.id: s for s in db.scalars(select(m.Site).where(m.Site.project_id == ctx.payload["project_id"]))}
        conn = ih.connected_map(db)
        now, written, source = utcnow(), 0, None
        gsc_sites = [s for s in sites.values() if s.gsc_property]
        if conn.get("gsc") == "CONNECTED" and gsc_sites:
            source = "gsc"
            for s in gsc_sites:
                rows = sources.gsc_search_analytics(db, ctx.org_id, s.gsc_property)
                best: dict[str, dict] = {}
                for r in rows:
                    q_ = r["query"].lower()
                    if q_ not in best or r["impressions"] > best[q_]["impressions"]:
                        best[q_] = r
                for k in kws:
                    if k.site_id not in (None, s.id):
                        continue
                    r = best.get(k.phrase.lower())
                    db.add(m.RankHistory(organisation_id=ctx.org_id, keyword_id=k.id, checked_at=now, source="gsc",
                                         position=round(r["position"]) if r else None, url=r["page"] if r else None))
                    written += 1
            ih.mark_synced(db, ctx.org_id, "gsc")
        elif conn.get("semrush") == "CONNECTED" and sites:
            source = "semrush"
            for s in sites.values():
                dom = urlparse(s.domain if "://" in s.domain else "https://" + s.domain).netloc
                mine = [k for k in kws if k.site_id in (None, s.id)]
                res = sources.semrush_positions(db, ctx.org_id, dom, [k.phrase for k in mine], database=(mine[0].country if mine else "us"))
                for k in mine:
                    r = res.get(k.phrase, {})
                    db.add(m.RankHistory(organisation_id=ctx.org_id, keyword_id=k.id, checked_at=now, source="semrush",
                                         position=r.get("position"), url=r.get("url")))
                    written += 1
            ih.mark_synced(db, ctx.org_id, "semrush")
        else:
            raise ih.NotConnected("No rank data source connected — connect Google Search Console or Semrush, "
                                  "or add positions manually / import CSV")
    return {"checked": written, "source": source}


# ------------------------------------------------------------------ PageSpeed (F-046)
@handler("pagespeed")
def pagespeed(ctx: JobCtx) -> dict:
    with ctx.session() as db:
        site = db.get(m.Site, ctx.payload["site_id"])
        urls = ctx.payload.get("urls") or [site.domain if "://" in site.domain else "https://" + site.domain.strip("/") + "/"]
        out = []
        for u in urls[:10]:
            for dev in ("mobile", "desktop"):
                r = sources.pagespeed(db, ctx.org_id, u, dev)
                db.add(m.PagespeedResult(organisation_id=ctx.org_id, site_id=site.id, url=u, device=dev, lcp=r["lcp"],
                                         cls=r["cls"], inp=r["inp"], score=r["score"], audits=r["audits"]))
                out.append({"url": u, "device": dev, "score": r["score"]})
        ih.mark_synced(db, ctx.org_id, "pagespeed")
    return {"results": out}


# ------------------------------------------------------------------ reports (F-010 / module 37)
def _fail_report(job, err):
    from ..db import system_session
    with system_session() as db:
        r = db.get(m.Report, job.payload.get("report_id"))
        if r:
            r.status = "FAILED"


FAIL_HOOKS["report"] = _fail_report


@handler("report")
def report(ctx: JobCtx) -> dict:
    with ctx.session() as db:
        rep = db.get(m.Report, ctx.payload["report_id"])
        if not rep:
            raise PermanentError("report not found")
        rep.status = "RUNNING"
        ar = db.get(m.AuditRun, rep.audit_run_id)
        if not ar or ar.status != "COMPLETED":
            raise PermanentError("Audit run is not completed")
        site = db.get(m.Site, ar.site_id)
        content = reports.build_pdf(db, ar.id, rep.white_label) if rep.format == "pdf" else reports.build_xlsx(db, ar.id, rep.white_label)
        ctype = "application/pdf" if rep.format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        used = db.scalar(select(func.coalesce(func.sum(m.StoredFile.size_bytes), 0))) or 0
        ent.check_count_limit(db, ctx.org_id, "storage_bytes", int(used), len(content))
        name = f"seo-audit-{urlparse('https://' + site.domain.split('://')[-1]).netloc or site.domain}-{ar.finished_at:%Y%m%d}.{rep.format}"
        f = reports.store(db, ctx.org_id, name, content, ctype)
        rep.stored_file_id, rep.status = f.id, "COMPLETED"
        db.add(m.Notification(organisation_id=ctx.org_id, kind="report_ready", title=f"Report ready: {name}", link="/reports"))
    return {"file": str(f.id), "bytes": len(content)}


# ------------------------------------------------------------------ scheduled audit (module 34 SEO Automation)
@handler("scheduled_audit")
def scheduled_audit(ctx: JobCtx) -> dict:
    from .jobs import enqueue
    with ctx.session() as db:
        if not ent.feature(db, ctx.org_id, "scheduled_audits").get("enabled"):
            raise PermanentError("Plan does not include automated audits")
        site = db.get(m.Site, ctx.payload["site_id"])
        if not site:
            raise PermanentError("site deleted")
        pages = int(ctx.payload.get("max_pages", 250))
        granted = ent.consume(db, ctx.org_id, "audit_pages", pages, allow_partial=True)
        ar = m.AuditRun(organisation_id=ctx.org_id, site_id=site.id, max_pages=granted, triggered_by="schedule",
                        summary={"reserved_pages": granted})
        db.add(ar)
        db.flush()
        enqueue(db, ctx.org_id, "audit", {"audit_run_id": str(ar.id)})
    return {"audit_run_id": str(ar.id)}


@handler("scheduled_rank_check")
def scheduled_rank_check(ctx: JobCtx) -> dict:
    return rank_check(ctx)


# ------------------------------------------------------------------ integration health (module 49)
@handler("integration_health")
def integration_health(ctx: JobCtx) -> dict:
    with ctx.session() as db:
        c = ih.run_health_check(db, ctx.org_id, ctx.payload["provider"])
        return {"status": c.status, "error": c.last_error}
