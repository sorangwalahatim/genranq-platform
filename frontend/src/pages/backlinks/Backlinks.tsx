import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Bar as RBar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { ago, bytes, fmtDate, fmtDateTime } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Bar, money, NoProjects, pct, StatusBadge, statusLabel, Tabs, UploadButton, useProject } from "./shared";

type BL = any;

export default function Backlinks() {
  const { pid, projects, picker } = useProject();
  const [sp] = useSearchParams();
  const [tab, setTab] = useState("overview");
  const [open, setOpen] = useState<string | null>(sp.get("backlink"));
  const [edit, setEdit] = useState<BL | null | undefined>(undefined);
  const can = useCan();
  const dash = useApi<any>(pid ? `/backlinks/dashboard?project_id=${pid}` : null, [pid]);
  useEffect(() => { if (sp.get("backlink")) setTab("links"); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (projects && !projects.length) return <><PageHead title="Backlinks" /><NoProjects /></>;
  return (
    <>
      <PageHead title="Backlinks" sub="Every placed link with its supplier, cost and classification — verified on the live page, with history, toxic scores and disavow approvals.">
        {picker}
        <Link className="btn" to="/backlinks/suppliers">Suppliers</Link>
        {can("create") && <button className="btn primary" disabled={!pid} onClick={() => setEdit(null)}>Add backlink</button>}
      </PageHead>
      <Tabs value={tab} onChange={setTab} tabs={[["overview", "Overview"], ["links", "Links"], ["toxic", "Toxic & disavow"]]} />
      {tab === "overview" && <Overview k={dash.data} loading={dash.loading} error={dash.error} onStatus={() => setTab("links")} />}
      {tab === "links" && pid && <LinkList pid={pid} onOpen={setOpen} onEdit={setEdit} reloadDash={dash.reload} />}
      {tab === "toxic" && pid && <Toxic pid={pid} />}
      {edit !== undefined && pid && <LinkForm pid={pid} bl={edit} onClose={() => setEdit(undefined)} onDone={() => { dash.reload(); setTab("links"); }} />}
      {open && <Detail id={open} onClose={() => setOpen(null)} onEdit={(b) => { setOpen(null); setEdit(b); }} />}
    </>
  );
}

// ------------------------------------------------------------------ overview / KPIs (F-019)
function Overview({ k, loading, error, onStatus }: any) {
  if (error) return <ErrorBox error={error} />;
  if (loading && !k) return <Loading />;
  if (!k) return null;
  if (!k.total) return <Panel><Empty title="No backlinks yet">Add links one by one, import a CSV, or add links discovered by Ahrefs/Semrush. KPIs appear here once links exist.</Empty></Panel>;
  const ret = (k.retention || []).filter((r: any) => r.placed).map((r: any) => ({ month: r.month, retention: r.retention == null ? null : Math.round(r.retention * 1000) / 10, checked: r.checked }));
  const spend = (k.spend_by_supplier || []).slice(0, 10);
  return (
    <div className="stack">
      <div className="bl-kpis">
        <div className="panel kpi"><div className="v">{k.total}</div><div className="l">Backlinks</div></div>
        <div className="panel kpi"><div className="v live">{k.live}</div><div className="l">Live</div></div>
        <div className="panel kpi"><div className="v removed">{k.removed}</div><div className="l">Removed</div></div>
        <div className="panel kpi"><div className="v toxic">{k.toxic}</div><div className="l">Toxic (score ≥ {k.toxic_threshold}, advisory)</div></div>
        <div className="panel kpi"><div className="v">{pct(k.live_rate)}</div><div className="l">Live rate ({k.checked} conclusively checked)</div></div>
        <div className="panel kpi"><div className="v">{k.cost_per_live_link?.length ? k.cost_per_live_link.map((c: any) => money(c.cost_per_live_link, c.currency)).join(" · ") : "—"}</div><div className="l">Cost per live link</div></div>
      </div>
      <div className="grid g2">
        <Panel title="Retention over time" actions={<span className="faint small">share of checked links live at month end</span>}>
          {ret.some((r: any) => r.retention != null) ? <div style={{ height: 220 }}><ResponsiveContainer>
            <LineChart data={ret}><CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="month" fontSize={12} tickLine={false} axisLine={false} />
              <YAxis domain={[0, 100]} width={34} fontSize={12} tickLine={false} axisLine={false} unit="%" /><Tooltip formatter={(v: any) => `${v}%`} />
              <Line dataKey="retention" name="Retention" stroke="var(--pass)" strokeWidth={2} connectNulls dot={false} /></LineChart></ResponsiveContainer></div>
            : <p className="muted">No conclusive verification yet — run “Verify” on the Links tab to start the retention history.</p>}
        </Panel>
        <Panel title="Status">
          {Object.entries(k.by_status as Record<string, number>).sort((a, b) => b[1] - a[1]).map(([s, n]) => (
            <div key={s} className="row" style={{ marginBottom: 8, cursor: "pointer" }} onClick={onStatus}>
              <span style={{ width: 130 }}><StatusBadge s={s} /></span><Bar value={n} max={k.total} /><span className="num" style={{ width: 40, textAlign: "right" }}>{n}</span>
            </div>))}
          <div className="row small muted" style={{ marginTop: 12, gap: 16 }}>
            {Object.entries(k.by_link_type).map(([t, n]: any) => <span key={t}>{t}: <b className="num">{n}</b></span>)}
            {Object.entries(k.by_paid).map(([t, n]: any) => <span key={t}>{t}: <b className="num">{n}</b></span>)}
          </div>
        </Panel>
      </div>
      <div className="grid g2">
        <Panel title="Spend by supplier">
          {!spend.length ? <p className="muted">No paid links with a cost yet.</p> : (<>
            <div style={{ height: 200 }}><ResponsiveContainer><BarChart data={spend.map((s: any) => ({ name: `${s.supplier} (${s.currency})`, spend: s.spend }))} layout="vertical" margin={{ left: 20 }}>
              <XAxis type="number" fontSize={12} tickLine={false} axisLine={false} /><YAxis type="category" dataKey="name" width={140} fontSize={12} tickLine={false} axisLine={false} />
              <Tooltip /><RBar dataKey="spend" fill="var(--accent)" radius={[0, 3, 3, 0]} /></BarChart></ResponsiveContainer></div>
            <table className="t"><thead><tr><th>Supplier</th><th className="r">Spend</th><th className="r">Links</th><th className="r">Live</th><th className="r">Cost / live link</th></tr></thead>
              <tbody>{spend.map((s: any) => <tr key={s.supplier + s.currency}><td>{s.supplier}</td><td className="r num">{money(s.spend, s.currency)}</td><td className="r num">{s.links}</td><td className="r num">{s.live_links}</td><td className="r num">{money(s.cost_per_live_link, s.currency)}</td></tr>)}</tbody></table>
          </>)}
        </Panel>
        <Panel title="Links per project">
          <table className="t"><thead><tr><th>Project</th><th className="r">Links</th><th className="r">Live</th><th></th></tr></thead>
            <tbody>{k.links_per_project.map((p: any) => <tr key={p.project_id}><td>{p.project}</td><td className="r num">{p.links}</td><td className="r num">{p.live}</td><td style={{ width: 120 }}><Bar value={p.live} max={p.links} color="var(--pass)" /></td></tr>)}</tbody></table>
        </Panel>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ list + bulk
function LinkList({ pid, onOpen, onEdit, reloadDash }: any) {
  const [f, setF] = useState({ status: "", link_type: "", paid_or_free: "", q: "" });
  const qs = new URLSearchParams({ project_id: pid, ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)) }).toString();
  const { data, error, loading, reload } = useApi<any>(`/backlinks?${qs}`, [qs]);
  const opts = useApi<any>("/backlinks/options").data;
  const sups = useApi<any[]>("/suppliers").data;
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState({ action: "verify", value: "" });
  const { run, busy } = useAction();
  const can = useCan();
  const items: BL[] = data?.items || [];
  useEffect(() => setSel(new Set()), [qs]);
  const refresh = () => { reload(); reloadDash(); };
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const doBulk = async () => {
    const ids = [...sel];
    if (bulk.action === "delete" && !confirm(`Delete ${ids.length} backlinks? This can't be undone.`)) return;
    const r = await run(() => api("/backlinks/bulk", { body: { ids, action: bulk.action, value: bulk.value || null, confirm: bulk.action === "delete" } }),
      bulk.action === "verify" ? "Verification queued — results appear in a minute" : "Done");
    if (r) { setSel(new Set()); refresh(); }
  };
  const valueOpts: Record<string, string[] | null> = {
    set_status: opts?.statuses, set_link_type: opts?.link_types, set_scope: opts?.scopes, set_frequency: opts?.frequencies, set_paid: opts?.paid, add_tag: null,
  };
  return (
    <Panel pad={false} title={`Links${data ? ` (${data.total})` : ""}`} actions={<>
      {can("export") && <button className="btn" onClick={() => download(`/projects/${pid}/backlinks/export.csv`, "backlinks.csv")}>Export CSV</button>}
      {can("create") && <UploadButton path={`/projects/${pid}/backlinks/import`} label="Import CSV" ok="Import finished" onDone={(r) => { refresh(); if (r.error_count) alert(`${r.created} created, ${r.updated} updated, ${r.error_count} rows skipped:\n` + r.errors.slice(0, 10).map((e: any) => `row ${e.row}: ${e.error}`).join("\n")); }} />}
      {can("edit") && <button className="btn" disabled={!items.length} onClick={() => run(() => api("/backlinks/verify", { body: { project_id: pid } }), "Verification of all links queued")}>Verify all</button>}
    </>}>
      <div className="bl-toolbar">
        <input className="input" style={{ width: 220 }} placeholder="Search URL or anchor" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} aria-label="Search" />
        <select className="input" style={{ width: "auto" }} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} aria-label="Status">
          <option value="">All statuses</option>{(opts?.statuses || []).map((s: string) => <option key={s} value={s}>{statusLabel(s)}</option>)}</select>
        <select className="input" style={{ width: "auto" }} value={f.link_type} onChange={(e) => setF({ ...f, link_type: e.target.value })} aria-label="Link type">
          <option value="">All link types</option>{(opts?.link_types || []).map((s: string) => <option key={s}>{s}</option>)}</select>
        <select className="input" style={{ width: "auto" }} value={f.paid_or_free} onChange={(e) => setF({ ...f, paid_or_free: e.target.value })} aria-label="Paid or free">
          <option value="">Paid & free</option><option value="paid">Paid</option><option value="free">Free</option></select>
      </div>
      {sel.size > 0 && can("edit") && (
        <div className="bl-bulk">
          <b>{sel.size} selected</b>
          <select className="input" style={{ width: "auto" }} value={bulk.action} onChange={(e) => setBulk({ action: e.target.value, value: "" })} aria-label="Bulk action">
            <option value="verify">Verify now</option><option value="set_status">Set status</option><option value="set_link_type">Set link type</option>
            <option value="set_scope">Set scope</option><option value="set_paid">Set paid/free</option><option value="set_frequency">Set re-check frequency</option>
            <option value="set_supplier">Set supplier</option><option value="add_tag">Add tag</option>{can("delete") && <option value="delete">Delete</option>}
          </select>
          {bulk.action === "set_supplier" ? <select className="input" style={{ width: "auto" }} value={bulk.value} onChange={(e) => setBulk({ ...bulk, value: e.target.value })} aria-label="Supplier">
            <option value="">No supplier</option>{(sups || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
            : valueOpts[bulk.action] ? <select className="input" style={{ width: "auto" }} value={bulk.value} onChange={(e) => setBulk({ ...bulk, value: e.target.value })} aria-label="Value">
              <option value="">Choose…</option>{valueOpts[bulk.action]!.map((v) => <option key={v} value={v}>{bulk.action === "set_status" ? statusLabel(v) : v}</option>)}</select>
              : bulk.action === "add_tag" ? <input className="input" style={{ width: 160 }} placeholder="Tag" value={bulk.value} onChange={(e) => setBulk({ ...bulk, value: e.target.value })} aria-label="Tag" /> : null}
          <button className={`btn sm ${bulk.action === "delete" ? "danger" : "primary"}`} disabled={busy || (!!valueOpts[bulk.action] && !bulk.value) || (bulk.action === "add_tag" && !bulk.value)} onClick={doBulk}>Apply</button>
          <button className="btn sm ghost" onClick={() => setSel(new Set())}>Clear</button>
        </div>)}
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !items.length ? <Empty title="No backlinks match">Add a backlink, import a CSV (columns: source_url, target_url, anchor, link_type, paid_or_free, price, currency, placement_date, supplier) or add links found by Ahrefs/Semrush.</Empty> : (
        <div className="table-wrap"><table className="t">
          <thead><tr><th style={{ width: 28 }}><input type="checkbox" aria-label="Select all" checked={sel.size === items.length} onChange={(e) => setSel(e.target.checked ? new Set(items.map((x) => x.id)) : new Set())} /></th>
            <th>Source page</th><th>Target · anchor</th><th>Type</th><th>Status</th><th className="r">Cost</th><th>Supplier</th><th className="r">Toxicity</th><th>Last check</th><th></th></tr></thead>
          <tbody>{items.map((b) => (
            <tr key={b.id} className="click" onClick={() => onOpen(b.id)}>
              <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${b.source_domain}`} checked={sel.has(b.id)} onChange={() => toggle(b.id)} /></td>
              <td><b>{b.source_domain}</b><div className="mono-url" style={{ maxWidth: 280 }}>{b.source_url}</div></td>
              <td><div className="mono-url" style={{ maxWidth: 240 }}>{b.target_url}</div><div className="small muted">“{b.anchor_text || "—"}”</div></td>
              <td><Badge>{b.link_type}</Badge><div className="faint small">{b.paid_or_free} · {b.link_scope}</div></td>
              <td><StatusBadge s={b.status} />{b.last_check?.status === "could_not_verify" && b.status !== "could_not_verify" && <div className="faint small">last check inconclusive</div>}</td>
              <td className="r num">{money(b.cost, b.currency)}</td><td>{b.supplier || <span className="faint">—</span>}</td>
              <td className="r num" style={{ color: b.toxicity_score >= 60 ? "var(--critical)" : undefined }}>{b.toxicity_score ?? "—"}</td>
              <td className="muted small">{ago(b.last_check?.checked_at)}</td>
              <td onClick={(e) => e.stopPropagation()}>{can("edit") && <button className="btn sm ghost" onClick={() => onEdit(b)}>Edit</button>}</td>
            </tr>))}</tbody>
        </table></div>)}
    </Panel>
  );
}

// ------------------------------------------------------------------ create / edit
function LinkForm({ pid, bl, onClose, onDone }: { pid: string; bl: BL | null; onClose: () => void; onDone: () => void }) {
  const opts = useApi<any>("/backlinks/options").data;
  const sups = useApi<any[]>("/suppliers").data;
  const [f, setF] = useState<any>(() => bl ? { ...bl, tags: (bl.tags || []).join(", "), cost: bl.cost ?? "", placement_date: bl.placement_date || "" } :
    { source_url: "", target_url: "", anchor_text: "", link_type: "dofollow", paid_or_free: "free", link_scope: "project", cost: "", currency: "USD", placement_date: "", supplier_id: "", notes: "", tags: "", verify_frequency: "weekly", verify_now: true });
  const { run, busy } = useAction();
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const save = async () => {
    const body: any = { source_url: f.source_url, target_url: f.target_url, anchor_text: f.anchor_text || null, link_type: f.link_type, paid_or_free: f.paid_or_free,
      link_scope: f.link_scope, cost: f.paid_or_free === "paid" && f.cost !== "" ? Number(f.cost) : null, currency: f.paid_or_free === "paid" ? f.currency : null,
      placement_date: f.placement_date || null, supplier_id: f.supplier_id || null, notes: f.notes || null, verify_frequency: f.verify_frequency,
      tags: String(f.tags || "").split(",").map((x: string) => x.trim()).filter(Boolean) };
    const r = await run(() => bl ? api(`/backlinks/${bl.id}`, { method: "PATCH", body }) : api("/backlinks", { body: { ...body, project_id: pid, verify_now: f.verify_now } }),
      bl ? "Backlink updated" : f.verify_now ? "Backlink added — verifying now" : "Backlink added");
    if (r) { onDone(); onClose(); }
  };
  return (
    <Modal wide title={bl ? "Edit backlink" : "Add backlink"} onClose={onClose} foot={<button className="btn primary" disabled={busy || !f.source_url || !f.target_url} onClick={save}>{bl ? "Save" : "Add backlink"}</button>}>
      <div className="form">
        <label className="field">Source page (where the link is placed)<input className="input" value={f.source_url} onChange={set("source_url")} placeholder="https://blog.example.com/post" autoFocus /></label>
        <label className="field">Target URL (your page)<input className="input" value={f.target_url} onChange={set("target_url")} placeholder="https://yoursite.com/page" /></label>
        <div className="grid g3">
          <label className="field">Anchor text<input className="input" value={f.anchor_text || ""} onChange={set("anchor_text")} /></label>
          <label className="field">Link type<select className="input" value={f.link_type} onChange={set("link_type")}>{(opts?.link_types || ["dofollow"]).map((x: string) => <option key={x}>{x}</option>)}</select></label>
          <label className="field">Scope<span className="hint">project / client / site link</span><select className="input" value={f.link_scope} onChange={set("link_scope")}>{(opts?.scopes || ["project"]).map((x: string) => <option key={x}>{x}</option>)}</select></label>
        </div>
        <div className="grid g3">
          <label className="field">Paid or free<select className="input" value={f.paid_or_free} onChange={set("paid_or_free")}><option value="free">Free</option><option value="paid">Paid</option></select></label>
          <label className="field">Price<input className="input" inputMode="decimal" disabled={f.paid_or_free !== "paid"} value={f.cost} onChange={set("cost")} /></label>
          <label className="field">Currency<input className="input" maxLength={3} disabled={f.paid_or_free !== "paid"} value={f.currency || ""} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} /></label>
        </div>
        <div className="grid g3">
          <label className="field">Placement date<input className="input" type="date" value={f.placement_date} onChange={set("placement_date")} /></label>
          <label className="field">Supplier<select className="input" value={f.supplier_id || ""} onChange={set("supplier_id")}><option value="">None</option>{(sups || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
          <label className="field">Re-check<select className="input" value={f.verify_frequency} onChange={set("verify_frequency")}>{(opts?.frequencies || ["weekly"]).map((x: string) => <option key={x}>{x}</option>)}</select></label>
        </div>
        <label className="field">Tags<span className="hint">Comma separated</span><input className="input" value={f.tags} onChange={set("tags")} /></label>
        <label className="field">Notes<textarea className="input" rows={3} value={f.notes || ""} onChange={set("notes")} /></label>
        {!bl && <label className="check"><input type="checkbox" checked={f.verify_now} onChange={set("verify_now")} /> Verify the link on the source page now</label>}
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ detail drawer
function Detail({ id, onClose, onEdit }: { id: string; onClose: () => void; onEdit: (b: BL) => void }) {
  const { data: b, error, reload } = useApi<any>(`/backlinks/${id}`, [id]);
  const { run } = useAction();
  const can = useCan();
  return (
    <Drawer title={b ? b.source_domain : "Backlink"} onClose={onClose} foot={b && <>
      {can("edit") && <button className="btn" onClick={() => run(() => api("/backlinks/verify", { body: { backlink_ids: [id] } }), "Verification queued").then(() => setTimeout(reload, 4000))}>Verify now</button>}
      {can("edit") && <button className="btn primary" onClick={() => onEdit(b)}>Edit</button>}
      {can("delete") && <button className="btn danger" onClick={() => confirm("Delete this backlink record?") && run(() => api(`/backlinks/${id}`, { method: "DELETE" }), "Deleted").then(onClose)}>Delete</button>}
    </>}>
      <ErrorBox error={error} />
      {!b ? <Loading /> : (<div className="stack">
        <dl className="bl-dl">
          <dt>Status</dt><dd><StatusBadge s={b.status} /> {b.last_check_note && <span className="small muted">{b.last_check_note}</span>}</dd>
          <dt>Source</dt><dd><a href={b.source_url} target="_blank" rel="noreferrer noopener" className="mono-url">{b.source_url}</a></dd>
          <dt>Target</dt><dd className="mono-url">{b.target_url}</dd>
          <dt>Anchor</dt><dd>{b.anchor_text || "—"}</dd>
          <dt>Classification</dt><dd>{b.link_type} · {b.paid_or_free} · {b.link_scope} link</dd>
          {b.cost != null && <><dt>Price</dt><dd>{money(b.cost, b.currency)}</dd></>}
          {b.supplier && <><dt>Supplier</dt><dd><Link to={`/backlinks/suppliers?supplier=${b.supplier_id}`}>{b.supplier}</Link></dd></>}
          <dt>Placed</dt><dd>{fmtDate(b.placement_date)}</dd>
          <dt>First seen live</dt><dd>{fmtDateTime(b.first_detected)}</dd>
          <dt>Last verified</dt><dd>{fmtDateTime(b.last_verified)}</dd>
          <dt>Next check</dt><dd>{b.verify_frequency === "off" ? "Automatic checks off" : `${fmtDateTime(b.next_check_at)} (${b.verify_frequency})`}</dd>
          {b.toxicity_score != null && <><dt>Toxicity</dt><dd><b style={{ color: b.toxicity_score >= 60 ? "var(--critical)" : undefined }}>{b.toxicity_score}</b> <span className="small muted">Semrush, advisory</span></dd></>}
          {b.tags?.length > 0 && <><dt>Tags</dt><dd>{b.tags.map((t: string) => <span key={t} className="bl-chip">{t}</span>)}</dd></>}
          {b.notes && <><dt>Notes</dt><dd>{b.notes}</dd></>}
        </dl>
        <div><h3 style={{ marginBottom: 6 }}>Verification history</h3>
          {!b.checks.length ? <p className="muted small">Never checked. “Verify now” fetches the source page and looks for the link.</p> : (
            <ul className="bl-timeline">{b.checks.map((c: any) => (
              <li key={c.id}>
                <div className="row"><StatusBadge s={c.status} /><span className="muted small">{fmtDateTime(c.checked_at)}</span>
                  {c.previous_status && c.previous_status !== c.status && c.status !== "could_not_verify" && <Badge kind="medium">{statusLabel(c.previous_status)} → {statusLabel(c.status)}</Badge>}
                  <span className="spacer" /><span className="faint small">HTTP {c.http_status ?? "—"}{c.rel_found ? ` · rel="${c.rel_found}"` : ""}{c.is_indexed === false ? " · not indexable" : ""}</span></div>
                <div className="small" style={{ marginTop: 4 }}>{c.reason}</div>
                {c.evidence?.snippet && <div className="bl-snippet">{c.evidence.snippet}</div>}
              </li>))}</ul>)}
        </div>
        <Files files={b.files} fields={{ backlink_id: id }} onChange={reload} />
      </div>)}
    </Drawer>
  );
}

export function Files({ files, fields, onChange }: { files: any[]; fields: Record<string, string>; onChange: () => void }) {
  const opts = useApi<any>("/backlinks/options").data;
  const [kind, setKind] = useState("invoice");
  const { run } = useAction();
  const can = useCan();
  return (
    <div><div className="row" style={{ marginBottom: 6 }}><h3>Files</h3><span className="spacer" />
      {can("create") && <><select className="input" style={{ width: "auto" }} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="File kind">{(opts?.file_kinds || ["invoice"]).map((k: string) => <option key={k}>{k}</option>)}</select>
        <UploadButton path="/backlink-files" fields={{ ...fields, kind }} label="Upload" accept=".pdf,.png,.jpg,.jpeg,.webp,.csv,.xlsx,.docx,.txt,.zip" ok="File uploaded" onDone={onChange} /></>}</div>
      {!files?.length ? <p className="muted small">No invoices or reports attached.</p> : (
        <table className="t"><tbody>{files.map((f) => (
          <tr key={f.id}><td><Badge>{f.kind}</Badge></td><td>{f.filename}<div className="faint small">{bytes(f.size_bytes)} · {fmtDate(f.created_at)}{f.note ? ` · ${f.note}` : ""}</div></td>
            <td className="r nowrap"><button className="btn sm" onClick={() => download(`/backlink-files/${f.id}/download`, f.filename)}>Download</button>
              {can("delete") && <button className="btn sm ghost danger" onClick={() => confirm(`Delete ${f.filename}?`) && run(() => api(`/backlink-files/${f.id}`, { method: "DELETE" }), "File deleted").then(onChange)}>Delete</button>}</td></tr>))}</tbody></table>)}
    </div>
  );
}

// ------------------------------------------------------------------ toxic & disavow (F-016)
function Toxic({ pid }: { pid: string }) {
  const [min, setMin] = useState(60);
  const tox = useApi<any>(`/projects/${pid}/toxic?min_score=${min}`, [pid, min]);
  const lists = useApi<any[]>(`/projects/${pid}/disavow`, [pid]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [asDomains, setAsDomains] = useState(true);
  const { run, busy } = useAction();
  const can = useCan();
  const items = tox.data?.items || [];
  const create = async () => {
    const r = await run(() => api(`/projects/${pid}/disavow`, { body: { title: title || `Disavow ${new Date().toLocaleDateString()}`, toxic_ids: [...sel], as_domains: asDomains } }), "Disavow draft created");
    if (r) { setSel(new Set()); setTitle(""); lists.reload(); tox.reload(); }
  };
  const act = async (id: string, what: "submit" | "generate") => {
    const r = await run(() => api(`/disavow/${id}/${what}`, { method: "POST" }), what === "submit" ? "Sent for manager approval" : "Disavow file generated");
    if (r) lists.reload();
  };
  return (
    <div className="stack">
      <div className="notice warn"><div><b>Toxic scores are advisory.</b> They come from Semrush’s Backlink Audit export. Nothing is disavowed automatically: a disavow list needs a manager’s approval before the Google disavow file can be generated, and uploading it to Search Console is up to you.</div></div>
      <Panel pad={false} title="Toxic backlinks" actions={<>
        <label className="row small">Min score <input className="input" style={{ width: 70 }} type="number" min={0} max={100} value={min} onChange={(e) => setMin(Number(e.target.value) || 0)} /></label>
        {can("edit") && <UploadButton path={`/projects/${pid}/toxic/import`} label="Import Backlink Audit CSV" ok="Toxic scores imported" onDone={() => tox.reload()} />}
        <Link className="btn ghost" to="/backlinks/semrush">Semrush</Link></>}>
        <ErrorBox error={tox.error} />
        {tox.loading && !tox.data ? <Loading /> : !items.length ? <Empty title="No toxic scores imported">In Semrush open Backlink Audit → export the table (CSV) and import it here. Rows are matched to your CRM links by source URL.</Empty> : (<>
          <div className="table-wrap"><table className="t"><thead><tr><th style={{ width: 28 }}></th><th>Source</th><th className="r">Score</th><th>Markers</th><th>In CRM</th><th>Disavow</th></tr></thead>
            <tbody>{items.map((t: any) => <tr key={t.id}>
              <td><input type="checkbox" aria-label={`Select ${t.source_domain}`} disabled={t.in_disavow} checked={sel.has(t.id)} onChange={() => setSel((s) => { const n = new Set(s); n.has(t.id) ? n.delete(t.id) : n.add(t.id); return n; })} /></td>
              <td><b>{t.source_domain}</b><div className="mono-url">{t.source_url}</div></td>
              <td className="r num" style={{ color: t.score >= 60 ? "var(--critical)" : t.score >= 45 ? "var(--medium)" : undefined }}>{t.score}</td>
              <td className="small muted">{t.markers || "—"}</td><td>{t.backlink_id ? <Badge kind="pass">yes</Badge> : <span className="faint small">no</span>}</td>
              <td>{t.in_disavow ? <Badge kind="medium">listed</Badge> : "—"}</td></tr>)}</tbody></table></div>
          {can("create") && <div className="bl-toolbar" style={{ borderTop: "1px solid var(--line-2)", borderBottom: 0 }}>
            <input className="input" style={{ width: 240 }} placeholder="List title" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="List title" />
            <label className="check"><input type="checkbox" checked={asDomains} onChange={(e) => setAsDomains(e.target.checked)} /> Disavow whole domains</label>
            <button className="btn primary" disabled={!sel.size || busy} onClick={create}>Create disavow draft ({sel.size})</button></div>}
        </>)}
      </Panel>
      <Panel pad={false} title="Disavow lists">
        {lists.loading && !lists.data ? <Loading /> : !lists.data?.length ? <Empty title="No disavow lists">Select toxic links above to draft one.</Empty> : (
          <table className="t"><thead><tr><th>List</th><th className="r">Domains</th><th className="r">URLs</th><th>Status</th><th>Created</th><th></th></tr></thead>
            <tbody>{lists.data.map((d) => <tr key={d.id}>
              <td>{d.title}<div className="faint small">{d.entries.slice(0, 4).map((e: any) => e.value).join(", ")}{d.entries.length > 4 ? "…" : ""}</div></td>
              <td className="r num">{d.domains}</td><td className="r num">{d.urls}</td>
              <td><Badge kind={d.status === "generated" || d.status === "approved" ? "pass" : d.status === "rejected" ? "rejected" : d.status === "pending_approval" ? "pending" : ""}>{d.status.replace("_", " ")}</Badge>
                {d.status === "pending_approval" && <div className="small"><Link to="/approvals">Awaiting manager in Approvals</Link></div>}</td>
              <td className="muted small">{fmtDate(d.created_at)}</td>
              <td className="r nowrap">
                {(d.status === "draft" || d.status === "rejected") && can("create") && <button className="btn sm" onClick={() => act(d.id, "submit")}>Request approval</button>}
                {d.status === "approved" && can("edit") && <button className="btn sm primary" onClick={() => act(d.id, "generate")}>Generate file</button>}
                {d.status === "generated" && <button className="btn sm" onClick={() => download(`/disavow/${d.id}/download`, "disavow.txt")}>Download .txt</button>}
              </td></tr>)}</tbody></table>)}
      </Panel>
    </div>
  );
}

