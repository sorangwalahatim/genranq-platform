"""Project monitoring API: sources & imports, teams, routing config, monitor runs, issues,
project / page / team / executive dashboards, change log, content watch and gap reports."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .. import models as m
from .. import models_monitor as mm
from ..deps import Ctx, deny_client, get_db, log, project_guard, require, visible_project_ids
from ..services import dashboards as dash
from ..services import ingest
from ..services import monitor as mon
from ..services.handlers_monitor import ensure_org_schedules
from ..services.jobs import enqueue
from ..util import to_dict

router = APIRouter(tags=["monitor"])
PROVIDERS = {"gsc": "api", "ga4": "api", "ahrefs": "api", "semrush": "api", "screaming_frog": "import"}
MAX_UPLOAD = 50 * 1024 * 1024


# ------------------------------------------------------------------ sources
class SourceIn(BaseModel):
    property: str | None = Field(None, max_length=500)
    mode: str = "api"
    enabled: bool = True
    config: dict = {}


@router.get("/projects/{pid}/sources")
def list_sources(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    from ..services import integrations as ih
    conn = ih.connected_map(db)
    have = {s.provider: s for s in db.scalars(select(mm.ProjectSource).where(mm.ProjectSource.project_id == pid))}
    out = []
    for prov, default_mode in PROVIDERS.items():
        s = have.get(prov)
        out.append({"provider": prov, "configured": bool(s), "property": s.property if s else None, "mode": s.mode if s else default_mode,
                    "enabled": s.enabled if s else False, "last_sync_at": s.last_sync_at if s else None, "last_status": s.last_status if s else None,
                    "last_error": s.last_error if s else None, "rows_last_sync": s.rows_last_sync if s else 0,
                    "org_connection": "IMPORT" if prov == "screaming_frog" else (conn.get(prov) or "NOT CONNECTED")})
    return out


@router.put("/projects/{pid}/sources/{provider}")
def put_source(pid: uuid.UUID, provider: str, body: SourceIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    project_guard(db, ctx, pid)
    if provider not in PROVIDERS:
        raise HTTPException(400, f"provider must be one of {list(PROVIDERS)}")
    if body.mode not in ("api", "import") or (provider == "screaming_frog" and body.mode == "api"):
        raise HTTPException(400, "mode must be api or import (Screaming Frog is import-only)")
    s = db.scalar(select(mm.ProjectSource).where(mm.ProjectSource.project_id == pid, mm.ProjectSource.provider == provider))
    if not s:
        s = mm.ProjectSource(organisation_id=ctx.org_id, project_id=pid, provider=provider)
        db.add(s)
    s.property, s.mode, s.enabled, s.config = body.property, body.mode, body.enabled, body.config
    ensure_org_schedules(db, ctx.org_id)
    log(db, ctx, "monitor.source_set", f"project:{pid}", {"provider": provider, "mode": body.mode})
    db.flush()
    return to_dict(s)


@router.post("/projects/{pid}/sources/{provider}/sync")
def sync_source(pid: uuid.UUID, provider: str, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, pid)
    s = db.scalar(select(mm.ProjectSource).where(mm.ProjectSource.project_id == pid, mm.ProjectSource.provider == provider))
    if not s or s.mode != "api":
        raise HTTPException(400, "Configure this source in API mode first (or upload a file)")
    j = enqueue(db, ctx.org_id, "source_sync", {"source_id": str(s.id), "then_monitor": True}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


@router.post("/projects/{pid}/import/{kind}")
async def import_file(pid: uuid.UUID, kind: str, file: UploadFile = File(...), domain: str | None = Form(None), is_own: bool = Form(False),
                      source: str = Form("import"), target: str = Form("own"), site_id: uuid.UUID | None = Form(None),
                      date_covered: date | None = Form(None), run_monitor: bool = Form(True),
                      db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    """kind: gsc | ga4 | keywords | refdomains | screaming_frog (CSV / XLSX exports)."""
    project_guard(db, ctx, pid)
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (50 MB max)")
    try:
        if kind == "gsc":
            res = ingest.import_gsc_csv(db, ctx.org_id, pid, content, default_date=date_covered)
        elif kind == "ga4":
            res = ingest.import_ga4_csv(db, ctx.org_id, pid, content)
        elif kind == "keywords":
            if not domain:
                raise HTTPException(400, "domain is required for a keyword export")
            res = ingest.import_keywords_csv(db, ctx.org_id, pid, content, domain, is_own, source)
        elif kind == "refdomains":
            if not domain:
                raise HTTPException(400, "domain is required for a referring-domains export")
            res = ingest.import_refdomains_csv(db, ctx.org_id, pid, content, domain, source, target=target or "own")
        elif kind == "screaming_frog":
            site = db.get(m.Site, site_id) if site_id else db.scalar(select(m.Site).where(m.Site.project_id == pid).limit(1))
            if not site or site.project_id != pid:
                raise HTTPException(400, "Add a site to the project first")
            res = ingest.import_screaming_frog(db, ctx.org_id, pid, site.id, content)
        else:
            raise HTTPException(400, "kind must be gsc, ga4, keywords, refdomains or screaming_frog")
    except ingest.ImportError_ as e:
        raise HTTPException(400, str(e))
    prov = {"gsc": "gsc", "ga4": "ga4", "screaming_frog": "screaming_frog"}.get(kind) or (source if source in PROVIDERS else None)
    if prov:
        s = db.scalar(select(mm.ProjectSource).where(mm.ProjectSource.project_id == pid, mm.ProjectSource.provider == prov))
        if not s:
            s = mm.ProjectSource(organisation_id=ctx.org_id, project_id=pid, provider=prov, mode="import")
            db.add(s)
        s.last_sync_at, s.last_status, s.last_error, s.rows_last_sync = mon.utcnow(), "ok", None, int(res.get("rows") or res.get("pages") or 0)
    ensure_org_schedules(db, ctx.org_id)
    log(db, ctx, "monitor.import", f"project:{pid}", {"kind": kind, **{k: v for k, v in res.items() if k in ("rows", "pages")}})
    job = enqueue(db, ctx.org_id, "monitor_project", {"project_id": str(pid)}, created_by=ctx.user_id) if run_monitor else None
    return {**res, "monitor_job_id": str(job.id) if job else None}


# ------------------------------------------------------------------ team + routing
class MemberIn(BaseModel):
    user_id: uuid.UUID
    team: str
    is_lead: bool = False


@router.get("/projects/{pid}/members")
def list_members(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    rows = db.execute(select(mm.ProjectMember, m.User.name, m.User.email).join(m.User, m.User.id == mm.ProjectMember.user_id)
                      .where(mm.ProjectMember.project_id == pid)).all()
    return [{"id": str(pm.id), "user_id": str(pm.user_id), "team": pm.team, "is_lead": pm.is_lead, "name": n,
             "email": e if ctx.role != "client" else None} for pm, n, e in rows]


@router.put("/projects/{pid}/members")
def set_members(pid: uuid.UUID, body: list[MemberIn], db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    project_guard(db, ctx, pid)
    for x in body:
        if x.team not in mm.TEAMS:
            raise HTTPException(400, f"team must be one of {mm.TEAMS}")
        u = db.get(m.User, x.user_id)
        if not u or not u.is_active:
            raise HTTPException(400, f"Unknown or inactive user {x.user_id}")
        if u.role == "client":
            raise HTTPException(400, "Client users can't be on a delivery team")
    db.execute(delete(mm.ProjectMember).where(mm.ProjectMember.project_id == pid))
    for x in {(x.user_id, x.team): x for x in body}.values():
        db.add(mm.ProjectMember(organisation_id=ctx.org_id, project_id=pid, user_id=x.user_id, team=x.team, is_lead=x.is_lead))
    log(db, ctx, "monitor.team_set", f"project:{pid}", {"members": len(body)})
    return {"ok": True, "members": len(body)}


class ConfigIn(BaseModel):
    enabled: bool | None = None
    thresholds: dict | None = None
    severity_overrides: dict | None = None
    routing: dict | None = None
    channels: dict | None = None
    important_urls: list[str] | None = None


@router.get("/projects/{pid}/monitor/config")
def get_config(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    p = project_guard(db, ctx, pid)
    c = mon.get_config(db, p)
    ch = dict(c.channels or {})
    for k in ("slack_webhook", "teams_webhook"):  # webhook URLs are secrets: show only that one is set
        if ch.get(k):
            ch[k] = "•••• configured"
    return {"enabled": c.enabled, "thresholds": mon.thresholds(c), "defaults": mon.DEFAULTS, "severity_overrides": c.severity_overrides,
            "routing": {**mon.ROUTING, **(c.routing or {})}, "channels": ch, "important_urls": c.important_urls, "teams": mm.TEAMS,
            "categories": mm.CATEGORIES}


@router.put("/projects/{pid}/monitor/config")
def put_config(pid: uuid.UUID, body: ConfigIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    p = project_guard(db, ctx, pid)
    c = mon.get_config(db, p)
    d = body.model_dump(exclude_unset=True)
    if "thresholds" in d:
        bad = [k for k in d["thresholds"] if k not in mon.DEFAULTS]
        if bad:
            raise HTTPException(400, f"Unknown thresholds: {bad}")
        if any(not isinstance(v, (int, float)) or v < 0 for v in d["thresholds"].values()):
            raise HTTPException(400, "Thresholds must be non-negative numbers")
    if "severity_overrides" in d and any(v not in mon.SEVERITY_ORDER for v in d["severity_overrides"].values()):
        raise HTTPException(400, "Severities: critical, high, medium, low")
    if "routing" in d and any(k not in mm.CATEGORIES or v not in mm.TEAMS for k, v in d["routing"].items()):
        raise HTTPException(400, f"Routing maps {mm.CATEGORIES} → {mm.TEAMS}")
    if "channels" in d:
        ch = dict(c.channels or {})
        for k, v in d["channels"].items():
            if k not in ("slack_webhook", "teams_webhook"):
                raise HTTPException(400, "channels: slack_webhook, teams_webhook")
            if v == "•••• configured":
                continue
            if v and not mon.valid_hook(v):
                raise HTTPException(400, f"{k} must be a Slack (hooks.slack.com) or Microsoft Teams (webhook.office.com) incoming webhook URL")
            ch[k] = v or None
        d["channels"] = ch
    for k, v in d.items():
        setattr(c, k, v)
    ensure_org_schedules(db, ctx.org_id)
    log(db, ctx, "monitor.config_updated", f"project:{pid}", {"fields": list(d)})
    return {"ok": True}


# ------------------------------------------------------------------ runs + dashboards
@router.post("/projects/{pid}/monitor/run")
def run_now(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, pid)
    ensure_org_schedules(db, ctx.org_id)
    j = enqueue(db, ctx.org_id, "monitor_project", {"project_id": str(pid)}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


@router.get("/projects/{pid}/monitor/runs")
def runs(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    return [to_dict(r) for r in db.scalars(select(mm.MonitorRun).where(mm.MonitorRun.project_id == pid).order_by(mm.MonitorRun.started_at.desc()).limit(30))]


@router.get("/projects/{pid}/monitor/overview")
def overview(pid: uuid.UUID, days: int = Query(28, ge=7, le=180), db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return dash.overview(db, project_guard(db, ctx, pid), days)


@router.get("/projects/{pid}/monitor/queries")
def queries(pid: uuid.UUID, days: int = Query(28, ge=7, le=180), q: str | None = None, page: str | None = None,
            db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return dash.queries(db, project_guard(db, ctx, pid), days, q, page)


@router.get("/projects/{pid}/monitor/pages")
def pages(pid: uuid.UUID, days: int = Query(28, ge=7, le=180), db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return dash.pages(db, project_guard(db, ctx, pid), days)


@router.get("/projects/{pid}/monitor/page")
def page(pid: uuid.UUID, url: str, days: int = Query(28, ge=7, le=180), db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return dash.page_detail(db, project_guard(db, ctx, pid), url, days)


@router.get("/projects/{pid}/monitor/changes")
def changes(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    return [to_dict(c) for c in db.scalars(select(mm.SiteChange).where(mm.SiteChange.project_id == pid).order_by(mm.SiteChange.detected_at.desc()).limit(500))]


@router.get("/dashboards/team/{team}")
def team_dash(team: str, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    if team not in mm.TEAMS:
        raise HTTPException(400, f"team must be one of {mm.TEAMS}")
    return dash.team_dashboard(db, team, visible_project_ids(db, ctx))


@router.get("/dashboards/executive")
def exec_dash(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return dash.executive(db, visible_project_ids(db, ctx))


# ------------------------------------------------------------------ issues
@router.get("/issues")
def list_issues(project_id: uuid.UUID | None = None, status: str | None = None, category: str | None = None, severity: str | None = None,
                opportunities: bool | None = None, url: str | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(mm.MonitorIssue).where(mm.MonitorIssue.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        q = q.where(mm.MonitorIssue.project_id == project_id)
    if status == "active":
        q = q.where(mm.MonitorIssue.status.in_(dash.OPEN))
    elif status:
        q = q.where(mm.MonitorIssue.status == status)
    if category:
        q = q.where(mm.MonitorIssue.categories.contains([category]))
    if severity:
        q = q.where(mm.MonitorIssue.severity == severity)
    if opportunities is not None:
        q = q.where(mm.MonitorIssue.is_opportunity.is_(opportunities))
    if url:
        q = q.where(mm.MonitorIssue.url == url)
    items = db.scalars(q.order_by(mm.MonitorIssue.last_seen.desc()).limit(1000)).all()
    names = {p.id: p.name for p in db.scalars(select(m.Project))}
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items = sorted(items, key=lambda i: (i.status not in dash.OPEN, order[i.severity]))
    return [{**dash._issue(i), "project_name": names.get(i.project_id)} for i in items]


@router.get("/issues/{iid}")
def get_issue(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    i = db.get(mm.MonitorIssue, iid)
    if not i:
        raise HTTPException(404)
    project_guard(db, ctx, i.project_id)
    tasks = [to_dict(t) for t in db.scalars(select(m.Task).where(m.Task.id.in_(i.task_ids or [])))]
    gr = [{"id": str(g.id), "kind": g.kind, "status": g.status} for g in db.scalars(select(mm.GapReport).where(mm.GapReport.issue_id == i.id))]
    pr = db.get(m.Project, i.project_id)
    return {**dash._issue(i), "project_name": pr.name if pr else None, "tasks": tasks, "gap_reports": gr}


class IssuePatch(BaseModel):
    status: str


@router.patch("/issues/{iid}")
def patch_issue(iid: uuid.UUID, body: IssuePatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    i = db.get(mm.MonitorIssue, iid)
    if not i:
        raise HTTPException(404)
    project_guard(db, ctx, i.project_id)
    if body.status not in ("ignored", "open", "in_progress"):
        raise HTTPException(400, "You can set ignored / open / in_progress; resolution is verified automatically")
    i.history = i.history + [{"at": mon.utcnow().isoformat(), "event": f"set_{body.status}", "by": str(ctx.user_id)}]
    i.status = body.status
    if body.status == "ignored":
        mon._close_tasks(db, i)
    log(db, ctx, "monitor.issue_status", f"issue:{iid}", {"status": body.status})
    return {"ok": True}


@router.post("/issues/{iid}/verify")
def verify_now(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    i = db.get(mm.MonitorIssue, iid)
    if not i:
        raise HTTPException(404)
    project_guard(db, ctx, i.project_id)
    if (i.verify or {}).get("type") != "url_check" or not i.url:
        raise HTTPException(400, "This issue is verified from data on the next monitoring runs, not by re-fetching a URL")
    if i.status not in ("verifying", "open", "in_progress", "reopened"):
        raise HTTPException(400, f"Issue is {i.status}")
    i.status = "verifying"
    i.verify = {**i.verify, "since": mon.utcnow().isoformat()}
    j = enqueue(db, ctx.org_id, "verify_issue", {"issue_id": str(i.id)}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


# ------------------------------------------------------------------ content watch + gaps
class WatchIn(BaseModel):
    url: str = Field(..., max_length=2000)
    published_at: date
    target_keyword: str | None = Field(None, max_length=300)
    competitor_urls: list[str] = Field(default_factory=list, max_length=10)
    title: str | None = None


@router.get("/projects/{pid}/content-watch")
def list_watch(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    from ..services.workstreams import CHECKPOINTS
    return {"checkpoints": CHECKPOINTS, "items": [to_dict(c) for c in db.scalars(select(mm.ContentWatch).where(mm.ContentWatch.project_id == pid)
                                                                                    .order_by(mm.ContentWatch.published_at.desc()))]}


@router.post("/projects/{pid}/content-watch")
def add_watch(pid: uuid.UUID, body: WatchIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be absolute (https://…)")
    from ..services.crawler import normalise
    url = normalise(body.url)
    if db.scalar(select(mm.ContentWatch.id).where(mm.ContentWatch.project_id == pid, mm.ContentWatch.url == url)):
        raise HTTPException(409, "Already watched")
    c = mm.ContentWatch(organisation_id=ctx.org_id, project_id=pid, url=url, published_at=body.published_at, target_keyword=body.target_keyword,
                        competitor_urls=body.competitor_urls, title=body.title, source="manual")
    db.add(c)
    db.flush()
    return to_dict(c)


@router.delete("/content-watch/{wid}")
def del_watch(wid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    c = db.get(mm.ContentWatch, wid)
    if not c:
        raise HTTPException(404)
    project_guard(db, ctx, c.project_id)
    db.delete(c)
    return {"ok": True}


class GapIn(BaseModel):
    kind: str
    target_url: str | None = None
    target_keyword: str | None = None
    competitor_urls: list[str] = Field(default_factory=list, max_length=10)


@router.get("/projects/{pid}/gaps")
def list_gaps(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    return [{**to_dict(g, exclude=("result",)), "summary": _gap_summary(g)} for g in
            db.scalars(select(mm.GapReport).where(mm.GapReport.project_id == pid).order_by(mm.GapReport.created_at.desc()).limit(100))]


def _gap_summary(g):
    r = g.result or {}
    if g.status != "COMPLETED":
        return r.get("error")
    if r.get("available") is False:
        return r.get("message")
    for k, label in (("keywords", "keywords"), ("topics", "topics"), ("prospects", "prospects"), ("competitors", "competitors")):
        if isinstance(r.get(k), list):
            return f"{len(r[k])} {label}"
    if r.get("root_causes") is not None:
        return f"{len(r['root_causes'])} root causes, {len(r.get('plan') or [])} plan steps"
    if r.get("recommendations") is not None:
        return f"{len(r['recommendations'])} recommendations"
    return None


@router.post("/projects/{pid}/gaps")
def new_gap(pid: uuid.UUID, body: GapIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    if body.kind not in ("keyword", "content", "competitor", "backlink", "structure", "full"):
        raise HTTPException(400, "kind: keyword, content, competitor, backlink, structure, full")
    if body.kind in ("structure", "full") and not (body.target_url or "").startswith(("http://", "https://")):
        raise HTTPException(400, "target_url (absolute) is required for structure / full page diagnosis")
    if body.kind == "structure" and not body.competitor_urls:
        raise HTTPException(400, "competitor_urls are required for a structure gap")
    g = mm.GapReport(organisation_id=ctx.org_id, project_id=pid, kind=body.kind, target_url=body.target_url,
                     params={"target_keyword": body.target_keyword, "competitor_urls": body.competitor_urls, "trigger": "manual"})
    db.add(g)
    db.flush()
    j = enqueue(db, ctx.org_id, "gap_analysis", {"gap_report_id": str(g.id)}, created_by=ctx.user_id)
    return {"id": str(g.id), "job_id": str(j.id)}


@router.get("/gaps/{gid}")
def get_gap(gid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    g = db.get(mm.GapReport, gid)
    if not g:
        raise HTTPException(404)
    project_guard(db, ctx, g.project_id)
    return to_dict(g)


# ------------------------------------------------------------------ email delivery (task assignments, alerts)
@router.get("/email/status")
def email_status(ctx: Ctx = Depends(require("view"))):
    from ..services import mailer
    return mailer.status()


@router.get("/email/outbox")
def email_outbox(mine: bool = False, limit: int = Query(100, ge=1, le=500), db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    """Emails sent (or not) by the platform. Managers see the organisation's outbox; others only their own."""
    from ..rbac import can
    q = select(mm.EmailOutbox).order_by(mm.EmailOutbox.created_at.desc()).limit(limit)
    if mine or not can(ctx.role, "manage_users"):
        q = q.where(mm.EmailOutbox.user_id == ctx.user_id)
    return [{k: v for k, v in to_dict(r).items() if k != "body"} | {"preview": r.body[:400]} for r in db.scalars(q)]


@router.get("/playbooks")
def playbook_library(ctx: Ctx = Depends(require("view"))):
    """The fix playbooks: solution, steps per team, RACI and prevention strategy for every issue type."""
    from ..services import playbooks
    return [{"kind": k, **v} for k, v in playbooks.PLAYBOOKS.items()]
