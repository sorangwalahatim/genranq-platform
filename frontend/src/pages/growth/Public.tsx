import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Answers } from "./Onboarding";
import { TOOL_META, ToolResult } from "./toolviews";
import "./growth.css";

async function pub(path: string, body?: any) {
  const r = await fetch(`/api/public${path}`, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data: j };
}
const errMsg = (d: any) => (typeof d?.detail === "string" ? d.detail : d?.detail?.message) || d?.message || "Something went wrong";

// ------------------------------------------------------------------ /f/:token — hosted / embedded form
export function PublicForm() {
  const { token = "" } = useParams();
  const [sp] = useSearchParams();
  const embed = sp.get("embed") === "1";
  const [form, setForm] = useState<any>(null);
  const [err, setErr] = useState("");
  const [vals, setVals] = useState<Record<string, any>>({});
  const [answers, setAnswers] = useState<any>({});
  const [hp, setHp] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    pub(`/forms/${token}`).then((r) => {
      if (!r.ok) { setErr(r.status === 410 ? "This form is not accepting submissions." : "Form not found."); return; }
      setForm(r.data);
      const init: Record<string, any> = {};
      for (const f of r.data.schema.fields) if (f.type === "hidden") init[f.key] = sp.get(f.key) || "";
      setVals(init);
    });
  }, [token]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErrors({});
    const body: any = { ...vals, _gq_t: form.render_token, _gq_referrer: sp.get("ref") || document.referrer, _gq_page: sp.get("page") || window.location.href };
    if (form.honeypot_field) body[form.honeypot_field] = hp;
    if (form.questionnaire) body._gq_answers = answers;
    const r = await pub(`/forms/${token}/submit`, body);
    setBusy(false);
    if (r.ok && r.data.ok) { if (r.data.redirect_url) { (embed ? window.top! : window).location.href = r.data.redirect_url; return; } setDone(r.data.message); return; }
    setErrors(r.data.errors || {}); setErr(r.data.message || errMsg(r.data));
  };
  const color = form?.brand?.color || "var(--accent)";
  return (
    <main className={`gq-public ${embed ? "embed" : ""}`}>
      <div className="gq-public-card">
        {!embed && form && <div className="small muted" style={{ marginBottom: 4 }}>{form.brand?.name}</div>}
        {!embed && form && <h1>{form.name}</h1>}
        {!form && !err && <p className="muted">Loading…</p>}
        {done ? <div className="notice" role="status">{done}</div> : form && (
          <form className="form" onSubmit={submit} noValidate>
            {form.schema.fields.filter((f: any) => f.type !== "hidden").map((f: any) => (
              <label key={f.key} className={f.type === "checkbox" && !f.options?.length ? "check" : "field"}>
                {f.type === "checkbox" && !f.options?.length ? <><input type="checkbox" checked={!!vals[f.key]} onChange={(e) => setVals({ ...vals, [f.key]: e.target.checked })} /> {f.label}{f.required && " *"}</> : <>
                  {f.label}{f.required && " *"}{f.help && <span className="hint">{f.help}</span>}
                  {f.type === "textarea" ? <textarea className="input" rows={4} value={vals[f.key] || ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} placeholder={f.placeholder} />
                    : f.type === "select" ? <select className="input" value={vals[f.key] || ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })}><option value="">Choose…</option>{f.options.map((o: string) => <option key={o}>{o}</option>)}</select>
                    : f.type === "checkbox" ? <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>{f.options.map((o: string) => <label key={o} className="check"><input type="checkbox" checked={(vals[f.key] || []).includes(o)} onChange={(e) => setVals({ ...vals, [f.key]: e.target.checked ? [...(vals[f.key] || []), o] : (vals[f.key] || []).filter((x: string) => x !== o) })} /> {o}</label>)}</div>
                    : <input className="input" type={f.type === "email" ? "email" : f.type === "phone" ? "tel" : "text"} value={vals[f.key] || ""} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} placeholder={f.placeholder} autoComplete={f.type === "email" ? "email" : f.type === "phone" ? "tel" : undefined} />}
                </>}
                {errors[f.key] && <span className="small" style={{ color: "var(--critical)" }}>{errors[f.key]}</span>}
              </label>
            ))}
            {form.questionnaire && <><Answers questions={form.questionnaire.questions} value={answers} onChange={setAnswers} />
              {Object.entries(errors).filter(([k]) => k.startsWith("q_")).map(([k, v]) => <span key={k} className="small" style={{ color: "var(--critical)" }}>{k.slice(2)}: {v}</span>)}</>}
            {form.honeypot_field && <div className="gq-hp" aria-hidden="true"><label>Website<input tabIndex={-1} autoComplete="off" value={hp} onChange={(e) => setHp(e.target.value)} /></label></div>}
            {err && <div className="notice err">{err}</div>}
            <button className="btn primary" disabled={busy} style={{ background: color, borderColor: color }}>{busy ? "Sending…" : form.schema.submit_label}</button>
          </form>
        )}
        {!form && err && <div className="notice err">{err}</div>}
      </div>
    </main>
  );
}

