"""Workstreams & AI studio API: strategies → activities (person / team / AI agent), the agent
catalog, project profile and the prompt-skills library with runs and Excel export."""
from __future__ import annotations

import re
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import models as m
from .. import models_monitor as mm
from ..deps import Ctx, deny_client, get_db, log, project_guard, require, visible_project_ids
from ..services import agents, skills, workstreams as ws
from ..services.handlers_monitor import ensure_org_schedules
from ..services.jobs import enqueue
from ..util import to_dict

router = APIRouter(tags=["studio"])
KEY_RE = re.compile(r"^[a-z0-9_]{3,60}$")


# ------------------------------------------------------------------ strategies
@router.get("/strategies")
def strategies(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return {"workstreams": [{"key": k, "label": ws.WORKSTREAM_LABEL[k]} for k in mm.WORKSTREAMS], "templates": ws.templates(db),
            "cadences": list(ws.CADENCE)}


class StrategyTemplateIn(BaseModel):
    key: str
    name: str = Field(..., max_length=200)
    description: str | None = Field(None, max_length=2000)
    activities: list[dict] = Field(..., max_length=100)


@router.post("/strategies")
def create_template(body: StrategyTemplateIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    if not KEY_RE.match(body.key) or body.key in ws.STRATEGY_BY_KEY:
        raise HTTPException(400, "key must be 3–60 chars a-z0-9_ and not a built-in strategy key")
    acts = [_clean_activity(a) for a in body.activities]
    t = db.scalar(select(mm.StrategyTemplate).where(mm.StrategyTemplate.key == body.key, mm.StrategyTemplate.organisation_id == ctx.org_id))
    if not t:
        t = mm.StrategyTemplate(organisation_id=ctx.org_id, key=body.key, name=body.name)
        db.add(t)
    t.name, t.description, t.activities = body.name, body.description, acts
    log(db, ctx, "strategy.template_saved", None, {"key": body.key})
    db.flush()
    return to_dict(t)


def _clean_activity(a: dict) -> dict:
    if not KEY_RE.match(str(a.get("key", ""))):
        raise HTTPException(400, f"activity key invalid: {a.get('key')}")
    if a.get("workstream") not in mm.WORKSTREAMS:
        raise HTTPException(400, f"workstream must be one of {mm.WORKSTREAMS}")
    if a.get("cadence", "monthly") not in ws.CADENCE:
        raise HTTPException(400, f"cadence must be one of {list(ws.CADENCE)}")
    who = a.get("assignee_type", "team")
    if who not in ("user", "team", "ai_agent"):
        raise HTTPException(400, "assignee_type: user, team or ai_agent")
    if who == "ai_agent" and not _agent_ok(a.get("agent_key")):
        raise HTTPException(400, f"Unknown agent {a.get('agent_key')}")
    return {"key": a["key"], "workstream": a["workstream"], "title": str(a.get("title") or a["key"])[:200], "cadence": a.get("cadence", "monthly"),
            "assignee_type": who, "agent_key": a.get("agent_key"), "team": a.get("team") or ws._team_for(a["workstream"]),
            "instructions": a.get("instructions"), "approval_required": bool(a.get("approval_required", True))}


def _agent_ok(key) -> bool:
    if not key:
        return False
    if key.startswith("skill:"):
        return True  # validated at run time (custom skills can be added later)
    return key in agents.BY_KEY


class ApplyIn(BaseModel):
    key: str
    overrides: dict = {}
    start: date | None = None


@router.get("/projects/{pid}/strategies")
def project_strategies(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    return [to_dict(s) for s in db.scalars(select(mm.ProjectStrategy).where(mm.ProjectStrategy.project_id == pid))]


@router.post("/projects/{pid}/strategies")
def apply(pid: uuid.UUID, body: ApplyIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, pid)
    for a in (body.overrides.get("activities") or {}).values():
        if a.get("assignee_type") == "ai_agent" and a.get("agent_key") and not _agent_ok(a["agent_key"]):
            raise HTTPException(400, f"Unknown agent {a['agent_key']}")
        if a.get("cadence") and a["cadence"] not in ws.CADENCE:
            raise HTTPException(400, "bad cadence")
    try:
        res = ws.apply_strategy(db, p, body.key, body.overrides, body.start)
    except KeyError:
        raise HTTPException(404, "Unknown strategy")
    ensure_org_schedules(db, ctx.org_id)
    log(db, ctx, "strategy.applied", f"project:{pid}", {"key": body.key, "added": len(res["added"])})
    return res


@router.delete("/project-strategies/{sid}")
def remove_strategy(sid: uuid.UUID, remove_activities: bool = False, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    s = db.get(mm.ProjectStrategy, sid)
    if not s:
        raise HTTPException(404)
    project_guard(db, ctx, s.project_id)
    if remove_activities:
        for a in db.scalars(select(mm.ProjectActivity).where(mm.ProjectActivity.strategy_id == s.id)):
            db.delete(a)
    db.delete(s)
    log(db, ctx, "strategy.removed", f"project:{s.project_id}", {"name": s.name})
    return {"ok": True}


# ------------------------------------------------------------------ activities
class ActivityIn(BaseModel):
    workstream: str
    key: str | None = None
    title: str = Field(..., max_length=300)
    instructions: str | None = Field(None, max_length=10000)
    cadence: str = "weekly"
    assignee_type: str = "team"
    assignee_user_ids: list[uuid.UUID] = []
    team: str | None = None
    agent_key: str | None = None
    approval_required: bool = True
    enabled: bool = True
    next_due: date | None = None
    config: dict = {}


class ActivityPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    instructions: str | None = Field(None, max_length=10000)
    cadence: str | None = None
    assignee_type: str | None = None
    assignee_user_ids: list[uuid.UUID] | None = None
    team: str | None = None
    agent_key: str | None = None
    approval_required: bool | None = None
    enabled: bool | None = None
    next_due: date | None = None
    config: dict | None = None
    workstream: str | None = None


def _check_activity(db, d: dict):
    if d.get("workstream") is not None and d["workstream"] not in mm.WORKSTREAMS:
        raise HTTPException(400, f"workstream must be one of {mm.WORKSTREAMS}")
    if d.get("cadence") is not None and d["cadence"] not in ws.CADENCE:
        raise HTTPException(400, f"cadence must be one of {list(ws.CADENCE)}")
    if d.get("assignee_type") is not None and d["assignee_type"] not in ("user", "team", "ai_agent"):
        raise HTTPException(400, "assignee_type: user, team or ai_agent")
    if d.get("team") is not None and d["team"] not in mm.TEAMS:
        raise HTTPException(400, f"team must be one of {mm.TEAMS}")
    if d.get("agent_key") and not _agent_ok(d["agent_key"]):
        raise HTTPException(400, f"Unknown agent {d['agent_key']}")
    if d.get("assignee_type") == "ai_agent" and not d.get("agent_key"):
        raise HTTPException(400, "Choose an AI agent or skill for an AI-assigned activity")
    for u in d.get("assignee_user_ids") or []:
        usr = db.get(m.User, u)
        if not usr or not usr.is_active or usr.role == "client":
            raise HTTPException(400, f"Invalid assignee {u}")


@router.get("/projects/{pid}/activities")
def list_activities(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    acts = db.scalars(select(mm.ProjectActivity).where(mm.ProjectActivity.project_id == pid).order_by(mm.ProjectActivity.workstream, mm.ProjectActivity.title)).all()
    last = {}
    for r in db.scalars(select(mm.ActivityRun).where(mm.ActivityRun.project_id == pid).order_by(mm.ActivityRun.started_at.desc()).limit(500)):
        last.setdefault(r.activity_id, r)
    return [{**to_dict(a), "agent_name": (agents.BY_KEY.get(a.agent_key) or {}).get("name") or (skills.BY_KEY.get((a.agent_key or "")[6:]) or {}).get("name"),
             "last_run": {"status": last[a.id].status, "summary": last[a.id].summary, "at": last[a.id].started_at,
                          "task_id": str(last[a.id].task_id) if last[a.id].task_id else None} if a.id in last else None} for a in acts]


@router.post("/projects/{pid}/activities")
def add_activity(pid: uuid.UUID, body: ActivityIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    d = body.model_dump()
    _check_activity(db, d)
    key = d.pop("key") or re.sub(r"[^a-z0-9]+", "_", body.title.lower()).strip("_")[:50] + "_" + uuid.uuid4().hex[:4]
    if not KEY_RE.match(key):
        raise HTTPException(400, "key: 3–60 chars a-z0-9_")
    if db.scalar(select(mm.ProjectActivity.id).where(mm.ProjectActivity.project_id == pid, mm.ProjectActivity.key == key)):
        raise HTTPException(409, "An activity with that key exists")
    d["team"] = d["team"] or ws._team_for(d["workstream"])
    d["next_due"] = d["next_due"] or date.today()
    a = mm.ProjectActivity(organisation_id=ctx.org_id, project_id=pid, key=key, **d)
    db.add(a)
    ensure_org_schedules(db, ctx.org_id)
    log(db, ctx, "activity.created", f"project:{pid}", {"title": a.title, "assignee_type": a.assignee_type})
    db.flush()
    return to_dict(a)


@router.patch("/activities/{aid}")
def patch_activity(aid: uuid.UUID, body: ActivityPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    a = db.get(mm.ProjectActivity, aid)
    if not a:
        raise HTTPException(404)
    project_guard(db, ctx, a.project_id)
    d = body.model_dump(exclude_unset=True)
    _check_activity(db, {**{"assignee_type": a.assignee_type, "agent_key": a.agent_key}, **d})
    for k, v in d.items():
        setattr(a, k, v)
    if d.get("enabled") and not a.next_due:
        a.next_due = date.today()
    log(db, ctx, "activity.updated", f"activity:{aid}", {"fields": list(d)})
    return to_dict(a)


@router.delete("/activities/{aid}")
def del_activity(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    a = db.get(mm.ProjectActivity, aid)
    if not a:
        raise HTTPException(404)
    project_guard(db, ctx, a.project_id)
    db.delete(a)
    log(db, ctx, "activity.deleted", f"project:{a.project_id}", {"title": a.title})
    return {"ok": True}


@router.post("/activities/{aid}/run")
def run_activity_now(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    a = db.get(mm.ProjectActivity, aid)
    if not a:
        raise HTTPException(404)
    project_guard(db, ctx, a.project_id)
    j = enqueue(db, ctx.org_id, "run_activity", {"activity_id": str(a.id), "triggered_by": "manual", "user_id": str(ctx.user_id)}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


@router.get("/projects/{pid}/activity-runs")
def activity_runs(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    return [to_dict(r) for r in db.scalars(select(mm.ActivityRun).where(mm.ActivityRun.project_id == pid).order_by(mm.ActivityRun.started_at.desc()).limit(200))]


@router.get("/workload")
def workload(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    """Who does what: activities per person / team / AI agent across visible projects."""
    pids = visible_project_ids(db, ctx)
    names = {u.id: u.name or u.email for u in db.scalars(select(m.User))}
    projects = {p.id: p.name for p in db.scalars(select(m.Project).where(m.Project.id.in_(pids)))}
    rows = []
    for a in db.scalars(select(mm.ProjectActivity).where(mm.ProjectActivity.project_id.in_(pids), mm.ProjectActivity.enabled)):
        who = ("AI: " + ((agents.BY_KEY.get(a.agent_key) or {}).get("name") or a.agent_key)) if a.assignee_type == "ai_agent" else \
            (", ".join(names.get(u, "?") for u in a.assignee_user_ids) if a.assignee_user_ids else f"Team: {a.team}")
        rows.append({"project": projects.get(a.project_id), "workstream": a.workstream, "activity": a.title, "who": who,
                     "assignee_type": a.assignee_type, "cadence": a.cadence, "next_due": a.next_due})
    return rows


# ------------------------------------------------------------------ agents
@router.get("/agents/catalog")
def catalog(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return agents.catalog(db)


@router.post("/projects/{pid}/agents/{key}/run")
def run_agent(pid: uuid.UUID, key: str, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    """Ad-hoc run: creates (or reuses) a one-off activity so the run, output and review task are recorded."""
    project_guard(db, ctx, pid)
    if key not in agents.BY_KEY:
        raise HTTPException(404, "Unknown agent")
    akey = f"adhoc_{key}"[:60]
    a = db.scalar(select(mm.ProjectActivity).where(mm.ProjectActivity.project_id == pid, mm.ProjectActivity.key == akey))
    if not a:
        ag = agents.BY_KEY[key]
        a = mm.ProjectActivity(organisation_id=ctx.org_id, project_id=pid, key=akey, workstream=ag["workstream"] if ag["workstream"] in mm.WORKSTREAMS else "strategy",
                               title=ag["name"], cadence="once", assignee_type="ai_agent", agent_key=key, team=ws._team_for(ag["workstream"]),
                               enabled=False, approval_required=True)
        db.add(a)
        db.flush()
    j = enqueue(db, ctx.org_id, "run_activity", {"activity_id": str(a.id), "triggered_by": "manual", "user_id": str(ctx.user_id)}, created_by=ctx.user_id)
    return {"job_id": str(j.id), "activity_id": str(a.id)}


# ------------------------------------------------------------------ project profile
@router.get("/projects/{pid}/profile")
def get_profile(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, pid)
    stored = db.get(mm.ProjectProfile, pid)
    return {"fields": [{"key": k, "label": lbl} for k, lbl in skills.PROFILE_FIELDS], "data": stored.data if stored else {},
            "effective": skills.get_profile(db, p)}


@router.put("/projects/{pid}/profile")
def put_profile(pid: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, pid)
    allowed = {k for k, _ in skills.PROFILE_FIELDS} | {"locations"}
    bad = [k for k in body if k not in allowed]
    if bad:
        raise HTTPException(400, f"Unknown profile fields: {bad}")
    clean = {}
    for k, v in body.items():
        if isinstance(v, list):
            v = [str(x)[:500] for x in v][:200]
        elif v is not None:
            v = str(v)[:5000]
        clean[k] = v
    pr = db.get(mm.ProjectProfile, pid)
    if not pr:
        pr = mm.ProjectProfile(project_id=pid, organisation_id=ctx.org_id, data={})
        db.add(pr)
    pr.data = {**(pr.data or {}), **clean}
    log(db, ctx, "project.profile_updated", f"project:{pid}", {"fields": list(clean)})
    return {"ok": True}


# ------------------------------------------------------------------ prompt skills
@router.get("/skills")
def list_skills(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    from ..services import integrations as ih
    conn = ih.connected_map(db)
    ai = any(conn.get(k) == "CONNECTED" for k in ("claude", "openai", "gemini", "perplexity"))
    return {"ai_provider_connected": ai, "skills": skills.library(db),
            "evidence_types": ["client_page", "competitor_pages", "gsc_queries", "keywords"]}


class SkillIn(BaseModel):
    key: str
    name: str = Field(..., max_length=200)
    category: str = Field("custom", max_length=60)
    workstream: str = "content"
    description: str | None = Field(None, max_length=2000)
    template: str = Field(..., max_length=60000)
    inputs: list[str] = Field(default_factory=list, max_length=40)
    evidence: list[str] = Field(default_factory=list, max_length=4)
    output_columns: list[str] = Field(default_factory=list, max_length=40)


def _check_skill(body: SkillIn):
    if not KEY_RE.match(body.key) or body.key in skills.BY_KEY:
        raise HTTPException(400, "key must be 3–60 chars a-z0-9_ and not a built-in skill key")
    if body.workstream not in mm.WORKSTREAMS:
        raise HTTPException(400, f"workstream must be one of {mm.WORKSTREAMS}")
    bad = [e for e in body.evidence if e not in ("client_page", "competitor_pages", "gsc_queries", "keywords")]
    if bad:
        raise HTTPException(400, f"Unknown evidence collectors: {bad}")


@router.post("/skills")
def create_skill(body: SkillIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    _check_skill(body)
    if db.scalar(select(mm.PromptSkill.id).where(mm.PromptSkill.key == body.key)):
        raise HTTPException(409, "A skill with that key exists — edit it instead")
    s = mm.PromptSkill(organisation_id=ctx.org_id, created_by=ctx.user_id, **body.model_dump())
    db.add(s)
    log(db, ctx, "skill.created", None, {"key": body.key})
    db.flush()
    return to_dict(s)


@router.put("/skills/{key}")
def update_skill(key: str, body: SkillIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    if body.key != key:
        raise HTTPException(400, "key can't change")
    _check_skill(body)
    s = db.scalar(select(mm.PromptSkill).where(mm.PromptSkill.key == key))
    if not s:
        raise HTTPException(404, "Built-in skills can't be edited — duplicate one as a custom skill")
    s.history = (s.history or []) + [{"version": s.version, "template": s.template, "inputs": s.inputs, "evidence": s.evidence,
                                      "output_columns": s.output_columns, "at": str(date.today())}]
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    s.version += 1
    log(db, ctx, "skill.updated", None, {"key": key, "version": s.version})
    return to_dict(s)


@router.delete("/skills/{key}")
def delete_skill(key: str, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    s = db.scalar(select(mm.PromptSkill).where(mm.PromptSkill.key == key))
    if not s:
        raise HTTPException(404)
    s.enabled = False  # keep history; runs keep referencing it
    log(db, ctx, "skill.disabled", None, {"key": key})
    return {"ok": True}


class SkillRunIn(BaseModel):
    inputs: dict = {}
    use_ai: bool = True


@router.post("/projects/{pid}/skills/{key}/run")
def run_skill(pid: uuid.UUID, key: str, body: SkillRunIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    if not skills.get_skill(db, key):
        raise HTTPException(404, "Unknown skill")
    inputs = {k: v for k, v in body.inputs.items() if isinstance(k, str) and len(k) <= 60}
    for k in ("competitor_urls", "url"):
        vals = inputs.get(k)
        vals = vals if isinstance(vals, list) else ([vals] if vals else [])
        for u in vals:
            if u and not re.match(r"^(https?://)?[\w.-]+(:\d+)?(/\S*)?$", str(u)):
                raise HTTPException(400, f"Invalid URL: {u}")
    if isinstance(inputs.get("competitor_urls"), list) and len(inputs["competitor_urls"]) > 10:
        raise HTTPException(400, "At most 10 competitor URLs")
    r = mm.SkillRun(organisation_id=ctx.org_id, project_id=pid, skill_key=key, status="QUEUED", created_by=ctx.user_id, inputs=inputs)
    db.add(r)
    db.flush()
    j = enqueue(db, ctx.org_id, "skill_run", {"run_id": str(r.id), "inputs": inputs, "use_ai": body.use_ai}, created_by=ctx.user_id, max_attempts=1)
    log(db, ctx, "skill.run", f"project:{pid}", {"skill": key})
    return {"run_id": str(r.id), "job_id": str(j.id)}


@router.get("/projects/{pid}/skill-runs")
def skill_runs(pid: uuid.UUID, skill: str | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, pid)
    q = select(mm.SkillRun).where(mm.SkillRun.project_id == pid)
    if skill:
        q = q.where(mm.SkillRun.skill_key == skill)
    return [to_dict(r, exclude=("evidence", "prompt", "output")) for r in db.scalars(q.order_by(mm.SkillRun.created_at.desc()).limit(200))]


@router.get("/skill-runs/{rid}")
def skill_run(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    r = db.get(mm.SkillRun, rid)
    if not r:
        raise HTTPException(404)
    project_guard(db, ctx, r.project_id)
    return {**to_dict(r), "output": skills.ordered_output(r.output)}


@router.get("/skill-runs/{rid}/xlsx")
def skill_run_xlsx(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("export"))):
    r = db.get(mm.SkillRun, rid)
    if not r:
        raise HTTPException(404)
    project_guard(db, ctx, r.project_id)
    if r.status not in ("COMPLETED", "NEEDS_DATA"):
        raise HTTPException(409, f"Run is {r.status}")
    sk = skills.get_skill(db, r.skill_key) or {"name": r.skill_key}
    data = skills.to_xlsx(r, sk["name"])
    fn = f"{r.skill_key}-{r.created_at:%Y%m%d-%H%M}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fn}"'})
