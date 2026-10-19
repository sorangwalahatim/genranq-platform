import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, Modal, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Delta, Evidence, fmtN, pathOf, StatusBadge, fmtDay } from "./shared";

// ------------------------------------------------------------------ change log
export function ChangesTab({ pid, ver }: { pid: string; ver: number }) {
  const { data, error, loading } = useApi<any[]>(`/projects/${pid}/monitor/changes`, [ver]);
  const [field, setField] = useState("");
  const [q, setQ] = useState("");
  const fields = useMemo(() => Array.from(new Set((data || []).map((c) => c.field))).sort(), [data]);
  const rows = (data || []).filter((c) => (!field || c.field === field) && (!q || c.url.toLowerCase().includes(q.toLowerCase())));
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <Panel title={`Site change log · ${rows.length}`} pad={false} actions={
      <div className="mon-filters">
        <input className="input" type="search" placeholder="Filter URLs" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter URLs" />
        <select className="input" value={field} onChange={(e) => setField(e.target.value)} aria-label="Field">
          <option value="">All fields</option>{fields.map((f) => <option key={f} value={f}>{human(f)}</option>)}
        </select>
      </div>}>
      {!data?.length ? (
        <Empty title="No changes detected yet">Changes are found by comparing consecutive crawls (site audits or Screaming Frog imports): status, title, meta, H1, canonical, robots, indexability, word count and pages added or removed.</Empty>
      ) : !rows.length ? <Empty title="No changes match" /> : (
        <div className="table-wrap"><table className="t">
          <thead><tr><th>Detected</th><th>Page</th><th>Field</th><th>Before → after</th><th>Source</th><th>Impact on clicks (7 days)</th></tr></thead>
          <tbody>{rows.map((c) => (
            <tr key={c.id}>
              <td className="nowrap small">{fmtDateTime(c.detected_at)}</td>
              <td style={{ minWidth: 180 }}><Link className="mono-url" to={`/projects/${pid}/monitor/page?url=${encodeURIComponent(c.url)}`}>{pathOf(c.url)}</Link></td>
              <td><Badge>{human(c.field)}</Badge></td>
              <td style={{ minWidth: 220, fontSize: 13 }}>
                <span className="muted" style={{ textDecoration: c.old_value ? "line-through" : undefined }}>{c.old_value || <i>empty</i>}</span>
                <span className="faint"> → </span><b style={{ fontWeight: 500 }}>{c.new_value || <i className="muted">empty</i>}</b>
              </td>
              <td className="small muted">{human(c.changed_by || "crawl")}</td>
              <td className="nowrap">
                {c.impact?.verdict ? (
                  <><Badge kind={c.impact.verdict === "positive" ? "pass" : c.impact.verdict === "negative" ? "critical" : ""}>{human(c.impact.verdict)}</Badge>{" "}
                    <span className="small num">{fmtN(c.impact.clicks_before)} → {fmtN(c.impact.clicks_after)}</span> <Delta pct={c.impact.change_pct} /></>
                ) : <span className="faint small">Measured 7 days after the change</span>}
              </td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ content watch
function addDays(d: string, n: number) { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); }

export function ContentWatchTab({ pid, ver }: { pid: string; ver: number }) {
  const { data, error, loading, reload } = useApi<any>(`/projects/${pid}/content-watch`, [ver]);
  const [add, setAdd] = useState(false);
  const [open, setOpen] = useState<any>(null);
  const can = useCan();
  const { run } = useAction();
  const cps = Object.keys(data?.checkpoints || {}).map(Number).sort((a, b) => a - b);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const items = data?.items || [];
  return (
    <>
      <Panel title={`Content watch · ${items.length}`} pad={false}
        actions={can("create") && <button className="btn primary sm" onClick={() => setAdd(true)}>Watch a URL</button>}>
        <div className="small muted" style={{ padding: "10px 16px", borderBottom: "1px solid var(--line-2)" }}>
          New posts are checked {cps.map((d) => `${d}`).join(" / ")} days after publishing against minimum targets
          ({cps.map((d) => `${d}d: ${Object.entries(data.checkpoints[d]).map(([k, v]) => `${k} ${k === "position" ? "≤" : "≥"}${v}`).join(", ")}`).join(" · ")}).
          A missed checkpoint raises a content issue and runs a gap analysis automatically. Blog-like URLs that first appear in Search Console are added on their own.
        </div>
        {!items.length ? (
          <Empty title="Nothing watched yet" action={can("create") && <button className="btn primary" onClick={() => setAdd(true)}>Watch a URL</button>}>
            Add a newly published article to track whether it reaches its impressions, clicks and ranking targets.
          </Empty>
        ) : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Page</th><th>Published</th><th>Checkpoints</th><th>Status</th><th>Source</th><th /></tr></thead>
            <tbody>{items.map((w: any) => (
              <tr key={w.id}>
                <td style={{ minWidth: 220 }}>
                  <button className="btn ghost" style={{ height: "auto", padding: 0, textAlign: "left", whiteSpace: "normal", fontWeight: 500 }} onClick={() => setOpen(w)}>{w.title || pathOf(w.url)}</button>
                  <div className="faint small">{w.target_keyword ? <>Target: “{w.target_keyword}”</> : "No target keyword"}</div>
                </td>
                <td className="nowrap small">{fmtDay(w.published_at)}</td>
                <td><div className="mon-cps">{cps.map((d) => {
                  const c = w.checkpoints?.[String(d)];
                  return (
                    <span key={d} className={`mon-cp ${c ? (c.passed ? "pass" : "fail") : ""}`}
                      title={c ? `${d} days: ${c.passed ? "passed" : c.misses?.join("; ")}` : `${d}-day check due ${fmtDay(addDays(w.published_at, d))}`}>
                      <b>{d}d</b>{c ? (c.passed ? "pass" : "fail") : "due"}
                    </span>
                  );
                })}</div></td>
                <td><StatusBadge s={w.status} /></td>
                <td className="small muted">{w.source === "auto_gsc" ? "Auto (Search Console)" : human(w.source)}</td>
                <td className="r nowrap">
                  <Link className="btn sm" to={`/projects/${pid}/monitor/page?url=${encodeURIComponent(w.url)}`}>Page</Link>{" "}
                  {can("delete") && <button className="btn ghost sm danger" aria-label={`Stop watching ${pathOf(w.url)}`}
                    onClick={() => confirm("Stop watching this URL? Its checkpoint history is removed.") && run(() => api(`/content-watch/${w.id}`, { method: "DELETE" }), "No longer watched").then(reload)}>Remove</button>}
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Panel>
      {add && <AddWatch pid={pid} onClose={() => setAdd(false)} onDone={reload} />}
      {open && (
        <Drawer title={open.title || pathOf(open.url)} onClose={() => setOpen(null)}>
          <dl className="kv">
            <dt>URL</dt><dd className="mono-url"><a href={open.url} target="_blank" rel="noreferrer noopener">{open.url}</a></dd>
            <dt>Published</dt><dd>{fmtDay(open.published_at)}</dd>
            <dt>Target keyword</dt><dd>{open.target_keyword || "—"}</dd>
            <dt>Competitors</dt><dd>{open.competitor_urls?.length ? open.competitor_urls.map((u: string) => <div key={u} className="mono-url">{u}</div>) : "—"}</dd>
            <dt>Status</dt><dd><StatusBadge s={open.status} /></dd>
          </dl>
          <div className="mon-section"><h3>Checkpoints</h3>
            {cps.map((d) => {
              const c = open.checkpoints?.[String(d)];
              return (
                <div key={d} style={{ padding: "8px 0", borderBottom: "1px solid var(--line-2)" }}>
                  <div className="row"><b>{d} days</b>{c ? <Badge kind={c.passed ? "pass" : "critical"}>{c.passed ? "Passed" : "Missed"}</Badge> : <Badge>Due {fmtDay(addDays(open.published_at, d))}</Badge>}
                    <span className="faint small">Target: {Object.entries(data.checkpoints[d]).map(([k, v]) => `${k} ${k === "position" ? "≤" : "≥"}${v}`).join(", ")}</span></div>
                  {c && <div className="small" style={{ marginTop: 4 }}>{fmtN(c.impressions)} impressions · {fmtN(c.clicks)} clicks · position {c.position ?? "—"} <span className="faint">(checked {fmtDay(c.checked_on)})</span></div>}
                  {c?.misses?.length > 0 && <div className="small" style={{ color: "var(--critical)" }}>{c.misses.join("; ")}</div>}
                </div>
              );
            })}
          </div>
        </Drawer>
      )}
    </>
  );
}

function AddWatch({ pid, onClose, onDone }: { pid: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ url: "", published_at: new Date().toISOString().slice(0, 10), target_keyword: "", title: "", competitors: "" });
  const { run, busy } = useAction();
  const comps = f.competitors.split("\n").map((x) => x.trim()).filter(Boolean);
  const valid = /^https?:\/\/\S+$/.test(f.url) && f.published_at && comps.length <= 10 && comps.every((u) => /^https?:\/\/\S+$/.test(u));
  const save = () => run(() => api(`/projects/${pid}/content-watch`, { body: { url: f.url.trim(), published_at: f.published_at, target_keyword: f.target_keyword || null, title: f.title || null, competitor_urls: comps } }), "URL added to content watch")
    .then((r) => { if (r) { onDone(); onClose(); } });
  return (
    <Modal title="Watch a published URL" onClose={onClose} foot={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!valid || busy} onClick={save}>Watch URL</button></>}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); if (valid) save(); }}>
        <label className="field">URL<input className="input" type="url" required value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://example.com/blog/post" autoFocus /></label>
        <div className="grid g2">
          <label className="field">Published on<input className="input" type="date" required value={f.published_at} onChange={(e) => setF({ ...f, published_at: e.target.value })} /></label>
          <label className="field">Title<span className="hint">Optional</span><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
        </div>
        <label className="field">Target keyword<span className="hint">Used for the automatic gap analysis if the post underperforms.</span><input className="input" value={f.target_keyword} onChange={(e) => setF({ ...f, target_keyword: e.target.value })} /></label>
        <label className="field">Competitor URLs<span className="hint">One per line, up to 10 — pages ranking for the target keyword.</span>
          <textarea className="input" rows={4} value={f.competitors} onChange={(e) => setF({ ...f, competitors: e.target.value })} /></label>
        {comps.length > 10 && <div className="notice err">At most 10 competitor URLs.</div>}
      </form>
    </Modal>
  );
}

// ------------------------------------------------------------------ gap reports
const GAP_KINDS: [string, string, string][] = [
  ["keyword", "Keyword gap", "Keywords competitors rank for in the top 20 where you don’t (needs competitor keyword exports or Semrush/Ahrefs)."],
  ["content", "Content gap", "Topics competitors cover with no page on your site, clustered with suggested titles."],
  ["competitor", "Competitor overlap", "Shared and unique keywords per competitor domain."],
  ["backlink", "Backlink gap", "Referring domains linking to competitors but not to you."],
  ["structure", "Structure gap", "Live comparison of one page against competitor pages: length, headings, FAQ, schema, tables."],
  ["full", "Full page diagnosis", "Keyword → competitors → content and structure gap → intent → links → technical → root causes and a plan."],
];
const GAP_LABEL = Object.fromEntries(GAP_KINDS.map(([k, l]) => [k, l]));

export function GapsTab({ pid, ver, openId, setOpenId }: { pid: string; ver: number; openId: string | null; setOpenId: (id: string | null) => void }) {
  const [poll, setPoll] = useState(false);
  const { data, error, loading, reload } = useApi<any[]>(`/projects/${pid}/gaps`, [ver], { poll: poll ? 4000 : undefined });
  const pending = (data || []).some((g) => g.status === "QUEUED" || g.status === "RUNNING");
  useEffect(() => { setPoll(pending); }, [pending]);
  const [add, setAdd] = useState(false);
  const can = useCan();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <>
      <Panel title={`Gap reports · ${data?.length || 0}`} pad={false} actions={can("create") && <button className="btn primary sm" onClick={() => setAdd(true)}>Run a gap analysis</button>}>
        {!data?.length ? (
          <Empty title="No gap reports yet" action={can("create") && <button className="btn primary" onClick={() => setAdd(true)}>Run a gap analysis</button>}>
            Gap analyses compare your keywords, content, page structure and links with competitors. They also run automatically when a watched post misses a checkpoint.
          </Empty>
        ) : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Analysis</th><th>Target</th><th>Status</th><th>Result</th><th>Trigger</th><th>Created</th></tr></thead>
            <tbody>{data.map((g) => (
              <tr key={g.id} className="click" tabIndex={0} onClick={() => setOpenId(g.id)} onKeyDown={(e) => e.key === "Enter" && setOpenId(g.id)}>
                <td style={{ fontWeight: 500 }}>{GAP_LABEL[g.kind] || human(g.kind)}</td>
                <td className="mono-url" style={{ maxWidth: 260 }}>{g.target_url ? pathOf(g.target_url) : <span className="faint">Whole project</span>}
                  {g.params?.target_keyword && <div className="faint small">“{g.params.target_keyword}”</div>}</td>
                <td><StatusBadge s={g.status} /></td>
                <td className="small">{g.summary || <span className="faint">—</span>}</td>
                <td className="small muted">{g.params?.trigger === "blog_underperforming" ? `Watched post missed ${g.params.checkpoint}-day check` : human(g.params?.trigger || "manual")}</td>
                <td className="small muted nowrap">{ago(g.created_at)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Panel>
      {add && <NewGap pid={pid} onClose={() => setAdd(false)} onDone={(id) => { reload(); setOpenId(id); }} />}
      {openId && <GapDrawer id={openId} pid={pid} onClose={() => setOpenId(null)} />}
    </>
  );
}

function NewGap({ pid, onClose, onDone }: { pid: string; onClose: () => void; onDone: (id: string) => void }) {
  const [f, setF] = useState({ kind: "keyword", target_url: "", target_keyword: "", competitors: "" });
  const { run, busy } = useAction();
  const comps = f.competitors.split("\n").map((x) => x.trim()).filter(Boolean);
  const needsUrl = f.kind === "structure" || f.kind === "full";
  const errs: string[] = [];
  if (needsUrl && !/^https?:\/\/\S+$/.test(f.target_url)) errs.push("An absolute page URL is required.");
  if (f.kind === "structure" && !comps.length) errs.push("Add at least one competitor URL.");
  if (comps.length > 10) errs.push("At most 10 competitor URLs.");
  const save = () => run<any>(() => api(`/projects/${pid}/gaps`, { body: { kind: f.kind, target_url: f.target_url || null, target_keyword: f.target_keyword || null, competitor_urls: comps } }), "Gap analysis queued")
    .then((r) => { if (r) { onDone(r.id); onClose(); } });
  return (
    <Modal title="Run a gap analysis" onClose={onClose} foot={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={busy || errs.length > 0} onClick={save}>Run analysis</button></>}>
      <div className="form">
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="small" style={{ fontWeight: 500, marginBottom: 6 }}>Analysis</legend>
          <div className="stack" style={{ gap: 6 }}>
            {GAP_KINDS.map(([k, l, d]) => (
              <label key={k} className="check" style={{ alignItems: "flex-start", border: "1px solid var(--line)", borderRadius: 7, padding: "8px 10px", background: f.kind === k ? "var(--accent-soft)" : undefined }}>
                <input type="radio" name="gapkind" checked={f.kind === k} onChange={() => setF({ ...f, kind: k })} style={{ marginTop: 3 }} />
                <span><b style={{ fontWeight: 500 }}>{l}</b><span className="muted small" style={{ display: "block" }}>{d}</span></span>
              </label>
            ))}
          </div>
        </fieldset>
        {needsUrl && (
          <label className="field">Page URL<input className="input" type="url" value={f.target_url} onChange={(e) => setF({ ...f, target_url: e.target.value })} placeholder="https://example.com/page" /></label>
        )}
        {f.kind === "full" && <label className="field">Target keyword<span className="hint">Optional — defaults to the page’s top query.</span><input className="input" value={f.target_keyword} onChange={(e) => setF({ ...f, target_keyword: e.target.value })} /></label>}
        {needsUrl && <label className="field">Competitor URLs<span className="hint">One per line, up to 10.{f.kind === "full" ? " Optional — taken from competitor keyword data when empty." : ""}</span>
          <textarea className="input" rows={4} value={f.competitors} onChange={(e) => setF({ ...f, competitors: e.target.value })} /></label>}
        {errs.length > 0 && (f.target_url || comps.length > 0) && <div className="notice warn">{errs.join(" ")}</div>}
      </div>
    </Modal>
  );
}

function GapDrawer({ id, pid, onClose }: { id: string; pid: string; onClose: () => void }) {
  const [poll, setPoll] = useState(true);
  const { data: g, error, loading } = useApi<any>(`/gaps/${id}`, [id], { poll: poll ? 3000 : undefined });
  useEffect(() => { if (g && !["QUEUED", "RUNNING"].includes(g.status)) setPoll(false); }, [g?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const r = g?.result || {};
  return (
    <Drawer title={g ? `${GAP_LABEL[g.kind] || human(g.kind)}${g.target_url ? ` · ${pathOf(g.target_url)}` : ""}` : "Gap report"} onClose={onClose}>
      <ErrorBox error={error} />
      {loading && !g ? <Loading /> : g && (
        <>
          <div className="row" style={{ marginBottom: 12 }}><StatusBadge s={g.status} /><span className="faint small">Created {fmtDateTime(g.created_at)}</span>
            {g.target_url && <Link className="btn sm" to={`/projects/${pid}/monitor/page?url=${encodeURIComponent(g.target_url)}`}>Page dashboard</Link>}</div>
          {["QUEUED", "RUNNING"].includes(g.status) && <div className="notice">The analysis is running — this updates automatically.</div>}
          {g.status === "FAILED" && <div className="notice err">{r.error || "The analysis failed."}</div>}
          {g.status === "COMPLETED" && <GapResult kind={g.kind} r={r} />}
        </>
      )}
    </Drawer>
  );
}

function Table({ cols, rows, max = 100 }: { cols: [string, string, ((r: any) => any)?][]; rows: any[]; max?: number }) {
  if (!rows?.length) return <p className="muted small">None.</p>;
  return (
    <div className="table-wrap"><table className="t"><thead><tr>{cols.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead>
      <tbody>{rows.slice(0, max).map((r, i) => <tr key={i}>{cols.map(([k, , fn]) => <td key={k} style={{ fontSize: 13 }}>{fn ? fn(r) : r[k] ?? "—"}</td>)}</tr>)}</tbody></table>
      {rows.length > max && <p className="faint small" style={{ padding: 8 }}>{rows.length - max} more not shown.</p>}
    </div>
  );
}

function Structure({ r }: { r: any }) {
  return (
    <>
      {r.recommendations?.length > 0 && <div className="mon-section"><h3>Recommendations</h3><ol className="mon-plan">{r.recommendations.map((x: string, i: number) => <li key={i}>{x}</li>)}</ol></div>}
      {r.metrics && <div className="mon-section"><h3>Your page vs competitor median</h3>
        <Table cols={[["k", "Signal"], ["ours", "Yours"], ["competitor_median", "Competitors"]]} rows={Object.entries(r.metrics).map(([k, v]: any) => ({ k: human(k), ...v }))} /></div>}
      {r.missing_sections?.length > 0 && <div className="mon-section"><h3>Sections competitors have</h3><div className="mon-chips">{r.missing_sections.map((x: string) => <Badge key={x}>{x}</Badge>)}</div></div>}
      {r.missing_topics?.length > 0 && <div className="mon-section"><h3>Missing topics</h3><div className="mon-chips">{r.missing_topics.map((x: string) => <Badge key={x}>{x}</Badge>)}</div></div>}
      {r.missing_schema?.length > 0 && <div className="mon-section"><h3>Missing schema</h3><div className="mon-chips">{r.missing_schema.map((x: string) => <Badge key={x}>{x}</Badge>)}</div></div>}
      {r.competitors?.length > 0 && <div className="mon-section"><h3>Compared pages</h3><Table cols={[["url", "URL", (c) => <span className="mono-url">{c.url}</span>], ["word_count", "Words"], ["h2", "H2s", (c) => c.h2?.length ?? 0]]} rows={r.competitors} /></div>}
    </>
  );
}

function KeywordRows({ rows }: { rows: any[] }) {
  return <Table rows={rows} cols={[["keyword", "Keyword"], ["volume", "Volume", (x) => fmtN(x.volume)], ["intent", "Intent", (x) => human(x.intent)],
    ["competitors", "Competitors", (x) => (x.competitors || []).slice(0, 3).map((c: any) => `${c.domain} #${c.position}`).join(", ")],
    ["our_position", "You", (x) => x.our_position ?? "—"], ["action", "Action", (x) => <Badge kind={x.action === "create content" ? "medium" : ""}>{human(x.action)}</Badge>]]} />;
}

function GapResult({ kind, r }: { kind: string; r: any }) {
  if (r.available === false) return <div className="notice nc"><Badge kind="NOT_CONNECTED">Needs data</Badge><div>{r.message} <Link to="/integrations">Integrations</Link></div></div>;
  if (kind === "keyword") return (
    <>
      <p className="small muted" style={{ marginBottom: 10 }}>{fmtN(r.total)} keyword gaps{r.by_intent ? " · " + Object.entries(r.by_intent).map(([k, v]) => `${v} ${k}`).join(", ") : ""}</p>
      <KeywordRows rows={r.keywords || []} />
    </>
  );
  if (kind === "content") return (
    <>
      <p className="small muted" style={{ marginBottom: 10 }}>{fmtN(r.uncovered_keywords)} uncovered keywords grouped into {r.topics?.length || 0} topics.</p>
      <Table rows={r.topics || []} cols={[["suggested_title", "Suggested page", (t) => <><b style={{ fontWeight: 500 }}>{t.suggested_title}</b><div className="faint small">{t.keywords?.slice(0, 6).join(", ")}</div></>],
        ["suggested_format", "Format"], ["intent", "Intent", (t) => human(t.intent)], ["volume", "Volume", (t) => fmtN(t.volume)]]} />
    </>
  );
  if (kind === "competitor") return <Table rows={r.competitors || []} cols={[["domain", "Domain"], ["keywords", "Keywords"], ["shared", "Shared"], ["only_them", "Only them"], ["only_us", "Only you"],
    ["top_missing", "Top missing", (c) => <span className="small">{(c.top_missing || []).slice(0, 8).join(", ")}</span>]]} />;
  if (kind === "backlink") return (
    <>
      <p className="small muted" style={{ marginBottom: 10 }}>Compared with {(r.competitors || []).join(", ")}.</p>
      <Table rows={r.prospects || []} cols={[["domain", "Referring domain"], ["linking_competitors", "Links to competitors"], ["domain_rating", "DR", (x) => fmtN(x.domain_rating)]]} />
    </>
  );
  if (kind === "structure") return <Structure r={r} />;
  // full diagnosis
  const st = r.steps || {};
  return (
    <>
      <div className="mon-section" style={{ marginTop: 0 }}><h3>Root causes</h3><ul className="mon-plan">{(r.root_causes || []).map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
      <div className="mon-section"><h3>Optimisation plan</h3>{r.plan?.length ? <ol className="mon-plan">{r.plan.map((x: string, i: number) => <li key={i}>{x}</li>)}</ol> : <p className="muted small">No plan steps.</p>}</div>
      {st.keyword_analysis && <div className="mon-section"><h3>Keyword analysis</h3>
        <p className="small" style={{ marginBottom: 6 }}>Target keyword: <b>{st.keyword_analysis.target_keyword || "—"}</b>{st.search_intent?.keyword_intent ? ` · ${human(st.search_intent.keyword_intent)} intent` : ""}</p>
        <Table rows={st.keyword_analysis.queries || []} max={15} cols={[["query", "Query"], ["impressions", "Impr."], ["clicks", "Clicks"], ["position", "Pos."]]} /></div>}
      {st.keyword_gap && <div className="mon-section"><h3>Keyword gap</h3>{st.keyword_gap.available === false ? <p className="muted small">{st.keyword_gap.message || "No competitor keyword data."}</p> : <KeywordRows rows={st.keyword_gap.keywords || []} />}</div>}
      {st.structure_gap && <div className="mon-section"><h3>Structure gap</h3>{st.structure_gap.available === false ? <p className="muted small">{st.structure_gap.message}</p> : <Structure r={st.structure_gap} />}</div>}
      {["internal_links", "technical", "ai_search", "backlinks"].filter((k) => st[k]).map((k) => (
        <div key={k} className="mon-section"><h3>{human(k)}</h3>{st[k].available === false ? <p className="muted small">{st[k].message}</p> : <Evidence ev={st[k]} />}</div>
      ))}
    </>
  );
}

