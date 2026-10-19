import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Modal, Select } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi } from "../../lib/hooks";
import { BucketBadge, SevBadge, StatusBadge, sourceLabel } from "./shared";

/** Detail drawer: plain-words explanation, step-by-step fix, affected pages, actions. */
export default function ErrorDetail({ item, canEdit, onDone }: { item: any; canEdit: boolean; onDone: () => void }) {
  const { run, busy } = useAction();
  const [ignoreOpen, setIgnoreOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [assignee, setAssignee] = useState<string>(item.assignee || "");
  const team = useApi<{ users: any[] }>("/team");

  const patch = (body: any, ok: string) => run(() => api(`/errorcenter/${item.id}`, { method: "PATCH", body }), ok).then((r) => r && onDone());

  const createTask = () => run(() => api(`/errorcenter/${item.id}/task`, { method: "POST" }), "Task created and assigned").then((r) => r && onDone());
  const smartAssign = () => run(() => api(`/errorcenter/${item.id}/smart-assign`, { method: "POST" }), "Re-assigned").then((r) => r && onDone());
  const saveAssignee = () => run(() => api(`/errorcenter/${item.id}`, { method: "PATCH", body: { assignee: assignee || null } }), "Assignee updated").then((r) => r && onDone());
  const ignore = () => {
    if (!reason.trim()) return;
    run(() => api(`/errorcenter/${item.id}`, { method: "PATCH", body: { status: "ignored", ignore_reason: reason.trim() } }), "Ignored").then((r) => { if (r) { setIgnoreOpen(false); onDone(); } });
  };

  const steps = (item.solution || "").split("\n").filter((l: string) => l.trim());
  const users = (team.data?.users || []).filter((u: any) => u.is_active && u.role !== "client");

  return (
    <div className="stack">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <SevBadge s={item.severity} />
        <StatusBadge s={item.status} />
        <Badge>{sourceLabel(item.source)}</Badge>
        <BucketBadge b={item.bucket} />
        {item.times_seen > 1 && <span className="faint small">Seen {item.times_seen} times</span>}
      </div>

      {item.detail && <p>{item.detail}</p>}

      <div>
        <h3 style={{ margin: "4px 0 8px" }}>How to fix it</h3>
        <ol style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 6 }}>
          {steps.map((s: string, i: number) => <li key={i}>{s.replace(/^\d+\.\s*/, "")}</li>)}
        </ol>
      </div>

      {!!(item.urls || []).length && (
        <div>
          <h3 style={{ margin: "4px 0 8px" }}>Affected pages ({item.urls.length})</h3>
          <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4, maxHeight: 220, overflow: "auto" }}>
            {item.urls.slice(0, 100).map((u: string, i: number) => <li key={i} style={{ wordBreak: "break-all" }}><span className="small">{u}</span></li>)}
          </ul>
          {item.urls.length > 100 && <div className="faint small">…and {item.urls.length - 100} more</div>}
        </div>
      )}

      {canEdit && (
        <>
          <div>
            <h3 style={{ margin: "4px 0 8px" }}>Who fixes this</h3>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <Select value={assignee} onChange={setAssignee} placeholder="Unassigned"
                options={[{ value: "", label: "Unassigned" }, ...users.map((u: any) => ({ value: u.id, label: `${u.name || u.email} (${u.role})` }))]} />
              <button className="btn sm" disabled={busy} onClick={saveAssignee}>Save</button>
              <button className="btn sm ghost" disabled={busy} onClick={smartAssign}>Auto-pick</button>
            </div>
            {item.assignee_name && <div className="faint small" style={{ marginTop: 6 }}>Currently: {item.assignee_name}</div>}
          </div>

          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" disabled={busy} onClick={createTask}>Create task</button>
            {item.status !== "fixed" && <button className="btn" disabled={busy} onClick={() => patch({ status: "fixed" }, "Marked fixed")}>Mark fixed</button>}
            {item.status !== "in_progress" && <button className="btn" disabled={busy} onClick={() => patch({ status: "in_progress" }, "In progress")}>Start work</button>}
            <button className="btn ghost" disabled={busy} onClick={() => setIgnoreOpen(true)}>Ignore…</button>
          </div>
        </>
      )}

      <div className="faint small">Tip: a task carries the fix steps above, so whoever picks it up knows exactly what to do.</div>

      {ignoreOpen && (
        <Modal title="Ignore this problem" onClose={() => setIgnoreOpen(false)}
          foot={<><button className="btn ghost" onClick={() => setIgnoreOpen(false)}>Cancel</button>
            <button className="btn primary" disabled={busy || !reason.trim()} onClick={ignore}>Ignore it</button></>}>
          <p>Why is this not a problem? A reason is required.</p>
          <input className="input" autoFocus placeholder="e.g. by design, false alarm, client accepted…" value={reason} onChange={(e) => setReason(e.target.value)} style={{ width: "100%" }} />
          <div className="faint small" style={{ marginTop: 8 }}>Ignored problems stay in the <Link to="/error-center/ignored">Ignored</Link> list and can be brought back anytime.</div>
        </Modal>
      )}
    </div>
  );
}
