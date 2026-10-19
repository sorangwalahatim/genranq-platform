import { useRef, useState } from "react";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { NoProjects, StatusBadge, useProject } from "./shared";

const REDIRECT_OPTS = [
  { value: "pending", label: "Not done" },
  { value: "done", label: "Done" },
  { value: "error", label: "Error" },
];

export default function RedirectsPage() {
  const { pid, projects, picker } = useProject();
  const can = useCan();
  const { run, busy } = useAction();
  const [oldUrl, setOldUrl] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const data = useApi<any>(pid ? `/redirects?project_id=${pid}` : null, [pid]);

  if (projects && !projects.length) return <><PageHead title="Redirect plan" />{<NoProjects />}</>;

  const add = () => {
    if (!oldUrl.trim() || !newUrl.trim()) return;
    run(() => api("/redirects", { body: { project_id: pid, old_url: oldUrl.trim(), new_url: newUrl.trim() } }), "Redirect saved")
      .then(() => { setOldUrl(""); setNewUrl(""); data.reload(); });
  };

  const upload = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    await run(() => api(`/redirects/import?project_id=${pid}`, { form: fd }), "Redirects imported");
    if (fileRef.current) fileRef.current.value = "";
    data.reload();
  };

  const flatten = (p: any) =>
    run(() => api(`/redirects/${p.rule_ids[0]}`, { method: "PATCH", body: { new_url: p.final_url } }), "Chain flattened").then(() => data.reload());

  const problems: any[] = data.data?.chains || data.data?.problems || [];
  const rules: any[] = data.data?.rules || [];

  return (
    <>
      <PageHead title="Redirect plan" sub="Old URL → new URL for the site move. Chains (A → B → C) and loops are flagged so you can flatten them.">{picker}</PageHead>
      {data.error ? <ErrorBox error={data.error} /> : data.loading && !data.data ? <Loading /> : (
        <>
          {problems.length > 0 && (
            <Panel title="Needs attention">
              <div className="stack">
                {problems.map((p: any, i: number) => (
                  <div key={i} className="panel" style={{ padding: 12, borderLeft: "4px solid var(--fail, #dc2626)" }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                      <Badge kind="fail">{p.type === "loop" ? "Loop" : "Chain"}</Badge>
                      <code style={{ wordBreak: "break-all" }}>{(p.hops || []).join(" → ")}</code>
                    </div>
                    <div style={{ marginTop: 6, fontSize: 13, opacity: 0.85 }}>{p.suggestion}</div>
                    {p.type === "chain" && can("edit") && (
                      <div style={{ marginTop: 8 }}>
                        <button className="btn primary" disabled={busy} onClick={() => flatten(p)}>Flatten chain</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </Panel>
          )}
          <Panel title="Redirects" actions={can("create") && (
            <>
              <input ref={fileRef} type="file" accept=".csv" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
              <button className="btn" disabled={busy} onClick={() => fileRef.current?.click()}>{busy ? "Uploading…" : "Import CSV"}</button>
            </>
          )}>
            {can("create") && (
              <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
                <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="Old URL (e.g. /old-page)" value={oldUrl} onChange={(e) => setOldUrl(e.target.value)} />
                <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="New URL (e.g. /new-page)" value={newUrl} onChange={(e) => setNewUrl(e.target.value)} />
                <button className="btn primary" disabled={busy || !oldUrl.trim() || !newUrl.trim()} onClick={add}>Add</button>
              </div>
            )}
            {!rules.length ? (
              <Empty title="No redirects yet">Add them one by one, or import a CSV with <code>old_url</code> and <code>new_url</code> columns.</Empty>
            ) : (
              <div className="table-wrap"><table className="t">
                <thead><tr><th>Old URL</th><th>New URL</th><th>Status</th><th>Note</th><th></th></tr></thead>
                <tbody>
                  {rules.map((r: any) => (
                    <RuleRow key={r.id} rule={r} canEdit={can("edit")} canDelete={can("delete")} onReload={() => data.reload()} />
                  ))}
                </tbody>
              </table></div>
            )}
          </Panel>
        </>
      )}
    </>
  );
}

function RuleRow({ rule, canEdit, canDelete, onReload }: { rule: any; canEdit: boolean; canDelete: boolean; onReload: () => void }) {
  const { run, busy } = useAction();
  const setStatus = (status: string) => run(() => api(`/redirects/${rule.id}`, { method: "PATCH", body: { status } }), "Saved").then(onReload);
  const del = () => run(() => api(`/redirects/${rule.id}`, { method: "DELETE" }), "Deleted").then(onReload);
  return (
    <tr>
      <td><code style={{ wordBreak: "break-all" }}>{rule.old_url}</code></td>
      <td><code style={{ wordBreak: "break-all" }}>{rule.new_url}</code></td>
      <td>{canEdit ? <Select value={rule.status} onChange={setStatus} options={REDIRECT_OPTS} /> : <StatusBadge s={rule.status} />}</td>
      <td style={{ fontSize: 13, opacity: 0.8 }}>{rule.note || "—"}</td>
      <td>{canDelete && <button className="btn" disabled={busy} onClick={del}>Delete</button>}</td>
    </tr>
  );
}
