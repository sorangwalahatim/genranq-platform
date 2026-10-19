import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel, ScoreRing } from "../../components/ui";
import { api } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { NoProjects, usePolled, useProject } from "./shared";

export default function PageCompare() {
  const { pid, projects, picker } = useProject();
  const list = useApi<any[]>(pid ? `/projects/${pid}/page-compare` : null, [pid]);
  const [cur, setCur] = useState<string | null>(null);
  const [f, setF] = useState({ our_url: "", competitors: "", keyword: "" });
  const { run, busy } = useAction();
  const can = useCan();
  if (projects && !projects.length) return <><PageHead title="Competitor audit — page to page" /><NoProjects /></>;
  const comps = f.competitors.split(/\s|,/).map((x) => x.trim()).filter(Boolean);
  const go = async () => {
    const r: any = await run(() => api(`/projects/${pid}/page-compare`, { body: { our_url: f.our_url, competitor_urls: comps, keyword: f.keyword || null } }), "Comparing pages…");
    if (r) { setCur(r.comparison_id); list.reload(); }
  };
  return (
    <>
      <PageHead title="Competitor audit — page to page" sub="Your page against up to five competing pages, side by side: title & meta, headings, depth, schema, links, images, fetch speed, FAQ and E-E-A-T signals — scored, with what to change.">
        {picker}<Link className="btn" to="/competitors">Whole-site audits</Link>
      </PageHead>
      <div className="split">
        <div className="stack">
          <Panel title="New comparison">
            <form className="form" onSubmit={(e) => { e.preventDefault(); go(); }}>
              <label className="field">Your page<input className="input" value={f.our_url} onChange={(e) => setF({ ...f, our_url: e.target.value })} placeholder="https://yoursite.com/page" /></label>
              <label className="field">Competitor pages<span className="hint">One URL per line, up to 5</span><textarea className="input" rows={4} value={f.competitors} onChange={(e) => setF({ ...f, competitors: e.target.value })} /></label>
              <label className="field">Target keyword (optional)<span className="hint">Checks it appears in title and H1</span><input className="input" value={f.keyword} onChange={(e) => setF({ ...f, keyword: e.target.value })} /></label>
              <div>{can("create") && <button className="btn primary" disabled={busy || !pid || !f.our_url || !comps.length || comps.length > 5}>Compare</button>}
                {comps.length > 5 && <span className="small" style={{ color: "var(--critical)", marginLeft: 8 }}>At most 5 competitor URLs</span>}</div>
            </form>
          </Panel>
          {cur && <Result id={cur} />}
        </div>
        <Panel pad={false} title="Saved comparisons">
          {list.loading && !list.data ? <Loading /> : !list.data?.length ? <Empty title="No comparisons yet" /> : (
            <table className="t"><tbody>{list.data.map((c) => <tr key={c.id} className="click" onClick={() => setCur(c.id)} style={c.id === cur ? { background: "var(--accent-soft)" } : undefined}>
              <td><div className="mono-url">{c.our_url}</div><div className="faint small">vs {c.competitor_urls.length} · {ago(c.created_at)}</div></td>
              <td className="r">{c.status === "COMPLETED" ? <><b className="num">{c.our_score}</b><div className="faint small">avg {c.competitor_avg}</div></> : <Badge kind={c.status}>{c.status}</Badge>}</td></tr>)}</tbody></table>)}
        </Panel>
      </div>
    </>
  );
}

