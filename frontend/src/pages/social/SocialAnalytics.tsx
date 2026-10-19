import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { CHANNEL_LABEL, CHANNELS, ChannelTag, PROVIDER_LABEL, ProjectPicker, useProjects } from "./shared";

const n = (v?: number | null) => (v == null ? "—" : v.toLocaleString());

export default function SocialAnalytics() {
  const { projects, pid, setPid } = useProjects(true);
  const [channel, setChannel] = useState("");
  const [days, setDays] = useState("30");
  const qs = new URLSearchParams({ days });
  if (pid) qs.set("project_id", pid);
  if (channel) qs.set("channel", channel);
  const { data, error, loading, reload } = useApi<any>(`/social/analytics?${qs}`, [qs.toString()]);
  const can = useCan();
  const { run } = useAction();
  const file = useRef<HTMLInputElement>(null);
  const connected = data ? Object.entries(data.providers).filter(([, s]) => s === "CONNECTED").map(([p]) => p) : [];
  const t = data?.totals;
  const importCsv = async (f: File) => {
    const fd = new FormData(); fd.append("project_id", pid); fd.append("file", f);
    await run<any>(() => api("/social/analytics/import", { form: fd })).then((r) => r && alert(`${r.rows} row(s) imported, ${r.matched_published} matched to posts published from Genranq.`));
    reload();
  };
  return (
    <>
      <PageHead title="Social analytics" sub="Engagement per published post — pulled from Meta, LinkedIn and X when connected, or imported from CSV exports. Each post counts once, at its latest snapshot.">
        <ProjectPicker projects={projects} pid={pid} setPid={setPid} allowAll />
        <Select value={channel} onChange={setChannel} label="Channel" placeholder="All channels" options={CHANNELS.map((c) => ({ value: c, label: CHANNEL_LABEL[c] }))} />
        <Select value={days} onChange={setDays} label="Period" options={[["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last 12 months"]].map(([v, l]) => ({ value: v, label: l }))} />
        {can("edit") && <><input ref={file} type="file" accept=".csv" hidden onChange={(e) => { if (e.target.files?.[0]) importCsv(e.target.files[0]); e.target.value = ""; }} />
          <button className="btn" disabled={!pid} title={pid ? "" : "Choose a project to import into"} onClick={() => file.current?.click()}>Import CSV</button>
          <button className="btn primary" disabled={!connected.length} onClick={() => run(() => api("/social/analytics/sync", { body: { project_id: pid || null } }), "Metrics sync queued").then(() => setTimeout(reload, 4000))}>Sync from platforms</button></>}
      </PageHead>
      {data && !connected.length && <div className="notice nc" style={{ marginBottom: 14 }}><div><b>NOT CONNECTED.</b> Meta, LinkedIn and X are not connected, so metrics can't be pulled automatically. <Link to="/integrations/meta">Connect a platform</Link>, or import a CSV with columns <code>channel, post_id, date, likes, comments, shares, impressions, clicks</code>.</div></div>}
      {data && connected.length > 0 && connected.length < 3 && <p className="faint small" style={{ marginBottom: 10 }}>Connected: {connected.map((p) => PROVIDER_LABEL[p]).join(", ")}. Posts on other platforms need a CSV import.</p>}
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (<>
        {data.failed_alerts.length > 0 && (
          <div className="notice err" style={{ marginBottom: 14 }}><div><b>{data.failed_alerts.length} post(s) failed to publish</b> in this period.
            <ul style={{ margin: "6px 0 0 18px" }}>{data.failed_alerts.slice(0, 5).map((f: any) => <li key={f.id}>{f.title || "(untitled)"} → {f.account || CHANNEL_LABEL[f.channel]}: {f.error} <span className="faint">({ago(f.at)})</span></li>)}</ul>
            <Link to="/social/automation">Open the publish queue</Link></div></div>
        )}
        <div className="grid g4" style={{ marginBottom: 16 }}>
          <div className="panel kpi"><div className="v">{n(t.posts)}</div><div className="l">Measured posts · {data.publishing.PUBLISHED || 0} published from Genranq</div></div>
          <div className="panel kpi"><div className="v">{n(t.impressions)}</div><div className="l">Impressions</div></div>
          <div className="panel kpi"><div className="v">{n(t.engagements)}</div><div className="l">Engagements (likes, comments, shares, clicks)</div></div>
          <div className="panel kpi"><div className="v">{t.engagement_rate == null ? "—" : `${t.engagement_rate}%`}</div><div className="l">Engagement rate</div></div>
        </div>
        {!t.posts ? <Panel><Empty title="No engagement data yet">Publish posts and sync from a connected platform, or import a CSV export of post metrics.</Empty></Panel> : (<>
          <div className="grid g2" style={{ marginBottom: 16 }}>
            <Panel title="Engagement by post date">
              <div style={{ height: 220 }}><ResponsiveContainer><LineChart data={data.trend.map((d: any) => ({ ...d, day: fmtDate(d.day) }))}>
                <CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="day" fontSize={11} tickLine={false} axisLine={false} /><YAxis fontSize={11} width={40} tickLine={false} axisLine={false} />
                <Tooltip /><Legend />
                <Line dataKey="likes" name="Likes" stroke="var(--accent)" strokeWidth={2} dot={false} />
                <Line dataKey="comments" name="Comments" stroke="var(--high)" strokeWidth={2} dot={false} />
                <Line dataKey="shares" name="Shares" stroke="var(--pass)" strokeWidth={2} dot={false} />
                <Line dataKey="clicks" name="Clicks" stroke="var(--medium)" strokeWidth={2} dot={false} />
              </LineChart></ResponsiveContainer></div>
            </Panel>
            <Panel title="By channel">
              <div style={{ height: 220 }}><ResponsiveContainer><BarChart data={data.by_channel.map((c: any) => ({ ...c, name: CHANNEL_LABEL[c.channel] }))}>
                <CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="name" fontSize={11} tickLine={false} axisLine={false} /><YAxis fontSize={11} width={40} tickLine={false} axisLine={false} />
                <Tooltip /><Legend /><Bar dataKey="engagements" name="Engagements" fill="var(--accent)" radius={[3, 3, 0, 0]} /><Bar dataKey="posts" name="Posts" fill="var(--faint)" radius={[3, 3, 0, 0]} />
              </BarChart></ResponsiveContainer></div>
            </Panel>
          </div>
          <div className="grid g2" style={{ marginBottom: 16 }}>
            <Panel title="Channels" pad={false}><table className="t"><thead><tr><th>Channel</th><th className="r">Posts</th><th className="r">Impr.</th><th className="r">Eng.</th><th className="r">Rate</th></tr></thead>
              <tbody>{data.by_channel.map((c: any) => <tr key={c.channel}><td><ChannelTag ch={c.channel} /></td><td className="r num">{n(c.posts)}</td><td className="r num">{n(c.impressions)}</td><td className="r num">{n(c.engagements)}</td><td className="r num">{c.engagement_rate == null ? "—" : `${c.engagement_rate}%`}</td></tr>)}</tbody></table></Panel>
            <Panel title="Projects" pad={false}><table className="t"><thead><tr><th>Project</th><th className="r">Posts</th><th className="r">Impr.</th><th className="r">Eng.</th><th className="r">Rate</th></tr></thead>
              <tbody>{data.by_project.map((p: any) => <tr key={p.project_id}><td>{p.project}</td><td className="r num">{n(p.posts)}</td><td className="r num">{n(p.impressions)}</td><td className="r num">{n(p.engagements)}</td><td className="r num">{p.engagement_rate == null ? "—" : `${p.engagement_rate}%`}</td></tr>)}</tbody></table></Panel>
          </div>
          <Panel title="Top posts" pad={false}><div className="table-wrap"><table className="t">
            <thead><tr><th>Post</th><th>Channel</th><th>Posted</th><th className="r">Likes</th><th className="r">Comments</th><th className="r">Shares</th><th className="r">Clicks</th><th className="r">Impr.</th><th className="r">Rate</th><th>Source</th></tr></thead>
            <tbody>{data.top_posts.map((p: any) => <tr key={p.channel + p.external_post_id}><td>{p.title || <span className="mono-url">{p.external_post_id}</span>}{p.account && <div className="faint small">{p.account}</div>}</td><td><ChannelTag ch={p.channel} /></td><td className="small">{fmtDate(p.posted_at)}</td>
              <td className="r num">{n(p.likes)}</td><td className="r num">{n(p.comments)}</td><td className="r num">{n(p.shares)}</td><td className="r num">{n(p.clicks)}</td><td className="r num">{n(p.impressions)}</td><td className="r num">{p.engagement_rate == null ? "—" : `${p.engagement_rate}%`}</td><td className="small">{p.source === "api" ? "API" : "CSV"}</td></tr>)}</tbody>
          </table></div></Panel>
        </>)}
      </>)}
    </>
  );
}
