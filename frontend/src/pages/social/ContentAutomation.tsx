import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Empty, ErrorBox, Loading, Modal, NotConnected, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { CHANNEL_LABEL, CHANNELS, ChannelTag, ProjectPicker, StatusBadge, fromLocalInput, toLocalInput, useProjects } from "./shared";

const COL_LABEL: Record<string, string> = { draft: "Draft", generated: "Generated", review: "In review", approved: "Approved", scheduled: "Scheduled", published: "Published", failed: "Failed" };

export default function ContentAutomation() {
  const { projects, pid, setPid, loaded } = useProjects();
  const [sp, setSp] = useSearchParams();
  const bid = sp.get("batch");
  const { data: batches, error, loading, reload } = useApi<any[]>(pid ? `/social/batches?project_id=${pid}` : null, [pid]);
  const [create, setCreate] = useState(false);
  const can = useCan();
  return (
    <>
      <PageHead title="Content automation" sub="Bulk pipeline: pick drafts or import topics → AI generation → approval → schedule → publish, with a status board per batch.">
        <ProjectPicker projects={projects} pid={pid} setPid={(v) => { setPid(v); setSp({}); }} />
        {can("create") && pid && <button className="btn primary" onClick={() => setCreate(true)}>New batch</button>}
      </PageHead>
      {loaded && !projects.length ? <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>} /></Panel> : bid ? (
        <Board bid={bid} onBack={() => { setSp({}); reload(); }} />
      ) : (
        <Panel title="Batches" pad={false}>
          <ErrorBox error={error} />
          {loading && !batches ? <Loading /> : !batches?.length ? <Empty title="No batches yet" action={can("create") && <button className="btn primary" onClick={() => setCreate(true)}>New batch</button>}>Start from existing drafts, a list of topics or a CSV.</Empty> : (
            <div className="table-wrap"><table className="t">
              <thead><tr><th>Batch</th><th>Source</th><th>Channel</th><th className="r">Items</th><th>Progress</th><th>Created</th></tr></thead>
              <tbody>{batches.map((b) => (
                <tr key={b.id} className="click" onClick={() => setSp({ batch: b.id })}>
                  <td><b>{b.name}</b></td><td>{human(b.source)}</td><td><ChannelTag ch={b.channel} /></td><td className="r num">{b.total}</td>
                  <td className="small">{Object.entries(b.states).map(([k, v]) => <span key={k} style={{ marginRight: 6 }}><StatusBadge s={k}>{`${COL_LABEL[k] || human(k)} ${v}`}</StatusBadge></span>)}</td>
                  <td className="muted small">{ago(b.created_at)}</td>
                </tr>))}</tbody>
            </table></div>
          )}
        </Panel>
      )}
      {create && <CreateBatch pid={pid} onClose={() => setCreate(false)} onDone={(id: string) => { reload(); setSp({ batch: id }); }} />}
    </>
  );
}

