"""Phase 6 growth: services catalog, marketplace listings across orgs, cart/checkout with wallet escrow,
order lifecycle with delivery verification against fixture pages, wallet ledger math, manual top-up approval,
Stripe flows (mocked), payouts, invoices and tenant isolation."""
import hashlib
import hmac
import json
import threading
import time
import uuid

import httpx
import pytest

from app.db import system_session, tenant_session
from app import models as m
from app.services import growth_market
from app.worker import run_once
from tests.conftest import Org

TARGET = "https://acme-client.example/emergency-plumber"


def make_platform_admin(client, email=None):
    from app.admin import set_pa
    a = Org(client, email=email or f"pa-{uuid.uuid4().hex[:6]}@genranq-platform.io")
    with system_session() as db:
        slug = db.get(m.Organisation, a.org_id).slug
    set_pa(a.email, slug, True)
    a.tokens = client.post("/api/auth/login", json={"email": a.email, "password": a.password, "organisation": slug}).json()
    return a


@pytest.fixture
def admins(client):
    return make_platform_admin(client), make_platform_admin(client)


def fund(org, admin, amount):
    r = org.post("/wallet/adjustments", {"amount": amount, "reason": "Bank transfer received", "reference": "BT-1"})
    assert r.status_code == 200, r.text
    r = admin.post(f"/admin/growth/adjustments/{r.json()['id']}/decide", {"decision": "approve", "notes": "matched bank statement"})
    assert r.status_code == 200, r.text
    return r.json()


def ledger_ok(org_id):
    with tenant_session(uuid.UUID(str(org_id))) as db:
        led = growth_market.ledger(db, uuid.UUID(str(org_id)))
    assert led["consistent"], led
    assert led["balance"] >= 0
    return led


def listing_body(site_server, **kw):
    return {"domain": "127.0.0.1", "url": site_server + "/growth/article.html", "category": "Home & Garden", "lang": "en",
            "country": "US", "description": "Plumbing and home improvement blog", "rules": "No casino", "dr": 45, "da": 38,
            "as": 30, "cf": 20, "tf": 15, "spam": 2, "org_traffic": 12000, "ref_domains": 340, "guest_post_price": 100,
            "link_ins_price": 60, "link_type": "dofollow", "tat": 7, "sample_urls": [site_server + "/growth/article.html"], **kw}


# ------------------------------------------------------------------ services catalog
def test_services_catalog(org):
    svcs = org.get("/services").json()
    assert [s["name"] for s in svcs] == ["Link Building", "Guest Post", "Digital PR", "Niche Edits", "Content", "Brand Mentions", "SEO Reseller"]
    lb = svcs[0]
    assert lb["orderable"] and lb["tiers"] and lb["deliverables"] and lb["tat_days"] and "most-popular" in lb["tags"]
    assert org.post(f"/services/{lb['id']}/cart", {"tier": "nope"}).status_code == 400
    assert org.post(f"/services/{lb['id']}/cart", {"tier": "starter", "addons": ["bogus"]}).status_code == 400
    r = org.post(f"/services/{lb['id']}/cart", {"tier": "starter", "addons": ["anchor_plan"]})
    assert r.status_code == 200 and r.json()["price"] == 600
    cart = org.get("/marketplace/cart").json()
    assert cart["total"] == 600 and cart["shortfall"] == 600
    r = org.post("/marketplace/checkout", {})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "insufficient_funds"
    assert org.get("/wallet").json()["balance"] == 0


