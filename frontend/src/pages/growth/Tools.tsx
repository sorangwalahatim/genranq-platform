import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { ago, fmtDateTime } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Copy, Status } from "./shared";
import { TOOL_META, ToolResult } from "./toolviews";

export function ToolsHome() {
  const { data, error, reload } = useApi<any>("/tools");
  const { data: projects } = useApi<any[]>("/projects");
  const { data: leads } = useApi<any[]>("/tools/leads");
  const { run } = useAction();
  const can = useCan();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <>
      <PageHead title="Free tools" sub="Run the SEO tools yourself, or publish them as email-gated lead magnets on your site (public pages, per-IP rate limited).">
        {can("export") && <button className="btn" onClick={() => download("/tools/leads/export.csv", "free-tool-leads.csv")}>Export captured emails</button>}
      </PageHead>
      <div className="grid g3" style={{ marginBottom: 16 }}>
        {data.tools.map((t: any) => (
          <section key={t.key} className="panel"><div className="panel-body">
            <h2 style={{ fontSize: 16, margin: "0 0 4px" }}><Link to={`/tools/${t.key}`}>{t.name}</Link></h2>
            <p className="small muted" style={{ marginTop: 0 }}>{TOOL_META[t.key]?.blurb}</p>
            <div className="row" style={{ justifyContent: "space-between" }}><span className="small">{t.runs} runs · {t.leads} emails captured</span>
              <div className="row" style={{ gap: 4 }}><Link className="btn sm" to={`/tools/${t.key}`}>Open</Link><Copy text={t.public_url} label="Copy public link" /></div></div>
          </div></section>
        ))}
      </div>
      <div className="split">
        <Panel title="Captured emails" pad={false}>{!leads?.length ? <Empty title="No emails captured yet">Share a public tool link; visitors enter their email to see results.</Empty> :
          <div className="table-wrap"><table className="t"><thead><tr><th>When</th><th>Email</th><th>Tool</th><th>Input</th></tr></thead>
            <tbody>{leads.slice(0, 100).map((l) => <tr key={l.id}><td className="small">{ago(l.created_at)}</td><td>{l.email}</td><td className="small">{TOOL_META[l.tool]?.name || l.tool}</td><td className="mono-url small" style={{ maxWidth: 220 }}>{l.input}</td></tr>)}</tbody></table></div>}</Panel>
        <Panel title="Public tools settings">
          <div className="form">
            <label className="check"><input type="checkbox" checked={data.free_tools_enabled} disabled={!can("manage_integrations")} onChange={(e) => run(() => api("/tools/public-settings", { method: "PUT", body: { free_tools_enabled: e.target.checked } }), "Saved").then(reload)} /> Public tool pages enabled</label>
            <label className="field">Also create leads in project<span className="hint">Captured emails become deduplicated leads with attribution.</span>
              <select className="input" value={data.project_id || ""} disabled={!can("manage_integrations")} onChange={(e) => run(() => api("/tools/public-settings", { method: "PUT", body: { project_id: e.target.value || null } }), "Saved").then(reload)}>
                <option value="">— only store emails —</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            <p className="small muted">Public key <code>{data.public_key}</code></p>
            {can("manage_integrations") && <button className="btn sm" onClick={() => confirm("Rotate the public key? Existing public links stop working.") && run(() => api("/tools/public-settings", { method: "PUT", body: { rotate_key: true } }), "Key rotated").then(reload)}>Rotate public key</button>}
          </div>
        </Panel>
      </div>
    </>
  );
}

