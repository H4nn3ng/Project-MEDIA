/* Money Psychology Dashboard — app.js */
'use strict';

// ──────────────────────────────────────────────────────────────── State
const _state = {
  running: null, current_step: null, step_status: {},
  last_exit_code: null, last_command: null, elapsed_s: null,
  api_calls: 0,
};

// ──────────────────────────────────────────────────────────────── Utils
const $ = id => document.getElementById(id);
const fmt = n => n == null ? '—' : String(n);

function api(path, opts = {}) {
  return fetch(path, opts).then(r => r.json());
}

function showToast(msg, ok = true) {
  let el = document.createElement('div');
  el.className = 'toast ' + (ok ? 'toast-ok' : 'toast-err');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2800);
}

// ──────────────────────────────────────────────────────────────── Navigation
const App = {};

App.nav = function(stage) {
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.stage === stage);
  });
  document.querySelectorAll('.stage').forEach(s => {
    s.classList.toggle('active', s.id === 'stage-' + stage);
  });
  if (stage === 'footage')     App.footage.load();
  if (stage === 'finalreview') App.finalreview.load();
  if (stage === 'corpus')      App.corpus.load();
  if (stage === 'intelligence') App.intelligence.load();
  if (stage === 'render')      App.pool.load();
};

// ──────────────────────────────────────────────────────────────── Pipeline control
App.run = function(command) {
  if (_state.running) { showToast('Already running: ' + _state.running, false); return; }
  // Clear log for new run
  _logLines = 0;
  $('log-lines').innerHTML = '';
  $('log-count').textContent = '0 lines';
  api('/api/run/' + command, { method: 'POST' })
    .then(d => { if (d.error) showToast(d.error, false); })
    .catch(() => showToast('Request failed', false));
};

App.stop = function() {
  api('/api/stop', { method: 'POST' });
};

App.toggleLog = function() {
  const drawer = $('log-drawer');
  const open = drawer.classList.toggle('open');
  $('btn-log').classList.toggle('active', open);
  $('btn-log-label').textContent = open ? 'Hide log' : 'Show log';
};

// ──────────────────────────────────────────────────────────────── Status rendering
function applyStatus(snap) {
  Object.assign(_state, snap);
  const running  = snap.running;
  const pill     = $('status-pill');
  const dot      = $('status-dot');
  const txt      = $('status-text');
  const elapsed  = $('elapsed');
  const btnStop  = $('btn-stop');
  const btnScrape = $('btn-run-scrape');
  const btnRender = $('btn-run-render');
  const btnRenderGo = $('btn-render-go');

  const apiChip = $('api-calls-chip');
  const apiCount = $('api-calls-count');

  if (running) {
    pill.className = 'status-pill running';
    dot.className  = 'status-dot running';
    txt.textContent = 'Running: ' + running;
    elapsed.textContent = snap.elapsed_s ? snap.elapsed_s + 's' : '';
    btnStop.disabled  = false;
    if (btnScrape) btnScrape.disabled = true;
    if (btnRender) btnRender.disabled = true;
    if (btnRenderGo) btnRenderGo.disabled = true;
    if (apiChip) { apiChip.style.display = ''; apiCount.textContent = snap.api_calls || 0; }
  } else {
    pill.className = 'status-pill idle';
    dot.className  = 'status-dot idle';
    const last = snap.last_command;
    const code = snap.last_exit_code;
    txt.textContent = last
      ? (code === 0 ? last + ' ✓ done' : last + ' ✗ failed')
      : 'No run in progress';
    elapsed.textContent = '';
    btnStop.disabled = true;
    if (btnScrape) btnScrape.disabled = false;
    if (btnRender) btnRender.disabled = false;
    if (btnRenderGo) btnRenderGo.disabled = false;
    if (apiChip) apiChip.style.display = 'none';
  }

  renderSteps(snap.step_status, snap.current_step, running);
  if (!running && snap.last_command) renderRecap(snap);
}

const _SCRAPE_STEPS = [
  { key: 'scrape',          label: 'Scraper' },
  { key: 'score_concepts',  label: 'Score concepts' },
  { key: 'factcheck',       label: 'Fact-check' },
  { key: 'score_media',     label: 'Score media' },
];

function renderSteps(status, current, running) {
  const row = $('steps-full');
  if (!row) return;
  const steps = _SCRAPE_STEPS;
  row.innerHTML = steps.map(s => {
    const st = status[s.key] || (s.key === current && running ? 'running' : 'pending');
    return `<div class="step-chip step-${st}">${s.label}</div>`;
  }).join('');
  $('step-live-label').textContent = current ? current.replace('_', ' ') : '';
}

function renderRecap(snap) {
  const el = $('last-run-recap');
  if (!el) return;
  el.style.display = '';
  const ok = snap.last_exit_code === 0;
  $('recap-label').textContent = 'Last: ' + (snap.last_command || '');
  $('recap-pill').textContent  = ok ? 'success' : 'failed';
  $('recap-pill').className    = 'pill ' + (ok ? 'pill-ok' : 'pill-err');
}

// ──────────────────────────────────────────────────────────────── Stats
function loadStats() {
  api('/api/stats').then(d => {
    $('ov-staging').textContent = fmt(d.staging?.footage);
    $('ov-footage').textContent = fmt(d.pool?.footage);
    $('ov-audio').textContent   = fmt(d.pool?.audio);

    const ready = d.final_review?.ready_to_post || {};
    const readyTotal = Object.values(ready).reduce((a,b)=>a+b,0);
    $('ov-ready').textContent     = fmt(readyTotal);
    $('ov-ready-sub').textContent = readyTotal === 1 ? 'ready' : 'ready';

    const poolF = d.pool?.footage || 0;
    $('rail-batch-value').textContent = fmt(poolF);
    const fill = Math.min(100, poolF * 10);
    $('rail-batch-fill').style.width = fill + '%';
  }).catch(() => {});
}

