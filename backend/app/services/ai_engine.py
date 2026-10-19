"""AI engine: provider hub routing (module 50) and cost & usage accounting (module 54).

* Routing: each organisation orders its AI providers, switches them on/off, picks a default model per task
  type and may set a monthly budget per provider. ``agents.llm`` asks :func:`route` for the ordered list of
  connected, enabled providers and falls back down that list when one errors.
* Budget: an organisation-wide monthly cap (USD). When this month's recorded spend reaches it, ``agents.llm``
  raises ``agents.BudgetExceeded`` (a ``NoAI``) before any provider is called. A provider whose own cap is
  reached is skipped (the next provider is tried).
* Usage: every successful call is written to ``ai_usage`` (+ ``ai_usage_detail``) in its own committed
  transaction, with cost from :data:`PRICES` — customer-facing list prices per million tokens, maintained here.
"""
from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, text

from .. import models as m
from .. import models_ai as ma
from ..db import tenant_session

PROVIDERS = ["claude", "openai", "gemini", "perplexity"]
PROVIDER_NAMES = {"claude": "Claude (Anthropic)", "openai": "OpenAI (ChatGPT)", "gemini": "Google Gemini", "perplexity": "Perplexity"}
DEFAULT_MODEL = {"claude": os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5"), "openai": "gpt-4o-mini", "gemini": "gemini-1.5-flash", "perplexity": "sonar"}
TASK_TYPES = ["general", "blog", "news", "whitepaper", "publication", "playbook", "web_page", "landing", "social", "image",
              "brief", "outline", "seo", "agent_team", "skill", "citations"]

# Customer-facing list prices, USD per 1M tokens (input, output). Longest model-id prefix wins.
# Reviewed 2026-10 against the providers' public pricing pages; update here when providers change prices.
PRICES: dict[str, dict[str, tuple[float, float]]] = {
    "claude": {
        "claude-fable-5-1": (10, 50), "claude-mythos-5": (10, 50), "claude-fable-5": (10, 50),
        "claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10), "claude-opus-5": (5, 25), "claude-sonnet-5": (2, 10),
        "claude-opus-4-8": (5, 25), "claude-opus-4-7": (5, 25), "claude-opus-4-6": (5, 25), "claude-opus-4-5": (5, 25),
        "claude-opus-4-1": (15, 75), "claude-opus-4": (15, 75),
        "claude-sonnet-4-6": (3, 15), "claude-sonnet-4-5": (3, 15), "claude-sonnet-4": (3, 15),
        "claude-haiku-4-5": (1, 5), "claude-3-5-haiku": (0.8, 4),
    },
    "openai": {
        "gpt-5-mini": (0.25, 2), "gpt-5": (1.25, 10), "gpt-4.1-mini": (0.4, 1.6), "gpt-4.1": (2, 8),
        "gpt-4o-mini": (0.15, 0.6), "gpt-4o": (2.5, 10), "o4-mini": (1.1, 4.4), "o3": (2, 8),
    },
    "gemini": {
        "gemini-2.5-pro": (1.25, 10), "gemini-2.5-flash": (0.3, 2.5), "gemini-2.0-flash": (0.1, 0.4),
        "gemini-1.5-pro": (1.25, 5), "gemini-1.5-flash": (0.075, 0.3),
    },
    "perplexity": {"sonar-reasoning": (1, 5), "sonar-pro": (3, 15), "sonar": (1, 1)},
}
# USD per generated image (standard quality, ~1 megapixel).
IMAGE_PRICES = {"openai": {"gpt-image-1": 0.04, "dall-e-3": 0.04}, "gemini": {"imagen-3.0-generate-002": 0.03}}
# Unknown model of a known provider: priced at the provider's most expensive listed model, flagged as estimated.
FALLBACK = {k: max(v.values(), key=lambda p: p[0] + p[1]) for k, v in PRICES.items()}


def price_for(provider: str, model: str | None) -> tuple[tuple[float, float], bool]:
    table = PRICES.get(provider, {})
    mdl = (model or "").lower()
    best = max((k for k in table if mdl.startswith(k)), key=len, default=None)
    if best:
        return table[best], False
    return FALLBACK.get(provider, (0.0, 0.0)), True


def cost_of(provider: str, model: str | None, tokens_in: int, tokens_out: int) -> float:
    (pin, pout), _ = price_for(provider, model)
    return round((tokens_in * pin + tokens_out * pout) / 1_000_000, 6)


def image_cost(provider: str, model: str, n: int) -> float:
    return round(IMAGE_PRICES.get(provider, {}).get(model, 0.04) * n, 6)


def price_table() -> list[dict]:
    rows = [{"provider": p, "model": mdl, "kind": "text", "input_per_mtok": a, "output_per_mtok": b}
            for p, t in PRICES.items() for mdl, (a, b) in t.items()]
    rows += [{"provider": p, "model": mdl, "kind": "image", "per_image": v} for p, t in IMAGE_PRICES.items() for mdl, v in t.items()]
    return rows


# ------------------------------------------------------------------ call scope (who/what the call is for)
_scope: contextvars.ContextVar[dict | None] = contextvars.ContextVar("ai_scope", default=None)


@contextmanager
def usage_scope(project_id=None, user_id=None, task_type: str | None = None):
    """Tag every llm() call inside the block with project/user/task and collect its usage records."""
    parent = _scope.get()
    d = {"project_id": project_id or (parent or {}).get("project_id"), "user_id": user_id or (parent or {}).get("user_id"),
         "task_type": task_type or (parent or {}).get("task_type"), "records": []}
    tok = _scope.set(d)
    try:
        yield d
    finally:
        _scope.reset(tok)
        if parent is not None:
            parent["records"].extend(d["records"])


def current_scope() -> dict:
    return _scope.get() or {}


def totals(records: list[dict]) -> dict:
    return {"tokens_in": sum(r["tokens_in"] for r in records), "tokens_out": sum(r["tokens_out"] for r in records),
            "cost": round(sum(r["cost"] for r in records), 6), "calls": len(records)}


# ------------------------------------------------------------------ provider rows + preferences
def ensure_provider(db, org_id, key: str) -> m.AiProvider:
    db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"ai_provider:{org_id}:{key}"})
    p = db.scalar(select(m.AiProvider).where(m.AiProvider.type == key, m.AiProvider.organisation_id == org_id).limit(1))
    if not p:
        p = m.AiProvider(organisation_id=org_id, name=PROVIDER_NAMES.get(key, key), type=key, default_model=DEFAULT_MODEL.get(key))
        db.add(p)
        db.flush()
    return p


