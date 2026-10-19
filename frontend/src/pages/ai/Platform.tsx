import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan, useToast } from "../../lib/hooks";
import { AiBanner, Field, ItemDrawer, lines, NoProjects, usePolled, useProject } from "./shared";

const money = (n: number | null | undefined) => (n == null ? "—" : `$${n < 1 ? n.toFixed(4) : n.toFixed(2)}`);

// ---------------------------------------------------------------- 32 Images
export function Images() {
  const { pid, projects, picker } = useProject();
  const sizes = useApi<any>("/studio/images/sizes");
  const imgs = useApi<any[]>(pid ? `/studio/images?project_id=${pid}` : null, [pid]);
  const [f, setF] = useState({ title: "", subtitle: "", sizes: ["link_1200x628", "square_1080x1080"], primary: "#1F3A5F", accent: "#F2B705", style: "" });
  const { run, busy } = useAction();
  const can = useCan();
  const toast = useToast();
  if (projects && !projects.length) return <><PageHead title="Image generator" /><NoProjects /></>;
  const tog = (k: string) => setF({ ...f, sizes: f.sizes.includes(k) ? f.sizes.filter((x) => x !== k) : [...f.sizes, k] });
  return (<>
    <PageHead title="Image generator" sub="Branded images in every social size. Text cards render instantly with your brand colours; AI images need OpenAI or Gemini.">{picker}</PageHead>
    <AiBanner image />
    <div className="split">
      <Panel title="Create images">
        <form className="form" onSubmit={(e) => { e.preventDefault(); run(() => api("/studio/images/cards", { body: { project_id: pid, title: f.title, subtitle: f.subtitle || null, sizes: f.sizes, primary: f.primary, accent: f.accent } }), "Images created").then(imgs.reload); }}>
          <Field label="Headline"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required /></Field>
          <Field label="Subtitle"><input className="input" value={f.subtitle} onChange={(e) => setF({ ...f, subtitle: e.target.value })} /></Field>
          <div className="grid g2">
            <Field label="Primary colour"><input className="input" type="color" value={f.primary} onChange={(e) => setF({ ...f, primary: e.target.value })} /></Field>
            <Field label="Accent colour"><input className="input" type="color" value={f.accent} onChange={(e) => setF({ ...f, accent: e.target.value })} /></Field>
          </div>
          <fieldset className="field"><legend>Sizes</legend>{(sizes.data?.sizes || []).map((s: any) => <label key={s.key} className="check"><input type="checkbox" checked={f.sizes.includes(s.key)} onChange={() => tog(s.key)} /> {s.width}×{s.height} <span className="faint small">{s.label}</span></label>)}</fieldset>
          <div className="row">
            {can("create") && <button className="btn primary" disabled={busy || !f.title || !f.sizes.length}>Create text cards</button>}
            {can("create") && <button type="button" className="btn" disabled={busy || !f.title || !f.sizes.length} onClick={() => run(() => api<any>("/studio/images/ai", { body: { project_id: pid, title: f.title, style: f.style || null, size: f.sizes[0] } }))
              .then((r: any) => r && (r.connected ? (toast("AI image queued"), setTimeout(imgs.reload, 4000)) : toast(r.message, true)))}>AI image ({f.sizes[0]})</button>}
          </div>
        </form>
      </Panel>
      <Panel title="Library" actions={<span className="faint small">{imgs.data?.length ?? 0}</span>}>
        {!imgs.data ? <Loading /> : !imgs.data.length ? <Empty title="No images yet" /> :
          <div className="ai-cards">{imgs.data.map((im) => <ImageCard key={im.id} im={im} onDelete={() => run(() => api(`/studio/images/${im.id}`, { method: "DELETE" }), "Deleted").then(imgs.reload)} />)}</div>}
      </Panel>
    </div>
  </>);
}
function ImageCard({ im, onDelete }: { im: any; onDelete: () => void }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let url = ""; let live = true;
    api<Response>(`/studio/images/${im.id}/file`, { raw: true }).then((r) => r.blob()).then((b) => { if (live) { url = URL.createObjectURL(b); setSrc(url); } }).catch(() => {});
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [im.id]);
  return <div className="ai-card">{src ? <img src={src} alt={im.title} /> : <div style={{ height: 160 }} />}
    <div className="meta"><b>{im.title}</b><div className="faint">{im.width}×{im.height} · {im.provider}</div>
      <div className="row" style={{ marginTop: 6 }}><button className="btn sm" onClick={() => download(`/studio/images/${im.id}/file`, `${im.size_key}.png`)}>Download</button><button className="btn ghost sm" onClick={onDelete}>Delete</button></div></div></div>;
}

