import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { fmtDate, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { QueriesTable } from "./ProjectMonitor";
import { Delta, fmtN, IssueDrawer, IssueTable, KpiTile, pathOf, StatusBadge, TrendChart, useProjects, fmtDay } from "./shared";

const PERIODS = [[7, "7 days"], [28, "28 days"], [90, "90 days"]] as const;

export default function PageDashboard() {
  const { id = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const url = sp.get("url") || "";
  const days = Number(sp.get("days") || 28);
  const nav = useNavigate();
  const can = useCan();
  const { run, busy } = useAction();
  const { data: projects } = useProjects();
  const project = projects?.find((p) => p.id === id);
  const { data: d, error, loading, reload } = useApi<any>(url ? `/projects/${id}/monitor/page?url=${encodeURIComponent(url)}&days=${days}` : null, [id, url, days]);
  const [issue, setIssue] = useState<string | null>(null);
  const setDays = (n: number) => { const x = new URLSearchParams(sp); x.set("days", String(n)); setSp(x, { replace: true }); };
  const diagnose = () => run<any>(() => api(`/projects/${id}/gaps`, { body: { kind: "full", target_url: url, competitor_urls: [] } }), "Full page diagnosis queued")
    .then((r) => r && nav(`/projects/${id}/monitor?tab=gaps&gap=${r.id}`));

  if (!url) return <Panel><Empty title="No page selected" action={<Link className="btn" to={`/projects/${id}/monitor?tab=pages`}>Landing pages</Link>}>Open a page from the landing pages list.</Empty></Panel>;
  const s = d?.search, a = d?.analytics;
  const series = (d?.series || []).map((r: any) => ({ ...r, ctr: r.impressions ? +(100 * r.clicks / r.impressions).toFixed(2) : null }));
  return (
    <>
      <div className="small" style={{ marginBottom: 6 }}>
        <Link to={`/projects/${id}/monitor`}>{project?.name || "Project"} monitoring</Link> <span className="faint">/</span> <Link to={`/projects/${id}/monitor?tab=pages`}>Landing pages</Link>
      </div>
      <PageHead title={<span style={{ wordBreak: "break-all" }}>{pathOf(url) || url}</span>} sub={<a href={url} target="_blank" rel="noreferrer noopener" className="mono-url">{url} ↗</a>}>
        <div className="seg" role="group" aria-label="Comparison period">
          {PERIODS.map(([n, l]) => <button key={n} className={days === n ? "on" : ""} aria-pressed={days === n} onClick={() => setDays(n)}>{l}</button>)}
        </div>
        {can("create") && <button className="btn primary" disabled={busy} onClick={diagnose}>Run full diagnosis</button>}
      </PageHead>
      <ErrorBox error={error} />
      {loading && !d ? <Loading /> : d && (
        <>
          <div className="mon-kpis">
            <KpiTile label="Clicks" value={s?.clicks.value} prev={s?.clicks.prev} change={s ? s.clicks.change_pct : undefined} />
            <KpiTile label="Impressions" value={s?.impressions.value} prev={s?.impressions.prev} change={s ? s.impressions.change_pct : undefined} />
            <KpiTile label="CTR" value={s?.ctr.value} prev={s?.ctr.prev} unit="%" decimals={2} diff={s && s.ctr.value != null && s.ctr.prev != null ? +(s.ctr.value - s.ctr.prev).toFixed(2) : undefined} />
            <KpiTile label="Avg position" value={s?.position.value} prev={s?.position.prev} decimals={1} invert diff={s && s.position.value != null && s.position.prev != null ? +(s.position.value - s.position.prev).toFixed(1) : undefined} />
            <KpiTile label="Sessions" value={a?.sessions.value} prev={a?.sessions.prev} change={a ? a.sessions.change_pct : undefined} note={!a ? "No GA4 data" : undefined} />
            <KpiTile label="Conversions" value={a?.conversions.value} prev={a?.conversions.prev} change={a ? a.conversions.change_pct : undefined} decimals={1} note={!a ? "No GA4 data" : undefined} />
          </div>

          <div className="split mon-split">
            <div className="stack">
              <Panel title="Clicks, impressions and position" actions={<span className="faint small">Last 90 days</span>}>
                {series.length ? <TrendChart data={series} lines={[{ key: "clicks", label: "Clicks", color: "var(--accent)" }, { key: "position", label: "Position", color: "var(--high)", axis: "right", reversed: true }]} />
                  : <p className="muted">No Search Console rows for this URL yet.</p>}
              </Panel>
              <Panel title={`Queries${d.queries?.summary ? ` · ${d.queries.summary.total}` : ""}`} pad={false}>
                <QueriesTable rows={d.queries?.rows || []} empty="No queries for this page in the period" />
              </Panel>
              <Panel title={`Issues · ${d.issues?.length || 0}`} pad={false}>
                {d.issues?.length ? <IssueTable issues={d.issues} onOpen={setIssue} /> : <Empty title="No issues on this page" />}
              </Panel>
            </div>
            <div className="stack">
              <Panel title="Latest crawl">
                {d.crawl ? (
                  <dl className="kv" style={{ gridTemplateColumns: "110px 1fr" }}>
                    <dt>Status</dt><dd><Badge kind={d.crawl.status === 200 ? "pass" : "critical"}>{d.crawl.status ?? "—"}</Badge> {d.crawl.noindex && <Badge kind="critical">noindex</Badge>}</dd>
                    <dt>Title</dt><dd>{d.crawl.title || <span className="faint">Missing</span>}</dd>
                    <dt>Meta description</dt><dd className="small">{d.crawl.meta_description || <span className="faint">Missing</span>}</dd>
                    <dt>H1</dt><dd>{(Array.isArray(d.crawl.h1) ? d.crawl.h1.join(" · ") : d.crawl.h1) || <span className="faint">Missing</span>}</dd>
                    <dt>Canonical</dt><dd className="mono-url">{d.crawl.canonical || "—"}{d.crawl.canonical && d.crawl.canonical !== url && <Badge kind="medium">Not self</Badge>}</dd>
                    <dt>Words</dt><dd className="num">{fmtN(d.crawl.word_count)}</dd>
                    <dt>Inlinks / depth</dt><dd className="num">{fmtN(d.crawl.inlinks)} / {d.crawl.depth ?? "—"}</dd>
                    {d.crawl.schema_types?.length > 0 && <><dt>Schema</dt><dd><div className="mon-chips">{d.crawl.schema_types.map((t: string) => <Badge key={t}>{t}</Badge>)}</div></dd></>}
                    {d.crawl.questions?.length > 0 && <><dt>Questions</dt><dd className="small">{d.crawl.questions.length} question headings</dd></>}
                  </dl>
                ) : <p className="muted small">This URL isn’t in the latest crawl. Run a site audit or import a Screaming Frog crawl.</p>}
              </Panel>
              {d.devices?.length > 0 && (
                <Panel title="Devices" pad={false}>
                  <div className="table-wrap"><table className="t"><thead><tr><th>Device</th><th className="r">Clicks</th><th className="r">Change</th><th className="r">Pos.</th></tr></thead>
                    <tbody>{d.devices.map((r: any) => <tr key={r.device}><td>{human(r.device)}</td><td className="r num">{fmtN(r.clicks)}</td><td className="r"><Delta pct={r.change_pct} /></td><td className="r num">{fmtN(r.position, 1)}</td></tr>)}</tbody></table></div>
                </Panel>
              )}
              <Panel title="Changes on this page" pad={false}>
                {d.changes?.length ? (
                  <div className="table-wrap"><table className="t"><thead><tr><th>When</th><th>Field</th><th>Before → after</th></tr></thead>
                    <tbody>{d.changes.map((c: any, i: number) => (
                      <tr key={i}><td className="small nowrap">{fmtDateTime(c.at)}</td><td><Badge>{human(c.field)}</Badge></td>
                        <td className="small"><span className="muted">{c.old || "empty"}</span> → <b style={{ fontWeight: 500 }}>{c.new || "empty"}</b>
                          {c.impact?.verdict && <div><Badge kind={c.impact.verdict === "positive" ? "pass" : c.impact.verdict === "negative" ? "critical" : ""}>{human(c.impact.verdict)}</Badge> <Delta pct={c.impact.change_pct} /></div>}</td></tr>
                    ))}</tbody></table></div>
                ) : <p className="muted small" style={{ padding: 16 }}>No changes detected between crawls.</p>}
              </Panel>
              {d.content_watch && (
                <Panel title="Content watch">
                  <div className="row" style={{ marginBottom: 8 }}><StatusBadge s={d.content_watch.status} /><span className="small muted">Published {fmtDay(d.content_watch.published_at)}{d.content_watch.target_keyword ? ` · “${d.content_watch.target_keyword}”` : ""}</span></div>
                  <div className="mon-cps">{["7", "14", "30", "60", "90"].map((k) => { const c = d.content_watch.checkpoints?.[k]; return <span key={k} className={`mon-cp ${c ? (c.passed ? "pass" : "fail") : ""}`} title={c?.misses?.join("; ")}><b>{k}d</b>{c ? (c.passed ? "pass" : "fail") : "due"}</span>; })}</div>
                </Panel>
              )}
              <Panel title="Gap reports">
                {d.gap_reports?.length ? d.gap_reports.map((g: any) => (
                  <div key={g.id} className="row" style={{ justifyContent: "space-between", padding: "4px 0" }}>
                    <Link to={`/projects/${id}/monitor?tab=gaps&gap=${g.id}`}>{human(g.kind)} analysis</Link><span className="row"><StatusBadge s={g.status} /><span className="faint small">{fmtDate(g.created_at)}</span></span>
                  </div>
                )) : <p className="muted small">None yet. “Run full diagnosis” compares this page with competitors and builds a plan.</p>}
              </Panel>
            </div>
          </div>
        </>
      )}
      {issue && <IssueDrawer id={issue} onClose={() => setIssue(null)} onChanged={reload} />}
    </>
  );
}
