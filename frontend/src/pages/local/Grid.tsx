import { Fragment, useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { ago, fmtDate } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { CsvButton, Kpi, LocationPick, NcBanner, NoLocation, NoProject, ProjectPick, useLocal } from "./shared";

export const rankColor = (p: number | null | undefined) =>
  p == null ? "var(--line)" : p === 0 ? "var(--critical)" : p <= 3 ? "var(--pass)" : p <= 10 ? "var(--medium)" : "var(--high)";

/** SVG heatmap: one cell per grid point, north at the top, colored by Maps rank. */
export function Heatmap({ scan, compare }: { scan: any; compare?: any[] | null }) {
  const n = scan.grid_size, cell = 52, gap = 6, size = n * cell + (n - 1) * gap;
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="lc-heat" role="img" aria-label={`Rank heatmap ${n} by ${n}`}>
      {scan.points.map((p: any) => {
        const x = p.col * (cell + gap), y = p.row * (cell + gap);
        const delta = compare?.[p.idx]?.improvement;
        const centre = p.row === Math.floor(n / 2) && p.col === Math.floor(n / 2) && n % 2 === 1;
        return (
          <g key={p.idx}>
            <title>{`Row ${p.row + 1}, col ${p.col + 1} · ${p.lat.toFixed(5)}, ${p.lng.toFixed(5)} · ${p.position == null ? "not checked" : p.position === 0 ? "not in top 20" : "rank " + p.position}${p.top?.length ? " · top: " + p.top.join(", ") : ""}`}</title>
            <rect x={x} y={y} width={cell} height={cell} rx={cell / 2} fill={rankColor(p.position)} stroke={centre ? "var(--ink)" : "none"} strokeWidth={centre ? 3 : 0} />
            <text x={x + cell / 2} y={y + cell / 2 + 5} textAnchor="middle" fill={p.position == null ? "var(--muted)" : "#fff"}>{p.position == null ? "·" : p.position === 0 ? "20+" : p.position}</text>
            {delta != null && delta !== 0 && <text x={x + cell - 2} y={y + 11} textAnchor="end" style={{ fontSize: 10 }} fill={delta > 0 ? "var(--pass)" : "var(--critical)"}>{delta > 0 ? "▲" : "▼"}</text>}
          </g>
        );
      })}
    </svg>
  );
}

