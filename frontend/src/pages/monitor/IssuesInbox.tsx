import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { human } from "../../lib/format";
import { useApi } from "../../lib/hooks";
import { filterText, IssueDrawer, IssueFilterBar, issueQuery, IssueTable, SevDot, SEVS, useProjects } from "./shared";

const KEYS = ["q", "project", "status", "severity", "team", "type"];

export default function IssuesInbox() {
  const [sp, setSp] = useSearchParams();
  const f: Record<string, string> = Object.fromEntries(KEYS.map((k) => [k, sp.get(k) ?? (k === "status" ? "active" : "")]));
  const setF = (n: Record<string, string>) => {
    const x = new URLSearchParams();
    for (const k of KEYS) if (n[k] || (k === "status" && n[k] === "")) x.set(k, n[k]);
    if (x.get("status") === "active") x.delete("status");
    if (sp.get("id")) x.set("id", sp.get("id")!);
    setSp(x, { replace: true });
  };
  const openId = sp.get("id");
  const setOpen = (id: string | null) => { const x = new URLSearchParams(sp); if (id) x.set("id", id); else x.delete("id"); setSp(x); };
  const { data: projects } = useProjects();
  const { data, error, loading, reload } = useApi<any[]>(issueQuery(f), [], { poll: 60000 });
  const rows = useMemo(() => filterText(data || [], f.q), [data, f.q]);
  const counts = useMemo(() => Object.fromEntries(SEVS.map((s) => [s, rows.filter((r) => r.severity === s).length])), [rows]);
  const filtered = Object.entries(f).some(([k, v]) => (k === "status" ? v !== "active" : !!v));

  return (
    <>
      <PageHead title="Issues" sub="Everything monitoring has found across your projects — routed to a team, tracked through tasks and verified after the fix." />
      <Panel pad={false} title={
        <span className="row" style={{ gap: 14 }}>
          <span>{rows.length} {rows.length === 1 ? "issue" : "issues"}</span>
          {SEVS.map((s) => counts[s] ? <span key={s} className="small muted" style={{ fontWeight: 400 }}><SevDot s={s} />{counts[s]} {human(s)}</span> : null)}
        </span>
      }>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--line-2)" }}>
          <IssueFilterBar f={f} setF={setF} projects={projects} showProject
            extra={filtered && <button className="btn ghost sm" onClick={() => setF({ status: "active" })}>Clear filters</button>} />
        </div>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !rows.length ? (
          <Empty title={filtered ? "No issues match these filters" : "No active issues"}>
            {filtered ? "Try widening the status or clearing filters." : "When monitoring finds a drop, a broken page or an opportunity, it appears here with its root cause and the team it was routed to."}
          </Empty>
        ) : <IssueTable issues={rows} onOpen={setOpen} showProject />}
      </Panel>
      {openId && <IssueDrawer id={openId} onClose={() => setOpen(null)} onChanged={reload} />}
    </>
  );
}
