"""Backlink CRM (module 13): CRUD + classification, supplier CRM, verification job against fixture pages,
removed-link notification, toxic import + disavow approval gating + file output, files isolation, KPI math,
CSV import/export, bulk actions and cross-organisation isolation."""
import io
import uuid

import pytest

from app.worker import run_once
from tests.conftest import Org

TARGET = "/services/drains.html"


@pytest.fixture
def org(client):
    return Org(client, plan="enterprise")  # client-user access is an Enterprise feature


def _project(org, site_server, name="Acme Plumbing"):
    p = org.project(name)
    r = org.post("/sites", {"project_id": p["id"], "domain": site_server})
    assert r.status_code == 200, r.text
    return p["id"]


def _bl(org, pid, src, tgt, **kw):
    r = org.post("/backlinks", {"project_id": pid, "source_url": src, "target_url": tgt, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _upload(org, path, content, ctype="application/pdf", headers=None, **data):
    return org.c.post("/api/" + path.lstrip("/"), headers=headers or org.h, files={"file": ("invoice #12.pdf", content, ctype)},
                      data={k: str(v) for k, v in data.items()})


# ------------------------------------------------------------------ CRM + classification
def test_backlink_crud_classification_and_validation(org, site_server):
    pid = _project(org, site_server)
    tgt = site_server + TARGET
    sup = org.post("/suppliers", {"name": "LinkHouse", "email": "sales@linkhouse.test", "rating": 4.5, "niche": "home",
                                  "contact_name": "Ana", "website": "https://linkhouse.test", "payment_terms": "net 30"}).json()
    b = _bl(org, pid, "https://homeblog.example/autumn-tips", tgt, anchor_text="drain unblocking springfield", link_type="dofollow",
            paid_or_free="paid", cost=150, currency="usd", placement_date="2026-09-01", supplier_id=sup["id"], link_scope="client",
            tags=["q3"], verify_frequency="monthly")
    assert b["link_type"] == "dofollow" and b["paid_or_free"] == "paid" and b["cost"] == 150 and b["currency"] == "USD"
    assert b["link_scope"] == "client" and b["placement_date"] == "2026-09-01" and b["supplier"] == "LinkHouse"
    assert b["status"] == "pending" and b["source_domain"] == "homeblog.example" and b["verify_frequency"] == "monthly"
    # a free link defaults to free with no cost; the target site was matched to the project's site
    f = _bl(org, pid, "https://forum.example/t/1", tgt, link_type="ugc")
    assert f["paid_or_free"] == "free" and f["target_site_id"] == b["target_site_id"]
    # target on a new host creates that site in the project
    other = _bl(org, pid, "https://news.example/a", "https://client-two.example/landing", link_scope="site")
    assert other["target_site_id"] != b["target_site_id"]

    # validation
    bad = [{"link_type": "follow"}, {"paid_or_free": "maybe"}, {"link_scope": "x"}, {"currency": "dollars"}, {"cost": -1},
           {"source_url": "not a url"}, {"paid_or_free": "free", "cost": 10}, {"verify_frequency": "hourly"}]
    for extra in bad:
        body = {"project_id": pid, "source_url": "https://x.example/p", "target_url": tgt, **extra}
        assert org.post("/backlinks", body).status_code in (400, 422), extra
    assert org.post("/backlinks", {"project_id": pid, "source_url": tgt, "target_url": tgt}).status_code == 400

    # read / patch / filter
    d = org.get(f"/backlinks/{b['id']}").json()
    assert d["checks"] == [] and d["files"] == []
    r = org.patch(f"/backlinks/{b['id']}", {"anchor_text": "drain unblocking", "link_type": "nofollow", "notes": "renegotiated"})
    assert r.status_code == 200 and r.json()["link_type"] == "nofollow" and r.json()["notes"] == "renegotiated"
    assert org.patch(f"/backlinks/{b['id']}", {"link_type": "weird"}).status_code == 400
    lst = org.get(f"/backlinks?project_id={pid}&link_type=ugc").json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == f["id"]
    assert org.get(f"/backlinks?project_id={pid}&q=homeblog").json()["total"] == 1
    assert org.get(f"/backlinks?project_id={pid}&supplier_id={sup['id']}").json()["total"] == 1

    # bulk: set fields, tag, delete needs delete permission + explicit confirmation
    ids = [b["id"], f["id"]]
    assert org.post("/backlinks/bulk", {"ids": ids, "action": "set_scope", "value": "project"}).json()["updated"] == 2
    assert org.post("/backlinks/bulk", {"ids": ids, "action": "add_tag", "value": "audit-2026"}).json()["updated"] == 2
    assert org.post("/backlinks/bulk", {"ids": ids, "action": "set_supplier", "value": sup["id"]}).json()["updated"] == 2
    assert all(x["link_scope"] == "project" and "audit-2026" in x["tags"] and x["supplier_id"] == sup["id"]
               for x in org.get(f"/backlinks?project_id={pid}").json()["items"] if x["id"] in ids)
    assert org.post("/backlinks/bulk", {"ids": ids, "action": "nope"}).status_code == 400
    staff = org.invite_user("staff")
    r = org.c.post("/api/backlinks/bulk", headers=staff, json={"ids": ids, "action": "delete", "confirm": True})
    assert r.status_code == 403
    r = org.post("/backlinks/bulk", {"ids": ids, "action": "delete"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "confirm_required"
    assert org.post("/backlinks/bulk", {"ids": ids, "action": "delete", "confirm": True}).json()["deleted"] == 2
    assert org.get(f"/backlinks/{b['id']}").status_code == 404
    assert org.c.delete(f"/api/backlinks/{other['id']}", headers=staff).status_code == 403
    assert org.c.delete(f"/api/backlinks/{other['id']}", headers=org.h).json() == {"ok": True}


def test_csv_import_export(org, site_server):
    pid = _project(org, site_server)
    tgt = site_server + TARGET
    csv_text = ("Source URL,Target URL,Anchor,Link type,Paid or free,Price,Currency,Placement date,Supplier,Scope,Notes\n"
                f"https://a.example/post,{tgt},drain unblocking,dofollow,paid,120,usd,2026-08-01,Vendor One,client,first\n"
                f"https://b.example/post,{tgt},plumber,nofollow,free,,,08/15/2026,,project,\n"
                f"https://c.example/post,{tgt},x,follow-ish,,,,,,,\n"
                f"not-a-url,{tgt},,,,,,,,,\n")
    r = org.c.post(f"/api/projects/{pid}/backlinks/import", headers=org.h, files={"file": ("bl.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 2 and res["error_count"] == 2 and res["new_suppliers"] == 1
    assert {e["row"] for e in res["errors"]} == {4, 5}
    items = {x["source_domain"]: x for x in org.get(f"/backlinks?project_id={pid}").json()["items"]}
    assert items["a.example"]["cost"] == 120 and items["a.example"]["supplier"] == "Vendor One" and items["a.example"]["link_scope"] == "client"
    assert items["b.example"]["placement_date"] == "2026-08-15" and items["b.example"]["link_type"] == "nofollow"
    # re-import updates instead of duplicating
    r = org.c.post(f"/api/projects/{pid}/backlinks/import", headers=org.h,
                   files={"file": ("bl.csv", f"source_url,target_url,cost\nhttps://a.example/post,{tgt}/,200\n".encode(), "text/csv")})
    assert r.json()["updated"] == 1 and r.json()["created"] == 0
    exp = org.get(f"/projects/{pid}/backlinks/export.csv")
    assert exp.status_code == 200 and exp.headers["content-type"].startswith("text/csv")
    lines = exp.text.strip().splitlines()
    assert lines[0].startswith("source_url,target_url,anchor_text") and len(lines) == 3
    assert any("https://a.example/post" in ln and ",200.0," in ln and "Vendor One" in ln for ln in lines)
    bad = org.c.post(f"/api/projects/{pid}/backlinks/import", headers=org.h, files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert bad.status_code == 400


# ------------------------------------------------------------------ verification (F-015)
def test_verification_job_against_fixture_pages(org, site_server):
    pid = _project(org, site_server)
    tgt = site_server + TARGET
    base = site_server + "/backlinks/"
    live = _bl(org, pid, base + "dofollow.html", tgt, anchor_text="drain unblocking springfield", link_type="dofollow")
    nof_expected = _bl(org, pid, base + "nofollow.html", tgt, anchor_text="drain unblocking springfield", link_type="nofollow")
    nof_changed = _bl(org, pid, base + "nofollow.html?v=2", tgt, link_type="dofollow")
    spons = _bl(org, pid, base + "sponsored.html", tgt, anchor_text="Acme Plumbing", link_type="sponsored")
    nolink = _bl(org, pid, base + "nolink.html", tgt)
    noindex = _bl(org, pid, base + "noindex.html", tgt)
    gone = _bl(org, pid, base + "gone.html", tgt)
    spa = _bl(org, pid, base + "spa.html", tgt)
    anchor_changed = _bl(org, pid, base + "dofollow.html?x=1", tgt, anchor_text="cheap plumber")
    r = org.post("/backlinks/verify", {"project_id": pid})
    assert r.status_code == 200 and r.json()["queued"] == 9
    run_once()
    items = {x["id"]: x for x in org.get(f"/backlinks?project_id={pid}").json()["items"]}
    st = {k: items[v["id"]]["status"] for k, v in dict(live=live, nof_expected=nof_expected, nof_changed=nof_changed, spons=spons, nolink=nolink,
                                                         noindex=noindex, gone=gone, spa=spa, anchor_changed=anchor_changed).items()}
    assert st == {"live": "live", "nof_expected": "live", "nof_changed": "changed", "spons": "live", "nolink": "removed",
                  "noindex": "noindex", "gone": "removed", "spa": "could_not_verify", "anchor_changed": "changed"}, st

    d = org.get(f"/backlinks/{live['id']}").json()
    c = d["checks"][0]
    assert c["status"] == "live" and c["link_found"] is True and c["rel_found"] == "" and c["http_status"] == 200 and c["is_indexed"] is True
    assert c["anchor_found"] == "drain unblocking springfield" and c["anchor_matches"] is True and "href" in c["evidence"]["snippet"]
    assert d["first_detected"] and d["last_verified"] and d["next_check_at"]
    assert d["status_changes"] == [{"at": c["checked_at"], "from": "pending", "to": "live", "reason": c["reason"]}]
    c = org.get(f"/backlinks/{nof_changed['id']}").json()["checks"][0]
    assert "nofollow" in c["rel_found"] and "recorded as dofollow" in c["reason"]
    c = org.get(f"/backlinks/{noindex['id']}").json()["checks"][0]
    assert c["noindex"] is True and c["is_indexed"] is False and c["link_found"] is True
    c = org.get(f"/backlinks/{gone['id']}").json()["checks"][0]
    assert c["http_status"] == 404 and c["link_found"] is False
    c = org.get(f"/backlinks/{spa['id']}").json()
    assert c["checks"][0]["status"] == "could_not_verify" and "JavaScript" in c["checks"][0]["reason"] and c["last_verified"] is None

    # live → removed: the source page was edited and the link dropped → notification for the project + status history
    before = {n["id"] for n in org.get("/notifications").json()["items"] if n["kind"] == "backlink_removed"}
    assert org.patch(f"/backlinks/{live['id']}", {"source_url": base + "nolink.html?edited=1"}).status_code == 200
    org.post("/backlinks/verify", {"backlink_ids": [live["id"]]})
    run_once()
    d = org.get(f"/backlinks/{live['id']}").json()
    assert d["status"] == "removed"
    assert [(s["from"], s["to"]) for s in d["status_changes"]] == [("live", "removed"), ("pending", "live")]
    notes = [n for n in org.get("/notifications").json()["items"] if n["kind"] == "backlink_removed" and n["id"] not in before]
    assert any("Backlink removed" in n["title"] and live["id"] in (n["link"] or "") for n in notes)
    # a could-not-verify check never overwrites a known status
    org.patch(f"/backlinks/{spons['id']}", {"source_url": base + "spa.html?s=1"})
    org.post("/backlinks/verify", {"backlink_ids": [spons["id"]]})
    run_once()
    d = org.get(f"/backlinks/{spons['id']}").json()
    assert d["status"] == "live" and d["checks"][0]["status"] == "could_not_verify" and "JavaScript" in d["last_check_note"]


def test_scheduled_rechecks(org, site_server):
    from app.db import tenant_session
    from app import models as m, models_backlinks as mb
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    pid = _project(org, site_server)
    b = _bl(org, pid, site_server + "/backlinks/dofollow.html", site_server + TARGET, verify_frequency="daily")
    off = _bl(org, pid, site_server + "/backlinks/nofollow.html", site_server + TARGET, verify_frequency="off")
    with tenant_session(org.org_id) as db:
        sch = db.scalar(select(m.JobSchedule).where(m.JobSchedule.kind == "backlinks_verify_due"))
        assert sch and sch.frequency == "daily"
        sch.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        for meta in db.scalars(select(mb.BacklinkMeta).where(mb.BacklinkMeta.backlink_id.in_([b["id"], off["id"]]))):
            meta.next_check_at = datetime.now(timezone.utc) - timedelta(hours=1)
    run_once()
    run_once()
    assert org.get(f"/backlinks/{b['id']}").json()["status"] == "live"
    d = org.get(f"/backlinks/{off['id']}").json()
    assert d["status"] == "pending" and d["checks"] == []  # frequency "off" is never re-checked automatically
    nxt = datetime.fromisoformat(org.get(f"/backlinks/{b['id']}").json()["next_check_at"])
    assert timedelta(hours=23) < nxt - datetime.now(timezone.utc) <= timedelta(days=1)


# ------------------------------------------------------------------ suppliers (F-014)
def test_supplier_crm(org, site_server):
    pid = _project(org, site_server)
    s = org.post("/suppliers", {"name": "Guest Post Co", "email": "hi@gpc.test", "phone": "+44 20 7946 0000", "country": "UK",
                                "niche": "home improvement", "rating": 4, "contact_name": "Sam", "website": "gpc.test",
                                "payment_method": "PayPal", "tags": ["reliable"]}).json()
    assert org.post("/suppliers", {"name": "x", "email": "nope"}).status_code == 400
    assert org.post("/suppliers", {"name": "x", "rating": 9}).status_code == 422
    r = org.post(f"/suppliers/{s['id']}/prices", {"domain": "https://www.homeblog.example/", "service": "guest_post", "price": 180,
                                                   "currency": "usd", "dr": 45, "link_type": "dofollow", "turnaround_days": 7})
    assert r.status_code == 200 and r.json()["domain"] == "homeblog.example" and r.json()["currency"] == "USD"
    assert org.post(f"/suppliers/{s['id']}/prices", {"domain": "bad domain", "price": 1}).status_code == 400
    assert org.post(f"/suppliers/{s['id']}/prices", {"domain": "a.example", "price": 1, "service": "pbn"}).status_code == 400
    assert org.post(f"/suppliers/{s['id']}/notes", {"kind": "email", "direction": "outbound", "subject": "Order", "body": "Ordered 2 posts"}).status_code == 200
    assert org.post(f"/suppliers/{s['id']}/notes", {"kind": "fax", "body": "x"}).status_code == 400
    _bl(org, pid, "https://homeblog.example/p1", site_server + TARGET, supplier_id=s["id"], cost=180, currency="USD", status="live")
    _bl(org, pid, "https://homeblog.example/p2", site_server + TARGET, supplier_id=s["id"], cost=90, currency="USD", status="removed")
    # credentials are linked through the vault; the supplier view never returns secrets
    cr = org.post("/vault", {"label": "GPC portal", "username": "agency", "secret": "s3cret-pass-123", "supplier_id": s["id"]})
    assert cr.status_code == 200, cr.text
    d = org.get(f"/suppliers/{s['id']}").json()
    assert d["profile"]["contact_name"] == "Sam" and d["profile"]["website"] == "https://gpc.test/" and d["profile"]["tags"] == ["reliable"]
    assert len(d["prices"]) == 1 and d["notes"][0]["subject"] == "Order" and len(d["backlinks"]) == 2
    assert d["stats"] == {"links": 2, "live": 1, "removed": 1, "spend": {"USD": 270.0}, "retention": 0.5}
    assert d["credentials"][0]["label"] == "GPC portal" and d["credentials"][0]["masked_hint"]
    assert "s3cret-pass-123" not in str(d) and "vault_ref" not in str(d)
    staff = org.invite_user("staff")  # staff lacks access_credentials → only a count
    ds = org.c.get(f"/api/suppliers/{s['id']}", headers=staff).json()
    assert ds["credentials"] is None and ds["credential_count"] == 1
    lst = org.get("/suppliers").json()
    row = next(x for x in lst if x["id"] == s["id"])
    assert row["stats"]["links"] == 2 and row["last_contact"] and row["price_rows"] == 1 and row["contact_name"] == "Sam"
    r = org.patch(f"/suppliers/{s['id']}", {"rating": 2.5, "status": "paused", "notes": "slow lately"})
    assert r.status_code == 200 and r.json()["rating"] == 2.5
    assert org.get("/suppliers?status=paused").json()[0]["id"] == s["id"]
    assert org.patch(f"/suppliers/{s['id']}", {"status": "dead"}).status_code == 400
    # client users never see the supplier CRM
    client = org.invite_user("client")
    assert org.c.get("/api/suppliers", headers=client).status_code == 403
    assert org.c.get(f"/api/suppliers/{s['id']}", headers=client).status_code == 403
    # delete needs explicit confirmation; links survive unlinked, credential stays in the vault unlinked
    assert org.c.delete(f"/api/suppliers/{s['id']}", headers=org.h).status_code == 400
    assert org.c.delete(f"/api/suppliers/{s['id']}?confirm=true", headers=org.h).json() == {"ok": True}
    assert all(x["supplier_id"] is None for x in org.get(f"/backlinks?project_id={pid}").json()["items"])
    assert any(v["label"] == "GPC portal" and v["supplier_id"] is None for v in org.get("/vault").json())


# ------------------------------------------------------------------ files (F-017)
def test_files_upload_download_isolation(org, org_factory, site_server):
    pid = _project(org, site_server)
    b = _bl(org, pid, "https://homeblog.example/p1", site_server + TARGET)
    s = org.post("/suppliers", {"name": "Files Co"}).json()
    pdf = b"%PDF-1.4 invoice 12 total 180 USD"
    r = _upload(org, "/backlink-files", pdf, kind="invoice", backlink_id=b["id"], note="Sept invoice")
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert r.json()["filename"] == "invoice_12.pdf"  # sanitised
    r2 = _upload(org, "/backlink-files", b"report", ctype="text/plain", kind="report", supplier_id=s["id"])
    r3 = _upload(org, "/backlink-files", b"monthly", ctype="text/plain", kind="report", project_id=pid)
    assert r2.status_code == 200 and r3.status_code == 200
    assert _upload(org, "/backlink-files", b"x", kind="invoice").status_code == 400  # no attachment target
    assert _upload(org, "/backlink-files", b"x", kind="bogus", project_id=pid).status_code == 400
    assert _upload(org, "/backlink-files", b"<script>", ctype="text/html", kind="other", project_id=pid).status_code == 415
    assert _upload(org, "/backlink-files", b"", kind="invoice", project_id=pid).status_code == 400
    d = org.get(f"/backlinks/{b['id']}").json()
    assert d["files"][0]["id"] == fid and d["files"][0]["note"] == "Sept invoice"
    assert len(org.get(f"/backlink-files?project_id={pid}").json()) == 2  # backlink file + project file
    assert org.get(f"/suppliers/{s['id']}").json()["files"][0]["kind"] == "report"
    dl = org.get(f"/backlink-files/{fid}/download")
    assert dl.status_code == 200 and dl.content == pdf

    # another organisation can neither download nor list nor delete it
    other = org_factory()
    assert other.get(f"/backlink-files/{fid}/download").status_code == 404
    assert other.c.delete(f"/api/backlink-files/{fid}", headers=other.h).status_code == 404
    assert other.get(f"/backlink-files?project_id={pid}").status_code == 404
    assert other.get(f"/backlink-files?backlink_id={b['id']}").status_code == 404
    assert _upload(other, "/backlink-files", b"x", headers=other.h, kind="invoice", backlink_id=b["id"]).status_code == 404
    # a client user not assigned to the project can't download; once assigned they can (but never supplier files)
    client_h = org.invite_user("client")
    assert org.c.get(f"/api/backlink-files/{fid}/download", headers=client_h).status_code == 404
    me = org.c.get("/api/auth/me", headers=client_h).json()["user"]
    assert org.patch(f"/projects/{pid}", {"client_user_ids": [me["id"]]}).status_code == 200
    assert org.c.get(f"/api/backlink-files/{fid}/download", headers=client_h).status_code == 200
    assert org.c.get(f"/api/backlink-files/{r2.json()['id']}/download", headers=client_h).status_code == 404
    # delete
    assert org.c.delete(f"/api/backlink-files/{fid}", headers=org.h).json() == {"ok": True}
    assert org.get(f"/backlink-files/{fid}/download").status_code == 404


# ------------------------------------------------------------------ toxic + disavow (F-016)
def test_toxic_import_and_disavow_approval_gating(org, site_server):
    pid = _project(org, site_server)
    tgt = site_server + TARGET
    spam = _bl(org, pid, "https://spammy-links.example/page1", tgt)
    _bl(org, pid, "https://good-blog.example/post", tgt)
    csv_text = ("Source url,Target url,Anchor,Toxicity Score,Toxic markers\n"
                f"https://spammy-links.example/page1,{tgt},casino,85,Link network\n"
                f"http://pbn-farm.example/x,{tgt},cheap,72,PBN\n"
                f"https://good-blog.example/post,{tgt},plumber,12,\n"
                f"https://bad-row.example/,{tgt},,abc,\n")
    r = org.c.post(f"/api/projects/{pid}/toxic/import", headers=org.h, files={"file": ("audit.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 3 and r.json()["matched_backlinks"] == 2 and r.json()["skipped"] == 1 and "advisory" in r.json()["advisory"]
    assert org.get(f"/backlinks/{spam['id']}").json()["toxicity_score"] == 85
    tox = org.get(f"/projects/{pid}/toxic?min_score=60").json()["items"]
    assert [t["source_domain"] for t in tox] == ["spammy-links.example", "pbn-farm.example"]
    assert org.get(f"/backlinks/dashboard?project_id={pid}").json()["toxic"] == 1
    # nothing is disavowed automatically
    assert org.get(f"/projects/{pid}/disavow").json() == []

    staff = org.invite_user("staff")
    manager = org.invite_user("manager")
    r = org.c.post(f"/api/projects/{pid}/disavow", headers=staff, json={"title": "Q4 cleanup", "toxic_ids": [t["id"] for t in tox],
                                                                         "entries": [{"type": "url", "value": "https://spam2.example/a?b=1"}]})
    assert r.status_code == 200, r.text
    dl = r.json()
    assert dl["status"] == "draft" and dl["domains"] == 2 and dl["urls"] == 1
    assert org.c.post(f"/api/projects/{pid}/disavow", headers=staff, json={"title": "x", "entries": [{"type": "domain", "value": "not a domain"}]}).status_code == 400
    # generating before approval is refused
    g = org.c.post(f"/api/disavow/{dl['id']}/generate", headers=staff)
    assert g.status_code == 409 and g.json()["detail"]["code"] == "approval_required"
    s = org.c.post(f"/api/disavow/{dl['id']}/submit", headers=staff).json()
    assert s["status"] == "pending_approval" and s["approval"]["chain"] == ["manager"]
    assert org.c.post(f"/api/disavow/{dl['id']}/generate", headers=staff).status_code == 409
    assert org.c.patch(f"/api/disavow/{dl['id']}", headers=staff, json={"title": "changed"}).status_code == 409
    aid = s["approval"]["id"]
    assert any(a["id"] == aid and a["entity_type"] == "disavow_list" for a in org.get("/approvals").json())
    # staff can't approve; the manager can
    assert org.c.post(f"/api/approvals/{aid}/decide", headers=staff, json={"decision": "approve"}).status_code == 403
    assert org.c.post(f"/api/approvals/{aid}/decide", headers=manager, json={"decision": "approve"}).status_code == 200
    assert org.c.get(f"/api/disavow/{dl['id']}", headers=staff).json()["status"] == "approved"
    g = org.c.post(f"/api/disavow/{dl['id']}/generate", headers=staff)
    assert g.status_code == 200 and g.json()["status"] == "generated" and g.json()["stored_file_id"]
    f = org.c.get(f"/api/disavow/{dl['id']}/download", headers=staff)
    assert f.status_code == 200 and f.headers["content-type"].startswith("text/plain")
    lines = f.text.splitlines()
    assert "domain:spammy-links.example" in lines and "domain:pbn-farm.example" in lines and "https://spam2.example/a?b=1" in lines
    assert all(ln.startswith(("#", "domain:", "http")) or not ln for ln in lines)
    assert "manager" in f.text  # approver named in the header
    tox = org.get(f"/projects/{pid}/toxic?min_score=60").json()["items"]
    assert all(t["in_disavow"] for t in tox)

    # a rejected list stays un-generatable until edited and re-approved
    r = org.c.post(f"/api/projects/{pid}/disavow", headers=staff, json={"title": "second", "entries": [{"type": "domain", "value": "www.other-spam.example"}]}).json()
    assert r["entries"][0]["value"] == "other-spam.example"
    s = org.c.post(f"/api/disavow/{r['id']}/submit", headers=staff).json()
    assert org.c.post(f"/api/approvals/{s['approval']['id']}/decide", headers=manager, json={"decision": "reject"}).status_code == 400  # notes needed
    assert org.c.post(f"/api/approvals/{s['approval']['id']}/decide", headers=manager, json={"decision": "reject", "notes": "keep"}).status_code == 200
    assert org.c.get(f"/api/disavow/{r['id']}", headers=staff).json()["status"] == "rejected"
    assert org.c.post(f"/api/disavow/{r['id']}/generate", headers=staff).status_code == 409


# ------------------------------------------------------------------ KPIs (F-019)
def test_dashboard_kpi_math(org, site_server):
    pid = _project(org, site_server)
    pid2 = _project(org, site_server, "Second")
    tgt = site_server + TARGET
    a = org.post("/suppliers", {"name": "Alpha"}).json()["id"]
    b = org.post("/suppliers", {"name": "Beta"}).json()["id"]
    _bl(org, pid, "https://s1.example/a", tgt, supplier_id=a, cost=100, currency="USD", status="live")
    _bl(org, pid, "https://s2.example/a", tgt, supplier_id=a, cost=50, currency="USD", status="live")
    _bl(org, pid, "https://s3.example/a", tgt, supplier_id=b, cost=30, currency="USD", status="removed")
    _bl(org, pid, "https://s4.example/a", tgt, status="changed")
    _bl(org, pid, "https://s5.example/a", tgt, status="pending")
    _bl(org, pid2, "https://s6.example/a", tgt, supplier_id=b, cost=80, currency="EUR", status="live")
    k = org.get("/backlinks/dashboard").json()
    assert k["total"] == 6 and k["live"] == 3 and k["removed"] == 1 and k["checked"] == 5
    assert k["live_rate"] == 0.6
    assert k["by_paid"] == {"paid": 4, "free": 2}
    sp = {(x["supplier"], x["currency"]): x for x in k["spend_by_supplier"]}
    assert sp[("Alpha", "USD")] == {"supplier": "Alpha", "currency": "USD", "spend": 150.0, "links": 2, "live_links": 2, "cost_per_live_link": 75.0}
    assert sp[("Beta", "USD")]["cost_per_live_link"] is None and sp[("Beta", "EUR")]["cost_per_live_link"] == 80.0
    cpl = {x["currency"]: x for x in k["cost_per_live_link"]}
    assert cpl["USD"] == {"currency": "USD", "spend": 180.0, "paid_links": 3, "live_paid_links": 2, "cost_per_live_link": 90.0}
    assert cpl["EUR"]["cost_per_live_link"] == 80.0
    lpp = {x["project"]: x for x in k["links_per_project"]}
    assert lpp["Acme Plumbing"]["links"] == 5 and lpp["Acme Plumbing"]["live"] == 2 and lpp["Second"]["links"] == 1
    assert len(k["retention"]) == 12 and k["retention"][-1]["placed"] == 6 and k["retention"][-1]["retention"] is None
    one = org.get(f"/backlinks/dashboard?project_id={pid}").json()
    assert one["total"] == 5 and one["live_rate"] == round(2 / 4, 4)
    # retention uses real checks
    v = _bl(org, pid2, site_server + "/backlinks/dofollow.html", tgt)
    r = _bl(org, pid2, site_server + "/backlinks/nolink.html", tgt)
    org.post("/backlinks/verify", {"backlink_ids": [v["id"], r["id"]]})
    run_once()
    ret = org.get(f"/backlinks/dashboard?project_id={pid2}").json()["retention"][-1]
    assert ret["checked"] == 2 and ret["live"] == 1 and ret["retention"] == 0.5
    # client users don't see agency spend
    client = org.invite_user("client")
    me = org.c.get("/api/auth/me", headers=client).json()["user"]
    org.patch(f"/projects/{pid}", {"client_user_ids": [me["id"]]})
    kc = org.c.get("/api/backlinks/dashboard", headers=client).json()
    assert kc["total"] == 5 and kc["spend_by_supplier"] == [] and kc["cost_per_live_link"] == []
    items = org.c.get(f"/api/backlinks?project_id={pid}", headers=client).json()["items"]
    assert items and all(x["cost"] is None and x["supplier"] is None for x in items)
    assert org.c.get(f"/api/backlinks?project_id={pid2}", headers=client).status_code == 404


# ------------------------------------------------------------------ tenant isolation
def test_cross_org_isolation(org, org_factory, site_server):
    pid = _project(org, site_server)
    b = _bl(org, pid, "https://homeblog.example/p1", site_server + TARGET, cost=10, currency="USD")
    s = org.post("/suppliers", {"name": "Secret Supplier"}).json()
    org.post(f"/suppliers/{s['id']}/notes", {"body": "private"})
    dl = org.post(f"/projects/{pid}/disavow", {"title": "x", "entries": [{"type": "domain", "value": "spam.example"}]}).json()
    rv = org.post(f"/projects/{pid}/relevink", {"target_url": site_server + TARGET, "keyword": "drain unblocking", "mode": "external",
                                               "candidates": [site_server + "/backlinks/garden.html"]}).json()
    pc = org.post(f"/projects/{pid}/page-compare", {"our_url": site_server + "/landing.html", "competitor_urls": [site_server + "/comp/a.html"]}).json()
    run_once()
    o = org_factory()
    opid = o.project("Theirs")["id"]
    assert o.get(f"/backlinks/{b['id']}").status_code == 404
    assert o.patch(f"/backlinks/{b['id']}", {"notes": "x"}).status_code == 404
    assert o.c.delete(f"/api/backlinks/{b['id']}", headers=o.h).status_code == 404
    assert o.get(f"/backlinks?project_id={pid}").status_code == 404
    assert o.get("/backlinks").json()["total"] == 0
    assert o.post("/backlinks", {"project_id": pid, "source_url": "https://a.example/", "target_url": site_server + TARGET}).status_code == 404
    assert o.post("/backlinks/bulk", {"ids": [b["id"]], "action": "set_scope", "value": "client"}).status_code == 404
    assert o.post("/backlinks/verify", {"backlink_ids": [b["id"]]}).status_code == 404
    assert o.post("/backlinks/verify", {"project_id": pid}).status_code == 404
    assert o.get(f"/backlinks/dashboard?project_id={pid}").status_code == 404
    assert o.get("/backlinks/dashboard").json()["total"] == 0
    assert o.get(f"/projects/{pid}/backlinks/export.csv").status_code == 404
    assert o.c.post(f"/api/projects/{pid}/backlinks/import", headers=o.h, files={"file": ("a.csv", b"source_url,target_url\n", "text/csv")}).status_code == 404
    assert o.get("/suppliers").json() == []
    assert o.get(f"/suppliers/{s['id']}").status_code == 404
    assert o.patch(f"/suppliers/{s['id']}", {"name": "pwned"}).status_code == 404
    assert o.post(f"/suppliers/{s['id']}/notes", {"body": "x"}).status_code == 404
    assert o.post(f"/backlinks", {"project_id": opid, "source_url": "https://a.example/", "target_url": "https://t.example/",
                                  "supplier_id": s["id"]}).status_code == 400
    assert o.get(f"/projects/{pid}/toxic").status_code == 404
    assert o.get(f"/projects/{pid}/disavow").status_code == 404
    assert o.get(f"/disavow/{dl['id']}").status_code == 404
    assert o.post(f"/disavow/{dl['id']}/submit").status_code == 404
    assert o.get(f"/relevink/{rv['run_id']}").status_code == 404
    assert o.get(f"/projects/{pid}/relevink").status_code == 404
    assert o.get(f"/page-compare/{pc['comparison_id']}").status_code == 404
    assert o.get(f"/projects/{pid}/page-compare").status_code == 404
    assert o.get(f"/projects/{pid}/providers/ahrefs").status_code == 404
    assert o.get(f"/projects/{pid}/keyword-research").status_code == 404
    assert o.get(f"/projects/{pid}/keyword-research/gap").status_code == 404
    assert o.post(f"/projects/{pid}/keyword-research/track", {"phrases": ["x"]}).status_code == 404
    # their org's dashboard and lists stay empty; ours unchanged
    assert org.get(f"/backlinks/{b['id']}").json()["notes"] is None
    assert org.get(f"/suppliers/{s['id']}").json()["name"] == "Secret Supplier"
    assert uuid.UUID(b["id"])