// ---------------------------------------------------------------- 50 Provider hub
export function Providers() {
  const pv = useApi<any>("/ai/providers");
  const st = useApi<any>("/ai/status");
  const [order, setOrder] = useState<string[]>([]);
  const [prefs, setPrefs] = useState<Record<string, any>>({});
  const [budget, setBudget] = useState<{ monthly_budget: string; alert_pct: number }>({ monthly_budget: "", alert_pct: 80 });
  const { run, busy } = useAction();
  const can = useCan();
  useEffect(() => {
    if (!pv.data) return;
    const ps = [...pv.data.providers].sort((a: any, b: any) => a.position - b.position);
    setOrder(ps.map((p: any) => p.key));
    setPrefs(Object.fromEntries(ps.map((p: any) => [p.key, { enabled: p.enabled, default_model: p.default_model, monthly_budget: p.monthly_budget ?? "" }])));
  }, [pv.data]);
  useEffect(() => { if (st.data?.budget) setBudget({ monthly_budget: st.data.budget.monthly_budget ?? "", alert_pct: st.data.budget.alert_pct ?? 80 }); }, [st.data]);
  if (pv.error) return <ErrorBox error={pv.error} />;
  if (!pv.data) return <Loading />;
  const byKey = Object.fromEntries(pv.data.providers.map((p: any) => [p.key, p]));
  const move = (i: number, d: number) => { const o = [...order]; const j = i + d; if (j < 0 || j >= o.length) return; [o[i], o[j]] = [o[j], o[i]]; setOrder(o); };
  const save = () => run(() => api("/ai/providers", { method: "PUT", body: { order, providers: Object.fromEntries(order.map((k) => [k, {
    enabled: prefs[k].enabled, default_model: prefs[k].default_model || null, monthly_budget: prefs[k].monthly_budget === "" ? null : +prefs[k].monthly_budget }])) } }), "Routing saved").then(pv.reload);
  return (<>
    <PageHead title="AI Provider Hub" sub="Connect Claude, OpenAI, Gemini and Perplexity in Integrations, then set the routing order, default model and budget caps here. Calls fall back to the next provider automatically." />
    <AiBanner />
    <Panel pad={false} title="Routing order" actions={can("manage_integrations") && <button className="btn primary" disabled={busy} onClick={save}>Save routing</button>}>
      <table className="t"><thead><tr><th>#</th><th>Provider</th><th>Status</th><th>Enabled</th><th>Default model</th><th>Monthly cap</th><th>Spent this month</th><th /></tr></thead>
        <tbody>{order.map((k, i) => { const p = byKey[k]; const pr = prefs[k] || {}; return <tr key={k}>
          <td className="num">{i + 1}</td><td><b>{p.name}</b></td><td><Badge kind={p.status === "CONNECTED" ? "pass" : "NOT_CONNECTED"}>{p.status.replace("_", " ")}</Badge></td>
          <td><input type="checkbox" aria-label={`Enable ${p.name}`} checked={!!pr.enabled} onChange={(e) => setPrefs({ ...prefs, [k]: { ...pr, enabled: e.target.checked } })} /></td>
          <td><select className="input" value={pr.default_model || ""} onChange={(e) => setPrefs({ ...prefs, [k]: { ...pr, default_model: e.target.value } })}>{p.models.map((mm: string) => <option key={mm}>{mm}</option>)}</select></td>
          <td><input className="input" style={{ width: 110 }} type="number" min={0} step="1" placeholder="no cap" value={pr.monthly_budget} onChange={(e) => setPrefs({ ...prefs, [k]: { ...pr, monthly_budget: e.target.value } })} /></td>
          <td className="num">{money(p.spent_this_month)}</td>
          <td className="row"><button className="btn ghost sm" aria-label="Move up" onClick={() => move(i, -1)}>↑</button><button className="btn ghost sm" aria-label="Move down" onClick={() => move(i, 1)}>↓</button></td>
        </tr>; })}</tbody></table>
    </Panel>
    <Panel title="Organisation budget">
      <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api("/ai/settings", { method: "PUT", body: { monthly_budget: budget.monthly_budget === "" ? null : +budget.monthly_budget, alert_pct: +budget.alert_pct } }), "Budget saved").then(st.reload); }}>
        <Field label="Monthly AI budget (USD)"><input className="input" type="number" min={0} value={budget.monthly_budget} onChange={(e) => setBudget({ ...budget, monthly_budget: e.target.value })} placeholder="no cap" /></Field>
        <Field label="Alert at %"><input className="input" type="number" min={1} max={100} value={budget.alert_pct} onChange={(e) => setBudget({ ...budget, alert_pct: +e.target.value })} /></Field>
        {can("manage_integrations") && <button className="btn" style={{ alignSelf: "end" }}>Save</button>}
      </form>
      {st.data?.budget && <p className="small">Spent this month: <b>{money(st.data.budget.spent)}</b>{st.data.budget.remaining != null && <> · remaining {money(st.data.budget.remaining)}</>}{st.data.budget.blocked && <> · <Badge kind="fail">calls paused</Badge></>}</p>}
    </Panel>
  </>);
}

