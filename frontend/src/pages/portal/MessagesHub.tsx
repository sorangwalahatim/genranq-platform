import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import { useMe } from "../../lib/hooks";
import { Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";

export default function MessagesHub() {
  const { projectId } = useParams();
  const { me } = useMe();
  const [msgs, setMsgs] = useState<any[]>([]);
  const [body, setBody] = useState("");
  const [err, setErr] = useState<ApiError | Error | null>(null);
  const [sending, setSending] = useState(false);

  const load = () => {
    api(`/portal/messages?project_id=${projectId}`).then(setMsgs).catch(setErr);
  };
  useEffect(load, [projectId]);

  const send = async () => {
    if (!body.trim() || sending) return;
    setSending(true);
    try {
      await api(`/portal/messages?project_id=${projectId}`, { body: { body: body.trim() } });
      setBody("");
      load();
    } catch (e: any) { setErr(e); } finally { setSending(false); }
  };

  if (err) return <><PageHead title="Messages" /><ErrorBox error={err} /></>;
  if (!msgs && !err) return <><PageHead title="Messages" /><Loading /></>;

  return (
    <>
      <PageHead title="Messages" sub="Talk directly with your agency team about this project.">
        <Link className="btn ghost" to={`/portal/${projectId}`}>Back to dashboard</Link>
      </PageHead>
      <Panel title="Conversation">
        {msgs.length === 0 && <Empty title="No messages yet" >Say hello — your agency team will reply here.</Empty>}
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16 }}>
          {msgs.map((m) => (
            <div key={m.id} className="card" style={{ alignSelf: m.mine ? "flex-end" : "flex-start", maxWidth: "75%",
              background: m.mine ? "#eff6ff" : undefined }}>
              <div className="muted" style={{ fontSize: "0.8rem" }}>
                {m.author_name}{m.author_role && m.author_role !== "client" ? " (agency)" : ""} · {new Date(m.created_at).toLocaleString()}
              </div>
              <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{m.body}</div>
            </div>
          ))}
        </div>
        <div className="field">
          <textarea className="input" rows={3} placeholder="Write a message…" value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        <div className="actions">
          <button className="btn" disabled={sending || !body.trim()} onClick={send}>{sending ? "Sending…" : "Send"}</button>
          <span className="muted">Signed in as {me?.user?.name || me?.user?.email}</span>
        </div>
      </Panel>
    </>
  );
}
