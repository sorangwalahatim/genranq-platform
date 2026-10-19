import { useEffect, useState } from "react";
import { Badge, Drawer, Empty, ErrorBox, Loading, PageHead, Panel } from "../../components/ui";
import { api } from "../../lib/api";
import { ago, human } from "../../lib/format";
import { useAction, useApi, useCan } from "../../lib/hooks";
import { LeadsNav, Tabs } from "./shared";

export default function Onboarding() {
  const { data: tpl } = useApi<any>("/onboarding/template");
  const { data, error, loading, reload } = useApi<any[]>("/onboarding/questionnaires");
  const { data: responses } = useApi<any[]>("/onboarding/responses");
  const [edit, setEdit] = useState<any>(null);
  const can = useCan();
  return (
    <>
      <PageHead title="Onboarding & profiling" sub="Qualification questionnaires (who / what / source / budget / timeline). Attach one to sign-up or to forms; scoring rules set lead score and segment.">
        {can("create") && tpl && <button className="btn primary" onClick={() => setEdit({ ...tpl.template, attach_signup: false, active: true })}>New from qualification template</button>}
      </PageHead>
      <LeadsNav />
      <SignupQuestionnaire />
      <Panel title="Questionnaires" pad={false}>
        <ErrorBox error={error} />
        {loading && !data ? <Loading /> : !data?.length ? <Empty title="No questionnaires yet">Start from the qualification template and adapt the questions and points.</Empty> : (
          <div className="table-wrap"><table className="t"><thead><tr><th>Name</th><th>Questions</th><th>Attached to</th><th className="r">Responses</th><th>Segments</th></tr></thead>
            <tbody>{data.map((q) => <tr key={q.id} className="click" onClick={() => setEdit(q)}>
              <td><b>{q.name}</b>{!q.active && <> <Badge kind="low">inactive</Badge></>}</td><td className="small">{q.questions.map((x: any) => x.category).join(", ")}</td>
              <td className="small">{[q.attach_signup && "sign-up", q.attached_forms.length && `${q.attached_forms.length} form(s)`].filter(Boolean).join(", ") || "—"}</td>
              <td className="r num">{q.responses}</td><td className="small">{Object.entries(q.segments_count).map(([k, v]) => `${k === "null" ? "none" : k}: ${v}`).join(" · ") || "—"}</td></tr>)}</tbody></table></div>
        )}
      </Panel>
      <div style={{ marginTop: 16 }}><Panel title="Latest responses" pad={false}>
        {!responses?.length ? <Empty title="No responses yet" /> : <div className="table-wrap"><table className="t"><thead><tr><th>When</th><th>Who</th><th className="r">Score</th><th>Segment</th><th>Answers</th></tr></thead>
          <tbody>{responses.slice(0, 50).map((r) => <tr key={r.id}><td className="small">{ago(r.created_at)}</td><td className="small">{r.lead_email || r.user || "—"}</td><td className="r num">{r.score}</td><td>{r.segment ? <Badge>{r.segment}</Badge> : "—"}</td>
            <td className="small">{Object.entries(r.answers).map(([k, v]: any) => `${k}: ${Array.isArray(v) ? v.join("/") : v}`).join(" · ")}</td></tr>)}</tbody></table></div>}
      </Panel></div>
      {edit && tpl && <Editor q={edit} meta={tpl} onClose={() => setEdit(null)} onDone={reload} />}
    </>
  );
}

