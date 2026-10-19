"""Background job handlers for the ai modules."""
import uuid

from .. import models as m
from .. import models_ai as ma
from ..db import tenant_session
from .jobs import FAIL_HOOKS, JobCtx, PermanentError, handler  # noqa: F401


def _user(db, uid):
    if not uid:
        return None, None
    u = db.get(m.User, uuid.UUID(uid))
    return (u.id, u.role) if u and u.is_active else (None, None)


@handler("ai_generate")
def ai_generate(ctx: JobCtx) -> dict:
    from . import ai_studio
    with ctx.session() as db:
        item = db.get(m.ContentItem, uuid.UUID(ctx.payload["item_id"]))
        if not item:
            raise PermanentError("Content item no longer exists")
        uid, role = _user(db, ctx.payload.get("user_id"))
        return ai_studio.generate(db, item, uid, role, ctx.payload.get("instructions"))


@handler("ai_image_generate")
def ai_image_generate(ctx: JobCtx) -> dict:
    from . import ai_images, integrations as ih, reports
    p = ctx.payload
    with ctx.session() as db:
        uid, _ = _user(db, p.get("user_id"))
        try:
            png, meta = ai_images.generate_ai(db, ctx.org_id, p["prompt"], p["size_key"])
        except ih.NotConnected as e:
            return {"connected": False, "message": str(e), "prompt": p["prompt"]}
        w, h, _ = ai_images.SIZES[p["size_key"]]
        f = reports.store(db, ctx.org_id, f"ai-{p['size_key']}.png", png, "image/png", user_id=uid, kind="image")
        a = ma.ImageAsset(organisation_id=ctx.org_id, project_id=uuid.UUID(p["project_id"]),
                          content_item_id=uuid.UUID(p["content_item_id"]) if p.get("content_item_id") else None, kind="ai",
                          size_key=p["size_key"], width=w, height=h, title=p.get("title"), prompt=p["prompt"], provider=meta["provider"],
                          stored_file_id=f.id, created_by=uid)
        db.add(a)
        db.flush()
        return {"connected": True, "asset_id": str(a.id), **meta}


@handler("ai_team_run")
def ai_team_run(ctx: JobCtx) -> dict:
    from . import ai_teams
    tid = uuid.UUID(ctx.payload["team_run_id"])
    uid = uuid.UUID(ctx.payload["user_id"]) if ctx.payload.get("user_id") else None
    return ai_teams.run_team(ctx.session, tid, uid)


def _fail_team(job, err):
    tid = (job.payload or {}).get("team_run_id")
    if not tid:
        return
    with tenant_session(job.organisation_id) as db:
        tr = db.get(ma.AgentTeamRun, uuid.UUID(tid))
        if tr and tr.status in ("QUEUED", "RUNNING"):
            tr.status, tr.error = "FAILED", err[:2000]


FAIL_HOOKS["ai_team_run"] = _fail_team
