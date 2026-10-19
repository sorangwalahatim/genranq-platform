"""Shared helpers for the growth modules: cross-tenant scoped writes, public-endpoint rate limits,
signed render tokens, URL validation, optional SMTP delivery and money rounding."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import smtplib
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_growth as g
from ..config import get_settings
from ..db import set_tenant, system_session

S = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def new_token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)


# ------------------------------------------------------------------ cross-tenant scoped section
@contextmanager
def as_org(db: Session, org_id):
    """Temporarily switch the RLS tenant of this transaction to ``org_id``.

    Used only for the two-sided marketplace (advertiser ↔ publisher) and escrow settlement, after the
    caller has authorised the action. Row-level security still applies to every statement inside the
    block — only rows of ``org_id`` are visible/writable — and the whole operation stays in one
    transaction, so both sides commit or roll back together. ORM objects of the other organisation
    are expunged on exit so nothing is lazily refreshed under the wrong tenant.
    """
    db.flush()
    cur = db.execute(text("select current_setting('app.org_id', true), current_setting('app.user_id', true), "
                          "current_setting('app.role', true)")).one()
    set_tenant(db, org_id, None, "system")
    try:
        yield
        db.flush()
    finally:
        for obj in list(db.identity_map.values()):
            if str(getattr(obj, "organisation_id", "")) == str(org_id) and str(org_id) != (cur[0] or ""):
                db.expunge(obj)
        set_tenant(db, cur[0] or None, cur[1] or None, cur[2] or None)


# ------------------------------------------------------------------ public endpoint plumbing
def client_ip(request: Request) -> str:
    return (request.client.host if request.client else None) or "unknown"


def ip_hash(ip: str) -> str:
    return hmac.new(S.jwt_secret.encode(), f"ip:{ip}".encode(), hashlib.sha256).hexdigest()[:32]


def rate_limit(db: Session, org_id, bucket: str, key: str, limit: int, window_sec: int) -> None:
    """Sliding-window limit stored per org (RLS-scoped). Raises 429 when exceeded."""
    kh = ip_hash(key)
    since = utcnow() - timedelta(seconds=window_sec)
    # serialise concurrent checks for the same key within the org
    db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{org_id}:{bucket}:{kh}"})
    n = db.scalar(select(func.count()).select_from(g.GrowthRateEvent).where(
        g.GrowthRateEvent.bucket == bucket, g.GrowthRateEvent.key_hash == kh, g.GrowthRateEvent.created_at >= since))
    if n >= limit:
        raise HTTPException(429, detail={"code": "rate_limited", "message": f"Too many requests — limit {limit} per {window_sec // 60 or 1} min. Try again later.",
                                         "retry_after": window_sec})
    db.add(g.GrowthRateEvent(organisation_id=org_id, bucket=bucket, key_hash=kh))
    if secrets.randbelow(50) == 0:  # opportunistic cleanup
        db.execute(delete(g.GrowthRateEvent).where(g.GrowthRateEvent.created_at < utcnow() - timedelta(days=2)))


def sign(value: str) -> str:
    return hmac.new(S.jwt_secret.encode(), f"growth:{value}".encode(), hashlib.sha256).hexdigest()[:40]


def render_token(form_token: str) -> str:
    ts = str(int(time.time()))
    return f"{ts}.{sign(form_token + ':' + ts)}"


def check_render_token(form_token: str, tok: str | None, min_seconds: int) -> str | None:
    """Returns a spam reason or None. Enforces a minimum time-to-submit and a 24 h expiry."""
    if not tok or "." not in tok:
        return "bad_token"
    ts, sig = tok.split(".", 1)
    if not ts.isdigit() or not hmac.compare_digest(sig, sign(form_token + ":" + ts)):
        return "bad_token"
    age = time.time() - int(ts)
    if age < min_seconds:
        return "too_fast"
    if age > 86400:
        return "bad_token"
    return None


def platform_org_id():
    """Organisation that owns the platform's own (un-keyed) public free tools: GROWTH_PLATFORM_ORG (slug)
    or the organisation of the first platform administrator. Read-only lookup of the org id only."""
    slug = os.environ.get("GROWTH_PLATFORM_ORG")
    with system_session() as sdb:
        if slug:
            oid = sdb.scalar(select(m.Organisation.id).where(m.Organisation.slug == slug))
            if oid:
                return oid
        return sdb.scalar(select(m.User.organisation_id).where(m.User.is_platform_admin.is_(True)).order_by(m.User.created_at).limit(1))


def resolve_public_key(key: str | None):
    """Map a public free-tools key to its organisation id (system lookup of the id only)."""
    if not key:
        oid = platform_org_id()
        if not oid:
            raise HTTPException(503, "Free tools are not configured on this server (no platform organisation).")
        return oid
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", key):
        raise HTTPException(404, "Unknown tools key")
    with system_session() as sdb:
        row = sdb.execute(select(g.GrowthOrgPublic.organisation_id, g.GrowthOrgPublic.free_tools_enabled)
                          .where(g.GrowthOrgPublic.public_key == key)).first()
    if not row or not row.free_tools_enabled:
        raise HTTPException(404, "Unknown tools key")
    return row.organisation_id


def ensure_public_key(db: Session, org_id) -> g.GrowthOrgPublic:
    row = db.scalar(select(g.GrowthOrgPublic))
    if not row:
        row = g.GrowthOrgPublic(organisation_id=org_id, public_key=new_token(12))
        db.add(row)
        db.flush()
    return row


# ------------------------------------------------------------------ validation
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


def valid_email(e: str | None) -> bool:
    return bool(e and len(e) <= 254 and EMAIL_RE.match(e.strip()))


def check_url(u: str | None, field: str = "url", required: bool = True) -> str | None:
    if not u:
        if required:
            raise HTTPException(400, f"{field} is required")
        return None
    u = u.strip()
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc or len(u) > 2000:
        raise HTTPException(400, f"{field} must be an http(s) URL")
    return u


def domain_of(u: str | None) -> str:
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    h = (urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


# ------------------------------------------------------------------ email (optional SMTP)
def send_email(to: str, subject: str, body: str) -> str:
    """Send through SMTP when configured; otherwise report NOT CONNECTED (nothing is pretended)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        return "not_connected"
    try:
        msg = EmailMessage()
        msg["From"] = os.environ.get("SMTP_FROM", "no-reply@genranq.com")
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(body)
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
            if os.environ.get("SMTP_STARTTLS", "1") == "1":
                s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return "sent"
    except Exception as e:  # noqa: BLE001
        return f"error: {str(e)[:120]}"


def uid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None
