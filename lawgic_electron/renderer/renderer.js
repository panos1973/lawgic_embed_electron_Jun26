'use strict';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const counts = { pending: 0, processing: 0, done: 0, review: 0, error: 0 };
let folder = null;
let paused = false;   // true when the run stopped on a credential/endpoint error

// ---- tabs ----
document.querySelectorAll('.tab').forEach((t) => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.view').forEach((x) => x.classList.remove('active'));
    t.classList.add('active');
    $(t.dataset.tab).classList.add('active');
    if (t.dataset.tab === 'review') loadReview();
    if (t.dataset.tab === 'browse') loadLaws();
    if (t.dataset.tab === 'timeline') loadLaws();   // populate the shared law datalist
    if (t.dataset.tab === 'settings') { loadSettings(); loadCollections(); }
  });
});

// ---- connection pill ----
function setPill(state, text) {
  const p = $('connPill');
  p.className = 'pill' + (state ? ' ' + state : '');
  $('connText').textContent = text;
}

// ---- counts ----
function renderCounts() {
  document.querySelectorAll('.chip').forEach((c) => {
    c.querySelector('b').textContent = counts[c.dataset.k] ?? 0;
  });
  const n = counts.review || 0;
  $('reviewBadge').textContent = n ? n : '';
}
function applyCounts(obj) {
  for (const k of Object.keys(counts)) counts[k] = obj[k] || 0;
  renderCounts();
}

// ---- log ----
function logLine({ stage, doc, msg, cls, ts }) {
  const time = ts || new Date().toLocaleTimeString('en-GB');   // HH:MM:SS
  const el = document.createElement('div');
  el.className = 'line';
  el.innerHTML =
    `<span class="time">${time}</span>` +
    `<span class="stage">${stage || ''}</span>` +
    `<span class="doc">${doc || ''}</span>` +
    `<span class="msg ${cls || ''}">${msg || ''}</span>`;
  const log = $('log');
  log.prepend(el);
  while (log.children.length > 300) log.removeChild(log.lastChild);
}

// ---- ingest ----
$('chooseBtn').addEventListener('click', async () => {
  const f = await window.api.selectFolder();
  if (f) {
    folder = f; $('folderPath').textContent = f;
    $('startBtn').disabled = false; $('enrichBtn').disabled = false;
  }
});

$('enrichBtn').addEventListener('click', () => {
  if (!folder) return;
  $('log').innerHTML = '';
  $('startBtn').disabled = true; $('enrichBtn').disabled = true; $('cancelBtn').disabled = false;
  setPill('busy', 'enriching');
  window.api.enrich(folder);   // backfill summaries/keywords/title; vectors untouched
});

$('startBtn').addEventListener('click', () => {
  if (!folder) return;
  $('log').innerHTML = '';
  paused = false; $('pausedBanner').hidden = true; $('resumeBtn').hidden = true;
  $('startBtn').disabled = true; $('cancelBtn').disabled = false;
  setPill('busy', 'ingesting');
  window.api.startIngest(folder);
});

$('resumeBtn').addEventListener('click', () => {
  if (!folder) return;
  paused = false; $('pausedBanner').hidden = true; $('resumeBtn').hidden = true;
  $('startBtn').disabled = true; $('cancelBtn').disabled = false;
  setPill('busy', 'resuming');
  logLine({ stage: 'resume', doc: '', msg: 'continuing — already-done files are skipped' });
  window.api.startIngest(folder);   // resume: the state DB skips done files, retries the rest
});

$('cancelBtn').addEventListener('click', () => window.api.cancel());

