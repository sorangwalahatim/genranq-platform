// Read-only snapshot mode (VITE_DEMO=1): the real UI, served from recorded API responses.
// Writes are refused with a clear message; nothing pretends to save.
type Rec = Record<string, { s: number; b: any }>;
export const DEMO = import.meta.env.VITE_DEMO === "1";

function key(method: string, url: string) {
  const u = new URL(url, "http://x");
  const q = [...u.searchParams.entries()].sort().map(([k, v]) => `${k}=${v}`).join("&");
  return `${method} ${u.pathname}${q ? "?" + q : ""}`;
}

export function installDemo() {
  const rec: Rec = (window as any).__GQ_DEMO__ || {};
  const byPath: Record<string, string> = {};
  for (const k of Object.keys(rec)) { const p = k.split("?")[0]; if (!byPath[p]) byPath[p] = k; }
  try {
    localStorage.setItem("genranq.session", JSON.stringify({ access_token: "demo", refresh_token: "demo" }));
  } catch { /* storage unavailable: session lives in memory via api.ts */ }
  const real = window.fetch.bind(window);
  const json = (s: number, b: any) => new Response(JSON.stringify(b), { status: s, headers: { "Content-Type": "application/json" } });
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.startsWith("/api")) return real(input as any, init);
    const method = (init?.method || "GET").toUpperCase();
    if (method !== "GET") {
      if (url.startsWith("/api/auth/logout") || url.startsWith("/api/notifications/read")) return json(200, { ok: true });
      return json(403, { detail: "Read-only snapshot — changes are disabled here. Run GENRANQ (docker compose up) to work with live data." });
    }
    const k = key("GET", url);
    const hit = rec[k] || rec[byPath[k.split("?")[0]]];
    if (hit) return json(hit.s, hit.b);
    return json(404, { detail: "This view wasn't captured in the snapshot." });
  };
}
