// ════════════════════════════════════════════════════════════════
// Auditarr frontend
// ════════════════════════════════════════════════════════════════

// State
let allFiles = [];
let filteredFiles = [];
let currentFileCategory = null;   // auto-picked on load
let currentSeverity = 'all';
let currentSearch = '';
let selectedIds = new Set();
let cfg = {};
let activeJobId = null;
let pollInterval = null;
let pluginsCache = [];
let integrationsCache = [];
let rulesSchema = null;
let customRulesCache = [];
let pendingIds = null;
let pendingPluginKind = null;
let pendingPlugin = null;

// Dashboard tab
let currentDashCategory = 'all';
let lastStats = null;

// Rule editor state
let editingRuleId = null;
let ruleEditorMode = 'visual'; // visual | json
let editorConditions = [{ field: 'codec', op: 'eq', value: '' }];
let editorMatch = 'all';

// Severity configuration (matches checks.py)
const SEVERITY_LABELS = {
  unplayable: 'Unplayable',
  always_transcode: 'Always Transcode',
  possible_transcode: 'Possible Transcode',
  high_bitrate: 'High Bitrate',
  info: 'Info',
  ok: 'OK',
};
const SEV_RANK = { ok:0, info:1, high_bitrate:2, possible_transcode:3, always_transcode:4, unplayable:5 };

const CATEGORY_LABELS = { media: 'Media', subtitle: 'Subtitles', image: 'Images', metadata: 'Metadata', junk: 'Junk' };
const CATEGORY_ICONS = { media: '🎬', subtitle: '💬', image: '🖼', metadata: '📄', junk: '❓' };

const MAX_VISIBLE_FILES = 500;  // Cap visible rows for performance

// ──────────────────────────────────────────────────────────────
// Init
// ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadConfig();
  // Pre-load plugins schema (avoids picker race)
  loadPluginsAndIntegrations();
  loadRulesSchema();
  await loadStats();
  await loadFiles();
  loadIntegrationEvents();
});

async function loadPluginsAndIntegrations() {
  try {
    const [pluginsR, integrationsR] = await Promise.all([
      fetch('/api/integrations/plugins'),
      fetch('/api/integrations'),
    ]);
    pluginsCache = await pluginsR.json();
    integrationsCache = await integrationsR.json();
    renderIntegrations();
  } catch (e) {}
}

async function loadRulesSchema() {
  try {
    const r = await fetch('/api/rules/schema');
    rulesSchema = await r.json();
  } catch (e) {}
}

