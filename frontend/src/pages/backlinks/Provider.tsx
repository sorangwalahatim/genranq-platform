import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Empty, ErrorBox, Loading, NotConnected, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDate } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { NoProjects, Tabs, UploadButton, useProject } from "./shared";

const NAME: Record<string, string> = { ahrefs: "Ahrefs", semrush: "Semrush" };

export default function Provider({ provider }: { provider: "ahrefs" | "semrush" }) {
  const { pid, projects, picker } = useProject();
  const { data: s, error, loading, reload } = useApi<any>(pid ? `/projects/${pid}/providers/${provider}` : null, [pid, provider]);
  const [tab, setTab] = useState("overview");
  const [domain, setDomain] = useState("");
  const [compDomain, setCompDomain] = useState("");
  const { run, busy } = useAction();
  const can = useCan();
  useEffect(() => { setDomain(s?.source?.domain || ""); }, [s?.source?.domain]);
  const name = NAME[provider];
  if (projects && !projects.length) return <><PageHead title={`${name} integration`} /><NoProjects /></>;
  const sync = async () => {
    const r = await run(() => api(`/projects/${pid}/providers/${provider}/sync`, { method: "POST" }), `${name} sync queued`);
    if (r) setTimeout(reload, 5000);
  };
  const imp = (kind: string, label: string, fields: Record<string, string> = {}) =>
    can("edit") ? <UploadButton path={`/projects/${pid}/providers/${provider}/import/${kind}`} fields={fields} label={label} ok="Imported" onDone={reload} disabled={!pid} /> : null;
  const tabs: [string, string][] = [["overview", "Overview"], ["backlinks", `Backlinks${s ? ` (${s.backlinks.length})` : ""}`], ["refdomains", "Referring domains"],
    ["keywords", provider === "semrush" ? "Positions" : "Organic keywords"]];
  if (provider === "semrush") tabs.push(["toxic", "Toxic scores"]);
  return (
    <>
      <PageHead title={`${name} integration`} sub={provider === "ahrefs" ? "Domain rating, referring domains, backlinks and organic keywords per project — from the Ahrefs API v3 when connected, or from Ahrefs CSV exports."
        : "Organic positions, backlinks and Backlink Audit toxic scores per project — from the Semrush API when connected, or from Semrush CSV exports."}>
        {picker}
        {s?.connected && can("edit") && <button className="btn primary" disabled={busy || !s.source?.domain} onClick={sync}>Sync now</button>}
      </PageHead>
      <ErrorBox error={error} />
      {loading && !s ? <Loading /> : s && (<div className="stack">
        {!s.connected ? <NotConnected what={name}>is <b>NOT CONNECTED</b>. <Link to={`/integrations/${provider}`}>Connect {name}</Link> to sync automatically — meanwhile import CSV exports below; everything on this page works from imports.</NotConnected>
          : <div className="notice"><Badge kind="CONNECTED">Connected</Badge><div>{s.source?.last_sync_at ? <>Last sync {ago(s.source.last_sync_at)} · {s.source.rows_last_sync} rows · <Badge kind={s.source.last_status === "ok" ? "pass" : "fail"}>{s.source.last_status}</Badge> {s.source.last_error}</> : "Not synced yet for this project."}</div></div>}
        <Panel title="Project settings">
          <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api(`/projects/${pid}/providers/${provider}/config`, { method: "PUT", body: { domain } }), "Saved").then(reload); }}>
            <label className="row small">Project domain <input className="input" style={{ width: 260 }} value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="example.com" /></label>
            {can("edit") && <button className="btn" disabled={!domain}>Save</button>}
            <span className="faint small">Used for API syncs and as the default domain for imports.</span>
          </form>
          {s.source?.last_status === "ok" && !s.connected && <p className="small muted" style={{ marginTop: 8 }}>Last import {ago(s.source.last_sync_at)} ({s.source.rows_last_sync} rows).</p>}
        </Panel>
        <Tabs value={tab} onChange={setTab} tabs={tabs} />
        {tab === "overview" && <Overview s={s} />}
        {tab === "backlinks" && <Backlinks pid={pid} s={s} imp={imp("backlinks", "Import backlinks export")} onChange={reload} />}
        {tab === "refdomains" && <Panel pad={false} title={`Referring domains${s.refdomains_snapshot ? ` · ${fmtDate(s.refdomains_snapshot)}` : ""}`} actions={<>
          {imp("refdomains", "Import (your site)", { is_own: "true", domain: s.source?.domain || "" })}
          <input className="input" style={{ width: 170 }} placeholder="competitor.com" value={compDomain} onChange={(e) => setCompDomain(e.target.value)} aria-label="Competitor domain" />
          {compDomain && imp("refdomains", "Import competitor's", { is_own: "false", domain: compDomain, target: compDomain })}</>}>
          {s.competitor_refdomains.length > 0 && <div className="bl-toolbar small">Competitors imported: {s.competitor_refdomains.map((c: any) => <span key={c.domain} className="bl-chip">{c.domain} · {c.refdomains}</span>)} — see the backlink gap in <Link to="/keyword-research">Keyword research</Link>.</div>}
          {!s.refdomains.length ? <Empty title="No referring domains">Export “Referring domains” from {name} (CSV) and import it.</Empty> :
            <table className="t"><thead><tr><th>Domain</th><th className="r">{provider === "ahrefs" ? "DR" : "Authority"}</th></tr></thead>
              <tbody>{s.refdomains.map((r: any) => <tr key={r.domain}><td>{r.domain}</td><td className="r num">{r.domain_rating ?? "—"}</td></tr>)}</tbody></table>}
        </Panel>}
        {tab === "keywords" && <Panel pad={false} title={provider === "semrush" ? "Organic positions" : "Organic keywords"} actions={<>
          {imp("keywords", "Import (your domain)", { is_own: "true", domain: s.source?.domain || "" })}
          <input className="input" style={{ width: 170 }} placeholder="competitor.com" value={compDomain} onChange={(e) => setCompDomain(e.target.value)} aria-label="Competitor domain" />
          {compDomain && imp("keywords", "Import competitor's", { is_own: "false", domain: compDomain })}</>}>
          <div className="bl-toolbar small">{s.keyword_sets.length ? s.keyword_sets.map((k: any) => <span key={k.domain} className="bl-chip">{k.domain}{k.is_own ? " (you)" : ""} · {k.keywords} kw · avg {k.avg_position ?? "—"}</span>) : "No keyword sets yet."}
            <span className="spacer" /><Link to="/keyword-research">Open keyword research →</Link></div>
          {!s.own_keywords.length ? <Empty title="No keywords for your domain">Import the “Organic keywords” / “Positions” export (Keyword, Position, Volume, KD, URL). A KD column unlocks keyword difficulty.</Empty> :
            <table className="t"><thead><tr><th>Keyword</th><th className="r">Position</th><th className="r">Volume</th><th className="r">KD</th><th>URL</th></tr></thead>
              <tbody>{s.own_keywords.map((k: any) => <tr key={k.keyword}><td>{k.keyword}</td><td className="r num">{k.position ?? "—"}</td><td className="r num">{k.volume ?? "—"}</td><td className="r num">{k.difficulty ?? "—"}</td><td className="mono-url">{k.url || "—"}</td></tr>)}</tbody></table>}
        </Panel>}
        {tab === "toxic" && <Panel pad={false} title="Backlink Audit toxic scores" actions={imp("toxic", "Import Backlink Audit CSV")}>
          <div className="bl-toolbar small muted">{s.toxic_note} Scores are advisory — disavowing needs approval on the <Link to="/backlinks">Backlinks → Toxic &amp; disavow</Link> tab.</div>
          {!s.toxic.length ? <Empty title="No toxic scores imported" /> :
            <table className="t"><thead><tr><th>Source</th><th className="r">Score</th><th>Markers</th><th>In CRM</th></tr></thead>
              <tbody>{s.toxic.map((t: any) => <tr key={t.id}><td><b>{t.source_domain}</b><div className="mono-url">{t.source_url}</div></td><td className="r num" style={{ color: t.score >= 60 ? "var(--critical)" : undefined }}>{t.score}</td><td className="small muted">{t.markers || "—"}</td><td>{t.backlink_id ? "yes" : "no"}</td></tr>)}</tbody></table>}
        </Panel>}
      </div>)}
    </>
  );
}

