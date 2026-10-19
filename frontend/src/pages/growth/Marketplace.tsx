import { Fragment, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { MarketNav, money, NC, num, Status, Tabs } from "./shared";

const METRICS: [string, string][] = [["dr", "DR"], ["da", "DA"], ["as", "AS"], ["cf", "CF"], ["tf", "TF"], ["spam", "Spam"]];

function Metric({ m, label }: { m: any; label: string }) {
  return <span className="gq-metric" title={m?.verified ? `Verified via ${m.source}` : "Publisher-declared (self-reported)"}><b className="num">{m?.value ?? "—"}</b>
    <small>{label}{m?.value != null && (m.verified ? <span className="gq-verified"> ✓</span> : <span className="gq-selfrep">*</span>)}</small></span>;
}

export default function Marketplace() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "browse";
  const { data: cart, reload: reloadCart } = useApi<any>("/marketplace/cart");
  return (
    <>
      <PageHead title="Backlink marketplace" sub="Advertisers browse publisher sites and buy guest posts or link insertions; publishers list their sites. Payment is held in escrow until the live link is verified.">
        <button className="btn" onClick={() => setSp({ tab: "cart" })}>Cart ({cart?.items?.length ?? 0}) · {money(cart?.total)}</button>
      </PageHead>
      <MarketNav />
      <Tabs value={tab} onChange={(t) => setSp({ tab: t })} tabs={[["browse", "Browse sites"], ["cart", `Cart (${cart?.items?.length ?? 0})`], ["sell", "My listings (publisher)"]]} />
      {tab === "browse" && <Browse onCart={reloadCart} />}
      {tab === "cart" && <Cart data={cart} reload={reloadCart} />}
      {tab === "sell" && <MyListings />}
      <p className="faint small" style={{ marginTop: 12 }}>* self-reported by the publisher · ✓ verified via Ahrefs/Semrush</p>
    </>
  );
}

const EMPTY_F = { q: "", category: "", country: "", lang: "", dr_min: "", da_min: "", traffic_min: "", spam_max: "", price_min: "", price_max: "", tat_max: "", link_type: "", verified_only: false };

