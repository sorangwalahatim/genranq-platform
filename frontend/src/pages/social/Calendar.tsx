import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Drawer, Empty, ErrorBox, Modal, PageHead, Panel, Select } from "../../components/ui";
import { api } from "../../lib/api";
import { fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useToast } from "../../lib/hooks";
import { CHANNEL_LABEL, CHANNELS, ChannelTag, ProjectPicker, STATES, StatusBadge, fromLocalInput, toLocalInput, useProjects } from "./shared";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const startOfWeek = (d: Date) => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };

export default function Calendar() {
  const { projects, pid, setPid } = useProjects(true);
  const [view, setView] = useState<"month" | "week">("month");
  const [anchor, setAnchor] = useState(() => new Date());
  const [channel, setChannel] = useState("");
  const [state, setState] = useState("");
  const [status, setStatus] = useState("");
  const [open, setOpen] = useState<any>(null);
  const [scheduling, setScheduling] = useState<{ item: any; date: Date } | null>(null);
  const [dropKey, setDropKey] = useState("");
  const toast = useToast();
  const { run } = useAction();

  const [start, days] = useMemo(() => {
    if (view === "week") return [startOfWeek(anchor), 7];
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const s = startOfWeek(first);
    const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
    const n = Math.ceil(((last.getTime() - s.getTime()) / 86400000 + 1) / 7) * 7;
    return [s, n];
  }, [view, anchor]);
  const end = addDays(start, days);
  const qs = new URLSearchParams({ start: start.toISOString(), end: end.toISOString() });
  if (pid) qs.set("project_id", pid);
  if (channel) qs.set("channel", channel);
  if (state) qs.set("state", state);
  if (status) qs.set("status", status);
  const { data, error, reload } = useApi<any>(`/social/calendar?${qs}`, [qs.toString()]);
  const byDay = useMemo(() => {
    const m: Record<string, any[]> = {};
    for (const e of data?.events || []) (m[dayKey(new Date(e.at))] ||= []).push(e);
    return m;
  }, [data]);

  const move = (dir: number) => setAnchor((a) => view === "week" ? addDays(a, 7 * dir) : new Date(a.getFullYear(), a.getMonth() + dir, 1));
  const title = view === "week" ? `${start.toLocaleDateString(undefined, { day: "numeric", month: "short" })} – ${addDays(start, 6).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`
    : anchor.toLocaleDateString(undefined, { month: "long", year: "numeric" });

  const onDrop = async (date: Date, e: React.DragEvent) => {
    e.preventDefault(); setDropKey("");
    const raw = e.dataTransfer.getData("application/json");
    if (!raw) return;
    const d = JSON.parse(raw);
    if (d.kind === "event") {
      const old = new Date(d.at);
      const at = new Date(date); at.setHours(old.getHours(), old.getMinutes(), 0, 0);
      if (at.getTime() < Date.now()) { toast("Cannot move a post into the past", true); return; }
      await run(() => api(`/social/schedules/${d.id}`, { method: "PATCH", body: { scheduled_at: at.toISOString() } }), `Moved to ${fmtDateTime(at.toISOString())}`);
      reload();
    } else if (d.kind === "item") {
      setScheduling({ item: d.item, date });
    }
  };

  const today = dayKey(new Date());
  return (
    <>
      <PageHead title="Content calendar" sub="Every scheduled post across projects and channels, plus Google Business posts. Drag approved posts to reschedule; drag approved content from the side list onto a day to schedule it.">
        <ProjectPicker projects={projects} pid={pid} setPid={setPid} allowAll />
      </PageHead>
      <div className="cal-toolbar">
        <button className="btn sm" onClick={() => move(-1)} aria-label="Previous">‹</button>
        <button className="btn sm" onClick={() => setAnchor(new Date())}>Today</button>
        <button className="btn sm" onClick={() => move(1)} aria-label="Next">›</button>
        <h2>{title}</h2>
        <div className="seg">{(["month", "week"] as const).map((v) => <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{human(v)}</button>)}</div>
        <span className="spacer" />
        <Select value={channel} onChange={setChannel} label="Channel" placeholder="All channels" options={[...CHANNELS, "gbp"].map((c) => ({ value: c, label: CHANNEL_LABEL[c] }))} />
        <Select value={state} onChange={setState} label="Content state" placeholder="Any content state" options={STATES.map((s) => ({ value: s, label: human(s) }))} />
        <Select value={status} onChange={setStatus} label="Post status" placeholder="Any post status" options={["scheduled", "publishing", "published", "failed", "cancelled"].map((s) => ({ value: s, label: human(s) }))} />
      </div>
      <ErrorBox error={error} />
      <div className="cal-layout">
        <div>
          <div className={`cal-grid ${view}`} role="grid" aria-label="Calendar">
            {DOW.map((d) => <div key={d} className="cal-dow" role="columnheader">{d}</div>)}
            {Array.from({ length: days }, (_, i) => {
              const d = addDays(start, i);
              const k = dayKey(d);
              const evs = byDay[k] || [];
              const other = view === "month" && d.getMonth() !== anchor.getMonth();
              return (
                <div key={k} role="gridcell" className={`cal-day ${other ? "other" : ""} ${k === today ? "today" : ""} ${dropKey === k ? "drop" : ""}`}
                  onDragOver={(e) => { e.preventDefault(); setDropKey(k); }} onDragLeave={() => setDropKey("")} onDrop={(e) => onDrop(d, e)}>
                  <span className="cal-num">{d.getDate()}</span>
                  {evs.map((e) => (
                    <div key={e.kind + e.id} className={`cal-ev st-${e.status} kind-${e.kind}`} draggable={e.draggable}
                      style={e.color ? { borderLeftColor: e.color } : undefined}
                      onDragStart={(ev) => ev.dataTransfer.setData("application/json", JSON.stringify({ kind: "event", id: e.id, at: e.at }))}
                      onClick={() => setOpen(e)} title={`${e.title} — ${e.account || ""}`}>
                      <div className="t">{new Date(e.at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })} {e.title}</div>
                      <div className="m"><ChannelTag ch={e.channel} />{!pid && <span>{e.project}</span>}</div>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
          {data && !data.events.length && <p className="muted small" style={{ marginTop: 10 }}>Nothing scheduled in this range{channel || state || status ? " for these filters" : ""}.</p>}
        </div>
        <div className="stack">
          <Panel title={`Approved, not scheduled (${data?.unscheduled?.length ?? 0})`}>
            {!data?.unscheduled?.length ? <p className="muted small">Approved content appears here until it is scheduled.</p> :
              data.unscheduled.map((it: any) => (
                <div key={it.id} className="side-item" draggable={data.can_schedule}
                  onDragStart={(ev) => ev.dataTransfer.setData("application/json", JSON.stringify({ kind: "item", item: it }))}
                  onClick={() => data.can_schedule && setScheduling({ item: it, date: addDays(new Date(), 1) })}>
                  <b>{it.title || "(untitled)"}</b><div className="faint">{it.project}{it.channel ? ` · ${CHANNEL_LABEL[it.channel] || it.channel}` : ""}</div>
                </div>))}
            {data && !data.can_schedule && <p className="faint small" style={{ marginTop: 8 }}>Scheduling needs the publish permission (manager or above).</p>}
          </Panel>
          <Panel title={`Awaiting approval (${data?.awaiting_approval?.length ?? 0})`} actions={<Link className="btn sm" to="/approvals">Approval queue</Link>}>
            {!data?.awaiting_approval?.length ? <p className="muted small">No content waiting for review.</p> :
              data.awaiting_approval.map((it: any) => (
                <div key={it.id} className="side-item" style={{ cursor: "default" }}>
                  <b>{it.title || "(untitled)"}</b><div className="faint">{it.project} · {it.approval_status ? <StatusBadge s={it.approval_status} /> : "no approval request"}</div>
                </div>))}
            <p className="faint small" style={{ marginTop: 8 }}>Only approved content can be scheduled or published.</p>
          </Panel>
        </div>
      </div>
      {open && <EventDrawer e={open} onClose={() => setOpen(null)} onDone={() => { setOpen(null); reload(); }} />}
      {scheduling && <ScheduleModal item={scheduling.item} date={scheduling.date} onClose={() => setScheduling(null)} onDone={reload} />}
    </>
  );
}

function EventDrawer({ e, onClose, onDone }: { e: any; onClose: () => void; onDone: () => void }) {
  const { run } = useAction();
  const [at, setAt] = useState(toLocalInput(new Date(e.at)));
  return (
    <Drawer title={e.title} onClose={onClose} foot={e.kind === "post" && ["scheduled", "failed"].includes(e.status) ? (
      <button className="btn danger" onClick={() => confirm("Cancel this scheduled post?") && run(() => api(`/social/schedules/${e.id}`, { method: "DELETE" }), "Post cancelled").then(onDone)}>Cancel post</button>) : undefined}>
      <dl className="kv">
        <dt>When</dt><dd>{fmtDateTime(e.at)}</dd>
        <dt>Project</dt><dd>{e.project}</dd>
        <dt>Channel</dt><dd><ChannelTag ch={e.channel} /> {e.account}</dd>
        {e.kind === "post" && <><dt>Content state</dt><dd><StatusBadge s={e.state} /></dd></>}
        <dt>Post status</dt><dd><StatusBadge s={e.status} /></dd>
        {e.campaign && <><dt>Campaign</dt><dd>{e.campaign}</dd></>}
        {e.external_url && <><dt>Live post</dt><dd><a href={e.external_url} target="_blank" rel="noreferrer">Open on {CHANNEL_LABEL[e.channel]}</a></dd></>}
        {e.error && <><dt>Error</dt><dd style={{ color: "var(--critical)" }}>{e.error}</dd></>}
      </dl>
      {e.kind === "gbp_post" && <p className="muted small" style={{ marginTop: 12 }}>Google Business posts are managed in Local SEO and shown here read-only.</p>}
      {e.draggable && (
        <form className="row" style={{ marginTop: 16 }} onSubmit={(ev) => { ev.preventDefault(); run(() => api(`/social/schedules/${e.id}`, { method: "PATCH", body: { scheduled_at: fromLocalInput(at) } }), "Rescheduled").then(onDone); }}>
          <input className="input" type="datetime-local" value={at} onChange={(ev) => setAt(ev.target.value)} aria-label="New time" style={{ width: "auto" }} />
          <button className="btn primary">Reschedule</button>
        </form>
      )}
      {e.status === "failed" && <p className="small" style={{ marginTop: 12 }}>Retry failed posts from <Link to="/social/automation">Social automation → Publish queue</Link>.</p>}
    </Drawer>
  );
}

export function ScheduleModal({ item, date, onClose, onDone }: { item: any; date: Date; onClose: () => void; onDone: () => void }) {
  const d = new Date(date); if (d.getHours() === 0) d.setHours(9, 0, 0, 0);
  const [at, setAt] = useState(toLocalInput(d));
  const { data: accts } = useApi<any>(`/social/accounts?project_id=${item.project_id}`);
  const { data: camps } = useApi<any[]>(`/social/campaigns?project_id=${item.project_id}`);
  const [sel, setSel] = useState<string[]>([]);
  const [camp, setCamp] = useState("");
  const { run, busy } = useAction();
  const list: any[] = (accts?.accounts || []).filter((a: any) => a.status !== "disabled");
  return (
    <Modal title={`Schedule “${item.title || "post"}”`} onClose={onClose}
      foot={<button className="btn primary" disabled={busy || !sel.length} onClick={() => run(() => api("/social/schedules", { body: { content_item_id: item.id, account_ids: sel, scheduled_at: fromLocalInput(at), campaign_id: camp || null } }), "Scheduled").then((r) => { if (r) { onDone(); onClose(); } })}>Schedule</button>}>
      <div className="form">
        <label className="field">Publish at<input className="input" type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /></label>
        <div className="field">Accounts
          {!list.length ? <Empty title="No accounts in this project"><Link to="/social/accounts">Map social accounts</Link> first.</Empty> :
            <div className="chips" style={{ marginTop: 6 }}>{list.map((a) => (
              <span key={a.id} className={`chip ${sel.includes(a.id) ? "on" : ""}`} role="checkbox" aria-checked={sel.includes(a.id)} tabIndex={0}
                onClick={() => setSel((s) => s.includes(a.id) ? s.filter((x) => x !== a.id) : [...s, a.id])}>
                {CHANNEL_LABEL[a.channel]} · {a.name}{a.state !== "ready" ? ` (${a.state})` : ""}</span>))}</div>}
        </div>
        {!!camps?.length && <label className="field">Campaign<select className="input" value={camp} onChange={(e) => setCamp(e.target.value)}>
          <option value="">None</option>{camps.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}
        <p className="faint small">Accounts whose provider is not connected will fail at publish time with NOT CONNECTED — nothing is posted.</p>
      </div>
    </Modal>
  );
}
