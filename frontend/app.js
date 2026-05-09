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
  await loadAuthInfo();
  await loadConfig();
  // Pre-load plugins schema (avoids picker race)
  loadPluginsAndIntegrations();
  loadRulesSchema();
  await loadStats();
  await loadFiles();
  loadIntegrationEvents();
  loadUpdateStatus();
  // Refresh update banner every 5 minutes
  setInterval(loadUpdateStatus, 5 * 60 * 1000);
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
// Wrap fetch to redirect to login on 401
const _origFetch = window.fetch.bind(window);
window.fetch = async function(input, init) {
  const r = await _origFetch(input, init);
  if (r.status === 401) {
    const url = (typeof input === 'string') ? input : (input.url || '');
    // Don't loop on the login endpoint itself
    if (!url.includes('/api/auth/')) {
      window.location.href = '/login.html';
    }
  }
  return r;
};

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
    rules: 'Rules',
    automation: 'Automation',
    config: 'Settings',
    help: 'Help',
  };
  document.getElementById('top-title').textContent = titles[name] || name;
  document.getElementById('top-sub').textContent = '';
  document.getElementById('search-input').style.display = (name === 'files') ? 'block' : 'none';

  if (name === 'integrations') { loadPluginsAndIntegrations(); loadIntegrationEvents(); }
  if (name === 'automation') { loadAutomationRules(); }
  if (name === 'rules') { loadAllRules(); }
  if (name === 'config') { loadAuthInfo(); loadUpdateStatus(); loadDbStats(); }
  if (name === 'help') { loadHelpDocs(); }
}

// ──────────────────────────────────────────────────────────────
// Scans
// ──────────────────────────────────────────────────────────────
async function startScan() {
  if (!confirm("Run a Full Scan?\n\nThis walks every file in your library_paths and runs ffprobe on each media file. This can take minutes to hours depending on library size.\n\nUse 'Re-eval Rules' instead if you only changed rules/thresholds.")) return;
  await _kickoff('/api/scan/start', 'btn-scan', '⏳ Scanning');
}

async function startReeval() {
  // Re-eval is fast — uses cached probe data, never re-reads media files
  await _kickoff('/api/scan/reeval', 'btn-reeval', '⚡ Re-evaluating');
}

