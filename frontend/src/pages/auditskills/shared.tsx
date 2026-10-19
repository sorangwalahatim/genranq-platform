import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Empty, Panel, Select } from "../../components/ui";
import { useApi, useProjectSelection } from "../../lib/hooks";

type Project = { id: string; name: string };

export function useProject() {
  const [sp] = useSearchParams();
  const { data: projects, loading } = useApi<Project[]>("/projects");
  const [pid, setPid] = useProjectSelection(projects);
  useEffect(() => { const p = sp.get("project"); if (p) setPid(p); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const picker = <Select value={pid} onChange={setPid} label="Project" options={(projects || []).map((p) => ({ value: p.id, label: p.name }))} />;
  return { pid, setPid, projects, loading, picker };
}

export function NoProjects() {
  return <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>}>Audit skills run per project — pick a project to begin.</Empty></Panel>;
}

export const SEV_ORDER = ["critical", "high", "medium", "low", "info"];

export function sevLabel(s: string) {
  return { critical: "Critical", high: "High", medium: "Medium", low: "Low", info: "OK" }[s] || s;
}
