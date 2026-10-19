import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { NoProjects, useProject } from "./shared";

type Skill = { key: string; name: string; desc: string; cost: string; needs: string };
type Run = any;

const UPLOAD_SKILLS = ["screaming_frog", "ahrefs", "semrush"];

export default function SkillsGrid() {
  const { pid, projects, picker } = useProject();
  const can = useCan();
  const { busy } = useAction();
  const skills = useApi<Skill[]>("/auditskills");
  const runs = useApi<Run[]>(pid ? `/auditskills/runs?project_id=${pid}` : null, [pid]);
  const [open, setOpen] = useState<string | null>(null);

  if (projects && !projects.length) return <><PageHead title="Audit Skills" /><NoProjects /></>;
  return (
    <>
      <PageHead title="Audit Skills" sub="7 ready-made checks. Pick one, press Run, and every problem found is saved with a plain-words fix.">
        {picker}
        <Link className="btn" to="/loading-time">Page loading times</Link>
      </PageHead>
      {skills.error ? <ErrorBox error={skills.error} /> : skills.loading ? <Loading /> : (
        <div className="cards" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 14, marginBottom: 22 }}>
          {skills.data?.map((s) => (
            <Panel key={s.key} title={s.name}>
              <p className="faint" style={{ minHeight: 60 }}>{s.desc}</p>
              <div className="row" style={{ gap: 8, margin: "8px 0" }}>
                <Badge>{s.cost}</Badge>
              </div>
              <div className="small faint" style={{ marginBottom: 10 }}>Needs: {s.needs}</div>
              {can("create") && <button className="btn primary" disabled={!pid || busy} onClick={() => setOpen(s.key)}>Run</button>}
            </Panel>
          ))}
        </div>
      )}
      <Panel title="Recent runs">
        {runs.loading ? <Loading /> : runs.error ? <ErrorBox error={runs.error} /> :
          !runs.data?.length ? <Empty title="No runs yet">Run a skill above — results appear here.</Empty> : (
          <table className="tbl"><thead><tr><th>Skill</th><th>When</th><th>Status</th><th>Problems found</th><th></th></tr></thead>
          <tbody>{runs.data.map((r: Run) => (
            <tr key={r.id}>
              <td>{r.skill_name}</td>
              <td className="small">{new Date(r.created_at).toLocaleString()}</td>
              <td><Badge kind={r.status === "completed" ? "pass" : r.status === "failed" ? "critical" : "low"}>{r.status}</Badge></td>
              <td className="r num">{Object.entries(r.finding_counts || {}).filter(([k]) => k !== "info").reduce((a: number, [, n]: any) => a + n, 0)}</td>
              <td className="r"><Link className="btn small" to={`/audit-skills/runs/${r.id}`}>Open</Link></td>
            </tr>))}</tbody></table>)}
      </Panel>
      {open && <RunModal skillKey={open} projectId={pid!} onClose={() => setOpen(null)} onDone={() => { runs.reload?.(); setOpen(null); }} />}
    </>
  );
}

