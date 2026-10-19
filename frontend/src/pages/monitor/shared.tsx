import { Fragment, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Drawer, ErrorBox, Loading } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate, fmtDateTime, human, sevColor } from "../../lib/format";
import { useAction, useApi, useCan, useToast } from "../../lib/hooks";
import "./monitor.css";

// ------------------------------------------------------------------ vocab
export const TEAMS = ["seo", "developer", "content", "backlink", "analytics", "pm"] as const;
export const CATEGORIES = ["developer", "seo", "content", "backlink", "analytics"] as const;
export const TEAM_LABEL: Record<string, string> = {
  seo: "SEO", developer: "Developer", content: "Content", backlink: "Backlink", analytics: "Analytics", pm: "Project manager",
};
export const ISSUE_STATUSES = ["open", "in_progress", "verifying", "reopened", "resolved", "ignored"] as const;
export const OPEN_STATUSES = ["open", "in_progress", "verifying", "reopened"];
export const SEVS = ["critical", "high", "medium", "low"] as const;
export const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };
export const PROVIDER_LABEL: Record<string, string> = {
  gsc: "Google Search Console", ga4: "Google Analytics 4", ahrefs: "Ahrefs", semrush: "Semrush", screaming_frog: "Screaming Frog",
};

/** Badge class for an issue / run status, mapped onto the shared badge palette. */
export function statusKind(s?: string | null) {
  switch (s) {
    case "resolved": case "COMPLETED": case "performing": case "ok": return "pass";
    case "verifying": case "in_progress": case "RUNNING": case "watching": case "HANDED_TO_HUMAN": return "medium";
    case "reopened": case "FAILED": case "underperforming": case "error": return "critical";
    case "open": case "NEEDS_DATA": return "high";
    case "ignored": case "QUEUED": case "NEEDS_CONNECTION": case "not_connected": case "done": return "low";
    default: return "";
  }
}

const STATUS_LABEL: Record<string, string> = {
  COMPLETED: "Completed", FAILED: "Failed", NEEDS_CONNECTION: "Needs connection", NEEDS_DATA: "Needs data", HANDED_TO_HUMAN: "Handed to a person",
  RUNNING: "Running", QUEUED: "Queued", ok: "OK", not_connected: "Not connected", in_progress: "In progress",
};
export const StatusBadge = ({ s }: { s?: string | null }) => <Badge kind={statusKind(s)}>{(s && STATUS_LABEL[s]) || human(s)}</Badge>;
export const SevBadge = ({ s }: { s: string }) => <Badge kind={s}>{human(s)}</Badge>;

export function pathOf(u?: string | null) {
  if (!u) return "";
  try { const x = new URL(u); return (x.pathname || "/") + (x.search || ""); } catch { return u; }
}

export function fmtN(n?: number | null, d = 0) {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: 0 });
}
export function fmtCompact(n?: number | null) {
  if (n == null) return "—";
  return Intl.NumberFormat(undefined, { notation: Math.abs(n) >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(n);
}

/** Percentage change or absolute difference, coloured by whether it's good. `invert` = lower is better (position). */
export function Delta({ pct, diff, invert, unit = "%", title }: { pct?: number | null; diff?: number | null; invert?: boolean; unit?: string; title?: string }) {
  const v = pct ?? diff;
  if (v == null || Number.isNaN(v)) return <span className="mon-delta flat" title={title}>—</span>;
  const good = invert ? v < 0 : v > 0;
  const cls = Math.abs(v) < 0.05 ? "flat" : good ? "up" : "down";
  const arrow = v > 0 ? "▲" : v < 0 ? "▼" : "";
  const val = Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 1 });
  return <span className={`mon-delta ${cls}`} title={title}>{arrow} {val}{pct != null ? unit : ""}<span className="sr-only">{good ? " improvement" : cls === "flat" ? "" : " decline"}</span></span>;
}

