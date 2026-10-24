// Floating control on LinkedIn pages: start the job from the page itself, and auto-start when the app opens this page.
(function () {
  const path = location.pathname;
  const onSaved = path.startsWith("/my-items/saved-posts");
  const onProfile = path.startsWith("/in/");
  if (!onSaved && !onProfile) return;
  if (document.getElementById("scholarpilot-box")) return;
  const STALE_MS = 90000;
  const ICON = chrome.runtime.getURL("icons/icon48.png");

  const css = document.createElement("style");
  css.textContent = `
  #scholarpilot-box{position:fixed;right:18px;bottom:18px;z-index:2147483647;width:300px;font:14px/1.4 "Segoe UI",system-ui,sans-serif;color:#2b2a23;
    background:#f7f1e6;border:1px solid #d9ccb2;border-radius:16px;box-shadow:0 12px 36px rgba(0,0,0,.28);overflow:hidden}
  #scholarpilot-box .sp-h{display:flex;align-items:center;gap:10px;padding:10px 12px;background:linear-gradient(180deg,#a9c3d6,#ecd7b8)}
  #scholarpilot-box .sp-h img{width:28px;height:28px;border-radius:8px}
  #scholarpilot-box .sp-h b{flex:1}
  #scholarpilot-box .sp-x{border:0;background:none;font-size:18px;cursor:pointer;color:#4b4a3f}
  #scholarpilot-box .sp-b{padding:12px}
  #scholarpilot-box button.sp-go{width:100%;padding:10px;border:0;border-radius:11px;background:#34503d;color:#fff;font:600 14px inherit;cursor:pointer}
  #scholarpilot-box button.sp-go:disabled{opacity:.55;cursor:default}
  #scholarpilot-box button.sp-2{width:100%;margin-top:6px;padding:7px;border-radius:11px;border:1px solid #d9ccb2;background:#fffaf0;cursor:pointer;display:none}
  #scholarpilot-box .sp-bar{height:6px;background:#e6dcc5;border-radius:6px;margin-top:10px;overflow:hidden;display:none}
  #scholarpilot-box .sp-bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#6f95b5,#34503d);transition:width .3s}
  #scholarpilot-box #scholarpilot-status{white-space:pre-line;margin-top:8px;font-size:13px;color:#444}
  #scholarpilot-box #scholarpilot-status[data-phase=error]{color:#a3321f}
  #scholarpilot-box #scholarpilot-status[data-phase=done]{color:#34503d}`;
  document.head.appendChild(css);

  const box = document.createElement("div");
  box.id = "scholarpilot-box";
  box.innerHTML = `<div class="sp-h"><img src="${ICON}" alt=""><b>ScholarPilot</b><button class="sp-x" title="Hide">×</button></div>
  <div class="sp-b"><div style="color:#7a7260;margin-bottom:8px">${onSaved
      ? "Scrolls your newest saved posts and sends the scholarship ones to your server."
      : "Reads this profile into your ScholarPilot profile."}</div>
  <button class="sp-go" id="scholarpilot-btn">${onSaved ? "Scroll &amp; send saved posts" : "Import my profile"}</button>
  <button class="sp-2" id="scholarpilot-stop">Stop and send what is loaded</button>
  <div class="sp-bar"><i></i></div>
  <div id="scholarpilot-status" data-phase="idle"></div></div>`;
  document.body.appendChild(box);

  const statusEl = box.querySelector("#scholarpilot-status"), btn = box.querySelector("#scholarpilot-btn");
  const stop = box.querySelector("#scholarpilot-stop"), bar = box.querySelector(".sp-bar"), fill = bar.firstElementChild;
  box.querySelector(".sp-x").onclick = () => box.remove();

  function paint(s) {
    if (!s) return;
    let active = s.phase === "scrolling" || s.phase === "sending";
    if (active && Date.now() - (s.t || 0) > STALE_MS) { active = false; s = { phase: "idle", text: "The last run was interrupted. You can start again.", pct: 0 }; }
    statusEl.dataset.phase = s.phase || "idle";
    statusEl.textContent = s.text || "";
    btn.disabled = active;
    stop.style.display = s.phase === "scrolling" && active && onSaved ? "block" : "none";
    bar.style.display = active || s.phase === "done" ? "block" : "none";
    fill.style.width = (s.pct || 0) + "%";
  }
  chrome.storage.local.get("status", v => paint(v.status));
  chrome.storage.onChanged.addListener(c => { if (c.status) paint(c.status.newValue); });

  function start() {
    statusEl.dataset.phase = "starting";
    statusEl.textContent = "Starting…";
    chrome.runtime.sendMessage({ type: onSaved ? "start" : "profile" });
  }
  btn.onclick = start;
  stop.onclick = () => chrome.runtime.sendMessage({ type: "stop" });

  // opened by the ScholarPilot app: begin once the list has rendered, then drop the marker from the address
  if (location.hash.includes("scholarpilot-autostart")) {
    history.replaceState(null, "", location.pathname + location.search);
    let tries = 0;
    const t = setInterval(() => {
      tries++;
      if (document.querySelectorAll("li").length > 2 || tries > 20) { clearInterval(t); start(); }
    }, 700);
  }
})();