function Overview({ s }: { s: any }) {
  const last = s.metrics[s.metrics.length - 1];
  return (<div className="stack">
    <div className="grid g4">
      <div className="panel kpi"><div className="v">{last?.domain_rating ?? "—"}</div><div className="l">{s.provider === "ahrefs" ? "Domain rating" : "Authority"}</div></div>
      <div className="panel kpi"><div className="v">{last?.refdomains ?? (s.refdomains.length || "—")}</div><div className="l">Referring domains</div></div>
      <div className="panel kpi"><div className="v">{last?.backlinks ?? (s.backlinks.length || "—")}</div><div className="l">Backlinks</div></div>
      <div className="panel kpi"><div className="v">{s.own_keywords.length || "—"}</div><div className="l">Own keywords imported</div></div>
    </div>
    <Panel title="Domain metrics over time">
      {s.metrics.length > 1 ? <div style={{ height: 220 }}><ResponsiveContainer><LineChart data={s.metrics.map((m: any) => ({ ...m, date: fmtDate(m.date) }))}>
        <CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="date" fontSize={12} tickLine={false} axisLine={false} /><YAxis width={40} fontSize={12} tickLine={false} axisLine={false} /><Tooltip />
        <Line dataKey="refdomains" name="Referring domains" stroke="var(--accent)" strokeWidth={2} dot={false} /><Line dataKey="domain_rating" name="DR" stroke="var(--pass)" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer></div>
        : <p className="muted">{s.metrics.length === 1 ? "One snapshot so far — the trend appears after the next sync or import." : "No domain metrics yet. Sync via the API or import a referring-domains export."}</p>}
    </Panel>
  </div>);
}