function Editor({ q, meta, onClose, onDone }: any) {
  const [d, setD] = useState<any>(JSON.parse(JSON.stringify(q)));
  const [tab, setTab] = useState("questions");
  const [answers, setAnswers] = useState<any>({});
  const [score, setScore] = useState<any>(null);
  const { run, busy } = useAction();
  const can = useCan();
  const body = { name: d.name, description: d.description, questions: d.questions, rules: d.rules, segments: d.segments, attach_signup: d.attach_signup, active: d.active };
  const save = () => run<any>(() => d.id ? api(`/onboarding/questionnaires/${d.id}`, { method: "PUT", body }) : api("/onboarding/questionnaires", { body }), "Questionnaire saved").then((r) => { if (r) { setD({ ...d, id: r.id }); onDone(); } });
  const setQ = (i: number, k: string, v: any) => { const qs = [...d.questions]; qs[i] = { ...qs[i], [k]: v }; setD({ ...d, questions: qs }); };
  return (
    <Drawer title={d.id ? d.name : "New questionnaire"} onClose={onClose} foot={can("edit") ? <div className="row">
      <button className="btn primary" disabled={busy} onClick={save}>Save</button>
      {d.id && can("delete") && <button className="btn danger" onClick={() => confirm("Delete questionnaire and responses?") && run(() => api(`/onboarding/questionnaires/${d.id}`, { method: "DELETE" })).then(() => { onDone(); onClose(); })}>Delete</button>}</div> : undefined}>
      <div className="form" style={{ marginBottom: 12 }}>
        <label className="field">Name<input className="input" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></label>
        <div className="row" style={{ gap: 16 }}><label className="check"><input type="checkbox" checked={d.attach_signup} onChange={(e) => setD({ ...d, attach_signup: e.target.checked })} /> Ask on sign-up / onboarding</label>
          <label className="check"><input type="checkbox" checked={d.active} onChange={(e) => setD({ ...d, active: e.target.checked })} /> Active</label></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[["questions", `Questions (${d.questions.length})`], ["rules", "Scoring & segments"], ["test", "Test scoring"]]} />
      {tab === "questions" && <div className="stack">
        {d.questions.map((qq: any, i: number) => (
          <div key={i} className="panel" style={{ padding: 12 }}>
            <div className="grid g3">
              <label className="field">Key<input className="input" value={qq.key} onChange={(e) => setQ(i, "key", e.target.value)} /></label>
              <label className="field">Type<select className="input" value={qq.type} onChange={(e) => setQ(i, "type", e.target.value)}>{meta.types.map((t: string) => <option key={t}>{t}</option>)}</select></label>
              <label className="field">Category<select className="input" value={qq.category || "other"} onChange={(e) => setQ(i, "category", e.target.value)}>{meta.categories.map((t: string) => <option key={t}>{t}</option>)}</select></label>
            </div>
            <label className="field" style={{ marginTop: 8 }}>Question<input className="input" value={qq.label} onChange={(e) => setQ(i, "label", e.target.value)} /></label>
            <label className="check" style={{ marginTop: 6 }}><input type="checkbox" checked={!!qq.required} onChange={(e) => setQ(i, "required", e.target.checked)} /> Required</label>
            {["single", "multi"].includes(qq.type) && <div style={{ marginTop: 8 }}>
              {(qq.options || []).map((o: any, j: number) => <div key={j} className="row" style={{ gap: 6, marginBottom: 4 }}>
                <input className="input" style={{ width: 130 }} value={o.value} placeholder="value" onChange={(e) => { const os = [...qq.options]; os[j] = { ...o, value: e.target.value }; setQ(i, "options", os); }} />
                <input className="input" style={{ flex: 1 }} value={o.label || ""} placeholder="label" onChange={(e) => { const os = [...qq.options]; os[j] = { ...o, label: e.target.value }; setQ(i, "options", os); }} />
                <input className="input" style={{ width: 80 }} inputMode="numeric" value={o.points ?? 0} aria-label="Points" onChange={(e) => { const os = [...qq.options]; os[j] = { ...o, points: Number(e.target.value) || 0 }; setQ(i, "options", os); }} />
                <button className="btn ghost sm" onClick={() => setQ(i, "options", qq.options.filter((_: any, k: number) => k !== j))} aria-label="Remove option">✕</button></div>)}
              <button className="btn sm" onClick={() => setQ(i, "options", [...(qq.options || []), { value: `option_${(qq.options || []).length + 1}`, label: "", points: 0 }])}>Add option</button>
            </div>}
            <button className="btn ghost sm" style={{ marginTop: 8 }} onClick={() => setD({ ...d, questions: d.questions.filter((_: any, k: number) => k !== i) })}>Remove question</button>
          </div>))}
        <button className="btn" onClick={() => setD({ ...d, questions: [...d.questions, { key: `q${d.questions.length + 1}`, label: "New question", type: "single", category: "other", options: [{ value: "yes", label: "Yes", points: 10 }, { value: "no", label: "No", points: 0 }] }] })}>Add question</button>
      </div>}
      {tab === "rules" && <div className="form">
        <b className="small">Rules (added on top of option points)</b>
        {d.rules.map((r: any, i: number) => <div key={i} className="row" style={{ gap: 6 }}>
          <select className="input" style={{ width: "auto" }} value={r.question} onChange={(e) => { const rs = [...d.rules]; rs[i] = { ...r, question: e.target.value }; setD({ ...d, rules: rs }); }}>{d.questions.map((q: any) => <option key={q.key}>{q.key}</option>)}</select>
          <select className="input" style={{ width: "auto" }} value={r.op} onChange={(e) => { const rs = [...d.rules]; rs[i] = { ...r, op: e.target.value }; setD({ ...d, rules: rs }); }}>{meta.ops.map((o: string) => <option key={o}>{o}</option>)}</select>
          <input className="input" style={{ flex: 1 }} value={r.value ?? ""} placeholder="value" onChange={(e) => { const rs = [...d.rules]; rs[i] = { ...r, value: isNaN(Number(e.target.value)) || e.target.value === "" ? e.target.value : Number(e.target.value) }; setD({ ...d, rules: rs }); }} />
          <input className="input" style={{ width: 80 }} inputMode="numeric" value={r.points} aria-label="Points" onChange={(e) => { const rs = [...d.rules]; rs[i] = { ...r, points: Number(e.target.value) || 0 }; setD({ ...d, rules: rs }); }} />
          <button className="btn ghost sm" onClick={() => setD({ ...d, rules: d.rules.filter((_: any, k: number) => k !== i) })} aria-label="Remove rule">✕</button></div>)}
        <button className="btn sm" disabled={!d.questions.length} onClick={() => setD({ ...d, rules: [...d.rules, { question: d.questions[0].key, op: "eq", value: "", points: 10 }] })}>Add rule</button>
        <b className="small" style={{ marginTop: 10 }}>Segments (highest minimum score that is met wins)</b>
        {d.segments.map((s: any, i: number) => <div key={i} className="row" style={{ gap: 6 }}>
          <input className="input" style={{ flex: 1 }} value={s.name} onChange={(e) => { const ss = [...d.segments]; ss[i] = { ...s, name: e.target.value }; setD({ ...d, segments: ss }); }} />
          <input className="input" style={{ width: 100 }} inputMode="numeric" value={s.min_score} aria-label="Minimum score" onChange={(e) => { const ss = [...d.segments]; ss[i] = { ...s, min_score: Number(e.target.value) || 0 }; setD({ ...d, segments: ss }); }} />
          <button className="btn ghost sm" onClick={() => setD({ ...d, segments: d.segments.filter((_: any, k: number) => k !== i) })} aria-label="Remove segment">✕</button></div>)}
        <button className="btn sm" onClick={() => setD({ ...d, segments: [...d.segments, { name: "Segment", min_score: 0 }] })}>Add segment</button>
      </div>}
      {tab === "test" && <div className="form">
        {!d.id && <p className="muted small">Save the questionnaire first to test it.</p>}
        <Answers questions={d.questions} value={answers} onChange={setAnswers} />
        <button className="btn" disabled={!d.id} onClick={() => run<any>(() => api(`/onboarding/questionnaires/${d.id}/score`, { body: { answers } })).then(setScore)}>Calculate score</button>
        {score && <div className="panel" style={{ padding: 12 }}><b>Score {score.score}</b> · segment {score.segment || "none"}
          <ul className="small">{score.breakdown.map((b: any, i: number) => <li key={i}>{b.question}: {b.reason} ({b.points > 0 ? "+" : ""}{b.points})</li>)}</ul>
          {score.missing.length > 0 && <p className="small" style={{ color: "var(--critical)" }}>Missing required: {score.missing.join(", ")}</p>}</div>}
      </div>}
    </Drawer>
  );
}

