import { Link, useParams } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { ago, scoreColor } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

const BUCKETS = ["Now (week 1-2)", "Next (week 3-6)", "Later"];

export function ActionPlans() {
  const { data, error, loading } = useApi<any[]>("/action-plans");
  return (
    <>
      <PageHead title="SEO action plans" sub="Generated automatically from each completed audit: issues ranked by priority and grouped into when to tackle them." />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : (
        <Panel pad={false}>
          {!data?.length ? <Empty title="No action plans yet" action={<Link className="btn primary" to="/audits?new=1">Run an audit</Link>}>Each audit produces a plan you can turn into tasks.</Empty> : (
            <table className="t"><thead><tr><th>Site</th><th className="r">Score</th>{BUCKETS.map((b) => <th key={b} className="r">{b}</th>)}<th>Tasks</th><th>Created</th></tr></thead>
              <tbody>{data.map((p) => (
                <tr key={p.id}><td><Link to={`/action-plans/${p.id}`}>{p.domain}</Link></td><td className="r num" style={{ color: scoreColor(p.score) }}>{p.score ?? "—"}</td>
                  {BUCKETS.map((b) => <td key={b} className="r num">{p.buckets[b]}</td>)}<td>{p.tasks_created ? <Badge kind="pass">Created</Badge> : <span className="faint">Not yet</span>}</td><td className="muted">{ago(p.created_at)}</td></tr>
              ))}</tbody></table>
          )}
        </Panel>
      )}
    </>
  );
}

export function ActionPlan() {
  const { id } = useParams();
  const { data: p, error, reload } = useApi<any>(`/action-plans/${id}`);
  const { run, busy } = useAction();
  const can = useCan();
  if (error) return <ErrorBox error={error} />;
  if (!p) return <Loading />;
  return (
    <>
      <PageHead title={`Action plan: ${p.domain}`} sub={<>From the audit scoring <b style={{ color: scoreColor(p.score) }}>{p.score}</b> · <Link to={`/audits/${p.audit_run_id}`}>view audit</Link></>}>
        {can("create") && !p.tasks_created && <button className="btn primary" disabled={busy || !p.items.length} onClick={() => run(() => api(`/action-plans/${id}/tasks`, { body: {} }), `${p.items.length} tasks created`).then(reload)}>Create {p.items.length} tasks</button>}
        {p.tasks_created && <Link className="btn" to={`/tasks?project=${p.project_id}`}>Open tasks</Link>}
      </PageHead>
      <div className="grid g3">
        {BUCKETS.map((b) => {
          const items = p.items.filter((i: any) => i.bucket === b);
          return (
            <Panel key={b} title={<>{b} <span className="faint num">{items.length}</span></>} pad={false}>
              {!items.length ? <p className="panel-body muted">Nothing here.</p> : items.map((i: any) => (
                <div key={i.issue_id} style={{ padding: "10px 16px", borderBottom: "1px solid var(--line-2)" }}>
                  <div className="row" style={{ justifyContent: "space-between" }}><Badge kind={i.severity} /><span className="faint small">#{i.rank} · {i.owner}</span></div>
                  <div style={{ fontWeight: 500, margin: "6px 0 2px" }}>{i.title}</div>
                  <div className="faint small">{i.code} · {i.affected} affected</div>
                  <details style={{ marginTop: 6 }}><summary className="small" style={{ cursor: "pointer", color: "var(--accent)" }}>How to fix</summary><p className="small" style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{i.recommendation}</p></details>
                </div>
              ))}
            </Panel>
          );
        })}
      </div>
    </>
  );
}