async function _kickoff(url, btnId, label) {
  const btn = document.getElementById(btnId);
  document.getElementById('btn-scan').disabled = true;
  document.getElementById('btn-reeval').disabled = true;
  if (btn) btn.innerHTML = label;
  document.getElementById('progress-wrap').style.display = 'block';
  try {
    const r = await fetch(url, { method: 'POST' });
    const d = await r.json();
    if (d.error) {
      toast(d.error, 'err');
      resetScanBtn();
      return;
    }
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
      toast(wasReeval
        ? `Re-evaluated ${s.total} files (no disk read, no ffprobe)`
        : `Scan complete — ${s.total} files`, 'ok');
    } else if (s.status === 'failed' || s.status === 'error') {
      clearInterval(pollInterval); pollInterval = null;
      activeJobId = null;
      resetScanBtn();
      toast(`Scan failed: ${s.error || 'unknown error'}`, 'err');
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
// Dashboard v2 — Media-centric, side panels, all clickable
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

  renderHero(s);
  renderSeverityTiles(s);
  renderClickableBars('codec-bars', s.codecs || {}, 'codec');
  renderClickableBars('audio-bars', s.audio_codecs || {}, 'audio_codec');
  renderClickableBars('res-bars', s.resolutions || {}, 'resolution');
  renderClickableBars('issue-cat-bars', s.issue_categories || {}, 'category');
  document.getElementById('codec-total').textContent = sumValues(s.codecs);
  document.getElementById('audio-total').textContent = sumValues(s.audio_codecs);
  document.getElementById('res-total').textContent = sumValues(s.resolutions);
  document.getElementById('issue-cat-total').textContent = Object.values(s.issue_categories || {}).reduce((a,b)=>a+b,0) + ' files';

  // Highlights
  document.getElementById('hl-dovi-p5').textContent = s.dovi_p5 || 0;
  document.getElementById('hl-av1').textContent = s.av1 || 0;
  document.getElementById('hl-hevc').textContent = s.hevc || 0;
  document.getElementById('hl-dovi-other').textContent = s.dovi_other || 0;

  renderSideCategories(s);
}

function sumValues(obj) {
  const sum = Object.values(obj || {}).reduce((a,b)=>a+b,0);
  return `${sum} files`;
}

// Hero panel always shows MEDIA stats by default
function renderHero(s) {
  const pc = (s.per_category && s.per_category.media) || { severity: {}, total: 0, size_gb: 0 };
  const sev = pc.severity || {};
  const total = pc.total || 0;

  const okish = (sev.ok || 0) + (sev.info || 0);
  const score = total > 0 ? Math.round(okish / total * 100) : 0;
  const scoreEl = document.getElementById('hero-score');
  scoreEl.textContent = total > 0 ? score : '—';
  scoreEl.style.color = total === 0 ? 'var(--muted)' :
                        score >= 90 ? 'var(--sev-ok)' :
                        score >= 70 ? 'var(--sev-possible-transcode)' :
                        score >= 40 ? 'var(--sev-always-transcode)' :
                                      'var(--sev-unplayable)';

  let statusText, detailText;
  if (total === 0) {
    statusText = 'NO MEDIA'; detailText = 'No media files in this library yet.';
  } else if (score >= 90) {
    statusText = 'EXCELLENT'; detailText = `${total} media files; almost all clean.`;
  } else if (score >= 70) {
    statusText = 'GOOD'; detailText = `${total} media files; some clients may transcode.`;
  } else if (score >= 40) {
    statusText = 'NEEDS ATTENTION'; detailText = `${total} media files; many will transcode.`;
  } else {
    statusText = 'POOR'; detailText = `${total} media files; significant playback issues.`;
  }

  document.getElementById('hero-cat-name').textContent = 'Media health';
  document.getElementById('hero-status').textContent = statusText;
  document.getElementById('hero-detail').textContent = detailText;
  document.getElementById('hero-quick-total').textContent = total.toLocaleString();
  document.getElementById('hero-quick-size').textContent = (pc.size_gb || 0).toFixed(1) + ' GB';
}

function renderSeverityTiles(s) {
  const pc = (s.per_category && s.per_category.media) || { severity: {} };
  const sev = pc.severity || {};
  const order = ['unplayable','always_transcode','possible_transcode','high_bitrate','info','ok'];
  const html = order.map(svKey => {
    const n = sev[svKey] || 0;
    const cls = n === 0 ? `sev-tile ${svKey} zero` : `sev-tile ${svKey}`;
    const label = SEVERITY_LABELS[svKey] || svKey;
    return `
      <div class="${cls}" onclick="goToFiles({file_category:'media',severity:'${svKey}'})">
        <div class="sev-tile-val">${n}</div>
        <div class="sev-tile-label">${escHtml(label)}</div>
      </div>`;
  }).join('');
  document.getElementById('sev-tiles').innerHTML = html;
}

function renderClickableBars(id, data, filterKey) {
  const el = document.getElementById(id);
  if (!el) return;
  const entries = Object.entries(data || {}).sort((a,b) => b[1]-a[1]).slice(0, 8);
  if (!entries.length) {
    el.innerHTML = '<div class="small" style="padding:6px 8px">No data</div>';
    return;
  }
  const max = entries[0][1];
  el.innerHTML = entries.map(([k,v]) => {
    // For category filter, we go to that category in the file browser instead
    const click = filterKey === 'category'
      ? `goToFiles({category:'${escHtml(k)}'})`
      : `goToFiles({file_category:'media',${filterKey}:'${escHtml(k)}'})`;
    return `
      <div class="cbar-row" onclick="${click}" title="Click to filter">
        <div class="cbar-label">${escHtml(k)}</div>
        <div class="cbar-track"><div class="cbar-fill" style="width:${(v/max)*100}%"></div></div>
        <div class="cbar-count">${v}</div>
      </div>`;
  }).join('');
}

function renderSideCategories(s) {
  const order = ['subtitle','image','metadata','junk'];
  const html = order
    .filter(cat => (s.per_category?.[cat]?.total || 0) > 0)
    .map(cat => {
      const pc = s.per_category[cat] || { severity: {}, total: 0 };
      const sev = pc.severity || {};
      // Highest severity drives the card colour
      const SEV_ORDER = ['unplayable','always_transcode','possible_transcode','high_bitrate','info','ok'];
      const worst = SEV_ORDER.find(s => (sev[s] || 0) > 0) || 'ok';
      // Severity pills (only non-zero counts)
      const pills = SEV_ORDER
        .filter(svKey => (sev[svKey] || 0) > 0)
        .map(svKey => `<span class="cat-side-sev-pill ${svKey}"
                            title="${escHtml(SEVERITY_LABELS[svKey])} — click to filter"
                            onclick="event.stopPropagation();goToFiles({file_category:'${cat}',severity:'${svKey}'})">${sev[svKey]} ${SEVERITY_LABELS[svKey]}</span>`)
        .join('');
      return `
        <div class="cat-side-card ${worst}" onclick="goToFiles({file_category:'${cat}'})">
          <div class="cat-side-head">
            <div class="cat-side-icon">${CATEGORY_ICONS[cat] || '·'}</div>
            <div class="cat-side-name">${escHtml(CATEGORY_LABELS[cat] || cat)}</div>
            <div class="cat-side-count">${pc.total}</div>
          </div>
          <div class="cat-side-sev-row">${pills}</div>
        </div>`;
    }).join('');
  document.getElementById('side-cat-list').innerHTML = html ||
    '<div class="small" style="padding:6px 4px">No other category files.</div>';
}

// ──────────────────────────────────────────────────────────────
// Click-to-filter helper — single entry point for ALL dashboard clicks
// ──────────────────────────────────────────────────────────────
function goToFiles({ file_category, category, severity, codec, audio_codec, resolution, search } = {}) {
  showView('files');
  if (file_category) currentFileCategory = file_category;
  if (severity) currentSeverity = severity;
  else currentSeverity = 'all';

  // Severity filter chip
  document.querySelectorAll('#filter-bar .chip').forEach(c =>
    c.classList.toggle('active', c.dataset.sev === currentSeverity));

  // Search/criteria — populate the search box for visibility
  let q = '';
  if (search) q = search;
  else if (codec) q = codec;
  else if (audio_codec) q = audio_codec;
  else if (resolution) q = resolution;
  else if (category && !file_category) q = category;
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
// Rules — built-in, custom, dropped, all in one tabbed view
// ──────────────────────────────────────────────────────────────
let _rulesTab = 'builtin';
let _builtinRulesCache = [];
let _droppedRulesCache = [];

async function loadAllRules() {
  if (!rulesSchema) await loadRulesSchema();
  try {
    const [bc, cc, dc] = await Promise.all([
      fetch('/api/rules/builtin').then(r => r.json()),
      fetch('/api/rules').then(r => r.json()),
      fetch('/api/rules/dropped').then(r => r.json()),
    ]);
    _builtinRulesCache = bc;
    customRulesCache = cc;
    _droppedRulesCache = dc;
    document.getElementById('rules-tab-builtin-count').textContent = bc.filter(r => !r.dropped).length;
    document.getElementById('rules-tab-custom-count').textContent = cc.length;
    document.getElementById('rules-tab-dropped-count').textContent = dc.length;
    renderBuiltinRules();
    renderCustomRules();
    renderDroppedRules();
  } catch (e) {
    toast('Failed to load rules', 'err');
  }
}

function setRulesTab(tab) {
  _rulesTab = tab;
  document.querySelectorAll('.rules-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === tab));
  for (const t of ['builtin','custom','dropped']) {
    const el = document.getElementById('rules-pane-' + t);
    if (el) el.style.display = (t === tab) ? 'block' : 'none';
  }
}

function renderBuiltinRules() {
  const el = document.getElementById('builtin-rules-list');
  if (!el) return;
  const visible = _builtinRulesCache.filter(r => !r.dropped);
  if (!visible.length) {
    el.innerHTML = '<div class="small">No built-in rules registered (something is very wrong).</div>';
    return;
  }
  // Group by category
  const groups = {};
  for (const r of visible) {
    const cat = r.category || 'other';
    (groups[cat] = groups[cat] || []).push(r);
  }
  const order = ['video','audio','hdr','container','subtitles','performance','non_media','other'];
  let html = '';
  for (const cat of order) {
    if (!groups[cat]) continue;
    html += `<div class="rule-section-head">${escHtml(cat)}</div>`;
    html += groups[cat].map(r => {
      const sev = r.severity_override || r.severity_default;
      return `
        <div class="custom-rule-card ${sev}">
          <div>
            <div class="custom-rule-name">
              ${escHtml(r.name)}
              <span class="builtin-pill">built-in</span>
              <span class="sev-badge ${sev}" style="margin-left:6px">${escHtml(SEVERITY_LABELS[sev] || sev)}</span>
              ${r.severity_override ? `<span class="small" style="margin-left:6px;color:var(--muted)">(was ${escHtml(SEVERITY_LABELS[r.severity_default])})</span>` : ''}
            </div>
            <div class="custom-rule-desc">${escHtml(r.description)}</div>
            <div class="custom-rule-cond">${escHtml(r.rule_key)}</div>
          </div>
          <div class="toggle ${r.enabled ? 'on' : ''}" onclick="toggleBuiltinRule('${escHtml(r.rule_key)}', ${r.enabled ? 0 : 1})" title="Enable / disable"></div>
          <div style="display:flex;gap:4px">
            <button class="icon-btn" onclick="overrideBuiltinSeverity('${escHtml(r.rule_key)}')" title="Override severity">⊕</button>
            <button class="icon-btn danger" onclick="dropBuiltinRule('${escHtml(r.rule_key)}')" title="Drop (move to Disabled tab)">⊘</button>
          </div>
        </div>`;
    }).join('');
  }
  el.innerHTML = html;
}

async function toggleBuiltinRule(ruleKey, enabled) {
  try {
    await fetch(`/api/rules/builtin/${ruleKey}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled: !!enabled })
    });
    toast(`${enabled ? 'Enabled' : 'Disabled'} ${ruleKey} (run Re-eval to apply)`, 'ok');
    loadAllRules();
  } catch { toast('Failed', 'err'); }
}

async function dropBuiltinRule(ruleKey) {
  if (!confirm(`Drop "${ruleKey}"? It will move to the Disabled / Discarded tab and stop being evaluated. You can restore it from there later.`)) return;
  try {
    await fetch(`/api/rules/builtin/${ruleKey}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled: false, dropped: true })
    });
    toast(`Dropped ${ruleKey}`, 'ok');
    loadAllRules();
  } catch { toast('Failed', 'err'); }
}

