import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PageHead, Panel, Badge, Loading, ErrorBox, Empty } from "../../components/ui";
import { api } from "../../lib/api";
import { useApi, useAction } from "../../lib/hooks";
import { useProject, NoProjects, Bar } from "../backlinks/shared";
import "../backlinks/backlinks.css";

type AnchorRow = {
  anchor_text: string; anchor_type: string; ours: number;
  competitors: Record<string, number>; buckets: Record<string, number>; total: number;
};
type SiteSummary = { total: number; buckets: Record<string, number>; types: Record<string, number> };
type AnchorsData = {
  sites: string[]; rows: AnchorRow[];
  summary: Record<string, SiteSummary>;
  warnings: { site_label: string; exact_pct: number; message: string }[];
};

const BUCKETS = ["0-20", "20-40", "40-60", "60+"];
const TYPE_LABEL: Record<string, string> = {
  branded: "Brand name", exact: "Exact keyword", partial: "Part of keyword", generic: "Generic word",
};
const TYPE_KIND: Record<string, string> = { branded: "live", exact: "high", partial: "medium", generic: "" };
const siteName = (s: string) => (s === "ours" ? "Your site" : s);

/** CSV import widget: site picker + brand + keywords + file, POSTs JSON {project_id, site_label, brand, keywords[], csv}. */
function ImportPanel({ pid, onDone }: { pid: string; onDone: () => void }) {
  const { run, busy } = useAction();
  const ref = useRef<HTMLInputElement>(null);
  const [site, setSite] = useState("ours");
  const [custom, setCustom] = useState("");
  const [brand, setBrand] = useState("");
  const [keywords, setKeywords] = useState("");
  const pick = async (f: File) => {
    const text = await f.text();
    const site_label = site === "ours" ? "ours" : custom.trim();
    if (!site_label) return;
    const r = await run(() => api("/linksintel/backlinks/import", {
      body: {
        project_id: pid, site_label,
        brand: brand.trim() || null,
        keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
        csv: text,
      },
    }), "Backlinks imported");
    if (ref.current) ref.current.value = "";
    if (r !== undefined) onDone();
  };
  return (
    <Panel title="Import backlinks">
      <div className="bl-toolbar" style={{ border: 0, padding: "0 0 12px" }}>
        <label>Site:&nbsp;
          <select value={site} onChange={(e) => setSite(e.target.value)}>
            <option value="ours">Your site</option>
            <option value="competitor">Competitor</option>
          </select>
        </label>
        {site === "competitor" && (
          <input placeholder="competitor domain, e.g. rival.com" value={custom} onChange={(e) => setCustom(e.target.value)} style={{ minWidth: 220 }} />
        )}
        <input placeholder="Your brand name (e.g. BrightDental)" value={brand} onChange={(e) => setBrand(e.target.value)} style={{ minWidth: 200 }} />
        <input placeholder="Target keywords, comma separated" value={keywords} onChange={(e) => setKeywords(e.target.value)} style={{ minWidth: 240 }} />
        <input ref={ref} type="file" accept=".csv,.txt" hidden onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
        <button className="btn primary" disabled={busy} onClick={() => ref.current?.click()}>
          {busy ? "Importing…" : "Choose CSV file"}
        </button>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--muted)" }}>
        CSV columns: <b>anchor_text</b>, <b>backlink_url</b> (optional), <b>dr</b> (optional). Uploading again replaces this site’s previous import.
      </div>
    </Panel>
  );
}

export default function AnchorAnalysis() {
  const { pid, picker } = useProject();
  const { data, error, loading, reload } = useApi<AnchorsData>(
    pid ? `/linksintel/anchors?project_id=${pid}` : null, [pid]);
  return (
    <div className="stack">
      <PageHead title="Links Intel" sub="Which words other sites use to link to you — and to your competitors. Your site always first.">
        {picker}
      </PageHead>
      <div className="tabs" role="tablist">
        <Link role="tab" aria-selected="true" className="on" to="/links-intel">Anchor analysis</Link>
        <Link role="tab" aria-selected="false" to="/links-intel/internal">Internal links</Link>
      </div>
      {!pid ? <NoProjects /> : (
        <>
          <ImportPanel pid={pid} onDone={reload} />
          {loading && <Loading />}
          {error && <ErrorBox error={error} />}
          {data && !data.rows.length && (
            <Empty title="No backlink data yet">Import a backlink CSV above — for your site and each competitor — to see the us-vs-them anchor table.</Empty>
          )}
          {data && !!data.rows.length && (
            <>
              <div className="bl-kpis">
                {data.sites.map((s) => {
                  const sm = data.summary[s];
                  return (
                    <div className="panel kpi" key={s} style={{ gridColumn: "span 2" }}>
                      <div className="v">{sm.total}</div>
                      <div className="l">{siteName(s)} — total links</div>
                      <div style={{ marginTop: 8 }}>
                        {Object.entries(sm.types).map(([t, n]) => n > 0 && (
                          <Badge key={t} kind={TYPE_KIND[t]}>{TYPE_LABEL[t]}: {n}</Badge>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
              {!!data.warnings.length && (
                <Panel title="Warnings — fix these">
                  {data.warnings.map((w, i) => (
                    <div key={i} className="bl-quote" style={{ borderLeftColor: "var(--critical)" }}>
                      <b>{siteName(w.site_label)}:</b> {w.message}
                    </div>
                  ))}
                </Panel>
              )}
              <Panel title="Anchor text — us vs them">
                <div style={{ overflowX: "auto" }}>
                  <table className="bl-cmp">
                    <thead>
                      <tr>
                        <th>Anchor text</th>
                        <th>Type</th>
                        <th>Your site</th>
                        {data.sites.filter((s) => s !== "ours").map((s) => <th key={s}>{s}</th>)}
                        <th>Strong sites (DR 60+)</th>
                        <th>Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.rows.map((r) => {
                        const maxB = Math.max(1, ...BUCKETS.map((b) => r.buckets[b] || 0));
                        return (
                          <tr key={r.anchor_text}>
                            <td>{r.anchor_text}</td>
                            <td><Badge kind={TYPE_KIND[r.anchor_type]}>{TYPE_LABEL[r.anchor_type] || r.anchor_type}</Badge></td>
                            <td className="ours"><b>{r.ours}</b></td>
                            {data.sites.filter((s) => s !== "ours").map((s) => <td key={s}>{r.competitors[s] || 0}</td>)}
                            <td><Bar value={r.buckets["60+"] || 0} max={maxB} /></td>
                            <td><b>{r.total}</b></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 8 }}>
                  DR = how strong the linking site is (0–100). The bar shows links from the strongest sites (DR 60+).
                </div>
              </Panel>
            </>
          )}
        </>
      )}
    </div>
  );
}