let total = 0, doneCount = 0;
window.api.onPipelineEvent((o) => {
  switch (o.type) {
    case 'scan':
      total = o.total; doneCount = 0;
      $('bar').style.width = '0%';
      $('progressLabel').textContent = `0 / ${total}`;
      logLine({ stage: 'scan', doc: `${o.total} files`, msg: o.folder, ts: o.ts });
      break;
    case 'doc_start': {
      const pos = o.index ? `${o.index} / ${o.total}` : (o.total ? `· / ${o.total}` : '');
      $('progressLabel').textContent = `${pos}${pos ? ' — ' : ''}${o.doc}`;
      logLine({ stage: 'start', doc: o.doc, msg: '', ts: o.ts });
      break;
    }
    case 'stage':
      logLine({ stage: o.stage, doc: o.doc, msg: o.msg, ts: o.ts });
      break;
    case 'doc_done': {
      doneCount++;
      if (total) $('bar').style.width = `${Math.round((doneCount / total) * 100)}%`;
      const cls = o.status === 'done' ? 'ok' : (o.status === 'error' ? 'bad' : '');
      logLine({ stage: o.status, doc: o.doc, msg: '', cls, ts: o.ts });
      break;
    }
    case 'summary': applyCounts(o.counts); break;
    case 'fatal': {
      paused = true;
      const prov = o.provider || 'a provider', stg = o.stage || '', det = o.detail || '';
      const where = o.doc ? ` at “${esc(o.doc)}”` : '';
      const b = $('pausedBanner');
      b.hidden = false;
      b.innerHTML = `<b>⏸ Paused${where}</b> — <b>${esc(prov)}</b> failed during ` +
        `${esc(stg)}. This looks like a wrong/expired key or URL, so the run stopped ` +
        `instead of failing every remaining file.` +
        `<div class="paused-detail">${esc(det)}</div>` +
        `Fix <b>${esc(prov)}</b> under <b>Settings</b>, click <b>Save</b>, then <b>Resume</b>.`;
      setPill('err', 'paused');
      logLine({ stage: 'paused', doc: o.doc || '', msg: `${prov}: ${det}`, cls: 'bad', ts: o.ts });
      break;
    }
    case 'log': logLine({ stage: '', doc: '', msg: o.msg, ts: o.ts }); break;
  }
});

window.api.onPipelineDone(({ code, error }) => {
  $('cancelBtn').disabled = true;
  $('enrichBtn').disabled = !folder;
  if (paused) {
    // stopped on a credential/endpoint error — offer Resume, not a fresh Start
    $('resumeBtn').hidden = false; $('startBtn').disabled = true;
    setPill('err', 'paused');
  } else if (code === 0) {
    setPill('ok', 'finished'); $('startBtn').disabled = !folder;
  } else {
    setPill('err', 'failed'); $('startBtn').disabled = !folder;
    logLine({ stage: 'error', doc: '', msg: error, cls: 'bad' });
  }
  refreshStatus();
});

async function refreshStatus() {
  const r = await window.api.getStatus();
  if (r && r.counts) applyCounts(r.counts);
  if (r && r.log && $('logPath')) $('logPath').textContent = r.log;
}

// ---- review ----
$('refreshReview').addEventListener('click', loadReview);
$('retryAll').addEventListener('click', () => {
  setPill('busy', 'retrying');
  document.querySelector('.tab[data-tab="ingest"]').click();
  window.api.retry();
});

async function loadReview() {
  const r = await window.api.getReview();
  const body = $('reviewBody');
  const docs = (r && r.docs) || [];
  if (!docs.length) { body.innerHTML = '<tr><td colspan="3" class="empty">— queue empty —</td></tr>'; return; }
  body.innerHTML = docs.map((d) =>
    `<tr><td>${d.doc_id}</td><td>${d.stage || '—'}</td><td>${d.error || ''}</td></tr>`).join('');
}

// ---- settings ----
const FIELDS = ['pythonPath', 'coreDir', 'stateDb', 'jurisdiction', 'concurrency',
  'voyageRpm', 'llmRpm', 'weaviateUrl',
  'weaviateApiKey', 'voyageApiKey', 'diEndpoint', 'diKey', 'azureDiModel', 'anthropicKey',
  'deepseekKey', 'geminiKey', 'azureOpenaiEndpoint', 'azureOpenaiKey',
  'azureOpenaiApiVersion'];

