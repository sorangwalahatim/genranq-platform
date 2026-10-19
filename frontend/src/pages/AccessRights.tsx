import { useMemo, useState } from "react";
import { Badge, ErrorBox, Loading, Modal, Panel } from "../components/ui";
import { api } from "../lib/api";
import { human } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

type Mod = { key: string; name: string; area: string; status: string };
type U = { user_id: string; name: string | null; email: string; role: string; is_active: boolean; restricted: boolean; editable: boolean;
  all_projects: boolean; project_ids: string[]; modules: string[] | null };

/** Per-user rights: which projects and which modules each person can use (enforced by the server). */
export default function AccessRights() {
  const users = useApi<U[]>("/access/users");
  const mods = useApi<Mod[]>("/access/modules");
  const projects = useApi<any[]>("/projects");
  const [edit, setEdit] = useState<U | null>(null);
  if (users.error) return <ErrorBox error={users.error} />;
  if (!users.data || !mods.data || !projects.data) return <Loading />;
  const pname = Object.fromEntries(projects.data.map((p) => [p.id, p.name]));
  return (
    <Panel pad={false} title="Access rights — projects & modules">
      <p className="faint small" style={{ padding: "0 16px" }}>Roles decide what someone can do (view, create, approve…); access rights decide where: which projects they see and which modules they can open. Owners always have full access.</p>
      <div className="table-wrap"><table className="t"><thead><tr><th>Person</th><th>Role</th><th>Projects</th><th>Modules</th><th /></tr></thead>
        <tbody>{users.data.map((u) => <tr key={u.user_id}>
          <td>{u.name || u.email}<div className="faint small">{u.email}</div></td><td>{human(u.role)}</td>
          <td>{u.all_projects ? <Badge kind="pass">All projects</Badge> : <span className="small">{u.project_ids.map((p) => pname[p] || "?").join(", ") || "None"}</span>}</td>
          <td>{u.modules === null ? <Badge kind="pass">All modules</Badge> : <span className="small">{u.modules.length} of {mods.data!.length}</span>}</td>
          <td>{u.editable ? <button className="btn sm" onClick={() => setEdit(u)}>Edit access</button> : <span className="faint small">{u.role === "owner" ? "full access" : "—"}</span>}</td>
        </tr>)}</tbody></table></div>
      {edit && <Editor u={edit} mods={mods.data} projects={projects.data} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); users.reload(); }} />}
    </Panel>
  );
}

function Editor({ u, mods, projects, onClose, onSaved }: { u: U; mods: Mod[]; projects: any[]; onClose: () => void; onSaved: () => void }) {
  const [allP, setAllP] = useState(u.all_projects);
  const [pids, setPids] = useState<Set<string>>(new Set(u.project_ids));
  const [allM, setAllM] = useState(u.modules === null);
  const [keys, setKeys] = useState<Set<string>>(new Set(u.modules ?? mods.map((m) => m.key)));
  const { run, busy } = useAction();
  const areas = useMemo(() => {
    const g: Record<string, Mod[]> = {};
    for (const m of mods) (g[m.area] ||= []).push(m);
    return Object.entries(g);
  }, [mods]);
  const tog = (s: Set<string>, k: string, set: (x: Set<string>) => void) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); set(n); };
  const setArea = (list: Mod[], on: boolean) => { const n = new Set(keys); list.forEach((m) => (on ? n.add(m.key) : n.delete(m.key))); setKeys(n); };
  const save = () => run(() => api(`/access/users/${u.user_id}`, { method: "PUT", body: { all_projects: allP, project_ids: allP ? [] : [...pids], modules: allM ? null : [...keys] } }), "Access updated").then((r) => r && onSaved());
  return (
    <Modal wide title={`Access for ${u.name || u.email}`} onClose={onClose} foot={<button className="btn primary" disabled={busy || (!allP && !pids.size)} onClick={save}>Save access</button>}>
      <h3>Projects</h3>
      <label className="check"><input type="checkbox" checked={allP} onChange={(e) => setAllP(e.target.checked)} /> All projects (including new ones)</label>
      {!allP && <div className="grid g3" style={{ margin: "8px 0 16px" }}>{projects.map((p) => <label key={p.id} className="check"><input type="checkbox" checked={pids.has(p.id)} onChange={() => tog(pids, p.id, setPids)} /> {p.name}</label>)}</div>}
      <h3 style={{ marginTop: 16 }}>Modules</h3>
      <label className="check"><input type="checkbox" checked={allM} onChange={(e) => setAllM(e.target.checked)} /> All modules the role allows</label>
      {!allM && areas.map(([area, list]) => {
        const on = list.filter((m) => keys.has(m.key)).length;
        return <fieldset key={area} className="field" style={{ marginTop: 10 }}>
          <legend className="row" style={{ gap: 8 }}><b>{area}</b> <span className="faint small">{on}/{list.length}</span>
            <button type="button" className="btn ghost sm" onClick={() => setArea(list, on < list.length)}>{on < list.length ? "Select all" : "Clear"}</button></legend>
          <div className="grid g3">{list.map((m) => <label key={m.key} className="check"><input type="checkbox" checked={keys.has(m.key)} onChange={() => tog(keys, m.key, setKeys)} /> {m.name}</label>)}</div>
        </fieldset>;
      })}
    </Modal>
  );
}
