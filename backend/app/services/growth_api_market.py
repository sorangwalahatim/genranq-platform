"""API: Services Catalog (73), Backlink Marketplace + Listings (14/74), Cart & Orders (75), Wallet (76) and the
platform-operator queues (manual adjustments, payouts, service orders, disputes, publisher reports)."""
from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from .. import models as m
from .. import models_growth as g
from ..db import system_session, tenant_session
from ..deps import Ctx, deny_client, get_ctx, get_db, log, project_guard, require, require_platform_admin
from ..rbac import RANK
from ..util import to_dict
from . import growth_catalog as cat
from . import growth_market as M
from . import reports
from .growth_common import check_url, money, utcnow
from .integrations import NotConnected, connected_map
from .jobs import enqueue

router = APIRouter()


def _money_role(ctx: Ctx):
    if ctx.role not in ("owner", "admin", "manager"):
        raise HTTPException(403, "Only owners, admins and managers can spend from the wallet")


# ================================================================== services catalog (73)
def _svc_dict(s: m.Service) -> dict:
    d = cat.detail(s.name) or {}
    return {"id": str(s.id), "name": s.name, "type": s.type, "tags": sorted(set((s.tags or []) + d.get("tags", []))),
            "description": d.get("description"), "deliverables": d.get("deliverables", []), "tat_days": d.get("tat_days"),
            "tiers": d.get("tiers", []), "addons": d.get("addons", []), "orderable": bool(d),
            "from_price": min((t["price"] for t in d.get("tiers", [])), default=None)}


@router.get("/services")
def services(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    order = list(cat.CATALOG)
    items = [_svc_dict(s) for s in db.scalars(select(m.Service))]
    return sorted(items, key=lambda s: order.index(s["name"]) if s["name"] in order else 99)


class ServiceCartIn(BaseModel):
    tier: str = Field(..., max_length=60)
    addons: list[str] = Field([], max_length=20)
    project_id: uuid.UUID | None = None
    target_url: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=5000)


