"""Backlink marketplace (14/74/75), services orders (73) and wallet (76).

Money rules
* Every balance change is a wallet_txn row written under a row lock on the wallet (SELECT … FOR UPDATE),
  so ``wallet.balance == sum(wallet_txn.amount)`` always holds and the balance can never go negative —
  concurrent checkouts serialise on the lock and the second one sees the reduced balance.
* Placing an order moves the price from the advertiser's balance into escrow ("charge"). Completion
  releases it to the publisher minus the platform fee ("earning"); rejection/cancel/refund returns it ("refund").
* Funds enter a wallet only via a paid Stripe Checkout session (verified webhook) or a manual adjustment
  approved by a platform administrator. Payouts need an owner's approval; the transfer itself is
  NOT CONNECTED (no payout rail) and is marked paid by the operator with a reference.
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import timedelta
from decimal import Decimal

from bs4 import BeautifulSoup
from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .. import models as m
from .. import models_growth as g
from ..db import system_session
from . import growth_catalog
from .crawler import CrawlBlocked, Crawler, normalise
from .growth_common import as_org, domain_of, money, utcnow
from .integrations import NotConnected, credentials, http_call

ZERO = Decimal("0.00")


def fee_pct() -> Decimal:
    try:
        v = Decimal(os.environ.get("MARKETPLACE_FEE_PCT", "10"))
    except Exception:  # noqa: BLE001
        v = Decimal("10")
    return max(ZERO, min(Decimal("50"), v))


# ================================================================== wallet ledger
def lock_wallet(db: Session, org_id) -> m.Wallet:
    db.execute(insert(m.Wallet).values(organisation_id=org_id, balance=0).on_conflict_do_nothing(index_elements=["organisation_id"]))
    return db.scalar(select(m.Wallet).where(m.Wallet.organisation_id == org_id).with_for_update()
                     .execution_options(populate_existing=True))


def post(db: Session, org_id, type_: str, amount, reason: str, *, order_id=None, ref: str | None = None, user_id=None) -> m.WalletTxn:
    w = lock_wallet(db, org_id)
    amt = money(amount)
    if amt == ZERO:
        raise HTTPException(400, "Amount must not be zero")
    new = money(w.balance) + amt
    if new < ZERO:
        raise HTTPException(402, detail={"code": "insufficient_funds", "balance": float(w.balance), "required": float(-amt),
                                         "message": f"Insufficient wallet balance ({money(w.balance)} available, {-amt} needed)"})
    w.balance = new
    t = m.WalletTxn(organisation_id=org_id, wallet_id=w.id, type=type_, amount=amt)
    db.add(t)
    db.flush()
    db.add(g.GrowthWalletTxnMeta(organisation_id=org_id, txn_id=t.id, reason=reason[:500], order_id=order_id, ref=ref,
                                 balance_after=new, created_by=user_id))
    db.flush()
    return t


def ledger(db: Session, org_id) -> dict:
    w = db.scalar(select(m.Wallet).where(m.Wallet.organisation_id == org_id))
    total = db.scalar(select(func.coalesce(func.sum(m.WalletTxn.amount), 0)).where(m.WalletTxn.organisation_id == org_id))
    held = db.scalar(select(func.coalesce(func.sum(g.GrowthEscrow.amount), 0)).where(g.GrowthEscrow.status == "held"))
    pending_payouts = db.scalar(select(func.coalesce(func.sum(g.GrowthPayout.amount), 0))
                                .where(g.GrowthPayout.status.in_(["pending_approval", "approved"])))
    bal = money(w.balance if w else 0)
    return {"balance": float(bal), "ledger_sum": float(money(total)), "consistent": bal == money(total),
            "escrow_held": float(money(held)), "payouts_pending": float(money(pending_payouts)), "currency": "usd",
            "fee_pct": float(fee_pct())}


def txn_rows(db: Session, limit=200, offset=0) -> list[dict]:
    q = (select(m.WalletTxn, g.GrowthWalletTxnMeta).outerjoin(g.GrowthWalletTxnMeta, g.GrowthWalletTxnMeta.txn_id == m.WalletTxn.id)
         .order_by(m.WalletTxn.created_at.desc(), m.WalletTxn.id).limit(limit).offset(offset))
    return [{"id": str(t.id), "type": t.type, "amount": float(t.amount), "created_at": t.created_at,
             "reason": x.reason if x else None, "order_id": str(x.order_id) if x and x.order_id else None,
             "ref": x.ref if x else None, "balance_after": float(x.balance_after) if x else None} for t, x in db.execute(q)]


# ================================================================== invoices
def next_invoice_number(db: Session, prefix: str = "INV") -> str:
    n = db.scalar(select(func.count()).select_from(g.GrowthInvoice)) or 0
    return f"{prefix}-{utcnow():%Y}-{n + 1:05d}"


def create_invoice(db: Session, org_id, kind: str, lines: list[dict], ref_type: str, ref_id) -> g.GrowthInvoice:
    lock_wallet(db, org_id)  # serialises numbering per organisation
    total = sum((money(l["amount"]) for l in lines), ZERO)
    inv = g.GrowthInvoice(organisation_id=org_id, number=next_invoice_number(db, "CN" if kind == "credit_note" else "INV"), kind=kind,
                          amount=total, lines=[{**l, "amount": float(money(l["amount"]))} for l in lines], ref_type=ref_type, ref_id=ref_id)
    db.add(inv)
    db.flush()
    return inv


def invoice_pdf(db: Session, inv: g.GrowthInvoice) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    org = db.get(m.Organisation, inv.organisation_id)
    b = org.branding or {}
    primary = colors.HexColor(b.get("primary_color") or "#1F3A5F")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"{'Credit note' if inv.kind == 'credit_note' else 'Invoice'} {inv.number}", author="Genranq")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=primary, alignment=0, fontSize=20)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)

    def esc(s):
        return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    title = {"topup": "Receipt — wallet top-up", "order": "Invoice", "credit_note": "Credit note", "payout": "Payout statement",
             "adjustment": "Wallet adjustment"}.get(inv.kind, "Invoice")
    st = [Paragraph("Genranq", ParagraphStyle("brand", parent=body, textColor=primary, fontSize=11)),
          Paragraph(f"{title} {esc(inv.number)}", h1),
          Paragraph(f"Billed to: <b>{esc(b.get('company_name') or org.name)}</b><br/>Date: {inv.created_at:%d %b %Y}<br/>"
                    f"Currency: {inv.currency.upper()}", body), Spacer(1, 6 * mm)]
    data = [["Description", "Qty", "Amount"]]
    for l in inv.lines:
        data.append([Paragraph(esc(l.get("description")), body), str(l.get("qty", 1)), f"{l['amount']:,.2f}"])
    data.append(["", "Total", f"{float(inv.amount):,.2f}"])
    t = Table(data, colWidths=[120 * mm, 18 * mm, 32 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9), ("LINEBELOW", (0, 0), (-1, 0), 0.6, primary),
                           ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
                           ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.HexColor("#E5E7EB")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    st += [t, Spacer(1, 8 * mm),
           Paragraph("Paid from your Genranq wallet balance." if inv.kind == "order" else
                     "Funds credited to your Genranq wallet." if inv.kind in ("topup", "adjustment", "credit_note") else "", body)]
    doc.build(st)
    return buf.getvalue()


# ================================================================== listings
LISTING_METRICS = ["da", "dr", "as_", "cf", "tf", "spam", "org_traffic", "ref_domains"]
LINK_TYPES = ["dofollow", "nofollow", "sponsored", "ugc"]
SORTS = {"price": "price", "dr": m.MarketplaceListing.dr, "da": m.MarketplaceListing.da, "as": m.MarketplaceListing.as_,
         "traffic": m.MarketplaceListing.org_traffic, "spam": m.MarketplaceListing.spam, "tat": m.MarketplaceListing.tat,
         "ref_domains": m.MarketplaceListing.ref_domains, "domain": m.MarketplaceListing.domain}


def publisher_label(org_id) -> str:
    return f"Publisher {str(org_id)[:6].upper()}" if org_id else "Genranq"


def listing_dict(l: m.MarketplaceListing, x: g.GrowthListingExt | None, viewer_org=None, full: bool = False) -> dict:
    src = (x.metrics_source if x else {}) or {}
    metrics = {}
    for k in LISTING_METRICS:
        v = getattr(l, k)
        name = "as" if k == "as_" else k
        s = src.get(name) or {}
        metrics[name] = {"value": float(v) if v is not None else None,
                         "source": s.get("source", "self") if v is not None else None,
                         "verified": s.get("source") in ("ahrefs", "semrush"), "verified_at": s.get("verified_at")}
    d = {"id": str(l.id), "domain": l.domain, "category": l.category, "lang": l.lang, "country": x.country if x else None,
         "url": x.url if x else None, "description": x.description if x else None, "rules": x.rules if x else None,
         "sample_urls": x.sample_urls if x else [], "status": x.status if x else "active",
         "guest_post_price": float(l.guest_post_price) if l.guest_post_price is not None else None,
         "link_ins_price": float(l.link_ins_price) if l.link_ins_price is not None else None,
         "link_type": l.link_type, "link_validity": l.link_validity, "tat": l.tat, "metrics": metrics,
         "verified_any": any(v["verified"] for v in metrics.values()),
         "publisher_id": str(l.publisher_org_id) if l.publisher_org_id else None, "publisher": publisher_label(l.publisher_org_id),
         "own": bool(viewer_org and l.publisher_org_id == viewer_org)}
    if full:
        d["updated_at"] = x.updated_at if x else None
    return d


def browse(viewer_org, *, q=None, category=None, country=None, lang=None, link_type=None, price_type="guest_post",
           filters: dict | None = None, verified_only=False, sort="dr", order="desc", page=1, page_size=25,
           blocked: list | None = None, include_own=True, ids: list | None = None) -> dict:
    """Cross-organisation catalogue read. Marketplace listings are public inventory by design, so the browse
    query reads listing + publisher-declared extension columns through a read-only system session and
    returns only public listing attributes (never other tables of the publisher)."""
    L, X = m.MarketplaceListing, g.GrowthListingExt
    price_col = L.guest_post_price if price_type == "guest_post" else L.link_ins_price
    cond = [func.coalesce(X.status, "active") == "active", price_col.isnot(None)]
    if q:
        like = f"%{q.strip()[:100]}%"
        cond.append(or_(L.domain.ilike(like), L.category.ilike(like), X.description.ilike(like)))
    if category:
        cond.append(L.category.ilike(category.strip()))
    if country:
        cond.append(X.country.ilike(country.strip()))
    if lang:
        cond.append(L.lang.ilike(lang.strip()))
    if link_type:
        cond.append(L.link_type == link_type)
    rng = {"da": L.da, "dr": L.dr, "as": L.as_, "cf": L.cf, "tf": L.tf, "spam": L.spam, "traffic": L.org_traffic,
           "ref_domains": L.ref_domains, "tat": L.tat, "price": price_col}
    for k, v in (filters or {}).items():
        if v is None:
            continue
        name, _, bound = k.rpartition("_")
        col = rng.get(name)
        if col is None or bound not in ("min", "max"):
            continue
        cond.append(col >= v if bound == "min" else col <= v)
    if blocked:
        cond.append(or_(L.publisher_org_id.is_(None), L.publisher_org_id.notin_(blocked)))
    if not include_own and viewer_org:
        cond.append(or_(L.publisher_org_id.is_(None), L.publisher_org_id != viewer_org))
    if ids is not None:
        cond.append(L.id.in_(ids))
    sc = SORTS.get(sort, L.dr)
    sc = price_col if isinstance(sc, str) else sc
    oc = sc.asc().nulls_last() if order == "asc" else sc.desc().nulls_last()
    with system_session() as sdb:
        base = select(L, X).outerjoin(X, X.listing_id == L.id).where(and_(*cond))
        rows = sdb.execute(base.order_by(oc, L.domain).limit(2000)).all()
        items = [listing_dict(l, x, viewer_org) for l, x in rows]
        facets = {"categories": sorted({r for (r,) in sdb.execute(select(L.category).distinct()) if r}),
                  "countries": sorted({r for (r,) in sdb.execute(select(X.country).distinct()) if r}),
                  "langs": sorted({r for (r,) in sdb.execute(select(L.lang).distinct()) if r})}
    if verified_only:
        items = [i for i in items if i["verified_any"]]
    total = len(items)
    page_size = max(1, min(100, page_size))
    start = (max(1, page) - 1) * page_size
    return {"items": items[start:start + page_size], "total": total, "page": page, "page_size": page_size, "facets": facets}


def get_listing_public(listing_id, viewer_org=None) -> dict | None:
    with system_session() as sdb:
        row = sdb.execute(select(m.MarketplaceListing, g.GrowthListingExt).outerjoin(
            g.GrowthListingExt, g.GrowthListingExt.listing_id == m.MarketplaceListing.id)
            .where(m.MarketplaceListing.id == listing_id)).first()
        return listing_dict(row[0], row[1], viewer_org, full=True) if row else None


def own_listing(db: Session, org_id, listing_id) -> tuple[m.MarketplaceListing, g.GrowthListingExt]:
    l = db.get(m.MarketplaceListing, listing_id)
    # marketplace_listing has no RLS predicate (shared inventory) — ownership is enforced here.
    if not l or l.publisher_org_id != org_id:
        raise HTTPException(404, "Listing not found in your publisher inventory")
    x = db.scalar(select(g.GrowthListingExt).where(g.GrowthListingExt.listing_id == l.id))
    if not x:
        x = g.GrowthListingExt(organisation_id=org_id, listing_id=l.id)
        db.add(x)
        db.flush()
    return l, x


def verify_metrics(db: Session, org_id, l: m.MarketplaceListing, x: g.GrowthListingExt) -> dict:
    """Refresh DR/AS/ref-domains from Ahrefs or Semrush when connected — otherwise NotConnected."""
    done, errors = {}, []
    src = dict(x.metrics_source or {})
    now = utcnow().isoformat()
    try:
        tok = credentials(db, org_id, "ahrefs")["secret"]
        r = http_call("GET", "https://api.ahrefs.com/v3/site-explorer/domain-rating",
                      params={"target": l.domain, "date": utcnow().date().isoformat()}, headers={"Authorization": f"Bearer {tok}"})
        if r.status_code == 200:
            dr = (r.json().get("domain_rating") or {}).get("domain_rating")
            if dr is not None:
                l.dr = dr
                src["dr"] = {"source": "ahrefs", "verified_at": now}
                done["dr"] = dr
        else:
            errors.append(f"Ahrefs HTTP {r.status_code}")
    except NotConnected:
        pass
    try:
        key = credentials(db, org_id, "semrush")["secret"]
        r = http_call("GET", "https://api.semrush.com/analytics/v1/", params={
            "key": key, "type": "backlinks_overview", "target": l.domain, "target_type": "root_domain",
            "export_columns": "ascore,total,domains_num"})
        lines = [ln for ln in r.text.strip().splitlines() if ln]
        if r.status_code == 200 and len(lines) >= 2 and not lines[0].startswith("ERROR"):
            vals = dict(zip(lines[0].split(";"), lines[1].split(";")))
            if vals.get("ascore", "").isdigit():
                l.as_ = int(vals["ascore"])
                src["as"] = {"source": "semrush", "verified_at": now}
                done["as"] = l.as_
            if vals.get("domains_num", "").isdigit():
                l.ref_domains = int(vals["domains_num"])
                src["ref_domains"] = {"source": "semrush", "verified_at": now}
                done["ref_domains"] = l.ref_domains
        else:
            errors.append(f"Semrush: {lines[0][:120] if lines else r.status_code}")
    except NotConnected:
        pass
    if not done and not errors:
        raise NotConnected("Ahrefs and Semrush are NOT CONNECTED — metrics stay publisher-declared (self-reported).")
    x.metrics_source = src
    x.updated_at = utcnow()
    return {"verified": done, "errors": errors}


# ================================================================== delivery verification
def verify_link(live_url: str, target_url: str, domain: str | None, require_dofollow: bool) -> dict:
    """Fetch the delivered page (SSRF-safe crawler) and check a link to the target exists."""
    out = {"checked_at": utcnow().isoformat(), "live_url": live_url, "target_url": target_url, "issues": []}
    if domain and domain_of(live_url) != domain_of(domain) and not domain_of(live_url).endswith("." + domain_of(domain)):
        out["issues"].append(f"Live URL is not on the listing domain {domain}")
    try:
        cr = Crawler(live_url, max_pages=1, render_sample=0, mobile_sample=0, external_check_limit=0, delay=0, timeout=20)
        resp = cr.fetch(live_url)
    except CrawlBlocked as e:
        out.update(status=None, found=False)
        out["issues"].append(str(e))
        out["verified"] = False
        return out
    out["status"] = resp.get("status")
    out["final_url"] = resp.get("final_url")
    if resp.get("status") != 200:
        out.update(found=False, verified=False)
        out["issues"].append(f"Page returned {resp.get('status') or resp.get('error')}")
        return out
    soup = BeautifulSoup(resp.get("body") or "", "html.parser")
    mr = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    noindex = bool(mr and "noindex" in (mr.get("content") or "").lower()) or \
        "noindex" in str((resp.get("headers") or {}).get("x-robots-tag", "")).lower()
    tgt = normalise(target_url).rstrip("/")
    match = None
    from urllib.parse import urljoin
    for a in soup.find_all("a", href=True):
        href = normalise(urljoin(resp.get("final_url") or live_url, a["href"].strip())).rstrip("/")
        if href == tgt or href.split("?")[0] == tgt.split("?")[0]:
            rel = a.get("rel") or []
            match = {"href": href, "rel": " ".join(rel) if isinstance(rel, list) else str(rel), "anchor": a.get_text(" ", strip=True)[:200]}
            break
    out["noindex"] = noindex
    out["found"] = bool(match)
    if match:
        out.update(rel=match["rel"] or "dofollow", anchor=match["anchor"])
        rels = set(match["rel"].lower().split())
        out["dofollow"] = not (rels & {"nofollow", "sponsored", "ugc"})
        if require_dofollow and not out["dofollow"]:
            out["issues"].append(f"Link is {match['rel']} but the listing promises dofollow")
    else:
        out["issues"].append("No link to the target URL was found on the page")
    if noindex:
        out["issues"].append("Page is noindex")
    out["verified"] = bool(match) and not out["issues"]
    return out


# ================================================================== orders
ADV_STATUSES = ["pending", "accepted", "in_progress", "delivered", "completed", "rejected", "disputed", "refunded"]
FLOW = {  # action → (actor side, from statuses, to status)
    "accept": ("publisher", {"pending"}, "accepted"),
    "reject": ("publisher", {"pending"}, "rejected"),
    "start": ("publisher", {"accepted"}, "in_progress"),
    "deliver": ("publisher", {"accepted", "in_progress", "delivered"}, "delivered"),
    "cancel": ("advertiser", {"pending"}, "refunded"),
    "complete": ("advertiser", {"delivered"}, "completed"),
    "dispute": ("advertiser", {"accepted", "in_progress", "delivered"}, "disputed"),
    "resolve_refund": ("platform", {"disputed"}, "refunded"),
    "resolve_release": ("platform", {"disputed"}, "completed"),
}


def order_number(order_id) -> str:
    return "GQ-" + order_id.hex[:8].upper()


def _event(action: str, by: str, note: str | None = None) -> dict:
    return {"at": utcnow().isoformat(), "action": action, "by": by, **({"note": note[:500]} if note else {})}


def checkout(db: Session, org_id, user_id, items: list[g.GrowthCartItem]) -> list[dict]:
    if not items:
        raise HTTPException(400, "Your cart is empty")
    if len(items) > 100:
        raise HTTPException(400, "At most 100 items per checkout")
    w = lock_wallet(db, org_id)  # held for the whole checkout → concurrent checkouts serialise
    blocked = set(db.scalars(select(g.GrowthBlockedPublisher.publisher_org_id)))
    priced = []
    for it in items:
        if it.kind == "service":
            svc = db.get(m.Service, it.service_id)
            if not svc:
                raise HTTPException(400, "Service no longer available")
            try:
                total, tier, addons = growth_catalog.price(svc.name, it.tier, it.addons or [])
            except ValueError as e:
                raise HTTPException(400, str(e))
            det = growth_catalog.detail(svc.name)
            priced.append((it, money(total), None, {"svc": svc, "tier": tier, "addons": addons, "tat": det["tat_days"]}))
            continue
        pub = get_listing_public(it.listing_id, org_id)
        if not pub or pub["status"] != "active":
            raise HTTPException(409, f"Listing {pub['domain'] if pub else it.listing_id} is no longer available")
        if pub["own"]:
            raise HTTPException(400, f"You cannot order from your own listing ({pub['domain']})")
        l = db.get(m.MarketplaceListing, it.listing_id)
        if l.publisher_org_id in blocked:
            raise HTTPException(400, f"You blocked the publisher of {l.domain}")
        price = l.guest_post_price if it.kind == "guest_post" else l.link_ins_price
        if price is None:
            raise HTTPException(400, f"{l.domain} does not offer {it.kind.replace('_', ' ')}")
        if not it.target_url:
            raise HTTPException(400, f"Target URL missing for {l.domain}")
        priced.append((it, money(price), l, None))
    total = sum((p for _, p, _, _ in priced), ZERO)
    if money(w.balance) < total:
        raise HTTPException(402, detail={"code": "insufficient_funds", "balance": float(w.balance), "required": float(total),
                                         "message": f"Insufficient wallet balance: {money(w.balance)} available, {total} needed. Add funds first."})
    fee = fee_pct()
    created, lines = [], []
    for it, price, l, svc in priced:
        tat_days = (svc["tat"] if svc else (l.tat or 14))
        o = m.Order(organisation_id=org_id, listing_id=l.id if l else None, service_id=svc["svc"].id if svc else None,
                    status="pending", amount=price, tat_due=utcnow() + timedelta(days=int(tat_days)))
        db.add(o)
        db.flush()
        num = order_number(o.id)
        if svc:
            title = f"{svc['svc'].name} — {svc['tier']['name']}"
            fee_amt, pub_amt = price, ZERO
        else:
            title = f"{'Guest post' if it.kind == 'guest_post' else 'Link insertion'} on {l.domain}"
            fee_amt = money(price * fee / 100)
            pub_amt = price - fee_amt
        go = g.GrowthOrder(organisation_id=org_id, order_id=o.id, number=num, publisher_org_id=l.publisher_org_id if l else None,
                           project_id=it.project_id, kind=it.kind, title=title, domain=l.domain if l else None,
                           target_url=it.target_url, anchor=it.anchor, brief=it.notes, tier=svc["tier"]["key"] if svc else None,
                           addons=[a["key"] for a in svc["addons"]] if svc else [], link_type=l.link_type if l else None,
                           fee_pct=fee if not svc else Decimal("100"), fee_amount=fee_amt, publisher_amount=pub_amt,
                           timeline=[_event("placed", "advertiser")], placed_by=user_id)
        db.add(go)
        db.add(g.GrowthEscrow(organisation_id=org_id, order_id=o.id, amount=price))
        post(db, org_id, "charge", -price, f"Escrow hold — order {num}: {title}", order_id=o.id, ref=num, user_id=user_id)
        db.flush()
        if l and l.publisher_org_id:
            with as_org(db, l.publisher_org_id):
                db.add(g.GrowthPublisherOrder(organisation_id=l.publisher_org_id, order_id=o.id, number=num, advertiser_org_id=org_id,
                                              listing_id=l.id, domain=l.domain, kind=it.kind, status="pending",
                                              target_url=it.target_url, anchor=it.anchor, brief=it.notes, link_type=l.link_type,
                                              amount=pub_amt, gross=price, tat_due=o.tat_due, timeline=[_event("placed", "advertiser")]))
                db.add(m.Notification(organisation_id=l.publisher_org_id, kind="marketplace_order", title=f"New order {num}: {title}",
                                      body=f"Earning {pub_amt} on completion. Accept or reject it.", link=f"/marketplace/orders?order={o.id}"))
        lines.append({"description": f"{title} (order {num})", "qty": 1, "amount": float(price)})
        created.append({"order_id": str(o.id), "number": num, "amount": float(price), "title": title})
    inv = create_invoice(db, org_id, "order", lines, "checkout", None)
    for it, *_ in priced:
        db.delete(it)
    return [{**c, "invoice_id": str(inv.id)} for c in created]


def _ctx_on(db, org, cur):
    from contextlib import nullcontext
    return nullcontext() if str(org) == str(cur) else as_org(db, org)


def transition(db: Session, cur_org, side: str, order_id, action: str, *, note: str | None = None, live_url: str | None = None,
               user_id=None) -> dict:
    """Apply an order action for ``side`` (advertiser/publisher/platform). The caller has resolved the side from
    rows visible to ``cur_org`` (or is a platform administrator)."""
    if action not in FLOW:
        raise HTTPException(400, f"action must be one of {list(FLOW)}")
    who, frm, to = FLOW[action]
    # locate both sides
    if side == "advertiser":
        go = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == order_id).with_for_update())
        if not go:
            raise HTTPException(404, "Order not found")
        adv_org, pub_org = cur_org, go.publisher_org_id
        is_service = go.kind == "service"
    elif side == "publisher":
        po = db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.order_id == order_id).with_for_update())
        if not po:
            raise HTTPException(404, "Order not found")
        adv_org, pub_org, is_service = po.advertiser_org_id, cur_org, False
    else:  # platform: cur_org is the advertiser org (session opened there by the admin endpoint)
        go = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == order_id).with_for_update())
        if not go:
            raise HTTPException(404, "Order not found")
        adv_org, pub_org, is_service = cur_org, go.publisher_org_id, go.kind == "service"
    allowed_side = who
    if is_service and who == "publisher":
        allowed_side = "platform"  # Genranq fulfils service orders
    if side != allowed_side:
        raise HTTPException(403, f"Only the {allowed_side} can {action.replace('_', ' ')} this order")
    with _ctx_on(db, adv_org, cur_org):
        order = db.scalar(select(m.Order).where(m.Order.id == order_id).with_for_update())
        go = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == order_id))
        if order.status not in frm:
            raise HTTPException(409, f"Cannot {action.replace('_', ' ')} an order that is {order.status.replace('_', ' ')}")
        if action == "deliver":
            if not live_url:
                raise HTTPException(400, "live_url is required to deliver")
            go.live_url, go.verified, go.verification = live_url, False, {"status": "pending"}
        if action == "complete" and not go.verified:
            raise HTTPException(409, "The delivered link has not passed automatic verification yet — re-run verification or open a dispute")
        if action in ("reject", "dispute") and not note:
            raise HTTPException(400, "A reason is required")
        order.status = to
        go.timeline = go.timeline + [_event(action, side, note or live_url)]
        go.note = note or go.note
        go.updated_at = utcnow()
        esc = db.scalar(select(g.GrowthEscrow).where(g.GrowthEscrow.order_id == order_id).with_for_update())
        if to == "refunded" or action == "reject":
            if esc.status != "held":
                raise HTTPException(409, "Escrow already settled")
            esc.status, esc.settled_at = "refunded", utcnow()
            post(db, adv_org, "refund", esc.amount, f"Refund — order {go.number} ({action.replace('_', ' ')})", order_id=order_id,
                 ref=go.number, user_id=user_id)
            create_invoice(db, adv_org, "credit_note", [{"description": f"Refund of order {go.number}: {go.title}", "qty": 1,
                                                          "amount": float(esc.amount)}], "order", order_id)
        if to == "completed":
            if esc.status != "held":
                raise HTTPException(409, "Escrow already settled")
            esc.status, esc.settled_at = "released", utcnow()
            _record_backlink(db, adv_org, go)
        pub_amount, number, title = go.publisher_amount, go.number, go.title
        db.add(m.Notification(organisation_id=adv_org, kind="marketplace_order", title=f"Order {number} {to.replace('_', ' ')}",
                              body=note, link=f"/marketplace/orders?order={order_id}"))
    if pub_org:
        with _ctx_on(db, pub_org, cur_org):
            po = db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.order_id == order_id))
            po.status = to
            po.timeline = po.timeline + [_event(action, side, note or live_url)]
            po.note = note or po.note
            po.updated_at = utcnow()
            if action == "deliver":
                po.live_url, po.verified, po.verification = live_url, False, {"status": "pending"}
            if to == "completed" and money(pub_amount) > ZERO:
                post(db, pub_org, "earning", pub_amount, f"Earning — order {number}: {title} (after {fee_pct()}% platform fee)",
                     order_id=order_id, ref=number)
            db.add(m.Notification(organisation_id=pub_org, kind="marketplace_order", title=f"Order {number} {to.replace('_', ' ')}",
                                  body=note, link=f"/marketplace/orders?order={order_id}"))
    return {"order_id": str(order_id), "status": to}


def _record_backlink(db: Session, org_id, go: g.GrowthOrder):
    """On completion, register the verified link in the advertiser's Backlink CRM when the project tracks the target site."""
    if not go.project_id or not go.live_url or not go.target_url or go.kind == "service":
        return
    td = domain_of(go.target_url)
    site = next((s for s in db.scalars(select(m.Site).where(m.Site.project_id == go.project_id)) if domain_of(s.domain) == td), None)
    if not site:
        return
    v = go.verification or {}
    bl = m.Backlink(organisation_id=org_id, project_id=go.project_id, target_site_id=site.id, source_url=go.live_url,
                    target_url=go.target_url, anchor_text=v.get("anchor") or go.anchor, link_type="dofollow" if v.get("dofollow") else (v.get("rel") or None),
                    paid_or_free="paid", status="live", cost=go.fee_amount + go.publisher_amount, currency="USD",
                    first_detected=utcnow(), last_verified=utcnow())
    db.add(bl)
    db.flush()
    o = db.get(m.Order, go.order_id)
    o.backlink_id = bl.id


