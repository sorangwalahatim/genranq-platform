"""Passwords (scrypt), JWT access tokens, rotating refresh tokens, TOTP MFA."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from .config import get_settings

S = get_settings()


# ------------------------------------------------------------------ passwords
def hash_password(pw: str) -> str:
    if len(pw) < 10:
        raise ValueError("Password must be at least 10 characters")
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, s, h = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(s), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(h))
    except Exception:
        return False


# ------------------------------------------------------------------ JWT
def now() -> datetime:
    return datetime.now(timezone.utc)


def make_access_token(user_id, org_id, role: str, *, mfa: bool = False, step_up: bool = False,
                      platform_admin: bool = False) -> str:
    t = now()
    claims = {
        "sub": str(user_id), "org": str(org_id), "role": role, "mfa": mfa, "pa": platform_admin,
        "iat": int(t.timestamp()), "exp": int((t + timedelta(minutes=S.access_ttl_min)).timestamp()),
        "typ": "access", "jti": uuid.uuid4().hex,
    }
    if step_up:
        claims["su"] = int((t + timedelta(minutes=S.step_up_ttl_min)).timestamp())
    return jwt.encode(claims, S.jwt_secret, algorithm="HS256")


def make_mfa_challenge(user_id, org_id) -> str:
    t = now()
    return jwt.encode({"sub": str(user_id), "org": str(org_id), "typ": "mfa_challenge",
                       "exp": int((t + timedelta(minutes=5)).timestamp())}, S.jwt_secret, algorithm="HS256")


def decode_token(token: str, typ: str = "access") -> dict:
    claims = jwt.decode(token, S.jwt_secret, algorithms=["HS256"])
    if claims.get("typ") != typ:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


def new_opaque_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_opaque(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ------------------------------------------------------------------ TOTP (RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_now(secret_b32: str, at: float | None = None) -> str:
    return _hotp(secret_b32, int((at or time.time()) // 30))


def verify_totp(secret_b32: str, code: str, window: int = 1, last_step: int | None = None) -> int | None:
    """Returns the matched time-step (truthy) or None. Steps <= last_step are rejected (no replay)."""
    code = (code or "").strip().replace(" ", "")
    c = int(time.time() // 30)
    for w in range(-window, window + 1):
        step = c + w
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_hotp(secret_b32, step), code):
            return step
    return None


def totp_uri(secret_b32: str, email: str) -> str:
    return f"otpauth://totp/Genranq:{email}?secret={secret_b32}&issuer=Genranq"