// ──────────────────────────────────────────────────────────────── SSE
function initSSE() {
  const es = new EventSource('/api/events');
  es.onmessage = e => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'status')  { applyStatus(msg.payload); loadStats(); }
    if (msg.type === 'log')     addLogLine(msg.line);
  };
  es.onerror = () => setTimeout(initSSE, 3000);
}

// ──────────────────────────────────────────────────────────────── Log resize
(function() {
  const MIN_H = 120, MAX_H = window.innerHeight * 0.85;
  let dragging = false, startY = 0, startH = 0;

  function setLogH(h) {
    h = Math.max(MIN_H, Math.min(MAX_H, h));
    document.documentElement.style.setProperty('--log-h', h + 'px');
  }

  document.addEventListener('DOMContentLoaded', () => {
    const handle = $('log-resize-handle');
    if (!handle) return;
    handle.addEventListener('mousedown', e => {
      const drawer = $('log-drawer');
      dragging  = true;
      startY    = e.clientY;
      startH    = drawer.getBoundingClientRect().height;
      drawer.classList.add('resizing');
      e.preventDefault();
    });
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    setLogH(startH + (startY - e.clientY));
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    $('log-drawer')?.classList.remove('resizing');
  });
})();

// ──────────────────────────────────────────────────────────────── Log drawer
// Lines from these internal libraries are noise — suppress them
const _LOG_NOISE = [
  /^httpx\s+—\s+HTTP Request:/i,
  /^google_genai\._api_client\s+—/i,
  /^google_genai\.models\s+—\s+AFC is enabled/i,
];

let _logLines = 0;
function addLogLine(raw) {
  // Strip Python log prefix "HH:MM:SS [LEVEL] logger — " for noise check
  const bodyForFilter = raw.replace(/^\d{2}:\d{2}:\d{2}\s+\[?\w+\]?\s+/, '');
  if (_LOG_NOISE.some(re => re.test(bodyForFilter))) return;

  // Auto-open drawer on first line of a new run
  if (_logLines === 0) {
    const drawer = $('log-drawer');
    if (!drawer.classList.contains('open')) App.toggleLog();
  }

  const container = $('log-lines');
  const row = document.createElement('div');
  row.className = 'log-row';

  // Parse "HH:MM:SS [LEVEL] name — msg" from Python logging format
  const tsMatch  = raw.match(/^(\d{2}:\d{2}:\d{2})\s+(.*)/s);
  const ts       = tsMatch ? tsMatch[1] : '';
  const body     = tsMatch ? tsMatch[2] : raw;

  // Detect level tag like [INFO], [WARNING], [ERROR]
  const lvlMatch = body.match(/^\[(INFO|WARNING|ERROR|DEBUG|CRITICAL)\]\s*/i);
  const level    = lvlMatch ? lvlMatch[1].toUpperCase() : '';
  const rest     = lvlMatch ? body.slice(lvlMatch[0].length) : body;

  const isErr  = level === 'ERROR'   || level === 'CRITICAL' || /error|exception|traceback|failed/i.test(rest);
  const isWarn = level === 'WARNING' || /warn/i.test(rest);
  const isOk   = /✓|complete|done|produced/i.test(rest) && !isErr;

  const tagCls = isErr ? 'log-tag-error' : isWarn ? 'log-tag-warn' : isOk ? 'log-tag-ok' : 'log-tag-step';
  const msgStyle = isErr ? 'color:#c27264' : isWarn ? 'color:var(--sand)' : isOk ? 'color:#98A986' : '';

  row.innerHTML =
    `<span class="log-ts">${_esc(ts)}</span>` +
    `<span class="log-tag ${tagCls}">${_esc(level)}</span>` +
    `<span class="log-msg" style="${msgStyle}">${_esc(rest)}</span>`;

  // Blinking cursor on last line
  const prev = container.querySelector('.log-cursor');
  if (prev) prev.remove();
  const cursor = document.createElement('span');
  cursor.className = 'log-cursor';
  row.querySelector('.log-msg').appendChild(cursor);

  container.appendChild(row);
  _logLines++;
  $('log-count').textContent = _logLines + ' lines';
  $('log-run-label').textContent = _state.running || _state.last_command || '—';

  // Auto-scroll if near bottom
  if (container.scrollHeight - container.scrollTop - container.clientHeight < 80) {
    container.scrollTop = container.scrollHeight;
  }
}

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ──────────────────────────────────────────────────────────────── Pool widget
App.pool = {};
App.pool.load = function() {
  api('/api/pool-summary').then(d => {
    const sub = $('pool-widget-sub');
    const rows = $('pool-rows');
    if (!rows) return;
    const f = d.footage?.clips || 0;
    const m = d.music?.tracks || 0;
    const b = d.beds?.tracks  || 0;
    const i = d.images?.total || 0;
    sub.textContent = `${f} clips · ${m} music · ${b} beds · ${i} images`;
    rows.innerHTML = `
      <div class="pool-row">
        <div class="pool-row-label">Footage clips</div>
        <div class="pool-row-val">${f}</div>
        <div class="pool-row-bar"><div class="pool-row-fill" style="width:${Math.min(100,f*8)}%"></div></div>
      </div>
      <div class="pool-row">
        <div class="pool-row-label">Music tracks</div>
        <div class="pool-row-val">${m}</div>
        <div class="pool-row-bar"><div class="pool-row-fill" style="width:${Math.min(100,m*8)}%"></div></div>
      </div>
      <div class="pool-row">
        <div class="pool-row-label">Bed / ambient</div>
        <div class="pool-row-val">${b}</div>
        <div class="pool-row-bar"><div class="pool-row-fill" style="width:${Math.min(100,b*8)}%"></div></div>
      </div>
      <div class="pool-row">
        <div class="pool-row-label">Images</div>
        <div class="pool-row-val">${i}</div>
        <div class="pool-row-bar"><div class="pool-row-fill" style="width:${Math.min(100,i*4)}%"></div></div>
      </div>`;
  }).catch(() => {});
};