# ------------------------------------------------------------------ wallet funding rules
def test_manual_topup_requires_platform_admin_approval(org, admins, client, monkeypatch):
    pa1, pa2 = admins
    w = org.get("/wallet").json()
    assert w["stripe_connected"] is False and "NOT CONNECTED" in w["stripe_message"]
    r = org.post("/wallet/topups", {"amount": 50})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_connected"
    staff = org.invite_user("manager")
    assert client.post("/api/wallet/adjustments", headers=staff, json={"amount": 100, "reason": "please"}).status_code == 403
    req = org.post("/wallet/adjustments", {"amount": 250, "reason": "Wire transfer 2026-10-01"}).json()
    assert req["status"] == "pending" and org.get("/wallet").json()["balance"] == 0  # nothing credited before approval
    assert org.post(f"/admin/growth/adjustments/{req['id']}/decide", {"decision": "approve"}).status_code == 403  # owner ≠ platform admin
    pend = pa1.get("/admin/growth/adjustments").json()
    assert any(a["id"] == req["id"] for a in pend)
    assert pa1.post(f"/admin/growth/adjustments/{req['id']}/decide", {"decision": "reject"}).status_code == 400  # needs notes
    assert pa1.post(f"/admin/growth/adjustments/{req['id']}/decide", {"decision": "approve", "notes": "ok"}).status_code == 200
    assert pa1.post(f"/admin/growth/adjustments/{req['id']}/decide", {"decision": "approve"}).status_code == 409  # once only
    w = org.get("/wallet").json()
    assert w["balance"] == 250 and w["transactions"][0]["type"] == "adjustment"
    assert any(i["kind"] == "adjustment" for i in w["invoices"])
    # platform-admin-initiated adjustment needs a *different* platform admin
    a = pa1.post("/admin/growth/adjustments", {"organisation_id": org.org_id, "amount": -50, "reason": "Correction of duplicate credit"}).json()
    assert pa1.post(f"/admin/growth/adjustments/{a['id']}/decide", {"decision": "approve"}).status_code == 403
    assert pa2.post(f"/admin/growth/adjustments/{a['id']}/decide", {"decision": "approve"}).status_code == 200
    assert org.get("/wallet").json()["balance"] == 200
    # a debit can never take the balance negative
    a = pa1.post("/admin/growth/adjustments", {"organisation_id": org.org_id, "amount": -500, "reason": "Too large correction"}).json()
    r = pa2.post(f"/admin/growth/adjustments/{a['id']}/decide", {"decision": "approve"})
    assert r.status_code == 402
    assert org.get("/wallet").json()["balance"] == 200
    ledger_ok(org.org_id)


