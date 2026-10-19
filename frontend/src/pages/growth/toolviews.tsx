import { Badge } from "../../components/ui";
import { fmtDate } from "../../lib/format";

export const TOOL_META: Record<string, { name: string; blurb: string; input: "url" | "domain" | "keyword" | "monitor" }> = {
  "domain-age": { name: "Domain Age Checker", blurb: "Registration date, expiry and registrar from the public RDAP registry.", input: "domain" },
  "broken-links": { name: "Broken Link Checker", blurb: "Crawl a site and find internal and external links that return errors.", input: "url" },
  "link-analyzer": { name: "Link Analyzer", blurb: "Internal vs external links, rel attributes, anchors and broken links on one page.", input: "url" },
  dofollow: { name: "Dofollow / Nofollow Checker", blurb: "Every link on a page with its rel attribute (nofollow, sponsored, ugc).", input: "url" },
  "website-finder": { name: "Website Finder", blurb: "Find guest-post opportunities: marketplace sites plus ready-made search footprints.", input: "keyword" },
  "backlink-monitor": { name: "Backlink Monitor", blurb: "Track that a backlink stays live and dofollow — checked daily.", input: "monitor" },
};

const st = (s: any) => (typeof s === "number" ? (s >= 400 ? <Badge kind="fail">{s}</Badge> : <Badge kind="pass">{s}</Badge>) : <Badge kind="fail">{String(s)}</Badge>);

export function ToolResult({ tool, r }: { tool: string; r: any }) {
  if (!r) return null;
  if (tool === "domain-age") return r.found === false ? <p>{r.message}</p> : (
    <dl className="kv"><dt>Domain</dt><dd>{r.domain}</dd><dt>Age</dt><dd><b>{r.age_years ?? "—"} years</b> ({r.age_days ?? "—"} days)</dd>
      <dt>Registered</dt><dd>{fmtDate(r.registered)}</dd><dt>Expires</dt><dd>{fmtDate(r.expires)}</dd><dt>Last changed</dt><dd>{fmtDate(r.updated)}</dd>
      <dt>Registrar</dt><dd>{r.registrar || "—"}</dd><dt>Status</dt><dd className="small">{(r.status || []).join(", ") || "—"}</dd>
      <dt>Nameservers</dt><dd className="small">{(r.nameservers || []).join(", ") || "—"}</dd></dl>);
  if (tool === "broken-links") return (<>
    <p><b>{r.broken.length}</b> broken links ({r.broken_internal} internal, {r.broken_external} external) across {r.pages_crawled} pages · {r.links_checked} of {r.links_found} links checked{r.unchecked ? ` (${r.unchecked} not checked — limit)` : ""}.</p>
    {r.broken.length > 0 && <div className="table-wrap"><table className="t"><thead><tr><th>Broken link</th><th>Status</th><th>Anchor</th><th>Found on</th></tr></thead>
      <tbody>{r.broken.map((b: any) => <tr key={b.href}><td className="mono-url">{b.href}{!b.internal && <Badge kind="low">external</Badge>}</td><td>{st(b.status)}</td><td className="small">{b.anchor || "—"}</td><td className="small mono-url">{b.found_on.slice(0, 3).join(", ")}</td></tr>)}</tbody></table></div>}</>);
  if (tool === "link-analyzer") return (<>
    <div className="grid g4" style={{ marginBottom: 12 }}>{[["Links", r.total], ["Internal", r.internal], ["External", r.external], ["Nofollow", r.nofollow]].map(([l, v]) => <div key={l as string} className="panel kpi"><div className="v">{v as any}</div><div className="l">{l}</div></div>)}</div>
    <p className="small">rel distribution: {Object.entries(r.rel_distribution).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>
    {r.broken.length > 0 && <p className="small" style={{ color: "var(--critical)" }}>Broken: {r.broken.map((b: any) => `${b.href} (${b.status})`).join(", ")}</p>}
    <p className="small"><b>Top anchors:</b> {r.top_anchors.slice(0, 10).map(([a, n]: any) => `“${a}” ×${n}`).join(", ")}</p>
    <LinkTable links={r.links} showStatus /></>);
  if (tool === "dofollow") return (<>
    <p><b>{r.dofollow}</b> dofollow · <b>{r.nofollow}</b> nofollow ({r.sponsored} sponsored, {r.ugc} ugc) of {r.total} links.</p><LinkTable links={r.links} /></>);
  if (tool === "website-finder") return (<>
    <p className="small muted">{r.serp?.message}</p>
    <h3 className="small">Search footprints</h3>
    <ul className="small">{r.queries.map((q: any) => <li key={q.query}><code>{q.query}</code> — <a href={q.google} target="_blank" rel="noreferrer">Google</a> · <a href={q.bing} target="_blank" rel="noreferrer">Bing</a></li>)}</ul>
    {r.marketplace && <><h3 className="small">Marketplace sites matching “{r.keyword}”</h3>{r.marketplace.length ? <ul className="small">{r.marketplace.map((l: any) => <li key={l.id}><b>{l.domain}</b> · {l.category || "—"} · DR {l.metrics?.dr?.value ?? "—"} · guest post {l.guest_post_price ?? "—"}</li>)}</ul> : <p className="muted small">No listed sites match.</p>}</>}</>);
  return <pre className="gq-code">{JSON.stringify(r, null, 2)}</pre>;
}

function LinkTable({ links, showStatus }: { links: any[]; showStatus?: boolean }) {
  return <div className="table-wrap"><table className="t"><thead><tr><th>Link</th><th>Anchor</th><th>rel</th><th>Type</th>{showStatus && <th>Status</th>}</tr></thead>
    <tbody>{links.map((l, i) => <tr key={i}><td className="mono-url">{l.href}</td><td className="small">{l.anchor || "—"}</td><td className="small">{l.rel || "—"}</td>
      <td><Badge kind={l.follow === "dofollow" ? "pass" : "low"}>{l.follow}</Badge> {l.internal ? <span className="faint small">internal</span> : <span className="faint small">external</span>}</td>
      {showStatus && <td>{l.status == null ? <span className="faint small">—</span> : st(l.status)}</td>}</tr>)}</tbody></table></div>;
}
