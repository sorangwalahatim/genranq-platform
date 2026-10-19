import { useEffect, useMemo, useState } from "react";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { AiBanner, Field, lines, NoProjects, usePolled, useProject } from "./shared";

const LIST_FIELDS = new Set(["primary_keywords", "secondary_keywords", "long_tail_keywords", "competitors", "competitor_urls", "pain_points", "locations"]);

export default function Skills() {
  const { pid, projects, picker } = useProject();
  const lib = useApi<any>("/skills");
  const runs = useApi<any[]>(pid ? `/projects/${pid}/skill-runs` : null, [pid], { poll: 6000 });
  const [tab, setTab] = useState("library");
  const [runSkill, setRunSkill] = useState<any | null>(null);
  const [view, setView] = useState<string | null>(null);
  const [edit, setEdit] = useState<any | null>(null);
  const can = useCan();
  const groups = useMemo(() => {
    const g: Record<string, any[]> = {};
    for (const s of lib.data?.skills || []) (g[s.category] ||= []).push(s);
    return Object.entries(g);
  }, [lib.data]);
  if (projects && !projects.length) return <><PageHead title="Skills" /><NoProjects /></>;
  return (<>
    <PageHead title="Skills" sub="Your analysis prompts as project skills: each collects real evidence (your pages, competitor pages, Search Console), runs the analysis, and produces tables you can export to Excel. AI adds the narrative when a provider is connected.">
      {picker}{can("create") && <button className="btn" onClick={() => setEdit({ key: "", name: "", category: "custom", workstream: "content", description: "", template: "", evidence: ["client_page"], output_columns: [] })}>New custom skill</button>}</PageHead>
    <AiBanner />
    <div className="tabs" role="tablist">{[["library", "Library"], ["runs", "Runs"], ["profile", "Project profile"]].map(([k, l]) =>
      <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
    {tab === "library" && (lib.error ? <ErrorBox error={lib.error} /> : !lib.data ? <Loading /> : groups.map(([cat, list]) => <Panel key={cat} pad={false} title={cat}>
      <table className="t"><tbody>{list.map((s) => <tr key={s.key}>
        <td><b>{s.name}</b> {s.builtin ? null : <Badge kind="info">custom v{s.version}</Badge>}<div className="small">{s.description}</div>
          <div className="faint small">Evidence: {(s.evidence || []).join(", ") || "profile only"}{s.output_columns?.length ? ` · ${s.output_columns.length} columns` : ""}</div></td>
        <td className="row" style={{ justifyContent: "flex-end" }}>
          {!s.builtin && can("edit") && <button className="btn sm" onClick={() => setEdit(s)}>Edit</button>}
          {can("create") && <button className="btn sm primary" onClick={() => setRunSkill(s)}>Run</button>}</td></tr>)}</tbody></table></Panel>))}
    {tab === "runs" && <Panel pad={false} title="Runs on this project">{!runs.data?.length ? <Empty title="No runs yet" /> :
      <table className="t"><thead><tr><th>Skill</th><th>Status</th><th>Mode</th><th>When</th></tr></thead><tbody>{runs.data.map((r) => <tr key={r.id} className="click" onClick={() => setView(r.id)}>
        <td><b>{r.skill_key}</b><div className="faint small">{r.summary}</div></td><td><Badge kind={r.status === "COMPLETED" ? "pass" : r.status === "NEEDS_DATA" ? "medium" : r.status === "FAILED" ? "fail" : "info"}>{r.status}</Badge></td>
        <td>{r.mode}</td><td className="faint small">{ago(r.created_at)}</td></tr>)}</tbody></table>}</Panel>}
    {tab === "profile" && pid && <Profile pid={pid} />}
    {runSkill && <RunModal s={runSkill} pid={pid} onClose={() => setRunSkill(null)} onRun={(id) => { setRunSkill(null); runs.reload(); setView(id); }} />}
    {view && <RunView id={view} onClose={() => setView(null)} />}
    {edit && <SkillEditor s={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); lib.reload(); }} />}
  </>);
}

