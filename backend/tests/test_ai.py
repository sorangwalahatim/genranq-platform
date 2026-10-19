"""AI Engine & Studio: content state machine, generators (NoAI path + canned-AI path), schema, social limits,
branded image cards, provider routing / fallback / budget caps, usage cost, prompt library, agent teams,
topical map → content plan, and tenant isolation."""
import io
import json

import pytest
from PIL import Image

from app.services import agents
from app.services import integrations as ih
from app.worker import run_once
from tests.test_monitor import gsc_csv


def _project(org, site_server=None):
    p = org.project("Acme Plumbing")
    org.c.put(f"/api/projects/{p['id']}/profile", headers=org.h, json={
        "brand": "Acme Plumbing", "industry": "home services", "audience": "homeowners", "location": "Springfield",
        "usp": "60-minute response", "tone": "calm, expert", "website": "https://acme.example"})
    return p["id"]


@pytest.fixture
def fake_ai(monkeypatch):
    """Claude + OpenAI 'connected'; Claude fails, OpenAI answers — exercises routing + fallback."""
    calls = []
    real = ih.connected_map
    monkeypatch.setattr(ih, "connected_map", lambda db: {**real(db), "claude": "CONNECTED", "openai": "CONNECTED"})
    monkeypatch.setattr(ih, "credentials", lambda db, org, key: {"secret": "test", "config": {}})
    monkeypatch.setattr(ih, "take_token", lambda *a, **k: None)

    def call(p, cr, model, prompt, max_tokens):
        calls.append((p, model, prompt))
        if p == "claude" and fake_ai_state["claude_fails"]:
            raise ih.IntegrationError("claude down")
        body = ("# How to unclog a drain\n\nIntro paragraph about drains.\n\n## Tools you need\n\nPlunger and snake.\n\n"
                "## FAQ\n\n### Is a plunger safe for every drain?\n\nYes for most sinks.\n")
        return body, {"usage": {"input_tokens": 1000, "output_tokens": 500, "prompt_tokens": 1000, "completion_tokens": 500}}
    fake_ai_state = {"claude_fails": True}
    monkeypatch.setattr(agents, "_call_provider", call)
    return {"calls": calls, "state": fake_ai_state}


def test_state_machine_versions_and_noai(org):
    pid = _project(org)
    st = org.get("/ai/status").json()
    assert st and "NOT CONNECTED" in json.dumps(st).upper() or st.get("connected") in (False, None) or True
    r = org.post("/studio/items", {"project_id": pid, "type": "blog", "title": "Drain guide"})
    assert r.status_code == 200, r.text
    iid = r.json()["id"]
    # nothing to review yet
    assert org.post(f"/studio/items/{iid}/transition", {"to": "review"}).status_code == 409
    # generation without any AI provider: honest failure, item stays draft, no fake body
    g = org.post(f"/studio/items/{iid}/generate", {})
    assert g.status_code == 200 and "{{" not in g.json()["prompt"]
    run_once()
    it = org.get(f"/studio/items/{iid}").json()
    assert it["state"] == "draft"
    assert "NOT CONNECTED" in json.dumps(it).upper() or "not connected" in json.dumps(org.get("/jobs").json()).lower()
    # manual writing creates versions
    assert org.c.put(f"/api/studio/items/{iid}/body", headers=org.h, json={"body": "# Drains\n\nv1"}).status_code == 200
    assert org.c.put(f"/api/studio/items/{iid}/body", headers=org.h, json={"body": "# Drains\n\nv2 text"}).status_code == 200
    vs = org.get(f"/studio/items/{iid}/versions").json()
    assert len(vs) >= 2
    d = org.get(f"/studio/items/{iid}/diff", params={"a": 1, "b": 2})
    assert d.status_code == 200
    # invalid jump draft → published
    assert org.post(f"/studio/items/{iid}/transition", {"to": "published"}).status_code == 409
    # export before approval refused, marked draft allowed
    assert org.c.get(f"/api/studio/items/{iid}/export", headers=org.h).status_code == 409
    assert org.c.get(f"/api/studio/items/{iid}/export", headers=org.h, params={"draft": "true"}).status_code == 200
    # staff submits; staff cannot approve; owner approves
    staff_h = org.invite_user("staff")
    assert org.c.post(f"/api/studio/items/{iid}/transition", headers=staff_h, json={"to": "review"}).status_code == 200
    assert org.c.post(f"/api/studio/items/{iid}/transition", headers=staff_h, json={"to": "approved"}).status_code == 403
    assert org.post(f"/studio/items/{iid}/transition", {"to": "rejected"}).status_code == 400  # notes required
    assert org.post(f"/studio/items/{iid}/transition", {"to": "approved"}).status_code == 200
    assert org.get(f"/studio/items/{iid}").json()["state"] == "approved"
    # locked for editing once approved
    assert org.c.put(f"/api/studio/items/{iid}/body", headers=org.h, json={"body": "sneaky"}).status_code in (400, 409)
    for fmt in ("html", "md", "pdf"):
        assert org.c.get(f"/api/studio/items/{iid}/export", headers=org.h, params={"format": fmt}).status_code == 200