// ──────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmtSize(bytes) {
  if (!bytes) return '—';
  const units = ['B','KB','MB','GB','TB'];
  let i = 0; let v = bytes;
  while (v >= 1024 && i < units.length-1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 ? 0 : (v >= 10 ? 1 : 2)) + ' ' + units[i];
}
function toast(msg, type='ok') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span>${type==='ok'?'✓':'✕'}</span>${escHtml(msg)}`;
  document.getElementById('toast-area').appendChild(el);
  setTimeout(() => el.remove(), 3500);
}
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
document.addEventListener('click', e => {
  if (e.target.classList?.contains('modal-bg')) e.target.classList.remove('open');
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.modal-bg.open').forEach(m => m.classList.remove('open'));
});

// Debounce
function debounce(fn, ms) {
  let h;
  return (...args) => { clearTimeout(h); h = setTimeout(() => fn(...args), ms); };
}

// ──────────────────────────────────────────────────────────────
// Navigation
// ──────────────────────────────────────────────────────────────
function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('view-' + name).classList.add('active');
  const navBtn = document.getElementById('nav-' + name);
  if (navBtn) navBtn.classList.add('active');

  const titles = {
    dashboard: 'Dashboard',
    files: 'Files',
    integrations: 'Integrations',
    rules: 'Custom Rules',
    automation: 'Automation',
    config: 'Settings',
  };
  document.getElementById('top-title').textContent = titles[name] || name;
  document.getElementById('top-sub').textContent = '';
  document.getElementById('search-input').style.display = (name === 'files') ? 'block' : 'none';

  if (name === 'integrations') { loadPluginsAndIntegrations(); loadIntegrationEvents(); }
  if (name === 'automation') { loadAutomationRules(); }
  if (name === 'rules') { loadCustomRules(); }
}

// ──────────────────────────────────────────────────────────────
// Scans
// ──────────────────────────────────────────────────────────────
async function startScan() { await _kickoff('/api/scan/start',  '⏳ Scanning'); }
async function startReeval() { await _kickoff('/api/scan/reeval', '⚡ Re-evaluating'); }

async function _kickoff(url, label) {
  const btn = document.getElementById('btn-scan');
  const btn2 = document.getElementById('btn-reeval');
  btn.disabled = btn2.disabled = true;
  btn.innerHTML = label;
  document.getElementById('progress-wrap').style.display = 'block';
  try {
    const r = await fetch(url, { method: 'POST' });
    const d = await r.json();
    activeJobId = d.job_id;
    pollInterval = setInterval(pollJob, 700);
  } catch (e) {
    toast('Failed to start', 'err');
    resetScanBtn();
  }
}

async function pollJob() {
  if (!activeJobId) return;
  try {
    const r = await fetch(`/api/scan/${activeJobId}/status`);
    const s = await r.json();
    const pct = s.total > 0 ? (s.processed / s.total * 100) : 0;
    document.getElementById('progress-bar').style.width = pct + '%';
    document.getElementById('progress-text').textContent = `${s.processed} / ${s.total}`;

    if (s.status === 'done') {
      clearInterval(pollInterval); pollInterval = null;
      const wasReeval = (s.kind === 'reeval');
      activeJobId = null;
      resetScanBtn();
      await Promise.all([loadStats(), loadFiles()]);
      toast(wasReeval ? 'Re-evaluation complete (no rescan)' : `Scan complete — ${s.total} files`, 'ok');
    }
  } catch (e) {}
}

function resetScanBtn() {
  document.getElementById('btn-scan').disabled = false;
  document.getElementById('btn-reeval').disabled = false;
  document.getElementById('btn-scan').innerHTML = '▶ Run Full Scan';
  document.getElementById('btn-reeval').innerHTML = '⚡ Re-eval Rules';
  setTimeout(() => { document.getElementById('progress-wrap').style.display = 'none'; }, 1500);
}

// ──────────────────────────────────────────────────────────────
// Dashboard
// ──────────────────────────────────────────────────────────────
async function loadStats() {
  const r = await fetch('/api/stats');
  const s = await r.json();
  lastStats = s;

  if (s.total === 0) {
    document.getElementById('welcome-dash').style.display = 'flex';
    document.getElementById('dashboard-content').style.display = 'none';
    document.getElementById('badge-files').textContent = '0';
    return;
  }
  document.getElementById('welcome-dash').style.display = 'none';
  document.getElementById('dashboard-content').style.display = 'block';
  document.getElementById('badge-files').textContent = s.total;

  // Update tab counts
  document.getElementById('cnt-all').textContent = s.total;
  for (const cat of ['media','subtitle','image','metadata','junk']) {
    const c = (s.per_category && s.per_category[cat]) ? s.per_category[cat].total : 0;
    const el = document.getElementById('cnt-' + cat);
    if (el) el.textContent = c;
    // hide tabs with 0 count
    const tab = document.querySelector(`.dash-tab[data-cat="${cat}"]`);
    if (tab) tab.style.display = c > 0 ? 'inline-flex' : 'none';
  }

  renderDashboardForCategory(currentDashCategory);
}

function setDashCategory(cat) {
  currentDashCategory = cat;
  document.querySelectorAll('.dash-tab').forEach(t => t.classList.toggle('active', t.dataset.cat === cat));
  renderDashboardForCategory(cat);
}

function renderDashboardForCategory(cat) {
  const s = lastStats;
  if (!s) return;

  let sev, total, sizeGb, label;
  if (cat === 'all') {
    sev = s.severity || {};
    total = s.total;
    sizeGb = s.total_size_gb;
    label = 'Library';
  } else {
    const pc = (s.per_category && s.per_category[cat]) || { severity: {}, total: 0, size_gb: 0 };
    sev = pc.severity || {};
    total = pc.total;
    sizeGb = pc.size_gb;
    label = CATEGORY_LABELS[cat] || cat;
  }

  document.getElementById('health-panel-title').textContent = `${label} Health`;

  // Health score
  const okish = (sev.ok || 0) + (sev.info || 0);
  const score = total > 0 ? Math.round(okish / total * 100) : 0;
  const scoreEl = document.getElementById('health-score');
  scoreEl.textContent = total > 0 ? score : '—';
  scoreEl.style.color = total === 0 ? 'var(--muted)' :
                        score >= 90 ? 'var(--sev-ok)' :
                        score >= 70 ? 'var(--sev-possible-transcode)' :
                        score >= 40 ? 'var(--sev-always-transcode)' :
                                      'var(--sev-unplayable)';

  let statusText, detailText;
  if (total === 0) {
    statusText = 'NO FILES'; detailText = `No ${label.toLowerCase()} files in this library yet.`;
  } else if (score >= 90) {
    statusText = 'EXCELLENT'; detailText = `${total} ${label.toLowerCase()} files; almost all clean.`;
  } else if (score >= 70) {
    statusText = 'GOOD'; detailText = `${total} ${label.toLowerCase()} files; some clients may transcode.`;
  } else if (score >= 40) {
    statusText = 'NEEDS ATTENTION'; detailText = `${total} ${label.toLowerCase()} files; many will transcode.`;
  } else {
    statusText = 'POOR'; detailText = `${total} ${label.toLowerCase()} files; significant playback issues.`;
  }
  document.getElementById('health-status').textContent = statusText;
  document.getElementById('health-detail').textContent = detailText;
  document.getElementById('health-meta-side').textContent = `${total.toLocaleString()} files · ${sizeGb} GB`;

  renderSeverityBars(sev, total, cat);

  // Category-specific tile grid
  const tiles = [];
  if (cat === 'all' || cat === 'media') {
    tiles.push({ val: s.dovi_p5,    label: 'DoVi Profile 5', click: 'goToFilesWithSearch("Profile 5")' });
    tiles.push({ val: s.av1,        label: 'AV1' });
    tiles.push({ val: s.hevc,       label: 'HEVC' });
    tiles.push({ val: s.dovi_other, label: 'DoVi (other)' });
  }
  if (cat === 'all') {
    tiles.unshift({ val: total, label: 'Total Files' });
    tiles.unshift({ val: sizeGb + ' GB', label: 'Total Size' });
  } else {
    tiles.unshift({ val: sizeGb + ' GB', label: `${label} Size` });
    tiles.unshift({ val: total, label: `${label} Files` });
  }
  document.getElementById('tile-grid').innerHTML = tiles.map(t => `
    <div class="tile" ${t.click ? `onclick="${t.click}"` : ''}>
      <div class="tile-val">${escHtml(t.val)}</div>
      <div class="tile-label">${escHtml(t.label)}</div>
    </div>
  `).join('');

  // Bars
  if (cat === 'all' || cat === 'media') {
    document.getElementById('distrib-title').textContent = 'Video Codecs';
    renderBars('codec-bars', s.codecs || {});
    renderBars('audio-bars', s.audio_codecs || {});
    renderBars('res-bars', s.resolutions || {});
    document.getElementById('extra-charts').style.display = 'grid';
  } else {
    // For subtitle/image/metadata/junk, hide media-specific charts
    document.getElementById('distrib-title').textContent = 'Issue Categories';
    renderBars('codec-bars', s.issue_categories || {});
    document.getElementById('extra-charts').style.display = 'none';
  }
  renderBars('issue-cat-bars', s.issue_categories || {});
}

function renderSeverityBars(sev, total, cat) {
  const order = ['unplayable','always_transcode','possible_transcode','high_bitrate','info','ok'];
  const html = order.map(s => {
    const n = sev[s] || 0;
    const pct = total > 0 ? (n / total * 100) : 0;
    return `
      <div class="sev-row" onclick="quickFilterSeverity('${s}', '${cat}')">
        <div class="sev-dot ${s}"></div>
        <div class="sev-info-col">
          <div class="sev-name-row">
            <span class="sev-name">${escHtml(SEVERITY_LABELS[s])}</span>
          </div>
          <div class="sev-bar-bg"><div class="sev-bar-fill ${s}" style="width:${pct}%"></div></div>
        </div>
        <div class="sev-count">${n}</div>
      </div>`;
  }).join('');
  document.getElementById('severity-bars').innerHTML = html;
}

function renderBars(id, data) {
  const el = document.getElementById(id);
  if (!el) return;
  const entries = Object.entries(data || {}).sort((a,b) => b[1]-a[1]).slice(0, 8);
  if (!entries.length) { el.innerHTML = '<div class="small">No data</div>'; return; }
  const max = entries[0][1];
  el.innerHTML = entries.map(([k,v]) => `
    <div class="bar-row">
      <div class="bar-label" title="${escHtml(k)}">${escHtml(k)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${(v/max)*100}%"></div></div>
      <div class="bar-count">${v}</div>
    </div>
  `).join('');
}

function quickFilterSeverity(sev, cat) {
  showView('files');
  if (cat && cat !== 'all') {
    currentFileCategory = cat;
  }
  setSeverity(sev);
}
function goToFilesWithSearch(q) {
  showView('files');
  document.getElementById('search-input').value = q;
  currentSearch = q.toLowerCase();
  applyFilter();
}

// ──────────────────────────────────────────────────────────────
// Files
// ──────────────────────────────────────────────────────────────
async function loadFiles() {
  // Use server-side limit; fetch up to MAX_VISIBLE_FILES * 4 = 2000 to allow filtering
  // (server-side filtering would be ideal for HUGE libraries; for this app cap at 5000)
  const r = await fetch('/api/files?limit=5000');
  allFiles = await r.json();
  renderCategorySubnav();
  if (allFiles.length === 0) {
    document.getElementById('welcome-files').style.display = 'flex';
    document.getElementById('files-wrap').style.display = 'none';
  } else {
    document.getElementById('welcome-files').style.display = 'none';
    document.getElementById('files-wrap').style.display = 'flex';
    document.getElementById('files-wrap').style.flexDirection = 'column';
  }
  applyFilter();
}

function renderCategorySubnav() {
  // Count files per category
  const counts = {};
  for (const f of allFiles) {
    const c = f.category || 'junk';
    counts[c] = (counts[c] || 0) + 1;
  }
  const order = ['media','subtitle','image','metadata','junk'];
  const available = order.filter(c => counts[c] > 0);

  // Auto-pick first available if current is empty (FIX FOR EMPTY CATEGORIES BUG)
  if (!currentFileCategory || !counts[currentFileCategory]) {
    currentFileCategory = available[0] || 'media';
  }

  const html = available.map(c => `
    <button class="subnav-btn ${c === currentFileCategory ? 'active' : ''}" onclick="setFileCategory('${c}')">
      ${CATEGORY_ICONS[c] || '·'} ${escHtml(CATEGORY_LABELS[c] || c)}
      <span class="count">${counts[c]}</span>
    </button>
  `).join('');
  document.getElementById('cat-subnav').innerHTML = html ||
    '<button class="subnav-btn active">No files</button>';
}

function setFileCategory(cat) {
  currentFileCategory = cat;
  selectedIds.clear();
  renderCategorySubnav();
  applyFilter();
}

function setSeverity(sev) {
  currentSeverity = sev;
  document.querySelectorAll('#filter-bar .chip').forEach(c => c.classList.toggle('active', c.dataset.sev === sev));
  applyFilter();
}

const onSearch = debounce(() => {
  currentSearch = document.getElementById('search-input').value.toLowerCase();
  applyFilter();
}, 200);

function applyFilter() {
  let res = allFiles.filter(f => (f.category || 'junk') === currentFileCategory);

  if (currentSeverity !== 'all') {
    res = res.filter(f => f.severity === currentSeverity);
  }
  if (currentSearch) {
    res = res.filter(f =>
      (f.path||'').toLowerCase().includes(currentSearch) ||
      (f.issues||[]).some(i => (i.message||'').toLowerCase().includes(currentSearch))
    );
  }
  res.sort((a, b) => {
    const sa = SEV_RANK[a.severity] || 0;
    const sb = SEV_RANK[b.severity] || 0;
    if (sb !== sa) return sb - sa;
    return (a.path||'').localeCompare(b.path||'');
  });
  filteredFiles = res;
  const truncated = res.length > MAX_VISIBLE_FILES;
  const cap = truncated ? `${MAX_VISIBLE_FILES} of ${res.length}` : `${res.length}`;
  document.getElementById('file-count').textContent = `${cap} files`;
  renderFiles();
}

function renderFiles() {
  const el = document.getElementById('files-list');
  if (filteredFiles.length === 0) {
    el.innerHTML = `
      <div class="empty">
        <div class="empty-icon">∅</div>
        <p>No files match these filters</p>
      </div>`;
    return;
  }
  // Cap visible rows for performance
  const visible = filteredFiles.slice(0, MAX_VISIBLE_FILES);
  let html = visible.map(fileCardHtml).join('');
  if (filteredFiles.length > MAX_VISIBLE_FILES) {
    html += `<div class="empty" style="padding:20px"><p class="small">Showing ${MAX_VISIBLE_FILES} of ${filteredFiles.length} matches. Refine filters to see more.</p></div>`;
  }
  el.innerHTML = html;
}

function fileCardHtml(f) {
  const sev = f.severity || 'ok';
  const isChecked = selectedIds.has(f.id);

  const issues = f.issues || [];
  // Show only top issue inline; detail modal shows all
  const topIssue = issues[0];
  const issueHtml = topIssue ? `<div class="issues-row">
    <span class="issue-chip ${topIssue.severity}" title="${escHtml(topIssue.message||'')}">${escHtml(topIssue.message||'')}</span>
    ${issues.length > 1 ? `<span class="issue-chip info">+${issues.length - 1}</span>` : ''}
  </div>` : '';

  const meta = [];
  if (f.codec) meta.push(`<span class="meta">${escHtml(f.codec)}</span>`);
  if (f.audio_codec) meta.push(`<span class="meta">${escHtml(f.audio_codec)}</span>`);
  if (f.resolution) meta.push(`<span class="meta">${escHtml(f.resolution)}</span>`);
  if (f.size_bytes) meta.push(`<span class="meta">${fmtSize(f.size_bytes)}</span>`);
  if (f.bitrate) meta.push(`<span class="meta">${Math.round(f.bitrate / 1_000_000)} Mbps</span>`);
  const metaHtml = meta.join('<span class="sep">·</span>');

  let arrPills = '';
  if (f.arr_kind) {
    arrPills = `<span class="arr-pill ${f.arr_kind}">${escHtml(f.arr_kind)}</span>`;
    if (f.monitored !== null && f.monitored !== undefined) {
      arrPills += ` <span class="monitored-pill ${f.monitored ? 'on' : 'off'}">${f.monitored ? '👁 monitored' : '∅ unmonitored'}</span>`;
    }
  }

  return `
    <div class="file-card ${sev}" data-id="${f.id}" onclick="onFileCardClick(event, ${f.id})">
      <input type="checkbox" class="check" ${isChecked ? 'checked' : ''}
             onclick="event.stopPropagation()"
             onchange="toggleSelect(${f.id}, this)" />
      <div class="file-main">
        <div class="file-title-row">
          <span class="file-name" title="${escHtml(f.path)}">${escHtml(f.name || f.path)}</span>
          <span class="sev-badge ${sev}">${escHtml(SEVERITY_LABELS[sev] || sev)}</span>
          ${arrPills}
        </div>
        <div class="file-path-row" title="${escHtml(f.path)}">${escHtml(f.path)}</div>
        ${metaHtml ? `<div class="file-meta-row">${metaHtml}</div>` : ''}
        ${issueHtml}
      </div>
      <div class="file-actions" onclick="event.stopPropagation()">
        ${f.arr_kind ? `<button class="icon-btn" onclick="toggleMonitor(${f.id}, ${!f.monitored})" title="${f.monitored ? 'Unmonitor' : 'Monitor'}">${f.monitored ? '👁' : '∅'}</button>` : ''}
        <button class="icon-btn" onclick="rescanDialog(${f.id})" title="Re-probe">↻</button>
        <button class="icon-btn" onclick="renameDialog(${f.id})" title="Rename">✎</button>
        <button class="icon-btn" onclick="moveDialog(${f.id})" title="Move">→</button>
        <button class="icon-btn danger" onclick="deleteDialog(${f.id})" title="Delete">✕</button>
      </div>
    </div>
  `;
}

function onFileCardClick(ev, id) {
  if (ev?.target.closest('button, input')) return;
  openFileDetail(id);
}

function toggleSelect(id, cb) {
  if (cb.checked) selectedIds.add(id); else selectedIds.delete(id);
  updateBulkBar();
}
function toggleSelectAll(cb) {
  if (cb.checked) filteredFiles.slice(0, MAX_VISIBLE_FILES).forEach(f => selectedIds.add(f.id));
  else selectedIds.clear();
  renderFiles();
  updateBulkBar();
}
function updateBulkBar() {
  const n = selectedIds.size;
  const bar = document.getElementById('bulk-bar');
  if (n > 0) {
    bar.classList.remove('hidden');
    document.getElementById('bulk-count').textContent = `${n} selected`;
  } else {
    bar.classList.add('hidden');
  }
}

// ──────────────────────────────────────────────────────────────
// File actions (rename / move / delete / rescan / monitor)
// ──────────────────────────────────────────────────────────────
function renameDialog(id) {
  const f = allFiles.find(x => x.id === id);
  if (!f) return;
  pendingIds = [id];
  document.getElementById('rename-orig').textContent = f.path;
  document.getElementById('rename-input').value = f.name || '';
  openModal('modal-rename');
}
async function confirmRename() {
  if (!pendingIds?.length) return;
  const newName = document.getElementById('rename-input').value.trim();
  if (!newName) return;
  const r = await fetch(`/api/files/${pendingIds[0]}/rename`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ new_name: newName })
  });
  const d = await r.json();
  if (d.ok) {
    closeModal('modal-rename'); closeModal('modal-detail');
    toast('Renamed', 'ok');
    await loadFiles();
  } else { toast(d.error || 'Failed', 'err'); }
}

function deleteDialog(id) {
  pendingIds = [id];
  const f = allFiles.find(x => x.id === id);
  document.getElementById('delete-msg').textContent = `Delete "${f?.name || f?.path || id}"? This cannot be undone.`;
  openModal('modal-delete');
}
function bulkDelete() {
  pendingIds = [...selectedIds];
  document.getElementById('delete-msg').textContent = `Delete ${pendingIds.length} selected files? This cannot be undone.`;
  openModal('modal-delete');
}
async function confirmDelete() {
  if (!pendingIds?.length) return;
  closeModal('modal-delete'); closeModal('modal-detail');
  let ok = 0, fail = 0;
  for (const id of pendingIds) {
    try {
      const r = await fetch(`/api/files/${id}/delete`, { method: 'POST' });
      const d = await r.json();
      if (d.ok) { ok++; selectedIds.delete(id); } else fail++;
    } catch { fail++; }
  }
  toast(`Deleted ${ok}${fail ? ' · ' + fail + ' failed' : ''}`, ok ? 'ok' : 'err');
  await loadFiles();
  updateBulkBar();
}

function moveDialog(id) {
  pendingIds = [id];
  document.getElementById('move-input').value = '';
  openModal('modal-move');
}
function bulkMoveDialog() {
  pendingIds = [...selectedIds];
  document.getElementById('move-input').value = '';
  openModal('modal-move');
}
async function confirmMove() {
  const dest = document.getElementById('move-input').value.trim();
  if (!dest || !pendingIds?.length) return;
  closeModal('modal-move'); closeModal('modal-detail');
  let ok = 0, fail = 0;
  for (const id of pendingIds) {
    try {
      const r = await fetch(`/api/files/${id}/move`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ destination: dest })
      });
      const d = await r.json();
      if (d.ok) { ok++; selectedIds.delete(id); } else fail++;
    } catch { fail++; }
  }
  toast(`Moved ${ok}${fail ? ' · ' + fail + ' failed' : ''}`, ok ? 'ok' : 'err');
  await loadFiles();
  updateBulkBar();
}

function rescanDialog(id) {
  pendingIds = [id];
  const f = allFiles.find(x => x.id === id);
  document.getElementById('rescan-msg').innerHTML = `
    <p class="small">Re-run <code>ffprobe</code> on:</p>
    <p class="small" style="margin-top: 4px; color: var(--text2)">${escHtml(f?.path||'')}</p>
    <p class="small" style="margin-top: 10px">For rule-only changes, use <strong>⚡ Re-eval Rules</strong> instead — it's instant and doesn't touch the file.</p>`;
  openModal('modal-rescan');
}
function bulkRescan() {
  pendingIds = [...selectedIds];
  document.getElementById('rescan-msg').innerHTML = `
    <p class="small">Re-run <code>ffprobe</code> on <strong>${pendingIds.length}</strong> selected files?</p>
    <p class="small" style="margin-top: 8px">For rule-only changes, use <strong>⚡ Re-eval Rules</strong> instead.</p>`;
  openModal('modal-rescan');
}
async function confirmRescan() {
  if (!pendingIds?.length) return;
  closeModal('modal-rescan'); closeModal('modal-detail');
  const paths = pendingIds.map(id => allFiles.find(f => f.id === id)?.path).filter(Boolean);
  try {
    const r = await fetch('/api/scan/targeted', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ paths })
    });
    const { job_id } = await r.json();
    activeJobId = job_id;
    toast(`Re-probing ${paths.length} file(s)…`, 'ok');
    document.getElementById('progress-wrap').style.display = 'block';
    document.getElementById('btn-scan').disabled = true;
    document.getElementById('btn-reeval').disabled = true;
    document.getElementById('btn-scan').innerHTML = '⏳ Re-probing';
    pollInterval = setInterval(pollJob, 600);
  } catch { toast('Failed', 'err'); }
}

async function toggleMonitor(id, monitored) {
  try {
    const r = await fetch(`/api/files/${id}/monitor`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ monitored })
    });
    const d = await r.json();
    toast(d.message || (d.ok ? 'OK' : 'Failed'), d.ok ? 'ok' : 'err');
    if (d.ok) await loadFiles();
  } catch (e) { toast('Failed', 'err'); }
}

// ──────────────────────────────────────────────────────────────
// File detail modal (with unified device matrix)
// ──────────────────────────────────────────────────────────────
async function openFileDetail(id) {
  const r = await fetch(`/api/files/${id}`);
  const f = await r.json();
  if (f.error) { toast(f.error, 'err'); return; }

  const sev = (f.issues || []).reduce((m, i) => SEV_RANK[i.severity] > SEV_RANK[m] ? i.severity : m, 'ok');
  const arrPills = f.arr_kind ? `<span class="arr-pill ${f.arr_kind}">${escHtml(f.arr_kind)}</span>` : '';
  const monPill = (f.monitored !== null && f.monitored !== undefined)
    ? ` <span class="monitored-pill ${f.monitored ? 'on' : 'off'}">${f.monitored ? '👁 monitored' : '∅ unmonitored'}</span>` : '';
  document.getElementById('detail-title').innerHTML = `
    <span class="sev-badge ${sev}">${escHtml(SEVERITY_LABELS[sev] || sev)}</span>
    ${arrPills}${monPill}
    <span style="font-size: 14px;">${escHtml(f.name || f.path)}</span>
  `;

  const tabsHtml = `
    <div class="detail-tabs">
      <button class="detail-tab active" onclick="switchDetailTab(this,'pane-overview')">Overview</button>
      <button class="detail-tab" onclick="switchDetailTab(this,'pane-matrix')">Devices (${(f.device_matrix||[]).length})</button>
      <button class="detail-tab" onclick="switchDetailTab(this,'pane-issues')">Issues (${(f.issues||[]).length})</button>
      ${f.probe ? `<button class="detail-tab" onclick="switchDetailTab(this,'pane-probe')">Raw Probe</button>` : ''}
      ${f.arr_metadata ? `<button class="detail-tab" onclick="switchDetailTab(this,'pane-arr')">*arr metadata</button>` : ''}
    </div>
  `;

  const dur = f.duration_sec ? `${Math.floor(f.duration_sec/60)}m ${Math.round(f.duration_sec%60)}s` : '—';
  const bitrate = f.bitrate ? `${(f.bitrate/1_000_000).toFixed(1)} Mbps` : '—';
  const arrMeta = f.arr_metadata || {};
  const arrInfo = f.arr_kind ? `
    <dt>${f.arr_kind}</dt><dd>${escHtml(arrMeta.title || arrMeta.series_title || '')}${arrMeta.year ? ' (' + arrMeta.year + ')' : ''}${arrMeta.season != null ? ' · S' + arrMeta.season : ''}</dd>
    ${arrMeta.quality ? `<dt>Quality</dt><dd>${escHtml(arrMeta.quality)}</dd>` : ''}
    ${arrMeta.release_group ? `<dt>Release group</dt><dd>${escHtml(arrMeta.release_group)}</dd>` : ''}` : '';
  const pairedHtml = f.paired_media ? `
    <dt>Paired media</dt><dd>${escHtml(f.paired_media.name)}</dd>` : '';

  const overviewPane = `
    <div class="detail-pane active" id="pane-overview">
      <dl class="meta-grid">
        <dt>Path</dt><dd>${escHtml(f.path)}</dd>
        <dt>Category</dt><dd>${escHtml(CATEGORY_LABELS[f.category] || f.category || '—')}</dd>
        <dt>Size</dt><dd>${fmtSize(f.size_bytes)} (${(f.size_bytes||0).toLocaleString()} bytes)</dd>
        ${f.category === 'media' ? `
          <dt>Container</dt><dd>${escHtml(f.container||'—')}</dd>
          <dt>Video codec</dt><dd>${escHtml(f.codec||'—')}</dd>
          <dt>Audio codec</dt><dd>${escHtml(f.audio_codec||'—')}</dd>
          <dt>Resolution</dt><dd>${escHtml(f.resolution||'—')}</dd>
          <dt>Duration</dt><dd>${dur}</dd>
          <dt>Bitrate</dt><dd>${bitrate}</dd>
          ${f.dovi_profile ? `<dt>DoVi profile</dt><dd>${escHtml(f.dovi_profile)}</dd>` : ''}
        ` : ''}
        ${pairedHtml}
        <dt>SHA-256</dt><dd style="font-size:11px;word-break:break-all;font-family:var(--mono)">${escHtml(f.hash_sha256||'(not hashed)')}</dd>
        <dt>First scanned</dt><dd>${escHtml((f.first_scanned||'').replace('T',' ').slice(0,19))}</dd>
        <dt>Last scanned</dt><dd>${escHtml((f.last_scanned||'').replace('T',' ').slice(0,19))}</dd>
        ${arrInfo}
      </dl>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
        <button class="btn" onclick="rescanDialog(${f.id})">↻ Re-probe</button>
        <button class="btn" onclick="renameDialog(${f.id})">✎ Rename</button>
        <button class="btn" onclick="moveDialog(${f.id})">→ Move</button>
        ${f.arr_kind ? `<button class="btn" onclick="toggleMonitor(${f.id}, ${!f.monitored})">${f.monitored ? '∅ Unmonitor' : '👁 Monitor'}</button>` : ''}
        ${f.category === 'junk' ? `<button class="btn" onclick="vtCheck(${f.id})">🛡 Check VirusTotal</button>` : ''}
        <button class="btn btn-danger" onclick="deleteDialog(${f.id})">✕ Delete</button>
      </div>
    </div>`;

  const matrixHtml = renderUnifiedMatrix(f.device_matrix || [], f.compatibility_mode);

  const issuesPane = `
    <div class="detail-pane" id="pane-issues">
      ${(f.issues||[]).length === 0
        ? '<div class="small">No issues — this file is clean.</div>'
        : (f.issues||[]).map(iss => issueCardHtml(iss)).join('')}
    </div>`;

  const probePane = f.probe ? `
    <div class="detail-pane" id="pane-probe">
      <pre class="probe">${escHtml(JSON.stringify(f.probe, null, 2))}</pre>
    </div>` : '';

  const arrPane = f.arr_metadata ? `
    <div class="detail-pane" id="pane-arr">
      <pre class="probe">${escHtml(JSON.stringify(f.arr_metadata, null, 2))}</pre>
    </div>` : '';

  document.getElementById('detail-body').innerHTML = tabsHtml + overviewPane + matrixHtml + issuesPane + probePane + arrPane;
  openModal('modal-detail');
}

function switchDetailTab(btn, paneId) {
  document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.detail-pane').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(paneId).classList.add('active');
}

function renderUnifiedMatrix(matrix, mode) {
  const STATUS_ICON = { ok: '✓', transcode: '↻', fail: '✕', partial: '◐' };

  // Group by ecosystem if mode is "both"
  const showEco = (mode === 'both');
  let cellsHtml;
  if (showEco) {
    const plex = matrix.filter(d => d.ecosystem === 'plex');
    const jellyfin = matrix.filter(d => d.ecosystem === 'jellyfin');
    cellsHtml = `
      ${plex.length ? `<div class="small" style="margin: 8px 0 4px;text-transform:uppercase;letter-spacing:0.6px;font-weight:600">Plex</div>
       <div class="matrix-grid">${plex.map((d, idx) => deviceCell(d, idx)).join('')}</div>` : ''}
      ${jellyfin.length ? `<div class="small" style="margin: 12px 0 4px;text-transform:uppercase;letter-spacing:0.6px;font-weight:600">Jellyfin</div>
       <div class="matrix-grid">${jellyfin.map((d, idx) => deviceCell(d, plex.length + idx)).join('')}</div>` : ''}
    `;
  } else {
    cellsHtml = `<div class="matrix-grid">${matrix.map((d, idx) => deviceCell(d, idx)).join('')}</div>`;
  }

  window._currentMatrix = matrix;

  return `
    <div class="detail-pane" id="pane-matrix">
      <p class="matrix-explainer">
        Each device shows the <strong>worst severity</strong> across all issues affecting it.
        Click any device to see the specific issues that affect it.
      </p>
      <div style="display:flex;align-items:center;gap:14px;margin-bottom:14px;flex-wrap:wrap">
        <span class="small" style="display:inline-flex;align-items:center;gap:4px"><span class="device-status-icon ok">✓</span> Direct play</span>
        <span class="small" style="display:inline-flex;align-items:center;gap:4px"><span class="device-status-icon transcode">↻</span> Transcode</span>
        <span class="small" style="display:inline-flex;align-items:center;gap:4px"><span class="device-status-icon partial">◐</span> Partial</span>
        <span class="small" style="display:inline-flex;align-items:center;gap:4px"><span class="device-status-icon fail">✕</span> Fail</span>
      </div>
      ${cellsHtml}
      <div id="device-issues-detail" style="margin-top:12px"></div>
    </div>`;
}

function deviceCell(d, idx) {
  const STATUS_ICON = { ok: '✓', transcode: '↻', fail: '✕', partial: '◐' };
  const sev = d.severity || 'ok';
  const cnt = (d.issues || []).length;
  const expandable = cnt > 0;
  return `
    <div class="device-cell ${sev}" id="dev-cell-${idx}"
         ${expandable ? `onclick="toggleDeviceCell(${idx})"` : ''}>
      <span class="device-name">${escHtml(d.device)}</span>
      ${cnt > 0 ? `<span class="device-issue-count">${cnt}</span>` : '<span></span>'}
      <span class="device-status-icon ${d.status}">${STATUS_ICON[d.status] || '?'}</span>
    </div>`;
}

function toggleDeviceCell(idx) {
  const matrix = window._currentMatrix || [];
  const d = matrix[idx];
  if (!d) return;
  const detail = document.getElementById('device-issues-detail');
  if (detail.dataset.openIdx === String(idx)) {
    detail.dataset.openIdx = '';
    detail.innerHTML = '';
    return;
  }
  detail.dataset.openIdx = String(idx);
  const issuesHtml = (d.issues || []).map(i => `
    <div class="device-issue-item">
      <span class="sev-badge ${i.severity}">${escHtml(SEVERITY_LABELS[i.severity] || i.severity)}</span>
      <span style="flex:1">${escHtml(i.message || i.rule_key || '')}</span>
    </div>`).join('');

  detail.innerHTML = `
    <div class="device-issues-panel">
      <div class="device-issues-panel-head">
        ${escHtml(d.device)} — ${(d.issues || []).length} issue(s)
      </div>
      ${issuesHtml || '<div class="small">No issues for this device.</div>'}
    </div>`;
}

function issueCardHtml(iss) {
  const sev = iss.severity || 'info';
  const affected = iss.affected || [];
  return `
    <div class="issue-card ${sev}">
      <div class="issue-card-head">
        <div class="issue-card-title">${escHtml(iss.message || iss.rule_key || 'Issue')}</div>
        <span class="sev-badge ${sev}">${escHtml(SEVERITY_LABELS[sev] || sev)}</span>
      </div>
      <div class="issue-card-detail">${escHtml(iss.detail || '')}</div>
      <div class="issue-card-meta">
        Rule: <code>${escHtml(iss.rule_key || '—')}</code> ·
        Category: ${escHtml(iss.category || '—')} ·
        Affects ${affected.length} device${affected.length === 1 ? '' : 's'}
      </div>
    </div>`;
}

async function vtCheck(id) {
  toast('Checking VirusTotal…', 'ok');
  try {
    const r = await fetch(`/api/files/${id}/virustotal`, { method:'POST' });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    const body = document.getElementById('detail-body');
    let html;
    if (d.not_found) {
      html = `<div class="issue-card info">
        <div class="issue-card-title">🛡 VirusTotal: not found</div>
        <p class="small" style="margin-top: 8px">This file's hash is not in VirusTotal's database.</p>
        <p class="small" style="margin-top: 8px">Hash: <code>${escHtml(d.hash)}</code></p>
      </div>`;
    } else {
      const isBad = d.malicious > 0;
      html = `<div class="issue-card ${isBad ? 'unplayable' : 'info'}">
        <div class="issue-card-title">🛡 VirusTotal: ${isBad ? '⚠️ MALICIOUS' : '✅ Clean'}</div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px">
          <div style="padding:10px;border-radius:6px;text-align:center;background:var(--surface3)"><div style="font-size:22px;font-weight:700">${d.malicious}</div><div class="small">MALICIOUS</div></div>
          <div style="padding:10px;border-radius:6px;text-align:center;background:var(--surface3)"><div style="font-size:22px;font-weight:700">${d.suspicious}</div><div class="small">SUSPICIOUS</div></div>
          <div style="padding:10px;border-radius:6px;text-align:center;background:var(--surface3)"><div style="font-size:22px;font-weight:700">${d.undetected}</div><div class="small">UNDETECTED</div></div>
          <div style="padding:10px;border-radius:6px;text-align:center;background:var(--surface3)"><div style="font-size:22px;font-weight:700">${d.harmless}</div><div class="small">HARMLESS</div></div>
        </div>
        <p style="margin-top:10px"><a href="${escHtml(d.permalink)}" target="_blank">→ Full report on VirusTotal</a></p>
        <p class="small" style="margin-top:6px">Hash: <code>${escHtml(d.hash)}</code></p>
      </div>`;
    }
    body.insertAdjacentHTML('afterbegin', html);
  } catch { toast('VT check failed', 'err'); }
}

// ──────────────────────────────────────────────────────────────
// Custom Rules
// ──────────────────────────────────────────────────────────────
async function loadCustomRules() {
  if (!rulesSchema) await loadRulesSchema();
  try {
    const r = await fetch('/api/rules');
    customRulesCache = await r.json();
    renderCustomRules();
  } catch (e) {}
}

function renderCustomRules() {
  const el = document.getElementById('custom-rules-list');
  if (!customRulesCache.length) {
    el.innerHTML = '<div class="small">No custom rules yet. Click + New Custom Rule to add one.</div>';
    return;
  }
  el.innerHTML = customRulesCache.map(r => {
    const sev = r.severity || 'info';
    const conds = (r.spec?.conditions || []).map(c => {
      const opLabels = { eq:'=', neq:'≠', gt:'>', gte:'≥', lt:'<', lte:'≤',
                         contains:'contains', starts_with:'starts with', ends_with:'ends with',
                         in:'in', is_null:'is null', not_null:'is not null' };
      return `${c.field} ${opLabels[c.op]||c.op}${c.value!==undefined?' '+JSON.stringify(c.value):''}`;
    }).join(`  ${r.spec?.match || 'all'}===any`?' OR ':' AND ');
    const matchOp = (r.spec?.match || 'all').toLowerCase() === 'any' ? ' OR ' : ' AND ';
    const condStr = (r.spec?.conditions || []).map(c => {
      const opLabels = { eq:'=', neq:'≠', gt:'>', gte:'≥', lt:'<', lte:'≤',
                         contains:'⊂', starts_with:'^', ends_with:'$',
                         in:'in', is_null:'∅', not_null:'¬∅' };
      return `${c.field} ${opLabels[c.op]||c.op}${c.value!==undefined?' '+JSON.stringify(c.value):''}`;
    }).join(matchOp);

    return `
      <div class="custom-rule-card ${sev}">
        <div>
          <div class="custom-rule-name">
            ${escHtml(r.name)}
            <span class="sev-badge ${sev}" style="margin-left:8px">${escHtml(SEVERITY_LABELS[sev] || sev)}</span>
          </div>
          ${r.description ? `<div class="custom-rule-desc">${escHtml(r.description)}</div>` : ''}
          <div class="custom-rule-cond">${escHtml(condStr || '(no conditions)')}</div>
        </div>
        <div class="toggle ${r.enabled ? 'on' : ''}" onclick="toggleCustomRule(${r.id}, ${r.enabled ? 0 : 1})"></div>
        <div style="display:flex;gap:4px">
          <button class="icon-btn" onclick="previewRule(${r.id})" title="Preview matches">👁</button>
          <button class="icon-btn" onclick="editRule(${r.id})" title="Edit">✎</button>
          <button class="icon-btn danger" onclick="deleteCustomRule(${r.id})" title="Delete">✕</button>
        </div>
      </div>
    `;
  }).join('');
}

async function toggleCustomRule(id, enabled) {
  await fetch(`/api/rules/${id}`, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled })
  });
  loadCustomRules();
}

async function deleteCustomRule(id) {
  if (!confirm('Delete this rule? Existing matches will be removed on next re-eval.')) return;
  await fetch(`/api/rules/${id}`, { method:'DELETE' });
  toast('Rule deleted', 'ok');
  loadCustomRules();
}

async function previewRule(id) {
  try {
    const r = await fetch(`/api/rules/${id}/preview`);
    const d = await r.json();
    const sample = (d.sample || []).map(s => `<div title="${escHtml(s.path)}">${escHtml(s.path)}</div>`).join('');
    toast(`${d.match_count} files match this rule`, 'ok');
  } catch { toast('Preview failed', 'err'); }
}

async function applyCustomRules() {
  toast('Applying rules...', 'ok');
  try {
    const r = await fetch('/api/rules/apply', { method: 'POST' });
    const d = await r.json();
    toast(`Applied ${d.rules} rule(s) → ${d.matches} matches`, 'ok');
    await loadStats();
    await loadFiles();
  } catch { toast('Apply failed', 'err'); }
}

// ──────────────────────────────────────────────────────────────
// Custom Rule Editor
// ──────────────────────────────────────────────────────────────
function showRuleEditor(ruleId) {
  if (!rulesSchema) {
    toast('Schema still loading, try again', 'err');
    loadRulesSchema();
    return;
  }
  editingRuleId = ruleId || null;
  ruleEditorMode = 'visual';
  document.getElementById('rule-editor-title').textContent = ruleId ? 'Edit Custom Rule' : 'New Custom Rule';

  if (ruleId) {
    const r = customRulesCache.find(x => x.id === ruleId);
    if (!r) return;
    document.getElementById('rule-edit-name').value = r.name || '';
    document.getElementById('rule-edit-description').value = r.description || '';
    document.getElementById('rule-edit-severity').value = r.severity || 'info';
    document.getElementById('rule-edit-message').value = r.message || '';
    document.getElementById('rule-edit-detail').value = r.detail || '';
    editorConditions = (r.spec?.conditions || []).slice();
    editorMatch = (r.spec?.match || 'all');
    if (editorConditions.length === 0) editorConditions = [{ field: 'codec', op: 'eq', value: '' }];
  } else {
    document.getElementById('rule-edit-name').value = '';
    document.getElementById('rule-edit-description').value = '';
    document.getElementById('rule-edit-severity').value = 'info';
    document.getElementById('rule-edit-message').value = '';
    document.getElementById('rule-edit-detail').value = '';
    editorConditions = [{ field: 'codec', op: 'eq', value: '' }];
    editorMatch = 'all';
  }
  document.getElementById('match-all').classList.toggle('active', editorMatch === 'all');
  document.getElementById('match-any').classList.toggle('active', editorMatch === 'any');
  document.getElementById('rule-preview').style.display = 'none';
  switchBuilderMode('visual');
  renderConditionRows();
  openModal('modal-rule-edit');
}

function editRule(id) { showRuleEditor(id); }

function switchBuilderMode(mode) {
  ruleEditorMode = mode;
  document.getElementById('builder-mode-visual').classList.toggle('active', mode === 'visual');
  document.getElementById('builder-mode-json').classList.toggle('active', mode === 'json');
  document.getElementById('builder-visual').style.display = mode === 'visual' ? 'block' : 'none';
  document.getElementById('builder-json').style.display = mode === 'json' ? 'block' : 'none';
  if (mode === 'json') {
    document.getElementById('rule-edit-json').value = JSON.stringify({
      match: editorMatch,
      conditions: editorConditions
    }, null, 2);
  } else {
    // syncs JSON back to visual
    try {
      const j = JSON.parse(document.getElementById('rule-edit-json').value || '{}');
      if (j.match) editorMatch = j.match;
      if (Array.isArray(j.conditions)) editorConditions = j.conditions;
      document.getElementById('match-all').classList.toggle('active', editorMatch === 'all');
      document.getElementById('match-any').classList.toggle('active', editorMatch === 'any');
      renderConditionRows();
    } catch (e) {}
  }
}

function setRuleMatch(m) {
  editorMatch = m;
  document.getElementById('match-all').classList.toggle('active', m === 'all');
  document.getElementById('match-any').classList.toggle('active', m === 'any');
}

function addRuleCondition() {
  editorConditions.push({ field: 'codec', op: 'eq', value: '' });
  renderConditionRows();
}

function removeRuleCondition(idx) {
  editorConditions.splice(idx, 1);
  if (!editorConditions.length) editorConditions.push({ field: 'codec', op: 'eq', value: '' });
  renderConditionRows();
}

function renderConditionRows() {
  if (!rulesSchema) return;
  const fields = rulesSchema.fields || [];
  const opsByType = rulesSchema.ops_by_type || {};
  const html = editorConditions.map((cond, idx) => {
    const fld = fields.find(f => f.key === cond.field) || fields[0];
    const ops = opsByType[fld?.type] || ['eq','neq'];
    const fieldOpts = fields.map(f => `<option value="${f.key}" ${f.key === cond.field ? 'selected' : ''}>${escHtml(f.label)}</option>`).join('');
    const opOpts = ops.map(o => `<option value="${o}" ${o === cond.op ? 'selected' : ''}>${escHtml(opLabel(o))}</option>`).join('');
    let valueInput;
    if (cond.op === 'is_null' || cond.op === 'not_null') {
      valueInput = '<span class="small" style="color:var(--muted);padding:7px 10px">(no value)</span>';
    } else if (fld?.type === 'enum') {
      const optsArr = (fld.options || []).map(o => `<option value="${o}" ${String(cond.value)===String(o)?'selected':''}>${escHtml(o)}</option>`).join('');
      valueInput = `<select class="field-input" onchange="updateCond(${idx},'value',this.value)">${optsArr}</select>`;
    } else {
      const placeholder = fld?.examples?.[0] || '';
      const valStr = cond.value === undefined || cond.value === null ? '' : cond.value;
      valueInput = `<input class="field-input" placeholder="${escHtml(placeholder)}" value="${escHtml(valStr)}"
                     onchange="updateCond(${idx},'value',this.value)" />`;
    }
    return `
      <div class="rule-cond-row">
        <select class="field-input" onchange="updateCond(${idx},'field',this.value);renderConditionRows()">${fieldOpts}</select>
        <select class="field-input" onchange="updateCond(${idx},'op',this.value);renderConditionRows()">${opOpts}</select>
        ${valueInput}
        <button class="icon-btn" onclick="removeRuleCondition(${idx})" title="Remove">✕</button>
      </div>`;
  }).join('');
  document.getElementById('rule-conds').innerHTML = html;
}

function opLabel(op) {
  return ({
    eq:'equals', neq:'not equals', gt:'greater than', gte:'≥', lt:'less than', lte:'≤',
    contains:'contains', starts_with:'starts with', ends_with:'ends with',
    in:'is one of', is_null:'is empty', not_null:'is not empty'
  })[op] || op;
}

function updateCond(idx, key, value) {
  if (!editorConditions[idx]) return;
  if (key === 'value') {
    // Try to coerce numbers
    if (value !== '' && !isNaN(value) && !isNaN(parseFloat(value))) {
      const n = parseFloat(value);
      editorConditions[idx][key] = n;
    } else {
      editorConditions[idx][key] = value;
    }
  } else {
    editorConditions[idx][key] = value;
  }
}

function getCurrentSpec() {
  if (ruleEditorMode === 'json') {
    try {
      return JSON.parse(document.getElementById('rule-edit-json').value || '{}');
    } catch (e) {
      toast('Invalid JSON: ' + e.message, 'err');
      return null;
    }
  }
  // Filter conditions where required value is missing
  const valid = editorConditions.filter(c => {
    if (c.op === 'is_null' || c.op === 'not_null') return true;
    return c.value !== undefined && c.value !== '' && c.value !== null;
  });
  return { match: editorMatch, conditions: valid };
}

async function testRule() {
  const spec = getCurrentSpec();
  if (!spec) return;
  if (!spec.conditions || !spec.conditions.length) {
    toast('Add at least one condition first', 'err');
    return;
  }
  try {
    const r = await fetch('/api/rules/test', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ spec })
    });
    const d = await r.json();
    document.getElementById('rule-preview').style.display = 'block';
    document.getElementById('rule-preview-count').textContent = `${d.match_count} files match`;
    document.getElementById('rule-preview-sample').innerHTML = (d.sample || [])
      .map(s => `<div title="${escHtml(s.path)}">${escHtml(s.path)}</div>`).join('') || '<div class="small">No matches found.</div>';
  } catch (e) { toast('Test failed', 'err'); }
}

async function saveRule() {
  const name = document.getElementById('rule-edit-name').value.trim();
  if (!name) { toast('Rule name required', 'err'); return; }
  const spec = getCurrentSpec();
  if (!spec) return;

  const data = {
    name,
    description: document.getElementById('rule-edit-description').value.trim(),
    severity:    document.getElementById('rule-edit-severity').value,
    spec,
    message:     document.getElementById('rule-edit-message').value.trim() || name,
    detail:      document.getElementById('rule-edit-detail').value.trim(),
    category:    'custom',
    affected_devices: [],
  };

  try {
    if (editingRuleId) {
      await fetch(`/api/rules/${editingRuleId}`, {
        method: 'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(data)
      });
      toast('Rule updated', 'ok');
    } else {
      await fetch('/api/rules', {
        method: 'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(data)
      });
      toast('Rule created', 'ok');
    }
    closeModal('modal-rule-edit');
    loadCustomRules();
  } catch (e) { toast('Save failed', 'err'); }
}

// ──────────────────────────────────────────────────────────────
// Integrations
// ──────────────────────────────────────────────────────────────
function renderIntegrations() {
  const list = document.getElementById('int-list');
  if (!list) return;
  if (!integrationsCache.length) {
    list.innerHTML = '<div class="small">No integrations connected yet.</div>';
    return;
  }
  const baseHost = window.location.origin;
  list.innerHTML = integrationsCache.map(s => {
    const plugin = pluginsCache.find(p => p.kind === s.kind) || {};
    const lastSync = s.last_sync ? new Date(s.last_sync).toLocaleString() : '—';
    const errMsg = s.last_error ? `<div class="int-card-status err">⚠ ${escHtml(s.last_error)}</div>` : '';
    const status = s.last_error ? '' : `<div class="int-card-status ok">last sync: ${escHtml(lastSync)}</div>`;
    const webhookUrl = `${baseHost}/api/integrations/webhook/${s.id}`;
    const webhookHtml = plugin.supports_webhook ? `
      <div style="margin-top:10px">
        <div class="small" style="text-transform:uppercase;letter-spacing:0.5px;font-weight:600;margin-bottom:4px">Webhook URL</div>
        <div class="webhook-box">${escHtml(webhookUrl)}</div>
        <div class="small" style="margin-top:4px">Paste into ${escHtml(plugin.display_name || s.kind)} → Settings → Connect → Webhook · Method <code>POST</code></div>
      </div>` : '';
    return `
      <div class="int-card">
        <div class="int-card-head">
          <div class="int-card-name">${escHtml(s.name)}</div>
          <div class="int-card-kind">${escHtml(s.kind)}</div>
          <div style="display:flex;gap:4px">
            <button class="icon-btn" onclick="testIntegration(${s.id})" title="Test connection">⚡</button>
            ${plugin.supports_sync ? `<button class="icon-btn" onclick="syncIntegration(${s.id})" title="Sync now">↻</button>` : ''}
            <button class="icon-btn" onclick="toggleIntegration(${s.id}, ${s.enabled ? 0 : 1})" title="${s.enabled ? 'Disable' : 'Enable'}">${s.enabled ? '⏸' : '▶'}</button>
            <button class="icon-btn danger" onclick="deleteIntegration(${s.id})" title="Remove">✕</button>
          </div>
        </div>
        <div class="int-card-meta">${escHtml(s.base_url)} · poll ${s.poll_interval}s · ${s.enabled ? 'enabled' : 'disabled'}</div>
        ${status}${errMsg}
        ${webhookHtml}
      </div>`;
  }).join('');
}

async function loadIntegrationEvents() {
  try {
    const r = await fetch('/api/integrations/events');
    const events = await r.json();
    const el = document.getElementById('int-events');
    if (!el) return;
    if (!events.length) {
      el.innerHTML = '<div class="small">No events received yet.</div>';
      return;
    }
    el.innerHTML = events.slice(0, 10).map(e => {
      const paths = JSON.parse(e.file_paths || '[]');
      const ts = e.received_at ? new Date(e.received_at).toLocaleString() : '';
      return `
        <div class="int-card" style="padding: 10px 12px">
          <div style="display:flex;align-items:center;gap:8px">
            <span class="int-card-kind">${escHtml(e.kind || '?')}</span>
            <strong style="font-size:12.5px">${escHtml(e.event_type || '?')}</strong>
            <span class="small" style="margin-left:auto">${escHtml(ts)}</span>
          </div>
          ${paths.length ? `<div class="small" style="margin-top:5px;font-family:var(--mono)">${paths.map(p => escHtml(p)).join('<br>')}</div>` : ''}
        </div>`;
    }).join('');
  } catch (e) {}
}

async function showPluginPicker() {
  // FIX FOR BUG: ensure plugins are loaded BEFORE showing picker
  if (!pluginsCache.length) {
    await loadPluginsAndIntegrations();
  }
  if (!pluginsCache.length) {
    toast('Could not load plugin list', 'err');
    return;
  }
  const grid = document.getElementById('plugin-grid');
  grid.innerHTML = pluginsCache.map(p => {
    const tags = [];
    if (p.supports_sync) tags.push('Sync');
    if (p.supports_webhook) tags.push('Webhook');
    if (p.supports_automation) tags.push('Automation');
    return `
      <div class="plugin-card" onclick="pickPlugin('${escHtml(p.kind)}')">
        <div class="plugin-card-head">
          <div class="plugin-card-name">${escHtml(p.display_name)}</div>
        </div>
        <div class="plugin-card-desc">${escHtml(p.description)}</div>
        <div class="plugin-tags">${tags.map(t => `<span class="plugin-tag">${escHtml(t)}</span>`).join('')}</div>
      </div>
    `;
  }).join('');
  openModal('modal-plugin-picker');
}

function pickPlugin(kind) {
  closeModal('modal-plugin-picker');
  pendingPluginKind = kind;
  pendingPlugin = pluginsCache.find(p => p.kind === kind);
  document.getElementById('add-int-head').innerHTML = `Add ${escHtml(pendingPlugin.display_name)} <button class="icon-btn x" onclick="closeModal('modal-add-int')">✕</button>`;
  document.getElementById('add-int-name').value = pendingPlugin.display_name;
  document.getElementById('add-int-url').value = '';
  document.getElementById('add-int-key').value = '';
  document.getElementById('add-int-poll').value = '900';
  document.getElementById('add-int-poll-wrap').style.display = pendingPlugin.supports_sync ? 'block' : 'none';
  const placeholders = {
    sonarr: 'http://localhost:8989', radarr: 'http://localhost:7878',
    plex: 'http://localhost:32400', jellyfin: 'http://localhost:8096',
    tdarr: 'http://localhost:8265', bazarr: 'http://localhost:6767',
  };
  document.getElementById('add-int-url').placeholder = placeholders[kind] || 'http://...';
  openModal('modal-add-int');
}

async function saveAddInt() {
  const data = {
    kind: pendingPluginKind,
    name: document.getElementById('add-int-name').value.trim(),
    base_url: document.getElementById('add-int-url').value.trim(),
    api_key: document.getElementById('add-int-key').value.trim(),
    poll_interval: parseInt(document.getElementById('add-int-poll').value) || 900,
  };
  if (!data.name || !data.base_url) {
    toast('Name and URL required', 'err');
    return;
  }
  try {
    const r = await fetch('/api/integrations', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data)
    });
    const d = await r.json();
    if (d.id) {
      closeModal('modal-add-int');
      toast(`${data.name} added`, 'ok');
      setTimeout(() => testIntegration(d.id), 200);
      await loadPluginsAndIntegrations();
    } else {
      toast(d.error || 'Failed', 'err');
    }
  } catch (e) { toast('Failed to add', 'err'); }
}

async function testIntegration(id) {
  toast('Testing connection…', 'ok');
  try {
    const r = await fetch(`/api/integrations/${id}/test`, { method: 'POST' });
    const d = await r.json();
    toast(d.message || (d.ok ? 'OK' : 'Failed'), d.ok ? 'ok' : 'err');
    await loadPluginsAndIntegrations();
  } catch { toast('Test failed', 'err'); }
}
async function syncIntegration(id) {
  toast('Sync started in background…', 'ok');
  await fetch(`/api/integrations/${id}/sync`, { method: 'POST' });
  setTimeout(loadPluginsAndIntegrations, 2500);
}
async function toggleIntegration(id, enabled) {
  await fetch(`/api/integrations/${id}`, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled })
  });
  await loadPluginsAndIntegrations();
}
async function deleteIntegration(id) {
  if (!confirm('Remove this integration?')) return;
  await fetch(`/api/integrations/${id}`, { method: 'DELETE' });
  toast('Removed', 'ok');
  await loadPluginsAndIntegrations();
}

// ──────────────────────────────────────────────────────────────
// Automation
// ──────────────────────────────────────────────────────────────
async function loadAutomationRules() {
  if (!integrationsCache.length) await loadPluginsAndIntegrations();

  try {
    const r = await fetch('/api/automation/rules');
    const rules = await r.json();
    const el = document.getElementById('rules-list');
    if (!rules.length) {
      el.innerHTML = '<div class="small">No rules yet. Add one to automatically toggle monitoring in Sonarr/Radarr based on file severity.</div>';
      return;
    }
    el.innerHTML = rules.map(r => {
      const intg = integrationsCache.find(i => i.id === r.integration_id);
      const intName = intg ? intg.name : `(deleted #${r.integration_id})`;
      const sevLabel = SEVERITY_LABELS[r.when_severity] || r.when_severity;
      const cmpLabel = { at_least: 'at least', at_most: 'at most', equals: 'equals' }[r.comparison] || r.comparison;
      const actionLabel = r.action === 'monitor' ? 'monitor' : 'unmonitor';
      const lastRun = r.last_run ? new Date(r.last_run).toLocaleString() : 'never';
      return `
        <div class="rule-card">
          <div>
            <div class="rule-name">${escHtml(r.name)}</div>
            <div class="rule-desc">→ ${escHtml(intName)} · when severity ${cmpLabel} <strong>${escHtml(sevLabel)}</strong> → <strong>${actionLabel}</strong></div>
            <div class="small" style="margin-top: 4px">Last run: ${escHtml(lastRun)}</div>
          </div>
          <div class="toggle ${r.enabled ? 'on' : ''}" onclick="toggleRule(${r.id}, ${r.enabled ? 0 : 1})"></div>
          <button class="icon-btn danger" onclick="deleteRule(${r.id})" title="Delete">✕</button>
        </div>`;
    }).join('');
  } catch (e) {}
}

