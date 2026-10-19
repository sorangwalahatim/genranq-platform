import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, PageHead, Panel, Select } from "../components/ui";
import { api } from "../lib/api";
import { fmtDate, human } from "../lib/format";
import { useAction, useApi, useCan, useMe } from "../lib/hooks";

const COLS = ["todo", "in_progress", "review", "done"];
const PRI = ["low", "medium", "high", "critical"];
const priKind: Record<string, string> = { critical: "critical", high: "high", medium: "medium", low: "low" };

export default function Tasks() {
  const [sp, setSp] = useSearchParams();
  const [view, setView] = useState<"board" | "list">(() => (localStorage.getItem("genranq.taskview") as any) || "board");
  const [q, setQ] = useState("");
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState("");
  const project = sp.get("project") || "";
  const { data: projects } = useApi<any[]>("/projects");
  const { data: team } = useApi<any>("/team");
  const qs = new URLSearchParams({ ...(project && { project_id: project }), ...(assignee && { assignee }), ...(priority && { priority }), ...(q && { q }) }).toString();
  const { data, error, loading, reload, setData } = useApi<any[]>(`/tasks?${qs}`, [qs]);
  const [open, setOpen] = useState<any>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState("");
  const can = useCan();
  const { run } = useAction();
  const users = team?.users || [];
  const uname = (id: string) => { const u = users.find((x: any) => x.id === id); return u ? (u.name || u.email) : "?"; };
  const setV = (v: "board" | "list") => { setView(v); try { localStorage.setItem("genranq.taskview", v); } catch { /* ignore */ } };

  const move = async (id: string, status: string) => {
    setData((d) => d && d.map((t) => (t.id === id ? { ...t, status } : t)));
    await run(() => api(`/tasks/${id}`, { method: "PATCH", body: { status } }));
    reload();
  };
  const bulk = async (body: any) => { await run(() => api("/tasks/bulk", { body: { ids: sel, ...body } }), "Tasks updated"); setSel([]); reload(); };
  const byCol = useMemo(() => Object.fromEntries(COLS.map((c) => [c, (data || []).filter((t) => t.status === c)])), [data]);

  return (
    <>
      <PageHead title="Tasks" sub="Work for every project, including fixes generated from audit action plans.">
        <div className="seg" role="group" aria-label="View"><button className={view === "board" ? "on" : ""} onClick={() => setV("board")}>Board</button><button className={view === "list" ? "on" : ""} onClick={() => setV("list")}>List</button></div>
        {can("create") && <button className="btn primary" onClick={() => setOpen({ project_id: project || projects?.[0]?.id, status: "todo", priority: "medium", assignee_ids: [], title: "" })} disabled={!projects?.length}>New task</button>}
      </PageHead>
      <div className="row" style={{ marginBottom: 14 }}>
        <input className="input" style={{ maxWidth: 260 }} placeholder="Search tasks" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search tasks" />
        <Select value={project} onChange={(v) => { v ? sp.set("project", v) : sp.delete("project"); setSp(sp); }} placeholder="All projects" label="Project" options={(projects || []).map((p) => ({ value: p.id, label: p.name }))} />
        <Select value={assignee} onChange={setAssignee} placeholder="Anyone" label="Assignee" options={users.map((u: any) => ({ value: u.id, label: u.name || u.email }))} />
        <Select value={priority} onChange={setPriority} placeholder="Any priority" label="Priority" options={PRI.map((p) => ({ value: p, label: human(p) }))} />
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !projects?.length ? <Panel><Empty title="Create a project first">Tasks belong to projects.</Empty></Panel> : view === "board" ? (
        <div className="kanban">
          {COLS.map((c) => (
            <div key={c} className={`col ${dragOver === c ? "drop" : ""}`} onDragOver={(e) => { e.preventDefault(); setDragOver(c); }} onDragLeave={() => setDragOver("")}
              onDrop={(e) => { setDragOver(""); const id = e.dataTransfer.getData("text/plain"); if (id) move(id, c); }}>
              <div className="col-head"><span>{human(c)}</span><span className="faint num">{byCol[c].length}</span></div>
              {byCol[c].map((t: any) => (
                <div key={t.id} className="card" draggable={can("edit")} onDragStart={(e) => e.dataTransfer.setData("text/plain", t.id)} onClick={() => setOpen(t)}
                  tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(t)}>
                  <div className="title">{t.title}</div>
                  <div className="row small"><Badge kind={priKind[t.priority]}>{human(t.priority)}</Badge>
                    {t.due_date && <span className={new Date(t.due_date) < new Date() && t.status !== "done" ? "badge critical" : "faint"}>{fmtDate(t.due_date)}</span>}
                    <span className="spacer" /><span className="faint">{t.assignee_ids.map(uname).map(initials).join(" ")}</span></div>
                  {!project && <div className="faint small" style={{ marginTop: 4 }}>{t.project_name}</div>}
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <Panel pad={false}>
          {sel.length > 0 && (
            <div className="panel-head"><span>{sel.length} selected</span><div className="row">
              <Select value="" onChange={(v) => v && bulk({ status: v })} placeholder="Set status…" options={COLS.map((c) => ({ value: c, label: human(c) }))} />
              <Select value="" onChange={(v) => v && bulk({ priority: v })} placeholder="Set priority…" options={PRI.map((c) => ({ value: c, label: human(c) }))} />
              {can("delete") && <button className="btn danger sm" onClick={() => confirm(`Delete ${sel.length} tasks?`) && bulk({ delete: true })}>Delete</button>}
            </div></div>
          )}
          {!data?.length ? <Empty title="No tasks match these filters" /> : (
            <div className="table-wrap"><table className="t"><thead><tr><th><input type="checkbox" aria-label="Select all" checked={sel.length === data.length} onChange={(e) => setSel(e.target.checked ? data.map((t) => t.id) : [])} /></th><th>Task</th><th>Project</th><th>Status</th><th>Priority</th><th>Assignees</th><th>Due</th></tr></thead>
              <tbody>{data.map((t) => (
                <tr key={t.id} className="click" onClick={() => setOpen(t)}>
                  <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${t.title}`} checked={sel.includes(t.id)} onChange={(e) => setSel(e.target.checked ? [...sel, t.id] : sel.filter((x) => x !== t.id))} /></td>
                  <td>{t.title}</td><td className="muted">{t.project_name}</td><td><Badge kind={t.status === "done" ? "done" : undefined}>{human(t.status)}</Badge></td>
                  <td><Badge kind={priKind[t.priority]}>{human(t.priority)}</Badge></td><td className="small">{t.assignee_ids.map(uname).join(", ") || "—"}</td><td className="muted">{fmtDate(t.due_date)}</td></tr>
              ))}</tbody></table></div>
          )}
        </Panel>
      )}
      {open && <TaskDrawer task={open} projects={projects || []} users={users} onClose={() => setOpen(null)} onSaved={reload} />}
    </>
  );
}

const initials = (n: string) => n.split(/[\s@.]/).filter(Boolean).slice(0, 2).map((x) => x[0]?.toUpperCase()).join("");

function TaskDrawer({ task, projects, users, onClose, onSaved }: any) {
  const [t, setT] = useState({ ...task, due_date: task.due_date ? task.due_date.slice(0, 10) : "" });
  const { run, busy } = useAction();
  const can = useCan();
  const { me } = useMe();
  const isNew = !task.id;
  const save = async () => {
    const body = { title: t.title, description: t.description || null, status: t.status, priority: t.priority, assignee_ids: t.assignee_ids,
      due_date: t.due_date ? new Date(t.due_date + "T17:00:00").toISOString() : null };
    const r = await run(() => (isNew ? api("/tasks", { body: { ...body, project_id: t.project_id } }) : api(`/tasks/${t.id}`, { method: "PATCH", body })), isNew ? "Task created" : "Task saved");
    if (r) { onSaved(); onClose(); }
  };
  const startTimer = () => run(() => api("/time/start", { body: { project_id: t.project_id, task_id: t.id, description: t.title } }), "Timer started").then(() => window.dispatchEvent(new Event("timer-changed")));
  const ro = !can(isNew ? "create" : "edit");
  return (
    <Drawer title={isNew ? "New task" : "Task"} onClose={onClose} foot={<>
      {!isNew && can("delete") && <button className="btn danger" onClick={() => confirm("Delete this task?") && run(() => api(`/tasks/${t.id}`, { method: "DELETE" }), "Task deleted").then(() => { onSaved(); onClose(); })}>Delete</button>}
      {!isNew && <button className="btn" onClick={startTimer}>Start timer</button>}
      <span className="spacer" />{!ro && <button className="btn primary" disabled={!t.title || busy} onClick={save}>{isNew ? "Create task" : "Save"}</button>}</>}>
      <div className="form">
        <label className="field">Title<input className="input" value={t.title} disabled={ro} onChange={(e) => setT({ ...t, title: e.target.value })} autoFocus={isNew} /></label>
        {isNew && <label className="field">Project<select className="input" value={t.project_id} onChange={(e) => setT({ ...t, project_id: e.target.value })}>{projects.map((p: any) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
        <div className="grid g2">
          <label className="field">Status<select className="input" disabled={ro} value={t.status} onChange={(e) => setT({ ...t, status: e.target.value })}>{COLS.map((c) => <option key={c} value={c}>{human(c)}</option>)}</select></label>
          <label className="field">Priority<select className="input" disabled={ro} value={t.priority} onChange={(e) => setT({ ...t, priority: e.target.value })}>{PRI.map((c) => <option key={c} value={c}>{human(c)}</option>)}</select></label>
        </div>
        <label className="field">Due date<input className="input" type="date" disabled={ro} value={t.due_date} onChange={(e) => setT({ ...t, due_date: e.target.value })} /></label>
        <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}><legend style={{ fontWeight: 500, marginBottom: 5 }}>Assignees</legend>
          <div className="row">{users.filter((u: any) => u.is_active && u.role !== "client").map((u: any) => (
            <label key={u.id} className="check badge" style={{ height: 26 }}><input type="checkbox" disabled={ro} checked={t.assignee_ids.includes(u.id)}
              onChange={(e) => setT({ ...t, assignee_ids: e.target.checked ? [...t.assignee_ids, u.id] : t.assignee_ids.filter((x: string) => x !== u.id) })} />{u.name || u.email}{u.id === me?.user.id ? " (you)" : ""}</label>
          ))}</div></fieldset>
        <label className="field">Description<textarea className="input" rows={10} disabled={ro} value={t.description || ""} onChange={(e) => setT({ ...t, description: e.target.value })} /></label>
        {t.source && <p className="faint small">Created from {t.source.startsWith("seo_plan") ? "an SEO action plan" : t.source}</p>}
      </div>
    </Drawer>
  );
}