async function overrideBuiltinSeverity(ruleKey) {
  const sev = prompt(`Override severity for "${ruleKey}".\nValid: ok, info, high_bitrate, possible_transcode, always_transcode, unplayable.\nLeave blank to clear override.`);
  if (sev === null) return;
  try {
    await fetch(`/api/rules/builtin/${ruleKey}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ severity_override: sev || null })
    });
    toast(`Severity for ${ruleKey}: ${sev || '(default restored)'}`, 'ok');
    loadAllRules();
  } catch { toast('Failed', 'err'); }
}

function renderCustomRules() {
  const el = document.getElementById('custom-rules-list');
  if (!el) return;
  if (!customRulesCache.length) {
    el.innerHTML = '<div class="small">No custom rules yet. Click + New Custom Rule to add one.</div>';
    return;
  }
  el.innerHTML = customRulesCache.map(r => {
    const sev = r.severity || 'info';
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
            <span class="custom-pill">custom</span>
            <span class="sev-badge ${sev}" style="margin-left:6px">${escHtml(SEVERITY_LABELS[sev] || sev)}</span>
          </div>
          ${r.description ? `<div class="custom-rule-desc">${escHtml(r.description)}</div>` : ''}
          <div class="custom-rule-cond">${escHtml(condStr || '(no conditions)')}</div>
        </div>
        <div class="toggle ${r.enabled ? 'on' : ''}" onclick="toggleCustomRule(${r.id}, ${r.enabled ? 0 : 1})"></div>
        <div style="display:flex;gap:4px">
          <button class="icon-btn" onclick="previewRule(${r.id})" title="Preview matches">👁</button>
          <button class="icon-btn" onclick="editRule(${r.id})" title="Edit">✎</button>
          <button class="icon-btn danger" onclick="dropCustomRule(${r.id})" title="Drop">⊘</button>
        </div>
      </div>
    `;
  }).join('');
}