def prefs(db) -> dict[str, tuple[m.AiProvider, ma.AiProviderPref | None]]:
    out = {}
    for prov in db.scalars(select(m.AiProvider)):
        if prov.type in PROVIDERS and prov.type not in out:
            out[prov.type] = (prov, db.get(ma.AiProviderPref, prov.id))
    return out


def ordered_providers(db, prefer: tuple | list = tuple(PROVIDERS)) -> list[dict]:
    """All four providers in the organisation's routing order with their settings."""
    pr = prefs(db)
    rows = []
    for i, key in enumerate(list(prefer) + [p for p in PROVIDERS if p not in prefer]):
        prov, pf = pr.get(key, (None, None))
        rows.append({"key": key, "position": pf.position if pf else 100 + i, "enabled": pf.enabled if pf else True,
                     "task_models": (pf.task_models if pf else {}) or {}, "monthly_budget": float(pf.monthly_budget) if pf and pf.monthly_budget is not None else None,
                     "default_model": (prov.default_model if prov else None) or DEFAULT_MODEL[key], "provider_id": str(prov.id) if prov else None,
                     "configured": pf is not None})
    rows.sort(key=lambda r: r["position"])
    return rows


def model_for(row: dict, task_type: str, conn_config: dict | None) -> str:
    tm = row.get("task_models") or {}
    return tm.get(task_type) or tm.get("default") or (conn_config or {}).get("model") or row.get("default_model") or DEFAULT_MODEL[row["key"]]


def route(db, org_id, task_type: str, prefer=tuple(PROVIDERS)) -> list[dict]:
    """Connected + enabled providers in routing order, each with the model to use and whether its own cap is hit."""
    from . import integrations as ih
    conn = ih.connected_map(db)
    out = []
    spend = provider_spend(db)
    for r in ordered_providers(db, prefer):
        if conn.get(r["key"]) not in ("CONNECTED", "RATE_LIMITED") or not r["enabled"]:
            continue
        capped = r["monthly_budget"] is not None and spend.get(r["key"], 0.0) >= r["monthly_budget"]
        out.append({**r, "over_budget": capped})
    return out


# ------------------------------------------------------------------ spend + budget
def month_start() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_spend(db) -> float:
    v = db.scalar(select(func.coalesce(func.sum(m.AiUsage.cost), 0)).where(m.AiUsage.at >= month_start()))
    return float(v or 0)


def provider_spend(db) -> dict[str, float]:
    rows = db.execute(select(m.AiProvider.type, func.coalesce(func.sum(m.AiUsage.cost), 0))
                      .join(m.AiProvider, m.AiProvider.id == m.AiUsage.provider_id)
                      .where(m.AiUsage.at >= month_start()).group_by(m.AiProvider.type)).all()
    return {k: float(v or 0) for k, v in rows}


def org_setting(db, org_id) -> ma.AiOrgSetting | None:
    return db.get(ma.AiOrgSetting, org_id)


def budget_status(db, org_id) -> dict:
    s = org_setting(db, org_id)
    cap = float(s.monthly_budget) if s and s.monthly_budget is not None else None
    spend = month_spend(db)
    return {"monthly_budget": cap, "spent": round(spend, 6), "remaining": None if cap is None else round(max(0.0, cap - spend), 6),
            "blocked": cap is not None and spend >= cap, "alert_pct": s.alert_pct if s else 80,
            "alert": cap is not None and cap > 0 and spend >= cap * (s.alert_pct if s else 80) / 100}


