import { useState } from "react";
import { PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { AiBanner, Field, ItemDrawer, ItemList, lines, NoProjects, useProject } from "./shared";

/** One screen per generator: create form on the left, that type's content items on the right. */
function Shell({ title, sub, types, form, image = false }: { title: string; sub: string; types: string; image?: boolean;
  form: (pid: string, done: (item: any) => void) => JSX.Element }) {
  const { pid, projects, picker } = useProject();
  const [k, setK] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  if (projects && !projects.length) return <><PageHead title={title} /><NoProjects /></>;
  const done = (item: any) => { setK((x) => x + 1); const it = Array.isArray(item) ? item[0] : item; if (it?.id) setOpen(it.id); };
  return (
    <>
      <PageHead title={title} sub={sub}>{picker}</PageHead>
      <AiBanner image={image} />
      <div className="split">
        <div className="stack">{pid && form(pid, done)}</div>
        {pid && <ItemList pid={pid} types={types} reloadKey={k} empty="Create one with the form." />}
      </div>
      {open && <ItemDrawer id={open} onClose={() => { setOpen(null); setK((x) => x + 1); }} />}
    </>
  );
}

function useSubmit() {
  const { run, busy } = useAction();
  const can = useCan();
  return { busy, can: can("create"), go: (path: string, body: any, done: (r: any) => void, msg = "Created") => run(() => api(path, { body }), msg).then((r) => r && done(r)) };
}

// ---------------------------------------------------------------- 24 Blog
export function BlogGenerator() {
  return <Shell title="Blog generator" types="blog"
    sub="Brief → outline → AI draft → schema → review & approval → publish-ready export. Built from your real Search Console queries and the project's brand voice."
    form={(pid, done) => <BlogForm pid={pid} done={done} />} />;
}
function BlogForm({ pid, done }: { pid: string; done: (r: any) => void }) {
  const src = useApi<any>(`/studio/blog/sources?project_id=${pid}`, [pid]);
  const [f, setF] = useState({ keyword: "", title: "", supporting: "", word_count: 1500, notes: "", use_agent_brief: true });
  const s = useSubmit();
  const pick = (i: any) => setF({ ...f, keyword: i.primary_keyword || i.keyword || i.query || "", title: i.title || "", supporting: (i.supporting_keywords || []).join(", ") });
  return (<>
    <Panel title="New blog post">
      <form className="form" onSubmit={(e) => { e.preventDefault(); s.go("/studio/blog", { project_id: pid, keyword: f.keyword, title: f.title || null, supporting_keywords: lines(f.supporting),
        word_count: f.word_count, notes: f.notes || null, use_agent_brief: f.use_agent_brief }, done, "Brief and outline ready"); }}>
        <Field label="Primary keyword"><input className="input" value={f.keyword} onChange={(e) => setF({ ...f, keyword: e.target.value })} required minLength={2} /></Field>
        <Field label="Title" hint="Leave empty to generate one from the keyword and intent"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <Field label="Supporting keywords" hint="Comma or line separated"><textarea className="input" rows={2} value={f.supporting} onChange={(e) => setF({ ...f, supporting: e.target.value })} /></Field>
        <div className="grid g2">
          <Field label="Target words"><input className="input" type="number" min={300} max={6000} value={f.word_count} onChange={(e) => setF({ ...f, word_count: +e.target.value })} /></Field>
          <label className="check" style={{ alignSelf: "end" }}><input type="checkbox" checked={f.use_agent_brief} onChange={(e) => setF({ ...f, use_agent_brief: e.target.checked })} /> Use competitor outline (content-brief agent)</label>
        </div>
        <Field label="Notes for the writer"><textarea className="input" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
        <div>{s.can && <button className="btn primary" disabled={s.busy || f.keyword.trim().length < 2}>Create brief</button>}</div>
      </form>
    </Panel>
    <Panel pad={false} title="Ideas from your search data">
      {!src.data ? null : !src.data.ideas?.length ? <p className="muted" style={{ padding: 16 }}>No Search Console data yet — import or connect GSC on the project's monitoring Sources tab to get ideas from real queries.</p> :
        <table className="t"><tbody>{src.data.ideas.slice(0, 12).map((i: any, n: number) => <tr key={n} className="click" onClick={() => pick(i)}>
          <td><b>{i.title}</b><div className="faint small">{i.primary_keyword} · {i.intent} · {i.format}</div></td><td className="num faint">{i.demand ?? ""}</td></tr>)}</tbody></table>}
    </Panel>
  </>);
}

// ---------------------------------------------------------------- 25 News
export function NewsGenerator() {
  return <Shell title="News generator" types="news" sub="Topics from queries that just appeared in Search Console, plus your own sources; drafted in the brand voice, then approved and handed to the calendar."
    form={(pid, done) => <NewsForm pid={pid} done={done} />} />;
}
function NewsForm({ pid, done }: { pid: string; done: (r: any) => void }) {
  const topics = useApi<any>(`/studio/news/topics?project_id=${pid}`, [pid]);
  const sources = useApi<any[]>(`/studio/news/sources?project_id=${pid}`, [pid]);
  const [f, setF] = useState({ headline: "", angle: "", source_urls: "", query: "" });
  const [ns, setNs] = useState({ name: "", url: "" });
  const s = useSubmit();
  return (<>
    <Panel title="New news article">
      <form className="form" onSubmit={(e) => { e.preventDefault(); s.go("/studio/news", { project_id: pid, headline: f.headline, angle: f.angle || null, source_urls: lines(f.source_urls).slice(0, 5), query: f.query || null }, done); }}>
        <Field label="Headline"><input className="input" value={f.headline} onChange={(e) => setF({ ...f, headline: e.target.value })} required minLength={3} /></Field>
        <Field label="Angle"><textarea className="input" rows={2} value={f.angle} onChange={(e) => setF({ ...f, angle: e.target.value })} /></Field>
        <Field label="Source URLs" hint="Up to 5 — cited, never invented"><textarea className="input" rows={2} value={f.source_urls} onChange={(e) => setF({ ...f, source_urls: e.target.value })} /></Field>
        <div>{s.can && <button className="btn primary" disabled={s.busy || f.headline.length < 3}>Create</button>}</div>
      </form>
    </Panel>
    <Panel pad={false} title="Topics from new search queries">
      {!topics.data?.topics?.length ? <p className="muted" style={{ padding: 16 }}>{topics.data?.has_search_data ? "No new queries in the last weeks." : "No Search Console data for this project yet."}</p> :
        <table className="t"><tbody>{topics.data.topics.slice(0, 15).map((t: any) => <tr key={t.query} className="click" onClick={() => setF({ ...f, headline: t.headline, query: t.query })}>
          <td><b>{t.headline}</b><div className="faint small">“{t.query}” · first seen {t.first_seen} · {t.impressions} impressions</div></td></tr>)}</tbody></table>}
    </Panel>
    <Panel title="Your news sources">
      {(sources.data || []).map((x: any) => <div key={x.id} className="row" style={{ justifyContent: "space-between" }}><span><b>{x.name}</b> <span className="mono-url">{x.url}</span></span>
        <button className="btn ghost sm" onClick={() => api(`/studio/news/sources/${x.id}`, { method: "DELETE" }).then(sources.reload)}>Remove</button></div>)}
      <form className="row" style={{ marginTop: 8 }} onSubmit={(e) => { e.preventDefault(); s.go("/studio/news/sources", { project_id: pid, ...ns }, () => { setNs({ name: "", url: "" }); sources.reload(); }, "Source added"); }}>
        <input className="input" placeholder="Name" value={ns.name} onChange={(e) => setNs({ ...ns, name: e.target.value })} />
        <input className="input" placeholder="https://… (RSS or page)" value={ns.url} onChange={(e) => setNs({ ...ns, url: e.target.value })} />
        <button className="btn" disabled={!ns.name || !ns.url}>Add</button>
      </form>
    </Panel>
  </>);
}

// ---------------------------------------------------------------- 26 Whitepaper
export function WhitepaperGenerator() {
  return <Shell title="Whitepaper generator" types="whitepaper" sub="Long-form sections with charts from your own project data, exported as a branded PDF."
    form={(pid, done) => <WpForm pid={pid} done={done} />} />;
}
function WpForm({ pid, done }: { pid: string; done: (r: any) => void }) {
  const data = useApi<any>(`/studio/whitepaper/data?project_id=${pid}`, [pid]);
  const [f, setF] = useState({ title: "", audience: "", sections: "", include_data: true });
  const s = useSubmit();
  return (<Panel title="New whitepaper">
    <form className="form" onSubmit={(e) => { e.preventDefault(); s.go("/studio/whitepaper", { project_id: pid, title: f.title, audience: f.audience || null, sections: lines(f.sections).length ? lines(f.sections) : null, include_data: f.include_data }, done); }}>
      <Field label="Title"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required minLength={3} /></Field>
      <Field label="Audience"><input className="input" value={f.audience} onChange={(e) => setF({ ...f, audience: e.target.value })} /></Field>
      <Field label="Sections" hint={`One per line. Default: ${(data.data?.default_sections || []).join(" · ")}`}><textarea className="input" rows={4} value={f.sections} onChange={(e) => setF({ ...f, sections: e.target.value })} /></Field>
      <label className="check"><input type="checkbox" checked={f.include_data} onChange={(e) => setF({ ...f, include_data: e.target.checked })} /> Include charts from project data</label>
      <p className="faint small">{data.data?.summary}</p>
      <div>{s.can && <button className="btn primary" disabled={s.busy || f.title.length < 3}>Create</button>}</div>
    </form>
  </Panel>);
}

// ---------------------------------------------------------------- 27 Publication
export function PublicationGenerator() {
  return <Shell title="Publication generator" types="publication" sub="Thought-leadership articles with E-E-A-T titles (experience, expertise, authority and trust proof built in)."
    form={(pid, done) => <PubForm pid={pid} done={done} />} />;
}
function PubForm({ pid, done }: { pid: string; done: (r: any) => void }) {
  const [topic, setTopic] = useState("");
  const [titles, setTitles] = useState<any[]>([]);
  const s = useSubmit();
  return (<>
    <Panel title="1 · E-E-A-T titles">
      <form className="row" onSubmit={(e) => { e.preventDefault(); s.go("/studio/publication/titles", { project_id: pid, topic: topic || null }, (r) => setTitles(r.titles || []), "Titles ready"); }}>
        <input className="input" placeholder="Topic (defaults to your primary keyword)" value={topic} onChange={(e) => setTopic(e.target.value)} />
        <button className="btn" disabled={s.busy}>Suggest titles</button>
      </form>
    </Panel>
    {titles.length > 0 && <Panel pad={false} title="2 · Pick a title to start the article">
      <table className="t"><tbody>{titles.map((t) => <tr key={t.id}>
        <td><b>{t.title}</b><div className="small">{t.subtitle}</div><div className="faint small">{t.eeat_focus} · {t.intent} · {t.persona} · {t.metric}</div></td>
        <td>{s.can && <button className="btn sm primary" onClick={() => s.go("/studio/publication", { project_id: pid, title: t.title, subtitle: t.subtitle, eeat_focus: t.eeat_focus,
          intent: t.intent, persona: t.persona, pain: t.pain, metric: t.metric }, done)}>Use</button>}</td></tr>)}</tbody></table>
    </Panel>}
  </>);
}

// ---------------------------------------------------------------- 28 Playbook
export function PlaybookGenerator() {
  return <Shell title="Playbook generator" types="playbook" sub="SOPs and playbooks with numbered steps, owners and checklists — versioned and exportable."
    form={(pid, done) => <PlaybookForm pid={pid} done={done} />} />;
}
function PlaybookForm({ pid, done }: { pid: string; done: (r: any) => void }) {
  const [f, setF] = useState({ title: "", goal: "", roles: "Owner", steps: "" });
  const s = useSubmit();
  const steps = lines(f.steps.replace(/,/g, "‚")).map((l) => { const [title, owner] = l.split("|").map((x) => x.trim()); return { title: title.replace(/‚/g, ","), owner: owner || null, checklist: [] }; });
  return (<Panel title="New playbook">
    <form className="form" onSubmit={(e) => { e.preventDefault(); s.go("/studio/playbook", { project_id: pid, title: f.title, goal: f.goal || null, roles: lines(f.roles), steps }, done); }}>
      <Field label="Title"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required minLength={3} /></Field>
      <Field label="Goal"><textarea className="input" rows={2} value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} /></Field>
      <Field label="Roles" hint="Comma separated"><input className="input" value={f.roles} onChange={(e) => setF({ ...f, roles: e.target.value })} /></Field>
      <Field label="Steps" hint="One per line, optionally “Step | Owner”. Leave empty to let AI draft the steps."><textarea className="input" rows={6} value={f.steps} onChange={(e) => setF({ ...f, steps: e.target.value })} /></Field>
      <div>{s.can && <button className="btn primary" disabled={s.busy || f.title.length < 3}>Create</button>}</div>
    </form>
  </Panel>);
}

