import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, NotConnected, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan, useToast } from "../../lib/hooks";
import { CATEGORIES, fmtN, Initials, PROVIDER_LABEL, SEVS, StatusBadge, Switch, TEAM_LABEL, TEAMS, useJobWatch } from "./shared";

// ------------------------------------------------------------------ sources
const PROPERTY_HINT: Record<string, string> = {
  gsc: "sc-domain:example.com or https://www.example.com/", ga4: "properties/123456789", ahrefs: "example.com", semrush: "example.com", screaming_frog: "",
};
const UPLOAD_HELP: Record<string, string> = {
  gsc: "Performance export (CSV/XLSX) with Date, Query, Page, Device, Country, Clicks, Impressions, Position — any subset of dimensions works.",
  ga4: "Landing-page report export with Date, Landing page, Device category, Session default channel group, Sessions, Total users, Key events.",
  screaming_frog: "Internal → HTML export (CSV/XLSX). Each upload is a crawl snapshot; changes between snapshots go to the change log.",
  ahrefs: "Organic keywords or Referring domains export — for your domain or a competitor’s.",
  semrush: "Organic Positions or Referring domains export — for your domain or a competitor’s.",
};

export function SourcesTab({ pid, project, onImported }: { pid: string; project?: any; onImported: () => void }) {
  const { data, error, loading, reload } = useApi<any[]>(`/projects/${pid}/sources`);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  return (
    <>
      <p className="muted small" style={{ marginBottom: 12, maxWidth: "90ch" }}>
        Each source is bound to this project only. Use the API when the provider is connected for your organisation, or upload exports — imported data is monitored exactly like synced data.
      </p>
      <div className="mon-src">
        {(data || []).map((s) => <SourceCard key={s.provider} pid={pid} s={s} sites={project?.sites || []} onSaved={reload} onImported={() => { reload(); onImported(); }} />)}
      </div>
    </>
  );
}