function renderDroppedRules() {
  const el = document.getElementById('dropped-rules-list');
  if (!el) return;
  if (!_droppedRulesCache.length) {
    el.innerHTML = '<div class="small">No dropped rules.</div>';
    return;
  }
  el.innerHTML = _droppedRulesCache.map(r => {
    const isBuiltin = (r.rule_kind === 'builtin');
    const ruleKey = isBuiltin ? (r.spec?.rule_key || r.message || r.name) : null;
    const sev = r.severity || 'info';
    return `
      <div class="custom-rule-card ${sev}">
        <div>
          <div class="custom-rule-name">
            ${escHtml(r.name)}
            <span class="${isBuiltin ? 'builtin-pill' : 'custom-pill'}">${isBuiltin ? 'built-in' : 'custom'}</span>
            <span class="dropped-pill">dropped</span>
          </div>
          ${r.description ? `<div class="custom-rule-desc">${escHtml(r.description)}</div>` : ''}
        </div>
        <div></div>
        <div style="display:flex;gap:4px">
          <button class="icon-btn" onclick="${isBuiltin ? `restoreBuiltinRule('${escHtml(ruleKey)}')` : `restoreCustomRule(${r.id})`}" title="Restore">↩</button>
          ${isBuiltin ? '' : `<button class="icon-btn danger" onclick="permanentDeleteCustomRule(${r.id})" title="Delete permanently">✕</button>`}
        </div>
      </div>
    `;
  }).join('');
}

async function dropCustomRule(id) {
  if (!confirm('Drop this rule? It moves to Disabled/Discarded; restore from there.')) return;
  await fetch(`/api/rules/${id}`, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled: 0, dropped: 1 })
  });
  toast('Rule dropped', 'ok');
  loadAllRules();
}
async function restoreCustomRule(id) {
  await fetch(`/api/rules/${id}`, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled: 1, dropped: 0 })
  });
  toast('Rule restored', 'ok');
  loadAllRules();
}
async function permanentDeleteCustomRule(id) {
  if (!confirm('Delete this rule permanently? This cannot be undone.')) return;
  await fetch(`/api/rules/${id}`, { method: 'DELETE' });
  toast('Rule deleted', 'ok');
  loadAllRules();
}
async function restoreBuiltinRule(ruleKey) {
  await fetch(`/api/rules/builtin/${ruleKey}`, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled: 1, dropped: 0 })
  });
  toast(`Restored ${ruleKey}`, 'ok');
  loadAllRules();
}

async function toggleCustomRule(id, enabled) {
  await fetch(`/api/rules/${id}`, {
    method:'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ enabled })
  });
  loadAllRules();
}

async function deleteCustomRule(id) {
  if (!confirm('Delete this rule? Existing matches will be removed on next re-eval.')) return;
  await fetch(`/api/rules/${id}`, { method:'DELETE' });
  toast('Rule deleted', 'ok');
  loadAllRules();
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
    loadAllRules();
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
      const actionLabels = {
        monitor:'monitor (*arr)', unmonitor:'unmonitor (*arr)',
        transcode_via_tdarr:'queue transcode (Tdarr)',
        search_subs_via_bazarr:'search subs (Bazarr)',
        delete_sub_via_bazarr:'delete sub (Bazarr)',
      };
      const actionLabel = actionLabels[r.action] || r.action;
      const lastRun = r.last_run ? new Date(r.last_run).toLocaleString() : 'never';
      const sevMatch = r.severity_match || 'highest';
      const fileCat = r.file_category ? ` · category: <strong>${escHtml(r.file_category)}</strong>` : '';
      const runs = r.runs_count ? ` · ${r.runs_count} run(s); last applied to ${r.last_action_count || 0} file(s)` : '';
      return `
        <div class="rule-card">
          <div>
            <div class="rule-name">${escHtml(r.name)}</div>
            <div class="rule-desc">→ ${escHtml(intName)} · when ${escHtml(sevMatch)} severity ${cmpLabel} <strong>${escHtml(sevLabel)}</strong> → <strong>${escHtml(actionLabel)}</strong>${fileCat}</div>
            <div class="small" style="margin-top: 4px">Last run: ${escHtml(lastRun)}${runs}</div>
          </div>
          <div class="toggle ${r.enabled ? 'on' : ''}" onclick="toggleRule(${r.id}, ${r.enabled ? 0 : 1})"></div>
          <button class="icon-btn danger" onclick="deleteRule(${r.id})" title="Delete">✕</button>
        </div>`;
    }).join('');
  } catch (e) {}
}

// State for the rule editor
let _tdarrLibrariesCache = {};
let _tdarrPluginsCache = {};
let _tdarrMode = 'library';

const ACTIONS_BY_KIND = {
  sonarr:   [{value:'unmonitor', label:'Unmonitor in Sonarr'},
             {value:'monitor',   label:'Monitor in Sonarr'}],
  radarr:   [{value:'unmonitor', label:'Unmonitor in Radarr'},
             {value:'monitor',   label:'Monitor in Radarr'}],
  bazarr:   [{value:'search_subs_via_bazarr', label:'Re-search subtitles (Bazarr)'},
             {value:'delete_sub_via_bazarr', label:'Delete subtitle file (Bazarr)'}],
  tdarr:    [{value:'transcode_via_tdarr', label:'Queue transcode in Tdarr'}],
};

async function showAddRuleDialog() {
  if (!integrationsCache.length) await loadPluginsAndIntegrations();
  if (!integrationsCache.length) {
    toast('Add an integration first', 'err');
    return;
  }
  const usable = integrationsCache.filter(i => {
    const p = pluginsCache.find(p => p.kind === i.kind);
    return p && p.supports_automation;
  });
  if (!usable.length) {
    toast('No automation-capable integration connected', 'err');
    return;
  }
  const select = document.getElementById('rule-int');
  select.innerHTML = usable.map(i => `<option value="${i.id}" data-kind="${i.kind}">${escHtml(i.name)} (${escHtml(i.kind)})</option>`).join('');
  document.getElementById('rule-name').value = '';
  document.getElementById('rule-cmp').value = 'at_least';
  document.getElementById('rule-sev').value = 'unplayable';
  const fileCatEl = document.getElementById('rule-file-cat');
  if (fileCatEl) fileCatEl.value = '';
  const bazarrMtEl = document.getElementById('rule-bazarr-media-type');
  if (bazarrMtEl) bazarrMtEl.value = '';
  const sevMatchEl = document.getElementById('rule-sev-match');
  if (sevMatchEl) sevMatchEl.value = 'highest';
  onRuleIntegrationChange();  // populates action select
  openModal('modal-add-rule');
}

