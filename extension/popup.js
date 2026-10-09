let BASE = "http://127.0.0.1:8000", TOKEN = "";
const $ = id => document.getElementById(id);
const msg = t => ($("msg").textContent = t);
const hdrs = () => ({ "Content-Type": "application/json", ...(TOKEN ? { "X-API-Key": TOKEN } : {}) });

chrome.storage.local.get(["srv", "tok"], v => {
  if (v.srv) BASE = v.srv.replace(/\/$/, "");
  TOKEN = v.tok || "";
  $("srv").value = v.srv || ""; $("tok").value = v.tok || "";
});

$("savecfg").onclick = async () => {
  const srv = $("srv").value.trim().replace(/\/$/, ""), tok = $("tok").value.trim();
  if (srv && !srv.startsWith("http://127.0.0.1")) {
    const ok = await chrome.permissions.request({ origins: [srv + "/*"] });
    if (!ok) return msg("Permission for that server was denied.");
  }
  chrome.storage.local.set({ srv, tok }); BASE = srv || "http://127.0.0.1:8000"; TOKEN = tok; msg("Saved.");
};

function paint(s) {
  if (!s) return;
  const active = s.phase === "scrolling" || s.phase === "sending";
  $("stop").style.display = s.phase === "scrolling" ? "block" : "none";
  $("go").disabled = active;
  $("bar").style.display = active || s.phase === "done" ? "block" : "none";
  $("fill").style.width = (s.pct || 0) + "%";
  msg(s.text || "");
}
chrome.storage.local.get("status", v => paint(v.status));
chrome.storage.onChanged.addListener(c => { if (c.status) paint(c.status.newValue); });

$("go").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab.url || !tab.url.includes("linkedin.com")) return msg("Open LinkedIn → Saved posts first.");
  msg("Starting…");
  chrome.runtime.sendMessage({ type: "start", tabId: tab.id });
};
$("stop").onclick = () => chrome.runtime.sendMessage({ type: "stop" });

// Reads YOUR OWN profile page (linkedin.com/in/...) that you have open, to pre-fill your ScholarPilot profile.
$("prof").onclick = async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!/linkedin\.com\/in\//.test(tab.url)) return msg("Open your own LinkedIn profile page first.");
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => document.body.innerText.slice(0, 40000) });
    const r = await fetch(BASE + "/api/profile/import", { method: "POST", headers: hdrs(), body: JSON.stringify({ text: result }) });
    const j = await r.json();
    msg(r.ok ? "Profile updated: " + (j.filled.join(", ") || "nothing new") : "Error: " + j.detail);
  } catch (e) { msg("Error: " + e.message); }
};
