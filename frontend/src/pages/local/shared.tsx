import { ReactNode, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, NotConnected, Panel, Select } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { useAction, useApi, useProjectSelection, useToast } from "../../lib/hooks";
import "./local.css";

export type Loc = {
  id: string; project_id: string; name: string; kind: "own" | "competitor"; place_id?: string | null; nap: Record<string, any>;
  primary_category?: string | null; secondary_categories: string[]; hours: Record<string, string[][]>; description?: string | null;
  photos_count: number; has_logo: boolean; services: string[]; products: string[]; attributes: string[]; qa_questions: number; qa_answered: number;
  social_links: string[]; citations: any[]; keywords: string[]; rating: number | null; review_count: number | null; posts_per_month: number | null;
  website?: string | null; lat?: number | null; lng?: number | null; source: string; gbp_resource?: string | null; reviews_recorded?: number;
};

export type Conn = { status: string; connected: boolean; link: string; name: string };
export type Status = { gbp: Conn; google_maps: Conn; ai: Conn };

/** Project + own-location selection shared by every Local SEO screen. */
export function useLocal(kind: "own" | "competitor" | null = "own") {
  const { data: projects } = useApi<any[]>("/projects");
  const [pid, setPid] = useProjectSelection(projects);
  const { data: status } = useApi<Status>("/local/status");
  const locs = useApi<Loc[]>(pid ? `/local/locations?project_id=${pid}${kind ? `&kind=${kind}` : ""}` : null, [pid, kind]);
  const [lid, setLid] = useState("");
  useEffect(() => {
    const list = locs.data || [];
    if (list.length && !list.find((l) => l.id === lid)) setLid(list[0].id);
    if (!list.length && lid) setLid("");
  }, [locs.data]);
  const loc = (locs.data || []).find((l) => l.id === lid) || null;
  return { projects, pid, setPid, status, locs, lid, setLid, loc };
}

export function ProjectPick({ projects, pid, setPid }: { projects: any[] | null; pid: string; setPid: (v: string) => void }) {
  return <Select value={pid} onChange={setPid} label="Project" options={(projects || []).map((p) => ({ value: p.id, label: p.name }))} />;
}

export function LocationPick({ locs, lid, setLid, label = "Location" }: { locs: Loc[] | null; lid: string; setLid: (v: string) => void; label?: string }) {
  if (!locs?.length) return null;
  return <Select value={lid} onChange={setLid} label={label} options={locs.map((l) => ({ value: l.id, label: l.name + (l.nap?.city ? ` · ${l.nap.city}` : "") }))} />;
}

export function NcBanner({ conn, provider, children }: { conn?: Conn; provider: "gbp" | "google_maps" | "ai"; children: ReactNode }) {
  if (!conn || conn.connected) return null;
  const link = provider === "ai" ? "/integrations" : `/integrations/${provider}`;
  return (
    <div style={{ marginBottom: 14 }}>
      <NotConnected what={conn.name}>is <b>NOT CONNECTED</b>. {children} <Link to={link}>Connect {conn.name}</Link>.</NotConnected>
    </div>
  );
}

export function NoProject({ projects }: { projects: any[] | null }) {
  if (projects && !projects.length)
    return <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>}>Local SEO data is organised per project.</Empty></Panel>;
  return null;
}

export function NoLocation() {
  return <Panel><Empty title="No business location yet" action={<Link className="btn primary" to="/local/gbp-audit">Add a location</Link>}>
    Add your Google Business Profile location manually, import a CSV, or sync it from Google Business Profile.</Empty></Panel>;
}

/** File button that uploads a CSV to `path` and reports row errors. */
export function CsvButton({ path, label, onDone, extra, hint }: { path: string; label: string; onDone?: (r: any) => void; extra?: Record<string, string>; hint?: string }) {
  const ref = useRef<HTMLInputElement>(null);
  const { run, busy } = useAction();
  const toast = useToast();
  const go = async (f: File) => {
    const fd = new FormData(); fd.append("file", f);
    Object.entries(extra || {}).forEach(([k, v]) => v && fd.append(k, v));
    const r = await run<any>(() => api(path, { form: fd }));
    if (r) {
      const errs = r.errors?.length ? ` · ${r.errors.length} row error(s): ${r.errors.slice(0, 3).map((e: any) => `row ${e.row}: ${e.error}`).join("; ")}` : "";
      toast(`${r.created ?? r.updated ?? 0} added${r.updated && r.created !== undefined ? `, ${r.updated} updated` : ""}${errs}`);
      onDone?.(r);
    }
    if (ref.current) ref.current.value = "";
  };
  return (<>
    <input ref={ref} type="file" accept=".csv,text/csv" hidden onChange={(e) => e.target.files?.[0] && go(e.target.files[0])} />
    <button className="btn" title={hint} disabled={busy} onClick={() => ref.current?.click()}>{busy ? "Importing…" : label}</button>
  </>);
}
export function isNC(e: any): e is ApiError { return e instanceof ApiError && e.code === "not_connected"; }

export function StatusBadge({ s }: { s: string }) {
  const kind = s === "published" ? "pass" : s === "approved" ? "approved" : s === "review" ? "pending" : s === "rejected" ? "rejected" : s === "publishing" ? "RUNNING" : "low";
  return <Badge kind={kind}>{s}</Badge>;
}

export const fieldStatusKind = (s: string) => (s === "match" || s === "pass" ? "pass" : s === "partial" ? "partial" : s === "missing" ? "na" : "fail");

export function Stars({ n }: { n: number | null | undefined }) {
  if (n == null) return <span className="faint">—</span>;
  return <span className="lc-stars" aria-label={`${n} stars`}>{"★".repeat(Math.round(n))}<span className="faint">{"★".repeat(5 - Math.round(n))}</span></span>;
}

export function Kpi({ v, l }: { v: ReactNode; l: string }) {
  return <div className="panel kpi"><div className="v">{v ?? "—"}</div><div className="l">{l}</div></div>;
}

export function copy(text: string) { try { navigator.clipboard.writeText(text); } catch { /* clipboard unavailable */ } }
