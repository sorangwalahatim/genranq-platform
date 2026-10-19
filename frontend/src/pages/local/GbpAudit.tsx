import { useEffect, useState } from "react";
import { Badge, CatBars, Empty, ErrorBox, Loading, PageHead, Panel, ScoreRing } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { LocationEditor } from "./LocationEditor";
import { CsvButton, Loc, LocationPick, NcBanner, NoProject, ProjectPick, useLocal } from "./shared";

/** Module 15 — GMB/GBP audit + location management. */
export default function GbpAudit() {
  const L = useLocal("own");
  const can = useCan();
  const { run, busy } = useAction();
  const [edit, setEdit] = useState<Loc | null | "new">(null);
  const runs = useApi<any[]>(L.pid && L.lid ? `/local/runs?project_id=${L.pid}&kind=gbp_audit&location_id=${L.lid}` : null, [L.pid, L.lid]);
  const [audit, setAudit] = useState<any>(null);
  useEffect(() => {
    setAudit(null);
    const last = runs.data?.[0];
    if (last) api(`/local/runs/${last.id}`).then(setAudit).catch(() => {});
  }, [runs.data]);
  const doAudit = async () => {
    const r = await run(() => api(`/local/locations/${L.lid}/gbp-audit`, { body: { check_maps: true } }), "Audit complete");
    if (r) { setAudit(r); runs.reload(); }
  };
  const sync = () => run(() => api(`/local/projects/${L.pid}/gbp-sync`, { body: {} }), "Sync queued — locations, reviews and posts will appear shortly");
  const res = audit?.result;
  return (
    <>
      <PageHead title="GBP audit" sub="Profile completeness, NAP consistency, same-address conflicts and knowledge-panel readiness — scored with weighted rules.">
        <ProjectPick {...L} />
        <LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} />
        {can("edit") && L.pid && <CsvButton path={`/local/projects/${L.pid}/import/locations`} label="Import locations CSV" onDone={() => L.locs.reload()}
          hint="Columns: name, address, city, region, postcode, phone, website, category, additional_categories (;-separated), lat, lng, photos, description" />}
        {can("edit") && <button className="btn" disabled={!L.pid || !L.status?.gbp.connected} onClick={sync}>Sync from GBP</button>}
        {can("create") && <button className="btn" disabled={!L.pid} onClick={() => setEdit("new")}>Add location</button>}
        {can("create") && <button className="btn primary" disabled={!L.lid || busy} onClick={doAudit}>{busy ? "Auditing…" : "Run audit"}</button>}
      </PageHead>
      <NcBanner conn={L.status?.gbp} provider="gbp">Profile data comes from manual entry and CSV import until it is connected; syncing locations, reviews and posts needs the connection.</NcBanner>
      <NoProject projects={L.projects} />
      <ErrorBox error={L.locs.error} />
      {L.pid && L.locs.loading && !L.locs.data ? <Loading /> : L.pid && !L.locs.data?.length ? (
        <Panel><Empty title="No business location yet" action={can("create") && <button className="btn primary" onClick={() => setEdit("new")}>Add location</button>}>
          Enter the profile manually, import a CSV, or connect Google Business Profile and sync.</Empty></Panel>
      ) : L.loc && (
        <div className="split lc-split">
          <div className="stack">
            {!res ? <Panel><Empty title="Not audited yet" action={can("create") && <button className="btn primary" onClick={doAudit}>Run audit</button>}>
              The audit scores {L.loc.name} against 18 weighted profile rules.</Empty></Panel> : (<>
              <Panel title={<>Score <span className="faint small">· {ago(audit.created_at)}</span></>}>
                <div className="row" style={{ gap: 28, alignItems: "center", flexWrap: "wrap" }}>
                  <ScoreRing score={res.score} />
                  <div style={{ flex: 1, minWidth: 220 }}><CatBars scores={res.groups} /></div>
                </div>
                {!res.maps_checked && <p className="muted small" style={{ marginTop: 10 }}>Same-address check used this project's listings only — Google Maps is not connected.</p>}
              </Panel>
              <Panel title={`Prioritised fixes (${res.fixes.length})`} pad={false}>
                {!res.fixes.length ? <Empty title="Nothing to fix">Every rule passes.</Empty> : (
                  <div className="table-wrap"><table className="t"><thead><tr><th>Priority</th><th>Rule</th><th>Fix</th><th className="r">Points</th></tr></thead>
                    <tbody>{res.fixes.map((f: any) => <tr key={f.key}><td><Badge kind={f.priority}>{f.priority}</Badge></td><td>{f.label}</td><td className="small">{f.fix}</td><td className="r num">+{f.points}</td></tr>)}</tbody></table></div>
                )}
              </Panel>
              <Panel title="All rules" pad={false}>
                <div className="table-wrap"><table className="t lc-rules"><thead><tr><th>Rule</th><th>Status</th><th>Found</th><th className="r">Score</th></tr></thead>
                  <tbody>{res.rules.map((r: any) => <tr key={r.key}><td>{r.label}<div className="faint small">{r.group}</div></td>
                    <td><Badge kind={r.status === "pass" ? "pass" : r.status === "partial" ? "partial" : "fail"}>{r.status}</Badge></td>
                    <td className="small">{r.detail}</td><td className="r num">{r.earned}/{r.weight}</td></tr>)}</tbody></table></div>
              </Panel>
              {res.nap.checks.length > 0 && (
                <Panel title={`NAP consistency · ${res.nap.consistency}%`} pad={false}>
                  <div className="table-wrap"><table className="t"><thead><tr><th>Source</th><th>Field</th><th>GBP</th><th>Source value</th><th>Result</th></tr></thead>
                    <tbody>{res.nap.checks.map((c: any, i: number) => <tr key={i}><td>{c.source}</td><td>{c.field}</td><td className="small">{c.gbp || "—"}</td><td className="small">{c.value}</td>
                      <td><Badge kind={c.result === "match" ? "pass" : c.result === "partial" ? "partial" : "fail"}>{c.result}</Badge></td></tr>)}</tbody></table></div>
                </Panel>
              )}
            </>)}
          </div>
          <div className="stack">
            <Panel title={L.loc.name} actions={can("edit") && <button className="btn sm" onClick={() => setEdit(L.loc)}>Edit profile</button>}>
              <dl className="kv">
                <dt>Address</dt><dd>{[L.loc.nap.address, L.loc.nap.city, L.loc.nap.postcode].filter(Boolean).join(", ") || "—"}</dd>
                <dt>Phone</dt><dd>{L.loc.nap.phone || "—"}</dd>
                <dt>Website</dt><dd className="mono-url">{L.loc.website || "—"}</dd>
                <dt>Primary category</dt><dd>{L.loc.primary_category || "—"}</dd>
                <dt>Rating</dt><dd>{L.loc.rating ?? "—"} ({L.loc.review_count ?? 0} reviews)</dd>
                <dt>Photos</dt><dd>{L.loc.photos_count}</dd>
                <dt>Source</dt><dd>{human(L.loc.source)}</dd>
              </dl>
            </Panel>
            {res && <Panel title={`Knowledge panel readiness · ${res.knowledge_panel.ready}/${res.knowledge_panel.total}`}>
              <ul className="lc-checklist">{res.knowledge_panel.items.map((k: any) => <li key={k.item}><span className={k.ok ? "ok" : "no"}>{k.ok ? "✓" : "✕"}</span>{k.item}</li>)}</ul>
            </Panel>}
            {res?.same_address?.length > 0 && <Panel title="Other listings at this address">
              <ul className="lc-checklist">{res.same_address.map((s: any, i: number) => <li key={i}><span className="no">!</span>{s.name} <span className="faint small">({s.source})</span></li>)}</ul>
            </Panel>}
            <Panel title="Audit history" pad={false}>
              {!runs.data?.length ? <p className="muted" style={{ padding: 16 }}>No runs yet.</p> : (
                <table className="t"><tbody>{runs.data.map((r) => <tr key={r.id} className="click" onClick={() => api(`/local/runs/${r.id}`).then(setAudit)}>
                  <td>{ago(r.created_at)}</td><td className="r num">{r.score}</td></tr>)}</tbody></table>
              )}
            </Panel>
          </div>
        </div>
      )}
      {edit && <LocationEditor pid={L.pid} kind="own" loc={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={(l) => { L.locs.reload(); L.setLid(l.id); }} />}
    </>
  );
}
