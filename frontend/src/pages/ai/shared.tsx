import { ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, NotConnected, Panel, Select } from "../../components/ui";
import { api, download } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan, useProjectSelection } from "../../lib/hooks";
import "./ai.css";

export type AiStatus = { connected: boolean; providers: string[]; image_connected: boolean; message?: string;
  budget?: { monthly_budget: number | null; spent: number; remaining: number | null; blocked: boolean; alert: boolean } };

export function useProject() {
  const { data: projects } = useApi<any[]>("/projects");
  const [pid, setPid] = useProjectSelection(projects);
  const picker = projects && projects.length ? (
    <Select value={pid} onChange={setPid} label="Project" options={projects.map((p) => ({ value: p.id, label: p.name }))} />
  ) : null;
  return { pid, setPid, projects, picker };
}

export function NoProjects() {
  return <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>}>
    Content, prompts and plans are organised per project.</Empty></Panel>;
}

export function AiBanner({ image = false }: { image?: boolean }) {
  const { data } = useApi<AiStatus>("/ai/status");
  if (!data) return null;
  const ok = image ? data.image_connected : data.connected;
  if (data.budget?.blocked) return <div className="notice err">Monthly AI budget reached — AI calls are paused. Raise the cap in <Link to="/ai/providers">AI Provider Hub</Link>.</div>;
  if (ok) return null;
  return <div style={{ marginBottom: 14 }}><NotConnected what={image ? "AI image generation (OpenAI / Gemini)" : "AI provider"}>
    is <b>NOT CONNECTED</b>. {image ? "Branded text cards still work." : "Briefs, outlines, schema, editing, approval and export still work — AI drafting needs Claude, OpenAI, Gemini or Perplexity."} <Link to="/integrations">Connect a provider</Link>.
  </NotConnected></div>;
}

export const STATE_KIND: Record<string, string> = { draft: "", generated: "info", review: "medium", approved: "pass", scheduled: "info", published: "pass", rejected: "fail" };

export function StateBadge({ s }: { s: string }) { return <Badge kind={STATE_KIND[s] || ""}>{s}</Badge>; }

/** Poll a job-backed resource until it leaves QUEUED/RUNNING. */
export function usePolled<T = any>(path: string | null, done: (d: T) => boolean = (d: any) => !["QUEUED", "RUNNING"].includes(d?.status)) {
  const r = useApi<T>(path, [path]);
  useEffect(() => {
    if (!r.data || done(r.data)) return;
    const t = setTimeout(() => r.reload(), 1500);
    return () => clearTimeout(t);
  }, [r.data]);
  return r;
}

/** List of content items of the given types, with an editor drawer. */
export function ItemList({ pid, types, title = "Content", empty, reloadKey }: { pid: string; types: string; title?: string; empty?: ReactNode; reloadKey?: any }) {
  const list = useApi<any[]>(pid ? `/studio/items?project_id=${pid}&type=${types}` : null, [pid, types, reloadKey], { poll: 5000 });
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Panel pad={false} title={title} actions={<span className="faint small">{list.data?.length ?? 0} items</span>}>
      {list.loading && !list.data ? <Loading /> : !list.data?.length ? <Empty title="Nothing yet">{empty}</Empty> : (
        <table className="t"><thead><tr><th>Title</th><th>State</th><th>Version</th><th>Updated</th></tr></thead>
          <tbody>{list.data.map((it) => <tr key={it.id} className="click" onClick={() => setOpen(it.id)}>
            <td><b>{it.title}</b><div className="faint small">{it.type}{it.channel ? ` · ${it.channel}` : ""}{it.generation && !["COMPLETED", "QUEUED", "RUNNING"].includes(it.generation.status) ? ` · AI: ${it.generation.status.replace("_", " ").toLowerCase()}` : ""}</div></td>
            <td><StateBadge s={it.state} /></td><td className="num">v{it.current_version}</td><td className="faint small">{ago(it.updated_at)}</td></tr>)}</tbody></table>)}
      {open && <ItemDrawer id={open} onClose={() => { setOpen(null); list.reload(); }} />}
    </Panel>
  );
}