function SourceCard({ pid, s, sites, onSaved, onImported }: { pid: string; s: any; sites: any[]; onSaved: () => void; onImported: () => void }) {
  const can = useCan();
  const toast = useToast();
  const { run, busy } = useAction();
  const watch = useJobWatch();
  const [f, setF] = useState({ property: s.property || "", mode: s.mode, enabled: s.configured ? s.enabled : true });
  useEffect(() => setF({ property: s.property || "", mode: s.mode, enabled: s.configured ? s.enabled : true }), [s.property, s.mode, s.enabled, s.configured]);
  const dirty = !s.configured || f.property !== (s.property || "") || f.mode !== s.mode || f.enabled !== s.enabled;
  const isSF = s.provider === "screaming_frog";
  const notConnected = !isSF && s.org_connection !== "CONNECTED";
  const [syncing, setSyncing] = useState(false);
  const save = () => run(() => api(`/projects/${pid}/sources/${s.provider}`, { method: "PUT", body: { property: f.property || null, mode: f.mode, enabled: f.enabled, config: {} } }), "Source saved").then(onSaved);
  const sync = async () => {
    const r = await run<any>(() => api(`/projects/${pid}/sources/${s.provider}/sync`, { method: "POST" }), "Sync queued");
    if (!r) return;
    setSyncing(true);
    watch(r.job_id, () => { setSyncing(false); onImported(); }, `${PROVIDER_LABEL[s.provider]} sync`);
  };
  const statusText = s.last_status === "not_connected" ? "Not connected" : s.last_status ? human(s.last_status) : "Never synced";
  return (
    <Panel title={PROVIDER_LABEL[s.provider]} actions={<Badge kind={s.org_connection === "CONNECTED" ? "CONNECTED" : isSF ? "" : "NOT_CONNECTED"}>{isSF ? "Import" : s.org_connection === "CONNECTED" ? "Connected" : "Not connected"}</Badge>}>
      {notConnected && (
        <NotConnected what={PROVIDER_LABEL[s.provider]}>
          isn’t connected for your organisation. <Link to={`/integrations/${s.provider}`}>Connect it</Link> for scheduled syncs, or upload exports below.
        </NotConnected>
      )}
      <dl className="kv" style={{ gridTemplateColumns: "110px 1fr" }}>
        <dt>Last sync</dt><dd>{s.last_sync_at ? <>{fmtDateTime(s.last_sync_at)} <span className="faint small">({ago(s.last_sync_at)})</span></> : "—"}</dd>
        <dt>Status</dt><dd>{s.last_status ? <StatusBadge s={s.last_status} /> : <span className="muted small">{statusText}</span>} {s.rows_last_sync ? <span className="faint small">{fmtN(s.rows_last_sync)} rows</span> : null}</dd>
        {s.last_error && <><dt>Error</dt><dd className="small" style={{ color: "var(--critical)" }}>{s.last_error}</dd></>}
      </dl>
      {can("manage_integrations") && (
        <div className="form" style={{ gap: 10 }}>
          {!isSF && <label className="field">Property<input className="input" value={f.property} placeholder={PROPERTY_HINT[s.provider]} onChange={(e) => setF({ ...f, property: e.target.value })} /></label>}
          <div className="row" style={{ justifyContent: "space-between" }}>
            {!isSF ? (
              <div className="seg" role="group" aria-label={`${PROVIDER_LABEL[s.provider]} mode`}>
                {["api", "import"].map((m) => <button key={m} type="button" className={f.mode === m ? "on" : ""} aria-pressed={f.mode === m} onClick={() => setF({ ...f, mode: m })}>{m === "api" ? "API" : "Import"}</button>)}
              </div>
            ) : <span className="small muted">Import only</span>}
            <label className="check small"><Switch checked={f.enabled} onChange={(v) => setF({ ...f, enabled: v })} label="Source enabled" /> Enabled</label>
          </div>
          <div className="row">
            <button className="btn sm" disabled={!dirty || busy} onClick={save}>{s.configured ? "Save" : "Set up"}</button>
            {can("edit") && s.configured && s.mode === "api" && <button className="btn sm" disabled={syncing || busy} onClick={sync}>{syncing ? "Syncing…" : "Sync now"}</button>}
          </div>
        </div>
      )}
      {can("edit") && <Uploader pid={pid} provider={s.provider} sites={sites} onDone={(res) => {
        toast(`Imported ${fmtN(res.rows ?? res.pages ?? 0)} ${res.pages != null && res.rows == null ? "pages" : "rows"}${res.monitor_job_id ? " — monitoring run queued" : ""}`);
        onImported();
        if (res.monitor_job_id) watch(res.monitor_job_id, onImported, "Monitoring run");
      }} />}
    </Panel>
  );
}

