from __future__ import annotations

import os
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update

from .. import models as m
from ..config import get_settings
from ..db import system_session, tenant_session
from ..deps import Ctx, get_ctx, log
from ..rbac import matrix
from ..security import (decode_token, hash_opaque, hash_password, make_access_token, make_mfa_challenge,
                        new_opaque_token, new_totp_secret, now, totp_uri, verify_password, verify_totp)
from ..services import vault

router = APIRouter(prefix="/auth", tags=["auth"])
S = get_settings()


class Signup(BaseModel):
    organisation: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    plan: str = "essentials"


class Login(BaseModel):
    email: EmailStr
    password: str
    organisation: str | None = None  # slug, when the email belongs to several orgs


class MfaVerify(BaseModel):
    challenge: str
    code: str


class Refresh(BaseModel):
    refresh_token: str


class StepUp(BaseModel):
    password: str
    code: str | None = None


class AcceptInvite(BaseModel):
    token: str
    name: str
    password: str = Field(min_length=10)


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "org"


def _is_pa(user: m.User) -> bool:
    # Granted only by the operator CLI (python -m app.admin grant-platform-admin), never by signup.
    return bool(user.is_platform_admin)


def _issue(db, user: m.User, *, mfa: bool, family=None) -> dict:
    raw, h = new_opaque_token()
    rt = m.RefreshToken(organisation_id=user.organisation_id, user_id=user.id, family_id=family or uuid.uuid4(),
                        token_hash=h, expires_at=now() + timedelta(days=S.refresh_ttl_days))
    db.add(rt)
    db.flush()
    return {"access_token": make_access_token(user.id, user.organisation_id, user.role, mfa=mfa, platform_admin=_is_pa(user)),
            "refresh_token": raw, "token_type": "bearer", "expires_in": S.access_ttl_min * 60}


@router.post("/signup")
def signup(body: Signup, request: Request):
    from ..services.entitlements import plans
    if body.plan not in plans():
        raise HTTPException(400, f"Unknown plan; choose one of {list(plans())}")
    with system_session() as db:
        slug, i = _slugify(body.organisation), 1
        base = slug
        while db.scalar(select(m.Organisation.id).where(m.Organisation.slug == slug)):
            i += 1
            slug = f"{base}-{i}"
        org = m.Organisation(name=body.organisation, slug=slug, plan=body.plan,
                             branding={"company_name": body.organisation, "primary_color": "#1F3A5F"})
        db.add(org)
        db.flush()
        user = m.User(organisation_id=org.id, email=body.email, role="owner", name=body.name, password_hash=hash_password(body.password))
        db.add(user)
        db.add(m.Wallet(organisation_id=org.id, balance=0))
        db.flush()
        db.add(m.ActivityLog(organisation_id=org.id, actor_id=user.id, action="org.created", target=f"organisation:{org.id}",
                             ip=request.client.host if request.client else None))
        tokens = _issue(db, user, mfa=False)
    return {**tokens, "organisation": {"id": str(org.id), "slug": slug}}


MAX_FAILS, LOCK_MIN = 5, 15


def _locked(user: m.User):
    if user.locked_until and user.locked_until > now():
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")


def _record_failure(user_id, reason: str):
    """Persist a failed attempt in its own transaction (the request transaction rolls back on error)."""
    with system_session() as db:
        u = db.get(m.User, user_id)
        u.failed_auth = (u.failed_auth or 0) + 1
        if u.failed_auth >= MAX_FAILS:
            u.locked_until, u.failed_auth = now() + timedelta(minutes=LOCK_MIN), 0
        db.add(m.ActivityLog(organisation_id=u.organisation_id, actor_id=u.id, action="auth.failed", meta={"reason": reason}))


def _check_totp(user: m.User, code: str | None) -> bool:
    step = verify_totp(vault.get(user.organisation_id, user.mfa_secret_ref), code or "", last_step=user.mfa_last_step) \
        if user.mfa_secret_ref else None
    if step is None:
        return False
    user.mfa_last_step = step
    return True