export function ItemDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const it = useApi<any>(`/studio/items/${id}`, [id], { poll: 4000 });
  const [body, setBody] = useState<string | null>(null);
  const [tab, setTab] = useState("write");
  const [note, setNote] = useState("");
  const { run, busy } = useAction();
  const can = useCan();
  useEffect(() => { if (it.data && body === null) setBody(it.data.body || ""); }, [it.data]);
  if (it.error) return <Drawer title="Content" onClose={onClose}><ErrorBox error={it.error} /></Drawer>;
  if (!it.data) return <Drawer title="Content" onClose={onClose}><Loading /></Drawer>;
  const d = it.data;
  const editable = ["draft", "generated"].includes(d.state);
  const dirty = body !== null && body !== (d.body || "");
  const gen = d.generation;
  const save = () => run(() => api(`/studio/items/${id}/body`, { method: "PUT", body: { body, note: note || null } }), "Saved as a new version").then(() => { setNote(""); it.reload(); });
  const generate = () => run(() => api(`/studio/items/${id}/generate`, { body: { instructions: note || null } }), "Generation queued").then(() => it.reload());
  const move = (to: string) => run(() => api(`/studio/items/${id}/transition`, { body: { to, note: note || null } }), `Moved to ${to}`).then(() => { setNote(""); it.reload(); });
  const fn = (d.seo?.slug || "content");
  return (
    <Drawer title={<span>{d.title} <StateBadge s={d.state} /></span>} onClose={onClose}
      foot={<div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
        {editable && dirty && <button className="btn primary" disabled={busy} onClick={save}>Save version</button>}
        {editable && can("edit") && <button className="btn" disabled={busy} onClick={generate}>Generate with AI</button>}
        {(d.allowed || []).filter((a: any) => a.to !== d.state).map((a: any) => (
          <button key={a.to + a.action} className={`btn ${a.action === "approve" ? "primary" : ""}`} disabled={busy || !can(a.permission)}
            title={can(a.permission) ? "" : `Needs the ${a.permission} permission`}
            onClick={() => move(a.action === "reject" ? "rejected" : a.to)}>{a.action}</button>))}
        <button className="btn ghost" onClick={() => download(`/studio/items/${id}/export?format=md${["approved", "scheduled", "published"].includes(d.state) ? "" : "&draft=true"}`, `${fn}.md`)}>Markdown</button>
        <button className="btn ghost" onClick={() => download(`/studio/items/${id}/export?format=html${["approved", "scheduled", "published"].includes(d.state) ? "" : "&draft=true"}`, `${fn}.html`)}>HTML</button>
        <button className="btn ghost" onClick={() => download(`/studio/items/${id}/export?format=pdf${["approved", "scheduled", "published"].includes(d.state) ? "" : "&draft=true"}`, `${fn}.pdf`)}>PDF</button>
      </div>}>
      {d.notes && d.state === "draft" && <div className="notice warn"><div><b>Changes requested:</b> {d.notes}</div></div>}
      {gen && !["COMPLETED", "QUEUED", "RUNNING"].includes(gen.status) && <div className="notice nc"><div><b>Generation didn’t run ({gen.status.replace("_", " ")}):</b> {gen.error || gen.message}</div></div>}
      {gen?.status === "QUEUED" || gen?.status === "RUNNING" ? <div className="notice"><div>Generating… this updates automatically.</div></div> : null}
      <div className="tabs" role="tablist">
        {["write", "seo", "schema", "versions", "history"].map((t) => <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}
      </div>
      {tab === "write" && <div className="stack">
        <textarea className="input ai-editor" value={body ?? ""} onChange={(e) => setBody(e.target.value)} readOnly={!editable} aria-label="Content body (Markdown)"
          placeholder={editable ? "Write in Markdown, or click Generate with AI." : ""} />
        <label className="field">{editable ? "Version note / AI instructions / review notes" : "Review notes"}
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder={d.state === "review" ? "Required when requesting changes" : "Optional"} /></label>
        {!editable && <p className="faint small">{d.state} content is locked. Request changes to edit it again.</p>}
      </div>}
      {tab === "seo" && <dl className="kv">
        <dt>Meta title</dt><dd>{d.seo?.meta_title || "—"} <span className="faint small">{(d.seo?.meta_title || "").length} chars</span></dd>
        <dt>Meta description</dt><dd>{d.seo?.meta_description || "—"} <span className="faint small">{(d.seo?.meta_description || "").length} chars</span></dd>
        <dt>Slug</dt><dd className="mono-url">{d.seo?.slug || "—"}</dd>
        {d.data?.primary_keyword && <><dt>Primary keyword</dt><dd>{d.data.primary_keyword}</dd></>}
        {d.data?.supporting_keywords?.length > 0 && <><dt>Supporting</dt><dd>{d.data.supporting_keywords.join(", ")}</dd></>}
        {d.data?.outline?.length > 0 && <><dt>Outline</dt><dd><ol>{d.data.outline.map((o: any, i: number) => <li key={i}>{o.heading || o.h2 || o.title || JSON.stringify(o)}</li>)}</ol></dd></>}
        {d.data?.faqs?.length > 0 && <><dt>FAQs</dt><dd><ul>{d.data.faqs.map((f: any, i: number) => <li key={i}>{f.question}</li>)}</ul></dd></>}
      </dl>}
      {tab === "schema" && <div>
        {d.schema_errors?.length ? <div className="notice err">{d.schema_errors.join("; ")}</div> : <p className="small"><Badge kind="pass">valid</Badge> JSON-LD ready to paste into the page head.</p>}
        <pre className="ai-pre">{JSON.stringify(d.schema, null, 2)}</pre>
      </div>}
      {tab === "versions" && (!d.versions?.length ? <Empty title="No versions yet" /> :
        <table className="t"><thead><tr><th>Version</th><th>Origin</th><th>When</th><th /></tr></thead><tbody>
          {d.versions.map((v: any) => <tr key={v.version}>
            <td className="num">v{v.version}</td><td>{v.origin}{v.model ? ` · ${v.model}` : ""}<div className="faint small">{v.note}</div></td>
            <td className="faint small">{ago(v.created_at)}</td>
            <td>{editable && (v.version) !== d.current_version && <button className="btn sm" onClick={() => run(() => api(`/studio/items/${id}/versions/${v.version}/restore`, { body: {} }), "Restored").then(() => { setBody(null); it.reload(); })}>Restore</button>}</td>
          </tr>)}</tbody></table>)}
      {tab === "history" && <ul className="timeline">{(d.transitions || []).map((t: any) => <li key={t.id}><b>{t.action}</b> {t.from_state} → {t.to_state} <span className="faint small">{ago(t.at)}</span>{t.note && <div className="small">{t.note}</div>}</li>)}
        {!d.transitions?.length && <li className="faint">Created as draft.</li>}</ul>}
    </Drawer>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field">{label}{hint && <span className="hint">{hint}</span>}{children}</label>;
}

export function lines(s: string): string[] { return s.split(/\n|,/).map((x) => x.trim()).filter(Boolean); }
