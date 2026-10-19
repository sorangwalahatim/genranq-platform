"""AI Engine & Studio API (Roadmap Phase 4): content studio generators with the content state machine,
AI provider hub, prompt library, agent teams, AI cost & usage, topical map and content plan builder."""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from .. import models as m
from .. import models_ai as ma
from ..deps import Ctx, deny_client, get_db, log, project_guard, require, visible_project_ids
from ..services import agents, ai_content as ac, ai_engine as eng, ai_exports as ax, ai_images as aimg
from ..services import ai_prompts as prompts, ai_studio as studio, ai_teams as teams, ai_topical as topical
from ..services import integrations as ih, reports, skills
from ..services.jobs import enqueue
from ..util import to_dict

router = APIRouter(tags=["ai"])
URL_RX = re.compile(r"^https?://[\w.-]+(:\d+)?(/\S*)?$")


def _url(u: str | None) -> str | None:
    if u in (None, ""):
        return None
    u = u.strip()
    if not URL_RX.match(u) or len(u) > 2000:
        raise HTTPException(400, f"Invalid URL: {u[:100]}")
    return u


def _item(db, ctx, iid) -> m.ContentItem:
    it = db.get(m.ContentItem, iid)
    if not it:
        raise HTTPException(404, "Content item not found")
    project_guard(db, ctx, it.project_id)
    return it


def _editable(it):
    if it.state not in ac.EDITABLE:
        raise HTTPException(409, f"Content in '{it.state}' is locked — reject it back to draft to edit")


def _ai_status(db) -> dict:
    conn = ih.connected_map(db)
    ai = [p for p in eng.PROVIDERS if conn.get(p) in ("CONNECTED", "RATE_LIMITED")]
    return {"connected": bool(ai), "providers": ai, "image_connected": any(p in ai for p in ("openai", "gemini")),
            "message": None if ai else "No AI provider connected (Claude, OpenAI, Gemini or Perplexity) — NOT CONNECTED. "
                                       "Data briefs, outlines, schema and exports still work; AI drafting needs a provider."}


