import { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { Badge, NotConnected, Select } from "../../components/ui";
import { useApi, useProjectSelection } from "../../lib/hooks";
import "./growth.css";

export const money = (n?: number | null, cur = "USD") =>
  n == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: cur, maximumFractionDigits: 2 }).format(n);
export const num = (n?: number | null, d = 0) => (n == null ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: d }));

export function Tabs({ value, onChange, tabs }: { value: string; onChange: (v: string) => void; tabs: [string, ReactNode][] }) {
  return <div className="tabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>{l}</button>)}</div>;
}

export function useProjects() {
  const { data: projects } = useApi<any[]>("/projects");
  const [pid, setPid] = useProjectSelection(projects);
  const picker = <Select value={pid} onChange={setPid} label="Project" options={(projects || []).map((p) => ({ value: p.id, label: p.name }))} />;
  return { projects, pid, setPid, picker };
}

export function Kpi({ v, l, tone }: { v: ReactNode; l: ReactNode; tone?: string }) {
  return <div className="panel kpi"><div className="v" style={tone ? { color: `var(--${tone})` } : undefined}>{v}</div><div className="l">{l}</div></div>;
}

const STATUS_KIND: Record<string, string> = {
  new: "QUEUED", contacted: "pending", qualified: "medium", won: "pass", lost: "rejected",
  pending: "pending", accepted: "medium", in_progress: "RUNNING", delivered: "medium", completed: "COMPLETED", rejected: "rejected",
  disputed: "high", refunded: "low", held: "pending", released: "pass", paid: "pass", approved: "approved", pending_approval: "pending",
  applied: "pass", active: "live", paused: "low", stopped: "low", live: "live", missing: "fail", nofollow: "medium", error: "fail",
  canceled: "low", accepted_hook: "pass", not_connected: "NOT_CONNECTED", test: "QUEUED", ok: "pass",
};
export function Status({ s }: { s?: string | null }) {
  return <Badge kind={STATUS_KIND[s || ""] || s || ""}>{(s || "—").replace(/_/g, " ")}</Badge>;
}

export function NC({ what, provider, children }: { what: string; provider: string; children?: ReactNode }) {
  return <div style={{ marginBottom: 14 }}><NotConnected what={what}>{children} <Link to={`/integrations/${provider}`}>Connect {provider}</Link>.</NotConnected></div>;
}

export function LeadsNav() {
  return (
    <nav className="gq-subnav" aria-label="Leads sections">
      <NavLink to="/leads" end>Leads</NavLink><NavLink to="/forms" end>Forms</NavLink><NavLink to="/forms/connectors">Connectors</NavLink>
      <NavLink to="/attribution">Attribution</NavLink><NavLink to="/leads/hubspot">HubSpot</NavLink><NavLink to="/leads/calendly">Calendly</NavLink>
      <NavLink to="/onboarding">Profiling</NavLink>
    </nav>
  );
}

export function MarketNav() {
  return (
    <nav className="gq-subnav" aria-label="Marketplace sections">
      <NavLink to="/marketplace" end>Browse &amp; sell</NavLink><NavLink to="/marketplace/orders">Orders</NavLink>
      <NavLink to="/services">Services</NavLink><NavLink to="/wallet">Wallet</NavLink>
    </nav>
  );
}

export function Copy({ text, label = "Copy" }: { text: string; label?: string }) {
  return <button className="btn sm" type="button" onClick={() => navigator.clipboard?.writeText(text)}>{label}</button>;
}

export function Code({ children }: { children: string }) {
  return <pre className="gq-code">{children}</pre>;
}