def apply_verification(db: Session, cur_org, order_id, result: dict):
    """Store a verification result on both sides (called from the publisher-org job)."""
    po = db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.order_id == order_id))
    if po:
        adv = po.advertiser_org_id
        po.verification, po.verified = result, bool(result.get("verified"))
        po.timeline = po.timeline + [_event("verified" if result.get("verified") else "verification_failed", "system",
                                            "; ".join(result.get("issues") or []) or None)]
    else:
        adv = cur_org
    with _ctx_on(db, adv, cur_org):
        go = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == order_id))
        if go:
            go.verification, go.verified = result, bool(result.get("verified"))
            go.timeline = go.timeline + [_event("verified" if result.get("verified") else "verification_failed", "system",
                                                "; ".join(result.get("issues") or []) or None)]
            db.add(m.Notification(organisation_id=adv, kind="marketplace_order",
                                  title=f"Order {go.number}: link {'verified — ready to complete' if result.get('verified') else 'verification failed'}",
                                  body="; ".join(result.get("issues") or []) or None, link=f"/marketplace/orders?order={order_id}"))


def adv_order_dict(o: m.Order, go: g.GrowthOrder, esc: g.GrowthEscrow | None = None) -> dict:
    return {"id": str(o.id), "number": go.number, "side": "advertiser", "status": o.status, "amount": float(o.amount or 0),
            "tat_due": o.tat_due, "kind": go.kind, "title": go.title, "domain": go.domain, "target_url": go.target_url,
            "anchor": go.anchor, "brief": go.brief, "attachments": go.attachments, "tier": go.tier, "addons": go.addons,
            "link_type": go.link_type, "live_url": go.live_url, "verification": go.verification, "verified": go.verified,
            "fee_amount": float(go.fee_amount), "publisher_amount": float(go.publisher_amount), "timeline": go.timeline,
            "note": go.note, "publisher_id": str(go.publisher_org_id) if go.publisher_org_id else None,
            "publisher": publisher_label(go.publisher_org_id), "project_id": str(go.project_id) if go.project_id else None,
            "escrow": esc.status if esc else None, "placed_at": go.placed_at, "updated_at": go.updated_at,
            "backlink_id": str(o.backlink_id) if o.backlink_id else None, "listing_id": str(o.listing_id) if o.listing_id else None}


