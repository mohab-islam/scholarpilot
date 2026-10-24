// Recommendation letters: saved professors, a step-by-step draft builder, signed copies, and the request email
// shown on each tracked scholarship that asks for a letter.
const REC_STEPS = [
  {title: 'Who is your recommender?', hint: 'Saved once, so you never type it again.', fields: [
    ['name', 'Professor name *', 'Prof. Jane Smith'], ['email', 'Email', 'jane.smith@university.edu'],
    ['title', 'Title', 'Associate Professor'], ['university', 'University', ''], ['department', 'Department', '']]},
  {title: 'What did they teach you?', hint: 'The draft is built around this, so be specific.', fields: [
    ['relationship', 'How do they know you?', 'Course lecturer / thesis supervisor / project advisor'],
    ['course', 'Course or project', 'Machine Learning'], ['course_where', 'Where (university, programme)', ''], ['course_year', 'When', '2024 spring semester']]},
  {title: 'One remarkable achievement', hint: 'What did you do in their class that stood out? Real facts only; the letter will not invent anything.', fields: [
    ['achievement', 'Your achievement', 'Built X that did Y, ranked top of the class, led the team project…', true],
    ['extra', 'Anything else they should mention (optional)', '', true]]},
];
const REC_KEYS = REC_STEPS.flatMap(s => s.fields.map(f => f[0]));
let recStep = -1, recForm = {}, recEdit = null;