// ──────────────────────────────────────────────────────────────── Sort Media
App.footage = {
  _type: 'footage',
  _items: [],
  _audioItems: [],
  _imgItems: [],
  _imgProgress: {},
  _progress: {},
};

App.footage.load = function() {
  App.footage.setType(App.footage._type);
};

App.footage.setType = function(type) {
  App.footage._type = type;
  ['footage','audio','images'].forEach(t => {
    $('tab-' + t)?.classList.toggle('active', t === type);
  });
  $('footage-progress-wrap').style.display = type === 'images' ? 'none' : '';
  $('footage-reviewer').style.display      = type === 'images' ? 'none' : '';
  $('images-tab-panel').style.display      = type === 'images' ? '' : 'none';

  if (type === 'footage') App.footage._loadFootage();
  if (type === 'audio')   App.footage._loadAudio();
  if (type === 'images')  App.images.load();
};

App.footage._loadFootage = function() {
  api('/api/media').then(items => {
    App.footage._items = items;
    const unreviewed = items.filter(i => !i.rating);
    const kept  = items.filter(i => i.rating === 'g').length;
    const rej   = items.filter(i => i.rating === 'b').length;
    const total = items.length;
    $('tab-count-footage').textContent = total ? `(${total})` : '';

    const pBar  = $('prog-kept');
    const pRej  = $('prog-rej');
    const pLbl  = $('footage-prog-label');
    if (total) {
      pBar.style.width  = (kept/total*100) + '%';
      pRej.style.width  = (rej/total*100)  + '%';
      pLbl.textContent  = `${kept} kept · ${rej} rejected · ${total-kept-rej} unreviewed`;
    } else {
      pLbl.textContent = 'No footage in staging';
    }
    $('btn-footage-commit').disabled = (kept + rej) === 0;

    const reviewer = $('footage-reviewer');
    if (!unreviewed.length && !total) {
      reviewer.innerHTML = '<div class="detail-empty" style="grid-column:1/-1">No footage in staging. Run scrape first.</div>';
      return;
    }
    if (!unreviewed.length) {
      reviewer.innerHTML = '<div class="detail-empty" style="grid-column:1/-1">All footage rated. Click Commit → to move to pool.</div>';
      return;
    }
    App.footage._renderCard(unreviewed[0], 0, unreviewed.length);
  });
};

App.footage._renderCard = function(item, idx, total) {
  const reviewer = $('footage-reviewer');
  const concept = item.concept_id ? `<div class="detail-meta-row"><span class="meta-label">Concept</span><span class="meta-val concept-tag">${item.concept_id}</span></div>` : '';
  const dur = item.duration ? `${item.duration}s` : '?';
  reviewer.innerHTML = `
    <div class="detail-card" id="detail-card">
      <div class="detail-media">
        <video src="/media/${item.media_url}" controls muted autoplay loop style="max-width:100%;max-height:360px;border-radius:8px"></video>
      </div>
      <div class="detail-info">
        <div class="detail-title">${item.title || item.filename}</div>
        <div class="detail-meta">
          <div class="detail-meta-row"><span class="meta-label">Source</span><span class="meta-val">${item.source}</span></div>
          <div class="detail-meta-row"><span class="meta-label">Score</span><span class="meta-val">${item.score ?? '—'}</span></div>
          <div class="detail-meta-row"><span class="meta-label">Tier</span><span class="meta-val tier-${item.tier}">${item.tier}</span></div>
          <div class="detail-meta-row"><span class="meta-label">Duration</span><span class="meta-val">${dur}</span></div>
          ${item.keyword ? `<div class="detail-meta-row"><span class="meta-label">Keyword</span><span class="meta-val">${item.keyword}</span></div>` : ''}
          ${concept}
        </div>
        <div class="detail-progress">${idx+1} of ${total} unreviewed</div>
        <div class="detail-actions">
          <button class="btn btn-primary" onclick="App.footage.rate('${item.filename}','g')">✓ Keep</button>
          <button class="btn btn-outline" onclick="App.footage.rate('${item.filename}','b')">✗ Reject</button>
          <button class="btn btn-ghost"   onclick="App.footage._loadFootage()">↷ Skip</button>
        </div>
      </div>
    </div>`;
};

App.footage.rate = function(filename, rating) {
  api('/api/media/rate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, rating }),
  }).then(() => App.footage._loadFootage());
};

App.footage.commit = function() {
  const type = App.footage._type;
  if (type === 'audio') {
    api('/api/audio/commit', { method: 'POST' }).then(d => {
      showToast(`Audio: ${d.moved_bad} track(s) rejected`);
      App.footage._loadAudio();
      loadStats();
    });
    return;
  }
  if (type === 'images') {
    api('/api/images/commit', { method: 'POST' }).then(d => {
      showToast(`Images: ${d.moved_bad} rejected`);
      App.images.load();
      loadStats();
    });
    return;
  }
  api('/api/media/commit', { method: 'POST' }).then(d => {
    showToast(`Committed: ${d.moved_good} → pool, ${d.moved_bad} → rejected`);
    App.footage._loadFootage();
    loadStats();
  });
};

