import { useMemo, useState } from "react";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { ago, fmtDateTime, human } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

export default function Automation() {
  const { data, error, loading, reload } = useApi<any>("/automation/schedules");
  const [show, setShow] = useState(false);
  const can = useCan();
  const { run } = useAction();
  return (
    <>
      <PageHead title="SEO automation" sub="Recurring audits and rank checks. Runs are queued in the background and you’re notified when they finish or fail.">
        {can("create") && <button className="btn primary" onClick={() => setShow(true)}>New schedule</button>}
      </PageHead>
      <ErrorBox error={error} />
      {data && !data.automated_audits_included && <div className="notice warn" style={{ marginBottom: 12 }}>Your plan includes real-time audits only. Scheduled audits are available on Advanced and Enterprise.</div>}
      {loading && !data ? <Loading /> : (
        <Panel pad={false}>
          {!data?.schedules.length ? <Empty title="No schedules yet">Schedule a weekly audit so regressions surface without anyone remembering to check.</Empty> : (
            <table className="t"><thead><tr><th>What</th><th>Target</th><th>Frequency</th><th>Next run</th><th>Last run</th><th>Status</th><th /></tr></thead>
              <tbody>{data.schedules.map((s: any) => (
                <tr key={s.id}><td>{s.kind === "scheduled_audit" ? `Audit (${s.payload.max_pages} pages)` : "Rank check"}</td><td>{s.target}</td><td>{human(s.frequency)}</td>
                  <td>{s.enabled ? fmtDateTime(s.next_run_at) : "—"}</td><td className="muted">{ago(s.last_run_at)}</td><td><Badge kind={s.enabled ? "live" : undefined}>{s.enabled ? "Active" : "Paused"}</Badge></td>
                  <td className="row">{can("edit") && <button className="btn sm" onClick={() => run(() => api(`/automation/schedules/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } })).then(reload)}>{s.enabled ? "Pause" : "Resume"}</button>}
                    {can("delete") && <button className="btn sm ghost danger" onClick={() => confirm("Delete this schedule?") && run(() => api(`/automation/schedules/${s.id}`, { method: "DELETE" })).then(reload)}>Delete</button>}</td></tr>
              ))}</tbody></table>
          )}
        </Panel>
      )}
      {show && <NewSchedule onClose={() => setShow(false)} onDone={reload} />}
    </>
  );
}

function NewSchedule({ onClose, onDone }: any) {
  const { data: projects } = useApi<any[]>("/projects");
  const sites = useMemo(() => (projects || []).flatMap((p) => p.sites.map((s: any) => ({ ...s, project: p.name }))), [projects]);
  const [f, setF] = useState({ kind: "scheduled_audit", frequency: "weekly", site_id: "", project_id: "", max_pages: 250 });
  const { run } = useAction();
  const body = { ...f, site_id: f.site_id || sites[0]?.id, project_id: f.project_id || projects?.[0]?.id };
  return (
    <Modal title="New schedule" onClose={onClose} foot={<button className="btn primary" onClick={() => run(() => api("/automation/schedules", { body }), "Schedule created").then((r) => { if (r) { onDone(); onClose(); } })}>Create schedule</button>}>
      <div className="form">
        <label className="field">What to run<select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="scheduled_audit">Site audit</option><option value="scheduled_rank_check">Rank check</option></select></label>
        {f.kind === "scheduled_audit" ? (<>
          <label className="field">Site<select className="input" value={body.site_id} onChange={(e) => setF({ ...f, site_id: e.target.value })}>{sites.map((s) => <option key={s.id} value={s.id}>{s.domain} — {s.project}</option>)}</select></label>
          <label className="field">Pages per audit<input className="input" type="number" min={10} max={10000} value={f.max_pages} onChange={(e) => setF({ ...f, max_pages: Number(e.target.value) })} /></label>
        </>) : <label className="field">Project<select className="input" value={body.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
        <label className="field">Frequency<select className="input" value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value })}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></label>
      </div>
    </Modal>
  );
}
