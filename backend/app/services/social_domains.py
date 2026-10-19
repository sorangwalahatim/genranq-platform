"""White-label custom domains (module 47): ownership verification by DNS.

Ownership: a TXT record ``_genranq-verify.<domain>`` = ``genranq-verify=<token>`` (needs dnspython).
Routing: ``<domain>`` must be a CNAME to CUSTOM_DOMAIN_TARGET. Without dnspython only the CNAME can be
checked (socket canonical-name lookup), and that is then the verification method. A domain is never
VERIFIED until the record really resolves. TLS certificates are provisioned by the operator."""
from __future__ import annotations

import os
import re
import secrets
import socket
from datetime import datetime, timezone

from sqlalchemy import select

from .. import models_social as sm
from ..db import system_session

DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
RESERVED = ("genranq.com", "genranq.app", "localhost")

try:  # optional dependency
    import dns.resolver  # type: ignore
    HAVE_DNSPYTHON = True
except ImportError:  # pragma: no cover
    HAVE_DNSPYTHON = False


def target() -> str:
    return os.environ.get("CUSTOM_DOMAIN_TARGET", "portal.genranq.com").rstrip(".").lower()


def clean(domain: str) -> str | None:
    d = (domain or "").strip().lower().rstrip(".")
    d = re.sub(r"^https?://", "", d).split("/")[0]
    if not DOMAIN_RE.match(d) or any(d == r or d.endswith("." + r) for r in RESERVED):
        return None
    return d


def new_token() -> str:
    return secrets.token_hex(16)


def txt_name(domain: str) -> str:
    return f"_genranq-verify.{domain}"


def txt_value(token: str) -> str:
    return f"genranq-verify={token}"


def lookup_txt(name: str) -> list[str]:
    if not HAVE_DNSPYTHON:
        raise RuntimeError("dnspython not installed")
    try:
        ans = dns.resolver.resolve(name, "TXT", lifetime=8)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    out = []
    for r in ans:
        out.append(b"".join(r.strings).decode("utf-8", "replace"))
    return out


def lookup_cname(name: str) -> str | None:
    if HAVE_DNSPYTHON:
        try:
            ans = dns.resolver.resolve(name, "CNAME", lifetime=8)
            return str(ans[0].target).rstrip(".").lower()
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            return None
    try:
        canonical, aliases, _ = socket.gethostbyname_ex(name)
    except OSError:
        return None
    return canonical.rstrip(".").lower() if canonical.lower() != name.lower() else None


def verify(d: sm.CustomDomain) -> sm.CustomDomain:
    now = datetime.now(timezone.utc)
    d.last_checked_at, d.last_error = now, None
    errors = []
    cname = None
    try:
        cname = lookup_cname(d.domain)
    except Exception as e:  # resolver failure is not verification
        errors.append(f"CNAME lookup failed: {e}")
    d.cname_ok = bool(cname) and (cname == target() or cname.endswith("." + target()))
    if not d.cname_ok:
        errors.append(f"{d.domain} must be a CNAME to {target()}" + (f" (found {cname})" if cname else " (no CNAME found)"))
    if HAVE_DNSPYTHON:
        d.method = "dns_txt"
        vals = []
        try:
            vals = lookup_txt(txt_name(d.domain))
        except Exception as e:
            errors.append(f"TXT lookup failed: {e}")
        d.txt_ok = txt_value(d.verification_token) in vals
        if not d.txt_ok:
            errors.append(f"TXT record {txt_name(d.domain)} = {txt_value(d.verification_token)} not found")
        owned = d.txt_ok
    else:
        d.method = "cname"
        d.txt_ok = False
        owned = d.cname_ok
    if owned and _claimed_elsewhere(d):
        owned = False
        errors.append("This domain is already verified by another organisation")
    if owned:
        d.status = "verified"
        d.verified_at = d.verified_at or now
    else:  # never verified, or the record has since been removed
        d.status, d.verified_at = "failed", None
    d.last_error = "; ".join(errors) or None
    return d


def _claimed_elsewhere(d: sm.CustomDomain) -> bool:
    with system_session() as sdb:
        return bool(sdb.scalar(select(sm.CustomDomain.id).where(sm.CustomDomain.domain == d.domain, sm.CustomDomain.status == "verified",
                                                                 sm.CustomDomain.organisation_id != d.organisation_id).limit(1)))


OPERATOR_STEPS = [
    "Point the platform ingress at CUSTOM_DOMAIN_TARGET (that hostname must resolve to the load balancer).",
    "For each VERIFIED domain, issue a certificate (e.g. cert-manager / Caddy on-demand TLS / ACM) — HTTP-01 works once the CNAME is live.",
    "Add the domain to the ingress host list and to CORS_ORIGINS for the API.",
    "Mark TLS as active for the domain (operator database update of custom_domain.tls_status = 'active').",
]