function CreateBatch({ pid, onClose, onDone }: any) {
  const [mode, setMode] = useState<"drafts" | "topics" | "csv">("topics");
  const [f, setF] = useState({ name: "", channel: "", topics: "", instructions: "" });
  const [sel, setSel] = useState<string[]>([]);
  const file = useRef<HTMLInputElement>(null);
  const { data: drafts } = useApi<any[]>(`/social/content/drafts?project_id=${pid}`);
  const { run, busy } = useAction();
  const submit = () => run<any>(() => {
    if (mode === "csv") {
      const fd = new FormData();
      fd.append("project_id", pid); fd.append("name", f.name); if (f.channel) fd.append("channel", f.channel);
      if (f.instructions) fd.append("instructions", f.instructions);
      fd.append("file", file.current!.files![0]);
      return api("/social/batches/csv", { form: fd });
    }
    return api("/social/batches", { body: { project_id: pid, name: f.name, channel: f.channel || null, instructions: f.instructions || null,
      item_ids: mode === "drafts" ? sel : [], topics: mode === "topics" ? f.topics.split("\n").map((t) => t.trim()).filter(Boolean) : [] } });
  }, "Batch created").then((r) => { if (r) { onDone(r.id); onClose(); } });
  const ready = f.name && (mode === "drafts" ? sel.length : mode === "topics" ? f.topics.trim() : true);
  return (
    <Modal title="New content batch" onClose={onClose} wide foot={<button className="btn primary" disabled={busy || !ready} onClick={submit}>Create batch</button>}>
      <div className="form">
        <div className="grid g2">
          <label className="field">Name<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></label>
          <label className="field">Channel<select className="input" value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}><option value="">Any / per item</option>{CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select></label>
        </div>
        <div className="seg">{(["topics", "drafts", "csv"] as const).map((m) => <button key={m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>{m === "topics" ? "Type topics" : m === "drafts" ? "Pick drafts" : "Upload CSV"}</button>)}</div>
        {mode === "topics" && <label className="field">Topics / briefs<span className="hint">One per line — each becomes a draft</span><textarea className="input" rows={7} value={f.topics} onChange={(e) => setF({ ...f, topics: e.target.value })} /></label>}
        {mode === "csv" && <label className="field">CSV file<span className="hint">Columns: topic (or title) required; optional channel, link_url, media_url</span><input ref={file} type="file" accept=".csv" className="input" /></label>}
        {mode === "drafts" && (!drafts?.length ? <p className="muted">No drafts in this project.</p> : (
          <div style={{ maxHeight: 260, overflowY: "auto" }}><table className="t"><tbody>{drafts.map((d) => (
            <tr key={d.id}><td style={{ width: 28 }}><input type="checkbox" checked={sel.includes(d.id)} onChange={() => setSel((s) => s.includes(d.id) ? s.filter((x) => x !== d.id) : [...s, d.id])} aria-label={`Select ${d.title}`} /></td>
              <td>{d.title || "(untitled)"}{d.in_batch && <span className="faint small"> · already in a batch</span>}</td><td><ChannelTag ch={d.channel} /></td></tr>))}</tbody></table></div>
        ))}
        <label className="field">Instructions for the AI<span className="hint">Optional: tone, hashtags, call to action…</span><textarea className="input" rows={2} value={f.instructions} onChange={(e) => setF({ ...f, instructions: e.target.value })} /></label>
      </div>
    </Modal>
  );
}

function Board({ bid, onBack }: { bid: string; onBack: () => void }) {
  const { data: b, error, reload } = useApi<any>(`/social/batches/${bid}`, [bid], { poll: 8000 });
  const [sel, setSel] = useState<string[]>([]);
  const [sched, setSched] = useState(false);
  const can = useCan();
  const { run, busy } = useAction();
  if (!b) return error ? <ErrorBox error={error} /> : <Loading />;
  const review = b.items.filter((i: any) => i.state === "review");
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <button className="btn sm" onClick={onBack}>‹ All batches</button>
        <h2 style={{ fontSize: 16 }}>{b.name}</h2><ChannelTag ch={b.channel} />
        <span className="spacer" />
        {can("create") && <button className="btn" disabled={busy || !b.counts.draft} onClick={() => run<any>(() => api(`/social/batches/${bid}/generate`, { method: "POST" })).then((r) => { if (r) reload(); })}>Generate drafts ({b.counts.draft})</button>}
        {can("create") && <button className="btn" disabled={busy || !b.counts.generated} onClick={() => run(() => api(`/social/batches/${bid}/request-approval`, { method: "POST" }), "Sent for approval").then(reload)}>Request approval ({b.counts.generated})</button>}
        {can("approve") && <button className="btn" disabled={busy || !sel.length} onClick={() => run<any>(() => api(`/social/batches/${bid}/approve`, { body: { item_ids: sel } })).then((r) => { if (r) { const skipped = Object.values(r.results).filter((x: any) => x !== "approved"); if (skipped.length) alert(`Some items were not approved:\n${skipped.join("\n")}`); setSel([]); reload(); } })}>Approve selected ({sel.length})</button>}
        {can("publish") && <button className="btn primary" disabled={!b.counts.approved} onClick={() => setSched(true)}>Schedule approved ({b.counts.approved})</button>}
      </div>
      {!b.ai_connected && b.counts.draft > 0 && <div style={{ marginBottom: 12 }}><NotConnected what="AI generation">needs Claude, OpenAI, Gemini or Perplexity. <Link to="/integrations">Connect a provider</Link> — until then items stay drafts and can be written by hand in the content studio.</NotConnected></div>}
      <div className="board">
        {b.columns.map((c: string) => (
          <div key={c} className="col">
            <div className="col-head"><b>{COL_LABEL[c]}</b><span className="num muted">{b.counts[c]}</span></div>
            {b.board[c].map((i: any) => (
              <div key={i.id} className="card">
                <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                  <div className="title">{i.title || "(untitled)"}</div>
                  {i.state === "review" && can("approve") && <input type="checkbox" checked={sel.includes(i.id)} onChange={() => setSel((s) => s.includes(i.id) ? s.filter((x) => x !== i.id) : [...s, i.id])} aria-label={`Select ${i.title} for approval`} />}
                </div>
                <ChannelTag ch={i.channel} />
                {i.body && i.body !== i.title && <div className="body">{i.body}</div>}
                {i.gen_status && i.gen_status !== "pending" && i.gen_status !== "generated" && <div className="small" style={{ marginTop: 4 }}><StatusBadge s={i.gen_status} />{i.gen_error && <div className="faint">{i.gen_error}</div>}</div>}
                {i.approval_status && i.state === "review" && <div className="small faint" style={{ marginTop: 4 }}>Approval: {i.approval_status}</div>}
                {i.schedules.map((s: any) => <div key={s.schedule_id} className="small faint" style={{ marginTop: 4 }}>{fmtDateTime(s.at)} · {human(s.status)}</div>)}
              </div>
            ))}
          </div>
        ))}
      </div>
      {review.length > 0 && can("approve") && <p className="faint small" style={{ marginTop: 8 }}>Tick items in review and choose “Approve selected”. You cannot approve your own submission unless you are an owner.</p>}
      {sched && <ScheduleBatch b={b} onClose={() => setSched(false)} onDone={reload} />}
    </>
  );
}

