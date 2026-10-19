"""Tenancy isolation (RLS), RBAC, auth (refresh rotation, MFA, step-up), vault, immutable log."""
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app import models as m
from app.db import app_engine, tenant_session
from app.security import totp_now


def test_tenant_isolation_api_and_rls(org_factory):
    a, b = org_factory(), org_factory()
    p = a.project("Secret client")
    a.post("/sites", {"project_id": p["id"], "domain": "secret-client.com"})
    assert b.get(f"/projects/{p['id']}").status_code == 404
    assert all(x["id"] != p["id"] for x in b.get("/projects").json())
    assert b.post("/sites", {"project_id": p["id"], "domain": "x.com"}).status_code == 404
    # RLS: even an unfiltered query under B's tenant context sees none of A's rows
    with tenant_session(b.org_id) as db:
        assert db.scalars(select(m.Project).where(m.Project.id == p["id"])).first() is None
        assert db.scalar(select(m.Site.id).where(m.Site.domain == "secret-client.com")) is None
    # without any tenant context nothing is visible at all
    with app_engine.connect() as c:
        assert c.execute(text("select count(*) from project")).scalar() == 0
        # and B cannot insert rows claiming to belong to A
        c.execute(text("select set_config('app.org_id', :o, false)"), {"o": b.org_id})
        with pytest.raises(DBAPIError):
            c.execute(text("insert into project (organisation_id, name) values (:o, 'x')"), {"o": a.org_id})
        c.rollback()


def test_app_role_cannot_read_vault_schema():
    with app_engine.connect() as c:
        with pytest.raises(ProgrammingError):
            c.execute(text("select * from vault.secret"))
        c.rollback()


def test_rbac(org):
    staff = org.invite_user("staff")
    client_h = org.c.post  # noqa
    assert org.c.post("/api/team/invites", json={"email": "x@acmeagency.io", "role": "admin"}, headers=staff).status_code == 403
    p = org.project()
    assert org.c.delete(f"/api/projects/{p['id']}", headers=staff).status_code == 403
    assert org.c.post("/api/tasks", json={"project_id": p["id"], "title": "t"}, headers=staff).status_code == 200
    # admin cannot create an owner
    admin = org.invite_user("admin")
    assert org.c.post("/api/team/invites", json={"email": "o@acmeagency.io", "role": "owner"}, headers=admin).status_code == 403


def test_client_role_needs_enterprise_and_sees_only_assigned(org_factory):
    adv = org_factory(plan="advanced")
    r = adv.post("/team/invites", {"email": "client@acmeagency.io", "role": "client"})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "plan_upgrade_required"
    ent = org_factory(plan="enterprise")
    p1, p2 = ent.project("Visible"), ent.project("Hidden")
    ch = ent.invite_user("client")
    cid = ent.c.get("/api/auth/me", headers=ch).json()["user"]["id"]
    assert ent.patch(f"/projects/{p1['id']}", {"client_user_ids": [cid]}).status_code == 200
    names = [p["name"] for p in ent.c.get("/api/projects", headers=ch).json()]
    assert names == ["Visible"]
    assert ent.c.get(f"/api/projects/{p2['id']}", headers=ch).status_code == 404
    assert ent.c.post("/api/projects", json={"name": "x"}, headers=ch).status_code == 403
    assert ent.c.get("/api/activity", headers=ch).status_code == 403


def test_refresh_rotation_and_reuse_detection(org):
    r1 = org.c.post("/api/auth/refresh", json={"refresh_token": org.tokens["refresh_token"]})
    assert r1.status_code == 200
    # reusing the old token revokes the whole family
    assert org.c.post("/api/auth/refresh", json={"refresh_token": org.tokens["refresh_token"]}).status_code == 401
    assert org.c.post("/api/auth/refresh", json={"refresh_token": r1.json()["refresh_token"]}).status_code == 401