VIEWS.rec = async function () {
  const list = await api('/recommenders');
  const step = REC_STEPS[recStep];
  const wizard = recStep < 0
    ? `<p class="mute">Add a professor, tell ScholarPilot what they taught you and one achievement, and it builds a strong generic draft for a master's application that they can edit and sign. Upload the signed letter here when you get it; it is then ready for every scholarship that needs one.</p>
       <button class="btn" id="radd">+ New recommendation letter</button> <button class="btn ghost" id="rres">Research what makes a letter strong</button>`
    : `<div class="timeline-mini">${REC_STEPS.map((s, i) => `<span class="pill ${i === recStep ? 'warn' : i < recStep ? 'ok' : ''}">${i + 1}. ${esc(s.title)}</span>`).join(' ')}</div>
       <h3 style="margin-top:12px">${esc(step.title)}</h3><p class="mute">${esc(step.hint)}</p>
       ${step.fields.map(([k, l, ph, area]) => `<label>${esc(l)}</label>${area
         ? `<textarea data-rf="${k}" placeholder="${esc(ph)}" style="min-height:90px">${esc(recForm[k] || '')}</textarea>`
         : `<input data-rf="${k}" placeholder="${esc(ph)}" value="${esc(recForm[k] || '')}">`}`).join('<div style="height:8px"></div>')}
       <p style="margin-top:14px">${recStep > 0 ? '<button class="btn ghost" id="rback">Back</button> ' : ''}<button class="btn ghost" id="rcancel">Cancel</button>
       <button class="btn" id="rnext">${recStep === REC_STEPS.length - 1 ? (recEdit ? 'Save changes' : 'Create & write the draft') : 'Next'}</button></p>`;
  $('#main').innerHTML = `<div class="card"><h2>Recommendation letters</h2>${wizard}</div>` + list.map(r => `
    <div class="card"><div class="top"><div><h3>${esc(r.name)}</h3><span class="mute">${esc([r.title, r.department, r.university].filter(Boolean).join(' · '))}${r.email ? ' · ✉ ' + esc(r.email) : ' · no email saved'}</span></div>
      <div>${chip(r.has_draft ? 'draft ready' : 'no draft', r.has_draft ? 'ok' : 'warn')} ${chip(r.has_signed ? 'signed letter uploaded' : 'not signed yet', r.has_signed ? 'ok' : '')}</div></div>
      <p class="mute">${esc([r.relationship, r.course && ('Course: ' + r.course), r.course_where, r.course_year].filter(Boolean).join(' · '))}</p>
      <label>Draft for the professor to sign (editable)</label>
      <textarea data-rd="${r.id}" style="min-height:240px" placeholder="No draft yet. Press “Write draft”.">${esc(r.draft || '')}</textarea>
      <div class="row" style="margin-top:8px"><input data-ri="${r.id}" placeholder="Ask for a change, e.g. shorter, mention the thesis more"><button class="btn sm" data-rg="${r.id}" style="flex:0;white-space:nowrap">${r.has_draft ? 'Rewrite' : 'Write draft'}</button></div>
      <p style="margin-top:10px"><button class="btn sm" data-rs="${r.id}">Save draft</button>
        ${r.has_draft ? `<a class="btn sm ghost" href="/api/recommenders/${r.id}/draft.docx">Download .docx</a>` : ''}
        <button class="btn sm ghost" data-re="${r.id}">Edit details</button>
        <button class="btn sm ghost" data-rx="${r.id}">Delete</button></p>
      <hr style="border:0;border-top:1px solid var(--line,#ddd);margin:12px 0">
      <label>Signed letter</label>
      ${r.has_signed ? `<p>${esc(r.signed_name || 'signed letter')} <span class="mute">(uploaded ${esc(r.signed_at || '')})</span>
        <a class="btn sm ghost" href="/api/recommenders/${r.id}/signed">Download</a> <button class="btn sm ghost" data-rsd="${r.id}">Remove</button></p>` : '<p class="mute">Upload the PDF or scan once the professor has signed it.</p>'}
      <input type="file" data-rsu="${r.id}" accept=".pdf,.doc,.docx,.png,.jpg,.jpeg"></div>`).join('')
    || '';
  if (!list.length) $('#main').insertAdjacentHTML('beforeend', '<div class="card mute">No recommenders yet.</div>');

  const grab = () => document.querySelectorAll('[data-rf]').forEach(i => { recForm[i.dataset.rf] = i.value; });
  const open = () => { recStep = 0; recForm = recEdit ? {...recEdit} : {}; render(); };
  if ($('#radd')) $('#radd').onclick = () => { recEdit = null; open(); };
  if ($('#rres')) $('#rres').onclick = e => busy(e.target, 'Researching…', async () => { await api('/recommenders/research', {method: 'POST'}); toast('Research saved; new drafts will use it'); });
  if ($('#rcancel')) $('#rcancel').onclick = () => { recStep = -1; recEdit = null; render(); };
  if ($('#rback')) $('#rback').onclick = () => { grab(); recStep--; render(); };
  if ($('#rnext')) $('#rnext').onclick = async e => {
    grab();
    if (recStep === 0 && !(recForm.name || '').trim()) return toast("Enter the professor's name");
    if (recStep < REC_STEPS.length - 1) { recStep++; return render(); }
    const body = Object.fromEntries(REC_KEYS.map(k => [k, recForm[k] || '']));
    await busy(e.target, 'Writing the draft…', async () => {
      if (recEdit) await api('/recommenders/' + recEdit.id, {method: 'PUT', body});
      else { const r = await api('/recommenders', {method: 'POST', body}); await api(`/recommenders/${r.id}/draft`, {method: 'POST'}); }
      recStep = -1; recEdit = null; toast('Saved'); render();
    });
  };
  document.querySelectorAll('[data-rg]').forEach(b => b.onclick = e => busy(e.target, 'Writing…', async () => {
    await api(`/recommenders/${b.dataset.rg}/draft`, {method: 'POST', body: {instruction: document.querySelector(`[data-ri="${b.dataset.rg}"]`).value}}); render(); }));
  document.querySelectorAll('[data-rs]').forEach(b => b.onclick = async () => {
    await api(`/recommenders/${b.dataset.rs}/draft`, {method: 'PUT', body: {draft: document.querySelector(`[data-rd="${b.dataset.rs}"]`).value}}); toast('Draft saved'); });
  document.querySelectorAll('[data-re]').forEach(b => b.onclick = () => { recEdit = list.find(x => x.id == b.dataset.re); open(); });
  document.querySelectorAll('[data-rx]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this recommender, their draft and the signed letter?')) return;
    await api('/recommenders/' + b.dataset.rx, {method: 'DELETE'}); render(); });
  document.querySelectorAll('[data-rsd]').forEach(b => b.onclick = async () => { await api(`/recommenders/${b.dataset.rsd}/signed`, {method: 'DELETE'}); render(); });
  document.querySelectorAll('[data-rsu]').forEach(inp => inp.onchange = async () => {
    if (!inp.files[0]) return;
    const fd = new FormData(); fd.append('file', inp.files[0]);
    try { await api(`/recommenders/${inp.dataset.rsu}/signed`, {method: 'POST', body: fd}); toast('Signed letter saved'); render(); } catch (e) { toast(e.message); }
  });
};