function Backlinks({ pid, s, imp, onChange }: any) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const { run, busy } = useAction();
  const can = useCan();
  const add = async () => {
    const r: any = await run(() => api(`/projects/${pid}/ext-backlinks/to-crm`, { body: { ids: [...sel] } }));
    if (r) { setSel(new Set()); onChange(); alert(`${r.added} added to the Backlink CRM${r.skipped ? `, ${r.skipped} skipped (already tracked or no target)` : ""}.`); }
  };
  return (<Panel pad={false} title="Backlinks found by the provider" actions={<>{imp}{can("create") && <button className="btn primary" disabled={!sel.size || busy} onClick={add}>Add {sel.size || ""} to CRM</button>}</>}>
    {!s.backlinks.length ? <Empty title="No backlinks imported">Export “Backlinks” from {NAME[s.provider]} (CSV, UTF-8 or UTF-16) and import it, or sync via the API.</Empty> : (
      <div className="table-wrap"><table className="t"><thead><tr><th style={{ width: 28 }}></th><th>Referring page</th><th>Target · anchor</th><th>Type</th><th className="r">{s.provider === "ahrefs" ? "DR" : "AS"}</th><th>First seen</th><th>Last seen</th></tr></thead>
        <tbody>{s.backlinks.map((b: any) => <tr key={b.id}>
          <td>{b.in_crm ? <Badge kind="pass">CRM</Badge> : <input type="checkbox" aria-label={`Select ${b.source_domain}`} checked={sel.has(b.id)} onChange={() => setSel((x) => { const n = new Set(x); n.has(b.id) ? n.delete(b.id) : n.add(b.id); return n; })} />}</td>
          <td><b>{b.source_domain}</b><div className="mono-url" style={{ maxWidth: 300 }}>{b.source_url}</div></td>
          <td><div className="mono-url" style={{ maxWidth: 220 }}>{b.target_url || "—"}</div><div className="small muted">“{b.anchor || "—"}”</div></td>
          <td>{b.rel ? <Badge>{b.rel}</Badge> : "—"}</td><td className="r num">{b.authority ?? "—"}</td><td className="small">{fmtDate(b.first_seen)}</td><td className="small">{fmtDate(b.last_seen)}</td></tr>)}</tbody></table></div>)}
  </Panel>);
}
