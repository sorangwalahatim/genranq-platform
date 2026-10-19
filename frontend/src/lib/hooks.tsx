import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError, getSession, onSession, setSession } from "./api";

// ------------------------------------------------------------ data fetching
export function useApi<T = any>(path: string | null, deps: any[] = [], opts: { poll?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(!!path);
  const seq = useRef(0);
  const load = useCallback(async (quiet = false) => {
    if (!path) return;
    const n = ++seq.current;
    if (!quiet) setLoading(true);
    try {
      const d = await api<T>(path);
      if (n === seq.current) { setData(d); setError(null); }
    } catch (e: any) {
      if (n === seq.current) setError(e);
    } finally {
      if (n === seq.current) setLoading(false);
    }
  }, [path, ...deps]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!opts.poll) return;
    const t = setInterval(() => { if (!document.hidden) load(true); }, opts.poll);
    return () => clearInterval(t);
  }, [load, opts.poll]);
  return { data, error, loading, reload: () => load(true), setData };
}

// ------------------------------------------------------------ toasts
type Toast = { id: number; msg: string; err?: boolean };
const ToastCtx = createContext<(msg: string, err?: boolean) => void>(() => {});
export function ToastHost({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((msg: string, err = false) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, msg, err }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), err ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-host" role="status" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.err ? "err" : ""}`}>{t.msg}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/** Run an async action with a toast for success or error. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    setBusy(true);
    try { const r = await fn(); if (ok) toast(ok); return r; }
    catch (e: any) { toast(e.message || "Something went wrong", true); return undefined; }
    finally { setBusy(false); }
  }, [toast]);
  return { run, busy };
}

// ------------------------------------------------------------ auth / me
export type Me = {
  user: { id: string; email: string; name: string; role: string; mfa_enabled: boolean };
  organisation: { id: string; name: string; slug: string; plan: string; branding: any };
  permissions: Record<string, boolean>;
  platform_admin: boolean;
};
const MeCtx = createContext<{ me: Me | null; reload: () => void; logout: () => void }>({ me: null, reload: () => {}, logout: () => {} });

export function MeProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [hasSession, setHas] = useState(!!getSession());
  useEffect(() => { const off = onSession(() => setHas(!!getSession())); return () => { off(); }; }, []);
  const reload = useCallback(() => { if (getSession()) api<Me>("/auth/me").then(setMe).catch(() => setMe(null)); else setMe(null); }, []);
  useEffect(() => { reload(); }, [hasSession, reload]);
  const logout = useCallback(async () => {
    const s = getSession();
    if (s) await api("/auth/logout", { body: { refresh_token: s.refresh_token } }).catch(() => {});
    setSession(null); setMe(null);
  }, []);
  return <MeCtx.Provider value={{ me, reload, logout }}>{children}</MeCtx.Provider>;
}
export const useMe = () => useContext(MeCtx);
export const useCan = () => { const { me } = useMe(); return (p: string) => !!me?.permissions?.[p]; };

// ------------------------------------------------------------ project selection (persisted per viewer)
const PKEY = "genranq.project";
export function useProjectSelection(projects: { id: string }[] | null) {
  const [pid, setPid] = useState<string>(() => { try { return localStorage.getItem(PKEY) || ""; } catch { return ""; } });
  useEffect(() => {
    if (projects && projects.length && !projects.find((p) => p.id === pid)) setPid(projects[0].id);
  }, [projects, pid]);
  const set = (v: string) => { setPid(v); try { localStorage.setItem(PKEY, v); } catch { /* ignore */ } };
  return [pid, set] as const;
}