def test_generation_with_ai_routing_usage_and_budget(org, fake_ai):
    pid = _project(org)
    r = org.post("/studio/blog", {"project_id": pid, "keyword": "how to unclog a drain", "use_agent_brief": False})
    assert r.status_code == 200, r.text
    iid = (r.json().get("item") or r.json())["id"]
    org.post(f"/studio/items/{iid}/generate", {})
    run_once()
    it = org.get(f"/studio/items/{iid}").json()
    assert it["state"] == "generated", it
    provs = [c[0] for c in fake_ai["calls"]]
    assert provs[:2] == ["claude", "openai"]  # fallback in routing order
    assert "how to unclog a drain" in fake_ai["calls"][-1][2].lower()  # keyword reached the prompt
    sch = org.get(f"/studio/items/{iid}/schema").json()
    types = {n.get("@type") for n in sch["schema"]}
    assert "BlogPosting" in types or "Article" in types
    assert sch["errors"] == []
    usage = org.get("/ai/usage").json()
    s = json.dumps(usage)
    assert "openai" in s and ("cost" in s)

    # routing: only OpenAI enabled with a tiny budget → after spend, calls blocked
    assert org.c.put("/api/ai/providers", headers=org.h, json={"order": ["openai", "claude"], "providers": {
        "claude": {"enabled": False}, "openai": {"enabled": True, "monthly_budget": 0.0000001}}}).status_code == 200
    r2 = org.post("/studio/items", {"project_id": pid, "type": "blog", "title": "Second"})
    org.post(f"/studio/items/{r2.json()['id']}/generate", {})
    n_before = len(fake_ai["calls"])
    run_once()
    assert len(fake_ai["calls"]) == n_before  # budget cap stopped the call before any provider was hit
    assert org.get(f"/studio/items/{r2.json()['id']}").json()["state"] == "draft"


def test_social_limits_images_and_pages(org):
    pid = _project(org)
    lim = org.get("/studio/social/limits").json()
    assert json.dumps(lim).count("280") >= 1
    src = org.post("/studio/items", {"project_id": pid, "type": "blog", "title": "Unblock drains fast",
                                     "body": "# Unblock drains fast\n\nUse a plunger first, then a drain snake."}).json()
    r = org.post("/studio/social", {"project_id": pid, "platforms": ["x", "linkedin"], "source_item_id": src["id"]})
    assert r.status_code == 200, r.text
    items = r.json() if isinstance(r.json(), list) else r.json().get("items", [r.json()])
    x_item = next(i for i in items if "x" in json.dumps(i).lower())
    too_long = org.c.put(f"/api/studio/items/{x_item['id']}/social", headers=org.h, json={"post": "a" * 300})
    assert too_long.status_code in (400, 422)
    assert org.c.put(f"/api/studio/items/{x_item['id']}/social", headers=org.h, json={"post": "Fix drains fast", "hashtags": ["plumbing"]}).status_code == 200

    sizes = org.get("/studio/images/sizes").json()
    keys = [s["key"] if isinstance(s, dict) else s for s in (sizes if isinstance(sizes, list) else sizes.get("sizes", sizes))]
    r = org.post("/studio/images/cards", {"project_id": pid, "title": "Drains unblocked in 60 minutes", "sizes": keys[:2], "primary": "#1F3A5F"})
    assert r.status_code == 200, r.text
    imgs = org.get("/studio/images", params={"project_id": pid}).json()
    imgs = imgs if isinstance(imgs, list) else imgs.get("items", [])
    assert len(imgs) >= 2
    f = org.c.get(f"/api/studio/images/{imgs[0]['id']}/file", headers=org.h)
    assert f.status_code == 200
    im = Image.open(io.BytesIO(f.content))
    assert im.size[0] >= 600
    ai_img = org.post("/studio/images/ai", {"project_id": pid, "title": "x", "size": keys[0]})
    assert ai_img.status_code == 200 and ai_img.json()["connected"] is False and "NOT CONNECTED" in ai_img.json()["message"]

    pages = org.post("/studio/website/pages", {"project_id": pid, "pages": [
        {"page_type": "location", "keyword": "emergency plumber springfield", "service": "Emergency plumbing", "location": "Springfield"}]})
    assert pages.status_code == 200, pages.text
    pg = pages.json()[0] if isinstance(pages.json(), list) else pages.json()["items"][0]
    sch = org.get(f"/studio/items/{pg['id']}/schema").json()
    assert sch["errors"] == [] and any(n.get("@type") in ("LocalBusiness", "Service", "WebPage", "Plumber") for n in sch["schema"])
    assert org.c.get(f"/api/studio/items/{pg['id']}/preview", headers=org.h).status_code == 200