function Browse({ onCart }: any) {
  const [f, setF] = useState<any>(EMPTY_F);
  const [applied, setApplied] = useState<any>(EMPTY_F);
  const [sort, setSort] = useState("dr");
  const [order, setOrder] = useState("desc");
  const [ptype, setPtype] = useState("guest_post");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const qs = new URLSearchParams(Object.fromEntries(Object.entries({ ...applied, sort, order, price_type: ptype, page: String(page) }).filter(([, v]) => v !== "" && v !== false).map(([k, v]) => [k, String(v)]))).toString();
  const { data, error, loading, reload } = useApi<any>(`/marketplace/listings?${qs}`, [qs]);
  const items = data?.items || [];
  const setSortCol = (c: string) => { if (sort === c) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(c); setOrder(c === "price" || c === "spam" || c === "tat" ? "asc" : "desc"); } };
  const Th = ({ c, children }: any) => <th className="r click" onClick={() => setSortCol(c)} aria-sort={sort === c ? (order === "asc" ? "ascending" : "descending") : undefined}>{children}{sort === c ? (order === "asc" ? " ▲" : " ▼") : ""}</th>;
  return (
    <>
      <Panel>
        <form className="gq-filters" onSubmit={(e) => { e.preventDefault(); setPage(1); setApplied(f); }}>
          <label style={{ gridColumn: "span 2" }}>Search<input className="input" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="domain, niche, description" /></label>
          <label>Category<select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}><option value="">Any</option>{(data?.facets?.categories || []).map((c: string) => <option key={c}>{c}</option>)}</select></label>
          <label>Country<select className="input" value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })}><option value="">Any</option>{(data?.facets?.countries || []).map((c: string) => <option key={c}>{c}</option>)}</select></label>
          <label>Language<select className="input" value={f.lang} onChange={(e) => setF({ ...f, lang: e.target.value })}><option value="">Any</option>{(data?.facets?.langs || []).map((c: string) => <option key={c}>{c}</option>)}</select></label>
          {([["dr_min", "DR ≥"], ["da_min", "DA ≥"], ["traffic_min", "Traffic ≥"], ["spam_max", "Spam ≤"], ["price_min", "Price ≥"], ["price_max", "Price ≤"], ["tat_max", "TAT ≤ days"]] as [string, string][]).map(([k, l]) =>
            <label key={k}>{l}<input className="input" inputMode="numeric" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>)}
          <label>Link type<select className="input" value={f.link_type} onChange={(e) => setF({ ...f, link_type: e.target.value })}><option value="">Any</option>{["dofollow", "nofollow", "sponsored", "ugc"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label>Price for<select className="input" value={ptype} onChange={(e) => setPtype(e.target.value)}><option value="guest_post">Guest post</option><option value="link_insertion">Link insertion</option></select></label>
          <label className="check" style={{ flexDirection: "row", alignSelf: "end" }}><input type="checkbox" checked={f.verified_only} onChange={(e) => setF({ ...f, verified_only: e.target.checked })} /> Verified metrics only</label>
          <div className="row" style={{ alignSelf: "end", gap: 6 }}><button className="btn primary">Apply</button><button type="button" className="btn ghost" onClick={() => { setF(EMPTY_F); setApplied(EMPTY_F); }}>Reset</button></div>
        </form>
      </Panel>
      <div style={{ marginTop: 16 }}><Panel pad={false} title={`${data?.total ?? 0} sites`} actions={<button className="btn primary sm" disabled={!sel.size} onClick={() => setBulk(true)}>Add {sel.size || ""} selected to cart</button>}>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !items.length ? <Empty title="No sites match">Loosen the filters — or list your own sites under “My listings”.</Empty> : (
          <div className="table-wrap"><table className="t"><thead><tr><th><input type="checkbox" aria-label="Select all" checked={items.every((i: any) => sel.has(i.id) || i.own)} onChange={(e) => setSel(e.target.checked ? new Set(items.filter((i: any) => !i.own).map((i: any) => i.id)) : new Set())} /></th>
            <th>Site</th><Th c="dr">Authority</Th><Th c="traffic">Traffic</Th><Th c="ref_domains">Ref. domains</Th><Th c="tat">TAT</Th><Th c="price">Price</Th></tr></thead>
            <tbody>{items.map((l: any) => (
              <tr key={l.id}>
                <td><input type="checkbox" disabled={l.own} checked={sel.has(l.id)} aria-label={`Select ${l.domain}`} onChange={(e) => { const s = new Set(sel); e.target.checked ? s.add(l.id) : s.delete(l.id); setSel(s); }} /></td>
                <td className="click" onClick={() => setOpen(l.id)}><b>{l.domain}</b> {l.own && <Badge>yours</Badge>}{l.in_cart && <Badge kind="pass">in cart</Badge>}
                  <div className="faint small">{[l.category, l.country, l.lang, l.link_type].filter(Boolean).join(" · ")}</div></td>
                <td className="r"><div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>{METRICS.map(([k, lab]) => <Metric key={k} m={l.metrics[k]} label={lab} />)}</div></td>
                <td className="r num">{num(l.metrics.org_traffic.value)}</td><td className="r num">{num(l.metrics.ref_domains.value)}</td><td className="r num">{l.tat ? `${l.tat} d` : "—"}</td>
                <td className="r num"><b>{money(ptype === "guest_post" ? l.guest_post_price : l.link_ins_price)}</b><div className="faint small">{ptype === "guest_post" ? (l.link_ins_price ? `LI ${money(l.link_ins_price)}` : "") : (l.guest_post_price ? `GP ${money(l.guest_post_price)}` : "")}</div></td>
              </tr>))}</tbody></table></div>
        )}
        {data && data.total > data.page_size && <div className="row" style={{ padding: 12, justifyContent: "flex-end" }}>
          <button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span className="small muted">Page {page} of {Math.ceil(data.total / data.page_size)}</span>
          <button className="btn sm" disabled={page * data.page_size >= data.total} onClick={() => setPage(page + 1)}>Next</button></div>}
      </Panel></div>
      {bulk && <AddToCart ids={[...sel]} kind={ptype} onClose={() => setBulk(false)} onDone={() => { setSel(new Set()); onCart(); reload(); }} />}
      {open && <ListingDrawer id={open} onClose={() => setOpen(null)} onCart={() => { onCart(); reload(); }} />}
    </>
  );
}

