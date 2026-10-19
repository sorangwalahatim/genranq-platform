import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorBox, Loading, Modal, PageHead, Panel, Select } from "../components/ui";
import { api, download } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

const iso = (d: Date) => d.toISOString().slice(0, 10);
function monday(d = new Date()) { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }

export default function Time() {
  const [start, setStart] = useState(iso(monday()));
  const end = iso(new Date(new Date(start).getTime() + 7 * 864e5));
  const { data, error, loading, reload } = useApi<any>(`/time?start=${start}&end=${end}`, [start]);
  const { data: projects } = useApi<any[]>("/projects");
  const { data: running, reload: reloadRunning } = useApi<any>("/time/running");
  const [proj, setProj] = useState("");
  const [desc, setDesc] = useState("");
  const [billable, setBillable] = useState(true);
  const [manual, setManual] = useState(false);
  const { run } = useAction();
  const can = useCan();
  const changed = () => { reload(); reloadRunning(); window.dispatchEvent(new Event("timer-changed")); };
  const shift = (days: number) => setStart(iso(new Date(new Date(start).getTime() + days * 864e5)));
  const days = Array.from({ length: 7 }, (_, i) => iso(new Date(new Date(start).getTime() + i * 864e5)));
  const chart = days.map((d) => ({ day: new Date(d).toLocaleDateString(undefined, { weekday: "short" }), hours: data?.by_day?.[d] || 0 }));
  return (
    <>
      <PageHead title="Time tracking" sub="Timers and timesheets with billable hours per project.">
        {can("export") && <button className="btn" onClick={() => download(`/time/export.csv?start=${start}&end=${end}`, `timesheet-${start}.csv`)}>Export CSV</button>}
        <button className="btn" onClick={() => setManual(true)}>Add time</button>
      </PageHead>
      <Panel>
        {running ? (
          <div className="row"><b>Running:</b> {running.description || "Untitled"} <span className="muted">since {fmtDateTime(running.started_at)}</span><span className="spacer" />
            <button className="btn primary" onClick={() => run(() => api("/time/stop", { method: "POST" }), "Timer stopped").then(changed)}>Stop timer</button></div>
        ) : (
          <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api("/time/start", { body: { project_id: proj || projects?.[0]?.id, description: desc || null, billable } }), "Timer started").then(() => { setDesc(""); changed(); }); }}>
            <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="What are you working on?" value={desc} onChange={(e) => setDesc(e.target.value)} />
            <Select value={proj || projects?.[0]?.id || ""} onChange={setProj} label="Project" options={(projects || []).map((p) => ({ value: p.id, label: p.name }))} />
            <label className="check"><input type="checkbox" checked={billable} onChange={(e) => setBillable(e.target.checked)} /> Billable</label>
            <button className="btn primary" disabled={!projects?.length}>Start timer</button>
          </form>
        )}
      </Panel>
      <div className="row" style={{ margin: "18px 0 12px" }}>
        <button className="btn sm" onClick={() => shift(-7)} aria-label="Previous week">‹</button>
        <b>Week of {new Date(start).toLocaleDateString(undefined, { day: "numeric", month: "long" })}</b>
        <button className="btn sm" onClick={() => shift(7)} aria-label="Next week">›</button>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <>
          <div className="grid g4">
            <div className="panel kpi"><div className="v">{data.totals.hours}h</div><div className="l">Tracked</div></div>
            <div className="panel kpi"><div className="v">{data.totals.billable_hours}h</div><div className="l">Billable</div></div>
            <div className="panel kpi"><div className="v">{data.totals.utilisation_pct ?? "—"}{data.totals.utilisation_pct != null && "%"}</div><div className="l">Billable share</div></div>
            <div className="panel kpi"><div className="v">{data.totals.billable_amount ? data.totals.billable_amount.toFixed(0) : "—"}</div><div className="l">Billable amount (at entry rates)</div></div>
          </div>
          <div className="split" style={{ marginTop: 16 }}>
            <Panel title="Hours per day">
              <div style={{ height: 200 }}><ResponsiveContainer><BarChart data={chart}><CartesianGrid vertical={false} stroke="var(--line-2)" /><XAxis dataKey="day" tickLine={false} axisLine={false} /><YAxis width={30} tickLine={false} axisLine={false} /><Tooltip /><Bar dataKey="hours" fill="var(--accent)" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div>
            </Panel>
            <Panel title="By project">{Object.keys(data.by_project).length ? Object.entries(data.by_project).map(([k, v]: any) => <div key={k} className="row" style={{ padding: "4px 0" }}><span style={{ flex: 1 }}>{k}</span><b className="num">{v}h</b></div>) : <p className="muted">No time this week.</p>}
              {Object.keys(data.by_user).length > 1 && <><h3 style={{ marginTop: 14 }}>By person</h3>{Object.entries(data.by_user).map(([k, v]: any) => <div key={k} className="row" style={{ padding: "4px 0" }}><span style={{ flex: 1 }}>{k}</span><b className="num">{v}h</b></div>)}</>}
            </Panel>
          </div>
          <div style={{ marginTop: 16 }}><Panel title="Entries" pad={false}>
            {!data.entries.length ? <Empty title="No entries this week" /> : (
              <div className="table-wrap"><table className="t"><thead><tr><th>Started</th><th>Person</th><th>Project</th><th>Description</th><th className="r">Hours</th><th>Billable</th><th /></tr></thead>
                <tbody>{data.entries.map((e: any) => (
                  <tr key={e.id}><td className="nowrap">{fmtDateTime(e.started_at)}</td><td>{e.user}</td><td>{e.project}</td><td>{e.description || <span className="faint">—</span>}{!e.ended_at && <span className="badge pass" style={{ marginLeft: 6 }}>running</span>}</td>
                    <td className="r num">{e.hours.toFixed(2)}</td><td>{e.billable ? "Yes" : "No"}</td>
                    <td><button className="btn ghost sm danger" onClick={() => confirm("Delete this entry?") && run(() => api(`/time/${e.id}`, { method: "DELETE" })).then(changed)}>Delete</button></td></tr>
                ))}</tbody></table></div>
            )}
          </Panel></div>
        </>
      )}
      {manual && <ManualTime projects={projects || []} onClose={() => setManual(false)} onDone={changed} />}
    </>
  );
}

