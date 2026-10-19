import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../../components/ui";
import { fmtDateTime } from "../../lib/format";
import { useApi } from "../../lib/hooks";
import { Kpi, LeadsNav, money, num, useProjects } from "./shared";

const MODELS = [{ value: "first_touch", label: "First touch" }, { value: "last_touch", label: "Last touch" }, { value: "linear", label: "Linear" }];
const DIMS = [{ value: "source", label: "Source" }, { value: "medium", label: "Medium" }, { value: "campaign", label: "Campaign" },
  { value: "source_medium", label: "Source / medium" }, { value: "channel", label: "Capture channel" }];

export default function Attribution() {
  const { pid, picker } = useProjects();
  const [model, setModel] = useState("linear");
  const [dim, setDim] = useState("source");
  const [since, setSince] = useState("");
  const qs = `dimension=${dim}${pid ? `&project_id=${pid}` : ""}${since ? `&since=${since}T00:00:00Z` : ""}`;
  const { data, error, loading } = useApi<any>(pid ? `/attribution/report?model=${model}&${qs}` : null, [qs, model]);
  const { data: cmp } = useApi<any>(pid ? `/attribution/compare?${qs}` : null, [qs]);
  const { data: tps } = useApi<any[]>(pid ? `/attribution/touchpoints?project_id=${pid}&limit=50` : null, [pid]);
  const rows = data?.rows || [];
  return (
    <>
      <PageHead title="Attribution" sub="Credit leads, conversions (won leads) and value to sources, mediums and campaigns using first-touch, last-touch or linear models.">
        {picker}<Select value={model} onChange={setModel} options={MODELS} label="Model" /><Select value={dim} onChange={setDim} options={DIMS} label="Dimension" />
        <input className="input" type="date" value={since} onChange={(e) => setSince(e.target.value)} aria-label="Leads created since" style={{ width: "auto" }} />
      </PageHead>
      <LeadsNav />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !data ? null : (<>
        <div className="grid g4" style={{ marginBottom: 16 }}>
          <Kpi v={num(data.totals.leads)} l="Leads" /><Kpi v={num(data.totals.touchpoints)} l="Touchpoints" />
          <Kpi v={num(data.totals.conversions)} l="Conversions (won)" tone="pass" /><Kpi v={money(data.totals.value)} l="Won value" />
        </div>
        {!rows.length ? <Panel><Empty title="No attributed leads yet">Leads captured from forms, webhooks, bookings or CSV carry their UTM / referrer touchpoints here.</Empty></Panel> : (<>
          <Panel title={`${MODELS.find((m) => m.value === model)?.label} — credited value by ${DIMS.find((d) => d.value === dim)?.label.toLowerCase()}`}>
            <div style={{ height: 260 }}><ResponsiveContainer><BarChart data={rows.slice(0, 12)} margin={{ left: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="key" fontSize={11} tickLine={false} interval={0} angle={-15} height={50} textAnchor="end" />
              <YAxis yAxisId="l" fontSize={11} width={40} tickLine={false} axisLine={false} /><YAxis yAxisId="r" orientation="right" fontSize={11} width={60} tickLine={false} axisLine={false} />
              <Tooltip /><Legend />
              <Bar yAxisId="l" dataKey="leads" name="Leads (credited)" fill="var(--low)" radius={[3, 3, 0, 0]} />
              <Bar yAxisId="l" dataKey="conversions" name="Conversions" fill="var(--pass)" radius={[3, 3, 0, 0]} />
              <Bar yAxisId="r" dataKey="value" name="Value" fill="var(--accent)" radius={[3, 3, 0, 0]} />
            </BarChart></ResponsiveContainer></div>
            <div className="table-wrap" style={{ marginTop: 12 }}><table className="t"><thead><tr><th>{DIMS.find((d) => d.value === dim)?.label}</th><th className="r">Leads</th><th className="r">Conversions</th><th className="r">Conv. rate</th><th className="r">Value</th><th className="r">Touchpoints</th></tr></thead>
              <tbody>{rows.map((r: any) => <tr key={r.key}><td>{r.key}</td><td className="r num">{num(r.leads, 2)}</td><td className="r num">{num(r.conversions, 2)}</td>
                <td className="r num">{r.conversion_rate == null ? "—" : `${(r.conversion_rate * 100).toFixed(1)}%`}</td><td className="r num">{money(r.value)}</td><td className="r num">{r.touchpoints}</td></tr>)}</tbody></table></div>
          </Panel>
          {cmp && <div style={{ marginTop: 16 }}><Panel title="Model comparison (credited value)" pad={false}><div className="table-wrap"><table className="t"><thead><tr><th>{DIMS.find((d) => d.value === dim)?.label}</th><th className="r">First touch</th><th className="r">Last touch</th><th className="r">Linear</th></tr></thead>
            <tbody>{cmp.rows.map((r: any) => <tr key={r.key}><td>{r.key}</td>{["first_touch", "last_touch", "linear"].map((mo) => <td key={mo} className="r num">{money(r[mo].value)}<div className="faint small">{num(r[mo].leads, 2)} leads</div></td>)}</tr>)}</tbody></table></div></Panel></div>}
        </>)}
        {tps && tps.length > 0 && <div style={{ marginTop: 16 }}><Panel title="Latest touchpoints" pad={false}><div className="table-wrap"><table className="t"><thead><tr><th>When</th><th>Lead</th><th>Source / medium</th><th>Campaign</th><th>Landing page</th><th>Via</th></tr></thead>
          <tbody>{tps.map((t) => <tr key={t.id}><td className="small">{fmtDateTime(t.occurred_at)}</td><td className="small">{t.lead_email || "—"}</td><td>{t.source} / {t.medium}</td><td className="small">{t.campaign || "—"}</td><td className="mono-url" style={{ maxWidth: 220 }}>{t.landing_url || "—"}</td><td className="small">{t.kind}</td></tr>)}</tbody></table></div></Panel></div>}
      </>)}
    </>
  );
}
