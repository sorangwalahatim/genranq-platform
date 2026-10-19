"""Product specification: traceability, coverage, and "Refresh Product Specification"
(build prompt §16). Refresh never auto-applies: it creates a pending snapshot with a
change summary that a platform administrator must approve."""
from __future__ import annotations

import json
import os
import time
import uuid

import jwt
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import desc, select

from .. import models as m
from ..db import system_session
from ..deps import Ctx, get_ctx, require_platform_admin
from ..seed import seed_global
from ..services.integrations import _ok, http_call
from ..spec import traceability
from ..spec.parser import SpecError, diff_specs, parse_workbook, public_spec
from ..spec.store import active_spec, invalidate
from ..util import to_dict

router = APIRouter(prefix="/spec", tags=["spec"])


@router.get("")
def get_spec(ctx: Ctx = Depends(get_ctx)):
    return public_spec(active_spec())  # Cost Estimate stripped: internal only


@router.get("/modules")
def modules(ctx: Ctx = Depends(get_ctx)):
    sp = active_spec()
    return {"modules": traceability.build_map(sp), "phases": sp["roadmap"]["phases"], "coverage": traceability.coverage(sp)}


@router.get("/internal/cost-estimate")
def cost(ctx: Ctx = Depends(require_platform_admin)):
    return active_spec()["cost_estimate"]


def _drive_download(file_id: str) -> bytes:
    """Read-only download via a Google service account (scope drive.readonly)."""
    sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa:
        raise HTTPException(409, detail={"code": "not_connected", "message": "Google Drive is NOT CONNECTED: set "
                                         "GOOGLE_SERVICE_ACCOUNT_JSON and share the spreadsheet with that service account (Viewer)."})
    info = json.loads(open(sa).read() if os.path.exists(sa) else sa)
    t = int(time.time())
    assertion = jwt.encode({"iss": info["client_email"], "scope": "https://www.googleapis.com/auth/drive.readonly",
                            "aud": "https://oauth2.googleapis.com/token", "iat": t, "exp": t + 3600},
                           info["private_key"], algorithm="RS256")
    tok = _ok(http_call("POST", "https://oauth2.googleapis.com/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}), "Drive auth").json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    meta = _ok(http_call("GET", f"https://www.googleapis.com/drive/v3/files/{file_id}", headers=h,
                         params={"fields": "mimeType,name", "supportsAllDrives": "true"}), "Drive metadata").json()
    if meta["mimeType"] == "application/vnd.google-apps.spreadsheet":
        r = http_call("GET", f"https://www.googleapis.com/drive/v3/files/{file_id}/export", headers=h,
                      params={"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
    else:
        r = http_call("GET", f"https://www.googleapis.com/drive/v3/files/{file_id}", headers=h,
                      params={"alt": "media", "supportsAllDrives": "true"})
    return _ok(r, "Drive download").content


def _stage(raw: bytes, source: str, ctx: Ctx) -> dict:
    try:
        new = parse_workbook(raw)
    except SpecError as e:
        raise HTTPException(400, str(e))
    cur = active_spec()
    d = diff_specs(cur, new)
    with system_session() as db:
        snap = m.SpecSnapshot(sha256=new["meta"]["sha256"], source=source, spec=new, status="pending_review" if d["total_changes"] else "no_changes",
                              diff=d, submitted_by=ctx.user_id)
        db.add(snap)
        db.flush()
        db.add(m.ActivityLog(organisation_id=ctx.org_id, actor_id=ctx.user_id, action="spec.refresh_staged",
                             target=f"spec_snapshot:{snap.id}", meta={"changes": d["total_changes"], "source": source}))
        return {"snapshot_id": str(snap.id), "status": snap.status, "counts": new["meta"]["counts"], "diff": d,
                "coverage": traceability.coverage(new)}


@router.post("/refresh/upload")
async def refresh_upload(file: UploadFile = File(...), ctx: Ctx = Depends(require_platform_admin)):
    return _stage(await file.read(), "upload", ctx)


@router.post("/refresh/drive")
def refresh_drive(body: dict | None = None, ctx: Ctx = Depends(require_platform_admin)):
    fid = (body or {}).get("file_id") or os.environ.get("SPEC_DRIVE_FILE_ID")
    if not fid:
        raise HTTPException(400, "No Drive file configured (SPEC_DRIVE_FILE_ID)")
    return _stage(_drive_download(fid), f"google_drive:{fid}", ctx)


@router.get("/snapshots")
def snapshots(ctx: Ctx = Depends(require_platform_admin)):
    with system_session() as db:
        return [{**to_dict(s, exclude=("spec",)), "counts": s.spec.get("meta", {}).get("counts")}
                for s in db.scalars(select(m.SpecSnapshot).order_by(desc(m.SpecSnapshot.created_at)).limit(50))]


@router.post("/snapshots/{sid}/{decision}")
def review(sid: uuid.UUID, decision: str, ctx: Ctx = Depends(require_platform_admin)):
    if decision not in ("approve", "reject"):
        raise HTTPException(400)
    with system_session() as db:
        s = db.get(m.SpecSnapshot, sid)
        if not s or s.status != "pending_review":
            raise HTTPException(404, "No pending snapshot")
        s.reviewed_by, s.reviewed_at = ctx.user_id, __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        if decision == "reject":
            s.status = "rejected"
        else:
            for a in db.scalars(select(m.SpecSnapshot).where(m.SpecSnapshot.status == "active")):
                a.status = "superseded"
            s.status = "active"
        db.add(m.ActivityLog(organisation_id=ctx.org_id, actor_id=ctx.user_id, action=f"spec.{decision}d", target=f"spec_snapshot:{sid}"))
        spec = s.spec
    invalidate()
    if decision == "approve":
        seed_global(spec)  # checklist catalogue follows the sheet; plans read the new Pricing Matrix live
    return {"status": "active" if decision == "approve" else "rejected"}
