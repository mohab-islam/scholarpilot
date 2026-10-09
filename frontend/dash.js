// Sidebar navigation, dashboard (big countdown, pinned status, missing papers), workspace to-dos, Gmail inbox.
Object.keys(TABS).forEach(k => delete TABS[k]);
Object.assign(TABS, {plan: 'Dashboard', sch: 'Scholarships', disc: 'Discover', li: 'LinkedIn', app: 'Application', mail: 'Inbox & Gmail', me: 'My profile', lab: 'Letter lab', write: 'Writer'});
const GROUPS = [['Overview', ['plan']], ['Find', ['disc', 'sch', 'li']], ['Apply', ['app', 'mail']], ['Build', ['me', 'lab', 'write']]];
const P = {
  plan: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  disc: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
  sch: '<path d="M22 9 12 4 2 9l10 5z"/><path d="M6 11.5V16c0 1.5 3 3 6 3s6-1.5 6-3v-4.5"/>',
  li: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  app: '<path d="M10 6h11M10 12h11M10 18h11"/><path d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  me: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
  lab: '<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/>',
  write: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
};
const svg = k => `<svg class="i" viewBox="0 0 24 24">${P[k]}</svg>`;
const groupOf = t => (GROUPS.find(([, ks]) => ks.includes(t)) || GROUPS[0]);

