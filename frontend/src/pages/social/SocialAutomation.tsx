import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Drawer, Empty, ErrorBox, Loading, Modal, PageHead, Panel, Select } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { CHANNEL_LABEL, ChannelTag, ProjectPicker, ProviderBanners, StatusBadge, WEEKDAYS, browserTz, useProjects } from "./shared";

export default function SocialAutomation() {
  const { projects, pid, setPid, loaded } = useProjects();
  const [tab, setTab] = useState<"rules" | "campaigns" | "queue">("rules");
  return (
    <>
      <PageHead title="Social automation" sub="Recurring publishing rules, campaigns and the publish queue. Only approved content is ever scheduled; a post is marked published only when the platform returns its post ID.">
        <ProjectPicker projects={projects} pid={pid} setPid={setPid} />
      </PageHead>
      {loaded && !projects.length ? <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>} /></Panel> : (
        <>
          <div className="tabs" role="tablist">
            {(["rules", "campaigns", "queue"] as const).map((t) => <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {t === "rules" ? "Recurring rules" : t === "campaigns" ? "Campaigns" : "Publish queue"}</button>)}
          </div>
          {pid && tab === "rules" && <Rules pid={pid} />}
          {pid && tab === "campaigns" && <Campaigns pid={pid} />}
          {pid && tab === "queue" && <Queue pid={pid} />}
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ rules
function Rules({ pid }: { pid: string }) {
  const { data, error, loading, reload } = useApi<any[]>(`/social/rules?project_id=${pid}`, [pid]);
  const { data: accts } = useApi<any>(`/social/accounts?project_id=${pid}`, [pid]);
  const [edit, setEdit] = useState<any>(null);
  const [queue, setQueue] = useState<any>(null);
  const can = useCan();
  const { run } = useAction();
  const names: Record<string, string> = Object.fromEntries((accts?.accounts || []).map((a: any) => [a.id, `${CHANNEL_LABEL[a.channel]} · ${a.name}`]));
  return (
    <>
      <ProviderBanners providers={accts?.providers} only={[...new Set<string>((accts?.accounts || []).map((a: any) => a.provider))]} />
      <Panel title="Recurring rules" actions={can("publish") && <button className="btn primary sm" onClick={() => setEdit({})}>New rule</button>} pad={false}>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !data?.length ? <Empty title="No recurring rules">A rule posts queued approved content at fixed weekly times in a timezone — e.g. Mon/Wed/Fri at 09:00 Europe/London.</Empty> : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Rule</th><th>Schedule</th><th>Accounts</th><th>Mode</th><th className="r">Queued items</th><th>Posts</th><th>Next post</th><th /></tr></thead>
            <tbody>{data.map((r) => (
              <tr key={r.id}>
                <td><b>{r.name}</b>{!r.enabled && <> <StatusBadge s="disabled" /></>}</td>
                <td className="small">{r.weekdays.map((d: number) => WEEKDAYS[d]).join(", ")} at {r.times.join(", ")}<div className="faint">{r.timezone}{r.start_date || r.end_date ? ` · ${r.start_date || "…"} – ${r.end_date || "…"}` : ""}</div></td>
                <td className="small">{(r.account_ids || []).map((a: string) => names[a] || "removed").join(", ") || <span className="faint">none</span>}</td>
                <td>{human(r.mode)}</td>
                <td className="r num">{(r.item_ids || []).length}</td>
                <td className="small">{Object.entries(r.counts || {}).map(([k, v]) => <span key={k} style={{ marginRight: 6 }}><StatusBadge s={k}>{`${human(k)} ${v}`}</StatusBadge></span>)}</td>
                <td className="small">{r.next_post_at ? fmtDateTime(r.next_post_at) : <span className="faint">{(r.item_ids || []).length ? "—" : "queue empty"}</span>}</td>
                <td className="r nowrap">{can("publish") && <>
                  <button className="btn sm" onClick={() => setQueue(r)}>Queue content</button>{" "}
                  <button className="btn sm" onClick={() => setEdit(r)}>Edit</button>{" "}
                  <button className="btn sm" onClick={() => run(() => api(`/social/rules/${r.id}`, { method: "PATCH", body: { enabled: !r.enabled } }), r.enabled ? "Rule paused — future posts cancelled" : "Rule enabled").then(reload)}>{r.enabled ? "Pause" : "Enable"}</button>{" "}
                  <button className="btn sm danger" onClick={() => confirm("Delete this rule? Its future posts are cancelled.") && run(() => api(`/social/rules/${r.id}`, { method: "DELETE" }), "Rule deleted").then(reload)}>Delete</button></>}
                </td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Panel>
      {edit && <RuleModal pid={pid} rule={edit.id ? edit : null} accounts={accts?.accounts || []} onClose={() => setEdit(null)} onDone={reload} />}
      {queue && <QueueItems pid={pid} rule={queue} onClose={() => setQueue(null)} onDone={reload} />}
    </>
  );
}

function RuleModal({ pid, rule, accounts, onClose, onDone }: any) {
  const [f, setF] = useState<any>(() => rule ? { ...rule, times: rule.times.join(", ") } :
    { name: "", weekdays: [0, 2, 4], times: "09:00", timezone: browserTz(), account_ids: [], mode: "queue", campaign_id: "", start_date: "", end_date: "", horizon_days: 14 });
  const { data: camps } = useApi<any[]>(`/social/campaigns?project_id=${pid}`);
  const [preview, setPreview] = useState<any[] | null>(null);
  const [perr, setPerr] = useState("");
  const { run, busy } = useAction();
  const times = f.times.split(/[,\s]+/).filter(Boolean);
  useEffect(() => {
    if (!f.weekdays.length || !times.length) { setPreview(null); return; }
    const t = setTimeout(() => api("/social/rules/preview", { body: { weekdays: f.weekdays, times, timezone: f.timezone, days: 14, start_date: f.start_date || null, end_date: f.end_date || null } })
      .then((r: any) => { setPreview(r.slots); setPerr(""); }).catch((e) => { setPreview(null); setPerr(e.message); }), 300);
    return () => clearTimeout(t);
  }, [f.weekdays.join(), f.times, f.timezone, f.start_date, f.end_date]);
  const save = () => {
    const body: any = { name: f.name, weekdays: f.weekdays, times, timezone: f.timezone, account_ids: f.account_ids, start_date: f.start_date || null, end_date: f.end_date || null, horizon_days: Number(f.horizon_days) || 14 };
    return run<any>(() => rule ? api(`/social/rules/${rule.id}`, { method: "PATCH", body })
      : api("/social/rules", { body: { ...body, project_id: pid, mode: f.mode, campaign_id: f.campaign_id || null } }),
    rule ? "Rule updated" : "Rule created").then((r) => { if (r) { onDone(); onClose(); } });
  };
  const toggle = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: x[k].includes(v) ? x[k].filter((y: any) => y !== v) : [...x[k], v] }));
  return (
    <Modal title={rule ? `Edit ${rule.name}` : "New recurring rule"} onClose={onClose} wide
      foot={<button className="btn primary" disabled={busy || !f.name || !f.weekdays.length || !times.length || !!perr} onClick={save}>{rule ? "Save" : "Create rule"}</button>}>
      <div className="form">
        <label className="field">Name<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></label>
        <div className="field">Days<div className="chips" style={{ marginTop: 6 }}>{WEEKDAYS.map((d, i) => <span key={d} role="checkbox" aria-checked={f.weekdays.includes(i)} tabIndex={0} className={`chip ${f.weekdays.includes(i) ? "on" : ""}`} onClick={() => toggle("weekdays", i)}>{d}</span>)}</div></div>
        <div className="grid g2">
          <label className="field">Times<span className="hint">24-hour, comma separated (local to the timezone)</span><input className="input" value={f.times} onChange={(e) => setF({ ...f, times: e.target.value })} /></label>
          <label className="field">Timezone<span className="hint">IANA name, e.g. Europe/London</span><input className="input" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></label>
        </div>
        <div className="grid g3">
          <label className="field">Starts<input className="input" type="date" value={f.start_date || ""} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></label>
          <label className="field">Ends<input className="input" type="date" value={f.end_date || ""} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></label>
          <label className="field">Plan ahead (days)<input className="input" type="number" min={1} max={60} value={f.horizon_days} onChange={(e) => setF({ ...f, horizon_days: e.target.value })} /></label>
        </div>
        <div className="field">Accounts<div className="chips" style={{ marginTop: 6 }}>{accounts.length ? accounts.map((a: any) => <span key={a.id} role="checkbox" aria-checked={f.account_ids.includes(a.id)} tabIndex={0} className={`chip ${f.account_ids.includes(a.id) ? "on" : ""}`} onClick={() => toggle("account_ids", a.id)}>{CHANNEL_LABEL[a.channel]} · {a.name}</span>) : <span className="muted small">No accounts — <Link to="/social/accounts">map some first</Link>.</span>}</div></div>
        {!rule && <div className="grid g2">
          <label className="field">Mode<select className="input" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="queue">Queue — each item posted once, in order</option><option value="evergreen">Evergreen — cycle through items repeatedly</option></select></label>
          <label className="field">Campaign<select className="input" value={f.campaign_id} onChange={(e) => setF({ ...f, campaign_id: e.target.value })}><option value="">None</option>{(camps || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        </div>}
        <div className="field">Next 14 days{perr ? <div className="notice err small">{perr}</div> : !preview ? <span className="muted small"> —</span> :
          <div className="small muted" style={{ marginTop: 4, maxHeight: 120, overflowY: "auto" }}>{preview.length} slot(s): {preview.slice(0, 12).map((s) => s.local).join(" · ")}{preview.length > 12 ? " …" : ""}</div>}</div>
      </div>
    </Modal>
  );
}

function QueueItems({ pid, rule, onClose, onDone }: any) {
  const states = rule.mode === "evergreen" ? "approved,scheduled,published" : "approved,scheduled";
  const { data, loading } = useApi<any[]>(`/social/content/items?project_id=${pid}&states=${states}`);
  const [sel, setSel] = useState<string[]>([]);
  const { run, busy } = useAction();
  const queued = new Set(rule.item_ids || []);
  return (
    <Modal title={`Queue content on ${rule.name}`} onClose={onClose} wide
      foot={<button className="btn primary" disabled={busy || !sel.length} onClick={() => run<any>(() => api(`/social/rules/${rule.id}/items`, { body: { content_item_ids: sel } })).then((r) => { if (r) { onDone(); onClose(); } })}>Queue {sel.length || ""}</button>}>
      {loading ? <Loading /> : !data?.length ? <Empty title="No approved content">Content must be approved before it can be queued. <Link to="/automation/content">Content automation</Link></Empty> : (
        <table className="t"><tbody>{data.map((it) => (
          <tr key={it.id}>
            <td style={{ width: 28 }}><input type="checkbox" disabled={queued.has(it.id)} checked={sel.includes(it.id)} onChange={() => setSel((s) => s.includes(it.id) ? s.filter((x) => x !== it.id) : [...s, it.id])} aria-label={`Select ${it.title}`} /></td>
            <td><b>{it.title || "(untitled)"}</b><div className="faint small">{it.body}</div></td><td><ChannelTag ch={it.channel} /></td>
            <td>{queued.has(it.id) ? <StatusBadge s="queued">Queued</StatusBadge> : <StatusBadge s={it.state} />}</td>
          </tr>))}</tbody></table>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------ campaigns
function Campaigns({ pid }: { pid: string }) {
  const { data, error, loading, reload } = useApi<any[]>(`/social/campaigns?project_id=${pid}`, [pid]);
  const [create, setCreate] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const can = useCan();
  return (
    <>
      <Panel title="Campaigns" actions={can("create") && <button className="btn primary sm" onClick={() => setCreate(true)}>New campaign</button>} pad={false}>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !data?.length ? <Empty title="No campaigns">Group posts and rules under a campaign with a start and end date; posts outside the window are refused.</Empty> : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Campaign</th><th>Window</th><th className="r">Posts</th><th>Status of posts</th><th>State</th></tr></thead>
            <tbody>{data.map((c) => (
              <tr key={c.id} className="click" onClick={() => setOpen(c.id)}>
                <td><span className="dot" style={{ background: c.color || "var(--accent)", marginRight: 8 }} /><b>{c.name}</b>{c.description && <div className="faint small">{c.description}</div>}</td>
                <td className="small">{c.start_date ? fmtDate(c.start_date) : "…"} – {c.end_date ? fmtDate(c.end_date) : "…"}</td>
                <td className="r num">{c.total}</td>
                <td className="small">{Object.entries(c.counts).map(([k, v]) => <span key={k} style={{ marginRight: 6 }}><StatusBadge s={k}>{`${human(k)} ${v}`}</StatusBadge></span>)}</td>
                <td><StatusBadge s={c.status} /></td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Panel>
      {create && <CampaignModal pid={pid} onClose={() => setCreate(false)} onDone={reload} />}
      {open && <CampaignDrawer id={open} onClose={() => setOpen(null)} onDone={reload} />}
    </>
  );
}

function CampaignModal({ pid, onClose, onDone }: any) {
  const [f, setF] = useState({ name: "", start_date: "", end_date: "", description: "", color: "#3b5bdb" });
  const { run, busy } = useAction();
  return (
    <Modal title="New campaign" onClose={onClose} foot={<button className="btn primary" disabled={busy || !f.name} onClick={() => run(() => api("/social/campaigns", { body: { ...f, project_id: pid, start_date: f.start_date || null, end_date: f.end_date || null, description: f.description || null } }), "Campaign created").then((r) => { if (r) { onDone(); onClose(); } })}>Create</button>}>
      <div className="form">
        <label className="field">Name<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoFocus /></label>
        <div className="grid g3">
          <label className="field">Start<input className="input" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></label>
          <label className="field">End<input className="input" type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></label>
          <label className="field">Colour<input className="input" type="color" value={f.color} onChange={(e) => setF({ ...f, color: e.target.value })} /></label>
        </div>
        <label className="field">Description<textarea className="input" rows={3} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
      </div>
    </Modal>
  );
}

function CampaignDrawer({ id, onClose, onDone }: any) {
  const { data } = useApi<any>(`/social/campaigns/${id}`);
  const { run } = useAction();
  const can = useCan();
  return (
    <Drawer title={data?.name || "Campaign"} onClose={onClose} foot={can("edit") && data ? <button className="btn" onClick={() => run(() => api(`/social/campaigns/${id}`, { method: "PATCH", body: { status: data.status === "active" ? "archived" : "active" } })).then(() => { onDone(); onClose(); })}>{data.status === "active" ? "Archive" : "Reactivate"}</button> : undefined}>
      {!data ? <Loading /> : <>
        <p className="muted">{data.start_date ? fmtDate(data.start_date) : "No start"} – {data.end_date ? fmtDate(data.end_date) : "no end"} · {data.total} post(s)</p>
        {data.rules.length > 0 && <p className="small">Rules: {data.rules.map((r: any) => r.name).join(", ")}</p>}
        <table className="t" style={{ marginTop: 10 }}><thead><tr><th>When</th><th>Post</th><th>Channel</th><th>Status</th></tr></thead>
          <tbody>{data.posts.map((p: any) => <tr key={p.schedule_id}><td className="small">{fmtDateTime(p.at)}{p.outside_window && <div style={{ color: "var(--critical)" }}>outside window</div>}</td><td>{p.title}</td><td><ChannelTag ch={p.channel} /> <span className="faint small">{p.account}</span></td><td><StatusBadge s={p.status} /></td></tr>)}</tbody></table>
        {!data.posts.length && <p className="muted small">No posts yet — choose this campaign when scheduling or on a rule.</p>}
      </>}
    </Drawer>
  );
}

// ------------------------------------------------------------------ publish queue
function Queue({ pid }: { pid: string }) {
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useApi<any>(`/social/publish-jobs?project_id=${pid}${status ? `&status=${status}` : ""}`, [pid, status], { poll: 15000 });
  const can = useCan();
  const { run } = useAction();
  const retry = (j: any) => {
    const unknown = j.error_code === "outcome_unknown";
    if (unknown && !confirm("The last attempt may have reached the platform. Check the account first. Retry only if the post is NOT live — continue?")) return;
    run(() => api(`/social/publish-jobs/${j.id}/retry`, { body: { confirm_not_posted: unknown } }), "Retry queued").then(reload);
  };
  return (
    <>
      <ProviderBanners providers={data && Object.entries(data.providers).map(([provider, st]) => ({ provider, status: st as string }))}
        only={[...new Set<string>((data?.items || []).map((j: any) => ({ facebook: "meta", instagram: "meta", linkedin: "linkedin", x: "x" } as any)[j.channel]).filter(Boolean))]} />
      <Panel title="Publish queue" actions={<Select value={status} onChange={setStatus} label="Status" placeholder="All statuses" options={["QUEUED", "SENDING", "PUBLISHED", "FAILED", "CANCELLED"].map((s) => ({ value: s, label: `${human(s.toLowerCase())}${data?.counts?.[s] ? ` (${data.counts[s]})` : ""}` }))} />} pad={false}>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !data?.items.length ? <Empty title="Nothing published yet">Publish jobs appear when a scheduled post comes due. Each has an idempotency key so a retry never double-posts.</Empty> : (
          <div className="table-wrap"><table className="t">
            <thead><tr><th>Post</th><th>Account</th><th>Scheduled</th><th>Status</th><th>Result</th><th className="r">Attempts</th><th /></tr></thead>
            <tbody>{data.items.map((j: any) => (
              <tr key={j.id}>
                <td><b>{j.title || "(untitled)"}</b><div className="faint small mono-url">{j.idempotency_key}</div></td>
                <td><ChannelTag ch={j.channel} /> <span className="small">{j.account}</span></td>
                <td className="small">{fmtDateTime(j.scheduled_at)}</td>
                <td><StatusBadge s={j.status} /></td>
                <td className="small" style={{ maxWidth: 340 }}>{j.external_url ? <a href={j.external_url} target="_blank" rel="noreferrer">View post</a> : j.external_post_id ? <span className="mono-url">{j.external_post_id}</span> : null}
                  {j.error && <div style={{ color: "var(--critical)" }}>{j.error_code === "not_connected" ? "NOT CONNECTED — " : ""}{j.error}</div>}
                  {j.published_at && <div className="faint">{ago(j.published_at)}</div>}</td>
                <td className="r num">{j.attempts}</td>
                <td className="r">{j.status === "FAILED" && can("publish") && <button className="btn sm" onClick={() => retry(j)}>Retry</button>}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Panel>
    </>
  );
}
