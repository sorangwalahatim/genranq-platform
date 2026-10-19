import { useEffect, useState } from "react";
import { Badge, Empty, ErrorBox, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { LocationPick, NoLocation, NoProject, ProjectPick, useLocal } from "./shared";

/** Module 16 — category finder: catalogue matching + competitor category frequency. */
export default function Categories() {
  const L = useLocal("own");
  const can = useCan();
  const { run, busy } = useAction();
  const [terms, setTerms] = useState("");
  const [res, setRes] = useState<any>(null);
  const [pick, setPick] = useState<{ primary: string; secondary: string[] }>({ primary: "", secondary: [] });
  const [q, setQ] = useState("");
  const cat = useApi<any>(`/local/categories/catalogue?limit=40${q ? `&q=${encodeURIComponent(q)}` : ""}`, [q]);
  const runs = useApi<any[]>(L.pid && L.lid ? `/local/runs?project_id=${L.pid}&kind=categories&location_id=${L.lid}` : null, [L.pid, L.lid]);
  useEffect(() => { setRes(null); const r = runs.data?.[0]; if (r) api(`/local/runs/${r.id}`).then(setRes).catch(() => {}); }, [runs.data]);
  useEffect(() => { if (L.loc) setPick({ primary: L.loc.primary_category || "", secondary: L.loc.secondary_categories || [] }); }, [L.loc?.id]);
  const find = async () => {
    const r = await run(() => api(`/local/locations/${L.lid}/categories`, { body: { terms: terms.split(/\n|,/).map((x) => x.trim()).filter(Boolean) } }));
    if (r) { setRes(r); runs.reload(); }
  };
  const apply = async () => { await run(() => api(`/local/locations/${L.lid}/categories/apply`, { body: pick }), "Categories saved to the profile"); L.locs.reload(); };
  const toggle = (name: string) => setPick((p) => p.secondary.includes(name) ? { ...p, secondary: p.secondary.filter((x) => x !== name) } : { ...p, secondary: [...p.secondary, name].slice(0, 9) });
  const r = res?.result;
  return (
    <>
      <PageHead title="Category finder" sub="Primary and secondary GBP category recommendations from your services and keywords, plus what top competitors use.">
        <ProjectPick {...L} /><LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} />
      </PageHead>
      <NoProject projects={L.projects} />
      {L.pid && L.locs.data && !L.locs.data.length ? <NoLocation /> : L.loc && (
        <div className="split lc-split">
          <div className="stack">
            <Panel title="Describe the business">
              <div className="form">
                <label className="field">Extra services / keywords<span className="hint">One per line. The profile's services, products, keywords and description are always included.</span>
                  <textarea className="input" rows={4} value={terms} onChange={(e) => setTerms(e.target.value)} placeholder={"emergency plumber\nblocked drains"} /></label>
                <div className="row"><button className="btn primary" disabled={busy || !can("create")} onClick={find}>{busy ? "Matching…" : "Find categories"}</button>
                  {res && <span className="muted small">Last run {ago(res.created_at)} · {r.competitor_count} competitor listing(s) compared</span>}</div>
              </div>
            </Panel>
            <ErrorBox error={runs.error} />
            {r && (<>
              {r.primary && <Panel title="Recommended primary category">
                <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
                  <div><h2 style={{ fontSize: 20 }}>{r.primary.name}</h2><div className="muted small">{r.primary.group}{r.primary.used_by_label ? ` · ${r.primary.used_by_label}` : ""}</div>
                    {r.primary.matched_terms.length > 0 && <div className="small">Matches: {r.primary.matched_terms.join(", ")}</div>}</div>
                  {can("edit") && <button className="btn" onClick={() => setPick({ ...pick, primary: r.primary.name })}>Use as primary</button>}
                </div>
              </Panel>}
              <Panel title="Secondary suggestions" pad={false}>
                {!r.secondary.length ? <Empty title="No further matches" /> : (
                  <div className="table-wrap"><table className="t"><thead><tr><th></th><th>Category</th><th>Why</th><th>Competitors</th><th className="r">Score</th></tr></thead>
                    <tbody>{r.secondary.map((s: any) => <tr key={s.name}>
                      <td><input type="checkbox" aria-label={`Select ${s.name}`} checked={pick.secondary.includes(s.name)} onChange={() => toggle(s.name)} disabled={!can("edit") || s.name === pick.primary} /></td>
                      <td>{s.name} {s.current && <Badge kind="pass">current</Badge>}<div className="faint small">{s.group}</div></td>
                      <td className="small">{s.matched_terms.join(", ") || "—"}</td><td className="small">{s.used_by_label || "—"}</td><td className="r num">{s.score}</td></tr>)}</tbody></table></div>
                )}
              </Panel>
              {r.missing_common.length > 0 && <Panel title="Used by most competitors but not by you">
                {r.missing_common.map((s: any) => <span key={s.name} className="lc-chip">{s.name} · {s.used_by}/{s.competitors}</span>)}
              </Panel>}
            </>)}
          </div>
          <div className="stack">
            <Panel title="Profile categories" actions={can("edit") && <button className="btn sm primary" disabled={busy} onClick={apply}>Save to profile</button>}>
              <dl className="kv"><dt>Primary</dt><dd>{pick.primary || <span className="faint">not set</span>}</dd>
                <dt>Secondary</dt><dd>{pick.secondary.length ? pick.secondary.map((s) => <span key={s} className="lc-chip on" onClick={() => toggle(s)} role="button" title="Remove">{s} ✕</span>) : <span className="faint">none</span>}</dd></dl>
              <p className="muted small" style={{ marginTop: 10 }}>Update the categories in Google Business Profile too — saving here records them for audits.</p>
            </Panel>
            {r?.competitor_frequency?.length > 0 && <Panel title="Competitor category frequency">
              <div className="lc-bars">{r.competitor_frequency.map((c: any) => <div key={c.category} className="lc-bar" style={{ gridTemplateColumns: "1fr 90px 30px" }}>
                <span className="small">{c.category}</span><div className="track"><div className="fill" style={{ width: `${(100 * c.used_by) / r.competitor_count}%` }} /></div><span className="num small">{c.used_by}</span></div>)}</div>
            </Panel>}
            <Panel title={`Category catalogue (${cat.data?.total ?? "…"})`}>
              <input className="input" placeholder="Search categories" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search categories" />
              <div style={{ marginTop: 8, maxHeight: 280, overflow: "auto" }}>{(cat.data?.items || []).map((c: any) => (
                <div key={c.name} className="row" style={{ justifyContent: "space-between", padding: "4px 0" }}><span className="small">{c.name} <span className="faint">· {c.group}</span></span>
                  {can("edit") && <button className="btn ghost sm" onClick={() => toggle(c.name)}>{pick.secondary.includes(c.name) ? "Remove" : "Add"}</button>}</div>))}</div>
            </Panel>
          </div>
        </div>
      )}
    </>
  );
}
