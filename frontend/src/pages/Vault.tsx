import { useState } from "react";
import { Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../components/ui";
import { api, setStepUp, steppedUp } from "../lib/api";
import { ago } from "../lib/format";
import { useAction, useApi, useMe } from "../lib/hooks";

export default function Vault() {
  const { data, error, loading, reload } = useApi<any[]>("/vault");
  const { data: projects } = useApi<any[]>("/projects");
  const [add, setAdd] = useState(false);
  const [revealed, setRevealed] = useState<Record<string, any>>({});
  const [stepFor, setStepFor] = useState<string | null>(null);
  const { run } = useAction();
  const sites = (projects || []).flatMap((p) => p.sites);
  const reveal = async (id: string) => {
    if (!steppedUp()) { setStepFor(id); return; }
    const r = await run(() => api(`/vault/${id}/reveal`, { method: "POST", stepUp: true }));
    if (r) { setRevealed((x) => ({ ...x, [id]: r })); setTimeout(() => setRevealed((x) => { const n = { ...x }; delete n[id]; return n; }), 30000); }
  };
  if (error && (error as any).status === 403) return <><PageHead title="Credential vault" /><ErrorBox error={error} /></>;
  return (
    <>
      <PageHead title="Credential vault" sub="Encrypted site and supplier logins. Every reveal requires re-authentication and is recorded in the activity log; revealed values hide again after 30 seconds.">
        <button className="btn primary" onClick={() => setAdd(true)}>Add credential</button>
      </PageHead>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : (
        <Panel pad={false}>
          {!data?.length ? <Empty title="The vault is empty">Store CMS, hosting or supplier logins here instead of in spreadsheets.</Empty> : (
            <table className="t"><thead><tr><th>Label</th><th>Linked to</th><th>Username</th><th>Secret</th><th>Updated</th><th /></tr></thead>
              <tbody>{data.map((c) => (
                <tr key={c.id}><td>{c.label}{c.integration && <div className="faint small">Integration key (write-only)</div>}</td><td>{c.site || c.supplier || c.integration || "—"}</td><td>{c.username || "—"}</td>
                  <td>{revealed[c.id] ? <code>{revealed[c.id].secret}</code> : <code className="faint">{c.masked_hint}</code>}</td><td className="muted">{ago(c.rotated_at || c.created_at)}</td>
                  <td className="row">{!c.integration && <button className="btn sm" onClick={() => reveal(c.id)}>{revealed[c.id] ? "Shown" : "Reveal"}</button>}
                    {!c.integration && <button className="btn sm ghost danger" onClick={() => confirm("Delete this credential permanently?") && run(() => api(`/vault/${c.id}`, { method: "DELETE" }), "Credential deleted").then(reload)}>Delete</button>}</td></tr>
              ))}</tbody></table>
          )}
        </Panel>
      )}
      {add && <AddCred sites={sites} onClose={() => setAdd(false)} onDone={reload} />}
      {stepFor && <StepUp onClose={() => setStepFor(null)} onDone={() => { const id = stepFor; setStepFor(null); reveal(id!); }} />}
    </>
  );
}

function AddCred({ sites, onClose, onDone }: any) {
  const [f, setF] = useState({ label: "", username: "", secret: "", site_id: "" });
  const { run } = useAction();
  return (
    <Modal title="Add credential" onClose={onClose} foot={<button className="btn primary" disabled={!f.label || !f.secret} onClick={() => run(() => api("/vault", { body: { ...f, site_id: f.site_id || null } }), "Credential stored").then((r) => { if (r) { onDone(); onClose(); } })}>Store credential</button>}>
      <div className="form">
        <label className="field">Label<input className="input" placeholder="WordPress admin — acme.com" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} /></label>
        <label className="field">Site<select className="input" value={f.site_id} onChange={(e) => setF({ ...f, site_id: e.target.value })}><option value="">Not linked</option>{sites.map((s: any) => <option key={s.id} value={s.id}>{s.domain}</option>)}</select></label>
        <label className="field">Username<input className="input" autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></label>
        <label className="field">Password or secret<input className="input" type="password" autoComplete="new-password" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} /></label>
      </div>
    </Modal>
  );
}

export function StepUp({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { me } = useMe();
  const [pw, setPw] = useState("");
  const [code, setCode] = useState("");
  const { run, busy } = useAction();
  return (
    <Modal title="Confirm it’s you" onClose={onClose} foot={<button className="btn primary" disabled={!pw || busy} onClick={() => run<any>(() => api("/auth/step-up", { body: { password: pw, code: code || null } })).then((r) => { if (r) { setStepUp(r.access_token, r.step_up_minutes); onDone(); } })}>Confirm</button>}>
      <div className="form">
        <p className="muted">Access to credentials needs a fresh sign-in. It stays unlocked for 10 minutes.</p>
        <label className="field">Password<input className="input" type="password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} /></label>
        {me?.user.mfa_enabled && <label className="field">Authentication code<input className="input num" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></label>}
      </div>
    </Modal>
  );
}
