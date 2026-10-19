import { useState } from "react";
import { Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { ago, fmtDate } from "../../lib/format";
import { useAction, useApi, useCan, useMe } from "../../lib/hooks";
import { StatusBadge } from "./shared";

export default function Extension() {
  const { data, error, loading, reload } = useApi<any>("/social/extension/tokens");
  const [create, setCreate] = useState(false);
  const [fresh, setFresh] = useState<any>(null);
  const can = useCan();
  const { me } = useMe();
  const { run } = useAction();
  return (
    <>
      <PageHead title="Chrome extension" sub="Audit any site, track the keyword you're searching and save Google Maps businesses as competitors — straight into your projects.">
        <button className="btn" onClick={() => run(() => download("/social/extension/download", "genranq-extension.zip"))}>Download extension (.zip)</button>
        {can("create") && <button className="btn primary" onClick={() => setCreate(true)}>Create extension token</button>}
      </PageHead>
      <ErrorBox error={error} />
      <div className="split">
        <Panel title="Install (load unpacked)">
          <ol className="steps">
            <li>Download the zip above and unzip it.</li>
            <li>Open <b>chrome://extensions</b> and switch on <b>Developer mode</b> (top right).</li>
            <li>Click <b>Load unpacked</b> and choose the unzipped <code>genranq-extension</code> folder.</li>
            <li>Create an extension token here, click the Genranq icon in the toolbar, paste this site's address (<code>{location.origin}</code>) and the token, and allow access when Chrome asks.</li>
            <li>Pick a project in the popup. On Google Search you'll see “Track keyword” and an “Audit” chip on each result; on Google Maps and business panels, “Save as competitor”.</li>
          </ol>
          <p className="faint small" style={{ marginTop: 10 }}>Permissions: storage, the active tab when you click the icon, and access to your Genranq address only. Content scripts run only on Google Search and Google Maps. Audits use your plan's audit-page quota.</p>
        </Panel>
        <Panel title="What a token can do">
          {data ? <ul className="steps small">{Object.entries(data.scopes).map(([k, v]: any) => <li key={k}><code>{k}</code> — {v}</li>)}</ul> : <Loading />}
          <p className="faint small" style={{ marginTop: 8 }}>Tokens work only for the extension endpoints (they cannot sign in to the app), act with your role and projects, and are stored hashed — copy a new token when it is shown.</p>
        </Panel>
      </div>
      <div style={{ marginTop: 16 }}>
        <Panel title="Extension tokens" pad={false}>
          {loading && !data ? <Loading /> : !data?.tokens.length ? <Empty title="No tokens yet" action={can("create") && <button className="btn primary" onClick={() => setCreate(true)}>Create extension token</button>} /> : (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Name</th><th>Owner</th><th>Token</th><th>Scopes</th><th>Last used</th><th>Expires</th><th>State</th><th /></tr></thead>
              <tbody>{data.tokens.map((t: any) => (
                <tr key={t.id}>
                  <td><b>{t.name}</b><div className="faint small">created {fmtDate(t.created_at)}</div></td><td className="small">{t.user}</td>
                  <td className="mono-url">{t.prefix}…</td><td className="small">{t.scopes.join(", ")}</td>
                  <td className="small">{t.last_used_at ? ago(t.last_used_at) : <span className="faint">never</span>}</td>
                  <td className="small">{t.expires_at ? fmtDate(t.expires_at) : "never"}</td>
                  <td><StatusBadge s={t.active ? "active" : t.revoked_at ? "rejected" : "disabled"}>{t.active ? "Active" : t.revoked_at ? "Revoked" : "Expired"}</StatusBadge></td>
                  <td className="r">{t.active && (t.user_id === me?.user.id || can("manage_users")) && <button className="btn sm danger" onClick={() => confirm("Revoke this token? The extension using it stops working immediately.") && run(() => api(`/social/extension/tokens/${t.id}`, { method: "DELETE" }), "Token revoked").then(reload)}>Revoke</button>}</td>
                </tr>))}</tbody>
            </table></div>
          )}
        </Panel>
      </div>
      {create && data && <CreateToken scopes={data.scopes} onClose={() => setCreate(false)} onDone={(t: any) => { setFresh(t); reload(); }} />}
      {fresh && (
        <Modal title="Copy your extension token" onClose={() => setFresh(null)} foot={<button className="btn primary" onClick={() => setFresh(null)}>Done</button>}>
          <p className="muted" style={{ marginBottom: 10 }}>This is the only time the token is shown. Paste it into the extension popup.</p>
          <div className="code">{fresh.token}</div>
          <button className="btn sm" style={{ marginTop: 10 }} onClick={() => navigator.clipboard?.writeText(fresh.token)}>Copy</button>
        </Modal>
      )}
    </>
  );
}

function CreateToken({ scopes, onClose, onDone }: any) {
  const [f, setF] = useState({ name: "Chrome on my laptop", scopes: Object.keys(scopes), expires_days: "90" });
  const { run, busy } = useAction();
  return (
    <Modal title="Create extension token" onClose={onClose}
      foot={<button className="btn primary" disabled={busy || !f.name || !f.scopes.length} onClick={() => run<any>(() => api("/social/extension/tokens", { body: { name: f.name, scopes: f.scopes, expires_days: f.expires_days ? Number(f.expires_days) : null } })).then((r) => { if (r) { onDone(r); onClose(); } })}>Create</button>}>
      <div className="form">
        <label className="field">Name<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></label>
        <div className="field">Scopes{Object.entries(scopes).map(([k, v]: any) => (
          <label key={k} className="check"><input type="checkbox" checked={f.scopes.includes(k)} onChange={() => setF((x) => ({ ...x, scopes: x.scopes.includes(k) ? x.scopes.filter((y) => y !== k) : [...x.scopes, k] }))} /> {v} <code className="faint">{k}</code></label>))}</div>
        <label className="field">Expires<select className="input" value={f.expires_days} onChange={(e) => setF({ ...f, expires_days: e.target.value })}>
          <option value="30">In 30 days</option><option value="90">In 90 days</option><option value="365">In a year</option></select></label>
      </div>
    </Modal>
  );
}
