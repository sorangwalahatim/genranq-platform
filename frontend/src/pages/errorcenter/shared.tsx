import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Empty, Panel, Select } from "../../components/ui";
import { useApi, useProjectSelection } from "../../lib/hooks";

export type Project = { id: string; name: string };

export function useProject() {
  const [sp] = useSearchParams();
  const { data: projects, loading } = useApi<Project[]>("/projects");
  const [pid, setPid] = useProjectSelection(projects);
  useEffect(() => { const p = sp.get("project"); if (p) setPid(p); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const picker = <Select value={pid} onChange={setPid} label="Project" options={(projects || []).map((p) => ({ value: p.id, label: p.name }))} />;
  return { pid, projects, loading, picker };
}

export function NoProjects() {
  return <Panel><Empty title="Create a project first" action={<Link className="btn primary" to="/projects">Go to projects</Link>}>The Error Center keeps one error inbox per project.</Empty></Panel>;
}

export const SOURCE_LABEL: Record<string, string> = {
  gsc: "Search Console",
  pagespeed: "PageSpeed",
  screaming_frog: "Screaming Frog",
  ahrefs: "Ahrefs",
  semrush: "Semrush",
  audit: "Site audit",
};
export const sourceLabel = (s: string) => SOURCE_LABEL[s] || s;

const SEV_KIND: Record<string, string> = { critical: "critical", warning: "high", info: "low" };
export const SevBadge = ({ s }: { s: string }) => <Badge kind={SEV_KIND[s] || "low"}>{s === "critical" ? "Urgent" : s === "warning" ? "Warning" : "Info"}</Badge>;

const STATUS_LABEL: Record<string, string> = { open: "Open", in_progress: "In progress", fixed: "Fixed", verified: "Verified", ignored: "Ignored" };
const STATUS_KIND: Record<string, string> = { open: "critical", in_progress: "medium", fixed: "pass", verified: "live", ignored: "low" };
export const StatusBadge = ({ s }: { s: string }) => <Badge kind={STATUS_KIND[s] || ""}>{STATUS_LABEL[s] || s}</Badge>;

export const BucketBadge = ({ b }: { b: string }) => <Badge kind={b === "developer" ? "high" : "medium"}>{b === "developer" ? "Developer" : "SEO team"}</Badge>;
