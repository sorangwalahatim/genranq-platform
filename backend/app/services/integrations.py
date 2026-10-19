"""Integrations Hub (module 49): one uniform connector per provider in the
*API Integrations* sheet — auth, credential storage (vault), health check, sync,
error handling, retry with backoff, rate limiting, last sync and disconnect.

Providers without credentials report NOT_CONNECTED; nothing pretends to work.
"""
from __future__ import annotations

import os
import shutil
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import models as m
from ..spec.store import active_spec
from . import vault


class IntegrationError(Exception):
    pass


class NotConnected(IntegrationError):
    pass


class RateLimited(IntegrationError):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry after {retry_after:.0f}s")
        self.retry_after = retry_after


@dataclass
class ProviderDef:
    key: str
    spec_name: str
    auth: str  # api_key / oauth2 / oauth2_or_webhook / license_cli / oauth2_or_api_key
    health: Callable | None = None
    oauth: dict | None = None  # authorize_url, token_url, scopes, client env prefix
    rate: tuple[float, float] = (60, 1.0)  # bucket capacity, refill/sec
    fields: list[dict] = field(default_factory=list)  # config fields the user supplies
    entitlement: str | None = None  # plan feature gate (e.g. accounting add-on)


# ---------------------------------------------------------------- http with retry
def http_call(method: str, url: str, *, retries: int = 3, timeout: float = 30, **kw) -> httpx.Response:
    last = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as c:
                r = c.request(method, url, **kw)
            if r.status_code == 429:
                ra = float(r.headers.get("retry-after", "0") or 0) or 2 ** attempt
                if attempt == retries:
                    raise RateLimited(ra)
                time.sleep(min(ra, 10))
                continue
            if r.status_code >= 500 and attempt < retries:
                time.sleep(min(2 ** attempt, 10))
                continue
            return r
        except httpx.TransportError as e:
            last = e
            if attempt < retries:
                time.sleep(min(2 ** attempt, 10))
                continue
            raise IntegrationError(f"network error: {e}") from e
    raise IntegrationError(str(last))


def _ok(r: httpx.Response, what: str):
    if r.status_code in (401, 403):
        raise IntegrationError(f"{what}: credentials rejected ({r.status_code})")
    if r.status_code >= 400:
        raise IntegrationError(f"{what}: HTTP {r.status_code} {r.text[:200]}")
    return r


# ---------------------------------------------------------------- health checks
def hc_claude(c):
    _ok(http_call("GET", "https://api.anthropic.com/v1/models", headers={"x-api-key": c["secret"], "anthropic-version": "2023-06-01"}), "Claude")


def hc_openai(c):
    _ok(http_call("GET", "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {c['secret']}"}), "OpenAI")


def hc_gemini(c):
    _ok(http_call("GET", "https://generativelanguage.googleapis.com/v1beta/models", params={"key": c["secret"]}), "Gemini")