// ------------------------------------------------------------------ /free and /free/:tool — public lead-gen tools
export function FreeToolsIndex() {
  const [sp] = useSearchParams();
  const key = sp.get("key");
  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => { pub(`/tools/info${key ? `?key=${encodeURIComponent(key)}` : ""}`).then((r) => r.ok ? setInfo(r.data) : setErr(errMsg(r.data))); }, [key]);
  return (
    <main className="gq-public"><div className="gq-public-card wide">
      <h1>Free SEO tools</h1>{info && <p className="muted">by {info.brand}</p>}
      {err && <div className="notice err">{err}</div>}
      <div className="grid g2" style={{ marginTop: 12 }}>{Object.entries(TOOL_META).map(([k, m]) => <Link key={k} to={`/free/${k}${key ? `?key=${encodeURIComponent(key)}` : ""}`} className="panel" style={{ padding: 14, textDecoration: "none", color: "inherit" }}>
        <b>{m.name}</b><p className="small muted" style={{ margin: "4px 0 0" }}>{m.blurb}</p></Link>)}</div>
    </div></main>
  );
}

export function FreeTool() {
  const { tool = "" } = useParams();
  const [sp] = useSearchParams();
  const key = sp.get("key");
  const meta = TOOL_META[tool];
  const [info, setInfo] = useState<any>(null);
  const [input, setInput] = useState("");
  const [target, setTarget] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [res, setRes] = useState<any>(null);
  const [monitor, setMonitor] = useState<any>(null);
  const manage = sp.get("manage");
  const q = key ? `?key=${encodeURIComponent(key)}` : "";
  useEffect(() => { pub(`/tools/info${q}`).then((r) => r.ok ? setInfo(r.data) : setErr(errMsg(r.data))); }, [q]);
  useEffect(() => { if (manage) pub(`/monitors/${manage}`).then((r) => r.ok ? setMonitor({ monitor: r.data, manage_token: manage }) : setErr("Monitor not found")); }, [manage]);
  if (!meta) return <main className="gq-public"><div className="gq-public-card"><h1>Unknown tool</h1><Link to={`/free${q}`}>All free tools</Link></div></main>;
  const utm = Object.fromEntries(["utm_source", "utm_medium", "utm_campaign"].filter((k) => sp.get(k)).map((k) => [k, sp.get(k)]));
  const run = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(""); setRes(null); setBusy(true);
    const ctx = { ...utm, referrer: document.referrer || undefined, page_url: window.location.href, email };
    const r = meta.input === "monitor" ? await pub(`/monitors${q}`, { ...ctx, source_url: input, target_url: target })
      : await pub(`/tools/${tool}${q}`, { ...ctx, [meta.input === "url" ? "url" : meta.input === "domain" ? "domain" : "keyword"]: input });
    setBusy(false);
    if (!r.ok || r.data.ok === false) { setErr(r.status === 429 ? "You’ve reached the free limit for now — please try again later." : errMsg(r.data)); return; }
    if (meta.input === "monitor") setMonitor(r.data); else setRes(r.data.result);
  };
  return (
    <main className="gq-public"><div className="gq-public-card wide">
      <div className="small muted"><Link to={`/free${q}`}>Free SEO tools</Link>{info && <> · by {info.brand}</>}</div>
      <h1>{meta.name}</h1><p className="muted">{meta.blurb}</p>
      {!monitor?.monitor && <form className="form" onSubmit={run}>
        <label className="field">{meta.input === "monitor" ? "Page that links to you" : meta.input === "url" ? "Page URL" : meta.input === "domain" ? "Domain" : "Keyword or niche"}
          <input className="input" value={input} onChange={(e) => setInput(e.target.value)} required placeholder={meta.input === "domain" ? "example.com" : meta.input === "keyword" ? "gardening" : "https://…"} /></label>
        {meta.input === "monitor" && <label className="field">Your URL it should link to<input className="input" value={target} onChange={(e) => setTarget(e.target.value)} required placeholder="https://yoursite.com/" /></label>}
        <label className="field">Your email<span className="hint">Required to see results. We’ll only use it to follow up about this tool.</span><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" /></label>
        <button className="btn primary" disabled={busy}>{busy ? "Checking…" : meta.input === "monitor" ? "Start monitoring" : "Check"}</button>
      </form>}
      {err && <div className="notice err" style={{ marginTop: 12 }}>{err}</div>}
      {res && <div style={{ marginTop: 16 }}><ToolResult tool={tool} r={res} /></div>}
      {monitor?.monitor && <MonitorStatus m={monitor.monitor} token={monitor.manage_token} />}
    </div></main>
  );
}

function MonitorStatus({ m, token }: { m: any; token: string }) {
  const [stopped, setStopped] = useState(m.status === "stopped");
  const link = `${window.location.origin}${window.location.pathname}?manage=${token}`;
  return (
    <div className="stack" style={{ marginTop: 12 }}>
      <div className={`notice ${m.last_status === "live" ? "" : "warn"}`}><div><b>Status: {m.last_status}</b>{m.last_rel ? ` (rel: ${m.last_rel})` : ""}<div className="small">{m.source_url} → {m.target_url}</div>
        <div className="small muted">Checked daily · next check {new Date(m.next_check_at).toLocaleString()} · email alerts: {m.email_alerts}</div></div></div>
      <p className="small">Bookmark your private status page: <a href={link}>{link}</a></p>
      {m.history?.length > 0 && <table className="t"><thead><tr><th>Checked</th><th>Status</th><th>HTTP</th><th>rel</th></tr></thead>
        <tbody>{m.history.map((h: any, i: number) => <tr key={i}><td className="small">{new Date(h.at).toLocaleString()}</td><td>{h.status}</td><td>{h.http ?? "—"}</td><td className="small">{h.rel || "—"}</td></tr>)}</tbody></table>}
      {!stopped ? <button className="btn" onClick={() => pub(`/monitors/${token}/stop`, {}).then(() => setStopped(true))}>Stop monitoring</button> : <p className="muted small">Monitoring stopped.</p>}
    </div>
  );
}
