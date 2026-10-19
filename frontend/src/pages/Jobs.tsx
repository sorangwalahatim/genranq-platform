import { useState } from "react";
import { Badge, Drawer, Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../components/ui";
import { api } from "../lib/api";
import { ago, fmtDateTime, human } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

export default function Jobs() {
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useApi<any>(`/jobs${status ? `?status=${status}` : ""}`, [status], { poll: 4000 });
  const [open, setOpen] = useState<any>(null);
  const { run } = useAction();
  const can = useCan();
  return (
    <>
      <PageHead title="Background jobs" sub="Crawls, audits, rank checks, PageSpeed tests and report generation run in a queue with automatic retries. Jobs that exhaust their retries stay here as failed so they can be retried.">
        <Select value={status} onChange={setStatus} placeholder="All statuses" label="Status" options={["QUEUED", "RUNNING", "COMPLETED", "FAILED"].map((s) => ({ value: s, label: `${human(s.toLowerCase())} (${data?.counts?.[s] || 0})` }))} />
      </PageHead>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : (
        <Panel pad={false}>
          {!data?.items.length ? <Empty title="No jobs" /> : (
            <table className="t"><thead><tr><th>Job</th><th>Status</th><th>Attempts</th><th>Progress</th><th>Queued</th><th>Finished</th><th /></tr></thead>
              <tbody>{data.items.map((j: any) => (
                <tr key={j.id} className="click" onClick={() => setOpen(j)}><td>{human(j.kind)}{j.payload?.triggered_by === "schedule" && <span className="faint small"> scheduled</span>}</td>
                  <td><Badge kind={j.status} />{j.status === "QUEUED" && j.attempts > 0 && <div className="faint small">retry at {fmtDateTime(j.run_at)}</div>}</td>
                  <td className="num">{j.attempts}/{j.max_attempts}</td><td style={{ width: 120 }}><div className="progress"><div style={{ width: `${j.progress}%` }} /></div></td>
                  <td className="muted">{ago(j.created_at)}</td><td className="muted">{ago(j.finished_at)}</td>
                  <td onClick={(e) => e.stopPropagation()}>{j.status === "FAILED" && can("edit") && <button className="btn sm" onClick={() => run(() => api(`/jobs/${j.id}/retry`, { method: "POST" }), "Job re-queued").then(reload)}>Retry</button>}
                    {j.status === "QUEUED" && can("edit") && <button className="btn sm ghost" onClick={() => run(() => api(`/jobs/${j.id}/cancel`, { method: "POST" }), "Job cancelled").then(reload)}>Cancel</button>}</td></tr>
              ))}</tbody></table>
          )}
        </Panel>
      )}
      {open && <Drawer title={human(open.kind)} onClose={() => setOpen(null)}>
        <dl className="kv"><dt>Status</dt><dd><Badge kind={open.status} /></dd><dt>Attempts</dt><dd>{open.attempts} of {open.max_attempts}</dd><dt>Started</dt><dd>{fmtDateTime(open.started_at)}</dd><dt>Finished</dt><dd>{fmtDateTime(open.finished_at)}</dd></dl>
        <h3 style={{ marginTop: 14 }}>Input</h3><pre className="code">{JSON.stringify(open.payload, null, 2)}</pre>
        {open.result && <><h3>Result</h3><pre className="code">{JSON.stringify(open.result, null, 2)}</pre></>}
        {open.error && <><h3>Error</h3><pre className="code">{open.error}</pre></>}
      </Drawer>}
    </>
  );
}
