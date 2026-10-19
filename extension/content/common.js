// Shared helpers for the Google Search / Maps content scripts. UI lives in a closed shadow root so page
// styles can't break it and the page can't read it. All network calls go through the service worker.
(() => {
  if (window.__genranq) return;
  const send = (msg) => new Promise((res) => {
    try { chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false, error: chrome.runtime.lastError?.message || "Extension unavailable" })); }
    catch (e) { res({ ok: false, error: "Extension was updated — reload this page." }); }
  });

  const host = document.createElement("div");
  host.id = "genranq-ext-root";
  host.style.cssText = "position:fixed;z-index:2147483646;right:16px;bottom:16px;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
    :host { all: initial; }
    .bar { display:flex; flex-direction:column; gap:6px; align-items:flex-end; font:13px/1.4 system-ui, sans-serif; }
    button { all: unset; cursor:pointer; background:#1f3a5f; color:#fff; padding:8px 12px; border-radius:8px; box-shadow:0 4px 14px rgba(0,0,0,.18); font:500 13px system-ui, sans-serif; }
    button:hover { filter:brightness(1.1); } button[disabled] { opacity:.5; cursor:default; }
    .toast { background:#111827; color:#fff; padding:8px 12px; border-radius:8px; max-width:320px; box-shadow:0 4px 14px rgba(0,0,0,.2); }
    .toast.err { background:#b42318; } .tag { font-size:11px; opacity:.75; }
  </style><div class="bar"></div>`;
  const bar = root.querySelector(".bar");
  const attach = () => document.body && !host.isConnected && document.body.appendChild(host);
  attach();

  function toast(text, ok = true) {
    const t = document.createElement("div");
    t.className = "toast" + (ok ? "" : " err");
    t.textContent = text;
    bar.prepend(t);
    setTimeout(() => t.remove(), ok ? 4000 : 7000);
  }

  function setButtons(defs) {
    attach();
    bar.querySelectorAll("button").forEach((b) => b.remove());
    for (const d of defs) {
      const b = document.createElement("button");
      b.textContent = d.label;
      b.title = d.title || d.label;
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { await d.run(); } finally { b.disabled = false; }
      });
      bar.appendChild(b);
    }
  }

  async function act(msg, okText) {
    const r = await send(msg);
    if (r.ok) toast(okText(r.data)); else toast(r.error || "Something went wrong", false);
    return r;
  }

  function inlineButton(label, onClick) {
    const b = document.createElement("span");
    b.textContent = label;
    b.setAttribute("role", "button");
    b.tabIndex = 0;
    b.style.cssText = "display:inline-block;margin-left:8px;padding:1px 7px;border:1px solid #1f3a5f;border-radius:10px;color:#1f3a5f;font:500 11px system-ui,sans-serif;cursor:pointer;vertical-align:middle;background:#fff;";
    const go = (e) => { e.preventDefault(); e.stopPropagation(); onClick(); };
    b.addEventListener("click", go);
    b.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && go(e));
    b.dataset.genranq = "1";
    return b;
  }

  window.__genranq = { send, toast, setButtons, act, inlineButton };
})();
