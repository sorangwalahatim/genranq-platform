"""Background job handlers for the growth modules."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from .. import models as m
from .. import models_growth as g
from . import growth_crm, growth_market, growth_tools
from .growth_common import send_email, utcnow
from .jobs import JobCtx, PermanentError, handler  # noqa: F401

MONITOR_INTERVAL = timedelta(days=1)


def ensure_monitor_schedule(db, org_id):
    """One daily sweep per organisation that has public/registered backlink monitors."""
    s = db.scalar(select(m.JobSchedule).where(m.JobSchedule.kind == "growth_monitor_sweep"))
    if not s:
        db.add(m.JobSchedule(organisation_id=org_id, kind="growth_monitor_sweep", payload={}, frequency="daily",
                             next_run_at=utcnow() + timedelta(hours=1)))


@handler("growth_order_verify")
def order_verify(ctx: JobCtx):
    oid = ctx.payload.get("order_id")
    with ctx.session() as db:
        po = db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.order_id == oid))
        src = po or db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == oid))
        if not src:
            raise PermanentError("Order not visible to this organisation")
        if not src.live_url:
            raise PermanentError("Order has no delivered URL")
        res = growth_market.verify_link(src.live_url, src.target_url, src.domain, require_dofollow=(src.link_type or "dofollow") == "dofollow"
                                        and src.kind != "service")
        growth_market.apply_verification(db, ctx.org_id, oid, res)
    return {"verified": res.get("verified"), "issues": res.get("issues")}


TOOL_FUNCS = {
    "broken-links": lambda inp, prog: growth_tools.broken_links(inp["url"], int(inp.get("max_pages", 25)), progress=prog),
    "link-analyzer": lambda inp, prog: growth_tools.link_analyzer(inp["url"]),
    "dofollow": lambda inp, prog: growth_tools.dofollow(inp["url"]),
    "domain-age": lambda inp, prog: growth_tools.domain_age(inp["domain"]),
}


@handler("growth_tool_run")
def tool_run(ctx: JobCtx):
    with ctx.session() as db:
        run = db.get(g.GrowthToolRun, ctx.payload["run_id"])
        if not run:
            raise PermanentError("Tool run not found")
        run.status = "RUNNING"
        tool, inp = run.tool, dict(run.input)
    try:
        result = TOOL_FUNCS[tool](inp, lambda a, b: ctx.progress(int(100 * a / max(1, b))))
        status, err = "COMPLETED", None
    except growth_tools.ToolError as e:
        result, status, err = {}, "FAILED", str(e)
    with ctx.session() as db:
        run = db.get(g.GrowthToolRun, ctx.payload["run_id"])
        run.status, run.result, run.error, run.finished_at = status, result, err, utcnow()
    return {"status": status}


def check_monitor(db, mon: g.GrowthPublicMonitor) -> dict:
    res = growth_tools.monitor_check(mon.source_url, mon.target_url)
    prev = mon.last_status
    mon.last_status, mon.last_rel, mon.last_checked_at = res["status"], res.get("rel"), utcnow()
    mon.next_check_at = utcnow() + MONITOR_INTERVAL
    mon.history = ([{"at": res["checked_at"], "status": res["status"], "http": res.get("http_status"), "rel": res.get("rel")}]
                   + list(mon.history or []))[:60]
    if prev not in ("pending", res["status"]):
        title = f"Backlink {res['status']}: {mon.source_url} → {mon.target_url}"
        db.add(m.Notification(organisation_id=mon.organisation_id, kind="backlink_monitor", title=title[:300],
                              body=f"Was {prev}. " + "; ".join(res.get("issues") or []), link="/tools/backlink-monitor"))
        mon.last_email_status = send_email(mon.email, "Your backlink status changed",
                                           f"{title}\nPrevious status: {prev}\nChecked: {res['checked_at']}")
    return res


@handler("growth_monitor_sweep")
def monitor_sweep(ctx: JobCtx):
    n = 0
    with ctx.session() as db:
        due = db.scalars(select(g.GrowthPublicMonitor).where(g.GrowthPublicMonitor.status == "active",
                                                            g.GrowthPublicMonitor.next_check_at <= utcnow())
                         .order_by(g.GrowthPublicMonitor.next_check_at).limit(200)).all()
        for mon in due:
            check_monitor(db, mon)
            n += 1
    return {"checked": n}


@handler("growth_hubspot_sync")
def hubspot_sync(ctx: JobCtx):
    with ctx.session() as db:
        log = growth_crm.sync_hubspot(db, ctx.org_id, ctx.payload.get("user_id"))
        return {"status": log.status, "counts": log.counts}


@handler("growth_calendly_sync")
def calendly_sync(ctx: JobCtx):
    with ctx.session() as db:
        log = growth_crm.sync_calendly(db, ctx.org_id, ctx.payload.get("user_id"))
        return {"status": log.status, "counts": log.counts}