export function ToolPage() {
  const { tool = "" } = useParams();
  const meta = TOOL_META[tool];
  const { data: home } = useApi<any>("/tools");
  const { data: runs, reload } = useApi<any[]>(tool !== "backlink-monitor" ? `/tools/runs?tool=${tool}` : null, [tool], { poll: 4000 });
  const [input, setInput] = useState("");
  const [pages, setPages] = useState("25");
  const [sel, setSel] = useState<string | null>(null);
  const { data: detail } = useApi<any>(sel ? `/tools/runs/${sel}` : null, [sel, runs?.find((r) => r.id === sel)?.status]);
  const { run, busy } = useAction();
  const can = useCan();
  if (!meta) return <Empty title="Unknown tool" action={<Link className="btn" to="/tools">All tools</Link>} />;
  const publicUrl = home?.tools.find((t: any) => t.key === tool)?.public_url;
  if (tool === "backlink-monitor") return <Monitors publicUrl={publicUrl} />;
  const go = () => run<any>(() => api(`/tools/${tool}/run`, { body: meta.input === "url" ? { url: input, max_pages: Number(pages) || 25 } : meta.input === "domain" ? { domain: input } : { keyword: input } })).then((r) => { if (r) { setSel(r.id); reload(); } });
  return (
    <>
      <PageHead title={meta.name} sub={meta.blurb}>{publicUrl && <Copy text={publicUrl} label="Copy public link" />}<Link className="btn" to="/tools">All tools</Link></PageHead>
      <Panel>
        <form className="row" style={{ gap: 8, flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); if (input.trim()) go(); }}>
          <input className="input" style={{ flex: 1, minWidth: 260 }} value={input} onChange={(e) => setInput(e.target.value)} aria-label={meta.input}
            placeholder={meta.input === "url" ? "https://example.com/page" : meta.input === "domain" ? "example.com" : "niche keyword, e.g. gardening"} />
          {tool === "broken-links" && <label className="small row" style={{ gap: 6 }}>Max pages<input className="input" style={{ width: 80 }} inputMode="numeric" value={pages} onChange={(e) => setPages(e.target.value)} /></label>}
          <button className="btn primary" disabled={busy || !can("create")}>{busy ? "Running…" : "Run"}</button>
        </form>
        {tool === "domain-age" && <p className="faint small">Looked up live via rdap.org. If the registry can’t be reached you’ll see an error, never an estimate.</p>}
      </Panel>
      <div className="split" style={{ marginTop: 16 }}>
        <Panel title="Result">{!sel ? <Empty title="Run the tool to see results" /> : !detail ? <Loading /> :
          detail.status === "FAILED" ? <div className="notice err">{detail.error}</div> : detail.status !== "COMPLETED" ? <div className="notice">Running… this updates automatically.</div> :
            <ToolResult tool={tool} r={detail.result} />}</Panel>
        <Panel title="History" pad={false}>{!runs?.length ? <Empty title="No runs yet" /> :
          <table className="t"><tbody>{runs.map((r) => <tr key={r.id} className="click" onClick={() => setSel(r.id)} style={r.id === sel ? { background: "var(--accent-soft)" } : undefined}>
            <td className="small mono-url" style={{ maxWidth: 200 }}>{r.input.url || r.input.domain || r.input.keyword}</td><td><Status s={r.status === "COMPLETED" ? "ok" : r.status === "FAILED" ? "error" : "pending"} /></td><td className="small muted">{ago(r.created_at)}</td></tr>)}</tbody></table>}</Panel>
      </div>
    </>
  );
}

function Monitors({ publicUrl }: { publicUrl?: string }) {
  const { data, error, reload } = useApi<any[]>("/tools/monitors");
  const [f, setF] = useState({ source_url: "", target_url: "", email: "" });
  const { run, busy } = useAction();
  const can = useCan();
  return (
    <>
      <PageHead title="Backlink monitor" sub="Checks daily that each backlink is still live, links to your target and stays dofollow. Status changes notify you in-app.">
        {publicUrl && <Copy text={publicUrl} label="Copy public link" />}<Link className="btn" to="/tools">All tools</Link>
      </PageHead>
      {can("create") && <Panel><form className="row" style={{ gap: 8, flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); run(() => api("/tools/monitors", { body: { ...f, email: f.email || null } }), "Monitor added").then(() => { setF({ source_url: "", target_url: "", email: "" }); reload(); }); }}>
        <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="Page with the backlink" value={f.source_url} onChange={(e) => setF({ ...f, source_url: e.target.value })} required />
        <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="Your URL it should link to" value={f.target_url} onChange={(e) => setF({ ...f, target_url: e.target.value })} required />
        <input className="input" style={{ width: 200 }} placeholder="Alert email (optional)" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
        <button className="btn primary" disabled={busy}>Monitor</button></form></Panel>}
      <div style={{ marginTop: 16 }}><Panel pad={false} title="Monitored links"><ErrorBox error={error} />
        {!data ? <Loading /> : !data.length ? <Empty title="No monitored backlinks" /> :
          <div className="table-wrap"><table className="t"><thead><tr><th>Source → target</th><th>Status</th><th>rel</th><th>Last check</th><th>Owner</th><th /></tr></thead>
            <tbody>{data.map((m) => <tr key={m.id}><td className="small"><div className="mono-url">{m.source_url}</div><div className="faint mono-url">→ {m.target_url}</div></td>
              <td><Status s={m.status === "stopped" ? "stopped" : m.last_status} /></td><td className="small">{m.last_rel || "—"}</td><td className="small">{fmtDateTime(m.last_checked_at)}</td>
              <td className="small">{m.email}{m.public && <> <Badge kind="low">public</Badge></>}</td>
              <td><div className="row" style={{ gap: 4 }}>{can("edit") && <button className="btn sm" onClick={() => run(() => api(`/tools/monitors/${m.id}/check`, { method: "POST" }), "Checked").then(reload)}>Check now</button>}
                {can("delete") && <button className="btn ghost sm" onClick={() => run(() => api(`/tools/monitors/${m.id}`, { method: "DELETE" })).then(reload)} aria-label="Remove">✕</button>}</div></td></tr>)}</tbody></table></div>}
      </Panel></div>
    </>
  );
}
