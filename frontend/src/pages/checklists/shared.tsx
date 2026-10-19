import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Empty, Panel, Select } from "../../components/ui";
import { useApi, useProjectSelection } from "../../lib/hooks";

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
  return <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>}>Checklists and redirect plans are kept per project.</Empty></Panel>;
}

const STATUS_KIND: Record<string, string> = { pending: "pending", pass: "live", fail: "fail", partial: "medium", done: "live", error: "fail" };
const STATUS_LABEL: Record<string, string> = { pending: "Not checked", pass: "Pass", fail: "Fail", partial: "Partial", done: "Done", error: "Error" };
export const StatusBadge = ({ s }: { s: string }) => <Badge kind={STATUS_KIND[s] || ""}>{STATUS_LABEL[s] || s}</Badge>;
export const statusLabel = (s: string) => STATUS_LABEL[s] || s;
