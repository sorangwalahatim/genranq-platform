"""Plans, feature entitlements and quotas derived from the *Pricing Matrix* sheet.

Nothing here hardcodes tier values: each row of the sheet is parsed at runtime from the
active spec snapshot, so approving a refreshed workbook changes limits.
The sheet carries no prices, so none are shown (Enterprise = "Contact sales").
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import models as m
from ..spec.store import active_spec

# Pricing Matrix row → entitlement key and how it is enforced.
ROW_KEYS = {
    "Companies / Contacts": ("contacts", "limit"),
    "Dashboard": ("dashboard", "flag"),
    "Roles & Permissions": ("roles_permissions", "flag"),
    "Staff Users": ("staff_users", "limit"),
    "Activity Logs": ("activity_logs", "flag"),
    "File Storage": ("storage_bytes", "limit"),
    "All Integrations (Google/Meta/Bing…)": ("integrations", "flag"),
    "Rank Tracker keywords": ("rank_keywords", "limit"),
    "Local Rank Tracker credits": ("local_rank_credits", "metered"),
    "SEO Audit pages": ("audit_pages", "metered"),
    "Audit frequency": ("scheduled_audits", "frequency"),
    "Report export (Excel/PDF)": ("report_export", "flag"),
    "Accounting (Xero/MYOB/QuickBooks)": ("accounting", "addon"),
    "API Access": ("api_access", "flag"),
    "Projects / Tasks": ("projects", "limit"),
    "Multiple assignee / filters / bulk": ("multi_assignee", "flag"),
    "Kanban & List views": ("kanban", "flag"),
    "Looker Studio & custom reporting": ("custom_reporting", "flag"),
    "Schedule reports": ("scheduled_reports", "flag"),
    "Report branding (logo)": ("report_branding", "flag"),
    "Report white-label": ("white_label", "flag"),
    "Client user access": ("client_users", "flag"),
    "Support: Email": ("support_email", "flag"),
    "Support: Chat": ("support_chat", "flag"),
    "Onboarding training": ("onboarding", "flag"),
    "Knowledge base / LMS / Zoom": ("knowledge_base", "flag"),
}

UNITS = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def parse_value(raw: str, kind: str):
    v = (raw or "").strip()
    if v in ("—", "-", "", "None"):
        return {"enabled": False, "limit": 0, "display": "—"}
    if v == "✔":
        return {"enabled": True, "limit": None, "display": "✔"}
    if v.lower() == "unlimited":
        return {"enabled": True, "limit": None, "display": "Unlimited"}
    if v.lower() == "custom":
        return {"enabled": True, "limit": None, "custom": True, "display": "Custom"}
    if v.lower() == "add-on":
        return {"enabled": False, "addon": True, "limit": None, "display": "Add-on"}
    mm = re.match(r"^([\d,.]+)\s*(KB|MB|GB|TB)$", v, re.I)
    if mm:
        return {"enabled": True, "limit": int(float(mm.group(1).replace(",", "")) * UNITS[mm.group(2).upper()]), "display": v}
    mm = re.match(r"^([\d,]+)\s*/\s*mo", v, re.I)
    if mm:
        return {"enabled": True, "limit": int(mm.group(1).replace(",", "")), "period": "month", "display": v}
    if kind == "frequency":
        return {"enabled": "automated" in v.lower(), "limit": None, "display": v}
    return {"enabled": True, "limit": None, "display": v}


def plans() -> dict:
    spec = active_spec()
    tiers = spec["pricing"]["tiers"]
    out = {}
    for t in tiers:
        key = _slug(t)
        feats = {}
        for row in spec["pricing"]["features"]:
            ek, kind = ROW_KEYS.get(row["feature"], (_slug(row["feature"]), "flag"))
            val = parse_value(row["values"].get(t, "—"), kind)
            val.update({"label": row["feature"], "kind": kind})
            feats[ek] = val
        out[key] = {"key": key, "name": t, "features": feats}
    return out


def plan_for(org: m.Organisation) -> dict:
    p = plans()
    if org.plan not in p:
        raise HTTPException(500, f"Organisation plan '{org.plan}' not in Pricing Matrix")
    plan = p[org.plan]
    overrides = (org.branding or {}).get("_entitlement_overrides", {})  # Enterprise custom contracts
    for k, v in overrides.items():
        if k in plan["features"]:
            plan["features"][k].update(v)
    for a in org.addons or []:
        if a in plan["features"] and plan["features"][a].get("addon"):
            plan["features"][a]["enabled"] = True
    return plan


def feature(db: Session, org_id, key: str) -> dict:
    org = db.get(m.Organisation, org_id)
    return plan_for(org)["features"].get(key, {"enabled": False, "limit": 0})


def require_feature(db: Session, org_id, key: str):
    f = feature(db, org_id, key)
    if not f.get("enabled"):
        raise HTTPException(402, detail={"code": "plan_upgrade_required", "feature": key,
                                         "message": f"'{f.get('label', key)}' is not included in your plan"})


def period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def metered_used(db: Session, org_id, metric: str) -> int:
    return db.scalar(select(m.UsageCounter.used).where(
        m.UsageCounter.organisation_id == org_id, m.UsageCounter.metric == metric,
        m.UsageCounter.period == period())) or 0


def consume(db: Session, org_id, metric: str, amount: int, *, allow_partial: bool = False) -> int:
    """Consume metered quota atomically (row lock). Returns the amount granted. Raises 402 when exhausted."""
    if not isinstance(amount, int) or amount <= 0:
        raise HTTPException(400, "Usage amount must be a positive integer")
    f = feature(db, org_id, metric)
    limit = f.get("limit")
    db.execute(insert(m.UsageCounter).values(organisation_id=org_id, metric=metric, period=period(), used=0)
               .on_conflict_do_nothing())
    row = db.scalar(select(m.UsageCounter).where(m.UsageCounter.organisation_id == org_id, m.UsageCounter.metric == metric,
                                                 m.UsageCounter.period == period()).with_for_update())
    grant = amount
    if limit is not None:
        remaining = max(0, limit - row.used)
        if remaining <= 0 or (remaining < amount and not allow_partial):
            raise HTTPException(402, detail={"code": "quota_exceeded", "metric": metric, "limit": limit,
                                             "used": row.used, "message": f"Monthly quota for {f.get('label', metric)} reached"})
        grant = min(amount, remaining)
    row.used = row.used + grant
    return grant


def refund(db: Session, org_id, metric: str, amount: int):
    if not amount or amount <= 0:
        return
    stmt = insert(m.UsageCounter).values(organisation_id=org_id, metric=metric, period=period(), used=0)
    db.execute(stmt.on_conflict_do_update(index_elements=["organisation_id", "metric", "period"],
                                          set_={"used": func.greatest(0, m.UsageCounter.used - amount)}))


def check_count_limit(db: Session, org_id, key: str, current: int, adding: int = 1):
    f = feature(db, org_id, key)
    if not f.get("enabled", True) and f.get("limit") == 0:
        raise HTTPException(402, detail={"code": "plan_upgrade_required", "feature": key,
                                         "message": f"'{f.get('label', key)}' is not included in your plan"})
    lim = f.get("limit")
    if lim is not None and current + adding > lim:
        raise HTTPException(402, detail={"code": "quota_exceeded", "metric": key, "limit": lim, "used": current,
                                         "message": f"Plan limit reached for {f.get('label', key)} ({lim})"})


def usage_summary(db: Session, org_id) -> dict:
    org = db.get(m.Organisation, org_id)
    plan = plan_for(org)
    F = plan["features"]
    storage = db.scalar(select(func.coalesce(func.sum(m.StoredFile.size_bytes), 0))) or 0
    rows = {
        "rank_keywords": db.scalar(select(func.count()).select_from(m.Keyword)) or 0,
        "audit_pages": metered_used(db, org_id, "audit_pages"),
        "local_rank_credits": metered_used(db, org_id, "local_rank_credits"),
        "storage_bytes": int(storage),
        "staff_users": db.scalar(select(func.count()).select_from(m.User).where(m.User.role != "client", m.User.is_active)) or 0,
        "projects": db.scalar(select(func.count()).select_from(m.Project)) or 0,
    }
    meters = []
    for k, used in rows.items():
        f = F.get(k, {})
        lim = f.get("limit")
        meters.append({"key": k, "label": f.get("label", k), "used": used, "limit": lim,
                       "remaining": None if lim is None else max(0, lim - used),
                       "period": f.get("period"), "display": f.get("display"), "custom": f.get("custom", False)})
    return {"plan": {"key": plan["key"], "name": plan["name"]}, "period": period(), "meters": meters,
            "features": F}