function onRuleIntegrationChange() {
  const sel = document.getElementById('rule-int');
  const opt = sel.options[sel.selectedIndex];
  const kind = opt?.dataset.kind || '';
  const actions = ACTIONS_BY_KIND[kind] || [];
  const actionSel = document.getElementById('rule-action');
  actionSel.innerHTML = actions.map(a => `<option value="${a.value}">${escHtml(a.label)}</option>`).join('');
  // Auto-select sensible default file category
  if (kind === 'tdarr') document.getElementById('rule-file-cat').value = 'media';
  if (kind === 'bazarr') document.getElementById('rule-file-cat').value = '';
  onRuleActionChange();
}

async function onRuleActionChange() {
  const sel = document.getElementById('rule-action');
  const action = sel.value;
  const tdarrCfg = document.getElementById('rule-tdarr-config');
  const bazarrCfg = document.getElementById('rule-bazarr-config');
  tdarrCfg.style.display = (action === 'transcode_via_tdarr') ? 'block' : 'none';
  bazarrCfg.style.display = (action === 'search_subs_via_bazarr' || action === 'delete_sub_via_bazarr') ? 'block' : 'none';

  if (action === 'transcode_via_tdarr') {
    setTdarrMode('library');
    await populateTdarrLibrariesAndPlugins();
  }
}

async function populateTdarrLibrariesAndPlugins() {
  const sel = document.getElementById('rule-int');
  const sid = sel.value;
  if (!sid) return;
  // Cache to avoid repeated fetches
  if (!_tdarrLibrariesCache[sid]) {
    try {
      const [lr, pr] = await Promise.all([
        fetch(`/api/tdarr/${sid}/libraries`),
        fetch(`/api/tdarr/${sid}/plugins`),
      ]);
      _tdarrLibrariesCache[sid] = await lr.json();
      _tdarrPluginsCache[sid] = await pr.json();
    } catch (e) {
      _tdarrLibrariesCache[sid] = []; _tdarrPluginsCache[sid] = [];
    }
  }
  const libs = _tdarrLibrariesCache[sid] || [];
  const plugins = _tdarrPluginsCache[sid] || [];
  document.getElementById('rule-tdarr-library').innerHTML =
    '<option value="">— pick a library —</option>' +
    libs.map(l => `<option value="${escHtml(l.id)}">${escHtml(l.name)}${l.folder ? ' — ' + escHtml(l.folder) : ''}</option>`).join('');
  document.getElementById('rule-tdarr-plugin').innerHTML =
    '<option value="">— pick a plugin / flow —</option>' +
    plugins.map(p => `<option value="${escHtml(p.id)}">${escHtml(p.name)}${p.type ? ' [' + escHtml(p.type) + ']' : ''}</option>`).join('');
}

function setTdarrMode(mode) {
  _tdarrMode = mode;
  ['library','plugin','inline'].forEach(m => {
    const btn = document.getElementById(`tdarr-mode-${m}`);
    if (btn) btn.classList.toggle('active', m === mode);
    const pick = document.getElementById(`tdarr-${m}-pick`);
    if (pick) pick.style.display = (m === mode) ? 'block' : 'none';
  });
}

async function saveAddRule() {
  const intSel = document.getElementById('rule-int');
  const opt = intSel.options[intSel.selectedIndex];
  const kind = opt?.dataset.kind || '';
  const action = document.getElementById('rule-action').value;
  const fileCat = document.getElementById('rule-file-cat').value;

  const data = {
    integration_id: parseInt(intSel.value),
    name: document.getElementById('rule-name').value.trim(),
    when_severity: document.getElementById('rule-sev').value,
    comparison: document.getElementById('rule-cmp').value,
    action: action,
    file_category: fileCat || null,
    severity_match: document.getElementById('rule-sev-match')?.value || 'highest',
    action_config: {},
  };
  if (!data.name) { toast('Name required', 'err'); return; }

  // Build action_config based on action type
  if (action === 'transcode_via_tdarr') {
    if (_tdarrMode === 'library') {
      const libId = document.getElementById('rule-tdarr-library').value;
      if (!libId) { toast('Pick a Tdarr library', 'err'); return; }
      data.action_config.library_id = libId;
    } else if (_tdarrMode === 'plugin') {
      const pluginId = document.getElementById('rule-tdarr-plugin').value;
      if (!pluginId) { toast('Pick a Tdarr plugin', 'err'); return; }
      data.action_config.plugin_id = pluginId;
    } else {  // inline
      data.action_config.inline_profile = {
        codec:          document.getElementById('inline-codec').value,
        container:      document.getElementById('inline-container').value,
        audio_codec:    document.getElementById('inline-audio').value,
        crf:            parseInt(document.getElementById('inline-crf').value) || 22,
        hardware_accel: document.getElementById('inline-hwa').value || null,
        resolution_max: document.getElementById('inline-resmax').value || null,
      };
    }
  } else if (action === 'search_subs_via_bazarr' || action === 'delete_sub_via_bazarr') {
    const mt = document.getElementById('rule-bazarr-media-type').value;
    if (mt) data.action_config.media_type = mt;
  }

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

  _wireAutosave();
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
  _autosaveConfig();
}

function addPath() {
  const v = document.getElementById('new-path').value.trim();
  if (!v) return;
  cfg.library_paths = [...(cfg.library_paths||[]), v];
  document.getElementById('new-path').value = '';
  renderConfig();
  _autosaveConfig();
}
function addIgnore() {
  const v = document.getElementById('new-ignore').value.trim();
  if (!v) return;
  cfg.ignore_patterns = [...(cfg.ignore_patterns||[]), v];
  document.getElementById('new-ignore').value = '';
  renderConfig();
  _autosaveConfig();
}
function addExt(key, inputId) {
  let v = document.getElementById(inputId).value.trim();
  if (!v) return;
  if (!v.startsWith('.')) v = '.' + v;
  v = v.toLowerCase();
  cfg[key] = [...(cfg[key]||[]), v];
  document.getElementById(inputId).value = '';
  renderConfig();
  _autosaveConfig();
}