// ---------------------------------------------------------------- 51 Prompt library
export function Prompts() {
  const list = useApi<any>("/ai/prompts");
  const { pid, picker } = useProject();
  const [sel, setSel] = useState<any | null>(null);
  const [edit, setEdit] = useState<any | null>(null);
  const [q, setQ] = useState("");
  const prompts: any[] = list.data?.prompts || list.data || [];
  const shown = prompts.filter((p) => !q || (p.name + p.type + (p.description || "")).toLowerCase().includes(q.toLowerCase()));
  const can = useCan();
  return (<>
    <PageHead title="Prompt library" sub="Built-in prompts for every content type plus your own, with {{variables}} filled from the project profile. Every edit is a new version.">
      {picker}{can("create") && <button className="btn primary" onClick={() => setEdit({ name: "", type: "blog", body: "", description: "" })}>New prompt</button>}</PageHead>
    <div className="split">
      <Panel pad={false} title={<input className="input" placeholder="Search prompts" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search prompts" />}>
        {!list.data ? <Loading /> : <table className="t"><tbody>{shown.map((p) => <tr key={p.id} className="click" onClick={() => setSel(p)} style={sel?.id === p.id ? { background: "var(--accent-soft)" } : undefined}>
          <td><b>{p.name}</b> {p.builtin ? <Badge>built-in</Badge> : <Badge kind="info">v{p.version}</Badge>}<div className="faint small">{p.type} · {p.description}</div></td></tr>)}</tbody></table>}
      </Panel>
      {sel ? <PromptDetail p={sel} pid={pid} onEdit={() => setEdit(sel)} onChanged={() => { list.reload(); }} /> : <Panel><Empty title="Pick a prompt" /></Panel>}
    </div>
    {edit && <PromptEditor p={edit} onClose={() => setEdit(null)} onSaved={(p) => { setEdit(null); list.reload(); setSel(p); }} />}
  </>);
}
function PromptDetail({ p, pid, onEdit, onChanged }: { p: any; pid: string; onEdit: () => void; onChanged: () => void }) {
  const versions = useApi<any[]>(`/ai/prompts/${p.id}/versions`, [p.id]);
  const [vars, setVars] = useState<Record<string, string>>({});
  const [out, setOut] = useState<any>(null);
  const { run, busy } = useAction();
  const can = useCan();
  const body = { variables: vars, project_id: pid || null };
  return (<Panel title={p.name} actions={<>
    {can("create") && <button className="btn sm" onClick={() => run(() => api<any>(`/ai/prompts/${p.id}/duplicate`, { body: {} }), "Duplicated").then(onChanged)}>Duplicate</button>}
    {!p.builtin && can("edit") && <button className="btn sm" onClick={onEdit}>Edit</button>}</>}>
    <pre className="ai-pre">{p.body}</pre>
    <h3 className="small" style={{ marginTop: 12 }}>Try it</h3>
    <div className="grid g2">{(p.variables || []).map((v: string) => <Field key={v} label={v} hint="Blank = from project profile"><input className="input" value={vars[v] || ""} onChange={(e) => setVars({ ...vars, [v]: e.target.value })} /></Field>)}</div>
    <div className="row"><button className="btn" disabled={busy} onClick={() => run(() => api(`/ai/prompts/${p.id}/render`, { body })).then(setOut)}>Render</button>
      {can("edit") && <button className="btn primary" disabled={busy} onClick={() => run(() => api(`/ai/prompts/${p.id}/test`, { body })).then(setOut)}>Run with AI</button>}</div>
    {out && <div style={{ marginTop: 10 }}>
      {out.connected === false && <div className="notice nc"><div>{out.message}</div></div>}
      {out.missing?.length > 0 && <p className="small">Missing variables: {out.missing.join(", ")}</p>}
      <pre className="ai-pre">{out.output || out.text || out.prompt || out.rendered}</pre>
      {out.cost != null && <p className="faint small">{out.provider} · {out.model} · {out.tokens_in}+{out.tokens_out} tokens · {money(out.cost)}</p>}
    </div>}
    {!p.builtin && <><h3 className="small" style={{ marginTop: 12 }}>Versions</h3>
      <table className="t"><tbody>{(versions.data || []).map((v) => <tr key={v.version}><td className="num">v{v.version}</td><td className="small">{v.note}</td><td className="faint small">{ago(v.created_at)}</td>
        <td>{v.version !== p.version && can("edit") && <button className="btn sm" onClick={() => run(() => api(`/ai/prompts/${p.id}/restore/${v.version}`, { body: {} }), "Restored").then(onChanged)}>Restore</button>}</td></tr>)}</tbody></table></>}
  </Panel>);
}
function PromptEditor({ p, onClose, onSaved }: { p: any; onClose: () => void; onSaved: (p: any) => void }) {
  const [f, setF] = useState({ name: p.name, type: p.type, body: p.body, description: p.description || "", note: "" });
  const { run, busy } = useAction();
  const save = () => run(() => api(p.id ? `/ai/prompts/${p.id}` : "/ai/prompts", { method: p.id ? "PUT" : "POST", body: { ...f, description: f.description || null, note: f.note || null } }), "Saved").then((r) => r && onSaved(r));
  return <Modal wide title={p.id ? `Edit ${p.name}` : "New prompt"} onClose={onClose} foot={<button className="btn primary" disabled={busy || f.name.length < 2 || f.body.length < 5} onClick={save}>Save</button>}>
    <div className="form">
      <div className="grid g2"><Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Type"><input className="input" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} /></Field></div>
      <Field label="Description"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      <Field label="Prompt" hint="Use {{variable}} — brand, industry, audience, tone, usp, location… come from the project profile"><textarea className="input ai-editor" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} /></Field>
      {p.id && <Field label="Version note"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>}
    </div></Modal>;
}

