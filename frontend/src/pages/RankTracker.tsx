import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Drawer, Empty, ErrorBox, Loading, Modal, NotConnected, PageHead, Panel, Select } from "../components/ui";
import { api } from "../lib/api";
import { ago, fmtDate } from "../lib/format";
import { useAction, useApi, useCan, useProjectSelection } from "../lib/hooks";

function Spark({ pts }: { pts: { p: number | null }[] }) {
  const v = pts.map((x) => x.p).filter((x): x is number => x != null);
  if (v.length < 2) return <span className="faint small">—</span>;
  const max = Math.max(...v), min = Math.min(...v), w = 80, h = 22;
  const d = v.map((p, i) => `${(i / (v.length - 1)) * w},${max === min ? h / 2 : ((p - min) / (max - min)) * h}`).join(" ");
  return <svg width={w} height={h} aria-hidden><polyline points={d} fill="none" stroke="var(--accent)" strokeWidth="1.5" /></svg>;
}

export default function RankTracker() {
  const [sp] = useSearchParams();
  const { data: projects } = useApi<any[]>("/projects");
  const [pid, setPid] = useProjectSelection(projects);
  useEffect(() => { const p = sp.get("project"); if (p) setPid(p); }, []);
  const { data: kws, error, loading, reload } = useApi<any[]>(pid ? `/keywords?project_id=${pid}` : null, [pid]);
  const { data: sum, reload: reloadSum } = useApi<any>(pid ? `/projects/${pid}/rank-summary` : null, [pid]);
  const { data: integ } = useApi<any[]>("/integrations");
  const [add, setAdd] = useState(false);
  const [hist, setHist] = useState<any>(null);
  const [q, setQ] = useState("");
  const can = useCan();
  const { run } = useAction();
  const file = useRef<HTMLInputElement>(null);
  const connected = (integ || []).filter((i) => ["gsc", "semrush"].includes(i.key) && i.status === "CONNECTED");
  const refresh = () => { reload(); reloadSum(); };
  const rows = (kws || []).filter((k) => !q || k.phrase.includes(q.toLowerCase()));
  const importCsv = async (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    await run(() => api(`/projects/${pid}/rank-import`, { form: fd }), "Positions imported"); refresh();
  };
  return (
    <>
      <PageHead title="Rank tracker" sub="Keyword positions over time from Search Console, Semrush, CSV imports or manual checks.">
        <Select value={pid} onChange={setPid} label="Project" options={(projects || []).map((p) => ({ value: p.id, label: p.name }))} />
        {can("edit") && <><input ref={file} type="file" accept=".csv" hidden onChange={(e) => e.target.files?.[0] && importCsv(e.target.files[0])} />
          <button className="btn" onClick={() => file.current?.click()} disabled={!pid}>Import CSV</button></>}
        {can("create") && <button className="btn" disabled={!pid || !connected.length} onClick={() => run(() => api(`/projects/${pid}/rank-check`, { method: "POST" }), "Rank check queued")}>Check rankings now</button>}
        {can("create") && <button className="btn primary" disabled={!pid} onClick={() => setAdd(true)}>Add keywords</button>}
      </PageHead>
      {!connected.length && <div style={{ marginBottom: 14 }}><NotConnected what="Automatic rank checks">need Google Search Console or Semrush. Until one is <Link to="/integrations">connected</Link>, record positions by CSV import (columns: keyword, position, url, date) or per keyword.</NotConnected></div>}
      {!projects?.length ? <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>} /></Panel> : (<>
        {sum && (
          <div className="grid g4" style={{ marginBottom: 16 }}>
            <div className="panel kpi"><div className="v">{sum.keywords}</div><div className="l">Tracked keywords</div></div>
            <div className="panel kpi"><div className="v">{sum.avg_position ?? "—"}</div><div className="l">Average position</div></div>
            <div className="panel kpi"><div className="v">{sum.top10}</div><div className="l">In the top 10</div></div>
            <div className="panel kpi"><div className="row" style={{ gap: 4, alignItems: "flex-end", height: 34 }}>
              {Object.entries(sum.distribution).map(([k, v]: any) => <div key={k} title={`${k}: ${v}`} style={{ flex: 1, background: k === "not ranking" ? "var(--line)" : "var(--accent)", height: `${sum.keywords ? Math.max(4, (v / sum.keywords) * 34) : 4}px`, borderRadius: 2 }} />)}
            </div><div className="l">Distribution: 1-3 → not ranking</div></div>
          </div>
        )}
        {sum?.trend?.length > 1 && (
          <Panel title="Average position trend"><div style={{ height: 180 }}><ResponsiveContainer>
            <LineChart data={sum.trend.map((t: any) => ({ day: fmtDate(t.day), avg: t.avg }))}><CartesianGrid vertical={false} stroke="var(--line-2)" />
              <XAxis dataKey="day" tickLine={false} axisLine={false} fontSize={12} /><YAxis reversed width={30} tickLine={false} axisLine={false} fontSize={12} /><Tooltip />
              <Line dataKey="avg" stroke="var(--accent)" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer></div></Panel>
        )}
        <div style={{ marginTop: 16 }}>
          <Panel title="Keywords" actions={<input className="input" style={{ width: 200 }} placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter keywords" />} pad={false}>
            <ErrorBox error={error} />
            {loading && !kws ? <Loading /> : !rows.length ? <Empty title="No keywords yet" action={can("create") && <button className="btn primary" onClick={() => setAdd(true)}>Add keywords</button>}>Add the phrases you want to rank for.</Empty> : (
              <div className="table-wrap"><table className="t"><thead><tr><th>Keyword</th><th>Locale</th><th className="r">Position</th><th className="r">7-day change</th><th className="r">Best</th><th>Trend</th><th>Ranking URL</th><th>Source</th><th>Checked</th></tr></thead>
                <tbody>{rows.map((k) => (
                  <tr key={k.id} className="click" onClick={() => setHist(k)}>
                    <td>{k.phrase}{k.tags?.length > 0 && <div className="faint small">{k.tags.join(", ")}</div>}</td><td className="faint small">{k.country}/{k.lang}</td>
                    <td className="r num" style={{ fontWeight: 600 }}>{k.position ?? <span className="faint">—</span>}</td>
                    <td className="r num" style={{ color: k.change_7d > 0 ? "var(--pass)" : k.change_7d < 0 ? "var(--critical)" : undefined }}>{k.change_7d ? (k.change_7d > 0 ? `▲ ${k.change_7d}` : `▼ ${-k.change_7d}`) : "—"}</td>
                    <td className="r num">{k.best ?? "—"}</td><td><Spark pts={k.spark} /></td><td className="mono-url" style={{ maxWidth: 240 }}>{k.url || "—"}</td>
                    <td>{k.source ? <Badge>{k.source}</Badge> : "—"}</td><td className="muted small">{ago(k.checked_at)}</td></tr>
                ))}</tbody></table></div>
            )}
          </Panel>
        </div>
      </>)}
      {add && <AddKeywords pid={pid} onClose={() => setAdd(false)} onDone={refresh} />}
      {hist && <History k={hist} onClose={() => setHist(null)} onDone={refresh} />}
    </>
  );
}

