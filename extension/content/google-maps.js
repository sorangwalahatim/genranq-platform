// Google Maps: when a place panel is open, offer "Save as competitor" and "Audit website".
// Maps is a single-page app, so the place is re-read whenever the URL or panel changes.
(() => {
  const G = window.__genranq;
  if (!G) return;
  let lastKey = "";

  function readPlace() {
    const main = document.querySelector("div[role='main'][aria-label]");
    const name = main?.getAttribute("aria-label") || document.querySelector("h1")?.textContent?.trim();
    if (!name || !/\/maps\/place\//.test(location.pathname)) return null;
    const scope = main || document;
    const site = scope.querySelector("a[data-item-id='authority']")?.href || null;
    const address = scope.querySelector("button[data-item-id='address']")?.getAttribute("aria-label")?.replace(/^Address:\s*/i, "") || null;
    const phone = scope.querySelector("button[data-item-id^='phone']")?.getAttribute("aria-label")?.replace(/^Phone:\s*/i, "") || null;
    const category = scope.querySelector("button[jsaction*='category']")?.textContent?.trim() || null;
    const rl = scope.querySelector("span[role='img'][aria-label*='star']")?.getAttribute("aria-label") || "";
    const rating = parseFloat((rl.match(/([0-9.]+)/) || [])[1]);
    const rv = scope.querySelector("span[aria-label*='review']")?.getAttribute("aria-label") || "";
    const reviews = parseInt((rv.replace(/[,.\s]/g, "").match(/(\d+)/) || [])[1], 10);
    return { name, website: site, address, phone, category, rating: Number.isFinite(rating) && rating <= 5 ? rating : null,
             reviews: Number.isFinite(reviews) ? reviews : null, maps_url: location.href.split("?")[0] };
  }

  function refresh() {
    const p = readPlace();
    const key = p ? `${p.name}|${p.address}|${p.website}` : "";
    if (key === lastKey) return;
    lastKey = key;
    if (!p) { G.setButtons([]); return; }
    const defs = [{ label: "Save as competitor", title: `Save ${p.name} to your Genranq project`,
                    run: () => G.act({ type: "competitor", business: p }, (d) => d.duplicate ? `${p.name} was already saved` : `Saved ${p.name} as a competitor`) }];
    if (p.website) defs.push({ label: "Audit website", run: () => G.act({ type: "audit", url: new URL(p.website).origin }, (d) => `Audit queued for ${d.domain}`) });
    G.setButtons(defs);
  }

  setInterval(refresh, 1500);
  refresh();
})();
