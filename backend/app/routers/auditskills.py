"""Audit Skills: 7 runnable per-project audit skills with stored findings.

Skills: pagespeed, screaming_frog, ahrefs, semrush, schema, crawler_files,
responsive. Each run stores findings (category, severity, url, title, detail,
recommendation) that the frontend renders as tables and reports.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from .. import models_auditskills as sm
from ..deps import Ctx, get_db, log, project_guard, require
from ..security import now
from ..services import auditskills as svc
from ..util import rows, to_dict

router = APIRouter(tags=["auditskills"])

SKILLS = {
    "pagespeed": {
        "name": "PageSpeed Audit",
        "desc": "Tests your pages with Google PageSpeed Insights (mobile + desktop): loading speed, LCP, FCP and a score out of 100.",
        "cost": "Free (needs a free Google API key)",
        "needs": "Page URLs + Google PageSpeed API key",
    },
    "screaming_frog": {
        "name": "Screaming Frog Import",
        "desc": "Upload a Screaming Frog crawl export (CSV). Finds missing titles, broken pages, missing descriptions and headings.",
        "cost": "Free",
        "needs": "Screaming Frog CSV export",
    },
    "ahrefs": {
        "name": "Ahrefs Audit Import",
        "desc": "Upload an Ahrefs site-audit export (CSV) — url, issue, severity columns. Every issue becomes a tracked finding.",
        "cost": "Free",
        "needs": "Ahrefs CSV export",
    },
    "semrush": {
        "name": "Semrush Audit Import",
        "desc": "Upload a Semrush site-audit export (CSV) — url, issue, severity columns. Every issue becomes a tracked finding.",
        "cost": "Free",
        "needs": "Semrush CSV export",
    },
    "schema": {
        "name": "Schema Checker",
        "desc": "Opens your pages and checks the Google schema markup: detects the page type (product, blog, FAQ, service…) and reports missing or broken schema.",
        "cost": "Free",
        "needs": "Page URLs",
    },
    "crawler_files": {
        "name": "Crawler Files Check",
        "desc": "Checks robots.txt, sitemap.xml and llms.txt for your domain: do they exist, are they valid, and do they point crawlers correctly?",
        "cost": "Free",
        "needs": "Domain name",
    },
    "responsive": {
        "name": "Responsive Check",
        "desc": "Records page × device test results (Android phones, iPhones, iPads, desktop) and tracks which pages fail on which device.",
        "cost": "Free",
        "needs": "Test results per page and device",
    },
}


class RunIn(BaseModel):
    project_id: uuid.UUID
    urls: list[str] | None = None
    api_key: str | None = None
    domain: str | None = None
    strategies: list[str] | None = None
    results: list[dict] | None = None


def _run_row(db, ctx, skill_key: str, project_id: uuid.UUID) -> sm.SkillRun:
    if skill_key not in SKILLS:
        raise HTTPException(404, f"Unknown skill '{skill_key}'")
    project_guard(db, ctx, project_id)
    run = sm.SkillRun(organisation_id=ctx.org_id, project_id=project_id, skill_key=skill_key,
                      status="running", cost_estimate=SKILLS[skill_key]["cost"], created_by=ctx.user_id)
    db.add(run)
    db.flush()
    return run


def _finish(db, ctx, run: sm.SkillRun, summary: dict):
    run.status = "completed"
    run.summary = summary
    run.finished_at = now()
    log(db, ctx, "auditskill.run", f"skill_run:{run.id}", {"skill": run.skill_key, "project": str(run.project_id)})
    return run


def _fail(db, run: sm.SkillRun, exc: Exception):
    run.status = "failed"
    run.error = str(exc)[:2000]
    run.finished_at = now()


@router.get("/auditskills")
def list_skills(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return [{"key": k, **v} for k, v in SKILLS.items()]


@router.post("/auditskills/{skill_key}/run")
def run_skill(skill_key: str, body: RunIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    run = _run_row(db, ctx, skill_key, body.project_id)
    try:
        summary = svc.RUNNERS[skill_key](db, ctx.org_id, body.project_id, run.id, body.model_dump())
        _finish(db, ctx, run, summary)
    except Exception as e:
        _fail(db, run, e)
    db.flush()
    return to_dict(run)


@router.get("/auditskills/runs")
def list_runs(project_id: uuid.UUID, skill_key: str | None = None, limit: int = Query(50, le=200),
              db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    q = select(sm.SkillRun).where(sm.SkillRun.project_id == project_id)
    if skill_key:
        q = q.where(sm.SkillRun.skill_key == skill_key)
    runs = db.scalars(q.order_by(desc(sm.SkillRun.created_at)).limit(limit)).all()
    out = []
    for r in runs:
        counts = {}
        for sev, n in db.execute(
                select(sm.SkillFinding.severity, func.count()).where(sm.SkillFinding.run_id == r.id)
                .group_by(sm.SkillFinding.severity)).all():
            counts[sev] = n
        out.append({**to_dict(r), "skill_name": SKILLS.get(r.skill_key, {}).get("name", r.skill_key),
                    "finding_counts": counts})
    return out


@router.get("/auditskills/runs/{rid}")
def get_run(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    run = db.get(sm.SkillRun, rid)
    if not run:
        raise HTTPException(404, "Run not found")
    project_guard(db, ctx, run.project_id)
    findings = db.scalars(select(sm.SkillFinding).where(sm.SkillFinding.run_id == rid)
                          .order_by(sm.SkillFinding.severity, sm.SkillFinding.id)).all()
    return {**to_dict(run), "skill_name": SKILLS.get(run.skill_key, {}).get("name", run.skill_key),
            "findings": rows(findings)}


@router.post("/auditskills/import/{skill_key}")
async def import_csv(skill_key: str, project_id: uuid.UUID, file: UploadFile = File(...),
                     db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    if skill_key not in ("screaming_frog", "ahrefs", "semrush"):
        raise HTTPException(400, "CSV import is only for screaming_frog, ahrefs and semrush")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "CSV is too big (max 5 MB)")
    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "Could not read the file — please upload a UTF-8 CSV")
    run = _run_row(db, ctx, skill_key, project_id)
    try:
        summary = svc.RUNNERS[skill_key](db, ctx.org_id, project_id, run.id, {"csv_text": csv_text})
        summary["filename"] = file.filename
        _finish(db, ctx, run, summary)
    except Exception as e:
        _fail(db, run, e)
    db.flush()
    return to_dict(run)


@router.patch("/auditskills/findings/{fid}")
def patch_finding(fid: int, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    f = db.get(sm.SkillFinding, fid)
    if not f:
        raise HTTPException(404, "Finding not found")
    project_guard(db, ctx, f.project_id)
    if body.get("status") in ("open", "ignored", "fixed"):
        f.status = body["status"]
    return to_dict(f)


@router.get("/auditskills/devices")
def device_profiles(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return {"devices": svc.DEVICE_PROFILES}
