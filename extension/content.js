// Floating control on LinkedIn pages, so the job can be started from the page itself (no toolbar popup needed).
(function () {
  const path = location.pathname;
  const onSaved = path.startsWith("/my-items/saved-posts");
  const onProfile = path.startsWith("/in/");
  if (!onSaved && !onProfile) return;
  if (document.getElementById("scholarpilot-box")) return;

  const box = document.createElement("div");
  box.id = "scholarpilot-box";
  box.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;font:14px system-ui,sans-serif;" +
    "background:#f7f1e6;color:#2b2a23;border:1px solid #cfc3a8;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.25);padding:12px;width:290px";
  box.innerHTML = '<b>ScholarPilot</b><div style="margin:6px 0;color:#666">' +
    (onSaved ? "Scroll all saved posts and send the scholarship ones to your server." : "Import this profile into your ScholarPilot profile (only empty fields are filled).") +
    '</div><button id="scholarpilot-btn" style="width:100%;padding:9px;border:0;border-radius:10px;background:#34503d;color:#fff;font-weight:600;cursor:pointer">' +
    (onSaved ? "Scroll &amp; send saved posts" : "Import my profile") + '</button>' +
    (onSaved ? '<button id="scholarpilot-stop" style="width:100%;padding:6px;margin-top:6px;border-radius:10px;border:1px solid #cfc3a8;background:none;cursor:pointer;display:none">Stop scrolling and send what is loaded</button>' : "") +
    '<div id="scholarpilot-status" data-phase="idle" style="white-space:pre-line;margin-top:8px;color:#444"></div>';
  document.body.appendChild(box);

  const statusEl = box.querySelector("#scholarpilot-status");
  const btn = box.querySelector("#scholarpilot-btn");
  const stop = box.querySelector("#scholarpilot-stop");

  function paint(s) {
    if (!s) return;
    statusEl.dataset.phase = s.phase || "idle";
    statusEl.textContent = s.text || "";
    const active = s.phase === "scrolling" || s.phase === "sending";
    btn.disabled = active;
    if (stop) stop.style.display = s.phase === "scrolling" ? "block" : "none";
  }
  chrome.storage.local.get("status", v => paint(v.status));
  chrome.storage.onChanged.addListener(c => { if (c.status) paint(c.status.newValue); });

  btn.onclick = () => {
    statusEl.dataset.phase = "starting";
    statusEl.textContent = "Starting…";
    chrome.runtime.sendMessage({ type: onSaved ? "start" : "profile" });
  };
  if (stop) stop.onclick = () => chrome.runtime.sendMessage({ type: "stop" });
})();
