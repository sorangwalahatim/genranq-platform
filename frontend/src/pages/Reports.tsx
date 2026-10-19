import { Link } from "react-router-dom";
import { Badge, Empty, ErrorBox, Loading, PageHead, Panel } from "../components/ui";
import { download } from "../lib/api";
import { ago, bytes } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

export default function Reports() {
  const { data, error, loading } = useApi<any[]>("/reports", [], { poll: 5000 });
  const { run } = useAction();
  return (
    <>
      <PageHead title="Reports" sub="Branded PDF and Excel audit reports. Generate one from any completed audit.">
        <Link className="btn" to="/audits">Choose an audit</Link>
      </PageHead>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : (
        <Panel pad={false}>
          {!data?.length ? <Empty title="No reports yet">Open a completed audit and choose “Export report”.</Empty> : (
            <table className="t"><thead><tr><th>File</th><th>Site</th><th>Format</th><th>Branding</th><th>Status</th><th className="r">Size</th><th>Created</th><th /></tr></thead>
              <tbody>{data.map((r) => (
                <tr key={r.id}><td>{r.filename || <span className="faint">Generating…</span>}</td><td>{r.domain}</td><td>{r.format.toUpperCase()}</td><td>{r.white_label ? "White-label" : "Branded"}</td>
                  <td><Badge kind={r.status} /></td><td className="r num">{bytes(r.size_bytes)}</td><td className="muted">{ago(r.created_at)}</td>
                  <td>{r.status === "COMPLETED" && <button className="btn sm" onClick={() => run(() => download(`/reports/${r.id}/download`, r.filename))}>Download</button>}</td></tr>
              ))}</tbody></table>
          )}
        </Panel>
      )}
    </>
  );
}