function AddToCart({ ids, kind, onClose, onDone }: any) {
  const { data: projects } = useApi<any[]>("/projects");
  const [f, setF] = useState({ target_url: "", anchor: "", project_id: "", notes: "", kind });
  const { run, busy } = useAction();
  return (
    <Modal title={`Add ${ids.length} site${ids.length === 1 ? "" : "s"} to cart`} onClose={onClose} foot={<button className="btn primary" disabled={busy || !f.target_url} onClick={() =>
      run<any>(() => api("/marketplace/cart", { body: { items: ids.map((id: string) => ({ listing_id: id, ...f, project_id: f.project_id || null })) } }), "Added to cart").then((r) => { if (r) { onDone(); onClose(); } })}>Add to cart</button>}>
      <div className="form">
        <div className="seg"><button className={f.kind === "guest_post" ? "on" : ""} onClick={() => setF({ ...f, kind: "guest_post" })}>Guest post</button><button className={f.kind === "link_insertion" ? "on" : ""} onClick={() => setF({ ...f, kind: "link_insertion" })}>Link insertion</button></div>
        <label className="field">Target URL<input className="input" value={f.target_url} onChange={(e) => setF({ ...f, target_url: e.target.value })} placeholder="https://yoursite.com/page" autoFocus /></label>
        <label className="field">Anchor text<input className="input" value={f.anchor} onChange={(e) => setF({ ...f, anchor: e.target.value })} /></label>
        <label className="field">Project<span className="hint">Completed links are added to the project’s Backlink CRM when the target site is tracked there.</span>
          <select className="input" value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}><option value="">—</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label className="field">Brief / notes<textarea className="input" rows={3} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      </div>
    </Modal>
  );
}

