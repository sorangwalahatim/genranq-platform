import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, scoreColor } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { LocationPick, NcBanner, NoLocation, NoProject, ProjectPick, Stars, useLocal } from "./shared";

/** Module 19 — local scan: side-by-side profile metrics for a keyword + location. */
export default function LocalScan() {
  const L = useLocal("own");
  const can = useCan();
  const { run, busy } = useAction();
  const [f, setF] = useState({ keyword: "", radius_km: 5, discover: false });
  const runs = useApi<any[]>(L.pid && L.lid ? `/local/runs?project_id=${L.pid}&kind=local_scan&location_id=${L.lid}` : null, [L.pid, L.lid]);
  const [res, setRes] = useState<any>(null);
  useEffect(() => { setRes(null); const r = runs.data?.[0]; if (r) api(`/local/runs/${r.id}`).then(setRes).catch(() => {}); }, [runs.data]);
  useEffect(() => { if (L.loc && !f.keyword) setF((x) => ({ ...x, keyword: L.loc!.keywords?.[0] || L.loc!.primary_category || "" })); }, [L.loc?.id]);
  const maps = !!L.status?.google_maps.connected;
  const go = async () => {
    const r = await run(() => api(`/local/projects/${L.pid}/scan`, { body: { location_id: L.lid, keyword: f.keyword, radius_km: +f.radius_km, discover: f.discover && maps } }));
    if (r) { setRes(r); runs.reload(); }
  };
  const r = res?.result;
  return (
    <>
      <PageHead title="Local scan" sub="Compare your listing with competitors for a keyword: rating, reviews, categories, photos, website, distance and a local score.">
        <ProjectPick {...L} /><LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} />
      </PageHead>
      <NcBanner conn={L.status?.google_maps} provider="google_maps">Scans compare the competitor listings you have added manually or by CSV; discovering who ranks for the keyword needs Google Maps.</NcBanner>
      <NoProject projects={L.projects} />
      {L.pid && L.locs.data && !L.locs.data.length ? <NoLocation /> : L.loc && (<>
        <Panel>
          <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>
            <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="Keyword, e.g. emergency plumber" value={f.keyword} onChange={(e) => setF({ ...f, keyword: e.target.value })} aria-label="Keyword" />
            <label className="field" style={{ width: 120 }}><input className="input" inputMode="decimal" value={f.radius_km} onChange={(e) => setF({ ...f, radius_km: e.target.value as any })} aria-label="Radius km" /></label>
            <label className="check" title={maps ? "" : "Google Maps NOT CONNECTED"}><input type="checkbox" disabled={!maps} checked={f.discover && maps} onChange={(e) => setF({ ...f, discover: e.target.checked })} /> Discover competitors on Google Maps</label>
            {can("create") && <button className="btn primary" disabled={!f.keyword.trim() || busy} onClick={go}>{busy ? "Scanning…" : "Run scan"}</button>}
          </div>
        </Panel>
        <div style={{ marginTop: 16 }}>
          {!r ? <Panel><Empty title="No scan yet">Run a scan to rank your listing against competitors for the keyword.</Empty></Panel> : (
            <Panel title={<>“{r.keyword}” · you are #{r.own_position} of {r.rows.length} <span className="faint small">· {ago(res.created_at)}</span></>} pad={false}>
              {r.note && <div className="notice" style={{ margin: 12 }}>{r.note} <Link to="/local/competitors">Add competitors</Link></div>}
              {r.discovery && <div className="notice" style={{ margin: 12 }}>Google Maps returned {r.discovery.results} results; your listing ranked {r.discovery.own_rank ? `#${r.discovery.own_rank}` : "outside the results"}; {r.discovery.competitors.length} competitors saved.</div>}
              <div className="table-wrap"><table className="t"><thead><tr><th>#</th><th>Business</th><th>Rating</th><th className="r">Reviews</th><th>Categories</th><th className="r">Photos</th><th>Website</th><th className="r">Distance</th><th className="r">Posts/mo</th><th className="r">Local score</th></tr></thead>
                <tbody>{r.rows.map((x: any) => <tr key={x.id} style={x.own ? { background: "var(--accent-soft)" } : undefined}>
                  <td className="num">{x.position}</td><td><b>{x.name}</b>{x.own && <> <Badge kind="pass">you</Badge></>}</td>
                  <td><Stars n={x.rating} /> <span className="num small">{x.rating ?? ""}</span></td><td className="r num">{x.review_count ?? "—"}</td>
                  <td className="small">{x.categories.join(", ") || "—"}</td><td className="r num">{x.photos_count}</td><td>{x.website ? "✓" : <span className="faint">—</span>}</td>
                  <td className="r num">{x.distance_km != null ? `${x.distance_km} km` : "—"}</td><td className="r num">{x.posts_per_month ?? "—"}</td>
                  <td className="r num" style={{ color: scoreColor(x.local_score), fontWeight: 600 }} title={Object.entries(x.components).map(([k, v]) => `${k}: ${v}`).join("\n")}>{x.local_score}</td></tr>)}</tbody></table></div>
              <p className="small muted" style={{ padding: "8px 16px 14px" }}>Local score (0–100) = rating weighted by review confidence (25) + review volume (20) + category relevance to the keyword (15) + photos (10) + website (10) + proximity (10) + posting cadence (10). Hover a score for its parts.</p>
            </Panel>
          )}
        </div>
      </>)}
    </>
  );
}
