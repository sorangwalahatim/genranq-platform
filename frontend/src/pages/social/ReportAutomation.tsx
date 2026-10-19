import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { bytes, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { StatusBadge, toLocalInput, fromLocalInput } from "./shared";

const CH_LABEL: Record<string, string> = { in_app: "In-app", teams: "Teams", email: "Email" };

export default function ReportAutomation() {
  const { data, error, loading, reload } = useApi<any>("/social/report-schedules");
  const { data: deliveries, reload: reloadD } = useApi<any[]>("/social/report-deliveries", [], { poll: 15000 });
  const { data: projects } = useApi<any[]>("/projects");
  const [edit, setEdit] = useState<any>(null);
  const [sp] = useSearchParams();
  const focus = sp.get("delivery");
  const can = useCan();
  const { run } = useAction();
  const pname = (id: string) => (projects || []).find((p) => p.id === id)?.name || "—";
  const refresh = () => { reload(); reloadD(); };
  return (
    <>
      <PageHead title="Report automation" sub="Scheduled, white-label report packs per project: latest audit (PDF/Excel), monitoring summary, rank changes and social analytics — delivered in-app, to Microsoft Teams and by email.">
        {can("export") && data?.plan.scheduled_reports && <button className="btn primary" onClick={() => setEdit({})}>New scheduled report</button>}
      </PageHead>
      <ErrorBox error={error} />
      {data && !data.plan.scheduled_reports && <div className="notice warn" style={{ marginBottom: 14 }}><div><b>Scheduled reports are not included in your plan.</b> On-demand exports stay available under <Link to="/reports">Reports</Link>. <Link to="/billing">Plan &amp; usage</Link></div></div>}
      {data && !data.email.configured && <div className="notice nc" style={{ marginBottom: 14 }}><div><b>Email NOT CONNECTED.</b> {data.email.message}</div></div>}
      {data && !data.teams_channels.some((c: any) => c.report_ready) && <p className="faint small" style={{ marginBottom: 12 }}>No Microsoft Teams channel receives “Reports ready” yet — <Link to="/notifications/teams">set one up</Link>.</p>}
      <Panel title="Schedules" pad={false}>
        {loading && !data ? <Loading /> : !data?.schedules.length ? <Empty title="No scheduled reports">Create a weekly or monthly pack for a client project.</Empty> : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Report</th><th>Project</th><th>Frequency</th><th>Contents</th><th>Delivery</th><th>Next run</th><th>Last</th><th /></tr></thead>
            <tbody>{data.schedules.map((r: any) => (
              <tr key={r.id}>
                <td><b>{r.name}</b>{r.white_label && <div className="faint small">White-label</div>}{!r.enabled && <div><StatusBadge s="disabled" /></div>}</td>
                <td>{pname(r.project_id)}</td><td>{human(r.frequency)}</td>
                <td className="small">{r.contents.map((c: string) => data.contents[c]).join(", ")}</td>
                <td className="small">{Object.entries(r.channels).filter(([, v]) => v).map(([k]) => CH_LABEL[k]).join(", ")}<div className="faint">{r.recipient_user_ids.length} user(s), {r.recipient_emails.length} email(s)</div></td>
                <td className="small">{r.next_run_at ? fmtDateTime(r.next_run_at) : "—"}</td>
                <td>{r.last_delivery ? <StatusBadge s={r.last_delivery.status} /> : <span className="faint">never</span>}</td>
                <td className="r nowrap">{can("export") && <>
                  <button className="btn sm" onClick={() => run(() => api(`/social/report-schedules/${r.id}/run`, { method: "POST" }), "Report run queued").then(() => setTimeout(refresh, 3000))}>Run now</button>{" "}
                  <button className="btn sm" onClick={() => setEdit(r)}>Edit</button>{" "}
                  <button className="btn sm" onClick={() => run(() => api(`/social/report-schedules/${r.id}`, { method: "PATCH", body: { enabled: !r.enabled } })).then(reload)}>{r.enabled ? "Pause" : "Resume"}</button></>}{" "}
                  {can("delete") && <button className="btn sm danger" onClick={() => confirm("Delete this schedule? Delivered files are kept.") && run(() => api(`/social/report-schedules/${r.id}`, { method: "DELETE" })).then(reload)}>Delete</button>}
                </td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Panel>
      <div style={{ marginTop: 16 }}>
        <Panel title="Delivery history" pad={false}>
          {!deliveries?.length ? <Empty title="Nothing delivered yet">Each run stores its files here, even when email or Teams is not connected.</Empty> : (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>When</th><th>Report</th><th>Status</th><th>Files</th><th>Channels</th></tr></thead>
              <tbody>{deliveries.map((d) => (
                <tr key={d.id} style={focus === d.id ? { background: "var(--accent-soft)" } : undefined}>
                  <td className="small">{fmtDateTime(d.created_at)}<div className="faint">{human(d.triggered_by)}</div></td>
                  <td>{d.schedule || "(deleted)"}<div className="faint small">{d.project}</div></td>
                  <td><StatusBadge s={d.status} />{d.error && <div className="small" style={{ color: "var(--critical)" }}>{d.error}</div>}</td>
                  <td className="small">{d.files.map((f: any) => <div key={f.stored_file_id}><button className="btn ghost sm" onClick={() => download(`/social/report-deliveries/${d.id}/files/${f.stored_file_id}`, f.filename)}>⬇ {f.filename}</button> <span className="faint">{bytes(f.size_bytes)}</span></div>)}
                    {(d.summary?.notes || []).map((n: string) => <div key={n} className="faint">{n}</div>)}</td>
                  <td className="small">{Object.entries(d.channels || {}).map(([k, v]: any) => <div key={k}>{CH_LABEL[k]}: <StatusBadge s={v.status} />{v.error && <span className="faint"> {v.error}</span>}</div>)}</td>
                </tr>))}</tbody>
            </table></div>
          )}
        </Panel>
      </div>
      {edit && data && <ScheduleModal r={edit.id ? edit : null} meta={data} projects={projects || []} onClose={() => setEdit(null)} onDone={reload} />}
    </>
  );
}

function ScheduleModal({ r, meta, projects, onClose, onDone }: any) {
  const { data: team } = useApi<any>("/team");
  const first = new Date(Date.now() + 86400_000); first.setHours(8, 0, 0, 0);
  const [f, setF] = useState<any>(() => r ? { ...r, recipient_emails: r.recipient_emails.join(", ") } : {
    project_id: projects[0]?.id || "", name: "", frequency: "weekly", contents: ["audit_pdf", "monitoring", "rank_changes", "social"],
    recipient_user_ids: [], recipient_emails: "", channels: { in_app: true, teams: false, email: false }, white_label: false, first_run_at: toLocalInput(first) });
  const { run, busy } = useAction();
  const toggle = (k: string, v: string) => setF((x: any) => ({ ...x, [k]: x[k].includes(v) ? x[k].filter((y: string) => y !== v) : [...x[k], v] }));
  const save = () => {
    const body: any = { name: f.name, frequency: f.frequency, contents: f.contents, recipient_user_ids: f.recipient_user_ids, white_label: f.white_label,
      recipient_emails: f.recipient_emails.split(/[,\s;]+/).filter(Boolean), channels: f.channels };
    return run(() => r ? api(`/social/report-schedules/${r.id}`, { method: "PATCH", body })
      : api("/social/report-schedules", { body: { ...body, project_id: f.project_id, first_run_at: f.first_run_at ? fromLocalInput(f.first_run_at) : null } }), "Saved")
      .then((x) => { if (x) { onDone(); onClose(); } });
  };
  const users = (team?.users || []).filter((u: any) => u.is_active);
  return (
    <Modal title={r ? `Edit ${r.name}` : "New scheduled report"} onClose={onClose} wide foot={<button className="btn primary" disabled={busy || !f.name || !f.contents.length || !f.project_id} onClick={save}>Save</button>}>
      <div className="form">
        <div className="grid g2">
          <label className="field">Name<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></label>
          <label className="field">Project<select className="input" disabled={!!r} value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}>{projects.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        </div>
        <div className="grid g2">
          <label className="field">Frequency<select className="input" value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value })}><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></label>
          {!r && <label className="field">First delivery<input className="input" type="datetime-local" value={f.first_run_at} onChange={(e) => setF({ ...f, first_run_at: e.target.value })} /></label>}
        </div>
        <div className="field">Contents<div className="chips" style={{ marginTop: 6 }}>{Object.entries(meta.contents).map(([k, l]: any) => <span key={k} role="checkbox" aria-checked={f.contents.includes(k)} tabIndex={0} className={`chip ${f.contents.includes(k) ? "on" : ""}`} onClick={() => toggle("contents", k)}>{l}</span>)}</div></div>
        <div className="field">Recipients (in-app + email)<div className="chips" style={{ marginTop: 6 }}>{users.map((u: any) => <span key={u.id} role="checkbox" aria-checked={f.recipient_user_ids.includes(u.id)} tabIndex={0} className={`chip ${f.recipient_user_ids.includes(u.id) ? "on" : ""}`} onClick={() => toggle("recipient_user_ids", u.id)}>{u.name || u.email}</span>)}</div>
          <span className="hint">Nobody selected = the whole team is notified in-app.</span></div>
        <label className="field">Extra email addresses<span className="hint">Comma separated (e.g. the client)</span><input className="input" value={f.recipient_emails} onChange={(e) => setF({ ...f, recipient_emails: e.target.value })} /></label>
        <div className="field">Delivery
          <label className="check"><input type="checkbox" checked disabled /> In-app notification (always)</label>
          <label className="check"><input type="checkbox" checked={!!f.channels.teams} onChange={(e) => setF({ ...f, channels: { ...f.channels, teams: e.target.checked } })} /> Microsoft Teams {!meta.teams_channels.some((c: any) => c.report_ready) && <span className="faint small">(no channel subscribed — will show NOT CONNECTED)</span>}</label>
          <label className="check"><input type="checkbox" checked={!!f.channels.email} onChange={(e) => setF({ ...f, channels: { ...f.channels, email: e.target.checked } })} /> Email {!meta.email.configured && <span className="faint small">(SMTP NOT CONNECTED — files are still stored)</span>}</label>
        </div>
        <label className="check"><input type="checkbox" disabled={!meta.plan.white_label} checked={f.white_label} onChange={(e) => setF({ ...f, white_label: e.target.checked })} /> White-label (your branding only{!meta.plan.white_label ? " — not in your plan" : ""})</label>
      </div>
    </Modal>
  );
}