@router.post("/services/{sid}/cart")
def service_to_cart(sid: uuid.UUID, body: ServiceCartIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    s = db.get(m.Service, sid)
    if not s:
        raise HTTPException(404, "Service not found")
    try:
        price, _, _ = cat.price(s.name, body.tier, body.addons)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.project_id:
        project_guard(db, ctx, body.project_id)
    it = g.GrowthCartItem(organisation_id=ctx.org_id, user_id=ctx.user_id, service_id=s.id, kind="service", tier=body.tier,
                          addons=body.addons, project_id=body.project_id, target_url=check_url(body.target_url, "target_url", False),
                          notes=body.notes)
    db.add(it)
    db.flush()
    return {"id": str(it.id), "price": price}


# ================================================================== listings (14/74)
class ListingIn(BaseModel):
    domain: str | None = Field(None, min_length=3, max_length=253)
    url: str | None = Field(None, max_length=2000)
    category: str | None = Field(None, max_length=100)
    lang: str | None = Field(None, max_length=10)
    country: str | None = Field(None, max_length=60)
    description: str | None = Field(None, max_length=3000)
    rules: str | None = Field(None, max_length=3000)
    sample_urls: list[str] = Field([], max_length=10)
    da: float | None = Field(None, ge=0, le=100)
    dr: float | None = Field(None, ge=0, le=100)
    as_: float | None = Field(None, ge=0, le=100, alias="as")
    cf: float | None = Field(None, ge=0, le=100)
    tf: float | None = Field(None, ge=0, le=100)
    spam: float | None = Field(None, ge=0, le=100)
    org_traffic: int | None = Field(None, ge=0, le=10_000_000_000)
    ref_domains: int | None = Field(None, ge=0, le=100_000_000)
    guest_post_price: float | None = Field(None, gt=0, le=1_000_000)
    link_ins_price: float | None = Field(None, gt=0, le=1_000_000)
    link_type: str = "dofollow"
    link_validity: str | None = Field(None, max_length=60)
    tat: int | None = Field(None, ge=1, le=120)
    status: str = "active"

    model_config = {"populate_by_name": True}


def _apply_listing(l: m.MarketplaceListing, x: g.GrowthListingExt, body: ListingIn, fields: set[str]):
    from .growth_common import domain_of
    if "domain" in fields:
        d = domain_of(body.domain or "")
        if not d or "." not in d and d != "localhost" and not d.replace(".", "").isdigit():
            raise HTTPException(400, "domain must be a hostname like example.com")
        l.domain = d
    if "link_type" in fields and body.link_type not in M.LINK_TYPES:
        raise HTTPException(400, f"link_type must be one of {M.LINK_TYPES}")
    if "status" in fields and body.status not in ("active", "paused"):
        raise HTTPException(400, "status must be active or paused")
    src = dict(x.metrics_source or {})
    for k in ("category", "lang", "link_type", "link_validity", "tat", "guest_post_price", "link_ins_price"):
        if k in fields:
            setattr(l, k, getattr(body, k))
    for k in M.LISTING_METRICS:
        name = "as" if k == "as_" else k
        if k in fields or name in fields:
            v = getattr(body, k)
            if getattr(l, k) is None or float(getattr(l, k)) != (float(v) if v is not None else None):
                src[name] = {"source": "self"}  # publisher edit → self-reported again
            setattr(l, k, v)
    x.metrics_source = src
    for k in ("country", "description", "rules", "status"):
        if k in fields:
            setattr(x, k, getattr(body, k))
    if "url" in fields:
        x.url = check_url(body.url, "url", False)
    if "sample_urls" in fields:
        x.sample_urls = [check_url(u, "sample_urls") for u in body.sample_urls]
    if l.guest_post_price is None and l.link_ins_price is None:
        raise HTTPException(400, "Set a guest post price and/or a link insertion price")
    x.updated_at = utcnow()


@router.get("/marketplace/listings")
def browse(q: str | None = Query(None, max_length=100), category: str | None = None, country: str | None = None, lang: str | None = None,
           link_type: str | None = None, price_type: str = "guest_post", verified_only: bool = False,
           da_min: float | None = None, da_max: float | None = None, dr_min: float | None = None, dr_max: float | None = None,
           as_min: float | None = None, cf_min: float | None = None, tf_min: float | None = None, spam_max: float | None = None,
           traffic_min: int | None = None, ref_domains_min: int | None = None, price_min: float | None = None, price_max: float | None = None,
           tat_max: int | None = None, sort: str = "dr", order: str = "desc", page: int = Query(1, ge=1, le=1000),
           page_size: int = Query(25, ge=1, le=100), include_own: bool = True,
           db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    if price_type not in ("guest_post", "link_insertion"):
        raise HTTPException(400, "price_type must be guest_post or link_insertion")
    if sort not in M.SORTS or order not in ("asc", "desc"):
        raise HTTPException(400, f"sort must be one of {list(M.SORTS)} and order asc|desc")
    blocked = list(db.scalars(select(g.GrowthBlockedPublisher.publisher_org_id)))
    filters = {k: v for k, v in dict(da_min=da_min, da_max=da_max, dr_min=dr_min, dr_max=dr_max, as_min=as_min, cf_min=cf_min,
                                     tf_min=tf_min, spam_max=spam_max, traffic_min=traffic_min, ref_domains_min=ref_domains_min,
                                     price_min=price_min, price_max=price_max, tat_max=tat_max).items() if v is not None}
    res = M.browse(ctx.org_id, q=q, category=category, country=country, lang=lang, link_type=link_type, price_type=price_type,
                   filters=filters, verified_only=verified_only, sort=sort, order=order, page=page, page_size=page_size,
                   blocked=blocked, include_own=include_own)
    in_cart = {str(i) for i in db.scalars(select(g.GrowthCartItem.listing_id).where(g.GrowthCartItem.user_id == ctx.user_id))}
    for it in res["items"]:
        it["in_cart"] = it["id"] in in_cart
    return res


@router.get("/marketplace/listings/{lid}")
def listing(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    d = M.get_listing_public(lid, ctx.org_id)
    if not d or (d["status"] != "active" and not d["own"]):
        raise HTTPException(404, "Listing not found")
    d["blocked"] = bool(d["publisher_id"] and db.scalar(select(g.GrowthBlockedPublisher).where(
        g.GrowthBlockedPublisher.publisher_org_id == uuid.UUID(d["publisher_id"]))))
    return d


@router.get("/marketplace/my-listings")
def my_listings(db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    out = []
    for l in db.scalars(select(m.MarketplaceListing).where(m.MarketplaceListing.publisher_org_id == ctx.org_id).order_by(m.MarketplaceListing.domain)):
        x = db.scalar(select(g.GrowthListingExt).where(g.GrowthListingExt.listing_id == l.id))
        d = M.listing_dict(l, x, ctx.org_id, full=True)
        d["orders"] = db.scalar(select(func.count()).select_from(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.listing_id == l.id))
        d["earned"] = float(db.scalar(select(func.coalesce(func.sum(g.GrowthPublisherOrder.amount), 0)).where(
            g.GrowthPublisherOrder.listing_id == l.id, g.GrowthPublisherOrder.status == "completed")) or 0)
        out.append(d)
    return {"items": out, "ahrefs": connected_map(db).get("ahrefs", "NOT_CONNECTED"), "semrush": connected_map(db).get("semrush", "NOT_CONNECTED")}


@router.post("/marketplace/my-listings")
def create_listing(body: ListingIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    n = db.scalar(select(func.count()).select_from(m.MarketplaceListing).where(m.MarketplaceListing.publisher_org_id == ctx.org_id))
    if n >= 500:
        raise HTTPException(402, "Listing limit (500) reached")
    l = m.MarketplaceListing(publisher_org_id=ctx.org_id, domain="x")
    x = g.GrowthListingExt(organisation_id=ctx.org_id, listing_id=None, created_by=ctx.user_id)
    if not body.domain:
        raise HTTPException(400, "domain is required")
    _apply_listing(l, x, body, set(body.model_fields_set) | {"domain", "link_type", "status"})
    if db.scalar(select(m.MarketplaceListing).where(m.MarketplaceListing.publisher_org_id == ctx.org_id, m.MarketplaceListing.domain == l.domain)):
        raise HTTPException(409, "You already list this domain")
    db.add(l)
    db.flush()
    x.listing_id = l.id
    db.add(x)
    db.flush()
    log(db, ctx, "marketplace.listing_created", f"listing:{l.id}", {"domain": l.domain})
    return M.listing_dict(l, x, ctx.org_id, full=True)


@router.patch("/marketplace/my-listings/{lid}")
def update_listing(lid: uuid.UUID, body: ListingIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    l, x = M.own_listing(db, ctx.org_id, lid)
    _apply_listing(l, x, body, set(body.model_fields_set))
    log(db, ctx, "marketplace.listing_updated", f"listing:{l.id}")
    return M.listing_dict(l, x, ctx.org_id, full=True)


@router.delete("/marketplace/my-listings/{lid}")
def delete_listing(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("delete"))):
    l, x = M.own_listing(db, ctx.org_id, lid)
    if db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.listing_id == l.id)):
        x.status = "paused"
        return {"ok": True, "paused": True, "message": "Listing has orders, so it was paused instead of deleted"}
    with system_session() as sdb:  # carts of other organisations may reference it
        sdb.execute(g.GrowthCartItem.__table__.delete().where(g.GrowthCartItem.listing_id == l.id))
    db.delete(x)
    db.flush()
    db.delete(l)
    log(db, ctx, "marketplace.listing_deleted", f"listing:{lid}")
    return {"ok": True}


@router.post("/marketplace/my-listings/{lid}/verify-metrics")
def verify_metrics(lid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    l, x = M.own_listing(db, ctx.org_id, lid)
    try:
        r = M.verify_metrics(db, ctx.org_id, l, x)
    except NotConnected as e:
        raise HTTPException(409, detail={"code": "not_connected", "provider": "ahrefs", "message": str(e)})
    return {**r, "listing": M.listing_dict(l, x, ctx.org_id, full=True)}


class ReportIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=200)
    details: str | None = Field(None, max_length=3000)
    order_id: uuid.UUID | None = None


@router.post("/marketplace/listings/{lid}/report")
def report_listing(lid: uuid.UUID, body: ReportIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    d = M.get_listing_public(lid, ctx.org_id)
    if not d or not d["publisher_id"] or d["own"]:
        raise HTTPException(404, "Listing not found")
    r = g.GrowthPublisherReport(organisation_id=ctx.org_id, publisher_org_id=uuid.UUID(d["publisher_id"]), listing_id=lid,
                                order_id=body.order_id, reason=body.reason, details=body.details, created_by=ctx.user_id)
    db.add(r)
    log(db, ctx, "marketplace.publisher_reported", f"listing:{lid}", {"reason": body.reason})
    db.flush()
    return to_dict(r)


@router.post("/marketplace/publishers/{pid}/block")
def block(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    if pid == ctx.org_id:
        raise HTTPException(400, "You cannot block yourself")
    with system_session() as sdb:
        doms = [d for (d,) in sdb.execute(select(m.MarketplaceListing.domain).where(m.MarketplaceListing.publisher_org_id == pid))]
    if not doms:
        raise HTTPException(404, "Publisher not found")
    if not db.scalar(select(g.GrowthBlockedPublisher).where(g.GrowthBlockedPublisher.publisher_org_id == pid)):
        db.add(g.GrowthBlockedPublisher(organisation_id=ctx.org_id, publisher_org_id=pid, domain=", ".join(doms[:5]), created_by=ctx.user_id))
    for it in db.scalars(select(g.GrowthCartItem).where(g.GrowthCartItem.listing_id.in_(
            select(m.MarketplaceListing.id).where(m.MarketplaceListing.publisher_org_id == pid)))):
        db.delete(it)
    log(db, ctx, "marketplace.publisher_blocked", f"publisher:{pid}")
    return {"ok": True}


@router.delete("/marketplace/publishers/{pid}/block")
def unblock(pid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit"))):
    b = db.scalar(select(g.GrowthBlockedPublisher).where(g.GrowthBlockedPublisher.publisher_org_id == pid))
    if b:
        db.delete(b)
    return {"ok": True}


@router.get("/marketplace/blocked")
def blocked(db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    return [{**to_dict(b), "publisher": M.publisher_label(b.publisher_org_id)} for b in db.scalars(select(g.GrowthBlockedPublisher))]


# ================================================================== cart & checkout (75)
class CartItemIn(BaseModel):
    listing_id: uuid.UUID
    kind: str = "guest_post"
    target_url: str | None = Field(None, max_length=2000)
    anchor: str | None = Field(None, max_length=200)
    project_id: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=5000)


class CartIn(BaseModel):
    items: list[CartItemIn] = Field(..., min_length=1, max_length=100)


class CartPatch(BaseModel):
    kind: str | None = None
    target_url: str | None = Field(None, max_length=2000)
    anchor: str | None = Field(None, max_length=200)
    project_id: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=5000)


@router.get("/marketplace/cart")
def cart(db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    items = db.scalars(select(g.GrowthCartItem).where(g.GrowthCartItem.user_id == ctx.user_id).order_by(g.GrowthCartItem.created_at)).all()
    out, total = [], Decimal("0")
    for it in items:
        d = to_dict(it)
        if it.kind == "service":
            s = db.get(m.Service, it.service_id)
            try:
                price, tier, addons = cat.price(s.name, it.tier, it.addons or [])
                d.update(title=f"{s.name} — {tier['name']}", price=price, available=True)
            except (ValueError, AttributeError):
                d.update(title="Unavailable service", price=None, available=False)
        else:
            l = M.get_listing_public(it.listing_id, ctx.org_id)
            price = (l or {}).get("guest_post_price" if it.kind == "guest_post" else "link_ins_price")
            d.update(listing=l, title=f"{'Guest post' if it.kind == 'guest_post' else 'Link insertion'} on {l['domain'] if l else '?'}",
                     price=price, available=bool(l and l["status"] == "active" and price is not None and not l["own"]))
        if d["available"] and d["price"]:
            total += money(d["price"])
        out.append(d)
    w = M.ledger(db, ctx.org_id)
    return {"items": out, "total": float(total), "balance": w["balance"], "shortfall": float(max(Decimal("0"), total - money(w["balance"])))}


@router.post("/marketplace/cart")
def add_to_cart(body: CartIn, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    added = []
    for it in body.items:
        if it.kind not in ("guest_post", "link_insertion"):
            raise HTTPException(400, "kind must be guest_post or link_insertion")
        l = M.get_listing_public(it.listing_id, ctx.org_id)
        if not l or l["status"] != "active":
            raise HTTPException(404, f"Listing {it.listing_id} not found")
        if l["own"]:
            raise HTTPException(400, f"{l['domain']} is your own listing")
        if (l["guest_post_price"] if it.kind == "guest_post" else l["link_ins_price"]) is None:
            raise HTTPException(400, f"{l['domain']} does not offer {it.kind.replace('_', ' ')}")
        if it.project_id:
            project_guard(db, ctx, it.project_id)
        if db.scalar(select(g.GrowthCartItem).where(g.GrowthCartItem.user_id == ctx.user_id, g.GrowthCartItem.listing_id == it.listing_id,
                                                    g.GrowthCartItem.kind == it.kind)):
            continue
        c = g.GrowthCartItem(organisation_id=ctx.org_id, user_id=ctx.user_id, listing_id=it.listing_id, kind=it.kind,
                             target_url=check_url(it.target_url, "target_url", False), anchor=it.anchor, project_id=it.project_id, notes=it.notes)
        db.add(c)
        db.flush()
        added.append(str(c.id))
    return {"added": len(added), "ids": added}


@router.patch("/marketplace/cart/{cid}")
def patch_cart(cid: uuid.UUID, body: CartPatch, db=Depends(get_db), ctx: Ctx = Depends(require("create"))):
    it = db.get(g.GrowthCartItem, cid)
    if not it or it.user_id != ctx.user_id:
        raise HTTPException(404)
    data = body.model_dump(exclude_unset=True)
    if "kind" in data and it.kind != "service":
        if data["kind"] not in ("guest_post", "link_insertion"):
            raise HTTPException(400, "kind must be guest_post or link_insertion")
        it.kind = data["kind"]
    if "target_url" in data:
        it.target_url = check_url(data["target_url"], "target_url", False)
    if data.get("project_id"):
        project_guard(db, ctx, data["project_id"])
        it.project_id = data["project_id"]
    for k in ("anchor", "notes"):
        if k in data:
            setattr(it, k, data[k])
    return to_dict(it)


@router.delete("/marketplace/cart/{cid}")
def del_cart(cid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    it = db.get(g.GrowthCartItem, cid)
    if not it or it.user_id != ctx.user_id:
        raise HTTPException(404)
    db.delete(it)
    return {"ok": True}


class CheckoutIn(BaseModel):
    item_ids: list[uuid.UUID] | None = None


@router.post("/marketplace/checkout")
def do_checkout(body: CheckoutIn | None = None, db=Depends(get_db), ctx: Ctx = Depends(require("create")), _c=Depends(deny_client)):
    _money_role(ctx)
    q = select(g.GrowthCartItem).where(g.GrowthCartItem.user_id == ctx.user_id)
    if body and body.item_ids:
        q = q.where(g.GrowthCartItem.id.in_(body.item_ids))
    items = db.scalars(q.order_by(g.GrowthCartItem.created_at)).all()
    orders = M.checkout(db, ctx.org_id, ctx.user_id, items)
    log(db, ctx, "marketplace.checkout", None, {"orders": [o["number"] for o in orders], "total": sum(o["amount"] for o in orders)})
    return {"orders": orders, "wallet": M.ledger(db, ctx.org_id)}


# ================================================================== orders (75)
def _side(db, oid) -> str | None:
    if db.scalar(select(g.GrowthOrder.id).where(g.GrowthOrder.order_id == oid)):
        return "advertiser"
    if db.scalar(select(g.GrowthPublisherOrder.id).where(g.GrowthPublisherOrder.order_id == oid)):
        return "publisher"
    return None


def _order_detail(db, oid) -> dict:
    side = _side(db, oid)
    if side == "advertiser":
        o = db.get(m.Order, oid)
        go = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == oid))
        esc = db.scalar(select(g.GrowthEscrow).where(g.GrowthEscrow.order_id == oid))
        return M.adv_order_dict(o, go, esc)
    if side == "publisher":
        return M.pub_order_dict(db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.order_id == oid)))
    raise HTTPException(404, "Order not found")


@router.get("/marketplace/orders")
def orders(side: str = "buying", status: str | None = None, project_id: uuid.UUID | None = None,
           db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    if side not in ("buying", "selling"):
        raise HTTPException(400, "side must be buying or selling")
    if side == "buying":
        q = select(m.Order, g.GrowthOrder, g.GrowthEscrow).join(g.GrowthOrder, g.GrowthOrder.order_id == m.Order.id) \
            .outerjoin(g.GrowthEscrow, g.GrowthEscrow.order_id == m.Order.id)
        if ctx.role == "client":  # clients only see orders on their projects
            from ..deps import visible_project_ids
            q = q.where(g.GrowthOrder.project_id.in_(visible_project_ids(db, ctx)))
        if status:
            q = q.where(m.Order.status == status)
        if project_id:
            q = q.where(g.GrowthOrder.project_id == project_id)
        items = [M.adv_order_dict(o, go, e) for o, go, e in db.execute(q.order_by(desc(g.GrowthOrder.placed_at)).limit(500))]
    else:
        if ctx.role == "client":
            raise HTTPException(403, "Not available to client users")
        q = select(g.GrowthPublisherOrder)
        if status:
            q = q.where(g.GrowthPublisherOrder.status == status)
        items = [M.pub_order_dict(p) for p in db.scalars(q.order_by(desc(g.GrowthPublisherOrder.placed_at)).limit(500))]
    counts: dict = {}
    for i in items:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    return {"items": items, "counts": counts}


@router.get("/marketplace/orders/{oid}")
def order(oid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view"))):
    d = _order_detail(db, oid)
    if ctx.role == "client" and (d["side"] == "publisher" or not d.get("project_id")):
        raise HTTPException(404, "Order not found")
    if ctx.role == "client":
        project_guard(db, ctx, uuid.UUID(d["project_id"]))
    return d


class ActionIn(BaseModel):
    action: str
    note: str | None = Field(None, max_length=3000)
    live_url: str | None = Field(None, max_length=2000)


@router.post("/marketplace/orders/{oid}/action")
def order_action(oid: uuid.UUID, body: ActionIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    side = _side(db, oid)
    if not side:
        raise HTTPException(404, "Order not found")
    if body.action in ("complete", "cancel", "dispute"):
        _money_role(ctx)
    live = check_url(body.live_url, "live_url") if body.action == "deliver" else None
    res = M.transition(db, ctx.org_id, side, oid, body.action, note=body.note, live_url=live, user_id=ctx.user_id)
    if body.action == "deliver":
        enqueue(db, ctx.org_id, "growth_order_verify", {"order_id": str(oid)}, created_by=ctx.user_id)
    log(db, ctx, f"marketplace.order_{body.action}", f"order:{oid}", {"note": body.note})
    db.flush()
    return {**res, "order": _order_detail(db, oid)}


@router.post("/marketplace/orders/{oid}/verify")
def order_verify(oid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    d = _order_detail(db, oid)
    if not d.get("live_url") or d["status"] not in ("delivered", "disputed"):
        raise HTTPException(409, "Only delivered orders with a live URL can be verified")
    j = enqueue(db, ctx.org_id, "growth_order_verify", {"order_id": str(oid)}, created_by=ctx.user_id)
    return {"job_id": str(j.id)}


class BriefIn(BaseModel):
    brief: str | None = Field(None, max_length=10000)
    anchor: str | None = Field(None, max_length=200)
    target_url: str | None = Field(None, max_length=2000)


@router.post("/marketplace/orders/{oid}/brief")
def order_brief(oid: uuid.UUID, body: BriefIn, db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    go = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == oid))
    if not go:
        raise HTTPException(404, "Order not found")
    o = db.get(m.Order, oid)
    if o.status not in ("pending", "accepted", "in_progress"):
        raise HTTPException(409, "The brief can only change before delivery")
    tgt = check_url(body.target_url, "target_url", False)
    for k, v in (("brief", body.brief), ("anchor", body.anchor), ("target_url", tgt)):
        if v is not None:
            setattr(go, k, v)
    go.timeline = go.timeline + [M._event("brief_updated", "advertiser")]
    vals = {"brief": go.brief, "anchor": go.anchor, "target_url": go.target_url}
    pub = go.publisher_org_id
    if pub:
        with M.as_org(db, pub):
            po = db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.order_id == oid))
            for k, v in vals.items():
                setattr(po, k, v)
            po.timeline = po.timeline + [M._event("brief_updated", "advertiser")]
    return _order_detail(db, oid)


@router.post("/marketplace/orders/{oid}/attachments")
async def order_attach(oid: uuid.UUID, file: UploadFile = File(...), db=Depends(get_db), ctx: Ctx = Depends(require("edit")), _c=Depends(deny_client)):
    go = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == oid))
    if not go:
        raise HTTPException(404, "Order not found")
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "Attachments must be under 10 MB")
    name = (file.filename or "attachment").replace("/", "_")[-120:]
    f = reports.store(db, ctx.org_id, name, raw, file.content_type or "application/octet-stream", ctx.user_id, kind="upload")
    meta = {"id": str(f.id), "filename": name, "size": len(raw)}
    go.attachments = go.attachments + [meta]
    if go.publisher_org_id:
        with M.as_org(db, go.publisher_org_id):
            po = db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.order_id == oid))
            po.attachments = po.attachments + [meta]
    return meta