App.footage._loadAudio = function() {
  api('/api/audio').then(items => {
    App.footage._audioItems = items;
    $('tab-count-audio').textContent = items.length ? `(${items.length})` : '';
    const reviewer = $('footage-reviewer');
    if (!items.length) {
      reviewer.innerHTML = '<div class="detail-empty" style="grid-column:1/-1">No audio in pool. Run scrape first.</div>';
      App.footage._updateAudioCommit();
      return;
    }
    reviewer.innerHTML = items.map(item => {
      const rejected = item.rating === 'b';
      const cls = rejected ? 'audio-card rejected' : 'audio-card';
      return `<div class="${cls}" id="ac-${CSS.escape(item.filename)}">
        <audio src="/media/${item.media_url}" controls style="width:100%"></audio>
        <div class="audio-info">
          <div class="audio-title">${_esc(item.title || item.filename)}</div>
          <div class="audio-meta">${_esc(item.source)} · ${item.media_kind || 'music'} · score ${item.score ?? '—'}</div>
        </div>
        <button class="btn btn-sm audio-reject-btn ${rejected ? 'active' : ''}"
                onclick="App.footage.toggleAudioReject(${JSON.stringify(item.filename)})">
          ${rejected ? '↩ Un-reject' : '✗ Reject'}
        </button>
      </div>`;
    }).join('') + `
      <div class="rejected-toggle-row">
        <button class="btn btn-ghost btn-sm" id="btn-show-rejected-audio"
                onclick="App.footage.toggleAudioRejected()">↩ Rejected</button>
      </div>
      <div id="audio-rejected-panel" style="display:none"></div>
    `;
    App.footage._loadAudioRejected();
    App.footage._updateAudioCommit();
  });
};

App.footage._audioRejectedOpen = false;
App.footage._audioRejectedItems = [];

App.footage.toggleAudioRejected = function() {
  App.footage._audioRejectedOpen = !App.footage._audioRejectedOpen;
  const panel = $('audio-rejected-panel');
  if (!panel) return;
  panel.style.display = App.footage._audioRejectedOpen ? '' : 'none';
  if (App.footage._audioRejectedOpen) App.footage._loadAudioRejected();
};

App.footage._loadAudioRejected = function() {
  api('/api/rejected/audio').then(items => {
    App.footage._audioRejectedItems = items;
    const btn = $('btn-show-rejected-audio');
    if (btn) btn.textContent = items.length
      ? `${App.footage._audioRejectedOpen ? '▲ Hide' : '↩ Rejected'} (${items.length})`
      : '↩ Rejected';
    const panel = $('audio-rejected-panel');
    if (!panel || !App.footage._audioRejectedOpen) return;
    if (!items.length) {
      panel.innerHTML = '<div class="empty">No rejected audio.</div>';
      return;
    }
    panel.innerHTML = items.map(item => `
      <div class="audio-card" id="arej-${CSS.escape(item.filename)}">
        <audio src="/media/${item.media_url}" controls style="width:100%"></audio>
        <div class="audio-info">
          <div class="audio-title">${_esc(item.title || item.filename)}</div>
          <div class="audio-meta">${_esc(item.source)} · score ${item.score ?? '—'}</div>
        </div>
        <button class="btn btn-sm restore-btn" onclick="App.footage.restoreAudio(${JSON.stringify(item.filename)})">↩ Restore</button>
      </div>
    `).join('');
  });
};

App.footage.restoreAudio = function(filename) {
  api('/api/rejected/audio/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
  }).then(d => {
    if (d.ok) {
      document.getElementById('arej-' + CSS.escape(filename))?.remove();
      App.footage._audioRejectedItems = App.footage._audioRejectedItems.filter(i => i.filename !== filename);
      showToast('Audio restored to pool');
      App.footage._loadAudio();
    } else {
      showToast(d.error || 'Restore failed', false);
    }
  });
};

App.footage.toggleAudioReject = function(filename) {
  const items   = App.footage._audioItems || [];
  const item    = items.find(i => i.filename === filename);
  if (!item) return;
  const newRating = item.rating === 'b' ? '' : 'b';
  api('/api/audio/rate', {method:'POST', headers:{'Content-Type':'application/json'},
       body: JSON.stringify({filename, rating: newRating})})
    .then(() => {
      item.rating = newRating;
      const card  = document.getElementById('ac-' + CSS.escape(filename));
      const btn   = card?.querySelector('.audio-reject-btn');
      if (card) card.classList.toggle('rejected', newRating === 'b');
      if (btn)  { btn.textContent = newRating === 'b' ? '↩ Restore' : '✗ Reject'; btn.classList.toggle('active', newRating === 'b'); }
      App.footage._updateAudioCommit();
    });
};

App.footage._updateAudioCommit = function() {
  const items   = App.footage._audioItems || [];
  const hasRejects = items.some(i => i.rating === 'b');
  $('btn-footage-commit').disabled = !hasRejects;
};

// ──────────────────────────────────────────────────────────────── Images
App.images = {};
App.images._items = [];
App.images._lightboxIdx = 0;

App.images.load = function() {
  api('/api/images').then(items => {
    App.images._items = items;
    $('tab-count-images').textContent = items.length ? `(${items.length})` : '';
    const grid = $('images-grid');
    if (!items.length) {
      grid.innerHTML = '<div class="empty">No images in pool.</div>';
    } else {
      grid.innerHTML = items.map((item, i) => {
        const cls = item.rating === 'b' ? 'img-card rejected' : 'img-card';
        return `<div class="${cls}" onclick="App.images.openLightbox(${i})">
          <img src="/media/${item.media_url}" alt="${_esc(item.title || '')}" loading="lazy">
          <div class="img-card-footer">
            <span class="img-score">${item.score ?? '—'}</span>
            ${item.rating === 'b' ? '<span class="img-rejected-badge">✗</span>' : ''}
          </div>
        </div>`;
      }).join('');
    }
    const hasRejects = items.some(i => i.rating === 'b');
    $('btn-footage-commit').disabled = !hasRejects;
    App.images._loadRejectedCount();
  });
};

