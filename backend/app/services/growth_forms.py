"""Form builder (64) and inbound form connectors (65): schema validation, submission validation,
spam guard, webhook signature verification, platform presets and field mapping."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time

from fastapi import HTTPException

from .growth_leads import LEAD_FIELDS

FIELD_TYPES = ["text", "email", "phone", "select", "checkbox", "textarea", "hidden"]
MAPPABLE = ["email", "phone", "name", "first_name", "last_name", "company", "value", "notes"]
UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"]
DEFAULT_SPAM = {"honeypot": True, "min_seconds": 3, "rate_limit": 5, "rate_window_min": 10, "blocked_words": []}
HONEYPOT_FIELD = "_gq_website"

CONTACT_TEMPLATE = {
    "fields": [
        {"key": "name", "label": "Your name", "type": "text", "required": True, "maps_to": "name", "validation": {"max_length": 120}},
        {"key": "email", "label": "Email", "type": "email", "required": True, "maps_to": "email"},
        {"key": "phone", "label": "Phone", "type": "phone", "required": False, "maps_to": "phone"},
        {"key": "company", "label": "Company", "type": "text", "required": False, "maps_to": "company"},
        {"key": "message", "label": "How can we help?", "type": "textarea", "required": True, "maps_to": "notes",
         "validation": {"min_length": 5, "max_length": 3000}},
        {"key": "consent", "label": "I agree to be contacted about my enquiry", "type": "checkbox", "required": True},
        *[{"key": k, "label": k, "type": "hidden"} for k in UTM_KEYS],
    ],
    "submit_label": "Send enquiry",
    "success_message": "Thanks — we’ll be in touch shortly.",
}


# ------------------------------------------------------------------ builder validation
def validate_schema(schema: dict) -> dict:
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields:
        raise HTTPException(400, "A form needs at least one field")
    if len(fields) > 60:
        raise HTTPException(400, "A form can have at most 60 fields")
    keys, out = set(), []
    for f in fields:
        k = str(f.get("key", ""))
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", k) or k in keys or k.startswith("_"):
            raise HTTPException(400, f"field key '{k}' must be unique, lowercase a-z0-9_ and start with a letter")
        keys.add(k)
        t = f.get("type")
        if t not in FIELD_TYPES:
            raise HTTPException(400, f"field type must be one of {FIELD_TYPES}")
        maps = f.get("maps_to")
        if maps and maps not in MAPPABLE:
            raise HTTPException(400, f"maps_to must be one of {MAPPABLE}")
        if t == "email" and not maps:
            maps = "email"
        if t == "phone" and not maps:
            maps = "phone"
        v = dict(f.get("validation") or {})
        for n in ("min_length", "max_length"):
            if n in v and (not isinstance(v[n], int) or not 0 <= v[n] <= 10000):
                raise HTTPException(400, f"{n} must be an integer 0–10000")
        if v.get("pattern"):
            if len(v["pattern"]) > 200:
                raise HTTPException(400, "pattern too long")
            try:
                re.compile(v["pattern"])
            except re.error:
                raise HTTPException(400, f"invalid pattern for field '{k}'")
        opts = [str(o)[:200] for o in (f.get("options") or [])]
        if t == "select" and not opts:
            raise HTTPException(400, f"select field '{k}' needs options")
        out.append({"key": k, "label": str(f.get("label") or k)[:200], "type": t, "required": bool(f.get("required")) and t != "hidden",
                    "maps_to": maps, "options": opts, "placeholder": str(f.get("placeholder") or "")[:200],
                    "help": str(f.get("help") or "")[:300], "validation": v})
    red = schema.get("redirect_url")
    if red and not re.match(r"^https?://", str(red)):
        raise HTTPException(400, "redirect_url must be http(s)")
    return {"fields": out, "submit_label": str(schema.get("submit_label") or "Submit")[:60],
            "success_message": str(schema.get("success_message") or "Thank you!")[:500], "redirect_url": red or None}


def validate_settings(settings: dict) -> dict:
    sp = {**DEFAULT_SPAM, **(settings.get("spam") or {})}
    if not isinstance(sp["min_seconds"], int) or not 0 <= sp["min_seconds"] <= 120:
        raise HTTPException(400, "spam.min_seconds must be 0–120")
    if not isinstance(sp["rate_limit"], int) or not 1 <= sp["rate_limit"] <= 1000:
        raise HTTPException(400, "spam.rate_limit must be 1–1000")
    if not isinstance(sp["rate_window_min"], int) or not 1 <= sp["rate_window_min"] <= 1440:
        raise HTTPException(400, "spam.rate_window_min must be 1–1440")
    words = [str(w).strip().lower()[:60] for w in (sp.get("blocked_words") or []) if str(w).strip()][:200]
    return {"spam": {"honeypot": bool(sp["honeypot"]), "min_seconds": sp["min_seconds"], "rate_limit": sp["rate_limit"],
                     "rate_window_min": sp["rate_window_min"], "blocked_words": words},
            "assignee_ids": [str(x) for x in (settings.get("assignee_ids") or [])][:50],
            "questionnaire_id": settings.get("questionnaire_id") or None,
            "notify": bool(settings.get("notify", True))}


# ------------------------------------------------------------------ submission validation
PHONE_RE = re.compile(r"^\+?[0-9 ()./-]{7,25}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


def validate_submission(schema: dict, values: dict) -> tuple[dict, dict]:
    clean, errors = {}, {}
    for f in schema["fields"]:
        k, t, v = f["key"], f["type"], values.get(f["key"])
        if isinstance(v, str):
            v = v.strip()
        if t == "checkbox":
            if f.get("options"):
                vals = v if isinstance(v, list) else ([v] if v not in (None, "", False) else [])
                bad = [x for x in vals if str(x) not in f["options"]]
                if bad:
                    errors[k] = "Invalid choice"
                    continue
                v = [str(x) for x in vals]
                if f["required"] and not v:
                    errors[k] = "Please choose at least one option"
                    continue
            else:
                v = v in (True, "true", "on", "1", 1, "yes")
                if f["required"] and not v:
                    errors[k] = "This box must be ticked"
                    continue
            clean[k] = v
            continue
        if v in (None, ""):
            if f["required"]:
                errors[k] = "This field is required"
            continue
        if not isinstance(v, (str, int, float)):
            errors[k] = "Invalid value"
            continue
        v = str(v)
        val = f.get("validation") or {}
        maxl = val.get("max_length") or (5000 if t == "textarea" else 500)
        if len(v) > maxl:
            errors[k] = f"Must be at most {maxl} characters"
            continue
        if val.get("min_length") and len(v) < val["min_length"]:
            errors[k] = f"Must be at least {val['min_length']} characters"
            continue
        if t == "email" and not EMAIL_RE.match(v):
            errors[k] = "Enter a valid email address"
            continue
        if t == "phone" and (not PHONE_RE.match(v) or len(re.sub(r"\D", "", v)) < 7):
            errors[k] = "Enter a valid phone number"
            continue
        if t == "select" and v not in f["options"]:
            errors[k] = "Choose one of the options"
            continue
        if val.get("pattern") and not re.fullmatch(val["pattern"], v):
            errors[k] = val.get("pattern_message") or "Invalid format"
            continue
        clean[k] = v
    return clean, errors


def lead_data_from(schema: dict, clean: dict) -> dict:
    d, extra = {}, {}
    for f in schema["fields"]:
        if f["key"] not in clean or f["type"] == "hidden":
            continue
        v = clean[f["key"]]
        if f.get("maps_to"):
            d[f["maps_to"]] = v if not isinstance(v, list) else ", ".join(v)
        elif not isinstance(v, bool):
            extra[f["key"]] = v if not isinstance(v, list) else ", ".join(v)
    return {**extra, **d}


def blocked_word(words: list[str], values: dict) -> str | None:
    if not words:
        return None
    blob = " ".join(str(v).lower() for v in values.values() if isinstance(v, (str, int, float)))
    for w in words:
        if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", blob):
            return w
    return None


# ------------------------------------------------------------------ connectors
PRESETS = {
    "generic": {
        "label": "Generic webhook (any platform)", "signature": "genranq",
        "header": "X-Genranq-Signature: sha256=<hex HMAC-SHA256 of the raw body with the connector secret>",
        "mapping": {"email": "email", "phone": "phone", "name": "name", "company": "company",
                    "utm_source": "utm_source", "utm_medium": "utm_medium", "utm_campaign": "utm_campaign", "referrer": "referrer", "page_url": "page_url"},
        "sample": {"name": "Ada Lovelace", "email": "ada@example.com", "phone": "+44 20 7946 0000", "company": "Analytical Engines Ltd",
                   "utm_source": "newsletter", "utm_medium": "email", "utm_campaign": "spring-launch", "page_url": "https://example.com/contact"},
    },
    "typeform": {
        "label": "Typeform", "signature": "typeform",
        "header": "Typeform-Signature: sha256=<base64 HMAC-SHA256> (set the secret in Typeform → Connect → Webhooks)",
        "mapping": {"email": "email", "name": "name", "phone": "phone", "company": "company",
                    "utm_source": "utm_source", "utm_medium": "utm_medium", "utm_campaign": "utm_campaign"},
        "sample": {"event_type": "form_response", "form_response": {
            "form_id": "lT4Z3j", "submitted_at": "2026-01-01T10:00:00Z",
            "hidden": {"utm_source": "google", "utm_medium": "cpc", "utm_campaign": "brand"},
            "answers": [
                {"type": "text", "text": "Grace Hopper", "field": {"id": "a1", "ref": "name", "type": "short_text"}},
                {"type": "email", "email": "grace@example.com", "field": {"id": "a2", "ref": "email", "type": "email"}},
                {"type": "phone_number", "phone_number": "+15555550100", "field": {"id": "a3", "ref": "phone", "type": "phone_number"}},
                {"type": "choice", "choice": {"label": "SEO"}, "field": {"id": "a4", "ref": "service", "type": "multiple_choice"}}]}},
    },
    "gravity_forms": {
        "label": "Gravity Forms (Webhooks add-on)", "signature": "genranq",
        "header": "Add a request header X-Genranq-Signature with the HMAC (or use a signing proxy); body format JSON, all fields",
        "mapping": {"first_name": "1.3", "last_name": "1.6", "email": "2", "phone": "3", "company": "4", "notes": "5",
                    "utm_source": "utm_source", "utm_campaign": "utm_campaign", "page_url": "source_url"},
        "sample": {"1.3": "Katherine", "1.6": "Johnson", "2": "kj@example.com", "3": "(555) 555-0101", "4": "NASA",
                   "5": "Need an SEO audit", "source_url": "https://example.com/contact?utm_source=bing&utm_medium=cpc", "form_id": "1"},
    },
    "webflow": {
        "label": "Webflow forms", "signature": "webflow",
        "header": "x-webflow-timestamp + x-webflow-signature (hex HMAC-SHA256 of '<timestamp>:<body>')",
        "mapping": {"name": "Name", "email": "Email", "phone": "Phone", "company": "Company", "notes": "Message", "page_url": "_page_url"},
        "sample": {"triggerType": "form_submission", "payload": {"name": "Contact", "siteId": "abc", "pageUrl": "https://example.webflow.io/contact",
                                                                  "data": {"Name": "Dorothy Vaughan", "Email": "dorothy@example.com", "Message": "Quote please"}}},
    },
    "facebook_lead_ads": {
        "label": "Facebook Lead Ads (via Meta)", "signature": "meta",
        "header": "X-Hub-Signature-256: sha256=<hex HMAC-SHA256 with your Meta app secret>; subscription verified with hub.verify_token",
        "mapping": {"email": "email", "name": "full_name", "phone": "phone_number", "company": "company_name",
                    "utm_campaign": "campaign_name"},
        "sample": {"object": "page", "entry": [{"id": "1234", "time": 1700000000, "changes": [
            {"field": "leadgen", "value": {"leadgen_id": "444444444", "page_id": "1234", "form_id": "555", "ad_id": "666"}}]}]},
    },
}


def verify_signature(preset: str, secret: str, headers: dict, body: bytes, tolerance: int = 300) -> bool:
    h = {k.lower(): v for k, v in headers.items()}
    scheme = PRESETS[preset]["signature"]
    key = secret.encode()
    if scheme in ("genranq", "meta"):
        got = h.get("x-genranq-signature" if scheme == "genranq" else "x-hub-signature-256", "")
        if not got.startswith("sha256="):
            return False
        exp = hmac.new(key, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(got[7:].strip().lower(), exp)
    if scheme == "typeform":
        got = h.get("typeform-signature", "")
        if not got.startswith("sha256="):
            return False
        exp = base64.b64encode(hmac.new(key, body, hashlib.sha256).digest()).decode()
        return hmac.compare_digest(got[7:].strip(), exp)
    if scheme == "webflow":
        ts, got = h.get("x-webflow-timestamp", ""), h.get("x-webflow-signature", "")
        if not ts.isdigit() or not got:
            return False
        t = int(ts) / (1000 if len(ts) > 11 else 1)
        if abs(time.time() - t) > tolerance:
            return False
        exp = hmac.new(key, f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(got.strip().lower(), exp)
    return False


def sign_for_preset(preset: str, secret: str, body: bytes) -> dict:
    """Headers a sender would attach (used by the sample tester and documentation)."""
    scheme = PRESETS[preset]["signature"]
    key = secret.encode()
    if scheme == "genranq":
        return {"X-Genranq-Signature": "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()}
    if scheme == "meta":
        return {"X-Hub-Signature-256": "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()}
    if scheme == "typeform":
        return {"Typeform-Signature": "sha256=" + base64.b64encode(hmac.new(key, body, hashlib.sha256).digest()).decode()}
    ts = str(int(time.time() * 1000))
    return {"x-webflow-timestamp": ts, "x-webflow-signature": hmac.new(key, f"{ts}:".encode() + body, hashlib.sha256).hexdigest()}


def _flat(d, prefix="", out=None) -> dict:
    out = {} if out is None else out
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, (dict, list)):
                _flat(v, f"{prefix}{k}.", out)
            else:
                out[f"{prefix}{k}"] = v
    elif isinstance(d, list):
        if all(isinstance(x, (str, int, float, bool)) for x in d):
            out[prefix.rstrip(".")] = ", ".join(str(x) for x in d)
        else:
            for i, v in enumerate(d):
                if isinstance(v, (dict, list)):
                    _flat(v, f"{prefix}{i}.", out)
                else:
                    out[f"{prefix}{i}"] = v
    return out


def _typeform_value(a: dict):
    t = a.get("type")
    if t == "choice":
        return (a.get("choice") or {}).get("label")
    if t == "choices":
        return ", ".join((a.get("choices") or {}).get("labels") or [])
    return a.get(t)


def flatten(preset: str, payload) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(400, "Payload must be a JSON object")
    if preset == "typeform":
        fr = payload.get("form_response") or {}
        out = {}
        for a in fr.get("answers") or []:
            f = a.get("field") or {}
            v = _typeform_value(a)
            if v is None:
                continue
            for k in (f.get("ref"), f.get("id")):
                if k:
                    out[str(k)] = v
        for k, v in (fr.get("hidden") or {}).items():
            out[str(k)] = v
        return out
    if preset == "webflow":
        p = payload.get("payload") or payload
        out = dict(p.get("data") or {})
        if p.get("pageUrl"):
            out["_page_url"] = p["pageUrl"]
        return {str(k): v for k, v in out.items()}
    if preset == "facebook_lead_ads":
        return {}
    return {k: v for k, v in _flat(payload).items() if isinstance(v, (str, int, float, bool))}


def facebook_lead_ids(payload: dict) -> list[str]:
    ids = []
    for e in payload.get("entry") or []:
        for c in e.get("changes") or []:
            v = c.get("value") or {}
            if c.get("field") == "leadgen" and v.get("leadgen_id"):
                ids.append(str(v["leadgen_id"]))
    return ids[:50]


def facebook_flat(lead: dict) -> dict:
    out = {fd.get("name"): ", ".join(fd.get("values") or []) for fd in lead.get("field_data") or [] if fd.get("name")}
    for k in ("campaign_name", "ad_name", "form_id", "created_time"):
        if lead.get(k):
            out[k] = lead[k]
    return out


def validate_mapping(mapping: dict) -> dict:
    out = {}
    for k, v in (mapping or {}).items():
        if k not in LEAD_FIELDS:
            raise HTTPException(400, f"Unknown lead field '{k}'. Allowed: {LEAD_FIELDS}")
        if not isinstance(v, str) or not v.strip() or len(v) > 200:
            raise HTTPException(400, f"Mapping for '{k}' must be a source field name")
        out[k] = v.strip()
    if not any(k in out for k in ("email", "phone", "name", "first_name")):
        raise HTTPException(400, "Map at least one of email, phone or name")
    return out


def apply_mapping(mapping: dict, flat: dict) -> tuple[dict, dict]:
    data, touch = {}, {}
    for lead_field, src in mapping.items():
        v = flat.get(src)
        if v in (None, ""):
            continue
        v = str(v)[:2000]
        if lead_field in UTM_KEYS or lead_field in ("referrer", "page_url"):
            touch[lead_field] = v
        else:
            data[lead_field] = v
    return data, touch


def parse_body(body: bytes, content_type: str | None) -> dict:
    ct = (content_type or "").lower()
    if "application/x-www-form-urlencoded" in ct:
        from urllib.parse import parse_qs
        q = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        return {k: (v if len(v) > 1 else v[0]) for k, v in q.items()}
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Body must be JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "Body must be a JSON object")
    return data