def pub_order_dict(po: g.GrowthPublisherOrder) -> dict:
    return {"id": str(po.order_id), "number": po.number, "side": "publisher", "status": po.status, "amount": float(po.amount),
            "gross": float(po.gross), "tat_due": po.tat_due, "kind": po.kind, "title": f"{'Guest post' if po.kind == 'guest_post' else 'Link insertion'} on {po.domain}",
            "domain": po.domain, "target_url": po.target_url, "anchor": po.anchor, "brief": po.brief, "attachments": po.attachments,
            "link_type": po.link_type, "live_url": po.live_url, "verification": po.verification, "verified": po.verified,
            "timeline": po.timeline, "note": po.note, "placed_at": po.placed_at, "updated_at": po.updated_at,
            "listing_id": str(po.listing_id) if po.listing_id else None}


# ================================================================== Stripe
def stripe_secret(db: Session, org_id) -> str:
    env = os.environ.get("STRIPE_SECRET_KEY")
    if env:
        return env
    return credentials(db, org_id, "stripe")["secret"]


def stripe_webhook_secret(db: Session, org_id) -> str | None:
    env = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if env:
        return env
    c = db.scalar(select(m.IntegrationConnection).where(m.IntegrationConnection.provider_key == "stripe"))
    return (c.config or {}).get("webhook_secret") if c else None