function Uploader({ pid, provider, sites, onDone }: { pid: string; provider: string; sites: any[]; onDone: (r: any) => void }) {
  const file = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [runMon, setRunMon] = useState(true);
  const [kind, setKind] = useState(provider === "ahrefs" || provider === "semrush" ? "keywords" : provider);
  const [domain, setDomain] = useState("");
  const [own, setOwn] = useState(true);
  const [dateCovered, setDateCovered] = useState("");
  const [site, setSite] = useState(sites[0]?.id || "");
  const { run, busy } = useAction();
  const isLink = provider === "ahrefs" || provider === "semrush";
  const id = `up-${provider}`;
  const submit = async () => {
    const fl = file.current?.files?.[0];
    if (!fl) return;
    const fd = new FormData();
    fd.append("file", fl);
    fd.append("run_monitor", String(runMon));
    if (isLink) {
      fd.append("domain", domain.trim()); fd.append("source", provider);
      if (kind === "keywords") fd.append("is_own", String(own)); else fd.append("target", own ? "own" : domain.trim());
    }
    if (provider === "gsc" && dateCovered) fd.append("date_covered", dateCovered);
    if (provider === "screaming_frog" && site) fd.append("site_id", site);
    const r = await run<any>(() => api(`/projects/${pid}/import/${kind}`, { form: fd }));
    if (r) { onDone(r); if (file.current) file.current.value = ""; setName(""); }
  };
  const ok = name && (!isLink || /^[\w.-]+\.[a-z]{2,}$/i.test(domain.trim())) && (provider !== "screaming_frog" || sites.length);
  return (
    <details style={{ borderTop: "1px solid var(--line-2)", paddingTop: 10 }}>
      <summary style={{ cursor: "pointer", fontWeight: 500, fontSize: 13.5 }}>Upload an export</summary>
      <div className="form" style={{ gap: 10, marginTop: 10 }}>
        <p className="small muted">{UPLOAD_HELP[provider]}</p>
        {isLink && (
          <>
            <div className="seg" role="group" aria-label="Export type">
              <button type="button" className={kind === "keywords" ? "on" : ""} aria-pressed={kind === "keywords"} onClick={() => setKind("keywords")}>Organic keywords</button>
              <button type="button" className={kind === "refdomains" ? "on" : ""} aria-pressed={kind === "refdomains"} onClick={() => setKind("refdomains")}>Referring domains</button>
            </div>
            <label className="field">Domain the export is for<input className="input" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="competitor.com" /></label>
            <label className="check small"><input type="checkbox" checked={own} onChange={(e) => setOwn(e.target.checked)} /> This is our own domain (untick for a competitor)</label>
          </>
        )}
        {provider === "gsc" && <label className="field">Date covered<span className="hint">Only for exports without a Date column.</span><input className="input" type="date" value={dateCovered} onChange={(e) => setDateCovered(e.target.value)} /></label>}
        {provider === "screaming_frog" && (sites.length ? (
          sites.length > 1 && <label className="field">Site<select className="input" value={site} onChange={(e) => setSite(e.target.value)}>{sites.map((x) => <option key={x.id} value={x.id}>{x.domain}</option>)}</select></label>
        ) : <div className="notice warn">Add a site to the project first (<Link to={`/projects/${pid}`}>project settings</Link>).</div>)}
        <label className="field" htmlFor={id}>File<input id={id} ref={file} className="input" style={{ paddingTop: 5 }} type="file" accept=".csv,.xlsx,.tsv,.txt" onChange={(e) => setName(e.target.files?.[0]?.name || "")} /></label>
        <label className="check small"><Switch checked={runMon} onChange={setRunMon} label="Run monitoring after import" /> Run monitoring after import</label>
        <div><button className="btn sm primary" disabled={!ok || busy} onClick={submit}>{busy ? "Importing…" : "Import"}</button></div>
      </div>
    </details>
  );
}

// ------------------------------------------------------------------ team & routing
const THRESHOLD_LABEL: Record<string, [string, string]> = {
  traffic_drop_pct: ["Traffic drop alert", "%"], traffic_drop_critical_pct: ["Traffic drop is critical at", "%"], min_clicks: ["Min weekly clicks to alert", "clicks"],
  page_drop_pct: ["Page clicks drop", "%"], page_min_clicks: ["Min page clicks", "clicks"], device_drop_pct: ["Device clicks drop", "%"], country_drop_pct: ["Country clicks drop", "%"],
  ctr_drop_pct: ["CTR drop at stable position", "%"], rank_drop_positions: ["Keyword falls by", "positions"], rank_up_positions: ["Keyword rises by", "positions"],
  rank_min_impr: ["Min impressions for rank moves", "impr."], new_query_min_impr: ["New query min impressions", "impr."], lost_query_min_clicks: ["Lost query min clicks", "clicks"],
  striking_min_impr: ["Striking-distance min impressions", "impr."], low_ctr_min_impr: ["Low-CTR min impressions", "impr."], tracking_zero_days: ["Tracking stopped after", "days"],
  sessions_drop_pct: ["Sessions drop", "%"], conversions_drop_pct: ["Conversions drop", "%"], refdomain_loss_min: ["Lost referring domains (min)", "domains"],
  refdomain_loss_pct: ["Lost referring domains", "%"], verify_window_days: ["Verification window", "days"], max_opportunity_tasks: ["Max opportunity tasks per run", "tasks"],
  verify_delay_sec: ["Wait before re-fetching a fixed URL", "sec"],
};
const ISSUE_KINDS = ["traffic_drop", "traffic_spike", "device_drop", "country_drop", "page_traffic_drop", "ctr_drop", "keyword_down", "keyword_up", "new_query", "lost_query",
  "opp_striking_distance", "opp_low_ctr", "sessions_drop", "device_sessions_drop", "landing_sessions_drop", "conversions_drop", "conversion_tracking", "tracking_stopped",
  "noindex_important", "http_404", "http_5xx", "redirect_chain", "canonical_changed", "missing_title", "missing_h1", "page_removed", "change_negative_impact",
  "tracked_keyword_down", "tracked_keyword_up", "refdomain_loss", "backlink_lost", "blog_underperforming"];

