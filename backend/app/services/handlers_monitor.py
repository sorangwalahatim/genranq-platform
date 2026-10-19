"""Job handlers for monitoring, source sync, fix verification, gap analysis, activities and skills."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import models as m
from .. import models_monitor as mm
from . import ingest
from . import integrations as ih
from .jobs import FAIL_HOOKS, JobCtx, PermanentError, enqueue, handler


def utcnow():
    return datetime.now(timezone.utc)


def _project(db, pid) -> m.Project:
    p = db.get(m.Project, pid)
    if not p:
        raise PermanentError("project not found")
    return p


SYNC = {"gsc": ingest.sync_gsc, "ga4": ingest.sync_ga4, "ahrefs": ingest.sync_ahrefs, "semrush": ingest.sync_semrush}


@handler("source_sync")
def source_sync(ctx: JobCtx) -> dict:
    try:
        with ctx.session() as db:
            src = db.get(mm.ProjectSource, ctx.payload["source_id"])
            if not src:
                raise PermanentError("source not found")
            fn = SYNC.get(src.provider)
            if not fn or src.mode != "api":
                raise PermanentError(f"{src.provider} is import-only — upload a file")
            res = fn(db, ctx.org_id, src)
            src.last_status, src.last_error, src.last_sync_at = "ok", None, utcnow()
            src.rows_last_sync = int(res.get("rows") or 0)
            if ctx.payload.get("then_monitor"):
                enqueue(db, ctx.org_id, "monitor_project", {"project_id": str(src.project_id)})
            return res
    except PermanentError:
        raise
    except Exception as e:  # record why the sync failed on the source (shown as NOT CONNECTED / error in the UI)
        with ctx.session() as db:
            src = db.get(mm.ProjectSource, ctx.payload["source_id"])
            if src:
                src.last_status = "not_connected" if isinstance(e, ih.NotConnected) else "error"
                src.last_error, src.last_sync_at = f"{e}"[:500], utcnow()
        raise


@handler("monitor_project")
def monitor_project(ctx: JobCtx) -> dict:
    from .monitor import run_monitor
    with ctx.session() as db:
        p = _project(db, ctx.payload["project_id"])
        cfg = db.get(mm.MonitorConfig, p.id)
        if cfg is not None and not cfg.enabled and ctx.payload.get("triggered_by") == "schedule":
            return {"skipped": "monitoring disabled for this project"}
        return run_monitor(db, p)


@handler("monitor_all")
def monitor_all(ctx: JobCtx) -> dict:
    """Daily per organisation: sync every API source, then monitor every active project."""
    n = 0
    with ctx.session() as db:
        projects = db.scalars(select(m.Project).where(m.Project.status == "active", m.Project.is_template.is_(False))).all()
        for p in projects:
            cfg = db.get(mm.MonitorConfig, p.id)
            if cfg is not None and not cfg.enabled:
                continue
            srcs = db.scalars(select(mm.ProjectSource).where(mm.ProjectSource.project_id == p.id, mm.ProjectSource.enabled,
                                                            mm.ProjectSource.mode == "api")).all()
            day = utcnow().date().isoformat()
            for s in srcs:
                enqueue(db, ctx.org_id, "source_sync", {"source_id": str(s.id)}, idempotency_key=f"sync:{s.id}:{day}", max_attempts=2)
            # monitoring runs after syncs have had time to finish
            enqueue(db, ctx.org_id, "monitor_project", {"project_id": str(p.id), "triggered_by": "schedule"},
                    run_at=utcnow() + timedelta(minutes=15 if srcs else 0), idempotency_key=f"mon:{p.id}:{day}")
            n += 1
    return {"projects": n}


@handler("verify_issue")
def verify_issue(ctx: JobCtx) -> dict:
    from .monitor import get_config, verify_url_issue
    with ctx.session() as db:
        issue = db.get(mm.MonitorIssue, ctx.payload["issue_id"])
        if not issue:
            raise PermanentError("issue not found")
        if issue.status != "verifying":
            return {"skipped": f"issue is {issue.status}"}
        p = _project(db, issue.project_id)
        return {"result": verify_url_issue(db, p, issue, get_config(db, p))}


def _fail_gap(job, err):
    from ..db import tenant_session
    with tenant_session(job.organisation_id) as db:
        g = db.get(mm.GapReport, job.payload.get("gap_report_id"))
        if g:
            g.status, g.result = "FAILED", {"error": str(err)[:500]}


FAIL_HOOKS["gap_analysis"] = _fail_gap


@handler("gap_analysis")
def gap_analysis(ctx: JobCtx) -> dict:
    from . import gaps
    with ctx.session() as db:
        g = db.get(mm.GapReport, ctx.payload["gap_report_id"])
        if not g:
            raise PermanentError("gap report not found")
        p = _project(db, g.project_id)
        g.status = "RUNNING"
        if g.kind == "keyword":
            res = gaps.keyword_gap(db, p)
        elif g.kind == "content":
            res = gaps.content_gap(db, p)
        elif g.kind == "competitor":
            res = gaps.competitor_gap(db, p)
        elif g.kind == "backlink":
            res = gaps.backlink_gap(db, p)
        elif g.kind == "structure":
            res = gaps.structure_gap(g.target_url, g.params.get("competitor_urls") or [])
        else:
            res = gaps.diagnose_page(db, p, g.target_url, g.params.get("target_keyword"), g.params.get("competitor_urls") or [])
        import json
        g.result, g.status = json.loads(json.dumps(res, default=str)), "COMPLETED"
        # attach the report to the content tasks of the issue that triggered it
        issue = db.scalar(select(mm.MonitorIssue).where(mm.MonitorIssue.project_id == p.id, mm.MonitorIssue.url == g.target_url,
                                                        mm.MonitorIssue.kind == "blog_underperforming").order_by(mm.MonitorIssue.last_seen.desc()))
        if issue:
            g.issue_id = issue.id
            plan = res.get("plan") or res.get("recommendations") or []
            for tid in issue.task_ids or []:
                t = db.get(m.Task, tid)
                if t:
                    t.output = {**(t.output or {}), "gap_report_id": str(g.id)}
                    if plan and "Gap analysis plan:" not in (t.description or ""):
                        t.description = (t.description or "") + "\n\nGap analysis plan:\n" + "\n".join(f"• {x}" for x in plan[:12])
        return {"gap_report_id": str(g.id), "kind": g.kind}


@handler("activities_tick")
def activities_tick(ctx: JobCtx) -> dict:
    from . import workstreams
    ran = []
    with ctx.session() as db:
        for a in workstreams.due_activities(db):
            enqueue(db, ctx.org_id, "run_activity", {"activity_id": str(a.id)}, idempotency_key=f"act:{a.id}:{a.next_due}")
            ran.append(str(a.id))
    return {"queued": len(ran)}


@handler("run_activity")
def run_activity(ctx: JobCtx) -> dict:
    from . import workstreams
    with ctx.session() as db:
        a = db.get(mm.ProjectActivity, ctx.payload["activity_id"])
        if not a:
            raise PermanentError("activity not found")
        p = _project(db, a.project_id)
        r = workstreams.run_activity(db, p, a, ctx.payload.get("triggered_by") or "schedule", ctx.payload.get("user_id"))
        return {"run_id": str(r.id), "status": r.status, "task_id": str(r.task_id) if r.task_id else None}


def _fail_skill(job, err):
    from ..db import tenant_session
    with tenant_session(job.organisation_id) as db:
        r = db.get(mm.SkillRun, job.payload.get("run_id"))
        if r:
            r.status, r.summary, r.finished_at = "FAILED", str(err)[:500], utcnow()


FAIL_HOOKS["skill_run"] = _fail_skill


@handler("skill_run")
def skill_run(ctx: JobCtx) -> dict:
    from . import skills
    with ctx.session() as db:
        run = db.get(mm.SkillRun, ctx.payload["run_id"])
        if not run:
            raise PermanentError("run not found")
        p = _project(db, run.project_id)
        try:
            skills.run_skill(db, p, run.skill_key, ctx.payload.get("inputs") or {}, use_ai=ctx.payload.get("use_ai", True), run=run)
        except KeyError:
            raise PermanentError("skill not found")
        return {"run_id": str(run.id), "status": run.status}


def ensure_org_schedules(db, org_id):
    """Every organisation gets a daily monitoring run and a daily activity scheduler."""
    for kind in ("monitor_all", "activities_tick"):
        if not db.scalar(select(m.JobSchedule).where(m.JobSchedule.kind == kind)):
            first = (utcnow() + timedelta(days=1)).replace(hour=1 if kind == "monitor_all" else 3, minute=0, second=0, microsecond=0)
            db.add(m.JobSchedule(organisation_id=org_id, kind=kind, payload={}, frequency="daily", next_run_at=first))


@handler("email_send")
def email_send(ctx: JobCtx) -> dict:
    from . import mailer
    with ctx.session() as db:
        row = db.get(mm.EmailOutbox, ctx.payload["outbox_id"])
        if not row or row.status == "sent":
            return {"skipped": True}
        try:
            st = mailer.deliver(row)
        except Exception as e:  # noqa: BLE001 — record and let the queue retry
            row.status, row.error = "failed", f"{type(e).__name__}: {e}"[:500]
            if ctx.attempt < 3:
                raise
            return {"status": "failed"}
        return {"status": st}
