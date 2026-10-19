import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { fmtDate } from "../lib/format";
import { useAction, useApi, useCan } from "../lib/hooks";

export function Projects() {
  const { data, error, loading, reload } = useApi<any[]>("/projects?include_templates=true");
  const [show, setShow] = useState(false);
  const can = useCan();
  const projects = (data || []).filter((p) => !p.is_template);
  const templates = (data || []).filter((p) => p.is_template);
  return (
    <>
      <PageHead title="Projects" sub="One project per client engagement. Sites, keywords, tasks and audits live inside a project.">
        {can("create") && <button className="btn primary" onClick={() => setShow(true)}>New project</button>}
      </PageHead>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : !projects.length ? (
        <Panel><Empty title="Create your first project" action={can("create") && <button className="btn primary" onClick={() => setShow(true)}>New project</button>}>A project groups a client’s sites, keywords, tasks and reports.</Empty></Panel>
      ) : (
        <Panel pad={false}>
          <div className="table-wrap"><table className="t"><thead><tr><th>Project</th><th>Client</th><th>Sites</th><th className="r">Keywords</th><th className="r">Open tasks</th><th>Created</th></tr></thead>
            <tbody>{projects.map((p) => (
              <tr key={p.id}><td><Link to={`/projects/${p.id}`}>{p.name}</Link> {p.status !== "active" && <Badge>{p.status}</Badge>}</td>
                <td>{p.client_name || "—"}</td><td className="small">{p.sites.map((s: any) => s.domain).join(", ") || <span className="faint">No sites</span>}</td>
                <td className="r num">{p.keywords}</td><td className="r num">{p.open_tasks}</td><td className="muted">{fmtDate(p.created_at)}</td></tr>
            ))}</tbody></table></div>
        </Panel>
      )}
      {templates.length > 0 && <p className="muted small" style={{ marginTop: 12 }}>Templates: {templates.map((t) => t.name).join(", ")}</p>}
      {show && <NewProject templates={templates} onClose={() => setShow(false)} onDone={reload} />}
    </>
  );
}

function NewProject({ templates, onClose, onDone }: { templates: any[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ name: "", client_name: "", description: "", template_id: "", is_template: false, domain: "" });
  const { run, busy } = useAction();
  const nav = useNavigate();
  const save = async () => {
    const p: any = await run(() => api("/projects", { body: { name: f.name, client_name: f.client_name || null, description: f.description || null, is_template: f.is_template, template_id: f.template_id || null } }), "Project created");
    if (!p) return;
    if (f.domain) await run(() => api("/sites", { body: { project_id: p.id, domain: f.domain } }));
    onDone(); onClose(); nav(`/projects/${p.id}`);
  };
  return (
    <Modal title="New project" onClose={onClose} foot={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!f.name || busy} onClick={save}>Create project</button></>}>
      <div className="form">
        <label className="field">Project name<input className="input" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="field">Client<input className="input" value={f.client_name} onChange={(e) => setF({ ...f, client_name: e.target.value })} /></label>
        <label className="field">Website<span className="hint">Optional — you can add more sites later.</span><input className="input" placeholder="example.com" value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })} /></label>
        {templates.length > 0 && <label className="field">Start from template<select className="input" value={f.template_id} onChange={(e) => setF({ ...f, template_id: e.target.value })}><option value="">Blank project</option>{templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>}
        <label className="check"><input type="checkbox" checked={f.is_template} onChange={(e) => setF({ ...f, is_template: e.target.checked })} /> Save as a reusable template</label>
      </div>
    </Modal>
  );
}