@router.get("/marketplace/orders/{oid}/attachments/{fid}")
def order_attachment(oid: uuid.UUID, fid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    side = _side(db, oid)
    row = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == oid)) if side == "advertiser" else \
        db.scalar(select(g.GrowthPublisherOrder).where(g.GrowthPublisherOrder.order_id == oid)) if side == "publisher" else None
    if not row or str(fid) not in {a["id"] for a in row.attachments}:
        raise HTTPException(404)
    if side == "advertiser":
        f = db.get(m.StoredFile, fid)
    else:
        with M.as_org(db, row.advertiser_org_id):  # file belongs to the advertiser; access granted by the order link
            f = db.get(m.StoredFile, fid)
            fname, ctype, path = f.filename, f.content_type, reports.path_of(f)
        return Response(path.read_bytes(), media_type=ctype or "application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    return Response(reports.path_of(f).read_bytes(), media_type=f.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{f.filename}"'})


# ================================================================== wallet (76)
class TopupIn(BaseModel):
    amount: float = Field(..., ge=10, le=50_000)


class AdjustIn(BaseModel):
    amount: float = Field(..., ge=-100_000, le=100_000)
    reason: str = Field(..., min_length=5, max_length=500)
    reference: str | None = Field(None, max_length=200)


class PayoutIn(BaseModel):
    amount: float = Field(..., ge=10, le=1_000_000)
    destination: str = Field(..., min_length=3, max_length=300)


class DecideIn(BaseModel):
    decision: str
    notes: str | None = Field(None, max_length=1000)


def _adj_dict(a: g.GrowthWalletAdjustment) -> dict:
    return to_dict(a)


@router.get("/wallet")
def wallet(request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    M.reconcile_payouts(db)
    try:
        M.stripe_secret(db, ctx.org_id)
        stripe = True
    except NotConnected:
        stripe = False
    return {**M.ledger(db, ctx.org_id), "transactions": M.txn_rows(db, 200),
            "stripe_connected": stripe,
            "stripe_message": None if stripe else "Payment gateway (Stripe) is NOT CONNECTED — card top-ups are unavailable. Funds can be added by a manual adjustment approved by a Genranq platform administrator.",
            "topups": [to_dict(t) for t in db.scalars(select(g.GrowthTopup).order_by(desc(g.GrowthTopup.created_at)).limit(50))],
            "adjustments": [_adj_dict(a) for a in db.scalars(select(g.GrowthWalletAdjustment).order_by(desc(g.GrowthWalletAdjustment.created_at)).limit(50))],
            "payouts": [M.payout_dict(p) for p in db.scalars(select(g.GrowthPayout).order_by(desc(g.GrowthPayout.created_at)).limit(50))],
            "invoices": [to_dict(i, exclude=("lines",)) for i in db.scalars(select(g.GrowthInvoice).order_by(desc(g.GrowthInvoice.created_at)).limit(100))],
            "escrow": [{**to_dict(e), "number": M.order_number(e.order_id)} for e in db.scalars(select(g.GrowthEscrow).where(g.GrowthEscrow.status == "held"))],
            "can_spend": ctx.role in ("owner", "admin", "manager"), "is_owner": ctx.role == "owner", "platform_admin": ctx.platform_admin}


@router.get("/wallet/export.csv")
def wallet_csv(db=Depends(get_db), ctx: Ctx = Depends(require("export")), _c=Depends(deny_client)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "type", "amount", "balance_after", "reason", "reference"])
    for t in reversed(M.txn_rows(db, 10000)):
        w.writerow([t["created_at"], t["type"], t["amount"], t["balance_after"], t["reason"], t["ref"] or ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=wallet.csv"})


@router.post("/wallet/topups")
def topup(body: TopupIn, request: Request, db=Depends(get_db), ctx: Ctx = Depends(require("manage_billing"))):
    from .growth_api_leads import base_url
    b = base_url(request)
    try:
        t = M.create_checkout(db, ctx.org_id, ctx.user_id, body.amount, f"{b}/wallet?topup=success", f"{b}/wallet?topup=cancelled")
    except NotConnected as e:
        raise HTTPException(409, detail={"code": "not_connected", "provider": "stripe",
                                         "message": f"Payment gateway (Stripe) is NOT CONNECTED — no payment was taken. ({e})"})
    log(db, ctx, "wallet.topup_started", f"topup:{t.id}", {"amount": body.amount})
    return {"id": str(t.id), "checkout_url": t.checkout_url, "status": t.status}


@router.post("/wallet/adjustments")
def request_adjustment(body: AdjustIn, db=Depends(get_db), ctx: Ctx = Depends(get_ctx)):
    if ctx.role != "owner" and not ctx.platform_admin:
        raise HTTPException(403, "Only the organisation owner (or a platform administrator) can request a manual adjustment")
    if money(body.amount) == 0:
        raise HTTPException(400, "Amount must not be zero")
    u = db.get(m.User, ctx.user_id)
    a = g.GrowthWalletAdjustment(organisation_id=ctx.org_id, amount=money(body.amount), reason=body.reason, reference=body.reference,
                                 requested_by=ctx.user_id, requested_by_email=u.email)
    db.add(a)
    db.flush()
    log(db, ctx, "wallet.adjustment_requested", f"adjustment:{a.id}", {"amount": body.amount, "reason": body.reason})
    return {**_adj_dict(a), "message": "Awaiting approval by a Genranq platform administrator. No funds were added yet."}


@router.post("/wallet/payouts")
def request_payout(body: PayoutIn, db=Depends(get_db), ctx: Ctx = Depends(require("manage_billing"))):
    hold = M.post(db, ctx.org_id, "payout", -money(body.amount), f"Payout request held: {body.destination}", user_id=ctx.user_id)
    p = g.GrowthPayout(organisation_id=ctx.org_id, amount=money(body.amount), destination=body.destination, requested_by=ctx.user_id,
                       hold_txn_id=hold.id)
    db.add(p)
    db.flush()
    a = m.Approval(organisation_id=ctx.org_id, entity_type="wallet_payout", entity_id=p.id, title=f"Payout of {money(body.amount)} USD",
                   chain=["owner"], requested_by=ctx.user_id, notes=body.destination)
    db.add(a)
    db.flush()
    p.approval_id = a.id
    db.add(m.Notification(organisation_id=ctx.org_id, kind="approval_requested", title=f"Approval requested: payout of {money(body.amount)} USD",
                          body="Awaiting owner", link="/wallet"))
    log(db, ctx, "wallet.payout_requested", f"payout:{p.id}", {"amount": body.amount})
    return M.payout_dict(p)


@router.post("/wallet/payouts/{pid}/decide")
def decide_payout(pid: uuid.UUID, body: DecideIn, db=Depends(get_db), ctx: Ctx = Depends(require("approve"))):
    from ..security import now
    p = db.get(g.GrowthPayout, pid)
    if not p or p.status != "pending_approval":
        raise HTTPException(404, "No pending payout")
    if RANK[ctx.role] < RANK["owner"]:
        raise HTTPException(403, "Payouts need an owner's approval")
    if p.requested_by == ctx.user_id:
        raise HTTPException(403, "A payout must be approved by someone other than the requester")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve or reject")
    if body.decision == "reject" and not body.notes:
        raise HTTPException(400, "Rejection requires notes")
    a = db.get(m.Approval, p.approval_id)
    a.decisions = a.decisions + [{"by": str(ctx.user_id), "role": ctx.role, "decision": body.decision, "notes": body.notes,
                                  "at": now().isoformat(), "step": a.step}]
    a.status = "approved" if body.decision == "approve" else "rejected"
    a.step = len(a.chain) if body.decision == "approve" else a.step
    M.reconcile_payouts(db)
    log(db, ctx, f"wallet.payout_{body.decision}", f"payout:{p.id}", {"notes": body.notes})
    return M.payout_dict(p)


@router.get("/wallet/invoices/{iid}/pdf")
def invoice_pdf(iid: uuid.UUID, db=Depends(get_db), ctx: Ctx = Depends(require("view")), _c=Depends(deny_client)):
    inv = db.get(g.GrowthInvoice, iid)
    if not inv:
        raise HTTPException(404)
    if inv.stored_file_id:
        f = db.get(m.StoredFile, inv.stored_file_id)
        data = reports.path_of(f).read_bytes()
    else:
        data = M.invoice_pdf(db, inv)
        f = reports.store(db, ctx.org_id, f"{inv.number}.pdf", data, "application/pdf", ctx.user_id)
        inv.stored_file_id = f.id
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{inv.number}.pdf"'})


# ================================================================== platform operator (Genranq staff)
class AdminAdjustIn(AdjustIn):
    organisation_id: uuid.UUID


def _admin_find(model, rid):
    with system_session() as sdb:
        row = sdb.get(model, rid)
        return row.organisation_id if row else None


@router.get("/admin/growth/adjustments")
def admin_adjustments(status: str | None = "pending", ctx: Ctx = Depends(require_platform_admin)):
    with system_session() as sdb:
        q = select(g.GrowthWalletAdjustment, m.Organisation.name).join(m.Organisation, m.Organisation.id == g.GrowthWalletAdjustment.organisation_id)
        if status:
            q = q.where(g.GrowthWalletAdjustment.status == status)
        return [{**to_dict(a), "organisation": n, "can_decide": a.requested_by != ctx.user_id}
                for a, n in sdb.execute(q.order_by(desc(g.GrowthWalletAdjustment.created_at)).limit(300))]


@router.post("/admin/growth/adjustments")
def admin_request_adjustment(body: AdminAdjustIn, ctx: Ctx = Depends(require_platform_admin)):
    if money(body.amount) == 0:
        raise HTTPException(400, "Amount must not be zero")
    with system_session() as sdb:
        if not sdb.get(m.Organisation, body.organisation_id):
            raise HTTPException(404, "Organisation not found")
        email = sdb.get(m.User, ctx.user_id).email
    with tenant_session(body.organisation_id) as db:
        a = g.GrowthWalletAdjustment(organisation_id=body.organisation_id, amount=money(body.amount), reason=body.reason,
                                     reference=body.reference, requested_by=ctx.user_id, requested_by_email=f"{email} (platform)")
        db.add(a)
        db.add(m.ActivityLog(organisation_id=body.organisation_id, actor_id=ctx.user_id, action="wallet.adjustment_requested",
                             target="platform_admin", meta={"amount": body.amount, "reason": body.reason}))
        db.flush()
        return to_dict(a)


@router.post("/admin/growth/adjustments/{aid}/decide")
def admin_decide_adjustment(aid: uuid.UUID, body: DecideIn, ctx: Ctx = Depends(require_platform_admin)):
    org = _admin_find(g.GrowthWalletAdjustment, aid)
    if not org:
        raise HTTPException(404)
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve or reject")
    with system_session() as sdb:
        email = sdb.get(m.User, ctx.user_id).email
    with tenant_session(org) as db:
        a = db.scalar(select(g.GrowthWalletAdjustment).where(g.GrowthWalletAdjustment.id == aid).with_for_update())
        if a.status != "pending":
            raise HTTPException(409, "Adjustment already decided")
        if a.requested_by == ctx.user_id:
            raise HTTPException(403, "A manual adjustment must be approved by a different platform administrator")
        if body.decision == "reject":
            if not body.notes:
                raise HTTPException(400, "Rejection requires notes")
            a.status, a.decided_by, a.decided_by_email, a.decision_notes, a.decided_at = "rejected", ctx.user_id, email, body.notes, utcnow()
        else:
            M.apply_adjustment(db, a, ctx.user_id, email, body.notes)
        db.add(m.ActivityLog(organisation_id=org, actor_id=ctx.user_id, action=f"wallet.adjustment_{body.decision}",
                             target=f"adjustment:{aid}", meta={"amount": float(a.amount), "notes": body.notes}))
        db.flush()
        return to_dict(a)


@router.get("/admin/growth/orders")
def admin_orders(ctx: Ctx = Depends(require_platform_admin)):
    with system_session() as sdb:
        q = select(m.Order, g.GrowthOrder, m.Organisation.name).join(g.GrowthOrder, g.GrowthOrder.order_id == m.Order.id) \
            .join(m.Organisation, m.Organisation.id == m.Order.organisation_id) \
            .where((g.GrowthOrder.kind == "service") | (m.Order.status == "disputed"))
        return [{**M.adv_order_dict(o, go), "organisation": n, "organisation_id": str(o.organisation_id)}
                for o, go, n in sdb.execute(q.order_by(desc(g.GrowthOrder.placed_at)).limit(500))]


@router.post("/admin/growth/orders/{oid}/action")
def admin_order_action(oid: uuid.UUID, body: ActionIn, ctx: Ctx = Depends(require_platform_admin)):
    org = _admin_find(m.Order, oid)
    if not org:
        raise HTTPException(404)
    with tenant_session(org) as db:
        live = check_url(body.live_url, "live_url") if body.action == "deliver" else None
        res = M.transition(db, org, "platform", oid, body.action, note=body.note, live_url=live, user_id=ctx.user_id)
        if body.action == "deliver":
            go = db.scalar(select(g.GrowthOrder).where(g.GrowthOrder.order_id == oid))
            if go.kind == "service":  # deliverable reviewed by the client; no link to crawl unless a target is set
                if go.target_url:
                    enqueue(db, org, "growth_order_verify", {"order_id": str(oid)})
                else:
                    go.verified, go.verification = True, {"verified": True, "note": "Service deliverable — review and complete"}
        db.add(m.ActivityLog(organisation_id=org, actor_id=ctx.user_id, action=f"marketplace.order_{body.action}",
                             target=f"order:{oid}", meta={"by": "platform", "note": body.note}))
        return res


@router.get("/admin/growth/payouts")
def admin_payouts(ctx: Ctx = Depends(require_platform_admin)):
    with system_session() as sdb:
        q = select(g.GrowthPayout, m.Organisation.name).join(m.Organisation, m.Organisation.id == g.GrowthPayout.organisation_id)
        return [{**M.payout_dict(p), "organisation": n} for p, n in sdb.execute(q.order_by(desc(g.GrowthPayout.created_at)).limit(300))]


class PaidIn(BaseModel):
    reference: str = Field(..., min_length=3, max_length=200)


@router.post("/admin/growth/payouts/{pid}/mark-paid")
def admin_mark_paid(pid: uuid.UUID, body: PaidIn, ctx: Ctx = Depends(require_platform_admin)):
    org = _admin_find(g.GrowthPayout, pid)
    if not org:
        raise HTTPException(404)
    with tenant_session(org) as db:
        M.reconcile_payouts(db)
        p = db.get(g.GrowthPayout, pid)
        if p.status != "approved":
            raise HTTPException(409, "Only owner-approved payouts can be marked paid")
        p.status, p.paid_reference = "paid", body.reference
        M.create_invoice(db, org, "payout", [{"description": f"Payout to {p.destination} (ref {body.reference})", "qty": 1,
                                              "amount": float(p.amount)}], "payout", p.id)
        db.add(m.ActivityLog(organisation_id=org, actor_id=ctx.user_id, action="wallet.payout_paid", target=f"payout:{pid}",
                             meta={"reference": body.reference}))
        return M.payout_dict(p)


@router.get("/admin/growth/reports")
def admin_reports(ctx: Ctx = Depends(require_platform_admin)):
    with system_session() as sdb:
        return [{**to_dict(r), "publisher": M.publisher_label(r.publisher_org_id)}
                for r in sdb.scalars(select(g.GrowthPublisherReport).order_by(desc(g.GrowthPublisherReport.created_at)).limit(300))]
