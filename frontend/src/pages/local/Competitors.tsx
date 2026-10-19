import { useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { scoreColor } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { LocationEditor } from "./LocationEditor";
import { CsvButton, Loc, LocationPick, NcBanner, NoProject, ProjectPick, Stars, useLocal } from "./shared";

/** Module 21 — competitor research: discovery, gaps and local-score ranking. */
export default function Competitors() {
  const L = useLocal("own");
  const can = useCan();
  const [kw, setKw] = useState("");
  const data = useApi<any>(L.pid ? `/local/projects/${L.pid}/competitors?${L.lid ? `location_id=${L.lid}&` : ""}${kw ? `keyword=${encodeURIComponent(kw)}` : ""}` : null, [L.pid, L.lid, kw]);
  const [edit, setEdit] = useState<Loc | null | "new">(null);
  const [disc, setDisc] = useState(false);
  const { run } = useAction();
  const d = data.data;
  const chart = d ? [...(d.own ? [{ name: "You", score: d.own.local.score }] : []), ...d.competitors.map((c: any) => ({ name: c.name, score: c.local.score }))] : [];
  return (
    <>
      <PageHead title="Competitor research" sub="Who you compete with in the local pack, how they score and where you are behind (categories, reviews, rating, photos, posting).">
        <ProjectPick {...L} /><LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} label="Your location" />
        {can("edit") && L.pid && <CsvButton path={`/local/projects/${L.pid}/import/competitors`} label="Import competitors CSV" onDone={() => data.reload()}
          hint="Columns: name, address, city, postcode, phone, website, category, additional_categories, rating, reviews, photos, posts_per_month, lat, lng" />}
        {can("create") && <button className="btn" disabled={!L.lid || !L.status?.google_maps.connected} title={L.status?.google_maps.connected ? "" : "Google Maps NOT CONNECTED"} onClick={() => setDisc(true)}>Discover on Google Maps</button>}
        {can("create") && <button className="btn primary" disabled={!L.pid} onClick={() => setEdit("new")}>Add competitor</button>}
      </PageHead>
      <NcBanner conn={L.status?.google_maps} provider="google_maps">Add competitors manually or by CSV; automatic discovery of who ranks nearby needs Google Maps.</NcBanner>
      <NoProject projects={L.projects} />
      <ErrorBox error={data.error} />
      {L.pid && data.loading && !d ? <Loading /> : d && (<>
        <Panel><div className="row" style={{ gap: 10 }}><span className="muted small">Score relevance for keyword</span>
          <input className="input" style={{ width: 260 }} placeholder={d.keyword || "e.g. plumber"} defaultValue={kw} onKeyDown={(e) => e.key === "Enter" && setKw((e.target as HTMLInputElement).value)} onBlur={(e) => setKw(e.target.value)} aria-label="Keyword" /></div></Panel>
        {!d.competitors.length ? <div style={{ marginTop: 16 }}><Panel><Empty title="No competitors yet" action={can("create") && <button className="btn primary" onClick={() => setEdit("new")}>Add competitor</button>}>
          Add the businesses that appear in the map pack for your main keywords.</Empty></Panel></div> : (<>
          <div className="split lc-split" style={{ marginTop: 16 }}>
            <Panel title="Local score ranking">
              <div style={{ height: Math.max(160, chart.length * 34) }}><ResponsiveContainer><BarChart data={chart} layout="vertical" margin={{ left: 10 }}>
                <XAxis type="number" domain={[0, 100]} fontSize={11} /><YAxis type="category" dataKey="name" width={130} fontSize={11} /><Tooltip />
                <Bar dataKey="score" name="Local score" fill="var(--accent)" radius={[0, 3, 3, 0]} /></BarChart></ResponsiveContainer></div>
            </Panel>
            <Panel title="Gaps vs competitors">
              {!d.gaps ? <p className="muted">Add your own location to see gaps.</p> : (<>
                {d.gaps.summary.length ? <ul style={{ margin: "0 0 12px", paddingLeft: 18 }}>{d.gaps.summary.map((s: string) => <li key={s} className="small">{s}</li>)}</ul> : <p className="muted small">No gaps — you match or beat the competitor median.</p>}
                <table className="t"><thead><tr><th>Metric</th><th className="r">You</th><th className="r">Median</th><th className="r">Best</th></tr></thead>
                  <tbody>{d.gaps.metrics.map((m: any) => <tr key={m.metric}><td>{m.label}</td><td className="r num" style={{ color: m.behind ? "var(--critical)" : undefined }}>{m.own ?? "—"}</td><td className="r num">{m.competitor_median ?? "—"}</td><td className="r num">{m.competitor_best ?? "—"}</td></tr>)}</tbody></table>
                {d.gaps.categories_missing.length > 0 && <div style={{ marginTop: 10 }}><div className="small muted">Categories competitors use that you don't:</div>
                  {d.gaps.categories_missing.map((c: any) => <span key={c.category} className="lc-chip">{c.category} · {c.used_by}/{c.of}</span>)}</div>}
              </>)}
            </Panel>
          </div>
          <div style={{ marginTop: 16 }}>
            <Panel title={`Competitors (${d.competitors.length})`} pad={false}>
              <div className="table-wrap"><table className="t"><thead><tr><th>Business</th><th>Rating</th><th className="r">Reviews</th><th>Categories</th><th className="r">Photos</th><th className="r">Posts/mo</th><th className="r">Distance</th><th className="r">Score</th><th>Source</th><th></th></tr></thead>
                <tbody>{d.competitors.map((c: any) => <tr key={c.id}>
                  <td><b>{c.name}</b><div className="faint small">{c.website || c.nap?.city || ""}</div></td><td><Stars n={c.rating} /> <span className="num small">{c.rating ?? ""}</span></td>
                  <td className="r num">{c.review_count ?? "—"}</td><td className="small">{[c.primary_category, ...c.secondary_categories].filter(Boolean).join(", ") || "—"}</td>
                  <td className="r num">{c.photos_count}</td><td className="r num">{c.posts_per_month ?? "—"}</td><td className="r num">{c.local.distance_km != null ? `${c.local.distance_km} km` : "—"}</td>
                  <td className="r num" style={{ color: scoreColor(c.local.score), fontWeight: 600 }}>{c.local.score}</td><td><Badge>{c.source}</Badge></td>
                  <td className="nowrap">{can("edit") && <button className="btn ghost sm" onClick={() => setEdit(c)}>Edit</button>}
                    {can("delete") && <button className="btn ghost sm danger" onClick={() => confirm(`Remove ${c.name}?`) && run(() => api(`/local/locations/${c.id}`, { method: "DELETE" }), "Removed").then(() => data.reload())}>Remove</button>}</td></tr>)}</tbody></table></div>
            </Panel>
          </div>
        </>)}
      </>)}
      {edit && <LocationEditor pid={L.pid} kind="competitor" loc={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => data.reload()} />}
      {disc && L.loc && <Discover pid={L.pid} loc={L.loc} onClose={() => setDisc(false)} onDone={() => data.reload()} />}
    </>
  );
}