function ListingDrawer({ id, onClose, onCart }: any) {
  const { data: l, error, reload } = useApi<any>(`/marketplace/listings/${id}`, [id]);
  const [add, setAdd] = useState(false);
  const [report, setReport] = useState("");
  const { run } = useAction();
  const can = useCan();
  if (error) return <Drawer title="Listing" onClose={onClose}><ErrorBox error={error} /></Drawer>;
  if (!l) return <Drawer title="Listing" onClose={onClose}><Loading /></Drawer>;
  return (
    <Drawer title={l.domain} onClose={onClose} foot={!l.own && can("create") ? <div className="row">
      <button className="btn primary" disabled={l.blocked} onClick={() => setAdd(true)}>Add to cart</button>
      {l.blocked ? <button className="btn" onClick={() => run(() => api(`/marketplace/publishers/${l.publisher_id}/block`, { method: "DELETE" }), "Publisher unblocked").then(reload)}>Unblock publisher</button>
        : <button className="btn" onClick={() => confirm("Block this publisher? Their sites will be hidden and removed from your cart.") && run(() => api(`/marketplace/publishers/${l.publisher_id}/block`, { method: "POST" }), "Publisher blocked").then(() => { reload(); onCart(); })}>Block publisher</button>}
    </div> : undefined}>
      <dl className="kv" style={{ marginBottom: 16 }}>
        <dt>Publisher</dt><dd>{l.publisher}</dd><dt>Category</dt><dd>{l.category || "—"}</dd><dt>Country / language</dt><dd>{l.country || "—"} / {l.lang || "—"}</dd>
        <dt>Guest post</dt><dd>{money(l.guest_post_price)}</dd><dt>Link insertion</dt><dd>{money(l.link_ins_price)}</dd>
        <dt>Link</dt><dd>{l.link_type || "—"}{l.link_validity ? ` · ${l.link_validity}` : ""}</dd><dt>Turnaround</dt><dd>{l.tat ? `${l.tat} days` : "—"}</dd>
        {Object.entries(l.metrics).map(([k, m]: any) => <Fragment key={k}><dt>{k.replace("_", " ").toUpperCase()}</dt><dd>{m.value ?? "—"} {m.value != null && (m.verified ? <Badge kind="pass">verified · {m.source}</Badge> : <Badge kind="low">self-reported</Badge>)}</dd></Fragment>)}
      </dl>
      {l.description && <p>{l.description}</p>}
      {l.rules && <div className="notice"><div><b>Publisher rules</b><div className="small">{l.rules}</div></div></div>}
      <h3 className="small muted" style={{ marginTop: 14 }}>Sample posts</h3>
      {l.sample_urls.length ? <ul className="small">{l.sample_urls.map((u: string) => <li key={u}><a href={u} target="_blank" rel="noreferrer nofollow">{u}</a></li>)}</ul> : <p className="muted small">No samples provided.</p>}
      {!l.own && can("create") && <form className="row" style={{ marginTop: 14 }} onSubmit={(e) => { e.preventDefault(); run(() => api(`/marketplace/listings/${id}/report`, { body: { reason: report } }), "Report sent to Genranq").then(() => setReport("")); }}>
        <input className="input" style={{ flex: 1 }} value={report} onChange={(e) => setReport(e.target.value)} placeholder="Report a problem with this publisher" minLength={3} required /><button className="btn">Report</button></form>}
      {add && <AddToCart ids={[id]} kind="guest_post" onClose={() => setAdd(false)} onDone={() => { onCart(); reload(); }} />}
    </Drawer>
  );
}

function Cart({ data, reload }: any) {
  const nav = useNavigate();
  const { run, busy } = useAction();
  const can = useCan();
  if (!data) return <Loading />;
  if (!data.items.length) return <Panel><Empty title="Your cart is empty" action={<Link className="btn" to="/marketplace">Browse sites</Link>}>Select sites or services and add them here.</Empty></Panel>;
  return (
    <div className="split">
      <Panel title="Items" pad={false}><div className="table-wrap"><table className="t"><thead><tr><th>Item</th><th>Target / anchor</th><th className="r">Price</th><th /></tr></thead>
        <tbody>{data.items.map((it: any) => <tr key={it.id}>
          <td><b>{it.title}</b>{!it.available && <div><Badge kind="rejected">unavailable</Badge></div>}{it.notes && <div className="faint small">{it.notes}</div>}</td>
          <td className="small">{it.kind !== "service" ? <>
            <input className="input" defaultValue={it.target_url || ""} placeholder="Target URL" onBlur={(e) => e.target.value !== (it.target_url || "") && run(() => api(`/marketplace/cart/${it.id}`, { method: "PATCH", body: { target_url: e.target.value } })).then(reload)} aria-label="Target URL" />
            <input className="input" style={{ marginTop: 4 }} defaultValue={it.anchor || ""} placeholder="Anchor text" onBlur={(e) => e.target.value !== (it.anchor || "") && run(() => api(`/marketplace/cart/${it.id}`, { method: "PATCH", body: { anchor: e.target.value } })).then(reload)} aria-label="Anchor" /></>
            : <span className="muted">{it.tier}{it.addons?.length ? ` + ${it.addons.join(", ")}` : ""}</span>}</td>
          <td className="r num">{money(it.price)}</td>
          <td><button className="btn ghost sm" onClick={() => run(() => api(`/marketplace/cart/${it.id}`, { method: "DELETE" })).then(reload)} aria-label="Remove">✕</button></td></tr>)}</tbody></table></div></Panel>
      <Panel title="Checkout">
        <dl className="kv"><dt>Total</dt><dd className="num"><b>{money(data.total)}</b></dd><dt>Wallet balance</dt><dd className="num">{money(data.balance)}</dd>
          {data.shortfall > 0 && <><dt>Shortfall</dt><dd className="num" style={{ color: "var(--critical)" }}>{money(data.shortfall)}</dd></>}</dl>
        <p className="muted small">Paying moves the amount into escrow. Publishers are paid only after the delivered link is verified and you complete the order; rejected or cancelled orders are refunded to your wallet.</p>
        {data.shortfall > 0 ? <Link className="btn primary" to="/wallet">Add funds</Link> :
          <button className="btn primary" disabled={busy || !can("create")} onClick={() => run<any>(() => api("/marketplace/checkout", { body: {} }), "Order placed").then((r) => { if (r) { reload(); nav("/marketplace/orders"); } })}>Pay {money(data.total)} from wallet</button>}
      </Panel>
    </div>
  );
}

