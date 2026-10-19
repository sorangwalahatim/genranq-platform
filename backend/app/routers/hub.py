"""Integrations Hub (49), Credential Vault (48), Billing/entitlements, Jobs."""
from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from .. import models as m
from ..config import get_settings
from ..db import tenant_session
from ..deps import Ctx, deny_client, get_ctx, get_db, log, require, require_step_up
from ..security import now
from ..services import entitlements as ent
from ..services import integrations as ih
from ..services import jobs as jq
from ..services import vault
from ..util import rows, to_dict

router = APIRouter(tags=["hub"])
S = get_settings()


# ------------------------------------------------------------------ integrations
class ConnectIn(BaseModel):
    secret: str | None = None
    config: dict = {}


@router.get("/integrations")
def integrations(db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    conns = {c.provider_key: c for c in db.scalars(select(m.IntegrationConnection))}
    creds = {c.id: c for c in db.scalars(select(m.Credential).where(m.Credential.integration.isnot(None)))}
    allowed = ent.feature(db, ctx.org_id, "integrations").get("enabled")
    out = []
    for p in ih.catalog():
        c = conns.get(p["key"]) if p["key"] else None
        cred = creds.get(c.credential_id) if c and c.credential_id else None
        gate = p.get("entitlement")
        out.append({**p, "status": c.status if c else "NOT_CONNECTED", "last_health_at": c.last_health_at if c else None,
                    "last_sync_at": c.last_sync_at if c else None, "last_error": c.last_error if c else None,
                    "error_count": c.error_count if c else 0, "connected_at": c.connected_at if c else None,
                    "credential_hint": cred.masked_hint if cred else None,
                    "config": {k: v for k, v in (c.config or {}).items()} if c else {},
                    "plan_allows": bool(allowed) and (not gate or ent.feature(db, ctx.org_id, gate).get("enabled"))})
    return out


def _provider(key: str) -> ih.ProviderDef:
    if key not in ih.BY_KEY:
        raise HTTPException(404, "Unknown provider")
    return ih.BY_KEY[key]


def _gate(db, ctx, p: ih.ProviderDef):
    ent.require_feature(db, ctx.org_id, "integrations")
    if p.entitlement:
        ent.require_feature(db, ctx.org_id, p.entitlement)


@router.post("/integrations/{key}/connect")
def connect(key: str, body: ConnectIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    p = _provider(key)
    _gate(db, ctx, p)
    if p.auth == "oauth2" and body.secret:
        raise HTTPException(400, "This provider connects via OAuth — use the Connect with OAuth button")
    if p.auth in ("api_key", "oauth2_or_api_key") and not body.secret and not ih.get_conn(db, ctx.org_id, key).credential_id:
        raise HTTPException(400, "API key required")
    allowed_cfg = {f["name"] for f in p.fields} | {"model"}
    cfg = {k: v for k, v in body.config.items() if k in allowed_cfg}
    for f in p.fields:
        if f.get("secret") and cfg.get(f["name"]):
            raise HTTPException(400, f"{f['label']} must be sent as the secret")
    ih.connect_api_key(db, ctx.org_id, ctx.user_id, key, body.secret, cfg)
    c = ih.run_health_check(db, ctx.org_id, key)
    log(db, ctx, "integration.connected", f"integration:{key}", {"status": c.status})
    return {"status": c.status, "error": c.last_error}


@router.post("/integrations/{key}/health")
def health(key: str, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    _provider(key)
    c = ih.run_health_check(db, ctx.org_id, key)
    log(db, ctx, "integration.health_check", f"integration:{key}", {"status": c.status})
    return {"status": c.status, "error": c.last_error, "checked_at": c.last_health_at}


@router.post("/integrations/{key}/disconnect")
def disconnect(key: str, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    _provider(key)
    ih.disconnect(db, ctx.org_id, key)
    log(db, ctx, "integration.disconnected", f"integration:{key}")
    return {"status": "NOT_CONNECTED"}


@router.post("/integrations/{key}/sync")
def sync(key: str, body: dict | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    _provider(key)
    if not ih.is_connected(db, key):
        raise HTTPException(409, detail={"code": "not_connected", "message": f"{ih.BY_KEY[key].spec_name} is NOT CONNECTED"})
    body = body or {}
    if key in ("gsc", "semrush") and body.get("project_id"):
        j = jq.enqueue(db, ctx.org_id, "rank_check", {"project_id": body["project_id"]}, created_by=ctx.user_id)
    elif key == "pagespeed" and body.get("site_id"):
        j = jq.enqueue(db, ctx.org_id, "pagespeed", {"site_id": body["site_id"]}, created_by=ctx.user_id)
    else:
        j = jq.enqueue(db, ctx.org_id, "integration_health", {"provider": key}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


@router.get("/integrations/{key}/oauth/start")
def oauth_start(key: str, request: Request, response: Response, db=Depends(get_db), ctx: Ctx = Depends(require("manage_integrations"))):
    """Creates a single-use state bound to this browser (HttpOnly cookie) and, where required, a PKCE verifier."""
    p = _provider(key)
    _gate(db, ctx, p)
    nonce, browser = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48) if p.oauth and p.oauth.get("pkce") else None
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() if verifier else None
    redirect_uri = str(request.url_for("oauth_callback", key=key))
    try:
        url = ih.oauth_authorize_url(key, nonce, redirect_uri, challenge)
    except ih.NotConnected as e:
        raise HTTPException(409, detail={"code": "oauth_app_not_configured", "message": str(e)})
    db.add(m.OAuthState(nonce=nonce, organisation_id=ctx.org_id, user_id=ctx.user_id, provider_key=key,
                        browser_hash=hashlib.sha256(browser.encode()).hexdigest(), code_verifier=verifier,
                        expires_at=now() + timedelta(minutes=10)))
    response.set_cookie("gq_oauth", browser, max_age=600, httponly=True, secure=request.url.scheme == "https",
                        samesite="lax", path="/api/integrations")
    return {"authorize_url": url}


@router.get("/integrations/{key}/oauth/callback", name="oauth_callback")
def oauth_callback(key: str, request: Request, code: str, state: str):
    from ..db import system_session
    from ..rbac import can
    browser = request.cookies.get("gq_oauth") or ""
    with system_session() as sdb:
        st = sdb.get(m.OAuthState, state, with_for_update=True)
        if (not st or st.used_at or st.expires_at < now() or st.provider_key != key
                or not secrets.compare_digest(st.browser_hash, hashlib.sha256(browser.encode()).hexdigest())):
            raise HTTPException(400, "Invalid or expired OAuth state — start the connection again from Integrations")
        st.used_at = now()
        user = sdb.get(m.User, st.user_id)
        if not user or not user.is_active or not can(user.role, "manage_integrations"):
            raise HTTPException(403, "You no longer have permission to manage integrations")
        org, uid, verifier = st.organisation_id, st.user_id, st.code_verifier
    with tenant_session(org, uid) as db:
        c = ih.oauth_exchange(db, org, uid, key, code, str(request.url_for("oauth_callback", key=key)), verifier)
        db.add(m.ActivityLog(organisation_id=org, actor_id=uid, action="integration.connected", target=f"integration:{key}",
                             meta={"status": c.status, "via": "oauth"}))
    resp = RedirectResponse(f"/integrations?connected={key}")
    resp.delete_cookie("gq_oauth", path="/api/integrations")
    return resp


# ------------------------------------------------------------------ vault (F-018)
class CredIn(BaseModel):
    label: str
    username: str | None = None
    secret: str
    supplier_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None


@router.get("/vault")
def list_creds(db=Depends(get_db), ctx: Ctx = Depends(require("access_credentials"))):
    sites = {s.id: s.domain for s in db.scalars(select(m.Site))}
    sup = {s.id: s.name for s in db.scalars(select(m.Supplier))}
    return [{**to_dict(c, exclude=("vault_ref",)), "site": sites.get(c.site_id), "supplier": sup.get(c.supplier_id)}
            for c in db.scalars(select(m.Credential).order_by(desc(m.Credential.created_at)))]


@router.post("/vault")
def add_cred(body: CredIn, db=Depends(get_db), ctx: Ctx = Depends(require("access_credentials"))):
    if body.site_id and not db.get(m.Site, body.site_id):
        raise HTTPException(400, "Unknown site")
    if body.supplier_id and not db.get(m.Supplier, body.supplier_id):
        raise HTTPException(400, "Unknown supplier")
    ref = vault.put(ctx.org_id, body.secret)
    c = m.Credential(organisation_id=ctx.org_id, label=body.label, username=body.username, vault_ref=ref, masked_hint=vault.mask(body.secret),
                     site_id=body.site_id, supplier_id=body.supplier_id, created_by=ctx.user_id)
    db.add(c)
    db.flush()
    log(db, ctx, "vault.credential_created", f"credential:{c.id}", {"label": body.label})
    return to_dict(c, exclude=("vault_ref",))


@router.post("/vault/{cid}/reveal")
def reveal(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("access_credentials")), _su: Ctx = Depends(require_step_up)):
    c = db.get(m.Credential, cid)
    if not c:
        raise HTTPException(404)
    if c.integration:
        raise HTTPException(403, "Integration API keys are write-only and never revealed")
    log(db, ctx, "vault.credential_revealed", f"credential:{c.id}", {"label": c.label})
    return {"secret": vault.get(ctx.org_id, c.vault_ref), "username": c.username}


@router.put("/vault/{cid}")
def rotate(cid: uuid.UUID, body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("access_credentials")), _su: Ctx = Depends(require_step_up)):
    c = db.get(m.Credential, cid)
    if not c or c.integration:
        raise HTTPException(404)
    old = c.vault_ref
    c.vault_ref = vault.put(ctx.org_id, body["secret"])
    c.masked_hint, c.rotated_at = vault.mask(body["secret"]), now()
    vault.remove(ctx.org_id, old)
    log(db, ctx, "vault.credential_rotated", f"credential:{c.id}")
    return to_dict(c, exclude=("vault_ref",))


@router.delete("/vault/{cid}")
def delete_cred(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("access_credentials"))):
    c = db.get(m.Credential, cid)
    if not c or c.integration:
        raise HTTPException(404)
    vault.remove(ctx.org_id, c.vault_ref)
    db.delete(c)
    log(db, ctx, "vault.credential_deleted", f"credential:{cid}")
    return {"ok": True}


# ------------------------------------------------------------------ billing (Pricing Matrix)
@router.get("/billing/plans")
def billing_plans(ctx: Ctx = Depends(get_ctx)):
    from ..spec.store import active_spec
    sp = active_spec()["pricing"]
    return {"tiers": sp["tiers"], "rows": sp["features"], "plans": ent.plans(),
            "prices": None, "note": "The Pricing Matrix defines features and limits but no prices; contact sales for quotes."}


@router.get("/billing/usage")
def billing_usage(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return ent.usage_summary(db, ctx.org_id)


@router.post("/billing/plan")
def change_plan(body: dict, db=Depends(get_db), ctx: Ctx = Depends(require("manage_billing"))):
    plans = ent.plans()
    if body.get("plan") not in plans:
        raise HTTPException(400, f"plan must be one of {list(plans)}")
    o = db.get(m.Organisation, ctx.org_id)
    old, o.plan = o.plan, body["plan"]
    log(db, ctx, "billing.plan_changed", f"organisation:{o.id}", {"from": old, "to": o.plan})
    # Payment collection is not part of the spec's Stripe scope (Stripe = wallet/marketplace, Phase 6).
    return {"plan": o.plan, "payment": "invoiced offline — Stripe subscription billing NOT CONNECTED"}


# ------------------------------------------------------------------ jobs
@router.get("/jobs")
def list_jobs(status: str | None = None, kind: str | None = None, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    q = select(m.Job).order_by(desc(m.Job.created_at))
    if status:
        q = q.where(m.Job.status == status)
    if kind:
        q = q.where(m.Job.kind == kind)
    counts = dict(db.execute(select(m.Job.status, func.count()).group_by(m.Job.status)).all())
    return {"counts": counts, "items": rows(db.scalars(q.limit(200)))}


@router.get("/jobs/{jid}")
def get_job(jid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(deny_client)):
    j = db.get(m.Job, jid)
    if not j:
        raise HTTPException(404)
    return to_dict(j)


@router.post("/jobs/{jid}/retry")
def retry_job(jid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    try:
        j = jq.retry(db, jid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if j.kind == "audit":
        ar = db.get(m.AuditRun, j.payload.get("audit_run_id"))
        if ar:
            granted = ent.consume(db, ctx.org_id, "audit_pages", ar.max_pages, allow_partial=True)
            ar.status, ar.max_pages = "QUEUED", granted
            ar.summary = {"reserved_pages": granted}
    log(db, ctx, "job.retried", f"job:{jid}")
    return to_dict(j)


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    j = db.get(m.Job, jid, with_for_update={"skip_locked": True})
    if not j or j.status != "QUEUED":
        raise HTTPException(400, "Only queued jobs can be cancelled")
    j.status, j.error, j.finished_at = "FAILED", "Cancelled by user", now()
    if j.kind == "audit":
        ar = db.get(m.AuditRun, j.payload.get("audit_run_id"))
        if ar:
            ar.status = "FAILED"
            ent.refund(db, ctx.org_id, "audit_pages", (ar.summary or {}).get("reserved_pages", 0))
            ar.summary = {**(ar.summary or {}), "reserved_pages": 0, "error": "Cancelled"}
    log(db, ctx, "job.cancelled", f"job:{jid}")
    return to_dict(j)