function AddKeywords({ pid, onClose, onDone }: any) {
  const [f, setF] = useState({ phrases: "", country: "us", lang: "en", tags: "", target_url: "" });
  const { run, busy } = useAction();
  const n = f.phrases.split("\n").filter((x) => x.trim()).length;
  return (
    <Modal title="Add keywords" onClose={onClose} foot={<button className="btn primary" disabled={!n || busy} onClick={() => run<any>(() => api("/keywords", { body: { project_id: pid, phrases: f.phrases.split("\n"), country: f.country, lang: f.lang, tags: f.tags ? f.tags.split(",").map((x) => x.trim()) : [], target_url: f.target_url || null } })).then((r) => { if (r) { onDone(); onClose(); } })}>Add {n || ""} keyword{n === 1 ? "" : "s"}</button>}>
      <div className="form">
        <label className="field">Keywords<span className="hint">One per line. Counts against your plan’s rank tracker limit.</span><textarea className="input" rows={8} value={f.phrases} onChange={(e) => setF({ ...f, phrases: e.target.value })} autoFocus /></label>
        <div className="grid g2">
          <label className="field">Country<input className="input" value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })} /></label>
          <label className="field">Language<input className="input" value={f.lang} onChange={(e) => setF({ ...f, lang: e.target.value })} /></label>
        </div>
        <label className="field">Tags<span className="hint">Comma separated</span><input className="input" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} /></label>
        <label className="field">Target URL<span className="hint">Used by the audit to check the keyword appears in the page title and H1.</span><input className="input" value={f.target_url} onChange={(e) => setF({ ...f, target_url: e.target.value })} /></label>
      </div>
    </Modal>
  );
}

function History({ k, onClose, onDone }: any) {
  const { data, reload } = useApi<any>(`/keywords/${k.id}/history`);
  const [pos, setPos] = useState("");
  const [url, setUrl] = useState("");
  const { run } = useAction();
  const can = useCan();
  const series = (data?.history || []).map((h: any) => ({ t: fmtDate(h.checked_at), p: h.position }));
  return (
    <Drawer title={k.phrase} onClose={onClose} foot={can("delete") ? <button className="btn danger" onClick={() => confirm("Stop tracking this keyword?") && run(() => api(`/keywords/${k.id}`, { method: "DELETE" }), "Keyword removed").then(() => { onDone(); onClose(); })}>Stop tracking</button> : undefined}>
      {series.length > 1 ? <div style={{ height: 220 }}><ResponsiveContainer><LineChart data={series}><CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="t" fontSize={11} /><YAxis reversed width={28} fontSize={11} domain={[1, "auto"]} /><Tooltip /><Line dataKey="p" name="Position" stroke="var(--accent)" strokeWidth={2} /></LineChart></ResponsiveContainer></div>
        : <p className="muted">Not enough history for a chart yet.</p>}
      {can("edit") && (
        <form className="row" style={{ margin: "16px 0" }} onSubmit={(e) => { e.preventDefault(); run(() => api(`/keywords/${k.id}/positions`, { body: { position: pos ? Number(pos) : null, url: url || null } }), "Position recorded").then(() => { setPos(""); reload(); onDone(); }); }}>
          <input className="input" style={{ width: 110 }} inputMode="numeric" placeholder="Position" value={pos} onChange={(e) => setPos(e.target.value)} aria-label="Position" />
          <input className="input" style={{ flex: 1 }} placeholder="Ranking URL (optional)" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Ranking URL" />
          <button className="btn">Record check</button>
        </form>
      )}
      <table className="t"><thead><tr><th>Checked</th><th className="r">Position</th><th>URL</th><th>Source</th></tr></thead>
        <tbody>{[...(data?.history || [])].reverse().map((h: any) => <tr key={h.id}><td>{fmtDate(h.checked_at)}</td><td className="r num">{h.position ?? "—"}</td><td className="mono-url">{h.url || "—"}</td><td>{h.source}</td></tr>)}</tbody></table>
    </Drawer>
  );
}
