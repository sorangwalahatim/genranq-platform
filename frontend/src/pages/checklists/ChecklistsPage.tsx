import { useState } from "react";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { NoProjects, useProject } from "./shared";

const TABS: [string, string][] = [["pre_launch", "Pre-Launch"], ["post_launch", "Post-Launch"], ["tech_audit", "Tech Audit"]];
const STATUS_OPTS = [
  { value: "pending", label: "Not checked" },
  { value: "pass", label: "Pass" },
  { value: "fail", label: "Fail" },
  { value: "partial", label: "Partial" },
];

export default function ChecklistsPage() {
  const { pid, projects, picker } = useProject();
  const [tab, setTab] = useState("pre_launch");
  const can = useCan();
  const { run, busy } = useAction();
  const lists = useApi<any[]>(pid ? `/checklists?project_id=${pid}` : null, [pid]);
  const current = (lists.data || []).find((c: any) => c.kind === tab);
  const detail = useApi<any>(current ? `/checklists/${current.id}` : null, [current?.id]);

  if (projects && !projects.length) return <><PageHead title="Checklists" />{<NoProjects />}</>;
  if (lists.error) return <><PageHead title="Checklists" sub="Pre-launch, post-launch and tech audit checks — tracked per project.">{picker}</PageHead><ErrorBox error={lists.error} /></>;

  const seed = () => run(() => api("/checklists/seed", { body: { project_id: pid } }), "Checklists loaded").then(() => lists.reload());

  return (
    <>
      <PageHead title="Checklists" sub="Pre-launch, post-launch and tech audit checks for this project. Mark each check as pass, fail or partial as you verify it.">{picker}</PageHead>
      {lists.loading && !lists.data ? <Loading /> : !(lists.data || []).length ? (
        <Panel><Empty title="No checklists for this project yet" action={can("create") && <button className="btn primary" disabled={busy} onClick={seed}>Load checklists</button>}>
          This loads the pre-launch, post-launch and tech audit checklists from your Excel sheets — 130+ checks, ready to work through.
        </Empty></Panel>
      ) : (
        <>
          <div className="tabs" role="tablist">
            {TABS.map(([k, label]) => {
              const c = (lists.data || []).find((x: any) => x.kind === k);
              const p = c?.progress;
              return (
                <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                  {label}{p ? ` (${p.done}/${p.total})` : ""}
                </button>
              );
            })}
          </div>
          {!current ? (
            <Panel><Empty title="Checklist not loaded" action={can("create") && <button className="btn primary" disabled={busy} onClick={seed}>Load checklists</button>}>
              This checklist has not been created for the project yet.
            </Empty></Panel>
          ) : detail.loading && !detail.data ? <Loading /> : detail.error ? <ErrorBox error={detail.error} /> : (
            <ChecklistDetail data={detail.data} canEdit={can("edit")} canCreate={can("create")} onReload={() => { detail.reload(); lists.reload(); }} />
          )}
        </>
      )}
    </>
  );
}

function Progress({ p }: { p: any }) {
  const pct = p.total ? Math.round((100 * p.done) / p.total) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "8px 0 16px" }}>
      <div style={{ flex: 1, height: 10, borderRadius: 6, background: "var(--border, #e5e7eb)", overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent, #16a34a)" }} />
      </div>
      <b>{p.done}/{p.total} done</b>
      {p.fail > 0 && <Badge kind="fail">{p.fail} failed</Badge>}
      {p.partial > 0 && <Badge kind="medium">{p.partial} partial</Badge>}
    </div>
  );
}

function ChecklistDetail({ data, canEdit, canCreate, onReload }: { data: any; canEdit: boolean; canCreate: boolean; onReload: () => void }) {
  return (
    <Panel title={data.label || data.name}>
      <Progress p={data.progress} />
      <div className="stack">
        {(data.items || []).map((it: any) => (
          <ItemRow key={it.id} item={it} canEdit={canEdit} canCreate={canCreate} onReload={onReload} />
        ))}
      </div>
    </Panel>
  );
}

function ItemRow({ item, canEdit, canCreate, onReload }: { item: any; canEdit: boolean; canCreate: boolean; onReload: () => void }) {
  const { run, busy } = useAction();
  const [evidence, setEvidence] = useState(item.evidence || "");
  const setStatus = (status: string) => run(() => api(`/checklist-items/${item.id}`, { method: "PATCH", body: { status } }), "Saved").then(onReload);
  const saveEvidence = () => run(() => api(`/checklist-items/${item.id}`, { method: "PATCH", body: { evidence } }), "Evidence saved").then(onReload);
  const recheck = () => run(() => api(`/checklist-items/${item.id}`, { method: "PATCH", body: { status: "pending" } }), "Marked for re-check").then(onReload);
  const makeTask = () => run(() => api(`/checklist-items/${item.id}/task`, { method: "POST" }), "Task created").then(onReload);
  return (
    <div className="panel" style={{ padding: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div><b>{item.title}</b></div>
          {item.detail && <div style={{ opacity: 0.75, fontSize: 13, marginTop: 4, whiteSpace: "pre-wrap" }}>{item.detail}</div>}
          <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
            {item.owner_role && <Badge>{item.owner_role}</Badge>}
            {item.priority && <Badge kind="medium">{item.priority}</Badge>}
            {item.task_id && <Badge kind="live">Task created</Badge>}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {canEdit ? (
            <Select value={item.status} onChange={setStatus} options={STATUS_OPTS} />
          ) : null}
          {canEdit && item.status !== "pending" && <button className="btn" disabled={busy} onClick={recheck}>Re-check</button>}
          {canCreate && item.status === "fail" && !item.task_id && (
            <button className="btn primary" disabled={busy} onClick={makeTask}>Create task</button>
          )}
        </div>
      </div>
      {canEdit && (
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <input className="input" style={{ flex: 1 }} placeholder="Evidence / notes (e.g. screenshot link, what you verified)" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
          <button className="btn" disabled={busy} onClick={saveEvidence}>Save</button>
        </div>
      )}
    </div>
  );
}
