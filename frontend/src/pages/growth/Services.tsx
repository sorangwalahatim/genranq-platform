import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead } from "../../components/ui";
import { api } from "../../lib/api";
import { human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { MarketNav, money } from "./shared";

export default function Services() {
  const { data, error, loading } = useApi<any[]>("/services");
  const [order, setOrder] = useState<any>(null);
  const can = useCan();
  return (
    <>
      <PageHead title="Services catalog" sub="Done-for-you link building, content and PR packages fulfilled by the Genranq team, paid from your wallet.">
        <Link className="btn" to="/marketplace?tab=cart">Go to cart</Link>
      </PageHead>
      <MarketNav />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty title="No services available" /> : (
        <div className="grid g3">
          {data.map((s) => (
            <section key={s.id} className="panel" style={{ display: "flex", flexDirection: "column" }}>
              <div className="panel-head"><h2>{s.name}</h2><div className="row" style={{ gap: 4 }}>{s.tags.map((t: string) => <Badge key={t} kind={t === "premium" ? "high" : "pass"}>{human(t)}</Badge>)}</div></div>
              <div className="panel-body" style={{ flex: 1, display: "flex", flexDirection: "column", gap: 10 }}>
                <p className="small" style={{ margin: 0 }}>{s.description}</p>
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{s.deliverables.map((d: string) => <li key={d}>{d}</li>)}</ul>
                <table className="t"><tbody>{s.tiers.map((t: any) => <tr key={t.key}><td><b>{t.name}</b><div className="faint small">{t.includes}</div></td><td className="r num">{money(t.price)}</td></tr>)}</tbody></table>
                {s.addons.length > 0 && <div className="small muted">Add-ons: {s.addons.map((a: any) => `${a.name} (${money(a.price)})`).join(", ")}</div>}
                <div className="row" style={{ justifyContent: "space-between", marginTop: "auto" }}><span className="small muted">Turnaround ~{s.tat_days} days</span>
                  {can("create") && s.orderable && <button className="btn primary sm" onClick={() => setOrder(s)}>Order</button>}</div>
              </div>
            </section>
          ))}
        </div>
      )}
      {order && <OrderService s={order} onClose={() => setOrder(null)} />}
    </>
  );
}

function OrderService({ s, onClose }: any) {
  const { data: projects } = useApi<any[]>("/projects");
  const [tier, setTier] = useState(s.tiers[0].key);
  const [addons, setAddons] = useState<string[]>([]);
  const [f, setF] = useState({ project_id: "", target_url: "", notes: "" });
  const { run, busy } = useAction();
  const total = (s.tiers.find((t: any) => t.key === tier)?.price || 0) + s.addons.filter((a: any) => addons.includes(a.key)).reduce((x: number, a: any) => x + a.price, 0);
  return (
    <Modal title={`Order ${s.name}`} onClose={onClose} foot={<button className="btn primary" disabled={busy} onClick={() => run(() => api(`/services/${s.id}/cart`, { body: { tier, addons, project_id: f.project_id || null, target_url: f.target_url || null, notes: f.notes || null } }), "Added to cart").then(onClose)}>Add to cart · {money(total)}</button>}>
      <div className="form">
        <label className="field">Package<select className="input" value={tier} onChange={(e) => setTier(e.target.value)}>{s.tiers.map((t: any) => <option key={t.key} value={t.key}>{t.name} — {money(t.price)}</option>)}</select></label>
        {s.addons.map((a: any) => <label key={a.key} className="check"><input type="checkbox" checked={addons.includes(a.key)} onChange={(e) => setAddons(e.target.checked ? [...addons, a.key] : addons.filter((x) => x !== a.key))} /> {a.name} (+{money(a.price)})</label>)}
        <label className="field">Project<select className="input" value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}><option value="">—</option>{(projects || []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label className="field">Target URL<input className="input" value={f.target_url} onChange={(e) => setF({ ...f, target_url: e.target.value })} placeholder="https://…" /></label>
        <label className="field">Brief<textarea className="input" rows={3} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      </div>
    </Modal>
  );
}
