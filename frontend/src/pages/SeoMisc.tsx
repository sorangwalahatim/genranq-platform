import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, NotConnected, PageHead, Panel, Select } from "../components/ui";
import { api } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

export function Checklist() {
  const { data, error, loading } = useApi<any[]>("/checklist");
  const [cat, setCat] = useState("");
  const cats = Array.from(new Set((data || []).map((c) => c.category)));
  return (
    <>
      <PageHead title="Automated audit checklist" sub={`${data?.length ?? 0} checks, each mapped to a data source, severity and fix owner. Loaded from the Audit Checklist sheet of the product specification.`}>
        <Select value={cat} onChange={setCat} placeholder="All categories" label="Category" options={cats.map((c) => ({ value: c, label: c }))} />
      </PageHead>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : (
        <Panel pad={false}><div className="table-wrap"><table className="t"><thead><tr><th>Code</th><th>Category</th><th>Check</th><th>Data source</th><th>Automation</th><th>Severity</th><th>Fix owner</th></tr></thead>
          <tbody>{data!.filter((c) => !cat || c.category === cat).map((c) => (
            <tr key={c.id}><td className="faint">{c.code}</td><td>{c.category}</td><td>{c.check}<div className="faint small">{c.recommendation}</div></td><td className="small">{c.data_source}</td>
              <td>{c.auto === "Y" ? "Automated" : "Partly / AI-assisted"}</td><td><Badge kind={c.severity === "High" ? "high" : c.severity === "Low" ? "low" : "medium"}>{c.severity}</Badge></td><td>{c.default_owner}</td></tr>
          ))}</tbody></table></div></Panel>
      )}
    </>
  );
}

export function PageSpeed() {
  const { data: projects } = useApi<any[]>("/projects");
  const sites = useMemo(() => (projects || []).flatMap((p) => p.sites), [projects]);
  const [site, setSite] = useState("");
  const sid = site || sites[0]?.id;
  const { data, error, reload } = useApi<any>(sid ? `/sites/${sid}/pagespeed` : null, [sid]);
  const { run } = useAction();
  const can = useCan();
  return (
    <>
      <PageHead title="PageSpeed & Core Web Vitals" sub="Google PageSpeed Insights for mobile and desktop: LCP, CLS, INP and performance score history.">
        <Select value={sid || ""} onChange={setSite} label="Site" options={sites.map((s: any) => ({ value: s.id, label: s.domain }))} />
        {can("create") && <button className="btn primary" disabled={!data?.connected} onClick={() => run(() => api(`/sites/${sid}/pagespeed`, { body: {} }), "PageSpeed test queued").then(() => setTimeout(reload, 15000))}>Test now</button>}
      </PageHead>
      {data && !data.connected && <div style={{ marginBottom: 14 }}><NotConnected what="Google PageSpeed Insights" /></div>}
      <ErrorBox error={error} />
      {!sites.length ? <Panel><Empty title="Add a site first" /></Panel> : (
        <Panel pad={false}>
          {!data?.results?.length ? <Empty title="No PageSpeed results yet">Results also arrive automatically with each audit once PageSpeed Insights is connected.</Empty> : (
            <table className="t"><thead><tr><th>Tested</th><th>URL</th><th>Device</th><th className="r">Score</th><th className="r">LCP</th><th className="r">CLS</th><th className="r">INP</th></tr></thead>
              <tbody>{data.results.map((r: any) => (
                <tr key={r.id}><td className="nowrap">{fmtDateTime(r.checked_at)}</td><td className="mono-url">{r.url}</td><td>{r.device}</td><td className="r num">{r.score ?? "—"}</td>
                  <td className="r num" style={{ color: r.lcp > 2.5 ? "var(--critical)" : undefined }}>{r.lcp != null ? `${r.lcp}s` : "—"}</td>
                  <td className="r num" style={{ color: r.cls > 0.1 ? "var(--critical)" : undefined }}>{r.cls ?? "—"}</td>
                  <td className="r num" style={{ color: r.inp > 200 ? "var(--critical)" : undefined }}>{r.inp != null ? `${r.inp}ms` : "—"}</td></tr>
              ))}</tbody></table>
          )}
        </Panel>
      )}
    </>
  );
}

export function Competitors() {
  const { data: projects, loading } = useApi<any[]>("/projects");
  const { data: audits } = useApi<any[]>("/audits");
  if (loading && !projects) return <Loading />;
  return (
    <>
      <PageHead title="Competitor audits" sub="Audit competitor sites with the same checklist and compare scores and individual checks side by side. Add competitors on each project page." />
      {!projects?.length ? <Panel><Empty title="Create a project first" /></Panel> : (
        <div className="stack">{projects.map((p) => {
          const comps = (audits || []).filter((a) => a.project_id === p.id);
          return (
            <Panel key={p.id} title={p.name} actions={<Link to={`/projects/${p.id}`} className="btn sm">Manage competitors</Link>} pad={false}>
              {!comps.length ? <p className="panel-body muted">No audits in this project yet.</p> : (
                <table className="t"><thead><tr><th>Domain</th><th>Type</th><th className="r">Score</th><th>Status</th></tr></thead>
                  <tbody>{comps.slice(0, 12).map((a) => <tr key={a.id}><td><Link to={`/audits/${a.id}`}>{a.domain}</Link></td><td>{a.is_competitor ? "Competitor" : "Own site"}</td><td className="r num">{a.score ?? "—"}</td><td><Badge kind={a.status} /></td></tr>)}</tbody></table>
              )}
            </Panel>
          );
        })}</div>
      )}
    </>
  );
}