def create_checkout(db: Session, org_id, user_id, amount, success_url: str, cancel_url: str) -> g.GrowthTopup:
    key = stripe_secret(db, org_id)  # raises NotConnected
    amt = money(amount)
    t = g.GrowthTopup(organisation_id=org_id, amount=amt, created_by=user_id)
    db.add(t)
    db.flush()
    r = http_call("POST", "https://api.stripe.com/v1/checkout/sessions", auth=(key, ""), data={
        "mode": "payment", "success_url": success_url, "cancel_url": cancel_url, "client_reference_id": str(t.id),
        "line_items[0][quantity]": "1", "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(int(amt * 100)),
        "line_items[0][price_data][product_data][name]": "Genranq wallet top-up",
        "metadata[org_id]": str(org_id), "metadata[topup_id]": str(t.id)}, retries=1)
    if r.status_code >= 400:
        t.status = "failed"
        raise HTTPException(502, f"Stripe rejected the checkout session: HTTP {r.status_code} {r.text[:200]}")
    js = r.json()
    t.stripe_session_id, t.checkout_url = js.get("id"), js.get("url")
    return t


def verify_stripe_signature(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    import hashlib
    import hmac
    import time as _t
    parts = {}
    for kv in (header or "").split(","):
        k, _, v = kv.strip().partition("=")
        parts.setdefault(k, []).append(v)
    ts = (parts.get("t") or [""])[0]
    if not ts.isdigit() or abs(_t.time() - int(ts)) > tolerance:
        return False
    exp = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(exp, s) for s in parts.get("v1", []))


