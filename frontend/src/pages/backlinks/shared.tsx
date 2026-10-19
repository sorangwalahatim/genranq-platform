import { ReactNode, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Empty, Panel, Select } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useProjectSelection } from "../../lib/hooks";
import "./backlinks.css";

export type Project = { id: string; name: string };

/** Shared project selector (persisted per viewer, overridable with ?project=). */
export function useProject() {
  const [sp] = useSearchParams();
  const { data: projects, loading } = useApi<Project[]>("/projects");
  const [pid, setPid] = useProjectSelection(projects);
  useEffect(() => { const p = sp.get("project"); if (p) setPid(p); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const picker = <Select value={pid} onChange={setPid} label="Project" options={(projects || []).map((p) => ({ value: p.id, label: p.name }))} />;
  return { pid, setPid, projects, loading, picker };
}

export function NoProjects() {
  return <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>}>Backlinks, keyword research and comparisons are kept per project.</Empty></Panel>;
}

const STATUS_KIND: Record<string, string> = { live: "live", removed: "fail", changed: "medium", noindex: "high", could_not_verify: "low", pending: "pending" };
const STATUS_LABEL: Record<string, string> = { live: "Live", removed: "Removed", changed: "Changed", noindex: "Not indexable", could_not_verify: "Couldn’t verify", pending: "Not checked" };
export const StatusBadge = ({ s }: { s: string }) => <Badge kind={STATUS_KIND[s] || ""}>{STATUS_LABEL[s] || s}</Badge>;
export const statusLabel = (s: string) => STATUS_LABEL[s] || s;

export function money(v: number | null | undefined, cur?: string | null) {
  if (v == null) return "—";
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur || "USD", maximumFractionDigits: 2 }).format(v); }
  catch { return `${v} ${cur || ""}`; }
}
export const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 1000) / 10}%`);

/** File-picker button that uploads to `path` with extra form fields. */
export function UploadButton({ path, fields = {}, label, accept = ".csv,.tsv,.txt", onDone, ok, disabled, primary }:
  { path: string; fields?: Record<string, string>; label: ReactNode; accept?: string; onDone?: (r: any) => void; ok?: string; disabled?: boolean; primary?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  const { run, busy } = useAction();
  const up = async (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    Object.entries(fields).forEach(([k, v]) => v != null && fd.append(k, v));
    const r = await run(() => api(path, { form: fd }), ok);
    if (ref.current) ref.current.value = "";
    if (r !== undefined) onDone?.(r);
  };
  return (<>
    <input ref={ref} type="file" accept={accept} hidden onChange={(e) => e.target.files?.[0] && up(e.target.files[0])} />
    <button className={`btn ${primary ? "primary" : ""}`} disabled={disabled || busy} onClick={() => ref.current?.click()}>{busy ? "Uploading…" : label}</button>
  </>);
}

/** Poll a job-backed record until it leaves QUEUED/RUNNING. */
export function usePolled<T = any>(path: string | null) {
  const r = useApi<T>(path, [path]);
  const st = (r.data as any)?.status;
  useEffect(() => {
    if (!path || !(st === "QUEUED" || st === "RUNNING")) return;
    const t = setInterval(() => r.reload(), 2500);
    return () => clearInterval(t);
  }, [path, st]); // eslint-disable-line react-hooks/exhaustive-deps
  return r;
}

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (v: string) => void }) {
  return <div className="tabs" role="tablist">{tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>{l}</button>)}</div>;
}

export function useTab(def: string) {
  const [t, setT] = useState(def);
  return [t, setT] as const;
}

export function Bar({ value, max, color = "var(--accent)" }: { value: number; max: number; color?: string }) {
  return <div className="bar-mini" style={{ minWidth: 60 }}><div style={{ width: `${max ? Math.min(100, (100 * value) / max) : 0}%`, background: color }} /></div>;
}
