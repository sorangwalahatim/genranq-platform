import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api, download } from "../../lib/api";
import { fmtDate, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan, useMe } from "../../lib/hooks";
import { Kpi, MarketNav, money, NC, Status, Tabs } from "./shared";

export default function Wallet() {
  const [sp] = useSearchParams();
  const { data: w, error, loading, reload } = useApi<any>("/wallet");
  const [tab, setTab] = useState("ledger");
  const [modal, setModal] = useState<"" | "topup" | "adjust" | "payout">("");
  const can = useCan();
  const { me } = useMe();
  if (error) return <ErrorBox error={error} />;
  if (loading && !w) return <Loading />;
  if (!w) return null;
  return (
    <>
      <PageHead title="Wallet & balance" sub="Funds for marketplace and service orders, escrow, publisher earnings, payouts and invoices.">
        {can("export") && <button className="btn" onClick={() => download("/wallet/export.csv", "wallet.csv")}>Export CSV</button>}
        {can("manage_billing") && <button className="btn" onClick={() => setModal("payout")}>Request payout</button>}
        {(w.is_owner || w.platform_admin) && <button className="btn" onClick={() => setModal("adjust")}>Request manual credit</button>}
        {can("manage_billing") && <button className="btn primary" disabled={!w.stripe_connected} onClick={() => setModal("topup")}>Add funds</button>}
      </PageHead>
      <MarketNav />
      {sp.get("topup") === "success" && <div className="notice" style={{ marginBottom: 14 }}>Payment submitted. Your balance updates as soon as Stripe confirms it.</div>}
      {!w.stripe_connected && <NC what="Payment gateway (Stripe)" provider="stripe">is not connected — card top-ups are unavailable and no payment is ever simulated. An owner can request a manual credit (e.g. after a bank transfer); it is applied only after a Genranq platform administrator approves it.</NC>}
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <Kpi v={money(w.balance)} l="Available balance" /><Kpi v={money(w.escrow_held)} l="Held in escrow (your orders)" />
        <Kpi v={money(w.payouts_pending)} l="Payouts pending" /><Kpi v={w.consistent ? "✓ balanced" : "⚠ mismatch"} l={`Ledger check · platform fee ${w.fee_pct}%`} tone={w.consistent ? "pass" : "critical"} />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[["ledger", "Transactions"], ["escrow", `Escrow (${w.escrow.length})`], ["payouts", "Payouts"], ["adjust", "Top-ups & adjustments"], ["invoices", `Invoices (${w.invoices.length})`], ...(me?.platform_admin ? [["admin", "Platform queue"] as [string, string]] : [])]} />
      {tab === "ledger" && <Panel pad={false}>{!w.transactions.length ? <Empty title="No transactions yet" /> :
        <div className="table-wrap"><table className="t"><thead><tr><th>When</th><th>Type</th><th>Description</th><th className="r">Amount</th><th className="r">Balance</th></tr></thead>
          <tbody>{w.transactions.map((t: any) => <tr key={t.id}><td className="small">{fmtDateTime(t.created_at)}</td><td><Badge>{t.type}</Badge></td><td className="small">{t.reason}{t.order_id && <> · <Link to={`/marketplace/orders?order=${t.order_id}`}>order</Link></>}</td>
            <td className="r num" style={{ color: t.amount < 0 ? "var(--critical)" : "var(--pass)" }}>{t.amount > 0 ? "+" : ""}{money(t.amount)}</td><td className="r num">{money(t.balance_after)}</td></tr>)}</tbody></table></div>}</Panel>}
      {tab === "escrow" && <Panel pad={false}>{!w.escrow.length ? <Empty title="Nothing in escrow" /> :
        <table className="t"><thead><tr><th>Order</th><th>Since</th><th className="r">Amount</th></tr></thead><tbody>{w.escrow.map((e: any) => <tr key={e.id}><td><Link to={`/marketplace/orders?order=${e.order_id}`}>{e.number}</Link></td><td className="small">{fmtDate(e.created_at)}</td><td className="r num">{money(e.amount)}</td></tr>)}</tbody></table>}</Panel>}
      {tab === "payouts" && <Payouts w={w} reload={reload} />}
      {tab === "adjust" && <Panel pad={false}>{!w.topups.length && !w.adjustments.length ? <Empty title="No top-ups or adjustments" /> :
        <table className="t"><thead><tr><th>When</th><th>Kind</th><th className="r">Amount</th><th>Status</th><th>Detail</th></tr></thead><tbody>
          {w.topups.map((t: any) => <tr key={t.id}><td className="small">{fmtDateTime(t.created_at)}</td><td>Stripe top-up</td><td className="r num">{money(t.amount)}</td><td><Status s={t.status} /></td><td className="small">{t.checkout_url && t.status === "created" ? <a href={t.checkout_url}>Continue to checkout</a> : t.stripe_session_id}</td></tr>)}
          {w.adjustments.map((a: any) => <tr key={a.id}><td className="small">{fmtDateTime(a.created_at)}</td><td>Manual adjustment</td><td className="r num">{money(a.amount)}</td><td><Status s={a.status} /></td>
            <td className="small">{a.reason}{a.reference ? ` (${a.reference})` : ""}<div className="faint">requested by {a.requested_by_email}{a.decided_by_email ? ` · decided by ${a.decided_by_email}` : " · awaiting platform administrator"}</div></td></tr>)}
        </tbody></table>}</Panel>}
      {tab === "invoices" && <Panel pad={false}>{!w.invoices.length ? <Empty title="No invoices yet" /> :
        <table className="t"><thead><tr><th>Number</th><th>Date</th><th>Kind</th><th className="r">Amount</th><th /></tr></thead><tbody>{w.invoices.map((i: any) => <tr key={i.id}><td className="num">{i.number}</td><td className="small">{fmtDate(i.created_at)}</td><td>{human(i.kind)}</td><td className="r num">{money(i.amount)}</td>
          <td><button className="btn sm" onClick={() => download(`/wallet/invoices/${i.id}/pdf`, `${i.number}.pdf`)}>PDF</button></td></tr>)}</tbody></table>}</Panel>}
      {tab === "admin" && me?.platform_admin && <PlatformQueue />}
      {modal === "topup" && <AmountModal title="Add funds with Stripe" min={10} label="Amount (USD)" cta="Continue to Stripe Checkout" onClose={() => setModal("")}
        submit={(amount: number) => api<any>("/wallet/topups", { body: { amount } }).then((r) => { if (r.checkout_url) window.location.href = r.checkout_url; })} />}
      {modal === "adjust" && <AdjustModal onClose={() => setModal("")} onDone={reload} />}
      {modal === "payout" && <PayoutModal balance={w.balance} onClose={() => setModal("")} onDone={reload} />}
    </>
  );
}

function AmountModal({ title, min, label, cta, onClose, submit }: any) {
  const [v, setV] = useState("");
  const { run, busy } = useAction();
  return <Modal title={title} onClose={onClose} foot={<button className="btn primary" disabled={busy || Number(v) < min} onClick={() => run(() => submit(Number(v)))}>{cta}</button>}>
    <label className="field">{label}<input className="input" inputMode="decimal" value={v} onChange={(e) => setV(e.target.value)} autoFocus /></label></Modal>;
}

function AdjustModal({ onClose, onDone }: any) {
  const [f, setF] = useState({ amount: "", reason: "", reference: "" });
  const { run, busy } = useAction();
  return <Modal title="Request a manual wallet credit" onClose={onClose} foot={<button className="btn primary" disabled={busy || !f.amount || f.reason.length < 5}
    onClick={() => run(() => api("/wallet/adjustments", { body: { amount: Number(f.amount), reason: f.reason, reference: f.reference || null } }), "Request sent for approval").then(() => { onDone(); onClose(); })}>Send for approval</button>}>
    <div className="form"><p className="muted small">Use this for offline payments (e.g. bank transfer). Nothing is credited until a Genranq platform administrator — not you — approves it.</p>
      <label className="field">Amount (USD)<input className="input" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></label>
      <label className="field">Reason<input className="input" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} placeholder="e.g. Bank transfer received 3 Oct" /></label>
      <label className="field">Reference<input className="input" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></label></div></Modal>;
}

function PayoutModal({ balance, onClose, onDone }: any) {
  const [f, setF] = useState({ amount: "", destination: "" });
  const { run, busy } = useAction();
  return <Modal title="Request a payout" onClose={onClose} foot={<button className="btn primary" disabled={busy || !f.amount || f.destination.length < 3}
    onClick={() => run(() => api("/wallet/payouts", { body: { amount: Number(f.amount), destination: f.destination } }), "Payout requested").then(() => { onDone(); onClose(); })}>Request payout</button>}>
    <div className="form"><p className="muted small">Available: {money(balance)}. The amount is held immediately; another owner must approve it. Automatic transfers are NOT CONNECTED — the Genranq team pays approved payouts manually and records the reference.</p>
      <label className="field">Amount (USD)<input className="input" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></label>
      <label className="field">Destination<input className="input" value={f.destination} onChange={(e) => setF({ ...f, destination: e.target.value })} placeholder="e.g. Business account ending 4421" /></label></div></Modal>;
}

function Payouts({ w, reload }: any) {
  const { run } = useAction();
  const { me } = useMe();
  if (!w.payouts.length) return <Panel><Empty title="No payout requests" /></Panel>;
  return <Panel pad={false}><table className="t"><thead><tr><th>Requested</th><th className="r">Amount</th><th>Destination</th><th>Status</th><th /></tr></thead><tbody>
    {w.payouts.map((p: any) => <tr key={p.id}><td className="small">{fmtDateTime(p.created_at)}</td><td className="r num">{money(p.amount)}</td><td className="small">{p.destination}</td>
      <td><Status s={p.status} />{p.transfer && <div className="faint small">{p.transfer}</div>}{p.paid_reference && <div className="faint small">ref {p.paid_reference}</div>}</td>
      <td>{p.status === "pending_approval" && me?.user.role === "owner" && <div className="row" style={{ gap: 4 }}>
        <button className="btn sm primary" onClick={() => run(() => api(`/wallet/payouts/${p.id}/decide`, { body: { decision: "approve" } }), "Payout approved").then(reload)}>Approve</button>
        <button className="btn sm" onClick={() => { const n = prompt("Reason for rejecting"); if (n) run(() => api(`/wallet/payouts/${p.id}/decide`, { body: { decision: "reject", notes: n } }), "Payout rejected").then(reload); }}>Reject</button></div>}</td></tr>)}
  </tbody></table></Panel>;
}

