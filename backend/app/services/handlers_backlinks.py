"""Background job handlers for the backlinks modules.

  backlinks_verify          verify a list of backlinks (one transaction per link)
  backlinks_verify_due      daily per organisation: queue verification for links whose re-check is due
  backlinks_provider_sync   Ahrefs / Semrush API ingest for one project source
  backlinks_kd_refresh      keyword difficulty from Ahrefs / Semrush
  backlinks_relevink        ReleVink placement finder run
  backlinks_page_compare    page-to-page competitor comparison
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import models as m
from .. import models_backlinks as mb
from .. import models_monitor as mm
from . import integrations as ih
from .jobs import FAIL_HOOKS, JobCtx, PermanentError, enqueue, handler  # noqa: F401


def utcnow():
    return datetime.now(timezone.utc)


def ensure_verify_schedule(db, org_id):
    if not db.scalar(select(m.JobSchedule).where(m.JobSchedule.kind == "backlinks_verify_due")):
        first = (utcnow() + timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)
        db.add(m.JobSchedule(organisation_id=org_id, kind="backlinks_verify_due", payload={}, frequency="daily", next_run_at=first))


@handler("backlinks_verify")
def verify(ctx: JobCtx) -> dict:
    from .backlinks_verify import Verifier, record
    ids = ctx.payload.get("backlink_ids") or []
    v = Verifier()
    results = []
    for i, bid in enumerate(ids):
        with ctx.session() as db:
            bl = db.get(m.Backlink, bid)
            if not bl:
                continue
            results.append(record(db, bl, v.inspect(bl)))
        if len(ids) > 5:
            ctx.progress(int(100 * (i + 1) / len(ids)))
    c = {}
    for r in results:
        c[r["check_status"]] = c.get(r["check_status"], 0) + 1
    return {"checked": len(results), "by_check_status": c, "newly_removed": sum(1 for r in results if r["newly_removed"]),
            "results": results[:100]}


@handler("backlinks_verify_due")
def verify_due(ctx: JobCtx) -> dict:
    with ctx.session() as db:
        due = db.execute(select(mb.BacklinkMeta.backlink_id).where(mb.BacklinkMeta.verify_frequency != "off",
                                                                   mb.BacklinkMeta.next_check_at <= utcnow())
                         .order_by(mb.BacklinkMeta.next_check_at).limit(2000)).scalars().all()
        day = utcnow().date().isoformat()
        for i in range(0, len(due), 25):
            chunk = [str(x) for x in due[i:i + 25]]
            enqueue(db, ctx.org_id, "backlinks_verify", {"backlink_ids": chunk, "triggered_by": "schedule"},
                    idempotency_key=f"blv:{ctx.org_id}:{day}:{chunk[0]}", max_attempts=2)
    return {"due": len(due)}


@handler("backlinks_provider_sync")
def provider_sync(ctx: JobCtx) -> dict:
    from .backlinks_providers import SYNC
    try:
        with ctx.session() as db:
            src = db.get(mm.ProjectSource, ctx.payload["source_id"])
            if not src or src.provider not in SYNC:
                raise PermanentError("source not found")
            res = SYNC[src.provider](db, ctx.org_id, src)
            src.last_status, src.last_error, src.last_sync_at = "ok", None, utcnow()
            src.rows_last_sync = int(res.get("rows") or 0)
            ih.mark_synced(db, ctx.org_id, src.provider)
            return json.loads(json.dumps(res, default=str))
    except PermanentError:
        raise
    except Exception as e:
        with ctx.session() as db:
            src = db.get(mm.ProjectSource, ctx.payload["source_id"])
            if src:
                src.last_status = "not_connected" if isinstance(e, ih.NotConnected) else "error"
                src.last_error, src.last_sync_at = f"{e}"[:500], utcnow()
                if not isinstance(e, ih.NotConnected):
                    ih.mark_synced(db, ctx.org_id, src.provider, error=str(e))
        raise


@handler("backlinks_kd_refresh")
def kd_refresh(ctx: JobCtx) -> dict:
    from .backlinks_providers import refresh_difficulty
    with ctx.session() as db:
        p = db.get(m.Project, ctx.payload["project_id"])
        if not p:
            raise PermanentError("project not found")
        return refresh_difficulty(db, ctx.org_id, p, ctx.payload.get("phrases") or [])


def _fail(model, key):
    def hook(job, err):
        from ..db import tenant_session
        with tenant_session(job.organisation_id) as db:
            r = db.get(model, job.payload.get(key))
            if r:
                r.status, r.result, r.finished_at = "FAILED", {"error": str(err)[:500]}, utcnow()
    return hook


FAIL_HOOKS["backlinks_relevink"] = _fail(mb.RelevinkRun, "run_id")
FAIL_HOOKS["backlinks_page_compare"] = _fail(mb.PageComparison, "comparison_id")


@handler("backlinks_relevink")
def relevink(ctx: JobCtx) -> dict:
    from . import backlinks_relevink as rv
    with ctx.session() as db:
        r = db.get(mb.RelevinkRun, ctx.payload["run_id"])
        if not r:
            raise PermanentError("run not found")
        p = db.get(m.Project, r.project_id)
        r.status = "RUNNING"
        res = rv.run(db, p, r)
        r.result = json.loads(json.dumps(res, default=str))
        r.status, r.finished_at = ("FAILED" if res.get("error") else "COMPLETED"), utcnow()
        return {"run_id": str(r.id), "status": r.status}


@handler("backlinks_page_compare")
def page_compare(ctx: JobCtx) -> dict:
    from . import backlinks_pagecompare as pc
    with ctx.session() as db:
        c = db.get(mb.PageComparison, ctx.payload["comparison_id"])
        if not c:
            raise PermanentError("comparison not found")
        c.status = "RUNNING"
        res = pc.compare(c.our_url, c.competitor_urls or [], c.keyword)
        c.result = json.loads(json.dumps(res, default=str))
        c.status, c.finished_at = ("COMPLETED" if res.get("available") else "FAILED"), utcnow()
        return {"comparison_id": str(c.id), "status": c.status}