def check_budget(db, org_id):
    from .agents import BudgetExceeded
    b = budget_status(db, org_id)
    if b["blocked"]:
        raise BudgetExceeded(f"AI monthly budget cap reached (${b['spent']:.2f} of ${b['monthly_budget']:.2f}) — "
                             "raise the cap in AI Provider Hub to continue")


def ai_quota(db, org_id) -> dict | None:
    """AI limits from the Pricing Matrix, if the active sheet defines any (rows mentioning AI)."""
    import re
    from . import entitlements
    org = db.get(m.Organisation, org_id)
    feats = entitlements.plan_for(org)["features"]
    rows = {k: v for k, v in feats.items() if re.search(r"\bAI\b|\btokens?\b|\bgenerat", v.get("label", ""), re.I)}
    return rows or None


# ------------------------------------------------------------------ usage recording
def record_usage(org_id, provider: str, model: str | None, tokens_in: int, tokens_out: int, *, task_type: str | None = None,
                 project_id=None, user_id=None, estimated: bool = False, kind: str = "text", units: int = 0,
                 cost: float | None = None) -> dict:
    """Write one ai_usage row in its own transaction (so spend is counted even if the caller rolls back)."""
    sc = current_scope()
    task_type = task_type or sc.get("task_type") or "general"
    project_id = project_id or sc.get("project_id")
    user_id = user_id or sc.get("user_id")
    if cost is None:
        cost = cost_of(provider, model, tokens_in, tokens_out)
    with tenant_session(org_id) as s:
        prov = ensure_provider(s, org_id, provider)
        u = m.AiUsage(organisation_id=org_id, provider_id=prov.id, model=model, tokens_in=int(tokens_in or 0),
                      tokens_out=int(tokens_out or 0), cost=Decimal(str(cost)))
        s.add(u)
        s.flush()
        if project_id and not s.get(m.Project, project_id):
            project_id = None
        if user_id and not s.get(m.User, user_id):
            user_id = None
        s.add(ma.AiUsageDetail(ai_usage_id=u.id, organisation_id=org_id, provider_key=provider, task_type=task_type, kind=kind,
                               units=units, project_id=project_id, user_id=user_id, estimated=estimated))
        rec = {"usage_id": u.id, "provider": provider, "model": model, "tokens_in": int(tokens_in or 0), "tokens_out": int(tokens_out or 0),
               "cost": float(cost), "task_type": task_type, "estimated": estimated}
    if sc:
        sc["records"].append(rec)
    return rec


def extract_tokens(provider: str, j: dict, prompt: str, text_out: str) -> tuple[int, int, bool]:
    u = (j or {}).get("usage") or {}
    if provider == "claude":
        tin, tout = u.get("input_tokens"), u.get("output_tokens")
    elif provider == "gemini":
        um = (j or {}).get("usageMetadata") or {}
        tin, tout = um.get("promptTokenCount"), um.get("candidatesTokenCount")
    else:
        tin, tout = u.get("prompt_tokens"), u.get("completion_tokens")
    if tin is None or tout is None:  # provider didn't report: ~4 characters per token
        return max(1, len(prompt) // 4), max(1, len(text_out or "") // 4), True
    return int(tin), int(tout), False


# ------------------------------------------------------------------ usage analytics
GROUPS = {"provider", "model", "day", "project", "user", "task"}


def usage_report(db, start: datetime, end: datetime, group: str, project_ids=None) -> list[dict]:
    U, D = m.AiUsage, ma.AiUsageDetail
    col = {"provider": D.provider_key, "model": U.model, "day": func.date_trunc("day", U.at), "project": D.project_id,
           "user": D.user_id, "task": D.task_type}[group]
    q = (select(col.label("k"), func.count().label("calls"), func.coalesce(func.sum(U.tokens_in), 0), func.coalesce(func.sum(U.tokens_out), 0),
                func.coalesce(func.sum(U.cost), 0), func.coalesce(func.sum(D.units), 0))
         .select_from(U).join(D, D.ai_usage_id == U.id, isouter=True).where(U.at >= start, U.at < end).group_by(col).order_by(col))
    if project_ids is not None:
        q = q.where(D.project_id.in_(project_ids))
    names = {}
    if group == "project":
        names = {p.id: p.name for p in db.scalars(select(m.Project))}
    elif group == "user":
        names = {u.id: (u.name or u.email) for u in db.scalars(select(m.User))}
    out = []
    for k, calls, tin, tout, cost, units in db.execute(q).all():
        key = k.date().isoformat() if hasattr(k, "date") and group == "day" else (str(k) if k is not None else None)
        out.append({"key": key, "label": names.get(k, key or "—") if group in ("project", "user") else (key or "—"),
                    "calls": int(calls), "tokens_in": int(tin), "tokens_out": int(tout), "cost": round(float(cost), 6), "images": int(units)})
    return out
