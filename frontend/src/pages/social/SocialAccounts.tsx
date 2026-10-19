import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { CHANNEL_LABEL, CHANNELS, ChannelTag, PROVIDER_LABEL, ProjectPicker, StatusBadge, useProjects } from "./shared";

export default function SocialAccounts() {
  const { projects, pid, setPid, loaded } = useProjects();
  const { data, error, loading, reload } = useApi<any>(pid ? `/social/accounts?project_id=${pid}` : null, [pid]);
  const [adding, setAdding] = useState<string | null>(null);
  const can = useCan();
  const { run } = useAction();
  const project = projects.find((p) => p.id === pid);
  const accounts: any[] = data?.accounts || [];
  return (
    <>
      <PageHead title="Social accounts" sub="Map Facebook pages, Instagram business accounts, LinkedIn pages and X accounts to each project. The connection itself lives in Integrations; here you choose which page belongs to which client.">
        <ProjectPicker projects={projects} pid={pid} setPid={setPid} />
      </PageHead>
      {loaded && !projects.length ? <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>} /></Panel> : (
        <>
          <ErrorBox error={error} />
          <div className="grid g3" style={{ marginBottom: 16 }}>
            {(data?.providers || []).map((p: any) => (
              <div key={p.provider} className="panel kpi">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <b>{PROVIDER_LABEL[p.provider]}</b><StatusBadge s={p.status === "CONNECTED" ? "CONNECTED" : "NOT_CONNECTED"}>{p.status === "CONNECTED" ? "Connected" : "NOT CONNECTED"}</StatusBadge>
                </div>
                <div className="l" style={{ marginTop: 6 }}>
                  {accounts.filter((a) => a.provider === p.provider).length} account(s) in {project?.name || "this project"}
                </div>
                <div className="row" style={{ marginTop: 10 }}>
                  {p.status !== "CONNECTED" && <Link className="btn sm" to={`/integrations/${p.provider}`}>Connect</Link>}
                  {can("edit") && <button className="btn sm primary" onClick={() => setAdding(p.provider)}>Add account</button>}
                </div>
              </div>
            ))}
          </div>
          <Panel title={`Accounts${project ? ` — ${project.name}` : ""}`} pad={false}>
            {loading && !data ? <Loading /> : !accounts.length ? (
              <Empty title="No social accounts mapped to this project">Add the pages and profiles this client publishes to. You can map accounts before connecting; publishing waits for the connection.</Empty>
            ) : (
              <div className="table-wrap"><table className="t">
                <thead><tr><th>Account</th><th>Channel</th><th>External ID</th><th>State</th><th>Health</th><th>Default</th><th /></tr></thead>
                <tbody>{accounts.map((a) => (
                  <tr key={a.id}>
                    <td><b>{a.name}</b>{a.handle && <div className="faint small">@{a.handle}</div>}</td>
                    <td><ChannelTag ch={a.channel} /></td>
                    <td className="mono-url">{a.external_id}</td>
                    <td><StatusBadge s={a.state === "ready" ? "ready" : a.state === "NOT CONNECTED" ? "NOT_CONNECTED" : a.state}>{a.state === "NOT CONNECTED" ? "NOT CONNECTED" : undefined}</StatusBadge></td>
                    <td className="small">{a.last_health_at ? <>{a.last_health_ok ? "OK" : <span style={{ color: "var(--critical)" }}>{a.last_error}</span>} <span className="faint">· {ago(a.last_health_at)}</span></> : <span className="faint">Not checked</span>}</td>
                    <td>{a.is_default ? <StatusBadge s="active">Default</StatusBadge> : can("edit") && <button className="btn sm ghost" onClick={() => run(() => api(`/social/accounts/${a.id}`, { method: "PATCH", body: { is_default: true } }), "Default updated").then(reload)}>Make default</button>}</td>
                    <td className="r nowrap">
                      {can("edit") && <button className="btn sm" onClick={() => run(() => api(`/social/accounts/${a.id}/health`, { method: "POST" }), "Health checked").then(reload)}>Check</button>}{" "}
                      {can("edit") && <button className="btn sm" onClick={() => run(() => api(`/social/accounts/${a.id}`, { method: "PATCH", body: { status: a.status === "disabled" ? "active" : "disabled" } })).then(reload)}>{a.status === "disabled" ? "Enable" : "Disable"}</button>}{" "}
                      {can("delete") && <button className="btn sm danger" onClick={() => confirm(`Remove ${a.name} from this project? Scheduled posts to it are cancelled.`) && run<any>(() => api(`/social/accounts/${a.id}`, { method: "DELETE" })).then((r) => { if (r) reload(); })}>Remove</button>}
                    </td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </Panel>
        </>
      )}
      {adding && <AddAccount provider={adding} pid={pid} onClose={() => setAdding(null)} onDone={reload} />}
    </>
  );
}

function AddAccount({ provider, pid, onClose, onDone }: { provider: string; pid: string; onClose: () => void; onDone: () => void }) {
  const { data: disc, loading } = useApi<any>(`/social/accounts/discover?provider=${provider}`);
  const channels = CHANNELS.filter((c) => ({ facebook: "meta", instagram: "meta", linkedin: "linkedin", x: "x" } as any)[c] === provider);
  const [f, setF] = useState({ channel: channels[0] as string, external_id: "", name: "", handle: "" });
  const { run, busy } = useAction();
  const add = (body: any) => run(() => api("/social/accounts", { body: { project_id: pid, ...body } }), `${body.name} added`).then((r) => { if (r) { onDone(); onClose(); } });
  return (
    <Modal title={`Add ${PROVIDER_LABEL[provider]} account`} onClose={onClose} wide
      foot={<button className="btn primary" disabled={busy || !f.external_id || !f.name} onClick={() => add({ ...f, handle: f.handle || null })}>Add manually</button>}>
      <h3 style={{ fontSize: 14, marginBottom: 8 }}>From your connection</h3>
      {loading ? <Loading /> : disc?.connected === false ? (
        <div className="notice nc" style={{ marginBottom: 16 }}><div><b>NOT CONNECTED.</b> {disc.message} <Link to={`/integrations/${provider}`}>Connect now</Link>.</div></div>
      ) : (disc?.accounts || []).length === 0 ? <p className="muted">The connection returned no pages or profiles.</p> : (
        <table className="t" style={{ marginBottom: 16 }}><tbody>{disc.accounts.map((a: any) => (
          <tr key={a.channel + a.external_id}>
            <td><b>{a.name}</b>{a.handle && <span className="faint"> @{a.handle}</span>}</td><td><span className={`ch-tag ch-${a.channel}`}>{CHANNEL_LABEL[a.channel]}</span></td>
            <td className="faint small">{a.mapped_project_ids.includes(pid) ? "Already in this project" : a.mapped_project_ids.length ? `In ${a.mapped_project_ids.length} other project(s)` : ""}</td>
            <td className="r"><button className="btn sm primary" disabled={busy || a.mapped_project_ids.includes(pid)} onClick={() => add(a)}>Add</button></td>
          </tr>))}</tbody></table>
      )}
      <h3 style={{ fontSize: 14, marginBottom: 8 }}>Or enter it manually</h3>
      <div className="form">
        <div className="grid g2">
          <label className="field">Channel<select className="input" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>
            {channels.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select></label>
          <label className="field">External ID<span className="hint">{f.channel === "linkedin" ? "Organization ID or urn:li:organization:…" : f.channel === "x" ? "Numeric user ID" : "Page / Instagram user ID"}</span>
            <input className="input" value={f.external_id} onChange={(e) => setF({ ...f, external_id: e.target.value })} /></label>
        </div>
        <div className="grid g2">
          <label className="field">Display name<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label className="field">Handle<input className="input" value={f.handle} onChange={(e) => setF({ ...f, handle: e.target.value })} placeholder="optional" /></label>
        </div>
      </div>
    </Modal>
  );
}