function ManualTime({ projects, onClose, onDone }: any) {
  const today = iso(new Date());
  const [f, setF] = useState({ project_id: projects[0]?.id, date: today, from: "09:00", to: "10:00", description: "", billable: true, hourly_rate: "" });
  const { run } = useAction();
  const save = () => run(() => api("/time", { body: { project_id: f.project_id, description: f.description || null, billable: f.billable, hourly_rate: f.hourly_rate ? Number(f.hourly_rate) : null,
    started_at: new Date(`${f.date}T${f.from}`).toISOString(), ended_at: new Date(`${f.date}T${f.to}`).toISOString() } }), "Time added").then((r) => { if (r) { onDone(); onClose(); } });
  return (
    <Modal title="Add time" onClose={onClose} foot={<button className="btn primary" onClick={save}>Add time</button>}>
      <div className="form">
        <label className="field">Project<select className="input" value={f.project_id} onChange={(e) => setF({ ...f, project_id: e.target.value })}>{projects.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <div className="grid g3">
          <label className="field">Date<input type="date" className="input" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></label>
          <label className="field">From<input type="time" className="input" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></label>
          <label className="field">To<input type="time" className="input" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></label>
        </div>
        <label className="field">Description<input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        <div className="grid g2">
          <label className="check"><input type="checkbox" checked={f.billable} onChange={(e) => setF({ ...f, billable: e.target.checked })} /> Billable</label>
          <label className="field">Hourly rate<input className="input" inputMode="decimal" value={f.hourly_rate} onChange={(e) => setF({ ...f, hourly_rate: e.target.value })} /></label>
        </div>
      </div>
    </Modal>
  );
}
