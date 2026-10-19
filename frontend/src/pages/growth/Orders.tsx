import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { fmtDate, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { MarketNav, money, Status, Tabs } from "./shared";

const STATUS = ["", "pending", "accepted", "in_progress", "delivered", "completed", "disputed", "rejected", "refunded"];

export default function Orders() {
  const [sp, setSp] = useSearchParams();
  const side = sp.get("side") || "buying";
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useApi<any>(`/marketplace/orders?side=${side}${status ? `&status=${status}` : ""}`, [side, status], { poll: 15000 });
  const open = sp.get("order");
  const items = data?.items || [];
  return (
    <>
      <PageHead title="Orders" sub="Guest-post, link-insertion and service orders. Funds stay in escrow until the live link is verified and the order is completed." />
      <MarketNav />
      <Tabs value={side} onChange={(s) => setSp({ side: s })} tabs={[["buying", "Buying (advertiser)"], ["selling", "Selling (publisher)"]]} />
      <Panel pad={false} title={<select className="input" style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">{STATUS.map((s) => <option key={s} value={s}>{s ? human(s) : "All statuses"}</option>)}</select>}
        actions={<span className="small muted">{Object.entries(data?.counts || {}).map(([k, v]) => `${human(k)} ${v}`).join(" · ")}</span>}>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !items.length ? <Empty title={side === "buying" ? "No orders yet" : "No orders for your listings yet"} action={side === "buying" ? <Link className="btn" to="/marketplace">Browse sites</Link> : <Link className="btn" to="/marketplace?tab=sell">Manage listings</Link>} /> : (
          <div className="table-wrap"><table className="t"><thead><tr><th>Order</th><th>Item</th><th>Status</th><th>Link check</th><th className="r">{side === "buying" ? "Amount" : "Your earning"}</th><th>Due</th></tr></thead>
            <tbody>{items.map((o: any) => (
              <tr key={o.id} className="click" onClick={() => setSp({ side, order: o.id })}>
                <td className="num">{o.number}</td><td><b>{o.title}</b><div className="faint small mono-url">{o.target_url}</div></td><td><Status s={o.status} /></td>
                <td>{o.live_url ? (o.verified ? <Badge kind="pass">verified</Badge> : o.verification?.status === "pending" ? <Badge kind="pending">checking</Badge> : <Badge kind="fail">failed</Badge>) : <span className="faint small">—</span>}</td>
                <td className="r num">{money(o.amount)}</td><td className="small">{fmtDate(o.tat_due)}</td></tr>))}</tbody></table></div>
        )}
      </Panel>
      {open && <OrderDrawer id={open} onClose={() => setSp({ side })} onChange={reload} />}
    </>
  );
}

function OrderDrawer({ id, onClose, onChange }: any) {
  const { data: o, error, reload } = useApi<any>(`/marketplace/orders/${id}`, [id]);
  const [live, setLive] = useState("");
  const [note, setNote] = useState("");
  const [brief, setBrief] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const { run, busy } = useAction();
  const can = useCan();
  const act = (action: string, extra: any = {}) => run(() => api(`/marketplace/orders/${id}/action`, { body: { action, note: note || undefined, ...extra } }), `Order ${human(action)}`).then(() => { setNote(""); reload(); onChange(); });
  if (error) return <Drawer title="Order" onClose={onClose}><ErrorBox error={error} /></Drawer>;
  if (!o) return <Drawer title="Order" onClose={onClose}><Loading /></Drawer>;
  const adv = o.side === "advertiser";
  const v = o.verification || {};
  return (
    <Drawer title={`${o.number} · ${o.title}`} onClose={onClose}>
      <div className="row" style={{ gap: 8, marginBottom: 12 }}><Status s={o.status} />{adv && o.escrow && <Badge kind={o.escrow === "held" ? "pending" : "low"}>escrow {o.escrow}</Badge>}</div>
      <dl className="kv" style={{ marginBottom: 14 }}>
        <dt>{adv ? "Amount (escrow)" : "Your earning"}</dt><dd className="num">{money(o.amount)}{!adv && <span className="faint small"> of {money(o.gross)} gross</span>}{adv && o.kind !== "service" && <span className="faint small"> · publisher {money(o.publisher_amount)} + fee {money(o.fee_amount)}</span>}</dd>
        <dt>Target URL</dt><dd className="mono-url">{o.target_url || "—"}</dd><dt>Anchor</dt><dd>{o.anchor || "—"}</dd>
        <dt>Due</dt><dd>{fmtDate(o.tat_due)}</dd>{adv && <><dt>Publisher</dt><dd>{o.publisher}</dd></>}
        {o.live_url && <><dt>Live URL</dt><dd><a href={o.live_url} target="_blank" rel="noreferrer nofollow">{o.live_url}</a></dd></>}
        {o.backlink_id && <><dt>Backlink CRM</dt><dd>recorded</dd></>}
      </dl>
      {o.live_url && <div className={`notice ${o.verified ? "" : v.status === "pending" ? "warn" : "err"}`} style={{ marginBottom: 14 }}><div>
        <b>{o.verified ? "Link verified" : v.status === "pending" ? "Verifying the live link…" : "Link verification failed"}</b>
        {v.checked_at && <div className="small">HTTP {v.status ?? "—"} · {v.found ? `link found (${v.rel}, anchor “${v.anchor}”)` : "link not found"}{v.noindex ? " · noindex" : ""} · checked {fmtDateTime(v.checked_at)}</div>}
        {(v.issues || []).map((i: string) => <div key={i} className="small">• {i}</div>)}
        {can("edit") && ["delivered", "disputed"].includes(o.status) && <button className="btn sm" style={{ marginTop: 6 }} onClick={() => run(() => api(`/marketplace/orders/${id}/verify`, { method: "POST" }), "Re-check queued").then(reload)}>Re-check now</button>}
      </div></div>}
      <h3 className="small muted">Brief</h3>
      {brief != null ? <div className="form"><textarea className="input" rows={5} value={brief} onChange={(e) => setBrief(e.target.value)} />
        <div className="row"><button className="btn sm primary" onClick={() => run(() => api(`/marketplace/orders/${id}/brief`, { body: { brief } }), "Brief updated").then(() => { setBrief(null); reload(); })}>Save brief</button><button className="btn sm ghost" onClick={() => setBrief(null)}>Cancel</button></div></div>
        : <><p className="small" style={{ whiteSpace: "pre-wrap" }}>{o.brief || <span className="muted">No brief yet.</span>}</p>
          {adv && can("edit") && ["pending", "accepted", "in_progress"].includes(o.status) && <button className="btn sm" onClick={() => setBrief(o.brief || "")}>Edit brief</button>}</>}
      <h3 className="small muted" style={{ marginTop: 12 }}>Attachments</h3>
      <ul className="small">{o.attachments.map((a: any) => <li key={a.id}><button className="btn ghost sm" onClick={() => download(`/marketplace/orders/${id}/attachments/${a.id}`, a.filename)}>{a.filename}</button></li>)}</ul>
      {adv && can("edit") && <><input ref={file} type="file" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) { const fd = new FormData(); fd.append("file", f); run(() => api(`/marketplace/orders/${id}/attachments`, { form: fd }), "Attached").then(reload); } e.target.value = ""; }} />
        <button className="btn sm" onClick={() => file.current?.click()}>Attach file</button></>}

      {can("edit") && <div className="panel" style={{ padding: 12, marginTop: 16 }}>
        <b className="small">Actions</b>
        <input className="input" style={{ marginTop: 8 }} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note / reason (required to reject or dispute)" aria-label="Note" />
        <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
          {!adv && o.status === "pending" && <><button className="btn primary sm" disabled={busy} onClick={() => act("accept")}>Accept</button><button className="btn danger sm" disabled={busy || !note} onClick={() => act("reject")}>Reject (refunds buyer)</button></>}
          {!adv && o.status === "accepted" && <button className="btn sm" onClick={() => act("start")}>Mark in progress</button>}
          {!adv && ["accepted", "in_progress", "delivered"].includes(o.status) && <div className="row" style={{ gap: 6, width: "100%" }}>
            <input className="input" style={{ flex: 1 }} value={live} onChange={(e) => setLive(e.target.value)} placeholder="Live URL of the published post" aria-label="Live URL" />
            <button className="btn primary sm" disabled={!live || busy} onClick={() => act("deliver", { live_url: live })}>{o.status === "delivered" ? "Re-deliver" : "Deliver"}</button></div>}
          {adv && o.status === "pending" && <button className="btn sm" onClick={() => confirm("Cancel and refund this order?") && act("cancel")}>Cancel & refund</button>}
          {adv && o.status === "delivered" && <button className="btn primary sm" disabled={!o.verified || busy} title={o.verified ? "" : "Available once the link is verified"} onClick={() => act("complete")}>Complete & release payment</button>}
          {adv && ["accepted", "in_progress", "delivered"].includes(o.status) && <button className="btn danger sm" disabled={!note} onClick={() => act("dispute")}>Open dispute</button>}
          {adv && o.listing_id && <Link className="btn ghost sm" to={`/marketplace?tab=browse`}>Report / block publisher</Link>}
        </div>
        {o.kind === "service" && adv && <p className="faint small" style={{ marginTop: 6 }}>Service orders are fulfilled by the Genranq team.</p>}
        {o.status === "disputed" && <p className="faint small" style={{ marginTop: 6 }}>A Genranq administrator reviews disputes and either refunds or releases the escrow.</p>}
      </div>}
      <h3 className="small muted" style={{ marginTop: 16 }}>Timeline</h3>
      <ul className="gq-timeline">{[...o.timeline].reverse().map((t: any, i: number) => <li key={i}><div>{human(t.action)} <span className="faint small">by {t.by}</span></div>{t.note && <div className="small">{t.note}</div>}<div className="faint small">{fmtDateTime(t.at)}</div></li>)}</ul>
    </Drawer>
  );
}
