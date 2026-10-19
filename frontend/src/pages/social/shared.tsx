import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge, NotConnected, Select } from "../../components/ui";
import { human } from "../../lib/format";
import { useApi, useProjectSelection } from "../../lib/hooks";
import "./social.css";

export const CHANNELS = ["facebook", "instagram", "linkedin", "x"] as const;
export const CHANNEL_LABEL: Record<string, string> = { facebook: "Facebook", instagram: "Instagram", linkedin: "LinkedIn", x: "X", gbp: "Google Business" };
export const CHANNEL_PROVIDER: Record<string, string> = { facebook: "meta", instagram: "meta", linkedin: "linkedin", x: "x" };
export const PROVIDER_LABEL: Record<string, string> = { meta: "Meta (Facebook / Instagram)", linkedin: "LinkedIn", x: "X (Twitter)" };
export const STATES = ["draft", "generated", "review", "approved", "scheduled", "published"] as const;
export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function statusKind(s?: string | null) {
  switch (s) {
    case "published": case "PUBLISHED": case "sent": case "delivered": case "verified": case "approved": case "ready": case "active": case "CONNECTED": return "pass";
    case "scheduled": case "QUEUED": case "queued": case "review": case "pending": case "running": case "partial": case "SENDING": case "publishing": case "generated": return "medium";
    case "failed": case "FAILED": case "error": case "rejected": return "critical";
    case "not_connected": case "NOT_CONNECTED": case "NOT CONNECTED": case "cancelled": case "CANCELLED": case "disabled": case "draft": return "low";
    default: return "";
  }
}
export const StatusBadge = ({ s, children }: { s?: string | null; children?: ReactNode }) =>
  <Badge kind={statusKind(s)}>{children ?? (s === "not_connected" || s === "NOT_CONNECTED" ? "Not connected" : human(s))}</Badge>;

export function ChannelTag({ ch }: { ch?: string | null }) {
  if (!ch) return <span className="faint">—</span>;
  return <span className={`ch-tag ch-${ch}`}>{CHANNEL_LABEL[ch] || ch}</span>;
}

/** Project list + persisted selection. allowAll adds an "All projects" option (value ""). */
export function useProjects(allowAll = false) {
  const { data: projects } = useApi<any[]>("/projects");
  const [pid, setPid] = useProjectSelection(allowAll ? null : projects);
  return { projects: projects || [], pid, setPid, loaded: !!projects };
}

export function ProjectPicker({ projects, pid, setPid, allowAll }: { projects: any[]; pid: string; setPid: (v: string) => void; allowAll?: boolean }) {
  return <Select value={pid} onChange={setPid} label="Project" placeholder={allowAll ? "All projects" : undefined}
    options={projects.map((p) => ({ value: p.id, label: p.name }))} />;
}

export function ProviderBanners({ providers, only }: { providers: { provider: string; status: string }[] | undefined; only?: string[] }) {
  const missing = (providers || []).filter((p) => p.status !== "CONNECTED" && (!only || only.includes(p.provider)));
  if (!missing.length) return null;
  return (
    <div className="stack" style={{ gap: 8, marginBottom: 14 }}>
      {missing.map((p) => (
        <NotConnected key={p.provider} what={PROVIDER_LABEL[p.provider] || p.provider}>
          is not connected — posts to its accounts will fail with NOT CONNECTED and are never marked published.{" "}
          <Link to={`/integrations/${p.provider}`}>Connect {PROVIDER_LABEL[p.provider] || p.provider}</Link>.
        </NotConnected>
      ))}
    </div>
  );
}

export const toLocalInput = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
export const fromLocalInput = (s: string) => new Date(s).toISOString();
export const browserTz = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } };
