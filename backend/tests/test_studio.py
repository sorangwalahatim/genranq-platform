"""Workstreams, strategies, activities (person / team / AI agent) and the prompt-skills library,
run on the fixture site + synthetic Search Console data with no AI provider connected."""
import io

from openpyxl import load_workbook

from app.worker import run_once
from tests.test_monitor import gsc_csv


def _prep(org, site_server):
    p = org.project("Acme Plumbing")
    pid = p["id"]
    org.post("/sites", {"project_id": pid, "domain": site_server})
    r = org.c.post(f"/api/projects/{pid}/import/gsc", headers=org.h, params={"run_monitor": "false"},
                   files={"file": ("gsc.csv", gsc_csv(site_server), "text/csv")})
    assert r.status_code == 200, r.text
    run_once()
    assert org.c.put(f"/api/projects/{pid}/profile", headers=org.h, json={
        "brand": "Acme Plumbing", "industry": "home services", "product": "emergency plumbing", "audience": "homeowners",
        "location": "Springfield", "usp": "60-minute response, fixed prices", "tone": "calm, expert",
        "primary_keywords": ["emergency plumber", "drain unblocking"], "website": site_server + "/landing.html",
        "competitors": [site_server + "/comp/a.html", site_server + "/comp/b.html", site_server + "/comp/c.html"]}).status_code == 200
    return pid


def test_strategies_activities_and_agents(org, site_server):
    pid = _prep(org, site_server)
    st = org.get("/strategies").json()
    keys = {t["key"] for t in st["templates"]}
    assert {"foundation", "topical_authority", "ai_visibility", "local", "social_media", "paid_social", "link_building"} <= keys
    assert {w["key"] for w in st["workstreams"]} >= {"on_page", "technical", "off_page", "aio", "aeo", "geo", "sxo", "social"}

    # apply with a per-activity override: blog ideas go to a specific person instead of an AI agent
    writer_h = org.invite_user("staff")
    writer = org.c.get("/api/auth/me", headers=writer_h).json()["user"]
    r = org.post(f"/projects/{pid}/strategies", {"key": "topical_authority", "overrides": {"activities": {
        "article_drafting": {"assignee_type": "user", "assignee_user_ids": [writer["id"]]}}}})
    assert r.status_code == 200 and "blog_ideas" in r.json()["added"]
    again = org.post(f"/projects/{pid}/strategies", {"key": "topical_authority"}).json()
    assert again["added"] == [] and "blog_ideas" in again["kept_existing"]  # customisations never overwritten

    acts = {a["key"]: a for a in org.get(f"/projects/{pid}/activities").json()}
    assert acts["article_drafting"]["assignee_type"] == "user"
    # AI agent on real data
    assert org.post(f"/activities/{acts['kw_mining']['id']}/run").status_code == 200
    # person-assigned activity
    assert org.post(f"/activities/{acts['article_drafting']['id']}/run").status_code == 200
    # AI skill activity
    assert org.post(f"/activities/{acts['blog_ideas']['id']}/run").status_code == 200
    run_once()
    runs = org.get(f"/projects/{pid}/activity-runs").json()
    by = {r["activity_id"]: r for r in runs}
    assert by[acts["kw_mining"]["id"]]["status"] == "COMPLETED"
    assert by[acts["article_drafting"]["id"]]["status"] == "HANDED_TO_HUMAN"
    tasks = {t["id"]: t for t in org.get(f"/tasks?project_id={pid}").json()}
    review = tasks[by[acts["kw_mining"]["id"]]["task_id"]]
    assert review["assignee_type"] == "ai_agent" and review["status"] == "review" and review["output"]
    human = tasks[by[acts["article_drafting"]["id"]]["task_id"]]
    assert human["assignee_ids"] == [writer["id"]] and human["workstream"] == "content"
    acts2 = {a["key"]: a for a in org.get(f"/projects/{pid}/activities").json()}
    assert acts2["kw_mining"]["next_due"] > acts["kw_mining"]["next_due"]  # cadence advanced

    # custom activity validation
    assert org.post(f"/projects/{pid}/activities", {"workstream": "nope", "title": "x"}).status_code == 400
    assert org.post(f"/projects/{pid}/activities", {"workstream": "social", "title": "x", "assignee_type": "ai_agent"}).status_code == 400
    r = org.post(f"/projects/{pid}/activities", {"workstream": "social", "title": "LinkedIn carousel", "assignee_type": "team", "team": "content",
                                                 "cadence": "weekly"})
    assert r.status_code == 200
    wl = org.get("/workload").json()
    assert any(w["activity"] == "LinkedIn carousel" and w["who"] == "Team: content" for w in wl)

    cat = org.get("/agents/catalog").json()
    assert len(cat["agents"]) >= 54 and len(cat["seo_skills"]) == 26 and len(cat["meta_skills"]) == 16
    assert cat["ai_provider_connected"] is False
    llm_agent = next(a for a in cat["agents"] if a["key"] == "article_drafting")
    assert llm_agent["state"] == "needs AI provider"