def test_stripe_checkout_and_webhook_signature(org, monkeypatch):
    calls = []
    sid = "cs_test_" + uuid.uuid4().hex[:10]

    def fake(method, url, **kw):
        calls.append((method, url, kw.get("data")))
        return httpx.Response(200, json={"id": sid, "url": f"https://checkout.stripe.com/c/pay/{sid}"})
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(growth_market, "http_call", fake)
    r = org.post("/wallet/topups", {"amount": 75})
    assert r.status_code == 200 and r.json()["checkout_url"].startswith("https://checkout.stripe.com")
    assert calls[0][2]["line_items[0][price_data][unit_amount]"] == "7500"
    assert org.get("/wallet").json()["balance"] == 0  # nothing until Stripe confirms payment
    evt = {"type": "checkout.session.completed", "data": {"object": {
        "id": sid, "payment_status": "paid", "amount_total": 7500,
        "metadata": {"org_id": org.org_id, "topup_id": r.json()["id"]}}}}
    raw = json.dumps(evt).encode()
    ts = str(int(time.time()))
    sig = hmac.new(b"whsec_test", f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    bad = org.c.post("/api/public/stripe/webhook", content=raw, headers={"Stripe-Signature": f"t={ts},v1={'0' * 64}"})
    assert bad.status_code == 400
    assert org.get("/wallet").json()["balance"] == 0
    ok = org.c.post("/api/public/stripe/webhook", content=raw, headers={"Stripe-Signature": f"t={ts},v1={sig}"})
    assert ok.status_code == 200 and ok.json()["credited"] == 75
    dup = org.c.post("/api/public/stripe/webhook", content=raw, headers={"Stripe-Signature": f"t={ts},v1={sig}"})
    assert dup.json()["duplicate"] is True
    w = org.get("/wallet").json()
    assert w["balance"] == 75 and w["topups"][0]["status"] == "paid"
    # amount tampering is rejected even with a valid signature for that body
    evt["data"]["object"]["amount_total"] = 999999
    evt["data"]["object"]["id"] = "cs_other"
    raw2 = json.dumps(evt).encode()
    sig2 = hmac.new(b"whsec_test", f"{ts}.".encode() + raw2, hashlib.sha256).hexdigest()
    assert org.c.post("/api/public/stripe/webhook", content=raw2, headers={"Stripe-Signature": f"t={ts},v1={sig2}"}).status_code == 400
    assert org.get("/wallet").json()["balance"] == 75
    ledger_ok(org.org_id)


# ------------------------------------------------------------------ marketplace end to end
def test_marketplace_listing_browse_checkout_escrow_lifecycle(org_factory, admins, site_server, client, monkeypatch):
    pa1, _ = admins
    pub, adv, third = org_factory(), org_factory(), org_factory()
    # publisher lists a site
    r = pub.post("/marketplace/my-listings", listing_body(site_server))
    assert r.status_code == 200, r.text
    lst = r.json()
    assert lst["own"] and lst["metrics"]["dr"]["source"] == "self" and not lst["metrics"]["dr"]["verified"]
    other = pub.post("/marketplace/my-listings", listing_body(site_server, domain="cheap.example", dr=10, guest_post_price=20, category="Finance", country="GB", description="Personal finance tips")).json()
    assert pub.post("/marketplace/my-listings", listing_body(site_server)).status_code == 409
    assert pub.post("/marketplace/my-listings", listing_body(site_server, domain="x.example", link_type="weird")).status_code == 400
    # verify metrics: NOT CONNECTED → 409, then mocked Ahrefs → verified
    assert pub.post(f"/marketplace/my-listings/{lst['id']}/verify-metrics").status_code == 409

    # advertiser browses across orgs with filters / sort
    def browse_ids(qs):
        r = adv.get("/marketplace/listings?page_size=100&" + qs)
        assert r.status_code == 200, r.text
        return [i["id"] for i in r.json()["items"] if i["id"] in (lst["id"], other["id"])], r.json()
    ids, res = browse_ids("sort=price&order=asc&q=example")
    ids2, _ = browse_ids("sort=price&order=asc&q=127.0.0.1")
    assert other["id"] in ids and lst["id"] in ids2
    assert "Home & Garden" in res["facets"]["categories"]
    mine = next(i for i in adv.get("/marketplace/listings?page_size=100&q=127.0.0.1").json()["items"] if i["id"] == lst["id"])
    assert mine["own"] is False
    assert browse_ids("dr_min=40&country=US&spam_max=5&q=127.0.0.1")[0] == [lst["id"]]
    assert browse_ids("dr_min=40&q=cheap.example")[0] == []
    assert browse_ids("q=Plumbing and home")[0] == [lst["id"]]
    assert browse_ids("price_max=50&q=cheap.example")[0] == [other["id"]]
    assert browse_ids("verified_only=true")[0] == []
    asc = [i["guest_post_price"] for i in adv.get("/marketplace/listings?sort=price&order=asc&page_size=100").json()["items"]]
    assert asc == sorted(asc)
    assert adv.get("/marketplace/listings?sort=bogus").status_code == 400
    # advertiser cannot edit or delete the publisher's listing; publisher can
    assert adv.patch(f"/marketplace/my-listings/{lst['id']}", {"guest_post_price": 1}).status_code == 404
    assert adv.c.delete(f"/api/marketplace/my-listings/{lst['id']}", headers=adv.h).status_code == 404
    assert adv.get("/marketplace/my-listings").json()["items"] == []
    assert pub.patch(f"/marketplace/my-listings/{lst['id']}", {"guest_post_price": 100, "dr": 46}).json()["metrics"]["dr"]["value"] == 46
    # publisher cannot buy own listing
    assert pub.post("/marketplace/cart", {"items": [{"listing_id": lst["id"], "target_url": TARGET}]}).status_code == 400

    # cart (bulk) + insufficient funds
    proj = adv.project("Client site")
    assert adv.post("/sites", {"project_id": proj["id"], "domain": "acme-client.example"}).status_code == 200
    r = adv.post("/marketplace/cart", {"items": [
        {"listing_id": lst["id"], "kind": "guest_post", "target_url": TARGET, "anchor": "emergency plumbers", "project_id": proj["id"]},
        {"listing_id": other["id"], "kind": "link_insertion", "target_url": TARGET}]})
    assert r.status_code == 200 and r.json()["added"] == 2
    cart = adv.get("/marketplace/cart").json()
    assert cart["total"] == 160
    assert adv.post("/marketplace/checkout", {}).status_code == 402
    fund(adv, pa1, 200)
    staff = adv.invite_user("staff")
    assert client.post("/api/marketplace/checkout", headers=staff, json={}).status_code == 403
    r = adv.post("/marketplace/checkout", {})
    assert r.status_code == 200, r.text
    orders = r.json()["orders"]
    assert len(orders) == 2 and r.json()["wallet"]["balance"] == 40 and r.json()["wallet"]["escrow_held"] == 160
    assert adv.get("/marketplace/cart").json()["items"] == []
    oid = next(o["order_id"] for o in orders if o["amount"] == 100)
    oid2 = next(o["order_id"] for o in orders if o["amount"] == 60)
    ledger_ok(adv.org_id)

    # both sides see only their own side
    selling = pub.get("/marketplace/orders?side=selling").json()["items"]
    assert {o["id"] for o in selling} == {oid, oid2}
    po = next(o for o in selling if o["id"] == oid)
    assert po["amount"] == 90 and po["gross"] == 100 and "advertiser_org_id" not in po
    assert pub.get("/marketplace/orders?side=buying").json()["items"] == []
    assert third.get(f"/marketplace/orders/{oid}").status_code == 404
    assert third.post(f"/marketplace/orders/{oid}/action", {"action": "accept"}).status_code == 404
    assert third.get("/marketplace/orders?side=selling").json()["items"] == []

    # role rules on actions
    assert adv.post(f"/marketplace/orders/{oid}/action", {"action": "accept"}).status_code == 403
    assert pub.post(f"/marketplace/orders/{oid}/action", {"action": "complete"}).status_code == 403
    assert pub.post(f"/marketplace/orders/{oid}/action", {"action": "deliver", "live_url": site_server + "/growth/article.html"}).status_code == 409
    # brief + attachment, visible to publisher
    adv.post(f"/marketplace/orders/{oid}/brief", {"brief": "800 words on drain care"})
    up = adv.c.post(f"/api/marketplace/orders/{oid}/attachments", headers=adv.h, files={"file": ("brief.txt", b"Use UK spelling", "text/plain")})
    assert up.status_code == 200
    fid = up.json()["id"]
    po = pub.get(f"/marketplace/orders/{oid}").json()
    assert po["brief"] == "800 words on drain care" and po["attachments"][0]["id"] == fid
    att = pub.c.get(f"/api/marketplace/orders/{oid}/attachments/{fid}", headers=pub.h)
    assert att.status_code == 200 and att.content == b"Use UK spelling"
    assert third.c.get(f"/api/marketplace/orders/{oid}/attachments/{fid}", headers=third.h).status_code == 404

    # lifecycle: accept → start → deliver wrong page → verification fails → complete blocked
    assert pub.post(f"/marketplace/orders/{oid}/action", {"action": "accept"}).json()["status"] == "accepted"
    assert pub.post(f"/marketplace/orders/{oid}/action", {"action": "start"}).json()["status"] == "in_progress"
    r = pub.post(f"/marketplace/orders/{oid}/action", {"action": "deliver", "live_url": site_server + "/growth/nolink.html"})
    assert r.status_code == 200 and r.json()["status"] == "delivered"
    run_once()
    o = adv.get(f"/marketplace/orders/{oid}").json()
    assert o["verified"] is False and any("No link" in i for i in o["verification"]["issues"])
    assert adv.post(f"/marketplace/orders/{oid}/action", {"action": "complete"}).status_code == 409
    # nofollow when dofollow was promised → fails too
    pub.post(f"/marketplace/orders/{oid}/action", {"action": "deliver", "live_url": site_server + "/growth/nofollow.html"})
    run_once()
    o = adv.get(f"/marketplace/orders/{oid}").json()
    assert o["verified"] is False and o["verification"]["rel"] == "nofollow sponsored"
    # correct page → verified → complete → release minus fee
    pub.post(f"/marketplace/orders/{oid}/action", {"action": "deliver", "live_url": site_server + "/growth/article.html"})
    run_once()
    o = adv.get(f"/marketplace/orders/{oid}").json()
    assert o["verified"] is True and o["verification"]["anchor"] == "emergency plumbers in Springfield" and o["verification"]["dofollow"]
    pre = pub.get("/wallet").json()["balance"]
    r = adv.post(f"/marketplace/orders/{oid}/action", {"action": "complete"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert pub.get("/wallet").json()["balance"] == pre + 90
    assert adv.post(f"/marketplace/orders/{oid}/action", {"action": "complete"}).status_code == 409
    o = adv.get(f"/marketplace/orders/{oid}").json()
    assert o["escrow"] == "released" and o["backlink_id"]  # recorded in the Backlink CRM for the tracked site
    assert [e["action"] for e in o["timeline"]][:3] == ["placed", "brief_updated", "accept"]

    # second order: publisher rejects → full refund to advertiser + credit note
    assert pub.post(f"/marketplace/orders/{oid2}/action", {"action": "reject"}).status_code == 400  # reason required
    r = pub.post(f"/marketplace/orders/{oid2}/action", {"action": "reject", "note": "Topic not accepted"})
    assert r.json()["status"] == "rejected"
    w = adv.get("/wallet").json()
    assert w["balance"] == 100 and w["escrow_held"] == 0
    kinds = {i["kind"] for i in w["invoices"]}
    assert {"order", "credit_note", "adjustment"} <= kinds
    inv = next(i for i in w["invoices"] if i["kind"] == "order")
    pdf = adv.c.get(f"/api/wallet/invoices/{inv['id']}/pdf", headers=adv.h)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert third.c.get(f"/api/wallet/invoices/{inv['id']}/pdf", headers=third.h).status_code == 404
    ledger_ok(adv.org_id)
    ledger_ok(pub.org_id)

    # dispute → platform resolves with refund
    adv.post("/marketplace/cart", {"items": [{"listing_id": lst["id"], "target_url": TARGET}]})
    o3 = adv.post("/marketplace/checkout", {}).json()["orders"][0]["order_id"]
    pub.post(f"/marketplace/orders/{o3}/action", {"action": "accept"})
    assert adv.post(f"/marketplace/orders/{o3}/action", {"action": "dispute"}).status_code == 400
    assert adv.post(f"/marketplace/orders/{o3}/action", {"action": "dispute", "note": "No response for 3 weeks"}).json()["status"] == "disputed"
    assert any(x["id"] == o3 for x in pa1.get("/admin/growth/orders").json())
    assert adv.post(f"/admin/growth/orders/{o3}/action", {"action": "resolve_refund"}).status_code == 403
    assert pa1.post(f"/admin/growth/orders/{o3}/action", {"action": "resolve_refund", "note": "Publisher unresponsive"}).json()["status"] == "refunded"
    assert adv.get("/wallet").json()["balance"] == 100
    assert pub.get(f"/marketplace/orders/{o3}").json()["status"] == "refunded"
    # cancel before acceptance
    adv.post("/marketplace/cart", {"items": [{"listing_id": lst["id"], "target_url": TARGET}]})
    o4 = adv.post("/marketplace/checkout", {}).json()["orders"][0]["order_id"]
    assert adv.post(f"/marketplace/orders/{o4}/action", {"action": "cancel"}).json()["status"] == "refunded"
    assert adv.get("/wallet").json()["balance"] == 100
    ledger_ok(adv.org_id)
    ledger_ok(pub.org_id)

    # report + block a publisher hides its listings and prevents purchase
    assert adv.post(f"/marketplace/listings/{lst['id']}/report", {"reason": "Link removed after 30 days"}).status_code == 200
    assert any(r["listing_id"] == lst["id"] for r in pa1.get("/admin/growth/reports").json())
    assert adv.post(f"/marketplace/publishers/{lst['publisher_id']}/block").status_code == 200
    assert browse_ids("q=127.0.0.1")[0] == []
    assert adv.get(f"/marketplace/listings/{lst['id']}").json()["blocked"] is True
    adv.c.delete(f"/api/marketplace/publishers/{lst['publisher_id']}/block", headers=adv.h)
    assert browse_ids("q=127.0.0.1")[0] == [lst["id"]]
    # paused listing disappears from browse; deleting a listing with orders pauses it
    r = pub.c.delete(f"/api/marketplace/my-listings/{lst['id']}", headers=pub.h).json()
    assert r["paused"] is True
    assert browse_ids("q=127.0.0.1")[0] == []


def test_verify_metrics_with_mocked_ahrefs(org, site_server, monkeypatch):
    lst = org.post("/marketplace/my-listings", listing_body(site_server, domain="verify.example")).json()
    from tests.test_growth_leads import _connect
    _connect(org, "ahrefs", "ahrefs-key")
    monkeypatch.setattr(growth_market, "http_call", lambda *a, **k: httpx.Response(200, json={"domain_rating": {"domain_rating": 61}}))
    r = org.post(f"/marketplace/my-listings/{lst['id']}/verify-metrics")
    assert r.status_code == 200, r.text
    d = r.json()["listing"]["metrics"]["dr"]
    assert d["value"] == 61 and d["verified"] and d["source"] == "ahrefs"
    # editing the metric makes it self-reported again
    d = org.patch(f"/marketplace/my-listings/{lst['id']}", {"dr": 70}).json()["metrics"]["dr"]
    assert d["source"] == "self" and not d["verified"]


def test_concurrent_checkout_never_overdraws(org_factory, admins, site_server):
    pa1, _ = admins
    pub, adv = org_factory(), org_factory()
    lst = pub.post("/marketplace/my-listings", listing_body(site_server, domain="race.example", guest_post_price=80)).json()
    fund(adv, pa1, 100)
    from tests.conftest import Org as _O  # noqa
    # two different users of the advertiser org each have one 80-item cart and check out at the same time
    mgr = adv.invite_user("manager")
    adv.post("/marketplace/cart", {"items": [{"listing_id": lst["id"], "target_url": TARGET}]})
    assert adv.c.post("/api/marketplace/cart", headers=mgr, json={"items": [{"listing_id": lst["id"], "target_url": TARGET}]}).status_code == 200
    results = []

    def go(h):
        results.append(adv.c.post("/api/marketplace/checkout", headers=h, json={}).status_code)
    ts = [threading.Thread(target=go, args=(h,)) for h in (adv.h, mgr)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [200, 402]
    led = ledger_ok(adv.org_id)
    assert led["balance"] == 20 and led["escrow_held"] == 80


def test_payouts_need_owner_approval_and_transfer_not_connected(org_factory, admins, client):
    pa1, _ = admins
    org = org_factory()
    fund(org, pa1, 500)
    p = org.post("/wallet/payouts", {"amount": 200, "destination": "IBAN ending 4421"}).json()
    assert p["status"] == "pending_approval"
    assert org.get("/wallet").json()["balance"] == 300  # held while pending
    assert org.post(f"/wallet/payouts/{p['id']}/decide", {"decision": "approve"}).status_code == 403  # requester cannot approve
    mgr = org.invite_user("manager")
    assert client.post(f"/api/wallet/payouts/{p['id']}/decide", headers=mgr, json={"decision": "approve"}).status_code == 403
    owner2 = org.invite_user("owner")
    r = client.post(f"/api/wallet/payouts/{p['id']}/decide", headers=owner2, json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and "NOT CONNECTED" in r.json()["transfer"]
    assert org.post(f"/admin/growth/payouts/{p['id']}/mark-paid", {"reference": "WISE-1"}).status_code == 403
    assert pa1.post(f"/admin/growth/payouts/{p['id']}/mark-paid", {"reference": "WISE-123"}).json()["status"] == "paid"
    # rejection via the generic Approvals screen releases the hold
    p2 = org.post("/wallet/payouts", {"amount": 100, "destination": "PayPal"}).json()
    assert client.post(f"/api/approvals/{p2['approval_id']}/decide", headers=owner2, json={"decision": "reject", "notes": "wrong account"}).status_code == 200
    w = org.get("/wallet").json()
    assert next(x for x in w["payouts"] if x["id"] == p2["id"])["status"] == "rejected"
    assert w["balance"] == 300
    assert org.post("/wallet/payouts", {"amount": 10_000, "destination": "Bank account"}).status_code == 402
    ledger_ok(org.org_id)


def test_wallet_isolation_and_ledger_invariant(org_factory, admins):
    pa1, _ = admins
    a, b = org_factory(), org_factory()
    fund(a, pa1, 120)
    assert b.get("/wallet").json()["balance"] == 0 and b.get("/wallet").json()["transactions"] == []
    adj = a.get("/wallet").json()["adjustments"][0]
    assert b.post(f"/admin/growth/adjustments/{adj['id']}/decide", {"decision": "approve"}).status_code == 403
    csv = a.c.get("/api/wallet/export.csv", headers=a.h)
    assert csv.status_code == 200 and "adjustment" in csv.text
    # direct ledger math: balance == sum(txns), never negative
    with tenant_session(uuid.UUID(a.org_id)) as db:
        for amt in (10, -30, 5.55, -0.55):
            growth_market.post(db, uuid.UUID(a.org_id), "adjustment", amt, "test")
        with pytest.raises(Exception):
            growth_market.post(db, uuid.UUID(a.org_id), "charge", -10_000, "too much")
    led = ledger_ok(a.org_id)
    assert led["balance"] == 105