export function TeamTab({ pid }: { pid: string }) {
  return (
    <div className="stack">
      <Members pid={pid} />
      <Config pid={pid} />
    </div>
  );
}

function Members({ pid }: { pid: string }) {
  const { data, error, loading, reload } = useApi<any[]>(`/projects/${pid}/members`);
  const { data: team } = useApi<any>("/team");
  const can = useCan();
  const { run, busy } = useAction();
  const [draft, setDraft] = useState<any[] | null>(null);
  useEffect(() => { if (data) setDraft(data.map((m) => ({ user_id: m.user_id, team: m.team, is_lead: m.is_lead }))); }, [data]);
  const users = useMemo(() => (team?.users || []).filter((u: any) => u.is_active && u.role !== "client"), [team]);
  const name = (id: string) => { const u = users.find((x: any) => x.id === id) || data?.find((m) => m.user_id === id); return u?.name || u?.email || "Team member"; };
  const dirty = JSON.stringify(draft) !== JSON.stringify(data?.map((m) => ({ user_id: m.user_id, team: m.team, is_lead: m.is_lead })));
  const editable = can("manage_users");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  const d = draft || [];
  return (
    <Panel title="Project team" actions={editable && <><button className="btn sm" disabled={!dirty || busy} onClick={() => reload()}>Discard</button>
      <button className="btn sm primary" disabled={!dirty || busy} onClick={() => run(() => api(`/projects/${pid}/members`, { method: "PUT", body: d }), "Team saved").then(reload)}>Save team</button></>}>
      <p className="small muted" style={{ marginBottom: 12 }}>Issues and tasks are routed to these people by team. Without a team member, work goes to the project manager, then the organisation’s admins.</p>
      <div className="mon-team">
        {TEAMS.map((t) => {
          const members = d.filter((m) => m.team === t);
          const avail = users.filter((u: any) => !members.find((m) => m.user_id === u.id));
          return (
            <div className="box" key={t}>
              <h3>{TEAM_LABEL[t]}<span className="faint small num">{members.length}</span></h3>
              {!members.length && <p className="faint small">Nobody yet</p>}
              {members.map((m) => (
                <div key={m.user_id} className="mon-person">
                  <Initials name={name(m.user_id)} /><span>{name(m.user_id)}</span>
                  {m.is_lead && <Badge kind="pass">Lead</Badge>}
                  <span className="spacer" />
                  {editable && <>
                    <button className="btn ghost sm" aria-pressed={m.is_lead} title={m.is_lead ? "Remove lead" : "Make team lead"} aria-label={`${m.is_lead ? "Remove lead" : "Make lead"}: ${name(m.user_id)}`}
                      onClick={() => setDraft(d.map((x) => x === m ? { ...x, is_lead: !x.is_lead } : x))}>{m.is_lead ? "★" : "☆"}</button>
                    <button className="btn ghost sm" aria-label={`Remove ${name(m.user_id)} from ${TEAM_LABEL[t]}`} onClick={() => setDraft(d.filter((x) => x !== m))}>✕</button>
                  </>}
                </div>
              ))}
              {editable && avail.length > 0 && (
                <select className="input" style={{ marginTop: 6, height: 30 }} value="" aria-label={`Add to ${TEAM_LABEL[t]}`}
                  onChange={(e) => e.target.value && setDraft([...d, { user_id: e.target.value, team: t, is_lead: false }])}>
                  <option value="">+ Add person…</option>
                  {avail.map((u: any) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
                </select>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function Config({ pid }: { pid: string }) {
  const { data: c, error, loading, reload } = useApi<any>(`/projects/${pid}/monitor/config`);
  const can = useCan();
  const editable = can("manage_integrations");
  const { run, busy } = useAction();
  const [f, setF] = useState<any>(null);
  const init = (x: any) => ({
    enabled: x.enabled, thresholds: { ...x.thresholds }, routing: { ...x.routing }, severity_overrides: { ...x.severity_overrides },
    slack: x.channels?.slack_webhook || "", teams: x.channels?.teams_webhook || "", important: (x.important_urls || []).join("\n"),
  });
  useEffect(() => { if (c) setF(init(c)); }, [c]);
  const [newKind, setNewKind] = useState("");
  if (loading && !c) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!c || !f) return null;
  const urls = f.important.split("\n").map((x: string) => x.trim()).filter(Boolean);
  const badUrl = urls.find((u: string) => !/^https?:\/\/\S+$/.test(u));
  const hookOk = (v: string, host: RegExp) => !v || v === "•••• configured" || host.test(v);
  const slackOk = hookOk(f.slack, /^https:\/\/hooks\.slack\.com\//);
  const teamsOk = hookOk(f.teams, /^https:\/\/[\w.-]*webhook\.office\.com\//);
  const thrBad = Object.values(f.thresholds).some((v: any) => v === "" || Number.isNaN(Number(v)) || Number(v) < 0);
  const save = () => {
    const thresholds = Object.fromEntries(Object.entries(f.thresholds).map(([k, v]) => [k, Number(v)]));
    return run(() => api(`/projects/${pid}/monitor/config`, { method: "PUT", body: {
      enabled: f.enabled, thresholds, routing: f.routing, severity_overrides: f.severity_overrides,
      channels: { slack_webhook: f.slack || null, teams_webhook: f.teams || null }, important_urls: urls,
    } }), "Monitoring settings saved").then(reload);
  };
  const changed = JSON.stringify(f) !== JSON.stringify(init(c));
  const dis = !editable;
  return (
    <>
      <Panel title="Monitoring" actions={<label className="check small"><Switch checked={f.enabled} disabled={dis} onChange={(v) => setF({ ...f, enabled: v })} label="Monitoring enabled" /> {f.enabled ? "Enabled" : "Paused"}</label>}>
        <div className="grid g2">
          <div>
            <h3 style={{ marginBottom: 8 }}>Routing</h3>
            <p className="small muted" style={{ marginBottom: 10 }}>Which team receives each category of issue.</p>
            <div className="form" style={{ gap: 8 }}>
              {CATEGORIES.map((cat) => (
                <label key={cat} className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
                  <span className="small">{TEAM_LABEL[cat]} issues →</span>
                  <select className="input" style={{ width: 180 }} disabled={dis} value={f.routing[cat] || cat} onChange={(e) => setF({ ...f, routing: { ...f.routing, [cat]: e.target.value } })}>
                    {TEAMS.map((t) => <option key={t} value={t}>{TEAM_LABEL[t]} team</option>)}
                  </select>
                </label>
              ))}
            </div>
          </div>
          <div>
            <h3 style={{ marginBottom: 8 }}>Notifications</h3>
            <p className="small muted" style={{ marginBottom: 10 }}>Critical and high issues and the run briefing are posted to these incoming webhooks, as well as in-app notifications.</p>
            <div className="form" style={{ gap: 10 }}>
              <label className="field">Slack webhook<input className="input" disabled={dis} value={f.slack} placeholder="https://hooks.slack.com/services/…" onChange={(e) => setF({ ...f, slack: e.target.value })} onFocus={() => f.slack === "•••• configured" && setF({ ...f, slack: "" })} />
                {!slackOk && <span className="hint" style={{ color: "var(--critical)" }}>Must be a hooks.slack.com URL</span>}</label>
              <label className="field">Microsoft Teams webhook<input className="input" disabled={dis} value={f.teams} placeholder="https://….webhook.office.com/…" onChange={(e) => setF({ ...f, teams: e.target.value })} onFocus={() => f.teams === "•••• configured" && setF({ ...f, teams: "" })} />
                {!teamsOk && <span className="hint" style={{ color: "var(--critical)" }}>Must be a webhook.office.com URL</span>}</label>
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="Thresholds" actions={editable && <button className="btn sm" onClick={() => setF({ ...f, thresholds: { ...c.defaults } })}>Reset all to defaults</button>}>
        <div className="mon-thr">
          {Object.keys(c.defaults).map((k) => {
            const [label, unit] = THRESHOLD_LABEL[k] || [human(k), ""];
            const v = f.thresholds[k];
            const isDef = Number(v) === c.defaults[k];
            return (
              <label key={k} className={isDef ? "" : "changed"}>
                <span>{label}</span>
                <span className="row">
                  <input className="input" style={{ width: 100 }} type="number" min={0} step="any" disabled={dis} value={v} onChange={(e) => setF({ ...f, thresholds: { ...f.thresholds, [k]: e.target.value } })} />
                  <span className="faint small">{unit}</span>
                  {!isDef && <button type="button" className="btn ghost sm" disabled={dis} onClick={() => setF({ ...f, thresholds: { ...f.thresholds, [k]: c.defaults[k] } })} title={`Default ${c.defaults[k]}`}>Default {c.defaults[k]}</button>}
                </span>
              </label>
            );
          })}
        </div>
      </Panel>

      <div className="grid g2">
        <Panel title="Severity overrides">
          <p className="small muted" style={{ marginBottom: 10 }}>Change the severity a type of issue is raised with.</p>
          {Object.keys(f.severity_overrides).length === 0 && <p className="faint small">No overrides — built-in severities apply.</p>}
          {Object.entries(f.severity_overrides).map(([k, v]: any) => (
            <div key={k} className="row" style={{ justifyContent: "space-between", padding: "4px 0", flexWrap: "nowrap" }}>
              <span className="small">{human(k)}</span>
              <span className="row" style={{ flexWrap: "nowrap" }}>
                <select className="input" style={{ width: 120 }} disabled={dis} value={v} aria-label={`Severity for ${human(k)}`} onChange={(e) => setF({ ...f, severity_overrides: { ...f.severity_overrides, [k]: e.target.value } })}>
                  {SEVS.map((s) => <option key={s} value={s}>{human(s)}</option>)}
                </select>
                {editable && <button className="btn ghost sm" aria-label={`Remove override for ${human(k)}`} onClick={() => { const o = { ...f.severity_overrides }; delete o[k]; setF({ ...f, severity_overrides: o }); }}>✕</button>}
              </span>
            </div>
          ))}
          {editable && (
            <div className="row" style={{ marginTop: 8 }}>
              <select className="input" style={{ flex: 1 }} value={newKind} onChange={(e) => setNewKind(e.target.value)} aria-label="Issue type to override">
                <option value="">Choose an issue type…</option>
                {ISSUE_KINDS.filter((k) => !f.severity_overrides[k]).map((k) => <option key={k} value={k}>{human(k)}</option>)}
              </select>
              <button className="btn sm" disabled={!newKind} onClick={() => { setF({ ...f, severity_overrides: { ...f.severity_overrides, [newKind]: "high" } }); setNewKind(""); }}>Add</button>
            </div>
          )}
        </Panel>
        <Panel title="Important URLs">
          <label className="field">Revenue and priority pages<span className="hint">One absolute URL per line. These pages are always treated as important: a 404, noindex, canonical change or missing title on them raises an issue (with higher severity).</span>
            <textarea className="input" rows={7} disabled={dis} value={f.important} onChange={(e) => setF({ ...f, important: e.target.value })} placeholder="https://example.com/pricing" /></label>
          {badUrl && <p className="small" style={{ color: "var(--critical)", marginTop: 6 }}>Not an absolute URL: {badUrl}</p>}
        </Panel>
      </div>

      {editable ? (
        <div className="row" style={{ justifyContent: "flex-end", position: "sticky", bottom: 0, padding: "10px 0", background: "var(--paper)" }}>
          {changed && <span className="small muted">Unsaved changes</span>}
          <button className="btn" disabled={!changed || busy} onClick={() => setF(init(c))}>Discard</button>
          <button className="btn primary" disabled={!changed || busy || !!badUrl || !slackOk || !teamsOk || thrBad} onClick={save}>Save monitoring settings</button>
        </div>
      ) : <Empty title="Read only">Ask an admin with the integrations permission to change monitoring settings.</Empty>}
    </>
  );
}