// ---- on a tracked scholarship that asks for a recommendation letter: ask the professor by email ----
async function recPanel() {
  if (typeof appId === 'undefined' || !appId || tab !== 'app') return;
  let d, gm;
  try { [d, gm] = await Promise.all([api(`/scholarships/${appId}/recommendations`), api('/gmail/status')]); } catch (e) { return; }
  if (!d.needed) return;
  const col = document.querySelector('#main .cols > div');
  if (!col) return;
  const card = document.createElement('div');
  card.className = 'card';
  const rs = d.recommenders;
  card.innerHTML = `<h2>Recommendation letter</h2><p class="mute">This scholarship asks for a letter. Pick a saved professor and ScholarPilot drafts the request email with the scholarship name and deadline filled in.</p>` +
    (rs.length ? `<label>Professor</label><select id="rcp">${rs.map(r => `<option value="${r.id}">${esc(r.name)}${r.request_status ? ' (' + esc(r.request_status) + ')' : ''}${r.has_signed ? ' · signed letter ready' : ''}</option>`).join('')}</select>
      <label style="margin-top:8px">Subject</label><input id="rcs"><label style="margin-top:8px">Email</label><textarea id="rcb" style="min-height:230px"></textarea>
      <label class="chk"><input type="checkbox" id="rca" checked> Attach the draft letter (when one exists) so they only edit and sign</label>
      <p id="rcn" class="mute"></p>
      <p>${gm.connected ? '<button class="btn" id="rcd">Save to Gmail drafts</button> <button class="btn ghost" id="rcsend">Send via Gmail…</button>' : '<button class="btn" id="rcm">Open in mail app</button> <span class="mute">Connect Gmail in “Inbox &amp; Gmail” to save drafts or send here.</span>'}
      <button class="btn ghost" id="rcg">Manage recommenders</button></p>`
      : '<p>No professor saved yet.</p><button class="btn" id="rcg">Add a recommender</button>');
  col.appendChild(card);
  const q = id => card.querySelector('#' + id);
  q('rcg').onclick = () => { tab = 'rec'; render(); };
  if (!rs.length) return;
  const cur = () => rs.find(r => r.id == q('rcp').value);
  const fill = () => { const r = cur(); q('rcs').value = r.subject; q('rcb').value = r.body;
    q('rcn').textContent = r.email ? `To: ${r.email}` : 'No email saved for this professor: add it under Recommendations.';
    q('rca').disabled = !r.has_draft; };
  q('rcp').onchange = fill; fill();
  // the attachment sentence only makes sense with an attachment: rebuild the text when the box changes
  q('rca').onchange = () => { const r = cur(), line = 'I have attached a draft based on what we discussed, so you only need to adjust it to your own words and sign it. ';
    q('rcb').value = q('rca').checked ? r.body : r.body.replace(line, ''); };
  const payload = (action, confirm) => ({recommender_id: +q('rcp').value, action, subject: q('rcs').value, body: q('rcb').value, attach_draft: q('rca').checked, confirm});
  if (q('rcd')) q('rcd').onclick = e => busy(e.target, 'Saving…', async () => { await api(`/scholarships/${appId}/recommendations/email`, {method: 'POST', body: payload('draft', false)}); toast('Saved to Gmail drafts'); render(); });
  if (q('rcsend')) q('rcsend').onclick = e => {
    const r = cur();
    if (!r.email) return toast('Add this professor’s email first');
    if (!confirm(`Send this email to ${r.name} <${r.email}> now? It cannot be unsent.`)) return;
    busy(e.target, 'Sending…', async () => { await api(`/scholarships/${appId}/recommendations/email`, {method: 'POST', body: payload('send', true)}); toast('Sent'); render(); });
  };
  if (q('rcm')) q('rcm').onclick = () => window.open(`mailto:${cur().email || ''}?subject=${encodeURIComponent(q('rcs').value)}&body=${encodeURIComponent(q('rcb').value)}`);
}
{
  const baseApp = VIEWS.app;
  VIEWS.app = async function () { await baseApp(); await recPanel(); };
}
