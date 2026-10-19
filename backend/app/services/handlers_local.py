"""Background job handlers for the local modules.

Job kinds:
  local_grid_scan       live geo-grid rank scan via Google Maps Places Text Search (location bias per point)
  local_gbp_sync        pull locations, reviews and posts from Google Business Profile
  local_gbp_publish     publish an APPROVED review reply or GBP post to Google Business Profile
  local_automation_run  GMB automation tick: render the template for each location → posts in review state
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from .. import models as m
from .. import models_local as ml
from . import entitlements as ent
from . import integrations as ih
from . import local_svc as S
from .jobs import FAIL_HOOKS, JobCtx, PermanentError, handler  # noqa: F401


@handler("local_grid_scan")
def h_grid_scan(ctx: JobCtx):
    """One short transaction per grid point, so progress survives rate-limit requeues and failures."""
    sid = uuid.UUID(ctx.payload["scan_id"])
    with ctx.session() as db:
        scan = db.get(m.GridScan, sid)
        if not scan or not db.scalar(select(ml.LocalGridScanExt).where(ml.LocalGridScanExt.scan_id == sid)):
            raise PermanentError("grid scan not found")
        scan.status = "RUNNING"
        n = scan.grid_size ** 2
    for idx in range(n):
        try:
            with ctx.session() as db:
                scan = db.get(m.GridScan, sid)
                x = db.scalar(select(ml.LocalGridScanExt).where(ml.LocalGridScanExt.scan_id == sid))
                S.live_point(db, ctx.org_id, scan, x, idx)
        except ih.RateLimited:
            raise  # re-queued by the job runner; checked points are kept
        except ih.NotConnected as e:
            _fail_scan(ctx.org_id, sid, f"NOT CONNECTED: {e}")
            raise
        except ih.IntegrationError as e:
            _fail_scan(ctx.org_id, sid, str(e))
            raise PermanentError(str(e))
        ctx.progress(int(100 * (idx + 1) / n))
    with ctx.session() as db:
        scan = db.get(m.GridScan, sid)
        x = db.scalar(select(ml.LocalGridScanExt).where(ml.LocalGridScanExt.scan_id == sid))
        met = S.recompute_scan(db, scan, x)
        ih.mark_synced(db, ctx.org_id, "google_maps")
        return {"scan_id": str(sid), "metrics": met}


def _fail_scan(org_id, sid, err: str):
    from ..db import tenant_session
    with tenant_session(org_id) as db:
        scan = db.get(m.GridScan, sid)
        x = db.scalar(select(ml.LocalGridScanExt).where(ml.LocalGridScanExt.scan_id == sid))
        if not scan or not x:
            return
        observed = sum(1 for p in S.scan_points(db, sid) if p.position is not None)
        # refund credits for points that were never checked
        unused = max(0, min(x.credits or 0, (scan.grid_size ** 2) - observed))
        if unused:
            ent.refund(db, org_id, "local_rank_credits", unused)
            x.credits = (x.credits or 0) - unused
        scan.status = "PARTIAL" if observed else "FAILED"
        x.error = err[:500]
        if observed:
            S.recompute_scan(db, scan, x)
            scan.status = "PARTIAL"


@handler("local_gbp_sync")
def h_gbp_sync(ctx: JobCtx):
    with ctx.session() as db:
        pid = uuid.UUID(ctx.payload["project_id"])
        if not db.get(m.Project, pid):
            raise PermanentError("project not found")
        try:
            return S.gbp_sync(db, ctx.org_id, pid, ctx.payload.get("resources") or None, ctx.progress)
        except (ih.NotConnected, ih.RateLimited):
            raise
        except ih.IntegrationError as e:
            ih.mark_synced(db, ctx.org_id, "gbp", str(e))
            raise PermanentError(str(e))


@handler("local_gbp_publish")
def h_gbp_publish(ctx: JobCtx):
    from . import local_google as G
    kind, eid = ctx.payload["kind"], uuid.UUID(ctx.payload["id"])
    with ctx.session() as db:
        if kind == "reply":
            rv = db.get(m.GbpReview, eid)
            x = db.scalar(select(ml.LocalReviewExt).where(ml.LocalReviewExt.review_id == eid))
            if not rv or not x or x.reply_status != "publishing":
                raise PermanentError("reply is not awaiting publication")
            try:
                G.gbp_reply(db, ctx.org_id, x.external_id, x.reply_draft)
            except ih.RateLimited:
                raise
            except ih.IntegrationError as e:
                x.reply_status, x.publish_error = "approved", f"{'NOT CONNECTED — publish manually. ' if isinstance(e, ih.NotConnected) else ''}{e}"[:500]
                return {"published": False, "error": x.publish_error}
            rv.reply, x.replied_at, x.reply_status, x.publish_error = x.reply_draft, S.utcnow(), "published", None
            return {"published": True}
        p = db.get(m.GbpPost, eid)
        x = db.scalar(select(ml.LocalPostExt).where(ml.LocalPostExt.post_id == eid))
        if not p or not x or p.status != "publishing":
            raise PermanentError("post is not awaiting publication")
        lst = db.scalar(select(ml.LocalListing).where(ml.LocalListing.location_id == p.location_id))
        try:
            res = G.gbp_create_post(db, ctx.org_id, lst.gbp_resource, {
                "post_type": p.post_type, "body": p.body, "title": x.title, "cta_type": x.cta_type, "cta_url": x.cta_url,
                "start_at": x.start_at, "end_at": x.end_at, "offer_code": x.offer_code, "image_ref": x.image_ref})
        except ih.RateLimited:
            raise
        except ih.IntegrationError as e:
            p.status, x.publish_error = "approved", f"{'NOT CONNECTED — publish manually. ' if isinstance(e, ih.NotConnected) else ''}{e}"[:500]
            return {"published": False, "error": x.publish_error}
        p.status, p.posted_at, x.external_name, x.publish_error = "published", S.utcnow(), res.get("name"), None
        return {"published": True, "name": res.get("name")}


@handler("local_automation_run")
def h_automation(ctx: JobCtx):
    with ctx.session() as db:
        a = db.get(ml.LocalAutomation, uuid.UUID(ctx.payload["automation_id"]))
        if not a:
            raise PermanentError("automation not found")
        return S.run_automation(db, a)