// ---------------------------------------------------------------- 53 Agent teams
export function AgentTeams() {
  const { pid, projects, picker } = useProject();
  const teams = useApi<any[]>("/ai/agent-teams");
  const runs = useApi<any[]>("/ai/team-runs", [], { poll: 5000 });
  const [edit, setEdit] = useState<any | null>(null);
  const [runFor, setRunFor] = useState<any | null>(null);
  const [view, setView] = useState<string | null>(null);
  const can = useCan();
  if (projects && !projects.length) return <><PageHead title="AI agent teams" /><NoProjects /></>;
  return (<>
    <PageHead title="AI agent teams" sub="Chain steps — data agents, prompt skills and AI prompts — where each step receives the previous step's output: research → outline → write → edit → SEO check.">
      {picker}{can("create") && <button className="btn primary" onClick={() => setEdit({ name: "", description: "", steps: [{ type: "agent", key: "keyword_mining" }, { type: "prompt", key: "agent.outline" }] })}>New team</button>}</PageHead>
    <AiBanner />
    <div className="split">
      <Panel pad={false} title="Teams">{!teams.data ? <Loading /> : !teams.data.length ? <Empty title="No teams yet" /> :
        <table className="t"><tbody>{teams.data.map((t) => <tr key={t.id}><td><b>{t.name}</b><div className="small">{t.steps.map((s: any, i: number) => <span key={i} className="ai-chip">{i + 1}. {s.name}</span>)}</div></td>
          <td className="row">{can("create") && <button className="btn sm primary" onClick={() => setRunFor(t)}>Run</button>}{can("edit") && <button className="btn sm" onClick={() => setEdit(t)}>Edit</button>}</td></tr>)}</tbody></table>}</Panel>
      <Panel pad={false} title="Runs">{!runs.data?.length ? <Empty title="No runs yet" /> :
        <table className="t"><tbody>{runs.data.map((r) => <tr key={r.id} className="click" onClick={() => setView(r.id)}><td><b>{r.input?.topic}</b><div className="faint small">{ago(r.created_at)} · {money(r.cost)}</div></td>
          <td><Badge kind={r.status === "COMPLETED" ? "pass" : r.status === "PARTIAL" ? "medium" : r.status === "FAILED" ? "fail" : "info"}>{r.status}</Badge></td></tr>)}</tbody></table>}</Panel>
    </div>
    {edit && <TeamEditor t={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); teams.reload(); }} />}
    {runFor && <RunTeam t={runFor} pid={pid} onClose={() => setRunFor(null)} onRun={(id) => { setRunFor(null); runs.reload(); setView(id); }} />}
    {view && <RunView id={view} onClose={() => setView(null)} />}
  </>);
}
function TeamEditor({ t, onClose, onSaved }: { t: any; onClose: () => void; onSaved: () => void }) {
  const cat = useApi<any>("/ai/agent-catalog");
  const sk = useApi<any>("/skills");
  const pr = useApi<any>("/ai/prompts");
  const [f, setF] = useState({ name: t.name, description: t.description || "", steps: t.steps.map((s: any) => ({ ...s })) as any[] });
  const { run, busy } = useAction();
  const setStep = (i: number, v: any) => setF({ ...f, steps: f.steps.map((s, j) => (j === i ? { ...s, ...v } : s)) });
  const agentsList = (cat.data?.agents || []).filter((a: any) => a.runnable);
  const prompts: any[] = pr.data?.prompts || [];
  const save = () => run(() => api(t.id ? `/ai/agent-teams/${t.id}` : "/ai/agent-teams", { method: t.id ? "PUT" : "POST",
    body: { name: f.name, description: f.description || null, steps: f.steps.map((s) => ({ type: s.type, name: s.name || undefined, key: s.type === "prompt" && s.template ? undefined : s.key, template: s.type === "prompt" ? s.template : undefined })) } }), "Saved").then((r) => r && onSaved());
  return <Modal wide title={t.id ? `Edit ${t.name}` : "New agent team"} onClose={onClose} foot={<button className="btn primary" disabled={busy || f.name.length < 2 || !f.steps.length} onClick={save}>Save</button>}>
    <div className="form">
      <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <div className="ai-steps">{f.steps.map((s, i) => <div className="ai-step" key={i}>
        <div className="row"><b>Step {i + 1}</b>
          <select className="input" style={{ width: "auto" }} value={s.type} onChange={(e) => setStep(i, { type: e.target.value, key: "", template: "" })}><option value="agent">Data agent</option><option value="skill">Prompt skill</option><option value="prompt">AI prompt</option></select>
          <button className="btn ghost sm" onClick={() => setF({ ...f, steps: f.steps.filter((_, j) => j !== i) })}>Remove</button></div>
        {s.type === "agent" && <select className="input" value={s.key || ""} onChange={(e) => setStep(i, { key: e.target.value })}><option value="">Choose agent…</option>{agentsList.map((a: any) => <option key={a.key} value={a.key}>{a.group} · {a.name}</option>)}</select>}
        {s.type === "skill" && <select className="input" value={s.key || ""} onChange={(e) => setStep(i, { key: e.target.value })}><option value="">Choose skill…</option>{(sk.data?.skills || []).map((x: any) => <option key={x.key} value={x.key}>{x.category} · {x.name}</option>)}</select>}
        {s.type === "prompt" && <>
          <select className="input" value={s.key || ""} onChange={(e) => setStep(i, { key: e.target.value, template: "" })}><option value="">Custom prompt text…</option>{prompts.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}</select>
          {!s.key && <textarea className="input" rows={3} placeholder="Use {{topic}} and {{previous}} (the previous step's output)" value={s.template || ""} onChange={(e) => setStep(i, { template: e.target.value })} />}</>}
      </div>)}</div>
      <button className="btn" type="button" onClick={() => setF({ ...f, steps: [...f.steps, { type: "prompt", key: "", template: "" }] })}>Add step</button>
    </div></Modal>;
}
function RunTeam({ t, pid, onClose, onRun }: { t: any; pid: string; onClose: () => void; onRun: (id: string) => void }) {
  const [f, setF] = useState({ topic: "", brief: "", create_content: "", title: "" });
  const { run, busy } = useAction();
  return <Modal title={`Run ${t.name}`} onClose={onClose} foot={<button className="btn primary" disabled={busy || f.topic.length < 2}
    onClick={() => run<any>(() => api(`/ai/agent-teams/${t.id}/run`, { body: { project_id: pid, topic: f.topic, brief: f.brief || null, create_content: f.create_content || null, title: f.title || null } }), "Run queued").then((r) => r && onRun(r.run_id))}>Run</button>}>
    <div className="form"><Field label="Topic"><input className="input" value={f.topic} onChange={(e) => setF({ ...f, topic: e.target.value })} /></Field>
      <Field label="Brief (optional)"><textarea className="input" rows={3} value={f.brief} onChange={(e) => setF({ ...f, brief: e.target.value })} /></Field>
      <Field label="Save final output as"><select className="input" value={f.create_content} onChange={(e) => setF({ ...f, create_content: e.target.value })}><option value="">Don’t create content</option><option value="blog">Blog draft</option><option value="news">News draft</option><option value="publication">Publication draft</option></select></Field></div></Modal>;
}
function RunView({ id, onClose }: { id: string; onClose: () => void }) {
  const r = usePolled<any>(`/ai/team-runs/${id}`);
  const [item, setItem] = useState<string | null>(null);
  const { run, busy } = useAction();
  if (item) return <ItemDrawer id={item} onClose={() => setItem(null)} />;
  return <Modal wide title="Agent team run" onClose={onClose}
    foot={r.data?.final_output && !r.data?.content_item_id ? <button className="btn" disabled={busy} onClick={() => run<any>(() => api(`/ai/team-runs/${id}/to-content`, { body: { type: "blog" } }), "Draft created").then((x) => x && setItem(x.id))}>Save as blog draft</button>
      : r.data?.content_item_id ? <button className="btn" onClick={() => setItem(r.data.content_item_id)}>Open content</button> : undefined}>
    {!r.data ? <Loading /> : <>
      <p><Badge kind={r.data.status === "COMPLETED" ? "pass" : r.data.status === "PARTIAL" ? "medium" : "info"}>{r.data.status}</Badge> {r.data.input?.topic} · {money(r.data.cost)}</p>
      {(r.data.steps || []).map((s: any) => <div key={s.id} className="ai-step" style={{ marginBottom: 8 }}>
        <div className="row"><b>{s.index + 1}. {s.input?.name || s.input?.step?.name || s.output?.summary?.split(" (")[0] || "Step"}</b>
          <Badge kind={s.state === "COMPLETED" ? "pass" : s.state === "FAILED" ? "fail" : "medium"}>{(s.state || s.status || "").replace("_", " ")}</Badge></div>
        {s.output?.summary && <div className="small">{s.output.summary}</div>}
        {s.state === "NEEDS_CONNECTION" && <div className="notice nc"><div>NOT CONNECTED — connect an AI provider to run this step.</div></div>}
        {s.output?.text && <pre className="ai-pre">{String(s.output.text).slice(0, 4000)}</pre>}</div>)}
      {r.data.final_output && <><h3 className="small">Final output</h3><pre className="ai-pre">{r.data.final_output}</pre></>}
    </>}</Modal>;
}

