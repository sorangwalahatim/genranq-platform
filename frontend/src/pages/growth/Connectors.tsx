import { useState } from "react";
import { Link } from "react-router-dom";
import { Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDateTime } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Code, Copy, LeadsNav, NC, Status, Tabs, useProjects } from "./shared";

export default function Connectors() {
  const { projects, pid, picker } = useProjects();
  const { data, error, loading, reload } = useApi<any>("/forms/connectors");
  const { data: presets } = useApi<any[]>("/forms/connectors/presets");
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<any>(null);
  const [open, setOpen] = useState<any>(null);
  const can = useCan();
  const items = data?.items || [];
  return (
    <>
      <PageHead title="Form connectors" sub="Receive leads from Typeform, Gravity Forms, Webflow, Facebook Lead Ads or any platform via signed webhooks.">
        {picker}{can("manage_integrations") && <button className="btn primary" disabled={!pid} onClick={() => setCreating(true)}>New connector</button>}
      </PageHead>
      <LeadsNav />
      {data && !data.meta_connected && items.some((c: any) => c.preset === "facebook_lead_ads") &&
        <NC what="Meta (Facebook/Instagram)" provider="meta">is needed to resolve Facebook lead IDs into lead fields. Deliveries are logged but not converted until it is connected.</NC>}
      <Panel pad={false} title="Connectors">
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !items.length ? <Empty title="No connectors yet" action={can("manage_integrations") && pid && <button className="btn primary" onClick={() => setCreating(true)}>Create a connector</button>}>Each connector gets its own endpoint and HMAC-SHA256 signing secret.</Empty> : (
          <div className="table-wrap"><table className="t"><thead><tr><th>Name</th><th>Platform</th><th>Status</th><th className="r">Deliveries</th><th className="r">Leads</th><th>Last delivery</th></tr></thead>
            <tbody>{items.map((c: any) => (
              <tr key={c.id} className="click" onClick={() => setOpen(c)}><td><b>{c.name}</b><div className="faint small">secret {c.secret_hint}</div></td><td>{c.preset_info.label}</td>
                <td><Status s={c.enabled ? "active" : "paused"} /></td><td className="r num">{c.deliveries}</td><td className="r num">{c.accepted}</td><td className="muted small">{ago(c.last_delivery_at)}</td></tr>))}</tbody></table></div>
        )}
      </Panel>
      {!projects?.length && <p className="muted small" style={{ marginTop: 10 }}>Create a <Link to="/projects">project</Link> to receive leads into.</p>}
      {creating && <CreateConnector pid={pid} presets={presets || []} onClose={() => setCreating(false)} onDone={(c: any) => { reload(); setSecret(c); }} />}
      {secret && <Modal title="Connector created" onClose={() => setSecret(null)} foot={<button className="btn primary" onClick={() => { setOpen(secret); setSecret(null); }}>Done — I stored the secret</button>}>
        <p>Copy the signing secret now — it is shown only once (stored encrypted in the vault).</p>
        <div className="row" style={{ justifyContent: "space-between" }}><b className="small">Endpoint</b><Copy text={secret.endpoint} /></div><Code>{secret.endpoint}</Code>
        <div className="row" style={{ justifyContent: "space-between", marginTop: 10 }}><b className="small">Secret</b><Copy text={secret.secret} /></div><Code>{secret.secret}</Code>
        {secret.verify_token && <><b className="small">Facebook verify token</b><Code>{secret.verify_token}</Code></>}
        <p className="muted small">{secret.preset_info.header}</p>
      </Modal>}
      {open && <ConnectorDrawer c={open} onClose={() => setOpen(null)} onChange={reload} onSecret={(c: any) => setSecret(c)} />}
    </>
  );
}

