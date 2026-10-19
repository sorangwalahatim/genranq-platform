import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate, human, scoreColor } from "../../lib/format";
import { useApi } from "../../lib/hooks";
import { CATEGORIES, Delta, fmtCompact, fmtN, Initials, IssueDrawer, IssueTable, KpiTile, SevBar, SevDot, SEVS, TEAM_LABEL, TrendChart, useProjects } from "./shared";

// ------------------------------------------------------------------ team dashboard
export function TeamDashboard() {
  const { team = "seo" } = useParams();
  const valid = (CATEGORIES as readonly string[]).includes(team);
  const { data: d, error, loading, reload } = useApi<any>(valid ? `/dashboards/team/${team}` : null, [team], { poll: 60000 });
  const { data: projects } = useProjects();
  const [open, setOpen] = useState<string | null>(null);
  const names = Object.fromEntries((projects || []).map((p) => [p.id, p.name]));
  const issues = (d?.issues || []).map((i: any) => ({ ...i, project_name: names[i.project_id] }));
  const sev = Object.fromEntries(SEVS.map((s) => [s, issues.filter((i: any) => i.severity === s).length]));
  const maxP = Math.max(1, ...(d?.issues_by_project || []).map((p: any) => SEVS.reduce((a, s) => a + (p[s] || 0), 0)));
  const now = Date.now();
  return (
    <>
      <PageHead title={`${TEAM_LABEL[team] || human(team)} team`} sub="Open issues routed to this team and the tasks on its plate, across every project you can see.">
        <nav className="seg" aria-label="Teams" style={{ flexWrap: "wrap", maxWidth: "100%" }}>
          {CATEGORIES.map((t) => <NavLink key={t} to={`/dashboards/team/${t}`} className={({ isActive }) => isActive ? "on" : ""}
            style={{ padding: "6px 12px", color: "inherit", textDecoration: "none" }}>{TEAM_LABEL[t]}</NavLink>)}
        </nav>
      </PageHead>
      {!valid ? <Panel><Empty title="Unknown team">Pick one of the teams above.</Empty></Panel> : (
        <>
          <ErrorBox error={error} />
          {loading && !d ? <Loading /> : d && (
            <>
              <div className="grid g4" style={{ marginBottom: 16 }}>
                <KpiTile label="Open issues" value={issues.length} note={<span><SevDot s="critical" />{sev.critical} critical · {sev.high} high</span>} />
                <KpiTile label="Projects affected" value={d.issues_by_project.length} note={<span>of {projects?.length ?? "—"} projects</span>} />
                <KpiTile label="Open tasks" value={d.tasks_open} note={<span>{d.members.length} {d.members.length === 1 ? "person" : "people"} with work</span>} />
                <KpiTile label="Overdue tasks" value={d.tasks_overdue} note={d.tasks_overdue ? <span style={{ color: "var(--critical)" }}>Needs attention</span> : <span>On track</span>} />
              </div>
              <div className="split mon-split">
                <div className="stack">
                  <Panel title="Issues" pad={false}>
                    {issues.length ? <IssueTable issues={issues} onOpen={setOpen} showProject /> : <Empty title="Nothing open for this team" />}
                  </Panel>
                  <Panel title="Tasks" pad={false}>
                    {d.tasks.length ? (
                      <div className="table-wrap"><table className="t"><thead><tr><th>Task</th><th>Project</th><th>Priority</th><th>Status</th><th>Due</th></tr></thead>
                        <tbody>{d.tasks.map((t: any) => {
                          const overdue = t.due_date && new Date(t.due_date).getTime() < now;
                          return (
                            <tr key={t.id}><td style={{ minWidth: 220 }}>{t.title}{t.assignee_type === "ai_agent" && <> <Badge kind="medium">AI</Badge></>}</td>
                              <td className="small nowrap">{t.project || "—"}</td><td><Badge kind={t.priority}>{human(t.priority)}</Badge></td>
                              <td><Badge kind={t.status === "review" ? "pending" : ""}>{human(t.status)}</Badge></td>
                              <td className="small nowrap" style={{ color: overdue ? "var(--critical)" : undefined }}>{fmtDate(t.due_date)}{overdue ? " · overdue" : ""}</td></tr>
                          );
                        })}</tbody></table></div>
                    ) : <Empty title="No open tasks" />}
                  </Panel>
                </div>
                <div className="stack">
                  <Panel title="Issues by project">
                    {d.issues_by_project.length ? d.issues_by_project.map((p: any) => (
                      <SevBar key={p.project_id} label={p.project || "Project"} counts={p} total={maxP} to={`/projects/${p.project_id}/monitor?tab=issues&team=${team}`} />
                    )) : <p className="muted small">No open issues.</p>}
                  </Panel>
                  <Panel title="People" pad={false}>
                    {d.members.length ? (
                      <div className="table-wrap"><table className="t"><thead><tr><th>Person</th><th className="r">Open</th><th className="r">Overdue</th><th className="r">Critical</th></tr></thead>
                        <tbody>{[...d.members].sort((a: any, b: any) => b.open - a.open).map((m: any) => (
                          <tr key={m.user_id}><td><span className="row" style={{ flexWrap: "nowrap" }}><Initials name={m.name} />{m.name}</span></td>
                            <td className="r num">{m.open}</td><td className="r num" style={{ color: m.overdue ? "var(--critical)" : undefined }}>{m.overdue}</td><td className="r num">{m.critical}</td></tr>
                        ))}</tbody></table></div>
                    ) : <p className="muted small" style={{ padding: 16 }}>Nobody on this team has open tasks. Add people to project teams under Monitoring → Team &amp; routing.</p>}
                  </Panel>
                </div>
              </div>
            </>
          )}
        </>
      )}
      {open && <IssueDrawer id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  );
}

// ------------------------------------------------------------------ executive dashboard
export function ExecutiveDashboard() {
  const { data: d, error, loading } = useApi<any>("/dashboards/executive", [], { poll: 120000 });
  const nav = useNavigate();
  const [series, setSeries] = useState<any[] | null>(null);
  const withSearch = useMemo(() => (d?.projects || []).filter((p: any) => p.search), [d]);

  // Organic clicks trend summed across projects (from each project's overview series).
  useEffect(() => {
    if (!d) return;
    let live = true;
    const ps = withSearch.slice(0, 15);
    if (!ps.length) { setSeries([]); return; }
    Promise.all(ps.map((p: any) => api<any>(`/projects/${p.project_id}/monitor/overview?days=28`).catch(() => null))).then((rs) => {
      if (!live) return;
      const by: Record<string, any> = {};
      rs.forEach((r, i) => (r?.series || []).forEach((x: any) => {
        const row = (by[x.date] ||= { date: x.date, total: 0 });
        row.total += x.clicks; row[ps[i].project] = (row[ps[i].project] || 0) + x.clicks;
      }));
      setSeries(Object.values(by).sort((a: any, b: any) => a.date.localeCompare(b.date)));
    });
    return () => { live = false; };
  }, [d, withSearch]);

  if (loading && !d) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!d) return null;
  const t = d.totals;
  const ch = t.clicks_prev ? +(100 * (t.clicks - t.clicks_prev) / t.clicks_prev).toFixed(1) : null;
  const movers = withSearch.map((p: any) => ({ ...p, ch: p.search.clicks.change_pct, diff: p.search.clicks.value - p.search.clicks.prev }))
    .filter((p: any) => p.ch != null).sort((a: any, b: any) => a.ch - b.ch);
  const sevTotals = Object.fromEntries(SEVS.map((s) => [s, d.projects.reduce((a: number, p: any) => a + (p.issues[s] || 0), 0)]));
  const maxIss = Math.max(1, ...d.projects.map((p: any) => SEVS.reduce((a, s) => a + (p.issues[s] || 0), 0)));
  const bars = withSearch.slice(0, 12).map((p: any) => ({ name: p.project, Current: p.search.clicks.value, Previous: p.search.clicks.prev }));

  return (
    <>
      <PageHead title="Executive overview" sub="Health, traffic and open risk across all projects — last 28 days compared with the 28 days before." />
      {!d.projects.length ? <Panel><Empty title="No projects yet" action={<Link className="btn primary" to="/projects">Create a project</Link>} /></Panel> : (
        <>
          <div className="grid g4" style={{ marginBottom: 16 }}>
            <KpiTile label="Organic clicks (28 days)" value={t.clicks} prev={t.clicks_prev} change={ch} />
            <KpiTile label="Critical issues" value={t.critical} note={<span>{sevTotals.medium + sevTotals.low} medium / low</span>} />
            <KpiTile label="High-priority issues" value={t.high} note={<Link to="/issues?severity=high">Open inbox</Link>} />
            <KpiTile label="Overdue tasks" value={t.tasks_overdue} note={<Link to="/tasks">All tasks</Link>} />
          </div>
          <div className="split mon-split">
            <div className="stack">
              <Panel title="Organic clicks — all projects" actions={<span className="faint small">Last 90 days</span>}>
                {series == null ? <Loading /> : <TrendChart data={series} lines={[{ key: "total", label: "Clicks", color: "var(--accent)" }]} />}
              </Panel>
              <Panel title="Project health" pad={false}>
                <div className="table-wrap"><table className="t">
                  <thead><tr><th className="r">Health</th><th>Project</th><th className="r">Clicks</th><th className="r">Change</th><th>Open issues</th><th className="r">Tasks</th><th>Latest briefing</th></tr></thead>
                  <tbody>{d.projects.map((p: any) => (
                    <tr key={p.project_id} className="click" tabIndex={0} onClick={() => nav(`/projects/${p.project_id}/monitor`)} onKeyDown={(e) => e.key === "Enter" && nav(`/projects/${p.project_id}/monitor`)}>
                      <td className="r"><span className="mon-health" style={{ color: scoreColor(p.health) }}>{p.health}</span></td>
                      <td style={{ minWidth: 150 }}><b style={{ fontWeight: 500 }}>{p.project}</b>{p.client && <div className="faint small">{p.client}</div>}</td>
                      <td className="r num">{p.search ? fmtN(p.search.clicks.value) : <span className="faint">—</span>}</td>
                      <td className="r">{p.search ? <Delta pct={p.search.clicks.change_pct} /> : <span className="faint small">No data</span>}</td>
                      <td className="nowrap">{SEVS.map((s) => p.issues[s] ? <span key={s} className="small" style={{ marginRight: 8 }}><SevDot s={s} />{p.issues[s]}</span> : null)}{!Object.keys(p.issues).length && <span className="faint small">None</span>}</td>
                      <td className="r num nowrap">{p.tasks_open}{p.tasks_overdue ? <span style={{ color: "var(--critical)" }}> · {p.tasks_overdue} late</span> : null}</td>
                      <td className="small muted" style={{ minWidth: 240, maxWidth: 420 }}>{p.briefing ? <span title={p.briefing}>{p.briefing.length > 180 ? p.briefing.slice(0, 180) + "…" : p.briefing}</span> : "Not monitored yet"}
                        {p.last_run && <div className="faint">{ago(p.last_run)}</div>}</td>
                    </tr>
                  ))}</tbody>
                </table></div>
              </Panel>
              {bars.length > 0 && (
                <Panel title="Clicks by project">
                  <div style={{ height: Math.max(180, bars.length * 34) }} role="img" aria-label="Clicks per project, current vs previous period">
                    <ResponsiveContainer>
                      <BarChart data={bars} layout="vertical" margin={{ left: 8, right: 12 }}>
                        <CartesianGrid horizontal={false} stroke="var(--line-2)" />
                        <XAxis type="number" tickLine={false} axisLine={false} fontSize={11} tickFormatter={fmtCompact} />
                        <YAxis type="category" dataKey="name" width={120} tickLine={false} axisLine={false} fontSize={12} />
                        <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12.5 }} />
                        <Legend wrapperStyle={{ fontSize: 12 }} />
                        <Bar dataKey="Previous" fill="var(--line)" radius={[0, 3, 3, 0]} isAnimationActive={false} />
                        <Bar dataKey="Current" fill="var(--accent)" radius={[0, 3, 3, 0]} isAnimationActive={false} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </Panel>
              )}
            </div>
            <div className="stack">
              <Panel title="Open issues by severity">
                <div className="sev-legend" style={{ marginBottom: 12 }}>
                  {SEVS.map((s) => <Link key={s} to={`/issues?severity=${s}`} style={{ color: "inherit" }}><b className="num" style={{ color: `var(--${s})` }}>{sevTotals[s]}</b><span className="muted small">{human(s)}</span></Link>)}
                </div>
                {d.projects.filter((p: any) => Object.keys(p.issues).length).map((p: any) => (
                  <SevBar key={p.project_id} label={p.project} counts={p.issues} total={maxIss} to={`/issues?project=${p.project_id}`} />
                ))}
              </Panel>
              <Panel title="Top movers">
                {!movers.length ? <p className="muted small">Needs Search Console data on at least one project.</p> : (
                  <>
                    <div className="small muted" style={{ marginBottom: 4 }}>Biggest gains</div>
                    {[...movers].reverse().filter((p: any) => p.ch > 0).slice(0, 4).map((p: any) => <Mover key={p.project_id} p={p} />)}
                    {!movers.some((p: any) => p.ch > 0) && <p className="faint small">No project grew.</p>}
                    <div className="small muted" style={{ margin: "10px 0 4px" }}>Biggest drops</div>
                    {movers.filter((p: any) => p.ch < 0).slice(0, 4).map((p: any) => <Mover key={p.project_id} p={p} />)}
                    {!movers.some((p: any) => p.ch < 0) && <p className="faint small">No project declined.</p>}
                  </>
                )}
              </Panel>
              <Panel title="Team dashboards">
                <div className="mon-chips">{CATEGORIES.map((c) => <Link key={c} className="btn sm" to={`/dashboards/team/${c}`}>{TEAM_LABEL[c]}</Link>)}</div>
              </Panel>
            </div>
          </div>
        </>
      )}
    </>
  );
}

function Mover({ p }: { p: any }) {
  return (
    <Link to={`/projects/${p.project_id}/monitor`} className="row" style={{ justifyContent: "space-between", padding: "5px 0", color: "inherit", borderBottom: "1px solid var(--line-2)", flexWrap: "nowrap" }}>
      <span>{p.project}</span>
      <span className="row" style={{ flexWrap: "nowrap" }}><span className="small num muted">{fmtN(p.search.clicks.prev)} → {fmtN(p.search.clicks.value)}</span><Delta pct={p.ch} /></span>
    </Link>
  );
}