async function showAddRuleDialog() {
  if (!integrationsCache.length) await loadPluginsAndIntegrations();
  if (!integrationsCache.length) {
    toast('Add a Sonarr or Radarr integration first', 'err');
    return;
  }
  const usable = integrationsCache.filter(i => {
    const p = pluginsCache.find(p => p.kind === i.kind);
    return p && p.supports_automation;
  });
  if (!usable.length) {
    toast('No Sonarr/Radarr integration available', 'err');
    return;
  }
  const select = document.getElementById('rule-int');
  select.innerHTML = usable.map(i => `<option value="${i.id}">${escHtml(i.name)} (${escHtml(i.kind)})</option>`).join('');
  document.getElementById('rule-name').value = '';
  document.getElementById('rule-cmp').value = 'at_least';
  document.getElementById('rule-sev').value = 'unplayable';
  document.getElementById('rule-action').value = 'unmonitor';
  openModal('modal-add-rule');
}

async function saveAddRule() {
  const data = {
    integration_id: parseInt(document.getElementById('rule-int').value),
    name: document.getElementById('rule-name').value.trim(),
    when_severity: document.getElementById('rule-sev').value,
    comparison: document.getElementById('rule-cmp').value,
    action: document.getElementById('rule-action').value,
  };
  if (!data.name) { toast('Name required', 'err'); return; }
  try {
    const r = await fetch('/api/automation/rules', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data)
    });
    const d = await r.json();
    if (d.id) {
      closeModal('modal-add-rule');
      toast('Rule added', 'ok');
      loadAutomationRules();
    } else { toast(d.error || 'Failed', 'err'); }
  } catch { toast('Failed', 'err'); }
}

