import { Fragment, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan, useMe, useToast } from "../../lib/hooks";
import {
  CATEGORIES, Delta, filterText, fmtN, IssueDrawer, IssueFilterBar, issueQuery, IssueTable, KpiTile, PROVIDER_LABEL, SevBar, SEVS,
  StatusBadge, TEAM_LABEL, TrendChart, useJobWatch, useProjects, useSort,
} from "./shared";
import { ChangesTab, ContentWatchTab, GapsTab } from "./MonitorTabs";
import { SourcesTab, TeamTab } from "./MonitorSettings";

const TABS = [
  ["overview", "Overview"], ["queries", "Queries"], ["pages", "Landing pages"], ["issues", "Issues"], ["changes", "Changes"],
  ["watch", "Content watch"], ["gaps", "Gaps"], ["sources", "Sources"], ["team", "Team & routing"],
] as const;
const PERIODS = [[7, "7 days"], [28, "28 days"], [90, "90 days"]] as const;

export default function ProjectMonitor() {
  const { id = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const { me } = useMe();
  const can = useCan();
  const isClient = me?.user.role === "client";
  const { data: projects, error: pErr } = useProjects();
  const project = projects?.find((p) => p.id === id);
  const tab = sp.get("tab") || "overview";
  const days = Number(sp.get("days") || 7);
  const [ver, setVer] = useState(0);
  const [running, setRunning] = useState(false);
  const { run } = useAction();
  const toast = useToast();
  const watch = useJobWatch();
  const tabs = TABS.filter(([k]) => !(isClient && k === "team"));
  const setParam = (k: string, v: string | null) => { const n = new URLSearchParams(sp); if (v == null) n.delete(k); else n.set(k, v); setSp(n, { replace: k !== "tab" }); };

  const runNow = async () => {
    const r = await run<any>(() => api(`/projects/${id}/monitor/run`, { method: "POST" }), "Monitoring run queued");
    if (!r) return;
    setRunning(true);
    watch(r.job_id, (j) => { setRunning(false); setVer((v) => v + 1); if (j?.status === "COMPLETED") toast("Monitoring run finished — dashboards updated"); }, "Monitoring run");
  };

  if (pErr) return <ErrorBox error={pErr} />;
  if (projects && !project) return <Panel><Empty title="Project not found" action={<Link className="btn" to="/projects">All projects</Link>}>It may have been removed, or you don’t have access to it.</Empty></Panel>;

  return (
    <>
      <PageHead title={<>Monitoring{project ? <span className="muted" style={{ fontWeight: 400 }}> · {project.name}</span> : null}</>}
        sub="Search Console, GA4, crawl and link data for this project: what changed, why, who is fixing it and whether the fix worked.">
        <select className="input" style={{ width: "auto", minWidth: 180 }} value={id} aria-label="Project"
          onChange={(e) => nav(`/projects/${e.target.value}/monitor${tab !== "overview" ? `?tab=${tab}` : ""}`)}>
          {(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <Link className="btn" to={`/projects/${id}/workstreams`}>Workstreams</Link>
        {can("edit") && <button className="btn primary" onClick={runNow} disabled={running}>{running ? "Running…" : "Run monitor now"}</button>}
      </PageHead>

      <div className="mon-sticky-tabs">
        <div className="tabs" role="tablist" aria-label="Monitoring sections">
          {tabs.map(([k, l]) => (
            <button key={k} role="tab" id={`tab-${k}`} aria-selected={tab === k} aria-controls="mon-panel" className={tab === k ? "on" : ""}
              onClick={() => { const n = new URLSearchParams(); n.set("tab", k); if (sp.get("days")) n.set("days", sp.get("days")!); setSp(n); }}>{l}</button>
          ))}
        </div>
      </div>

      <div id="mon-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {["overview", "queries", "pages"].includes(tab) && (
          <div className="row" style={{ marginBottom: 14, justifyContent: "space-between" }}>
            <div className="seg" role="group" aria-label="Comparison period">
              {PERIODS.map(([d, l]) => <button key={d} className={days === d ? "on" : ""} aria-pressed={days === d} onClick={() => setParam("days", String(d))}>{l}</button>)}
            </div>
            <span className="faint small">Each period is compared with the one before it.</span>
          </div>
        )}
        {tab === "overview" && <Overview pid={id} days={days} ver={ver} goTab={(t, extra) => { const n = new URLSearchParams({ tab: t, ...(extra || {}) }); setSp(n); }} />}
        {tab === "queries" && <Queries pid={id} days={days} ver={ver} />}
        {tab === "pages" && <Pages pid={id} days={days} ver={ver} />}
        {tab === "issues" && <ProjectIssues pid={id} ver={ver} initial={{ team: sp.get("team") || "", severity: sp.get("severity") || "" }} />}
        {tab === "changes" && <ChangesTab pid={id} ver={ver} />}
        {tab === "watch" && <ContentWatchTab pid={id} ver={ver} />}
        {tab === "gaps" && <GapsTab pid={id} ver={ver} openId={sp.get("gap")} setOpenId={(g) => setParam("gap", g)} />}
        {tab === "sources" && <SourcesTab pid={id} project={project} onImported={() => setVer((v) => v + 1)} />}
        {tab === "team" && !isClient && <TeamTab pid={id} />}
      </div>
    </>
  );
}

// ------------------------------------------------------------------ overview
function Overview({ pid, days, ver, goTab }: { pid: string; days: number; ver: number; goTab: (t: string, extra?: Record<string, string>) => void }) {
  const { data: d, error, loading } = useApi<any>(`/projects/${pid}/monitor/overview?days=${days}`, [ver]);
  if (loading && !d) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!d) return null;
  const s = d.search, a = d.analytics;
  const noData = !s && !a;
  const issuesBySev: Record<string, number> = {};
  const byTeam: Record<string, Record<string, number>> = {};
  for (const r of d.open_issues || []) {
    issuesBySev[r.severity] = (issuesBySev[r.severity] || 0) + r.count;
    (byTeam[r.category] ||= {})[r.severity] = (byTeam[r.category]?.[r.severity] || 0) + r.count;
  }
  const totalOpen = Object.values(issuesBySev).reduce((x, y) => x + y, 0);
  const maxTeam = Math.max(1, ...Object.values(byTeam).map((c) => Object.values(c).reduce((x, y) => x + y, 0)));
  const st = d.last_run?.stats || {};
  const totalDevClicks = (d.devices || []).reduce((x: number, r: any) => x + r.clicks, 0);
  const series = (d.series || []).map((r: any) => ({ ...r, ctr: r.impressions ? +(100 * r.clicks / r.impressions).toFixed(2) : null }));

  return (
    <>
      {noData && (
        <div className="notice warn" style={{ marginBottom: 16 }}>
          <div><b>No Search Console or GA4 data for this project yet.</b> Connect the sources or upload CSV exports, then run the monitor.
            <div style={{ marginTop: 8 }}><button className="btn sm" onClick={() => goTab("sources")}>Set up sources</button></div></div>
        </div>
      )}

      <div className="mon-kpis">
        <KpiTile label="Clicks" value={s?.clicks.value} prev={s?.clicks.prev} change={s ? s.clicks.change_pct : undefined} />
        <KpiTile label="Impressions" value={s?.impressions.value} prev={s?.impressions.prev} change={s ? s.impressions.change_pct : undefined} />
        <KpiTile label="CTR" value={s?.ctr.value} prev={s?.ctr.prev} unit="%" decimals={2}
          diff={s && s.ctr.value != null && s.ctr.prev != null ? +(s.ctr.value - s.ctr.prev).toFixed(2) : undefined} />
        <KpiTile label="Avg position" value={s?.position.value} prev={s?.position.prev} decimals={1} invert
          diff={s && s.position.value != null && s.position.prev != null ? +(s.position.value - s.position.prev).toFixed(1) : undefined} />
        <KpiTile label="Sessions" value={a?.sessions.value} prev={a?.sessions.prev} change={a ? a.sessions.change_pct : undefined}
          note={!a ? <span>GA4 not imported</span> : undefined} />
        <KpiTile label="Conversions" value={a?.conversions.value} prev={a?.conversions.prev} change={a ? a.conversions.change_pct : undefined} decimals={1}
          note={!a ? <span>GA4 not imported</span> : undefined} />
      </div>
      {d.anchor && <p className="faint small" style={{ margin: "-8px 0 14px" }}>Search data up to {fmtDate(d.anchor)} — last {days} days vs the {days} days before.</p>}

      <div className="split mon-split">
        <div className="stack">
          <Panel pad={false}>
            <div className="mon-briefing">
              <div className="meta"><b style={{ color: "var(--ink)", fontSize: 13 }}>Monitoring briefing</b>
                {d.last_run ? <span>{fmtDateTime(d.last_run.at)} · {ago(d.last_run.at)}</span> : null}
                {st.new != null && <Badge>{st.new} new</Badge>}
                {st.resolved ? <Badge kind="pass">{st.resolved} resolved</Badge> : null}
                {st.reopened ? <Badge kind="critical">{st.reopened} reopened</Badge> : null}
              </div>
              {d.last_run?.summary ? <p>{d.last_run.summary}</p> : <p className="muted">The monitor hasn’t run on this project yet. It runs daily once a source is set up, or press “Run monitor now”.</p>}
            </div>
          </Panel>
          <Panel title="Clicks and impressions" actions={<span className="faint small">Last 90 days</span>}>
            <TrendChart data={series} lines={[{ key: "clicks", label: "Clicks", color: "var(--accent)" }, { key: "impressions", label: "Impressions", color: "var(--faint)", axis: "right" }]} />
          </Panel>
          <div className="grid g2">
            <Panel title="Average position"><TrendChart height={170} data={series} lines={[{ key: "position", label: "Position", color: "var(--high)", reversed: true }]} /></Panel>
            <Panel title="CTR %"><TrendChart height={170} data={series} lines={[{ key: "ctr", label: "CTR %", color: "var(--pass)" }]} /></Panel>
          </div>
          {d.analytics_series?.length > 0 && (
            <Panel title="Sessions and conversions (GA4)">
              <TrendChart data={d.analytics_series} lines={[{ key: "sessions", label: "Sessions", color: "var(--accent)" }, { key: "conversions", label: "Conversions", color: "var(--pass)", axis: "right" }]} />
            </Panel>
          )}
        </div>

        <div className="stack">
          <Panel title="Open issues" actions={<button className="btn sm" onClick={() => goTab("issues")}>View all</button>}>
            {totalOpen ? (
              <>
                <div className="sev-legend" style={{ marginBottom: 12 }}>
                  {SEVS.map((sv) => (
                    <button key={sv} className="btn ghost" style={{ height: "auto", padding: 0, display: "block", textAlign: "left" }} onClick={() => goTab("issues", { severity: sv })}>
                      <b className="num" style={{ color: `var(--${sv})`, fontSize: 20, display: "block" }}>{issuesBySev[sv] || 0}</b><span className="muted small">{human(sv)}</span>
                    </button>
                  ))}
                </div>
                <div className="small muted" style={{ marginBottom: 4 }}>By team</div>
                {CATEGORIES.filter((c) => byTeam[c]).map((c) => (
                  <button key={c} className="btn ghost" style={{ display: "block", width: "100%", height: "auto", padding: 0, fontWeight: 400, textAlign: "left" }} onClick={() => goTab("issues", { team: c })}>
                    <SevBar label={TEAM_LABEL[c]} counts={byTeam[c]} total={maxTeam} />
                  </button>
                ))}
              </>
            ) : <p className="muted">No open issues. {d.last_run ? "Everything the monitor checks is within thresholds." : ""}</p>}
          </Panel>

          <Panel title="Devices" pad={false}>
            {d.devices?.length ? (
              <div className="table-wrap"><table className="t"><thead><tr><th>Device</th><th className="r">Clicks</th><th className="r">Change</th><th className="r">Share</th><th className="r">Pos.</th></tr></thead>
                <tbody>{d.devices.map((r: any) => (
                  <tr key={r.device}><td>{human(r.device)}</td><td className="r num">{fmtN(r.clicks)}</td><td className="r"><Delta pct={r.change_pct} /></td>
                    <td className="r num">{totalDevClicks ? Math.round((100 * r.clicks) / totalDevClicks) : 0}%</td><td className="r num">{fmtN(r.position, 1)}</td></tr>
                ))}</tbody></table></div>
            ) : <p className="muted small" style={{ padding: 16 }}>No device breakdown in the imported data.</p>}
          </Panel>

          {d.countries?.length > 0 && (
            <Panel title="Countries" pad={false}>
              <div className="table-wrap"><table className="t"><thead><tr><th>Country</th><th className="r">Clicks</th><th className="r">Change</th></tr></thead>
                <tbody>{d.countries.slice(0, 8).map((r: any) => <tr key={r.country}><td>{r.country.toUpperCase()}</td><td className="r num">{fmtN(r.clicks)}</td><td className="r"><Delta pct={r.change_pct} /></td></tr>)}</tbody></table></div>
            </Panel>
          )}

          {d.channels?.length > 0 && (
            <Panel title="GA4 channels" pad={false}>
              <div className="table-wrap"><table className="t"><thead><tr><th>Channel</th><th className="r">Sessions</th><th className="r">Conv.</th></tr></thead>
                <tbody>{d.channels.map((r: any) => <tr key={r.channel}><td>{r.channel}</td><td className="r num">{fmtN(r.sessions)}</td><td className="r num">{fmtN(r.conversions, 1)}</td></tr>)}</tbody></table></div>
            </Panel>
          )}

          <Panel title="Data & activity">
            <dl className="kv" style={{ gridTemplateColumns: "130px 1fr" }}>
              {(d.sources || []).map((x: any) => (
                <Fragment key={x.provider}><dt>{PROVIDER_LABEL[x.provider] || x.provider}</dt>
                  <dd><StatusBadge s={x.status || "not synced"} /> <span className="faint small">{human(x.mode)} · {ago(x.last_sync_at)}</span></dd></Fragment>
              ))}
              {!d.sources?.length && <><dt>Sources</dt><dd><button className="btn sm" onClick={() => goTab("sources")}>Set up</button></dd></>}
              <dt>Site changes</dt><dd><button className="btn ghost sm" style={{ padding: 0 }} onClick={() => goTab("changes")}>{d.changes_7d} in the last 7 days</button></dd>
              <dt>Content watch</dt><dd>{Object.keys(d.content_watch || {}).length ? Object.entries(d.content_watch).map(([k, v]: any) => <span key={k} style={{ marginRight: 6 }}><StatusBadge s={k} /> <span className="num">{v}</span></span>) : <span className="muted">Nothing watched</span>}</dd>
            </dl>
          </Panel>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ queries
const QSTATUS = [["", "All"], ["new", "New"], ["lost", "Lost"], ["up", "Up"], ["down", "Down"], ["top3", "Top 3"], ["top10", "Top 10"], ["striking", "Striking distance (8–20)"]] as const;

export function QueriesTable({ rows, empty }: { rows: any[]; empty?: string }) {
  const { sorted, th } = useSort(rows, "impressions");
  if (!rows.length) return <Empty title={empty || "No queries match"} />;
  return (
    <div className="table-wrap"><table className="t">
      <thead><tr>{th("query", "Query", { def: "asc" })}{th("status", "Status")}{th("clicks", "Clicks", { r: true })}{th("clicks_change", "Δ clicks", { r: true })}
        {th("impressions", "Impr.", { r: true })}{th("ctr", "CTR", { r: true })}{th("position", "Position", { r: true, def: "asc" })}{th("position_change", "Δ pos.", { r: true })}{th("intent", "Intent", { def: "asc" })}</tr></thead>
      <tbody>{sorted.map((r) => (
        <tr key={r.query}>
          <td style={{ minWidth: 200 }}>{r.query}</td>
          <td>{r.status ? <Badge kind={r.status === "new" ? "pass" : "critical"}>{human(r.status)}</Badge>
            : r.position_change >= 1 ? <Badge kind="pass">Up</Badge> : r.position_change <= -1 ? <Badge kind="high">Down</Badge> : <span className="faint small">—</span>}</td>
          <td className="r num">{fmtN(r.clicks)}</td>
          <td className="r"><Delta diff={r.clicks_change} unit="" /></td>
          <td className="r num">{fmtN(r.impressions)}</td>
          <td className="r num">{r.ctr == null ? "—" : `${fmtN(r.ctr, 2)}%`}</td>
          <td className="r num" style={{ fontWeight: 600 }}>{fmtN(r.position, 1)}</td>
          <td className="r"><Delta diff={r.position_change} unit="" title="Positions gained (+) or lost (−)" /></td>
          <td className="small muted">{human(r.intent)}</td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

function Queries({ pid, days, ver }: { pid: string; days: number; ver: number }) {
  const { data, error, loading } = useApi<any>(`/projects/${pid}/monitor/queries?days=${days}`, [ver]);
  const [q, setQ] = useState("");
  const [st, setSt] = useState("");
  const rows = useMemo(() => (data?.rows || []).filter((r: any) => {
    if (q && !r.query.includes(q.toLowerCase())) return false;
    switch (st) {
      case "new": case "lost": return r.status === st;
      case "up": return (r.position_change || 0) >= 1;
      case "down": return (r.position_change || 0) <= -1;
      case "top3": return r.position && r.position <= 3;
      case "top10": return r.position && r.position <= 10;
      case "striking": return r.position && r.position >= 8 && r.position <= 20;
      default: return true;
    }
  }), [data, q, st]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data?.anchor) return <Panel><Empty title="No query data yet">Import a Search Console export with a Query column, or connect Search Console in Sources.</Empty></Panel>;
  const sm = data.summary || {};
  return (
    <Panel title={`Queries · ${fmtN(sm.total)}`} pad={false}
      actions={<input className="input" type="search" style={{ width: 220 }} placeholder="Filter queries" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter queries" />}>
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--line-2)" }}>
        <div className="mon-chips" role="group" aria-label="Query status">
          {QSTATUS.map(([k, l]) => (
            <button key={k} className={`mon-chip ${st === k ? "on" : ""}`} aria-pressed={st === k} onClick={() => setSt(k)}>
              {l}<span className="num">{k ? sm[k] ?? 0 : sm.total}</span>
            </button>
          ))}
        </div>
      </div>
      <QueriesTable rows={rows} />
      {data.rows.length >= 500 && <p className="faint small" style={{ padding: "8px 16px" }}>Showing the top 500 queries by impressions.</p>}
    </Panel>
  );
}

// ------------------------------------------------------------------ pages
function Pages({ pid, days, ver }: { pid: string; days: number; ver: number }) {
  const { data, error, loading } = useApi<any>(`/projects/${pid}/monitor/pages?days=${days}`, [ver]);
  const [q, setQ] = useState("");
  const rows = useMemo(() => (data?.rows || []).filter((r: any) => !q || r.url.toLowerCase().includes(q.toLowerCase())), [data, q]);
  const { sorted, th } = useSort(rows, "clicks");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data?.anchor) return <Panel><Empty title="No landing-page data yet">Import a Search Console export with a Page column, or connect Search Console in Sources.</Empty></Panel>;
  return (
    <Panel title={`Landing pages · ${fmtN(data.rows.length)}`} pad={false}
      actions={<input className="input" type="search" style={{ width: 220 }} placeholder="Filter URLs" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter URLs" />}>
      {!sorted.length ? <Empty title="No pages match" /> : (
        <div className="table-wrap"><table className="t">
          <thead><tr>{th("path", "Page", { def: "asc" })}{th("clicks", "Clicks", { r: true })}{th("clicks_change_pct", "Change", { r: true })}{th("impressions", "Impr.", { r: true })}
            {th("ctr", "CTR", { r: true })}{th("position", "Pos.", { r: true, def: "asc" })}{th("queries", "Queries", { r: true })}{th("sessions", "Sessions", { r: true })}
            {th("sessions_change_pct", "Δ sessions", { r: true })}{th("conversions", "Conv.", { r: true })}{th("open_issues", "Issues", { r: true })}</tr></thead>
          <tbody>{sorted.map((r: any) => (
            <tr key={r.url}>
              <td style={{ minWidth: 220 }}><Link className="mono-url" to={`/projects/${pid}/monitor/page?url=${encodeURIComponent(r.url)}`}>{r.path || r.url}</Link></td>
              <td className="r num" style={{ fontWeight: 600 }}>{fmtN(r.clicks)}</td>
              <td className="r"><Delta pct={r.clicks_change_pct} /></td>
              <td className="r num">{fmtN(r.impressions)}</td>
              <td className="r num">{r.ctr == null ? "—" : `${fmtN(r.ctr, 2)}%`}</td>
              <td className="r num">{fmtN(r.position, 1)}</td>
              <td className="r num">{fmtN(r.queries)}</td>
              <td className="r num">{fmtN(r.sessions)}</td>
              <td className="r"><Delta pct={r.sessions_change_pct} /></td>
              <td className="r num">{fmtN(r.conversions, 1)}</td>
              <td className="r">{r.open_issues ? <Badge kind="high">{r.open_issues}</Badge> : <span className="faint">0</span>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ issues (project)
function ProjectIssues({ pid, ver, initial }: { pid: string; ver: number; initial: Record<string, string> }) {
  const [f, setF] = useState<Record<string, string>>({ status: "active", ...Object.fromEntries(Object.entries(initial).filter(([, v]) => v)) });
  const [open, setOpen] = useState<string | null>(null);
  const { data, error, loading, reload } = useApi<any[]>(issueQuery(f, pid), [ver]);
  const rows = filterText(data || [], f.q);
  return (
    <>
      <Panel pad={false} title={`Issues · ${rows.length}`} actions={<Link to={`/issues?project=${pid}`} className="small">Open in the issues inbox</Link>}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--line-2)" }}><IssueFilterBar f={f} setF={setF} /></div>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !rows.length ? (
          <Empty title={f.status === "active" && !f.severity && !f.team && !f.q ? "No active issues" : "No issues match these filters"}>
            Issues are raised by monitoring runs from Search Console, GA4, crawls and link data, routed to the right team and verified after the fix.
          </Empty>
        ) : <IssueTable issues={rows} onOpen={setOpen} />}
      </Panel>
      {open && <IssueDrawer id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  );
}

