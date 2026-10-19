import { useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useApi, useMe } from "../lib/hooks";
import { ago } from "../lib/format";

export type Mod = { num: number; area: string; name: string; description: string; priority: string; complexity: string; phase: number;
  status: "live" | "partial" | "planned"; route: string | null; slug: string; features: any[]; tables: string[]; integrations: string[];
  jobs: string[]; permission: string; gap: string | null };

const WORKSPACE: { to: string; label: string; mod?: string }[] = [
  { to: "/issues", label: "Issues inbox", mod: "monitoring" },
  { to: "/dashboards/executive", label: "Executive dashboard", mod: "monitoring" },
  { to: "/dashboards/team/seo", label: "Team dashboards", mod: "monitoring" },
  { to: "/workload", label: "Workload (people · teams · AI)", mod: "workstreams" },
  { to: "/agents", label: "AI agents", mod: "agents" },
  { to: "/ai/skills", label: "Analysis skills", mod: "agents" },
  { to: "/dashboards/executive", label: "Executive dashboard" },
  { to: "/issues", label: "Issues inbox" },
  { to: "/dashboards/team/seo", label: "Team dashboards" },
  { to: "/workload", label: "Workload" },
  { to: "/agents", label: "AI agents & skills" },
  { to: "/checklist", label: "Audit checklist" },
  { to: "/checklists", label: "Checklists" },
  { to: "/redirects", label: "Redirects" },
  { to: "/audit-skills", label: "Audit skills" },
  { to: "/error-center", label: "Error center" },
  { to: "/links-intel", label: "Links intel" },
  { to: "/reports", label: "📊 Reports" },
  { to: "/action-plans", label: "SEO action plans" },
  { to: "/billing", label: "Plan & usage" },
  { to: "/jobs", label: "Background jobs" },
  { to: "/activity", label: "Activity log" },
  { to: "/spec", label: "Product spec" },
  { to: "/settings", label: "Settings" },
];