async function toggleRule(id, enabled) {
  await fetch(`/api/automation/rules/${id}`, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled })
  });
  loadAutomationRules();
}
async function deleteRule(id) {
  if (!confirm('Delete this rule?')) return;
  await fetch(`/api/automation/rules/${id}`, { method:'DELETE' });
  loadAutomationRules();
}
async function runAutomation() {
  toast('Running automation rules…', 'ok');
  try {
    const r = await fetch('/api/automation/run', { method:'POST' });
    const d = await r.json();
    toast(`Applied ${d.actions_run || 0} action(s)`, 'ok');
    loadAutomationRules();
    loadFiles();
  } catch { toast('Failed', 'err'); }
}

// ──────────────────────────────────────────────────────────────
// Config
// ──────────────────────────────────────────────────────────────
async function loadConfig() {
  const r = await fetch('/api/config');
  cfg = await r.json();
  renderConfig();
}

function renderConfig() {
  document.getElementById('workers-range').value = cfg.workers || 4;
  document.getElementById('workers-val').textContent = cfg.workers || 4;
  document.getElementById('offset-range').value = cfg.sample_offset_seconds || 60;
  document.getElementById('offset-val').textContent = cfg.sample_offset_seconds || 60;
  document.getElementById('bitrate-range').value = cfg.bitrate_threshold_mbps || 80;
  document.getElementById('bitrate-val').textContent = cfg.bitrate_threshold_mbps || 80;
  document.getElementById('vt-key').value = cfg.virustotal_api_key || '';

  document.getElementById('schedule-toggle').classList.toggle('on', !!cfg.schedule_enabled);
  document.getElementById('schedule-time-wrap').style.display = cfg.schedule_enabled ? 'block' : 'none';
  document.getElementById('schedule-time').value = cfg.schedule_time || '02:00';

  document.getElementById('prune-toggle').classList.toggle('on', cfg.prune_missing !== false);

  // Compatibility mode segmented selector
  const mode = cfg.compatibility_mode || 'plex';
  document.querySelectorAll('.seg-row button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));

  renderTags('paths-tags', cfg.library_paths || [], 'library_paths');
  renderTags('ignore-tags', cfg.ignore_patterns || [], 'ignore_patterns');
  renderTags('media-ext-tags',    cfg.media_extensions || [],    'media_extensions');
  renderTags('subtitle-ext-tags', cfg.subtitle_extensions || [], 'subtitle_extensions');
  renderTags('image-ext-tags',    cfg.image_extensions || [],    'image_extensions');
  renderTags('metadata-ext-tags', cfg.metadata_extensions || [], 'metadata_extensions');
}

function renderTags(containerId, items, key) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = items.map(item => `
    <div class="tag">
      <span>${escHtml(item)}</span>
      <button class="tag-x" onclick="removeTag('${escHtml(key)}','${escHtml(item).replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'")}')">×</button>
    </div>
  `).join('');
}

