"""AI Agent Teams (module 53): compose multi-step workflows from

  * ``agent``  — an existing agent (agents.AGENTS, executed with agents.run_agent),
  * ``skill``  — a prompt skill (skills.run_skill: data analysis + optional AI step),
  * ``prompt`` — an LLM step from the prompt library (or an inline template) that receives the previous
                 step's output as {{previous}}.

A run is a background job; every step is an ``agent_run`` row (+ agent_run_step) with its input, output,
tokens and cost. Steps commit one by one so progress is visible while the team works. When no AI provider
is connected, LLM steps are recorded as NEEDS_CONNECTION and data steps still run — nothing is simulated.
"""
from __future__ import annotations

import json
import re
import uuid

from sqlalchemy import select

from .. import models as m
from .. import models_ai as ma
from . import agents, ai_content as ac, ai_engine as eng, ai_prompts as prompts, skills

STEP_TYPES = ("agent", "skill", "prompt")

TEMPLATES = [
    {"key": "article_pipeline", "name": "Article pipeline: research → outline → write → edit → SEO check", "output_type": "blog",
     "description": "Data brief from the project's queries and competitors, then four LLM steps that each build on the previous one.",
     "steps": [{"type": "agent", "key": "content_brief", "name": "Research (data brief)"},
               {"type": "prompt", "key": "agent.research", "name": "Research notes"},
               {"type": "prompt", "key": "agent.outline", "name": "Outline"},
               {"type": "prompt", "key": "agent.write", "name": "Write"},
               {"type": "prompt", "key": "agent.edit", "name": "Edit"},
               {"type": "prompt", "key": "agent.seo_check", "name": "SEO check"}]},
    {"key": "faq_hub", "name": "FAQ hub: questions → answers → schema", "output_type": "blog",
     "description": "FAQ skill on Search Console questions, then an LLM writes answer-first copy for each question.",
     "steps": [{"type": "skill", "key": "faq_schema", "name": "Question discovery", "use_ai": False},
               {"type": "prompt", "name": "Answers", "template": "Write a FAQ page in Markdown for {{brand}} answering each question below in 40–60 words, "
                "answer first. Use '## FAQ' then '### <question>' for each.\n{{previous}}"}]},
    {"key": "content_gap_ideas", "name": "Content gap → blog ideas → brief", "output_type": None,
     "description": "Finds topics competitors cover, turns real queries into blog titles and briefs the best one.",
     "steps": [{"type": "agent", "key": "content_gap", "name": "Content gap"},
               {"type": "agent", "key": "blog_ideas", "name": "Blog ideas", "config": {"create_drafts": False}},
               {"type": "prompt", "key": "brief.outline", "name": "Brief the top idea"}]},
]


def validate_steps(db, steps: list[dict]) -> list[dict]:
    if not steps:
        raise ValueError("A team needs at least one step")
    if len(steps) > 20:
        raise ValueError("A team can have at most 20 steps")
    out = []
    for i, s in enumerate(steps):
        t = s.get("type")
        if t not in STEP_TYPES:
            raise ValueError(f"Step {i + 1}: type must be one of {STEP_TYPES}")
        st = {"type": t, "name": str(s.get("name") or s.get("key") or t)[:120], "config": s.get("config") if isinstance(s.get("config"), dict) else {}}
        if t == "agent":
            if s.get("key") not in agents.BY_KEY:
                raise ValueError(f"Step {i + 1}: unknown agent '{s.get('key')}'")
            st["key"] = s["key"]
        elif t == "skill":
            if not skills.get_skill(db, s.get("key") or ""):
                raise ValueError(f"Step {i + 1}: unknown skill '{s.get('key')}'")
            st["key"], st["use_ai"] = s["key"], bool(s.get("use_ai", True))
        else:
            if s.get("prompt_id"):
                try:
                    pid = uuid.UUID(str(s["prompt_id"]))
                except ValueError:
                    raise ValueError(f"Step {i + 1}: bad prompt_id")
                if not db.get(m.Prompt, pid):
                    raise ValueError(f"Step {i + 1}: prompt not found")
                st["prompt_id"] = str(pid)
            elif s.get("key"):
                if s["key"] not in prompts.BUILTINS and not db.scalar(select(m.Prompt.id).where(m.Prompt.name == s["key"])):
                    raise ValueError(f"Step {i + 1}: unknown prompt '{s['key']}'")
                st["key"] = s["key"]
            elif str(s.get("template") or "").strip():
                st["template"] = str(s["template"])[:20000]
            else:
                raise ValueError(f"Step {i + 1}: a prompt step needs a prompt key, prompt_id or template")
            if s.get("agent_id"):
                st["agent_id"] = str(s["agent_id"])
        out.append(st)
    return out


