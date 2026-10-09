// Dashboard, application workspace, letter lab, sweep. Extends VIEWS/TABS from index.html.
Object.keys(TABS).forEach(k => delete TABS[k]);
Object.assign(TABS, {plan: 'Dashboard', sch: 'Scholarships', disc: 'Discover', app: 'Application', me: 'My profile', lab: 'Letter lab', write: 'Writer', li: 'LinkedIn'});

const stepIcon = s => s.done ? '✓' : '○';
const timeline = steps => `<div style="display:flex;gap:4px;flex-wrap:wrap;margin:8px 0">${steps.map(s => `<span class="pill ${s.done ? 'ok' : ''}" title="${s.auto ? 'completes automatically' : 'manual'}">${stepIcon(s)} ${esc(s.label)}</span>`).join('')}</div>`;

function countdown(days) {
  if (days === null || days === undefined) return ['no deadline', ''];
  if (days < 0) return ['closed', 'bad'];
  return [days === 0 ? 'today!' : days + ' days left', days <= 7 ? 'bad' : days <= 21 ? 'warn' : 'ok'];
}

VIEWS.lab = async function () {
  const [pb, tpl] = await Promise.all([api('/playbook'), api('/templates')]);
  $('#main').innerHTML = `<div class="card"><h2>Research the perfect letter formula</h2>
    <p class="mute">Five search agents run in parallel across the web: letter structures, what selection committees look for, authentic voice, cold-email advice, and specifics for your field and target countries. The writer then applies this research.</p>
    <button class="btn" id="rp">${pb.length ? 'Refresh' : 'Run'} research agents</button></div>
    ${pb.map(p => `<div class="card"><h3>${esc(p.topic)}</h3><div style="white-space:pre-wrap">${esc(p.body)}</div>
      <p class="mute">Sources: ${p.sources.slice(0, 5).map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title || 'link')}</a>`).join(' · ')}</p></div>`).join('')}
    <div class="card"><h2>Templates</h2><p class="mute">Generated from the research and your profile. Pick one in the Writer.</p>
    <button class="btn" id="gt">Generate templates</button>
    ${tpl.map(t => `<div class="card"><div class="top"><h3>${esc(t.name)}</h3><button class="btn sm ghost" data-td="${t.id}">delete</button></div><p class="mute">${esc(t.description)}</p><div style="white-space:pre-wrap">${esc(t.body)}</div></div>`).join('')}</div>`;
  $('#rp').onclick = e => busy(e.target, 'Agents searching (1-3 min)…', async () => { const r = await api('/playbook/research', {method: 'POST'}); toast(`${r.topics} topics researched`); render(); });
  $('#gt').onclick = e => busy(e.target, 'Writing templates…', async () => { await api('/templates/generate', {method: 'POST'}); render(); });
  document.querySelectorAll('[data-td]').forEach(b => b.onclick = async () => { await api('/templates/' + b.dataset.td, {method: 'DELETE'}); render(); });
};

VIEWS.disc = async function () {
  const prof = await api('/profile');
  $('#main').innerHTML = `<div class="card"><h2>Sweep the web for open scholarships</h2>
    <p class="mute">Six search agents run in parallel (regions, government schemes, university-specific) using your profile. Everything lands in Scholarships with dates and required documents where verifiable.</p>
    <p class="mute">Searching for: <b>${esc(prof.target_level)}</b> in <b>${esc(prof.target_field)}</b> for applicants from <b>${esc(prof.nationality)}</b>. Only scholarships you are eligible for that are open now or opening later are kept. <a href="#" id="chg">Change</a></p>
    <button class="btn" id="sw">Sweep now</button></div>
    <div class="card"><h2>Targeted search</h2><textarea id="q" placeholder="e.g. fully funded masters in AI in Europe, deadlines before March"></textarea><br><br><button class="btn" id="go">Search</button></div>`;
  $('#sw').onclick = e => busy(e.target, 'Agents searching (2-4 min)…', async () => { const r = await api('/sweep', {method: 'POST', body: {}}); toast(`${r.found} results from ${r.agents} agents`); tab = 'sch'; render(); });
  $('#chg').onclick = ev => { ev.preventDefault(); tab = 'onboard'; _render(); };
  $('#go').onclick = e => busy(e.target, 'Searching Google…', async () => { const r = await api('/discover', {method: 'POST', body: {query: $('#q').value}}); toast(`Found ${r.added}`); tab = 'sch'; render(); });
};


// ---- profile-first gate: nothing else works until country, degree and major are set
VIEWS.onboard = async function () {
  const [p, st] = await Promise.all([api('/profile'), api('/profile/status')]);
  const opt = (v, list) => list.map(o => `<option ${o === v ? 'selected' : ''}>${o}</option>`).join('');
  $('#main').innerHTML = `<div class="card"><h2>👋 Let's build your scholar profile first</h2>
    <p class="mute">Scholarships are filtered to the ones <b>you</b> are eligible for (country, degree, major) and that are open now or opening later. Closed ones are hidden. Takes one minute; you can add more detail later.</p>
    <label>Your country (nationality) *</label><input id="o_nat" value="${esc(p.nationality)}" placeholder="e.g. Egypt">
    <label>Degree you want to study *</label><select id="o_lvl"><option value="">Choose…</option>${opt(p.target_level, ["Bachelor's", "Master's", "PhD"])}</select>
    <label>Major / field *</label><input id="o_fld" value="${esc(p.target_field)}" placeholder="e.g. Artificial Intelligence, Public Health">
    <label>Current education (degree, university, grades)</label><textarea id="o_edu">${esc(p.education)}</textarea>
    <label>Preferred countries (optional)</label><input id="o_ctr" value="${esc(p.target_countries)}" placeholder="e.g. Germany, Netherlands, Canada">
    <label>Intended start (optional)</label><input id="o_st" value="${esc(p.start_year)}" placeholder="e.g. Fall 2027">
    <br><br><button class="btn" id="o_go">Save & continue</button>
    ${st.core_missing.length ? `<p class="mute">Still needed: ${esc(st.core_missing.join(', '))}</p>` : ''}</div>`;
  $('#o_go').onclick = async () => {
    const data = {...p, nationality: $('#o_nat').value.trim(), target_level: $('#o_lvl').value, target_field: $('#o_fld').value.trim(),
      education: $('#o_edu').value, target_countries: $('#o_ctr').value, start_year: $('#o_st').value};
    if (!data.nationality || !data.target_level || !data.target_field) return toast('Country, degree and major are required');
    await api('/profile', {method: 'PUT', body: {data}});
    tab = 'disc'; render();
  };
};
const _render = render;
render = async function () {
  try { const st = await api('/profile/status'); if (!st.core_complete && tab !== 'me') tab = 'onboard'; } catch (e) {}
  return _render();
};