function Discover({ pid, loc, onClose, onDone }: { pid: string; loc: Loc; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ keyword: loc.keywords?.[0] || loc.primary_category || "", radius_km: "5", limit: "10" });
  const { run, busy } = useAction();
  return (
    <Modal title="Discover competitors on Google Maps" onClose={onClose} foot={<button className="btn primary" disabled={!f.keyword.trim() || busy} onClick={async () => {
      const r: any = await run(() => api(`/local/projects/${pid}/competitors/discover`, { body: { location_id: loc.id, keyword: f.keyword, radius_km: +f.radius_km, limit: +f.limit } }));
      if (r) { onDone(); onClose(); }
    }}>{busy ? "Searching…" : "Discover"}</button>}>
      <div className="form">
        <label className="field">Keyword<input className="input" value={f.keyword} onChange={(e) => setF({ ...f, keyword: e.target.value })} autoFocus /></label>
        <div className="grid g2"><label className="field">Radius (km)<input className="input" value={f.radius_km} onChange={(e) => setF({ ...f, radius_km: e.target.value })} /></label>
          <label className="field">Max competitors<input className="input" value={f.limit} onChange={(e) => setF({ ...f, limit: e.target.value })} /></label></div>
        <p className="small muted">Searches around {loc.name}'s coordinates and saves the businesses found (with Place details) as competitor listings.</p>
      </div>
    </Modal>
  );
}
