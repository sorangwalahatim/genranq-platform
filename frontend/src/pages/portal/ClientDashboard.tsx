import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, ApiError } from "../../lib/api";
import { useMe } from "../../lib/hooks";
import { Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../../components/ui";

type Tab = "seo" | "social" | "backlinks" | "gmb" | "work";
const TABS: { id: Tab; label: string }[] = [
  { id: "seo", label: "Google rankings" },
  { id: "social", label: "Social media" },
  { id: "backlinks", label: "Backlinks" },
  { id: "gmb", label: "Google business" },
  { id: "work", label: "Work done" },
];

function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="card kpi">
      <div className="muted">{label}</div>
      <div style={{ fontSize: "1.8rem", fontWeight: 700 }}>{value}</div>
      {sub && <div className="muted">{sub}</div>}
    </div>
  );
}

function NotReady({ reason }: { reason: string }) {
  return (
    <div className="empty">
      <h3>Nothing to show yet</h3>
      <p className="muted">{reason}</p>
    </div>
  );
}

function ChartCard({ title, children }: { title: string; children: any }) {
  return (
    <Panel title={title}>
      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer>
      </div>
    </Panel>
  );
}

export default function ClientDashboard() {
  const { projectId } = useParams();
  const { me } = useMe();
  const [days, setDays] = useState("90");
  const [tab, setTab] = useState<Tab>("seo");
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<ApiError | Error | null>(null);

  useEffect(() => {
    setData(null); setErr(null);
    api(`/portal/dashboard?project_id=${projectId}&days=${days}`)
      .then(setData)
      .catch(setErr);
  }, [projectId, days]);

  if (err) return <><PageHead title="Client dashboard" /><ErrorBox error={err} /></>;
  if (!data) return <><PageHead title="Client dashboard" /><Loading /></>;

  const isClient = me?.user?.role === "client";
  const seo = data.seo || {}, soc = data.social || {}, bl = data.backlinks || {}, gmb = data.gmb || {}, work = data.work || {};

  return (
    <>
      <PageHead title="Client dashboard" sub={isClient ? "Your project's results, in plain words." : "What the client sees — no internal data."}>
        <Select value={days} onChange={setDays} label="Period"
          options={[{ value: "30", label: "Last 30 days" }, { value: "90", label: "Last 90 days" }, { value: "180", label: "Last 6 months" }]} />
        {!isClient && <Link className="btn" to={`/portal/${projectId}/messages`}>Messages</Link>}
        {!isClient && <Link className="btn ghost" to={`/portal/${projectId}/team`}>Team space</Link>}
      </PageHead>

      <div className="actions" style={{ marginBottom: 12 }}>
        {TABS.map((t) => (
          <button key={t.id} className={"btn" + (tab === t.id ? "" : " ghost")} onClick={() => setTab(t.id)}>{t.label}</button>
        ))}
      </div>

      {tab === "seo" && (
        !seo.connected ? <NotReady reason={seo.reason} /> : <>
          <p>{seo.summary}</p>
          <div className="grid g4">
            <Stat label="Keywords tracked" value={seo.keywords} />
            <Stat label="In Google's top 10" value={seo.top10} />
            <Stat label="Average position" value={seo.avg_position} sub="lower is better" />
            <Stat label="Ranking now" value={seo.ranking} />
          </div>
          {seo.trend?.length > 1 && (
            <ChartCard title="Average position over time (lower is better)">
              <LineChart data={seo.trend}>
                <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="day" tick={{ fontSize: 11 }} /><YAxis reversed domain={["auto", "auto"]} />
                <Tooltip /><Line type="monotone" dataKey="avg" stroke="#2563eb" dot={false} name="Avg position" />
              </LineChart>
            </ChartCard>
          )}
          <div className="grid g3">
            <ChartCard title="Where your keywords rank">
              <BarChart data={Object.entries(seo.distribution || {}).map(([k, v]) => ({ bucket: k, n: v }))}>
                <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="bucket" tick={{ fontSize: 11 }} /><YAxis allowDecimals={false} />
                <Tooltip /><Bar dataKey="n" fill="#16a34a" name="Keywords" />
              </BarChart>
            </ChartCard>
            <Panel title="Biggest climbs">
              {seo.movers_up?.length ? seo.movers_up.map((m: any) => (
                <div key={m.phrase} className="kv"><span>{m.phrase}</span><b style={{ color: "green" }}>#{m.position} ↑{m.change}</b></div>
              )) : <p className="muted">No big climbs yet.</p>}
            </Panel>
            <Panel title="Needs attention">
              {seo.movers_down?.length ? seo.movers_down.map((m: any) => (
                <div key={m.phrase} className="kv"><span>{m.phrase}</span><b style={{ color: "#dc2626" }}>#{m.position} ↓{Math.abs(m.change)}</b></div>
              )) : <p className="muted">Nothing dropping right now.</p>}
            </Panel>
          </div>
        </>
      )}

      {tab === "social" && (
        !soc.connected ? <NotReady reason={soc.reason} /> : <>
          <p>{soc.summary}</p>
          <div className="grid g4">
            <Stat label="Posts (30 days)" value={soc.posts_30d} />
            <Stat label="People reached" value={(soc.impressions_30d || 0).toLocaleString()} />
            <Stat label="Likes, comments, shares" value={(soc.engagement_30d || 0).toLocaleString()} />
            <Stat label="Accounts" value={soc.accounts?.length || 0} />
          </div>
          {(soc.by_channel?.length > 0) && (
            <ChartCard title="Reach by platform (last 30 days)">
              <BarChart data={soc.by_channel} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" /><XAxis type="number" /><YAxis dataKey="channel" type="category" width={90} />
                <Tooltip /><Bar dataKey="impressions" fill="#7c3aed" name="People reached" />
              </BarChart>
            </ChartCard>
          )}
          <Panel title="Connected accounts">
            {soc.accounts?.map((a: any) => (
              <div key={a.channel + a.name} className="kv"><span>{a.channel} — {a.name}</span><span className="muted">{a.status}</span></div>
            ))}
          </Panel>
        </>
      )}

      {tab === "backlinks" && (
        !bl.connected ? <NotReady reason={bl.reason} /> : <>
          <p>{bl.summary}</p>
          <div className="grid g4">
            <Stat label="Total backlinks" value={bl.total} />
            <Stat label="Live right now" value={bl.live} />
            <Stat label="Average strength (DR)" value={bl.avg_dr ?? "—"} />
            <Stat label="New (30 days)" value={bl.new_30d} />
          </div>
          {bl.by_month?.length > 1 && (
            <ChartCard title="New backlinks per month">
              <BarChart data={bl.by_month}>
                <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="month" tick={{ fontSize: 11 }} /><YAxis allowDecimals={false} />
                <Tooltip /><Bar dataKey="new" fill="#2563eb" name="New links" />
              </BarChart>
            </ChartCard>
          )}
        </>
      )}

      {tab === "gmb" && (
        !gmb.connected ? <NotReady reason={gmb.reason} /> : <>
          <p>{gmb.summary}</p>
          <div className="grid g4">
            <Stat label="Locations" value={gmb.locations} />
            <Stat label="Google reviews" value={gmb.reviews} />
            <Stat label="Average rating" value={gmb.avg_rating ? `${gmb.avg_rating} / 5` : "—"} />
          </div>
          {gmb.location_names?.length > 0 && (
            <Panel title="Locations">{gmb.location_names.map((n: string) => <div key={n} className="kv"><span>{n}</span></div>)}</Panel>
          )}
        </>
      )}

      {tab === "work" && (
        <>
          <p>{work.summary}</p>
          <div className="grid g4">
            <Stat label="To do" value={work.todo} />
            <Stat label="In progress" value={work.in_progress} />
            <Stat label="In review" value={work.review} />
            <Stat label="Finished" value={work.done} />
          </div>
          {!isClient && <Link className="btn" to="/tasks">Open tasks</Link>}
        </>
      )}
    </>
  );
}