function togglePrune() {
  cfg.prune_missing = !cfg.prune_missing;
  document.getElementById('prune-toggle').classList.toggle('on', cfg.prune_missing);
  _autosaveConfig();
}

function toggleSchedule() {
  cfg.schedule_enabled = !cfg.schedule_enabled;
  document.getElementById('schedule-toggle').classList.toggle('on', cfg.schedule_enabled);
  document.getElementById('schedule-time-wrap').style.display = cfg.schedule_enabled ? 'block' : 'none';
  _autosaveConfig();
}

async function setCompatMode(mode) {
  cfg.compatibility_mode = mode;
  document.querySelectorAll('.seg-row button').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
  _setSaveStatus("Saving…", "pending");
  try {
    const r = await fetch('/api/config', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(cfg)
    });
    if (r.ok) _setSaveStatus(`Saved (mode: ${mode})`, "ok");
    else _setSaveStatus("Save failed", "err");
  } catch { _setSaveStatus("Save failed", "err"); }
}

// Auto-save: collect values from inputs, push to /api/config debounced.
let _saveStatusTimer = null;
function _setSaveStatus(text, kind = "ok") {
  const el = document.getElementById('autosave-status');
  if (!el) return;
  el.textContent = text;
  el.dataset.kind = kind;
  if (_saveStatusTimer) clearTimeout(_saveStatusTimer);
  if (text && kind === "ok") {
    _saveStatusTimer = setTimeout(() => {
      if (el.textContent === text) el.textContent = "";
    }, 2500);
  }
}

const _autosaveConfig = debounce(async () => {
  // Snapshot UI values into cfg before sending
  cfg.workers = parseInt(document.getElementById('workers-range').value);
  cfg.sample_offset_seconds = parseInt(document.getElementById('offset-range').value);
  cfg.bitrate_threshold_mbps = parseInt(document.getElementById('bitrate-range').value);
  cfg.virustotal_api_key = document.getElementById('vt-key').value;
  cfg.schedule_time = document.getElementById('schedule-time').value;

  _setSaveStatus("Saving…", "pending");
  try {
    const r = await fetch('/api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg)
    });
    if (r.ok) _setSaveStatus("Saved", "ok");
    else _setSaveStatus("Save failed", "err");
  } catch (e) {
    _setSaveStatus("Save failed", "err");
  }
}, 600);

// Hook auto-save to all settings inputs after the page renders config
function _wireAutosave() {
  const ids = [
    'workers-range', 'offset-range', 'bitrate-range',
    'vt-key', 'schedule-time'
  ];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el || el.dataset.autosaveBound) continue;
    el.addEventListener('input', _autosaveConfig);
    el.addEventListener('change', _autosaveConfig);
    el.dataset.autosaveBound = '1';
  }
}

// Manual save (kept as a fallback button + Ctrl+S handler)
async function saveConfig() {
  await _autosaveConfig.flush?.();   // if available
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

// ──────────────────────────────────────────────────────────────
// Auth (logout, password change, API token)
// ──────────────────────────────────────────────────────────────
async function loadAuthInfo() {
  try {
    const r = await fetch('/api/auth/status');
    const s = await r.json();
    if (!s.authenticated) {
      window.location.href = '/login.html';
      return;
    }
    const w = document.getElementById('user-widget');
    w.style.display = 'flex';
    document.getElementById('user-icon-letter').textContent =
      (s.username || '?').charAt(0).toUpperCase();
    document.getElementById('user-name-text').textContent = s.username || '';

    const acctU = document.getElementById('account-username');
    if (acctU) acctU.value = s.username || '';

    // Pull API token (only when on Settings)
    const tokenEl = document.getElementById('account-api-token');
    if (tokenEl && !tokenEl.value) {
      try {
        const tr = await fetch('/api/auth/api-token');
        const td = await tr.json();
        tokenEl.value = td.api_token || '';
      } catch (e) {}
    }
  } catch (e) {
    window.location.href = '/login.html';
  }
}

async function doLogout() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
  window.location.href = '/login.html';
}

async function changePassword() {
  const oldp = document.getElementById('account-old-pw').value;
  const newp = document.getElementById('account-new-pw').value;
  if (!oldp || !newp) { toast('Both passwords required', 'err'); return; }
  if (newp.length < 8) { toast('New password must be ≥ 8 chars', 'err'); return; }
  try {
    const r = await fetch('/api/auth/change-password', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ old_password: oldp, new_password: newp })
    });
    const d = await r.json();
    if (r.ok && d.ok) {
      toast('Password changed; please log in again', 'ok');
      setTimeout(() => doLogout(), 1500);
    } else {
      toast(d.error || 'Failed', 'err');
    }
  } catch { toast('Failed', 'err'); }
}

async function regenerateApiToken() {
  if (!confirm('Regenerate the API token? Existing scripts will stop working.')) return;
  try {
    const r = await fetch('/api/auth/api-token', { method:'POST' });
    const d = await r.json();
    document.getElementById('account-api-token').value = d.api_token || '';
    toast('New API token generated', 'ok');
  } catch { toast('Failed', 'err'); }
}

async function copyApiToken() {
  const v = document.getElementById('account-api-token').value;
  try {
    await navigator.clipboard.writeText(v);
    toast('Token copied to clipboard', 'ok');
  } catch {
    toast('Could not copy — select & Ctrl+C', 'err');
  }
}

// ──────────────────────────────────────────────────────────────
// Update banner
// ──────────────────────────────────────────────────────────────
let _updateState = null;

async function loadUpdateStatus() {
  try {
    const r = await fetch('/api/update/check');
    const s = await r.json();
    _updateState = s;
    renderUpdateBanner(s);
    renderUpdateStatusCard(s);
  } catch (e) {}
}

