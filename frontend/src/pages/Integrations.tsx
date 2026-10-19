import { useState } from "react";
import { Badge, Drawer, ErrorBox, Loading, PageHead, Panel, Select } from "../components/ui";
import { api } from "../lib/api";
import { ago, human } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

const STATUS_LABEL: Record<string, string> = { NOT_CONNECTED: "Not connected", CONNECTED: "Connected", ERROR: "Error", PENDING: "Unverified", RATE_LIMITED: "Rate limited" };

export default function Integrations() {
  const { data, error, loading, reload } = useApi<any[]>("/integrations");
  const [open, setOpen] = useState<any>(null);
  const [cat, setCat] = useState("");
  if (loading && !data) return <Loading />;
  const cats = Array.from(new Set((data || []).map((d) => d.category)));
  const items = (data || []).filter((d) => !cat || d.category === cat);
  const connected = (data || []).filter((d) => d.status === "CONNECTED").length;
  return (
    <>
      <PageHead title="Integrations" sub={`${connected} of ${data?.length ?? 0} providers connected. Every provider from the API Integrations sheet has a connector here; anything not connected is labelled and the features that need it say so.`}>
        <Select value={cat} onChange={setCat} placeholder="All categories" label="Category" options={cats.map((c) => ({ value: c, label: c }))} />
      </PageHead>
      <ErrorBox error={error} />
      <Panel pad={false}>
        <div className="table-wrap"><table className="t"><thead><tr><th>Provider</th><th>Used for</th><th>Auth</th><th>Status</th><th>Last check</th><th>Last sync</th><th>Phase</th></tr></thead>
          <tbody>{items.map((i) => (
            <tr key={i.provider} className="click" onClick={() => setOpen(i)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(i)}>
              <td><b style={{ fontWeight: 500 }}>{i.provider}</b><div className="faint small">{i.category}</div></td>
              <td className="small" style={{ maxWidth: 280 }}>{i.purpose}</td><td className="small">{i.auth_spec}</td>
              <td><Badge kind={i.status}>{STATUS_LABEL[i.status] || i.status}</Badge>{!i.plan_allows && <div className="faint small">Not in plan</div>}</td>
              <td className="muted small">{ago(i.last_health_at)}</td><td className="muted small">{ago(i.last_sync_at)}</td><td className="faint">{i.phase}</td></tr>
          ))}</tbody></table></div>
      </Panel>
      {open && <Connect i={open} onClose={() => setOpen(null)} onDone={async () => { await reload(); }} />}
    </>
  );
}

function Connect({ i, onClose, onDone }: any) {
  const [secret, setSecret] = useState("");
  const [config, setConfig] = useState<Record<string, string>>(i.config || {});
  const { run, busy } = useAction();
  const can = useCan();
  const manage = can("manage_integrations");
  const oauth = i.auth.startsWith("oauth2");
  const keyBased = i.auth === "api_key" || i.auth === "oauth2_or_api_key" || i.auth === "license_cli" || i.auth === "oauth2_or_webhook";
  const after = (r: any) => { if (r) { onDone(); onClose(); } };
  return (
    <Drawer title={i.provider} onClose={onClose} foot={manage && i.status !== "NOT_CONNECTED" ? <>
      <button className="btn danger" onClick={() => run(() => api(`/integrations/${i.key}/disconnect`, { method: "POST" }), "Disconnected").then(after)}>Disconnect</button>
      <span className="spacer" /><button className="btn" onClick={() => run(() => api<any>(`/integrations/${i.key}/health`, { method: "POST" }), "Health check finished").then(after)}>Run health check</button></> : undefined}>
      <div className="row" style={{ marginBottom: 14 }}><Badge kind={i.status}>{STATUS_LABEL[i.status] || i.status}</Badge><Badge>{i.category}</Badge><Badge>Roadmap phase {i.phase}</Badge></div>
      <dl className="kv" style={{ marginBottom: 16 }}>
        <dt>Used for</dt><dd>{i.purpose}</dd>
        <dt>Authentication</dt><dd>{i.auth_spec}</dd>
        <dt>Quota / cost</dt><dd>{i.quota}</dd>
        <dt>Credential</dt><dd>{i.credential_hint ? <code>{i.credential_hint}</code> : "None stored"} <span className="faint small">(encrypted in the vault; never shown again)</span></dd>
        <dt>Last health check</dt><dd>{ago(i.last_health_at)}</dd>
        <dt>Last sync</dt><dd>{ago(i.last_sync_at)}</dd>
        {i.error_count > 0 && <><dt>Consecutive errors</dt><dd>{i.error_count}</dd></>}
      </dl>
      {i.last_error && <div className="notice err" style={{ marginBottom: 14 }}>{i.last_error}</div>}
      {!i.plan_allows ? <div className="notice warn">This integration isn’t included in your plan{i.entitlement === "accounting" ? " (accounting is an add-on)" : ""}.</div>
        : !manage ? <p className="muted">Ask an owner or admin to manage integrations.</p> : (
        <div className="form">
          {oauth && (
            <div className="panel panel-body">
              <h3 style={{ marginBottom: 6 }}>Connect with OAuth</h3>
              {i.oauth_app_configured === false ? <p className="muted small">The platform’s OAuth app for this provider hasn’t been configured by your Genranq administrator yet, so OAuth sign-in isn’t available.</p>
                : <button className="btn primary" onClick={() => run<any>(() => api(`/integrations/${i.key}/oauth/start`)).then((r) => r && (window.location.href = r.authorize_url))}>Continue to {i.provider}</button>}
            </div>
          )}
          {keyBased && (<>
            {i.auth !== "license_cli" && i.auth !== "oauth2_or_webhook" && <label className="field">{i.auth === "oauth2_or_api_key" ? "Personal access token" : "API key"}<input className="input" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={i.credential_hint ? "Enter a new key to rotate" : ""} /></label>}
            {i.auth === "license_cli" && <label className="field">Licence key<span className="hint">Stored encrypted. The CLI must be installed on the worker host.</span><input className="input" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} /></label>}
            {i.fields.filter((f: any) => !f.secret).map((f: any) => <label key={f.name} className="field">{f.label}<input className="input" value={config[f.name] || ""} onChange={(e) => setConfig({ ...config, [f.name]: e.target.value })} /></label>)}
            {i.fields.filter((f: any) => f.secret).map((f: any) => <label key={f.name} className="field">{f.label}<input className="input" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} /></label>)}
            {i.category === "AI" && <label className="field">Default model<span className="hint">Optional</span><input className="input" value={config.model || ""} onChange={(e) => setConfig({ ...config, model: e.target.value })} /></label>}
            <button className="btn primary" disabled={busy || (!secret && !Object.keys(config).length)} onClick={() => run<any>(() => api(`/integrations/${i.key}/connect`, { body: { secret: secret || null, config } })).then((r) => {
              if (r) { if (r.status !== "CONNECTED") alert(`Saved, but the health check reported ${human(r.status)}${r.error ? `: ${r.error}` : ""}`); after(r); }
            })}>Save and test connection</button>
          </>)}
        </div>
      )}
    </Drawer>
  );
}
