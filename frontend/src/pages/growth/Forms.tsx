import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Code, Copy, LeadsNav, Status, Tabs, useProjects } from "./shared";

export default function Forms() {
  const { projects, pid, picker } = useProjects();
  const { data, error, loading, reload } = useApi<any[]>(pid ? `/forms?project_id=${pid}` : null, [pid]);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const can = useCan();
  return (
    <>
      <PageHead title="Form builder" sub="Contact and custom forms with validation, spam protection and embeds. Submissions become deduplicated, attributed leads.">
        {picker}{can("create") && <button className="btn primary" disabled={!pid} onClick={() => setCreating(true)}>New form</button>}
      </PageHead>
      <LeadsNav />
      {!projects ? <Loading /> : !projects.length ? <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>} /></Panel> : (
        <Panel pad={false} title="Forms">
          <ErrorBox error={error} />
          {loading && !data ? <Loading /> : !data?.length ? <Empty title="No forms yet" action={can("create") && <button className="btn primary" onClick={() => setCreating(true)}>Create a contact form</button>}>Start from the contact template or build a custom form.</Empty> : (
            <div className="table-wrap"><table className="t"><thead><tr><th>Form</th><th>Status</th><th className="r">Submissions</th><th className="r">Leads</th><th className="r">Spam blocked</th><th>Public page</th><th>Updated</th></tr></thead>
              <tbody>{data.map((f) => (
                <tr key={f.id} className="click" onClick={() => setOpen(f.id)}>
                  <td><b>{f.name}</b><div className="faint small">{human(f.kind)} · {f.schema.fields.length} fields</div></td><td><Status s={f.status} /></td>
                  <td className="r num">{f.submissions}</td><td className="r num">{f.leads}</td><td className="r num">{f.spam_blocked}</td>
                  <td><a href={`/f/${f.public_token}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>Open ↗</a></td><td className="muted small">{ago(f.updated_at)}</td>
                </tr>))}</tbody></table></div>
          )}
        </Panel>
      )}
      {creating && <CreateForm pid={pid} onClose={() => setCreating(false)} onDone={(id: string) => { reload(); setOpen(id); }} />}
      {open && <FormEditor id={open} onClose={() => setOpen(null)} onChange={reload} />}
    </>
  );
}

function CreateForm({ pid, onClose, onDone }: any) {
  const [name, setName] = useState("Contact us");
  const [kind, setKind] = useState("contact");
  const { run, busy } = useAction();
  return (
    <Modal title="New form" onClose={onClose} foot={<button className="btn primary" disabled={busy || !name} onClick={() => run<any>(() => api("/forms", { body: { project_id: pid, name, kind } }), "Form created").then((f) => { if (f) { onClose(); onDone(f.id); } })}>Create</button>}>
      <div className="form">
        <label className="field">Name<input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></label>
        <div className="seg"><button className={kind === "contact" ? "on" : ""} onClick={() => setKind("contact")}>Contact form template</button><button className={kind === "custom" ? "on" : ""} onClick={() => setKind("custom")}>Custom (start with email)</button></div>
      </div>
    </Modal>
  );
}

const TYPES = ["text", "email", "phone", "select", "checkbox", "textarea", "hidden"];
const MAPS = ["", "email", "phone", "name", "first_name", "last_name", "company", "value", "notes"];

function FormEditor({ id, onClose, onChange }: any) {
  const { data: f, error, reload } = useApi<any>(`/forms/${id}`, [id]);
  const { data: meta } = useApi<any>("/leads/meta");
  const { data: qs } = useApi<any[]>("/onboarding/questionnaires");
  const [tab, setTab] = useState("fields");
  const [schema, setSchema] = useState<any>(null);
  const [settings, setSettings] = useState<any>(null);
  const [name, setName] = useState("");
  const { run, busy } = useAction();
  const can = useCan();
  useEffect(() => { if (f) { setSchema(JSON.parse(JSON.stringify(f.schema))); setSettings(JSON.parse(JSON.stringify(f.settings))); setName(f.name); } }, [f]);
  if (error) return <Drawer title="Form" onClose={onClose}><ErrorBox error={error} /></Drawer>;
  if (!f || !schema || !settings) return <Drawer title="Form" onClose={onClose}><Loading /></Drawer>;
  const save = () => run(() => api(`/forms/${id}`, { method: "PATCH", body: { name, schema, settings } }), "Form saved").then(() => { reload(); onChange(); });
  const upd = (i: number, k: string, v: any) => { const fs = [...schema.fields]; fs[i] = { ...fs[i], [k]: v }; setSchema({ ...schema, fields: fs }); };
  const moveF = (i: number, d: number) => { const fs = [...schema.fields]; const j = i + d; if (j < 0 || j >= fs.length) return; [fs[i], fs[j]] = [fs[j], fs[i]]; setSchema({ ...schema, fields: fs }); };
  const spam = settings.spam;
  return (
    <Drawer title={f.name} onClose={onClose} foot={can("edit") ? <div className="row">
      <button className="btn primary" disabled={busy} onClick={save}>Save changes</button>
      <button className="btn" onClick={() => run(() => api(`/forms/${id}`, { method: "PATCH", body: { status: f.status === "active" ? "paused" : "active" } })).then(() => { reload(); onChange(); })}>{f.status === "active" ? "Pause form" : "Activate form"}</button>
      {can("delete") && <button className="btn danger" onClick={() => confirm("Delete this form and its submissions?") && run(() => api(`/forms/${id}`, { method: "DELETE" }), "Form deleted").then(() => { onChange(); onClose(); })}>Delete</button>}
    </div> : undefined}>
      <Tabs value={tab} onChange={setTab} tabs={[["fields", "Fields"], ["settings", "Spam & routing"], ["embed", "Embed"], ["subs", `Submissions (${f.submissions})`]]} />
      {tab === "fields" && (<div className="form">
        <label className="field">Form name<input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <div>
          <div className="gq-field-row small muted"><span>Key</span><span>Label</span><span>Type</span><span>Maps to lead</span><span>Req.</span><span /></div>
          {schema.fields.map((fd: any, i: number) => (
            <div key={i} className="gq-field-row">
              <input className="input" value={fd.key} onChange={(e) => upd(i, "key", e.target.value)} aria-label="Field key" />
              <input className="input" value={fd.label} onChange={(e) => upd(i, "label", e.target.value)} aria-label="Label" />
              <select className="input" value={fd.type} onChange={(e) => upd(i, "type", e.target.value)} aria-label="Type">{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
              <select className="input" value={fd.maps_to || ""} onChange={(e) => upd(i, "maps_to", e.target.value || null)} aria-label="Maps to">{MAPS.map((t) => <option key={t} value={t}>{t || "custom field"}</option>)}</select>
              <input type="checkbox" checked={!!fd.required} disabled={fd.type === "hidden"} onChange={(e) => upd(i, "required", e.target.checked)} aria-label="Required" />
              <div className="row" style={{ gap: 2 }}><button className="btn ghost sm" onClick={() => moveF(i, -1)} aria-label="Move up">↑</button><button className="btn ghost sm" onClick={() => moveF(i, 1)} aria-label="Move down">↓</button>
                <button className="btn ghost sm" onClick={() => setSchema({ ...schema, fields: schema.fields.filter((_: any, j: number) => j !== i) })} aria-label="Remove">✕</button></div>
              {fd.type === "select" || (fd.type === "checkbox" && fd.options?.length) ? <input className="input" style={{ gridColumn: "1 / -1" }} placeholder="Options, comma separated" value={(fd.options || []).join(", ")} onChange={(e) => upd(i, "options", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} /> : null}
              {["text", "textarea"].includes(fd.type) && <div className="row" style={{ gridColumn: "1 / -1", gap: 8 }}>
                <input className="input" style={{ width: 110 }} placeholder="Min length" inputMode="numeric" value={fd.validation?.min_length ?? ""} onChange={(e) => upd(i, "validation", { ...fd.validation, min_length: e.target.value ? Number(e.target.value) : undefined })} />
                <input className="input" style={{ width: 110 }} placeholder="Max length" inputMode="numeric" value={fd.validation?.max_length ?? ""} onChange={(e) => upd(i, "validation", { ...fd.validation, max_length: e.target.value ? Number(e.target.value) : undefined })} />
                <input className="input" style={{ flex: 1 }} placeholder="Regex pattern (optional)" value={fd.validation?.pattern ?? ""} onChange={(e) => upd(i, "validation", { ...fd.validation, pattern: e.target.value || undefined })} />
              </div>}
            </div>
          ))}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn sm" onClick={() => setSchema({ ...schema, fields: [...schema.fields, { key: `field_${schema.fields.length + 1}`, label: "New field", type: "text", required: false }] })}>Add field</button>
            <button className="btn sm" onClick={() => { const have = new Set(schema.fields.map((x: any) => x.key)); setSchema({ ...schema, fields: [...schema.fields, ...["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"].filter((k) => !have.has(k)).map((k) => ({ key: k, label: k, type: "hidden" }))] }); }}>Add hidden UTM fields</button>
          </div>
        </div>
        <div className="grid g2"><label className="field">Submit button<input className="input" value={schema.submit_label} onChange={(e) => setSchema({ ...schema, submit_label: e.target.value })} /></label>
          <label className="field">Redirect URL after submit<input className="input" value={schema.redirect_url || ""} onChange={(e) => setSchema({ ...schema, redirect_url: e.target.value || null })} placeholder="optional https://…" /></label></div>
        <label className="field">Success message<input className="input" value={schema.success_message} onChange={(e) => setSchema({ ...schema, success_message: e.target.value })} /></label>
      </div>)}
      {tab === "settings" && (<div className="form">
        <label className="check"><input type="checkbox" checked={spam.honeypot} onChange={(e) => setSettings({ ...settings, spam: { ...spam, honeypot: e.target.checked } })} /> Hidden honeypot field (bots that fill it are silently dropped)</label>
        <div className="grid g3">
          <label className="field">Minimum seconds before submit<input className="input" inputMode="numeric" value={spam.min_seconds} onChange={(e) => setSettings({ ...settings, spam: { ...spam, min_seconds: Number(e.target.value) || 0 } })} /></label>
          <label className="field">Submissions per IP<input className="input" inputMode="numeric" value={spam.rate_limit} onChange={(e) => setSettings({ ...settings, spam: { ...spam, rate_limit: Number(e.target.value) || 1 } })} /></label>
          <label className="field">…per minutes<input className="input" inputMode="numeric" value={spam.rate_window_min} onChange={(e) => setSettings({ ...settings, spam: { ...spam, rate_window_min: Number(e.target.value) || 1 } })} /></label>
        </div>
        <label className="field">Blocked words<span className="hint">Comma separated; submissions containing any are rejected.</span>
          <input className="input" value={spam.blocked_words.join(", ")} onChange={(e) => setSettings({ ...settings, spam: { ...spam, blocked_words: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } })} /></label>
        <fieldset className="panel" style={{ padding: 12 }}><legend className="small muted">Assign new leads (round-robin) and notify</legend>
          {(meta?.users || []).map((u: any) => <label key={u.id} className="check"><input type="checkbox" checked={settings.assignee_ids.includes(u.id)} onChange={(e) => setSettings({ ...settings, assignee_ids: e.target.checked ? [...settings.assignee_ids, u.id] : settings.assignee_ids.filter((x: string) => x !== u.id) })} /> {u.name} <span className="faint small">{u.role}</span></label>)}
          <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={settings.notify} onChange={(e) => setSettings({ ...settings, notify: e.target.checked })} /> Notify the team when unassigned leads arrive</label>
        </fieldset>
        <label className="field">Qualification questionnaire<span className="hint">Questions are appended to the form; answers score the lead (<Link to="/onboarding">manage</Link>).</span>
          <select className="input" value={settings.questionnaire_id || ""} onChange={(e) => setSettings({ ...settings, questionnaire_id: e.target.value || null })}>
            <option value="">None</option>{(qs || []).map((q: any) => <option key={q.id} value={q.id}>{q.name}</option>)}</select></label>
        <SpamStats id={id} />
      </div>)}
      {tab === "embed" && (<div className="form">
        <p className="muted small">The form is live at its public page; embed it with the script (renders natively, recommended) or an iframe. Hidden UTM fields are filled from the page URL; the referrer and landing page are captured for attribution.</p>
        <div><div className="row" style={{ justifyContent: "space-between" }}><b className="small">Public page</b><Copy text={f.embed.page_url} /></div><Code>{f.embed.page_url}</Code></div>
        <div><div className="row" style={{ justifyContent: "space-between" }}><b className="small">JavaScript embed</b><Copy text={f.embed.script} /></div><Code>{f.embed.script}</Code></div>
        <div><div className="row" style={{ justifyContent: "space-between" }}><b className="small">Iframe</b><Copy text={f.embed.iframe} /></div><Code>{f.embed.iframe}</Code></div>
        <div><div className="row" style={{ justifyContent: "space-between" }}><b className="small">Direct POST endpoint (JSON or form-encoded)</b><Copy text={f.embed.endpoint} /></div><Code>{f.embed.endpoint}</Code></div>
        {can("edit") && <button className="btn" onClick={() => confirm("Rotate the public token? Existing embeds will stop working.") && run(() => api(`/forms/${id}/rotate-token`, { method: "POST" }), "Token rotated").then(reload)}>Rotate public token</button>}
      </div>)}
      {tab === "subs" && <Submissions id={id} />}
    </Drawer>
  );
}

function SpamStats({ id }: { id: string }) {
  const { data } = useApi<any>(`/forms/${id}/spam`);
  if (!data) return null;
  const entries = Object.entries(data.by_reason || {});
  return <div><b className="small">Blocked so far</b>{entries.length ? <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>{entries.map(([k, v]: any) => <Badge key={k} kind="low">{human(k)}: {v}</Badge>)}</div> : <p className="muted small">Nothing blocked yet.</p>}</div>;
}

function Submissions({ id }: { id: string }) {
  const { data } = useApi<any[]>(`/forms/${id}/submissions`);
  if (!data) return <Loading />;
  if (!data.length) return <Empty title="No submissions yet">Share the public page or embed the form.</Empty>;
  return (
    <table className="t"><thead><tr><th>When</th><th>Fields</th><th>Attribution</th><th>Lead</th></tr></thead>
      <tbody>{data.map((s) => (
        <tr key={s.id}><td className="small">{fmtDateTime(s.created_at)}</td>
          <td className="small">{Object.entries(s.payload.fields || {}).filter(([k]) => !k.startsWith("utm_")).map(([k, v]: any) => <div key={k}><span className="muted">{k}:</span> {String(v)}</div>)}</td>
          <td className="small">{Object.values(s.payload.utm || {}).join(" / ") || s.payload.referrer || "direct"}</td>
          <td>{s.lead_id ? <Link to={`/leads?lead=${s.lead_id}`}>Open</Link> : "—"}</td></tr>))}</tbody></table>
  );
}