// ---------------------------------------------------------------- 54 Usage
export function Usage() {
  const [days, setDays] = useState(30);
  const [group, setGroup] = useState("provider");
  const u = useApi<any>(`/ai/usage?days=${days}&group=${group}`, [days, group]);
  return (<>
    <PageHead title="AI cost & usage" sub="Every AI call is recorded with provider, model, tokens and cost — by provider, model, day, project, user or task.">
      <select className="input" style={{ width: "auto" }} value={days} onChange={(e) => setDays(+e.target.value)} aria-label="Period">{[7, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}</select>
      <select className="input" style={{ width: "auto" }} value={group} onChange={(e) => setGroup(e.target.value)} aria-label="Group by">{["provider", "model", "day", "project", "user", "task"].map((g) => <option key={g}>{g}</option>)}</select>
      <button className="btn" onClick={() => download(`/ai/usage.csv?days=${days}&group=${group}`, "ai-usage.csv")}>CSV</button>
    </PageHead>
    {u.error ? <ErrorBox error={u.error} /> : !u.data ? <Loading /> : <>
      <div className="grid g4">{[["Calls", u.data.totals.calls], ["Tokens in", u.data.totals.tokens_in.toLocaleString()], ["Tokens out", u.data.totals.tokens_out.toLocaleString()], ["Cost", money(u.data.totals.cost)]].map(([k, v]) =>
        <div className="panel kpi" key={k as string}><div className="panel-body"><div className="faint small">{k}</div><div className="num" style={{ fontSize: 26 }}>{v}</div></div></div>)}</div>
      <p className="faint small">{u.data.quota_note}{u.data.budget?.monthly_budget != null && <> · Budget {money(u.data.budget.spent)} of {money(u.data.budget.monthly_budget)} this month</>}</p>
      {u.data.daily?.length > 0 && <Panel title="Daily cost"><div style={{ height: 220 }}><ResponsiveContainer><BarChart data={u.data.daily}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" /><XAxis dataKey="key" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip /><Bar dataKey="cost" fill="var(--accent)" /></BarChart></ResponsiveContainer></div></Panel>}
      <Panel pad={false} title={`By ${group}`}>{!u.data.rows.length ? <Empty title="No AI usage yet">Usage appears once an AI provider is connected and used.</Empty> :
        <table className="t"><thead><tr><th>{group}</th><th className="r">Calls</th><th className="r">Tokens in</th><th className="r">Tokens out</th><th className="r">Images</th><th className="r">Cost</th></tr></thead>
          <tbody>{u.data.rows.map((r: any) => <tr key={r.key ?? r.label}><td>{r.label}</td><td className="r num">{r.calls}</td><td className="r num">{r.tokens_in.toLocaleString()}</td><td className="r num">{r.tokens_out.toLocaleString()}</td><td className="r num">{r.images}</td><td className="r num">{money(r.cost)}</td></tr>)}</tbody></table>}</Panel>
    </>}
  </>);
}

// ---------------------------------------------------------------- 61 Topical map
export function TopicalMap() {
  const { pid, projects, picker } = useProject();
  const maps = useApi<any[]>(pid ? `/topical-maps?project_id=${pid}` : null, [pid]);
  const [sel, setSel] = useState<string | null>(null);
  const [f, setF] = useState({ name: "", seeds: "", keywords: "", min_cluster: 2 });
  const { run, busy } = useAction();
  const can = useCan();
  useEffect(() => { if (maps.data?.length && !sel) setSel(maps.data[0].id); }, [maps.data]);
  if (projects && !projects.length) return <><PageHead title="Topical map" /><NoProjects /></>;
  return (<>
    <PageHead title="Topical map" sub="Parent/child keyword clustering into pillars and clusters, from your seeds, Search Console queries and imported keywords — with which page covers each topic.">{picker}</PageHead>
    <div className="split">
      <div className="stack">
        <Panel title="New topical map"><form className="form" onSubmit={(e) => { e.preventDefault(); run<any>(() => api("/topical-maps", { body: { project_id: pid, name: f.name, seeds: lines(f.seeds), keywords: f.keywords || null, min_cluster: f.min_cluster } }), "Map built").then((r) => { if (r) { setSel(r.id); maps.reload(); } }); }}>
          <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Seed topics" hint="Comma or line separated"><input className="input" value={f.seeds} onChange={(e) => setF({ ...f, seeds: e.target.value })} /></Field>
          <Field label="Extra keywords" hint="Optional — one per line (Search Console queries are added automatically)"><textarea className="input" rows={4} value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} /></Field>
          <div>{can("create") && <button className="btn primary" disabled={busy || f.name.length < 2}>Build map</button>}</div></form></Panel>
        <Panel pad={false} title="Maps">{!maps.data?.length ? <Empty title="No maps yet" /> : <table className="t"><tbody>{maps.data.map((m) => <tr key={m.id} className="click" onClick={() => setSel(m.id)}
          style={sel === m.id ? { background: "var(--accent-soft)" } : undefined}><td><b>{m.name}</b><div className="faint small">{m.stats?.pillars} pillars · {m.stats?.keywords} keywords · {m.stats?.coverage_pct}% covered</div></td></tr>)}</tbody></table>}</Panel>
      </div>
      {sel ? <MapView id={sel} /> : <Panel><Empty title="Build or pick a map" /></Panel>}
    </div>
  </>);
}
const COV: Record<string, string> = { covered: "pass", partial: "medium", gap: "fail" };
function MapView({ id }: { id: string }) {
  const m = useApi<any>(`/topical-maps/${id}`, [id]);
  if (!m.data) return <Panel><Loading /></Panel>;
  const node = (n: any): JSX.Element => <li key={n.id}><Badge kind={COV[n.coverage_status] || ""}>{n.coverage_status || "gap"}</Badge> <b>{n.keyword}</b> <span className="faint small">{n.kind} · {n.intent} · {n.impressions ?? 0} impr.</span>
    {n.coverage_url && <div className="mono-url">{n.coverage_url}</div>}
    {n.keywords?.length > 0 && <div>{n.keywords.slice(0, 12).map((k: any) => <span key={k.keyword} className="ai-chip">{k.keyword}</span>)}</div>}
    {n.children?.length > 0 && <ul>{n.children.map(node)}</ul>}</li>;
  return <Panel title={m.data.name} actions={<span className="faint small">{m.data.stats?.covered} covered · {m.data.stats?.partial} partial · {m.data.stats?.gap} gaps</span>}>
    <ul className="ai-tree">{(m.data.tree || []).map(node)}</ul></Panel>;
}

// ---------------------------------------------------------------- 62 Content plan
export function ContentPlan() {
  const { pid, projects, picker } = useProject();
  const maps = useApi<any[]>(pid ? `/topical-maps?project_id=${pid}` : null, [pid]);
  const plans = useApi<any[]>(pid ? `/content-plans?project_id=${pid}` : null, [pid]);
  const [sel, setSel] = useState<string | null>(null);
  const [f, setF] = useState({ topical_map_id: "", name: "", per_week: 2 });
  const { run, busy } = useAction();
  const can = useCan();
  useEffect(() => { if (plans.data?.length && !sel) setSel(plans.data[0].id); }, [plans.data]);
  if (projects && !projects.length) return <><PageHead title="Content plan" /><NoProjects /></>;
  return (<>
    <PageHead title="Content plan builder" sub="Pillar + cluster plan from a topical map: weekly schedule, briefs, internal-link map, then drafts and tasks in one click.">{picker}</PageHead>
    <div className="split">
      <div className="stack">
        <Panel title="New plan">{!maps.data?.length ? <p className="muted">Build a topical map first.</p> :
          <form className="form" onSubmit={(e) => { e.preventDefault(); run<any>(() => api("/content-plans", { body: { ...f } }), "Plan created").then((r) => { if (r) { setSel(r.id); plans.reload(); } }); }}>
            <Field label="Topical map"><select className="input" value={f.topical_map_id} onChange={(e) => setF({ ...f, topical_map_id: e.target.value })} required><option value="">Choose…</option>{maps.data.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></Field>
            <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label="Posts per week"><input className="input" type="number" min={1} max={14} value={f.per_week} onChange={(e) => setF({ ...f, per_week: +e.target.value })} /></Field>
            <div>{can("create") && <button className="btn primary" disabled={busy || !f.topical_map_id || f.name.length < 2}>Create plan</button>}</div></form>}</Panel>
        <Panel pad={false} title="Plans">{!plans.data?.length ? <Empty title="No plans yet" /> : <table className="t"><tbody>{plans.data.map((p) => <tr key={p.id} className="click" onClick={() => setSel(p.id)}
          style={sel === p.id ? { background: "var(--accent-soft)" } : undefined}><td><b>{p.name}</b><div className="faint small">{p.settings?.per_week}/week from {p.settings?.start}</div></td></tr>)}</tbody></table>}</Panel>
      </div>
      {sel ? <PlanView id={sel} /> : <Panel><Empty title="Create or pick a plan" /></Panel>}
    </div>
  </>);
}
function PlanView({ id }: { id: string }) {
  const p = useApi<any>(`/content-plans/${id}`, [id]);
  const [pick, setPick] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<string | null>(null);
  const { run, busy } = useAction();
  const can = useCan();
  const chosen = useMemo(() => Object.keys(pick).filter((k) => pick[k]), [pick]);
  if (!p.data) return <Panel><Loading /></Panel>;
  return (<div className="stack">
    <Panel pad={false} title={p.data.name} actions={can("create") && <button className="btn primary" disabled={busy || !chosen.length}
      onClick={() => run(() => api(`/content-plans/${id}/convert`, { body: { brief_ids: chosen, create_tasks: true } }), "Drafts and tasks created").then(() => { setPick({}); p.reload(); })}>Create drafts + tasks ({chosen.length})</button>}>
      <table className="t"><thead><tr><th /><th>Week</th><th>Title</th><th>Role</th><th>Action</th><th>Status</th></tr></thead>
        <tbody>{(p.data.briefs || []).map((b: any) => <tr key={b.id}>
          <td><input type="checkbox" aria-label={`Select ${b.title}`} disabled={!!b.content_item_id} checked={!!pick[b.id]} onChange={(e) => setPick({ ...pick, [b.id]: e.target.checked })} /></td>
          <td className="num">{b.week}</td><td><b>{b.title}</b><div className="faint small">{b.intent} · demand {b.demand}</div></td><td>{b.role}</td><td className="small">{b.action}</td>
          <td>{b.content_item_id ? <button className="btn sm" onClick={() => setOpen(b.content_item_id)}>{b.content_state || "draft"}</button> : <Badge>{b.status}</Badge>}</td></tr>)}</tbody></table>
    </Panel>
    <Panel pad={false} title="Internal-link map" actions={<span className="faint small">{p.data.internal_link_map?.count} links</span>}>
      <table className="t"><thead><tr><th>From</th><th>To</th><th>Anchor</th><th>Type</th></tr></thead>
        <tbody>{(p.data.internal_link_map?.links || []).slice(0, 200).map((l: any, i: number) => <tr key={i}><td className="mono-url">{l.from}</td><td className="mono-url">{l.to}</td><td>{l.anchor}</td><td className="faint small">{l.type}</td></tr>)}</tbody></table>
    </Panel>
    {open && <ItemDrawer id={open} onClose={() => { setOpen(null); p.reload(); }} />}
  </div>);
}
