import { Badge, ErrorBox, Loading, Meter, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { bytes } from "../lib/format";
import { useAction, useApi, useCan, useMe } from "../lib/hooks";

export default function Billing() {
  const { data: plans } = useApi<any>("/billing/plans");
  const { data: usage, error, reload } = useApi<any>("/billing/usage");
  const { me, reload: reloadMe } = useMe();
  const can = useCan();
  const { run } = useAction();
  if (!usage || !plans) return error ? <ErrorBox error={error} /> : <Loading />;
  const cur = usage.plan.key;
  const fmt = (m: any, v: number | null) => (m.key === "storage_bytes" ? bytes(v) : v ?? "—");
  return (
    <>
      <PageHead title="Plan & usage" sub={`You’re on the ${usage.plan.name} plan. Usage resets monthly (${usage.period}).`} />
      <Panel title="Usage this month" pad={false}>
        <table className="t"><thead><tr><th>Limit</th><th className="r">Used</th><th className="r">Included</th><th className="r">Remaining</th><th style={{ width: 160 }} /></tr></thead>
          <tbody>{usage.meters.map((m: any) => (
            <tr key={m.key}><td>{m.label}</td><td className="r num">{fmt(m, m.used)}</td><td className="r num">{m.limit == null ? (m.custom ? "Custom" : "Unlimited") : fmt(m, m.limit)}{m.period && m.limit != null ? " / mo" : ""}</td>
              <td className="r num">{m.limit == null ? "—" : fmt(m, m.remaining)}</td><td><Meter used={m.used} limit={m.limit} /></td></tr>
          ))}</tbody></table>
      </Panel>
      <div style={{ marginTop: 16 }}>
        <Panel title="Compare plans" pad={false}>
          <div className="table-wrap"><table className="t"><thead><tr><th>Feature</th>{plans.tiers.map((t: string) => <th key={t}>{t}{t.toLowerCase() === cur && <Badge kind="pass">Current</Badge>}</th>)}</tr></thead>
            <tbody>{plans.rows.map((r: any) => <tr key={r.feature}><td>{r.feature}</td>{plans.tiers.map((t: string) => <td key={t} style={{ background: t.toLowerCase() === cur ? "var(--accent-soft)" : undefined }}>{r.values[t] === "—" ? <span className="faint">—</span> : r.values[t]}</td>)}</tr>)}</tbody>
            {can("manage_billing") && <tfoot><tr><td className="muted small">Pricing is quoted by sales.</td>{plans.tiers.map((t: string) => <td key={t}>{t.toLowerCase() !== cur && <button className="btn sm" onClick={() => confirm(`Switch to ${t}? Limits change immediately.`) && run(() => api("/billing/plan", { body: { plan: t.toLowerCase() } }), `Switched to ${t}`).then(() => { reload(); reloadMe(); })}>Switch to {t}</button>}</td>)}</tr></tfoot>}
          </table></div>
        </Panel>
      </div>
      {!can("manage_billing") && <p className="muted small" style={{ marginTop: 10 }}>Only the organisation owner ({me?.user.role === "owner" ? "you" : "ask them"}) can change plans.</p>}
    </>
  );
}
