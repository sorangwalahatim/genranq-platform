import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, NotConnected, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { NoProjects, Tabs, useProject } from "./shared";

const INTENTS = ["informational", "commercial", "transactional", "local", "navigational"];
const kdColor = (d: number | null) => d == null ? undefined : d >= 70 ? "var(--critical)" : d >= 40 ? "var(--medium)" : "var(--pass)";

export default function KeywordResearch() {
  const { pid, projects, picker } = useProject();
  const { data: k, error, loading, reload } = useApi<any>(pid ? `/projects/${pid}/keyword-research` : null, [pid]);
  const [tab, setTab] = useState("keywords");
  const [q, setQ] = useState("");
  const [intent, setIntent] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const { run, busy } = useAction();
  const can = useCan();
  if (projects && !projects.length) return <><PageHead title="Keyword research" /><NoProjects /></>;
  const rows = (k?.keywords || []).filter((x: any) => (!q || x.keyword.includes(q.toLowerCase())) && (!intent || x.intent === intent));
  const track = async (phrases: string[]) => {
    const r: any = await run(() => api(`/projects/${pid}/keyword-research/track`, { body: { phrases } }));
    if (r) { setSel(new Set()); reload(); alert(`${r.added} added to the rank tracker${r.skipped_duplicates ? `, ${r.skipped_duplicates} already tracked` : ""}.`); }
  };
  const toggle = (kw: string) => setSel((s) => { const n = new Set(s); n.has(kw) ? n.delete(kw) : n.add(kw); return n; });
  return (
    <>
      <PageHead title="Keyword research" sub="Your tracked keywords, Search Console queries and imported own/competitor keywords in one list — with intent, clusters, competitor gaps and difficulty when Ahrefs or Semrush data exists.">
        {picker}
        {can("create") && <button className="btn primary" disabled={!sel.size || busy} onClick={() => track([...sel])}>Add {sel.size || ""} to rank tracker</button>}
      </PageHead>
      <ErrorBox error={error} />
      {loading && !k ? <Loading /> : k && (<div className="stack">
        {!k.difficulty_available && <NotConnected what="Keyword difficulty">{k.difficulty_message} <Link to="/integrations/ahrefs">Ahrefs</Link> · <Link to="/integrations/semrush">Semrush</Link> · <Link to="/backlinks/ahrefs">import exports</Link>.</NotConnected>}
        {k.difficulty_available && (k.connections.ahrefs || k.connections.semrush) && can("edit") &&
          <div className="row"><button className="btn sm" onClick={() => run(() => api(`/projects/${pid}/keyword-research/difficulty`, { body: {} }), "Difficulty refresh queued")}>Fetch missing difficulty from {k.connections.semrush ? "Semrush" : "Ahrefs"}</button></div>}
        <div className="grid g4">
          <div className="panel kpi"><div className="v">{k.total}</div><div className="l">Keywords ({Object.entries(k.by_source).map(([s, n]: any) => `${n} ${s}`).join(", ") || "none"})</div></div>
          <div className="panel kpi"><div className="v">{k.tracked}</div><div className="l">In the rank tracker</div></div>
          <div className="panel kpi"><div className="v">{k.long_tail}</div><div className="l">Long-tail (4+ words)</div></div>
          <div className="panel kpi"><div className="row" style={{ gap: 4 }}>{INTENTS.filter((i) => k.by_intent[i]).map((i) => <span key={i} className="bl-chip">{i} {k.by_intent[i]}</span>)}</div><div className="l">By intent</div></div>
        </div>
        {!k.has_gsc && <div className="notice nc small"><div>No Search Console queries for this project yet — <Link to="/integrations/gsc">connect GSC</Link> or import a GSC export on the project’s Sources to add real search demand.</div></div>}
        <Tabs value={tab} onChange={setTab} tabs={[["keywords", "Keywords"], ["clusters", `Clusters (${k.clusters.length})`], ["gap", "Gap vs competitors"]]} />
        {tab === "keywords" && <Panel pad={false} title="Keywords" actions={<>
          <input className="input" style={{ width: 200 }} placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter keywords" />
          <select className="input" style={{ width: "auto" }} value={intent} onChange={(e) => setIntent(e.target.value)} aria-label="Intent"><option value="">All intents</option>{INTENTS.map((i) => <option key={i}>{i}</option>)}</select></>}>
          {!rows.length ? <Empty title="No keywords">Track keywords, connect Search Console, or import Ahrefs/Semrush organic keyword exports (yours and competitors’).</Empty> : (
            <div className="table-wrap"><table className="t"><thead><tr><th style={{ width: 28 }}></th><th>Keyword</th><th>Intent</th><th>Source</th><th className="r">Impr.</th><th className="r">Clicks</th><th className="r">Our pos.</th><th className="r">Volume</th><th className="r">KD</th><th>Competitors</th></tr></thead>
              <tbody>{rows.slice(0, 500).map((x: any) => <tr key={x.keyword}>
                <td>{x.tracked ? <Badge kind="pass">tracked</Badge> : <input type="checkbox" aria-label={`Select ${x.keyword}`} checked={sel.has(x.keyword)} onChange={() => toggle(x.keyword)} />}</td>
                <td>{x.keyword}{x.long_tail && <span className="faint small"> · long-tail</span>}</td><td><Badge>{x.intent}</Badge></td>
                <td className="small muted">{x.competitor_only ? "competitor" : x.source}</td>
                <td className="r num">{x.impressions ?? "—"}</td><td className="r num">{x.clicks ?? "—"}</td><td className="r num">{x.position ?? "—"}</td><td className="r num">{x.volume ?? "—"}</td>
                <td className="r num" style={{ color: kdColor(x.difficulty) }} title={x.difficulty_source ? `from ${x.difficulty_source}` : "needs Ahrefs/Semrush"}>{x.difficulty ?? <span className="faint">—</span>}</td>
                <td className="small">{x.competitors?.length ? x.competitors.slice(0, 3).map((c: any) => `${c.domain} #${c.position ?? "?"}`).join(", ") : "—"}</td></tr>)}</tbody></table></div>)}
        </Panel>}
        {tab === "clusters" && (!k.clusters.length ? <Panel><Empty title="No clusters yet" /></Panel> : <div className="grid g2">{k.clusters.map((c: any) => (
          <Panel key={c.head + c.pillar} title={<>{c.head} <span className="faint small">· {c.size} keywords</span></>} actions={<><Badge>{c.intent}</Badge>{c.avg_difficulty != null && <span className="small" style={{ color: kdColor(c.avg_difficulty) }}>KD {c.avg_difficulty}</span>}
            {can("create") && <button className="btn sm" onClick={() => track(c.keywords.map((x: any) => x.keyword))}>Track all</button>}</>}>
            <div className="small muted" style={{ marginBottom: 6 }}>Pillar: <b>{c.pillar}</b> · {c.impressions ? `${c.impressions} impressions` : `${c.volume || 0} volume`}</div>
            <div>{c.keywords.slice(0, 20).map((x: any) => <span key={x.keyword} className="bl-chip">{x.keyword}</span>)}</div>
          </Panel>))}</div>)}
        {tab === "gap" && <Gap pid={pid} track={track} />}
      </div>)}
    </>
  );
}