App.images._loadRejectedCount = function() {
  api('/api/rejected/images').then(items => {
    const btn = $('btn-show-rejected-images');
    if (!btn) return;
    btn.textContent = items.length ? `↩ Rejected (${items.length})` : '↩ Rejected';
    btn.style.display = '';
    App.images._rejectedItems = items;
    if (App.images._rejectedOpen) App.images._renderRejected(items);
  });
};

App.images._rejectedOpen = false;
App.images._rejectedItems = [];

App.images.toggleRejected = function() {
  App.images._rejectedOpen = !App.images._rejectedOpen;
  const panel = $('images-rejected-panel');
  panel.style.display = App.images._rejectedOpen ? '' : 'none';
  $('btn-show-rejected-images').textContent = App.images._rejectedOpen
    ? `▲ Hide rejected (${App.images._rejectedItems.length})`
    : `↩ Rejected (${App.images._rejectedItems.length})`;
  if (App.images._rejectedOpen) App.images._renderRejected(App.images._rejectedItems);
};

App.images._renderRejected = function(items) {
  const grid = $('images-rejected-panel');
  if (!items.length) {
    grid.innerHTML = '<div class="empty">No rejected images.</div>';
    return;
  }
  grid.innerHTML = items.map(item => `
    <div class="img-card rej-card" id="rej-${CSS.escape(item.filename)}">
      <img src="/media/${item.media_url}" alt="${_esc(item.title || '')}" loading="lazy">
      <div class="img-card-footer">
        <span class="img-score">${item.score ?? '—'}</span>
        <button class="btn btn-xs restore-btn" onclick="event.stopPropagation();App.images.restore(${JSON.stringify(item.filename)})">↩</button>
      </div>
    </div>
  `).join('');
};

App.images.restore = function(filename) {
  api('/api/rejected/images/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
  }).then(d => {
    if (d.ok) {
      App.images._rejectedItems = App.images._rejectedItems.filter(i => i.filename !== filename);
      App.images._renderRejected(App.images._rejectedItems);
      $('btn-show-rejected-images').textContent = `▲ Hide rejected (${App.images._rejectedItems.length})`;
      showToast('Image restored to pool');
      App.images.load();
    } else {
      showToast(d.error || 'Restore failed', false);
    }
  });
};

App.images.openLightbox = function(idx) {
  App.images._lightboxIdx = idx;
  App.images._showLightbox();
};

App.images._showLightbox = function() {
  const items = App.images._items;
  const idx   = App.images._lightboxIdx;
  const item  = items[idx];
  if (!item) return;
  $('lightbox-img').src    = '/media/' + item.media_url;
  $('lightbox-counter').textContent = `${idx+1} / ${items.length}`;
  $('img-lightbox').style.display = 'flex';
  const g = $('lightbox-rate-g');
  const b = $('lightbox-rate-b');
  g.classList.toggle('active', item.rating === 'g');
  b.classList.toggle('active', item.rating === 'b');
};

App.images.lightboxNav = function(dir) {
  const items = App.images._items;
  App.images._lightboxIdx = (App.images._lightboxIdx + dir + items.length) % items.length;
  App.images._showLightbox();
};

App.images.closeLightbox = function() {
  $('img-lightbox').style.display = 'none';
};

App.images.lightboxBgClick = function(e) {
  if (e.target === $('img-lightbox')) App.images.closeLightbox();
};

App.images.rateInLightbox = function(rating) {
  const item = App.images._items[App.images._lightboxIdx];
  if (!item) return;
  const newRating = item.rating === rating ? '' : rating;
  api('/api/images/rate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: item.filename, rating: newRating }),
  }).then(() => {
    item.rating = newRating;
    App.images._showLightbox();
    if (newRating) App.images.lightboxNav(1);
  });
};

// ──────────────────────────────────────────────────────────────── Final Review
App.finalreview = {
  _category: 'reel',
  _renders: [],
  _slideshow: { slides: [], idx: 0 },
};

App.finalreview.load = function() {
  api('/api/renders').then(renders => {
    App.finalreview._renders = renders;
    const reels     = renders.filter(r => r.format === 'reel');
    const carousels = renders.filter(r => r.format === 'carousel');
    $('fr-count-reel').textContent     = reels.length     ? `(${reels.length})`     : '';
    $('fr-count-carousel').textContent = carousels.length ? `(${carousels.length})` : '';
    App.finalreview.setCategory(App.finalreview._category);
  });
};

App.finalreview.setCategory = function(cat) {
  App.finalreview._category = cat;
  ['reel','carousel'].forEach(c => {
    $('fr-tab-' + c)?.classList.toggle('active', c === cat);
  });
  const items = App.finalreview._renders.filter(r => r.format === cat);
  App.finalreview._renderList(items);
};

