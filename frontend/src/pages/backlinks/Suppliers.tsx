import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate, fmtDateTime } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { Files } from "./Backlinks";
import { money, pct, StatusBadge, Tabs } from "./shared";
import "./backlinks.css";

const Stars = ({ r }: { r: number | null }) => r == null ? <span className="faint">—</span> :
  <span title={`${r} of 5`} aria-label={`Rating ${r} of 5`} style={{ color: "var(--medium)", letterSpacing: 1 }}>{"★".repeat(Math.round(r))}<span style={{ color: "var(--line)" }}>{"★".repeat(5 - Math.round(r))}</span></span>;

export default function Suppliers() {
  const [sp] = useSearchParams();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useApi<any[]>(`/suppliers?${new URLSearchParams({ ...(q ? { q } : {}), ...(status ? { status } : {}) })}`, [q, status]);
  const [open, setOpen] = useState<string | null>(sp.get("supplier"));
  const [edit, setEdit] = useState<any | null | undefined>(undefined);
  const can = useCan();
  return (
    <>
      <PageHead title="Suppliers" sub="Link vendors and publishers: contacts, price lists, communication history, ratings, invoices and the links they placed. Logins live in the encrypted vault.">
        <Link className="btn" to="/backlinks">Backlinks</Link>
        {can("create") && <button className="btn primary" onClick={() => setEdit(null)}>Add supplier</button>}
      </PageHead>
      <Panel pad={false} title={`Suppliers${data ? ` (${data.length})` : ""}`} actions={<>
        <input className="input" style={{ width: 200 }} placeholder="Search name, niche, email" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search suppliers" />
        <select className="input" style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status"><option value="">All</option><option value="active">Active</option><option value="paused">Paused</option><option value="blacklisted">Blacklisted</option></select></>}>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !data?.length ? <Empty title="No suppliers yet" action={can("create") && <button className="btn primary" onClick={() => setEdit(null)}>Add supplier</button>}>Add the vendors you buy links from to track spend, retention and price lists per supplier.</Empty> : (
          <div className="table-wrap"><table className="t"><thead><tr><th>Supplier</th><th>Contact</th><th>Niche</th><th>Rating</th><th className="r">Links</th><th className="r">Live</th><th className="r">Retention</th><th className="r">Spend</th><th>Last contact</th><th>Status</th></tr></thead>
            <tbody>{data.map((s) => (
              <tr key={s.id} className="click" onClick={() => setOpen(s.id)}>
                <td><b>{s.name}</b>{s.website && <div className="mono-url">{s.website}</div>}</td>
                <td>{s.contact_name || "—"}<div className="faint small">{s.email || ""}</div></td><td className="small">{s.niche || "—"}</td><td><Stars r={s.rating} /></td>
                <td className="r num">{s.stats.links}</td><td className="r num">{s.stats.live}</td><td className="r num">{pct(s.stats.retention)}</td>
                <td className="r num">{Object.entries(s.stats.spend).map(([c, v]: any) => money(v, c)).join(" · ") || "—"}</td>
                <td className="muted small">{ago(s.last_contact)}</td>
                <td><Badge kind={s.status === "active" ? "pass" : s.status === "blacklisted" ? "fail" : "medium"}>{s.status}</Badge></td></tr>))}</tbody></table></div>)}
      </Panel>
      {edit !== undefined && <SupplierForm s={edit} onClose={() => setEdit(undefined)} onDone={reload} />}
      {open && <SupplierDetail id={open} onClose={() => setOpen(null)} onEdit={(s: any) => setEdit(s)} onChange={reload} />}
    </>
  );
}

