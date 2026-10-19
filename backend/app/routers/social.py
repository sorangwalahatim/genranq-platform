"""Social & delivery modules: social accounts (38), content calendar (39), social automation (36),
content automation (33), social analytics (40), report automation (37), Microsoft Teams (46),
white-label domains (47) and the Chrome extension with personal access tokens (23).

All API paths live under /api/social/… ; the extension's token-authenticated endpoints under /api/social/ext/…"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import secrets
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_social as sm
from ..db import system_session, tenant_session
from ..deps import Ctx, deny_client, get_db, log, project_guard, require, visible_project_ids
from ..rbac import can
from ..services import entitlements as ent
from ..services import integrations as ih
from ..services import reports as rep
from ..services import social as soc
from ..services import social_domains as dom
from ..services import social_reports as srep
from ..services import social_teams as teams
from ..services import vault
from ..services.jobs import enqueue
from ..util import rows, to_dict

router = APIRouter(tags=["social"])
URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.I)
AI_PROVIDERS = ("claude", "openai", "gemini", "perplexity")


def _now():
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _vis(db, ctx) -> list:
    return list(db.scalars(visible_project_ids(db, ctx)))


def _item(db, ctx, iid) -> m.ContentItem:
    it = db.get(m.ContentItem, iid)
    if not it:
        raise HTTPException(404, "Content item not found")
    project_guard(db, ctx, it.project_id)
    return it


def _account(db, ctx, aid) -> sm.SocialAccount:
    a = db.get(sm.SocialAccount, aid)
    if not a:
        raise HTTPException(404, "Social account not found")
    project_guard(db, ctx, a.project_id)
    return a


def _sched(db, ctx, sid) -> sm.SocialSchedule:
    ss = db.scalar(select(sm.SocialSchedule).where(sm.SocialSchedule.schedule_id == sid))
    if not ss:
        raise HTTPException(404, "Scheduled post not found")
    project_guard(db, ctx, ss.project_id)
    return ss


def _url(u: str | None, what: str) -> str | None:
    if not u:
        return None
    u = u.strip()
    if not URL_RE.match(u) or len(u) > 2000:
        raise HTTPException(400, f"{what} must be an http(s) URL")
    return u


def _workflow(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except soc.WorkflowUnavailable as e:
        raise HTTPException(409, detail={"code": "workflow_unavailable", "message": str(e)})
    except ValueError as e:
        raise HTTPException(409, str(e))


# ================================================================== 38 social accounts
@router.get("/social/providers")
def providers(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    st = soc.provider_status(db)
    return [{"provider": p, "label": soc.PROVIDER_LABEL[p], "status": st[p],
             "channels": [c for c, pp in soc.CHANNELS.items() if pp == p]} for p in soc.PROVIDER_LABEL]


def _account_out(a: sm.SocialAccount, st: dict, pnames: dict) -> dict:
    conn = st.get(a.provider, "NOT_CONNECTED")
    return {**to_dict(a), "project": pnames.get(a.project_id), "provider_status": conn, "channel_label": soc.CHANNEL_LABEL[a.channel],
            "publish_ready": conn == "CONNECTED" and a.status == "active",
            "state": "disabled" if a.status == "disabled" else ("NOT CONNECTED" if conn != "CONNECTED" else
                                                                ("error" if a.status == "error" else "ready"))}


@router.get("/social/accounts")
def list_accounts(project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(sm.SocialAccount).where(sm.SocialAccount.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        project_guard(db, ctx, project_id)
        q = q.where(sm.SocialAccount.project_id == project_id)
    st = soc.provider_status(db)
    pnames = {p.id: p.name for p in db.scalars(select(m.Project))}
    accts = db.scalars(q.order_by(sm.SocialAccount.channel, sm.SocialAccount.name)).all()
    return {"providers": [{"provider": p, "label": soc.PROVIDER_LABEL[p], "status": st[p]} for p in soc.PROVIDER_LABEL],
            "accounts": [_account_out(a, st, pnames) for a in accts]}


@router.get("/social/accounts/discover")
def discover(provider: str, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    if provider not in soc.PROVIDER_LABEL:
        raise HTTPException(400, f"provider must be one of {list(soc.PROVIDER_LABEL)}")
    try:
        found = soc.discover(db, ctx.org_id, provider)
    except ih.NotConnected as e:
        return {"connected": False, "message": f"{e}. Connect it in Integrations to list pages automatically, or add the account manually.",
                "accounts": []}
    except (soc.PlatformError, ih.IntegrationError) as e:
        raise HTTPException(502, f"{soc.PROVIDER_LABEL[provider]}: {e}")
    mapped: dict = {}
    for a in db.scalars(select(sm.SocialAccount)):
        mapped.setdefault((a.channel, a.external_id), []).append(str(a.project_id))
    return {"connected": True, "accounts": [{**f, "mapped_project_ids": mapped.get((f["channel"], f["external_id"]), [])} for f in found]}


class AccountIn(BaseModel):
    project_id: uuid.UUID
    channel: str
    external_id: str = Field(..., min_length=1, max_length=200)
    name: str = Field(..., min_length=1, max_length=200)
    handle: str | None = Field(None, max_length=200)
    is_default: bool = False


def _set_default(db, a: sm.SocialAccount):
    for o in db.scalars(select(sm.SocialAccount).where(sm.SocialAccount.project_id == a.project_id, sm.SocialAccount.channel == a.channel,
                                                       sm.SocialAccount.id != a.id)):
        o.is_default = False
    a.is_default = True


@router.post("/social/accounts")
def add_account(body: AccountIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    project_guard(db, ctx, body.project_id)
    if body.channel not in soc.CHANNELS:
        raise HTTPException(400, f"channel must be one of {list(soc.CHANNELS)}")
    ext = body.external_id.strip()
    if body.channel == "linkedin":
        ext = ext.rsplit(":", 1)[-1]
    if not re.match(r"^[A-Za-z0-9_.:-]+$", ext):
        raise HTTPException(400, "external_id contains invalid characters")
    if db.scalar(select(sm.SocialAccount.id).where(sm.SocialAccount.project_id == body.project_id, sm.SocialAccount.channel == body.channel,
                                                   sm.SocialAccount.external_id == ext)):
        raise HTTPException(409, "That account is already mapped to this project")
    a = sm.SocialAccount(organisation_id=ctx.org_id, project_id=body.project_id, provider=soc.CHANNELS[body.channel], channel=body.channel,
                         external_id=ext, name=body.name.strip(), handle=(body.handle or "").lstrip("@") or None, created_by=ctx.user_id)
    db.add(a)
    db.flush()
    first = not db.scalar(select(func.count()).select_from(sm.SocialAccount).where(
        sm.SocialAccount.project_id == a.project_id, sm.SocialAccount.channel == a.channel, sm.SocialAccount.id != a.id))
    if body.is_default or first:
        _set_default(db, a)
    log(db, ctx, "social.account_mapped", f"social_account:{a.id}", {"channel": a.channel, "project_id": str(a.project_id)})
    return _account_out(a, soc.provider_status(db), {})


class AccountPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    handle: str | None = Field(None, max_length=200)
    is_default: bool | None = None
    status: str | None = None


@router.patch("/social/accounts/{aid}")
def patch_account(aid: uuid.UUID, body: AccountPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    a = _account(db, ctx, aid)
    if body.name:
        a.name = body.name.strip()
    if body.handle is not None:
        a.handle = body.handle.lstrip("@") or None
    if body.status is not None:
        if body.status not in ("active", "disabled"):
            raise HTTPException(400, "status must be active or disabled")
        a.status = body.status
    if body.is_default:
        _set_default(db, a)
    log(db, ctx, "social.account_updated", f"social_account:{a.id}", body.model_dump(exclude_none=True))
    return _account_out(a, soc.provider_status(db), {})


@router.delete("/social/accounts/{aid}")
def delete_account(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete")), _c: Ctx = Depends(deny_client)):
    a = _account(db, ctx, aid)
    cancelled = 0
    for ss in db.scalars(select(sm.SocialSchedule).where(sm.SocialSchedule.social_account_id == a.id, sm.SocialSchedule.status == "scheduled")):
        ss.status = "cancelled"
        cancelled += 1
    for r in db.scalars(select(sm.SocialRule).where(sm.SocialRule.project_id == a.project_id)):
        if a.id in (r.account_ids or []):
            r.account_ids = [x for x in r.account_ids if x != a.id]
    log(db, ctx, "social.account_removed", f"social_account:{a.id}", {"cancelled_posts": cancelled})
    db.delete(a)
    return {"ok": True, "cancelled_posts": cancelled}


@router.post("/social/accounts/{aid}/health")
def account_health(aid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    a = _account(db, ctx, aid)
    soc.account_health(db, ctx.org_id, a)
    return _account_out(a, soc.provider_status(db), {})


# ================================================================== content items helpers
class ItemMetaIn(BaseModel):
    link_url: str | None = None
    media_url: str | None = None


@router.put("/social/items/{iid}/meta")
def put_item_meta(iid: uuid.UUID, body: ItemMetaIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    it = _item(db, ctx, iid)
    meta = soc.post_meta(db, it.id) or sm.SocialPostMeta(organisation_id=ctx.org_id, content_item_id=it.id)
    meta.link_url, meta.media_url = _url(body.link_url, "link_url"), _url(body.media_url, "media_url")
    db.add(meta)
    db.flush()
    return to_dict(meta)


def _approval_for(db, item_id) -> m.Approval | None:
    return db.scalar(select(m.Approval).where(m.Approval.entity_type == "content_item", m.Approval.entity_id == item_id)
                     .order_by(desc(m.Approval.created_at)).limit(1))


def _request_approval(db, ctx, it: m.ContentItem, note: str | None = None) -> m.Approval:
    """Submit for review through the content workflow (which creates the approval request) and tell Teams."""
    if it.state in ("draft", "generated"):
        _workflow(soc.transition, db, ctx, it, "review", note or "Sent for approval")
    if it.state != "review":
        raise HTTPException(409, f"Only draft, generated or in-review content can be sent for approval (this item is '{it.state}')")
    a = _approval_for(db, it.id)
    if not a:
        raise HTTPException(409, "The content workflow did not create an approval request")
    teams.dispatch(db, ctx.org_id, "approval_requested", f"Approval requested: {a.title}", "Awaiting manager", link="/approvals",
                   project_id=it.project_id, dedupe_key=f"approval:{a.id}")
    return a


@router.post("/social/items/{iid}/request-approval")
def request_approval(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    it = _item(db, ctx, iid)
    a = _request_approval(db, ctx, it)
    log(db, ctx, "social.approval_requested", f"content_item:{it.id}")
    return {"approval_id": str(a.id), "state": it.state}


def _approve(db, ctx, it: m.ContentItem, notes: str | None) -> str:
    """Human approval of an in-review item through the content workflow (which enforces the approval chain,
    the approver's role and that nobody but an owner approves their own submission)."""
    if it.state != "review":
        return f"skipped ({it.state})"
    try:
        with db.begin_nested():
            soc.transition(db, ctx, it, "approved", notes or "Approved")
    except soc.WorkflowUnavailable as e:
        raise HTTPException(409, detail={"code": "workflow_unavailable", "message": str(e)})
    except HTTPException as e:
        return f"skipped ({e.detail if isinstance(e.detail, str) else e.detail.get('message')})"
    return "approved" if it.state == "approved" else f"approval step recorded ({it.state})"


# ================================================================== 39 calendar
@router.get("/social/calendar")
def calendar(start: datetime, end: datetime, project_id: uuid.UUID | None = None, channel: str | None = None,
             state: str | None = None, status: str | None = None, campaign_id: uuid.UUID | None = None,
             db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    start, end = _aware(start), _aware(end)
    if end <= start or end - start > timedelta(days=62):
        raise HTTPException(400, "Range must be positive and at most 62 days")
    pids = _vis(db, ctx)
    if project_id:
        project_guard(db, ctx, project_id)
        pids = [project_id]
    if channel and channel not in (*soc.CHANNELS, "gbp"):
        raise HTTPException(400, "Unknown channel")
    q = select(m.Schedule, sm.SocialSchedule, m.ContentItem, sm.SocialAccount) \
        .join(sm.SocialSchedule, sm.SocialSchedule.schedule_id == m.Schedule.id) \
        .join(m.ContentItem, m.ContentItem.id == m.Schedule.content_item_id) \
        .outerjoin(sm.SocialAccount, sm.SocialAccount.id == sm.SocialSchedule.social_account_id) \
        .where(sm.SocialSchedule.project_id.in_(pids), m.Schedule.scheduled_at >= start, m.Schedule.scheduled_at < end)
    if channel and channel != "gbp":
        q = q.where(sm.SocialAccount.channel == channel)
    if state:
        q = q.where(m.ContentItem.state == state)
    if status:
        q = q.where(sm.SocialSchedule.status == status)
    if campaign_id:
        q = q.where(sm.SocialSchedule.campaign_id == campaign_id)
    pnames = {p.id: p.name for p in db.scalars(select(m.Project))}
    camps = {c.id: c.name for c in db.scalars(select(m.Campaign))}
    ccolors = {c.campaign_id: c.color for c in db.scalars(select(sm.SocialCampaign))}
    can_move = can(ctx.role, "publish")
    events = []
    if channel != "gbp":
        rowsq = db.execute(q.order_by(m.Schedule.scheduled_at)).all()
        pj_ids = [ss.publish_job_id for _, ss, _, _ in rowsq if ss.publish_job_id]
        pubs = {p.publish_job_id: p for p in db.scalars(select(sm.SocialPublish).where(sm.SocialPublish.publish_job_id.in_(pj_ids)))} if pj_ids else {}
        for s, ss, it, a in rowsq:
            sp = pubs.get(ss.publish_job_id)
            events.append({"kind": "post", "id": str(s.id), "at": s.scheduled_at, "title": it.title or "(untitled)", "content_item_id": str(it.id),
                           "project_id": str(ss.project_id), "project": pnames.get(ss.project_id), "channel": a.channel if a else None,
                           "account": a.name if a else "(removed account)", "state": it.state, "status": ss.status,
                           "campaign_id": str(ss.campaign_id) if ss.campaign_id else None, "campaign": camps.get(ss.campaign_id),
                           "color": ccolors.get(ss.campaign_id), "rule_id": str(ss.rule_id) if ss.rule_id else None,
                           "external_url": sp.external_url if sp else None, "error": sp.error if sp else None,
                           "draggable": can_move and ss.status == "scheduled" and it.state in soc.APPROVED_OR_PUBLISHED})
    if (not channel or channel == "gbp") and not state and not campaign_id:
        gq = select(m.GbpPost, m.GbpLocation).join(m.GbpLocation, m.GbpLocation.id == m.GbpPost.location_id) \
            .where(m.GbpLocation.project_id.in_(pids), m.GbpPost.posted_at >= start, m.GbpPost.posted_at < end)
        if status:
            gq = gq.where(m.GbpPost.status == status)
        for gp, loc in db.execute(gq).all():
            events.append({"kind": "gbp_post", "id": str(gp.id), "at": gp.posted_at, "title": (gp.body or gp.post_type or "GBP post")[:80],
                           "project_id": str(loc.project_id), "project": pnames.get(loc.project_id), "channel": "gbp",
                           "account": loc.name, "state": None, "status": gp.status, "draggable": False})
    events.sort(key=lambda e: e["at"])
    # side lists: approved but unscheduled, and the approval queue
    active = select(sm.SocialSchedule.content_item_id).where(sm.SocialSchedule.status.in_(("scheduled", "publishing")))
    unscheduled = db.scalars(select(m.ContentItem).where(m.ContentItem.project_id.in_(pids), m.ContentItem.state.in_(soc.APPROVED),
                                                         m.ContentItem.id.not_in(active)).order_by(m.ContentItem.created_at).limit(100)).all()
    review = db.scalars(select(m.ContentItem).where(m.ContentItem.project_id.in_(pids), m.ContentItem.state == "review")
                        .order_by(m.ContentItem.created_at).limit(100)).all()
    appr = {a.entity_id: a for a in db.scalars(select(m.Approval).where(m.Approval.entity_type == "content_item",
                                                                       m.Approval.entity_id.in_([r.id for r in review])))} if review else {}
    return {"events": events,
            "unscheduled": [{"id": str(i.id), "title": i.title, "project_id": str(i.project_id), "project": pnames.get(i.project_id),
                             "channel": i.channel, "type": i.type} for i in unscheduled],
            "awaiting_approval": [{"id": str(i.id), "title": i.title, "project": pnames.get(i.project_id), "channel": i.channel,
                                   "approval_id": str(appr[i.id].id) if i.id in appr else None,
                                   "approval_status": appr[i.id].status if i.id in appr else None} for i in review],
            "can_schedule": can_move}


class ScheduleIn(BaseModel):
    content_item_id: uuid.UUID
    account_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=20)
    scheduled_at: datetime
    campaign_id: uuid.UUID | None = None


@router.post("/social/schedules")
def create_schedule(body: ScheduleIn, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    it = _item(db, ctx, body.content_item_id)
    at = _aware(body.scheduled_at)
    if at < _now() - timedelta(minutes=1):
        raise HTTPException(400, "Choose a time in the future")
    camp = None
    if body.campaign_id:
        camp = db.scalar(select(sm.SocialCampaign).where(sm.SocialCampaign.campaign_id == body.campaign_id))
        if not camp or camp.project_id != it.project_id:
            raise HTTPException(400, "Campaign not in this project")
        soc.check_campaign_window(camp, at)
    out = []
    for aid in dict.fromkeys(body.account_ids):
        a = _account(db, ctx, aid)
        ss = soc.schedule_item(db, ctx.org_id, it, a, at, user_id=ctx.user_id, campaign_id=body.campaign_id)
        out.append({"schedule_id": str(ss.schedule_id), "account": a.name, "channel": a.channel})
    log(db, ctx, "social.scheduled", f"content_item:{it.id}", {"at": at.isoformat(), "accounts": len(out)})
    return {"scheduled": out}


class ReschedIn(BaseModel):
    scheduled_at: datetime


@router.patch("/social/schedules/{sid}")
def move_schedule(sid: uuid.UUID, body: ReschedIn, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    ss = _sched(db, ctx, sid)
    s = soc.reschedule(db, ctx.org_id, ss, _aware(body.scheduled_at), ctx.user_id)
    log(db, ctx, "social.rescheduled", f"schedule:{sid}", {"at": s.scheduled_at.isoformat()})
    return {"schedule_id": str(sid), "scheduled_at": s.scheduled_at}


@router.delete("/social/schedules/{sid}")
def cancel_schedule(sid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    ss = _sched(db, ctx, sid)
    if ss.status not in ("scheduled", "failed"):
        raise HTTPException(409, f"A {ss.status} post cannot be cancelled")
    ss.status = "cancelled"
    if ss.publish_job_id:
        pj = db.get(m.PublishJob, ss.publish_job_id)
        if pj and pj.status in ("QUEUED", "FAILED"):
            pj.status = "CANCELLED"
    log(db, ctx, "social.schedule_cancelled", f"schedule:{sid}")
    return {"ok": True}


# ================================================================== 36 campaigns
class CampaignIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = Field(None, max_length=2000)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


def _campaign_out(db, c: m.Campaign, sc: sm.SocialCampaign) -> dict:
    counts = dict(db.execute(select(sm.SocialSchedule.status, func.count()).where(sm.SocialSchedule.campaign_id == c.id)
                             .group_by(sm.SocialSchedule.status)).all())
    return {"id": str(c.id), "name": c.name, "project_id": str(sc.project_id), "start_date": sc.start_date, "end_date": sc.end_date,
            "description": sc.description, "color": sc.color, "status": sc.status, "created_at": c.created_at, "counts": counts,
            "total": sum(counts.values())}


@router.get("/social/campaigns")
def list_campaigns(project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(m.Campaign, sm.SocialCampaign).join(sm.SocialCampaign, sm.SocialCampaign.campaign_id == m.Campaign.id) \
        .where(sm.SocialCampaign.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        q = q.where(sm.SocialCampaign.project_id == project_id)
    return [_campaign_out(db, c, sc) for c, sc in db.execute(q.order_by(desc(m.Campaign.created_at))).all()]


def _check_window(s, e):
    if s and e and e < s:
        raise HTTPException(400, "end_date must be on or after start_date")


@router.post("/social/campaigns")
def create_campaign(body: CampaignIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    project_guard(db, ctx, body.project_id)
    _check_window(body.start_date, body.end_date)
    c = m.Campaign(organisation_id=ctx.org_id, name=body.name.strip())
    db.add(c)
    db.flush()
    sc = sm.SocialCampaign(organisation_id=ctx.org_id, campaign_id=c.id, project_id=body.project_id, start_date=body.start_date,
                           end_date=body.end_date, description=body.description, color=body.color)
    db.add(sc)
    db.flush()
    log(db, ctx, "social.campaign_created", f"campaign:{c.id}", {"name": c.name})
    return _campaign_out(db, c, sc)


def _campaign(db, ctx, cid) -> tuple[m.Campaign, sm.SocialCampaign]:
    sc = db.scalar(select(sm.SocialCampaign).where(sm.SocialCampaign.campaign_id == cid))
    if not sc:
        raise HTTPException(404, "Campaign not found")
    project_guard(db, ctx, sc.project_id)
    return db.get(m.Campaign, cid), sc


class CampaignPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = Field(None, max_length=2000)
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    status: str | None = None


@router.patch("/social/campaigns/{cid}")
def patch_campaign(cid: uuid.UUID, body: CampaignPatch, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c: Ctx = Depends(deny_client)):
    c, sc = _campaign(db, ctx, cid)
    fs = body.model_fields_set
    if body.name:
        c.name = body.name.strip()
    for k in ("start_date", "end_date", "description", "color"):
        if k in fs:
            setattr(sc, k, getattr(body, k))
    if body.status:
        if body.status not in ("active", "archived"):
            raise HTTPException(400, "status must be active or archived")
        sc.status = body.status
    _check_window(sc.start_date, sc.end_date)
    return _campaign_out(db, c, sc)


@router.get("/social/campaigns/{cid}")
def get_campaign(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    c, sc = _campaign(db, ctx, cid)
    posts = db.execute(select(m.Schedule, sm.SocialSchedule, m.ContentItem, sm.SocialAccount)
                       .join(sm.SocialSchedule, sm.SocialSchedule.schedule_id == m.Schedule.id)
                       .join(m.ContentItem, m.ContentItem.id == m.Schedule.content_item_id)
                       .outerjoin(sm.SocialAccount, sm.SocialAccount.id == sm.SocialSchedule.social_account_id)
                       .where(sm.SocialSchedule.campaign_id == cid).order_by(m.Schedule.scheduled_at)).all()
    rules = db.scalars(select(sm.SocialRule).where(sm.SocialRule.campaign_id == cid)).all()
    return {**_campaign_out(db, c, sc),
            "posts": [{"schedule_id": str(s.id), "at": s.scheduled_at, "title": it.title, "state": it.state, "status": ss.status,
                       "channel": a.channel if a else None, "account": a.name if a else None,
                       "outside_window": bool((sc.start_date and s.scheduled_at.date() < sc.start_date) or (sc.end_date and s.scheduled_at.date() > sc.end_date))}
                      for s, ss, it, a in posts],
            "rules": [{"id": str(r.id), "name": r.name, "enabled": r.enabled} for r in rules]}


@router.delete("/social/campaigns/{cid}")
def delete_campaign(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete")), _c: Ctx = Depends(deny_client)):
    c, sc = _campaign(db, ctx, cid)
    log(db, ctx, "social.campaign_deleted", f"campaign:{c.id}", {"name": c.name})
    db.delete(sc)
    db.delete(c)
    return {"ok": True}


# ================================================================== 36 recurring rules
class RuleIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    weekdays: list[int] = Field(..., min_length=1, max_length=7)
    times: list[str] = Field(..., min_length=1, max_length=12)
    timezone: str = "UTC"
    start_date: date | None = None
    end_date: date | None = None
    mode: str = "queue"
    campaign_id: uuid.UUID | None = None
    horizon_days: int = Field(14, ge=1, le=60)
    item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    enabled: bool = True


def _validate_rule(db, ctx, body) -> None:
    if any(d not in range(7) for d in body.weekdays):
        raise HTTPException(400, "weekdays are 0 (Mon) … 6 (Sun)")
    if body.mode not in ("queue", "evergreen"):
        raise HTTPException(400, "mode must be queue or evergreen")
    soc.zone(body.timezone)
    for t in body.times:
        if not soc.TIME_RE.match(t):
            raise HTTPException(400, f"Time '{t}' must be HH:MM (24h)")
    _check_window(body.start_date, body.end_date)
    for aid in body.account_ids:
        a = db.get(sm.SocialAccount, aid)
        if not a or a.project_id != body.project_id:
            raise HTTPException(400, "Every account must belong to the rule's project")
    if body.campaign_id:
        sc = db.scalar(select(sm.SocialCampaign).where(sm.SocialCampaign.campaign_id == body.campaign_id))
        if not sc or sc.project_id != body.project_id:
            raise HTTPException(400, "Campaign not in this project")


def _rule_items(db, ctx, project_id, ids, mode) -> list[str]:
    out = []
    ok = soc.APPROVED_OR_PUBLISHED if mode == "evergreen" else soc.APPROVED
    for iid in dict.fromkeys(ids):
        it = db.get(m.ContentItem, iid)
        if not it or it.project_id != project_id:
            raise HTTPException(400, "Every item must belong to the rule's project")
        if it.state not in ok:
            raise HTTPException(409, detail={"code": "not_approved", "message": f"'{it.title or it.id}' is '{it.state}' — only approved content can be queued"})
        out.append(str(it.id))
    return out


def _rule_out(db, r: sm.SocialRule) -> dict:
    counts = dict(db.execute(select(sm.SocialSchedule.status, func.count()).where(sm.SocialSchedule.rule_id == r.id)
                             .group_by(sm.SocialSchedule.status)).all())
    nxt = db.scalar(select(func.min(m.Schedule.scheduled_at)).join(sm.SocialSchedule, sm.SocialSchedule.schedule_id == m.Schedule.id)
                    .where(sm.SocialSchedule.rule_id == r.id, sm.SocialSchedule.status == "scheduled"))
    return {**to_dict(r), "counts": counts, "next_post_at": nxt}


def _ensure_rule_backstop(db, org_id):
    if not db.scalar(select(m.JobSchedule.id).where(m.JobSchedule.kind == "social_rules_materialize")):
        db.add(m.JobSchedule(organisation_id=org_id, kind="social_rules_materialize", payload={}, frequency="daily",
                             next_run_at=_now() + timedelta(days=1)))


@router.get("/social/rules")
def list_rules(project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(sm.SocialRule).where(sm.SocialRule.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        q = q.where(sm.SocialRule.project_id == project_id)
    return [_rule_out(db, r) for r in db.scalars(q.order_by(sm.SocialRule.created_at))]


@router.post("/social/rules")
def create_rule(body: RuleIn, db=Depends(get_db), ctx: Ctx = Depends(require("publish")), _c: Ctx = Depends(deny_client)):
    project_guard(db, ctx, body.project_id)
    _validate_rule(db, ctx, body)
    r = sm.SocialRule(organisation_id=ctx.org_id, project_id=body.project_id, campaign_id=body.campaign_id, name=body.name.strip(),
                      account_ids=list(dict.fromkeys(body.account_ids)), weekdays=sorted(set(body.weekdays)), times=sorted(set(body.times)),
                      timezone=body.timezone, start_date=body.start_date, end_date=body.end_date, mode=body.mode,
                      horizon_days=body.horizon_days, enabled=body.enabled, created_by=ctx.user_id,
                      item_ids=_rule_items(db, ctx, body.project_id, body.item_ids, body.mode))
    db.add(r)
    db.flush()
    res = soc.materialize(db, r, user_id=ctx.user_id)
    _ensure_rule_backstop(db, ctx.org_id)
    log(db, ctx, "social.rule_created", f"social_rule:{r.id}", {"name": r.name, "scheduled": res["created"]})
    return {**_rule_out(db, r), "materialized": res}


class RulePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    account_ids: list[uuid.UUID] | None = Field(None, max_length=20)
    weekdays: list[int] | None = Field(None, min_length=1, max_length=7)
    times: list[str] | None = Field(None, min_length=1, max_length=12)
    timezone: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    horizon_days: int | None = Field(None, ge=1, le=60)
    enabled: bool | None = None


def _rule(db, ctx, rid) -> sm.SocialRule:
    r = db.get(sm.SocialRule, rid)
    if not r:
        raise HTTPException(404, "Rule not found")
    project_guard(db, ctx, r.project_id)
    return r


def _drop_future(db, r: sm.SocialRule) -> int:
    n = 0
    for ss in db.scalars(select(sm.SocialSchedule).where(sm.SocialSchedule.rule_id == r.id, sm.SocialSchedule.status == "scheduled")):
        ss.status = "cancelled"
        n += 1
    return n


@router.patch("/social/rules/{rid}")
def patch_rule(rid: uuid.UUID, body: RulePatch, db=Depends(get_db), ctx: Ctx = Depends(require("publish")), _c: Ctx = Depends(deny_client)):
    r = _rule(db, ctx, rid)
    fs = body.model_fields_set
    merged = RuleIn(project_id=r.project_id, name=body.name or r.name,
                    account_ids=body.account_ids if body.account_ids is not None else list(r.account_ids or []),
                    weekdays=body.weekdays or r.weekdays, times=body.times or r.times, timezone=body.timezone or r.timezone,
                    start_date=body.start_date if "start_date" in fs else r.start_date, end_date=body.end_date if "end_date" in fs else r.end_date,
                    mode=r.mode, campaign_id=r.campaign_id)
    _validate_rule(db, ctx, merged)
    timing = any(k in fs for k in ("weekdays", "times", "timezone", "start_date", "end_date", "account_ids"))
    cancelled = _drop_future(db, r) if timing or body.enabled is False else 0
    r.name, r.account_ids, r.weekdays, r.times, r.timezone = merged.name, merged.account_ids, sorted(set(merged.weekdays)), sorted(set(merged.times)), merged.timezone
    r.start_date, r.end_date = merged.start_date, merged.end_date
    if body.horizon_days:
        r.horizon_days = body.horizon_days
    if body.enabled is not None:
        r.enabled = body.enabled
    db.flush()
    res = soc.materialize(db, r, user_id=ctx.user_id)
    log(db, ctx, "social.rule_updated", f"social_rule:{r.id}", {"cancelled": cancelled, "scheduled": res["created"]})
    return {**_rule_out(db, r), "materialized": res, "cancelled": cancelled}


@router.delete("/social/rules/{rid}")
def delete_rule(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("publish")), _c: Ctx = Depends(deny_client)):
    r = _rule(db, ctx, rid)
    n = _drop_future(db, r)
    log(db, ctx, "social.rule_deleted", f"social_rule:{r.id}", {"cancelled": n})
    db.delete(r)
    return {"ok": True, "cancelled": n}


class PreviewIn(BaseModel):
    weekdays: list[int] = Field(..., min_length=1, max_length=7)
    times: list[str] = Field(..., min_length=1, max_length=12)
    timezone: str = "UTC"
    days: int = Field(14, ge=1, le=60)
    start_date: date | None = None
    end_date: date | None = None


def _slots_out(slots, tz):
    z = soc.zone(tz)
    return [{"utc": s, "local": s.astimezone(z).strftime("%a %d %b %Y %H:%M %Z")} for s in slots]


@router.post("/social/rules/preview")
def preview_rule(body: PreviewIn, ctx: Ctx = Depends(require("view"))):
    if any(d not in range(7) for d in body.weekdays):
        raise HTTPException(400, "weekdays are 0 (Mon) … 6 (Sun)")
    now = _now()
    return {"slots": _slots_out(soc.expand(body.weekdays, body.times, body.timezone, now, now + timedelta(days=body.days),
                                           body.start_date, body.end_date), body.timezone)}


@router.get("/social/rules/{rid}/preview")
def preview_saved(rid: uuid.UUID, days: int = Query(14, ge=1, le=60), db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    r = _rule(db, ctx, rid)
    now = _now()
    return {"slots": _slots_out(soc.expand(r.weekdays, r.times, r.timezone, now, now + timedelta(days=days), r.start_date, r.end_date), r.timezone)}


class RuleItemsIn(BaseModel):
    content_item_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)


@router.post("/social/rules/{rid}/items")
def queue_items(rid: uuid.UUID, body: RuleItemsIn, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    r = _rule(db, ctx, rid)
    new = _rule_items(db, ctx, r.project_id, body.content_item_ids, r.mode)
    r.item_ids = list(dict.fromkeys([*(r.item_ids or []), *new]))
    db.flush()
    res = soc.materialize(db, r, user_id=ctx.user_id)
    log(db, ctx, "social.rule_items_queued", f"social_rule:{r.id}", {"items": len(new), "scheduled": res["created"]})
    return {**_rule_out(db, r), "materialized": res}


@router.post("/social/rules/{rid}/materialize")
def materialize_rule(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    r = _rule(db, ctx, rid)
    return soc.materialize(db, r, user_id=ctx.user_id)


# ================================================================== 36 publish jobs
@router.get("/social/publish-jobs")
def publish_jobs(project_id: uuid.UUID | None = None, status: str | None = None, limit: int = Query(200, le=500),
                 db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(m.PublishJob, sm.SocialPublish, m.ContentItem, sm.SocialAccount, m.Schedule) \
        .join(sm.SocialPublish, sm.SocialPublish.publish_job_id == m.PublishJob.id) \
        .join(m.ContentItem, m.ContentItem.id == m.PublishJob.content_item_id) \
        .outerjoin(sm.SocialAccount, sm.SocialAccount.id == sm.SocialPublish.social_account_id) \
        .outerjoin(m.Schedule, m.Schedule.id == sm.SocialPublish.schedule_id) \
        .where(sm.SocialPublish.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        q = q.where(sm.SocialPublish.project_id == project_id)
    if status:
        q = q.where(m.PublishJob.status == status)
    out = []
    for pj, sp, it, a, s in db.execute(q.order_by(desc(sm.SocialPublish.created_at)).limit(limit)).all():
        out.append({"id": str(pj.id), "status": pj.status, "channel": pj.channel, "idempotency_key": pj.idempotency_key,
                    "title": it.title, "content_item_id": str(it.id), "account": a.name if a else None, "project_id": str(sp.project_id),
                    "scheduled_at": s.scheduled_at if s else None, "schedule_id": str(sp.schedule_id) if sp.schedule_id else None,
                    "external_post_id": sp.external_post_id, "external_url": sp.external_url, "published_at": sp.published_at,
                    "attempts": sp.attempts, "error": sp.error, "error_code": sp.error_code, "created_at": sp.created_at})
    counts = dict(db.execute(select(m.PublishJob.status, func.count()).join(sm.SocialPublish, sm.SocialPublish.publish_job_id == m.PublishJob.id)
                             .where(sm.SocialPublish.project_id.in_(visible_project_ids(db, ctx))).group_by(m.PublishJob.status)).all())
    return {"items": out, "counts": counts, "providers": soc.provider_status(db)}


class RetryIn(BaseModel):
    confirm_not_posted: bool = False


@router.post("/social/publish-jobs/{pjid}/retry")
def retry_publish(pjid: uuid.UUID, body: RetryIn, db=Depends(get_db), ctx: Ctx = Depends(require("publish"))):
    pj = db.get(m.PublishJob, pjid)
    sp = db.scalar(select(sm.SocialPublish).where(sm.SocialPublish.publish_job_id == pjid)) if pj else None
    if not pj or not sp:
        raise HTTPException(404, "Publish job not found")
    project_guard(db, ctx, sp.project_id)
    if pj.status != "FAILED":
        raise HTTPException(409, f"Only failed publish jobs can be retried (this one is {pj.status})")
    if sp.error_code == "outcome_unknown" and not body.confirm_not_posted:
        raise HTTPException(409, detail={"code": "confirm_required", "message": "The last attempt may have reached the platform. Check the "
                                         "account and confirm the post is not live before retrying."})
    ss = db.scalar(select(sm.SocialSchedule).where(sm.SocialSchedule.schedule_id == sp.schedule_id))
    if not ss:
        raise HTTPException(409, "The schedule for this post was removed")
    s = db.get(m.Schedule, ss.schedule_id)
    pj.status, ss.status, sp.error, sp.error_code = "QUEUED", "scheduled", None, None
    db.flush()
    soc.enqueue_publish(db, ctx.org_id, s, ctx.user_id, force=True, suffix=f":retry:{sp.attempts}:{secrets.token_hex(4)}")
    log(db, ctx, "social.publish_retried", f"publish_job:{pj.id}", {"confirmed_not_posted": body.confirm_not_posted})
    return {"ok": True}


# ================================================================== 33 content automation
@router.get("/social/content/drafts")
def drafts(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    batched = select(sm.ContentBatchItem.content_item_id)
    items = db.scalars(select(m.ContentItem).where(m.ContentItem.project_id == project_id, m.ContentItem.state == "draft")
                       .order_by(desc(m.ContentItem.created_at)).limit(500)).all()
    inb = set(db.scalars(batched.where(sm.ContentBatchItem.content_item_id.in_([i.id for i in items])))) if items else set()
    return [{**to_dict(i), "in_batch": i.id in inb} for i in items]


@router.get("/social/content/items")
def content_items(project_id: uuid.UUID, states: str = "approved,scheduled", db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    """Content items of a project in the given states (e.g. approved items to queue on a rule)."""
    project_guard(db, ctx, project_id)
    want = [x for x in states.split(",") if x in ("draft", "generated", "review", "approved", "scheduled", "published")]
    items = db.scalars(select(m.ContentItem).where(m.ContentItem.project_id == project_id, m.ContentItem.state.in_(want))
                       .order_by(desc(m.ContentItem.created_at)).limit(500)).all()
    return [{**to_dict(i), "body": soc.item_body(db, i)[:280]} for i in items]


class BatchIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    channel: str | None = None
    item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    topics: list[str] = Field(default_factory=list, max_length=200)
    instructions: str | None = Field(None, max_length=4000)


def _make_batch(db, ctx, project_id, name, channel, item_ids, topics, instructions, source, metas=None) -> sm.ContentBatch:
    if channel and channel not in soc.CHANNELS:
        raise HTTPException(400, f"channel must be one of {list(soc.CHANNELS)}")
    if not item_ids and not topics:
        raise HTTPException(400, "Pick at least one draft or provide topics")
    if len(item_ids) + len(topics) > 200:
        raise HTTPException(400, "At most 200 items per batch")
    b = sm.ContentBatch(organisation_id=ctx.org_id, project_id=project_id, name=name.strip(), source=source, channel=channel,
                        instructions=instructions, created_by=ctx.user_id)
    db.add(b)
    db.flush()
    pos = 0
    for iid in dict.fromkeys(item_ids):
        it = db.get(m.ContentItem, iid)
        if not it or it.project_id != project_id:
            raise HTTPException(400, "Every item must belong to the batch's project")
        if it.state != "draft":
            raise HTTPException(409, f"'{it.title or it.id}' is '{it.state}' — only drafts can be batched for generation")
        if channel and not it.channel:
            it.channel = channel
        db.add(sm.ContentBatchItem(organisation_id=ctx.org_id, batch_id=b.id, content_item_id=it.id, position=pos))
        pos += 1
    for i, t in enumerate(topics):
        t = (t or "").strip()
        if not t:
            continue
        if len(t) > 500:
            raise HTTPException(400, "Topics must be at most 500 characters")
        ch = (metas[i].get("channel") if metas else None) or channel
        it = m.ContentItem(organisation_id=ctx.org_id, project_id=project_id, type="social", state="draft", channel=ch, title=t)
        db.add(it)
        db.flush()
        if metas and (metas[i].get("link_url") or metas[i].get("media_url")):
            db.add(sm.SocialPostMeta(organisation_id=ctx.org_id, content_item_id=it.id, link_url=metas[i].get("link_url"),
                                     media_url=metas[i].get("media_url")))
        db.add(sm.ContentBatchItem(organisation_id=ctx.org_id, batch_id=b.id, content_item_id=it.id, position=pos))
        pos += 1
    if not pos:
        raise HTTPException(400, "No usable topics")
    db.flush()
    log(db, ctx, "content_batch.created", f"content_batch:{b.id}", {"items": pos, "source": source})
    return b


@router.post("/social/batches")
def create_batch(body: BatchIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    project_guard(db, ctx, body.project_id)
    b = _make_batch(db, ctx, body.project_id, body.name, body.channel, body.item_ids, body.topics, body.instructions,
                    "drafts" if body.item_ids else "topics")
    return batch_board(b.id, db, ctx)


@router.post("/social/batches/csv")
async def create_batch_csv(project_id: uuid.UUID = Form(...), name: str = Form(..., max_length=200), channel: str | None = Form(None),
                           instructions: str | None = Form(None, max_length=4000), file: UploadFile = File(...),
                           db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    """CSV columns: topic (or title) — required; optional channel, link_url, media_url."""
    project_guard(db, ctx, project_id)
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(413, "CSV too large (2 MB max)")
    try:
        rdr = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    except UnicodeDecodeError:
        raise HTTPException(400, "File must be UTF-8 CSV")
    cols = {(c or "").strip().lower(): c for c in (rdr.fieldnames or [])}
    tcol = cols.get("topic") or cols.get("title")
    if not tcol:
        raise HTTPException(400, "CSV needs a 'topic' (or 'title') column")
    topics, metas = [], []
    for i, r in enumerate(rdr, 2):
        t = (r.get(tcol) or "").strip()
        if not t:
            continue
        ch = (r.get(cols["channel"]) or "").strip().lower() if "channel" in cols else ""
        if ch and ch not in soc.CHANNELS:
            raise HTTPException(400, f"Row {i}: channel must be one of {list(soc.CHANNELS)}")
        meta = {"channel": ch or None}
        for k in ("link_url", "media_url"):
            v = (r.get(cols[k]) or "").strip() if k in cols else ""
            if v and not URL_RE.match(v):
                raise HTTPException(400, f"Row {i}: {k} must be an http(s) URL")
            meta[k] = v or None
        topics.append(t)
        metas.append(meta)
    b = _make_batch(db, ctx, project_id, name, channel or None, [], topics, instructions, "csv", metas)
    return batch_board(b.id, db, ctx)


def _batch(db, ctx, bid) -> sm.ContentBatch:
    b = db.get(sm.ContentBatch, bid)
    if not b:
        raise HTTPException(404, "Batch not found")
    project_guard(db, ctx, b.project_id)
    return b


BOARD_COLUMNS = ["draft", "generated", "review", "approved", "scheduled", "published", "failed"]


def _column(it: m.ContentItem, sched: str | None) -> str:
    if it.state in soc.APPROVED and sched in ("scheduled", "publishing"):
        return "scheduled"
    if it.state in soc.APPROVED and sched == "failed":
        return "failed"
    return it.state if it.state in BOARD_COLUMNS else "draft"


@router.get("/social/batches")
def list_batches(project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    q = select(sm.ContentBatch).where(sm.ContentBatch.project_id.in_(visible_project_ids(db, ctx)))
    if project_id:
        q = q.where(sm.ContentBatch.project_id == project_id)
    out = []
    for b in db.scalars(q.order_by(desc(sm.ContentBatch.created_at)).limit(100)):
        states = dict(db.execute(select(m.ContentItem.state, func.count()).join(sm.ContentBatchItem, sm.ContentBatchItem.content_item_id == m.ContentItem.id)
                                 .where(sm.ContentBatchItem.batch_id == b.id).group_by(m.ContentItem.state)).all())
        out.append({**to_dict(b), "states": states, "total": sum(states.values())})
    return out


@router.get("/social/batches/{bid}")
def batch_board(bid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    b = _batch(db, ctx, bid)
    rowsq = db.execute(select(sm.ContentBatchItem, m.ContentItem).join(m.ContentItem, m.ContentItem.id == sm.ContentBatchItem.content_item_id)
                       .where(sm.ContentBatchItem.batch_id == b.id).order_by(sm.ContentBatchItem.position)).all()
    ids = [it.id for _, it in rowsq]
    sched = {}
    if ids:
        for ss, s in db.execute(select(sm.SocialSchedule, m.Schedule).join(m.Schedule, m.Schedule.id == sm.SocialSchedule.schedule_id)
                                .where(sm.SocialSchedule.content_item_id.in_(ids), sm.SocialSchedule.status != "cancelled")
                                .order_by(m.Schedule.scheduled_at)):
            sched.setdefault(ss.content_item_id, []).append({"schedule_id": str(s.id), "at": s.scheduled_at, "status": ss.status})
    items = []
    for bi, it in rowsq:
        ss = sched.get(it.id, [])
        agg = "failed" if any(x["status"] == "failed" for x in ss) else ("scheduled" if any(x["status"] in ("scheduled", "publishing") for x in ss)
                                                                         else ("published" if ss else None))
        a = _approval_for(db, it.id)
        items.append({"id": str(it.id), "title": it.title, "channel": it.channel, "state": it.state, "column": _column(it, agg),
                      "gen_status": bi.gen_status, "gen_error": bi.gen_error, "body": soc.item_body(db, it)[:400],
                      "approval_status": a.status if a else None, "schedules": ss})
    cols = {c: [i for i in items if i["column"] == c] for c in BOARD_COLUMNS}
    conn = ih.connected_map(db)
    return {**to_dict(b), "columns": BOARD_COLUMNS, "board": cols, "counts": {c: len(v) for c, v in cols.items()}, "items": items,
            "ai_connected": any(conn.get(p) == "CONNECTED" for p in AI_PROVIDERS)}


@router.post("/social/batches/{bid}/generate")
def batch_generate(bid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    b = _batch(db, ctx, bid)
    conn = ih.connected_map(db)
    connected = any(conn.get(p) == "CONNECTED" for p in AI_PROVIDERS)
    queued = 0
    for bi, it in db.execute(select(sm.ContentBatchItem, m.ContentItem).join(m.ContentItem, m.ContentItem.id == sm.ContentBatchItem.content_item_id)
                             .where(sm.ContentBatchItem.batch_id == b.id)).all():
        if it.state != "draft" or bi.gen_status == "queued":
            continue
        if not connected:
            bi.gen_status, bi.gen_error = "not_connected", "NOT CONNECTED: no AI provider (Claude, OpenAI, Gemini or Perplexity). The item stays a draft."
            continue
        j = enqueue(db, ctx.org_id, "social_generate", {"batch_item_id": str(bi.id)}, created_by=ctx.user_id,
                    idempotency_key=f"socialgen:{bi.id}:{secrets.token_hex(4)}")
        bi.gen_status, bi.gen_error, bi.job_id = "queued", None, j.id
        queued += 1
    log(db, ctx, "content_batch.generate", f"content_batch:{b.id}", {"queued": queued, "ai_connected": connected})
    if not connected:
        return {"connected": False, "queued": 0, "message": "No AI provider is NOT CONNECTED — connect Claude, OpenAI, Gemini or Perplexity "
                                                            "in Integrations. Items stay drafts; you can still write them manually."}
    return {"connected": True, "queued": queued}


@router.post("/social/batches/{bid}/request-approval")
def batch_request_approval(bid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    b = _batch(db, ctx, bid)
    done, skipped = 0, []
    for it in db.scalars(select(m.ContentItem).join(sm.ContentBatchItem, sm.ContentBatchItem.content_item_id == m.ContentItem.id)
                         .where(sm.ContentBatchItem.batch_id == b.id)):
        if it.state not in ("generated", "review"):
            continue
        try:
            _request_approval(db, ctx, it, f"Content batch: {b.name}")
            done += 1
        except HTTPException as e:
            skipped.append({"id": str(it.id), "reason": e.detail if isinstance(e.detail, str) else e.detail.get("message")})
    log(db, ctx, "content_batch.approval_requested", f"content_batch:{b.id}", {"items": done})
    return {"requested": done, "skipped": skipped}


class ApproveIn(BaseModel):
    item_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=1000)


@router.post("/social/batches/{bid}/approve")
def batch_approve(bid: uuid.UUID, body: ApproveIn, db=Depends(get_db), ctx: Ctx = Depends(require("approve")), _c: Ctx = Depends(deny_client)):
    b = _batch(db, ctx, bid)
    members = set(db.scalars(select(sm.ContentBatchItem.content_item_id).where(sm.ContentBatchItem.batch_id == b.id)))
    res = {}
    for iid in body.item_ids:
        if iid not in members:
            raise HTTPException(400, "Item is not in this batch")
        res[str(iid)] = _approve(db, ctx, db.get(m.ContentItem, iid), body.notes)
    log(db, ctx, "content_batch.approved", f"content_batch:{b.id}", {"results": res})
    return {"results": res}


class BatchScheduleIn(BaseModel):
    mode: str  # rule / spread
    rule_id: uuid.UUID | None = None
    account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    start_at: datetime | None = None
    interval_hours: float = Field(24, ge=0.25, le=24 * 30)
    campaign_id: uuid.UUID | None = None


@router.post("/social/batches/{bid}/schedule")
def batch_schedule(bid: uuid.UUID, body: BatchScheduleIn, db=Depends(get_db), ctx: Ctx = Depends(require("publish")), _c: Ctx = Depends(deny_client)):
    b = _batch(db, ctx, bid)
    active = select(sm.SocialSchedule.content_item_id).where(sm.SocialSchedule.status.in_(("scheduled", "publishing", "published")))
    items = db.scalars(select(m.ContentItem).join(sm.ContentBatchItem, sm.ContentBatchItem.content_item_id == m.ContentItem.id)
                       .where(sm.ContentBatchItem.batch_id == b.id, m.ContentItem.state.in_(soc.APPROVED), m.ContentItem.id.not_in(active))
                       .order_by(sm.ContentBatchItem.position)).all()
    if not items:
        raise HTTPException(409, detail={"code": "nothing_to_schedule", "message": "No approved, unscheduled items in this batch"})
    if body.mode == "rule":
        r = _rule(db, ctx, body.rule_id) if body.rule_id else None
        if not r or r.project_id != b.project_id:
            raise HTTPException(400, "Choose a rule in this batch's project")
        if r.mode != "queue":
            raise HTTPException(400, "Batches can only be added to queue rules")
        r.item_ids = list(dict.fromkeys([*(r.item_ids or []), *[str(i.id) for i in items]]))
        db.flush()
        res = soc.materialize(db, r, user_id=ctx.user_id)
        log(db, ctx, "content_batch.scheduled", f"content_batch:{b.id}", {"rule": str(r.id), "scheduled": res["created"]})
        return {"mode": "rule", "queued": len(items), "materialized": res}
    if body.mode != "spread":
        raise HTTPException(400, "mode must be rule or spread")
    if not body.account_ids or not body.start_at:
        raise HTTPException(400, "account_ids and start_at are required")
    start = _aware(body.start_at)
    if start < _now() - timedelta(minutes=1):
        raise HTTPException(400, "start_at must be in the future")
    accts = [_account(db, ctx, a) for a in dict.fromkeys(body.account_ids)]
    if any(a.project_id != b.project_id for a in accts):
        raise HTTPException(400, "Accounts must belong to the batch's project")
    if body.campaign_id:
        camp = db.scalar(select(sm.SocialCampaign).where(sm.SocialCampaign.campaign_id == body.campaign_id))
        if not camp or camp.project_id != b.project_id:
            raise HTTPException(400, "Campaign not in this project")
    else:
        camp = None
    out, skipped = [], []
    for n, it in enumerate(items):
        at = start + timedelta(hours=body.interval_hours * n)
        soc.check_campaign_window(camp, at)
        for a in accts:
            try:
                with db.begin_nested():
                    ss = soc.schedule_item(db, ctx.org_id, it, a, at, user_id=ctx.user_id, campaign_id=body.campaign_id)
                out.append({"item": str(it.id), "schedule_id": str(ss.schedule_id), "at": at, "account": a.name})
            except HTTPException as e:
                skipped.append({"item": str(it.id), "account": a.name, "reason": e.detail if isinstance(e.detail, str) else e.detail.get("message")})
    log(db, ctx, "content_batch.scheduled", f"content_batch:{b.id}", {"scheduled": len(out)})
    return {"mode": "spread", "scheduled": out, "skipped": skipped}


# ================================================================== 40 analytics
@router.get("/social/analytics")
def analytics(project_id: uuid.UUID | None = None, channel: str | None = None, days: int = Query(30, ge=1, le=365),
              db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    pids = _vis(db, ctx)
    if project_id:
        project_guard(db, ctx, project_id)
        pids = [project_id]
    if channel and channel not in soc.CHANNELS:
        raise HTTPException(400, "Unknown channel")
    since = _now() - timedelta(days=days)
    snaps = soc.latest_snapshots(db, pids, channel, since)
    pnames = {p.id: p.name for p in db.scalars(select(m.Project))}
    anames = {a.id: a.name for a in db.scalars(select(sm.SocialAccount))}
    agg = soc.aggregate(snaps, pnames, anames)
    fq = select(m.PublishJob, sm.SocialPublish, m.ContentItem, sm.SocialAccount).join(sm.SocialPublish, sm.SocialPublish.publish_job_id == m.PublishJob.id) \
        .join(m.ContentItem, m.ContentItem.id == m.PublishJob.content_item_id).outerjoin(sm.SocialAccount, sm.SocialAccount.id == sm.SocialPublish.social_account_id) \
        .where(sm.SocialPublish.project_id.in_(pids), m.PublishJob.status == "FAILED", sm.SocialPublish.created_at >= since)
    if channel:
        fq = fq.where(m.PublishJob.channel == channel)
    failed = [{"id": str(pj.id), "title": it.title, "account": a.name if a else None, "channel": pj.channel, "error": sp.error,
               "error_code": sp.error_code, "at": sp.created_at} for pj, sp, it, a in db.execute(fq.order_by(desc(sm.SocialPublish.created_at)).limit(50)).all()]
    pub = dict(db.execute(select(m.PublishJob.status, func.count()).join(sm.SocialPublish, sm.SocialPublish.publish_job_id == m.PublishJob.id)
                          .where(sm.SocialPublish.project_id.in_(pids), sm.SocialPublish.created_at >= since).group_by(m.PublishJob.status)).all())
    return {**agg, "failed_alerts": failed, "publishing": pub, "providers": soc.provider_status(db), "days": days,
            "sources": sorted({s.source for s in snaps})}


@router.post("/social/analytics/import")
async def analytics_import(project_id: uuid.UUID = Form(...), file: UploadFile = File(...), db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    project_guard(db, ctx, project_id)
    raw = await file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "File too large (10 MB max)")
    try:
        res = soc.import_metrics_csv(db, ctx.org_id, project_id, raw)
    except soc.CsvError as e:
        raise HTTPException(400, str(e))
    log(db, ctx, "social.analytics_imported", f"project:{project_id}", res)
    return res


class SyncIn(BaseModel):
    project_id: uuid.UUID | None = None


@router.post("/social/analytics/sync")
def analytics_sync(body: SyncIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    if body.project_id:
        project_guard(db, ctx, body.project_id)
    st = soc.provider_status(db)
    if not any(v == "CONNECTED" for v in st.values()):
        raise HTTPException(409, detail={"code": "not_connected", "message": "Meta, LinkedIn and X are NOT CONNECTED — connect one in "
                                         "Integrations or import a CSV of post metrics."})
    j = enqueue(db, ctx.org_id, "social_metrics_sync", {"project_id": str(body.project_id) if body.project_id else None}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


# ================================================================== 37 report automation
class ReportScheduleIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    frequency: str = "weekly"
    contents: list[str] = Field(..., min_length=1)
    recipient_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    recipient_emails: list[str] = Field(default_factory=list, max_length=50)
    channels: dict = Field(default_factory=lambda: {"in_app": True, "teams": False, "email": False})
    white_label: bool = False
    first_run_at: datetime | None = None
    enabled: bool = True


EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[a-z]{2,}$", re.I)


def _validate_report(db, ctx, body):
    if body.frequency not in srep.FREQUENCIES:
        raise HTTPException(400, "frequency must be weekly or monthly")
    bad = [c for c in body.contents if c not in srep.CONTENTS]
    if bad:
        raise HTTPException(400, f"Unknown contents: {bad}; choose from {list(srep.CONTENTS)}")
    for e in body.recipient_emails:
        if not EMAIL_RE.match(e):
            raise HTTPException(400, f"Invalid email: {e}")
    for u in body.recipient_user_ids:
        if not db.get(m.User, u):
            raise HTTPException(400, "Unknown recipient user")
    if body.white_label:
        ent.require_feature(db, ctx.org_id, "white_label")
    return {"in_app": True, "teams": bool(body.channels.get("teams")), "email": bool(body.channels.get("email"))}


def _report_out(db, rs: sm.ReportSchedule) -> dict:
    js = db.get(m.JobSchedule, rs.job_schedule_id) if rs.job_schedule_id else None
    last = db.scalar(select(sm.ReportDelivery).where(sm.ReportDelivery.report_schedule_id == rs.id).order_by(desc(sm.ReportDelivery.created_at)).limit(1))
    return {**to_dict(rs), "next_run_at": js.next_run_at if js and js.enabled else None,
            "last_delivery": {"id": str(last.id), "status": last.status, "created_at": last.created_at} if last else None}


@router.get("/social/report-schedules")
def list_report_schedules(project_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    q = select(sm.ReportSchedule).order_by(sm.ReportSchedule.created_at)
    if project_id:
        q = q.where(sm.ReportSchedule.project_id == project_id)
    teams_ch = db.scalars(select(sm.TeamsChannel).where(sm.TeamsChannel.enabled)).all()
    return {"schedules": [_report_out(db, r) for r in db.scalars(q)], "contents": srep.CONTENTS,
            "email": {"configured": bool(srep.smtp_settings()), "message": None if srep.smtp_settings() else
                      "Email is NOT CONNECTED (the operator has not set SMTP_HOST / SMTP_FROM). Reports are still stored and announced in-app."},
            "teams_channels": [{"id": str(c.id), "name": c.name, "project_id": str(c.project_id) if c.project_id else None,
                                "report_ready": "report_ready" in (c.events or [])} for c in teams_ch],
            "plan": {"scheduled_reports": bool(ent.feature(db, ctx.org_id, "scheduled_reports").get("enabled")),
                     "white_label": bool(ent.feature(db, ctx.org_id, "white_label").get("enabled"))}}


@router.post("/social/report-schedules")
def create_report_schedule(body: ReportScheduleIn, db=Depends(get_db), ctx: Ctx = Depends(require("export")), _c: Ctx = Depends(deny_client)):
    ent.require_feature(db, ctx.org_id, "scheduled_reports")
    project_guard(db, ctx, body.project_id)
    ch = _validate_report(db, ctx, body)
    rs = sm.ReportSchedule(organisation_id=ctx.org_id, project_id=body.project_id, name=body.name.strip(), frequency=body.frequency,
                           contents=list(dict.fromkeys(body.contents)), recipient_user_ids=list(dict.fromkeys(body.recipient_user_ids)),
                           recipient_emails=sorted({e.lower() for e in body.recipient_emails}), channels=ch, white_label=body.white_label,
                           enabled=body.enabled, created_by=ctx.user_id)
    db.add(rs)
    db.flush()
    js = m.JobSchedule(organisation_id=ctx.org_id, kind="social_report_run", payload={"report_schedule_id": str(rs.id)},
                       frequency=body.frequency, next_run_at=_aware(body.first_run_at) if body.first_run_at else _now() + timedelta(days=srep.PERIOD_DAYS[body.frequency]),
                       enabled=body.enabled, created_by=ctx.user_id)
    db.add(js)
    db.flush()
    rs.job_schedule_id = js.id
    log(db, ctx, "report_automation.created", f"report_schedule:{rs.id}", {"frequency": rs.frequency, "contents": rs.contents})
    return _report_out(db, rs)


def _rsched(db, ctx, rid) -> sm.ReportSchedule:
    rs = db.get(sm.ReportSchedule, rid)
    if not rs:
        raise HTTPException(404, "Report schedule not found")
    project_guard(db, ctx, rs.project_id)
    return rs


class ReportSchedulePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    frequency: str | None = None
    contents: list[str] | None = None
    recipient_user_ids: list[uuid.UUID] | None = Field(None, max_length=50)
    recipient_emails: list[str] | None = Field(None, max_length=50)
    channels: dict | None = None
    white_label: bool | None = None
    enabled: bool | None = None


@router.patch("/social/report-schedules/{rid}")
def patch_report_schedule(rid: uuid.UUID, body: ReportSchedulePatch, db=Depends(get_db), ctx: Ctx = Depends(require("export")),
                          _c: Ctx = Depends(deny_client)):
    rs = _rsched(db, ctx, rid)
    merged = ReportScheduleIn(project_id=rs.project_id, name=body.name or rs.name, frequency=body.frequency or rs.frequency,
                              contents=body.contents or rs.contents,
                              recipient_user_ids=body.recipient_user_ids if body.recipient_user_ids is not None else list(rs.recipient_user_ids or []),
                              recipient_emails=body.recipient_emails if body.recipient_emails is not None else rs.recipient_emails,
                              channels=body.channels if body.channels is not None else rs.channels,
                              white_label=body.white_label if body.white_label is not None else rs.white_label)
    ch = _validate_report(db, ctx, merged)
    rs.name, rs.frequency, rs.contents, rs.channels, rs.white_label = merged.name, merged.frequency, merged.contents, ch, merged.white_label
    rs.recipient_user_ids, rs.recipient_emails = merged.recipient_user_ids, sorted({e.lower() for e in merged.recipient_emails})
    if body.enabled is not None:
        rs.enabled = body.enabled
    js = db.get(m.JobSchedule, rs.job_schedule_id) if rs.job_schedule_id else None
    if js:
        js.frequency, js.enabled = rs.frequency, rs.enabled
    return _report_out(db, rs)


@router.delete("/social/report-schedules/{rid}")
def delete_report_schedule(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete")), _c: Ctx = Depends(deny_client)):
    rs = _rsched(db, ctx, rid)
    if rs.job_schedule_id:
        js = db.get(m.JobSchedule, rs.job_schedule_id)
        rs.job_schedule_id = None
        db.flush()
        if js:
            db.delete(js)
    db.delete(rs)
    log(db, ctx, "report_automation.deleted", f"report_schedule:{rid}")
    return {"ok": True}


@router.post("/social/report-schedules/{rid}/run")
def run_report_now(rid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("export")), _c: Ctx = Depends(deny_client)):
    rs = _rsched(db, ctx, rid)
    ent.require_feature(db, ctx.org_id, "report_export")
    j = enqueue(db, ctx.org_id, "social_report_run", {"report_schedule_id": str(rs.id), "triggered_by": "manual"}, created_by=ctx.user_id)
    log(db, ctx, "report_automation.run", f"report_schedule:{rs.id}")
    return {"job_id": str(j.id)}


@router.get("/social/report-deliveries")
def list_deliveries(schedule_id: uuid.UUID | None = None, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    q = select(sm.ReportDelivery).order_by(desc(sm.ReportDelivery.created_at)).limit(200)
    if schedule_id:
        q = q.where(sm.ReportDelivery.report_schedule_id == schedule_id)
    names = {r.id: r.name for r in db.scalars(select(sm.ReportSchedule))}
    pnames = {p.id: p.name for p in db.scalars(select(m.Project))}
    return [{**to_dict(d), "schedule": names.get(d.report_schedule_id), "project": pnames.get(d.project_id)} for d in db.scalars(q)]


@router.get("/social/report-deliveries/{did}/files/{fid}")
def delivery_file(did: uuid.UUID, fid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("export"))):
    d = db.get(sm.ReportDelivery, did)
    if not d:
        raise HTTPException(404)
    project_guard(db, ctx, d.project_id)
    if str(fid) not in {f["stored_file_id"] for f in d.files or []}:
        raise HTTPException(404)
    f = db.get(m.StoredFile, fid)
    if not f:
        raise HTTPException(404)
    log(db, ctx, "report_automation.downloaded", f"stored_file:{fid}")
    return FileResponse(rep.path_of(f), media_type=f.content_type, filename=f.filename)


# ================================================================== 46 Microsoft Teams
class TeamsChannelIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    project_id: uuid.UUID | None = None
    webhook_url: str | None = Field(None, max_length=2048)
    use_hub: bool = False
    events: list[str] = Field(default_factory=lambda: list(teams.EVENTS))
    enabled: bool = True


def _teams_out(c: sm.TeamsChannel, pnames: dict) -> dict:
    return {**to_dict(c, exclude=("webhook_ref",)), "project": pnames.get(c.project_id)}


def _hub_teams(db) -> dict:
    c = db.scalar(select(m.IntegrationConnection).where(m.IntegrationConnection.provider_key == "ms_teams"))
    return {"status": c.status if c else "NOT_CONNECTED", "has_webhook": bool(c and (c.config or {}).get("webhook_url"))}


@router.get("/social/teams")
def teams_overview(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    pnames = {p.id: p.name for p in db.scalars(select(m.Project))}
    chans = db.scalars(select(sm.TeamsChannel).order_by(sm.TeamsChannel.created_at)).all()
    return {"events": teams.EVENTS, "hub": _hub_teams(db), "channels": [_teams_out(c, pnames) for c in chans]}


def _teams_validate(db, ctx, body) -> None:
    bad = [e for e in body.events if e not in teams.EVENTS]
    if bad:
        raise HTTPException(400, f"Unknown events {bad}; choose from {list(teams.EVENTS)}")
    if body.project_id:
        project_guard(db, ctx, body.project_id)


@router.post("/social/teams/channels")
def create_teams_channel(body: TeamsChannelIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    _teams_validate(db, ctx, body)
    c = sm.TeamsChannel(organisation_id=ctx.org_id, project_id=body.project_id, name=body.name.strip(), events=list(dict.fromkeys(body.events)),
                        enabled=body.enabled, use_hub=body.use_hub, created_by=ctx.user_id)
    if body.use_hub:
        if not _hub_teams(db)["has_webhook"]:
            raise HTTPException(409, detail={"code": "not_connected", "message": "Microsoft Teams is NOT CONNECTED in the Integrations Hub "
                                             "with a webhook URL — connect it there or paste a channel webhook URL here."})
    else:
        if not body.webhook_url or not teams.valid_teams_url(body.webhook_url.strip()):
            raise HTTPException(400, "Paste a Microsoft Teams incoming-webhook URL (https://…webhook.office.com/… or a Teams Workflows URL)")
        c.webhook_ref = vault.put(ctx.org_id, body.webhook_url.strip())
        c.webhook_hint = urlparse(body.webhook_url).hostname + "/…" + body.webhook_url.strip()[-6:]
    db.add(c)
    db.flush()
    teams.ensure_sweep(db, ctx.org_id)
    log(db, ctx, "teams.channel_created", f"teams_channel:{c.id}", {"events": c.events, "project_id": str(c.project_id) if c.project_id else None})
    return _teams_out(c, {})


class TeamsPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    webhook_url: str | None = Field(None, max_length=2048)
    events: list[str] | None = None
    enabled: bool | None = None


def _tchan(db, ctx, cid) -> sm.TeamsChannel:
    c = db.get(sm.TeamsChannel, cid)
    if not c:
        raise HTTPException(404, "Channel not found")
    if c.project_id:
        project_guard(db, ctx, c.project_id)
    return c


@router.patch("/social/teams/channels/{cid}")
def patch_teams_channel(cid: uuid.UUID, body: TeamsPatch, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    c = _tchan(db, ctx, cid)
    if body.events is not None:
        bad = [e for e in body.events if e not in teams.EVENTS]
        if bad:
            raise HTTPException(400, f"Unknown events {bad}")
        c.events = list(dict.fromkeys(body.events))
    if body.name:
        c.name = body.name.strip()
    if body.enabled is not None:
        c.enabled = body.enabled
    if body.webhook_url:
        u = body.webhook_url.strip()
        if not teams.valid_teams_url(u):
            raise HTTPException(400, "Not a Microsoft Teams webhook URL")
        old = c.webhook_ref
        c.webhook_ref, c.webhook_hint, c.use_hub = vault.put(ctx.org_id, u), urlparse(u).hostname + "/…" + u[-6:], False
        if old:
            vault.remove(ctx.org_id, old)
    teams.ensure_sweep(db, ctx.org_id)
    return _teams_out(c, {})


@router.delete("/social/teams/channels/{cid}")
def delete_teams_channel(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    c = _tchan(db, ctx, cid)
    if c.webhook_ref:
        vault.remove(ctx.org_id, c.webhook_ref)
    db.delete(c)
    log(db, ctx, "teams.channel_deleted", f"teams_channel:{cid}")
    return {"ok": True}


@router.post("/social/teams/channels/{cid}/test")
def test_teams_channel(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    c = _tchan(db, ctx, cid)
    org = db.get(m.Organisation, ctx.org_id)
    d = teams.send(db, ctx.org_id, c, "test", f"Test message from {org.name}",
                   f"This channel will receive: {', '.join(teams.EVENTS[e] for e in c.events) or 'no events yet'}.", link="/notifications/teams")
    log(db, ctx, "teams.test_sent", f"teams_channel:{cid}", {"status": d.status})
    return to_dict(d)


@router.get("/social/teams/log")
def teams_log(channel_id: uuid.UUID | None = None, limit: int = Query(100, le=500), db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    q = select(sm.TeamsDelivery).order_by(desc(sm.TeamsDelivery.created_at)).limit(limit)
    if channel_id:
        q = q.where(sm.TeamsDelivery.channel_id == channel_id)
    names = {c.id: c.name for c in db.scalars(select(sm.TeamsChannel))}
    return [{**to_dict(d), "channel": names.get(d.channel_id)} for d in db.scalars(q)]


# ================================================================== 47 white-label
class WhiteLabelIn(BaseModel):
    portal_title: str | None = Field(None, max_length=120)
    support_email: str | None = Field(None, max_length=200)


@router.get("/social/white-label")
def white_label(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    o = db.get(m.Organisation, ctx.org_id)
    st = db.get(sm.SocialOrgState, ctx.org_id)
    doms = db.scalars(select(sm.CustomDomain).order_by(sm.CustomDomain.created_at)).all()
    return {"branding": {k: v for k, v in (o.branding or {}).items() if not k.startswith("_")}, "org_name": o.name,
            "portal_title": st.portal_title if st else None, "support_email": st.support_email if st else None,
            "plan": {"white_label": bool(ent.feature(db, ctx.org_id, "white_label").get("enabled")),
                     "report_branding": bool(ent.feature(db, ctx.org_id, "report_branding").get("enabled"))},
            "cname_target": dom.target(), "dns_checker": "dnspython" if dom.HAVE_DNSPYTHON else "socket (CNAME only)",
            "operator_steps": dom.OPERATOR_STEPS,
            "domains": [{**to_dict(d), "txt_name": dom.txt_name(d.domain), "txt_value": dom.txt_value(d.verification_token)} for d in doms]}


@router.put("/social/white-label")
def put_white_label(body: WhiteLabelIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    ent.require_feature(db, ctx.org_id, "white_label")
    if body.support_email and not EMAIL_RE.match(body.support_email):
        raise HTTPException(400, "Invalid support email")
    st = teams.state(db, ctx.org_id)
    st.portal_title, st.support_email = (body.portal_title or "").strip() or None, body.support_email or None
    log(db, ctx, "white_label.updated", f"organisation:{ctx.org_id}", body.model_dump())
    return white_label(db, ctx)


class DomainIn(BaseModel):
    domain: str = Field(..., max_length=253)


@router.post("/social/white-label/domains")
def add_domain(body: DomainIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    ent.require_feature(db, ctx.org_id, "white_label")
    d = dom.clean(body.domain)
    if not d:
        raise HTTPException(400, "Enter a hostname you control, like portal.youragency.com")
    if db.scalar(select(sm.CustomDomain.id).where(sm.CustomDomain.domain == d)):
        raise HTTPException(409, "Domain already added")
    if db.scalar(select(func.count()).select_from(sm.CustomDomain)) >= 10:
        raise HTTPException(400, "At most 10 custom domains")
    cd = sm.CustomDomain(organisation_id=ctx.org_id, domain=d, verification_token=dom.new_token())
    db.add(cd)
    db.flush()
    log(db, ctx, "white_label.domain_added", f"custom_domain:{cd.id}", {"domain": d})
    return {**to_dict(cd), "txt_name": dom.txt_name(d), "txt_value": dom.txt_value(cd.verification_token), "cname_target": dom.target()}


@router.post("/social/white-label/domains/{did}/verify")
def verify_domain(did: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    cd = db.get(sm.CustomDomain, did)
    if not cd:
        raise HTTPException(404, "Domain not found")
    dom.verify(cd)
    if cd.status == "verified":
        o = db.get(m.Organisation, ctx.org_id)
        if not (o.branding or {}).get("custom_domain"):
            o.branding = {**(o.branding or {}), "custom_domain": cd.domain}
    log(db, ctx, "white_label.domain_checked", f"custom_domain:{cd.id}", {"status": cd.status})
    return {**to_dict(cd), "txt_name": dom.txt_name(cd.domain), "txt_value": dom.txt_value(cd.verification_token)}


@router.delete("/social/white-label/domains/{did}")
def delete_domain(did: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("manage_users"))):
    cd = db.get(sm.CustomDomain, did)
    if not cd:
        raise HTTPException(404, "Domain not found")
    o = db.get(m.Organisation, ctx.org_id)
    if (o.branding or {}).get("custom_domain") == cd.domain:
        o.branding = {k: v for k, v in o.branding.items() if k != "custom_domain"}
    db.delete(cd)
    log(db, ctx, "white_label.domain_removed", f"custom_domain:{did}", {"domain": cd.domain})
    return {"ok": True}


# ================================================================== 23 Chrome extension — token management
EXT_SCOPES = {"read": "Read projects", "audit:create": "Queue site audits", "keyword:create": "Track keywords",
              "competitor:create": "Save competitors"}
EXT_PREFIX = "gqx_"


def _ext_dir() -> Path:
    return Path(os.environ.get("EXTENSION_DIR") or Path(__file__).resolve().parents[3] / "extension")


class TokenIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: list(EXT_SCOPES))
    expires_days: int | None = Field(90, ge=1, le=365)


@router.get("/social/extension/tokens")
def list_tokens(db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c: Ctx = Depends(deny_client)):
    q = select(sm.ExtensionToken).order_by(desc(sm.ExtensionToken.created_at))
    if not can(ctx.role, "manage_users"):
        q = q.where(sm.ExtensionToken.user_id == ctx.user_id)
    names = {u.id: (u.name or u.email) for u in db.scalars(select(m.User))}
    return {"scopes": EXT_SCOPES, "tokens": [{**to_dict(t, exclude=("token_hash",)), "user": names.get(t.user_id),
                                              "active": not t.revoked_at and (not t.expires_at or t.expires_at > _now())}
                                             for t in db.scalars(q)]}


@router.post("/social/extension/tokens")
def create_token(body: TokenIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c: Ctx = Depends(deny_client)):
    bad = [s for s in body.scopes if s not in EXT_SCOPES]
    if bad or not body.scopes:
        raise HTTPException(400, f"scopes must be a non-empty subset of {list(EXT_SCOPES)}")
    if db.scalar(select(func.count()).select_from(sm.ExtensionToken).where(sm.ExtensionToken.user_id == ctx.user_id,
                                                                          sm.ExtensionToken.revoked_at.is_(None))) >= 10:
        raise HTTPException(400, "At most 10 active extension tokens per user — revoke an old one first")
    raw = EXT_PREFIX + secrets.token_urlsafe(32)
    t = sm.ExtensionToken(organisation_id=ctx.org_id, user_id=ctx.user_id, name=body.name.strip(), prefix=raw[:12],
                          token_hash=hashlib.sha256(raw.encode()).hexdigest(), scopes=list(dict.fromkeys(body.scopes)),
                          expires_at=_now() + timedelta(days=body.expires_days) if body.expires_days else None)
    db.add(t)
    db.flush()
    log(db, ctx, "extension.token_created", f"extension_token:{t.id}", {"scopes": t.scopes})
    return {**to_dict(t, exclude=("token_hash",)), "token": raw, "note": "Copy this token now — it is shown only once."}


@router.delete("/social/extension/tokens/{tid}")
def revoke_token(tid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c: Ctx = Depends(deny_client)):
    t = db.get(sm.ExtensionToken, tid)
    if not t or (t.user_id != ctx.user_id and not can(ctx.role, "manage_users")):
        raise HTTPException(404, "Token not found")
    t.revoked_at = t.revoked_at or _now()
    log(db, ctx, "extension.token_revoked", f"extension_token:{t.id}")
    return {"ok": True}


@router.get("/social/extension/download")
def download_extension(request: Request, ctx: Ctx = Depends(require("view")), _c: Ctx = Depends(deny_client)):
    root = _ext_dir()
    if not (root / "manifest.json").is_file():
        raise HTTPException(503, "Extension sources are not deployed on this server (EXTENSION_DIR)")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts) and rel.name != "config.json":
                z.write(p, f"genranq-extension/{rel.as_posix()}")
        base = str(request.base_url).rstrip("/")
        z.writestr("genranq-extension/config.json", '{"default_api_base": "%s"}\n' % base.replace('"', ""))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="genranq-extension.zip"'})


# ================================================================== 23 Chrome extension — token-authenticated API
@dataclass
class ExtAuth:
    db: Session
    ctx: Ctx
    scopes: list


def ext_auth(request: Request, authorization: str | None = Header(None)) -> Iterator[ExtAuth]:
    """Validates an extension personal access token and opens the tenant session for its owner.
    PATs work only on /api/social/ext/* — the regular API rejects them (they are not JWTs)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing extension token")
    raw = authorization.split(" ", 1)[1].strip()
    if not raw.startswith(EXT_PREFIX) or len(raw) > 200:
        raise HTTPException(401, "Use an extension token created on the Extension page")
    h = hashlib.sha256(raw.encode()).hexdigest()
    with system_session() as sdb:
        row = sdb.execute(select(sm.ExtensionToken, m.User).join(m.User, m.User.id == sm.ExtensionToken.user_id)
                          .where(sm.ExtensionToken.token_hash == h)).first()
        if not row:
            raise HTTPException(401, "Invalid extension token")
        t, u = row
        if t.revoked_at or (t.expires_at and t.expires_at < _now()):
            raise HTTPException(401, "Extension token revoked or expired")
        if not u.is_active or u.organisation_id != t.organisation_id:
            raise HTTPException(401, "User inactive")
        t.last_used_at = _now()
        org, uid, role, scopes = t.organisation_id, u.id, u.role, list(t.scopes or [])
    ctx = Ctx(uid, org, role, False, None, False, request.client.host if request.client else None)
    with tenant_session(org, uid, role) as db:
        yield ExtAuth(db, ctx, scopes)


def _need(a: ExtAuth, scope: str, perm: str = "create"):
    if scope not in a.scopes:
        raise HTTPException(403, f"This extension token lacks the '{scope}' scope")
    if not can(a.ctx.role, perm):
        raise HTTPException(403, f"Your role ({a.ctx.role}) lacks the '{perm}' permission")


@router.get("/social/ext/me")
def ext_me(a: ExtAuth = Depends(ext_auth)):
    _need(a, "read", "view")
    u = a.db.get(m.User, a.ctx.user_id)
    o = a.db.get(m.Organisation, a.ctx.org_id)
    projects = a.db.scalars(select(m.Project).where(m.Project.id.in_(visible_project_ids(a.db, a.ctx)), m.Project.status == "active")
                            .order_by(m.Project.name)).all()
    return {"user": {"name": u.name, "email": u.email, "role": u.role}, "organisation": o.name, "scopes": a.scopes,
            "projects": [{"id": str(p.id), "name": p.name} for p in projects]}


class ExtAuditIn(BaseModel):
    project_id: uuid.UUID
    url: str = Field(..., max_length=2000)
    max_pages: int = Field(100, ge=1, le=2000)


def _origin(url: str) -> tuple[str, str]:
    u = url.strip()
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname or "@" in p.netloc:
        raise HTTPException(400, "Enter a website URL like https://example.com")
    host = p.hostname.lower()
    return f"{p.scheme}://{host}" + (f":{p.port}" if p.port else ""), host.removeprefix("www.")


@router.post("/social/ext/audit")
def ext_audit(body: ExtAuditIn, a: ExtAuth = Depends(ext_auth)):
    from . import seo
    _need(a, "audit:create")
    db, ctx = a.db, a.ctx
    project_guard(db, ctx, body.project_id)
    origin, host = _origin(body.url)
    seo._clean_domain(origin)
    site = None
    for s in db.scalars(select(m.Site).where(m.Site.project_id == body.project_id)):
        if _origin(s.domain)[1] == host:
            site = s
            break
    created = False
    if not site:
        site = m.Site(organisation_id=ctx.org_id, project_id=body.project_id, domain=origin)
        db.add(site)
        db.flush()
        created = True
        log(db, ctx, "site.created", f"site:{site.id}", {"domain": origin, "via": "extension"})
    res = seo.start_audit(site.id, seo.AuditIn(max_pages=body.max_pages), db, ctx)
    log(db, ctx, "extension.audit_queued", f"audit_run:{res['id']}", {"url": body.url})
    return {"audit_run_id": str(res["id"]), "job_id": res["job_id"], "site_id": str(site.id), "site_created": created,
            "domain": site.domain, "pages_granted": res["pages_granted"], "link": f"/audits/{res['id']}"}


class ExtKeywordIn(BaseModel):
    project_id: uuid.UUID
    phrase: str = Field(..., min_length=1, max_length=200)
    country: str = Field("us", pattern=r"^[a-z]{2}$")
    lang: str = Field("en", pattern=r"^[a-z]{2}$")


@router.post("/social/ext/keyword")
def ext_keyword(body: ExtKeywordIn, a: ExtAuth = Depends(ext_auth)):
    from . import seo
    _need(a, "keyword:create")
    res = seo.add_keywords(seo.KeywordsIn(project_id=body.project_id, phrases=[body.phrase], country=body.country, lang=body.lang,
                                          tags=["extension"]), a.db, a.ctx)
    return {**res, "phrase": body.phrase.strip().lower(), "link": f"/rank-tracker?project={body.project_id}"}


class ExtCompetitorIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=300)
    website: str | None = Field(None, max_length=2000)
    address: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=60)
    category: str | None = Field(None, max_length=200)
    rating: float | None = Field(None, ge=0, le=5)
    reviews: int | None = Field(None, ge=0)
    maps_url: str | None = Field(None, max_length=2000)


@router.post("/social/ext/competitor")
def ext_competitor(body: ExtCompetitorIn, a: ExtAuth = Depends(ext_auth)):
    from . import seo
    _need(a, "competitor:create")
    db, ctx = a.db, a.ctx
    project_guard(db, ctx, body.project_id)
    maps = body.maps_url
    if maps and not re.match(r"^https://(www\.)?google\.[a-z.]+/maps|^https://maps\.app\.goo\.gl/|^https://(www\.)?google\.[a-z.]+/search", maps):
        maps = None
    comp_id = None
    host = None
    if body.website:
        origin, host = _origin(body.website)
        seo._clean_domain(origin)
        existing = next((c for c in db.scalars(select(m.Competitor).where(m.Competitor.project_id == body.project_id))
                         if _origin(c.domain)[1] == host), None)
        if existing:
            comp_id = existing.id
        else:
            c = m.Competitor(organisation_id=ctx.org_id, project_id=body.project_id, domain=host)
            db.add(c)
            db.flush()
            comp_id = c.id
    dup = db.scalar(select(sm.SavedBusiness).where(sm.SavedBusiness.project_id == body.project_id, sm.SavedBusiness.name == body.name.strip(),
                                                   or_(sm.SavedBusiness.address == body.address, sm.SavedBusiness.address.is_(None))).limit(1))
    if dup:
        dup.competitor_id = dup.competitor_id or comp_id
        b = dup
    else:
        b = sm.SavedBusiness(organisation_id=ctx.org_id, project_id=body.project_id, name=body.name.strip(), website=body.website,
                             address=body.address, phone=body.phone, category=body.category, rating=body.rating, reviews=body.reviews,
                             maps_url=maps, competitor_id=comp_id, created_by=ctx.user_id)
        db.add(b)
        db.flush()
    log(db, ctx, "extension.competitor_saved", f"saved_business:{b.id}", {"name": b.name, "domain": host})
    return {"saved_business_id": str(b.id), "competitor_id": str(comp_id) if comp_id else None, "domain": host, "duplicate": bool(dup),
            "link": "/competitors"}


@router.get("/social/saved-businesses")
def saved_businesses(project_id: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    project_guard(db, ctx, project_id)
    return rows(db.scalars(select(sm.SavedBusiness).where(sm.SavedBusiness.project_id == project_id).order_by(desc(sm.SavedBusiness.created_at))))