def _run(org, pid, key, inputs=None):
    r = org.post(f"/projects/{pid}/skills/{key}/run", {"inputs": inputs or {}, "use_ai": True})
    assert r.status_code == 200, r.text
    run_once()
    return org.get(f"/skill-runs/{r.json()['run_id']}").json()


def test_prompt_skills_library(org, site_server):
    pid = _prep(org, site_server)
    lib = org.get("/skills").json()
    keys = {s["key"] for s in lib["skills"]}
    for k in ("meta_gap_analysis", "icp_deep_dive", "domain_terms", "competitor_page_analysis", "advanced_keyword_discovery", "content_plan_90",
              "seo_llm_gap", "usp_heading_gap", "tone_style_guide", "semantic_seo", "geo_query_dataset", "eeat_titles"):
        assert k in keys, k

    meta = _run(org, pid, "meta_gap_analysis")
    assert meta["status"] == "COMPLETED" and meta["mode"] == "data"
    assert any("NOT CONNECTED" in n for n in meta["output"]["notes"])  # honest about the AI step
    assert "{{" not in meta["prompt"] and "Acme Plumbing" in meta["prompt"]  # placeholders filled from the profile

    plan = _run(org, pid, "content_plan_90")
    rows = next(iter(plan["output"]["tables"].values()))
    assert len(rows) == 90
    stages = [r.get("D: Funnel stage") for r in rows]
    assert stages.count("TOFU") == 40 and stages.count("MOFU") == 30 and stages.count("BOFU") == 20
    x = org.c.get(f"/api/skill-runs/{plan['id']}/xlsx", headers=org.h)
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb.worksheets[0]
    assert ws.max_row >= 91 and ws.cell(1, 15).value and ws.cell(1, 15).value.startswith("O")

    geo = _run(org, pid, "geo_query_dataset", {"service": "emergency plumbing"})
    qrows = next(iter(geo["output"]["tables"].values()))
    assert len(qrows) >= 100 and list(qrows[0])[:3] == ["Query ID", "Search Query", "Search Intent"]
    assert len({r["Search Query"] for r in qrows}) == len(qrows)

    eeat = _run(org, pid, "eeat_titles")
    t = next(iter(eeat["output"]["tables"].values()))
    assert 10 <= len(t) <= 15 and len(t[0]) == 17 and all(len(r["Main Title (≤70 characters)"]) <= 70 for r in t)

    for k in ("competitor_page_analysis", "usp_heading_gap", "tone_style_guide", "seo_llm_gap", "domain_terms",
              "advanced_keyword_discovery", "semantic_seo", "icp_deep_dive"):
        r = _run(org, pid, k)
        assert r["status"] == "COMPLETED", (k, r["summary"], r["output"].get("notes"))

    # custom skill with versioning; without AI it can't produce output → NEEDS_DATA, prompt saved
    body = {"key": "brand_story", "name": "Brand story", "template": "Write a brand story for {{brand}}.\n{{EVIDENCE}}",
            "evidence": ["client_page"], "workstream": "content"}
    assert org.post("/skills", body).status_code == 200
    assert org.post("/skills", {**body, "key": "meta_gap_analysis"}).status_code == 400  # can't shadow built-ins
    upd = org.c.put("/api/skills/brand_story", headers=org.h, json={**body, "template": "v2 {{brand}}"}).json()
    assert upd["version"] == 2 and upd["history"][0]["template"].startswith("Write")
    cr = _run(org, pid, "brand_story")
    assert cr["status"] == "NEEDS_DATA" and cr["prompt"] == "v2 Acme Plumbing"

    # skills are schedulable as project activities
    r = org.post(f"/projects/{pid}/activities", {"workstream": "geo", "title": "Quarterly GEO dataset", "assignee_type": "ai_agent",
                                                 "agent_key": "skill:geo_query_dataset", "cadence": "quarterly"})
    assert r.status_code == 200
    org.post(f"/activities/{r.json()['id']}/run")
    run_once()
    run = next(x for x in org.get(f"/projects/{pid}/activity-runs").json() if x["activity_id"] == r.json()["id"])
    assert run["status"] == "COMPLETED" and run["output"]["skill_run_id"]


def test_studio_isolation(org, org_factory, site_server):
    pid = _prep(org, site_server)
    meta = _run(org, pid, "eeat_titles")
    org.post("/skills", {"key": "secret_skill", "name": "S", "template": "x", "workstream": "content"})
    other = org_factory()
    assert other.get(f"/skill-runs/{meta['id']}").status_code == 404
    assert other.c.get(f"/api/skill-runs/{meta['id']}/xlsx", headers=other.h).status_code == 404
    assert other.post(f"/projects/{pid}/skills/eeat_titles/run", {"inputs": {}}).status_code == 404
    assert "secret_skill" not in {s["key"] for s in other.get("/skills").json()["skills"]}
    assert other.get(f"/projects/{pid}/activities").status_code == 404
    assert other.get(f"/projects/{pid}/profile").status_code == 404