@router.post("/login")
def login(body: Login, request: Request):
    with system_session() as db:
        q = select(m.User, m.Organisation).join(m.Organisation).where(m.User.email == body.email, m.User.is_active)
        rows = db.execute(q).all()
        if body.organisation:
            rows = [r for r in rows if r[1].slug == body.organisation]
        unlocked = [(u, o) for u, o in rows if not (u.locked_until and u.locked_until > now())]
        if rows and not unlocked:
            raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
        rows = unlocked
        ok = [(u, o) for u, o in rows if verify_password(body.password, u.password_hash)]
        if not ok:
            failed = [u.id for u, _ in rows]
    if not ok:
        for uid in failed:
            _record_failure(uid, "password")
        raise HTTPException(401, "Invalid email or password")
    with system_session() as db:
        ok = [(db.get(m.User, u.id), db.get(m.Organisation, o.id)) for u, o in ok]
        if len(ok) > 1:
            return {"choose_organisation": [{"slug": o.slug, "name": o.name} for _, o in ok]}
        user, org = ok[0]
        user.failed_auth = 0
        if user.mfa_enabled:
            return {"mfa_required": True, "challenge": make_mfa_challenge(user.id, org.id)}
        user.last_login_at = now()
        db.add(m.ActivityLog(organisation_id=org.id, actor_id=user.id, action="auth.login", ip=request.client.host if request.client else None))
        return _issue(db, user, mfa=False)


@router.post("/mfa/verify")
def mfa_verify(body: MfaVerify, request: Request):
    try:
        c = decode_token(body.challenge, "mfa_challenge")
    except Exception:
        raise HTTPException(401, "Challenge expired")
    with system_session() as db:
        user = db.get(m.User, uuid.UUID(c["sub"]))
        if not user or not user.is_active:
            raise HTTPException(401, "Invalid code")
        _locked(user)
        if not _check_totp(user, body.code):
            bad = user.id
        else:
            bad = None
            user.failed_auth = 0
            user.last_login_at = now()
            db.add(m.ActivityLog(organisation_id=user.organisation_id, actor_id=user.id, action="auth.login", meta={"mfa": True},
                                 ip=request.client.host if request.client else None))
            return _issue(db, user, mfa=True)
    _record_failure(bad, "mfa")
    raise HTTPException(401, "Invalid code")


@router.post("/refresh")
def refresh(body: Refresh):
    reused = False
    with system_session() as db:
        rt = db.scalar(select(m.RefreshToken).where(m.RefreshToken.token_hash == hash_opaque(body.refresh_token)))
        if not rt or rt.expires_at < now():
            raise HTTPException(401, "Invalid refresh token")
        if rt.revoked_at:
            # Reuse of a rotated token ⇒ likely theft: revoke the whole family.
            db.execute(update(m.RefreshToken).where(m.RefreshToken.family_id == rt.family_id, m.RefreshToken.revoked_at.is_(None))
                       .values(revoked_at=now()))
            db.add(m.ActivityLog(organisation_id=rt.organisation_id, actor_id=rt.user_id, action="auth.refresh_reuse_detected"))
            reused = True
    if reused:
        raise HTTPException(401, "Refresh token reuse detected; please sign in again")
    with system_session() as db:
        rt = db.scalar(select(m.RefreshToken).where(m.RefreshToken.token_hash == hash_opaque(body.refresh_token)).with_for_update())
        if rt.revoked_at:
            raise HTTPException(401, "Refresh token already used")
        user = db.get(m.User, rt.user_id)
        if not user.is_active:
            raise HTTPException(401, "User inactive")
        out = _issue(db, user, mfa=user.mfa_enabled, family=rt.family_id)
        rt.revoked_at = now()
        rt.replaced_by = db.scalar(select(m.RefreshToken.id).where(m.RefreshToken.token_hash == hash_opaque(out["refresh_token"])))
        return out


@router.post("/logout")
def logout(body: Refresh):
    with system_session() as db:
        rt = db.scalar(select(m.RefreshToken).where(m.RefreshToken.token_hash == hash_opaque(body.refresh_token)))
        if rt:
            db.execute(update(m.RefreshToken).where(m.RefreshToken.family_id == rt.family_id).values(revoked_at=now()))
    return {"ok": True}


