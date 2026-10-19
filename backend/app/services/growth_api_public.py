"""Public (unauthenticated) endpoints: embeddable forms, inbound connector webhooks, Calendly and Stripe
webhooks, public free tools and the public backlink monitor.

Pattern: resolve the target organisation from a public token with a read-only system lookup of the id,
then do every read/write inside ``tenant_session(org_id)`` (RLS). Per-IP rate limits on every write."""
from __future__ import annotations

import json
import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import func, select

from .. import models as m
from .. import models_growth as g
from ..db import system_session, tenant_session
from ..deps import notify
from . import growth_crm as crm
from . import growth_forms as F
from . import growth_leads as L
from . import growth_market as M
from . import growth_tools as T
from . import vault
from .growth_common import (check_render_token, client_ip, ip_hash, new_token, rate_limit, render_token, resolve_public_key, utcnow,
                            valid_email)
from .integrations import IntegrationError, NotConnected, http_call, oauth_access_token

router = APIRouter(prefix="/public", tags=["public"])
CORS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}


def _json(data, status=200):
    return JSONResponse(data, status_code=status, headers=CORS)


# ================================================================== forms
def _form_org(token: str):
    if not token or len(token) > 64:
        raise HTTPException(404, "Form not found")
    with system_session() as sdb:
        row = sdb.execute(select(g.GrowthFormMeta.organisation_id, g.GrowthFormMeta.form_id)
                          .where(g.GrowthFormMeta.public_token == token)).first()
    if not row:
        raise HTTPException(404, "Form not found")
    return row.organisation_id, row.form_id


def _public_schema(db, f: m.Form, meta: g.GrowthFormMeta) -> dict:
    qn = None
    qid = (meta.settings or {}).get("questionnaire_id")
    if qid:
        q = db.get(g.GrowthQuestionnaire, L.uuid_or_400(qid))
        if q and q.active:
            qn = {"id": str(q.id), "name": q.name, "questions": [
                {k: v for k, v in qq.items() if k != "options"} | ({"options": [{"value": o["value"], "label": o.get("label") or o["value"]}
                                                                               for o in qq.get("options", [])]} if qq.get("options") else {})
                for qq in q.questions]}
    org = db.get(m.Organisation, f.organisation_id)
    return {"name": f.name, "schema": f.schema, "questionnaire": qn, "status": meta.status,
            "honeypot_field": F.HONEYPOT_FIELD if (meta.settings or {}).get("spam", {}).get("honeypot", True) else None,
            "render_token": render_token(meta.public_token),
            "brand": {"name": (org.branding or {}).get("company_name") or org.name, "color": (org.branding or {}).get("primary_color")}}


@router.get("/forms/{token}")
def public_form(token: str):
    org_id, form_id = _form_org(token)
    with tenant_session(org_id) as db:
        f = db.get(m.Form, form_id)
        meta = db.scalar(select(g.GrowthFormMeta).where(g.GrowthFormMeta.form_id == form_id))
        if meta.status != "active":
            raise HTTPException(410, "This form is not accepting submissions")
        return _json(_public_schema(db, f, meta))


def _spam(org_id, form_id, reason, ip):
    with tenant_session(org_id) as db:
        db.add(g.GrowthSpamEvent(organisation_id=org_id, form_id=form_id, reason=reason, ip_hash=ip_hash(ip)))


