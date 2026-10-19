import { FormEvent, ReactNode, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Logo } from "../components/Layout";
import { api, setSession } from "../lib/api";
import { useMe } from "../lib/hooks";

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-side">
        <div className="row" style={{ color: "#fff", fontWeight: 600, fontSize: 18 }}><Logo /> Genranq</div>
        <div>
          <h2>Audit, rank and report for every client from one place.</h2>
          <p style={{ marginTop: 14, maxWidth: "44ch" }}>62 automated audit checks, rank tracking, action plans your team can work through, and white-label reports.</p>
        </div>
        <p className="small" style={{ color: "#7b8aa6" }}>Each organisation’s data is isolated at the database level.</p>
      </div>
      <div className="auth-main"><div className="auth-card">{children}</div></div>
    </div>
  );
}

export function Login() {
  const nav = useNavigate();
  const loc = useLocation() as any;
  const { reload } = useMe();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgs, setOrgs] = useState<any[] | null>(null);
  const [org, setOrg] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const done = (t: any) => { setSession({ access_token: t.access_token, refresh_token: t.refresh_token }); reload(); nav(loc.state?.from || "/", { replace: true }); };
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(""); setBusy(true);
    try {
      if (challenge) { done(await api("/auth/mfa/verify", { body: { challenge, code } })); return; }
      const r: any = await api("/auth/login", { body: { email, password, organisation: org || undefined } });
      if (r.choose_organisation) { setOrgs(r.choose_organisation); setOrg(r.choose_organisation[0].slug); }
      else if (r.mfa_required) setChallenge(r.challenge);
      else done(r);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Shell>
      <h1 style={{ marginBottom: 6 }}>Sign in</h1>
      <p className="muted" style={{ marginBottom: 22 }}>New to Genranq? <Link to="/signup">Create an organisation</Link></p>
      <form className="form" onSubmit={submit}>
        {!challenge ? (<>
          <label className="field">Work email<input className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
          <label className="field">Password<input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          {orgs && <label className="field">Organisation<select className="input" value={org} onChange={(e) => setOrg(e.target.value)}>{orgs.map((o) => <option key={o.slug} value={o.slug}>{o.name}</option>)}</select></label>}
        </>) : (
          <label className="field">Authentication code<span className="hint">Enter the 6-digit code from your authenticator app.</span>
            <input className="input num" inputMode="numeric" autoFocus value={code} onChange={(e) => setCode(e.target.value)} /></label>
        )}
        {err && <div className="notice err">{err}</div>}
        <button className="btn primary" disabled={busy}>{challenge ? "Verify" : "Sign in"}</button>
      </form>
    </Shell>
  );
}

export function Signup() {
  const nav = useNavigate();
  const { reload } = useMe();
  const [f, setF] = useState({ organisation: "", name: "", email: "", password: "", plan: "essentials" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(""); setBusy(true);
    try { const t: any = await api("/auth/signup", { body: f }); setSession({ access_token: t.access_token, refresh_token: t.refresh_token }); reload(); nav("/"); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <Shell>
      <h1 style={{ marginBottom: 6 }}>Create your organisation</h1>
      <p className="muted" style={{ marginBottom: 22 }}>Already have an account? <Link to="/login">Sign in</Link></p>
      <form className="form" onSubmit={submit}>
        <label className="field">Organisation name<input className="input" required value={f.organisation} onChange={set("organisation")} /></label>
        <label className="field">Your name<input className="input" required value={f.name} onChange={set("name")} /></label>
        <label className="field">Work email<input className="input" type="email" required value={f.email} onChange={set("email")} /></label>
        <label className="field">Password<span className="hint">At least 10 characters.</span><input className="input" type="password" minLength={10} required value={f.password} onChange={set("password")} /></label>
        <label className="field">Plan<select className="input" value={f.plan} onChange={set("plan")}><option value="essentials">Essentials</option><option value="advanced">Advanced</option><option value="enterprise">Enterprise</option></select></label>
        {err && <div className="notice err">{err}</div>}
        <button className="btn primary" disabled={busy}>Create organisation</button>
      </form>
    </Shell>
  );
}

export function AcceptInvite() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const { reload } = useMe();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try { const t: any = await api("/auth/invites/accept", { body: { token: sp.get("token"), name, password } }); setSession({ access_token: t.access_token, refresh_token: t.refresh_token }); reload(); nav("/"); }
    catch (e: any) { setErr(e.message); }
  };
  return (
    <Shell>
      <h1 style={{ marginBottom: 18 }}>Join your team</h1>
      <form className="form" onSubmit={submit}>
        <label className="field">Your name<input className="input" required value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="field">Choose a password<input className="input" type="password" minLength={10} required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {err && <div className="notice err">{err}</div>}
        <button className="btn primary">Accept invite</button>
      </form>
    </Shell>
  );
}
