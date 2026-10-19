import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorBox, Loading, Modal, PageHead, Panel, Select } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDateTime } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { StatusBadge } from "./shared";

export default function TeamsNotifications() {
  const { data, error, loading, reload } = useApi<any>("/social/teams");
  const [chan, setChan] = useState("");
  const { data: logRows, reload: reloadLog } = useApi<any[]>(`/social/teams/log${chan ? `?channel_id=${chan}` : ""}`, [chan], { poll: 20000 });
  const [edit, setEdit] = useState<any>(null);
  const can = useCan();
  const { run } = useAction();
  const manage = can("manage_integrations");
  return (
    <>
      <PageHead title="Microsoft Teams" sub="Send approvals requested, critical monitoring issues, reports ready and publish failures to Teams channels — org-wide or per project.">
        {manage && <button className="btn primary" onClick={() => setEdit({})}>Add channel</button>}
      </PageHead>
      <ErrorBox error={error} />
      {data && (
        <div className={`notice ${data.hub.status === "CONNECTED" ? "" : "nc"}`} style={{ marginBottom: 14 }}>
          <StatusBadge s={data.hub.status === "CONNECTED" ? "CONNECTED" : "NOT_CONNECTED"}>{data.hub.status === "CONNECTED" ? "Hub connected" : "Hub NOT CONNECTED"}</StatusBadge>
          <div>Integrations Hub — Microsoft Teams: {data.hub.status === "CONNECTED" ? (data.hub.has_webhook ? "a webhook URL is saved there; channels can reuse it." : "connected via OAuth (no webhook URL to reuse).") : <>not connected. You can still paste channel webhook URLs below, or <Link to="/integrations/ms_teams">connect it in Integrations</Link>.</>}</div>
        </div>
      )}
      <Panel title="Channels" pad={false}>
        {loading && !data ? <Loading /> : !data?.channels.length ? <Empty title="No Teams channels">In Teams: channel ⋯ → Workflows (or Connectors) → “Post to a channel when a webhook request is received”, copy the URL and add it here.</Empty> : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Channel</th><th>Scope</th><th>Webhook</th><th>Events</th><th>State</th><th /></tr></thead>
            <tbody>{data.channels.map((c: any) => (
              <tr key={c.id}>
                <td><b>{c.name}</b></td><td>{c.project || "All projects"}</td>
                <td className="mono-url">{c.use_hub ? "Integrations Hub webhook" : c.webhook_hint}</td>
                <td className="small">{c.events.map((e: string) => data.events[e]).join(", ") || <span className="faint">none</span>}</td>
                <td><StatusBadge s={c.enabled ? "active" : "disabled"} /></td>
                <td className="r nowrap">{manage && <>
                  <button className="btn sm" onClick={() => run<any>(() => api(`/social/teams/channels/${c.id}/test`, { method: "POST" })).then((r) => { if (r) { alert(r.status === "sent" ? "Test message delivered." : `Not delivered: ${r.error}`); reloadLog(); } })}>Send test</button>{" "}
                  <button className="btn sm" onClick={() => setEdit(c)}>Edit</button>{" "}
                  <button className="btn sm" onClick={() => run(() => api(`/social/teams/channels/${c.id}`, { method: "PATCH", body: { enabled: !c.enabled } })).then(reload)}>{c.enabled ? "Disable" : "Enable"}</button>{" "}
                  <button className="btn sm danger" onClick={() => confirm(`Remove ${c.name}?`) && run(() => api(`/social/teams/channels/${c.id}`, { method: "DELETE" })).then(reload)}>Remove</button></>}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Panel>
      <div style={{ marginTop: 16 }}>
        <Panel title="Delivery log" actions={<Select value={chan} onChange={setChan} label="Channel" placeholder="All channels" options={(data?.channels || []).map((c: any) => ({ value: c.id, label: c.name }))} />} pad={false}>
          {!logRows?.length ? <Empty title="Nothing sent yet" /> : (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>When</th><th>Channel</th><th>Event</th><th>Message</th><th>Result</th></tr></thead>
              <tbody>{logRows.map((d) => (
                <tr key={d.id}><td className="small" title={fmtDateTime(d.created_at)}>{ago(d.created_at)}</td><td>{d.channel || "(removed)"}</td>
                  <td className="small">{data?.events[d.event] || (d.event === "test" ? "Test" : d.event)}</td><td>{d.title}</td>
                  <td><StatusBadge s={d.status === "not_connected" ? "not_connected" : d.status} />{d.http_status ? <span className="faint small"> HTTP {d.http_status}</span> : null}{d.error && <div className="small" style={{ color: "var(--critical)" }}>{d.error}</div>}</td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
      </div>
      {edit && data && <ChannelModal c={edit.id ? edit : null} events={data.events} hub={data.hub} onClose={() => setEdit(null)} onDone={reload} />}
    </>
  );
}

function ChannelModal({ c, events, hub, onClose, onDone }: any) {
  const { data: projects } = useApi<any[]>("/projects");
  const [f, setF] = useState<any>(() => c ? { name: c.name, project_id: c.project_id || "", webhook_url: "", use_hub: c.use_hub, events: c.events }
    : { name: "", project_id: "", webhook_url: "", use_hub: false, events: Object.keys(events) });
  const { run, busy } = useAction();
  const save = () => run(() => c ? api(`/social/teams/channels/${c.id}`, { method: "PATCH", body: { name: f.name, events: f.events, ...(f.webhook_url ? { webhook_url: f.webhook_url } : {}) } })
    : api("/social/teams/channels", { body: { name: f.name, project_id: f.project_id || null, events: f.events, use_hub: f.use_hub, webhook_url: f.use_hub ? null : f.webhook_url } }), "Channel saved")
    .then((r) => { if (r) { onDone(); onClose(); } });
  return (
    <Modal title={c ? `Edit ${c.name}` : "Add Teams channel"} onClose={onClose}
      foot={<button className="btn primary" disabled={busy || !f.name || (!c && !f.use_hub && !f.webhook_url)} onClick={save}>Save</button>}>
      <div className="form">
        <label className="field">Name<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></label>
        {!c && <label className="field">Scope<select className="input" value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}><option value="">All projects (org-wide)</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
        {!c && hub.has_webhook && <label className="check"><input type="checkbox" checked={f.use_hub} onChange={(e) => setF({ ...f, use_hub: e.target.checked })} /> Reuse the Integrations Hub webhook</label>}
        {!f.use_hub && <label className="field">Incoming webhook URL<span className="hint">{c ? "Leave empty to keep the saved URL. " : ""}https://…webhook.office.com/… or a Teams Workflows URL. Stored encrypted.</span>
          <input className="input" value={f.webhook_url} onChange={(e) => setF({ ...f, webhook_url: e.target.value })} placeholder="https://" /></label>}
        <div className="field">Events<div className="chips" style={{ marginTop: 6 }}>{Object.entries(events).map(([k, l]: any) => (
          <span key={k} role="checkbox" aria-checked={f.events.includes(k)} tabIndex={0} className={`chip ${f.events.includes(k) ? "on" : ""}`}
            onClick={() => setF((x: any) => ({ ...x, events: x.events.includes(k) ? x.events.filter((y: string) => y !== k) : [...x.events, k] }))}>{l}</span>))}</div></div>
      </div>
    </Modal>
  );
}