async function loadSettings() {
  const s = await window.api.getSettings();
  FIELDS.forEach((f) => { if ($(f)) $(f).value = s[f] || ''; });
  $('llmChoice').value = `${s.llmProvider || 'anthropic'}|${s.llmModel || 'claude-haiku-4-5'}`;
  $('llmThinking').checked = !!s.llmThinking;
  $('amendLlm').checked = (s.amendExtractor === 'llm');
  $('tableVision').checked = !!s.tableVision;
  $('encNote').textContent = s.encryptionAvailable
    ? '· keys encrypted at rest' : '· plaintext (no OS keychain)';
}

$('saveSettings').addEventListener('click', async () => {
  const obj = {};
  FIELDS.forEach((f) => { if ($(f)) obj[f] = $(f).value.trim(); });
  const [provider, model] = $('llmChoice').value.split('|');
  obj.llmProvider = provider;
  obj.llmModel = model;
  obj.llmThinking = $('llmThinking').checked;
  obj.amendExtractor = $('amendLlm').checked ? 'llm' : 'deterministic';
  obj.tableVision = $('tableVision').checked;
  await window.api.saveSettings(obj);
  $('savedNote').textContent = 'saved ✓';
  setTimeout(() => ($('savedNote').textContent = ''), 2000);
});

// ---- credential / connectivity self-check ----
$('testCreds').addEventListener('click', async () => {
  // persist the current form first so the check uses the keys on screen, not stale ones
  const obj = {};
  FIELDS.forEach((f) => { if ($(f)) obj[f] = $(f).value.trim(); });
  const [provider, model] = $('llmChoice').value.split('|');
  obj.llmProvider = provider; obj.llmModel = model;
  obj.llmThinking = $('llmThinking').checked;
  obj.amendExtractor = $('amendLlm').checked ? 'llm' : 'deterministic';
  obj.tableVision = $('tableVision').checked;
  await window.api.saveSettings(obj);

  $('diagNote').textContent = 'checking…';
  const rep = await window.api.runDiag();
  const lines = [];
  if (!rep || rep.error) {
    lines.push('error: ' + ((rep && rep.error) || 'no response'));
  } else {
    lines.push('Credentials present:');
    for (const [k, v] of Object.entries(rep.configured || {})) {
      const mark = v === true ? '✓' : v === false ? '·' : v;
      lines.push(`  ${mark}  ${k}`);
    }
    lines.push('Connectivity:');
    for (const c of (rep.checks || [])) {
      lines.push(`  ${c.ok ? '✓ OK  ' : '✗ FAIL'}  ${c.service}: ${c.detail}`);
    }
  }
  $('diagOut').textContent = lines.join('\n');
  $('diagOut').hidden = false;
  $('diagNote').textContent = (rep && rep.ok) ? 'all OK ✓' : 'see results';
  setTimeout(() => ($('diagNote').textContent = ''), 3000);
});

// ---- logs ----
$('openLogs').addEventListener('click', async () => {
  const p = await window.api.openLogs();
  if (p) $('logPath').textContent = p;
});

$('resetState').addEventListener('click', async () => {
  if (!confirm('Reset ingest history?\n\nClears the local record of processed documents so they will be re-embedded on the next run. Does not delete anything in Weaviate (vectors are overwritten on re-ingest).')) return;
  $('resetStateNote').textContent = 'clearing…';
  const r = await window.api.resetState();
  $('resetStateNote').textContent = r && typeof r.cleared === 'number'
    ? `✓ cleared (${r.cleared})` : `error: ${(r && r.error) || 'failed'}`;
  setTimeout(() => ($('resetStateNote').textContent = ''), 4000);
});