function SupplierForm({ s, onClose, onDone }: { s: any | null; onClose: () => void; onDone: () => void }) {
  const p = s?.profile || {};
  const [f, setF] = useState<any>({ name: s?.name || "", email: s?.email || "", phone: s?.phone || "", country: s?.country || "", niche: s?.niche || "",
    rating: s?.rating ?? "", contact_name: p.contact_name || "", website: p.website || "", payment_terms: p.payment_terms || "", payment_method: p.payment_method || "",
    status: p.status || "active", tags: (p.tags || []).join(", "), notes: p.notes || "" });
  const { run, busy } = useAction();
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    const body = { ...Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v === "" ? null : v])), rating: f.rating === "" ? null : Number(f.rating),
      tags: String(f.tags).split(",").map((x) => x.trim()).filter(Boolean) };
    const r = await run(() => s ? api(`/suppliers/${s.id}`, { method: "PATCH", body }) : api("/suppliers", { body }), s ? "Supplier saved" : "Supplier added");
    if (r) { onDone(); onClose(); }
  };
  return (
    <Modal wide title={s ? `Edit ${s.name}` : "Add supplier"} onClose={onClose} foot={<button className="btn primary" disabled={busy || !f.name.trim()} onClick={save}>Save</button>}>
      <div className="form">
        <div className="grid g2"><label className="field">Name<input className="input" value={f.name} onChange={set("name")} autoFocus /></label>
          <label className="field">Website<input className="input" value={f.website} onChange={set("website")} placeholder="https://" /></label></div>
        <div className="grid g3"><label className="field">Contact person<input className="input" value={f.contact_name} onChange={set("contact_name")} /></label>
          <label className="field">Email<input className="input" type="email" value={f.email} onChange={set("email")} /></label>
          <label className="field">Phone<input className="input" value={f.phone} onChange={set("phone")} /></label></div>
        <div className="grid g3"><label className="field">Country<input className="input" value={f.country} onChange={set("country")} /></label>
          <label className="field">Niche<input className="input" value={f.niche} onChange={set("niche")} /></label>
          <label className="field">Rating (0–5)<input className="input" type="number" min={0} max={5} step={0.5} value={f.rating} onChange={set("rating")} /></label></div>
        <div className="grid g3"><label className="field">Payment terms<input className="input" value={f.payment_terms} onChange={set("payment_terms")} placeholder="e.g. 50% upfront" /></label>
          <label className="field">Payment method<input className="input" value={f.payment_method} onChange={set("payment_method")} /></label>
          <label className="field">Status<select className="input" value={f.status} onChange={set("status")}><option>active</option><option>paused</option><option>blacklisted</option></select></label></div>
        <label className="field">Tags<span className="hint">Comma separated</span><input className="input" value={f.tags} onChange={set("tags")} /></label>
        <label className="field">Notes<textarea className="input" rows={3} value={f.notes} onChange={set("notes")} /></label>
      </div>
    </Modal>
  );
}

