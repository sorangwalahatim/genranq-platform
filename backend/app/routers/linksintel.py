"""Links Intel: anchor-wise backlink analysis + internal link map (project-wise)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..deps import Ctx, get_db, log, project_guard, require
from ..services import linksintel as li

router = APIRouter(tags=["linksintel"])


class BacklinksImportIn(BaseModel):
    project_id: uuid.UUID
    site_label: str = Field(..., description="'ours' for your site, or a competitor domain")
    brand: str | None = Field(None, description="Brand/company name, used to spot branded anchors")
    keywords: list[str] = Field(default_factory=list, description="Target keywords, used to spot exact-match anchors")
    csv: str = Field(..., description="CSV text with columns: anchor_text, backlink_url, dr")


class InternalImportIn(BaseModel):
    project_id: uuid.UUID
    site_label: str = Field(..., description="'ours' for your site, or a competitor domain")
    csv: str = Field(..., description="CSV text with columns: source_url, anchor_text, target_url")


@router.post("/linksintel/backlinks/import")
def import_backlinks(body: BacklinksImportIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    """Import a backlink CSV for your site or one competitor. Replaces that site's previous import."""
    project_guard(db, ctx, body.project_id)
    label = (body.site_label or "").strip()
    if not label:
        raise HTTPException(400, "site_label is required")
    try:
        res = li.import_backlinks_csv(db, ctx.org_id, body.project_id, label, body.csv,
                                      brand=body.brand, keywords=body.keywords)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "linksintel.backlinks_import", f"project:{body.project_id}",
        {"site_label": label, **res})
    return {"ok": True, **res}


@router.get("/linksintel/anchors")
def anchors(project_id: uuid.UUID = Query(...), db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    """Us-vs-them anchor table: our site first, then each competitor + over-optimization warnings."""
    project_guard(db, ctx, project_id)
    return li.anchor_summary(db, project_id)


@router.post("/linksintel/internal/import")
def import_internal(body: InternalImportIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    """Import an internal-links CSV for your site or one competitor. Replaces that site's previous import."""
    project_guard(db, ctx, body.project_id)
    label = (body.site_label or "").strip()
    if not label:
        raise HTTPException(400, "site_label is required")
    try:
        res = li.import_internal_links(db, ctx.org_id, body.project_id, label, body.csv)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "linksintel.internal_import", f"project:{body.project_id}",
        {"site_label": label, **res})
    return {"ok": True, **res}


@router.get("/linksintel/internal")
def internal(project_id: uuid.UUID = Query(...),
             site_label: str | None = Query(None),
             all_pages: str | None = Query(None, description="Newline-separated full page list for orphan detection"),
             db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    """Internal-link rows (source -> anchor -> target) + orphan pages per site."""
    project_guard(db, ctx, project_id)
    pages = [p.strip() for p in (all_pages or "").splitlines() if p.strip()]
    return li.internal_summary(db, project_id, site_label=site_label or None, all_pages=pages)