/** Module 20 — geo-grid rank tracking (live via Google Maps, or imported observations). */
export default function Grid() {
  const L = useLocal("own");
  const can = useCan();
  const scans = useApi<any[]>(L.pid && L.lid ? `/local/grid/scans?project_id=${L.pid}&location_id=${L.lid}` : null, [L.pid, L.lid]);
  const [sid, setSid] = useState("");
  const [cmpId, setCmpId] = useState("");
  const [creating, setCreating] = useState(false);
  useEffect(() => { if (scans.data?.length && !scans.data.find((s) => s.id === sid)) setSid(scans.data[0].id); if (!scans.data?.length) setSid(""); }, [scans.data]);
  const scan = useApi<any>(sid ? `/local/grid/scans/${sid}` : null, [sid], { poll: 4000 });
  const cmp = useApi<any>(sid && cmpId && cmpId !== sid ? `/local/grid/compare?a=${cmpId}&b=${sid}` : null, [sid, cmpId]);
  const s = scan.data;
  const { run } = useAction();
  return (
    <>
      <PageHead title="Rank grid (teleport)" sub="Where the business ranks in Google Maps across an N×N grid around the location: ARP, ATRP and share of local voice.">
        <ProjectPick {...L} /><LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} />
        {can("create") && <button className="btn primary" disabled={!L.lid} onClick={() => setCreating(true)}>New grid scan</button>}
      </PageHead>
      <NcBanner conn={L.status?.google_maps} provider="google_maps">Live scans need it. You can still create a grid, download the point template and import rank observations per point (CSV).</NcBanner>
      <NoProject projects={L.projects} />
      <ErrorBox error={scans.error || scan.error} />
      {L.pid && L.locs.data && !L.locs.data.length ? <NoLocation /> : scans.data && !scans.data.length ? (
        <Panel><Empty title="No grid scans yet" action={can("create") && <button className="btn primary" onClick={() => setCreating(true)}>New grid scan</button>}>
          Pick a keyword, grid size and radius. Points are placed with great-circle offsets from the location's coordinates.</Empty></Panel>
      ) : !s ? (sid ? <Loading /> : null) : (
        <div className="split lc-split">
          <div className="stack">
            <Panel title={<>“{s.keyword}” · {s.grid_size}×{s.grid_size} · {s.radius_km} km radius</>} actions={<Badge kind={s.status === "COMPLETED" ? "pass" : s.status === "FAILED" ? "fail" : s.status === "RUNNING" || s.status === "QUEUED" ? "RUNNING" : "pending"}>{s.status}</Badge>}>
              {s.error && <div className="notice warn" style={{ marginBottom: 10 }}>{s.error}</div>}
              <Heatmap scan={s} compare={cmp.data?.per_point} />
              <div className="lc-legend"><span><i style={{ background: "var(--pass)" }} />1–3</span><span><i style={{ background: "var(--medium)" }} />4–10</span>
                <span><i style={{ background: "var(--high)" }} />11–20</span><span><i style={{ background: "var(--critical)" }} />not in top 20</span><span><i style={{ background: "var(--line)" }} />not checked</span>
                {cmp.data && <span>▲▼ change vs comparison scan</span>}</div>
              {can("edit") && s.status !== "RUNNING" && s.status !== "QUEUED" && (
                <div className="row" style={{ marginTop: 14, flexWrap: "wrap" }}>
                  <button className="btn" onClick={() => download(`/local/grid/scans/${s.id}/template.csv`, `grid-${s.keyword}.csv`)}>Download point template</button>
                  <CsvButton path={`/local/grid/scans/${s.id}/import`} label="Import ranks CSV" onDone={() => { scan.reload(); scans.reload(); }}
                    hint="Columns: row, col, rank (or lat, lng, rank). Rank 1–20; blank = not checked; 20+ / x = not found." />
                  {can("delete") && <button className="btn danger" onClick={() => confirm("Delete this scan?") && run(() => api(`/local/grid/scans/${s.id}`, { method: "DELETE" }), "Scan deleted").then(() => scans.reload())}>Delete</button>}
                </div>
              )}
            </Panel>
          </div>
          <div className="stack">
            <div className="grid g2">
              <Kpi v={s.metrics.arp ?? "—"} l="ARP · avg rank where found" />
              <Kpi v={s.metrics.atrp ?? "—"} l="ATRP · not found = 21" />
              <Kpi v={s.metrics.solv != null ? `${s.metrics.solv}%` : "—"} l="SoLV · points in top 3" />
              <Kpi v={`${s.metrics.observed}/${s.metrics.points}`} l={`Points checked · ${s.metrics.found} found`} />
            </div>
            <Panel title="Compare with" >
              <select className="input" value={cmpId} onChange={(e) => setCmpId(e.target.value)} aria-label="Compare with scan">
                <option value="">No comparison</option>
                {(scans.data || []).filter((x) => x.id !== sid).map((x) => <option key={x.id} value={x.id}>{fmtDate(x.created_at)} · {x.keyword} · {x.grid_size}×{x.grid_size}</option>)}
              </select>
              {cmp.data && <dl className="kv" style={{ marginTop: 10 }}>
                {(["arp", "atrp", "solv", "top3"] as const).map((k) => { const v = cmp.data.deltas[k]; const good = v != null && ((k === "arp" || k === "atrp") ? v < 0 : v > 0);
                  return <Fragment key={k}><dt>{k.toUpperCase()} change</dt><dd style={{ color: v ? (good ? "var(--pass)" : "var(--critical)") : undefined }}>{v == null ? "—" : (v > 0 ? "+" : "") + v}</dd></Fragment>; })}
              </dl>}
              {cmp.data && !cmp.data.same_center && <p className="small muted">The scans have different centres — per-point changes are approximate.</p>}
            </Panel>
            <Panel title="Scan history" pad={false}>
              <table className="t"><tbody>{(scans.data || []).map((x) => <tr key={x.id} className="click" onClick={() => setSid(x.id)} style={x.id === sid ? { background: "var(--accent-soft)" } : undefined}>
                <td>{x.keyword}<div className="faint small">{ago(x.created_at)} · {x.mode}</div></td><td className="r num">{x.metrics.solv != null ? `${x.metrics.solv}%` : "—"}</td><td><Badge>{x.status}</Badge></td></tr>)}</tbody></table>
            </Panel>
          </div>
        </div>
      )}
      {creating && L.loc && <NewScan pid={L.pid} loc={L.loc} live={!!L.status?.google_maps.connected} onClose={() => setCreating(false)} onDone={(x: any) => { scans.reload(); setSid(x.id); }} />}
    </>
  );
}

