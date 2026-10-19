// API client: bearer tokens, silent refresh-token rotation, structured errors.
export class ApiError extends Error {
  status: number;
  code?: string;
  detail: any;
  constructor(status: number, detail: any) {
    const msg = typeof detail === "string" ? detail : detail?.message || (Array.isArray(detail) ? detail.map((d: any) => d.msg).join("; ") : "Request failed");
    super(msg);
    this.status = status;
    this.detail = detail;
    this.code = typeof detail === "object" && detail ? detail.code : undefined;
  }
}

const KEY = "genranq.session";
type Session = { access_token: string; refresh_token: string; step_up_token?: string; step_up_until?: number };

function load(): Session | null {
  if ((window as any).__GQ_DEMO__) return { access_token: "demo", refresh_token: "demo" };  // read-only snapshot build
  try { const s = sessionStorage.getItem(KEY) || localStorage.getItem(KEY); return s ? JSON.parse(s) : null; } catch { return null; }
}
let session: Session | null = load();
const listeners = new Set<() => void>();

export function setSession(s: Session | null) {
  session = s;
  try { if (s) localStorage.setItem(KEY, JSON.stringify(s)); else localStorage.removeItem(KEY); } catch { /* storage unavailable */ }
  listeners.forEach((l) => l());
}
export const getSession = () => session;
export const onSession = (l: () => void) => { listeners.add(l); return () => listeners.delete(l); };

export function setStepUp(token: string, minutes: number) {
  if (!session) return;
  setSession({ ...session, step_up_token: token, step_up_until: Date.now() + minutes * 60_000 - 5000 });
}
export const steppedUp = () => !!(session?.step_up_until && session.step_up_until > Date.now());

let refreshing: Promise<boolean> | null = null;
async function refresh(): Promise<boolean> {
  if (!session?.refresh_token) return false;
  refreshing ||= (async () => {
    const r = await fetch("/api/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: session!.refresh_token }) });
    if (!r.ok) { setSession(null); return false; }
    const j = await r.json();
    setSession({ ...session!, access_token: j.access_token, refresh_token: j.refresh_token });
    return true;
  })().finally(() => setTimeout(() => (refreshing = null), 0));
  return refreshing;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: any; form?: FormData; stepUp?: boolean; raw?: boolean } = {}): Promise<T> {
  const doFetch = () => {
    const headers: Record<string, string> = {};
    const tok = opts.stepUp && steppedUp() ? session?.step_up_token : session?.access_token;
    if (tok) headers.Authorization = `Bearer ${tok}`;
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    return fetch(`/api${path}`, { method: opts.method || (opts.body !== undefined || opts.form ? "POST" : "GET"), headers,
      body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined) });
  };
  let r = await doFetch();
  if (r.status === 401 && session?.refresh_token && !path.startsWith("/auth/login")) {
    if (await refresh()) r = await doFetch();
  }
  if (opts.raw) {
    if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({}))).detail);
    return r as any;
  }
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) throw new ApiError(r.status, data?.detail ?? r.statusText);
  return data as T;
}

export async function download(path: string, filename: string) {
  const r: Response = await api(path, { raw: true });
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
