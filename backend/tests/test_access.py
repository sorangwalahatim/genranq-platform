"""Per-user access rights: project scope and module permissions, enforced server-side."""


def test_project_and_module_restrictions(org):
    a = org.project("Client A")
    b = org.project("Client B")
    org.post("/tasks", {"project_id": a["id"], "title": "Task in A"})
    org.post("/tasks", {"project_id": b["id"], "title": "Task in B"})
    staff_h = org.invite_user("staff")
    staff = org.c.get("/api/auth/me", headers=staff_h).json()["user"]
    get = lambda p, **kw: org.c.get(f"/api{p}", headers=staff_h, **kw)  # noqa: E731

    # before restriction: sees both projects and the backlinks module
    assert {p["name"] for p in get("/projects").json()} >= {"Client A", "Client B"}
    assert get("/access/me").json()["modules"] is None

    mods = {x["key"] for x in org.get("/access/modules").json()}
    assert {"m2", "m13", "m24", "monitoring", "workstreams"} <= mods and len(mods) >= 79
    r = org.c.put(f"/api/access/users/{staff['id']}", headers=org.h, json={"all_projects": False, "project_ids": [a["id"]],
                                                                          "modules": ["m2", "m3", "monitoring"]})
    assert r.status_code == 200, r.text

    me = get("/access/me").json()
    assert me["all_projects"] is False and me["project_ids"] == [a["id"]] and set(me["modules"]) == {"m2", "m3", "monitoring"}
    # projects: only A
    assert [p["name"] for p in get("/projects").json()] == ["Client A"]
    assert get(f"/projects/{b['id']}").status_code == 404
    titles = {t["title"] for t in get("/tasks").json()}
    assert "Task in A" in titles and "Task in B" not in titles
    assert org.c.post("/api/tasks", headers=staff_h, json={"project_id": b["id"], "title": "x"}).status_code == 404
    assert get(f"/issues?project_id={b['id']}").json() == [] or get(f"/issues?project_id={b['id']}").status_code == 404
    # modules: allowed vs blocked (server-side, not just hidden in the sidebar)
    assert get("/tasks").status_code == 200 and get("/issues").status_code == 200
    for path in ("/backlinks", f"/studio/items?project_id={a['id']}", "/wallet", "/ai/usage", "/local/status", "/strategies"):
        r = get(path)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "module_denied", (path, r.status_code)
    # always-open basics still work
    assert get("/notifications").status_code == 200 and get("/team").status_code == 200

    # staff can't change access; owner can't be restricted; unrestricted owner unaffected
    owner = org.get("/auth/me").json()["user"]
    assert org.c.put(f"/api/access/users/{owner['id']}", headers=staff_h, json={"modules": []}).status_code == 403
    assert org.c.put(f"/api/access/users/{owner['id']}", headers=org.h, json={"modules": []}).status_code == 400
    assert org.get("/backlinks").status_code == 200
    users = {u["email"]: u for u in org.get("/access/users").json()}
    assert users[staff["email"]]["restricted"] is True and users[owner["email"]]["editable"] is False
    # bad input
    assert org.c.put(f"/api/access/users/{staff['id']}", headers=org.h, json={"modules": ["m999"]}).status_code == 400
    assert org.c.put(f"/api/access/users/{staff['id']}", headers=org.h, json={"all_projects": False, "project_ids": ["00000000-0000-0000-0000-000000000000"]}).status_code == 400

    # restore full access
    assert org.c.put(f"/api/access/users/{staff['id']}", headers=org.h, json={"all_projects": True, "modules": None}).status_code == 200
    assert get("/backlinks").status_code == 200 and len(get("/projects").json()) >= 2


def test_manager_cannot_change_admin(org):
    admin_h = org.invite_user("admin")
    mgr_h = org.invite_user("manager")
    admin = org.c.get("/api/auth/me", headers=admin_h).json()["user"]
    assert org.c.put(f"/api/access/users/{admin['id']}", headers=mgr_h, json={"modules": ["m2"]}).status_code == 403


def test_access_isolation(org, org_factory):
    staff_h = org.invite_user("staff")
    staff = org.c.get("/api/auth/me", headers=staff_h).json()["user"]
    other = org_factory()
    assert other.c.put(f"/api/access/users/{staff['id']}", headers=other.h, json={"modules": ["m2"]}).status_code == 404
    assert staff["email"] not in {u["email"] for u in other.get("/access/users").json()}
