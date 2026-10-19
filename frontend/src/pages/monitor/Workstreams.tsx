import { Fragment, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Evidence, Initials, StatusBadge, Switch, TEAM_LABEL, TEAMS, useJobWatch, useProjects, fmtDay } from "./shared";

type Catalog = { agents: any[]; seo_skills: any[]; meta_skills: any[]; ai_provider_connected: boolean };


/** Who does the work, as a short label. */
function whoLabel(a: any, users: Record<string, string>, agentName?: string) {
  if (a.assignee_type === "ai_agent") return { kind: "ai" as const, text: agentName || a.agent_name || a.agent_key || "AI agent" };
  if (a.assignee_type === "user" || a.assignee_user_ids?.length) {
    const n = (a.assignee_user_ids || []).map((u: string) => users[u] || "Team member");
    return { kind: undefined, text: n.length ? n.join(", ") : `${TEAM_LABEL[a.team] || "Project"} team` };
  }
  return { kind: "team" as const, text: `${TEAM_LABEL[a.team] || human(a.team) || "Project"} team` };
}

export function AgentPicker({ value, onChange, catalog, skills, id }: { value: string; onChange: (v: string) => void; catalog?: Catalog | null; skills?: any[]; id?: string }) {
  const groups = useMemo(() => {
    const g: Record<string, any[]> = {};
    for (const a of catalog?.agents || []) (g[a.group] ||= []).push(a);
    return g;
  }, [catalog]);
  const sel = (catalog?.agents || []).find((a) => a.key === value);
  const sk = value?.startsWith("skill:") ? (skills || []).find((s) => s.key === value.slice(6)) : null;
  return (
    <>
      <select id={id} className="input" value={value || ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose an agent or skill…</option>
        {Object.entries(groups).map(([g, list]) => (
          <optgroup key={g} label={`Agents · ${g}`}>
            {list.map((a) => <option key={a.key} value={a.key} disabled={!a.runnable && a.key !== value}>{a.name}{a.runnable ? (a.state !== "live" ? ` — ${a.state}` : "") : ` — ${a.state}`}</option>)}
          </optgroup>
        ))}
        {!!skills?.length && (
          <optgroup label="Prompt skills (AI)">
            {skills.map((s) => <option key={s.key} value={`skill:${s.key}`}>{s.name}</option>)}
          </optgroup>
        )}
      </select>
      {sel && <span className="hint">{sel.description} <Badge kind={sel.state === "live" ? "live" : "pending"}>{sel.state}</Badge></span>}
      {sk && <span className="hint">{sk.description}{catalog && !catalog.ai_provider_connected && <> <Badge kind="NOT_CONNECTED">Needs AI provider</Badge></>}</span>}
    </>
  );
}

function UserMulti({ users, value, onChange, label }: { users: any[]; value: string[]; onChange: (v: string[]) => void; label: string }) {
  return (
    <div className="mon-multi" role="group" aria-label={label}>
      {!users.length && <span className="faint small">No team members.</span>}
      {users.map((u) => (
        <label key={u.id}><input type="checkbox" checked={value.includes(u.id)} onChange={(e) => onChange(e.target.checked ? [...value, u.id] : value.filter((x) => x !== u.id))} />
          <Initials name={u.name || u.email} />{u.name || u.email}<span className="faint small">{human(u.role)}</span></label>
      ))}
    </div>
  );
}

/** Assignment fields shared by the activity editor and the strategy apply overrides. */
function Assignment({ v, set, users, catalog, skills, idp }: { v: any; set: (p: any) => void; users: any[]; catalog?: Catalog | null; skills?: any[]; idp: string }) {
  return (
    <>
      <div className="field" style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 13, fontWeight: 500 }}>
        <span id={`${idp}-who`}>Who does it</span>
        <div className="seg" role="radiogroup" aria-labelledby={`${idp}-who`}>
          {[["ai_agent", "AI agent"], ["team", "Team"], ["user", "People"]].map(([k, l]) => (
            <button key={k} type="button" role="radio" aria-checked={v.assignee_type === k} className={v.assignee_type === k ? "on" : ""} onClick={() => set({ assignee_type: k })}>{l}</button>
          ))}
        </div>
      </div>
      {v.assignee_type === "ai_agent" && (
        <label className="field" htmlFor={`${idp}-agent`}>Agent or skill
          <AgentPicker id={`${idp}-agent`} value={v.agent_key || ""} onChange={(x) => set({ agent_key: x })} catalog={catalog} skills={skills} /></label>
      )}
      {v.assignee_type === "user" && (
        <div className="field" style={{ display: "flex", flexDirection: "column", gap: 5, fontSize: 13, fontWeight: 500 }}>People<UserMulti label="Assignees" users={users} value={v.assignee_user_ids || []} onChange={(x) => set({ assignee_user_ids: x })} /></div>
      )}
      <label className="field">{v.assignee_type === "ai_agent" ? "Reviewing team" : v.assignee_type === "user" ? "Team (fallback & reporting)" : "Team"}
        <select className="input" value={v.team || ""} onChange={(e) => set({ team: e.target.value })}>
          {TEAMS.map((t) => <option key={t} value={t}>{TEAM_LABEL[t]}</option>)}
        </select>
        {v.assignee_type === "ai_agent" && <span className="hint">The AI output is handed to this team as a review task.</span>}
      </label>
    </>
  );
}