// ---- Weaviate collections (reset for fast re-testing) ----
function renderCollections(cols) {
  const box = $('collectionList');
  if (!cols || !cols.length) { box.innerHTML = '<div class="empty">— no data —</div>'; return; }
  box.innerHTML = cols.map((c) => {
    const count = c.count < 0 ? 'absent' : `${c.count} objects`;
    return `<div class="coll-row">
      <span class="coll-name">${c.name}</span>
      <span class="coll-count">${count}</span>
      <button class="btn danger sm" data-reset="${c.key}" data-name="${c.name}"
              ${c.count < 0 ? 'disabled' : ''}>Reset</button>
    </div>`;
  }).join('');
  box.querySelectorAll('button[data-reset]').forEach((b) => {
    b.addEventListener('click', () => resetCollection(b.dataset.reset, b.dataset.name));
  });
}

async function loadCollections() {
  $('collectionList').innerHTML = '<div class="empty">loading…</div>';
  const r = await window.api.listCollections();
  if (r && r.collections) renderCollections(r.collections);
  else $('collectionList').innerHTML = `<div class="empty">error: ${(r && r.error) || 'failed'}</div>`;
}

async function resetCollection(key, name) {
  if (!confirm(`Reset ${name}?\n\nThis permanently deletes all its embedded objects for this jurisdiction. The schema is kept. This cannot be undone.`)) return;
  $('resetNote').textContent = `resetting ${name}…`;
  const r = await window.api.resetCollection(key);
  const res = (r && r.results && r.results[0]) || {};
  $('resetNote').textContent = r && r.error ? `error: ${r.error}`
    : `✓ ${name}: deleted ${res.deleted ?? 0}`;
  setTimeout(() => ($('resetNote').textContent = ''), 4000);
  loadCollections();
}

$('refreshCollections').addEventListener('click', loadCollections);
$('resetAllCollections').addEventListener('click', async () => {
  if (!confirm('Reset ALL collections?\n\nThis permanently deletes every embedded object across all 5 collections for this jurisdiction. The schemas are kept. This cannot be undone.')) return;
  $('resetNote').textContent = 'resetting all…';
  const r = await window.api.resetCollection('all');
  $('resetNote').textContent = r && r.error ? `error: ${r.error}` : '✓ all collections reset';
  setTimeout(() => ($('resetNote').textContent = ''), 4000);
  loadCollections();
});

// ---- browse (inspect what was embedded for one law) ----
let lastInspect = null;   // { collection, law, objects }
const _TEXT_FIELDS = ['chunk_text', 'new_text', 'change_description', 'delegation_scope'];
const _META_FIELDS = ['canonical_id', 'article_number', 'article_title', 'hierarchy_path',
  'version', 'is_current', 'valid_from', 'valid_to', 'legal_force_status', 'chunk_index',
  'total_chunks', 'context_window_id', 'legal_domain', 'action', 'scope',
  'target_law_number', 'target_article_number', 'enabling_law_number', 'implementing_law_number'];

async function loadLaws() {
  const r = await window.api.listLaws();
  const laws = (r && r.laws) || [];
  $('lawOptions').innerHTML = laws.map((l) =>
    `<option value="${esc(l.law_number)}">${esc(l.instrument_key || '')} · ` +
    `${esc((l.document_title || '').slice(0, 60))} · ${l.chunks} chunks</option>`).join('');
  $('browseNote').textContent = r && r.error ? `error: ${esc(r.error)}`
    : (laws.length ? `${laws.length} law(s) embedded` : 'no laws embedded yet — run an ingest first');
  setTimeout(() => ($('browseNote').textContent = ''), 4000);
}

async function loadChunks() {
  const collection = $('browseCollection').value;
  const law = $('browseLaw').value.trim();
  if (!law) { $('browseNote').textContent = 'enter a law number'; return; }
  $('browseResults').innerHTML = '<div class="empty">loading…</div>';
  $('copyJson').disabled = $('copyMd').disabled = true;
  const r = await window.api.inspectLaw(collection, law);
  if (!r || r.error) {
    $('browseResults').innerHTML = `<div class="empty">error: ${esc((r && r.error) || 'failed')}</div>`;
    return;
  }
  lastInspect = { collection, law, objects: r.objects || [] };
  renderBrowse(lastInspect);
  const has = lastInspect.objects.length > 0;
  $('copyJson').disabled = $('copyMd').disabled = !has;
}

