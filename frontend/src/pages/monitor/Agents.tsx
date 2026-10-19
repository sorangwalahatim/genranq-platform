import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Drawer, Empty, ErrorBox, Loading, NotConnected, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, fmtDateTime, human } from "../../lib/format";
import { useAction, useApi, useCan, useProjectSelection } from "../../lib/hooks";
import { Evidence, StatusBadge, useJobWatch, useProjects } from "./shared";
import { RunTable } from "./Workstreams";

const GROUP_ORDER = ["Strategy", "Keywords", "Technical", "Content", "Internal linking", "Link building", "GEO & AI", "Local & measurement", "Social"];
const EXECUTOR_LABEL: Record<string, string> = { data: "Runs on project data", llm: "Uses your AI provider", source: "Needs an external source", human: "Prepares work for a person" };

function stateKind(s: string) {
  if (s === "live") return "live";
  if (s.startsWith("live")) return "medium";
  if (s === "planned") return "planned";
  if (s === "prepares human task") return "pending";
  return "NOT_CONNECTED";
}
export function StateBadge({ s }: { s: string }) {
  return <Badge kind={stateKind(s)}>{s === "live" ? "Live" : s.charAt(0).toUpperCase() + s.slice(1)}</Badge>;
}

export default function Agents() {
  const { data: cat, error, loading } = useApi<any>("/agents/catalog");
  const { data: projects } = useProjects();
  const [pid, setPid] = useProjectSelection(projects);
  const [tab, setTab] = useState<"agents" | "seo" | "meta">("agents");
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [result, setResult] = useState<{ agent: any; run: any | null; pending: boolean } | null>(null);
  const can = useCan();
  const { run } = useAction();
  const watch = useJobWatch();
  const { data: runs, reload: reloadRuns } = useApi<any[]>(pid ? `/projects/${pid}/activity-runs` : null, [pid]);
  const { data: acts, reload: reloadActs } = useApi<any[]>(pid ? `/projects/${pid}/activities` : null, [pid]);
  const titles = Object.fromEntries((acts || []).map((a) => [a.id, a.title]));
  const byKey = Object.fromEntries((cat?.agents || []).map((a: any) => [a.key, a]));

  const match = (x: any) => (!q || `${x.name || ""} ${x.key} ${x.description}`.toLowerCase().includes(q.toLowerCase()))
    && (!state || (state === "live" ? x.state === "live" : state === "needs" ? x.state.startsWith("needs") || x.state.includes("(needs") : x.state === state));
  const groups = useMemo(() => {
    const g: Record<string, any[]> = {};
    for (const a of (cat?.agents || []).filter(match)) (g[a.group] ||= []).push(a);
    return Object.entries(g).sort((a, b) => (GROUP_ORDER.indexOf(a[0]) + 99) % 99 - (GROUP_ORDER.indexOf(b[0]) + 99) % 99);
  }, [cat, q, state]); // eslint-disable-line react-hooks/exhaustive-deps

  const runAgent = async (a: any) => {
    const r = await run<any>(() => api(`/projects/${pid}/agents/${a.key}/run`, { method: "POST" }), `${a.name} started`);
    if (!r) return;
    setResult({ agent: a, run: null, pending: true });
    watch(r.job_id, async () => {
      const list = await api<any[]>(`/projects/${pid}/activity-runs`).catch(() => []);
      const latest = list.find((x) => x.activity_id === r.activity_id) || null;
      setResult({ agent: a, run: latest, pending: false });
      reloadRuns(); reloadActs();
    }, a.name);
  };

  if (error) return <ErrorBox error={error} />;
  const live = (cat?.agents || []).filter((a: any) => a.state === "live").length;
  const projectName = projects?.find((p) => p.id === pid)?.name;
  return (
    <>
      <PageHead title="AI agents" sub="The agent catalog: what each one does, whether it can run today, and what it needs. Agent output always becomes a review task — nothing is published automatically.">
        {can("edit") && (projects?.length ? (
          <label className="row small" style={{ flexWrap: "nowrap" }}><span className="muted">Run on</span>
            <select className="input" style={{ width: "auto", minWidth: 180 }} value={pid} onChange={(e) => setPid(e.target.value)} aria-label="Project to run agents on">
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select></label>
        ) : <Link className="btn" to="/projects">Create a project to run agents</Link>)}
      </PageHead>
      {cat && !cat.ai_provider_connected && (
        <div style={{ marginBottom: 14 }}><NotConnected what="AI provider">— agents that write or review content need Claude, OpenAI, Gemini or Perplexity. <Link to="/integrations">Connect one in Integrations</Link>. Data agents run without it.</NotConnected></div>
      )}
      {loading && !cat ? <Loading /> : cat && (
        <>
          <div className="tabs" role="tablist" aria-label="Catalog">
            {([["agents", `Agents · ${cat.agents.length}`], ["seo", `SEO skills · ${cat.seo_skills.length}`], ["meta", `Meta Ads skills · ${cat.meta_skills.length}`]] as const).map(([k, l]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>
          <div className="row" style={{ marginBottom: 14, justifyContent: "space-between" }}>
            <div className="mon-filters">
              <input className="input" type="search" placeholder="Search agents and skills" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
              <select className="input" value={state} onChange={(e) => setState(e.target.value)} aria-label="State">
                <option value="">All states</option><option value="live">Live</option><option value="needs">Needs a connection or data</option>
                <option value="prepares human task">Prepares a human task</option><option value="planned">Planned</option>
              </select>
            </div>
            {tab === "agents" && <span className="small muted">{live} of {cat.agents.length} live now</span>}
          </div>

          {tab === "agents" && (!groups.length ? <Panel><Empty title="No agents match" /></Panel> : groups.map(([g, list]) => (
            <section key={g} style={{ marginBottom: 20 }} aria-labelledby={`g-${g}`}>
              <h2 id={`g-${g}`} style={{ marginBottom: 10 }}>{g} <span className="faint num" style={{ fontWeight: 400 }}>{list.length}</span></h2>
              <div className="mon-agents">
                {list.map((a: any) => (
                  <article key={a.key} className="mon-agent" aria-label={a.name}>
                    <div className="h"><b><span className="n">#{a.num}</span>{a.name}</b><StateBadge s={a.state} /></div>
                    <p>{a.description}</p>
                    <div className="f">
                      <span className="faint small">{EXECUTOR_LABEL[a.executor] || human(a.executor)}{a.needs ? ` · ${a.needs}` : ""}</span>
                      {a.runnable && can("edit") && pid
                        ? <button className="btn sm" onClick={() => runAgent(a)} title={`Run ${a.name} on ${projectName}`}>Run on project</button>
                        : a.state.startsWith("needs") && a.executor === "llm" ? <Link className="btn sm" to="/integrations">Connect AI</Link> : null}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )))}

          {tab === "seo" && (
            <Panel pad={false}>
              <div className="table-wrap"><table className="t"><thead><tr><th>Skill</th><th>What it does</th><th>Powered by</th><th>State</th><th /></tr></thead>
                <tbody>{cat.seo_skills.filter(match).map((s: any) => {
                  const ag = byKey[s.agent];
                  return (
                    <tr key={s.key}><td className="nowrap" style={{ fontWeight: 500 }}>{s.key}</td><td>{s.description}</td>
                      <td className="small">{ag ? ag.name : s.agent ? <span className="faint">{human(s.agent)}</span> : <span className="faint">—</span>}</td>
                      <td><StateBadge s={s.state} /></td>
                      <td className="r">{ag?.runnable && can("edit") && pid && <button className="btn sm" onClick={() => runAgent(ag)}>Run</button>}</td></tr>
                  );
                })}</tbody></table></div>
            </Panel>
          )}

          {tab === "meta" && (
            <>
              {cat.meta_skills.some((m: any) => m.state.startsWith("needs")) && (
                <div style={{ marginBottom: 14 }}><NotConnected what="Meta Ads API">— most Meta Ads skills read your ad account. <Link to="/integrations/meta">Connect Meta</Link>. Landing-page quality, the CPA/ROAS calculator and brand DNA work without it.</NotConnected></div>
              )}
              <Panel pad={false}>
                <div className="table-wrap"><table className="t"><thead><tr><th>Skill</th><th>What it does</th><th>State</th></tr></thead>
                  <tbody>{cat.meta_skills.filter(match).map((s: any) => (
                    <tr key={s.key}><td className="nowrap" style={{ fontWeight: 500 }}>{s.key}</td><td>{s.description}</td><td><StateBadge s={s.state} /></td></tr>
                  ))}</tbody></table></div>
              </Panel>
            </>
          )}

          {pid && (
            <div style={{ marginTop: 20 }}>
              <Panel title={<>Recent runs{projectName ? <span className="muted" style={{ fontWeight: 400 }}> · {projectName}</span> : null}</>} pad={false}
                actions={<Link className="small" to={`/projects/${pid}/workstreams`}>Workstreams</Link>}>
                {runs?.length ? <RunTable runs={runs.slice(0, 20)} titles={titles} pid={pid} /> : <Empty title="No runs on this project yet" />}
              </Panel>
            </div>
          )}
        </>
      )}

      {result && (
        <Drawer title={`${result.agent.name} · ${projectName || "project"}`} onClose={() => setResult(null)}>
          {result.pending ? <div className="notice" aria-live="polite">Running… the result appears here when the agent finishes.</div> : !result.run ? (
            <div className="notice warn">The run didn’t report back yet. It will appear under Recent runs when it finishes.</div>
          ) : (
            <>
              <div className="row" style={{ marginBottom: 10 }}><StatusBadge s={result.run.status} /><span className="faint small">{fmtDateTime(result.run.started_at)} · {ago(result.run.finished_at || result.run.started_at)}</span></div>
              {result.run.summary && <p style={{ marginBottom: 12 }}>{result.run.summary}</p>}
              {result.run.status === "NEEDS_CONNECTION" && <NotConnected what="This agent">needs a connection. <Link to="/integrations">Open Integrations</Link>. A task was created for the team meanwhile.</NotConnected>}
              {result.run.task_id && <p className="small" style={{ margin: "10px 0" }}>{result.run.status === "COMPLETED" ? "The output was attached to a review task for the team." : "A task was created for the team."} <Link to={`/tasks?project=${pid}`}>Open tasks</Link></p>}
              {result.run.output && Object.keys(result.run.output).length > 0 && <div className="mon-section"><h3>Output</h3><Evidence ev={result.run.output} /></div>}
            </>
          )}
        </Drawer>
      )}
    </>
  );
}