@router.post("/forms/{token}/submit")
async def public_submit(token: str, request: Request):
    org_id, form_id = _form_org(token)
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise HTTPException(413, "Submission too large")
    body = F.parse_body(raw, request.headers.get("content-type"))
    ip = client_ip(request)
    with tenant_session(org_id) as db:
        meta = db.scalar(select(g.GrowthFormMeta).where(g.GrowthFormMeta.form_id == form_id))
        if meta.status != "active":
            raise HTTPException(410, "This form is not accepting submissions")
        spam = (meta.settings or {}).get("spam") or F.DEFAULT_SPAM
    # 1) per-IP rate limit (committed on its own so rejected attempts still count)
    try:
        with tenant_session(org_id) as rdb:
            rate_limit(rdb, org_id, f"form:{form_id}", ip, spam["rate_limit"], spam["rate_window_min"] * 60)
    except HTTPException:
        _spam(org_id, form_id, "rate_limited", ip)
        raise
    # 2) honeypot → silently accepted, nothing stored except the spam counter
    if spam.get("honeypot", True) and str(body.get(F.HONEYPOT_FIELD) or "").strip():
        _spam(org_id, form_id, "honeypot", ip)
        return _json({"ok": True, "message": "Thank you!"})
    # 3) time-to-submit via signed render token
    reason = check_render_token(token, body.get("_gq_t"), spam.get("min_seconds", 3))
    if reason:
        _spam(org_id, form_id, reason, ip)
        msg = "Submitted too quickly — please take a moment and try again." if reason == "too_fast" else "This form expired — reload the page and try again."
        return _json({"ok": False, "code": reason, "message": msg}, 400)
    values = {k: v for k, v in body.items() if not k.startswith("_gq_")}
    with tenant_session(org_id) as db:
        f = db.get(m.Form, form_id)
        meta = db.scalar(select(g.GrowthFormMeta).where(g.GrowthFormMeta.form_id == form_id).with_for_update())
        w = F.blocked_word(spam.get("blocked_words") or [], values)
        if w:
            db.add(g.GrowthSpamEvent(organisation_id=org_id, form_id=form_id, reason="blocked_word", ip_hash=ip_hash(ip)))
            return _json({"ok": False, "code": "blocked", "message": "Your message could not be accepted."}, 400)
        clean, errors = F.validate_submission(f.schema, values)
        answers = body.get("_gq_answers") or {}
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except ValueError:
                answers = {}
        qn = None
        qid = (meta.settings or {}).get("questionnaire_id")
        if qid:
            qn = db.get(g.GrowthQuestionnaire, L.uuid_or_400(qid))
            if qn and qn.active:
                try:
                    res = L.score_answers(qn, answers if isinstance(answers, dict) else {})
                    for k in res["missing"]:
                        errors[f"q_{k}"] = "This question is required"
                except HTTPException as e:
                    errors["_answers"] = str(e.detail)
        if errors:
            return _json({"ok": False, "code": "invalid", "errors": errors, "message": "Please correct the highlighted fields."}, 422)
        data = F.lead_data_from(f.schema, clean)
        touch = {k: clean.get(k) or body.get(k) for k in F.UTM_KEYS if clean.get(k) or body.get(k)}
        touch["referrer"] = str(body.get("_gq_referrer") or "")[:2000] or None
        touch["page_url"] = str(body.get("_gq_page") or "")[:2000] or None
        sub = m.FormSubmission(organisation_id=org_id, form_id=form_id, payload={
            "fields": clean, "answers": answers if qn else {}, "utm": {k: touch.get(k) for k in F.UTM_KEYS if touch.get(k)},
            "referrer": touch["referrer"], "page_url": touch["page_url"],
            "user_agent": (request.headers.get("user-agent") or "")[:300], "ip_hash": ip_hash(ip)})
        db.add(sub)
        db.flush()
        lead, created = L.capture(db, org_id, f.project_id, data, source="form", kind="form", form_id=form_id, touch=touch,
                                  ref=f"submission:{sub.id}")
        sub.lead_id = lead.id
        x = L.ext_of(db, lead)
        L.activity(db, org_id, lead.id, "submission", f"Submitted form '{f.name}'", {"submission_id": str(sub.id)})
        if qn and qn.active:
            L.apply_profile(db, qn, answers, lead, enforce_required=False)
        assignees = [a for a in (meta.settings or {}).get("assignee_ids", [])]
        if not x.assignee_id and assignees:
            pick = assignees[meta.rr_index % len(assignees)]
            meta.rr_index += 1
            try:
                L.assign(db, lead, uuid.UUID(pick))
            except HTTPException:
                pass
        elif (meta.settings or {}).get("notify", True):
            notify(db, org_id, "lead_new", f"New {'lead' if created else 'submission'} from '{f.name}': {L.lead_label(db, lead, x)}",
                   None, f"/leads?lead={lead.id}", user_id=x.assignee_id)
        sch = f.schema or {}
        return _json({"ok": True, "message": sch.get("success_message") or "Thank you!", "redirect_url": sch.get("redirect_url")})


