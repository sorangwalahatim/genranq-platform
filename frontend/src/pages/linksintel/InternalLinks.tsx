import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PageHead, Panel, Badge, Loading, ErrorBox, Empty } from "../../components/ui";
import { api } from "../../lib/api";
import { useApi, useAction } from "../../lib/hooks";
import { useProject, NoProjects, Tabs } from "../backlinks/shared";
import "../backlinks/backlinks.css";

type LinkRow = {
  site_label: string; source_url: string; anchor_text: string; target_url: string; target_type: string;
};
type InternalData = {
  sites: string[]; rows: LinkRow[];
  inbound_counts: Record<string, Record<string, number>>;
  orphans: Record<string, string[]>;
};

const TYPE_LABEL: Record<string, string> = { service: "Service page", blog: "Blog", product: "Product", other: "Other page" };
const TYPE_KIND: Record<string, string> = { service: "live", blog: "medium", product: "high", other: "" };
const siteName = (s: string) => (s === "ours" ? "Your site" : s);

function ImportPanel({ pid, onDone }: { pid: string; onDone: () => void }) {
  const { run, busy } = useAction();
  const ref = useRef<HTMLInputElement>(null);
  const [site, setSite] = useState("ours");
  const [custom, setCustom] = useState("");
  const pick = async (f: File) => {
    const text = await f.text();
    const site_label = site === "ours" ? "ours" : custom.trim();
    if (!site_label) return;
    const r = await run(() => api("/linksintel/internal/import", {
      body: { project_id: pid, site_label, csv: text },
    }), "Internal links imported");
    if (ref.current) ref.current.value = "";
    if (r !== undefined) onDone();
  };
  return (
    <Panel title="Import internal links">
      <div className="bl-toolbar" style={{ border: 0, padding: "0 0 12px" }}>
        <label>Site:&nbsp;
          <select value={site} onChange={(e) => setSite(e.target.value)}>
            <option value="ours">Your site</option>
            <option value="competitor">Competitor</option>
          </select>
        </label>
        {site === "competitor" && (
          <input placeholder="competitor domain, e.g. rival.com" value={custom} onChange={(e) => setCustom(e.target.value)} style={{ minWidth: 220 }} />
        )}
        <input ref={ref} type="file" accept=".csv,.txt" hidden onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
        <button className="btn primary" disabled={busy} onClick={() => ref.current?.click()}>
          {busy ? "Importing…" : "Choose CSV file"}
        </button>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--muted)" }}>
        CSV columns: <b>source_url</b>, <b>anchor_text</b>, <b>target_url</b>. Uploading again replaces this site’s previous import.
      </div>
    </Panel>
  );
}

export default function InternalLinks() {
  const { pid, picker } = useProject();
  const [tab, setTab] = useState("ours");
  const [pages, setPages] = useState("");
  const [pagesSent, setPagesSent] = useState("");
  const q = pid
    ? `/linksintel/internal?project_id=${pid}` + (pagesSent ? `&all_pages=${encodeURIComponent(pagesSent)}` : "")
    : null;
  const { data, error, loading, reload } = useApi<InternalData>(q, [q]);
  const sites = data?.sites?.length ? data.sites : ["ours"];
  const active = sites.includes(tab) ? tab : sites[0];
  const rows = (data?.rows || []).filter((r) => r.site_label === active);
  const orphans = data?.orphans?.[active] || [];
  return (
    <div className="stack">
      <PageHead title="Links Intel" sub="Which page links to which — with the exact linking word. Your site always first.">
        {picker}
      </PageHead>
      <div className="tabs" role="tablist">
        <Link role="tab" aria-selected="false" to="/links-intel">Anchor analysis</Link>
        <Link role="tab" aria-selected="true" className="on" to="/links-intel/internal">Internal links</Link>
      </div>
      {!pid ? <NoProjects /> : (
        <>
          <ImportPanel pid={pid} onDone={() => { setPagesSent(""); setPages(""); reload(); }} />
          {loading && <Loading />}
          {error && <ErrorBox error={error} />}
          {data && !data.rows.length && (
            <Empty title="No internal-link data yet">Import an internal-links CSV above — for your site and each competitor — to see the link map.</Empty>
          )}
          {data && !!data.rows.length && (
            <>
              <Tabs tabs={sites.map((s) => [s, siteName(s)] as [string, string])} value={active} onChange={setTab} />
              <Panel title={`Link map — ${siteName(active)}`}>
                <div style={{ overflowX: "auto" }}>
                  <table className="bl-cmp">
                    <thead>
                      <tr><th>From page</th><th>Linking word</th><th>To page</th><th>Page type</th><th>Links to it</th></tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <tr key={i}>
                          <td style={{ wordBreak: "break-all" }}>{r.source_url}</td>
                          <td><b>“{r.anchor_text}”</b></td>
                          <td style={{ wordBreak: "break-all" }}>{r.target_url}</td>
                          <td><Badge kind={TYPE_KIND[r.target_type]}>{TYPE_LABEL[r.target_type] || r.target_type}</Badge></td>
                          <td>{Object.values(data.inbound_counts[r.target_url] || {}).reduce((a, b) => a + b, 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
              <Panel title="Orphan pages — no internal links point to them">
                <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 8 }}>
                  Paste your full page list (one URL per line, e.g. from your sitemap). Pages with zero incoming internal links show up in red.
                </div>
                <textarea rows={4} style={{ width: "100%" }} placeholder={"https://yoursite.com/\nhttps://yoursite.com/services/\n…"}
                  value={pages} onChange={(e) => setPages(e.target.value)} />
                <div style={{ marginTop: 8 }}>
                  <button className="btn primary" onClick={() => setPagesSent(pages)}>Find orphan pages</button>
                </div>
                {pagesSent && !orphans.length && (
                  <div style={{ marginTop: 10, color: "var(--pass)" }}>No orphan pages found for {siteName(active)} — every page gets at least one internal link.</div>
                )}
                {!!orphans.length && (
                  <ul style={{ marginTop: 10 }}>
                    {orphans.map((u) => (
                      <li key={u} style={{ color: "var(--critical)", wordBreak: "break-all" }}>{u}</li>
                    ))}
                  </ul>
                )}
              </Panel>
            </>
          )}
        </>
      )}
    </div>
  );
}