// ---------------------------------------------------------------- 29 Website + 60 Landing pages
function PageForm({ pid, done, landing }: { pid: string; done: (r: any) => void; landing?: boolean }) {
  const plan = useApi<any>(landing ? null : `/studio/website/plan?project_id=${pid}`, [pid]);
  const [f, setF] = useState({ page_type: landing ? "landing" : "service", keyword: "", service: "", location: "", cta: "" });
  const s = useSubmit();
  const body = { page_type: f.page_type, keyword: f.keyword, service: f.service || null, location: f.location || null, cta: f.cta || null };
  return (<>
    <Panel title={landing ? "New landing page" : "New website page"}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); landing ? s.go("/studio/landing-pages", { project_id: pid, ...body }, done) : s.go("/studio/website/pages", { project_id: pid, pages: [body] }, done); }}>
        <Field label="Page type"><select className="input" value={f.page_type} onChange={(e) => setF({ ...f, page_type: e.target.value })}>
          <option value="landing">Landing</option><option value="service">Service</option><option value="location">Location</option></select></Field>
        <Field label="Target keyword"><input className="input" value={f.keyword} onChange={(e) => setF({ ...f, keyword: e.target.value })} required minLength={2} /></Field>
        <div className="grid g2">
          <Field label="Service"><input className="input" value={f.service} onChange={(e) => setF({ ...f, service: e.target.value })} /></Field>
          <Field label="Location"><input className="input" value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} required={f.page_type === "location"} /></Field>
        </div>
        <Field label="Call to action"><input className="input" value={f.cta} onChange={(e) => setF({ ...f, cta: e.target.value })} placeholder="Book a free quote" /></Field>
        <div>{s.can && <button className="btn primary" disabled={s.busy || f.keyword.length < 2}>Create page plan</button>}</div>
      </form>
    </Panel>
    {!landing && plan.data && <Panel pad={false} title="Suggested pages (service × location from your profile)">
      {!plan.data.pages?.length ? <p className="muted" style={{ padding: 16 }}>Add services and locations to the project profile (AI → Skills → Project profile) to get a site plan.</p> :
        <table className="t"><tbody>{plan.data.pages.map((p: any, i: number) => <tr key={i}><td><b>{p.title || p.keyword}</b><div className="faint small">{p.page_type} · {p.keyword}</div></td>
          <td>{s.can && <button className="btn sm" onClick={() => s.go("/studio/website/pages", { project_id: pid, pages: [{ page_type: p.page_type, keyword: p.keyword, service: p.service, location: p.location }] }, done)}>Create</button>}</td></tr>)}</tbody></table>}
    </Panel>}
  </>);
}
export function WebsiteGenerator() {
  return <Shell title="Website generator" types="web_page" sub="Service and location pages: section copy, title/meta, H1 and LocalBusiness / Service schema per intent, with an HTML preview."
    form={(pid, done) => <PageForm pid={pid} done={done} />} />;
}
export function LandingGenerator() {
  return <Shell title="Landing page generator" types="landing" sub="Landing page copy, meta and schema per search intent — reviewed, approved and exported."
    form={(pid, done) => <PageForm pid={pid} done={done} landing />} />;
}