function removeTag(key, val) {
  cfg[key] = (cfg[key] || []).filter(x => x !== val);
  renderConfig();
}

function addPath() {
  const v = document.getElementById('new-path').value.trim();
  if (!v) return;
  cfg.library_paths = [...(cfg.library_paths||[]), v];
  document.getElementById('new-path').value = '';
  renderConfig();
}
function addIgnore() {
  const v = document.getElementById('new-ignore').value.trim();
  if (!v) return;
  cfg.ignore_patterns = [...(cfg.ignore_patterns||[]), v];
  document.getElementById('new-ignore').value = '';
  renderConfig();
}
function addExt(key, inputId) {
  let v = document.getElementById(inputId).value.trim();
  if (!v) return;
  if (!v.startsWith('.')) v = '.' + v;
  v = v.toLowerCase();
  cfg[key] = [...(cfg[key]||[]), v];
  document.getElementById(inputId).value = '';
  renderConfig();
}

function togglePrune() {
  cfg.prune_missing = !cfg.prune_missing;
  document.getElementById('prune-toggle').classList.toggle('on', cfg.prune_missing);
}

function toggleSchedule() {
  cfg.schedule_enabled = !cfg.schedule_enabled;
  document.getElementById('schedule-toggle').classList.toggle('on', cfg.schedule_enabled);
  document.getElementById('schedule-time-wrap').style.display = cfg.schedule_enabled ? 'block' : 'none';
}

async function setCompatMode(mode) {
  cfg.compatibility_mode = mode;
  document.querySelectorAll('.seg-row button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  // Persist immediately so file detail reflects right away
  try {
    await fetch('/api/config', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(cfg)
    });
    toast(`Compatibility: ${mode}`, 'ok');
  } catch { toast('Save failed', 'err'); }
}

async function saveConfig() {
  cfg.workers = parseInt(document.getElementById('workers-range').value);
  cfg.sample_offset_seconds = parseInt(document.getElementById('offset-range').value);
  cfg.bitrate_threshold_mbps = parseInt(document.getElementById('bitrate-range').value);
  cfg.virustotal_api_key = document.getElementById('vt-key').value;
  cfg.schedule_time = document.getElementById('schedule-time').value;

  try {
    const r = await fetch('/api/config', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(cfg)
    });
    if (r.ok) toast('Configuration saved', 'ok');
    else toast('Save failed', 'err');
  } catch { toast('Save failed', 'err'); }
}