function RunModal({ s, pid, onClose, onRun }: { s: any; pid: string; onClose: () => void; onRun: (id: string) => void }) {
  const prof = useApi<any>(`/projects/${pid}/profile`, [pid]);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [ai, setAi] = useState(true);
  const { run, busy } = useAction();
  const fields: string[] = Array.from(new Set([...(s.inputs || []), ...((s.evidence || []).includes("competitor_pages") ? ["competitor_urls"] : []), ...((s.evidence || []).includes("client_page") ? ["url"] : [])]));
  useEffect(() => {
    const e = prof.data?.effective || {};
    const v: Record<string, string> = {};
    for (const f of fields) {
      const src = f === "url" ? e.website : f === "competitor_urls" ? e.competitors : e[f];
      v[f] = Array.isArray(src) ? src.join("\n") : src || "";
    }
    setVals(v);
  }, [prof.data]);
  const go = () => {
    const inputs: Record<string, any> = {};
    for (const [k, v] of Object.entries(vals)) if (v.trim()) inputs[k] = LIST_FIELDS.has(k) ? lines(v) : v.trim();
    run<any>(() => api(`/projects/${pid}/skills/${s.key}/run`, { body: { inputs, use_ai: ai } }), "Skill queued").then((r) => r && onRun(r.run_id));
  };
  return <Modal title={`Run: ${s.name}`} onClose={onClose} foot={<button className="btn primary" disabled={busy} onClick={go}>Run skill</button>}>
    <p className="small">{s.description}</p>
    <div className="form">{fields.map((f) => <Field key={f} label={f.replace(/_/g, " ")} hint={LIST_FIELDS.has(f) ? "One per line (max 10 URLs)" : "Prefilled from the project profile"}>
      {LIST_FIELDS.has(f) ? <textarea className="input" rows={3} value={vals[f] || ""} onChange={(e) => setVals({ ...vals, [f]: e.target.value })} />
        : <input className="input" value={vals[f] || ""} onChange={(e) => setVals({ ...vals, [f]: e.target.value })} />}</Field>)}
      <label className="check"><input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} /> Add the AI narrative (needs a connected AI provider)</label></div></Modal>;
}

function RunView({ id, onClose }: { id: string; onClose: () => void }) {
  const r = usePolled<any>(`/skill-runs/${id}`);
  const [tab, setTab] = useState<string | null>(null);
  const d = r.data;
  const tables = Object.entries(d?.output?.tables || {}).filter(([, rows]: any) => rows?.length) as [string, any[]][];
  const cur = tab || tables[0]?.[0] || "notes";
  return <Modal wide title={d ? `${d.skill_key} · ${d.status}` : "Skill run"} onClose={onClose}
    foot={d?.status === "COMPLETED" ? <button className="btn primary" onClick={() => download(`/skill-runs/${id}/xlsx`, `${d.skill_key}.xlsx`)}>Download Excel</button> : undefined}>
    {!d || ["QUEUED", "RUNNING"].includes(d.status) ? <div className="empty" aria-busy="true">Collecting evidence and analysing…</div> : <>
      <p className="small">{d.summary}</p>
      {(d.output?.notes || []).map((n: string, i: number) => <div key={i} className="notice nc" style={{ marginBottom: 6 }}><div>{n}</div></div>)}
      <div className="tabs" role="tablist">{tables.map(([n]) => <button key={n} className={cur === n ? "on" : ""} onClick={() => setTab(n)}>{n}</button>)}
        {d.output?.ai_text && <button className={cur === "ai" ? "on" : ""} onClick={() => setTab("ai")}>AI narrative</button>}
        <button className={cur === "prompt" ? "on" : ""} onClick={() => setTab("prompt")}>Prompt used</button></div>
      {cur === "ai" && <pre className="ai-pre">{d.output.ai_text}</pre>}
      {cur === "prompt" && <pre className="ai-pre">{d.prompt}</pre>}
      {tables.filter(([n]) => n === cur).map(([n, rows]) => <SortTable key={n} rows={rows} cols={d.output?.column_order?.[n] || Object.keys(rows[0] || {})} />)}
    </>}</Modal>;
}

function SortTable({ rows, cols }: { rows: any[]; cols: string[] }) {
  const [sort, setSort] = useState<{ k: string; d: number } | null>(null);
  const sorted = useMemo(() => !sort ? rows : [...rows].sort((a, b) => {
    const x = a[sort.k], y = b[sort.k];
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""))) * sort.d;
  }), [rows, sort]);
  return <div style={{ overflow: "auto", maxHeight: 520 }}><table className="t"><thead><tr>{cols.map((c) => <th key={c} className="click" onClick={() => setSort({ k: c, d: sort?.k === c ? -sort.d : 1 })}>{c}{sort?.k === c ? (sort.d > 0 ? " ▲" : " ▼") : ""}</th>)}</tr></thead>
    <tbody>{sorted.slice(0, 500).map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className="small">{typeof r[c] === "object" && r[c] !== null ? JSON.stringify(r[c]) : String(r[c] ?? "")}</td>)}</tr>)}</tbody></table>
    {rows.length > 500 && <p className="faint small">Showing 500 of {rows.length} rows — download Excel for all.</p>}</div>;
}