function ScheduleBatch({ b, onClose, onDone }: any) {
  const [mode, setMode] = useState<"spread" | "rule">("spread");
  const { data: accts } = useApi<any>(`/social/accounts?project_id=${b.project_id}`);
  const { data: rules } = useApi<any[]>(`/social/rules?project_id=${b.project_id}`);
  const t = new Date(Date.now() + 3600_000); t.setMinutes(0, 0, 0);
  const [f, setF] = useState({ start_at: toLocalInput(t), interval_hours: 24, rule_id: "", account_ids: [] as string[] });
  const { run, busy } = useAction();
  const queueRules = (rules || []).filter((r) => r.mode === "queue");
  const submit = () => run<any>(() => api(`/social/batches/${b.id}/schedule`, { body: mode === "rule" ? { mode, rule_id: f.rule_id } : { mode, account_ids: f.account_ids, start_at: fromLocalInput(f.start_at), interval_hours: Number(f.interval_hours) } }))
    .then((r) => { if (r) { const n = r.mode === "rule" ? r.materialized.created : r.scheduled.length; alert(`${n} post(s) scheduled${r.skipped?.length ? `; ${r.skipped.length} skipped: ${r.skipped.map((s: any) => s.reason).join("; ")}` : ""}`); onDone(); onClose(); } });
  return (
    <Modal title="Schedule approved items" onClose={onClose} foot={<button className="btn primary" disabled={busy || (mode === "rule" ? !f.rule_id : !f.account_ids.length)} onClick={submit}>Schedule</button>}>
      <div className="form">
        <div className="seg"><button className={mode === "spread" ? "on" : ""} onClick={() => setMode("spread")}>Spread from a start time</button><button className={mode === "rule" ? "on" : ""} onClick={() => setMode("rule")}>Add to a recurring rule</button></div>
        {mode === "spread" ? <>
          <div className="grid g2">
            <label className="field">First post<input className="input" type="datetime-local" value={f.start_at} onChange={(e) => setF({ ...f, start_at: e.target.value })} /></label>
            <label className="field">Hours between posts<input className="input" type="number" min={0.25} step={0.25} value={f.interval_hours} onChange={(e) => setF({ ...f, interval_hours: Number(e.target.value) })} /></label>
          </div>
          <div className="field">Accounts<div className="chips" style={{ marginTop: 6 }}>{(accts?.accounts || []).map((a: any) => <span key={a.id} role="checkbox" aria-checked={f.account_ids.includes(a.id)} tabIndex={0} className={`chip ${f.account_ids.includes(a.id) ? "on" : ""}`} onClick={() => setF((x) => ({ ...x, account_ids: x.account_ids.includes(a.id) ? x.account_ids.filter((y) => y !== a.id) : [...x.account_ids, a.id] }))}>{CHANNEL_LABEL[a.channel]} · {a.name}</span>)}</div></div>
        </> : !queueRules.length ? <p className="muted">No queue rules in this project — <Link to="/social/automation">create one</Link>.</p> :
          <label className="field">Rule<select className="input" value={f.rule_id} onChange={(e) => setF({ ...f, rule_id: e.target.value })}><option value="">Choose…</option>{queueRules.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>}
      </div>
    </Modal>
  );
}
