import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { ago, fmtDate, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Kpi, LeadsNav, money, Status, useProjects } from "./shared";

const STATUSES = ["new", "contacted", "qualified", "won", "lost"];

export default function Leads() {
  const [sp, setSp] = useSearchParams();
  const { projects, pid, picker } = useProjects();
  const [view, setView] = useState<"board" | "list">("board");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState("");
  const [dupes, setDupes] = useState(false);
  const qs = new URLSearchParams({ ...(pid ? { project_id: pid } : {}), ...(q ? { q } : {}), ...(status ? { status } : {}), ...(assignee ? { assignee } : {}), ...(dupes ? { duplicates: "true" } : {}) }).toString();
  const { data, error, loading, reload } = useApi<any>(pid ? `/leads?${qs}` : null, [qs]);
  const { data: meta } = useApi<any>("/leads/meta");
  const [add, setAdd] = useState(false);
  const open = sp.get("lead");
  const can = useCan();
  const { run } = useAction();
  const file = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState<string | null>(null);

  const importCsv = async (f: File) => {
    const fd = new FormData(); fd.append("file", f); fd.append("project_id", pid);
    const r = await run<any>(() => api("/leads/import", { form: fd }));
    if (r) { alert(`Imported: ${r.created} new, ${r.deduplicated} merged into existing leads${r.errors.length ? `, ${r.errors.length} rows skipped` : ""}.`); reload(); }
  };
  const move = (id: string, st: string) => run(() => api(`/leads/${id}`, { method: "PATCH", body: { status: st } })).then(reload);
  const items: any[] = data?.items || [];

  return (
    <>
      <PageHead title="Leads" sub="Every lead from forms, webhooks, bookings, CSV and CRM — deduplicated, scored and attributed.">
        {picker}
        {can("export") && <button className="btn" disabled={!pid} onClick={() => download(`/leads/export.csv?project_id=${pid}`, "leads.csv")}>Export CSV</button>}
        {can("create") && <><input ref={file} type="file" accept=".csv" hidden onChange={(e) => { e.target.files?.[0] && importCsv(e.target.files[0]); e.target.value = ""; }} />
          <button className="btn" disabled={!pid} onClick={() => file.current?.click()}>Import CSV</button>
          <button className="btn primary" disabled={!pid} onClick={() => setAdd(true)}>Add lead</button></>}
      </PageHead>
      <LeadsNav />
      {!projects ? <Loading /> : !projects.length ? <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>}>Leads belong to a project.</Empty></Panel> : (<>
        <div className="grid g4" style={{ marginBottom: 16 }}>
          <Kpi v={data?.total ?? "—"} l="Leads (filtered)" />
          <Kpi v={(data?.counts?.new || 0) + (data?.counts?.contacted || 0) + (data?.counts?.qualified || 0)} l="Open in pipeline" />
          <Kpi v={data?.counts?.won ?? 0} l="Won" tone="pass" />
          <Kpi v={money(data?.pipeline_value)} l="Open pipeline value" />
        </div>
        <Panel pad={false} title={<div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <input className="input" style={{ width: 200 }} placeholder="Search name, email, company" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search leads" />
          {view === "list" && <select className="input" style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">All statuses</option>{STATUSES.map((s) => <option key={s} value={s}>{human(s)}</option>)}</select>}
          <select className="input" style={{ width: "auto" }} value={assignee} onChange={(e) => setAssignee(e.target.value)} aria-label="Assignee">
            <option value="">Anyone</option><option value="me">Assigned to me</option><option value="none">Unassigned</option>
            {(meta?.users || []).map((u: any) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
          <label className="check"><input type="checkbox" checked={dupes} onChange={(e) => setDupes(e.target.checked)} /> Possible duplicates</label>
        </div>} actions={<div className="seg"><button className={view === "board" ? "on" : ""} onClick={() => setView("board")}>Pipeline</button><button className={view === "list" ? "on" : ""} onClick={() => setView("list")}>List</button></div>}>
          <ErrorBox error={error} />
          {loading && !data ? <Loading /> : !items.length ? <Empty title="No leads yet" action={<Link className="btn" to="/forms">Create a form</Link>}>Publish a form, connect a webhook, import a CSV or add leads by hand.</Empty> :
            view === "board" ? (
              <div className="gq-kanban" style={{ padding: 12 }}>
                {STATUSES.map((s) => (
                  <div key={s} className={`col ${dragging ? "drop" : ""}`} onDragOver={(e) => e.preventDefault()} onDrop={() => { if (dragging) move(dragging, s); setDragging(null); }}>
                    <div className="col-head"><span>{human(s)}</span><span className="muted num">{data.counts?.[s] ?? 0}</span></div>
                    {items.filter((l) => l.status === s).map((l) => (
                      <div key={l.id} className="gq-card" draggable={can("edit")} onDragStart={() => setDragging(l.id)} onDragEnd={() => setDragging(null)} onClick={() => setSp({ lead: l.id })}>
                        <b>{l.name || l.email || l.phone}</b>
                        <div className="muted small">{l.company || l.email || "—"}</div>
                        <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                          {l.value != null && <span className="small num">{money(l.value)}</span>}
                          {l.segment && <Badge>{l.segment}</Badge>}
                          {l.possible_duplicate_of && <Badge kind="high">dup?</Badge>}
                          <span className="faint small">{l.utm_source}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <div className="table-wrap"><table className="t"><thead><tr><th>Lead</th><th>Status</th><th className="r">Score</th><th className="r">Value</th><th>Source / medium</th><th>Assignee</th><th>Created</th></tr></thead>
                <tbody>{items.map((l) => (
                  <tr key={l.id} className="click" onClick={() => setSp({ lead: l.id })}>
                    <td><b>{l.name || l.email || l.phone}</b><div className="faint small">{[l.email, l.company].filter(Boolean).join(" · ")}</div></td>
                    <td><Status s={l.status} />{l.possible_duplicate_of && <> <Badge kind="high">dup?</Badge></>}</td>
                    <td className="r num">{l.score}{l.segment && <div className="faint small">{l.segment}</div>}</td>
                    <td className="r num">{money(l.value)}</td>
                    <td className="small">{l.utm_source || "—"} / {l.utm_medium || "—"}{l.utm_campaign && <div className="faint">{l.utm_campaign}</div>}</td>
                    <td className="small">{l.assignee || <span className="faint">—</span>}</td><td className="muted small">{ago(l.created_at)}</td>
                  </tr>))}</tbody></table></div>
            )}
        </Panel>
      </>)}
      {add && <AddLead pid={pid} onClose={() => setAdd(false)} onDone={reload} />}
      {open && <LeadDrawer id={open} users={meta?.users || []} onClose={() => setSp({})} onChange={reload} />}
    </>
  );
}

function AddLead({ pid, onClose, onDone }: any) {
  const [f, setF] = useState<any>({ name: "", email: "", phone: "", company: "", value: "", status: "new", utm_source: "", utm_medium: "", utm_campaign: "", notes: "" });
  const { run, busy } = useAction();
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const save = () => run<any>(() => api("/leads", { body: { project_id: pid, ...Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "")), value: f.value ? Number(f.value) : null } }))
    .then((r) => { if (r) { if (!r.created) alert("This matches an existing lead — the details were merged into it."); onDone(); onClose(); } });
  return (
    <Modal title="Add lead" onClose={onClose} foot={<button className="btn primary" disabled={busy || !(f.email || f.phone || f.name)} onClick={save}>Save lead</button>}>
      <div className="form">
        <div className="grid g2"><label className="field">Name<input className="input" value={f.name} onChange={set("name")} autoFocus /></label>
          <label className="field">Company<input className="input" value={f.company} onChange={set("company")} /></label></div>
        <div className="grid g2"><label className="field">Email<input className="input" type="email" value={f.email} onChange={set("email")} /></label>
          <label className="field">Phone<input className="input" value={f.phone} onChange={set("phone")} /></label></div>
        <div className="grid g2"><label className="field">Deal value<input className="input" inputMode="decimal" value={f.value} onChange={set("value")} /></label>
          <label className="field">Status<select className="input" value={f.status} onChange={set("status")}>{STATUSES.map((s) => <option key={s} value={s}>{human(s)}</option>)}</select></label></div>
        <div className="grid g3"><label className="field">Source<input className="input" value={f.utm_source} onChange={set("utm_source")} placeholder="e.g. referral" /></label>
          <label className="field">Medium<input className="input" value={f.utm_medium} onChange={set("utm_medium")} /></label>
          <label className="field">Campaign<input className="input" value={f.utm_campaign} onChange={set("utm_campaign")} /></label></div>
        <label className="field">Note<textarea className="input" rows={3} value={f.notes} onChange={set("notes")} /></label>
        <p className="muted small">Leads are deduplicated within the project by normalised email and phone number.</p>
      </div>
    </Modal>
  );
}

function LeadDrawer({ id, users, onClose, onChange }: any) {
  const { data: l, error, reload } = useApi<any>(`/leads/${id}`, [id]);
  const [note, setNote] = useState("");
  const [edit, setEdit] = useState<any>(null);
  const { run } = useAction();
  const can = useCan();
  useEffect(() => setEdit(null), [id]);
  const patch = (body: any, ok?: string) => run(() => api(`/leads/${id}`, { method: "PATCH", body }), ok).then(() => { reload(); onChange(); });
  if (error) return <Drawer title="Lead" onClose={onClose}><ErrorBox error={error} /></Drawer>;
  if (!l) return <Drawer title="Lead" onClose={onClose}><Loading /></Drawer>;
  return (
    <Drawer title={l.name || l.email || l.phone} onClose={onClose}
      foot={can("delete") ? <button className="btn danger" onClick={() => confirm("Delete this lead and its timeline?") && run(() => api(`/leads/${id}`, { method: "DELETE" }), "Lead deleted").then(() => { onChange(); onClose(); })}>Delete lead</button> : undefined}>
      {l.duplicate_candidate && (
        <div className="notice warn" style={{ marginBottom: 14 }}><div><b>Possible duplicate</b> of {l.duplicate_candidate.name || l.duplicate_candidate.email} ({l.duplicate_candidate.company || "no company"}).
          {can("edit") && <div className="row" style={{ marginTop: 8 }}>
            <button className="btn sm primary" onClick={() => run(() => api(`/leads/${id}/merge`, { body: { into_id: l.duplicate_candidate.id } }), "Leads merged").then(() => { onChange(); onClose(); })}>Merge into it</button>
            <button className="btn sm" onClick={() => run(() => api(`/leads/${id}/dismiss-duplicate`, { method: "POST" })).then(reload)}>Not a duplicate</button></div>}</div></div>
      )}
      <div className="row" style={{ gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        <select className="input" style={{ width: "auto" }} value={l.status} disabled={!can("edit")} onChange={(e) => {
          const st = e.target.value; const reason = st === "lost" ? prompt("Why was it lost?") || undefined : undefined; patch({ status: st, lost_reason: reason }, "Status updated");
        }} aria-label="Status">{STATUSES.map((s) => <option key={s} value={s}>{human(s)}</option>)}</select>
        <select className="input" style={{ width: "auto" }} value={l.assignee_id || ""} disabled={!can("edit") || !users.length} onChange={(e) => patch({ assignee_id: e.target.value || null }, "Assignment updated")} aria-label="Assignee">
          <option value="">Unassigned</option>{users.map((u: any) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
        {can("edit") && <button className="btn sm" onClick={() => setEdit({ name: l.name || "", company: l.company || "", email: l.email || "", phone: l.phone || "", value: l.value ?? "" })}>Edit details</button>}
      </div>
      {edit ? (
        <form className="form" style={{ marginBottom: 16 }} onSubmit={(e) => { e.preventDefault(); patch({ ...edit, value: edit.value === "" ? null : Number(edit.value) }, "Lead saved").then(() => setEdit(null)); }}>
          {["name", "company", "email", "phone", "value"].map((k) => <label key={k} className="field">{human(k)}<input className="input" value={edit[k]} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></label>)}
          <div className="row"><button className="btn primary">Save</button><button type="button" className="btn ghost" onClick={() => setEdit(null)}>Cancel</button></div>
        </form>
      ) : (
        <dl className="kv" style={{ marginBottom: 16 }}>
          <dt>Email</dt><dd>{l.email || "—"}</dd><dt>Phone</dt><dd>{l.phone || "—"}</dd><dt>Company</dt><dd>{l.company || "—"}</dd>
          <dt>Value</dt><dd>{money(l.value)}</dd><dt>Score / segment</dt><dd>{l.score}{l.segment ? ` · ${l.segment}` : ""}</dd>
          <dt>First touch</dt><dd>{l.utm_source || "—"} / {l.utm_medium || "—"}{l.utm_campaign ? ` · ${l.utm_campaign}` : ""}</dd>
          <dt>Captured via</dt><dd>{l.source || "—"} · {fmtDate(l.created_at)}</dd>
          {l.crm_contact_id && <><dt>HubSpot contact</dt><dd>{l.crm_contact_id}</dd></>}
          {l.lost_reason && <><dt>Lost reason</dt><dd>{l.lost_reason}</dd></>}
          {Object.entries(l.custom || {}).map(([k, v]: any) => <Fragment key={k}><dt>{human(k)}</dt><dd>{String(v)}</dd></Fragment>)}
        </dl>
      )}
      <h3 className="small muted">Touchpoints ({l.touchpoints.length})</h3>
      {l.touchpoints.length ? <table className="t" style={{ marginBottom: 16 }}><thead><tr><th>When</th><th>Source / medium</th><th>Campaign</th><th>Via</th></tr></thead>
        <tbody>{l.touchpoints.map((t: any) => <tr key={t.id}><td className="small">{fmtDateTime(t.occurred_at)}</td><td>{t.source} / {t.medium}</td><td className="small">{t.campaign || "—"}</td><td className="small">{t.kind}</td></tr>)}</tbody></table>
        : <p className="muted small">No touchpoints recorded.</p>}
      {l.bookings.length > 0 && <><h3 className="small muted">Bookings</h3><ul className="small">{l.bookings.map((b: any) => <li key={b.id}>{b.event_name || "Meeting"} — {fmtDateTime(b.start_time)} <Status s={b.status} /></li>)}</ul></>}
      <h3 className="small muted">Notes</h3>
      {can("edit") && <form className="row" style={{ marginBottom: 10 }} onSubmit={(e) => { e.preventDefault(); if (note.trim()) run(() => api(`/leads/${id}/notes`, { body: { body: note } })).then(() => { setNote(""); reload(); }); }}>
        <input className="input" style={{ flex: 1 }} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note" aria-label="Note" /><button className="btn">Add</button></form>}
      {l.notes.map((n: any) => <div key={n.id} className="panel" style={{ padding: 10, marginBottom: 8 }}><div className="small">{n.body}</div><div className="faint small">{n.author || "system"} · {ago(n.created_at)}</div></div>)}
      <h3 className="small muted" style={{ marginTop: 16 }}>Activity</h3>
      <ul className="gq-timeline">{l.activity.map((a: any) => <li key={a.id}><div>{a.summary}</div><div className="faint small">{a.actor} · {fmtDateTime(a.created_at)}</div></li>)}</ul>
    </Drawer>
  );
}