// ---- nature scenes: sky, clouds, layered forest ridges (deterministic per seed)
const PALETTES = [
  {sky: ['#e9c9a0', '#5f86a8'], forest: ['#6d8a74', '#456149', '#2d4434', '#1d3026'], sun: '#fff1cf'},   // dawn
  {sky: ['#c9dadd', '#6a8fa3'], forest: ['#7e9884', '#587260', '#3d5748', '#27392e'], sun: '#ffffff'},   // mist
  {sky: ['#e3ab8d', '#566f94'], forest: ['#5b7266', '#3e5549', '#2a3c32', '#18241e'], sun: '#ffd9b0'},   // dusk
  {sky: ['#b3d2e6', '#5b8fb8'], forest: ['#7b9a7d', '#557458', '#38523f', '#213528'], sun: '#fffbea'},   // noon
  {sky: ['#ead7b6', '#7f9fae'], forest: ['#8a9b76', '#61754f', '#44583f', '#2d3b2b'], sun: '#fff6df'},   // sand
];
function rng(seed) {
  let h = 2166136261;
  for (const ch of String(seed)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
  return () => { h += 0x6D2B79F5; let t = Math.imul(h ^ h >>> 15, 1 | h); t ^= t + Math.imul(t ^ t >>> 7, 61 | t); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
function ridge(r, W, H, base, amp, pines) {
  let d = `M0,${H}L0,${base}`;
  const step = pines ? 9 : 40, ph = r() * 6, f = 0.6 + r() * 1.2;
  for (let x = 0; x <= W; x += step) {
    let y = base + Math.sin(x / W * Math.PI * 2 * f + ph) * amp + Math.sin(x / 55 + ph) * amp * .25;
    if (pines) y -= (Math.round(x / step) % 2 ? 1 : 0) * (8 + r() * 26);
    d += `L${x},${y.toFixed(1)}`;
  }
  return d + `L${W},${H}Z`;
}
function sceneSVG(seed, W, H, soft) {
  const r = rng(seed), pal = PALETTES[Math.floor(r() * PALETTES.length)];
  const sx = W * (0.15 + r() * 0.7), sy = H * (0.2 + r() * 0.22);
  let clouds = '';
  for (let i = 0; i < 5; i++) clouds += `<ellipse cx="${(r() * W).toFixed(0)}" cy="${(H * (0.1 + r() * 0.38)).toFixed(0)}" rx="${(W * (0.1 + r() * 0.16)).toFixed(0)}" ry="${(H * (0.025 + r() * 0.04)).toFixed(0)}" fill="#fff" opacity="${(0.5 + r() * 0.35).toFixed(2)}"/>`;
  const bases = [0.62, 0.7, 0.79, 0.88];
  let hills = '';
  pal.forest.forEach((c, i) => {
    const fade = soft ? (0.55 + i * 0.15) : 1;
    hills += `<path d="${ridge(r, W, H, H * bases[i], H * (0.05 - i * 0.008), i >= 1)}" fill="${c}" opacity="${fade}"/>`;
    if (i === 0) hills += `<rect y="${H * 0.6}" width="${W}" height="${H * 0.18}" fill="url(#m)" opacity=".5"/>`;
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid slice"><defs>
    <linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${pal.sky[1]}"/><stop offset=".75" stop-color="${pal.sky[0]}"/></linearGradient>
    <radialGradient id="g" cx="${sx}" cy="${sy}" r="${W * 0.35}" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="${pal.sun}" stop-opacity=".9"/><stop offset="1" stop-color="${pal.sun}" stop-opacity="0"/></radialGradient>
    <linearGradient id="m" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#fff" stop-opacity=".9"/></linearGradient>
    <filter id="b"><feGaussianBlur stdDeviation="${W / 60}"/></filter></defs>
    <rect width="${W}" height="${H}" fill="url(#s)"/><rect width="${W}" height="${H}" fill="url(#g)"/>
    <g filter="url(#b)">${clouds}</g>${hills}${soft ? `<rect width="${W}" height="${H}" fill="#efe3cc" opacity=".32"/>` : ''}</svg>`;
}
const sceneCache = {};
function art(seed) {
  return sceneCache[seed] ||= `url('data:image/svg+xml,${encodeURIComponent(sceneSVG(seed, 800, 500, false))}')`;
}
// page background: a calm, pale, misty version of the same scene
document.querySelector('.ambient').style.backgroundImage = `url('data:image/svg+xml,${encodeURIComponent(sceneSVG('scholarpilot-sky', 1600, 1000, true))}')`;

let me = {name: '', pct: 0}, urgent = false;
nav = function () {
  const dock = [];
  GROUPS.forEach(([g, ks], i) => { if (i) dock.push('<div class="sep"></div>'); ks.forEach(k => dock.push(`<button class="${k === tab ? 'on' : ''}" data-t="${k}" data-tip="${TABS[k]}">${svg(k)}</button>`)); });
  $('#nav').innerHTML = dock.join('');
  $('#nav').onclick = e => { const b = e.target.closest('button'); if (b && b.dataset.t) { tab = b.dataset.t; render(); } };
  const [gname, ks] = groupOf(tab);
  $('#tabs').innerHTML = ks.length > 1 ? ks.map(k => `<button class="${k === tab ? 'on' : ''}" data-t="${k}">${TABS[k]}</button>`).join('') : `<button class="on">${TABS[tab] || 'Setup'}</button>`;
  $('#tabs').onclick = e => { const b = e.target.closest('button'); if (b && b.dataset.t) { tab = b.dataset.t; render(); } };
  const init = (me.name || '?').trim().split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  $('#me').innerHTML = `<div class="av">${esc(init)}</div><div><b>${esc(me.name || 'Your profile')}</b><span>${me.pct}% complete</span></div>`;
  $('#me').onclick = () => { tab = 'me'; render(); };
  $('#bell').innerHTML = '<svg class="i" viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10 21a2 2 0 0 0 4 0"/></svg>' + (urgent ? '<span class="dot"></span>' : '');
  $('#bell').onclick = () => { tab = 'plan'; render(); };
};
$('#gs').addEventListener('keydown', e => { if (e.key === 'Enter') { searchQ = e.target.value.trim(); tab = 'sch'; render(); } });
async function refreshChrome() {
  try {
    const [p, st, apps] = await Promise.all([api('/profile'), api('/profile/status'), api('/applications')]);
    me = {name: p.full_name || '', pct: st.percent};
    urgent = apps.some(a => a.days_left !== null && a.days_left >= 0 && a.days_left <= 7);
    nav();
  } catch (e) {}
}

let ticker = null;
const _renderBase = render;
render = async function () { clearInterval(ticker); const r = _renderBase(); refreshChrome(); return r; };

const pad = n => String(n).padStart(2, '0');
function parts(deadline) {
  const ms = new Date(deadline + 'T23:59:59') - new Date();
  if (ms <= 0) return null;
  const s = Math.floor(ms / 1000);
  return {d: Math.floor(s / 86400), h: Math.floor(s % 86400 / 3600), m: Math.floor(s % 3600 / 60), s: s % 60};
}
const chip = (label, cls = '') => `<span class="pill ${cls}">${label}</span>`;

VIEWS.plan = async function () {
  const d = await api('/dashboard');
  const n = d.next;
  const sorted = [...d.apps].filter(a => a.days_left !== null && a.days_left >= 0).sort((x, y) => x.days_left - y.days_left);
  const todosOpen = d.apps.reduce((x, a) => x + (a.todos_open || 0), 0);
  const near = sorted.filter(a => a.days_left <= 14).length;
  const hero = n ? `<div class="hero art" style="--art:${art(n.name)}">
      <span class="pill badge">⏳ Next deadline</span>
      <div class="hstats"><span class="pill">${d.apps.length} tracked</span><span class="pill">${near} due ≤ 14d</span><span class="pill">${d.missing.length} papers missing</span><span class="pill">${todosOpen} to-dos</span></div>
      <h1>${esc(n.name)}</h1>
      <div class="mute">${esc(n.provider)}${n.country ? ' · ' + esc(n.country) : ''} · closes ${esc(n.deadline)}</div>
      <div class="clock" id="clock"></div>
      <div class="mute">Next step: <b style="color:#fff">${esc(n.next)}</b> · ${n.progress}% complete</div>
      <p style="margin:14px 0 0"><button class="btn" id="hopen">Open workspace</button> <button class="btn ghost" id="hpull">Refresh requirements</button></p></div>`
    : `<div class="hero art" style="--art:${art('empty')};min-height:240px"><h1>No upcoming deadline</h1><div class="mute">Track a scholarship and its deadline appears here as a live countdown.</div>
      <p style="margin:14px 0 0"><button class="btn" id="hgo">Find scholarships</button></p></div>`;
  $('#main').innerHTML = `<div class="dgrid"><aside>
      <div class="panel"><h3 class="ph">Closing soon <span class="chipbtn">by deadline</span></h3><div id="soon">${sorted.length ? '' : '<span class="mute">No tracked deadlines yet.</span>'}</div></div>
      <div class="panel"><h3 class="ph">In progress</h3><div id="prog">${d.apps.length ? '' : '<span class="mute">Nothing tracked yet.</span>'}</div></div>
    </aside><section>${hero}
      <div class="rowhead">Missing papers <span class="chipbtn">${d.missing.length} to prepare</span></div>
      <div class="tiles" id="tiles">${d.missing.length ? '' : '<div class="card mute" style="grid-column:1/-1">✅ Nothing missing for your tracked scholarships (or their requirements are still being pulled).</div>'}</div>
      <div class="rowhead">Inbox updates ${d.gmail ? '<a href="#" id="sync" class="chipbtn">Sync Gmail</a>' : '<a href="#" id="cg" class="chipbtn">Connect Gmail</a>'}</div>
      <div class="card" id="ev"></div></section></div>`;

  if (n) {
    const paint = () => {
      const p = parts(n.deadline);
      $('#clock').innerHTML = p ? [[p.d, 'days'], [p.h, 'hours'], [p.m, 'min'], [p.s, 'sec']].map(([v, l]) => `<div><b>${pad(v)}</b><span>${l}</span></div>`).join('') : '<div><b>Closed</b></div>';
    };
    paint(); ticker = setInterval(paint, 1000);
    $('#hopen').onclick = () => { appId = n.id; tab = 'app'; render(); };
    $('#hpull').onclick = e => busy(e.target, 'Reading…', async () => { const r = await api(`/scholarships/${n.id}/requirements`, {method: 'POST'}); toast(`${r.added} items added`); render(); });
  } else if ($('#hgo')) $('#hgo').onclick = () => { tab = 'disc'; render(); };

  sorted.slice(0, 3).forEach(a => {
    const [t, c] = countdown(a.days_left);
    const el = document.createElement('div'); el.className = 'thumb art'; el.style.setProperty('--art', art(a.name));
    el.innerHTML = `<span class="days">${t}</span><b>${esc(a.name)}</b><span class="go"><svg class="i" viewBox="0 0 24 24" style="width:16px;height:16px"><path d="M7 17 17 7M8 7h9v9"/></svg></span>`;
    el.onclick = () => { appId = a.id; tab = 'app'; render(); };
    $('#soon').append(el);
  });
  d.apps.forEach(a => {
    const el = document.createElement('div'); el.className = 'li';
    el.innerHTML = `<div class="sq art" style="--art:${art(a.name)}"></div><div style="flex:1;min-width:0"><b>${esc(a.name)}</b><span class="mute">${esc(a.status)} · ${a.todos_open || 0} to-dos · ${esc(a.next)}</span><div class="bar"><i style="width:${a.progress}%"></i></div></div>`;
    el.onclick = () => { appId = a.id; tab = 'app'; render(); };
    $('#prog').append(el);
  });
  d.missing.forEach(m => {
    const el = document.createElement('div'); el.className = 'tile art'; el.style.setProperty('--art', art(m.text));
    const soonest = m.for.map(f => f.deadline).filter(Boolean).sort()[0];
    el.innerHTML = `<div class="top"><span class="pill">${m.for.length} scholarship${m.for.length > 1 ? 's' : ''}</span>${soonest ? `<span class="pill">by ${esc(soonest.slice(5))}</span>` : ''}</div>
      <div><h3>${esc(m.text)}</h3><div class="mute">${m.for.slice(0, 2).map(f => esc(f.name)).join(', ')}${m.for.length > 2 ? ' +' + (m.for.length - 2) : ''}</div>
      <div class="acts" style="margin-top:10px"><button class="btn" data-up>Upload</button><button class="btn" data-ok style="background:rgba(255,255,255,.2);color:#fff">Mark ready</button></div></div>`;
    el.querySelector('[data-up]').onclick = () => { tab = 'me'; render(); };
    el.querySelector('[data-ok]').onclick = async () => { await Promise.all(m.for.map(f => api('/todos/' + f.todo_id, {method: 'PATCH', body: {done: true}}))); render(); };
    $('#tiles').append(el);
  });
  $('#ev').innerHTML = d.events.length ? d.events.map(e => `<div class="miss"><b>${esc(e.sname || 'Professor')}</b> ${chip(esc(e.kind.replace('_', ' ')), e.kind === 'acceptance' ? 'ok' : e.kind === 'rejection' ? 'bad' : '')}<div class="mute">${esc(e.summary)}</div></div>`).join('')
    : `<span class="mute">${d.gmail ? 'No status-changing emails found yet.' : 'Connect Gmail to update statuses automatically from university emails.'}</span>`;
  const sy = $('#sync'); if (sy) sy.onclick = ev => { ev.preventDefault(); busy(ev.target, 'Reading mail…', async () => { const r = await api('/gmail/sync', {method: 'POST'}); toast(`${r.relevant} relevant emails, ${r.updates.length} updates`); render(); }); };
  const cg = $('#cg'); if (cg) cg.onclick = ev => { ev.preventDefault(); tab = 'mail'; render(); };
};

VIEWS.app = async function () {
  if (!appId) { $('#main').innerHTML = '<div class="card">Pick a tracked scholarship from the Dashboard.</div>'; return; }
  const [w, gm] = await Promise.all([api(`/scholarships/${appId}/workspace`), api('/gmail/status')]);
  const s = w.scholarship, v = w.view, [t, c] = countdown(v.days_left);
  const ec = s.eligibility_check ? JSON.parse(s.eligibility_check) : null;
  const done = k => v.steps.find(x => x.key === k).done;
  const sec = (k, title, body) => `<div class="card"><h2><span class="st ${done(k) ? 'on' : ''}"></span>${title}</h2>${body}</div>`;
  const todoRow = x => `<label class="chk"><input type="checkbox" data-todo="${x.id}" ${x.done || x.covered ? 'checked' : ''} ${x.covered ? 'disabled' : ''}> <span>${esc(x.text)}${x.covered ? ' <span class="pill ok">covered by your uploaded file</span>' : ''}${x.source === 'email' ? ' <span class="pill warn">requested by email</span>' : ''}</span>${x.detail ? `<span class="mute"> — ${esc(x.detail)}</span>` : ''} <a href="#" data-tdel="${x.id}" class="mute">✕</a></label>`;
  const docs = w.todos.filter(x => x.kind === 'document'), steps = w.todos.filter(x => x.kind !== 'document');
  $('#main').innerHTML = `<div class="card banner art" style="--art:${art(s.name)};padding:24px 28px"><div class="top"><div><h2 style="font-size:26px;letter-spacing:-.03em">${esc(s.name)}</h2><span class="mute">${esc(s.provider)} · ${esc(s.country)} · ${esc(s.level)}</span></div>${chip(t, c)}</div>
    ${timeline(v.steps)}<div class="bar"><i style="width:${v.progress}%"></i></div>
    <div class="row" style="margin-top:8px"><div><label>Status</label><select id="st">${['new', 'interested', 'preparing', 'submitted', 'interview', 'waitlisted', 'accepted', 'rejected', 'skipped'].map(o => `<option ${o === s.status ? 'selected' : ''}>${o}</option>`).join('')}</select></div>
    <div><label>Deadline</label><div>${esc(s.deadline || '?')} ${s.url ? `· <a href="${esc(s.url)}" target="_blank" rel="noopener">Official page</a>` : ''}</div></div></div></div>
    <div class="cols">
    <div>
      ${sec('documents', 'Required documents & to-dos', `<p class="mute">Pulled automatically from the official page when you track a scholarship.</p>
        <button class="btn sm ghost" id="pull">↻ Re-pull requirements from the official page</button>
        <h3 style="margin-top:12px">Documents</h3>${docs.map(todoRow).join('') || '<span class="mute">None yet.</span>'}
        <h3 style="margin-top:12px">Steps</h3>${steps.map(todoRow).join('') || '<span class="mute">None yet.</span>'}
        <div class="row" style="margin-top:10px"><input id="nt" placeholder="Add your own to-do"><button class="btn sm" id="ntb" style="flex:0">Add</button></div>`)}
      ${sec('eligibility', 'Eligibility', `<p class="mute">${esc(s.eligibility || 'No eligibility text recorded.')}</p><button class="btn sm" id="el">Check against my profile</button>
        ${ec ? `<p>${chip(esc(ec.verdict), ec.verdict === 'eligible' ? 'ok' : ec.verdict === 'not_eligible' ? 'bad' : 'warn')}</p><div class="mute">Met: ${esc((ec.met || []).join('; '))}<br>Gaps: ${esc((ec.gaps || []).join('; '))}<br>Next: ${esc((ec.next_actions || []).join('; '))}</div>` : ''}`)}
      ${sec('letter', 'Motivation letter', `<button class="btn sm" id="wl">Write it for this scholarship →</button> ${w.letters.filter(l => l.kind === 'motivation').map(l => chip(esc(l.title))).join('')}`)}
      ${sec('submit', 'Submit & result', `<label class="chk"><input type="checkbox" data-step="submit" ${done('submit') ? 'checked' : ''}> Submitted</label><label class="chk"><input type="checkbox" data-step="result" ${done('result') ? 'checked' : ''}> Result received</label><p class="mute">With Gmail connected these update automatically from university emails.</p>`)}
    </div><div>
      ${sec('research', 'University, labs & professors', `<div class="row"><input id="tg" placeholder="Specific university/programme (optional)"><button class="btn" id="ur" style="flex:0">Research</button></div>
        <p class="mute" style="white-space:pre-wrap">${esc(s.research)}</p>
        ${w.professors.map(p => `<div class="card"><div class="top"><div><h3>${esc(p.name)}</h3><span class="mute">${esc(p.title)} · ${esc(p.department)} · ${esc(p.university)}${p.lab ? ' · ' + esc(p.lab) : ''}</span></div>
          <select data-pst="${p.id}">${['found', 'drafted', 'sent', 'replied', 'no_reply'].map(o => `<option ${o === p.status ? 'selected' : ''}>${o}</option>`).join('')}</select></div>
          <p class="mute">${esc(p.research_interests)}<br>${p.recent_work ? 'Recent: ' + esc(p.recent_work) + '<br>' : ''}Why fit: ${esc(p.why_fit)}</p>
          <p class="mute">${p.email ? `✉ ${esc(p.email)} (public; verify)` : 'No public email found.'} ${p.homepage ? `· <a href="${esc(p.homepage)}" target="_blank" rel="noopener">page</a>` : ''}
          · <a href="https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(p.name + ' ' + p.university)}" target="_blank" rel="noopener">LinkedIn search</a></p>
          <button class="btn sm" data-draft="${p.id}">${p.letter_id ? 'Redraft' : 'Draft email'}</button>
          ${p.letter_id && gm.connected ? `<button class="btn sm ghost" data-gm="draft" data-p="${p.id}">Save to Gmail drafts</button> ${p.email ? `<button class="btn sm ghost" data-gm="send" data-p="${p.id}">Send via Gmail…</button>` : ''}` : ''}
          ${p.letter_id && !gm.connected ? `<button class="btn sm ghost" data-mail="${p.id}" data-email="${esc(p.email)}" data-lid="${p.letter_id}">Open in mail app</button>` : ''}
          <button class="btn sm ghost" data-pdel="${p.id}">remove</button></div>`).join('')}
        ${gm.connected ? '' : '<p class="mute">Connect Gmail in “Inbox & Gmail” to save drafts or send from here.</p>'}`)}
    </div></div>`;

  $('#st').onchange = async e => { await api('/scholarships/' + appId, {method: 'PATCH', body: {status: e.target.value}}); render(); };
  $('#pull').onclick = e => busy(e.target, 'Reading official page…', async () => { const r = await api(`/scholarships/${appId}/requirements`, {method: 'POST'}); toast(`${r.added} items added`); render(); });
  $('#ntb').onclick = async () => { const x = $('#nt').value.trim(); if (!x) return; await api(`/scholarships/${appId}/todos`, {method: 'POST', body: {text: x, kind: 'step'}}); render(); };
  document.querySelectorAll('[data-todo]').forEach(cb => cb.onchange = async () => { await api('/todos/' + cb.dataset.todo, {method: 'PATCH', body: {done: cb.checked}}); render(); });
  document.querySelectorAll('[data-tdel]').forEach(a => a.onclick = async ev => { ev.preventDefault(); await api('/todos/' + a.dataset.tdel, {method: 'DELETE'}); render(); });
  $('#el').onclick = e => busy(e.target, 'Checking…', async () => { await api(`/scholarships/${appId}/eligibility`, {method: 'POST'}); render(); });
  $('#wl').onclick = () => { writeFor = appId; tab = 'write'; render(); };
  document.querySelectorAll('[data-step]').forEach(cb => cb.onchange = async () => { await api(`/scholarships/${appId}/steps`, {method: 'POST', body: {step: cb.dataset.step, done: cb.checked}}); render(); });
  $('#ur').onclick = e => busy(e.target, 'Researching (1-2 min)…', async () => { const r = await api(`/scholarships/${appId}/university-research`, {method: 'POST', body: {target: $('#tg').value}}); toast(`${r.professors_added} professors added`); render(); });
  document.querySelectorAll('[data-pst]').forEach(sel => sel.onchange = async () => { await api('/professors/' + sel.dataset.pst, {method: 'PATCH', body: {status: sel.value}}); render(); });
  document.querySelectorAll('[data-pdel]').forEach(b => b.onclick = async () => { await api('/professors/' + b.dataset.pdel, {method: 'DELETE'}); render(); });
  document.querySelectorAll('[data-draft]').forEach(b => b.onclick = e => busy(e.target, 'Drafting…', async () => { await api(`/professors/${b.dataset.draft}/draft`, {method: 'POST'}); render(); }));
  document.querySelectorAll('[data-gm]').forEach(b => b.onclick = async e => {
    const send = b.dataset.gm === 'send';
    if (send && !confirm('Send this email now from your Gmail? It cannot be unsent.')) return;
    await busy(e.target, '…', async () => { await api(`/professors/${b.dataset.p}/gmail`, {method: 'POST', body: {professor_id: +b.dataset.p, action: b.dataset.gm, confirm: send}}); toast(send ? 'Sent' : 'Saved to Gmail drafts'); render(); });
  });
  document.querySelectorAll('[data-mail]').forEach(b => b.onclick = async () => {
    const lt = (await api('/letters')).find(l => l.id == b.dataset.lid); if (!lt) return;
    const m = lt.content.match(/^Subject:\s*(.*)\n+/i); const subj = m ? m[1] : ''; const body = m ? lt.content.slice(m[0].length) : lt.content;
    window.open(`mailto:${b.dataset.email}?subject=${encodeURIComponent(subj)}&body=${encodeURIComponent(body)}`);
  });
};

VIEWS.mail = async function () {
  const [st, wt] = await Promise.all([api('/gmail/status'), api('/watcher')]);
  $('#main').innerHTML = `<div class="card"><div class="top"><h2>Gmail connection</h2>${chip(st.connected ? 'connected: ' + esc(st.email) : 'not connected', st.connected ? 'ok' : 'warn')}</div>
    ${st.error ? `<p class="mute" style="color:var(--bad)">${esc(st.error)}</p>` : ''}
    ${st.connected ? `<p class="mute">ScholarPilot can save drafts and (only when you press Send and confirm) send email, and read recent mail <b>that mentions your tracked scholarships or professors</b> to update statuses. Only those matching emails are sent to the AI for classification.</p>
      ${wt.interval_min ? `<p class="mute">Always-on watcher: checks every ${wt.interval_min} min. Last run: ${esc(wt.last_run || 'pending')} (${esc(wt.result)})</p>` : ''}<button class="btn" id="sync">Sync now</button> <button class="btn ghost" id="dc">Disconnect</button><div id="res" class="mute" style="margin-top:10px"></div>`
    : `<ol class="mute"><li>In Google Cloud Console → APIs & Services → enable the <b>Gmail API</b> (already done on the hosted project).</li>
      <li>OAuth consent screen → External → add your Gmail as a <b>test user</b>.</li>
      <li>Credentials → Create credentials → OAuth client ID → <b>Web application</b> (hosted) or <b>Desktop app</b> (local) → download the JSON.</li>
      <li>Upload it here, then press Connect and approve in the browser tab that opens.</li></ol>
      <div class="row"><input type="file" id="cf"><button class="btn" id="cu" style="flex:0">Upload</button></div><br>
      <button class="btn" id="cn" ${st.has_credentials ? '' : 'disabled'}>${st.connecting ? 'Waiting for approval…' : 'Connect Gmail'}</button>`}</div>
    <div class="card"><h2>How statuses update</h2><p class="mute">Acceptance, rejection, interview, waitlist and “application received” emails set the status (never downgrading a final result). Document requests become to-dos. Professor replies mark outreach as replied. Everything is listed on the Dashboard so you can verify and correct it.</p></div>`;
  const q = id => document.getElementById(id);
  if (q('cu')) q('cu').onclick = e => busy(e.target, '…', async () => { const f = q('cf').files[0]; if (!f) return toast('Choose the JSON file'); const fd = new FormData(); fd.append('file', f); await api('/gmail/credentials', {method: 'POST', body: fd}); render(); });
  if (q('cn')) q('cn').onclick = async () => { const c = await api('/gmail/connect', {method: 'POST'}); if (c.url) { location.href = c.url; return; } toast('Approve access in the browser tab that opened'); const iv = setInterval(async () => { const s = await api('/gmail/status'); if (s.connected || (!s.connecting && s.error)) { clearInterval(iv); render(); } }, 2000); };
  if (q('dc')) q('dc').onclick = async () => { await api('/gmail/disconnect', {method: 'POST'}); render(); };
  if (q('sync')) q('sync').onclick = e => busy(e.target, 'Reading mail…', async () => { const r = await api('/gmail/sync', {method: 'POST'}); q('res').innerHTML = `Checked ${r.checked} emails, ${r.relevant} relevant.<br>` + (r.updates.map(u => `• ${esc(u.kind)}: ${esc(u.summary)}`).join('<br>') || 'No status changes.'); });
};

{ const h = location.hash.slice(1).split('/'); if (TABS[h[0]]) { tab = h[0]; if (h[1]) appId = +h[1]; } }
render();