function renderUpdateBanner(s) {
  const banner = document.getElementById('update-banner');
  if (!banner) return;
  const dismissed = sessionStorage.getItem('auditarr_update_dismissed') === (s.latest_sha || '');
  if (s.available && !dismissed) {
    banner.classList.add('visible');
    document.getElementById('update-banner-detail').innerHTML =
      ` — ${escHtml(s.latest_message || '')} <code>${escHtml(s.latest_short || '')}</code>`;
    const link = document.getElementById('update-banner-view');
    link.onclick = () => { if (s.latest_url) window.open(s.latest_url, '_blank'); };
  } else {
    banner.classList.remove('visible');
  }
}

function renderUpdateStatusCard(s) {
  const el = document.getElementById('update-status-card');
  if (!el) return;
  if (s.last_error) {
    el.innerHTML = `
      <div style="color: var(--sev-unplayable);">⚠ ${escHtml(s.last_error)}</div>
      <div class="small" style="margin-top: 4px">Last checked: ${escHtml(s.last_checked || 'never')}</div>`;
    return;
  }
  if (!s.latest_sha) {
    el.innerHTML = `<div class="small">Not checked yet — click <strong>Check now</strong>.</div>`;
    return;
  }
  const same = s.current_sha && s.current_sha === s.latest_sha;
  if (same) {
    el.innerHTML = `
      <div style="color: var(--sev-ok);">✓ Up to date — running <code>${escHtml(s.current_short)}</code></div>
      <div class="small" style="margin-top: 4px">${escHtml(s.latest_message || '')}</div>
      <div class="small" style="margin-top: 2px">Last checked: ${escHtml(s.last_checked || '')}</div>`;
    return;
  }
  el.innerHTML = `
    <div><strong style="color: var(--sev-ok)">⬆ Update available</strong></div>
    <div style="margin-top: 6px">
      <span class="small">Current:</span> <code>${escHtml(s.current_short || 'unknown')}</code>
      <span class="small" style="margin-left: 8px">→</span>
      <span class="small">Latest:</span> <code>${escHtml(s.latest_short)}</code>
    </div>
    <div class="small" style="margin-top: 6px">${escHtml(s.latest_message || '')}</div>
    ${s.latest_committed_at ? `<div class="small" style="margin-top: 2px">Committed ${escHtml(s.latest_committed_at)}</div>` : ''}
    <div class="small" style="margin-top: 8px">
      Pull with <code>git pull</code> in the Auditarr folder, then click <strong>Mark current as latest</strong>.
    </div>
  `;
}

async function forceUpdateCheck() {
  toast('Checking GitHub…', 'ok');
  try {
    const r = await fetch('/api/update/refresh', { method: 'POST' });
    const s = await r.json();
    _updateState = s;
    renderUpdateBanner(s);
    renderUpdateStatusCard(s);
    if (s.last_error) toast(s.last_error, 'err');
    else if (s.available) toast('Update available!', 'ok');
    else toast('Up to date', 'ok');
  } catch { toast('Check failed', 'err'); }
}

async function markCurrentVersion() {
  if (!_updateState || !_updateState.latest_sha) {
    toast('Run "Check now" first', 'err');
    return;
  }
  try {
    const r = await fetch('/api/update/mark-current', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ sha: _updateState.latest_sha })
    });
    const d = await r.json();
    if (d.ok) {
      toast(`Marked ${(d.current_sha||'').slice(0,7)} as current`, 'ok');
      sessionStorage.removeItem('auditarr_update_dismissed');
      loadUpdateStatus();
    }
  } catch { toast('Failed', 'err'); }
}

function dismissUpdateBanner() {
  if (_updateState && _updateState.latest_sha) {
    sessionStorage.setItem('auditarr_update_dismissed', _updateState.latest_sha);
  }
  document.getElementById('update-banner').classList.remove('visible');
}

// ──────────────────────────────────────────────────────────────
// Bazarr / Tdarr action buttons (used from file detail modal)
// ──────────────────────────────────────────────────────────────
async function bazarrSearchSubs(serverId, mediaType, bazarrId) {
  try {
    const r = await fetch(`/api/bazarr/${serverId}/search-subs`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ media_type: mediaType, bazarr_id: bazarrId })
    });
    const d = await r.json();
    toast(d.message || (d.ok ? 'OK' : 'Failed'), d.ok ? 'ok' : 'err');
  } catch { toast('Failed', 'err'); }
}

async function bazarrDeleteSub(serverId, path, mediaType, bazarrId) {
  if (!confirm('Tell Bazarr to delete this subtitle?')) return;
  try {
    const r = await fetch(`/api/bazarr/${serverId}/delete-sub`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ path, media_type: mediaType, bazarr_id: bazarrId })
    });
    const d = await r.json();
    toast(d.message || (d.ok ? 'OK' : 'Failed'), d.ok ? 'ok' : 'err');
  } catch { toast('Failed', 'err'); }
}

async function tdarrQueue(fileId, serverId, opts) {
  try {
    const r = await fetch(`/api/tdarr/${serverId}/queue`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ file_id: fileId, ...opts })
    });
    const d = await r.json();
    toast(d.message || (d.ok ? 'Queued' : 'Failed'), d.ok ? 'ok' : 'err');
  } catch { toast('Failed', 'err'); }
}

// ──────────────────────────────────────────────────────────────
// Help: README + Changelog (tiny markdown renderer)
// ──────────────────────────────────────────────────────────────
async function loadHelpDocs() {
  try {
    const [r1, r2] = await Promise.all([
      fetch('/api/help/readme').then(r => r.json()),
      fetch('/api/help/changelog').then(r => r.json()),
    ]);
    document.getElementById('readme-body').innerHTML = renderMarkdown(r1.content || '');
    document.getElementById('changelog-body').innerHTML = renderMarkdown(r2.content || '');
  } catch (e) {
    document.getElementById('readme-body').textContent = 'Failed to load.';
  }
}
function setHelpTab(tab) {
  document.querySelectorAll('.help-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.help-pane').forEach(p => p.classList.remove('active'));
  const el = document.getElementById('help-pane-' + tab);
  if (el) el.classList.add('active');
}

