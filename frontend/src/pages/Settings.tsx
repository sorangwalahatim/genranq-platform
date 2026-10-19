import { useEffect, useState } from "react";
import { Badge, ErrorBox, PageHead, Panel } from "../components/ui";
import { api } from "../lib/api";
import { useAction, useApi, useCan, useMe } from "../lib/hooks";

export default function Settings() {
  const { me, reload } = useMe();
  const { data: org, error } = useApi<any>("/org");
  const { data: sso } = useApi<any>("/auth/sso");
  const can = useCan();
  const { run } = useAction();
  const [b, setB] = useState<any>({});
  useEffect(() => { if (org) setB({ name: org.name, ...org.branding }); }, [org]);
  const [mfa, setMfa] = useState<any>(null);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  return (
    <>
      <PageHead title="Settings" sub="Organisation branding, white-label reporting and your account security." />
      <ErrorBox error={error} />
      <div className="grid g2">
        <Panel title="Organisation & branding">
          <div className="form">
            <label className="field">Organisation name<input className="input" disabled={!can("manage_users")} value={b.name || ""} onChange={(e) => setB({ ...b, name: e.target.value })} /></label>
            <label className="field">Company name on reports<input className="input" disabled={!can("manage_users")} value={b.company_name || ""} onChange={(e) => setB({ ...b, company_name: e.target.value })} /></label>
            <label className="field">Logo URL<span className="hint">Report branding</span><input className="input" disabled={!can("manage_users")} value={b.logo_url || ""} onChange={(e) => setB({ ...b, logo_url: e.target.value })} /></label>
            <label className="field">Brand colour<div className="row"><input type="color" value={b.primary_color || "#1F3A5F"} disabled={!can("manage_users")} onChange={(e) => setB({ ...b, primary_color: e.target.value })} aria-label="Brand colour" /><code>{b.primary_color}</code></div></label>
            <label className="check"><input type="checkbox" disabled={!can("manage_users")} checked={!!b.white_label} onChange={(e) => setB({ ...b, white_label: e.target.checked })} /> White-label by default (Advanced plan and above)</label>
            <label className="field">Custom domain<span className="hint">Domain provisioning ships with roadmap phase 6.</span><input className="input" disabled value={b.custom_domain || ""} placeholder="reports.youragency.com" /></label>
            {can("manage_users") && <button className="btn primary" onClick={() => run(() => api("/org", { method: "PATCH", body: { name: b.name, branding: { company_name: b.company_name, logo_url: b.logo_url || undefined, primary_color: b.primary_color, white_label: b.white_label } } }), "Settings saved").then(reload)}>Save branding</button>}
          </div>
        </Panel>
        <div className="stack">
          <Panel title="Two-factor authentication">
            {me?.user.mfa_enabled ? <p><Badge kind="pass">On</Badge> Your account asks for an authenticator code at sign-in.</p> : !mfa ? (
              <><p className="muted" style={{ marginBottom: 12 }}>Protect your account with an authenticator app. Required to reveal stored credentials once enabled.</p>
                <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api("/auth/mfa/setup", { body: { password: pw } })).then((r) => { if (r) { setMfa(r); setPw(""); } }); }}>
                  <input className="input" style={{ flex: 1 }} type="password" placeholder="Confirm your password" value={pw} onChange={(e) => setPw(e.target.value)} aria-label="Password" />
                  <button className="btn primary" disabled={!pw}>Set up two-factor</button></form></>
            ) : (
              <div className="form">
                <p>Add this key to your authenticator app, then enter the 6-digit code it shows.</p>
                <pre className="code">{mfa.secret}</pre>
                <p className="faint small" style={{ wordBreak: "break-all" }}>{mfa.otpauth_uri}</p>
                <label className="field">Code<input className="input num" inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></label>
                <button className="btn primary" onClick={() => run(() => api("/auth/mfa/enable", { body: { code } }), "Two-factor enabled").then((r) => { if (r) { setMfa(null); reload(); } })}>Turn on</button>
              </div>
            )}
          </Panel>
          <Panel title="Single sign-on">
            <p><Badge kind={sso?.status}>{sso?.status === "CONNECTED" ? "Connected" : "Not connected"}</Badge> OIDC {sso?.issuer && `(${sso.issuer})`}</p>
            {sso?.note && <p className="muted small" style={{ marginTop: 6 }}>Your Genranq administrator can enable SSO for the platform.</p>}
          </Panel>
          <Panel title="Your account">
            <dl className="kv"><dt>Name</dt><dd>{me?.user.name}</dd><dt>Email</dt><dd>{me?.user.email}</dd><dt>Role</dt><dd>{me?.user.role}</dd><dt>Organisation</dt><dd>{me?.organisation.name} ({me?.organisation.slug})</dd></dl>
          </Panel>
        </div>
      </div>
    </>
  );
}