@router.get("/ai/status")
def ai_status(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return {**_ai_status(db), "budget": eng.budget_status(db, ctx.org_id) if ctx.role != "client" else None}


# =================================================================== content items (state machine)
def item_view(db, it: m.ContentItem, full: bool = False) -> dict:
    mt = db.get(ma.ContentMeta, it.id)
    d = to_dict(it)
    d.update({"generator": mt.generator if mt else None, "current_version": mt.current_version if mt else 0,
              "notes": mt.notes if mt else None, "updated_at": mt.updated_at if mt else it.created_at})
    data = (mt.data if mt else {}) or {}
    d["seo"] = data.get("seo")
    d["generation"] = {k: v for k, v in (data.get("generation") or {}).items() if k != "raw"} or None
    if full:
        lv = ac.latest_version(db, it)
        d["data"] = data
        d["body"] = lv.body if lv else ""
        d["versions"] = ac.versions(db, it)
        d["transitions"] = [to_dict(t) for t in db.scalars(select(ma.ContentTransition).where(ma.ContentTransition.content_item_id == it.id)
                                                            .order_by(ma.ContentTransition.at))]
        appr = db.get(m.Approval, mt.approval_id) if mt and mt.approval_id else None
        d["approval"] = to_dict(appr) if appr else None
        d["allowed"] = [{"to": b, "action": a, "permission": p} for (src, b), (a, p) in ac.TRANSITIONS.items() if src == it.state]
        d["schema"] = studio.schema_for(db, it)
        d["schema_errors"] = ac.validate_schema(d["schema"])
    return d


@router.get("/studio/items")
def list_items(project_id: uuid.UUID | None = None, type: str | None = None, state: str | None = None,
               db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(m.ContentItem).where(m.ContentItem.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        project_guard(db, ctx, project_id)
        q = q.where(m.ContentItem.project_id == project_id)
    if type:
        q = q.where(m.ContentItem.type.in_(type.split(",")))
    if state:
        q = q.where(m.ContentItem.state.in_(state.split(",")))
    items = db.scalars(q.order_by(m.ContentItem.created_at.desc()).limit(500)).all()
    for it in items:
        ac.sync_approval(db, it)
    return [item_view(db, it) for it in items]


class ItemIn(BaseModel):
    project_id: uuid.UUID
    type: str = Field(..., max_length=40)
    title: str = Field(..., min_length=1, max_length=300)
    body: str | None = Field(None, max_length=400000)


@router.post("/studio/items")
def create_item(body: ItemIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    if body.type not in studio.CONTENT_TYPES:
        raise HTTPException(400, f"type must be one of {list(studio.CONTENT_TYPES)}")
    it = studio.new_item(db, ctx.user_id, p, body.type, body.title, {"voice": studio.voice(db, p), "seo": {"meta_title": body.title[:60]}})
    if body.body:
        ac.add_version(db, it, body.body, author_id=ctx.user_id, origin="manual")
    log(db, ctx, "content.created", f"content_item:{it.id}", {"type": body.type})
    return item_view(db, it, True)


@router.get("/studio/items/{iid}")
def get_item(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    it = _item(db, ctx, iid)
    ac.sync_approval(db, it)
    return {**item_view(db, it, True), "ai": _ai_status(db)}


class ItemPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    seo: dict | None = None
    faqs: list[dict] | None = Field(None, max_length=40)
    outline: list[dict] | None = Field(None, max_length=60)
    supporting_keywords: list[str] | None = Field(None, max_length=50)
    word_count: int | None = Field(None, ge=100, le=10000)
    sections: list[str] | None = Field(None, max_length=20)  # whitepaper section titles
    brief_notes: str | None = Field(None, max_length=5000)


@router.patch("/studio/items/{iid}")
def patch_item(iid: uuid.UUID, body: ItemPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    _editable(it)
    mt = ac.meta_of(db, it)
    d = dict(mt.data or {})
    upd = body.model_dump(exclude_unset=True)
    if "title" in upd:
        it.title = upd.pop("title")
    if "seo" in upd:
        s = {k: str(v)[:400] for k, v in (upd.pop("seo") or {}).items() if k in ("meta_title", "meta_description", "slug") and v is not None}
        if len(s.get("meta_title", "")) > 70:
            raise HTTPException(400, "Meta title must be at most 70 characters")
        if len(s.get("meta_description", "")) > 170:
            raise HTTPException(400, "Meta description must be at most 170 characters")
        d["seo"] = {**(d.get("seo") or {}), **s}
    if "faqs" in upd:
        d["faqs"] = [{"question": str(f.get("question"))[:300], "answer": (str(f["answer"])[:2000] if f.get("answer") else None)}
                     for f in upd.pop("faqs") or [] if f.get("question")]
    if "outline" in upd:
        d["outline"] = [{"h2": str(o.get("h2"))[:200], "h3": [str(x)[:200] for x in (o.get("h3") or [])][:20]} for o in upd.pop("outline") or [] if o.get("h2")]
    d.update({k: v for k, v in upd.items()})
    mt.data = d
    log(db, ctx, "content.updated", f"content_item:{it.id}", {"fields": list(body.model_dump(exclude_unset=True))})
    return item_view(db, it, True)


class BodyIn(BaseModel):
    body: str = Field(..., max_length=400000)
    note: str | None = Field(None, max_length=500)


@router.put("/studio/items/{iid}/body")
def put_body(iid: uuid.UUID, body: BodyIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    _editable(it)
    if it.type == "social":
        raise HTTPException(400, "Edit social posts with the post editor so the platform limit is enforced")
    lv = ac.latest_version(db, it)
    if lv and lv.body == body.body:
        raise HTTPException(409, "No changes since the latest version")
    mt = ac.meta_of(db, it)
    if it.type == "whitepaper":
        mt.data = {**(mt.data or {}), "section_bodies": studio.sections_from_markdown(body.body)}
    v = ac.add_version(db, it, body.body, author_id=ctx.user_id, origin="manual", note=body.note)
    log(db, ctx, "content.version", f"content_item:{it.id}", {"version": v.version})
    return {"version": v.version, "item": item_view(db, it, True)}


@router.get("/studio/items/{iid}/versions")
def list_versions(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return ac.versions(db, _item(db, ctx, iid))


@router.get("/studio/items/{iid}/versions/{n}")
def get_version(iid: uuid.UUID, n: int, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    v, vm = ac.get_version(db, _item(db, ctx, iid), n)
    return {"version": v.version, "body": v.body, "created_at": v.created_at, "origin": vm.origin if vm else None,
            "model": vm.model if vm else None, "note": vm.note if vm else None, "data": vm.data if vm else {}}


@router.get("/studio/items/{iid}/diff")
def diff(iid: uuid.UUID, a: int, b: int, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    it = _item(db, ctx, iid)
    va, _ = ac.get_version(db, it, a)
    vb, _ = ac.get_version(db, it, b)
    return {"a": a, "b": b, **ac.diff(va.body or "", vb.body or "", f"v{a}", f"v{b}")}


@router.post("/studio/items/{iid}/versions/{n}/restore")
def restore(iid: uuid.UUID, n: int, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    _editable(it)
    v, vm = ac.get_version(db, it, n)
    mt = ac.meta_of(db, it)
    if vm and vm.data:
        mt.data = vm.data
    nv = ac.add_version(db, it, v.body or "", author_id=ctx.user_id, origin="restore", note=f"Restored from v{n}", data=vm.data if vm else None)
    log(db, ctx, "content.restored", f"content_item:{it.id}", {"from": n, "version": nv.version})
    return {"version": nv.version}


class TransitionIn(BaseModel):
    to: Literal["draft", "generated", "review", "approved", "rejected", "scheduled", "published"]
    note: str | None = Field(None, max_length=4000)


@router.post("/studio/items/{iid}/transition")
def do_transition(iid: uuid.UUID, body: TransitionIn, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    it = _item(db, ctx, iid)
    ac.sync_approval(db, it)
    ac.transition(db, ctx, it, body.to, body.note)
    return item_view(db, it, True)


class GenerateIn(BaseModel):
    instructions: str | None = Field(None, max_length=4000)


@router.post("/studio/items/{iid}/generate")
def generate(iid: uuid.UUID, body: GenerateIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    _editable(it)
    prompt, info = studio.prompt_values(db, it, (ac.meta_of(db, it).data or {}), body.instructions)
    j = enqueue(db, ctx.org_id, "ai_generate", {"item_id": str(it.id), "user_id": str(ctx.user_id), "instructions": body.instructions},
                created_by=ctx.user_id, max_attempts=2)
    log(db, ctx, "content.generate", f"content_item:{it.id}", {"prompt": info.get("key")})
    return {"job_id": str(j.id), "ai": _ai_status(db), "prompt": prompt, "prompt_info": info}


@router.get("/studio/items/{iid}/prompt")
def item_prompt(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    it = _item(db, ctx, iid)
    prompt, info = studio.prompt_values(db, it, (ac.meta_of(db, it).data or {}))
    return {"prompt": prompt, "info": info}


@router.get("/studio/items/{iid}/schema")
def item_schema(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    it = _item(db, ctx, iid)
    nodes = studio.schema_for(db, it)
    return {"schema": nodes, "errors": ac.validate_schema(nodes)}


def _export_payload(db, it):
    mt = ac.meta_of(db, it)
    d = mt.data or {}
    lv = ac.latest_version(db, it)
    body = lv.body if lv else ""
    if it.type in ("web_page", "landing") and not body:
        body = studio.page_markdown(d)
    project = db.get(m.Project, it.project_id)
    org = db.get(m.Organisation, it.organisation_id)
    brand = skills.get_profile(db, project).get("brand") or org.name
    return d, body, brand, (org.branding or {}).get("primary_color") or "#1F3A5F"


@router.get("/studio/items/{iid}/export")
def export(iid: uuid.UUID, format: Literal["html", "md", "pdf", "docx"] = "html", draft: bool = False,
           db=Depends(get_db), ctx: Ctx = Depends(require("export"))):
    it = _item(db, ctx, iid)
    ready = it.state in ("approved", "scheduled", "published")
    if not ready and not draft:
        raise HTTPException(409, "Publish-ready export needs approved content — submit it for review, or export with draft=true for a marked draft")
    d, body, brand, color = _export_payload(db, it)
    if not body.strip():
        raise HTTPException(409, "Nothing to export yet")
    seo = d.get("seo") or {}
    schema = studio.schema_for(db, it)
    fn = ac.slugify(seo.get("slug") or it.title or "content")[:60]
    log(db, ctx, "content.exported", f"content_item:{it.id}", {"format": format, "draft": not ready})
    if format == "html":
        doc = ac.html_document(it.title or "", ac.md_to_html(body), seo, schema, draft=not ready, canonical=d.get("url"))
        return Response(doc, media_type="text/html; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{fn}.html"'})
    if format == "md":
        return Response(ac.markdown_export(it.title or "", body, seo, schema), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fn}.md"'})
    if format == "pdf":
        data = ax.pdf(it.title or "", body, brand=brand, subtitle=d.get("subtitle") or d.get("goal"),
                      charts=d.get("charts") if it.type == "whitepaper" else None, chart_section=d.get("chart_section"), primary=color)
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fn}.pdf"'})
    data = ax.docx(it.title or "", body, brand=brand, subtitle=d.get("subtitle") or d.get("goal"))
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{fn}.docx"'})


@router.get("/studio/items/{iid}/preview")
def preview(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    it = _item(db, ctx, iid)
    d, body, brand, color = _export_payload(db, it)
    if it.type in ("web_page", "landing"):
        parts = [f"<h1>{ac.html.escape(d.get('h1') or it.title or '')}</h1>"]
        for s in d.get("sections") or []:
            if s["key"] == "faq":
                continue
            parts.append(f"<section data-key=\"{s['key']}\"><h2>{ac.html.escape(s['heading'])}</h2>"
                         + (ac.md_to_html(s["body"]) if s.get("body") else "<p style=\"color:#8a93a3\">[section copy not written yet]</p>") + "</section>")
        faqs = [f for f in d.get("faqs") or [] if f.get("question")]
        if faqs:
            parts.append("<section><h2>FAQ</h2>" + "".join(f"<h3>{ac.html.escape(f['question'])}</h3>"
                                                            + (f"<p>{ac.html.escape(f['answer'])}</p>" if f.get("answer") else "<p style=\"color:#8a93a3\">[answer to write]</p>")
                                                            for f in faqs) + "</section>")
        parts.append(f"<p><a class=\"cta\" href=\"#\" style=\"background:{ac.html.escape(color)};color:#fff;padding:12px 18px;border-radius:6px;text-decoration:none\">"
                     f"{ac.html.escape(d.get('cta') or 'Contact us')}</a></p>")
        inner = "\n".join(parts)
    else:
        inner = ac.md_to_html(body) if body else "<p style=\"color:#8a93a3\">Nothing written yet.</p>"
    style = ("<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;line-height:1.6;color:#14213d}"
             f"h1,h2{{color:{ac.html.escape(color)}}}section{{margin:24px 0}}</style>")
    doc = ac.html_document(it.title or "", style + inner, d.get("seo") or {}, studio.schema_for(db, it), draft=it.state not in ("approved", "published"))
    return Response(doc, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


@router.delete("/studio/items/{iid}")
def delete_item(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    it = _item(db, ctx, iid)
    if it.state in ("published", "scheduled"):
        raise HTTPException(409, f"{it.state.capitalize()} content can't be deleted")
    log(db, ctx, "content.deleted", f"content_item:{it.id}", {"title": it.title, "type": it.type})
    db.delete(it)
    return {"ok": True}


@router.get("/studio/overview")
def overview(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    return {"counts": studio.by_project_counts(db, project_id), "types": {k: v["label"] for k, v in studio.CONTENT_TYPES.items()},
            "ai": _ai_status(db)}


# =================================================================== 24 blog
@router.get("/studio/blog/sources")
def blog_sources(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, project_id)
    return studio.blog_sources(db, p)


class BlogIn(BaseModel):
    project_id: uuid.UUID
    keyword: str = Field(..., min_length=2, max_length=200)
    title: str | None = Field(None, max_length=200)
    supporting_keywords: list[str] | None = Field(None, max_length=30)
    word_count: int = Field(1500, ge=300, le=6000)
    notes: str | None = Field(None, max_length=4000)
    use_agent_brief: bool = True


@router.post("/studio/blog")
def create_blog(body: BlogIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    it = studio.create_blog(db, ctx.user_id, p, body.keyword, body.title, [s[:200] for s in body.supporting_keywords] if body.supporting_keywords is not None else None,
                            body.word_count, body.notes, body.use_agent_brief)
    log(db, ctx, "content.created", f"content_item:{it.id}", {"type": "blog", "keyword": body.keyword})
    return item_view(db, it, True)


# =================================================================== 25 news
@router.get("/studio/news/topics")
def news_topics(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, project_id)
    return {"topics": studio.news_topics(db, p), "has_search_data": bool(skills.gsc_queries(db, p, limit=1))}


class SourceIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=120)
    url: str = Field(..., max_length=2000)


@router.get("/studio/news/sources")
def news_sources(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    return [to_dict(s) for s in db.scalars(select(ma.NewsSource).where(ma.NewsSource.project_id == project_id).order_by(ma.NewsSource.name))]


@router.post("/studio/news/sources")
def add_news_source(body: SourceIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    project_guard(db, ctx, body.project_id)
    u = _url(body.url)
    if db.scalar(select(ma.NewsSource.id).where(ma.NewsSource.project_id == body.project_id, ma.NewsSource.url == u)):
        raise HTTPException(409, "That source is already added")
    s = ma.NewsSource(organisation_id=ctx.org_id, project_id=body.project_id, name=body.name, url=u)
    db.add(s)
    db.flush()
    return to_dict(s)


@router.delete("/studio/news/sources/{sid}")
def del_news_source(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    s = db.get(ma.NewsSource, sid)
    if not s:
        raise HTTPException(404)
    project_guard(db, ctx, s.project_id)
    db.delete(s)
    return {"ok": True}


@router.get("/studio/news/sources/{sid}/headlines")
def source_headlines(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    s = db.get(ma.NewsSource, sid)
    if not s:
        raise HTTPException(404)
    project_guard(db, ctx, s.project_id)
    return studio.source_headlines(s.url)


class NewsIn(BaseModel):
    project_id: uuid.UUID
    headline: str = Field(..., min_length=3, max_length=200)
    angle: str | None = Field(None, max_length=1000)
    source_urls: list[str] = Field(default_factory=list, max_length=5)
    query: str | None = Field(None, max_length=300)


@router.post("/studio/news")
def create_news(body: NewsIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    urls = [_url(u) for u in body.source_urls if u]
    it = studio.create_news(db, ctx.user_id, p, body.headline, body.angle, urls, body.query)
    log(db, ctx, "content.created", f"content_item:{it.id}", {"type": "news"})
    return item_view(db, it, True)


# =================================================================== 26 whitepaper
@router.get("/studio/whitepaper/data")
def whitepaper_data(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, project_id)
    charts = studio.project_charts(db, p)
    return {"charts": charts, "summary": studio.data_summary(charts), "default_sections": studio.WP_SECTIONS}


class WhitepaperIn(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=3, max_length=200)
    audience: str | None = Field(None, max_length=300)
    sections: list[str] | None = Field(None, max_length=15)
    include_data: bool = True


@router.post("/studio/whitepaper")
def create_whitepaper(body: WhitepaperIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    secs = [s[:150] for s in body.sections] if body.sections else None
    it = studio.create_whitepaper(db, ctx.user_id, p, body.title, body.audience, secs, body.include_data)
    log(db, ctx, "content.created", f"content_item:{it.id}", {"type": "whitepaper"})
    return item_view(db, it, True)


# =================================================================== 27 publication
class TitlesIn(BaseModel):
    project_id: uuid.UUID
    topic: str | None = Field(None, max_length=200)
    metric: str | None = Field(None, max_length=100)
    count: int = Field(15, ge=1, le=15)


@router.post("/studio/publication/titles")
def publication_titles(body: TitlesIn, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, body.project_id)
    return studio.eeat_titles(db, p, body.topic, body.metric, body.count)


class PublicationIn(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=3, max_length=200)
    subtitle: str | None = Field(None, max_length=400)
    eeat_focus: str | None = Field(None, max_length=40)
    intent: str | None = Field(None, max_length=40)
    persona: str | None = Field(None, max_length=200)
    pain: str | None = Field(None, max_length=200)
    metric: str | None = Field(None, max_length=100)
    keyword: str | None = Field(None, max_length=200)
    experience_proof: str | None = Field(None, max_length=500)
    expertise: str | None = Field(None, max_length=500)
    authority: str | None = Field(None, max_length=500)
    trust: str | None = Field(None, max_length=500)
    seo_variant: str | None = Field(None, max_length=200)


@router.post("/studio/publication")
def create_publication(body: PublicationIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    it = studio.create_publication(db, ctx.user_id, p, body.model_dump(exclude={"project_id"}))
    log(db, ctx, "content.created", f"content_item:{it.id}", {"type": "publication"})
    return item_view(db, it, True)


# =================================================================== 28 playbook
class StepIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    owner: str | None = Field(None, max_length=80)
    details: str | None = Field(None, max_length=4000)
    checklist: list[str] = Field(default_factory=list, max_length=20)


class PlaybookIn(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=3, max_length=200)
    goal: str | None = Field(None, max_length=2000)
    roles: list[str] = Field(default_factory=lambda: ["Owner"], max_length=20)
    steps: list[StepIn] = Field(default_factory=list, max_length=60)


@router.post("/studio/playbook")
def create_playbook(body: PlaybookIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    it = studio.create_playbook(db, ctx.user_id, p, body.title, body.goal, body.roles, [s.model_dump() for s in body.steps])
    log(db, ctx, "content.created", f"content_item:{it.id}", {"type": "playbook"})
    return item_view(db, it, True)


class PlaybookPut(BaseModel):
    purpose: str | None = Field(None, max_length=2000)
    scope: str | None = Field(None, max_length=2000)
    roles: list[str] | None = Field(None, max_length=20)
    steps: list[StepIn] = Field(..., max_length=60)
    kpis: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(None, max_length=500)


@router.put("/studio/items/{iid}/playbook")
def put_playbook(iid: uuid.UUID, body: PlaybookPut, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    if it.type != "playbook":
        raise HTTPException(400, "Not a playbook")
    _editable(it)
    mt = ac.meta_of(db, it)
    d = dict(mt.data or {})
    roles = [r.strip()[:80] for r in (body.roles if body.roles is not None else d.get("roles") or []) if r.strip()] or ["Owner"]
    pb = studio.clean_playbook({**body.model_dump(exclude={"roles", "note"}), "steps": [s.model_dump() for s in body.steps]}, roles)
    unknown = sorted({s["owner"] for s in pb["steps"]} - set(roles))
    if unknown:
        raise HTTPException(400, f"Step owners must be one of the playbook roles {roles}; unknown: {unknown}")
    d.update(playbook=pb, roles=roles)
    mt.data = d
    v = ac.add_version(db, it, studio.playbook_markdown(it.title, pb, roles), author_id=ctx.user_id, origin="manual", note=body.note)
    return {"version": v.version, "item": item_view(db, it, True)}


# =================================================================== 29 / 60 website + landing pages
@router.get("/studio/website/plan")
def website_plan(project_id: uuid.UUID, services: str = "", locations: str = "", db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    p = project_guard(db, ctx, project_id)
    sv = [s.strip()[:120] for s in services.split(",") if s.strip()][:15]
    lc = [s.strip()[:120] for s in locations.split(",") if s.strip()][:25]
    v = studio.voice(db, p)
    return {"pages": studio.plan_website(db, p, sv, lc), "profile_services": (v.get("primary_keywords") or []) + (v.get("secondary_keywords") or []),
            "profile_locations": skills._list(v.get("location"))}


class PageIn(BaseModel):
    page_type: Literal["landing", "service", "location"] = "landing"
    keyword: str = Field(..., min_length=2, max_length=200)
    service: str | None = Field(None, max_length=200)
    location: str | None = Field(None, max_length=200)
    cta: str | None = Field(None, max_length=120)


class PagesIn(BaseModel):
    project_id: uuid.UUID
    pages: list[PageIn] = Field(..., min_length=1, max_length=100)


@router.post("/studio/website/pages")
def create_website_pages(body: PagesIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    out = []
    for pg in body.pages:
        if pg.page_type == "location" and not pg.location:
            raise HTTPException(400, f"Location page '{pg.keyword}' needs a location")
        out.append(studio.create_page(db, ctx.user_id, p, "web_page", pg.page_type, pg.keyword, pg.service, pg.location, pg.cta))
    log(db, ctx, "content.created", f"project:{p.id}", {"type": "web_page", "count": len(out)})
    return [item_view(db, it) for it in out]


class LandingIn(PageIn):
    project_id: uuid.UUID


@router.post("/studio/landing-pages")
def create_landing(body: LandingIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    if body.page_type == "location" and not body.location:
        raise HTTPException(400, "A location page needs a location")
    it = studio.create_page(db, ctx.user_id, p, "landing", body.page_type, body.keyword, body.service, body.location, body.cta)
    log(db, ctx, "content.created", f"content_item:{it.id}", {"type": "landing"})
    return item_view(db, it, True)


class SectionIn(BaseModel):
    key: str = Field(..., max_length=40)
    heading: str = Field(..., max_length=200)
    body: str = Field("", max_length=8000)


class SectionsPut(BaseModel):
    sections: list[SectionIn] = Field(..., max_length=20)
    faqs: list[dict] | None = Field(None, max_length=30)
    h1: str | None = Field(None, max_length=200)
    cta: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=500)


@router.put("/studio/items/{iid}/sections")
def put_sections(iid: uuid.UUID, body: SectionsPut, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    if it.type not in ("web_page", "landing"):
        raise HTTPException(400, "Not a website or landing page")
    _editable(it)
    mt = ac.meta_of(db, it)
    d = dict(mt.data or {})
    known = {s["key"] for s in d.get("sections") or []}
    bad = [s.key for s in body.sections if s.key not in known]
    if bad:
        raise HTTPException(400, f"Unknown sections {bad}")
    d["sections"] = [s.model_dump() for s in body.sections]
    if body.faqs is not None:
        d["faqs"] = [{"question": str(f.get("question"))[:300], "answer": str(f.get("answer"))[:2000] if f.get("answer") else None}
                     for f in body.faqs if f.get("question")]
    if body.h1:
        d["h1"] = body.h1
    if body.cta:
        d["cta"] = body.cta
    mt.data = d
    v = ac.add_version(db, it, studio.page_markdown(d), author_id=ctx.user_id, origin="manual", note=body.note)
    return {"version": v.version, "item": item_view(db, it, True)}


# =================================================================== 30 social posts
@router.get("/studio/social/limits")
def social_limits(ctx: Ctx = Depends(require("view"))):
    return {"limits": studio.PLATFORM_LIMITS, "notes": {"x": "Links count as 23 characters (t.co)"}}


class SocialIn(BaseModel):
    project_id: uuid.UUID
    platforms: list[Literal["x", "linkedin", "instagram", "facebook"]] = Field(..., min_length=1, max_length=4)
    source_item_id: uuid.UUID | None = None
    url: str | None = Field(None, max_length=2000)


@router.post("/studio/social")
def create_social(body: SocialIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    if not body.source_item_id and not body.url:
        raise HTTPException(400, "Choose a source content item or enter a URL")
    try:
        src = studio.social_source(db, p, body.source_item_id, _url(body.url))
    except ValueError as e:
        raise HTTPException(400, str(e))
    items = studio.create_social(db, ctx.user_id, p, list(dict.fromkeys(body.platforms)), src)
    log(db, ctx, "content.created", f"project:{p.id}", {"type": "social", "platforms": body.platforms})
    return [item_view(db, it, True) for it in items]


class SocialPut(BaseModel):
    post: str = Field(..., min_length=1, max_length=63206)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    link: str | None = Field(None, max_length=2000)


@router.put("/studio/items/{iid}/social")
def put_social(iid: uuid.UUID, body: SocialPut, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    if it.type != "social":
        raise HTTPException(400, "Not a social post")
    _editable(it)
    try:
        v = studio.set_social(db, ctx.user_id, it, body.post, body.hashtags, _url(body.link))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"version": v.version, "item": item_view(db, it, True)}


# =================================================================== 32 images
@router.get("/studio/images/sizes")
def image_sizes(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return {"sizes": [{"key": k, "width": w, "height": h, "label": lbl} for k, (w, h, lbl) in aimg.SIZES.items()],
            "ai_connected": bool(aimg.image_providers(db)), "providers": aimg.image_providers(db)}


class CardsIn(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=200)
    subtitle: str | None = Field(None, max_length=200)
    sizes: list[str] = Field(..., min_length=1, max_length=6)
    content_item_id: uuid.UUID | None = None
    primary: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


def _brand(db, p):
    org = db.get(m.Organisation, p.organisation_id)
    return skills.get_profile(db, p).get("brand") or org.name, (org.branding or {}).get("primary_color")


@router.post("/studio/images/cards")
def make_cards(body: CardsIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    bad = [s for s in body.sizes if s not in aimg.SIZES]
    if bad:
        raise HTTPException(400, f"Unknown sizes {bad}")
    if body.content_item_id:
        _item(db, ctx, body.content_item_id)
    brand, color = _brand(db, p)
    out = []
    for sk in dict.fromkeys(body.sizes):
        png = aimg.text_card(body.title, sk, brand=brand, primary=body.primary or color, accent=body.accent, subtitle=body.subtitle)
        f = reports.store(db, ctx.org_id, f"card-{sk}.png", png, "image/png", user_id=ctx.user_id, kind="image")
        w, h, _ = aimg.SIZES[sk]
        a = ma.ImageAsset(organisation_id=ctx.org_id, project_id=p.id, content_item_id=body.content_item_id, kind="text_card", size_key=sk,
                          width=w, height=h, title=body.title, stored_file_id=f.id, created_by=ctx.user_id, provider="pillow")
        db.add(a)
        db.flush()
        out.append(to_dict(a))
    log(db, ctx, "image.cards", f"project:{p.id}", {"sizes": body.sizes})
    return out


class AiImageIn(BaseModel):
    project_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=200)
    style: str | None = Field(None, max_length=300)
    size: str
    content_item_id: uuid.UUID | None = None


@router.post("/studio/images/ai")
def ai_image(body: AiImageIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    if body.size not in aimg.SIZES:
        raise HTTPException(400, "Unknown size")
    if body.content_item_id:
        _item(db, ctx, body.content_item_id)
    brand, color = _brand(db, p)
    prompt = aimg.image_prompt(db, body.title, brand, color or "#1F3A5F", body.style or "", body.size)
    if not aimg.image_providers(db):
        return {"connected": False, "prompt": prompt,
                "message": "AI image generation needs OpenAI or Google Gemini — NOT CONNECTED. Branded text cards work without it."}
    j = enqueue(db, ctx.org_id, "ai_image_generate", {"project_id": str(p.id), "prompt": prompt, "size_key": body.size, "title": body.title,
                                                      "content_item_id": str(body.content_item_id) if body.content_item_id else None,
                                                      "user_id": str(ctx.user_id)}, created_by=ctx.user_id, max_attempts=2)
    return {"connected": True, "job_id": str(j.id), "prompt": prompt}


@router.get("/studio/images")
def list_images(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    return [to_dict(a) for a in db.scalars(select(ma.ImageAsset).where(ma.ImageAsset.project_id == project_id)
                                           .order_by(ma.ImageAsset.created_at.desc()).limit(300))]


@router.get("/studio/images/{aid}/file")
def image_file(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    a = db.get(ma.ImageAsset, aid)
    if not a or not a.stored_file_id:
        raise HTTPException(404)
    project_guard(db, ctx, a.project_id)
    f = db.get(m.StoredFile, a.stored_file_id)
    return Response(reports.path_of(f).read_bytes(), media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{ac.slugify(a.title or "image")}-{a.width}x{a.height}.png"'})


@router.delete("/studio/images/{aid}")
def delete_image(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    a = db.get(ma.ImageAsset, aid)
    if not a:
        raise HTTPException(404)
    project_guard(db, ctx, a.project_id)
    f = db.get(m.StoredFile, a.stored_file_id) if a.stored_file_id else None
    db.delete(a)
    if f:
        try:
            reports.path_of(f).unlink(missing_ok=True)
        except OSError:
            pass
        db.delete(f)
    return {"ok": True}


# =================================================================== 50 provider hub
@router.get("/ai/providers")
def providers(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    conn = ih.connected_map(db)
    spend = eng.provider_spend(db)
    rows = []
    for r in eng.ordered_providers(db):
        rows.append({**r, "name": eng.PROVIDER_NAMES[r["key"]], "status": conn.get(r["key"], "NOT_CONNECTED"),
                     "models": list(eng.PRICES[r["key"]]), "spent_this_month": round(spend.get(r["key"], 0.0), 6)})
    return {"providers": rows, "task_types": eng.TASK_TYPES, "settings": eng.budget_status(db, ctx.org_id)}


class ProviderPref(BaseModel):
    enabled: bool = True
    default_model: str | None = Field(None, max_length=100)
    task_models: dict[str, str] = Field(default_factory=dict)
    monthly_budget: float | None = Field(None, ge=0, le=1_000_000)

    @field_validator("task_models")
    @classmethod
    def _tm(cls, v):
        bad = [k for k in v if k not in eng.TASK_TYPES and k != "default"]
        if bad:
            raise ValueError(f"unknown task types {bad}")
        if any(not re.match(r"^[\w.:/-]{1,100}$", x) for x in v.values()):
            raise ValueError("invalid model id")
        return v


class ProvidersPut(BaseModel):
    order: list[Literal["claude", "openai", "gemini", "perplexity"]] = Field(..., min_length=1, max_length=4)
    providers: dict[Literal["claude", "openai", "gemini", "perplexity"], ProviderPref] = Field(default_factory=dict)


@router.put("/ai/providers")
def save_providers(body: ProvidersPut, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    if len(set(body.order)) != len(body.order):
        raise HTTPException(400, "order has duplicates")
    order = list(body.order) + [p for p in eng.PROVIDERS if p not in body.order]
    for i, key in enumerate(order):
        prov = eng.ensure_provider(db, ctx.org_id, key)
        pf = db.get(ma.AiProviderPref, prov.id) or ma.AiProviderPref(ai_provider_id=prov.id, organisation_id=ctx.org_id, provider_key=key)
        db.add(pf)
        pf.position = i
        cfg = body.providers.get(key)
        if cfg:
            pf.enabled, pf.task_models, pf.monthly_budget = cfg.enabled, cfg.task_models, cfg.monthly_budget
            if cfg.default_model:
                if not re.match(r"^[\w.:/-]{1,100}$", cfg.default_model):
                    raise HTTPException(400, "invalid model id")
                prov.default_model = cfg.default_model
    # fallback chain mirrors the order (spec column ai_provider.fallback_id)
    provs = [eng.ensure_provider(db, ctx.org_id, k) for k in order]
    for a, b in zip(provs, provs[1:] + [None]):
        a.fallback_id = b.id if b else None
    log(db, ctx, "ai.routing_saved", None, {"order": order})
    db.flush()
    return providers(db, ctx)


class SettingsIn(BaseModel):
    monthly_budget: float | None = Field(None, ge=0, le=1_000_000)
    alert_pct: int = Field(80, ge=1, le=100)


@router.put("/ai/settings")
def save_settings(body: SettingsIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    s = db.get(ma.AiOrgSetting, ctx.org_id) or ma.AiOrgSetting(organisation_id=ctx.org_id)
    db.add(s)
    s.monthly_budget, s.alert_pct = body.monthly_budget, body.alert_pct
    db.flush()
    log(db, ctx, "ai.budget_saved", None, body.model_dump())
    return eng.budget_status(db, ctx.org_id)


@router.get("/ai/prices")
def prices(ctx: Ctx = Depends(require("view"))):
    return {"prices": eng.price_table(), "currency": "USD", "unit": "per 1M tokens (text) / per image",
            "note": "Providers' public list prices; your provider bills you directly."}


# =================================================================== 51 prompt library
def prompt_view(db, p: m.Prompt) -> dict:
    d = to_dict(p)
    d["builtin"] = p.organisation_id is None
    d["variables"] = prompts.variables(p.body)
    if d["builtin"]:
        d["description"] = (prompts.BUILTINS.get(p.name) or (None, None, None))[2]
    else:
        lv = db.scalar(select(ma.PromptVersion).where(ma.PromptVersion.prompt_id == p.id).order_by(ma.PromptVersion.version.desc()).limit(1))
        d["description"] = lv.description if lv else None
        d["overrides_builtin"] = p.name in prompts.BUILTINS
    return d


@router.get("/ai/prompts")
def list_prompts(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    prompts.ensure_builtins()
    rows = db.scalars(select(m.Prompt).order_by(m.Prompt.type, m.Prompt.name)).all()
    return {"prompts": [prompt_view(db, p) for p in rows], "types": sorted({p.type for p in rows} | set(eng.TASK_TYPES))}


class PromptIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    type: str = Field(..., min_length=2, max_length=40)
    body: str = Field(..., min_length=5, max_length=60000)
    description: str | None = Field(None, max_length=1000)
    note: str | None = Field(None, max_length=500)

    @field_validator("name", "type")
    @classmethod
    def _n(cls, v):
        if not re.match(r"^[\w .:-]+$", v):
            raise ValueError("letters, digits, spaces and . : - _ only")
        return v.strip()


def _prompt(db, pid) -> m.Prompt:
    p = db.get(m.Prompt, pid)
    if not p:
        raise HTTPException(404, "Prompt not found")
    return p


def _own_prompt(db, pid) -> m.Prompt:
    p = _prompt(db, pid)
    if p.organisation_id is None:
        raise HTTPException(403, "Built-in prompts are read-only — duplicate it to customise")
    return p


@router.post("/ai/prompts")
def create_prompt(body: PromptIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    if db.scalar(select(m.Prompt.id).where(m.Prompt.name == body.name, m.Prompt.organisation_id == ctx.org_id)):
        raise HTTPException(409, "A prompt with that name exists — edit it instead")
    p = m.Prompt(organisation_id=ctx.org_id, name=body.name, type=body.type, body=body.body, version=1, is_custom=True)
    db.add(p)
    db.flush()
    db.add(ma.PromptVersion(organisation_id=ctx.org_id, prompt_id=p.id, version=1, name=p.name, body=p.body, description=body.description,
                            note=body.note or "Created", created_by=ctx.user_id))
    db.flush()
    log(db, ctx, "prompt.created", f"prompt:{p.id}", {"name": p.name})
    return prompt_view(db, p)


@router.get("/ai/prompts/{pid}")
def get_prompt(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    return prompt_view(db, _prompt(db, pid))


@router.put("/ai/prompts/{pid}")
def update_prompt(pid: uuid.UUID, body: PromptIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    p = _own_prompt(db, pid)
    if body.name != p.name and db.scalar(select(m.Prompt.id).where(m.Prompt.name == body.name, m.Prompt.organisation_id == ctx.org_id)):
        raise HTTPException(409, "Another prompt has that name")
    if (p.name, p.type, p.body) == (body.name, body.type, body.body):
        raise HTTPException(409, "No changes")
    p.name, p.type, p.body, p.version = body.name, body.type, body.body, p.version + 1
    db.add(ma.PromptVersion(organisation_id=ctx.org_id, prompt_id=p.id, version=p.version, name=p.name, body=p.body,
                            description=body.description, note=body.note, created_by=ctx.user_id))
    db.flush()
    log(db, ctx, "prompt.updated", f"prompt:{p.id}", {"version": p.version})
    return prompt_view(db, p)


@router.delete("/ai/prompts/{pid}")
def delete_prompt(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete")), _c: Ctx = Depends(deny_client)):
    p = _own_prompt(db, pid)
    log(db, ctx, "prompt.deleted", f"prompt:{p.id}", {"name": p.name})
    db.delete(p)
    return {"ok": True}


@router.get("/ai/prompts/{pid}/versions")
def prompt_versions(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    p = _prompt(db, pid)
    if p.organisation_id is None:
        return [{"version": p.version, "body": p.body, "name": p.name, "note": "Built-in", "created_at": None}]
    return [to_dict(v) for v in db.scalars(select(ma.PromptVersion).where(ma.PromptVersion.prompt_id == pid).order_by(ma.PromptVersion.version.desc()))]


@router.post("/ai/prompts/{pid}/restore/{version}")
def restore_prompt(pid: uuid.UUID, version: int, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    p = _own_prompt(db, pid)
    v = db.scalar(select(ma.PromptVersion).where(ma.PromptVersion.prompt_id == pid, ma.PromptVersion.version == version))
    if not v:
        raise HTTPException(404, "Version not found")
    p.body, p.version = v.body, p.version + 1
    db.add(ma.PromptVersion(organisation_id=ctx.org_id, prompt_id=p.id, version=p.version, name=p.name, body=p.body, description=v.description,
                            note=f"Restored from v{version}", created_by=ctx.user_id))
    db.flush()
    return prompt_view(db, p)


@router.post("/ai/prompts/{pid}/duplicate")
def duplicate_prompt(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    src = _prompt(db, pid)
    name = src.name
    if src.organisation_id is not None or db.scalar(select(m.Prompt.id).where(m.Prompt.name == name, m.Prompt.organisation_id == ctx.org_id)):
        n = 2
        while db.scalar(select(m.Prompt.id).where(m.Prompt.name == f"{src.name} copy {n}", m.Prompt.organisation_id == ctx.org_id)):
            n += 1
        name = f"{src.name} copy {n}"
    body = PromptIn(name=name, type=src.type, body=src.body, description=prompt_view(db, src).get("description"), note=f"Duplicated from {src.name}")
    return create_prompt(body, db, ctx, ctx)


class RenderIn(BaseModel):
    variables: dict[str, str | int | float | list[str] | None] = Field(default_factory=dict)
    project_id: uuid.UUID | None = None


def _render_vals(db, ctx, body: RenderIn) -> dict:
    vals = {}
    if body.project_id:
        p = project_guard(db, ctx, body.project_id)
        vals.update({k: v for k, v in skills.get_profile(db, p).items() if isinstance(v, (str, list))})
    vals.update({k: v for k, v in body.variables.items() if isinstance(k, str) and len(k) <= 60})
    return vals


@router.post("/ai/prompts/{pid}/render")
def render_prompt(pid: uuid.UUID, body: RenderIn, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    p = _prompt(db, pid)
    text, missing = prompts.render(p.body, _render_vals(db, ctx, body), strict=True)
    return {"text": text, "missing": missing, "variables": prompts.variables(p.body)}


@router.post("/ai/prompts/{pid}/test")
def test_prompt(pid: uuid.UUID, body: RenderIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    p = _prompt(db, pid)
    text, missing = prompts.render(p.body, _render_vals(db, ctx, body))
    try:
        with eng.usage_scope(project_id=body.project_id, user_id=ctx.user_id, task_type=p.type if p.type in eng.TASK_TYPES else "general"):
            out, meta = agents.llm(db, ctx.org_id, text, max_tokens=2000)
    except agents.NoAI as e:
        return {"connected": False, "message": str(e), "prompt": text, "missing": missing}
    return {"connected": True, "output": out, "model": meta.get("model"), "provider": meta.get("provider"), "cost": meta.get("cost"),
            "tokens_in": meta.get("tokens_in"), "tokens_out": meta.get("tokens_out"), "prompt": text, "missing": missing}


# =================================================================== 53 agents + agent teams
@router.get("/ai/agent-catalog")
def agent_catalog(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    prompts.ensure_builtins()
    cat = agents.catalog(db)
    return {"agents": [{k: a[k] for k in ("key", "name", "group", "executor", "description", "state", "runnable")} for a in cat["agents"]],
            "skills": [{"key": s["key"], "name": s["name"], "category": s["category"]} for s in skills.library(db)],
            "prompts": [{"key": p.name, "type": p.type, "id": str(p.id), "builtin": p.organisation_id is None}
                        for p in db.scalars(select(m.Prompt).order_by(m.Prompt.name))],
            "templates": teams.TEMPLATES, "ai_connected": cat["ai_provider_connected"]}


class AgentIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    role: Literal["researcher", "writer", "editor", "seo", "analyst", "reviewer"] = "writer"
    provider: Literal["claude", "openai", "gemini", "perplexity"] | None = None


@router.get("/ai/agents")
def list_personas(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    provs = {p.id: p.type for p in db.scalars(select(m.AiProvider))}
    return [{**to_dict(a), "provider": provs.get(a.provider_id)} for a in db.scalars(select(m.Agent).order_by(m.Agent.name))]


@router.post("/ai/agents")
def create_persona(body: AgentIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    prov = eng.ensure_provider(db, ctx.org_id, body.provider) if body.provider else None
    a = m.Agent(organisation_id=ctx.org_id, name=body.name, role=body.role, provider_id=prov.id if prov else None)
    db.add(a)
    db.flush()
    return {**to_dict(a), "provider": body.provider}


@router.delete("/ai/agents/{aid}")
def delete_persona(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete")), _c: Ctx = Depends(deny_client)):
    a = db.get(m.Agent, aid)
    if not a:
        raise HTTPException(404)
    db.delete(a)
    return {"ok": True}


class TeamIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    description: str | None = Field(None, max_length=1000)
    output_type: str | None = Field(None, max_length=40)
    steps: list[dict] = Field(..., min_length=1, max_length=20)


def team_view(db, t: m.AgentTeam) -> dict:
    mt = db.get(ma.AgentTeamMeta, t.id)
    last = db.scalar(select(ma.AgentTeamRun).where(ma.AgentTeamRun.agent_team_id == t.id).order_by(ma.AgentTeamRun.created_at.desc()).limit(1))
    return {**to_dict(t), "description": mt.description if mt else None, "output_type": mt.output_type if mt else None,
            "last_run": {"id": str(last.id), "status": last.status, "at": last.created_at} if last else None}


def _team_body(db, body: TeamIn):
    if body.output_type and body.output_type not in studio.CONTENT_TYPES:
        raise HTTPException(400, f"output_type must be one of {list(studio.CONTENT_TYPES)}")
    for s in body.steps:
        if s.get("agent_id"):
            try:
                if not db.get(m.Agent, uuid.UUID(str(s["agent_id"]))):
                    raise HTTPException(400, "Agent persona not found")
            except ValueError:
                raise HTTPException(400, "bad agent_id")
    try:
        return teams.validate_steps(db, body.steps)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/ai/agent-teams")
def list_teams(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    return [team_view(db, t) for t in db.scalars(select(m.AgentTeam).order_by(m.AgentTeam.name))]


@router.post("/ai/agent-teams")
def create_team(body: TeamIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    steps = _team_body(db, body)
    t = m.AgentTeam(organisation_id=ctx.org_id, name=body.name, steps=steps)
    db.add(t)
    db.flush()
    db.add(ma.AgentTeamMeta(agent_team_id=t.id, organisation_id=ctx.org_id, description=body.description, output_type=body.output_type,
                            created_by=ctx.user_id))
    db.flush()
    log(db, ctx, "agent_team.created", f"agent_team:{t.id}", {"steps": len(steps)})
    return team_view(db, t)


@router.put("/ai/agent-teams/{tid}")
def update_team(tid: uuid.UUID, body: TeamIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    t = db.get(m.AgentTeam, tid)
    if not t:
        raise HTTPException(404)
    t.name, t.steps = body.name, _team_body(db, body)
    mt = db.get(ma.AgentTeamMeta, t.id) or ma.AgentTeamMeta(agent_team_id=t.id, organisation_id=ctx.org_id)
    db.add(mt)
    mt.description, mt.output_type = body.description, body.output_type
    db.flush()
    return team_view(db, t)


@router.delete("/ai/agent-teams/{tid}")
def delete_team(tid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete")), _c: Ctx = Depends(deny_client)):
    t = db.get(m.AgentTeam, tid)
    if not t:
        raise HTTPException(404)
    db.delete(t)
    log(db, ctx, "agent_team.deleted", f"agent_team:{tid}", {"name": t.name})
    return {"ok": True}


class TeamRunIn(BaseModel):
    project_id: uuid.UUID
    topic: str = Field(..., min_length=2, max_length=300)
    brief: str | None = Field(None, max_length=20000)
    create_content: str | None = Field(None, max_length=40)
    title: str | None = Field(None, max_length=200)


@router.post("/ai/agent-teams/{tid}/run")
def run_team(tid: uuid.UUID, body: TeamRunIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    t = db.get(m.AgentTeam, tid)
    if not t:
        raise HTTPException(404)
    project_guard(db, ctx, body.project_id)
    if body.create_content and body.create_content not in studio.CONTENT_TYPES:
        raise HTTPException(400, f"create_content must be one of {list(studio.CONTENT_TYPES)}")
    tr = ma.AgentTeamRun(organisation_id=ctx.org_id, agent_team_id=t.id, project_id=body.project_id, status="QUEUED",
                         input=body.model_dump(mode="json", exclude={"project_id"}), created_by=ctx.user_id)
    db.add(tr)
    db.flush()
    j = enqueue(db, ctx.org_id, "ai_team_run", {"team_run_id": str(tr.id), "user_id": str(ctx.user_id)}, created_by=ctx.user_id, max_attempts=1)
    tr.job_id = j.id
    log(db, ctx, "agent_team.run", f"agent_team:{t.id}", {"project": str(body.project_id)})
    return {"run_id": str(tr.id), "job_id": str(j.id), "ai": _ai_status(db)}


@router.get("/ai/team-runs")
def team_runs(team_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    q = select(ma.AgentTeamRun).where(ma.AgentTeamRun.project_id.in_(visible_project_ids(db, ctx)))
    if team_id:
        q = q.where(ma.AgentTeamRun.agent_team_id == team_id)
    if project_id:
        q = q.where(ma.AgentTeamRun.project_id == project_id)
    return [to_dict(r, exclude=("final_output",)) for r in db.scalars(q.order_by(ma.AgentTeamRun.created_at.desc()).limit(100))]


@router.get("/ai/team-runs/{rid}")
def team_run(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    tr = db.get(ma.AgentTeamRun, rid)
    if not tr:
        raise HTTPException(404)
    project_guard(db, ctx, tr.project_id)
    return {**to_dict(tr), "steps": teams.step_view(db, tr.id)}


class ToContentIn(BaseModel):
    type: str = Field("blog", max_length=40)
    title: str | None = Field(None, max_length=200)


@router.post("/ai/team-runs/{rid}/to-content")
def team_to_content(rid: uuid.UUID, body: ToContentIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    tr = db.get(ma.AgentTeamRun, rid)
    if not tr:
        raise HTTPException(404)
    project_guard(db, ctx, tr.project_id)
    if body.type not in studio.CONTENT_TYPES:
        raise HTTPException(400, "Unknown content type")
    if tr.content_item_id:
        raise HTTPException(409, "This run already became a content item")
    try:
        it = teams.to_content(db, tr, body.type, body.title, ctx.user_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    tr.content_item_id = it.id
    return item_view(db, it, True)


# =================================================================== 54 cost & usage
@router.get("/ai/usage")
def usage(days: int = Query(30, ge=1, le=366), group: str = "provider", project_id: uuid.UUID | None = None,
          db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    if group not in eng.GROUPS:
        raise HTTPException(400, f"group must be one of {sorted(eng.GROUPS)}")
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    start = end - timedelta(days=days)
    pids = None
    if project_id:
        project_guard(db, ctx, project_id)
        pids = [project_id]
    rows = eng.usage_report(db, start, end, group, pids)
    daily = eng.usage_report(db, start, end, "day", pids)
    tot = {"calls": sum(r["calls"] for r in rows), "tokens_in": sum(r["tokens_in"] for r in rows), "tokens_out": sum(r["tokens_out"] for r in rows),
           "cost": round(sum(r["cost"] for r in rows), 6), "images": sum(r["images"] for r in rows)}
    quota = eng.ai_quota(db, ctx.org_id)
    return {"group": group, "days": days, "rows": rows, "daily": daily, "totals": tot, "budget": eng.budget_status(db, ctx.org_id),
            "provider_spend": eng.provider_spend(db), "quota": quota,
            "quota_note": None if quota else "Your plan (Pricing Matrix) sets no AI usage limits; the monthly budget cap in AI Provider Hub applies."}


@router.get("/ai/usage.csv")
def usage_csv(days: int = Query(30, ge=1, le=366), db=Depends(get_db), ctx: Ctx = Depends(deny_client), _e: Ctx = Depends(require("export"))):
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    U, D = m.AiUsage, ma.AiUsageDetail
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["at", "provider", "model", "task_type", "project_id", "user_id", "tokens_in", "tokens_out", "images", "cost_usd", "estimated"])
    for u, d in db.execute(select(U, D).join(D, D.ai_usage_id == U.id, isouter=True).where(U.at >= end - timedelta(days=days)).order_by(U.at)):
        w.writerow([u.at.isoformat(), d.provider_key if d else "", u.model, d.task_type if d else "", d.project_id if d else "",
                    d.user_id if d else "", u.tokens_in, u.tokens_out, d.units if d else 0, f"{float(u.cost):.6f}", d.estimated if d else ""])
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="ai-usage.csv"'})


# =================================================================== 61 topical map
class MapIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=2, max_length=200)
    seeds: list[str] = Field(default_factory=list, max_length=30)
    keywords: str | list[str] | None = Field(None)
    min_cluster: int = Field(2, ge=1, le=10)


def _kw_list(v) -> list[str]:
    items = v if isinstance(v, list) else re.split(r"[\n,;]+", v or "")
    out = []
    for x in items:
        x = str(x).strip().lower()
        if x and len(x) <= 200 and x not in ("keyword", "query", "keywords"):
            out.append(x)
    if len(out) > 5000:
        raise HTTPException(400, "At most 5,000 imported keywords")
    return list(dict.fromkeys(out))


@router.get("/topical-maps")
def list_maps(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    out = []
    for t in db.scalars(select(m.TopicalMap).where(m.TopicalMap.project_id == project_id)):
        mt = db.get(ma.TopicalMapMeta, t.id)
        out.append({**to_dict(t), "stats": mt.stats if mt else {}, "seeds": mt.seeds if mt else [], "created_at": mt.created_at if mt else None})
    return sorted(out, key=lambda x: str(x["created_at"]), reverse=True)


@router.post("/topical-maps")
def create_map(body: MapIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    p = project_guard(db, ctx, body.project_id)
    try:
        tm = topical.build(db, p, body.name, [s.strip()[:200] for s in body.seeds if s.strip()], _kw_list(body.keywords), ctx.user_id, body.min_cluster)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "topical_map.created", f"project:{p.id}", {"name": body.name})
    return get_map(tm.id, db, ctx)


@router.get("/topical-maps/{mid}")
def get_map(mid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    t = db.get(m.TopicalMap, mid)
    if not t:
        raise HTTPException(404)
    project_guard(db, ctx, t.project_id)
    mt = db.get(ma.TopicalMapMeta, t.id)
    return {**to_dict(t), "seeds": mt.seeds if mt else [], "sources": mt.sources if mt else {}, "stats": mt.stats if mt else {},
            "created_at": mt.created_at if mt else None, "tree": topical.tree(db, t.id)}


@router.delete("/topical-maps/{mid}")
def delete_map(mid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    t = db.get(m.TopicalMap, mid)
    if not t:
        raise HTTPException(404)
    project_guard(db, ctx, t.project_id)
    for pl in db.scalars(select(m.ContentPlan).where(m.ContentPlan.topical_map_id == t.id)):
        db.delete(pl)
    db.flush()
    db.execute(m.TopicNode.__table__.update().where(m.TopicNode.topical_map_id == t.id).values(parent_id=None))
    db.delete(t)
    return {"ok": True}


# =================================================================== 62 content plan
class PlanIn(BaseModel):
    topical_map_id: uuid.UUID
    name: str = Field(..., min_length=2, max_length=200)
    per_week: int = Field(2, ge=1, le=14)
    start: date | None = None


def _plan(db, ctx, pid) -> tuple[m.ContentPlan, ma.ContentPlanMeta]:
    pl = db.get(m.ContentPlan, pid)
    if not pl:
        raise HTTPException(404)
    mt = db.get(ma.ContentPlanMeta, pl.id)
    tm = db.get(m.TopicalMap, pl.topical_map_id)
    project_guard(db, ctx, mt.project_id if mt else tm.project_id)
    return pl, mt


@router.get("/content-plans")
def list_plans(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    out = []
    for pl, mt in db.execute(select(m.ContentPlan, ma.ContentPlanMeta).join(ma.ContentPlanMeta, ma.ContentPlanMeta.content_plan_id == m.ContentPlan.id)
                             .where(ma.ContentPlanMeta.project_id == project_id).order_by(ma.ContentPlanMeta.created_at.desc())):
        n = db.scalar(select(func.count()).select_from(m.ContentBrief).where(m.ContentBrief.content_plan_id == pl.id))
        conv = db.scalar(select(func.count()).select_from(m.ContentBrief).where(m.ContentBrief.content_plan_id == pl.id, m.ContentBrief.content_item_id.is_not(None)))
        out.append({"id": str(pl.id), "name": mt.name, "topical_map_id": str(pl.topical_map_id), "created_at": mt.created_at, "items": n,
                    "converted": conv, "links": (pl.internal_link_map or {}).get("count", 0), "settings": mt.settings})
    return out


@router.post("/content-plans")
def create_plan(body: PlanIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    t = db.get(m.TopicalMap, body.topical_map_id)
    if not t:
        raise HTTPException(404, "Topical map not found")
    p = project_guard(db, ctx, t.project_id)
    try:
        pl = topical.build_plan(db, p, t, body.name, body.per_week, body.start, ctx.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "content_plan.created", f"project:{p.id}", {"name": body.name})
    return get_plan(pl.id, db, ctx)


@router.get("/content-plans/{pid}")
def get_plan(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    pl, mt = _plan(db, ctx, pid)
    briefs = []
    for b in db.scalars(select(m.ContentBrief).where(m.ContentBrief.content_plan_id == pl.id)):
        it = db.get(m.ContentItem, b.content_item_id) if b.content_item_id else None
        briefs.append({"id": str(b.id), "topic_node_id": str(b.topic_node_id), "content_item_id": str(b.content_item_id) if b.content_item_id else None,
                       "content_state": it.state if it else None, **b.brief})
    briefs.sort(key=lambda x: (x.get("week") or 0, x.get("role") != "pillar"))
    return {"id": str(pl.id), "name": mt.name if mt else None, "topical_map_id": str(pl.topical_map_id), "settings": mt.settings if mt else {},
            "project_id": str(mt.project_id) if mt else None, "internal_link_map": pl.internal_link_map, "briefs": briefs}


@router.post("/content-plans/{pid}/briefs/{bid}/enrich")
def enrich_brief(pid: uuid.UUID, bid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    pl, mt = _plan(db, ctx, pid)
    b = db.get(m.ContentBrief, bid)
    if not b or b.content_plan_id != pl.id:
        raise HTTPException(404)
    p = db.get(m.Project, mt.project_id)
    with eng.usage_scope(project_id=p.id, user_id=ctx.user_id, task_type="brief"):
        status, summary, out = agents.run_agent(db, p, "content_brief", {"keyword": b.brief.get("primary_keyword")})
    b.brief = {**b.brief, "agent_brief": {"status": status, "summary": summary, "competitor_urls": out.get("competitor_urls"),
                                          "competitor_outline": out.get("competitor_outline"), "ai_outline": out.get("ai_outline")},
               "status": "briefed" if b.brief.get("status") == "planned" else b.brief.get("status")}
    return {"id": str(b.id), **b.brief}


class ConvertIn(BaseModel):
    brief_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=200)
    create_tasks: bool = True
    assignee_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


@router.post("/content-plans/{pid}/convert")
def convert_plan(pid: uuid.UUID, body: ConvertIn, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    pl, mt = _plan(db, ctx, pid)
    p = db.get(m.Project, mt.project_id)
    for u in body.assignee_ids:
        usr = db.get(m.User, u)
        if not usr or not usr.is_active or usr.role == "client":
            raise HTTPException(400, f"Invalid assignee {u}")
    out = []
    for bid in dict.fromkeys(body.brief_ids):
        b = db.get(m.ContentBrief, bid)
        if not b or b.content_plan_id != pl.id:
            raise HTTPException(404, f"Brief {bid} not in this plan")
        if b.content_item_id:
            continue
        br = b.brief
        ctype = br.get("content_type") if br.get("content_type") in studio.CONTENT_TYPES else "blog"
        data = {"primary_keyword": br["primary_keyword"], "supporting_keywords": br.get("supporting_keywords") or [], "intent": br.get("intent"),
                "voice": studio.voice(db, p), "seo": {"meta_title": br["title"][:60], "slug": ac.slugify(br["primary_keyword"])},
                "url": br.get("target_url"), "content_plan_id": str(pl.id), "brief": br,
                "outline": studio.build_outline(br["primary_keyword"], br.get("intent") or "informational", br.get("supporting_keywords") or [], [], None)
                if ctype == "blog" else None, "faqs": [], "requirements": br.get("requirements")}
        it = studio.new_item(db, ctx.user_id, p, ctype, br["title"], data)
        b.content_item_id = it.id
        task_id = None
        if body.create_tasks:
            due = datetime.fromisoformat(br["publish_by"]).replace(tzinfo=timezone.utc) if br.get("publish_by") else None
            t = m.Task(organisation_id=ctx.org_id, project_id=p.id, title=f"{br['action'].capitalize()}: {br['title']}"[:300],
                       description=f"Primary keyword: {br['primary_keyword']}\nTarget URL: {br.get('target_url')}\nLinks to: {', '.join(br.get('links_to') or [])}"
                                   f"\nContent draft: /studio/items/{it.id}",
                       status="todo", priority="high" if br.get("role") == "pillar" else "medium", assignee_ids=body.assignee_ids, due_date=due,
                       source=f"content_plan:{pl.id}", workstream="content", created_by=ctx.user_id)
            db.add(t)
            db.flush()
            task_id = str(t.id)
        b.brief = {**br, "status": "in_production", "task_id": task_id}
        out.append({"brief_id": str(b.id), "content_item_id": str(it.id), "task_id": task_id})
    log(db, ctx, "content_plan.converted", f"content_plan:{pl.id}", {"count": len(out)})
    return {"converted": out}


@router.delete("/content-plans/{pid}")
def delete_plan(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    pl, _ = _plan(db, ctx, pid)
    db.delete(pl)
    return {"ok": True}
