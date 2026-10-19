import { Link } from "react-router-dom";
import { Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { fmtDateTime } from "../../lib/format";
import { BucketBadge, NoProjects, SevBadge, sourceLabel, useProject } from "./shared";

/** Ignored problems: who ignored what and when, with one-click restore. */
export default function IgnoredView() {
  const { pid, projects, picker } = useProject();
  const can = useCan();
  const { run } = useAction();
  const list = useApi<{ total: number; items: any[] }>(pid ? `/errorcenter/ignored?project_id=${pid}` : null, [pid]);

  if (projects && !projects.length) return <><PageHead title="Ignored problems" /><NoProjects /></>;
  return (
    <>
      <PageHead title="Ignored problems" sub="Problems you chose to skip — with the reason, who ignored them and when. Bring any back with one click.">
        {picker}
        <Link className="btn" to="/error-center">Back to inbox</Link>
      </PageHead>
      <Panel pad={false}>
        {list.error ? <div style={{ padding: 16 }}><ErrorBox error={list.error} /></div>
          : list.loading && !list.data ? <Loading />
          : !list.data?.items.length ? <div style={{ padding: 16 }}><Empty title="Nothing ignored">Problems you ignore land here instead of the inbox.</Empty></div>
          : <table className="tbl"><thead><tr><th></th><th>Problem</th><th>Tool</th><th>Team</th><th>Why ignored</th><th>By</th><th>When</th><th></th></tr></thead>
            <tbody>{list.data.items.map((it: any) => (
              <tr key={it.id}>
                <td><SevBadge s={it.severity} /></td>
                <td><b>{it.title}</b></td>
                <td>{sourceLabel(it.source)}</td>
                <td><BucketBadge b={it.bucket} /></td>
                <td>{it.ignore_reason || <span className="faint">—</span>}</td>
                <td>{it.ignored_by_name || <span className="faint">—</span>}</td>
                <td className="small faint">{it.ignored_at ? fmtDateTime(it.ignored_at) : "—"}</td>
                <td>{can("edit") && <button className="btn sm" onClick={() => run(() => api(`/errorcenter/${it.id}/restore`, { method: "POST" }), "Back in the inbox").then((r) => r && list.reload())}>Restore</button>}</td>
              </tr>))}
            </tbody></table>}
      </Panel>
    </>
  );
}