EMBED_JS = r"""(function(){
var s=document.currentScript;var base=s.src.split('/api/public/')[0];var token=__TOKEN__;
var el=document.getElementById(s.getAttribute('data-target'))||(function(){var d=document.createElement('div');s.parentNode.insertBefore(d,s);return d;})();
function h(t,a,c){var e=document.createElement(t);for(var k in (a||{}))e.setAttribute(k,a[k]);if(c)e.textContent=c;return e;}
fetch(base+'/api/public/forms/'+token).then(function(r){return r.json();}).then(function(f){
 if(!f.schema){el.textContent='This form is unavailable.';return;}
 var form=h('form',{'class':'gq-form'});var q=new URLSearchParams(location.search);
 f.schema.fields.forEach(function(fd){
  if(fd.type==='hidden'){var i=h('input',{type:'hidden',name:fd.key});i.value=q.get(fd.key)||'';form.appendChild(i);return;}
  var w=h('label',{style:'display:block;margin:0 0 12px'});w.appendChild(h('span',{style:'display:block;font-weight:600;margin-bottom:4px'},fd.label+(fd.required?' *':'')));
  var inp;
  if(fd.type==='textarea'){inp=h('textarea',{name:fd.key,rows:'4'});}
  else if(fd.type==='select'){inp=h('select',{name:fd.key});inp.appendChild(h('option',{value:''},'Choose…'));fd.options.forEach(function(o){inp.appendChild(h('option',{value:o},o));});}
  else if(fd.type==='checkbox'){inp=h('input',{type:'checkbox',name:fd.key,value:'true'});}
  else{inp=h('input',{type:fd.type==='phone'?'tel':(fd.type==='email'?'email':'text'),name:fd.key,placeholder:fd.placeholder||''});}
  if(fd.required)inp.required=true;if(fd.type!=='checkbox')inp.style.cssText='width:100%;padding:8px;border:1px solid #ccc;border-radius:6px;box-sizing:border-box';
  w.appendChild(inp);form.appendChild(w);});
 if(f.honeypot_field){var hp=h('input',{type:'text',name:f.honeypot_field,tabindex:'-1',autocomplete:'off','aria-hidden':'true'});hp.style.cssText='position:absolute;left:-9999px';form.appendChild(hp);}
 var btn=h('button',{type:'submit'},f.schema.submit_label||'Submit');btn.style.cssText='padding:10px 16px;border:0;border-radius:6px;color:#fff;background:'+((f.brand&&f.brand.color)||'#2f5bea');form.appendChild(btn);
 var msg=h('div',{role:'status'});form.appendChild(msg);
 form.addEventListener('submit',function(ev){ev.preventDefault();var d={};new FormData(form).forEach(function(v,k){d[k]=v;});
  d._gq_t=f.render_token;d._gq_referrer=document.referrer;d._gq_page=location.href;btn.disabled=true;
  fetch(base+'/api/public/forms/'+token+'/submit',{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify(d)})
  .then(function(r){return r.json();}).then(function(r){btn.disabled=false;
   if(r.ok){if(r.redirect_url){location.href=r.redirect_url;return;}form.innerHTML='';form.appendChild(h('p',{},r.message));}
   else{msg.textContent=r.message+(r.errors?' '+Object.keys(r.errors).map(function(k){return k+': '+r.errors[k];}).join('; '):'');}})
  .catch(function(){btn.disabled=false;msg.textContent='Network error — please try again.';});});
 el.innerHTML='';el.appendChild(form);});
})();"""


