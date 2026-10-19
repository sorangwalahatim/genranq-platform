import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel, Select } from "../components/ui";
import { api } from "../lib/api";
import { ago } from "../lib/format";
import { useAction, useApi, useMe } from "../lib/hooks";

export default function Spec() {
  const { me } = useMe();
  const { data, error } = useApi<any>("/spec/modules");
  const { data: spec } = useApi<any>("/spec");
  const [tab, setTab] = useState("roadmap");
  const [phase, setPhase] = useState("");
  const [status, setStatus] = useState("");
  if (error) return <ErrorBox error={error} />;
  if (!data || !spec) return <Loading />;
  const mods = data.modules.filter((m: any) => (phase === "" || String(m.phase) === phase) && (!status || m.status === status));
  const cov = data.coverage;
  return (
    <>
      <PageHead title="Product specification" sub={<>Genranq is built from the Build Plan workbook ({spec.meta.sheets_read.length} sheets: {spec.meta.counts.modules} modules, {spec.meta.counts.features} backlog features, {spec.meta.counts.tables} tables, {spec.meta.counts.integrations} integrations, {spec.meta.counts.checks} audit checks). This page traces each requirement to what exists in the product.</>} />
      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="panel kpi"><div className="v" style={{ color: "var(--pass)" }}>{cov.by_status.live}</div><div className="l">Modules live</div></div>
        <div className="panel kpi"><div className="v" style={{ color: "var(--medium)" }}>{cov.by_status.partial}</div><div className="l">Modules partially live</div></div>
        <div className="panel kpi"><div className="v">{cov.by_status.planned}</div><div className="l">Modules planned (later phases)</div></div>
        <div className="panel kpi"><div className="v">{spec.roadmap.totals["Sequential total (weeks)"]}</div><div className="l">Roadmap weeks (sequential)</div></div>
      </div>
      <div className="tabs" role="tablist">
        {[["roadmap", "Roadmap"], ["modules", "Module traceability"], ["features", "Feature backlog"], ["schema", "Database schema"], ...(me?.platform_admin ? [["refresh", "Refresh specification"], ["cost", "Internal cost estimate"]] : [])].map(([k, l]) =>
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "roadmap" && (
        <div className="stack">{spec.roadmap.phases.map((p: any) => {
          const pm = data.modules.filter((m: any) => m.phase === p.phase);
          const live = pm.filter((m: any) => m.status !== "planned").length;
          return (
            <Panel key={p.phase} title={<>{p.name} <span className="faint small">· {p.weeks} weeks · {p.squad} squad · depends on {p.depends_on}</span></>} actions={<Badge kind={live === pm.length ? "live" : live ? "partial-status" : "planned"}>{live}/{pm.length} modules live</Badge>}>
              <p className="muted" style={{ marginBottom: 10 }}>{p.deliverables}</p>
              <div className="row">{pm.map((m: any) => <Link key={m.num} to={m.route || `/m/${m.num}`}><Badge kind={m.status}>{m.name}</Badge></Link>)}</div>
            </Panel>
          );
        })}</div>
      )}
      {tab === "modules" && (
        <Panel pad={false}>
          <div className="panel-head"><span className="muted small">Module → backlog features → tables → integrations → jobs → permission → phase → status</span>
            <div className="row"><Select value={phase} onChange={setPhase} placeholder="All phases" label="Phase" options={[0, 1, 2, 3, 4, 5, 6].map((p) => ({ value: String(p), label: `Phase ${p}` }))} />
              <Select value={status} onChange={setStatus} placeholder="Any status" label="Status" options={["live", "partial", "planned"].map((s) => ({ value: s, label: s }))} /></div></div>
          <div className="table-wrap"><table className="t"><thead><tr><th>#</th><th>Module</th><th>Priority</th><th>Phase</th><th>Features</th><th>Tables</th><th>Integrations</th><th>Status</th></tr></thead>
            <tbody>{mods.map((m: any) => (
              <tr key={m.num}><td className="faint">{m.num}</td><td><Link to={m.route || `/m/${m.num}`}>{m.name}</Link><div className="faint small">{m.area} · {m.description}</div>{m.gap && <div className="small" style={{ color: "var(--medium)" }}>{m.gap}</div>}</td>
                <td>{m.priority}</td><td>{m.phase}</td><td className="small">{m.features.map((f: any) => f.id).join(", ") || "—"}</td>
                <td className="small">{m.tables.join(", ") || "—"}</td><td className="small">{m.integrations.join(", ") || "—"}</td><td><Badge kind={m.status} /></td></tr>
            ))}</tbody></table></div>
        </Panel>
      )}
      {tab === "features" && (
        <Panel pad={false}><div className="table-wrap"><table className="t"><thead><tr><th>ID</th><th>Epic</th><th>Feature</th><th>MoSCoW</th><th className="r">Points</th><th>Acceptance</th><th>Modules</th></tr></thead>
          <tbody>{spec.features.map((f: any) => {
            const ms = data.modules.filter((m: any) => m.features.some((x: any) => x.id === f.id));
            return <tr key={f.id}><td className="faint">{f.id}</td><td>{f.epic}</td><td>{f.name}</td><td>{f.moscow}</td><td className="r num">{f.points}</td><td className="small muted">{f.acceptance}</td>
              <td>{ms.length ? ms.map((m: any) => <Badge key={m.num} kind={m.status}>{m.name}</Badge>) : <Badge kind="live">Platform</Badge>}</td></tr>;
          })}</tbody></table></div></Panel>
      )}
      {tab === "schema" && (
        <div className="grid g3">{spec.schema.map((t: any) => (
          <Panel key={t.name} title={<>{t.name}{t.time_series && <span className="faint small"> · time-series</span>}</>} pad={false}>
            <table className="t"><tbody>{t.columns.map((c: any) => <tr key={c.spec_column}><td>{c.spec_column}</td><td className="faint small">{c.type}</td><td>{c.key && <Badge kind={c.key === "PK" ? "pass" : undefined}>{c.key}</Badge>}</td></tr>)}</tbody></table>
          </Panel>
        ))}</div>
      )}
      {tab === "refresh" && <Refresh />}
      {tab === "cost" && <Cost />}
    </>
  );
}

function Refresh() {
  const { data: snaps, reload } = useApi<any[]>("/spec/snapshots");
  const [diff, setDiff] = useState<any>(null);
  const { run, busy } = useAction();
  const file = useRef<HTMLInputElement>(null);
  const upload = async (f: File) => { const fd = new FormData(); fd.append("file", f); const r = await run(() => api("/spec/refresh/upload", { form: fd })); if (r) { setDiff(r); reload(); } };
  return (
    <div className="stack">
      <Panel title="Refresh from the source workbook">
        <p className="muted" style={{ marginBottom: 12 }}>Reads all nine sheets, compares them with the specification in use and shows what changed. Nothing changes until you approve. The source file is only ever read.</p>
        <div className="row">
          <input ref={file} type="file" accept=".xlsx" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <button className="btn primary" disabled={busy} onClick={() => file.current?.click()}>Upload workbook</button>
          <button className="btn" disabled={busy} onClick={() => run(() => api("/spec/refresh/drive", { body: {} })).then((r) => { if (r) { setDiff(r); reload(); } })}>Pull from Google Drive</button>
        </div>
      </Panel>
      <Panel title="Versions" pad={false}>
        {!snaps?.length ? <Empty title="No versions" /> : <table className="t"><thead><tr><th>Loaded</th><th>Source</th><th>Status</th><th>Changes</th><th /></tr></thead>
          <tbody>{snaps.map((s) => <tr key={s.id}><td>{ago(s.created_at)}</td><td className="small">{s.source}</td><td><Badge kind={s.status === "active" ? "live" : s.status === "pending_review" ? "pending" : undefined}>{s.status.replace("_", " ")}</Badge></td>
            <td className="num">{s.diff?.total_changes ?? "—"}</td><td>{s.status === "pending_review" && <button className="btn sm" onClick={() => setDiff({ snapshot_id: s.id, diff: s.diff, status: s.status })}>Review</button>}</td></tr>)}</tbody></table>}
      </Panel>
      {diff && <DiffModal d={diff} onClose={() => setDiff(null)} onDone={reload} />}
    </div>
  );
}

function DiffModal({ d, onClose, onDone }: any) {
  const { run } = useAction();
  const decide = (x: string) => run(() => api(`/spec/snapshots/${d.snapshot_id}/${x}`, { method: "POST" }), x === "approve" ? "Specification updated" : "Change rejected").then((r) => { if (r) { onDone(); onClose(); } });
  const label = (it: any) => it.name || it.id || it.provider || it.code || it.feature || it.phase || JSON.stringify(it).slice(0, 60);
  return (
    <Modal wide title={`Change summary: ${d.diff.total_changes} change${d.diff.total_changes === 1 ? "" : "s"}`} onClose={onClose}
      foot={d.status === "pending_review" ? <><button className="btn danger" onClick={() => decide("reject")}>Reject</button><button className="btn primary" onClick={() => decide("approve")}>Approve and apply</button></> : <button className="btn" onClick={onClose}>Close</button>}>
      {d.diff.total_changes === 0 && <p>The workbook matches the specification in use.</p>}
      {d.diff.sections.filter((s: any) => s.added.length + s.removed.length + s.changed.length).map((s: any) => (
        <div key={s.section} style={{ marginBottom: 16 }}>
          <h3>{s.section}</h3>
          {s.added.map((a: any, i: number) => <div key={"a" + i} className="small"><Badge kind="pass">New</Badge> {label(a)}</div>)}
          {s.removed.map((a: any, i: number) => <div key={"r" + i} className="small"><Badge kind="fail">Removed</Badge> {label(a)}</div>)}
          {s.changed.map((c: any) => <div key={c.key} className="small"><Badge kind="medium">Changed</Badge> {c.key}: {c.fields.map((f: string) => `${f}: “${c.before[f]}” → “${c.after[f]}”`).join("; ")}</div>)}
        </div>
      ))}
    </Modal>
  );
}

function Cost() {
  const { data, error } = useApi<any>("/spec/internal/cost-estimate");
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <Panel title="Internal effort and cost estimate" pad={false}>
      <div className="notice warn" style={{ margin: 16 }}>Internal planning data, visible to platform administrators only and never shown to customers.</div>
      <table className="t"><thead><tr><th>Phase</th><th className="r">Weeks</th><th className="r">Devs</th><th className="r">Dev-weeks</th><th className="r">Hours</th><th className="r">Cost</th></tr></thead>
        <tbody>{data.phases.map((p: any) => <tr key={p.phase}><td>{p.phase}</td><td className="r num">{p.weeks}</td><td className="r num">{p.devs}</td><td className="r num">{p.dev_weeks}</td><td className="r num">{p.hours}</td><td className="r num">{p.cost?.toLocaleString()}</td></tr>)}
          <tr><td><b>Total</b></td><td /><td /><td className="r num"><b>{data.summary.total.dev_weeks}</b></td><td className="r num"><b>{data.summary.total.hours}</b></td><td className="r num"><b>{data.summary.total.cost.toLocaleString()}</b></td></tr>
          <tr><td colSpan={5}>Grand total incl. {Math.round((data.assumptions["Contingency %"] || 0) * 100)}% contingency</td><td className="r num"><b>{data.summary.grand_total.toLocaleString()}</b></td></tr></tbody></table>
    </Panel>
  );
}