def settle_topup(db: Session, org_id, session: dict) -> dict:
    tid = (session.get("metadata") or {}).get("topup_id") or session.get("client_reference_id")
    t = db.scalar(select(g.GrowthTopup).where(g.GrowthTopup.id == tid).with_for_update()) if tid else None
    if not t or t.stripe_session_id != session.get("id"):
        raise HTTPException(400, "Unknown checkout session")
    if t.status == "paid":
        return {"ok": True, "duplicate": True}
    if session.get("payment_status") != "paid":
        return {"ok": True, "ignored": "not paid"}
    paid = money(Decimal(session.get("amount_total") or 0) / 100)
    if paid != money(t.amount):
        raise HTTPException(400, "Amount mismatch")
    txn = post(db, org_id, "add-funds", paid, "Wallet top-up via Stripe Checkout", ref=session.get("id"), user_id=t.created_by)
    t.status, t.paid_at, t.txn_id = "paid", utcnow(), txn.id
    create_invoice(db, org_id, "topup", [{"description": "Wallet top-up (Stripe)", "qty": 1, "amount": float(paid)}], "topup", t.id)
    db.add(m.Notification(organisation_id=org_id, kind="wallet", title=f"Wallet topped up: {paid} USD", link="/wallet"))
    return {"ok": True, "credited": float(paid)}


