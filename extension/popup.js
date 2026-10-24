const LOCAL = (typeof SP_CONFIG !== "undefined" && SP_CONFIG) || {};
let BASE = LOCAL.srv || "http://127.0.0.1:8000", TOKEN = LOCAL.tok || "";
const $ = id => document.getElementById(id);
const STALE_MS = 90000; // an "active" status this old belongs to a job that died with its service worker
const msg = (t, cls = "") => { $("msg").textContent = t; $("msg").className = cls; };
const hdrs = () => ({ "Content-Type": "application/json", ...(TOKEN ? { "X-API-Key": TOKEN } : {}) });

async function ping() {
  try {
    const r = await fetch(BASE + "/favicon.ico");
    $("conn").textContent = r.status < 500 ? "Connected to " + new URL(BASE).host : "Server answered " + r.status;
  } catch (e) { $("conn").textContent = "Server not reachable"; }
}

chrome.storage.local.get(["srv", "tok", "limit"], v => {
  if (v.srv) BASE = v.srv.replace(/\/$/, "");
  TOKEN = v.tok || LOCAL.tok || "";
  $("srv").value = v.srv || ""; $("tok").value = v.tok || ""; $("lim").value = v.limit || "";
  ping();
});

$("savecfg").onclick = async () => {
  const srv = $("srv").value.trim().replace(/\/$/, ""), tok = $("tok").value.trim();
  if (srv && !srv.startsWith("http://127.0.0.1")) {
    const ok = await chrome.permissions.request({ origins: [srv + "/*"] });
    if (!ok) return msg("Permission for that server was denied.", "err");
  }
  chrome.storage.local.set({ srv, tok, limit: Number($("lim").value) || 0 });
  BASE = srv || "http://127.0.0.1:8000"; TOKEN = tok || LOCAL.tok || ""; msg("Saved.", "ok"); ping();
};

function paint(s) {
  if (!s) return;
  let active = s.phase === "scrolling" || s.phase === "sending";
  if (active && Date.now() - (s.t || 0) > STALE_MS) { active = false; s = { phase: "idle", text: "The last run was interrupted. You can start again.", pct: 0 }; }
  $("stop").style.display = active && s.phase === "scrolling" ? "block" : "none";
  $("reset").style.display = active ? "block" : "none";
  $("go").disabled = active; $("prof").disabled = active;
  $("bar").style.display = active || s.phase === "done" ? "block" : "none";
  $("fill").style.width = (s.pct || 0) + "%";
  msg(s.text || "", s.phase === "error" ? "err" : s.phase === "done" ? "ok" : "");
}
chrome.storage.local.get("status", v => paint(v.status));
chrome.storage.onChanged.addListener(c => { if (c.status) paint(c.status.newValue); });
$("reset").onclick = () => chrome.storage.local.set({ status: { phase: "idle", text: "", pct: 0, t: Date.now() } });

$("go").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/linkedin\.com\/my-items\/saved-posts/.test(tab.url || "")) {
    await chrome.tabs.create({ url: "https://www.linkedin.com/my-items/saved-posts/#scholarpilot-autostart" });
    return window.close();
  }
  msg("Starting…");
  chrome.runtime.sendMessage({ type: "start", tabId: tab.id });
};
$("stop").onclick = () => chrome.runtime.sendMessage({ type: "stop" });

$("prof").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/linkedin\.com\/in\//.test(tab.url || "")) {
    msg("Opening your profile: click this button again once it has loaded.");
    return chrome.tabs.create({ url: "https://www.linkedin.com/in/me/" });
  }
  msg("Reading your profile…");
  chrome.runtime.sendMessage({ type: "profile", tabId: tab.id, overwrite: $("ow").checked });
};
