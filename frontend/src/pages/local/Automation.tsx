import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Loc, NoLocation, NoProject, ProjectPick, useLocal } from "./shared";

const VARS = ["location_name", "city", "region", "postcode", "phone", "website", "address", "primary_category", "month", "year", "date"];

/** Module 35 — GMB automation: one template × many locations, recurring, posts land in review for approval. */
export default function Automation() {
  const L = useLocal("own");
  const can = useCan();
  const list = useApi<any[]>(L.pid ? `/local/automations?project_id=${L.pid}` : null, [L.pid]);
  const [edit, setEdit] = useState<any>(null);
  const { run } = useAction();
  return (
    <>
      <PageHead title="GMB automation" sub="Bulk-create posts across locations from a template with per-location variables, on a weekly or monthly schedule. Created posts wait for approval.">
        <ProjectPick {...L} />
        {can("create") && <button className="btn primary" disabled={!L.locs.data?.length} onClick={() => setEdit({})}>New automation</button>}
      </PageHead>
      <NoProject projects={L.projects} />
      <ErrorBox error={list.error} />
      {L.pid && L.locs.data && !L.locs.data.length ? <NoLocation /> : list.loading && !list.data ? <Loading /> : !list.data?.length ? (
        <Panel><Empty title="No automations" action={can("create") && L.locs.data?.length ? <button className="btn primary" onClick={() => setEdit({})}>New automation</button> : undefined}>
          Example: a weekly tip post for every branch, personalised with {"{location_name}"} and {"{city}"}.</Empty></Panel>
      ) : (
        <Panel pad={false}>
          <div className="table-wrap"><table className="t"><thead><tr><th>Automation</th><th>Type</th><th>Schedule</th><th className="r">Locations</th><th className="r">Posts</th><th className="r">Awaiting approval</th><th>Last run</th><th></th></tr></thead>
            <tbody>{list.data.map((a) => <tr key={a.id}>
              <td><b>{a.name}</b><div className="faint small" style={{ maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.body_template}</div></td>
              <td>{human(a.post_type)}</td>
              <td>{a.enabled ? <Badge kind="pass">{a.frequency}</Badge> : <Badge>paused</Badge>}{a.next_run_at && <div className="faint small">next {fmtDateTime(a.next_run_at)}</div>}</td>
              <td className="r num">{a.location_ids.length}</td><td className="r num">{a.posts_created}</td>
              <td className="r num">{a.posts_pending ? <Link to="/local/gmb-posts">{a.posts_pending}</Link> : 0}</td><td className="small">{ago(a.last_run_at)}</td>
              <td className="nowrap">
                {can("create") && a.enabled && <button className="btn ghost sm" onClick={() => run(() => api(`/local/automations/${a.id}/run`, { body: {} }), "Run queued — posts will appear in GMB posts for review").then(list.reload)}>Run now</button>}
                {can("edit") && <button className="btn ghost sm" onClick={() => run(() => api(`/local/automations/${a.id}`, { method: "PATCH", body: { enabled: !a.enabled } })).then(list.reload)}>{a.enabled ? "Pause" : "Resume"}</button>}
                {can("edit") && <button className="btn ghost sm" onClick={() => setEdit(a)}>Edit</button>}
                {can("delete") && <button className="btn ghost sm danger" onClick={() => confirm(`Delete ${a.name}?`) && run(() => api(`/local/automations/${a.id}`, { method: "DELETE" }), "Deleted").then(list.reload)}>Delete</button>}
              </td></tr>)}</tbody></table></div>
        </Panel>
      )}
      {edit && L.locs.data && <Editor a={edit} pid={L.pid} locs={L.locs.data} onClose={() => setEdit(null)} onDone={list.reload} />}
    </>
  );
}

function Editor({ a, pid, locs, onClose, onDone }: { a: any; pid: string; locs: Loc[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: a.name || "", post_type: a.post_type || "update", title_template: a.title_template || "", body_template: a.body_template || "",
    cta_type: a.cta_type || "", cta_url_template: a.cta_url_template || "", image_ref: a.image_ref || "", frequency: a.frequency || "weekly", start_at: "",
    location_ids: (a.location_ids || locs.map((l) => l.id)) as string[], variables: (a.variables || {}) as Record<string, Record<string, string>> });
  const [preview, setPreview] = useState<any[] | null>(null);
  const [varName, setVarName] = useState("");
  const { run, busy } = useAction();
  const custom = Array.from(new Set(Object.values(f.variables).flatMap((v) => Object.keys(v))));
  const body = () => ({ ...f, project_id: pid, title_template: f.title_template || null, cta_type: f.cta_type || null, cta_url_template: f.cta_url_template || null,
    image_ref: f.image_ref || null, start_at: f.start_at ? new Date(f.start_at).toISOString() : null });
  const setVar = (lid: string, k: string, v: string) => setF({ ...f, variables: { ...f.variables, [lid]: { ...(f.variables[lid] || {}), [k]: v } } });
  const save = async () => {
    const b: any = body();
    const r = await run(() => a.id ? api(`/local/automations/${a.id}`, { method: "PATCH", body: { ...b, project_id: undefined } }) : api("/local/automations", { body: b }),
      f.frequency === "once" ? "Saved — posts are being created for review" : "Automation scheduled");
    if (r) { onDone(); onClose(); }
  };
  return (
    <Modal wide title={a.id ? `Edit ${a.name}` : "New automation"} onClose={onClose} foot={<>
      <button className="btn" disabled={busy || !f.body_template} onClick={async () => setPreview((await run<any[]>(() => api("/local/automations/preview", { body: body() }))) || null)}>Preview</button>
      <button className="btn primary" disabled={busy || !f.name || !f.body_template || !f.location_ids.length} onClick={save}>Save</button></>}>
      <div className="form">
        <div className="grid g3">
          <label className="field">Name<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></label>
          <label className="field">Post type<select className="input" value={f.post_type} onChange={(e) => setF({ ...f, post_type: e.target.value })}>{["update", "offer", "event"].map((t) => <option key={t}>{t}</option>)}</select></label>
          <label className="field">Schedule<select className="input" value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value })}>
            <option value="once">Once (now)</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></label>
        </div>
        {f.frequency !== "once" && <label className="field">First run<span className="hint">leave empty to start now</span><input className="input" type="datetime-local" value={f.start_at} onChange={(e) => setF({ ...f, start_at: e.target.value })} /></label>}
        <label className="field">Title template<input className="input" value={f.title_template} onChange={(e) => setF({ ...f, title_template: e.target.value })} placeholder="{location_name}: spring offer" /></label>
        <label className="field">Body template<span className="hint">Variables: {VARS.map((v) => `{${v}}`).join(" ")} {custom.map((v) => `{${v}}`).join(" ")}</span>
          <textarea className="input" rows={5} value={f.body_template} onChange={(e) => setF({ ...f, body_template: e.target.value })} /></label>
        <div className="grid g3">
          <label className="field">Button<select className="input" value={f.cta_type} onChange={(e) => setF({ ...f, cta_type: e.target.value })}>{["", "BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL"].map((c) => <option key={c} value={c}>{c ? human(c.toLowerCase()) : "None"}</option>)}</select></label>
          <label className="field">Button link template<input className="input" value={f.cta_url_template} onChange={(e) => setF({ ...f, cta_url_template: e.target.value })} placeholder="https://site/{city}" /></label>
          <label className="field">Image URL<input className="input" value={f.image_ref} onChange={(e) => setF({ ...f, image_ref: e.target.value })} /></label>
        </div>
        <div className="field">Locations & per-location variables
          <div className="row" style={{ gap: 6, margin: "6px 0" }}><input className="input" style={{ width: 200 }} placeholder="new variable, e.g. promo" value={varName} onChange={(e) => setVarName(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} aria-label="New variable" />
            <button className="btn sm" disabled={!varName} onClick={() => { const v: any = { ...f.variables }; f.location_ids.forEach((l) => { v[l] = { ...(v[l] || {}), [varName]: v[l]?.[varName] || "" }; }); setF({ ...f, variables: v }); setVarName(""); }}>Add variable</button></div>
          <div className="table-wrap"><table className="t"><thead><tr><th></th><th>Location</th><th>City</th>{custom.map((c) => <th key={c}>{`{${c}}`}</th>)}</tr></thead>
            <tbody>{locs.map((l) => { const on = f.location_ids.includes(l.id); return <tr key={l.id}>
              <td><input type="checkbox" aria-label={`Include ${l.name}`} checked={on} onChange={() => setF({ ...f, location_ids: on ? f.location_ids.filter((x) => x !== l.id) : [...f.location_ids, l.id] })} /></td>
              <td>{l.name}</td><td className="small">{l.nap?.city || <span className="faint">—</span>}</td>
              {custom.map((c) => <td key={c}><input className="input" style={{ minWidth: 120 }} disabled={!on} value={f.variables[l.id]?.[c] || ""} onChange={(e) => setVar(l.id, c, e.target.value)} aria-label={`${c} for ${l.name}`} /></td>)}</tr>; })}</tbody></table></div>
        </div>
        {preview && <Panel title="Preview" pad={false}>{preview.map((p) => <div key={p.location_id} className="lc-review">
          <b>{p.location_name}</b> {p.missing_vars?.length > 0 && <Badge kind="fail">missing: {p.missing_vars.join(", ")} — will be skipped</Badge>}{p.too_long && <Badge kind="fail">too long</Badge>}
          {p.title && <div><b>{p.title}</b></div>}<div className="lc-post-body">{p.body}</div>{p.cta_url && <div className="small muted">{p.cta_url}</div>}</div>)}</Panel>}
        <p className="small muted">Each run creates one post per location in <b>review</b> state with an approval request — nothing is published automatically.</p>
      </div>
    </Modal>
  );
}