const BLANK = { domain: "", url: "", category: "", lang: "en", country: "", description: "", rules: "", sample_urls: "", da: "", dr: "", as: "", cf: "", tf: "", spam: "", org_traffic: "", ref_domains: "", guest_post_price: "", link_ins_price: "", link_type: "dofollow", link_validity: "permanent", tat: "7", status: "active" };

function MyListings() {
  const { data, error, loading, reload } = useApi<any>("/marketplace/my-listings");
  const [edit, setEdit] = useState<any>(null);
  const { run } = useAction();
  const can = useCan();
  const verifyAvailable = data && (data.ahrefs === "CONNECTED" || data.semrush === "CONNECTED");
  return (
    <>
      {data && !verifyAvailable && <NC what="Ahrefs / Semrush" provider="ahrefs">aren’t connected, so metrics stay publisher-declared (shown as self-reported to buyers).</NC>}
      <Panel pad={false} title="Your sites" actions={can("create") && <button className="btn primary sm" onClick={() => setEdit({ ...BLANK })}>List a site</button>}>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !data?.items.length ? <Empty title="You haven’t listed any sites">List a site you own to receive guest-post and link-insertion orders. Earnings are credited to your wallet after each verified delivery.</Empty> : (
          <div className="table-wrap"><table className="t"><thead><tr><th>Site</th><th>Status</th><th>Metrics</th><th className="r">Guest post</th><th className="r">Link ins.</th><th className="r">Orders</th><th className="r">Earned</th><th /></tr></thead>
            <tbody>{data.items.map((l: any) => <tr key={l.id}>
              <td className="click" onClick={() => setEdit({ ...BLANK, ...Object.fromEntries(Object.entries(l).filter(([, v]) => v != null && typeof v !== "object")), sample_urls: l.sample_urls.join("\n"), ...Object.fromEntries(Object.entries(l.metrics).map(([k, m]: any) => [k, m.value ?? ""])) })}><b>{l.domain}</b><div className="faint small">{l.category}</div></td>
              <td><Status s={l.status} /></td><td><div className="row" style={{ gap: 8 }}>{METRICS.slice(0, 3).map(([k, lab]) => <Metric key={k} m={l.metrics[k]} label={lab} />)}</div></td>
              <td className="r num">{money(l.guest_post_price)}</td><td className="r num">{money(l.link_ins_price)}</td><td className="r num">{l.orders}</td><td className="r num">{money(l.earned)}</td>
              <td>{can("edit") && <button className="btn sm" disabled={!verifyAvailable} title={verifyAvailable ? "Refresh metrics from Ahrefs/Semrush" : "Connect Ahrefs or Semrush"} onClick={() => run(() => api(`/marketplace/my-listings/${l.id}/verify-metrics`, { method: "POST" }), "Metrics verified").then(reload)}>Verify metrics</button>}</td></tr>)}</tbody></table></div>
        )}
      </Panel>
      {edit && <ListingForm l={edit} onClose={() => setEdit(null)} onDone={reload} />}
    </>
  );
}