@router.post("/step-up")
def step_up(body: StepUp, ctx: Ctx = Depends(get_ctx)):
    """Re-authenticate to obtain a short-lived elevated token (required by the credential vault)."""
    with system_session() as db:
        user = db.get(m.User, ctx.user_id)
        _locked(user)
        err = None
        if not verify_password(body.password, user.password_hash):
            err = "Wrong password"
        elif user.mfa_enabled and not _check_totp(user, body.code):
            err = "Invalid MFA code"
        if not err:
            user.failed_auth = 0
    if err:
        _record_failure(ctx.user_id, "step_up")
        raise HTTPException(401, err)
    with system_session() as db:
        user = db.get(m.User, ctx.user_id)
        db.add(m.ActivityLog(organisation_id=user.organisation_id, actor_id=user.id, action="auth.step_up", ip=ctx.ip))
        return {"access_token": make_access_token(user.id, user.organisation_id, user.role, mfa=user.mfa_enabled, step_up=True,
                                                  platform_admin=_is_pa(user)),
                "step_up_minutes": S.step_up_ttl_min, "mfa_enabled": user.mfa_enabled}


@router.get("/me")
def me(ctx: Ctx = Depends(get_ctx)):
    with tenant_session(ctx.org_id, ctx.user_id) as db:
        u = db.get(m.User, ctx.user_id)
        o = db.get(m.Organisation, ctx.org_id)
        return {"user": {"id": str(u.id), "email": u.email, "name": u.name, "role": u.role, "mfa_enabled": u.mfa_enabled},
                "organisation": {"id": str(o.id), "name": o.name, "slug": o.slug, "plan": o.plan, "branding": {k: v for k, v in (o.branding or {}).items() if not k.startswith('_')}},
                "permissions": matrix()[u.role], "platform_admin": ctx.platform_admin, "stepped_up": ctx.stepped_up}


@router.post("/mfa/setup")
def mfa_setup(body: dict, ctx: Ctx = Depends(get_ctx)):
    """Enrolment requires the account password so a stolen access token can't bind an attacker's authenticator."""
    with system_session() as db:
        u = db.get(m.User, ctx.user_id)
        _locked(u)
        ok = verify_password(str(body.get("password", "")), u.password_hash)
    if not ok:
        _record_failure(ctx.user_id, "mfa_setup")
        raise HTTPException(401, "Wrong password")
    secret = new_totp_secret()
    with tenant_session(ctx.org_id, ctx.user_id) as db:
        u = db.get(m.User, ctx.user_id)
        if u.mfa_enabled:
            raise HTTPException(400, "MFA already enabled")
        if u.mfa_secret_ref:
            vault.remove(ctx.org_id, u.mfa_secret_ref)
        u.mfa_secret_ref = vault.put(ctx.org_id, secret)
        return {"secret": secret, "otpauth_uri": totp_uri(secret, u.email)}


@router.post("/mfa/enable")
def mfa_enable(body: dict, ctx: Ctx = Depends(get_ctx)):
    with tenant_session(ctx.org_id, ctx.user_id) as db:
        u = db.get(m.User, ctx.user_id)
        if not u.mfa_secret_ref or not _check_totp(u, str(body.get("code", ""))):
            raise HTTPException(400, "Invalid code")
        u.mfa_enabled = True
        log(db, ctx, "auth.mfa_enabled", f"user:{u.id}")
    return {"ok": True}


@router.post("/invites/accept")
def accept_invite(body: AcceptInvite):
    with system_session() as db:
        inv = db.scalar(select(m.Invite).where(m.Invite.token_hash == hash_opaque(body.token)))
        if not inv or inv.accepted_at or inv.expires_at < now():
            raise HTTPException(400, "Invite invalid or expired")
        if db.scalar(select(m.User.id).where(m.User.organisation_id == inv.organisation_id, m.User.email == inv.email)):
            raise HTTPException(409, "User already exists in this organisation")
        u = m.User(organisation_id=inv.organisation_id, email=inv.email, role=inv.role, name=body.name, password_hash=hash_password(body.password))
        db.add(u)
        inv.accepted_at = now()
        db.flush()
        db.add(m.ActivityLog(organisation_id=inv.organisation_id, actor_id=u.id, action="team.invite_accepted", target=f"user:{u.id}"))
        return _issue(db, u, mfa=False)


@router.get("/sso")
def sso_status():
    issuer = os.environ.get("OIDC_ISSUER")
    return {"provider": "OIDC", "status": "CONNECTED" if issuer and os.environ.get("OIDC_CLIENT_ID") else "NOT_CONNECTED",
            "issuer": issuer, "note": None if issuer else "Set OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET to enable SSO"}