@router.get("/forms/{token}/embed.js")
def embed_js(token: str):
    _form_org(token)
    return Response(EMBED_JS.replace("__TOKEN__", json.dumps(token)), media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"})


# ================================================================== inbound connectors (module 65)
def _connector_org(token: str):
    if not token or len(token) > 64:
        raise HTTPException(404, "Unknown endpoint")
    with system_session() as sdb:
        row = sdb.execute(select(g.GrowthConnector.organisation_id, g.GrowthConnector.id).where(g.GrowthConnector.token == token)).first()
    if not row:
        raise HTTPException(404, "Unknown endpoint")
    return row.organisation_id, row.id


@router.get("/hooks/{token}")
def hook_verify(token: str, request: Request):
    """Facebook webhook subscription handshake (hub.challenge)."""
    org_id, cid = _connector_org(token)
    p = request.query_params
    with tenant_session(org_id) as db:
        c = db.get(g.GrowthConnector, cid)
        if p.get("hub.mode") == "subscribe" and c.verify_token and p.get("hub.verify_token") == c.verify_token:
            return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403, "Verification failed")


def _fetch_fb_leads(db, org_id, ids: list[str]) -> list[dict]:
    tok = oauth_access_token(db, org_id, "meta")  # NotConnected when Meta isn't connected
    out = []
    for lid in ids:
        if not lid.isdigit():
            continue
        r = http_call("GET", f"https://graph.facebook.com/v19.0/{lid}", params={
            "fields": "field_data,created_time,campaign_name,ad_name,form_id", "access_token": tok}, retries=1)
        if r.status_code >= 400:
            raise IntegrationError(f"Meta Graph API HTTP {r.status_code}: {r.text[:200]}")
        out.append(r.json())
    return out


@router.post("/hooks/{token}")
async def hook(token: str, request: Request):
    org_id, cid = _connector_org(token)
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise HTTPException(413, "Payload too large")
    ip = client_ip(request)
    with tenant_session(org_id) as rdb:
        rate_limit(rdb, org_id, f"hook:{cid}", ip, 300, 60)
    with tenant_session(org_id) as db:
        c = db.get(g.GrowthConnector, cid)
        if not c.enabled:
            db.add(g.GrowthConnectorDelivery(organisation_id=org_id, connector_id=cid, status="rejected", http_status=410, error="Connector disabled"))
            return _json({"ok": False, "message": "Connector disabled"}, 410)
        secret = vault.get(org_id, c.secret_ref)
        ok = F.verify_signature(c.preset, secret, dict(request.headers), raw)
        c.last_delivery_at = utcnow()
        if not ok:
            db.add(g.GrowthConnectorDelivery(organisation_id=org_id, connector_id=cid, status="rejected", signature_ok=False, http_status=401,
                                             error="Invalid or missing signature", payload={"_bytes": len(raw)}))
            return _json({"ok": False, "message": "Invalid signature"}, 401)
        try:
            payload = F.parse_body(raw, request.headers.get("content-type"))
        except HTTPException as e:
            db.add(g.GrowthConnectorDelivery(organisation_id=org_id, connector_id=cid, status="error", signature_ok=True, http_status=400,
                                             error=str(e.detail)))
            return _json({"ok": False, "message": e.detail}, 400)
        flats: list[dict] = []
        if c.preset == "facebook_lead_ads":
            ids = F.facebook_lead_ids(payload)
            try:
                flats = [F.facebook_flat(x) for x in _fetch_fb_leads(db, org_id, ids)]
            except NotConnected as e:
                db.add(g.GrowthConnectorDelivery(organisation_id=org_id, connector_id=cid, status="not_connected", signature_ok=True,
                                                 http_status=202, payload=payload,
                                                 error=f"Meta (Facebook/Instagram) is NOT CONNECTED — {len(ids)} lead ID(s) received but their fields "
                                                       f"can only be fetched through the Graph API. ({e})"))
                return _json({"ok": True, "accepted": 0, "message": "Received; Meta NOT CONNECTED so leads could not be resolved"}, 202)
            except IntegrationError as e:
                db.add(g.GrowthConnectorDelivery(organisation_id=org_id, connector_id=cid, status="error", signature_ok=True, http_status=502,
                                                 payload=payload, error=str(e)[:500]))
                return _json({"ok": False, "message": "Upstream error"}, 502)
        else:
            flats = [F.flatten(c.preset, payload)]
        accepted = []
        for flat in flats:
            data, touch = F.apply_mapping(c.mapping, flat)
            if c.preset == "facebook_lead_ads":
                touch.setdefault("utm_source", "facebook")
                touch.setdefault("utm_medium", "paid_social")
            if not touch.get("utm_source") and not touch.get("referrer") and not touch.get("page_url"):
                touch["utm_source"], touch["utm_medium"] = c.preset.replace("_", "-"), "webhook"
            try:
                with db.begin_nested():
                    lead, created = L.capture(db, org_id, c.project_id, data, source=f"webhook:{c.preset}", kind="webhook",
                                              form_id=c.form_id, touch=touch, ref=f"connector:{c.id}")
                    if c.form_id:
                        db.add(m.FormSubmission(organisation_id=org_id, form_id=c.form_id, payload={"fields": flat, "connector": c.name},
                                                lead_id=lead.id))
                db.add(g.GrowthConnectorDelivery(organisation_id=org_id, connector_id=cid, status="accepted", signature_ok=True, http_status=200,
                                                 payload=payload, mapped={"lead": data, "touch": touch}, lead_id=lead.id))
                if created:
                    notify(db, org_id, "lead_new", f"New lead via {c.name}: {L.lead_label(db, lead)}", None, f"/leads?lead={lead.id}")
                accepted.append(str(lead.id))
            except HTTPException as e:
                db.add(g.GrowthConnectorDelivery(organisation_id=org_id, connector_id=cid, status="error", signature_ok=True, http_status=422,
                                                 payload=payload, mapped={"lead": data}, error=str(e.detail)))
                return _json({"ok": False, "message": e.detail}, 422)
        return _json({"ok": True, "accepted": len(accepted), "lead_ids": accepted})


