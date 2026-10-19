import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Badge, CatBars, Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel, ScoreRing, Select, SeverityStrip } from "../components/ui";
import { api } from "../lib/api";
import { ago, fmtDateTime, human, scoreColor } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

const SOURCE_KEYS: Record<string, string> = { gsc: "Search Console", pagespeed: "PageSpeed Insights", ahrefs: "Ahrefs", semrush: "Semrush", screaming_frog: "Screaming Frog", ga4: "Analytics 4", gbp: "Business Profile", claude: "Claude (AI recommendations)" };

export function Audits() {
  const [sp, setSp] = useSearchParams();
  const { data, error, loading, reload } = useApi<any[]>("/audits", [], { poll: 4000 });
  const can = useCan();
  const showNew = sp.get("new") === "1";
  return (
    <>
      <PageHead title="SEO audits" sub="Crawl a site, run all 62 checklist checks against it and the connected data sources, and get a scored, prioritised list of fixes.">
        {can("create") && <button className="btn primary" onClick={() => { sp.set("new", "1"); setSp(sp); }}>Run an audit</button>}
      </PageHead>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : (
        <Panel pad={false}>
          {!data?.length ? <Empty title="No audits yet" action={can("create") && <button className="btn primary" onClick={() => { sp.set("new", "1"); setSp(sp); }}>Run your first audit</button>}>An audit crawls up to your plan’s page allowance and grades every checklist item.</Empty> : (
            <div className="table-wrap"><table className="t"><thead><tr><th>Site</th><th>Status</th><th className="r">Score</th><th>Critical</th><th>High</th><th>Medium</th><th>Low</th><th className="r">Pages</th><th>Trigger</th><th>Started</th></tr></thead>
              <tbody>{data.map((a) => (
                <tr key={a.id}>
                  <td><Link to={`/audits/${a.id}`}>{a.domain}</Link>{a.is_competitor && <span className="faint small"> competitor of {a.site_domain}</span>}</td>
                  <td>{a.status === "RUNNING" || a.status === "QUEUED" ? (
                    <div style={{ minWidth: 110 }}><Badge kind={a.status} /><div className="progress" style={{ marginTop: 4 }}><div style={{ width: `${a.progress || 2}%` }} /></div></div>
                  ) : <Badge kind={a.status} />}{a.status === "FAILED" && a.job_error && <div className="faint small" style={{ maxWidth: 240 }}>{a.job_error.slice(0, 120)}</div>}</td>
                  <td className="r num" style={{ fontWeight: 600, color: scoreColor(a.score) }}>{a.score ?? "—"}</td>
                  {["critical", "high", "medium", "low"].map((s) => <td key={s} className="num">{a.summary?.issues?.[s] ?? "—"}</td>)}
                  <td className="r num">{a.summary?.pages_crawled ?? "—"}</td><td className="muted">{human(a.triggered_by)}</td><td className="muted">{ago(a.created_at)}</td></tr>
              ))}</tbody></table></div>
          )}
        </Panel>
      )}
      {showNew && <NewAudit onClose={() => { sp.delete("new"); sp.delete("site"); sp.delete("competitor"); setSp(sp); }} onDone={reload} initialSite={sp.get("site") || ""} initialComp={sp.get("competitor") || ""} />}
    </>
  );
}