function renderBrowse({ collection, law, objects }) {
  const box = $('browseResults');
  if (!objects.length) {
    box.innerHTML = `<div class="empty">no objects for ${esc(law)} in ${esc(collection)}</div>`;
    return;
  }
  const head = `<div class="browse-head">${esc(collection)} · ${esc(law)} · <b>${objects.length}</b> object(s)</div>`;
  const cards = objects.map((o) => {
    const id = o.canonical_id || o._uuid || '';
    const meta = _META_FIELDS
      .filter((f) => o[f] !== undefined && o[f] !== '' && o[f] !== null)
      .map((f) => `<span class="kv"><i>${f}</i>${esc(Array.isArray(o[f]) ? o[f].join(', ') : o[f])}</span>`)
      .join('');
    const text = _TEXT_FIELDS.map((f) => o[f] ? `<pre class="chunk-text">${esc(o[f])}</pre>` : '').join('');
    return `<div class="chunk-card"><div class="chunk-id">${esc(id)}</div>` +
           `<div class="chunk-meta">${meta}</div>${text}</div>`;
  }).join('');
  box.innerHTML = head + cards;
}

function toMarkdown({ collection, law, objects }) {
  let md = `# ${collection} — law ${law} (${objects.length} object(s))\n\n`;
  for (const o of objects) {
    md += `## ${o.canonical_id || o._uuid || ''}\n`;
    const meta = _META_FIELDS
      .filter((f) => o[f] !== undefined && o[f] !== '' && o[f] !== null)
      .map((f) => `- **${f}**: ${Array.isArray(o[f]) ? o[f].join(', ') : o[f]}`);
    if (meta.length) md += meta.join('\n') + '\n';
    for (const f of _TEXT_FIELDS) if (o[f]) md += `\n\`\`\`\n${o[f]}\n\`\`\`\n`;
    md += '\n';
  }
  return md;
}

async function copyText(text, label) {
  try { await navigator.clipboard.writeText(text); $('browseNote').textContent = `copied ${label} ✓`; }
  catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); $('browseNote').textContent = `copied ${label} ✓`; }
    catch (e) { $('browseNote').textContent = 'copy failed'; }
    document.body.removeChild(ta);
  }
  setTimeout(() => ($('browseNote').textContent = ''), 3000);
}

$('refreshLaws').addEventListener('click', loadLaws);
$('loadChunks').addEventListener('click', loadChunks);
$('copyJson').addEventListener('click', () => lastInspect && copyText(JSON.stringify(lastInspect.objects, null, 2), 'JSON'));
$('copyMd').addEventListener('click', () => lastInspect && copyText(toMarkdown(lastInspect), 'Markdown'));

// ---- timeline / chain QA (consolidate · graph-status · history) ----
$('runConsolidate').addEventListener('click', async () => {
  const btn = $('runConsolidate');
  btn.disabled = true;
  $('consolidateNote').textContent = 'rebuilding… (re-embeds amended text; may take a while)';
  const r = await window.api.consolidate();
  btn.disabled = false;
  $('consolidateNote').textContent = '';
  $('consolidateOut').hidden = false;
  if (!r || r.error) {
    $('consolidateOut').textContent = 'error: ' + ((r && r.error) || 'failed');
    return;
  }
  $('consolidateOut').textContent =
    `articles touched:  ${r.articles ?? 0}\n` +
    `versions written:  ${r.versions_written ?? 0}\n` +
    `pending (target law not ingested yet):  ${r.pending ?? 0}`;
});