# ================================================================== Calendly webhook (module 44)
@router.post("/calendly/{token}")
async def calendly_hook(token: str, request: Request):
    if not token or len(token) > 64:
        raise HTTPException(404)
    with system_session() as sdb:
        org_id = sdb.scalar(select(g.GrowthCalendlySettings.organisation_id).where(g.GrowthCalendlySettings.webhook_token == token))
    if not org_id:
        raise HTTPException(404, "Unknown endpoint")
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise HTTPException(413)
    with tenant_session(org_id) as rdb:
        rate_limit(rdb, org_id, "calendly", client_ip(request), 300, 60)
    with tenant_session(org_id) as db:
        s = crm.cal_settings(db, org_id)
        verified = False
        if s.signing_ref:
            key = vault.get(org_id, s.signing_ref)
            if not crm.verify_calendly(key, request.headers.get("calendly-webhook-signature"), raw):
                db.add(g.GrowthSyncLog(organisation_id=org_id, provider="calendly", direction="webhook", status="error", error="Invalid signature"))
                return _json({"ok": False, "message": "Invalid signature"}, 401)
            verified = True
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(400, "Body must be JSON")
        ev = body.get("event")
        if ev not in ("invitee.created", "invitee.canceled"):
            return _json({"ok": True, "ignored": ev})
        b = crm.record_booking(db, org_id, s, ev, body.get("payload") or {}, verified=verified)
        db.add(g.GrowthSyncLog(organisation_id=org_id, provider="calendly", direction="webhook", status="ok",
                               counts={"event": ev, "matched": bool(b.lead_id), "verified": verified}))
        return _json({"ok": True, "booking_id": str(b.id), "lead_id": str(b.lead_id) if b.lead_id else None, "signature_verified": verified})


# ================================================================== Stripe webhook (module 76)
@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    raw = await request.body()
    if len(raw) > 512 * 1024:
        raise HTTPException(413)
    try:
        evt = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid payload")
    obj = ((evt.get("data") or {}).get("object") or {})
    org_raw = (obj.get("metadata") or {}).get("org_id")
    try:
        org_id = uuid.UUID(str(org_raw))
    except ValueError:
        raise HTTPException(400, "Missing organisation metadata")
    with system_session() as sdb:
        if not sdb.get(m.Organisation, org_id):
            raise HTTPException(400, "Unknown organisation")
    with tenant_session(org_id) as db:
        secret = M.stripe_webhook_secret(db, org_id)
        if not secret:
            raise HTTPException(503, detail={"code": "not_connected", "message": "Stripe webhook secret NOT CONFIGURED — event rejected"})
        if not M.verify_stripe_signature(secret, request.headers.get("stripe-signature", ""), raw):
            raise HTTPException(400, "Invalid Stripe signature")
        if evt.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            return M.settle_topup(db, org_id, obj)
        if evt.get("type") == "checkout.session.expired":
            t = db.scalar(select(g.GrowthTopup).where(g.GrowthTopup.stripe_session_id == obj.get("id")))
            if t and t.status == "created":
                t.status = "expired"
        return {"ok": True}


