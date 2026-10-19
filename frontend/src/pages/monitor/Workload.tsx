import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { fmtDate, human } from "../../lib/format";
import { useApi } from "../../lib/hooks";
import { Initials, KpiTile, useProjects, fmtDay } from "./shared";

const WS_LABEL: Record<string, string> = {
  strategy: "Strategy", on_page: "On-page", technical: "Technical", off_page: "Off-page", content: "Content", aio: "AIO", aeo: "AEO", geo: "GEO", sxo: "SXO",
  local: "Local", analytics: "Analytics", social: "Social", paid_social: "Paid social",
};
const KIND_LABEL: Record<string, string> = { person: "People", team: "Teams", ai: "AI agents" };

type Row = { project: string; workstream: string; activity: string; who: string; assignee_type: string; cadence: string; next_due: string | null };

/** Split the API's "who" label into individual assignees with a kind. */
function assignees(r: Row): { name: string; kind: "person" | "team" | "ai" }[] {
  if (r.who.startsWith("AI: ")) return [{ name: r.who.slice(4), kind: "ai" }];
  if (r.who.startsWith("Team: ")) return [{ name: `${human(r.who.slice(6))} team`, kind: "team" }];
  return r.who.split(", ").map((n) => ({ name: n, kind: "person" as const }));
}

export default function Workload() {
  const { data, error, loading } = useApi<Row[]>("/workload");
  const { data: projects } = useProjects();
  const [kind, setKind] = useState("");
  const [project, setProject] = useState("");
  const [q, setQ] = useState("");
  const [view, setView] = useState<"who" | "project">("who");
  const today = new Date().toISOString().slice(0, 10);
  const rows = useMemo(() => (data || []).filter((r) => (!project || r.project === project) && (!q || `${r.activity} ${r.who} ${r.project}`.toLowerCase().includes(q.toLowerCase()))), [data, project, q]);
  const byWho = useMemo(() => {
    const m: Record<string, { name: string; kind: string; items: Row[] }> = {};
    for (const r of rows) for (const a of assignees(r)) {
      if (kind && a.kind !== kind) continue;
      (m[a.kind + a.name] ||= { name: a.name, kind: a.kind, items: [] }).items.push(r);
    }
    return Object.values(m).sort((a, b) => b.items.length - a.items.length);
  }, [rows, kind]);
  const byProject = useMemo(() => {
    const m: Record<string, Row[]> = {};
    for (const r of rows) if (!kind || assignees(r).some((a) => a.kind === kind)) (m[r.project || "—"] ||= []).push(r);
    return Object.entries(m).sort((a, b) => a[0].localeCompare(b[0]));
  }, [rows, kind]);
  const counts = useMemo(() => {
    const c = { person: new Set<string>(), team: new Set<string>(), ai: new Set<string>() };
    for (const r of data || []) for (const a of assignees(r)) c[a.kind].add(a.name);
    return c;
  }, [data]);
  const due = (data || []).filter((r) => r.next_due && r.next_due <= today).length;
  const pid = (name: string) => projects?.find((p) => p.name === name)?.id;

  return (
    <>
      <PageHead title="Workload" sub="Who does what across projects — people, teams and AI agents — from every enabled workstream activity." />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? (
        <Panel><Empty title="No activities yet" action={projects?.[0] && <Link className="btn primary" to={`/projects/${projects[0].id}/workstreams`}>Set up workstreams</Link>}>
          Apply a strategy to a project to create recurring activities and assign them.</Empty></Panel>
      ) : (
        <>
          <div className="grid g4" style={{ marginBottom: 16 }}>
            <KpiTile label="Active activities" value={data.length} note={<span>{due} due today or overdue</span>} />
            <KpiTile label="People" value={counts.person.size} />
            <KpiTile label="Teams" value={counts.team.size} />
            <KpiTile label="AI agents" value={counts.ai.size} />
          </div>
          <Panel pad={false} title={
            <div className="seg" role="group" aria-label="Group by">
              <button className={view === "who" ? "on" : ""} aria-pressed={view === "who"} onClick={() => setView("who")}>By assignee</button>
              <button className={view === "project" ? "on" : ""} aria-pressed={view === "project"} onClick={() => setView("project")}>By project</button>
            </div>
          } actions={
            <div className="mon-filters">
              <input className="input" type="search" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search activities" />
              <select className="input" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Assignee type">
                <option value="">Everyone</option>{Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <select className="input" value={project} onChange={(e) => setProject(e.target.value)} aria-label="Project">
                <option value="">All projects</option>{Array.from(new Set(data.map((r) => r.project))).sort().map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          }>
            {view === "who" ? (
              !byWho.length ? <Empty title="Nothing matches" /> : (
                <div className="mon-team" style={{ padding: 16 }}>
                  {byWho.map((w) => (
                    <div key={w.kind + w.name} className="box">
                      <h3><span className="row" style={{ flexWrap: "nowrap" }}><Initials name={w.name} kind={w.kind === "ai" ? "ai" : w.kind === "team" ? "team" : undefined} />{w.name}</span>
                        <span className="faint small num">{w.items.length}</span></h3>
                      {w.items.slice(0, 12).map((r, i) => (
                        <div key={i} className="small" style={{ padding: "4px 0", borderTop: "1px solid var(--line-2)" }}>
                          <div>{r.activity}</div>
                          <div className="faint">{pid(r.project) ? <Link to={`/projects/${pid(r.project)}/workstreams`}>{r.project}</Link> : r.project} · {WS_LABEL[r.workstream] || r.workstream} · {human(r.cadence)}
                            {r.next_due && <span style={{ color: r.next_due <= today ? "var(--high)" : undefined }}> · {fmtDay(r.next_due)}</span>}</div>
                        </div>
                      ))}
                      {w.items.length > 12 && <div className="faint small" style={{ paddingTop: 4 }}>+{w.items.length - 12} more</div>}
                    </div>
                  ))}
                </div>
              )
            ) : !byProject.length ? <Empty title="Nothing matches" /> : (
              <div className="table-wrap"><table className="t">
                <thead><tr><th>Project</th><th>Workstream</th><th>Activity</th><th>Who</th><th>Cadence</th><th>Next due</th></tr></thead>
                <tbody>{byProject.flatMap(([p, items]) => items.map((r, i) => (
                  <tr key={p + i}>
                    <td>{i === 0 ? (pid(p) ? <Link to={`/projects/${pid(p)}/workstreams`}><b style={{ fontWeight: 500 }}>{p}</b></Link> : p) : ""}</td>
                    <td className="small">{WS_LABEL[r.workstream] || r.workstream}</td>
                    <td>{r.activity}</td>
                    <td className="small">{r.assignee_type === "ai_agent" ? <Badge kind="medium">AI</Badge> : null} {r.who.replace(/^AI: /, "")}</td>
                    <td className="small">{human(r.cadence)}</td>
                    <td className="small nowrap" style={{ color: r.next_due && r.next_due <= today ? "var(--high)" : undefined }}>{fmtDay(r.next_due)}</td>
                  </tr>
                )))}</tbody>
              </table></div>
            )}
          </Panel>
        </>
      )}
    </>
  );
}