function RunModal({ skillKey, projectId, onClose, onDone }: { skillKey: string; projectId: string; onClose: () => void; onDone: () => void }) {
  const { run, busy } = useAction();
  const [urls, setUrls] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [domain, setDomain] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<{ url: string; device: string; passed: boolean; notes: string }[]>([]);
  const devices = useApi<{ devices: string[] }>("/auditskills/devices");
  const [rUrl, setRUrl] = useState(""); const [rDev, setRDev] = useState(""); const [rPass, setRPass] = useState(true); const [rNotes, setRNotes] = useState("");

  const submit = () => run(async () => {
    if (UPLOAD_SKILLS.includes(skillKey)) {
      if (!file) throw new Error("Choose a CSV file first.");
      const form = new FormData();
      form.append("file", file);
      return api(`/auditskills/import/${skillKey}?project_id=${projectId}`, { form });
    }
    const body: any = { project_id: projectId };
    if (skillKey === "pagespeed") {
      body.urls = urls.split("\n").map((u) => u.trim()).filter(Boolean);
      body.api_key = apiKey.trim();
      body.strategies = ["mobile", "desktop"];
    } else if (skillKey === "schema") {
      body.urls = urls.split("\n").map((u) => u.trim()).filter(Boolean);
    } else if (skillKey === "crawler_files") {
      body.domain = domain.trim();
    } else if (skillKey === "responsive") {
      body.results = rows;
    }
    return api(`/auditskills/${skillKey}/run`, { body });
  }, "Skill finished — opening results…").then((r: any) => { if (r) { onDone(); window.location.href = `/audit-skills/runs/${r.id}`; } });

  return (
    <Modal title={`Run: ${skillKey.replace(/_/g, " ")}`} onClose={onClose} wide
      foot={<><button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={busy} onClick={submit}>{busy ? "Running…" : "Run now"}</button></>}>
      {skillKey === "pagespeed" && (<>
        <label>Page URLs (one per line, up to 10)<textarea rows={5} style={{ width: "100%" }} value={urls} onChange={(e) => setUrls(e.target.value)} placeholder="https://example.com/" /></label>
        <label style={{ display: "block", marginTop: 10 }}>Google PageSpeed API key
          <input type="password" style={{ width: "100%" }} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="Paste your free API key" /></label>
        <p className="small faint">Free key: Google Cloud Console → enable “PageSpeed Insights API” → create an API key. Without a key the run stops with a clear message — nothing is guessed.</p>
      </>)}
      {UPLOAD_SKILLS.includes(skillKey) && (<>
        <label>CSV export file<input type="file" accept=".csv" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label>
        <p className="small faint">{skillKey === "screaming_frog"
          ? "In Screaming Frog: crawl, then export the Internal tab (or Bulk Export) as CSV."
          : "Export your site audit as CSV. Columns accepted: url, issue, severity."}</p>
      </>)}
      {skillKey === "schema" && (<>
        <label>Page URLs (one per line, up to 10)<textarea rows={5} style={{ width: "100%" }} value={urls} onChange={(e) => setUrls(e.target.value)} placeholder="https://example.com/product/widget" /></label>
        <p className="small faint">Each page is opened for real; the page type (product, blog, FAQ, service…) is detected and the matching Google schema is checked.</p>
      </>)}
      {skillKey === "crawler_files" && (<>
        <label>Domain<input style={{ width: "100%" }} value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="example.com" /></label>
        <p className="small faint">Checks /robots.txt, /sitemap.xml and /llms.txt on the live domain.</p>
      </>)}
      {skillKey === "responsive" && (<>
        <p className="small faint">Add the results of your real device / browser testing below. Nothing is guessed — enter what you actually saw.</p>
        <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
          <input placeholder="Page URL" style={{ flex: 2 }} value={rUrl} onChange={(e) => setRUrl(e.target.value)} />
          <select value={rDev} onChange={(e) => setRDev(e.target.value)} style={{ flex: 1 }}>
            <option value="">Device…</option>{devices.data?.devices?.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <select value={rPass ? "1" : "0"} onChange={(e) => setRPass(e.target.value === "1")}>
            <option value="1">Looks fine</option><option value="0">Has a problem</option>
          </select>
          <input placeholder="What did you see? (optional)" style={{ flex: 2 }} value={rNotes} onChange={(e) => setRNotes(e.target.value)} />
          <button className="btn" disabled={!rUrl.trim() || !rDev} onClick={() => { setRows([...rows, { url: rUrl.trim(), device: rDev, passed: rPass, notes: rNotes.trim() }]); setRUrl(""); setRDev(""); setRNotes(""); }}>Add</button>
        </div>
        {rows.length > 0 && <table className="tbl"><thead><tr><th>Page</th><th>Device</th><th>Result</th><th></th></tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}><td className="small">{r.url}</td><td className="small">{r.device}</td>
            <td><Badge kind={r.passed ? "pass" : "critical"}>{r.passed ? "OK" : "Problem"}</Badge></td>
            <td className="r"><button className="btn small" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button></td></tr>)}</tbody></table>}
      </>)}
    </Modal>
  );
}
