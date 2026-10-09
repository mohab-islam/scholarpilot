// Runs the whole job in the service worker so it keeps going if the popup is closed.
let running = false, stopFlag = false;

const setStatus = s => chrome.storage.local.set({ status: { ...s, t: Date.now() } });

// ---- injected into the LinkedIn page -------------------------------------------------
function stepScroll() {
  window.scrollTo(0, document.body.scrollHeight);
  document.querySelectorAll("main, [class*='scaffold-finite-scroll'], [class*='scaffold-layout__main']")
    .forEach(e => { e.scrollTop = e.scrollHeight; });
  const btn = [...document.querySelectorAll("button")].find(b => /show more|load more|see more results/i.test(b.innerText) && !b.disabled);
  if (btn) btn.click();
  return new Promise(r => setTimeout(() => r({ h: document.body.scrollHeight, n: document.querySelectorAll("li").length }), 1800));
}

function scrapePosts() {
  const norm = t => t.toLowerCase().replace(/\W+/g, " ").trim();
  const cands = [];
  document.querySelectorAll("li, div.entity-result, div.reusable-search__result-container").forEach(el => {
    const text = (el.innerText || "").trim();
    if (text.length < 80 || text.length > 8000) return;
    const a = el.querySelector('a[href*="/feed/update/"], a[href*="/posts/"], a[href*="activity"]');
    const links = [...el.querySelectorAll("a[href]")].map(x => x.href)
      .filter(h => h.startsWith("http") && (!h.includes("linkedin.com") || h.includes("/safety/go")));
    cands.push({ url: a ? a.href.split("?")[0] : "", author: "", text, links: [...new Set(links)], n: norm(text) });
  });
  // keep the outermost container of each post: drop elements whose text is contained in a bigger one
  cands.sort((x, y) => y.n.length - x.n.length);
  const kept = [];
  for (const c of cands) if (!kept.some(k => k.n.includes(c.n.slice(0, 200)))) kept.push(c);
  return kept.map(({ n, ...rest }) => rest);
}
// -------------------------------------------------------------------------------------

function activityId(p) { const m = /(?:urn:li:activity:|activity-)(\d{8,})/.exec(p.url || ""); return m ? m[1] : ""; }

async function run(tabId) {
  if (running) return;
  running = true; stopFlag = false;
  try {
    const cfg = await chrome.storage.local.get(["srv", "tok"]);
    const base = (cfg.srv || "http://127.0.0.1:8000").replace(/\/$/, "");
    const headers = { "Content-Type": "application/json", ...(cfg.tok ? { "X-API-Key": cfg.tok } : {}) };

    // 1) scroll until nothing new loads
    let same = 0, last = "", iter = 0;
    while (!stopFlag && same < 4 && iter < 250) {
      const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: stepScroll });
      const sig = result.h + ":" + result.n;
      same = sig === last ? same + 1 : 0; last = sig; iter++;
      await setStatus({ phase: "scrolling", text: `Scrolling… ${result.n} items loaded (round ${iter})`, pct: Math.min(40, iter / 5) });
    }

    // 2) collect and de-duplicate
    const [{ result: raw }] = await chrome.scripting.executeScript({ target: { tabId }, func: scrapePosts });
    const seen = new Set(), posts = [];
    for (const p of raw) {
      const key = activityId(p) || p.url || p.text.slice(0, 160).toLowerCase().replace(/\W+/g, " ");
      if (seen.has(key)) continue;
      seen.add(key); posts.push(p);
    }
    const removed = raw.length - posts.length;
    if (!posts.length) { await setStatus({ phase: "done", text: "No posts found on this page. Open LinkedIn → Saved posts first.", pct: 100 }); return; }

    // 3) send in small batches (the server skips non-scholarship posts and ones it already processed)
    const tot = { received: 0, duplicates: 0, already_seen: 0, not_scholarship: 0, scholarships: 0, filtered_out: 0, errors: 0 };
    const BATCH = 6;
    for (let i = 0; i < posts.length; i += BATCH) {
      await setStatus({ phase: "sending", text: `Analysing posts ${i + 1}–${Math.min(i + BATCH, posts.length)} of ${posts.length}…`, pct: 40 + 60 * i / posts.length });
      const r = await fetch(base + "/api/linkedin/ingest", { method: "POST", headers, body: JSON.stringify({ posts: posts.slice(i, i + BATCH) }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { await setStatus({ phase: "error", text: "Server said: " + (j.detail || r.status), pct: 100 }); return; }
      for (const k of Object.keys(j)) tot[k] = (tot[k] || 0) + j[k];
    }
    const dup = removed + tot.duplicates;
    await setStatus({ phase: "done", pct: 100, text:
      `Done. ${posts.length} unique posts (${dup} duplicates removed).\n` +
      `• ${tot.scholarships} scholarships added\n• ${tot.not_scholarship} posts skipped as not scholarship-related\n` +
      `• ${tot.already_seen} already processed in an earlier run\n` +
      `• ${tot.filtered_out} found but not eligible or already closed` + (tot.errors ? `\n• ${tot.errors} errors (will retry next run)` : "") });
  } catch (e) {
    await setStatus({ phase: "error", text: "Error: " + e.message, pct: 100 });
  } finally { running = false; }
}

chrome.runtime.onMessage.addListener((msg, _s, reply) => {
  if (msg.type === "start") { run(msg.tabId); reply({ ok: true }); }
  if (msg.type === "stop") { stopFlag = true; reply({ ok: true }); }
});