function NewScan({ pid, loc, live, onClose, onDone }: any) {
  const [f, setF] = useState({ keyword: loc.keywords?.[0] || "", grid_size: 5, radius_km: 2, lat: loc.lat?.toString() || "", lng: loc.lng?.toString() || "", mode: live ? "live" : "import" });
  const { run, busy } = useAction();
  const ok = f.keyword.trim() && f.lat && f.lng;
  return (
    <Modal title="New grid scan" onClose={onClose} foot={<button className="btn primary" disabled={!ok || busy} onClick={async () => {
      const r = await run<any>(() => api("/local/grid/scans", { body: { project_id: pid, location_id: loc.id, keyword: f.keyword, grid_size: +f.grid_size, radius_km: +f.radius_km,
        center_lat: +f.lat, center_lng: +f.lng, mode: f.mode } }), f.mode === "live" ? "Scan queued" : "Grid created — import ranks per point");
      if (r) { onDone(r); onClose(); }
    }}>{f.mode === "live" ? `Run scan (${f.grid_size * f.grid_size} credits)` : "Create grid"}</button>}>
      <div className="form">
        <label className="field">Keyword<input className="input" value={f.keyword} onChange={(e) => setF({ ...f, keyword: e.target.value })} autoFocus /></label>
        <div className="grid g2">
          <label className="field">Grid size<select className="input" value={f.grid_size} onChange={(e) => setF({ ...f, grid_size: +e.target.value })}>{[3, 4, 5, 6, 7, 8, 9].map((n) => <option key={n} value={n}>{n}×{n} ({n * n} points)</option>)}</select></label>
          <label className="field">Radius (km)<span className="hint">centre to edge</span><input className="input" inputMode="decimal" value={f.radius_km} onChange={(e) => setF({ ...f, radius_km: e.target.value as any })} /></label>
        </div>
        <div className="grid g2">
          <label className="field">Centre latitude<input className="input" inputMode="decimal" value={f.lat} onChange={(e) => setF({ ...f, lat: e.target.value })} /></label>
          <label className="field">Centre longitude<input className="input" inputMode="decimal" value={f.lng} onChange={(e) => setF({ ...f, lng: e.target.value })} /></label>
        </div>
        <div className="seg" role="group" aria-label="Mode">
          <button className={f.mode === "live" ? "on" : ""} disabled={!live} title={live ? "" : "Google Maps NOT CONNECTED"} onClick={() => setF({ ...f, mode: "live" })}>Live (Google Maps)</button>
          <button className={f.mode === "import" ? "on" : ""} onClick={() => setF({ ...f, mode: "import" })}>Import observations</button>
        </div>
        <p className="small muted">Spacing between points: {f.grid_size > 1 ? ((2 * +f.radius_km) / (f.grid_size - 1)).toFixed(2) : "—"} km. Live scans use your plan's Local Rank Tracker credits (one per point).</p>
      </div>
    </Modal>
  );
}