export function Answers({ questions, value, onChange }: { questions: any[]; value: any; onChange: (v: any) => void }) {
  return <>{questions.map((q) => (
    <fieldset key={q.key} className="field" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend style={{ fontWeight: 500, fontSize: 13, marginBottom: 4 }}>{q.label}{q.required && " *"} <span className="faint small">{human(q.category)}</span></legend>
      {q.type === "single" ? <select className="input" value={value[q.key] ?? ""} onChange={(e) => onChange({ ...value, [q.key]: e.target.value || undefined })}><option value="">Choose…</option>{q.options.map((o: any) => <option key={o.value} value={o.value}>{o.label || o.value}</option>)}</select>
        : q.type === "multi" ? <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>{q.options.map((o: any) => <label key={o.value} className="check"><input type="checkbox" checked={(value[q.key] || []).includes(o.value)}
          onChange={(e) => onChange({ ...value, [q.key]: e.target.checked ? [...(value[q.key] || []), o.value] : (value[q.key] || []).filter((x: string) => x !== o.value) })} /> {o.label || o.value}</label>)}</div>
        : <input className="input" inputMode={q.type === "number" ? "decimal" : undefined} type={q.type === "email" ? "email" : "text"} value={value[q.key] ?? ""}
          onChange={(e) => onChange({ ...value, [q.key]: q.type === "number" ? (e.target.value === "" ? undefined : Number(e.target.value)) : e.target.value })} />}
    </fieldset>))}</>;
}

function SignupQuestionnaire() {
  const { data, reload } = useApi<any>("/onboarding/signup");
  const [answers, setAnswers] = useState<any>({});
  const { run, busy } = useAction();
  useEffect(() => { if (data?.response) setAnswers(data.response.answers); }, [data]);
  if (!data?.questionnaire) return null;
  return (
    <div style={{ marginBottom: 16 }}><Panel title={`Your onboarding: ${data.questionnaire.name}`}>
      {data.response && <p className="small muted">Answered {ago(data.response.created_at)} — score {data.response.score}{data.response.segment ? `, ${data.response.segment}` : ""}. You can update your answers.</p>}
      <div className="form"><Answers questions={data.questionnaire.questions} value={answers} onChange={setAnswers} />
        <button className="btn primary" disabled={busy} onClick={() => run(() => api("/onboarding/signup", { body: { answers } }), "Thanks — profile saved").then(reload)}>Save my answers</button></div>
    </Panel></div>
  );
}