function Profile({ pid }: { pid: string }) {
  const p = useApi<any>(`/projects/${pid}/profile`, [pid]);
  const [f, setF] = useState<Record<string, string>>({});
  const { run, busy } = useAction();
  const can = useCan();
  useEffect(() => { if (p.data) setF(Object.fromEntries(p.data.fields.map((x: any) => { const v = p.data.data?.[x.key]; return [x.key, Array.isArray(v) ? v.join("\n") : v || ""]; }))); }, [p.data]);
  if (!p.data) return <Loading />;
  const save = () => run(() => api(`/projects/${pid}/profile`, { method: "PUT", body: Object.fromEntries(Object.entries(f).map(([k, v]) => [k, LIST_FIELDS.has(k) ? lines(v) : v || null])) }), "Profile saved").then(p.reload);
  return <Panel title="Project profile" actions={can("edit") && <button className="btn primary" disabled={busy} onClick={save}>Save</button>}>
    <p className="faint small">Every skill, generator and prompt fills its placeholders from this profile.</p>
    <div className="grid g2">{p.data.fields.map((x: any) => <Field key={x.key} label={x.label} hint={LIST_FIELDS.has(x.key) ? "One per line" : undefined}>
      {LIST_FIELDS.has(x.key) ? <textarea className="input" rows={3} value={f[x.key] || ""} onChange={(e) => setF({ ...f, [x.key]: e.target.value })} />
        : <input className="input" value={f[x.key] || ""} onChange={(e) => setF({ ...f, [x.key]: e.target.value })} />}</Field>)}</div>
  </Panel>;
}

function SkillEditor({ s, onClose, onSaved }: { s: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ ...s, inputs: (s.inputs || []).join(", "), output_columns: (s.output_columns || []).join(", ") });
  const { run, busy } = useAction();
  const ev = ["client_page", "competitor_pages", "gsc_queries", "keywords"];
  const save = () => run(() => api(s.key ? `/skills/${s.key}` : "/skills", { method: s.key ? "PUT" : "POST", body: {
    key: f.key, name: f.name, category: f.category || "custom", workstream: f.workstream, description: f.description || null, template: f.template,
    inputs: lines(f.inputs), evidence: f.evidence, output_columns: lines(f.output_columns) } }), "Skill saved").then((r) => r && onSaved());
  return <Modal wide title={s.key ? `Edit ${s.name} (v${s.version})` : "New custom skill"} onClose={onClose} foot={<button className="btn primary" disabled={busy || !f.key || !f.name || !f.template} onClick={save}>Save</button>}>
    <div className="form">
      <div className="grid g2"><Field label="Key" hint="a-z, 0-9, _"><input className="input" value={f.key} disabled={!!s.key} onChange={(e) => setF({ ...f, key: e.target.value })} /></Field>
        <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field></div>
      <Field label="Description"><input className="input" value={f.description || ""} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
      <Field label="Prompt template" hint="{{brand}}, {{industry}}, {{primary_keywords}}… from the profile; {{EVIDENCE}} = collected evidence"><textarea className="input ai-editor" value={f.template} onChange={(e) => setF({ ...f, template: e.target.value })} /></Field>
      <fieldset className="field"><legend>Evidence to collect</legend>{ev.map((e) => <label key={e} className="check"><input type="checkbox" checked={f.evidence.includes(e)}
        onChange={() => setF({ ...f, evidence: f.evidence.includes(e) ? f.evidence.filter((x: string) => x !== e) : [...f.evidence, e] })} /> {e.replace("_", " ")}</label>)}</fieldset>
      <div className="grid g2"><Field label="Extra inputs" hint="comma separated"><input className="input" value={f.inputs} onChange={(e) => setF({ ...f, inputs: e.target.value })} /></Field>
        <Field label="Output table columns" hint="comma separated — AI must return this table"><input className="input" value={f.output_columns} onChange={(e) => setF({ ...f, output_columns: e.target.value })} /></Field></div>
      {s.history?.length > 0 && <p className="faint small">{s.history.length} earlier version(s) kept.</p>}
    </div></Modal>;
}
