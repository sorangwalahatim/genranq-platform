import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel, SeverityStrip } from "../../components/ui";
import { api } from "../../lib/api";
import { fmtDateTime } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { SEV_ORDER, sevLabel } from "./shared";

type Finding = any;

export default function RunDetail() {
  const { id } = useParams();
  const can = useCan();
  const { run, busy } = useAction();
  const [sev, setSev] = useState("");
  const [showIgnored, setShowIgnored] = useState(false);
  const data = useApi<any>(`/auditskills/runs/${id}`);

  const setStatus = (f: Finding, status: string) => run(
    () => api(`/auditskills/findings/${f.id}`, { method: "PATCH", body: { status } }),
    status === "ignored" ? "Finding ignored" : status === "fixed" ? "Marked as fixed" : "Reopened")
    .then(() => data.reload());

  return (
    <>
      <PageHead title={data.data?.skill_name || "Run results"} sub={data.data ? `Finished ${fmtDateTime(data.data.finished_at)} · ${data.data.cost_estimate || ""}` : ""}>
        <Link className="btn" to="/audit-skills">All skills</Link>
      </PageHead>
      {data.loading ? <Loading /> : data.error ? <ErrorBox error={data.error} /> : !data.data ? <Empty title="Run not found" /> : (
        <>
          {data.data.status === "failed" && <Panel><ErrorBox error={new Error(data.data.error || "The run failed")} /></Panel>}
          {data.data.summary && Object.keys(data.data.summary).length > 0 && (
            <Panel title="Summary">
              <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
                {Object.entries(data.data.summary).filter(([k]) => !["note", "devices"].includes(k)).map(([k, v]: any) => (
                  <div key={k}><div className="faint small">{k.replace(/_/g, " ")}</div><div style={{ fontSize: 20 }}>{String(v)}</div></div>))}
              </div>
              {data.data.summary.note && <p className="small faint" style={{ marginTop: 8 }}>{data.data.summary.note}</p>}
            </Panel>
          )}
          <Panel title={`Findings (${data.data.findings?.length || 0})`}
            actions={<>
              <select value={sev} onChange={(e) => setSev(e.target.value)}>
                <option value="">All severities</option>
                {SEV_ORDER.map((s) => <option key={s} value={s}>{sevLabel(s)}</option>)}
              </select>
              <label className="small"><input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} /> Show ignored</label>
            </>}>
            <SeverityStrip issues={countBy(data.data.findings, "severity")} />
            {!data.data.findings?.length ? <Empty title="No findings">Nothing wrong was found — good news.</Empty> : (
              <table className="tbl"><thead><tr><th style={{ width: 90 }}>Severity</th><th>Problem</th><th>Page</th><th>What to do</th><th style={{ width: 130 }}></th></tr></thead>
              <tbody>{data.data.findings
                .filter((f: Finding) => (!sev || f.severity === sev) && (showIgnored || f.status !== "ignored"))
                .map((f: Finding) => (
                  <tr key={f.id} style={f.status !== "open" ? { opacity: 0.55 } : undefined}>
                    <td><Badge kind={f.severity}>{sevLabel(f.severity)}</Badge>{f.status !== "open" && <div className="small faint">{f.status}</div>}</td>
                    <td><b>{f.title}</b>{f.detail && <div className="small faint" style={{ whiteSpace: "pre-wrap" }}>{f.detail}</div>}</td>
                    <td className="small">{f.url ? <a href={f.url} target="_blank" rel="noreferrer">{shortUrl(f.url)}</a> : "—"}</td>
                    <td className="small" style={{ whiteSpace: "pre-wrap" }}>{f.recommendation || "—"}</td>
                    <td className="r">{can("edit") && (
                      f.status === "open"
                        ? <><button className="btn small" disabled={busy} onClick={() => setStatus(f, "fixed")}>Fixed</button>{" "}
                            <button className="btn small" disabled={busy} onClick={() => setStatus(f, "ignored")}>Ignore</button></>
                        : <button className="btn small" disabled={busy} onClick={() => setStatus(f, "open")}>Reopen</button>)}</td>
                  </tr>))}</tbody></table>)}
          </Panel>
        </>
      )}
    </>
  );
}

function countBy(list: any[], key: string) {
  const out: Record<string, number> = {};
  (list || []).forEach((x) => { out[x[key]] = (out[x[key]] || 0) + 1; });
  return out;
}

function shortUrl(u: string) {
  try { const p = new URL(u); return p.pathname === "/" ? p.hostname : p.hostname + p.pathname; }
  catch { return u.length > 50 ? u.slice(0, 50) + "…" : u; }
}