export function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#2f5bea" />
      <path d="M8 22l5-6 4 3 7-9" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function Layout() {
  const { me, logout } = useMe();
  const { data: spec } = useApi<{ modules: Mod[] }>("/spec/modules");
  const { data: access } = useApi<{ modules: string[] | null }>("/access/me");
  const allowed = useMemo(() => (access?.modules ? new Set(access.modules) : null), [access]);
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem("genranq.nav") || "{}"); } catch { return {}; }
  });
  const toggle = (a: string) => {
    const n = { ...collapsed, [a]: !collapsed[a] };
    setCollapsed(n);
    try { localStorage.setItem("genranq.nav", JSON.stringify(n)); } catch { /* ignore */ }
  };
  const groups = useMemo(() => {
    const g: [string, Mod[]][] = [];
    for (const m of spec?.modules || []) {
      if (allowed && !allowed.has(`m${m.num}`)) continue;
      let e = g.find((x) => x[0] === m.area);
      if (!e) { e = [m.area, []]; g.push(e); }
      e[1].push(m);
    }
    return g;
  }, [spec, allowed]);

  return (
    <div className="shell">
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Main navigation">
        <div className="brand"><Logo /> Genranq</div>
        {me && <div className="org-chip"><b>{me.organisation.name}</b>{cap(me.organisation.plan)} plan · {cap(me.user.role)}</div>}
        <nav className="nav">
          {groups.map(([area, mods]) => {
            const live = mods.filter((m) => m.status !== "planned").length;
            return (
              <div className="nav-group" key={area}>
                <button onClick={() => toggle(area)} aria-expanded={!collapsed[area]}>
                  <span>{area}</span><span>{live}/{mods.length} {collapsed[area] ? "▸" : "▾"}</span>
                </button>
                {!collapsed[area] && mods.map((m) => (
                  <NavLink key={m.num} to={m.route || `/m/${m.num}`} end={m.route === "/"} className={m.status === "planned" ? "planned" : ""}
                    title={m.description}>
                    <span>{m.name}</span>
                    {m.status === "planned" && <span className="phase">P{m.phase}</span>}
                  </NavLink>
                ))}
              </div>
            );
          })}
          <div className="nav-group">
            <button onClick={() => toggle("_ws")}><span>Workspace</span><span>{collapsed._ws ? "▸" : "▾"}</span></button>
            {!collapsed._ws && WORKSPACE.filter((w) => !w.mod || !allowed || allowed.has(w.mod)).map((w) => <NavLink key={w.to} to={w.to}>{w.label}</NavLink>)}
          </div>
        </nav>
      </aside>
      {open && <div className="drawer-wrap" style={{ zIndex: 44 }} onClick={() => setOpen(false)} />}
      <div className="main">
        <header className="topbar">
          <button className="btn ghost sm menu-btn" onClick={() => setOpen(true)} aria-label="Open navigation">☰</button>
          <QuickJump mods={spec?.modules || []} />
          <div className="spacer" />
          <TimerChip />
          <Notifications />
          <UserMenu name={me?.user.name || me?.user.email || ""} onLogout={logout} />
        </header>
        <main className="content" id="content"><Outlet /></main>
      </div>
    </div>
  );
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function QuickJump({ mods }: { mods: Mod[] }) {
  const [q, setQ] = useState("");
  const [focus, setFocus] = useState(false);
  const nav = useNavigate();
  const items = useMemo(() => {
    const all = [...mods.map((m) => ({ label: m.name, sub: `${m.area}${m.status === "planned" ? ` · Phase ${m.phase}` : ""}`, to: m.route || `/m/${m.num}` })),
      ...WORKSPACE.map((w) => ({ label: w.label, sub: "Workspace", to: w.to }))];
    const s = q.toLowerCase().trim();
    return s ? all.filter((x) => (x.label + " " + x.sub).toLowerCase().includes(s)).slice(0, 8) : [];
  }, [q, mods]);
  return (
    <div className="search" style={{ position: "relative" }}>
      <input className="input" placeholder="Jump to a module…" value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => setFocus(true)}
        onBlur={() => setTimeout(() => setFocus(false), 150)} aria-label="Jump to a module"
        onKeyDown={(e) => { if (e.key === "Enter" && items[0]) { nav(items[0].to); setQ(""); } }} />
      {focus && items.length > 0 && (
        <div className="popover" style={{ left: 0, right: "auto", top: 40 }}>
          {items.map((i) => (
            <button key={i.to + i.label} className="btn ghost" style={{ width: "100%", justifyContent: "space-between", height: 38, borderRadius: 0 }}
              onMouseDown={() => { nav(i.to); setQ(""); }}>
              <span>{i.label}</span><span className="faint small">{i.sub}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function TimerChip() {
  const { data, reload } = useApi<any>("/time/running", [], { poll: 30000 });
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  useEffect(() => { const h = () => reload(); window.addEventListener("timer-changed", h); return () => window.removeEventListener("timer-changed", h); }, [reload]);
  if (!data) return null;
  const s = Math.floor((Date.now() - new Date(data.started_at).getTime()) / 1000);
  const t = `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  return (
    <div className="row">
      <Link to="/time" className="badge pass num" title={data.description || "Timer running"}>● {t}</Link>
      <button className="btn sm" onClick={async () => { await api("/time/stop", { method: "POST" }); reload(); window.dispatchEvent(new Event("timer-changed")); }}>Stop</button>
    </div>
  );
}

function Notifications() {
  const { data, reload } = useApi<any>("/notifications", [], { poll: 20000 });
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h); return () => document.removeEventListener("mousedown", h);
  }, []);
  const markAll = async () => { await api("/notifications/read", { body: {} }); reload(); };
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button className="btn ghost sm" onClick={() => setOpen(!open)} aria-label={`Notifications, ${data?.unread || 0} unread`}>
        🔔{data?.unread ? <span className="badge critical" style={{ marginLeft: 4 }}>{data.unread}</span> : null}
      </button>
      {open && (
        <div className="popover">
          <div className="panel-head"><h3>Notifications</h3><button className="btn ghost sm" onClick={markAll}>Mark all read</button></div>
          {!data?.items?.length && <div className="empty">You’re all caught up.</div>}
          {data?.items?.map((n: any) => (
            <Link key={n.id} to={n.link || "#"} onClick={() => setOpen(false)} style={{ display: "block", padding: "10px 16px", borderBottom: "1px solid var(--line-2)", color: "inherit", textDecoration: "none", background: n.read_at ? undefined : "var(--accent-soft)" }}>
              <div style={{ fontWeight: 500 }}>{n.title}</div>
              {n.body && <div className="muted small">{n.body}</div>}
              <div className="faint small">{ago(n.created_at)}</div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function UserMenu({ name, onLogout }: { name: string; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const setTheme = (t: string) => { document.documentElement.dataset.theme = t; try { localStorage.setItem("genranq.theme", t); } catch { /* ignore */ } };
  return (
    <div style={{ position: "relative" }}>
      <button className="btn sm" onClick={() => setOpen(!open)} aria-expanded={open}>{name || "Account"} ▾</button>
      {open && (
        <div className="popover" style={{ width: 220 }} onMouseLeave={() => setOpen(false)}>
          <Link to="/settings" className="btn ghost" style={{ width: "100%", borderRadius: 0 }} onClick={() => setOpen(false)}>Settings &amp; security</Link>
          <button className="btn ghost" style={{ width: "100%", borderRadius: 0 }} onClick={() => setTheme("light")}>Light theme</button>
          <button className="btn ghost" style={{ width: "100%", borderRadius: 0 }} onClick={() => setTheme("dark")}>Dark theme</button>
          <button className="btn ghost danger" style={{ width: "100%", borderRadius: 0 }} onClick={onLogout}>Sign out</button>
        </div>
      )}
    </div>
  );
}