export function KpiTile({ label, value, prev, change, diff, invert, unit = "", decimals = 0, note }: {
  label: string; value?: number | null; prev?: number | null; change?: number | null; diff?: number | null; invert?: boolean; unit?: string; decimals?: number; note?: ReactNode;
}) {
  return (
    <div className="panel mon-kpi">
      <div className="l">{label}</div>
      <div className="v">{value == null ? "—" : fmtN(value, decimals) + unit}</div>
      <div className="p">
        {change !== undefined ? <Delta pct={change} invert={invert} /> : diff !== undefined ? <Delta diff={diff} invert={invert} unit="" /> : null}
        {note ?? (prev != null ? <>vs {fmtN(prev, decimals)}{unit}</> : null)}
      </div>
    </div>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <span className="mon-switch">
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} aria-label={label} onChange={(e) => onChange(e.target.checked)} />
      <span aria-hidden />
    </span>
  );
}

export function Initials({ name, kind }: { name?: string | null; kind?: "ai" | "team" }) {
  const t = kind === "ai" ? "AI" : (name || "?").split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((x) => x[0]?.toUpperCase()).join("");
  return <span className={`mon-avatar ${kind || ""}`} aria-hidden>{t}</span>;
}

// ------------------------------------------------------------------ charts
export type LineDef = { key: string; label: string; color: string; axis?: "left" | "right"; reversed?: boolean };
export function TrendChart({ data, lines, height = 220, xKey = "date" }: { data: any[]; lines: LineDef[]; height?: number; xKey?: string }) {
  if (!data?.length) return <p className="muted small">No data for this period.</p>;
  const right = lines.find((l) => l.axis === "right");
  return (
    <div style={{ height }} role="img" aria-label={`Trend chart of ${lines.map((l) => l.label).join(", ")}`}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 6, right: right ? 4 : 12, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--line-2)" />
          <XAxis dataKey={xKey} tickLine={false} axisLine={false} fontSize={11} minTickGap={28}
            tickFormatter={(d) => { const x = new Date(d); return isNaN(+x) ? d : x.toLocaleDateString(undefined, { day: "numeric", month: "short" }); }} />
          <YAxis yAxisId="left" width={44} tickLine={false} axisLine={false} fontSize={11} tickFormatter={fmtCompact}
            reversed={!!lines.find((l) => (l.axis || "left") === "left" && l.reversed)} />
          {right && <YAxis yAxisId="right" orientation="right" width={40} tickLine={false} axisLine={false} fontSize={11} reversed={!!right.reversed} tickFormatter={fmtCompact} />}
          <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12.5 }}
            labelFormatter={(d) => fmtDate(String(d))} formatter={(v: any, n: any) => [typeof v === "number" ? fmtN(v, 2) : v, n]} />
          {lines.length > 1 && <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />}
          {lines.map((l) => (
            <Line key={l.key} yAxisId={l.axis || "left"} dataKey={l.key} name={l.label} stroke={l.color} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ------------------------------------------------------------------ jobs
/** Poll a background job until it finishes, then call onDone. */
export function useJobWatch() {
  const toast = useToast();
  const timers = useRef<number[]>([]);
  useEffect(() => () => { timers.current.forEach(clearTimeout); }, []);
  return useCallback((jobId: string | null | undefined, onDone: (job: any) => void, label = "Job") => {
    if (!jobId) { onDone(null); return; }
    let n = 0;
    const tick = async () => {
      n++;
      try {
        const j = await api<any>(`/jobs/${jobId}`);
        if (j.status === "COMPLETED") { onDone(j); return; }
        if (j.status === "FAILED") { toast(`${label} failed: ${(j.error || "").split("\n")[0]}`, true); onDone(j); return; }
      } catch { onDone(null); return; }
      if (n < 60) timers.current.push(window.setTimeout(tick, n < 10 ? 1200 : 3000));
      else onDone(null);
    };
    timers.current.push(window.setTimeout(tick, 700));
  }, [toast]);
}

// ------------------------------------------------------------------ projects
export function useProjects() {
  return useApi<any[]>("/projects");
}

// ------------------------------------------------------------------ issues
export function IssueFilterBar({ f, setF, projects, showProject, extra }: {
  f: Record<string, string>; setF: (f: Record<string, string>) => void; projects?: any[] | null; showProject?: boolean; extra?: ReactNode;
}) {
  const set = (k: string, v: string) => setF({ ...f, [k]: v });
  return (
    <div className="mon-filters">
      <input className="input" type="search" placeholder="Search title, URL, query" value={f.q || ""} onChange={(e) => set("q", e.target.value)} aria-label="Search issues" />
      {showProject && (
        <select className="input" value={f.project || ""} onChange={(e) => set("project", e.target.value)} aria-label="Project">
          <option value="">All projects</option>
          {(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      )}
      <select className="input" value={f.status ?? "active"} onChange={(e) => set("status", e.target.value)} aria-label="Status">
        <option value="active">Active (open, in progress, verifying, reopened)</option>
        <option value="">All statuses</option>
        {ISSUE_STATUSES.map((s) => <option key={s} value={s}>{human(s)}</option>)}
      </select>
      <select className="input" value={f.severity || ""} onChange={(e) => set("severity", e.target.value)} aria-label="Severity">
        <option value="">All severities</option>
        {SEVS.map((s) => <option key={s} value={s}>{human(s)}</option>)}
      </select>
      <select className="input" value={f.team || ""} onChange={(e) => set("team", e.target.value)} aria-label="Team">
        <option value="">All teams</option>
        {CATEGORIES.map((s) => <option key={s} value={s}>{TEAM_LABEL[s]}</option>)}
      </select>
      <select className="input" value={f.type || ""} onChange={(e) => set("type", e.target.value)} aria-label="Type">
        <option value="">Problems and opportunities</option>
        <option value="problems">Problems only</option>
        <option value="opportunities">Opportunities only</option>
      </select>
      {extra}
    </div>
  );
}

/** Build the /issues query string from a filter object (server-side filters). */
export function issueQuery(f: Record<string, string>, projectId?: string) {
  const qs = new URLSearchParams();
  const pid = projectId || f.project;
  if (pid) qs.set("project_id", pid);
  const st = f.status ?? "active";
  if (st) qs.set("status", st);
  if (f.severity) qs.set("severity", f.severity);
  if (f.team) qs.set("category", f.team);
  if (f.type === "problems") qs.set("opportunities", "false");
  if (f.type === "opportunities") qs.set("opportunities", "true");
  return `/issues?${qs}`;
}

export function filterText(items: any[], q?: string) {
  const s = (q || "").trim().toLowerCase();
  if (!s) return items;
  return items.filter((i) => `${i.title} ${i.url || ""} ${i.query || ""} ${i.kind}`.toLowerCase().includes(s));
}

export function IssueTable({ issues, onOpen, showProject }: { issues: any[]; onOpen: (id: string) => void; showProject?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="t">
        <thead><tr><th>Severity</th><th>Issue</th>{showProject && <th>Project</th>}<th>Teams</th><th>Status</th><th className="r">Seen</th><th>Last seen</th></tr></thead>
        <tbody>
          {issues.map((i) => (
            <tr key={i.id} className="click" tabIndex={0} onClick={() => onOpen(i.id)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen(i.id))}
              aria-label={`${i.severity} issue: ${i.title}`}>
              <td><SevBadge s={i.severity} /></td>
              <td style={{ minWidth: 260 }}>
                <div style={{ fontWeight: 500 }}>{i.title}</div>
                <div className="faint small">
                  {i.is_opportunity && <Badge kind="pass">Opportunity</Badge>}{" "}
                  {human(i.kind)}{i.url ? <> · <span className="mono-url">{pathOf(i.url)}</span></> : null}{i.query ? <> · “{i.query}”</> : null}
                </div>
              </td>
              {showProject && <td className="nowrap">{i.project_name || "—"}</td>}
              <td><div className="mon-chips">{(i.categories?.length ? i.categories : [i.category]).map((c: string) => <Badge key={c}>{TEAM_LABEL[c] || human(c)}</Badge>)}</div></td>
              <td><StatusBadge s={i.status} /></td>
              <td className="r num">{i.occurrences}×</td>
              <td className="muted small nowrap">{ago(i.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const EVENT_LABEL: Record<string, [string, string]> = {
  detected: ["Detected by monitoring", "bad"], resolved: ["Resolved — no longer detected", "good"], reopened: ["Reopened", "bad"],
  in_progress: ["Work started on a linked task", "info"], fix_marked_done: ["Fix marked done — verification started", "info"],
  task_reopened_by_user: ["Task reopened by a user", "info"], verified: ["Verified fixed", "good"], verification_failed: ["Verification failed", "bad"],
  set_ignored: ["Ignored", ""], set_open: ["Reopened manually", "info"], set_in_progress: ["Marked in progress", "info"],
};

function EvidenceValue({ v }: { v: any }) {
  if (v == null) return <>—</>;
  if (typeof v === "boolean") return <>{v ? "Yes" : "No"}</>;
  if (typeof v === "number") return <>{fmtN(v, 2)}</>;
  if (typeof v === "string") return <>{v.startsWith("http") ? pathOf(v) : v}</>;
  if (Array.isArray(v) && v.every((x) => x == null || typeof x !== "object")) return <>{v.map((x) => (typeof x === "string" && x.startsWith("http") ? pathOf(x) : String(x))).join(", ")}</>;
  return <>{JSON.stringify(v)}</>;
}

/** Simple scalar evidence as tiles; lists/objects as compact tables. */
export function Evidence({ ev }: { ev: Record<string, any> }) {
  const entries = Object.entries(ev || {});
  if (!entries.length) return <p className="muted small">No evidence recorded.</p>;
  const scalars = entries.filter(([, v]) => v == null || typeof v !== "object");
  const complex = entries.filter(([, v]) => v != null && typeof v === "object" && (Array.isArray(v) ? v.length : Object.keys(v).length));
  return (
    <>
      {scalars.length > 0 && <div className="mon-ev">{scalars.map(([k, v]) => <div key={k}><b><EvidenceValue v={v} /></b><span>{human(k)}</span></div>)}</div>}
      {complex.map(([k, v]) => (
        <div key={k} style={{ marginTop: 12 }}>
          <div className="small muted" style={{ fontWeight: 500, marginBottom: 4 }}>{human(k)}</div>
          <MiniTable v={v} />
        </div>
      ))}
    </>
  );
}

function MiniTable({ v }: { v: any }) {
  if (Array.isArray(v)) {
    if (!v.length) return null;
    if (typeof v[0] !== "object") return <div className="mon-chips">{v.slice(0, 40).map((x, i) => <Badge key={i}>{String(x).startsWith("http") ? pathOf(String(x)) : String(x)}</Badge>)}</div>;
    const cols = Array.from(new Set(v.flatMap((r) => Object.keys(r)))).slice(0, 7);
    return (
      <div className="table-wrap"><table className="t"><thead><tr>{cols.map((c) => <th key={c}>{human(c)}</th>)}</tr></thead>
        <tbody>{v.slice(0, 25).map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className={typeof r[c] === "number" ? "num" : ""} style={{ fontSize: 12.5 }}><EvidenceValue v={r[c]} /></td>)}</tr>)}</tbody></table></div>
    );
  }
  return <div className="mon-ev">{Object.entries(v).map(([k, x]) => <div key={k}><b><EvidenceValue v={x} /></b><span>{human(k)}</span></div>)}</div>;
}

export function IssueDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged?: () => void }) {
  const { data: i, error, loading, reload } = useApi<any>(`/issues/${id}`, [id]);
  const { data: team } = useApi<any>("/team");
  const { data: projects } = useProjects();
  const can = useCan();
  const { run, busy } = useAction();
  const watch = useJobWatch();
  const names: Record<string, string> = Object.fromEntries((team?.users || []).map((u: any) => [u.id, u.name || u.email]));
  const after = () => { reload(); onChanged?.(); };
  const setStatus = (status: string, msg: string) => run(() => api(`/issues/${id}`, { method: "PATCH", body: { status } }), msg).then(after);
  const verify = () => run<any>(() => api(`/issues/${id}/verify`, { method: "POST" }), "Verification queued — the URL is re-fetched now").then((r) => {
    after(); if (r?.job_id) watch(r.job_id, after, "Verification");
  });
  const rc = i?.root_cause || {};
  const rcRows: [string, any][] = (["what", "when", "where", "why", "who"] as const).filter((k) => rc[k]).map((k) => [k, rc[k]]);
  const verifyText = rc.verify || (i?.verify?.type === "url_check" ? `The URL is re-fetched automatically when the linked tasks are done and must be ${Object.entries(i.verify.expect || {}).map(([k, v]) => `${human(k).toLowerCase()} = ${v}`).join(", ")}.`
    : i?.verify?.type === "metric" ? `Verified from data on the next monitoring runs (${human(i.verify.metric)}).` : null);
  const isOpen = i && OPEN_STATUSES.includes(i.status);
  const canUrlVerify = i?.verify?.type === "url_check" && i.url && ["verifying", "open", "in_progress", "reopened"].includes(i.status);
  return (
    <Drawer title={i ? i.title : "Issue"} onClose={onClose} foot={i && can("edit") ? (
      <>
        {isOpen && <button className="btn" disabled={busy} onClick={() => setStatus("ignored", "Issue ignored — linked tasks closed")}>Ignore</button>}
        {(i.status === "ignored" || i.status === "resolved") && <button className="btn" disabled={busy} onClick={() => setStatus("open", "Issue reopened")}>Reopen</button>}
        {i.status === "open" || i.status === "reopened" ? <button className="btn" disabled={busy} onClick={() => setStatus("in_progress", "Marked in progress")}>Mark in progress</button> : null}
        <span className="spacer" />
        {canUrlVerify && <button className="btn primary" disabled={busy} onClick={verify}>Verify now</button>}
      </>
    ) : undefined}>
      <ErrorBox error={error} />
      {loading && !i ? <Loading /> : i && (
        <>
          <div className="row" style={{ marginBottom: 10 }}>
            <SevBadge s={i.severity} /><StatusBadge s={i.status} />
            {i.is_opportunity && <Badge kind="pass">Opportunity</Badge>}
            {(i.categories?.length ? i.categories : [i.category]).map((c: string) => <Badge key={c}>{TEAM_LABEL[c] || human(c)}</Badge>)}
          </div>
          <dl className="kv" style={{ marginBottom: 6 }}>
            <dt>Project</dt><dd><Link to={`/projects/${i.project_id}/monitor`}>{(projects || []).find((p) => p.id === i.project_id)?.name || "Open project monitor"}</Link></dd>
            <dt>Type</dt><dd>{human(i.kind)}{i.dimension ? <span className="muted"> · {i.dimension}</span> : null}</dd>
            {i.url && <><dt>URL</dt><dd className="mono-url"><Link to={`/projects/${i.project_id}/monitor/page?url=${encodeURIComponent(i.url)}`}>{i.url}</Link></dd></>}
            {i.query && <><dt>Query</dt><dd>“{i.query}”</dd></>}
            <dt>First seen</dt><dd>{fmtDateTime(i.first_seen)} · seen {i.occurrences}×, last {ago(i.last_seen)}</dd>
            {i.resolved_at && <><dt>Resolved</dt><dd>{fmtDateTime(i.resolved_at)}</dd></>}
          </dl>

          {i.recommendation && <div className="notice" style={{ marginTop: 12 }}><div><b>What to do.</b> {i.recommendation}</div></div>}

          <div className="mon-section">
            <h3>Root cause</h3>
            {rcRows.length ? (
              <dl className="mon-rc">{rcRows.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}</dl>
            ) : <p className="muted small">This issue is a direct detection — the evidence below is the cause.</p>}
          </div>

          {rc.fix && <FixPlan fix={rc.fix} />}

          {rc.evidence && Object.keys(rc.evidence).length > 0 && (
            <div className="mon-section"><h3>Root-cause evidence</h3><Evidence ev={rc.evidence} /></div>
          )}
          <div className="mon-section"><h3>Evidence</h3><Evidence ev={i.evidence} /></div>

          {verifyText && <div className="mon-section"><h3>How the fix is verified</h3><p className="small">{verifyText}</p></div>}

          <div className="mon-section">
            <h3>Linked tasks</h3>
            {i.tasks?.length ? (
              <div className="table-wrap"><table className="t"><thead><tr><th>Task</th><th>Status</th><th>Priority</th><th>Assignees</th><th>Due</th></tr></thead>
                <tbody>{i.tasks.map((t: any) => (
                  <tr key={t.id}>
                    <td><Link to={`/tasks?project=${t.project_id}`}>{t.title}</Link></td>
                    <td><Badge kind={t.status === "done" ? "done" : t.status === "review" ? "pending" : ""}>{human(t.status)}</Badge></td>
                    <td><Badge kind={t.priority}>{human(t.priority)}</Badge></td>
                    <td className="small">{(t.assignee_ids || []).map((u: string) => names[u] || "Team member").join(", ") || <span className="faint">Unassigned</span>}</td>
                    <td className="small muted nowrap">{fmtDate(t.due_date)}</td>
                  </tr>
                ))}</tbody></table></div>
            ) : <p className="muted small">No tasks — opportunities beyond the per-run limit and ignored issues don’t create tasks.</p>}
          </div>

          {i.gap_reports?.length > 0 && (
            <div className="mon-section"><h3>Gap analysis</h3>
              <div className="mon-chips">{i.gap_reports.map((g: any) => (
                <Link key={g.id} className="btn sm" to={`/projects/${i.project_id}/monitor?tab=gaps&gap=${g.id}`}>{human(g.kind)} gap · {human(g.status)}</Link>
              ))}</div>
            </div>
          )}

          <div className="mon-section">
            <h3>History</h3>
            <ol className="mon-tl">
              {[...(i.history || [])].reverse().map((h: any, n: number) => {
                const [label, tone] = EVENT_LABEL[h.event] || [human(h.event), "info"];
                return (
                  <li key={n} className={tone}>
                    <div>{label}{h.by && names[h.by] ? <span className="muted"> by {names[h.by]}</span> : null}</div>
                    {h.note && <div className="small muted">{h.note}</div>}
                    {h.reasons && <div className="small" style={{ color: "var(--critical)" }}>{Array.isArray(h.reasons) ? h.reasons.join("; ") : String(h.reasons)}</div>}
                    <div className="when">{fmtDateTime(h.at)}</div>
                  </li>
                );
              })}
            </ol>
          </div>
        </>
      )}
    </Drawer>
  );
}

export function SevDot({ s }: { s: string }) { return <span className="mon-sev-dot" style={{ background: sevColor[s] }} aria-hidden />; }

/** Horizontal stacked bar of counts by severity. */
export function SevBar({ label, counts, total, to }: { label: ReactNode; counts: Record<string, number>; total: number; to?: string }) {
  const n = SEVS.reduce((a, s) => a + (counts[s] || 0), 0);
  return (
    <div className="mon-bar">
      <span>{to ? <Link to={to}>{label}</Link> : label}</span>
      <div className="track" title={SEVS.map((s) => `${human(s)}: ${counts[s] || 0}`).join(" · ")}>
        {SEVS.map((s) => counts[s] ? <div key={s} style={{ width: `${(100 * counts[s]) / Math.max(1, total)}%`, background: sevColor[s] }} /> : null)}
      </div>
      <span className="num" style={{ textAlign: "right" }}>{n}</span>
    </div>
  );
}

// ------------------------------------------------------------------ sortable tables
export function useSort<T extends Record<string, any>>(rows: T[], initial: string, initialDir: "asc" | "desc" = "desc") {
  const [key, setKey] = useState(initial);
  const [dir, setDir] = useState<"asc" | "desc">(initialDir);
  const sorted = useMemo(() => {
    const r = [...rows];
    r.sort((a, b) => {
      const x = a[key], y = b[key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;  // nulls last whatever the direction
      if (y == null) return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return dir === "asc" ? c : -c;
    });
    return r;
  }, [rows, key, dir]);
  const th = (k: string, children: ReactNode, { r, def = "desc" }: { r?: boolean; def?: "asc" | "desc" } = {}) => (
    <th key={k} className={r ? "r" : ""} aria-sort={key === k ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" onClick={() => { if (key === k) setDir(dir === "asc" ? "desc" : "asc"); else { setKey(k); setDir(def); } }}
        style={{ all: "unset", cursor: "pointer", display: "inline-flex", gap: 4 }}>
        {children}<span aria-hidden className="faint">{key === k ? (dir === "asc" ? "▲" : "▼") : ""}</span>
      </button>
    </th>
  );
  return { sorted, th };
}

/** Format a date-only value (YYYY-MM-DD) in local time, so it never shifts a day across time zones. */
export function fmtDay(s?: string | null) {
  if (!s) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return fmtDate(s);
  return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}


/** Solution, steps per team, who is responsible / accountable / consulted, deadline and prevention strategy. */
export function FixPlan({ fix }: { fix: any }) {
  const who = (l: any[] | undefined) => (l && l.length ? l.map((p) => p.name).join(", ") : "nobody on this team yet — the project manager assigns it");
  return (
    <div className="mon-section">
      <h3>Fix plan &amp; responsibility</h3>
      <div className="notice" style={{ marginBottom: 10 }}><div><b>Solution.</b> {fix.solution}</div></div>
      <dl className="kv" style={{ marginBottom: 10 }}>
        {Object.keys(fix.responsible || {}).map((t) => <Fragment key={t}><dt>Responsible · {TEAM_LABEL[t] || human(t)}</dt><dd>{who(fix.responsible[t])}</dd></Fragment>)}
        <dt>Accountable</dt><dd>{who(fix.accountable)}</dd>
        {fix.consulted?.length > 0 && <><dt>Consulted</dt><dd>{fix.consulted.map((c: string) => TEAM_LABEL[c] || human(c)).join(", ")}</dd></>}
        <dt>Due</dt><dd>{fix.due} <span className="faint small">({fix.sla_days}-day SLA)</span></dd>
      </dl>
      {Object.entries(fix.steps || {}).map(([team, steps]: any) => (
        <div key={team} style={{ marginBottom: 8 }}><b className="small">{TEAM_LABEL[team] || human(team)} steps</b>
          <ol className="small" style={{ margin: "4px 0 0 18px" }}>{steps.map((s: string, n: number) => <li key={n}>{s}</li>)}</ol></div>
      ))}
      {fix.strategy?.length > 0 && <><b className="small">Prevention strategy</b>
        <ul className="small" style={{ margin: "4px 0 0 18px" }}>{fix.strategy.map((s: string, n: number) => <li key={n}>{s}</li>)}</ul></>}
    </div>
  );
}