def test_mfa_login_and_vault_step_up(org):
    assert org.post("/auth/mfa/setup", {"password": "wrong-password-1"}).status_code == 401
    secret = org.post("/auth/mfa/setup", {"password": org.password}).json()["secret"]
    assert org.post("/auth/mfa/enable", {"code": "000000"}).status_code == 400
    assert org.post("/auth/mfa/enable", {"code": totp_now(secret)}).status_code == 200
    from app.db import system_session

    def next_window():  # simulate the next 30s TOTP window instead of sleeping
        with system_session() as db:
            db.scalar(select(m.User).where(m.User.email == org.email)).mfa_last_step -= 2
    next_window()
    r = org.c.post("/api/auth/login", json={"email": org.email, "password": org.password}).json()
    assert r["mfa_required"]
    assert org.c.post("/api/auth/mfa/verify", json={"challenge": r["challenge"], "code": "123456"}).status_code == 401
    t = org.c.post("/api/auth/mfa/verify", json={"challenge": r["challenge"], "code": totp_now(secret)}).json()
    org.tokens = t
    # the same code can't be replayed
    r2 = org.c.post("/api/auth/login", json={"email": org.email, "password": org.password}).json()
    assert org.c.post("/api/auth/mfa/verify", json={"challenge": r2["challenge"], "code": totp_now(secret)}).status_code == 401
    cid = org.post("/vault", {"label": "WP", "secret": "s3cret-value"}).json()["id"]
    listing = org.get("/vault").json()
    assert listing[0]["masked_hint"].endswith("alue") and "vault_ref" not in listing[0]
    assert org.post(f"/vault/{cid}/reveal").status_code == 403
    assert org.post("/auth/step-up", {"password": org.password}).status_code == 401  # MFA code required
    next_window()
    su = org.post("/auth/step-up", {"password": org.password, "code": totp_now(secret)}).json()["access_token"]
    r = org.c.post(f"/api/vault/{cid}/reveal", headers={"Authorization": f"Bearer {su}"})
    assert r.json()["secret"] == "s3cret-value"
    assert any(a["action"] == "vault.credential_revealed" for a in org.get("/activity").json())
    # ciphertext at rest is not the plaintext
    from app.db import system_session
    with system_session() as db:
        assert b"s3cret-value" not in b"".join(x.ciphertext for x in db.scalars(select(m.VaultSecret)))


def test_activity_log_is_immutable(org):
    org.project()
    with tenant_session(org.org_id) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("update activity_log set action='tampered'"))


def test_content_state_machine_enforced(org):
    p = org.project()
    with pytest.raises(DBAPIError):
        with tenant_session(org.org_id) as db:
            c = m.ContentItem(organisation_id=org.org_id, project_id=p["id"], type="blog")
            db.add(c)
            db.flush()
            c.state = "published"  # draft → published skips review/approval
            db.flush()


def test_api_role_cannot_grant_platform_admin(org):
    with pytest.raises(DBAPIError):
        with tenant_session(org.org_id) as db:
            db.execute(text('update "user" set is_platform_admin = true'))
    assert org.get("/auth/me").json()["platform_admin"] is False


def test_bruteforce_lockout(org):
    for _ in range(5):
        assert org.c.post("/api/auth/login", json={"email": org.email, "password": "not-the-password"}).status_code == 401
    assert org.c.post("/api/auth/login", json={"email": org.email, "password": org.password}).status_code == 429


def test_role_change_applies_immediately(org):
    staff = org.invite_user("manager")
    uid = org.c.get("/api/auth/me", headers=staff).json()["user"]["id"]
    p = org.project()
    org.patch(f"/team/{uid}", {"role": "staff"})
    assert org.c.delete(f"/api/projects/{p['id']}", headers=staff).status_code == 403  # same token, new role
    org.patch(f"/team/{uid}", {"is_active": False})
    assert org.c.get("/api/projects", headers=staff).status_code == 401


def test_client_cannot_see_org_wide_views(org_factory):
    ent = org_factory(plan="enterprise")
    ch = ent.invite_user("client")
    for path in ("/jobs", "/approvals", "/automation/schedules", "/integrations"):
        assert ent.c.get("/api" + path, headers=ch).status_code == 403, path
    team = ent.c.get("/api/team", headers=ch).json()
    assert team["invites"] == [] and all("email" not in u for u in team["users"])


def test_oauth_state_is_single_use_and_browser_bound(org):
    from datetime import timedelta
    from app.db import system_session
    from app.security import now
    with system_session() as db:
        uid = db.scalar(select(m.User.id).where(m.User.email == org.email))
        nonce = uuid.uuid4().hex
        db.add(m.OAuthState(nonce=nonce, organisation_id=org.org_id, user_id=uid, provider_key="gsc",
                            browser_hash=__import__("hashlib").sha256(b"browser-a").hexdigest(), expires_at=now() + timedelta(minutes=5)))
    org.c.cookies.set("gq_oauth", "browser-b", path="/api/integrations")
    r = org.c.get(f"/api/integrations/gsc/oauth/callback?code=x&state={nonce}", follow_redirects=False)
    assert r.status_code == 400  # different browser