function PlatformQueue() {
  const { data: adj, reload: r1 } = useApi<any[]>("/admin/growth/adjustments?status=pending");
  const { data: orders, reload: r2 } = useApi<any[]>("/admin/growth/orders");
  const { data: payouts, reload: r3 } = useApi<any[]>("/admin/growth/payouts");
  const { data: reports } = useApi<any[]>("/admin/growth/reports");
  const { run } = useAction();
  const [live, setLive] = useState<Record<string, string>>({});
  return (
    <div className="stack">
      <Panel title="Manual adjustments awaiting approval" pad={false}>{!adj?.length ? <Empty title="Nothing to approve" /> :
        <table className="t"><thead><tr><th>Organisation</th><th className="r">Amount</th><th>Reason</th><th>Requested by</th><th /></tr></thead><tbody>{adj.map((a) => <tr key={a.id}>
          <td>{a.organisation}</td><td className="r num">{money(a.amount)}</td><td className="small">{a.reason}{a.reference ? ` (${a.reference})` : ""}</td><td className="small">{a.requested_by_email}</td>
          <td>{a.can_decide ? <div className="row" style={{ gap: 4 }}><button className="btn sm primary" onClick={() => run(() => api(`/admin/growth/adjustments/${a.id}/decide`, { body: { decision: "approve" } }), "Applied").then(r1)}>Approve</button>
            <button className="btn sm" onClick={() => { const n = prompt("Reason"); if (n) run(() => api(`/admin/growth/adjustments/${a.id}/decide`, { body: { decision: "reject", notes: n } })).then(r1); }}>Reject</button></div> : <span className="faint small">you requested this</span>}</td></tr>)}</tbody></table>}</Panel>
      <Panel title="Service orders & disputes" pad={false}>{!orders?.length ? <Empty title="No service orders or disputes" /> :
        <table className="t"><thead><tr><th>Order</th><th>Organisation</th><th>Status</th><th className="r">Amount</th><th>Actions</th></tr></thead><tbody>{orders.map((o) => <tr key={o.id}>
          <td>{o.number}<div className="faint small">{o.title}</div></td><td className="small">{o.organisation}</td><td><Status s={o.status} /></td><td className="r num">{money(o.amount)}</td>
          <td><div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
            {o.kind === "service" && o.status === "pending" && <button className="btn sm" onClick={() => run(() => api(`/admin/growth/orders/${o.id}/action`, { body: { action: "accept" } })).then(r2)}>Accept</button>}
            {o.kind === "service" && ["accepted", "in_progress"].includes(o.status) && <><input className="input" style={{ width: 180 }} placeholder="Deliverable URL" value={live[o.id] || ""} onChange={(e) => setLive({ ...live, [o.id]: e.target.value })} />
              <button className="btn sm" disabled={!live[o.id]} onClick={() => run(() => api(`/admin/growth/orders/${o.id}/action`, { body: { action: "deliver", live_url: live[o.id] } })).then(r2)}>Deliver</button></>}
            {o.status === "disputed" && <><button className="btn sm" onClick={() => run(() => api(`/admin/growth/orders/${o.id}/action`, { body: { action: "resolve_refund", note: "Resolved by platform" } })).then(r2)}>Refund buyer</button>
              <button className="btn sm" onClick={() => run(() => api(`/admin/growth/orders/${o.id}/action`, { body: { action: "resolve_release", note: "Resolved by platform" } })).then(r2)}>Release to publisher</button></>}
          </div></td></tr>)}</tbody></table>}</Panel>
      <Panel title="Payouts" pad={false}>{!payouts?.length ? <Empty title="No payouts" /> :
        <table className="t"><thead><tr><th>Organisation</th><th className="r">Amount</th><th>Destination</th><th>Status</th><th /></tr></thead><tbody>{payouts.map((p) => <tr key={p.id}>
          <td>{p.organisation}</td><td className="r num">{money(p.amount)}</td><td className="small">{p.destination}</td><td><Status s={p.status} /></td>
          <td>{p.status === "approved" && <button className="btn sm" onClick={() => { const ref = prompt("Transfer reference (after paying manually)"); if (ref) run(() => api(`/admin/growth/payouts/${p.id}/mark-paid`, { body: { reference: ref } }), "Marked paid").then(r3); }}>Mark paid</button>}</td></tr>)}</tbody></table>}</Panel>
      <Panel title="Publisher reports" pad={false}>{!reports?.length ? <Empty title="No reports" /> :
        <table className="t"><thead><tr><th>When</th><th>Publisher</th><th>Reason</th><th>Details</th></tr></thead><tbody>{reports.map((r) => <tr key={r.id}><td className="small">{fmtDate(r.created_at)}</td><td>{r.publisher}</td><td>{r.reason}</td><td className="small">{r.details || "—"}</td></tr>)}</tbody></table>}</Panel>
    </div>
  );
}
