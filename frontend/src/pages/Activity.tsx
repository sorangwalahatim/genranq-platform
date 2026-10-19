import { useState } from "react";
import { Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../components/ui";
import { fmtDateTime } from "../lib/format";
import { useApi } from "../lib/hooks";

const AREAS = ["auth", "org", "team", "project", "task", "site", "keyword", "rank", "audit", "seo_plan", "report", "integration", "vault", "approval", "billing", "job", "automation", "spec"];

export default function Activity() {
  const [action, setAction] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const qs = new URLSearchParams({ ...(action && { action }), ...(q && { q }), limit: "100", offset: String(page * 100) }).toString();
  const { data, error, loading } = useApi<any[]>(`/activity?${qs}`, [qs]);
  return (
    <>
      <PageHead title="Activity log" sub="An immutable record of who did what. Entries cannot be edited or deleted." />
      <div className="row" style={{ marginBottom: 12 }}>
        <Select value={action} onChange={(v) => { setAction(v); setPage(0); }} placeholder="All areas" label="Area" options={AREAS.map((a) => ({ value: a, label: a.replace("_", " ") }))} />
        <input className="input" style={{ maxWidth: 260 }} placeholder="Search action or target" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} aria-label="Search activity" />
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : (
        <Panel pad={false}>
          {!data?.length ? <Empty title="No matching activity" /> : (
            <div className="table-wrap"><table className="t"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Details</th><th>IP</th></tr></thead>
              <tbody>{data.map((a) => (
                <tr key={a.id}><td className="nowrap">{fmtDateTime(a.created_at)}</td><td>{a.actor}</td><td><code>{a.action}</code></td><td className="small">{a.target || "—"}</td>
                  <td className="small muted" style={{ maxWidth: 320 }}>{Object.keys(a.meta || {}).length ? JSON.stringify(a.meta).slice(0, 160) : ""}</td><td className="faint small">{a.ip || ""}</td></tr>
              ))}</tbody></table></div>
          )}
          <div className="panel-head"><button className="btn sm" disabled={!page} onClick={() => setPage(page - 1)}>Newer</button><button className="btn sm" disabled={(data?.length || 0) < 100} onClick={() => setPage(page + 1)}>Older</button></div>
        </Panel>
      )}
    </>
  );
}