# ================================================================== free tools (67–72)
TOOL_LIMITS = {"domain-age": 20, "broken-links": 5, "link-analyzer": 10, "dofollow": 20, "website-finder": 20}


def _capture_tool_lead(db, org_id, tool: str, inp: str, email: str, body: dict):
    db.add(m.FreeToolLead(organisation_id=org_id, tool=tool, input=inp[:500], email=email.strip()[:254]))
    pub = db.scalar(select(g.GrowthOrgPublic))
    if pub and pub.project_id:
        touch = {k: body.get(k) for k in ("utm_source", "utm_medium", "utm_campaign", "referrer", "page_url") if body.get(k)}
        if not touch.get("utm_source"):
            touch.update(utm_source="free_tool", utm_medium=tool)
        lead, _ = L.capture(db, org_id, pub.project_id, {"email": email}, source=f"free_tool:{tool}", kind="free_tool", touch=touch,
                            ref=f"{tool}:{inp[:100]}")
        L.activity(db, org_id, lead.id, "free_tool", f"Used free tool {T.TOOLS[tool]} ({inp[:80]})")


def run_tool(tool: str, body: dict, public: bool, viewer_org=None) -> dict:
    if tool == "domain-age":
        return T.domain_age(str(body.get("domain") or body.get("input") or ""))
    if tool == "broken-links":
        return T.broken_links(str(body.get("url") or body.get("input") or ""), max_pages=5 if public else min(200, int(body.get("max_pages") or 25)),
                              check_limit=40 if public else 300)
    if tool == "link-analyzer":
        return T.link_analyzer(str(body.get("url") or body.get("input") or ""), check_limit=25 if public else 100)
    if tool == "dofollow":
        return T.dofollow(str(body.get("url") or body.get("input") or ""))
    if tool == "website-finder":
        kw = str(body.get("keyword") or body.get("input") or "")
        queries = T.footprint_queries(kw, body.get("extra") if isinstance(body.get("extra"), list) else None)
        listings = M.browse(viewer_org, q=kw.split()[0] if kw.split() else kw, page_size=20)["items"] if kw.strip() else []
        return {"keyword": kw, "queries": queries, "marketplace": listings,
                "serp": {"connected": False, "message": "Automated search-result pulls are NOT CONNECTED (no search API configured). "
                                                        "Open the queries above in a search engine to find sites."}}
    raise HTTPException(404, "Unknown tool")


@router.get("/tools/info")
def tools_info(key: str | None = None):
    org_id = resolve_public_key(key)
    with tenant_session(org_id) as db:
        org = db.get(m.Organisation, org_id)
        return _json({"brand": (org.branding or {}).get("company_name") or org.name, "tools": T.TOOLS, "requires_email": True})


@router.post("/tools/{tool}")
async def public_tool(tool: str, request: Request, key: str | None = None):
    if tool not in TOOL_LIMITS:
        raise HTTPException(404, "Unknown tool")
    org_id = resolve_public_key(key)
    body = F.parse_body(await request.body(), request.headers.get("content-type"))
    email = str(body.get("email") or "")
    if not valid_email(email):
        return _json({"ok": False, "code": "email_required", "message": "Enter your email to use this free tool."}, 400)
    with tenant_session(org_id) as rdb:
        rate_limit(rdb, org_id, f"tool:{tool}", client_ip(request), TOOL_LIMITS[tool], 3600)
    inp = str(body.get("domain") or body.get("url") or body.get("keyword") or body.get("input") or "")
    try:
        result = run_tool(tool, body, public=True, viewer_org=None)
        status, err = "COMPLETED", None
    except T.ToolError as e:
        result, status, err = {}, "FAILED", str(e)
    with tenant_session(org_id) as db:
        _capture_tool_lead(db, org_id, tool, inp, email, body)
        db.add(g.GrowthToolRun(organisation_id=org_id, tool=tool, input={"input": inp, "email_hash": ip_hash(email.lower())}, status=status,
                               result=result if tool != "website-finder" else {"queries": len(result.get("queries", []))},
                               error=err, public=True, finished_at=utcnow()))
    if err:
        return _json({"ok": False, "code": "tool_error", "message": err}, 422)
    return _json({"ok": True, "result": json.loads(json.dumps(result, default=str))})


