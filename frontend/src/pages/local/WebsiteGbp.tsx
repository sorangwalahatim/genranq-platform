import { useEffect, useState } from "react";
import { Badge, Empty, PageHead, Panel, ScoreRing } from "../../components/ui";
import { api } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { fieldStatusKind, LocationPick, NoLocation, NoProject, ProjectPick, useLocal } from "./shared";

const LABEL: Record<string, string> = { name: "Business name", address: "Address", phone: "Phone", hours: "Opening hours", website: "Website URL",
  categories: "Categories vs page topics", schema: "LocalBusiness schema", schema_fields: "Schema fields match", maps: "Google Maps embed" };

const show = (v: any) => v == null || v === "" ? "—" : Array.isArray(v) ? (v.length && typeof v[0] === "object" ? v.map((c: any) => `${c.category}${c.on_page ? " ✓" : " ✕"}`).join(", ") : v.join(", "))
  : typeof v === "object" ? Object.entries(v).map(([d, s]: any) => `${d} ${s.map((x: string[]) => x.join("–")).join(", ")}`).join("; ") || "—" : String(v);

/** Module 22 — website ↔ GBP consistency audit. */
export default function WebsiteGbp() {
  const L = useLocal("own");
  const can = useCan();
  const { run, busy } = useAction();
  const [url, setUrl] = useState("");
  const [extra, setExtra] = useState("");
  const runs = useApi<any[]>(L.pid && L.lid ? `/local/runs?project_id=${L.pid}&kind=website_gbp&location_id=${L.lid}` : null, [L.pid, L.lid]);
  const [res, setRes] = useState<any>(null);
  useEffect(() => { setRes(null); const r = runs.data?.[0]; if (r) api(`/local/runs/${r.id}`).then(setRes).catch(() => {}); }, [runs.data]);
  useEffect(() => { setUrl(L.loc?.website || ""); }, [L.loc?.id]);
  const go = async () => {
    const r = await run(() => api(`/local/locations/${L.lid}/website-audit`, { body: { url: url || null, extra_urls: extra ? [extra] : [] } }), "Comparison complete");
    if (r) { setRes(r); runs.reload(); }
  };
  const r = res?.result;
  return (
    <>
      <PageHead title="Website ↔ GBP audit" sub="Fetch the website and check that name, address, phone, hours, categories and LocalBusiness schema match the Google Business Profile.">
        <ProjectPick {...L} /><LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} />
      </PageHead>
      <NoProject projects={L.projects} />
      {L.pid && L.locs.data && !L.locs.data.length ? <NoLocation /> : L.loc && (<>
        <Panel>
          <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>
            <input className="input" style={{ flex: 2, minWidth: 220 }} placeholder="https://example.com" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Website URL" />
            <input className="input" style={{ flex: 1, minWidth: 180 }} placeholder="Contact page URL (optional)" value={extra} onChange={(e) => setExtra(e.target.value)} aria-label="Contact page URL" />
            {can("create") && <button className="btn primary" disabled={busy || !url} onClick={go}>{busy ? "Fetching…" : "Compare"}</button>}
          </div>
        </Panel>
        <div style={{ marginTop: 16 }}>
          {!r ? <Panel><Empty title="Not compared yet">Run a comparison to check field-by-field consistency.</Empty></Panel> : (
            <div className="split lc-split">
              <Panel title={<>Field results <span className="faint small">· {r.url} · {ago(res.created_at)}</span></>} pad={false}>
                <div className="table-wrap"><table className="t"><thead><tr><th>Field</th><th>Result</th><th>GBP</th><th>Website</th></tr></thead>
                  <tbody>{r.fields.map((f: any) => <tr key={f.field}><td>{LABEL[f.field] || f.field}{f.note && <div className="faint small">{f.note}</div>}</td>
                    <td><Badge kind={fieldStatusKind(f.status)}>{f.status}</Badge></td><td className="small">{show(f.gbp)}</td><td className="small">{show(f.website)}</td></tr>)}</tbody></table></div>
              </Panel>
              <div className="stack">
                <Panel title="Consistency score"><div style={{ display: "flex", justifyContent: "center" }}><ScoreRing score={r.score} /></div>
                  <p className="small muted" style={{ marginTop: 8 }}>Schema types found: {r.page.schema_types.join(", ") || "none"}</p></Panel>
                {r.fixes.length > 0 && <Panel title="Fixes"><ul style={{ margin: 0, paddingLeft: 18 }}>{r.fixes.map((f: any) => <li key={f.field} className="small" style={{ marginBottom: 6 }}><b>{LABEL[f.field]}:</b> {f.fix}</li>)}</ul></Panel>}
              </div>
            </div>
          )}
        </div>
      </>)}
    </>
  );
}