$('runGraphStatus').addEventListener('click', async () => {
  $('graphNote').textContent = 'checking…';
  const r = await window.api.graphStatus();
  $('graphNote').textContent = '';
  $('graphOut').hidden = false;
  if (!r || r.error) {
    $('graphOut').textContent = 'error: ' + ((r && r.error) || 'failed');
    return;
  }
  const dangling = r.dangling_targets || [];
  const unresolved = r.unresolved_targets || [];
  const lines = [
    `amendment edges:               ${r.amendments ?? 0}`,
    `  resolved (target present):   ${r.target_law_present ?? 0}`,
    `  dangling (target absent):    ${r.target_law_missing ?? 0}`,
    `  unresolved (no canonical):   ${r.unresolved ?? 0}`,
    `  undated:                     ${r.undated ?? 0}`,
  ];
  if (dangling.length) lines.push('', 'Dangling targets (target law not ingested yet):',
    ...dangling.map((t) => '  ' + t));
  if (unresolved.length) lines.push('', 'Unresolved references:',
    ...unresolved.map((t) => '  ' + t));
  $('graphOut').textContent = lines.join('\n');
});

$('runHistory').addEventListener('click', async () => {
  const law = $('historyLaw').value.trim();
  const article = $('historyArticle').value.trim();
  if (!law || !article) {
    $('historyNote').textContent = 'enter law + article';
    setTimeout(() => ($('historyNote').textContent = ''), 2500);
    return;
  }
  $('historyOut').innerHTML = '<div class="empty">loading…</div>';
  const r = await window.api.provisionHistory(law, article);
  if (!r || r.error) {
    $('historyOut').innerHTML = `<div class="empty">error: ${esc((r && r.error) || 'failed')}</div>`;
    return;
  }
  renderHistory(r);
});

function renderHistory(h) {
  const head = `<div class="browse-head">${esc(h.law_number)} άρθρο ${esc(h.article_number)} · ` +
    `<b>${h.version_count}</b> version(s) · <b>${h.edit_count}</b> edge(s)` +
    (h.unresolved_edits ? ` · ${h.unresolved_edits} unresolved` : '') + `</div>`;
  const pending = (h.edit_count && !h.target_present)
    ? `<p class="hint">No text versions yet — the target/base law isn't ingested; the edges below resolve once it is.</p>` : '';
  const versions = (h.versions || []).map((v) =>
    `<div class="chunk-card"><div class="chunk-id">v${v.version ?? '?'} · ` +
    `${esc(v.valid_from || '?')} → ${esc(v.valid_to || 'current')}${v.is_current ? ' · current' : ''}</div>` +
    `<div class="chunk-meta"><span class="kv"><i>status</i>${esc(v.legal_force_status || '')}</span>` +
    `<span class="kv"><i>chars</i>${v.chars ?? 0}</span></div>` +
    (v.text_preview ? `<pre class="chunk-text">${esc(v.text_preview)}</pre>` : '') + `</div>`).join('');
  const edits = (h.edits || []).map((e) =>
    `<div class="chunk-card"><div class="chunk-id">${esc(e.effective_date || '????-??-??')} · ` +
    `${esc(e.action || '?')}${e.resolved === false ? ' · UNRESOLVED' : ''}</div>` +
    `<div class="chunk-meta"><span class="kv"><i>by</i>ν.${esc(e.source_law_number || '?')}` +
    `${e.source_article_number ? (' άρθρο ' + esc(e.source_article_number)) : ''}</span>` +
    `<span class="kv"><i>scope</i>${esc(e.scope || '')}</span></div></div>`).join('');
  $('historyOut').innerHTML = head + pending +
    `<div class="browse-head">Text timeline</div>` +
    (versions || '<div class="empty">— no versions —</div>') +
    `<div class="browse-head">Amendment edges (chronological)</div>` +
    (edits || '<div class="empty">— no edges —</div>');
}

// ---- app version ----
async function showVersion() {
  try {
    const v = await window.api.getAppVersion();
    if (!v) return;
    $('appVer').textContent = `v${v}`;
    document.title = `Lawgic · FEK Ingest · v${v}`;
  } catch (_) { /* non-fatal */ }
}

// ---- init ----
renderCounts();
refreshStatus();
showVersion();
