import { useState } from "react";
import { Link } from "react-router-dom";
import { Drawer, Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../../components/ui";
import { useApi, useCan } from "../../lib/hooks";
import ErrorDetail from "./ErrorDetail";
import { BucketBadge, NoProjects, SevBadge, StatusBadge, sourceLabel, useProject } from "./shared";

type Item = any;

const STATUS_OPTS = [{ value: "", label: "All open" }, { value: "open", label: "Open" }, { value: "in_progress", label: "In progress" }, { value: "fixed", label: "Fixed" }, { value: "verified", label: "Verified" }];

export default function ErrorInbox() {
  const { pid, projects, picker } = useProject();
  const [status, setStatus] = useState("");
  const [bucket, setBucket] = useState("");
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Item | null>(null);
  const can = useCan();

  const qs = pid ? `project_id=${pid}${status ? `&status=${status}` : ""}${bucket ? `&bucket=${bucket}` : ""}${source ? `&source=${source}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}` : null;
  const list = useApi<{ total: number; items: Item[] }>(qs ? `/errorcenter?${qs}` : null, [qs]);
  const summary = useApi<any>(pid ? `/errorcenter/summary?project_id=${pid}` : null, [pid]);
  const reload = () => { list.reload(); summary.reload(); };

  if (projects && !projects.length) return <><PageHead title="Error Center" /><NoProjects /></>;
  return (
    <>
      <PageHead title="Error Center" sub="Every problem found by Search Console, PageSpeed, Screaming Frog, Ahrefs, Semrush and site audits — in one inbox, assigned to the right person.">
        {picker}
        <Link className="btn" to="/error-center/ignored">Ignored</Link>
      </PageHead>

      {summary.data && (
        <div className="grid g4" style={{ marginBottom: 16 }}>
          <div className="panel kpi"><div className="v">{summary.data.open_total}</div><div className="l">Open problems</div></div>
          <div className="panel kpi"><div className="v">{summary.data.by_bucket?.developer || 0}</div><div className="l">For developer</div></div>
          <div className="panel kpi"><div className="v">{summary.data.by_bucket?.seo || 0}</div><div className="l">For SEO team</div></div>
          <div className="panel kpi"><div className="v">{summary.data.ignored || 0}</div><div className="l">Ignored</div></div>
        </div>
      )}

      <Panel pad={false} actions={
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <input className="input" style={{ width: 200 }} placeholder="Search problems…" value={q} onChange={(e) => setQ(e.target.value)} />
          <Select value={status} onChange={setStatus} options={STATUS_OPTS} placeholder="Status" />
          <Select value={bucket} onChange={setBucket} options={[{ value: "", label: "Everyone" }, { value: "seo", label: "SEO team" }, { value: "developer", label: "Developer" }]} placeholder="Team" />
          <Select value={source} onChange={setSource} options={[{ value: "", label: "All tools" }, ...["gsc", "pagespeed", "screaming_frog", "ahrefs", "semrush", "audit"].map((s) => ({ value: s, label: sourceLabel(s) }))]} placeholder="Tool" />
        </div>
      }>
        {list.error ? <div style={{ padding: 16 }}><ErrorBox error={list.error} /></div>
          : list.loading && !list.data ? <Loading />
          : !list.data?.items.length ? <div style={{ padding: 16 }}><Empty title="No problems here">When an audit tool finds an error, it appears here automatically with a fix plan.</Empty></div>
          : <table className="tbl"><thead><tr><th></th><th>Problem</th><th>Tool</th><th>Pages hit</th><th>Team</th><th>Status</th><th>Assigned to</th></tr></thead>
            <tbody>{list.data.items.map((it: Item) => (
              <tr key={it.id} onClick={() => setOpen(it)} style={{ cursor: "pointer" }}>
                <td><SevBadge s={it.severity} /></td>
                <td><b>{it.title}</b><div className="faint small">{it.times_seen > 1 ? `Seen ${it.times_seen}×` : ""}</div></td>
                <td>{sourceLabel(it.source)}</td>
                <td className="num">{it.url_count}</td>
                <td><BucketBadge b={it.bucket} /></td>
                <td><StatusBadge s={it.status} /></td>
                <td>{it.assignee_name || <span className="faint">—</span>}</td>
              </tr>))}
            </tbody></table>}
      </Panel>

      {open && (
        <Drawer title={open.title} onClose={() => setOpen(null)}>
          <ErrorDetail item={open} canEdit={can("edit")} onDone={() => { setOpen(null); reload(); }} />
        </Drawer>
      )}
    </>
  );
}
