import { useState } from "react";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel, Select } from "../components/ui";
import { api } from "../lib/api";
import { ago, human } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

export default function Approvals() {
  const [status, setStatus] = useState("pending");
  const { data, error, loading, reload } = useApi<any[]>(`/approvals${status ? `?status=${status}` : ""}`, [status]);
  const [decide, setDecide] = useState<any>(null);
  const [create, setCreate] = useState(false);
  const can = useCan();
  return (
    <>
      <PageHead title="Approvals" sub="Multi-step sign-off for content, access and changes. Each step needs the named role or higher.">
        <Select value={status} onChange={setStatus} placeholder="All" label="Status" options={["pending", "approved", "rejected"].map((s) => ({ value: s, label: human(s) }))} />
        {can("create") && <button className="btn primary" onClick={() => setCreate(true)}>Request approval</button>}
      </PageHead>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : (
        <Panel pad={false}>
          {!data?.length ? <Empty title={status === "pending" ? "Nothing waiting for approval" : "No approvals"} /> : (
            <div className="table-wrap"><table className="t"><thead><tr><th>Request</th><th>Type</th><th>Chain</th><th>Status</th><th>Requested</th><th /></tr></thead>
              <tbody>{data.map((a) => (
                <tr key={a.id}><td>{a.title}{a.notes && <div className="faint small">{a.notes}</div>}</td><td>{human(a.entity_type)}</td>
                  <td className="small">{a.chain.map((r: string, i: number) => <span key={i} style={{ fontWeight: i === a.step && a.status === "pending" ? 600 : 400, color: i < a.step ? "var(--pass)" : undefined }}>{i > 0 && " → "}{human(r)}</span>)}</td>
                  <td><Badge kind={a.status} /></td><td className="muted">{ago(a.created_at)}</td>
                  <td>{a.can_decide && <button className="btn sm primary" onClick={() => setDecide(a)}>Review</button>}</td></tr>
              ))}</tbody></table></div>
          )}
        </Panel>
      )}
      {decide && <Decide a={decide} onClose={() => setDecide(null)} onDone={reload} />}
      {create && <Create onClose={() => setCreate(false)} onDone={reload} />}
    </>
  );
}

function Decide({ a, onClose, onDone }: any) {
  const [notes, setNotes] = useState("");
  const { run } = useAction();
  const go = (decision: string) => run(() => api(`/approvals/${a.id}/decide`, { body: { decision, notes: notes || null } }), decision === "approve" ? "Approved" : "Rejected").then((r) => { if (r) { onDone(); onClose(); } });
  return (
    <Modal title={a.title} onClose={onClose} foot={<><button className="btn danger" onClick={() => go("reject")} disabled={!notes}>Reject</button><button className="btn primary" onClick={() => go("approve")}>Approve</button></>}>
      <p className="muted" style={{ marginBottom: 12 }}>Step {a.step + 1} of {a.chain.length}: needs {human(a.awaiting_role)} sign-off.</p>
      {a.decisions.map((d: any, i: number) => <p key={i} className="small">✓ {human(d.role)} {d.decision}d {d.notes && `— ${d.notes}`}</p>)}
      <label className="field" style={{ marginTop: 12 }}>Notes<span className="hint">Required when rejecting.</span><textarea className="input" value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
    </Modal>
  );
}

function Create({ onClose, onDone }: any) {
  const [f, setF] = useState({ title: "", entity_type: "content_item", chain: "manager,admin", notes: "" });
  const { run } = useAction();
  return (
    <Modal title="Request approval" onClose={onClose} foot={<button className="btn primary" disabled={!f.title} onClick={() => run(() => api("/approvals", { body: { ...f, chain: f.chain.split(",") } }), "Approval requested").then((r) => { if (r) { onDone(); onClose(); } })}>Send request</button>}>
      <div className="form">
        <label className="field">What needs approval?<input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
        <label className="field">Type<select className="input" value={f.entity_type} onChange={(e) => setF({ ...f, entity_type: e.target.value })}><option value="content_item">Content</option><option value="task">Task</option><option value="permission">Permission change</option><option value="credential_access">Credential access</option></select></label>
        <label className="field">Approval chain<select className="input" value={f.chain} onChange={(e) => setF({ ...f, chain: e.target.value })}><option value="manager">Manager</option><option value="manager,admin">Manager → Admin</option><option value="admin,owner">Admin → Owner</option><option value="manager,admin,owner">Manager → Admin → Owner</option></select></label>
        <label className="field">Notes<textarea className="input" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      </div>
    </Modal>
  );
}
