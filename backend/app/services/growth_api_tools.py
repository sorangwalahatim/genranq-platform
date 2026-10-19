"""API (authenticated): free tools workspace (67–72) — run tools, captured free-tool leads, public key/embed
settings and the organisation's backlink monitors."""
from __future__ import annotations

import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from .. import models as m
from .. import models_growth as g
from ..deps import Ctx, deny_client, get_db, log, project_guard, require
from ..util import to_dict
from . import growth_tools as T
from .growth_api_leads import base_url
from .growth_api_public import run_tool
from .growth_common import ensure_public_key, new_token, valid_email
from .handlers_growth import check_monitor, ensure_monitor_schedule
from .jobs import enqueue

router = APIRouter()


@router.get("/tools")
def tools_home(request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    pub = ensure_public_key(db, ctx.org_id)
    b = base_url(request)
    slugs = {"domain-age": "domain-age", "broken-links": "broken-links", "link-analyzer": "link-analyzer", "dofollow": "dofollow",
             "website-finder": "website-finder", "backlink-monitor": "backlink-monitor"}
    leads = dict(db.execute(select(m.FreeToolLead.tool, func.count()).where(m.FreeToolLead.organisation_id == ctx.org_id)
                            .group_by(m.FreeToolLead.tool)).all())
    runs = dict(db.execute(select(g.GrowthToolRun.tool, func.count()).group_by(g.GrowthToolRun.tool)).all())
    return {"tools": [{"key": k, "name": v, "public_url": f"{b}/free/{slugs[k]}?key={pub.public_key}", "leads": leads.get(k, 0),
                       "runs": runs.get(k, 0)} for k, v in T.TOOLS.items()],
            "public_key": pub.public_key, "free_tools_enabled": pub.free_tools_enabled,
            "project_id": str(pub.project_id) if pub.project_id else None}


class PublicSettingsIn(BaseModel):
    free_tools_enabled: bool | None = None
    project_id: uuid.UUID | None = None
    rotate_key: bool = False


@router.put("/tools/public-settings")
def public_settings(body: PublicSettingsIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    pub = ensure_public_key(db, ctx.org_id)
    if body.free_tools_enabled is not None:
        pub.free_tools_enabled = body.free_tools_enabled
    if "project_id" in body.model_fields_set:
        if body.project_id:
            project_guard(db, ctx, body.project_id)
        pub.project_id = body.project_id
    if body.rotate_key:
        pub.public_key = new_token(12)
    log(db, ctx, "tools.public_settings", None, body.model_dump(mode="json"))
    return to_dict(pub)


class RunIn(BaseModel):
    url: str | None = Field(None, max_length=2000)
    domain: str | None = Field(None, max_length=253)
    keyword: str | None = Field(None, max_length=100)
    max_pages: int = Field(25, ge=1, le=200)
    extra: list[str] = Field([], max_length=10)


@router.post("/tools/{tool}/run")
def run(tool: str, body: RunIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    if tool not in T.TOOLS or tool == "backlink-monitor":
        raise HTTPException(404, "Unknown tool")
    inp = body.model_dump(exclude_none=True)
    r = g.GrowthToolRun(organisation_id=ctx.org_id, tool=tool, input=inp, created_by=ctx.user_id)
    db.add(r)
    db.flush()
    if tool == "broken-links":
        try:
            T.clean_url(body.url or "")
        except T.ToolError as e:
            raise HTTPException(400, str(e))
        enqueue(db, ctx.org_id, "growth_tool_run", {"run_id": str(r.id)}, created_by=ctx.user_id)
        return to_dict(r)
    try:
        r.result = json.loads(json.dumps(run_tool(tool, inp, public=False, viewer_org=ctx.org_id), default=str))
        r.status = "COMPLETED"
    except T.ToolError as e:
        r.status, r.error = "FAILED", str(e)
    from .growth_common import utcnow
    r.finished_at = utcnow()
    return to_dict(r)


@router.get("/tools/runs")
def runs(tool: str | None = None, limit: int = Query(30, le=200), db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    q = select(g.GrowthToolRun).where(g.GrowthToolRun.public.is_(False)).order_by(desc(g.GrowthToolRun.created_at)).limit(limit)
    if tool:
        q = q.where(g.GrowthToolRun.tool == tool)
    return [to_dict(r, exclude=("result",)) | {"summary": {k: v for k, v in (r.result or {}).items() if not isinstance(v, (list, dict))}}
            for r in db.scalars(q)]


@router.get("/tools/runs/{rid}")
def run_detail(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    r = db.get(g.GrowthToolRun, rid)
    if not r:
        raise HTTPException(404)
    return to_dict(r)


@router.get("/tools/leads")
def tool_leads(tool: str | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    # free_tool_lead rows with a NULL organisation are readable by every tenant (built-in policy): always filter to ours.
    q = select(m.FreeToolLead).where(m.FreeToolLead.organisation_id == ctx.org_id).order_by(desc(m.FreeToolLead.created_at)).limit(1000)
    if tool:
        q = q.where(m.FreeToolLead.tool == tool)
    return [to_dict(x) for x in db.scalars(q)]


@router.get("/tools/leads/export.csv")
def tool_leads_csv(db=Depends(get_db), ctx: Ctx = Depends(require("export")), _c=Depends(deny_client)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["created_at", "tool", "email", "input"])
    for x in db.scalars(select(m.FreeToolLead).where(m.FreeToolLead.organisation_id == ctx.org_id).order_by(desc(m.FreeToolLead.created_at))):
        w.writerow([x.created_at, x.tool, x.email, x.input])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=free-tool-leads.csv"})


class MonitorIn(BaseModel):
    source_url: str = Field(..., max_length=2000)
    target_url: str = Field(..., max_length=2000)
    email: str | None = Field(None, max_length=254)


@router.get("/tools/monitors")
def monitors(db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    rows = db.scalars(select(g.GrowthPublicMonitor).order_by(desc(g.GrowthPublicMonitor.created_at)).limit(500)).all()
    return [to_dict(x, exclude=("manage_token",)) for x in rows]


@router.post("/tools/monitors")
def add_monitor(body: MonitorIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    try:
        src, tgt = T.clean_url(body.source_url), T.clean_url(body.target_url)
    except T.ToolError as e:
        raise HTTPException(400, str(e))
    if body.email and not valid_email(body.email):
        raise HTTPException(400, "Invalid email")
    if db.scalar(select(func.count()).select_from(g.GrowthPublicMonitor).where(g.GrowthPublicMonitor.status == "active")) >= 1000:
        raise HTTPException(402, "Monitor limit (1,000) reached")
    email = body.email or db.get(m.User, ctx.user_id).email
    mon = g.GrowthPublicMonitor(organisation_id=ctx.org_id, email=email, source_url=src, target_url=tgt, manage_token=new_token(24), public=False)
    db.add(mon)
    db.flush()
    ensure_monitor_schedule(db, ctx.org_id)
    check_monitor(db, mon)
    return to_dict(mon, exclude=("manage_token",))


@router.post("/tools/monitors/{mid}/check")
def check(mid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    mon = db.get(g.GrowthPublicMonitor, mid)
    if not mon:
        raise HTTPException(404)
    check_monitor(db, mon)
    return to_dict(mon, exclude=("manage_token",))


@router.delete("/tools/monitors/{mid}")
def del_monitor(mid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    mon = db.get(g.GrowthPublicMonitor, mid)
    if not mon:
        raise HTTPException(404)
    db.delete(mon)
    return {"ok": True}
