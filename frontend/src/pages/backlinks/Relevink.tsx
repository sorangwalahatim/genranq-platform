import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { NoProjects, usePolled, useProject } from "./shared";

export default function Relevink() {
  const { pid, projects, picker } = useProject();
  const runs = useApi<any[]>(pid ? `/projects/${pid}/relevink` : null, [pid]);
  const [cur, setCur] = useState<string | null>(null);
  const [f, setF] = useState({ target_url: "", keyword: "", anchor: "", mode: "both", candidates: "", include_db: false, use_ai: false });
  const { run, busy } = useAction();
  const can = useCan();
  if (projects && !projects.length) return <><PageHead title="ReleVink" /><NoProjects /></>;
  const go = async () => {
    const r: any = await run(() => api(`/projects/${pid}/relevink`, { body: { ...f, anchor: f.anchor || null, candidates: f.candidates.split(/\s|,/).map((x) => x.trim()).filter(Boolean) } }), "Finding placements…");
    if (r) { setCur(r.run_id); runs.reload(); }
  };
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <>
      <PageHead title="ReleVink" sub="Find the most relevant pages to place a link to a target URL — on your own site (internal links) and on external sites you’re considering — with the sentence where it fits and a suggested anchor.">{picker}</PageHead>
      <div className="split">
        <div className="stack">
          <Panel title="Find placements">
            <form className="form" onSubmit={(e) => { e.preventDefault(); go(); }}>
              <label className="field">Target URL<span className="hint">The page that should receive the link</span><input className="input" value={f.target_url} onChange={set("target_url")} placeholder="https://yoursite.com/service" /></label>
              <div className="grid g2">
                <label className="field">Keyword / topic<input className="input" value={f.keyword} onChange={set("keyword")} placeholder="drain unblocking" /></label>
                <label className="field">Anchor text (optional)<input className="input" value={f.anchor} onChange={set("anchor")} /></label>
              </div>
              <label className="field">Where to look<select className="input" value={f.mode} onChange={set("mode")}><option value="both">Internal + external</option><option value="internal">Internal (your crawled pages)</option><option value="external">External candidates</option></select></label>
              {f.mode !== "internal" && <>
                <label className="field">External candidates<span className="hint">Domains or page URLs, one per line (max 30). Domains are explored from their homepage.</span>
                  <textarea className="input" rows={4} value={f.candidates} onChange={set("candidates")} placeholder={"homeblog.example\nhttps://news.example/home-improvement"} /></label>
                <label className="check"><input type="checkbox" checked={f.include_db} onChange={set("include_db")} /> Also consider domains from my backlink & supplier database</label>
              </>}
              <label className="check"><input type="checkbox" checked={f.use_ai} onChange={set("use_ai")} /> Refine insertion sentences with AI (needs a connected AI provider)</label>
              <div>{can("create") && <button className="btn primary" disabled={busy || !pid || !f.target_url || f.keyword.trim().length < 2}>Find placements</button>}</div>
            </form>
          </Panel>
          {cur && <Result id={cur} />}
        </div>
        <Panel pad={false} title="Previous runs">
          {runs.loading && !runs.data ? <Loading /> : !runs.data?.length ? <Empty title="No runs yet" /> : (
            <table className="t"><tbody>{runs.data.map((r) => <tr key={r.id} className="click" onClick={() => setCur(r.id)} style={r.id === cur ? { background: "var(--accent-soft)" } : undefined}>
              <td><b>{r.keyword}</b><div className="mono-url">{r.target_url}</div><div className="faint small">{r.mode} · {ago(r.created_at)}</div></td>
              <td><Badge kind={r.status}>{r.status}</Badge></td></tr>)}</tbody></table>)}
        </Panel>
      </div>
    </>
  );
}

function Result({ id }: { id: string }) {
  const { data: r, error } = usePolled<any>(`/relevink/${id}`);
  if (error) return <ErrorBox error={error} />;
  if (!r || r.status === "QUEUED" || r.status === "RUNNING") return <Panel><div className="empty" aria-busy="true">Fetching and scoring pages…</div></Panel>;
  if (r.status === "FAILED") return <div className="notice err">Run failed: {r.result?.error}</div>;
  const res = r.result;
  return (<div className="stack">
    <div className={`notice ${res.ai?.used ? "" : "nc"}`}><div>{res.ai?.used ? <>AI refined {res.ai.refined} insertion sentences ({res.ai.model}).</> : <>{res.ai?.note}{!r.ai_connected && r.use_ai && <> <Link to="/integrations">Connect an AI provider</Link>.</>}</>}
      <div className="small muted">Query terms: {res.query_terms?.join(", ")}</div></div></div>
    {res.internal && <Placements title="Internal placements (your pages)" block={res.internal} />}
    {res.external && <Placements title="External placements" block={res.external} external />}
  </div>);
}

function Placements({ title, block, external }: { title: string; block: any; external?: boolean }) {
  if (!block.available) return <Panel title={title}><p className="muted">{block.message}</p></Panel>;
  const ps = block.placements || [];
  return (
    <Panel pad={false} title={title} actions={<span className="faint small">{external ? `${block.candidates} candidates · ${block.pages_fetched} pages fetched` : `${block.pages_considered} crawled pages · top ${block.pages_fetched} read`}</span>}>
      {block.skipped_over_limit?.length > 0 && <div className="bl-toolbar small muted">Skipped (fetch limit): {block.skipped_over_limit.join(", ")}</div>}
      {!ps.length ? <Empty title="No relevant pages">None of the pages share terms with the keyword.</Empty> : (
        <table className="t"><thead><tr><th className="r">Score</th><th>Page · where the link fits</th></tr></thead>
          <tbody>{ps.map((p: any) => <tr key={p.url}>
            <td className="r" style={{ width: 70 }}><span className="bl-score" style={{ color: p.score >= 50 ? "var(--pass)" : p.score >= 20 ? "var(--medium)" : "var(--faint)" }}>{p.fetched === false ? "—" : Math.round(p.score)}</span></td>
            <td><div className="row"><a href={p.url} target="_blank" rel="noreferrer noopener"><b>{p.title || p.url}</b></a>
              {p.already_links && <Badge kind="pass">already links to target</Badge>}{p.noindex && <Badge kind="high">noindex</Badge>}{p.fetched === false && <Badge kind="fail">{p.error || "not fetched"}</Badge>}</div>
              <div className="mono-url">{p.url}</div>
              {p.sentence && <><div className="bl-quote">{p.sentence}</div>
                <div className="small">Anchor: <b>“{p.suggested_anchor}”</b> — {p.how}</div></>}
              {p.ai_sentence && <div className="small" style={{ marginTop: 4 }}><Badge>AI</Badge> {p.ai_sentence}</div>}
              {p.fetched !== false && <div className="faint small">term similarity {p.term_similarity} · sentence match {p.sentence_match}</div>}
            </td></tr>)}</tbody></table>)}
    </Panel>
  );
}