export default function Workstreams() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const can = useCan();
  const { data: projects } = useProjects();
  const project = projects?.find((p) => p.id === id);
  const { data: meta, error: mErr } = useApi<any>("/strategies");
  const { data: applied, reload: reloadApplied } = useApi<any[]>(`/projects/${id}/strategies`, [id]);
  const { data: acts, error, loading, reload } = useApi<any[]>(`/projects/${id}/activities`, [id]);
  const { data: runs, reload: reloadRuns } = useApi<any[]>(`/projects/${id}/activity-runs`, [id]);
  const { data: team } = useApi<any>("/team");
  const { data: catalog } = useApi<Catalog>("/agents/catalog");
  const { data: sk } = useApi<any>("/skills");
  const [edit, setEdit] = useState<any>(null);
  const [applyKey, setApplyKey] = useState<string | null>(null);
  const [hist, setHist] = useState<any>(null);
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [showOff, setShowOff] = useState(true);
  const { run } = useAction();
  const watch = useJobWatch();
  const users = (team?.users || []).filter((u: any) => u.is_active && u.role !== "client");
  const names: Record<string, string> = Object.fromEntries((team?.users || []).map((u: any) => [u.id, u.name || u.email]));
  const wsLabel: Record<string, string> = Object.fromEntries((meta?.workstreams || []).map((w: any) => [w.key, w.label]));
  const appliedKeys = new Set((applied || []).map((s) => s.template_key));
  const refreshAll = () => { reload(); reloadRuns(); };
  const visible = (acts || []).filter((a) => showOff || a.enabled);
  const grouped = (meta?.workstreams || []).map((w: any) => [w, visible.filter((a) => a.workstream === w.key)] as const).filter(([, l]: any) => l.length);
  const actTitle: Record<string, string> = Object.fromEntries((acts || []).map((a) => [a.id, a.title]));

  const runNow = async (a: any) => {
    const r = await run<any>(() => api(`/activities/${a.id}/run`, { method: "POST" }), `Running “${a.title}”`);
    if (!r) return;
    setRunning((x) => ({ ...x, [a.id]: true }));
    watch(r.job_id, () => { setRunning((x) => ({ ...x, [a.id]: false })); refreshAll(); }, a.title);
  };
  const toggle = (a: any, enabled: boolean) => run(() => api(`/activities/${a.id}`, { method: "PATCH", body: { enabled } }), enabled ? "Activity enabled" : "Activity paused").then(reload);
  const removeStrategy = (s: any) => {
    const n = (acts || []).filter((a) => a.strategy_id === s.id).length;
    if (!confirm(`Remove the “${s.name}” strategy from this project?`)) return;
    const also = n > 0 && confirm(`Also delete its ${n} activities? Choose Cancel to keep them as custom activities.`);
    run(() => api(`/project-strategies/${s.id}?remove_activities=${also}`, { method: "DELETE" }), "Strategy removed").then(() => { reloadApplied(); reload(); });
  };

  if (mErr) return <ErrorBox error={mErr} />;
  return (
    <>
      <PageHead title={<>Workstreams{project ? <span className="muted" style={{ fontWeight: 400 }}> · {project.name}</span> : null}</>}
        sub="SEO strategies broken into recurring activities — each done by a person, a team or an AI agent whose output is reviewed before anything goes live.">
        <select className="input" style={{ width: "auto", minWidth: 180 }} value={id} aria-label="Project" onChange={(e) => nav(`/projects/${e.target.value}/workstreams`)}>
          {(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <Link className="btn" to={`/projects/${id}/monitor`}>Monitoring</Link>
        {can("create") && <button className="btn primary" onClick={() => setEdit({ workstream: "on_page", cadence: "monthly", assignee_type: "team", team: "seo", assignee_user_ids: [], approval_required: true, enabled: true })}>Add activity</button>}
      </PageHead>

      <Panel title="Strategies" actions={applied?.length ? <span className="small muted">{applied.length} applied</span> : null}>
        {applied && applied.length > 0 && (
          <div className="mon-chips" style={{ marginBottom: 14 }}>
            {applied.map((s) => (
              <span key={s.id} className="badge pass" style={{ height: 26, paddingRight: 4 }}>{s.name}
                {can("delete") && <button className="btn ghost sm" style={{ height: 20, padding: "0 6px" }} aria-label={`Remove strategy ${s.name}`} onClick={() => removeStrategy(s)}>✕</button>}</span>
            ))}
          </div>
        )}
        <div className="mon-agents">
          {(meta?.templates || []).map((t: any) => (
            <div key={t.key} className="mon-agent">
              <div className="h"><b>{t.name}</b>{appliedKeys.has(t.key) ? <Badge kind="pass">Applied</Badge> : !t.builtin ? <Badge>Custom</Badge> : null}</div>
              <p>{t.description}</p>
              <div className="f">
                <span className="small faint">{t.activities.length} activities · {Array.from(new Set(t.activities.map((a: any) => wsLabel[a.workstream] || a.workstream))).slice(0, 3).join(", ")}</span>
                {can("create") && <button className="btn sm" onClick={() => setApplyKey(t.key)}>{appliedKeys.has(t.key) ? "Add missing…" : "Apply…"}</button>}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <div className="row" style={{ margin: "20px 0 10px", justifyContent: "space-between" }}>
        <h2>Activities {acts ? <span className="muted num" style={{ fontWeight: 400 }}>· {acts.filter((a) => a.enabled).length} active of {acts.length}</span> : null}</h2>
        <label className="check small"><input type="checkbox" checked={showOff} onChange={(e) => setShowOff(e.target.checked)} /> Show paused and one-off activities</label>
      </div>
      <ErrorBox error={error} />
      {loading && !acts ? <Loading /> : !acts?.length ? (
        <Panel><Empty title="No activities yet" action={can("create") && <button className="btn primary" onClick={() => setApplyKey("foundation")}>Apply the Technical foundation strategy</button>}>
          Apply a strategy above to add its recurring activities, or add a custom activity.</Empty></Panel>
      ) : grouped.map(([w, list]: any) => (
        <section key={w.key} className="panel mon-ws" aria-labelledby={`ws-${w.key}`}>
          <div className="panel-head"><h2 id={`ws-${w.key}`}>{w.label}</h2><span className="faint small">{list.length}</span></div>
          <div className="mon-act mon-act-head" aria-hidden><span>Activity</span><span>Who</span><span>Cadence</span><span>Next due</span><span>Last run</span><span /></div>
          {list.map((a: any) => {
            const who = whoLabel(a, names);
            const overdue = a.enabled && a.next_due && a.next_due < new Date().toISOString().slice(0, 10);
            return (
              <div key={a.id} className={`mon-act ${a.enabled ? "" : "off"}`}>
                <div>
                  <div className="t">{a.title} {a.approval_required && a.assignee_type === "ai_agent" && <Badge kind="low">Review</Badge>}</div>
                  {a.instructions && <div className="faint small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.instructions}</div>}
                </div>
                <div className="who c-who"><Initials name={who.text} kind={who.kind} /><span title={who.text}>{who.text}</span></div>
                <div className="small c-cad">{human(a.cadence)}</div>
                <div className={`small c-due ${overdue ? "" : "muted"}`} style={{ color: overdue ? "var(--high)" : undefined }}>{a.enabled ? (a.next_due ? fmtDay(a.next_due) : "—") : "Paused"}</div>
                <div className="last c-last">
                  {a.last_run ? (
                    <>
                      <div className="row" style={{ gap: 6 }}><StatusBadge s={a.last_run.status} /><span className="faint">{ago(a.last_run.at)}</span>
                        {a.last_run.task_id && <Link to={`/tasks?project=${id}`} className="small">Task</Link>}</div>
                      {a.last_run.summary && <div className="sum" title={a.last_run.summary}>{a.last_run.summary}</div>}
                    </>
                  ) : <span className="faint">Not run yet</span>}
                </div>
                <div className="row" style={{ flexWrap: "nowrap", justifyContent: "flex-end" }}>
                  {can("edit") && <Switch checked={a.enabled} onChange={(v) => toggle(a, v)} label={`${a.title} enabled`} />}
                  {can("edit") && <button className="btn sm" disabled={running[a.id]} onClick={() => runNow(a)}>{running[a.id] ? "Running…" : "Run now"}</button>}
                  <button className="btn ghost sm" onClick={() => setHist(a)} aria-label={`History of ${a.title}`}>History</button>
                  {can("edit") && <button className="btn ghost sm" onClick={() => setEdit(a)} aria-label={`Edit ${a.title}`}>Edit</button>}
                </div>
              </div>
            );
          })}
        </section>
      ))}

      <div style={{ marginTop: 20 }}>
        <Panel title="Run history" pad={false}>
          {!runs?.length ? <Empty title="No runs yet">Due activities run automatically; use “Run now” to run one immediately.</Empty> : (
            <RunTable runs={runs.slice(0, 50)} titles={actTitle} pid={id} />
          )}
        </Panel>
      </div>

      {edit && <ActivityEditor pid={id} a={edit} meta={meta} users={users} catalog={catalog} skills={sk?.skills} onClose={() => setEdit(null)} onDone={refreshAll} />}
      {applyKey && meta && <ApplyStrategy pid={id} tpl={meta.templates.find((t: any) => t.key === applyKey)} existing={new Set((acts || []).map((a) => a.key))} cadences={meta.cadences}
        wsLabel={wsLabel} users={users} catalog={catalog} skills={sk?.skills} onClose={() => setApplyKey(null)} onDone={() => { reloadApplied(); reload(); }} />}
      {hist && (
        <Drawer title={`History · ${hist.title}`} onClose={() => setHist(null)}>
          <RunHistory runs={(runs || []).filter((r) => r.activity_id === hist.id)} pid={id} />
        </Drawer>
      )}
    </>
  );
}

export function RunTable({ runs, titles, pid }: { runs: any[]; titles: Record<string, string>; pid: string }) {
  const [open, setOpen] = useState<any>(null);
  return (
    <>
      <div className="table-wrap"><table className="t">
        <thead><tr><th>Started</th><th>Activity</th><th>Status</th><th>Summary</th><th>Trigger</th><th /></tr></thead>
        <tbody>{runs.map((r) => (
          <tr key={r.id} className="click" tabIndex={0} onClick={() => setOpen(r)} onKeyDown={(e) => e.key === "Enter" && setOpen(r)}>
            <td className="small nowrap">{fmtDateTime(r.started_at)}</td>
            <td style={{ minWidth: 160 }}>{titles[r.activity_id] || r.agent_key || "Activity"}{r.agent_key && <div className="faint small">{r.agent_key}</div>}</td>
            <td><StatusBadge s={r.status} /></td>
            <td className="small" style={{ maxWidth: 420 }}>{r.summary || r.error || "—"}</td>
            <td className="small muted">{human(r.triggered_by)}</td>
            <td>{r.task_id && <Link className="small" to={`/tasks?project=${pid}`} onClick={(e) => e.stopPropagation()}>Task</Link>}</td>
          </tr>
        ))}</tbody>
      </table></div>
      {open && <Drawer title={titles[open.activity_id] || "Activity run"} onClose={() => setOpen(null)}><RunHistory runs={[open]} pid={pid} /></Drawer>}
    </>
  );
}

function RunHistory({ runs, pid }: { runs: any[]; pid: string }) {
  if (!runs.length) return <Empty title="No runs yet" />;
  return (
    <ol className="mon-tl">
      {runs.map((r) => (
        <li key={r.id} className={r.status === "COMPLETED" ? "good" : r.status === "FAILED" ? "bad" : "info"}>
          <div className="row"><StatusBadge s={r.status} /><span className="small muted">{human(r.triggered_by)} run</span>
            {r.task_id && <Link className="small" to={`/tasks?project=${pid}`}>Open task</Link>}</div>
          {r.summary && <p className="small" style={{ margin: "6px 0" }}>{r.summary}</p>}
          {r.error && <p className="small" style={{ color: "var(--critical)" }}>{r.error}</p>}
          {r.output && Object.keys(r.output).length > 0 && (
            <details style={{ margin: "6px 0" }}><summary className="small" style={{ cursor: "pointer" }}>Output</summary><div style={{ marginTop: 8 }}><Evidence ev={r.output} /></div></details>
          )}
          <div className="when">{fmtDateTime(r.started_at)}{r.finished_at ? ` · finished ${ago(r.finished_at)}` : ""}</div>
        </li>
      ))}
    </ol>
  );
}

function ActivityEditor({ pid, a, meta, users, catalog, skills, onClose, onDone }: any) {
  const isNew = !a.id;
  const [f, setF] = useState<any>({ ...a, instructions: a.instructions || "", next_due: a.next_due || "" });
  const set = (p: any) => setF((x: any) => ({ ...x, ...p }));
  const { run, busy } = useAction();
  const errs: string[] = [];
  if (!f.title?.trim()) errs.push("Title is required.");
  if (f.assignee_type === "ai_agent" && !f.agent_key) errs.push("Choose an agent or skill.");
  if (f.assignee_type === "user" && !(f.assignee_user_ids || []).length) errs.push("Choose at least one person.");
  const body = {
    workstream: f.workstream, title: f.title.trim(), instructions: f.instructions || null, cadence: f.cadence, assignee_type: f.assignee_type,
    assignee_user_ids: f.assignee_type === "user" ? f.assignee_user_ids : [], team: f.team || null, agent_key: f.assignee_type === "ai_agent" ? f.agent_key : null,
    approval_required: f.approval_required, enabled: f.enabled, next_due: f.next_due || null,
  };
  const save = () => run(() => isNew ? api(`/projects/${pid}/activities`, { body }) : api(`/activities/${a.id}`, { method: "PATCH", body }), isNew ? "Activity added" : "Activity saved")
    .then((r) => { if (r) { onDone(); onClose(); } });
  const del = () => confirm(`Delete “${a.title}”? Its run history is kept.`) && run(() => api(`/activities/${a.id}`, { method: "DELETE" }), "Activity deleted").then(() => { onDone(); onClose(); });
  return (
    <Modal wide title={isNew ? "Add activity" : `Edit · ${a.title}`} onClose={onClose} foot={<>
      {!isNew && <button className="btn danger" onClick={del} style={{ marginRight: "auto" }}>Delete</button>}
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy || errs.length > 0} onClick={save}>{isNew ? "Add activity" : "Save"}</button></>}>
      <div className="form">
        <div className="grid g2">
          <label className="field">Title<input className="input" value={f.title || ""} onChange={(e) => set({ title: e.target.value })} autoFocus maxLength={300} /></label>
          <label className="field">Workstream<select className="input" value={f.workstream} onChange={(e) => set({ workstream: e.target.value })}>
            {(meta?.workstreams || []).map((w: any) => <option key={w.key} value={w.key}>{w.label}</option>)}</select></label>
        </div>
        <label className="field">Instructions<span className="hint">What “done” looks like; included in every task this activity creates.</span>
          <textarea className="input" rows={3} value={f.instructions} onChange={(e) => set({ instructions: e.target.value })} maxLength={10000} /></label>
        <div className="grid g2">
          <div className="form" style={{ gap: 14 }}><Assignment v={f} set={set} users={users} catalog={catalog} skills={skills} idp="ed" /></div>
          <div className="form" style={{ gap: 14 }}>
            <label className="field">Cadence<select className="input" value={f.cadence} onChange={(e) => set({ cadence: e.target.value })}>
              {(meta?.cadences || []).map((c: string) => <option key={c} value={c}>{human(c)}</option>)}</select></label>
            <label className="field">Next due<input className="input" type="date" value={f.next_due || ""} onChange={(e) => set({ next_due: e.target.value })} />
              <span className="hint">{isNew ? "Defaults to today." : "Leave as is to keep the schedule."}</span></label>
            <label className="check"><input type="checkbox" checked={!!f.approval_required} onChange={(e) => set({ approval_required: e.target.checked })} /> Require human review of AI output</label>
            <label className="check"><input type="checkbox" checked={!!f.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled</label>
          </div>
        </div>
        {errs.length > 0 && f.title && <div className="notice warn">{errs.join(" ")}</div>}
      </div>
    </Modal>
  );
}

function ApplyStrategy({ pid, tpl, existing, cadences, wsLabel, users, catalog, skills, onClose, onDone }: any) {
  const [ov, setOv] = useState<Record<string, any>>({});
  const [start, setStart] = useState(new Date().toISOString().slice(0, 10));
  const [open, setOpen] = useState<string | null>(null);
  const { run, busy } = useAction();
  const toAdd = tpl.activities.filter((a: any) => !existing.has(a.key));
  const eff = (a: any) => ({ ...a, assignee_user_ids: [], ...(ov[a.key] || {}) });
  const set = (k: string, p: any) => setOv((x) => ({ ...x, [k]: { ...(x[k] || {}), ...p } }));
  const agentName = (k?: string) => k?.startsWith("skill:") ? (skills || []).find((s: any) => s.key === k.slice(6))?.name || k : (catalog?.agents || []).find((x: any) => x.key === k)?.name || k;
  const invalid = toAdd.some((a: any) => { const e = eff(a); return (e.assignee_type === "ai_agent" && !e.agent_key) || (e.assignee_type === "user" && !e.assignee_user_ids.length); });
  const apply = () => run<any>(() => api(`/projects/${pid}/strategies`, { body: { key: tpl.key, start, overrides: { activities: ov } } }))
    .then((r) => { if (r) { onDone(); onClose(); } return r; });
  return (
    <Modal wide title={`Apply strategy · ${tpl.name}`} onClose={onClose} foot={<>
      <span className="small muted" style={{ marginRight: "auto" }}>{toAdd.length} to add{tpl.activities.length - toAdd.length ? ` · ${tpl.activities.length - toAdd.length} already in the project (kept as is)` : ""}</span>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={busy || !toAdd.length || invalid} onClick={() => apply().then((r) => r && undefined)}>Apply {toAdd.length} activities</button></>}>
      <p className="muted small" style={{ marginBottom: 12 }}>{tpl.description} Adjust any activity before applying — cadence, who does it, and which agent runs it.</p>
      <label className="field" style={{ maxWidth: 220, marginBottom: 12 }}>First run on<input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
      <div className="table-wrap"><table className="t">
        <thead><tr><th>Activity</th><th>Cadence</th><th>Who</th><th /></tr></thead>
        <tbody>{tpl.activities.map((a: any) => {
          const kept = existing.has(a.key);
          const e = eff(a);
          return (
            <Fragment key={a.key}>
              <tr style={{ opacity: kept ? 0.55 : 1 }}>
                <td><div style={{ fontWeight: 500 }}>{a.title}</div><div className="faint small">{wsLabel[a.workstream] || a.workstream}{kept ? " · already in project" : ""}</div></td>
                <td>{kept ? human(a.cadence) : (
                  <select className="input" style={{ width: 130, height: 30 }} aria-label={`Cadence for ${a.title}`} value={e.cadence} onChange={(x) => set(a.key, { cadence: x.target.value })}>
                    {cadences.map((c: string) => <option key={c} value={c}>{human(c)}</option>)}</select>
                )}</td>
                <td className="small">{e.assignee_type === "ai_agent" ? <>AI · {agentName(e.agent_key) || <span style={{ color: "var(--critical)" }}>choose an agent</span>}</>
                  : e.assignee_type === "user" ? (e.assignee_user_ids.length ? e.assignee_user_ids.map((u: string) => users.find((x: any) => x.id === u)?.name || "Person").join(", ") : <span style={{ color: "var(--critical)" }}>choose people</span>)
                  : `${TEAM_LABEL[e.team] || e.team} team`}</td>
                <td className="r">{!kept && <button className="btn ghost sm" aria-expanded={open === a.key} onClick={() => setOpen(open === a.key ? null : a.key)}>{open === a.key ? "Done" : "Customise"}</button>}</td>
              </tr>
              {open === a.key && !kept && (
                <tr><td colSpan={4} style={{ background: "var(--surface-2)" }}>
                  <div className="grid g2">
                    <div className="form" style={{ gap: 12 }}><Assignment v={e} set={(p: any) => set(a.key, p)} users={users} catalog={catalog} skills={skills} idp={`ap-${a.key}`} /></div>
                    <div className="form" style={{ gap: 12 }}>
                      <label className="field">Title<input className="input" value={e.title} onChange={(x) => set(a.key, { title: x.target.value })} /></label>
                      <label className="field">Instructions<textarea className="input" rows={3} value={e.instructions || ""} onChange={(x) => set(a.key, { instructions: x.target.value })} /></label>
                      <label className="check"><input type="checkbox" checked={!!e.approval_required} onChange={(x) => set(a.key, { approval_required: x.target.checked })} /> Require human review</label>
                    </div>
                  </div>
                </td></tr>
              )}
            </Fragment>
          );
        })}</tbody>
      </table></div>
    </Modal>
  );
}