App.finalreview._renderList = function(items) {
  const el = $('fr-reviewer');
  if (!items.length) {
    el.innerHTML = '<div class="detail-empty">No renders pending.</div>';
    return;
  }
  el.innerHTML = items.map(r => {
    const verdictClass = r.verdict === 'postable' ? 'verdict-post' : r.verdict === 'not_postable' ? 'verdict-nopost' : '';
    const verdictLabel = r.verdict === 'postable' ? '✓ Postable' : r.verdict === 'not_postable' ? '✗ Not postable' : 'Pending';
    return `<div class="fr-card ${verdictClass}" id="fr-card-${r.name}" onclick="App.finalreview.openRender('${r.name}')">
      <div class="fr-card-head">
        <div class="fr-card-name">${r.name}</div>
        <div class="fr-card-format">${r.format}</div>
        ${r.concept_id ? `<div class="fr-concept-tag">${r.concept_id}</div>` : ''}
      </div>
      <div class="fr-card-caption">${r.caption || ''}</div>
      <div class="fr-card-verdict ${verdictClass}">${verdictLabel}</div>
    </div>`;
  }).join('');
};

App.finalreview.openRender = function(name) {
  api('/api/renders/' + name).then(detail => {
    App.finalreview._showDetail(detail);
  });
};

App.finalreview._showDetail = function(detail) {
  const el = $('fr-reviewer');
  if (detail.format === 'reel') {
    const variants = detail.variants || [];
    const verdict  = detail.verdict;
    const v = variants[0];
    el.innerHTML = `
      <div class="fr-detail">
        <button class="btn btn-ghost btn-sm" onclick="App.finalreview.load()">← Back</button>
        <h3 class="fr-detail-title">${detail.name}</h3>
        ${detail.concept_id ? `<div class="fr-concept-tag" style="margin:8px 0">${detail.concept_id}</div>` : ''}
        ${variants.map(v => `
          <div class="fr-reel-variant ${detail.chosen === v.filename ? 'fr-chosen' : ''}">
            <video src="/media/${v.media_url}" controls style="width:100%;max-height:540px;border-radius:8px"></video>
            <div class="fr-variant-meta">${v.filename} · ${v.size_mb} MB</div>
            <button class="btn btn-sm btn-primary" onclick="App.finalreview.selectVariant('${detail.name}','${v.filename}')">Select this variant</button>
          </div>`).join('')}
        ${detail.caption ? `<div class="fr-caption-box"><pre>${detail.caption}</pre></div>` : ''}
        <div class="fr-verdict-bar">
          <button class="btn btn-primary" onclick="App.finalreview.submitVerdict('${detail.name}','postable')">✓ Postable</button>
          <button class="btn btn-outline" onclick="App.finalreview.submitVerdict('${detail.name}','not_postable')">✗ Not postable</button>
          <button class="btn btn-ghost btn-sm" onclick="App.finalreview.deleteRender('${detail.name}')">🗑 Delete</button>
        </div>
        ${verdict ? `<div class="fr-current-verdict">Current verdict: <b>${verdict}</b></div>` : ''}
      </div>`;
    App.finalreview._currentDetail = detail;
  }
  if (detail.format === 'carousel') {
    const slides = detail.slides || [];
    App.finalreview._slideshow = { slides, idx: 0, name: detail.name };
    el.innerHTML = `
      <div class="fr-detail">
        <button class="btn btn-ghost btn-sm" onclick="App.finalreview.load()">← Back</button>
        <h3 class="fr-detail-title">${detail.name}</h3>
        ${detail.concept_id ? `<div class="fr-concept-tag" style="margin:8px 0">${detail.concept_id}</div>` : ''}
        <div class="fr-slides-grid">
          ${slides.map((s,i) => `<img src="/media/${s.media_url}" class="fr-slide-thumb" onclick="App.finalreview.openSlideshow(${i})">`).join('')}
        </div>
        ${detail.caption ? `<div class="fr-caption-box"><pre>${detail.caption}</pre></div>` : ''}
        <div class="fr-verdict-bar">
          <button class="btn btn-primary" onclick="App.finalreview.submitVerdict('${detail.name}','postable')">✓ Postable</button>
          <button class="btn btn-outline" onclick="App.finalreview.submitVerdict('${detail.name}','not_postable')">✗ Not postable</button>
          <button class="btn btn-ghost btn-sm" onclick="App.finalreview.deleteRender('${detail.name}')">🗑 Delete</button>
        </div>
        ${detail.verdict ? `<div class="fr-current-verdict">Current verdict: <b>${detail.verdict}</b></div>` : ''}
      </div>`;
    App.finalreview._currentDetail = detail;
  }
};

App.finalreview.selectVariant = function(name, variant) {
  App.finalreview._selectedVariant = variant;
  document.querySelectorAll('.fr-reel-variant').forEach(el => {
    el.classList.toggle('fr-chosen', el.querySelector('.fr-variant-meta')?.textContent?.includes(variant));
  });
};

App.finalreview.submitVerdict = function(name, verdict) {
  const chosen = App.finalreview._selectedVariant || '';
  api('/api/renders/' + name + '/verdict', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ verdict, chosen_variant: chosen }),
  }).then(d => {
    if (d.error) { showToast(d.error, false); return; }
    showToast(verdict === 'postable' ? '✓ Approved → ready to post' : '✗ Moved to not_postable');
    App.finalreview.load();
    loadStats();
  });
};

App.finalreview.deleteRender = function(name) {
  if (!confirm('Delete render ' + name + '?')) return;
  api('/api/renders/' + name, { method: 'DELETE' }).then(d => {
    if (d.error) { showToast(d.error, false); return; }
    showToast('Deleted');
    App.finalreview.load();
  });
};

App.finalreview.openSlideshow = function(idx) {
  const ss = App.finalreview._slideshow;
  ss.idx = idx;
  App.finalreview._showSlide();
  $('carousel-lightbox').style.display = 'flex';
};

App.finalreview._showSlide = function() {
  const ss = App.finalreview._slideshow;
  const slide = ss.slides[ss.idx];
  if (!slide) return;
  $('cs-lightbox-img').src     = '/media/' + slide.media_url;
  $('cs-lightbox-counter').textContent = `${ss.idx+1} / ${ss.slides.length}`;
};

