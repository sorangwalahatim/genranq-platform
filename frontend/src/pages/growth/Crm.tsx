import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDateTime } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Code, Copy, Kpi, LeadsNav, NC, Status } from "./shared";

export function Hubspot() {
  const { data, error, reload } = useApi<any>("/leads/hubspot");
  const { data: projects } = useApi<any[]>("/projects");
  const [map, setMap] = useState<Record<string, string>>({});
  const [pid, setPid] = useState("");
  const { run, busy } = useAction();
  const can = useCan();
  useEffect(() => { if (data) { setMap(data.settings.field_map); setPid(data.settings.project_id || ""); } }, [data]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <>
      <PageHead title="HubSpot" sub="Two-way contact sync: pull contacts changed since the last sync, push leads changed locally.">
        {can("edit") && <button className="btn primary" disabled={busy || !data.connected} onClick={() => run(() => api("/leads/hubspot/sync", { method: "POST" }), "Sync finished").then(reload)}>Sync now</button>}
      </PageHead>
      <LeadsNav />
      {!data.connected && <NC what="HubSpot" provider="hubspot">is not connected, so nothing is synced. Leads keep working locally;</NC>}
      <div className="grid g3" style={{ marginBottom: 16 }}>
        <Kpi v={<Badge kind={data.status}>{data.status.replace("_", " ")}</Badge>} l="Connection" /><Kpi v={data.linked_leads} l="Leads linked to contacts" /><Kpi v={data.pending_push} l="Leads changed since last sync" />
      </div>
      <div className="split">
        <Panel title="Sync log" pad={false}>{!data.logs.length ? <Empty title="No syncs yet" /> :
          <div className="table-wrap"><table className="t"><thead><tr><th>When</th><th>Status</th><th>Result</th></tr></thead>
            <tbody>{data.logs.map((l: any) => <tr key={l.id}><td className="small">{fmtDateTime(l.created_at)}</td><td><Status s={l.status} /></td>
              <td className="small">{l.error || Object.entries(l.counts || {}).filter(([, v]) => v).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ") || "no changes"}</td></tr>)}</tbody></table></div>}
        </Panel>
        <Panel title="Settings">
          <div className="form">
            <label className="field">Project for contacts pulled from HubSpot<select className="input" value={pid} onChange={(e) => setPid(e.target.value)}><option value="">— don’t create leads from HubSpot —</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            <b className="small">Field mapping (lead field → HubSpot property)</b>
            {data.mappable.map((k: string) => <label key={k} className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><span style={{ width: 110 }} className="small">{k}</span>
              <input className="input" value={map[k] || ""} onChange={(e) => setMap({ ...map, [k]: e.target.value })} placeholder="not synced" /></label>)}
            <p className="muted small">{data.conflict_rule} Status maps to <code>hs_lead_status</code> ({Object.entries(data.status_map).map(([a, b]) => `${a}→${b}`).join(", ")}).</p>
            {can("manage_integrations") && <button className="btn primary" disabled={busy} onClick={() => run(() => api("/leads/hubspot/settings", { method: "PUT", body: { project_id: pid || null, field_map: Object.fromEntries(Object.entries(map).filter(([, v]) => v)) } }), "Settings saved").then(reload)}>Save settings</button>}
          </div>
        </Panel>
      </div>
    </>
  );
}

export function Calendly() {
  const { data, error, reload } = useApi<any>("/leads/calendly");
  const { data: projects } = useApi<any[]>("/projects");
  const { data: meta } = useApi<any>("/leads/meta");
  const [link, setLink] = useState({ name: "", url: "", user_id: "", project_id: "" });
  const [key, setKey] = useState("");
  const { run, busy } = useAction();
  const can = useCan();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <>
      <PageHead title="Calendly" sub="Booking links per person/project and lead → booking tracking. Bookings are matched to leads by email.">
        {can("edit") && <button className="btn" disabled={!data.connected || busy} onClick={() => run(() => api("/leads/calendly/sync", { method: "POST" }), "Bookings pulled").then(reload)}>Pull bookings</button>}
        {can("manage_integrations") && <button className="btn" disabled={!data.connected || busy} onClick={() => run(() => api("/leads/calendly/register-webhook", { method: "POST" }), "Webhook registered").then(reload)}>Register webhook in Calendly</button>}
      </PageHead>
      <LeadsNav />
      {!data.connected && <NC what="Calendly API" provider="calendly">is not connected — API pulls and automatic webhook registration are unavailable. The webhook receiver below works without it.</NC>}
      <div className="grid g3" style={{ marginBottom: 16 }}>
        <Kpi v={data.stats.total} l="Bookings received" /><Kpi v={data.stats.matched} l="Matched to leads" />
        <Kpi v={data.stats.match_rate == null ? "—" : `${Math.round(data.stats.match_rate * 100)}%`} l="Match rate" />
      </div>
      <div className="split">
        <div className="stack">
          <Panel title="Booking links" pad={false}>
            {!data.links.length ? <Empty title="No booking links">Add a Calendly scheduling link; share the tracked URL so bookings are attributed.</Empty> :
              <div className="table-wrap"><table className="t"><thead><tr><th>Link</th><th>Owner</th><th className="r">Bookings</th><th>Tracked URL</th><th /></tr></thead>
                <tbody>{data.links.map((l: any) => <tr key={l.id}><td><b>{l.name}</b><div className="faint small mono-url">{l.url}</div></td><td className="small">{l.user || "—"}</td><td className="r num">{l.bookings}</td>
                  <td><Copy text={l.tracked_url} label="Copy tracked URL" /></td>
                  <td>{can("delete") && <button className="btn ghost sm" onClick={() => run(() => api(`/leads/calendly/links/${l.id}`, { method: "DELETE" })).then(reload)} aria-label="Remove">✕</button>}</td></tr>)}</tbody></table></div>}
            {can("create") && <form className="row" style={{ padding: 12, gap: 8, flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); run(() => api("/leads/calendly/links", { body: { ...link, user_id: link.user_id || null, project_id: link.project_id || null } }), "Link added").then(() => { setLink({ name: "", url: "", user_id: "", project_id: "" }); reload(); }); }}>
              <input className="input" style={{ width: 160 }} placeholder="Name" value={link.name} onChange={(e) => setLink({ ...link, name: e.target.value })} required />
              <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="https://calendly.com/you/30min" value={link.url} onChange={(e) => setLink({ ...link, url: e.target.value })} required />
              <select className="input" style={{ width: "auto" }} value={link.user_id} onChange={(e) => setLink({ ...link, user_id: e.target.value })} aria-label="Owner"><option value="">Owner…</option>{(meta?.users || []).map((u: any) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
              <select className="input" style={{ width: "auto" }} value={link.project_id} onChange={(e) => setLink({ ...link, project_id: e.target.value })} aria-label="Project"><option value="">Project…</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
              <button className="btn">Add</button></form>}
          </Panel>
          <Panel title="Bookings" pad={false}>
            {!data.bookings.length ? <Empty title="No bookings yet" /> :
              <div className="table-wrap"><table className="t"><thead><tr><th>Invitee</th><th>Event</th><th>Start</th><th>Status</th><th>Lead</th><th>Signed</th></tr></thead>
                <tbody>{data.bookings.map((b: any) => <tr key={b.id}><td>{b.name}<div className="faint small">{b.email}</div></td><td className="small">{b.event_name || "—"}</td><td className="small">{fmtDateTime(b.start_time)}</td>
                  <td><Status s={b.status} /></td><td>{b.lead_id ? <Link to={`/leads?lead=${b.lead_id}`}>Open</Link> : <span className="faint small">no match</span>}</td><td className="small">{b.signature_verified ? "✓" : "—"}</td></tr>)}</tbody></table></div>}
          </Panel>
        </div>
        <Panel title="Webhook receiver">
          <div className="form">
            <div><div className="row" style={{ justifyContent: "space-between" }}><b className="small">Webhook URL (invitee.created, invitee.canceled)</b><Copy text={data.webhook_url} /></div><Code>{data.webhook_url}</Code></div>
            <p className="small">Signature verification: {data.signing_key_configured ? <Badge kind="pass">signing key configured</Badge> : <Badge kind="NOT_CONNECTED">no signing key — unsigned events accepted</Badge>}</p>
            {can("manage_integrations") && <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api("/leads/calendly/settings", { method: "PUT", body: { signing_key: key } }), "Signing key saved").then(() => { setKey(""); reload(); }); }}>
              <input className="input" type="password" style={{ flex: 1 }} placeholder="Webhook signing key" value={key} onChange={(e) => setKey(e.target.value)} minLength={16} /><button className="btn">Save key</button></form>}
            <label className="field">Create leads for unknown invitees in<select className="input" value={data.settings.project_id || ""} disabled={!can("manage_integrations")}
              onChange={(e) => run(() => api("/leads/calendly/settings", { method: "PUT", body: { project_id: e.target.value || null } }), "Saved").then(reload)}>
              <option value="">— only match existing leads —</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
            {data.settings.last_pull_at && <p className="muted small">Last API pull {ago(data.settings.last_pull_at)}</p>}
            {can("manage_integrations") && <button className="btn ghost sm" onClick={() => confirm("Rotate the webhook URL? Update it in Calendly afterwards.") && run(() => api("/leads/calendly/rotate-url", { method: "POST" })).then(reload)}>Rotate webhook URL</button>}
          </div>
        </Panel>
      </div>
    </>
  );
}
