import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { StatusBadge } from "./shared";

export default function WhiteLabel() {
  const { data, error, loading, reload } = useApi<any>("/social/white-label");
  const [f, setF] = useState({ portal_title: "", support_email: "" });
  const [domain, setDomain] = useState("");
  const can = useCan();
  const { run, busy } = useAction();
  useEffect(() => { if (data) setF({ portal_title: data.portal_title || "", support_email: data.support_email || "" }); }, [data]);
  if (loading && !data) return <Loading />;
  const b = data?.branding || {};
  const manage = can("manage_users") && data?.plan.white_label;
  return (
    <>
      <PageHead title="White-label" sub="Client portal on your own domain, with your branding on the portal and on reports." />
      <ErrorBox error={error} />
      {data && !data.plan.white_label && <div className="notice warn" style={{ marginBottom: 14 }}><div><b>White-label is not included in your plan.</b> <Link to="/billing">Plan &amp; usage</Link></div></div>}
      {data && <div className="split">
        <div className="stack">
          <Panel title="Custom domains">
            {manage && <form className="row" style={{ marginBottom: 12 }} onSubmit={(e) => { e.preventDefault(); run(() => api("/social/white-label/domains", { body: { domain } }), "Domain added — create the DNS records below").then((r) => { if (r) { setDomain(""); reload(); } }); }}>
              <input className="input" style={{ flex: 1 }} placeholder="portal.youragency.com" value={domain} onChange={(e) => setDomain(e.target.value)} aria-label="Domain" />
              <button className="btn primary" disabled={busy || !domain}>Add domain</button>
            </form>}
            {!data.domains.length ? <Empty title="No custom domains">Add a hostname you control; we give you two DNS records to create.</Empty> : data.domains.map((d: any) => (
              <div key={d.id} className="panel" style={{ padding: 14, marginBottom: 10 }}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <b>{d.domain}</b>
                  <div className="row">
                    <StatusBadge s={d.status === "verified" ? "verified" : d.status === "failed" ? "failed" : "pending"}>{d.status === "verified" ? "Verified" : d.status === "failed" ? "Not verified" : "Pending"}</StatusBadge>
                    <StatusBadge s={d.tls_status === "active" ? "active" : "pending"}>{d.tls_status === "active" ? "TLS active" : "TLS: awaiting operator"}</StatusBadge>
                  </div>
                </div>
                <ol className="steps small" style={{ marginTop: 10 }}>
                  <li>Ownership — TXT record <div className="code">{d.txt_name}</div> value <div className="code">{d.txt_value}</div> {d.txt_ok ? "✓ found" : ""}</li>
                  <li>Routing — CNAME <div className="code">{d.domain} → {data.cname_target}</div> {d.cname_ok ? "✓ found" : ""}</li>
                </ol>
                {d.last_error && <p className="small" style={{ color: "var(--critical)" }}>{d.last_error}</p>}
                <div className="row" style={{ marginTop: 8 }}>
                  {manage && <button className="btn sm primary" onClick={() => run<any>(() => api(`/social/white-label/domains/${d.id}/verify`, { method: "POST" })).then((r) => { if (r) reload(); })}>Check DNS now</button>}
                  {can("manage_users") && <button className="btn sm danger" onClick={() => confirm(`Remove ${d.domain}?`) && run(() => api(`/social/white-label/domains/${d.id}`, { method: "DELETE" })).then(reload)}>Remove</button>}
                  <span className="faint small">{d.last_checked_at ? `Checked ${ago(d.last_checked_at)}${d.method ? ` via ${d.method === "dns_txt" ? "TXT record" : "CNAME"}` : ""}` : "Not checked yet"}</span>
                </div>
              </div>
            ))}
            <p className="faint small">DNS checks use {data.dns_checker}. A domain stays NOT VERIFIED until its record really resolves; changes can take up to an hour to propagate.</p>
          </Panel>
          <Panel title="What happens after verification">
            <p className="small muted" style={{ marginBottom: 8 }}>TLS certificates are provisioned by the platform operator. Until the operator marks TLS active, the domain is verified but not yet serving the portal.</p>
            <ol className="steps small">{data.operator_steps.map((s: string) => <li key={s}>{s}</li>)}</ol>
          </Panel>
        </div>
        <div className="stack">
          <Panel title="Client portal">
            <form className="form" onSubmit={(e) => { e.preventDefault(); run(() => api("/social/white-label", { method: "PUT", body: { portal_title: f.portal_title || null, support_email: f.support_email || null } }), "Saved").then(reload); }}>
              <label className="field">Portal title<input className="input" value={f.portal_title} disabled={!manage} onChange={(e) => setF({ ...f, portal_title: e.target.value })} placeholder={b.company_name || data.org_name} /></label>
              <label className="field">Support email<input className="input" type="email" value={f.support_email} disabled={!manage} onChange={(e) => setF({ ...f, support_email: e.target.value })} /></label>
              {manage && <button className="btn primary" disabled={busy}>Save</button>}
            </form>
          </Panel>
          <Panel title="Branding preview" actions={<Link className="btn sm" to="/settings/branding">Edit branding</Link>}>
            <div className="preview-portal">
              <div className="bar" style={{ background: b.primary_color || "#1F3A5F" }}>{b.logo_url && <img src={b.logo_url} alt="" />}{f.portal_title || b.company_name || data.org_name}</div>
              <div className="body">
                <div>{data.domains.find((d: any) => d.status === "verified")?.domain || "your-portal-domain"} · client dashboard</div>
                <div style={{ marginTop: 6 }}>Reports footer: {b.report_footer || (b.white_label ? (b.company_name || data.org_name) : `${b.company_name || data.org_name} · Generated with Genranq`)}</div>
                {f.support_email && <div style={{ marginTop: 6 }}>Support: {f.support_email}</div>}
              </div>
            </div>
            <p className="faint small" style={{ marginTop: 8 }}>Logo and colours come from organisation settings.</p>
          </Panel>
        </div>
      </div>}
    </>
  );
}
