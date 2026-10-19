import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import { useMe } from "../../lib/hooks";
import { Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";

// Team-only space: internal notes + recent activity. Clients can never see this
// (the API refuses client users outright).
export default function InternalSpace() {
  const { projectId } = useParams();
  const { me } = useMe();
  const [notes, setNotes] = useState<any[] | null>(null);
  const [body, setBody] = useState("");
  const [err, setErr] = useState<ApiError | Error | null>(null);
  const [sending, setSending] = useState(false);

  const load = () => {
    api(`/portal/internal-notes?project_id=${projectId}`).then(setNotes).catch(setErr);
  };
  useEffect(load, [projectId]);

  const send = async () => {
    if (!body.trim() || sending) return;
    setSending(true);
    try {
      await api(`/portal/internal-notes?project_id=${projectId}`, { body: { body: body.trim() } });
      setBody("");
      load();
    } catch (e: any) { setErr(e); } finally { setSending(false); }
  };

  if (me?.user?.role === "client") {
    return <><PageHead title="Team space" /><Empty title="Not for clients">This area is only for the agency team.</Empty></>;
  }
  if (err) return <><PageHead title="Team space" /><ErrorBox error={err} /></>;
  if (!notes) return <><PageHead title="Team space" /><Loading /></>;

  return (
    <>
      <PageHead title="Team space" sub="Internal notes about this project. The client never sees anything here.">
        <Link className="btn ghost" to={`/portal/${projectId}`}>Client dashboard</Link>
        <Link className="btn ghost" to={`/portal/${projectId}/messages`}>Messages</Link>
      </PageHead>
      <Panel title="Internal notes">
        {notes.length === 0 && <Empty title="No notes yet">Leave context, handover notes or reminders for the team.</Empty>}
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 16 }}>
          {notes.map((n) => (
            <div key={n.id} className="card">
              <div className="muted" style={{ fontSize: "0.8rem" }}>
                {n.author_name} · {new Date(n.created_at).toLocaleString()}
              </div>
              <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{n.body}</div>
            </div>
          ))}
        </div>
        <div className="field">
          <textarea className="input" rows={3} placeholder="Internal note — hidden from the client…" value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        <div className="actions">
          <button className="btn" disabled={sending || !body.trim()} onClick={send}>{sending ? "Saving…" : "Save note"}</button>
        </div>
      </Panel>
    </>
  );
}
