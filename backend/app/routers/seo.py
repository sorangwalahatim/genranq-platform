"""SEO Core & Audit (Roadmap Phase 1): sites, keywords, rank tracker, audits, crawl
explorer, PageSpeed, competitor audits, SEO action plans, reports, automation."""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select

from .. import models as m
from ..deps import Ctx, deny_client, get_db, log, project_guard, require, visible_project_ids
from ..security import now
from ..services import audit_engine, entitlements as ent, integrations as ih, reports as rep
from ..services.jobs import enqueue
from ..util import rows, to_dict

router = APIRouter(tags=["seo"])
DOMAIN_RE = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(:\d+)?(/.*)?$|^(https?://)?localhost(:\d+)?(/.*)?$|^(https?://)?127\.0\.0\.1(:\d+)?(/.*)?$", re.I)


def _site(db, ctx, sid) -> m.Site:
    s = db.get(m.Site, sid)
    if not s:
        raise HTTPException(404, "Site not found")
    project_guard(db, ctx, s.project_id)
    return s


def _clean_domain(d: str) -> str:
    d = d.strip().rstrip("/")
    if not DOMAIN_RE.match(d):
        raise HTTPException(400, "Enter a domain like example.com or https://example.com")
    return d


# ------------------------------------------------------------------ sites & competitors
class SiteIn(BaseModel):
    project_id: uuid.UUID
    domain: str
    gsc_property: str | None = None
    ga4_property: str | None = None


@router.post("/sites")
def create_site(body: SiteIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    s = m.Site(organisation_id=ctx.org_id, project_id=body.project_id, domain=_clean_domain(body.domain),
               gsc_property=body.gsc_property, ga4_property=body.ga4_property)
    db.add(s)
    db.flush()
    log(db, ctx, "site.created", f"site:{s.id}", {"domain": s.domain})
    return to_dict(s)


@router.patch("/sites/{sid}")
def patch_site(sid: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    s = _site(db, ctx, sid)
    for k in ("gsc_property", "ga4_property"):
        if k in body:
            setattr(s, k, body[k] or None)
    if body.get("domain"):
        s.domain = _clean_domain(body["domain"])
    return to_dict(s)


@router.delete("/sites/{sid}")
def delete_site(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    s = _site(db, ctx, sid)
    log(db, ctx, "site.deleted", f"site:{s.id}", {"domain": s.domain})
    db.delete(s)
    return {"ok": True}


@router.post("/competitors")
def add_competitor(body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, uuid.UUID(body["project_id"]))
    c = m.Competitor(organisation_id=ctx.org_id, project_id=body["project_id"], domain=_clean_domain(body["domain"]))
    db.add(c)
    db.flush()
    return to_dict(c)


@router.delete("/competitors/{cid}")
def del_competitor(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    c = db.get(m.Competitor, cid)
    if c:
        project_guard(db, ctx, c.project_id)
        db.delete(c)
    return {"ok": True}


# ------------------------------------------------------------------ keywords & rank tracker
class KeywordsIn(BaseModel):
    project_id: uuid.UUID
    site_id: uuid.UUID | None = None
    phrases: list[str] = Field(min_length=1, max_length=1000)
    country: str = "us"
    lang: str = "en"
    tags: list[str] = []
    frequency: str = "weekly"
    target_url: str | None = None


@router.get("/keywords")
def list_keywords(project_id: uuid.UUID, tag: str | None = None, q: str | None = None,
                  db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    qq = select(m.Keyword).where(m.Keyword.project_id == project_id)
    if q:
        qq = qq.where(m.Keyword.phrase.ilike(f"%{q}%"))
    kws = db.scalars(qq.order_by(m.Keyword.phrase)).all()
    if tag:
        kws = [k for k in kws if tag in (k.tags or [])]
    ids = [k.id for k in kws]
    hist: dict = {}
    if ids:
        for r in db.scalars(select(m.RankHistory).where(m.RankHistory.keyword_id.in_(ids),
                                                         m.RankHistory.checked_at > now() - timedelta(days=90))
                            .order_by(m.RankHistory.checked_at)):
            hist.setdefault(r.keyword_id, []).append(r)
    out = []
    for k in kws:
        h = hist.get(k.id, [])
        cur = h[-1] if h else None
        prev = next((x for x in reversed(h[:-1]) if x.checked_at < (cur.checked_at - timedelta(days=6))), None) if cur else None
        best = min((x.position for x in h if x.position), default=None)
        change = (prev.position - cur.position) if (cur and prev and cur.position and prev.position) else None
        out.append({**to_dict(k), "position": cur.position if cur else None, "url": cur.url if cur else None,
                    "checked_at": cur.checked_at if cur else None, "source": cur.source if cur else None,
                    "change_7d": change, "best": best,
                    "spark": [{"t": x.checked_at, "p": x.position} for x in h[-30:]]})
    return out


@router.post("/keywords")
def add_keywords(body: KeywordsIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    if body.site_id:
        st = db.get(m.Site, body.site_id)
        if not st or st.project_id != body.project_id:
            raise HTTPException(400, "site_id is not in this project")
    phrases = sorted({p.strip().lower() for p in body.phrases if p.strip()})
    existing = set(db.scalars(select(m.Keyword.phrase).where(m.Keyword.project_id == body.project_id, m.Keyword.country == body.country,
                                                             m.Keyword.lang == body.lang)))
    new = [p for p in phrases if p not in existing]
    total = db.scalar(select(func.count()).select_from(m.Keyword))
    ent.check_count_limit(db, ctx.org_id, "rank_keywords", total, len(new))
    for p in new:
        db.add(m.Keyword(organisation_id=ctx.org_id, project_id=body.project_id, site_id=body.site_id, phrase=p, country=body.country,
                         lang=body.lang, tags=body.tags, frequency=body.frequency, target_url=body.target_url))
    log(db, ctx, "keyword.added", f"project:{body.project_id}", {"count": len(new)})
    return {"added": len(new), "skipped_duplicates": len(phrases) - len(new)}


@router.patch("/keywords/{kid}")
def patch_keyword(kid: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    k = db.get(m.Keyword, kid)
    if not k:
        raise HTTPException(404)
    project_guard(db, ctx, k.project_id)
    for f in ("tags", "target_url", "frequency", "search_volume", "difficulty"):
        if f in body:
            setattr(k, f, body[f])
    return to_dict(k)


@router.delete("/keywords/{kid}")
def delete_keyword(kid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    k = db.get(m.Keyword, kid)
    if k:
        project_guard(db, ctx, k.project_id)
        db.delete(k)
    return {"ok": True}


@router.get("/keywords/{kid}/history")
def keyword_history(kid: uuid.UUID, days: int = 180, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    k = db.get(m.Keyword, kid)
    if not k:
        raise HTTPException(404)
    project_guard(db, ctx, k.project_id)
    h = db.scalars(select(m.RankHistory).where(m.RankHistory.keyword_id == kid, m.RankHistory.checked_at > now() - timedelta(days=days))
                   .order_by(m.RankHistory.checked_at)).all()
    return {"keyword": to_dict(k), "history": rows(h)}


class ManualRank(BaseModel):
    position: int | None = Field(None, ge=1, le=200)
    url: str | None = None
    checked_at: datetime | None = None


@router.post("/keywords/{kid}/positions")
def manual_position(kid: uuid.UUID, body: ManualRank, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    k = db.get(m.Keyword, kid)
    if not k:
        raise HTTPException(404)
    project_guard(db, ctx, k.project_id)
    r = m.RankHistory(organisation_id=ctx.org_id, keyword_id=kid, checked_at=body.checked_at or now(), position=body.position,
                      url=body.url, source="manual")
    db.add(r)
    log(db, ctx, "rank.manual_entry", f"keyword:{kid}", {"position": body.position})
    return {"ok": True}


@router.post("/projects/{pid}/rank-import")
async def rank_import(pid: uuid.UUID, file: UploadFile = File(...), db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    """CSV columns: keyword, position, url (optional), date (optional, ISO). Unknown keywords are added (quota-checked)."""
    project_guard(db, ctx, pid)
    text = (await file.read()).decode("utf-8-sig")
    rdr = csv.DictReader(io.StringIO(text))
    cols = {c.lower().strip(): c for c in (rdr.fieldnames or [])}
    if "keyword" not in cols or "position" not in cols:
        raise HTTPException(400, "CSV needs 'keyword' and 'position' columns")
    kws = {k.phrase: k for k in db.scalars(select(m.Keyword).where(m.Keyword.project_id == pid))}
    data = list(rdr)
    new = {r[cols["keyword"]].strip().lower() for r in data if r[cols["keyword"]].strip()} - set(kws)
    total = db.scalar(select(func.count()).select_from(m.Keyword))
    ent.check_count_limit(db, ctx.org_id, "rank_keywords", total, len(new))
    for p in new:
        k = m.Keyword(organisation_id=ctx.org_id, project_id=pid, phrase=p)
        db.add(k)
        kws[p] = k
    db.flush()
    n = 0
    parsed = []
    for i, r in enumerate(data, 2):
        p = r[cols["keyword"]].strip().lower()
        if not p:
            continue
        pos = (r[cols["position"]] or "").strip()
        when = (r.get(cols["date"]) or "").strip() if "date" in cols else ""
        try:
            pv = int(float(pos)) if pos and pos.lower() not in ("-", "n/a", "") else None
            if pv is not None and not 1 <= pv <= 200:
                raise ValueError("position out of range")
            dt = datetime.fromisoformat(when).replace(tzinfo=timezone.utc) if when else now()
        except ValueError as e:
            raise HTTPException(400, f"Row {i}: {e}")
        parsed.append((p, pv, dt, (r.get(cols["url"]) or None) if "url" in cols else None))
    for p, pv, dt, url in parsed:
        db.add(m.RankHistory(organisation_id=ctx.org_id, keyword_id=kws[p].id, source="import", position=pv, url=url, checked_at=dt))
        n += 1
    log(db, ctx, "rank.imported", f"project:{pid}", {"rows": n, "new_keywords": len(new)})
    return {"rows": n, "new_keywords": len(new)}


@router.post("/projects/{pid}/rank-check")
def rank_check(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, pid)
    conn = ih.connected_map(db)
    if conn.get("gsc") != "CONNECTED" and conn.get("semrush") != "CONNECTED":
        raise HTTPException(409, detail={"code": "not_connected", "message": "No rank data source connected. Connect Google Search "
                                         "Console or Semrush in Integrations, or enter positions manually / import a CSV."})
    j = enqueue(db, ctx.org_id, "rank_check", {"project_id": str(pid)}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


@router.get("/projects/{pid}/rank-summary")
def rank_summary(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    kws = list_keywords(pid, None, None, db, ctx)
    pos = [k["position"] for k in kws if k["position"]]
    buckets = {"1-3": 0, "4-10": 0, "11-20": 0, "21-50": 0, "51-100": 0, "not ranking": 0}
    for k in kws:
        p = k["position"]
        b = ("not ranking" if not p else "1-3" if p <= 3 else "4-10" if p <= 10 else "11-20" if p <= 20 else "21-50" if p <= 50 else "51-100")
        buckets[b] += 1
    movers = sorted([k for k in kws if k["change_7d"]], key=lambda k: -abs(k["change_7d"]))[:10]
    # daily average position trend (90d)
    ids = [uuid.UUID(str(k["id"])) for k in kws]
    trend = []
    if ids:
        day = func.date_trunc("day", m.RankHistory.checked_at)
        trend = [{"day": d, "avg": round(float(a), 1), "n": n} for d, a, n in db.execute(
            select(day, func.avg(m.RankHistory.position), func.count()).where(m.RankHistory.keyword_id.in_(ids), m.RankHistory.position.isnot(None))
            .group_by(day).order_by(day)).all()]
    return {"keywords": len(kws), "ranking": len(pos), "avg_position": round(sum(pos) / len(pos), 1) if pos else None,
            "top10": sum(1 for p in pos if p <= 10), "distribution": buckets,
            "movers": [{"phrase": k["phrase"], "position": k["position"], "change": k["change_7d"]} for k in movers],
            "trend": trend}


# ------------------------------------------------------------------ audits
class AuditIn(BaseModel):
    max_pages: int = Field(250, ge=1, le=10000)
    competitor_id: uuid.UUID | None = None


@router.post("/sites/{sid}/audits")
def start_audit(sid: uuid.UUID, body: AuditIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    s = _site(db, ctx, sid)
    if body.competitor_id:
        c = db.get(m.Competitor, body.competitor_id)
        if not c or c.project_id != s.project_id:
            raise HTTPException(400, "Competitor not in this project")
    running = db.scalar(select(func.count()).select_from(m.AuditRun).where(m.AuditRun.site_id == sid, m.AuditRun.status.in_(("QUEUED", "RUNNING")),
                                                                            m.AuditRun.competitor_id.is_(body.competitor_id) if body.competitor_id is None
                                                                            else m.AuditRun.competitor_id == body.competitor_id))
    if running:
        raise HTTPException(409, "An audit for this site is already queued or running")
    granted = ent.consume(db, ctx.org_id, "audit_pages", body.max_pages, allow_partial=True)
    ar = m.AuditRun(organisation_id=ctx.org_id, site_id=sid, competitor_id=body.competitor_id, max_pages=granted,
                    summary={"reserved_pages": granted}, triggered_by="manual")
    db.add(ar)
    db.flush()
    j = enqueue(db, ctx.org_id, "audit", {"audit_run_id": str(ar.id)}, created_by=ctx.user_id, max_attempts=2)
    log(db, ctx, "audit.started", f"audit_run:{ar.id}", {"site": s.domain, "max_pages": granted})
    return {**to_dict(ar), "job_id": str(j.id), "pages_granted": granted, "pages_requested": body.max_pages}


@router.get("/audits")
def list_audits(site_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    vis = select(m.Site.id).where(m.Site.project_id.in_(visible_project_ids(db, ctx)))
    q = select(m.AuditRun, m.Site).join(m.Site).where(m.AuditRun.site_id.in_(vis))
    if site_id:
        q = q.where(m.AuditRun.site_id == site_id)
    if project_id:
        q = q.where(m.Site.project_id == project_id)
    comps = {c.id: c.domain for c in db.scalars(select(m.Competitor))}
    out = []
    for a, s in db.execute(q.order_by(desc(m.AuditRun.created_at)).limit(200)).all():
        job = db.scalar(select(m.Job).where(m.Job.kind == "audit", m.Job.payload["audit_run_id"].astext == str(a.id)).order_by(desc(m.Job.created_at)).limit(1))
        out.append({**to_dict(a), "domain": comps.get(a.competitor_id) or s.domain, "site_domain": s.domain, "project_id": s.project_id,
                    "is_competitor": bool(a.competitor_id), "progress": job.progress if job else None, "job_status": job.status if job else None,
                    "job_error": job.error if job else None})
    return out


@router.get("/audits/{aid}")
def get_audit(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    a = db.get(m.AuditRun, aid)
    if not a:
        raise HTTPException(404)
    s = _site(db, ctx, a.site_id)
    res = db.execute(select(m.AuditResult, m.AuditChecklistItem).join(m.AuditChecklistItem, m.AuditResult.checklist_item_id == m.AuditChecklistItem.id)
                     .where(m.AuditResult.audit_run_id == aid).order_by(m.AuditChecklistItem.code)).all()
    issues = db.scalars(select(m.AuditIssue).where(m.AuditIssue.audit_run_id == aid).order_by(desc(m.AuditIssue.priority_score))).all()
    prev = db.scalar(select(m.AuditRun).where(m.AuditRun.site_id == a.site_id, m.AuditRun.status == "COMPLETED", m.AuditRun.id != a.id,
                                              m.AuditRun.competitor_id.is_(a.competitor_id) if a.competitor_id is None else m.AuditRun.competitor_id == a.competitor_id,
                                              m.AuditRun.created_at < a.created_at).order_by(desc(m.AuditRun.created_at)).limit(1))
    job = db.scalar(select(m.Job).where(m.Job.kind == "audit", m.Job.payload["audit_run_id"].astext == str(a.id)).order_by(desc(m.Job.created_at)).limit(1))
    plan = db.scalar(select(m.SeoPlan).where(m.SeoPlan.audit_run_id == aid).order_by(desc(m.SeoPlan.created_at)).limit(1))
    comp = db.get(m.Competitor, a.competitor_id) if a.competitor_id else None
    return {
        **to_dict(a), "domain": comp.domain if comp else s.domain, "site": to_dict(s),
        "job": to_dict(job) if job else None,
        "previous": {"id": str(prev.id), "score": float(prev.score) if prev.score is not None else None, "finished_at": prev.finished_at,
                     "category_scores": prev.category_scores} if prev else None,
        "results": [{**to_dict(r), "code": it.code, "category": it.category, "check": it.check, "data_source": it.data_source,
                     "auto": it.auto, "severity": it.severity, "owner": it.default_owner, "recommendation": it.recommendation} for r, it in res],
        "issues": rows(issues),
        "plan_id": str(plan.id) if plan else None,
    }


@router.get("/audits/{aid}/pages")
def audit_pages(aid: uuid.UUID, q: str | None = None, status: int | None = None, limit: int = Query(200, le=1000), offset: int = 0,
                db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    a = db.get(m.AuditRun, aid)
    if not a:
        raise HTTPException(404)
    _site(db, ctx, a.site_id)
    qq = select(m.CrawlPage).where(m.CrawlPage.crawl_run_id == a.crawl_run_id)
    if q:
        qq = qq.where(m.CrawlPage.url.ilike(f"%{q}%"))
    if status:
        qq = qq.where(m.CrawlPage.status_code == status)
    total = db.scalar(select(func.count()).select_from(qq.subquery()))
    pages = db.scalars(qq.order_by(m.CrawlPage.depth, m.CrawlPage.url).limit(limit).offset(offset)).all()
    keep = ("title", "meta_description", "h1", "word_count", "inlinks", "outlinks", "canonical", "noindex", "elapsed", "schema_types", "source")
    return {"total": total, "pages": [{"url": p.url, "status": p.status_code, "depth": p.depth,
                                       **{k: p.data.get(k) for k in keep}} for p in pages]}


@router.get("/audits/{aid}/compare/{other}")
def compare(aid: uuid.UUID, other: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    """Competitor audit (module 58): side-by-side scores and per-check grades."""
    a, b = get_audit(aid, db, ctx), get_audit(other, db, ctx)
    gb = {r["code"]: r for r in b["results"]}
    return {"a": {"id": a["id"], "domain": a["domain"], "score": a["score"], "category_scores": a["category_scores"], "summary": a["summary"]},
            "b": {"id": b["id"], "domain": b["domain"], "score": b["score"], "category_scores": b["category_scores"], "summary": b["summary"]},
            "checks": [{"code": r["code"], "check": r["check"], "category": r["category"], "a": r["grade"], "b": gb.get(r["code"], {}).get("grade"),
                        "a_count": r["count"], "b_count": gb.get(r["code"], {}).get("count")} for r in a["results"]]}


# ------------------------------------------------------------------ action plans (F-049)
@router.get("/action-plans")
def list_plans(project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(m.SeoPlan, m.AuditRun, m.Site).join(m.AuditRun, m.SeoPlan.audit_run_id == m.AuditRun.id).join(m.Site, m.AuditRun.site_id == m.Site.id) \
        .where(m.SeoPlan.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        q = q.where(m.SeoPlan.project_id == project_id)
    return [{**to_dict(p, exclude=("items",)), "domain": s.domain, "score": float(a.score) if a.score is not None else None,
             "item_count": len(p.items), "buckets": {b: sum(1 for i in p.items if i["bucket"] == b) for b in ("Now (week 1-2)", "Next (week 3-6)", "Later")}}
            for p, a, s in db.execute(q.order_by(desc(m.SeoPlan.created_at)).limit(100)).all()]


@router.get("/action-plans/{plid}")
def get_plan(plid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = db.get(m.SeoPlan, plid)
    if not p:
        raise HTTPException(404)
    project_guard(db, ctx, p.project_id)
    a = db.get(m.AuditRun, p.audit_run_id)
    s = db.get(m.Site, a.site_id)
    return {**to_dict(p), "domain": s.domain, "score": float(a.score) if a.score is not None else None}


@router.post("/action-plans/{plid}/tasks")
def plan_tasks(plid: uuid.UUID, body: dict | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = db.get(m.SeoPlan, plid)
    if not p:
        raise HTTPException(404)
    project_guard(db, ctx, p.project_id)
    if p.tasks_created:
        raise HTTPException(409, "Tasks already created from this plan")
    n = audit_engine.plan_to_tasks(db, ctx.org_id, p, ctx.user_id, (body or {}).get("limit"))
    log(db, ctx, "seo_plan.tasks_created", f"seo_plan:{p.id}", {"tasks": n})
    return {"created": n}


# ------------------------------------------------------------------ pagespeed (module 55)
@router.post("/sites/{sid}/pagespeed")
def run_psi(sid: uuid.UUID, body: dict | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    _site(db, ctx, sid)
    if not ih.is_connected(db, "pagespeed"):
        raise HTTPException(409, detail={"code": "not_connected", "message": "Google PageSpeed Insights is NOT CONNECTED — add an API key in Integrations."})
    j = enqueue(db, ctx.org_id, "pagespeed", {"site_id": str(sid), "urls": (body or {}).get("urls")}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


@router.get("/sites/{sid}/pagespeed")
def psi_history(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    _site(db, ctx, sid)
    return {"connected": ih.is_connected(db, "pagespeed"),
            "results": rows(db.scalars(select(m.PagespeedResult).where(m.PagespeedResult.site_id == sid).order_by(desc(m.PagespeedResult.checked_at)).limit(200)))}


# ------------------------------------------------------------------ reports (F-010)
class ReportIn(BaseModel):
    audit_run_id: uuid.UUID
    format: str = "pdf"
    white_label: bool = False


@router.post("/reports")
def create_report(body: ReportIn, db=Depends(get_db), ctx: Ctx = Depends(require("export"))):
    if body.format not in ("pdf", "xlsx"):
        raise HTTPException(400, "format must be pdf or xlsx")
    ent.require_feature(db, ctx.org_id, "report_export")
    if body.white_label:
        ent.require_feature(db, ctx.org_id, "white_label")
    a = db.get(m.AuditRun, body.audit_run_id)
    if not a or a.status != "COMPLETED":
        raise HTTPException(400, "Audit is not completed")
    _site(db, ctx, a.site_id)
    r = m.Report(organisation_id=ctx.org_id, audit_run_id=a.id, format=body.format, white_label=body.white_label)
    db.add(r)
    db.flush()
    j = enqueue(db, ctx.org_id, "report", {"report_id": str(r.id)}, created_by=ctx.user_id)
    log(db, ctx, "report.requested", f"report:{r.id}", {"format": body.format})
    return {**to_dict(r), "job_id": str(j.id)}


@router.get("/reports")
def list_reports(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    vis = select(m.AuditRun.id).join(m.Site).where(m.Site.project_id.in_(visible_project_ids(db, ctx)))
    out = []
    for r in db.scalars(select(m.Report).where(m.Report.audit_run_id.in_(vis)).order_by(desc(m.Report.created_at)).limit(200)):
        f = db.get(m.StoredFile, r.stored_file_id) if r.stored_file_id else None
        a = db.get(m.AuditRun, r.audit_run_id)
        out.append({**to_dict(r), "filename": f.filename if f else None, "size_bytes": f.size_bytes if f else None,
                    "domain": db.get(m.Site, a.site_id).domain if a else None})
    return out


@router.get("/reports/{rid}/download")
def download_report(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("export"))):
    r = db.get(m.Report, rid)
    if not r or not r.stored_file_id:
        raise HTTPException(404, "Report not ready")
    a = db.get(m.AuditRun, r.audit_run_id)
    _site(db, ctx, a.site_id)
    f = db.get(m.StoredFile, r.stored_file_id)
    log(db, ctx, "report.downloaded", f"report:{r.id}")
    return FileResponse(rep.path_of(f), media_type=f.content_type, filename=f.filename)


# ------------------------------------------------------------------ automation (module 34)
class ScheduleIn(BaseModel):
    kind: str  # scheduled_audit / scheduled_rank_check
    frequency: str  # daily/weekly/monthly
    site_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    max_pages: int = Field(250, ge=10, le=10000)
    first_run_at: datetime | None = None


@router.get("/automation/schedules")
def list_schedules(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    sites = {s.id: s.domain for s in db.scalars(select(m.Site))}
    projects = {p.id: p.name for p in db.scalars(select(m.Project))}
    out = []
    for s in db.scalars(select(m.JobSchedule).order_by(m.JobSchedule.next_run_at)):
        d = to_dict(s)
        d["target"] = sites.get(uuid.UUID(s.payload["site_id"])) if s.payload.get("site_id") else projects.get(uuid.UUID(s.payload.get("project_id", str(uuid.UUID(int=0)))))
        out.append(d)
    return {"schedules": out, "automated_audits_included": ent.feature(db, ctx.org_id, "scheduled_audits").get("enabled")}


@router.post("/automation/schedules")
def create_schedule(body: ScheduleIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    if body.frequency not in ("daily", "weekly", "monthly"):
        raise HTTPException(400, "frequency must be daily/weekly/monthly")
    if body.kind == "scheduled_audit":
        ent.require_feature(db, ctx.org_id, "scheduled_audits")
        if not body.site_id:
            raise HTTPException(400, "site_id required")
        _site(db, ctx, body.site_id)
        payload = {"site_id": str(body.site_id), "max_pages": body.max_pages}
    elif body.kind == "scheduled_rank_check":
        if not body.project_id:
            raise HTTPException(400, "project_id required")
        project_guard(db, ctx, body.project_id)
        payload = {"project_id": str(body.project_id)}
    else:
        raise HTTPException(400, "Unsupported schedule kind")
    s = m.JobSchedule(organisation_id=ctx.org_id, kind=body.kind, payload=payload, frequency=body.frequency,
                      next_run_at=body.first_run_at or now() + timedelta(minutes=1), created_by=ctx.user_id)
    db.add(s)
    db.flush()
    log(db, ctx, "automation.schedule_created", f"job_schedule:{s.id}", {"kind": body.kind, "frequency": body.frequency})
    return to_dict(s)


@router.patch("/automation/schedules/{sid}")
def toggle_schedule(sid: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    s = db.get(m.JobSchedule, sid)
    if not s:
        raise HTTPException(404)
    if "enabled" in body:
        s.enabled = bool(body["enabled"])
    if body.get("frequency") in ("daily", "weekly", "monthly"):
        s.frequency = body["frequency"]
    return to_dict(s)


@router.delete("/automation/schedules/{sid}")
def delete_schedule(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    s = db.get(m.JobSchedule, sid)
    if s:
        db.delete(s)
    return {"ok": True}


# ------------------------------------------------------------------ checklist catalogue (module 57)
@router.get("/checklist")
def checklist(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return rows(db.scalars(select(m.AuditChecklistItem).order_by(m.AuditChecklistItem.code)))