# ================================================================== public backlink monitor (70)
FREE_MONITORS_PER_EMAIL = 3


def _monitor_public(mon: g.GrowthPublicMonitor) -> dict:
    return {"source_url": mon.source_url, "target_url": mon.target_url, "status": mon.status, "last_status": mon.last_status,
            "last_rel": mon.last_rel, "last_checked_at": mon.last_checked_at, "next_check_at": mon.next_check_at,
            "history": mon.history[:30], "email": mon.email[:2] + "***" + mon.email[mon.email.find("@"):],
            "email_alerts": "enabled" if os.environ.get("SMTP_HOST") else "NOT CONNECTED (no email provider configured) — check this page for status"}


@router.post("/monitors")
async def public_monitor(request: Request, key: str | None = None):
    org_id = resolve_public_key(key)
    body = F.parse_body(await request.body(), request.headers.get("content-type"))
    email = str(body.get("email") or "")
    if not valid_email(email):
        return _json({"ok": False, "message": "Enter a valid email."}, 400)
    try:
        src, tgt = T.clean_url(str(body.get("source_url") or "")), T.clean_url(str(body.get("target_url") or ""))
    except T.ToolError as e:
        return _json({"ok": False, "message": str(e)}, 400)
    with tenant_session(org_id) as rdb:
        rate_limit(rdb, org_id, "monitor:create", client_ip(request), 5, 3600)
    from .handlers_growth import check_monitor, ensure_monitor_schedule
    with tenant_session(org_id) as db:
        n = db.scalar(select(func.count()).select_from(g.GrowthPublicMonitor).where(
            func.lower(g.GrowthPublicMonitor.email) == email.lower(), g.GrowthPublicMonitor.status == "active", g.GrowthPublicMonitor.public.is_(True)))
        if n >= FREE_MONITORS_PER_EMAIL:
            return _json({"ok": False, "code": "free_limit", "message": f"The free tier monitors up to {FREE_MONITORS_PER_EMAIL} backlinks per email."}, 402)
        mon = g.GrowthPublicMonitor(organisation_id=org_id, email=email.strip(), source_url=src, target_url=tgt, manage_token=new_token(24))
        db.add(mon)
        db.flush()
        _capture_tool_lead(db, org_id, "backlink-monitor", f"{src} -> {tgt}", email, body)
        ensure_monitor_schedule(db, org_id)
        check_monitor(db, mon)
        return _json({"ok": True, "manage_token": mon.manage_token, "monitor": json.loads(json.dumps(_monitor_public(mon), default=str))})


def _monitor_org(tok: str):
    if not tok or len(tok) > 64:
        raise HTTPException(404)
    with system_session() as sdb:
        row = sdb.execute(select(g.GrowthPublicMonitor.organisation_id, g.GrowthPublicMonitor.id).where(g.GrowthPublicMonitor.manage_token == tok)).first()
    if not row:
        raise HTTPException(404, "Monitor not found")
    return row.organisation_id, row.id


@router.get("/monitors/{tok}")
def public_monitor_status(tok: str):
    org_id, mid = _monitor_org(tok)
    with tenant_session(org_id) as db:
        return _json(json.loads(json.dumps(_monitor_public(db.get(g.GrowthPublicMonitor, mid)), default=str)))


@router.post("/monitors/{tok}/stop")
def public_monitor_stop(tok: str):
    org_id, mid = _monitor_org(tok)
    with tenant_session(org_id) as db:
        mon = db.get(g.GrowthPublicMonitor, mid)
        mon.status = "stopped"
        return _json({"ok": True})