App.finalreview.slideshowNav = function(dir) {
  const ss = App.finalreview._slideshow;
  ss.idx = (ss.idx + dir + ss.slides.length) % ss.slides.length;
  App.finalreview._showSlide();
};

App.finalreview.closeSlideshow = function() {
  $('carousel-lightbox').style.display = 'none';
};

App.finalreview.slideshowBgClick = function(e) {
  if (e.target === $('carousel-lightbox')) App.finalreview.closeSlideshow();
};

// ──────────────────────────────────────────────────────────────── Corpus Browser
App.corpus = {
  _view: 'upcoming',
  _data: null,
};

App.corpus.load = function() {
  api('/api/corpus').then(d => {
    App.corpus._data = d;
    $('corpus-count-upcoming').textContent    = d.upcoming.length    ? `(${d.upcoming.length})`    : '';
    $('corpus-count-used').textContent        = d.used.length        ? `(${d.used.length})`        : '';
    $('corpus-count-quarantined').textContent = d.quarantined.length ? `(${d.quarantined.length})` : '';
    App.corpus.setView(App.corpus._view);
  });
};

App.corpus.setView = function(view) {
  App.corpus._view = view;
  ['upcoming','used','quarantined'].forEach(v => {
    $('corpus-tab-' + v)?.classList.toggle('active', v === view);
  });
  if (App.corpus._data) App.corpus._renderList();
};

App.corpus._renderList = function() {
  const d    = App.corpus._data;
  const view = App.corpus._view;
  const items = d[view] || [];
  const el = $('corpus-list');
  if (!items.length) {
    el.innerHTML = `<div class="detail-empty">No concepts in "${view}".</div>`;
    return;
  }
  el.innerHTML = `<div class="corpus-grid">` + items.map((c, i) => {
    const fmtUsed  = c.formats_used.length ? c.formats_used.join(' → ') : 'none';
    const nextFmt  = view === 'upcoming' ? `<span class="next-format-badge">${c.next_format}</span>` : '';
    const qReason  = c.quarantine_reason ? `<div class="corpus-qreason">Reason: ${c.quarantine_reason}</div>` : '';
    const lastUsed = c.last_used ? `<span class="corpus-date">${c.last_used.slice(0,10)}</span>` : '';
    const prov     = c.provisional ? '<span class="prov-badge">provisional</span>' : '';
    return `<div class="corpus-row ${view === 'quarantined' ? 'corpus-quarantined' : ''}">
      <div class="corpus-rank">${i+1}</div>
      <div class="corpus-body">
        <div class="corpus-name">${c.name} ${prov}</div>
        <div class="corpus-id">${c.concept_id}</div>
        <div class="corpus-meta">
          ${nextFmt}
          <span class="corpus-used-label">Used: ${fmtUsed}</span>
          ${lastUsed}
          ${qReason}
        </div>
      </div>
    </div>`;
  }).join('') + `</div>`;
};

App.corpus.openAdd = function() {
  ['cf-name','cf-definition','cf-mechanism','cf-visual','cf-s1','cf-s2','cf-s3',
   'cf-src-author','cf-src-year','cf-src-work','cf-src-url'].forEach(id => {
    const el = $(id); if (el) el.value = '';
  });
  $('concept-add-error').textContent = '';
  $('concept-add-btn').disabled = false;
  $('concept-modal-backdrop').style.display = '';
  $('concept-modal').style.display = '';
  setTimeout(() => $('cf-name')?.focus(), 50);
};

App.corpus.closeAdd = function() {
  $('concept-modal-backdrop').style.display = 'none';
  $('concept-modal').style.display = 'none';
};

App.corpus.submitAdd = function() {
  const name       = ($('cf-name')?.value || '').trim();
  const definition = ($('cf-definition')?.value || '').trim();
  if (!name || !definition) {
    $('concept-add-error').textContent = 'Name and definition are required.';
    return;
  }
  $('concept-add-btn').disabled = true;
  $('concept-add-error').textContent = '';

  api('/api/corpus/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      canonical_name:  name,
      definition:      definition,
      mechanism:       $('cf-mechanism')?.value || '',
      visual_concepts: $('cf-visual')?.value    || '',
      scenario_1:      $('cf-s1')?.value        || '',
      scenario_2:      $('cf-s2')?.value        || '',
      scenario_3:      $('cf-s3')?.value        || '',
      source_author:   $('cf-src-author')?.value || '',
      source_work:     $('cf-src-work')?.value   || '',
      source_year:     $('cf-src-year')?.value   || '',
      source_url:      $('cf-src-url')?.value    || '',
    }),
  }).then(d => {
    if (d.error) {
      $('concept-add-error').textContent = d.error;
      $('concept-add-btn').disabled = false;
      return;
    }
    App.corpus.closeAdd();
    showToast(`Added: ${name}`);
    App.corpus.load();
  }).catch(() => {
    $('concept-add-error').textContent = 'Request failed — try again.';
    $('concept-add-btn').disabled = false;
  });
};

// ──────────────────────────────────────────────────────────────── Intelligence
App.intelligence = {};
App.intelligence.load = function() {
  api('/api/intelligence').then(d => {
    function chips(id, items) {
      const el = $(id);
      if (!el) return;
      el.innerHTML = items.map(k => `<span class="chip">${k}</span>`).join('');
    }
    chips('intel-keywords-high', d.high_keywords || []);
    chips('intel-keywords-low',  d.low_keywords  || []);
    chips('intel-tags-high',     d.high_tags     || []);
    chips('intel-tags-low',      d.low_tags      || []);
    $('intel-keywords-high-count').textContent = (d.high_keywords||[]).length;
    $('intel-keywords-low-count').textContent  = (d.low_keywords||[]).length;
    $('intel-tags-high-count').textContent     = (d.high_tags||[]).length;
    $('intel-tags-low-count').textContent      = (d.low_tags||[]).length;

    const foot = $('intel-foot');
    if (foot) {
      foot.textContent = `${d.rejected_count || 0} rejected source IDs on blocklist`;
    }
  });
};