function NewAudit({ onClose, onDone, initialSite, initialComp }: any) {
  const { data: projects } = useApi<any[]>("/projects");
  const { data: usage } = useApi<any>("/billing/usage");
  const sites = useMemo(() => (projects || []).flatMap((p) => p.sites.map((s: any) => ({ ...s, project: p.name }))), [projects]);
  const [site, setSite] = useState(initialSite);
  const [comp, setComp] = useState(initialComp);
  const [pages, setPages] = useState(100);
  const { data: proj } = useApi<any>(site ? `/projects/${sites.find((s) => s.id === site)?.project_id}` : null, [site, sites.length]);
  useEffect(() => { if (!site && sites[0]) setSite(sites[0].id); }, [sites]);
  const meter = usage?.meters?.find((m: any) => m.key === "audit_pages");
  const { run, busy } = useAction();
  const nav = useNavigate();
  const go = async () => {
    const r: any = await run(() => api(`/sites/${site}/audits`, { body: { max_pages: pages, competitor_id: comp || null } }), "Audit queued");
    if (r) { onDone(); onClose(); nav(`/audits/${r.id}`); }
  };
  return (
    <Modal title="Run an audit" onClose={onClose} foot={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!site || busy} onClick={go}>Start audit</button></>}>
      {!sites.length ? <Empty title="Add a site first" action={<Link className="btn primary" to="/projects">Go to projects</Link>}>Audits run against a site inside a project.</Empty> : (
        <div className="form">
          <label className="field">Site<select className="input" value={site} onChange={(e) => { setSite(e.target.value); setComp(""); }}>{sites.map((s) => <option key={s.id} value={s.id}>{s.domain} — {s.project}</option>)}</select></label>
          {proj?.competitors?.length > 0 && <label className="field">Audit a competitor instead<span className="hint">Results are kept alongside your site for side-by-side comparison.</span>
            <select className="input" value={comp} onChange={(e) => setComp(e.target.value)}><option value="">No — audit my site</option>{proj.competitors.map((c: any) => <option key={c.id} value={c.id}>{c.domain}</option>)}</select></label>}
          <label className="field">Pages to crawl: <b className="num">{pages}</b>
            <input type="range" min={10} max={Math.max(10, Math.min(2000, meter?.remaining ?? 2000))} step={10} value={pages} onChange={(e) => setPages(Number(e.target.value))} />
            <span className="hint">{meter ? (meter.limit == null ? "Your plan has no monthly page limit." : `${meter.remaining} of ${meter.limit} audit pages left this month. Unused pages are returned when the crawl finishes.`) : ""}</span></label>
        </div>
      )}
    </Modal>
  );
}

export function AuditDetail() {
  const { id } = useParams();
  const { data: a, error, reload } = useApi<any>(`/audits/${id}`);
  const running = a && ["QUEUED", "RUNNING"].includes(a.status);
  useEffect(() => { if (!running) return; const t = setInterval(reload, 3000); return () => clearInterval(t); }, [running]);
  const [tab, setTab] = useState("issues");
  const [issue, setIssue] = useState<any>(null);
  const [exp, setExp] = useState(false);
  const can = useCan();
  if (error) return <ErrorBox error={error} />;
  if (!a) return <Loading />;
  const delta = a.previous && a.score != null && a.previous.score != null ? Math.round((a.score - a.previous.score) * 10) / 10 : null;
  return (
    <>
      <PageHead title={a.domain} sub={<>{a.competitor_id ? "Competitor audit" : "Site audit"} · {a.finished_at ? `completed ${fmtDateTime(a.finished_at)}` : human(a.status)} · {a.summary?.pages_crawled ?? 0} pages crawled</>}>
        {a.plan_id && <Link className="btn" to={`/action-plans/${a.plan_id}`}>Action plan</Link>}
        {a.status === "COMPLETED" && can("export") && <button className="btn" onClick={() => setExp(true)}>Export report</button>}
        {can("create") && a.status !== "RUNNING" && a.status !== "QUEUED" && <Link className="btn primary" to={`/audits?new=1&site=${a.site_id}${a.competitor_id ? `&competitor=${a.competitor_id}` : ""}`}>Re-run</Link>}
      </PageHead>

      {running && (
        <Panel><div className="row"><Badge kind={a.status} /><b>{a.status === "QUEUED" ? "Waiting for a worker…" : "Crawling and checking…"}</b><span className="spacer" /><span className="num">{a.job?.progress ?? 0}%</span></div>
          <div className="progress" style={{ marginTop: 10 }}><div style={{ width: `${a.job?.progress || 2}%` }} /></div>
          <p className="muted small" style={{ marginTop: 8 }}>You can leave this page — you’ll get a notification when it finishes.</p></Panel>
      )}
      {a.status === "FAILED" && <div className="notice err">This audit failed: {a.summary?.error || a.job?.error || "unknown error"}. Unused audit pages were returned to your allowance.</div>}

      {a.status === "COMPLETED" && (<>
        <div className="split">
          <Panel pad={false}>
            <div className="score-hero">
              <ScoreRing score={a.score} />
              <div>
                <div className="row"><h2 style={{ fontSize: 18 }}>Overall SEO score</h2>
                  {delta != null && <Badge kind={delta >= 0 ? "pass" : "fail"}>{delta >= 0 ? "+" : ""}{delta} since {ago(a.previous.finished_at)}</Badge>}</div>
                <SeverityStrip issues={a.summary.issues} />
                <p className="muted small" style={{ marginTop: 10 }}>{a.summary.pass} checks passed · {a.summary.evaluated} of {a.results.length} checks evaluated ({a.summary.coverage_pct}% coverage). Connect more data sources to raise coverage.</p>
              </div>
            </div>
          </Panel>
          <Panel title="Category scores"><CatBars scores={a.category_scores} /></Panel>
        </div>
        {a.summary.warnings?.length > 0 && <div className="notice warn" style={{ marginTop: 12 }}>{a.summary.warnings.join(" · ")}</div>}

        <div className="tabs" style={{ marginTop: 20 }} role="tablist">
          {[["issues", `Issues (${a.issues.length})`], ["checklist", "Full checklist"], ["pages", "Crawled pages"], ["sources", "Data sources"], ["compare", "Compare"]].map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>))}
        </div>
        {tab === "issues" && <IssuesTab a={a} onOpen={setIssue} />}
        {tab === "checklist" && <ChecklistTab a={a} />}
        {tab === "pages" && <PagesTab id={a.id} />}
        {tab === "sources" && <SourcesTab a={a} />}
        {tab === "compare" && <CompareTab a={a} />}
      </>)}
      {issue && <IssueDrawer i={issue} onClose={() => setIssue(null)} />}
      {exp && <ExportModal a={a} onClose={() => setExp(false)} />}
    </>
  );
}