def _as_text(obj, limit=15000) -> str:
    if isinstance(obj, str):
        return obj[:limit]
    return json.dumps(obj, default=str, ensure_ascii=False)[:limit]


def _tables_md(tables: dict, limit=40) -> str:
    out = []
    for name, rows in (tables or {}).items():
        if not rows:
            continue
        cols = list(dict.fromkeys(k for r in rows for k in r))[:8]
        out += [f"### {name}", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        out += ["| " + " | ".join(str(r.get(c, ""))[:80].replace("|", "/") for c in cols) + " |" for r in rows[:limit]]
        out.append("")
    return "\n".join(out)


def _prompt_body(db, step) -> str:
    if step.get("prompt_id"):
        p = db.get(m.Prompt, uuid.UUID(step["prompt_id"]))
        return p.body if p else ""
    if step.get("key"):
        if step["key"] in prompts.BUILTINS:
            return prompts.get_body(db, step["key"])[0]
        p = db.scalar(select(m.Prompt).where(m.Prompt.name == step["key"]).limit(1))
        return p.body if p else ""
    return step.get("template") or ""


def run_step(db, project, step: dict, values: dict, prev: str, user_id) -> dict:
    t = step["type"]
    if t == "agent":
        cfg = {**step.get("config", {}), "keyword": values.get("topic"), "topic": values.get("topic"), "text": prev or values.get("topic")}
        status, summary, out = agents.run_agent(db, project, step["key"], {k: v for k, v in cfg.items() if v is not None})
        text = summary + ("\n\n" + _as_text(out) if out else "")
        return {"state": status, "summary": summary, "text": text, "data": json.loads(json.dumps(out, default=str))}
    if t == "skill":
        inputs = {**step.get("config", {}), "topic": values.get("topic"), "previous_output": prev[:4000] if prev else None}
        run = skills.run_skill(db, project, step["key"], {k: v for k, v in inputs.items() if v}, user_id=user_id, use_ai=step.get("use_ai", True))
        o = run.output or {}
        text = (o.get("ai_text") or "") + ("\n\n" if o.get("ai_text") else "") + _tables_md(o.get("tables") or {})
        state = run.status if text.strip() else "NEEDS_DATA"
        return {"state": state, "summary": run.summary, "text": text.strip(), "data": {"skill_run_id": str(run.id), "notes": o.get("notes")}}
    body = _prompt_body(db, step)
    vals = {**values, "previous": prev or "(no previous output)"}
    rendered, missing = prompts.render(body, vals)
    prefer = agents.DEFAULT_PREFER
    if step.get("agent_id"):
        ag = db.get(m.Agent, uuid.UUID(step["agent_id"]))
        prov = db.get(m.AiProvider, ag.provider_id) if ag and ag.provider_id else None
        if ag and ag.role:
            rendered = f"You are acting as the team's {ag.role} ({ag.name}).\n\n" + rendered
        if prov:
            prefer = (prov.type,) + tuple(p for p in agents.DEFAULT_PREFER if p != prov.type)
    try:
        text, meta = agents.llm(db, project.organisation_id, rendered, max_tokens=int(step.get("config", {}).get("max_tokens") or 4000),
                                prefer=prefer, task_type="agent_team")
    except agents.NoAI as e:
        return {"state": "NEEDS_CONNECTION", "summary": str(e), "text": "", "prompt": rendered, "missing": missing}
    return {"state": "COMPLETED", "summary": f"{step['name']} ({meta.get('model')})", "text": text, "prompt": rendered, "missing": missing,
            "model": meta.get("model")}


def run_team(session_factory, team_run_id, user_id=None) -> dict:
    with session_factory() as db:
        tr = db.get(ma.AgentTeamRun, team_run_id)
        team = db.get(m.AgentTeam, tr.agent_team_id)
        project = db.get(m.Project, tr.project_id)
        tr.status = "RUNNING"
        steps = list(team.steps or [])
        values = {**{k: v for k, v in skills.get_profile(db, project).items() if isinstance(v, (str, int, float, list))}, **(tr.input or {})}
        values.setdefault("topic", (tr.input or {}).get("topic"))
        prev = str((tr.input or {}).get("brief") or "")
    states = []
    for i, step in enumerate(steps):
        with session_factory() as db:
            project = db.get(m.Project, tr.project_id)
            ar = m.AgentRun(organisation_id=project.organisation_id, agent_team_id=team.id,
                            status={"state": "RUNNING", "step": i, "name": step.get("name")},
                            input={"step": step, "previous": prev[:20000], "team_run_id": str(team_run_id)})
            db.add(ar)
            db.flush()
            meta = ma.AgentRunStep(agent_run_id=ar.id, organisation_id=project.organisation_id, team_run_id=team_run_id, step_index=i,
                                   state="RUNNING", started_at=ac.utcnow())
            db.add(meta)
            db.flush()
            with eng.usage_scope(project_id=project.id, user_id=user_id, task_type="agent_team") as sc:
                sp = db.begin_nested()
                try:
                    res = run_step(db, project, step, values, prev, user_id)
                    sp.commit()
                except Exception as e:  # noqa: BLE001 — a failing step is recorded, the team stops
                    sp.rollback()
                    res = {"state": "FAILED", "summary": f"{type(e).__name__}: {e}"[:1000], "text": ""}
            tot = eng.totals(sc["records"])
            ar.status = {"state": res["state"], "step": i, "name": step.get("name"), "summary": res.get("summary")}
            ar.output = json.loads(json.dumps({**{k: v for k, v in res.items() if k != "state"}, **tot}, default=str))
            meta.state, meta.finished_at = res["state"], ac.utcnow()
            states.append(res["state"])
            if res["state"] == "COMPLETED" and res.get("text"):
                prev = res["text"]
            trr = db.get(ma.AgentTeamRun, team_run_id)
            trr.tokens_in += tot["tokens_in"]
            trr.tokens_out += tot["tokens_out"]
            trr.cost = float(trr.cost or 0) + tot["cost"]
            if res["state"] == "FAILED":
                break
    with session_factory() as db:
        trr = db.get(ma.AgentTeamRun, team_run_id)
        trr.final_output = prev or None
        trr.status = "FAILED" if "FAILED" in states else ("COMPLETED" if all(s == "COMPLETED" for s in states) else "PARTIAL")
        trr.error = None if trr.status != "FAILED" else "A step failed — see the step output"
        trr.finished_at = ac.utcnow()
        out_type = (trr.input or {}).get("create_content")
        if out_type and prev and "COMPLETED" in states and any(s.get("type") == "prompt" for s in steps):
            item = to_content(db, trr, out_type, (trr.input or {}).get("title"), user_id)
            trr.content_item_id = item.id
        return {"status": trr.status, "steps": states, "content_item_id": str(trr.content_item_id) if trr.content_item_id else None}


def to_content(db, tr: ma.AgentTeamRun, ctype: str, title: str | None, user_id) -> m.ContentItem:
    from . import ai_studio as studio
    if not tr.final_output:
        raise ValueError("This run has no output to turn into content")
    project = db.get(m.Project, tr.project_id)
    seo, body = ac.split_meta(tr.final_output)
    title = title or ac.h1_of(body) or (tr.input or {}).get("topic") or "Agent team output"
    data = {"seo": {**seo, "slug": ac.slugify(title)}, "primary_keyword": (tr.input or {}).get("topic"), "voice": studio.voice(db, project),
            "agent_team_run_id": str(tr.id)}
    it = studio.new_item(db, user_id, project, ctype if ctype in studio.CONTENT_TYPES else "blog", title, data)
    ac.add_version(db, it, body, author_id=user_id, origin="agent_team", note=f"From agent team run {tr.id}")
    return it


def step_view(db, tr_id) -> list[dict]:
    out = []
    for ar, st in db.execute(select(m.AgentRun, ma.AgentRunStep).join(ma.AgentRunStep, ma.AgentRunStep.agent_run_id == m.AgentRun.id)
                             .where(ma.AgentRunStep.team_run_id == tr_id).order_by(ma.AgentRunStep.step_index)):
        out.append({"id": str(ar.id), "index": st.step_index, "state": st.state, "status": ar.status, "input": ar.input, "output": ar.output,
                    "started_at": st.started_at, "finished_at": st.finished_at})
    return out


def team_name_ok(name: str) -> bool:
    return bool(re.match(r"^.{2,120}$", name or ""))