function CreateConnector({ pid, presets, onClose, onDone }: any) {
  const [name, setName] = useState("");
  const [preset, setPreset] = useState("generic");
  const { data: forms } = useApi<any[]>(`/forms?project_id=${pid}`);
  const [formId, setFormId] = useState("");
  const { run, busy } = useAction();
  return (
    <Modal title="New connector" onClose={onClose} foot={<button className="btn primary" disabled={busy || !name} onClick={() => run<any>(() => api("/forms/connectors", { body: { name, preset, project_id: pid, form_id: formId || null } })).then((c) => { if (c) { onClose(); onDone(c); } })}>Create</button>}>
      <div className="form">
        <label className="field">Name<input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Website Typeform" autoFocus /></label>
        <label className="field">Platform<select className="input" value={preset} onChange={(e) => setPreset(e.target.value)}>{presets.map((p: any) => <option key={p.key} value={p.key}>{p.label}</option>)}</select>
          <span className="hint">{presets.find((p: any) => p.key === preset)?.header}</span></label>
        <label className="field">Record submissions against form (optional)<select className="input" value={formId} onChange={(e) => setFormId(e.target.value)}><option value="">— none —</option>{(forms || []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
      </div>
    </Modal>
  );
}

const LEAD_FIELDS = ["email", "phone", "name", "first_name", "last_name", "company", "value", "status", "notes", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "referrer", "page_url"];

function ConnectorDrawer({ c, onClose, onChange, onSecret }: any) {
  const [tab, setTab] = useState("mapping");
  const [mapping, setMapping] = useState<Record<string, string>>(c.mapping);
  const [payload, setPayload] = useState("");
  const [result, setResult] = useState<any>(null);
  const { data: presets } = useApi<any[]>("/forms/connectors/presets");
  const { data: dl, reload } = useApi<any[]>(`/forms/connectors/${c.id}/deliveries`, [c.id]);
  const { run, busy } = useAction();
  const can = useCan();
  const sample = presets?.find((p) => p.key === c.preset)?.sample;
  const test = (create = false) => {
    let body: any = undefined;
    if (payload.trim()) { try { body = JSON.parse(payload); } catch { alert("Payload must be valid JSON"); return; } }
    run<any>(() => api(`/forms/connectors/${c.id}/test`, { body: { payload: body, create_lead: create } }), create ? "Test lead created" : undefined).then((r) => { if (r) { setResult(r); reload(); onChange(); } });
  };
  return (
    <Drawer title={c.name} onClose={onClose} foot={can("manage_integrations") ? <div className="row">
      <button className="btn" onClick={() => run(() => api(`/forms/connectors/${c.id}`, { method: "PATCH", body: { enabled: !c.enabled } })).then(() => { onChange(); onClose(); })}>{c.enabled ? "Disable" : "Enable"}</button>
      <button className="btn" onClick={() => confirm("Rotate the signing secret? The sender must be updated.") && run<any>(() => api(`/forms/connectors/${c.id}/rotate-secret`, { method: "POST" })).then((r) => r && onSecret(r))}>Rotate secret</button>
      <button className="btn danger" onClick={() => confirm("Delete this connector and its delivery log?") && run(() => api(`/forms/connectors/${c.id}`, { method: "DELETE" })).then(() => { onChange(); onClose(); })}>Delete</button></div> : undefined}>
      <div className="row" style={{ justifyContent: "space-between" }}><b className="small">Endpoint</b><Copy text={c.endpoint} /></div><Code>{c.endpoint}</Code>
      <p className="muted small">{c.preset_info.header}</p>
      <Tabs value={tab} onChange={setTab} tabs={[["mapping", "Field mapping"], ["test", "Sample tester"], ["log", `Delivery log (${dl?.length ?? 0})`]]} />
      {tab === "mapping" && <div className="form">
        <p className="muted small">Map lead fields to the source field names in the incoming payload (nested keys use dots, e.g. <code>contact.email</code>; Typeform uses field refs).</p>
        {LEAD_FIELDS.map((k) => <label key={k} className="field" style={{ flexDirection: "row", alignItems: "center", gap: 10 }}><span style={{ width: 120 }}>{k}</span>
          <input className="input" value={mapping[k] || ""} onChange={(e) => setMapping({ ...mapping, [k]: e.target.value })} placeholder="source field" /></label>)}
        {can("manage_integrations") && <button className="btn primary" disabled={busy} onClick={() => run(() => api(`/forms/connectors/${c.id}`, { method: "PATCH", body: { mapping: Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)) } }), "Mapping saved").then(onChange)}>Save mapping</button>}
      </div>}
      {tab === "test" && <div className="form">
        <label className="field">Sample payload (JSON)<span className="hint">Leave empty to use the {c.preset_info.label} sample.</span>
          <textarea className="input" rows={8} value={payload} onChange={(e) => setPayload(e.target.value)} placeholder={JSON.stringify(sample, null, 2)} style={{ fontFamily: "monospace", fontSize: 12 }} /></label>
        <div className="row"><button className="btn" onClick={() => test(false)}>Preview mapping</button>{can("edit") && <button className="btn" onClick={() => test(true)}>Create a test lead</button>}</div>
        {result && <div>
          {result.note && <div className="notice warn">{result.note}</div>}
          <p><b>Lead:</b> {result.valid ? "valid" : "missing email/phone/name"} </p><Code>{JSON.stringify({ lead: result.lead, attribution: result.touch }, null, 2)}</Code>
          {result.unmapped_fields?.length > 0 && <p className="small muted">Unmapped source fields: {result.unmapped_fields.join(", ")}</p>}
          <p className="small"><b>Signed request example</b> (headers a sender must attach):</p><Code>{JSON.stringify(result.example_headers, null, 2)}</Code>
        </div>}
      </div>}
      {tab === "log" && (!dl ? <Loading /> : !dl.length ? <Empty title="No deliveries yet" /> :
        <table className="t"><thead><tr><th>When</th><th>Status</th><th>Signature</th><th>Detail</th></tr></thead>
          <tbody>{dl.map((d) => <tr key={d.id}><td className="small">{fmtDateTime(d.created_at)}</td><td><Status s={d.status === "accepted" ? "accepted_hook" : d.status} /></td>
            <td className="small">{d.signature_ok == null ? "—" : d.signature_ok ? "valid" : "invalid"}</td>
            <td className="small">{d.error || (d.lead_id ? <Link to={`/leads?lead=${d.lead_id}`}>Lead</Link> : "—")}</td></tr>)}</tbody></table>)}
    </Drawer>
  );
}