function IssuesTab({ a, onOpen }: any) {
  const [sev, setSev] = useState("");
  const [cat, setCat] = useState("");
  const cats = Array.from(new Set(a.issues.map((i: any) => i.category))) as string[];
  const items = a.issues.filter((i: any) => (!sev || i.severity === sev) && (!cat || i.category === cat));
  return (
    <Panel pad={false}>
      <div className="panel-head"><span className="muted small">Ranked by priority = impact × prevalence × ease of fix</span><div className="row">
        <Select value={sev} onChange={setSev} placeholder="All severities" label="Severity" options={["critical", "high", "medium", "low"].map((s) => ({ value: s, label: human(s) }))} />
        <Select value={cat} onChange={setCat} placeholder="All categories" label="Category" options={cats.map((c) => ({ value: c, label: c }))} /></div></div>
      {!items.length ? <Empty title="No issues match" /> : (
        <div className="table-wrap"><table className="t"><thead><tr><th className="r">Priority</th><th>Issue</th><th>Severity</th><th>Category</th><th className="r">Affected</th><th>Owner</th></tr></thead>
          <tbody>{items.map((i: any) => (
            <tr key={i.id} className="click" onClick={() => onOpen(i)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(i)}>
              <td className="r num">{i.priority_score.toFixed(1)}</td>
              <td><b style={{ fontWeight: 500 }}>{i.title}</b><div className="faint small">{i.rule} · {i.evidence?.message}</div></td>
              <td><Badge kind={i.severity} />{i.evidence?.grade === "partial" && <span className="faint small"> partial</span>}</td><td>{i.category}</td><td className="r num">{i.affected_count}</td><td className="small">{i.owner}</td></tr>
          ))}</tbody></table></div>
      )}
    </Panel>
  );
}

function IssueDrawer({ i, onClose }: any) {
  const { message, grade, ai_recommendation, ai_model, ...rest } = i.evidence || {};
  return (
    <Drawer title={i.title} onClose={onClose}>
      <div className="row" style={{ marginBottom: 12 }}><Badge kind={i.severity} /><Badge>{i.rule}</Badge><Badge>{i.category}</Badge><Badge>Owner: {i.owner}</Badge></div>
      <p style={{ marginBottom: 14 }}><b>Finding.</b> {message}</p>
      <h3>How to fix</h3>
      <p style={{ margin: "6px 0 14px", whiteSpace: "pre-wrap" }}>{i.recommendation}</p>
      {ai_recommendation && <><h3>AI guidance <span className="faint small">({ai_model})</span></h3><p style={{ whiteSpace: "pre-wrap", margin: "6px 0 14px" }}>{ai_recommendation}</p></>}
      {i.affected_urls?.length > 0 && <><h3>Affected ({i.affected_count})</h3>
        <ul style={{ paddingLeft: 18, margin: "6px 0 14px" }}>{i.affected_urls.slice(0, 100).map((u: string) => <li key={u} className="mono-url">{u}</li>)}</ul></>}
      {Object.keys(rest).length > 0 && <><h3>Evidence</h3><pre className="code">{JSON.stringify(rest, null, 2).slice(0, 4000)}</pre></>}
    </Drawer>
  );
}