// ──────────────────────────────────────────────────────────────── Toast CSS (inline fallback)
(function injectToastStyles() {
  const s = document.createElement('style');
  s.textContent = `
.toast { position:fixed; bottom:24px; right:24px; padding:12px 20px; border-radius:8px;
  font:500 13px Inter,sans-serif; z-index:9999; animation:fadeInUp .2s; }
.toast-ok  { background:#2e7d32; color:#fff; }
.toast-err { background:#c62828; color:#fff; }
@keyframes fadeInUp { from { opacity:0; transform:translateY(8px); } }
.audio-card { padding:12px; border:1px solid var(--border); border-radius:8px; margin-bottom:8px; }
.audio-title { font-weight:500; font-size:13px; margin-top:6px; }
.audio-meta  { font-size:12px; color:var(--muted); margin-top:2px; }
.fr-detail { padding:8px 0; }
.fr-detail-title { font:italic 600 22px var(--serif); color:var(--text); margin:12px 0 8px; }
.fr-reel-variant { margin:12px 0; padding:12px; border:1px solid var(--border); border-radius:8px; }
.fr-reel-variant.fr-chosen { border-color:var(--sage); background:var(--sage-s); }
.fr-variant-meta { font-size:12px; color:var(--muted); margin:6px 0; }
.fr-slides-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:8px; margin:12px 0; }
.fr-slide-thumb { width:100%; aspect-ratio:1.35; object-fit:cover; border-radius:6px; cursor:pointer; }
.fr-caption-box { margin:12px 0; padding:12px; background:var(--card2); border-radius:8px; }
.fr-caption-box pre { white-space:pre-wrap; font:13px var(--mono); color:var(--text2); }
.fr-verdict-bar { display:flex; gap:10px; margin:16px 0; }
.fr-current-verdict { font-size:13px; color:var(--muted); margin-top:4px; }
.verdict-post { border-left:3px solid #2e7d32 !important; }
.verdict-nopost { border-left:3px solid #c62828 !important; }
.fr-card { padding:14px 16px; border:1px solid var(--border); border-radius:10px; margin-bottom:10px; cursor:pointer; transition:border-color .15s; }
.fr-card:hover { border-color:var(--sage); }
.fr-card-head { display:flex; align-items:center; gap:10px; margin-bottom:4px; }
.fr-card-name { font-weight:500; font-size:13px; }
.fr-card-format { font-size:11px; background:var(--sage-s); color:var(--sage-d); padding:2px 8px; border-radius:999px; }
.fr-concept-tag { font-size:11px; background:var(--terra-s); color:var(--terra); padding:2px 8px; border-radius:999px; }
.fr-card-caption { font-size:12px; color:var(--muted); margin-bottom:8px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.fr-card-verdict { font-size:12px; font-weight:500; }
.corpus-grid { display:flex; flex-direction:column; gap:1px; }
.corpus-row { display:flex; gap:12px; align-items:flex-start; padding:12px 16px; border:1px solid var(--border); border-radius:8px; margin-bottom:8px; }
.corpus-row.corpus-quarantined { border-color:#c62828; background:#fff5f5; }
.corpus-rank { width:28px; font:400 12px var(--mono); color:var(--muted); padding-top:2px; }
.corpus-name { font-weight:500; font-size:14px; }
.corpus-id   { font:400 11px var(--mono); color:var(--muted); margin-top:2px; }
.corpus-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:6px; font-size:12px; }
.corpus-used-label { color:var(--muted); }
.corpus-date { color:var(--muted); font:400 11px var(--mono); }
.corpus-qreason { color:#c62828; font-size:12px; width:100%; }
.next-format-badge { background:var(--sage-s); color:var(--sage-d); padding:2px 8px; border-radius:999px; font-size:11px; font-weight:500; }
.prov-badge { font-size:10px; background:var(--sand-s); color:var(--sand); padding:1px 6px; border-radius:999px; vertical-align:middle; }
.concept-tag { background:var(--terra-s); color:var(--terra); padding:2px 8px; border-radius:999px; font-size:11px; }
.tier-priority { color:var(--sage); font-weight:600; }
.tier-standard { color:var(--muted); }
.detail-card { display:grid; grid-template-columns:1fr 320px; gap:24px; padding:16px 0; }
.detail-media video { border-radius:8px; }
.detail-title { font:italic 500 18px var(--serif); margin-bottom:10px; }
.detail-meta { display:flex; flex-direction:column; gap:6px; margin-bottom:14px; }
.detail-meta-row { display:flex; gap:8px; font-size:13px; }
.meta-label { color:var(--muted); min-width:68px; }
.meta-val { color:var(--text); }
.detail-progress { font-size:12px; color:var(--muted); margin-bottom:12px; }
.detail-actions { display:flex; gap:8px; }
.detail-empty { padding:48px; text-align:center; color:var(--muted); }
@media (max-width:700px) { .detail-card { grid-template-columns:1fr; } }`;
  document.head.appendChild(s);
})();

// ──────────────────────────────────────────────────────────────── Init
window.addEventListener('DOMContentLoaded', () => {
  api('/api/status').then(applyStatus);
  loadStats();
  initSSE();
});
