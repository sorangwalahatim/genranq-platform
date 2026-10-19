import { useState } from "react";
import { Empty, ErrorBox, Loading, Modal, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { fmtDate, human } from "../../lib/format";
import { useAction, useApi, useCan, useToast } from "../../lib/hooks";
import { copy, LocationPick, NcBanner, NoLocation, NoProject, ProjectPick, StatusBadge, useLocal } from "./shared";

const CTAS = ["", "BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL"];
const STATES = ["", "draft", "review", "approved", "published", "rejected"];

/** Module 31 — GMB posts: create / AI draft → review → approved → published (publishing only when connected). */
export default function GmbPosts() {
  const L = useLocal("own");
  const can = useCan();
  const [status, setStatus] = useState("");
  const [onlyLoc, setOnlyLoc] = useState(false);
  const posts = useApi<any[]>(L.pid ? `/local/posts?project_id=${L.pid}${status ? `&status=${status}` : ""}${onlyLoc && L.lid ? `&location_id=${L.lid}` : ""}` : null, [L.pid, status, onlyLoc, L.lid]);
  const [edit, setEdit] = useState<any>(null);
  const [ai, setAi] = useState(false);
  return (
    <>
      <PageHead title="GMB posts" sub="Create update, offer and event posts. Every post is reviewed and approved before it can be published.">
        <ProjectPick {...L} /><LocationPick locs={L.locs.data} lid={L.lid} setLid={L.setLid} />
        {can("create") && <button className="btn" disabled={!L.lid || !L.status?.ai.connected} title={L.status?.ai.connected ? "" : "AI provider NOT CONNECTED"} onClick={() => setAi(true)}>AI draft</button>}
        {can("create") && <button className="btn primary" disabled={!L.lid} onClick={() => setEdit({})}>New post</button>}
      </PageHead>
      <NcBanner conn={L.status?.gbp} provider="gbp">Approved posts stay “approved” with a copy button — publish them in Google Business Profile manually and mark them as posted.</NcBanner>
      <NcBanner conn={L.status?.ai} provider="ai">AI drafting is unavailable — write posts manually.</NcBanner>
      <NoProject projects={L.projects} />
      <ErrorBox error={posts.error} />
      {L.pid && L.locs.data && !L.locs.data.length ? <NoLocation /> : (
        <Panel pad={false} title="Posts" actions={<>
          <label className="check small"><input type="checkbox" checked={onlyLoc} onChange={(e) => setOnlyLoc(e.target.checked)} /> This location only</label>
          <select className="input" style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">{STATES.map((s) => <option key={s} value={s}>{s ? human(s) : "All statuses"}</option>)}</select></>}>
          {posts.loading && !posts.data ? <Loading /> : !posts.data?.length ? <Empty title="No posts" action={can("create") && L.lid && <button className="btn primary" onClick={() => setEdit({})}>New post</button>}>Write a post or let AI draft one.</Empty> :
            posts.data.map((p) => <PostRow key={p.id} p={p} onEdit={() => setEdit(p)} onChange={posts.reload} />)}
        </Panel>
      )}
      {edit && L.lid && <PostEditor post={edit} lid={L.lid} onClose={() => setEdit(null)} onDone={posts.reload} />}
      {ai && L.lid && <AiDraft lid={L.lid} onClose={() => setAi(false)} onDone={(p) => { posts.reload(); setEdit(p); }} />}
    </>
  );
}

function PostRow({ p, onEdit, onChange }: { p: any; onEdit: () => void; onChange: () => void }) {
  const can = useCan();
  const toast = useToast();
  const { run, busy } = useAction();
  const act = async (a: string, body: any = {}, ok?: string) => { if (await run(() => api(`/local/posts/${p.id}/${a}`, { body }), ok)) onChange(); };
  const publish = async () => {
    const r: any = await run(() => api(`/local/posts/${p.id}/publish`, { body: {} }));
    if (!r) return;
    if (r.queued) toast("Publishing to Google Business Profile…"); else { copy(r.text); toast(`${r.message} (text copied)`, true); }
    onChange();
  };
  return (
    <div className="lc-review">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div className="row" style={{ gap: 8 }}><StatusBadge s={p.status} /><b>{p.title || human(p.post_type)}</b><span className="lc-chip">{p.post_type}</span>
          <span className="faint small">{p.location_name} · {p.status === "published" ? `posted ${fmtDate(p.posted_at)}` : `created ${fmtDate(p.created_at)}`} · {p.source}</span></div>
        <span className="faint small num">{p.length}/1500</span>
      </div>
      <div className="lc-post-body" style={{ marginTop: 6 }}>{p.body}</div>
      <div className="small muted" style={{ marginTop: 4 }}>{p.cta_type ? `Button: ${human(p.cta_type)}${p.cta_url ? ` → ${p.cta_url}` : ""}` : "No button"}{p.image_ref ? " · image attached" : " · no image"}
        {p.start_at && ` · ${fmtDate(p.start_at)}${p.end_at ? ` – ${fmtDate(p.end_at)}` : ""}`}{p.offer_code && ` · code ${p.offer_code}`}</div>
      {p.publish_error && <div className="notice warn small" style={{ marginTop: 6 }}>{p.publish_error}</div>}
      <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        {can("edit") && ["draft", "review", "approved", "rejected"].includes(p.status) && <button className="btn sm" onClick={onEdit}>Edit</button>}
        {can("edit") && ["draft", "rejected"].includes(p.status) && <button className="btn sm" disabled={busy} onClick={() => act("submit", {}, "Submitted for review")}>Submit for review</button>}
        {can("approve") && p.status === "review" && <><button className="btn sm primary" disabled={busy} onClick={() => act("approve", {}, "Approved")}>Approve</button>
          <button className="btn sm danger" disabled={busy} onClick={() => { const n = prompt("Reason for rejection"); if (n) act("reject", { notes: n }, "Rejected"); }}>Reject</button></>}
        {can("publish") && p.status === "approved" && <><button className="btn sm primary" disabled={busy} onClick={publish}>Publish</button>
          <button className="btn sm" onClick={() => { copy(p.body); toast("Post copied"); }}>Copy</button>
          <button className="btn sm" disabled={busy} onClick={() => act("mark-published", {}, "Marked as published")}>Mark as posted manually</button></>}
        {can("delete") && p.status !== "publishing" && <button className="btn sm ghost danger" onClick={() => confirm("Delete this post?") && run(() => api(`/local/posts/${p.id}`, { method: "DELETE" }), "Deleted").then(onChange)}>Delete</button>}
      </div>
    </div>
  );
}

const dt = (s?: string | null) => (s ? s.slice(0, 10) : "");

function PostEditor({ post, lid, onClose, onDone }: { post: any; lid: string; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ post_type: post.post_type || "update", title: post.title || "", body: post.body || "", cta_type: post.cta_type || "", cta_url: post.cta_url || "",
    start_at: dt(post.start_at), end_at: dt(post.end_at), offer_code: post.offer_code || "", image_ref: post.image_ref || "" });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const { run, busy } = useAction();
  const save = async () => {
    const body = { ...f, location_id: post.location_id || lid, cta_type: f.cta_type || null, cta_url: f.cta_url || null, title: f.title || null, offer_code: f.offer_code || null,
      image_ref: f.image_ref || null, start_at: f.start_at ? f.start_at + "T09:00:00Z" : null, end_at: f.end_at ? f.end_at + "T21:00:00Z" : null };
    const r = await run(() => post.id ? api(`/local/posts/${post.id}`, { method: "PATCH", body }) : api("/local/posts", { body }), post.id ? (post.status === "draft" ? "Saved" : "Saved — back to draft for re-approval") : "Draft created");
    if (r) { onDone(); onClose(); }
  };
  return (
    <Modal wide title={post.id ? "Edit post" : "New post"} onClose={onClose} foot={<button className="btn primary" disabled={!f.body.trim() || busy} onClick={save}>Save draft</button>}>
      <div className="form">
        <div className="seg" role="group" aria-label="Post type">{["update", "offer", "event"].map((t) => <button key={t} className={f.post_type === t ? "on" : ""} onClick={() => setF({ ...f, post_type: t })}>{human(t)}</button>)}</div>
        {f.post_type !== "update" && <label className="field">Title{f.post_type === "event" && <span className="hint">required for events</span>}<input className="input" value={f.title} onChange={set("title")} /></label>}
        <label className="field">Text<span className="hint">{f.body.length}/1500 characters</span><textarea className="input" rows={7} maxLength={1500} value={f.body} onChange={set("body")} /></label>
        <div className="grid g2">
          <label className="field">Button<select className="input" value={f.cta_type} onChange={set("cta_type")}>{CTAS.map((c) => <option key={c} value={c}>{c ? human(c.toLowerCase()) : "None"}</option>)}</select></label>
          <label className="field">Button link<input className="input" value={f.cta_url} onChange={set("cta_url")} disabled={!f.cta_type || f.cta_type === "CALL"} placeholder="https://" /></label>
        </div>
        {f.post_type !== "update" && <div className="grid g3">
          <label className="field">Start<input className="input" type="date" value={f.start_at} onChange={set("start_at")} /></label>
          <label className="field">End<input className="input" type="date" value={f.end_at} onChange={set("end_at")} /></label>
          {f.post_type === "offer" && <label className="field">Coupon code<input className="input" value={f.offer_code} onChange={set("offer_code")} /></label>}
        </div>}
        <label className="field">Image<span className="hint">https URL of the photo (or a stored file id)</span><input className="input" value={f.image_ref} onChange={set("image_ref")} placeholder="https://…/photo.jpg" /></label>
        {post.status && post.status !== "draft" && <p className="small muted">Saving changes moves this post back to draft — it will need approval again.</p>}
      </div>
    </Modal>
  );
}

function AiDraft({ lid, onClose, onDone }: { lid: string; onClose: () => void; onDone: (p: any) => void }) {
  const [f, setF] = useState({ post_type: "update", brief: "", tone: "friendly and professional" });
  const { run, busy } = useAction();
  return (
    <Modal title="AI draft a post" onClose={onClose} foot={<button className="btn primary" disabled={f.brief.trim().length < 3 || busy} onClick={async () => {
      const r = await run(() => api("/local/posts/ai-draft", { body: { location_id: lid, ...f } }), "Draft created — review and edit before submitting");
      if (r) { onClose(); onDone(r); }
    }}>{busy ? "Drafting…" : "Draft"}</button>}>
      <div className="form">
        <div className="seg" role="group" aria-label="Post type">{["update", "offer", "event"].map((t) => <button key={t} className={f.post_type === t ? "on" : ""} onClick={() => setF({ ...f, post_type: t })}>{human(t)}</button>)}</div>
        <label className="field">Brief<span className="hint">What should the post say? Include real offers/dates — the AI is told not to invent any.</span><textarea className="input" rows={4} value={f.brief} onChange={(e) => setF({ ...f, brief: e.target.value })} autoFocus /></label>
        <label className="field">Tone<input className="input" value={f.tone} onChange={(e) => setF({ ...f, tone: e.target.value })} /></label>
      </div>
    </Modal>
  );
}
