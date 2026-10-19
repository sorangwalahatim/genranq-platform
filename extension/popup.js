// Popup: sign in with an extension token, pick the target project, and act on the current tab.
const $ = (id) => document.getElementById(id);
const send = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false, error: chrome.runtime.lastError?.message })));
let tab = null;
let pageInfo = { url: null, query: null, business: null, country: "us", lang: "en" };

function show(text, ok = true, link) {
  const m = $("msg");
  m.className = ok ? "ok" : "err";
  m.textContent = text;
  if (link) {
    chrome.storage.local.get("apiBase").then(({ apiBase }) => {
      const a = document.createElement("a");
      a.href = apiBase + link; a.target = "_blank"; a.textContent = " Open in Genranq";
      m.appendChild(a);
    });
  }
}

async function defaults() {
  try { const r = await fetch(chrome.runtime.getURL("config.json")); return await r.json(); } catch { return {}; }
}

function originOf(u) { const x = new URL(u); return `${x.protocol}//${x.host}`; }

async function readPage() {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url || !/^https?:/.test(tab.url)) return;
  const u = new URL(tab.url);
  pageInfo.url = tab.url;
  const isGoogle = /(^|\.)google\.[a-z.]+$/.test(u.hostname);
  if (isGoogle && u.pathname === "/search") {
    pageInfo.query = u.searchParams.get("q");
    pageInfo.country = (u.searchParams.get("gl") || "us").toLowerCase().slice(0, 2);
    pageInfo.lang = (u.searchParams.get("hl") || "en").toLowerCase().slice(0, 2);
    pageInfo.url = null; // auditing google.com makes no sense; content buttons audit individual results
  }
  if (isGoogle && u.pathname.startsWith("/maps")) pageInfo.url = null; // Maps details come from the in-page button
  const bits = [];
  if (pageInfo.url) bits.push(`Site: ${originOf(pageInfo.url)}`);
  if (pageInfo.query) bits.push(`Search: “${pageInfo.query}”`);
  if (isGoogle && u.pathname.startsWith("/maps")) bits.push("On Google Maps, use the “Save as competitor” button on the place panel.");
  $("page").textContent = bits.join(" · ") || "Open a website, a Google search or a Google Maps place.";
  $("audit").disabled = !pageInfo.url;
  $("keyword").disabled = !pageInfo.query;
  $("competitor").disabled = true;
  $("competitor").title = "Use the button shown on Google Maps / the business panel in Google Search";
}

async function loadMe() {
  const r = await send({ type: "me" });
  if (!r.ok) {
    if (r.status === 401) { await chrome.storage.local.remove("token"); }
    $("login").hidden = false; $("main").hidden = true;
    if (r.error) show(r.error, false);
    return;
  }
  const me = r.data;
  $("who").textContent = `${me.user.name || me.user.email} · ${me.organisation}`;
  const { projectId } = await chrome.storage.local.get("projectId");
  const sel = $("project");
  sel.innerHTML = "";
  for (const p of me.projects) {
    const o = document.createElement("option");
    o.value = p.id; o.textContent = p.name; if (p.id === projectId) o.selected = true;
    sel.appendChild(o);
  }
  if (!me.projects.length) show("No projects yet — create one in Genranq.", false);
  await saveProject();
  $("login").hidden = true; $("main").hidden = false;
  if (!me.scopes.includes("audit:create")) $("audit").disabled = true;
  if (!me.scopes.includes("keyword:create")) $("keyword").disabled = true;
}

async function saveProject() {
  const sel = $("project");
  const opt = sel.options[sel.selectedIndex];
  if (opt) await chrome.storage.local.set({ projectId: opt.value, projectName: opt.textContent });
}

$("signin").addEventListener("click", async () => {
  let base = $("apiBase").value.trim().replace(/\/+$/, "");
  const token = $("token").value.trim();
  if (!/^https?:\/\//.test(base) || !token.startsWith("gqx_")) return show("Enter the Genranq address and a token starting with gqx_", false);
  base = originOf(base);
  const granted = await chrome.permissions.request({ origins: [base + "/*"] }).catch(() => false);
  if (!granted) return show("Permission to reach your Genranq address is required.", false);
  await chrome.storage.local.set({ apiBase: base, token });
  $("token").value = "";
  await loadMe();
  if (!$("main").hidden) show("Signed in.");
});

$("signout").addEventListener("click", async () => {
  await chrome.storage.local.remove(["token", "projectId", "projectName"]);
  $("main").hidden = true; $("login").hidden = false; $("who").textContent = "";
  show("Signed out. Revoke the token in Genranq if you no longer need it.");
});

$("project").addEventListener("change", saveProject);

$("audit").addEventListener("click", async () => {
  $("audit").disabled = true;
  const r = await send({ type: "audit", url: originOf(pageInfo.url), maxPages: Number($("maxPages").value) || 100 });
  $("audit").disabled = false;
  r.ok ? show(`Audit queued for ${r.data.domain} (${r.data.pages_granted} pages).`, true, r.data.link) : show(r.error, false);
});

$("keyword").addEventListener("click", async () => {
  const r = await send({ type: "keyword", phrase: pageInfo.query, country: pageInfo.country, lang: pageInfo.lang });
  r.ok ? show(r.data.added ? `Now tracking “${r.data.phrase}”.` : `“${r.data.phrase}” is already tracked.`, true, r.data.link) : show(r.error, false);
});

(async () => {
  const d = await defaults();
  const { apiBase, token } = await chrome.storage.local.get(["apiBase", "token"]);
  $("apiBase").value = apiBase || d.default_api_base || "";
  await readPage();
  if (apiBase && token) await loadMe(); else $("login").hidden = false;
})();
