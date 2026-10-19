import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel, ScoreRing, SeverityStrip } from "../components/ui";
import { ago, human, scoreColor } from "../lib/format";
import { useApi, useMe } from "../lib/hooks";

export default function Dashboard() {
  const { me } = useMe();
  const { data, error, loading } = useApi<any>("/dashboard", [], { poll: 30000 });
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const d = data!;
  const latest = d.audits[0];
  const t = d.tasks || {};
  return (
    <>
      <PageHead title={`Good to see you, ${me?.user.name?.split(" ")[0] || ""}`} sub="Where every client site stands today and what your team should do next.">
        <Link className="btn" to="/tasks">Open tasks</Link>
        <Link className="btn primary" to="/audits?new=1">Run an audit</Link>
      </PageHead>

      <div className="split">
        <Panel pad={false}>
          {latest ? (
            <div className="score-hero">
              <ScoreRing score={latest.score} />
              <div>
                <div className="muted small">Latest audit · {ago(latest.finished_at)}</div>
                <h2 style={{ fontSize: 22, marginTop: 2 }}><Link to={`/audits/${latest.id}`} style={{ color: "inherit" }}>{latest.domain}</Link></h2>
                <SeverityStrip issues={latest.issues} />
              </div>
            </div>
          ) : (
            <Empty title="No audits yet" action={<Link className="btn primary" to="/audits?new=1">Run your first audit</Link>}>
              Add a site to a project and run an audit to get a score, prioritised issues and an action plan.
            </Empty>
          )}
        </Panel>
        <div className="grid g2">
          <Kpi v={d.projects} l="Active projects" to="/projects" />
          <Kpi v={(t.todo || 0) + (t.in_progress || 0) + (t.review || 0)} l={`Open tasks${d.overdue_tasks ? ` · ${d.overdue_tasks} overdue` : ""}`} to="/tasks" />
          <Kpi v={d.keywords} l={d.avg_position ? `Keywords · avg position ${d.avg_position}` : "Tracked keywords"} to="/rank-tracker" />
          <Kpi v={`${d.billable_hours_7d}h`} l={`Billable of ${d.hours_7d}h this week`} to="/time" />
        </div>
      </div>

      <div className="split" style={{ marginTop: 16 }}>
        <Panel title="Recent audits" actions={<Link to="/audits" className="small">All audits</Link>} pad={false}>
          {d.audits.length ? (
            <div className="table-wrap"><table className="t"><thead><tr><th>Site</th><th className="r">Score</th><th>Critical</th><th>High</th><th>Finished</th></tr></thead>
              <tbody>{d.audits.map((a: any) => (
                <tr key={a.id}><td><Link to={`/audits/${a.id}`}>{a.domain}</Link></td>
                  <td className="r num" style={{ color: scoreColor(a.score), fontWeight: 600 }}>{a.score ?? "—"}</td>
                  <td className="num">{a.issues?.critical ?? 0}</td><td className="num">{a.issues?.high ?? 0}</td><td className="muted">{ago(a.finished_at)}</td></tr>
              ))}</tbody></table></div>
          ) : <Empty title="Nothing here yet" />}
        </Panel>
        <Panel title="Activity" actions={me?.user.role !== "client" ? <Link to="/activity" className="small">Full log</Link> : null}>
          {d.activity.length ? d.activity.map((a: any) => (
            <div key={a.id} style={{ padding: "6px 0", borderBottom: "1px solid var(--line-2)" }}>
              <div><b style={{ fontWeight: 500 }}>{a.actor}</b> <span className="muted">{human(a.action.replace(".", " "))}</span></div>
              <div className="faint small">{a.target || ""} · {ago(a.created_at)}</div>
            </div>
          )) : <p className="muted">No activity yet.</p>}
          {d.pending_approvals > 0 && <div className="notice" style={{ marginTop: 12 }}><Badge kind="pending">{d.pending_approvals}</Badge> <Link to="/approvals">approvals waiting</Link></div>}
        </Panel>
      </div>
    </>
  );
}

function Kpi({ v, l, to }: { v: any; l: string; to: string }) {
  return <Link to={to} className="panel kpi" style={{ color: "inherit", textDecoration: "none" }}><div className="v">{v ?? "—"}</div><div className="l">{l}</div></Link>;
}