function ChecklistTab({ a }: any) {
  const [grade, setGrade] = useState("");
  const by: Record<string, any[]> = {};
  a.results.filter((r: any) => !grade || r.grade === grade).forEach((r: any) => (by[r.category] ||= []).push(r));
  const counts = a.results.reduce((acc: any, r: any) => ({ ...acc, [r.grade]: (acc[r.grade] || 0) + 1 }), {});
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        {["", "fail", "partial", "pass", "not_run", "manual", "na"].map((g) => <button key={g} className={`btn sm ${grade === g ? "primary" : ""}`} onClick={() => setGrade(g)}>{g ? `${human(g === "na" ? "not applicable" : g)} (${counts[g] || 0})` : `All (${a.results.length})`}</button>)}
      </div>
      <div className="stack">{Object.entries(by).map(([cat, rs]) => (
        <Panel key={cat} title={cat} actions={a.category_scores[cat] != null && <b className="num" style={{ color: scoreColor(a.category_scores[cat]) }}>{Math.round(a.category_scores[cat])}</b>} pad={false}>
          <table className="t"><tbody>{rs.map((r: any) => (
            <tr key={r.id}><td style={{ width: 80 }} className="faint small">{r.code}</td><td><div>{r.check}</div><div className="faint small">{r.message}</div></td>
              <td className="small muted" style={{ width: 170 }}>{r.data_source}{r.auto === "P" && " · partly automated"}</td>
              <td style={{ width: 80 }}><Badge kind={r.severity === "High" ? "high" : r.severity === "Low" ? "low" : "medium"}>{r.severity}</Badge></td>
              <td style={{ width: 100 }}><Badge kind={r.grade}>{r.grade === "na" ? "N/A" : human(r.grade)}</Badge></td></tr>
          ))}</tbody></table>
        </Panel>
      ))}</div>
    </>
  );
}

function PagesTab({ id }: { id: string }) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const { data } = useApi<any>(`/audits/${id}/pages?limit=100&offset=${page * 100}${q ? `&q=${encodeURIComponent(q)}` : ""}`, [q, page]);
  return (
    <Panel pad={false}>
      <div className="panel-head"><input className="input" style={{ maxWidth: 300 }} placeholder="Filter URLs" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} aria-label="Filter URLs" /><span className="muted small">{data?.total ?? 0} URLs</span></div>
      <div className="table-wrap"><table className="t"><thead><tr><th>URL</th><th>Status</th><th className="r">Depth</th><th>Title</th><th className="r">Words</th><th className="r">Inlinks</th><th>Indexable</th><th className="r">Time</th></tr></thead>
        <tbody>{data?.pages.map((p: any) => (
          <tr key={p.url}><td className="mono-url" style={{ maxWidth: 280 }}>{p.url}{p.source === "sitemap" && <span className="faint"> (sitemap)</span>}</td>
            <td><Badge kind={p.status === 200 ? "pass" : p.status >= 400 || !p.status ? "fail" : "partial"}>{p.status ?? "error"}</Badge></td>
            <td className="r num">{p.depth >= 0 ? p.depth : "—"}</td><td className="small" style={{ maxWidth: 260 }}>{p.title || <span className="faint">missing</span>}</td>
            <td className="r num">{p.word_count ?? "—"}</td><td className="r num">{p.inlinks ?? "—"}</td><td>{p.noindex ? "noindex" : p.canonical && p.canonical !== p.url ? "canonicalised" : "yes"}</td>
            <td className="r num">{p.elapsed != null ? `${(p.elapsed * 1000).toFixed(0)} ms` : "—"}</td></tr>
        ))}</tbody></table></div>
      <div className="panel-head"><button className="btn sm" disabled={!page} onClick={() => setPage(page - 1)}>Previous</button><button className="btn sm" disabled={(page + 1) * 100 >= (data?.total || 0)} onClick={() => setPage(page + 1)}>Next</button></div>
    </Panel>
  );
}