function SupplierDetail({ id, onClose, onEdit, onChange }: any) {
  const { data: s, error, reload } = useApi<any>(`/suppliers/${id}`, [id]);
  const [tab, setTab] = useState("overview");
  const { run } = useAction();
  const can = useCan();
  const refresh = () => { reload(); onChange(); };
  return (
    <Drawer title={s?.name || "Supplier"} onClose={onClose} foot={s && <>
      {can("edit") && <button className="btn primary" onClick={() => onEdit(s)}>Edit</button>}
      {can("delete") && <button className="btn danger" onClick={() => confirm(`Delete ${s.name}? Its price list, notes and files are removed and its backlinks unlinked.`) &&
        run(() => api(`/suppliers/${id}?confirm=true`, { method: "DELETE" }), "Supplier deleted").then((r) => { if (r) { onChange(); onClose(); } })}>Delete</button>}</>}>
      <ErrorBox error={error} />
      {!s ? <Loading /> : (<>
        <Tabs value={tab} onChange={setTab} tabs={[["overview", "Overview"], ["prices", `Prices (${s.prices.length})`], ["notes", `History (${s.notes.length})`], ["links", `Links (${s.backlinks.length})`], ["files", "Files & logins"]]} />
        {tab === "overview" && <div className="stack">
          <div className="grid g3">
            <div className="panel kpi"><div className="v">{s.stats.links}</div><div className="l">Links</div></div>
            <div className="panel kpi"><div className="v">{pct(s.stats.retention)}</div><div className="l">Retention (live / checked)</div></div>
            <div className="panel kpi"><div className="v" style={{ fontSize: 20 }}>{Object.entries(s.stats.spend).map(([c, v]: any) => money(v, c)).join(" · ") || "—"}</div><div className="l">Spend</div></div>
          </div>
          <dl className="bl-dl">
            <dt>Rating</dt><dd><Stars r={s.rating} /></dd><dt>Status</dt><dd><Badge>{s.profile.status}</Badge></dd>
            <dt>Contact</dt><dd>{s.profile.contact_name || "—"}{s.email && <> · <a href={`mailto:${s.email}`}>{s.email}</a></>}{s.phone && <> · {s.phone}</>}</dd>
            <dt>Website</dt><dd>{s.profile.website ? <a href={s.profile.website} target="_blank" rel="noreferrer noopener">{s.profile.website}</a> : "—"}</dd>
            <dt>Country · niche</dt><dd>{s.country || "—"} · {s.niche || "—"}</dd>
            <dt>Payment</dt><dd>{[s.profile.payment_terms, s.profile.payment_method].filter(Boolean).join(" · ") || "—"}</dd>
            {s.profile.tags?.length > 0 && <><dt>Tags</dt><dd>{s.profile.tags.map((t: string) => <span key={t} className="bl-chip">{t}</span>)}</dd></>}
            {s.profile.notes && <><dt>Notes</dt><dd>{s.profile.notes}</dd></>}
          </dl></div>}
        {tab === "prices" && <Prices s={s} onChange={refresh} />}
        {tab === "notes" && <Notes s={s} onChange={refresh} />}
        {tab === "links" && (!s.backlinks.length ? <p className="muted">No backlinks from this supplier yet.</p> :
          <table className="t"><thead><tr><th>Source</th><th>Status</th><th className="r">Cost</th></tr></thead>
            <tbody>{s.backlinks.map((b: any) => <tr key={b.id}><td><Link to={`/backlinks?project=${b.project_id}&backlink=${b.id}`}>{b.source_domain}</Link><div className="faint small">{b.project}</div></td>
              <td><StatusBadge s={b.status} /></td><td className="r num">{money(b.cost, b.currency)}</td></tr>)}</tbody></table>)}
        {tab === "files" && <div className="stack">
          <Files files={s.files} fields={{ supplier_id: id }} onChange={reload} />
          <div><h3 style={{ marginBottom: 6 }}>Logins</h3>
            {s.can_access_credentials ? (<>
              {!s.credentials.length ? <p className="muted small">No logins stored for this supplier.</p> :
                <table className="t"><tbody>{s.credentials.map((c: any) => <tr key={c.id}><td>{c.label}<div className="faint small">{c.username || ""}</div></td><td className="mono-url">{c.masked_hint}</td><td className="muted small">{c.rotated_at ? `rotated ${fmtDate(c.rotated_at)}` : `added ${fmtDate(c.created_at)}`}</td></tr>)}</tbody></table>}
              <p className="small muted" style={{ marginTop: 8 }}>Secrets are never shown here. Add, reveal (with re-authentication) or rotate them in the <Link to="/vault">Vault</Link> and pick this supplier.</p></>)
              : <p className="small muted">{s.credential_count} login{s.credential_count === 1 ? "" : "s"} stored in the vault. Your role can’t access credentials.</p>}
          </div></div>}
      </>)}
    </Drawer>
  );
}