const ROWS: [string, string, (m: any) => any][] = [
  ["Title", "title_length", (m) => <>{m.title_length} chars<div className="faint small" style={{ maxWidth: 220 }}>{m.title}</div></>],
  ["Meta description", "meta_length", (m) => `${m.meta_length} chars`],
  ["H1 / H2 / H3", "h2_count", (m) => `${m.h1.length} / ${m.h2_count} / ${m.h3_count}${m.heading_level_skips ? ` · ${m.heading_level_skips} level skips` : ""}`],
  ["Words", "word_count", (m) => m.word_count],
  ["Schema", "schema_types", (m) => m.schema_types.join(", ") || "none"],
  ["Internal / external links", "internal_links", (m) => `${m.internal_links} / ${m.external_links}`],
  ["Images (missing alt)", "images", (m) => `${m.images} (${m.images_missing_alt})`],
  ["Fetch time", "fetch_seconds", (m) => `${m.fetch_seconds}s`],
  ["FAQ questions", "faq_questions", (m) => `${m.faq_questions}${m.faq_schema ? " + FAQPage" : ""}`],
  ["Trust signals", "trust_score", (m) => <>{m.trust_score}<div className="faint small" style={{ maxWidth: 200 }}>{Object.entries(m.trust_signals).filter(([, v]) => v).map(([k]) => k).join(", ")}</div></>],
];

function Result({ id }: { id: string }) {
  const { data: c, error } = usePolled<any>(`/page-compare/${id}`);
  if (error) return <ErrorBox error={error} />;
  if (!c || c.status === "QUEUED" || c.status === "RUNNING") return <Panel><div className="empty" aria-busy="true">Fetching and measuring pages…</div></Panel>;
  const r = c.result;
  if (c.status === "FAILED" || !r?.available) return <div className="notice err">{r?.message || r?.error || "Comparison failed"}</div>;
  const short = (u: string) => { try { const x = new URL(u); return x.host + x.pathname; } catch { return u; } };
  return (<div className="stack">
    <Panel>
      <div className="score-hero" style={{ padding: 0 }}>
        <ScoreRing score={r.our_score} label="your page" />
        <div><h2>Rank {r.our_rank} of {r.page_count}</h2><p className="muted">Competitor average {r.competitor_avg} / 100{r.keyword ? ` · keyword “${r.keyword}”` : ""}.</p>
          {r.failed?.length > 0 && <p className="small" style={{ color: "var(--high)" }}>Not fetched: {r.failed.map((x: any) => `${short(x.url)} (${x.status ?? x.error})`).join(", ")}</p>}</div>
      </div>
    </Panel>
    <Panel pad={false} title="Side by side">
      <div className="table-wrap"><table className="t bl-cmp"><thead><tr><th></th>{r.pages.map((p: any) => <th key={p.url} title={p.url}>{p.ours ? "Yours" : short(p.url)}</th>)}</tr></thead>
        <tbody>
          <tr><td><b>Score</b></td>{r.pages.map((p: any) => <td key={p.url} className={p.ours ? "ours" : ""}><span className="bl-score">{p.score.total}</span></td>)}</tr>
          {Object.keys(r.weights).map((k) => <tr key={k}><td>{r.labels[k]} <span className="faint small">/{r.weights[k]}</span></td>
            {r.pages.map((p: any) => <td key={p.url} className={`num ${r.leaders[k] === p.url ? "best" : ""} ${p.ours ? "ours" : ""}`}>{p.score.dimensions[k]}</td>)}</tr>)}
          {ROWS.map(([label, , fn]) => <tr key={label}><td className="muted">{label}</td>{r.pages.map((p: any) => <td key={p.url} className={`small ${p.ours ? "ours" : ""}`}>{fn(p.metrics)}</td>)}</tr>)}
        </tbody></table></div>
    </Panel>
    <Panel title="What to change on your page">
      {!r.recommendations.length ? <p className="muted">Your page matches or beats the competitors on every dimension.</p> :
        <ol style={{ paddingLeft: 18, margin: 0 }}>{r.recommendations.map((x: any, i: number) => <li key={i} style={{ marginBottom: 8 }}><Badge>{x.label}</Badge> {x.recommendation}
          {x.gap != null && <span className="faint small"> (−{x.gap} pts vs {short(x.best_competitor)})</span>}</li>)}</ol>}
      {r.structure_gap?.missing_topics?.length > 0 && <p className="small muted" style={{ marginTop: 10 }}>Topics competitors cover that you don’t: {r.structure_gap.missing_topics.slice(0, 15).join(", ")}</p>}
    </Panel>
  </div>);
}