function SourcesTab({ a }: any) {
  const notRun = a.results.filter((r: any) => r.grade === "not_run");
  return (
    <div className="grid g2">
      <Panel title="Data sources for this run">
        {Object.entries(SOURCE_KEYS).map(([k, l]) => (
          <div key={k} className="row" style={{ padding: "5px 0" }}><span style={{ flex: 1 }}>{l}</span><Badge kind={a.sources?.[k] || "NOT_CONNECTED"}>{a.sources?.[k] === "CONNECTED" ? "Used" : "Not connected"}</Badge></div>))}
        <div className="row" style={{ padding: "5px 0" }}><span style={{ flex: 1 }}>Genranq crawler</span><Badge kind="pass">Used</Badge></div>
        <Link to="/integrations" className="btn sm" style={{ marginTop: 10 }}>Manage integrations</Link>
      </Panel>
      <Panel title={`Checks waiting on a data source (${notRun.length})`}>
        {notRun.map((r: any) => <div key={r.id} style={{ padding: "5px 0", borderBottom: "1px solid var(--line-2)" }}><b style={{ fontWeight: 500 }}>{r.code}</b> {r.check}<div className="faint small">{r.message}</div></div>)}
      </Panel>
    </div>
  );
}

function CompareTab({ a }: any) {
  const { data: audits } = useApi<any[]>(`/audits?site_id=${a.site_id}`);
  const options = (audits || []).filter((x) => x.id !== a.id && x.status === "COMPLETED");
  const [other, setOther] = useState("");
  const { data: cmp } = useApi<any>(other ? `/audits/${a.id}/compare/${other}` : null, [other]);
  return (
    <Panel title="Compare with another audit" actions={<Select value={other} onChange={setOther} placeholder="Choose an audit" label="Compare with" options={options.map((o) => ({ value: o.id, label: `${o.domain}${o.is_competitor ? " (competitor)" : ""} — ${ago(o.finished_at)}` }))} />} pad={false}>
      {!options.length && <Empty title="Nothing to compare yet">Run another audit of this site, or audit a competitor from the project page.</Empty>}
      {cmp && (<>
        <div className="grid g2 panel-body">
          {[cmp.a, cmp.b].map((x: any) => <div key={x.id} className="row"><ScoreRing score={x.score} size={90} label="" /><div><b>{x.domain}</b><CatBars scores={x.category_scores} /></div></div>)}
        </div>
        <div className="table-wrap"><table className="t"><thead><tr><th>Check</th><th>{cmp.a.domain}</th><th>{cmp.b.domain}</th></tr></thead>
          <tbody>{cmp.checks.filter((c: any) => c.a !== c.b).map((c: any) => <tr key={c.code}><td><span className="faint small">{c.code}</span> {c.check}</td><td><Badge kind={c.a} /></td><td><Badge kind={c.b} /></td></tr>)}</tbody></table></div>
        <p className="panel-body faint small">Only checks with different results are listed.</p>
      </>)}
    </Panel>
  );
}

function ExportModal({ a, onClose }: any) {
  const [fmt, setFmt] = useState("pdf");
  const [wl, setWl] = useState(false);
  const { run, busy } = useAction();
  const nav = useNavigate();
  return (
    <Modal title="Export report" onClose={onClose} foot={<button className="btn primary" disabled={busy} onClick={() => run(() => api("/reports", { body: { audit_run_id: a.id, format: fmt, white_label: wl } }), "Report is being generated").then((r) => { if (r) { onClose(); nav("/reports"); } })}>Generate report</button>}>
      <div className="form">
        <div className="seg"><button className={fmt === "pdf" ? "on" : ""} onClick={() => setFmt("pdf")}>PDF</button><button className={fmt === "xlsx" ? "on" : ""} onClick={() => setFmt("xlsx")}>Excel</button></div>
        <label className="check"><input type="checkbox" checked={wl} onChange={(e) => setWl(e.target.checked)} /> White-label (remove Genranq branding — Advanced plan and above)</label>
        <p className="muted small">Reports use your logo and colour from <Link to="/settings">Settings</Link>.</p>
      </div>
    </Modal>
  );
}