function ListingForm({ l, onClose, onDone }: any) {
  const [f, setF] = useState<any>(l);
  const { run, busy } = useAction();
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const n = (v: any) => (v === "" || v == null ? null : Number(v));
  const body = { ...f, sample_urls: String(f.sample_urls || "").split("\n").map((x: string) => x.trim()).filter(Boolean),
    ...Object.fromEntries(["da", "dr", "as", "cf", "tf", "spam", "org_traffic", "ref_domains", "guest_post_price", "link_ins_price", "tat"].map((k) => [k, n(f[k])])) };
  ["id", "own", "publisher", "publisher_id", "verified_any", "updated_at", "metrics", "orders", "earned"].forEach((k) => delete body[k]);
  const save = () => run<any>(() => l.id ? api(`/marketplace/my-listings/${l.id}`, { method: "PATCH", body }) : api("/marketplace/my-listings", { body }), "Listing saved").then((r) => { if (r) { onDone(); onClose(); } });
  return (
    <Modal wide title={l.id ? `Edit ${l.domain}` : "List a site"} onClose={onClose} foot={<div className="row">
      {l.id && <button className="btn danger" onClick={() => confirm("Remove this listing? (Listings with orders are paused instead.)") && run(() => api(`/marketplace/my-listings/${l.id}`, { method: "DELETE" })).then(() => { onDone(); onClose(); })}>Remove</button>}
      <button className="btn primary" disabled={busy || !f.domain} onClick={save}>Save listing</button></div>}>
      <div className="form">
        <div className="grid g3"><label className="field">Domain<input className="input" value={f.domain} onChange={set("domain")} disabled={!!l.id} /></label>
          <label className="field">Category<input className="input" value={f.category} onChange={set("category")} /></label>
          <label className="field">Status<select className="input" value={f.status} onChange={set("status")}><option>active</option><option>paused</option></select></label></div>
        <div className="grid g3"><label className="field">Country<input className="input" value={f.country} onChange={set("country")} /></label>
          <label className="field">Language<input className="input" value={f.lang} onChange={set("lang")} /></label>
          <label className="field">Site URL<input className="input" value={f.url} onChange={set("url")} /></label></div>
        <b className="small">Metrics (declared — buyers see them as self-reported until verified)</b>
        <div className="grid g4">{["dr", "da", "as", "cf", "tf", "spam", "org_traffic", "ref_domains"].map((k) => <label key={k} className="field">{k.replace("_", " ").toUpperCase()}<input className="input" inputMode="numeric" value={f[k]} onChange={set(k)} /></label>)}</div>
        <div className="grid g4"><label className="field">Guest post price<input className="input" inputMode="decimal" value={f.guest_post_price} onChange={set("guest_post_price")} /></label>
          <label className="field">Link insertion price<input className="input" inputMode="decimal" value={f.link_ins_price} onChange={set("link_ins_price")} /></label>
          <label className="field">Link type<select className="input" value={f.link_type} onChange={set("link_type")}>{["dofollow", "nofollow", "sponsored", "ugc"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="field">Turnaround (days)<input className="input" inputMode="numeric" value={f.tat} onChange={set("tat")} /></label></div>
        <label className="field">Link validity<input className="input" value={f.link_validity} onChange={set("link_validity")} placeholder="permanent / 12 months" /></label>
        <label className="field">Description<textarea className="input" rows={2} value={f.description} onChange={set("description")} /></label>
        <label className="field">Rules for advertisers<textarea className="input" rows={2} value={f.rules} onChange={set("rules")} /></label>
        <label className="field">Sample post URLs<span className="hint">One per line</span><textarea className="input" rows={2} value={f.sample_urls} onChange={set("sample_urls")} /></label>
      </div>
    </Modal>
  );
}