// ---------------------------------------------------------------- 30 Social posts
export function SocialPostsGenerator() {
  return <Shell title="Social posts" types="social" sub="Per-platform posts with captions and hashtags, from your content. Character limits are enforced per platform; scheduling happens in the Content calendar."
    form={(pid, done) => <SocialForm pid={pid} done={done} />} />;
}
function SocialForm({ pid, done }: { pid: string; done: (r: any) => void }) {
  const lim = useApi<any>("/studio/social/limits");
  const src = useApi<any[]>(`/studio/items?project_id=${pid}&type=blog,news,publication,whitepaper,landing,web_page`, [pid]);
  const [f, setF] = useState({ platforms: ["linkedin", "x"] as string[], source_item_id: "", url: "" });
  const s = useSubmit();
  const tog = (p: string) => setF({ ...f, platforms: f.platforms.includes(p) ? f.platforms.filter((x) => x !== p) : [...f.platforms, p] });
  return (<Panel title="New social posts">
    <form className="form" onSubmit={(e) => { e.preventDefault(); s.go("/studio/social", { project_id: pid, platforms: f.platforms, source_item_id: f.source_item_id || null, url: f.url || null }, done); }}>
      <div className="row">{["x", "linkedin", "instagram", "facebook"].map((p) => <label key={p} className="check"><input type="checkbox" checked={f.platforms.includes(p)} onChange={() => tog(p)} /> {p} <span className="faint small">({lim.data?.limits?.[p]})</span></label>)}</div>
      <Field label="From content"><select className="input" value={f.source_item_id} onChange={(e) => setF({ ...f, source_item_id: e.target.value })}>
        <option value="">— none —</option>{(src.data || []).map((i) => <option key={i.id} value={i.id}>{i.title} ({i.type}, {i.state})</option>)}</select></Field>
      <Field label="…or from a URL" hint="Fetched safely and summarised"><input className="input" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="https://" /></Field>
      <p className="faint small">{lim.data?.notes?.x}</p>
      <div>{s.can && <button className="btn primary" disabled={s.busy || !f.platforms.length || (!f.source_item_id && !f.url)}>Create posts</button>}</div>
    </form>
  </Panel>);
}
