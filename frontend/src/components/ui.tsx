import { ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../lib/api";
import { human, scoreColor, sevColor, SEV } from "../lib/format";

export function PageHead({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      {children && <div className="actions">{children}</div>}
    </div>
  );
}

export function Badge({ kind, children }: { kind?: string | null; children?: ReactNode }) {
  return <span className={`badge ${kind || ""}`}>{children ?? human(kind)}</span>;
}

export function Panel({ title, actions, children, pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; pad?: boolean }) {
  return (
    <section className="panel">
      {(title || actions) && <div className="panel-head"><h2>{title}</h2>{actions && <div className="row">{actions}</div>}</div>}
      {pad ? <div className="panel-body">{children}</div> : children}
    </section>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{children && <p>{children}</p>}{action && <div style={{ marginTop: 14 }}>{action}</div>}</div>;
}

export function Loading() { return <div className="empty" aria-busy="true">Loading…</div>; }

export function ErrorBox({ error }: { error: ApiError | Error | null }) {
  if (!error) return null;
  const e = error as ApiError;
  if (e.code === "plan_upgrade_required" || e.code === "quota_exceeded")
    return <div className="notice warn"><div><b>{e.message}</b><div className="small">Upgrade or adjust your plan in <Link to="/billing">Plan &amp; usage</Link>.</div></div></div>;
  return <div className="notice err">{e.message}</div>;
}

export function NotConnected({ what, children }: { what: string; children?: ReactNode }) {
  return (
    <div className="notice nc">
      <Badge kind="NOT_CONNECTED">Not connected</Badge>
      <div><b>{what}</b> {children || <>isn’t connected yet. <Link to="/integrations">Connect it in Integrations</Link>.</>}</div>
    </div>
  );
}

export function Modal({ title, onClose, children, foot, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined}>
        <div className="modal-head"><h2>{title}</h2><button className="btn ghost sm" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children, foot }: { title: ReactNode; onClose: () => void; children: ReactNode; foot?: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="drawer-wrap" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="modal-head"><h2 style={{ paddingRight: 12 }}>{title}</h2><button className="btn ghost sm" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="modal-body" style={{ flex: 1 }}>{children}</div>
        {foot && <div className="modal-foot">{foot}</div>}
      </aside>
    </div>
  );
}

export function ScoreRing({ score, size = 148, label = "of 100" }: { score: number | null | undefined; size?: number; label?: string }) {
  const r = (size - 14) / 2, c = 2 * Math.PI * r;
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce || score == null) { setShown(score ?? 0); return; }
    let raf = 0; const t0 = performance.now();
    const step = (t: number) => { const k = Math.min(1, (t - t0) / 700); setShown((score ?? 0) * (1 - Math.pow(1 - k, 3))); if (k < 1) raf = requestAnimationFrame(step); };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [score]);
  return (
    <div className="score-ring" style={{ width: size, height: size }} role="img" aria-label={`SEO score ${score ?? "not available"}`}>
      <svg width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line-2)" strokeWidth="10" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={scoreColor(score)} strokeWidth="10" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - shown / 100)} />
      </svg>
      <div className="val"><b style={{ color: scoreColor(score) }}>{score == null ? "—" : Math.round(shown)}</b><span>{label}</span></div>
    </div>
  );
}

export function SeverityStrip({ issues }: { issues: Record<string, number> }) {
  const total = SEV.reduce((a, s) => a + (issues?.[s] || 0), 0);
  return (
    <>
      <div className="sev-strip" aria-hidden>
        {SEV.map((s) => (issues?.[s] ? <div key={s} style={{ width: `${(100 * issues[s]) / total}%`, background: sevColor[s] }} /> : null))}
      </div>
      <div className="sev-legend">
        {SEV.map((s) => (
          <div key={s}><b className="num" style={{ color: sevColor[s] }}>{issues?.[s] || 0}</b><span className="muted">{human(s)}</span></div>
        ))}
      </div>
    </>
  );
}

export function CatBars({ scores }: { scores: Record<string, number> }) {
  const items = Object.entries(scores || {}).sort((a, b) => a[1] - b[1]);
  if (!items.length) return <p className="muted">No category scores yet.</p>;
  return (
    <div>
      {items.map(([c, v]) => (
        <div className="catbar" key={c}>
          <span>{c}</span>
          <div className="track"><div className="fill" style={{ width: `${v}%`, background: scoreColor(v) }} /></div>
          <span className="num" style={{ textAlign: "right" }}>{Math.round(v)}</span>
        </div>
      ))}
    </div>
  );
}

export function Meter({ used, limit }: { used: number; limit: number | null }) {
  if (limit == null) return <span className="muted small">No limit</span>;
  const pct = limit ? Math.min(100, (100 * used) / limit) : 100;
  return <div className={`bar-mini ${pct >= 90 ? "meter-over" : ""}`} title={`${used} of ${limit}`}><div style={{ width: `${pct}%` }} /></div>;
}

export function Select({ value, onChange, options, label, placeholder }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; label?: string; placeholder?: string }) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} style={{ width: "auto", minWidth: 160 }}>
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
}