function Prices({ s, onChange }: any) {
  const [f, setF] = useState({ domain: "", service: "guest_post", price: "", currency: "USD", dr: "", link_type: "dofollow", turnaround_days: "" });
  const { run, busy } = useAction();
  const can = useCan();
  const add = async () => {
    const r = await run(() => api(`/suppliers/${s.id}/prices`, { body: { ...f, price: Number(f.price), dr: f.dr ? Number(f.dr) : null, turnaround_days: f.turnaround_days ? Number(f.turnaround_days) : null } }), "Price added");
    if (r) { setF({ ...f, domain: "", price: "", dr: "", turnaround_days: "" }); onChange(); }
  };
  return (<div className="stack">
    {!s.prices.length ? <p className="muted">No price list yet.</p> : (
      <table className="t"><thead><tr><th>Domain</th><th>Service</th><th className="r">Price</th><th className="r">DR</th><th>Type</th><th className="r">TAT</th><th></th></tr></thead>
        <tbody>{s.prices.map((p: any) => <tr key={p.id}><td>{p.domain}</td><td className="small">{p.service.replace("_", " ")}</td><td className="r num">{money(p.price, p.currency)}</td>
          <td className="r num">{p.dr ?? "—"}</td><td className="small">{p.link_type || "—"}</td><td className="r num">{p.turnaround_days != null ? `${p.turnaround_days}d` : "—"}</td>
          <td>{can("edit") && <button className="btn sm ghost danger" onClick={() => run(() => api(`/supplier-prices/${p.id}`, { method: "DELETE" })).then(onChange)}>Remove</button>}</td></tr>)}</tbody></table>)}
    {can("edit") && <form className="form" onSubmit={(e) => { e.preventDefault(); add(); }}>
      <div className="grid g3"><input className="input" placeholder="domain.com" value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })} aria-label="Domain" />
        <select className="input" value={f.service} onChange={(e) => setF({ ...f, service: e.target.value })} aria-label="Service"><option value="guest_post">Guest post</option><option value="link_insertion">Link insertion</option><option value="niche_edit">Niche edit</option><option value="other">Other</option></select>
        <div className="row" style={{ flexWrap: "nowrap" }}><input className="input" inputMode="decimal" placeholder="Price" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} aria-label="Price" />
          <input className="input" style={{ width: 70 }} maxLength={3} value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value.toUpperCase() })} aria-label="Currency" /></div></div>
      <div className="grid g3"><input className="input" placeholder="DR" inputMode="numeric" value={f.dr} onChange={(e) => setF({ ...f, dr: e.target.value })} aria-label="DR" />
        <select className="input" value={f.link_type} onChange={(e) => setF({ ...f, link_type: e.target.value })} aria-label="Link type"><option>dofollow</option><option>nofollow</option><option>sponsored</option><option>ugc</option></select>
        <input className="input" placeholder="Turnaround (days)" inputMode="numeric" value={f.turnaround_days} onChange={(e) => setF({ ...f, turnaround_days: e.target.value })} aria-label="Turnaround days" /></div>
      <div><button className="btn primary" disabled={busy || !f.domain || !f.price}>Add price</button></div>
    </form>}
  </div>);
}

function Notes({ s, onChange }: any) {
  const [f, setF] = useState({ kind: "email", direction: "outbound", subject: "", body: "" });
  const { run, busy } = useAction();
  const can = useCan();
  return (<div className="stack">
    {can("create") && <form className="form" onSubmit={(e) => { e.preventDefault(); run(() => api(`/suppliers/${s.id}/notes`, { body: f }), "Logged").then((r) => { if (r) { setF({ ...f, subject: "", body: "" }); onChange(); } }); }}>
      <div className="row"><select className="input" style={{ width: "auto" }} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} aria-label="Kind">{["email", "call", "meeting", "chat", "note"].map((k) => <option key={k}>{k}</option>)}</select>
        <select className="input" style={{ width: "auto" }} value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })} aria-label="Direction"><option value="outbound">Outbound</option><option value="inbound">Inbound</option></select>
        <input className="input" style={{ flex: 1 }} placeholder="Subject" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} aria-label="Subject" /></div>
      <textarea className="input" rows={3} placeholder="What was said or agreed" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} aria-label="Body" />
      <div><button className="btn primary" disabled={busy || !f.body.trim()}>Log</button></div></form>}
    {!s.notes.length ? <p className="muted">No communication logged yet.</p> : <ul className="bl-timeline">{s.notes.map((n: any) => (
      <li key={n.id}><div className="row"><Badge>{n.kind}</Badge>{n.direction && <span className="faint small">{n.direction}</span>}<b>{n.subject}</b><span className="spacer" /><span className="muted small">{fmtDateTime(n.occurred_at)}</span>
        {can("delete") && <button className="btn sm ghost" aria-label="Delete note" onClick={() => run(() => api(`/supplier-notes/${n.id}`, { method: "DELETE" })).then(onChange)}>✕</button>}</div>
        <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{n.body}</div></li>))}</ul>}
  </div>);
}
