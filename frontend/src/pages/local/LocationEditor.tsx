import { useState } from "react";
import { Drawer } from "../../components/ui";
import { api } from "../../lib/api";
import { useAction } from "../../lib/hooks";
import { Loc } from "./shared";

const DAY = { mon: "Mo", tue: "Tu", wed: "We", thu: "Th", fri: "Fr", sat: "Sa", sun: "Su" } as Record<string, string>;
export const hoursToText = (h: Record<string, string[][]> = {}) =>
  Object.entries(h).map(([d, slots]) => slots.map((s) => `${DAY[d] || d} ${s[0]}-${s[1]}`).join("; ")).join("; ");
const lines = (s: string) => s.split(/\n|,/).map((x) => x.trim()).filter(Boolean);
const num = (s: string) => (s.trim() === "" ? null : Number(s));

/** Add / edit a business location or competitor listing (manual entry — works without any connection). */
export function LocationEditor({ pid, kind, loc, onClose, onSaved }: { pid: string; kind: "own" | "competitor"; loc?: Loc | null; onClose: () => void; onSaved: (l: Loc) => void }) {
  const n = loc?.nap || {};
  const [f, setF] = useState({
    name: loc?.name || "", address: n.address || "", city: n.city || "", region: n.region || "", postcode: n.postcode || "", country: n.country || "",
    phone: n.phone || "", website: n.website || "", lat: n.lat?.toString() || "", lng: n.lng?.toString() || "", place_id: loc?.place_id || "",
    primary_category: loc?.primary_category || "", secondary: (loc?.secondary_categories || []).join(", "), hours: hoursToText(loc?.hours),
    description: loc?.description || "", photos_count: String(loc?.photos_count ?? 0), has_logo: !!loc?.has_logo,
    services: (loc?.services || []).join("\n"), products: (loc?.products || []).join("\n"), attributes: (loc?.attributes || []).join("\n"),
    qa_questions: String(loc?.qa_questions ?? 0), qa_answered: String(loc?.qa_answered ?? 0), social: (loc?.social_links || []).join("\n"),
    keywords: (loc?.keywords || []).join("\n"), rating: loc?.rating?.toString() || "", review_count: loc?.review_count?.toString() || "",
    posts_per_month: loc?.posts_per_month?.toString() || "",
    citations: (loc?.citations || []).map((c: any) => [c.source, c.name, c.address, c.phone].join(" | ")).join("\n"),
  });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const { run, busy } = useAction();
  const save = async () => {
    const body: any = {
      name: f.name, place_id: f.place_id || null,
      nap: { address: f.address, city: f.city, region: f.region, postcode: f.postcode, country: f.country, phone: f.phone, website: f.website || null,
        lat: num(f.lat), lng: num(f.lng) },
      primary_category: f.primary_category || null, secondary_categories: lines(f.secondary), hours: f.hours || {},
      description: f.description || null, photos_count: Number(f.photos_count || 0), has_logo: f.has_logo,
      services: lines(f.services), products: lines(f.products), attributes: lines(f.attributes),
      qa_questions: Number(f.qa_questions || 0), qa_answered: Number(f.qa_answered || 0), social_links: lines(f.social), keywords: lines(f.keywords),
      rating: num(f.rating), review_count: num(f.review_count), posts_per_month: num(f.posts_per_month),
      citations: f.citations.split("\n").filter((x) => x.trim()).map((l) => { const [source, name, address, phone] = l.split("|").map((x) => x.trim()); return { source, name, address, phone }; }),
    };
    const r = await run<Loc>(() => loc ? api(`/local/locations/${loc.id}`, { method: "PATCH", body }) : api("/local/locations", { body: { ...body, project_id: pid, kind } }), "Saved");
    if (r) { onSaved(r); onClose(); }
  };
  const T = (k: string, label: string, hint?: string, props: any = {}) => (
    <label className="field">{label}{hint && <span className="hint">{hint}</span>}<input className="input" value={(f as any)[k]} onChange={set(k)} {...props} /></label>
  );
  const A = (k: string, label: string, hint?: string, rows = 3) => (
    <label className="field">{label}{hint && <span className="hint">{hint}</span>}<textarea className="input" rows={rows} value={(f as any)[k]} onChange={set(k)} /></label>
  );
  return (
    <Drawer title={loc ? `Edit ${loc.name}` : kind === "own" ? "Add business location" : "Add competitor listing"} onClose={onClose}
      foot={<button className="btn primary" disabled={!f.name.trim() || busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>}>
      <div className="form">
        {T("name", "Business name", undefined, { autoFocus: true })}
        <div className="grid g2">{T("address", "Street address")}{T("city", "City")}</div>
        <div className="grid g3">{T("region", "Region / state")}{T("postcode", "Postcode")}{T("country", "Country")}</div>
        <div className="grid g2">{T("phone", "Phone")}{T("website", "Website", "https URL")}</div>
        <div className="grid g3">{T("lat", "Latitude", "needed for grid scans", { inputMode: "decimal" })}{T("lng", "Longitude", undefined, { inputMode: "decimal" })}{T("place_id", "Google place ID")}</div>
        <div className="grid g2">{T("primary_category", "Primary category")}{T("secondary", "Secondary categories", "comma separated")}</div>
        {T("hours", "Opening hours", "e.g. Mo-Fr 08:00-18:00; Sa 09:00-13:00")}
        {kind === "own" && A("description", "Description", `${f.description.length} / 750 characters`, 4)}
        <div className="grid g3">
          {T("photos_count", "Photos", undefined, { inputMode: "numeric" })}
          {T("rating", "Rating (listing total)", "0–5", { inputMode: "decimal" })}
          {T("review_count", "Review count (listing total)", undefined, { inputMode: "numeric" })}
        </div>
        {kind === "competitor" && T("posts_per_month", "Posts per month", "observed cadence", { inputMode: "decimal" })}
        {kind === "own" && <>
          <label className="check"><input type="checkbox" checked={f.has_logo} onChange={set("has_logo")} /> Logo uploaded</label>
          <div className="grid g2">{A("services", "Services", "one per line")}{A("products", "Products", "one per line")}</div>
          <div className="grid g2">{A("attributes", "Attributes", "one per line")}{A("keywords", "Target local keywords", "one per line")}</div>
          <div className="grid g2">{T("qa_questions", "Q&A questions", undefined, { inputMode: "numeric" })}{T("qa_answered", "Q&A answered", undefined, { inputMode: "numeric" })}</div>
          {A("social", "Social profiles", "one URL per line")}
          {A("citations", "Citations (other listings of this business)", "one per line: Source | name | address | phone", 3)}
        </>}
      </div>
    </Drawer>
  );
}