function Gap({ pid, track }: { pid: string; track: (p: string[]) => void }) {
  const { data: g, error, loading } = useApi<any>(`/projects/${pid}/keyword-research/gap`, [pid]);
  const can = useCan();
  if (error) return <ErrorBox error={error} />;
  if (loading && !g) return <Loading />;
  if (!g?.available) return <Panel><Empty title="No competitor keyword data">{g?.message} <Link to="/backlinks/semrush">Import competitor keywords</Link>.</Empty></Panel>;
  return (<div className="stack">
    <Panel pad={false} title={`${g.total} keywords competitors rank for (top 20) and you don’t`}>
      <div className="table-wrap"><table className="t"><thead><tr><th>Keyword</th><th>Intent</th><th className="r">Volume</th><th className="r">KD</th><th>Competitors</th><th className="r">Your pos.</th><th>Action</th><th></th></tr></thead>
        <tbody>{g.keywords.slice(0, 300).map((r: any) => <tr key={r.keyword}><td>{r.keyword}</td><td><Badge>{r.intent}</Badge></td><td className="r num">{r.volume || "—"}</td>
          <td className="r num" style={{ color: kdColor(r.difficulty) }}>{r.difficulty ?? "—"}</td>
          <td className="small">{r.competitors.map((c: any) => `${c.domain} #${c.position}`).join(", ")}</td><td className="r num">{r.our_position ?? "—"}</td>
          <td className="small">{r.action}</td><td>{r.tracked ? <Badge kind="pass">tracked</Badge> : can("create") && <button className="btn sm" onClick={() => track([r.keyword])}>Track</button>}</td></tr>)}</tbody></table></div>
    </Panel>
    {g.content_gap?.length > 0 && <Panel pad={false} title="Content to create">
      <table className="t"><thead><tr><th>Suggested page</th><th>Keywords</th><th>Format</th><th className="r">Volume</th></tr></thead>
        <tbody>{g.content_gap.map((t: any) => <tr key={t.topic}><td><b>{t.suggested_title}</b><div className="faint small">{t.pillar_keyword}</div></td><td className="small">{t.keywords.slice(0, 6).join(", ")}</td><td className="small">{t.suggested_format}</td><td className="r num">{t.volume || "—"}</td></tr>)}</tbody></table>
    </Panel>}
  </div>);
}