export function ProjectDetail() {
  const { id } = useParams();
  const { data: p, error, reload } = useApi<any>(`/projects/${id}`);
  const { data: team } = useApi<any>("/team");
  const { data: audits } = useApi<any[]>(`/audits?project_id=${id}`);
  const can = useCan();
  const { run } = useAction();
  const [domain, setDomain] = useState("");
  const [comp, setComp] = useState("");
  const [editSite, setEditSite] = useState<any>(null);
  if (error) return <ErrorBox error={error} />;
  if (!p) return <Loading />;
  const clients = (team?.users || []).filter((u: any) => u.role === "client");
  const toggleClient = (uid: string) => {
    const ids = p.client_user_ids.includes(uid) ? p.client_user_ids.filter((x: string) => x !== uid) : [...p.client_user_ids, uid];
    run(() => api(`/projects/${id}`, { method: "PATCH", body: { client_user_ids: ids } }), "Client access updated").then(reload);
  };
  return (
    <>
      <PageHead title={p.name} sub={p.client_name ? `Client: ${p.client_name}` : undefined}>
        <Link className="btn primary" to={`/portal/${id}`}>📊 Report</Link>
        <Link className="btn" to={`/projects/${id}/monitor`}>Monitor</Link>
        <Link className="btn" to={`/projects/${id}/workstreams`}>Workstreams</Link>
        <Link className="btn" to={`/tasks?project=${id}`}>Tasks</Link>
        <Link className="btn" to={`/rank-tracker?project=${id}`}>Rank tracker</Link>
        {can("delete") && <button className="btn danger" onClick={() => confirm(`Delete ${p.name} and everything in it?`) && run(() => api(`/projects/${id}`, { method: "DELETE" }), "Project deleted").then(() => history.back())}>Delete</button>}
      </PageHead>
      <div className="grid g2">
        <Panel title="Sites">
          {p.sites.map((s: any) => (
            <div key={s.id} className="row" style={{ padding: "8px 0", borderBottom: "1px solid var(--line-2)" }}>
              <div style={{ flex: 1 }}><b style={{ fontWeight: 500 }}>{s.domain}</b><div className="faint small">Search Console: {s.gsc_property || "not set"} · GA4: {s.ga4_property || "not set"}</div></div>
              {can("edit") && <button className="btn sm" onClick={() => setEditSite(s)}>Edit</button>}
              <Link className="btn sm" to={`/audits?new=1&site=${s.id}`}>Audit</Link>
            </div>
          ))}
          {can("create") && <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); run(() => api("/sites", { body: { project_id: id, domain } }), "Site added").then(() => { setDomain(""); reload(); }); }}>
            <input className="input" style={{ flex: 1 }} placeholder="Add a site, e.g. example.com" value={domain} onChange={(e) => setDomain(e.target.value)} />
            <button className="btn" disabled={!domain}>Add site</button></form>}
        </Panel>
        <Panel title="Competitors">
          {!p.competitors.length && <p className="muted">Add competitor domains to audit them side by side.</p>}
          {p.competitors.map((c: any) => (
            <div key={c.id} className="row" style={{ padding: "6px 0", borderBottom: "1px solid var(--line-2)" }}>
              <span style={{ flex: 1 }}>{c.domain}</span>
              {p.sites[0] && <Link className="btn sm" to={`/audits?new=1&site=${p.sites[0].id}&competitor=${c.id}`}>Audit</Link>}
              {can("delete") && <button className="btn sm ghost danger" onClick={() => run(() => api(`/competitors/${c.id}`, { method: "DELETE" })).then(reload)}>Remove</button>}
            </div>
          ))}
          {can("create") && <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); run(() => api("/competitors", { body: { project_id: id, domain: comp } }), "Competitor added").then(() => { setComp(""); reload(); }); }}>
            <input className="input" style={{ flex: 1 }} placeholder="competitor.com" value={comp} onChange={(e) => setComp(e.target.value)} /><button className="btn" disabled={!comp}>Add</button></form>}
        </Panel>
        <Panel title="Audits" pad={false}>
          {audits?.length ? <table className="t"><tbody>{audits.slice(0, 8).map((a) => (
            <tr key={a.id}><td><Link to={`/audits/${a.id}`}>{a.domain}</Link>{a.is_competitor && <span className="faint small"> competitor</span>}</td><td><Badge kind={a.status} /></td><td className="num r">{a.score ?? "—"}</td><td className="muted small">{fmtDate(a.created_at)}</td></tr>
          ))}</tbody></table> : <Empty title="No audits yet" />}
        </Panel>
        {can("manage_users") && (
          <Panel title="Client access">
            {!clients.length ? <p className="muted">Invite users with the Client role from <Link to="/team">Team</Link> to give them read-only access to this project. (Enterprise plan)</p>
              : clients.map((u: any) => <label key={u.id} className="check" style={{ padding: "4px 0" }}><input type="checkbox" checked={p.client_user_ids.includes(u.id)} onChange={() => toggleClient(u.id)} />{u.name || u.email}</label>)}
          </Panel>
        )}
      </div>
      {editSite && <SiteEdit site={editSite} onClose={() => setEditSite(null)} onDone={reload} />}
    </>
  );
}

function SiteEdit({ site, onClose, onDone }: any) {
  const [f, setF] = useState({ gsc_property: site.gsc_property || "", ga4_property: site.ga4_property || "" });
  const { run } = useAction();
  return (
    <Modal title={`Edit ${site.domain}`} onClose={onClose} foot={<>
      <button className="btn danger" onClick={() => confirm("Delete this site and its audits?") && run(() => api(`/sites/${site.id}`, { method: "DELETE" }), "Site deleted").then(() => { onDone(); onClose(); })}>Delete site</button>
      <span className="spacer" /><button className="btn primary" onClick={() => run(() => api(`/sites/${site.id}`, { method: "PATCH", body: f }), "Site saved").then(() => { onDone(); onClose(); })}>Save</button></>}>
      <div className="form">
        <label className="field">Search Console property<span className="hint">e.g. sc-domain:example.com or https://www.example.com/</span><input className="input" value={f.gsc_property} onChange={(e) => setF({ ...f, gsc_property: e.target.value })} /></label>
        <label className="field">GA4 property ID<input className="input" value={f.ga4_property} onChange={(e) => setF({ ...f, ga4_property: e.target.value })} /></label>
      </div>
    </Modal>
  );
}
