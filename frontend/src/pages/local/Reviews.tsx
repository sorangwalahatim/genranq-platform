import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Line, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { fmtDate } from "../../lib/format";
import { useAction, useApi, useCan, useToast } from "../../lib/hooks";
import { copy, CsvButton, Kpi, LocationPick, NcBanner, NoLocation, NoProject, ProjectPick, Stars, StatusBadge, useLocal } from "./shared";

/** Module 17 — review audit with sentiment/topics and an approval-gated reply workflow. */
export default function Reviews() {
  const L = useLocal("own");
  const can = useCan();
  const audit = useApi<any>(L.lid ? `/local/locations/${L.lid}/review-audit` : null, [L.lid]);
  const [filter, setFilter] = useState({ status: "", sentiment: "" });
  const qs = new URLSearchParams(Object.entries(filter).filter(([, v]) => v) as any).toString();
  const list = useApi<any[]>(L.lid ? `/local/locations/${L.lid}/reviews${qs ? "?" + qs : ""}` : null, [L.lid, qs]);
  const [add, setAdd] = useState(false);
  const refresh = () => { audit.reload(); list.reload(); };
  const st = audit.data?.stats;
  return (
    <>
      <PageHead title="Review audit" sub="Volume, rating distribution, velocity, response rate & time, sentiment and topics — with AI-drafted replies that need approval before posting.">
        <ProjectPick {...L} /><LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} />
        {can("edit") && L.pid && <CsvButton path={`/local/projects/${L.pid}/import/reviews`} label="Import reviews CSV" extra={{ location_id: L.lid }} onDone={refresh}
          hint="Columns: location (optional), author, rating, text, date, reply, reply_date" />}
        {can("create") && <button className="btn primary" disabled={!L.lid} onClick={() => setAdd(true)}>Add review</button>}
      </PageHead>
      <NcBanner conn={L.status?.gbp} provider="gbp">Reviews come from CSV import or manual entry, and approved replies must be posted by hand.</NcBanner>
      <NcBanner conn={L.status?.ai} provider="ai">AI reply drafting is unavailable — write replies manually.</NcBanner>
      <NoProject projects={L.projects} />
      <ErrorBox error={audit.error} />
      {L.pid && L.locs.data && !L.locs.data.length ? <NoLocation /> : !st ? (L.lid ? <Loading /> : null) : (<>
        <div className="grid g4" style={{ marginBottom: 16 }}>
          <Kpi v={<>{st.avg_rating ?? "—"} <Stars n={st.avg_rating} /></>} l={`Average rating · ${st.total_count} reviews`} />
          <Kpi v={st.response_rate != null ? `${st.response_rate}%` : "—"} l="Response rate" />
          <Kpi v={st.avg_response_hours != null ? `${st.avg_response_hours} h` : "—"} l="Average response time" />
          <Kpi v={st.velocity_per_month ?? "—"} l={`Reviews / month · ${st.last_90.count} in 90 days`} />
        </div>
        {audit.data.recommendations.length > 0 && <div className="notice warn" style={{ marginBottom: 16 }}><ul style={{ margin: 0, paddingLeft: 18 }}>{audit.data.recommendations.map((r: string) => <li key={r}>{r}</li>)}</ul></div>}
        <div className="grid g2" style={{ marginBottom: 16 }}>
          <Panel title="Velocity & rating trend">
            {st.per_month.length < 1 ? <p className="muted">No dated reviews.</p> : <div style={{ height: 200 }}><ResponsiveContainer>
              <ComposedChart data={st.per_month}><CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="month" fontSize={11} tickLine={false} />
                <YAxis yAxisId="c" width={28} fontSize={11} allowDecimals={false} /><YAxis yAxisId="r" orientation="right" domain={[1, 5]} width={24} fontSize={11} /><Tooltip />
                <Bar yAxisId="c" dataKey="count" name="Reviews" fill="var(--accent)" radius={[3, 3, 0, 0]} />
                <Line yAxisId="r" dataKey="avg_rating" name="Avg rating" stroke="var(--medium)" strokeWidth={2} connectNulls /></ComposedChart></ResponsiveContainer></div>}
            {st.rating_trend_90d != null && <p className="small muted">Last 90 days {st.last_90.avg_rating}★ vs previous 90 days {st.prev_90.avg_rating}★ ({st.rating_trend_90d > 0 ? "+" : ""}{st.rating_trend_90d}).</p>}
          </Panel>
          <Panel title="Rating distribution & sentiment">
            <div className="lc-bars">{["5", "4", "3", "2", "1"].map((k) => <div key={k} className="lc-bar"><span>{k}★</span>
              <div className="track"><div className="fill" style={{ width: `${st.count ? (100 * st.distribution[k]) / st.count : 0}%`, background: +k >= 4 ? "var(--pass)" : +k === 3 ? "var(--medium)" : "var(--critical)" }} /></div>
              <span className="num">{st.distribution[k]}</span></div>)}</div>
            <div className="row" style={{ gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <Badge kind="pass">{st.sentiment.positive} positive</Badge><Badge>{st.sentiment.neutral} neutral</Badge><Badge kind="fail">{st.sentiment.negative} negative</Badge>
              {st.unanswered_negative > 0 && <Badge kind="high">{st.unanswered_negative} low ratings unanswered</Badge>}
            </div>
          </Panel>
        </div>
        <div className="grid g2" style={{ marginBottom: 16 }}>
          <Panel title="Topics mentioned" pad={false}>
            {!st.topics.length ? <p className="muted" style={{ padding: 16 }}>No recognisable topics yet.</p> : (
              <table className="t"><thead><tr><th>Topic</th><th className="r">Mentions</th><th className="r">Positive</th><th className="r">Negative</th><th className="r">Avg sentiment</th></tr></thead>
                <tbody>{st.topics.map((t: any) => <tr key={t.topic}><td>{t.topic}</td><td className="r num">{t.mentions}</td><td className="r num">{t.positive}</td><td className="r num">{t.negative}</td>
                  <td className="r num" style={{ color: t.avg_sentiment < -0.1 ? "var(--critical)" : t.avg_sentiment > 0.1 ? "var(--pass)" : undefined }}>{t.avg_sentiment}</td></tr>)}</tbody></table>
            )}
          </Panel>
          <Panel title="Competitor comparison" pad={false}>
            {audit.data.competitors.length < 2 ? <p className="muted" style={{ padding: 16 }}>Add competitor listings in <Link to="/local/competitors">Competitor research</Link> to compare.</p> : (
              <div style={{ height: 220, padding: 12 }}><ResponsiveContainer><BarChart data={audit.data.competitors} layout="vertical" margin={{ left: 20 }}>
                <XAxis type="number" fontSize={11} /><YAxis type="category" dataKey="name" width={120} fontSize={11} /><Tooltip />
                <Bar dataKey="review_count" name="Reviews" fill="var(--accent)" radius={[0, 3, 3, 0]} /></BarChart></ResponsiveContainer></div>
            )}
          </Panel>
        </div>
        <Panel title="Reviews" pad={false} actions={<>
          <select className="input" style={{ width: "auto" }} aria-label="Reply status" value={filter.status} onChange={(e) => setFilter({ ...filter, status: e.target.value })}>
            <option value="">All replies</option>{["none", "draft", "review", "approved", "published", "rejected"].map((s) => <option key={s} value={s}>{s}</option>)}</select>
          <select className="input" style={{ width: "auto" }} aria-label="Sentiment" value={filter.sentiment} onChange={(e) => setFilter({ ...filter, sentiment: e.target.value })}>
            <option value="">All sentiment</option><option value="positive">Positive</option><option value="neutral">Neutral</option><option value="negative">Negative</option></select></>}>
          {list.loading && !list.data ? <Loading /> : !list.data?.length ? <Empty title="No reviews">Import a CSV or add reviews manually.</Empty> :
            list.data.map((r) => <ReviewRow key={r.id} r={r} aiOk={!!L.status?.ai.connected} onChange={refresh} />)}
        </Panel>
      </>)}
      {add && <AddReview lid={L.lid} onClose={() => setAdd(false)} onDone={refresh} />}
    </>
  );
}

function ReviewRow({ r, aiOk, onChange }: { r: any; aiOk: boolean; onChange: () => void }) {
  const can = useCan();
  const toast = useToast();
  const { run, busy } = useAction();
  const [text, setText] = useState<string | null>(null);
  const act = async (path: string, body?: any, ok?: string) => { const x = await run(() => api(`/local/reviews/${r.id}/${path}`, { body: body ?? {} }), ok); if (x) onChange(); return x; };
  const publish = async () => {
    const x: any = await run(() => api(`/local/reviews/${r.id}/publish`, { body: {} }));
    if (!x) return;
    if (x.queued) toast("Publishing to Google Business Profile…"); else { copy(x.text); toast(`${x.message} (reply copied to clipboard)`, true); }
    onChange();
  };
  const draft = r.reply_draft;
  return (
    <div className="lc-review">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div className="row" style={{ gap: 8 }}><Stars n={r.rating} /><b>{r.author || "Anonymous"}</b><span className="faint small">{fmtDate(r.reviewed_at)}</span>
          <Badge kind={r.sentiment_label === "positive" ? "pass" : r.sentiment_label === "negative" ? "fail" : undefined}>{r.sentiment_label}</Badge>
          {r.topics.map((t: string) => <span key={t} className="lc-chip">{t}</span>)}</div>
        <StatusBadge s={r.reply_status} />
      </div>
      {r.body && <blockquote>{r.body}</blockquote>}
      {r.reply && <div className="lc-reply"><b>Owner reply</b> · {fmtDate(r.replied_at)}<br />{r.reply}</div>}
      {r.reply_status !== "published" && draft && text === null && <div className="lc-reply" style={{ borderLeftColor: "var(--medium)" }}><b>Draft reply</b>{r.reply_source === "ai" && " (AI)"}<br />{draft}</div>}
      {r.publish_error && <div className="notice warn small" style={{ marginTop: 6 }}>{r.publish_error}</div>}
      {text !== null && <div style={{ marginTop: 8 }}><textarea className="input" rows={4} value={text} onChange={(e) => setText(e.target.value)} aria-label="Reply text" />
        <div className="row" style={{ marginTop: 6 }}><button className="btn sm primary" disabled={!text.trim() || busy} onClick={async () => { if (await run(() => api(`/local/reviews/${r.id}/draft`, { method: "PUT", body: { text } }), "Draft saved")) { setText(null); onChange(); } }}>Save draft</button>
          <button className="btn sm ghost" onClick={() => setText(null)}>Cancel</button></div></div>}
      {r.reply_status !== "published" && text === null && (
        <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
          {can("create") && ["none", "draft", "rejected"].includes(r.reply_status) && <button className="btn sm" disabled={busy || !aiOk} title={aiOk ? "" : "AI provider NOT CONNECTED"} onClick={() => act("ai-draft", {}, "AI draft ready — review it before submitting")}>AI draft</button>}
          {can("edit") && ["none", "draft", "rejected", "review", "approved"].includes(r.reply_status) && <button className="btn sm" onClick={() => setText(draft || "")}>{draft ? "Edit draft" : "Write reply"}</button>}
          {can("edit") && ["draft", "rejected"].includes(r.reply_status) && draft && <button className="btn sm" disabled={busy} onClick={() => act("submit", undefined, "Submitted for approval")}>Submit for approval</button>}
          {can("approve") && r.reply_status === "review" && <><button className="btn sm primary" disabled={busy} onClick={() => act("approve", {}, "Approved")}>Approve</button>
            <button className="btn sm danger" disabled={busy} onClick={() => { const n = prompt("Reason for rejection"); if (n) act("reject", { notes: n }, "Rejected"); }}>Reject</button></>}
          {can("publish") && r.reply_status === "approved" && <><button className="btn sm primary" disabled={busy} onClick={publish}>Publish to Google</button>
            <button className="btn sm" onClick={() => { copy(draft); toast("Reply copied"); }}>Copy</button>
            <button className="btn sm" disabled={busy} onClick={() => act("mark-published", undefined, "Marked as published")}>Mark as posted manually</button></>}
        </div>
      )}
    </div>
  );
}

function AddReview({ lid, onClose, onDone }: { lid: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ rating: "5", author: "", body: "", reviewed_at: new Date().toISOString().slice(0, 10), reply: "" });
  const { run, busy } = useAction();
  return (
    <Modal title="Add review" onClose={onClose} foot={<button className="btn primary" disabled={busy} onClick={async () => {
      if (await run(() => api(`/local/locations/${lid}/reviews`, { body: { rating: +f.rating, author: f.author || null, body: f.body || null, reviewed_at: f.reviewed_at ? f.reviewed_at + "T12:00:00Z" : null, reply: f.reply || null } }), "Review added")) { onDone(); onClose(); }
    }}>Add</button>}>
      <div className="form">
        <div className="grid g3">
          <label className="field">Rating<select className="input" value={f.rating} onChange={(e) => setF({ ...f, rating: e.target.value })}>{[5, 4, 3, 2, 1].map((n) => <option key={n}>{n}</option>)}</select></label>
          <label className="field">Author<input className="input" value={f.author} onChange={(e) => setF({ ...f, author: e.target.value })} /></label>
          <label className="field">Date<input className="input" type="date" value={f.reviewed_at} onChange={(e) => setF({ ...f, reviewed_at: e.target.value })} /></label>
        </div>
        <label className="field">Review text<textarea className="input" rows={4} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} /></label>
        <label className="field">Existing owner reply<span className="hint">Optional — if it has already been answered</span><textarea className="input" rows={2} value={f.reply} onChange={(e) => setF({ ...f, reply: e.target.value })} /></label>
      </div>
    </Modal>
  );
}
