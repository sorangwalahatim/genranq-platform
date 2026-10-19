import { Link, Navigate, useParams } from "react-router-dom";
import { Badge, ErrorBox, Loading, PageHead, Panel } from "../components/ui";
import { useApi } from "../lib/hooks";

/** Modules scheduled for later roadmap phases: shows exactly what is specified and what is already in place,
 * without pretending the feature works. */
export default function ModulePage() {
  const { num } = useParams();
  const { data, error } = useApi<any>("/spec/modules");
  const { data: integ } = useApi<any[]>("/integrations");
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const m = data.modules.find((x: any) => String(x.num) === num);
  if (!m) return <div className="empty"><h3>Unknown module</h3></div>;
  if (m.route) return <Navigate to={m.route} replace />;
  const phase = data.phases.find((p: any) => p.phase === m.phase);
  const status = (name: string) => integ?.find((i) => i.provider === name)?.status || "NOT_CONNECTED";
  return (
    <>
      <PageHead title={m.name} sub={m.description}><Badge kind="planned">Scheduled for {phase?.name || `phase ${m.phase}`}</Badge></PageHead>
      <div className="notice nc" style={{ marginBottom: 16 }}>
        <div>This module isn’t available yet. Its data model is already in the database, and it ships with roadmap phase {m.phase} ({phase?.focus}, {phase?.weeks} weeks, after {phase?.depends_on}). The specification for it is below.</div>
      </div>
      <div className="grid g2">
        <Panel title="Specification">
          <dl className="kv"><dt>Area</dt><dd>{m.area}</dd><dt>Priority</dt><dd>{m.priority}</dd><dt>Complexity</dt><dd>{m.complexity}</dd><dt>Phase</dt><dd>{m.phase}</dd><dt>Permission</dt><dd>{m.permission}</dd></dl>
        </Panel>
        <Panel title="Backlog features">
          {!m.features.length ? <p className="muted">No backlog feature references this module directly.</p> : m.features.map((f: any) => (
            <div key={f.id} style={{ padding: "6px 0", borderBottom: "1px solid var(--line-2)" }}><b style={{ fontWeight: 500 }}>{f.id} {f.name}</b> <Badge>{f.moscow}</Badge> <span className="faint small">{f.points} pts</span><div className="muted small">{f.acceptance}</div></div>
          ))}
        </Panel>
        <Panel title="Database tables (created)">
          {m.tables.length ? <div className="row">{m.tables.map((t: string) => <Badge key={t} kind="pass">{t}</Badge>)}</div> : <p className="muted">None specified.</p>}
        </Panel>
        <Panel title="Integrations">
          {m.integrations.length ? m.integrations.map((i: string) => <div key={i} className="row" style={{ padding: "4px 0" }}><span style={{ flex: 1 }}>{i}</span><Badge kind={status(i)}>{status(i) === "CONNECTED" ? "Connected" : "Not connected"}</Badge></div>) : <p className="muted">None required.</p>}
          {m.integrations.length > 0 && <Link to="/integrations" className="btn sm" style={{ marginTop: 8 }}>Manage integrations</Link>}
        </Panel>
      </div>
    </>
  );
}