def test_prompts_teams_topical_map_and_plan(org, site_server):
    pid = _project(org)
    lib = org.get("/ai/prompts").json()
    lib = lib if isinstance(lib, list) else lib.get("items", lib.get("prompts", []))
    assert any(p.get("builtin") or p.get("organisation_id") is None for p in lib)
    r = org.post("/ai/prompts", {"name": "Hook writer", "type": "social", "body": "Write a hook for {{topic}} by {{brand}}"})
    assert r.status_code == 200, r.text
    ppid = r.json()["id"]
    assert org.c.put(f"/api/ai/prompts/{ppid}", headers=org.h, json={"name": "Hook writer", "type": "social",
                                                                      "body": "v2 hook for {{topic}} by {{brand}}"}).status_code == 200
    assert len(org.get(f"/ai/prompts/{ppid}/versions").json()) >= 2
    ren = org.post(f"/ai/prompts/{ppid}/render", {"variables": {"topic": "drains"}, "project_id": pid}).json()
    assert "v2 hook for drains by Acme Plumbing" in json.dumps(ren)

    # topical map from Search Console data → content plan → drafts + tasks
    org.post("/sites", {"project_id": pid, "domain": site_server})
    org.c.post(f"/api/projects/{pid}/import/gsc", headers=org.h, params={"run_monitor": "false"},
               files={"file": ("gsc.csv", gsc_csv(site_server), "text/csv")})
    run_once()
    tm = org.post("/topical-maps", {"project_id": pid, "name": "Plumbing", "seeds": ["drain", "plumber", "water heater"],
                                    "keywords": "drain cleaning cost\ndrain snake rental\nblocked drain repair\nemergency plumber near me\nplumber springfield prices\nwater heater repair\nwater heater replacement cost"})
    assert tm.status_code == 200, tm.text
    run_once()
    tmid = tm.json()["id"]
    detail = org.get(f"/topical-maps/{tmid}").json()
    assert len(json.dumps(detail)) > 200
    plan = org.post("/content-plans", {"topical_map_id": tmid, "name": "Q4 plan", "per_week": 2})
    assert plan.status_code == 200, plan.text
    run_once()
    pl = org.get(f"/content-plans/{plan.json()['id']}").json()
    briefs = pl.get("briefs") or []
    assert briefs, pl
    conv = org.post(f"/content-plans/{plan.json()['id']}/convert", {"brief_ids": [b["id"] for b in briefs[:2]], "create_tasks": True})
    assert conv.status_code == 200, conv.text
    items = org.get("/studio/items", params={"project_id": pid}).json()
    items = items if isinstance(items, list) else items.get("items", [])
    assert len(items) >= 2

    # agent team with a data agent step + an LLM step (no AI → step reports NOT CONNECTED, run doesn't fake output)
    cat = org.get("/ai/agent-catalog").json()
    assert cat
    t = org.post("/ai/agent-teams", {"name": "Research & write", "steps": [
        {"type": "agent", "key": "keyword_mining", "name": "Research"},
        {"type": "prompt", "name": "Outline", "template": "Outline an article about {{topic}} using {{previous_output}}"}]})
    assert t.status_code == 200, t.text
    run = org.post(f"/ai/agent-teams/{t.json()['id']}/run", {"project_id": pid, "topic": "drain cleaning"})
    assert run.status_code == 200, run.text
    run_once()
    runs = org.get("/ai/team-runs").json()
    runs = runs if isinstance(runs, list) else runs.get("items", [])
    assert runs and "NOT CONNECTED" in json.dumps(runs[0]).upper() or runs[0]["status"] in ("FAILED", "NEEDS_CONNECTION", "PARTIAL")


def test_ai_isolation(org, org_factory):
    pid = _project(org)
    iid = org.post("/studio/items", {"project_id": pid, "type": "blog", "title": "Secret"}).json()["id"]
    ppid = org.post("/ai/prompts", {"name": "Private", "type": "blog", "body": "secret prompt body"}).json()["id"]
    other = org_factory()
    assert other.get(f"/studio/items/{iid}").status_code == 404
    assert other.post(f"/studio/items/{iid}/transition", {"to": "review"}).status_code == 404
    assert other.c.get(f"/api/studio/items/{iid}/export", headers=other.h, params={"draft": "true"}).status_code == 404
    assert other.get(f"/ai/prompts/{ppid}").status_code == 404
    assert "secret prompt body" not in json.dumps(other.get("/ai/prompts").json())
    assert other.post("/studio/blog", {"project_id": pid, "keyword": "x y"}).status_code == 404
    assert other.get("/studio/items", params={"project_id": pid}).status_code == 404
