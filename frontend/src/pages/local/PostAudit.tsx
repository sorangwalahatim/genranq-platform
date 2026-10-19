import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { fmtDate } from "../../lib/format";
import { useApi, useCan } from "../../lib/hooks";
import { CsvButton, Kpi, LocationPick, NcBanner, NoLocation, NoProject, ProjectPick, useLocal } from "./shared";

/** Module 18 — GBP post audit: cadence, gaps, types, CTA / image usage, length. */
export default function PostAudit() {
  const L = useLocal("own");
  const can = useCan();
  const a = useApi<any>(L.lid ? `/local/locations/${L.lid}/post-audit` : null, [L.lid]);
  const d = a.data;
  return (
    <>
      <PageHead title="Post audit" sub="How consistently this location posts on Google, which post types and CTAs it uses, and where the gaps are.">
        <ProjectPick {...L} /><LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} />
        {can("edit") && L.pid && <CsvButton path={`/local/projects/${L.pid}/import/posts`} label="Import past posts CSV" extra={{ location_id: L.lid }} onDone={() => a.reload()}
          hint="Columns: location (optional), type (update/offer/event), title, text, cta, url, date, image (URL or yes/no)" />}
        <Link className="btn primary" to="/local/gmb-posts">Create posts</Link>
      </PageHead>
      <NcBanner conn={L.status?.gbp} provider="gbp">Post history comes from CSV import and posts published through GENRANQ.</NcBanner>
      <NoProject projects={L.projects} />
      <ErrorBox error={a.error} />
      {L.pid && L.locs.data && !L.locs.data.length ? <NoLocation /> : !d ? (L.lid ? <Loading /> : null) : (<>
        <div className="grid g4" style={{ marginBottom: 16 }}>
          <Kpi v={d.published} l="Published posts" />
          <Kpi v={d.days_since_last != null ? `${d.days_since_last} d` : "never"} l="Since last post" />
          <Kpi v={d.per_week_90d} l="Posts / week (90 days)" />
          <Kpi v={d.max_gap_days != null ? `${d.max_gap_days} d` : "—"} l={`Longest gap · avg ${d.avg_gap_days ?? "—"} d`} />
        </div>
        {d.recommendations.length > 0 && <div className="notice warn" style={{ marginBottom: 16 }}><ul style={{ margin: 0, paddingLeft: 18 }}>{d.recommendations.map((r: string) => <li key={r}>{r}</li>)}</ul></div>}
        <div className="split lc-split">
          <Panel title="Posts per week">
            {!d.weekly.length ? <Empty title="No published posts" /> : <div style={{ height: 220 }}><ResponsiveContainer><BarChart data={d.weekly}>
              <CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="week" fontSize={11} tickLine={false} /><YAxis allowDecimals={false} width={24} fontSize={11} /><Tooltip />
              <Bar dataKey="posts" fill="var(--accent)" radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer></div>}
          </Panel>
          <Panel title="Mix & quality">
            <dl className="kv">
              <dt>Updates / offers / events</dt><dd>{d.types.update} / {d.types.offer} / {d.types.event}</dd>
              <dt>With call-to-action</dt><dd>{d.cta_pct != null ? `${d.cta_pct}%` : "—"}</dd>
              <dt>With image</dt><dd>{d.image_pct != null ? `${d.image_pct}%` : "—"}</dd>
              <dt>Average length</dt><dd>{d.avg_length != null ? `${d.avg_length} chars` : "—"}</dd>
              <dt>Under 100 chars</dt><dd>{d.too_short}</dd>
              <dt>Over 1,500 chars</dt><dd>{d.too_long}</dd>
              <dt>Drafts awaiting</dt><dd>{d.drafts}</dd>
            </dl>
          </Panel>
        </div>
        <div className="grid g2" style={{ marginTop: 16 }}>
          <Panel title="Gaps over 14 days" pad={false}>
            {!d.gaps_over_14d.length ? <p className="muted" style={{ padding: 16 }}>No long gaps.</p> : (
              <table className="t"><thead><tr><th>From</th><th>To</th><th className="r">Days</th></tr></thead>
                <tbody>{d.gaps_over_14d.map((g: any, i: number) => <tr key={i}><td>{fmtDate(g.from)}</td><td>{g.ongoing ? "today (ongoing)" : fmtDate(g.to)}</td><td className="r num">{g.days}</td></tr>)}</tbody></table>
            )}
          </Panel>
          <Panel title="Competitor cadence" pad={false}>
            {!d.competitors.length ? <p className="muted" style={{ padding: 16 }}>Record competitors' posts per month in <Link to="/local/competitors">Competitor research</Link> to compare.</p> : (
              <table className="t"><thead><tr><th>Business</th><th className="r">Posts / month</th><th>Last post</th></tr></thead>
                <tbody><tr><td><b>You</b></td><td className="r num">{d.own_posts_per_month}</td><td>{d.days_since_last != null ? `${d.days_since_last} d ago` : "—"}</td></tr>
                  {d.competitors.map((c: any) => <tr key={c.name}><td>{c.name}</td><td className="r num">{c.posts_per_month ?? "—"}</td><td>{fmtDate(c.last_post_at)}</td></tr>)}</tbody></table>
            )}
          </Panel>
        </div>
      </>)}
    </>
  );
}