function renderMarkdown(src) {
  // Tiny safe-ish markdown → HTML. Escapes input first, then transforms.
  let s = src;
  // 1. Escape HTML
  s = s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  // 2. Code fences ``` ```
  s = s.replace(/```([a-z]*)\n([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code>${code}</code></pre>`);

  // 3. Inline code `…`
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');

  // 4. Headers
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  s = s.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  // 5. Bold / italic
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\b_([^_]+)_\b/g, '<em>$1</em>');

  // 6. Links [text](url)
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');

  // 7. Tables (very simple — leading/trailing pipe rows)
  s = s.replace(/((^\|.*\|\s*\n)+)/gm, (block) => {
    const rows = block.trim().split('\n').map(r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim()));
    if (rows.length < 2) return block;
    const isSep = (row) => row.every(c => /^:?-+:?$/.test(c));
    let header = null, body = [];
    if (rows[1] && isSep(rows[1])) { header = rows[0]; body = rows.slice(2); }
    else { body = rows; }
    let html = '<table>';
    if (header) html += '<thead><tr>' + header.map(c => `<th>${c}</th>`).join('') + '</tr></thead>';
    html += '<tbody>' + body.map(row =>
      '<tr>' + row.map(c => `<td>${c}</td>`).join('') + '</tr>').join('') + '</tbody>';
    return html + '</table>\n';
  });

  // 8. Lists — group consecutive `-` lines
  s = s.replace(/((^- .+(?:\n|$))+)/gm, (block) => {
    const items = block.trim().split('\n').map(l => l.replace(/^- /, ''));
    return '<ul>' + items.map(i => `<li>${i}</li>`).join('') + '</ul>\n';
  });
  s = s.replace(/((^\d+\. .+(?:\n|$))+)/gm, (block) => {
    const items = block.trim().split('\n').map(l => l.replace(/^\d+\. /, ''));
    return '<ol>' + items.map(i => `<li>${i}</li>`).join('') + '</ol>\n';
  });

  // 9. Paragraphs from bare text
  s = s.split(/\n\n+/).map(p => {
    if (/^\s*<(h\d|ul|ol|pre|table|p|div|blockquote)/.test(p)) return p;
    return '<p>' + p.replace(/\n/g, '<br>') + '</p>';
  }).join('\n');

  return s;
}

// ──────────────────────────────────────────────────────────────
// Database management: stats, backup, restore, vacuum, integrity, clean
// ──────────────────────────────────────────────────────────────
async function loadDbStats() {
  try {
    const r = await fetch('/api/db/stats');
    const s = await r.json();
    const sizeMb = (s.size_bytes / 1024 / 1024).toFixed(2);
    const html = `
      <div>📁  <strong>${escHtml(s.path)}</strong></div>
      <div>📦  ${sizeMb} MB</div>
      <div>📑  ${s.files} files · ${s.evaluations} evaluations</div>
      <div>⚙  ${s.integrations} integrations · ${s.automation_rules} automation rules · ${s.custom_rules} custom rules</div>
      <div>🔢  schema v${s.schema_version}</div>
    `;
    document.getElementById('db-stats-card').innerHTML = html;
  } catch (e) {
    document.getElementById('db-stats-card').textContent = 'Failed to load DB stats';
  }
}

async function downloadBackup() {
  try {
    const r = await fetch('/api/db/backup');
    if (!r.ok) { toast('Backup failed', 'err'); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ts = new Date().toISOString().replace(/[T:]/g,'-').slice(0,19);
    a.download = `auditarr-backup-${ts}.db`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast('Backup downloaded', 'ok');
  } catch (e) { toast('Backup failed', 'err'); }
}

async function restoreBackup(input) {
  const file = input.files[0];
  if (!file) return;
  if (!confirm(`Restore from "${file.name}"?\n\nThis REPLACES the current Auditarr database. Make sure you have a fresh backup of the live database first.`)) {
    input.value = '';
    return;
  }
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/api/db/restore', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.ok) {
      toast(`Restored. Schema version: ${d.schema_version}. Reloading…`, 'ok');
      setTimeout(() => window.location.reload(), 1500);
    } else {
      toast(d.error || 'Restore failed', 'err');
    }
  } catch (e) {
    toast('Restore failed', 'err');
  }
  input.value = '';
}

async function vacuumDb() {
  toast('Vacuuming…', 'ok');
  try {
    const r = await fetch('/api/db/vacuum', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      const saved = (d.saved_bytes / 1024 / 1024).toFixed(2);
      toast(`Vacuum complete. Saved ${saved} MB`, 'ok');
      loadDbStats();
    } else { toast(d.error || 'Vacuum failed', 'err'); }
  } catch { toast('Vacuum failed', 'err'); }
}

async function checkIntegrity() {
  try {
    const r = await fetch('/api/db/integrity');
    const d = await r.json();
    toast(d.ok ? 'Integrity: OK ✓' : `Integrity: FAILED — ${d.message}`, d.ok ? 'ok' : 'err');
  } catch { toast('Integrity check failed', 'err'); }
}

async function cleanEvaluations() {
  if (!confirm('Delete ALL stored evaluations? Files stay; you will need to run Re-eval Rules to re-populate them.')) return;
  try {
    const r = await fetch('/api/db/clean', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      toast(`Cleared ${d.deleted_evaluations} evaluations. Run Re-eval Rules.`, 'ok');
      loadStats();
      loadFiles();
      loadDbStats();
    }
  } catch { toast('Clean failed', 'err'); }
}
