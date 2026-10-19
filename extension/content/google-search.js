// Google Search: "Track this keyword" for the current query, an "Audit" chip on each organic result, and
// "Save as competitor" for the business (Google Business Profile) panel when one is shown.
(() => {
  const G = window.__genranq;
  if (!G) return;
  const params = new URLSearchParams(location.search);
  const q = (params.get("q") || "").trim();
  const country = (params.get("gl") || "us").toLowerCase().slice(0, 2);
  const lang = (params.get("hl") || document.documentElement.lang || "en").toLowerCase().slice(0, 2);

  function businessPanel() {
    const panel = document.querySelector("#rhs [data-attrid='title']")?.closest("#rhs") || document.querySelector("#rhs");
    if (!panel) return null;
    const name = panel.querySelector("[data-attrid='title']")?.textContent?.trim();
    if (!name) return null;
    const site = [...panel.querySelectorAll("a[href^='http']")].find((a) => /website/i.test(a.textContent || "") && !/google\./.test(a.hostname));
    const address = panel.querySelector("[data-attrid*='address'] span:last-child")?.textContent?.trim();
    const phone = panel.querySelector("[data-attrid*='phone'] span:last-child")?.textContent?.trim();
    const ratingTxt = panel.querySelector("[aria-label*='Rated']")?.getAttribute("aria-label") || "";
    const rating = parseFloat((ratingTxt.match(/([0-9.]+)/) || [])[1]);
    const category = panel.querySelector("[data-attrid='subtitle']")?.textContent?.trim();
    if (!address && !phone && !site) return null; // not a local business panel
    return { name, website: site?.href || null, address: address || null, phone: phone || null, category: category || null,
             rating: Number.isFinite(rating) && rating <= 5 ? rating : null, maps_url: location.href.split("#")[0] };
  }

  function decorateResults() {
    document.querySelectorAll("#search a h3").forEach((h3) => {
      const a = h3.closest("a");
      if (!a || a.dataset.genranqDone || !/^https?:/.test(a.href) || /google\./.test(a.hostname)) return;
      a.dataset.genranqDone = "1";
      const origin = new URL(a.href).origin;
      h3.parentElement.appendChild(G.inlineButton("Audit", () =>
        G.act({ type: "audit", url: origin }, (d) => `Audit queued for ${d.domain} (${d.pages_granted} pages)`)));
    });
  }

  const buttons = [];
  if (q) buttons.push({ label: `Track “${q.length > 40 ? q.slice(0, 40) + "…" : q}”`, title: "Track this keyword in Genranq",
                        run: () => G.act({ type: "keyword", phrase: q, country, lang }, (d) => d.added ? `Now tracking “${d.phrase}”` : `“${d.phrase}” is already tracked`) });
  const biz = businessPanel();
  if (biz) {
    buttons.push({ label: "Save business as competitor", run: () => G.act({ type: "competitor", business: biz },
      (d) => d.duplicate ? `${biz.name} was already saved` : `Saved ${biz.name}${d.domain ? ` (${d.domain})` : ""} as a competitor`) });
    if (biz.website) buttons.push({ label: "Audit this business site", run: () => G.act({ type: "audit", url: new URL(biz.website).origin },
      (d) => `Audit queued for ${d.domain}`) });
  }
  G.setButtons(buttons);
  decorateResults();
  new MutationObserver(() => decorateResults()).observe(document.getElementById("search") || document.body, { childList: true, subtree: true });
})();