def hc_perplexity(c):
    _ok(http_call("POST", "https://api.perplexity.ai/chat/completions", headers={"Authorization": f"Bearer {c['secret']}"},
                  json={"model": "sonar", "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}), "Perplexity")


def hc_ahrefs(c):
    _ok(http_call("GET", "https://api.ahrefs.com/v3/subscription-info/limits-and-usage", headers={"Authorization": f"Bearer {c['secret']}"}), "Ahrefs")


def hc_semrush(c):
    r = _ok(http_call("GET", "https://www.semrush.com/users/countapiunits.html", params={"key": c["secret"]}), "Semrush")
    if not r.text.strip().isdigit():
        raise IntegrationError(f"Semrush: {r.text[:120]}")


def hc_psi(c):
    _ok(http_call("GET", "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                  params={"url": "https://www.google.com", "key": c["secret"], "category": "performance", "strategy": "mobile"},
                  timeout=90, retries=1), "PageSpeed Insights")


def hc_maps(c):
    r = _ok(http_call("GET", "https://maps.googleapis.com/maps/api/geocode/json", params={"address": "London", "key": c["secret"]}), "Google Maps")
    if r.json().get("status") not in ("OK", "ZERO_RESULTS"):
        raise IntegrationError(f"Google Maps: {r.json().get('status')} {r.json().get('error_message', '')}")


def hc_stripe(c):
    _ok(http_call("GET", "https://api.stripe.com/v1/balance", auth=(c["secret"], "")), "Stripe")


def _bearer(url, name):
    def f(c):
        _ok(http_call("GET", url, headers={"Authorization": f"Bearer {c['secret']}"}), name)
    return f


def hc_teams(c):
    url = c.get("config", {}).get("webhook_url") or ""
    if url:
        if not url.startswith("https://"):
            raise IntegrationError("Teams webhook URL must be https")
        return
    _bearer("https://graph.microsoft.com/v1.0/me", "Microsoft Teams")(c)


def hc_screaming_frog(c):
    path = os.environ.get("SCREAMING_FROG_CLI", "screamingfrogseospidercli")
    if not shutil.which(path) and not os.path.exists(path):
        raise IntegrationError(f"Screaming Frog CLI not found at '{path}' on the worker host")


GOOGLE_OAUTH = {"authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_url": "https://oauth2.googleapis.com/token", "env": "GOOGLE_OAUTH",
                "extra": {"access_type": "offline", "prompt": "consent"}}


def _g(scopes):
    return {**GOOGLE_OAUTH, "scopes": scopes}


PROVIDERS: list[ProviderDef] = [
    ProviderDef("claude", "Claude (Anthropic)", "api_key", hc_claude, rate=(50, 0.8)),
    ProviderDef("ahrefs", "Ahrefs", "api_key", hc_ahrefs, rate=(30, 0.2)),
    ProviderDef("semrush", "Semrush", "api_key", hc_semrush, rate=(10, 0.15)),
    ProviderDef("gsc", "Google Search Console", "oauth2",
                _bearer("https://www.googleapis.com/webmasters/v3/sites", "Search Console"),
                oauth=_g(["https://www.googleapis.com/auth/webmasters.readonly"]), rate=(20, 0.3)),
    ProviderDef("gbp", "Google Business Profile", "oauth2",
                _bearer("https://mybusinessaccountmanagement.googleapis.com/v1/accounts", "Business Profile"),
                oauth=_g(["https://www.googleapis.com/auth/business.manage"]), rate=(10, 0.1)),
    ProviderDef("google_maps", "Google Maps / Places", "api_key", hc_maps, rate=(50, 1)),
    ProviderDef("ga4", "Google Analytics 4", "oauth2",
                _bearer("https://analyticsadmin.googleapis.com/v1beta/accountSummaries", "GA4"),
                oauth=_g(["https://www.googleapis.com/auth/analytics.readonly"]), rate=(10, 0.2)),
    ProviderDef("google_ads", "Google Ads", "oauth2", None,
                oauth=_g(["https://www.googleapis.com/auth/adwords"]),
                fields=[{"name": "developer_token", "label": "Developer token", "secret": True}]),
    ProviderDef("meta", "Meta (Facebook/Instagram)", "oauth2", _bearer("https://graph.facebook.com/v19.0/me", "Meta"),
                oauth={"authorize_url": "https://www.facebook.com/v19.0/dialog/oauth",
                       "token_url": "https://graph.facebook.com/v19.0/oauth/access_token", "env": "META_OAUTH",
                       "scopes": ["pages_manage_posts", "pages_read_engagement", "instagram_content_publish", "leads_retrieval"]}),
    ProviderDef("linkedin", "LinkedIn", "oauth2", _bearer("https://api.linkedin.com/v2/userinfo", "LinkedIn"),
                oauth={"authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
                       "token_url": "https://www.linkedin.com/oauth/v2/accessToken", "env": "LINKEDIN_OAUTH",
                       "scopes": ["openid", "profile", "w_member_social"]}),
    ProviderDef("x", "X (Twitter)", "oauth2", _bearer("https://api.twitter.com/2/users/me", "X"),
                oauth={"authorize_url": "https://twitter.com/i/oauth2/authorize",
                       "token_url": "https://api.twitter.com/2/oauth2/token", "env": "X_OAUTH", "pkce": True,
                       "scopes": ["tweet.read", "tweet.write", "users.read", "offline.access"]}),
    ProviderDef("microsoft_ads_bing", "Microsoft Ads / Bing WMT", "oauth2", None,
                oauth={"authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                       "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "env": "MICROSOFT_OAUTH",
                       "scopes": ["https://ads.microsoft.com/msads.manage", "offline_access"]}),
    ProviderDef("hubspot", "HubSpot", "oauth2", _bearer("https://api.hubapi.com/account-info/v3/details", "HubSpot"),
                oauth={"authorize_url": "https://app.hubspot.com/oauth/authorize",
                       "token_url": "https://api.hubapi.com/oauth/v1/token", "env": "HUBSPOT_OAUTH",
                       "scopes": ["crm.objects.contacts.read", "crm.objects.contacts.write", "crm.objects.deals.read", "forms"]}),
    ProviderDef("calendly", "Calendly", "oauth2_or_api_key", _bearer("https://api.calendly.com/users/me", "Calendly"),
                oauth={"authorize_url": "https://auth.calendly.com/oauth/authorize",
                       "token_url": "https://auth.calendly.com/oauth/token", "env": "CALENDLY_OAUTH", "scopes": []}),
    ProviderDef("ms_teams", "Microsoft Teams", "oauth2_or_webhook", hc_teams,
                oauth={"authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                       "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "env": "MICROSOFT_OAUTH",
                       "scopes": ["ChannelMessage.Send", "offline_access"]},
                fields=[{"name": "webhook_url", "label": "Incoming webhook URL (alternative to OAuth)", "secret": False}],
                rate=(4, 0.5)),
    ProviderDef("screaming_frog", "Screaming Frog", "license_cli", hc_screaming_frog,
                fields=[]),
    ProviderDef("accounting", "Xero / MYOB / QuickBooks", "oauth2", None,
                oauth={"authorize_url": "https://login.xero.com/identity/connect/authorize",
                       "token_url": "https://identity.xero.com/connect/token", "env": "XERO_OAUTH",
                       "scopes": ["accounting.transactions", "offline_access"]},
                fields=[{"name": "system", "label": "System (xero/myob/quickbooks)", "secret": False}],
                entitlement="accounting"),
    ProviderDef("looker_studio", "Looker Studio", "oauth2", None,
                oauth=_g(["https://www.googleapis.com/auth/datastudio"])),
    ProviderDef("openai", "OpenAI (ChatGPT)", "api_key", hc_openai, rate=(50, 0.8)),
    ProviderDef("perplexity", "Perplexity", "api_key", hc_perplexity, rate=(20, 0.3)),
    ProviderDef("gemini", "Google Gemini", "api_key", hc_gemini, rate=(50, 0.8)),
    ProviderDef("pagespeed", "Google PageSpeed Insights", "api_key", hc_psi, rate=(240, 4)),
    ProviderDef("stripe", "Payment gateway (Stripe)", "api_key", hc_stripe, rate=(25, 25)),
]
BY_KEY = {p.key: p for p in PROVIDERS}
BY_SPEC = {p.spec_name: p for p in PROVIDERS}


def catalog() -> list[dict]:
    """Merge connector definitions with the live spec rows (purpose/quota/phase come from the sheet)."""
    out, spec_rows = [], {r["provider"]: r for r in active_spec()["integrations"]}
    for name, row in spec_rows.items():
        p = BY_SPEC.get(name)
        oauth_configured = None
        if p and p.oauth:
            env = p.oauth["env"]
            oauth_configured = bool(os.environ.get(f"{env}_CLIENT_ID") and os.environ.get(f"{env}_CLIENT_SECRET"))
        out.append({
            "key": p.key if p else None, "provider": name, "category": row["category"], "purpose": row["purpose"],
            "auth_spec": row["auth"], "quota": row["quota"], "phase": row["phase"],
            "auth": p.auth if p else "unsupported", "has_connector": p is not None,
            "has_health_check": bool(p and p.health), "oauth_app_configured": oauth_configured,
            "fields": p.fields if p else [], "entitlement": p.entitlement if p else None,
        })
    return out


# ---------------------------------------------------------------- connection state
def get_conn(db: Session, org_id, key: str) -> m.IntegrationConnection:
    c = db.scalar(select(m.IntegrationConnection).where(m.IntegrationConnection.provider_key == key))
    if not c:
        c = m.IntegrationConnection(organisation_id=org_id, provider_key=key, status="NOT_CONNECTED", config={})
        db.add(c)
        db.flush()
    return c


def credentials(db: Session, org_id, key: str) -> dict:
    """Decrypted credential for server-side use only. Raises NotConnected."""
    c = db.scalar(select(m.IntegrationConnection).where(m.IntegrationConnection.provider_key == key))
    if not c or c.status not in ("CONNECTED", "RATE_LIMITED") or not c.credential_id:
        if c and key == "ms_teams" and c.config.get("webhook_url") and c.status == "CONNECTED":
            return {"secret": None, "config": c.config}
        raise NotConnected(f"{BY_KEY[key].spec_name} is NOT CONNECTED")
    cred = db.get(m.Credential, c.credential_id)
    secret = vault.get(org_id, cred.vault_ref)
    return {"secret": secret, "config": c.config, "connection": c}


def connect_api_key(db: Session, org_id, user_id, key: str, secret: str | None, config: dict) -> m.IntegrationConnection:
    p = BY_KEY[key]
    c = get_conn(db, org_id, key)
    if secret:
        ref = vault.put(org_id, secret)
        if c.credential_id:
            old = db.get(m.Credential, c.credential_id)
            vault.remove(org_id, old.vault_ref)
            old.vault_ref, old.masked_hint, old.rotated_at = ref, vault.mask(secret), datetime.now(timezone.utc)
        else:
            cred = m.Credential(organisation_id=org_id, integration=key, label=f"{p.spec_name} credential",
                                vault_ref=ref, masked_hint=vault.mask(secret), created_by=user_id)
            db.add(cred)
            db.flush()
            c.credential_id = cred.id
    c.config = {**(c.config or {}), **{k: v for k, v in (config or {}).items() if v not in (None, "")}}
    c.connected_by, c.connected_at = user_id, datetime.now(timezone.utc)
    c.status = "PENDING"
    db.flush()
    return c


def run_health_check(db: Session, org_id, key: str) -> m.IntegrationConnection:
    p = BY_KEY[key]
    c = get_conn(db, org_id, key)
    now = datetime.now(timezone.utc)
    if not c.credential_id and not (key in ("ms_teams", "screaming_frog") and c.config):
        c.status, c.last_error = "NOT_CONNECTED", None
        return c
    try:
        creds = {"secret": vault.get(org_id, db.get(m.Credential, c.credential_id).vault_ref) if c.credential_id else None,
                 "config": c.config or {}}
        if creds["secret"] and p.oauth and creds["secret"].startswith("{"):
            import json as _json
            creds["secret"] = _json.loads(creds["secret"]).get("access_token")
        if p.health is None:
            raise IntegrationError("No live health-check endpoint for this provider; credential stored, unverified")
        take_token(db, org_id, key)
        p.health(creds)
        c.status, c.last_error, c.last_health_ok, c.error_count = "CONNECTED", None, True, 0
    except RateLimited as e:
        c.status, c.last_error, c.last_health_ok = "RATE_LIMITED", str(e), False
    except IntegrationError as e:
        c.status, c.last_error, c.last_health_ok = "ERROR", str(e)[:500], False
        c.error_count = (c.error_count or 0) + 1
    c.last_health_at = now
    return c


def disconnect(db: Session, org_id, key: str):
    c = get_conn(db, org_id, key)
    if c.credential_id:
        cred = db.get(m.Credential, c.credential_id)
        c.credential_id = None
        db.flush()
        if cred:
            vault.remove(org_id, cred.vault_ref)
            db.delete(cred)
    c.status, c.config, c.last_error, c.last_health_ok = "NOT_CONNECTED", {}, None, None
    c.connected_at = c.connected_by = None


# ---------------------------------------------------------------- OAuth2 (authorization-code)
def oauth_authorize_url(key: str, state: str, redirect_uri: str, code_challenge: str | None = None) -> str:
    p = BY_KEY[key]
    if not p.oauth:
        raise IntegrationError("Provider does not use OAuth")
    env = p.oauth["env"]
    cid = os.environ.get(f"{env}_CLIENT_ID")
    if not cid or not os.environ.get(f"{env}_CLIENT_SECRET"):
        raise NotConnected(f"Platform OAuth app for {p.spec_name} is not configured "
                           f"(set {env}_CLIENT_ID / {env}_CLIENT_SECRET)")
    q = {"client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "state": state,
         "scope": " ".join(p.oauth["scopes"]), **p.oauth.get("extra", {})}
    if code_challenge:
        q.update(code_challenge=code_challenge, code_challenge_method="S256")
    return p.oauth["authorize_url"] + "?" + urllib.parse.urlencode(q)


def oauth_exchange(db: Session, org_id, user_id, key: str, code: str, redirect_uri: str, code_verifier: str | None = None):
    p = BY_KEY[key]
    env = p.oauth["env"]
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": os.environ[f"{env}_CLIENT_ID"], "client_secret": os.environ[f"{env}_CLIENT_SECRET"]}
    if code_verifier:
        data["code_verifier"] = code_verifier
    r = _ok(http_call("POST", p.oauth["token_url"], data=data), "token exchange")
    tok = r.json()
    import json as _json
    blob = _json.dumps({"access_token": tok.get("access_token"), "refresh_token": tok.get("refresh_token"),
                        "expires_at": time.time() + float(tok.get("expires_in", 3600))})
    c = connect_api_key(db, org_id, user_id, key, blob, {})
    return run_health_check(db, org_id, key) if p.health else c


# ---------------------------------------------------------------- rate limiting (token bucket in DB)
def take_token(db: Session, org_id, key: str, cost: float = 1.0):
    cap, refill = BY_KEY[key].rate
    db.execute(insert(m.RateLimitBucket).values(organisation_id=org_id, provider=key, tokens=cap, capacity=cap,
                                                refill_per_sec=refill).on_conflict_do_nothing())
    row = db.execute(text("""
        update rate_limit_bucket set
          tokens = least(capacity, tokens + extract(epoch from (now() - updated_at)) * refill_per_sec) - :cost,
          updated_at = now()
        where organisation_id = :o and provider = :p
          and least(capacity, tokens + extract(epoch from (now() - updated_at)) * refill_per_sec) >= :cost
        returning tokens"""), {"o": org_id, "p": key, "cost": cost}).first()
    if row is None:
        raise RateLimited(cost / float(refill))


def mark_synced(db: Session, org_id, key: str, error: str | None = None):
    c = get_conn(db, org_id, key)
    if error:
        c.last_error, c.error_count = error[:500], (c.error_count or 0) + 1
        if c.error_count >= 5:
            c.status = "ERROR"
    else:
        c.last_sync_at, c.last_error, c.error_count = datetime.now(timezone.utc), None, 0


def is_connected(db: Session, key: str) -> bool:
    c = db.scalar(select(m.IntegrationConnection).where(m.IntegrationConnection.provider_key == key))
    return bool(c and c.status in ("CONNECTED", "RATE_LIMITED"))


def connected_map(db: Session) -> dict[str, str]:
    rows = db.scalars(select(m.IntegrationConnection)).all()
    return {r.provider_key: r.status for r in rows}


def oauth_access_token(db: Session, org_id, key: str) -> str:
    """Return a valid OAuth access token, refreshing it if expired."""
    import json as _json
    cr = credentials(db, org_id, key)
    blob = _json.loads(cr["secret"])
    if blob.get("expires_at", 0) - 60 > time.time():
        return blob["access_token"]
    p = BY_KEY[key]
    env = p.oauth["env"]
    if not blob.get("refresh_token"):
        raise NotConnected(f"{p.spec_name} token expired; reconnect")
    r = _ok(http_call("POST", p.oauth["token_url"], data={
        "grant_type": "refresh_token", "refresh_token": blob["refresh_token"],
        "client_id": os.environ[f"{env}_CLIENT_ID"], "client_secret": os.environ[f"{env}_CLIENT_SECRET"]}), "token refresh")
    tok = r.json()
    blob.update(access_token=tok["access_token"], expires_at=time.time() + float(tok.get("expires_in", 3600)))
    if tok.get("refresh_token"):
        blob["refresh_token"] = tok["refresh_token"]
    connect_api_key(db, org_id, None, key, _json.dumps(blob), {})
    cr["connection"].status = "CONNECTED"
    return blob["access_token"]
