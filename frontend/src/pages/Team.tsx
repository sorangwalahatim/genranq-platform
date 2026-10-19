import { useState } from "react";
import { Badge, ErrorBox, Loading, Modal, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { ago, human } from "../lib/format";
import { useAction, useApi, useCan, useMe } from "../lib/hooks";
import AccessRights from "./AccessRights";

const ROLES = ["owner", "admin", "manager", "staff", "client"];

export default function Team() {
  const { data, error, loading, reload } = useApi<any>("/team");
  const { data: rb } = useApi<any>("/rbac/matrix");
  const can = useCan();
  const { me } = useMe();
  const { run } = useAction();
  const [inv, setInv] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  if (loading && !data) return <Loading />;
  const patch = (id: string, body: any, msg: string) => run(() => api(`/team/${id}`, { method: "PATCH", body }), msg).then(reload);
  return (
    <>
      <PageHead title="Team" sub="People in your organisation, their roles and what each role can do.">
        {can("manage_users") && <button className="btn primary" onClick={() => setInv(true)}>Invite someone</button>}
      </PageHead>
      <ErrorBox error={error} />
      <Panel pad={false}>
        <div className="table-wrap"><table className="t"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Department</th><th>MFA</th><th>Last sign-in</th><th>Status</th></tr></thead>
          <tbody>{data?.users.map((u: any) => (
            <tr key={u.id}><td>{u.name || "—"}{u.id === me?.user.id && <span className="faint"> (you)</span>}</td><td>{u.email}</td>
              <td>{can("manage_users") && u.id !== me?.user.id ? (
                <select className="input" style={{ width: 120 }} value={u.role} onChange={(e) => patch(u.id, { role: e.target.value }, "Role updated")} aria-label={`Role for ${u.email}`}>
                  {ROLES.map((r) => <option key={r} value={r}>{human(r)}</option>)}</select>) : human(u.role)}</td>
              <td>{can("manage_users") ? <input className="input" style={{ width: 140 }} defaultValue={u.department || ""} onBlur={(e) => e.target.value !== (u.department || "") && patch(u.id, { department: e.target.value }, "Department saved")} aria-label="Department" /> : u.department || "—"}</td>
              <td>{u.mfa_enabled ? <Badge kind="pass">On</Badge> : <Badge>Off</Badge>}</td><td className="muted">{ago(u.last_login_at)}</td>
              <td>{can("manage_users") && u.id !== me?.user.id ? <button className="btn sm" onClick={() => patch(u.id, { is_active: !u.is_active }, u.is_active ? "User deactivated" : "User reactivated")}>{u.is_active ? "Deactivate" : "Reactivate"}</button> : <Badge kind={u.is_active ? "pass" : "fail"}>{u.is_active ? "Active" : "Inactive"}</Badge>}</td></tr>
          ))}</tbody></table></div>
        {data?.invites.length > 0 && <div className="panel-body"><h3>Pending invites</h3>{data.invites.map((i: any) => <div key={i.id} className="row small" style={{ padding: "4px 0" }}>{i.email} <Badge>{human(i.role)}</Badge><span className="faint">expires {ago(i.expires_at).replace(" ago", "")}</span></div>)}</div>}
      </Panel>
      {can("manage_users") && <div style={{ marginTop: 16 }}><AccessRights /></div>}
      {rb && (
        <div style={{ marginTop: 16 }}><Panel title="Role permissions" pad={false}>
          <div className="table-wrap"><table className="t"><thead><tr><th>Permission</th>{rb.roles.map((r: string) => <th key={r}>{human(r)}</th>)}</tr></thead>
            <tbody>{Object.keys(rb.matrix.owner).map((p) => <tr key={p}><td>{human(p)}</td>{rb.roles.map((r: string) => <td key={r}>{rb.matrix[r][p] ? "✓" : <span className="faint">—</span>}</td>)}</tr>)}</tbody></table></div>
          <div className="panel-body small muted">Clients only see projects they are assigned to. Credential access additionally requires re-authentication.</div>
        </Panel></div>
      )}
      {inv && <Invite onClose={() => setInv(false)} onDone={(l) => { setLink(l); reload(); }} />}
      {link && (
        <Modal title="Invite created" onClose={() => setLink(null)}>
          <p style={{ marginBottom: 10 }}>Email delivery isn’t connected yet, so share this one-time link with the person directly. It expires in 7 days.</p>
          <pre className="code">{window.location.origin + link}</pre>
          <button className="btn" onClick={() => navigator.clipboard?.writeText(window.location.origin + link)}>Copy link</button>
        </Modal>
      )}
    </>
  );
}

function Invite({ onClose, onDone }: { onClose: () => void; onDone: (link: string) => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("staff");
  const { run, busy } = useAction();
  return (
    <Modal title="Invite someone" onClose={onClose} foot={<button className="btn primary" disabled={!email || busy} onClick={() => run(() => api<any>("/team/invites", { body: { email, role } })).then((r) => { if (r) { onDone(r.accept_url); onClose(); } })}>Create invite</button>}>
      <div className="form">
        <label className="field">Email<input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus /></label>
        <label className="field">Role<select className="input" value={role} onChange={(e) => setRole(e.target.value)}>{ROLES.map((r) => <option key={r} value={r}>{human(r)}</option>)}</select>
          <span className="hint">Client users need the Enterprise plan and only see projects you assign them to.</span></label>
      </div>
    </Modal>
  );
}