# ================================================================== manual adjustments & payouts
def apply_adjustment(db: Session, adj: g.GrowthWalletAdjustment, decided_by, email: str, notes: str | None):
    if adj.status != "pending":
        raise HTTPException(409, "Adjustment already decided")
    txn = post(db, adj.organisation_id, "adjustment", adj.amount, f"Manual adjustment: {adj.reason}", ref=adj.reference or str(adj.id),
               user_id=decided_by)
    adj.status, adj.txn_id = "applied", txn.id
    adj.decided_by, adj.decided_by_email, adj.decision_notes, adj.decided_at = decided_by, email, notes, utcnow()
    if money(adj.amount) > ZERO:
        create_invoice(db, adj.organisation_id, "adjustment", [{"description": f"Manual wallet credit: {adj.reason}", "qty": 1,
                                                                  "amount": float(adj.amount)}], "adjustment", adj.id)
    db.add(m.Notification(organisation_id=adj.organisation_id, kind="wallet", title=f"Wallet adjustment applied: {money(adj.amount)} USD",
                          body=adj.reason, link="/wallet"))


def reconcile_payouts(db: Session):
    """Payout approvals may be decided on the generic Approvals screen; sync their outcome here."""
    for p in db.scalars(select(g.GrowthPayout).where(g.GrowthPayout.status == "pending_approval").with_for_update()):
        a = db.get(m.Approval, p.approval_id) if p.approval_id else None
        if not a or a.status == "pending":
            continue
        if a.status == "approved":
            p.status, p.decided_at = "approved", utcnow()
        elif a.status == "rejected":
            t = post(db, p.organisation_id, "payout-reversal", p.amount, "Payout request rejected — funds released", ref=str(p.id))
            p.status, p.decided_at, p.release_txn_id = "rejected", utcnow(), t.id


def payout_dict(p: g.GrowthPayout) -> dict:
    return {"id": str(p.id), "amount": float(p.amount), "destination": p.destination, "status": p.status,
            "approval_id": str(p.approval_id) if p.approval_id else None, "paid_reference": p.paid_reference,
            "created_at": p.created_at, "decided_at": p.decided_at,
            "transfer": "NOT CONNECTED — no payout rail configured; the operator transfers manually and records the reference"
            if p.status == "approved" else None}


def json_dumps(o) -> str:
    return json.dumps(o, default=str)
