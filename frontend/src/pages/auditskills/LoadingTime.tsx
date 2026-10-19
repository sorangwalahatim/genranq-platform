import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { useApi } from "../../lib/hooks";
import { NoProjects, useProject } from "./shared";

type Row = { url: string; device: string; lcp: number | null; fcp: number | null; tti: number | null; score: number | null; run_at: string; run_id: string };

export default function LoadingTime() {
  const { pid, projects, picker } = useProject();
  const runs = useApi<any[]>(pid ? `/auditskills/runs?project_id=${pid}&skill_key=pagespeed&limit=5` : null, [pid]);
  const latest = (runs.data || []).filter((r) => r.status === "completed").slice(0, 3);
  const d1 = useApi<any>(latest[0] ? `/auditskills/runs/${latest[0].id}` : null, [latest[0]?.id]);
  const d2 = useApi<any>(latest[1] ? `/auditskills/runs/${latest[1].id}` : null, [latest[1]?.id]);
  const d3 = useApi<any>(latest[2] ? `/auditskills/runs/${latest[2].id}` : null, [latest[2]?.id]);

  const rows: Row[] = useMemo(() => {
    const map = new Map<string, Row>();
    for (const d of [d1.data, d2.data, d3.data]) {
      if (!d?.findings) continue;
      for (const f of d.findings) {
        if (!f.data || f.data.lcp === undefined) continue;
        const key = `${f.url}::${f.data.device}`;
        const cur = map.get(key);
        if (!cur || new Date(d.created_at) > new Date(cur.run_at)) {
          map.set(key, { url: f.url, device: f.data.device, lcp: f.data.lcp, fcp: f.data.fcp, tti: f.data.tti,
                          score: f.data.score, run_at: d.created_at, run_id: d.id });
        }
      }
    }
    return [...map.values()].sort((a, b) => (b.lcp ?? -1) - (a.lcp ?? -1)); // slowest first
  }, [d1.data, d2.data, d3.data]);

  const slow = rows.filter((r) => r.lcp != null && r.lcp > 3).length;
  if (projects && !projects.length) return <><PageHead title="Page loading times" /><NoProjects /></>;
  return (
    <>
      <PageHead title="Page loading times" sub="Every tested page, slowest first. LCP over 3 seconds hurts Google rankings — fix those first.">
        {picker}
        <Link className="btn" to="/audit-skills">Audit skills</Link>
      </PageHead>
      {runs.loading ? <Loading /> : runs.error ? <ErrorBox error={runs.error} /> : (
        <Panel title={`Pages (${rows.length})`} actions={slow > 0 ? <Badge kind="critical">{slow} slow page{slow === 1 ? "" : "s"}</Badge> : undefined}>
          {!rows.length ? <Empty title="No PageSpeed results yet" action={<Link className="btn primary" to="/audit-skills">Run the PageSpeed skill</Link>}>
            Run the PageSpeed audit skill on your pages — their loading times will appear here, slowest first.</Empty> : (
            <table className="tbl"><thead><tr><th>Page</th><th>Device</th><th className="r">LCP</th><th className="r">FCP</th><th className="r">TTI</th><th className="r">Score</th><th></th></tr></thead>
            <tbody>{rows.map((r, i) => {
              const bad = r.lcp != null && r.lcp > 3;
              return (
                <tr key={i} style={bad ? { background: "var(--critical-soft)" } : undefined}>
                  <td className="small"><a href={r.url} target="_blank" rel="noreferrer">{shortUrl(r.url)}</a></td>
                  <td className="small">{r.device}</td>
                  <td className="r num"><b>{fmtS(r.lcp)}</b></td>
                  <td className="r num">{fmtS(r.fcp)}</td>
                  <td className="r num">{fmtS(r.tti)}</td>
                  <td className="r num">{r.score ?? "—"}</td>
                  <td className="r">{bad ? <Badge kind="critical">Slow</Badge> : <Badge kind="pass">OK</Badge>}</td>
                </tr>);
            })}</tbody></table>)}
          <p className="small faint" style={{ marginTop: 8 }}>LCP = when the main content appears · FCP = first thing on screen · TTI = when the page responds to clicks. Measured by Google PageSpeed Insights.</p>
        </Panel>
      )}
    </>
  );
}

function fmtS(v: number | null) { return v == null ? "—" : `${v}s`; }
function shortUrl(u: string) {
  try { const p = new URL(u); return p.pathname === "/" ? p.hostname : p.hostname + p.pathname; }
  catch { return u.length > 45 ? u.slice(0, 45) + "…" : u; }
}
