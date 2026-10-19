// Service worker: the only place that talks to the Genranq API. Content scripts and the popup send it
// messages; it attaches the extension token (Authorization: Bearer gqx_…) and calls /api/social/ext/*.
const EXT_PATH = "/api/social/ext";

async function settings() {
  return chrome.storage.local.get(["apiBase", "token", "projectId", "projectName"]);
}

async function call(path, method = "GET", body) {
  const s = await settings();
  if (!s.apiBase || !s.token) throw new Error("Open the Genranq extension and sign in with an extension token first.");
  const res = await fetch(s.apiBase + EXT_PATH + path, {
    method,
    headers: { Authorization: `Bearer ${s.token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const d = data && data.detail;
    const msg = typeof d === "string" ? d : (d && d.message) || (Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `HTTP ${res.status}`);
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}

async function withProject(fn) {
  const s = await settings();
  if (!s.projectId) throw new Error("Choose a project in the Genranq extension popup first.");
  return fn(s.projectId, s);
}

const actions = {
  me: () => call("/me"),
  audit: ({ url, maxPages }) => withProject((project_id) => call("/audit", "POST", { project_id, url, max_pages: maxPages || 100 })),
  keyword: ({ phrase, country, lang }) => withProject((project_id) => call("/keyword", "POST", { project_id, phrase, country: country || "us", lang: lang || "en" })),
  competitor: ({ business }) => withProject((project_id) => call("/competitor", "POST", { project_id, ...business })),
  status: async () => { const s = await settings(); return { signedIn: !!(s.apiBase && s.token), projectName: s.projectName || null, apiBase: s.apiBase || null }; },
};

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  const fn = actions[msg && msg.type];
  if (!fn) return false;
  Promise.resolve(fn(msg))
    .then((data) => reply({ ok: true, data }))
    .catch((e) => reply({ ok: false, error: e.message || String(e), status: e.status }));
  return true; // async reply
});
