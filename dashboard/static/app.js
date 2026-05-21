/* app.js — Healing Agent Dashboard */

const STEP_META = {
  step1:  { name: 'Climate',  sub: 'mood seed'   },
  step3:  { name: 'Keywords', sub: 'expand'       },
  step3b: { name: 'Scripts',  sub: 'gen quotes'   },
  step4:  { name: 'Footage',  sub: 'video pool'   },
  step4b: { name: 'Images',   sub: 'photo pool'   },
  step5:  { name: 'Audio',    sub: 'ambient'      },
  step6:  { name: 'Score',    sub: 'rank'         },
  step6b: { name: 'Score img',sub: 'rank photos'  },
  step7:  { name: 'Download', sub: 'persist'      },
  step8:  { name: 'Summary',  sub: 'write logs'   },
  carousel:{ name: 'Carousel',sub: 'render slides'},
  story:  { name: 'Story',    sub: 'render posts' },
  reel:   { name: 'Reel',     sub: 'assemble'     },
};

const FULL_STEPS   = ['step1','step3','step3b','step4','step4b','step5','step6','step6b','step7','step8'];
const QUICK_STEPS  = ['step1','step3','step3b'];
const EDITOR_STEPS = ['carousel','story','reel'];

const EDITOR_CMDS  = new Set(['carousel','story','reel']);

// ─────────────────────────────────────────────────────────
const App = {
  status:        {},
  logLines:      [],
  logOpen:       false,
  currentStage:  'pipeline',
  elapsedTimer:  null,

  // ── Init ─────────────────────────────────────────────
  init() {
    _buildStepChips('steps-full', FULL_STEPS);
    _buildStepChips('steps-editor', EDITOR_STEPS);
    this._connectSSE();
    this._loadOverview();
    document.addEventListener('keydown', e => this._onKey(e));
  },

  // ── Navigation ───────────────────────────────────────
  nav(stage) {
    document.querySelectorAll('audio').forEach(a => { a.pause(); a.currentTime = 0; });
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('stage-' + stage).classList.add('active');
    document.querySelector(`[data-stage="${stage}"]`).classList.add('active');
    this.currentStage = stage;

    if (stage === 'footage')      this.footage.load();
    if (stage === 'render')       this.poolWidget.load();
    if (stage === 'finalreview')  this.finalreview.load();
    if (stage === 'timeline')     this.timeline.load();
    if (stage === 'intelligence') this.intelligence.load();
  },

  // ── Run / Stop ───────────────────────────────────────
  async run(command) {
    const res  = await fetch('/api/run/' + command, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) { alert('Cannot start: ' + (data.error || res.status)); return; }

    // Clear step UI
    const isEditor = EDITOR_CMDS.has(command);
    const steps    = isEditor ? EDITOR_STEPS
                   : command === 'agent_quick' ? QUICK_STEPS : FULL_STEPS;
    const containerId = isEditor ? 'steps-editor' : 'steps-full';
    steps.forEach(k => _setChip(containerId, k, ''));

    if (isEditor) {
      document.getElementById('steps-editor').style.display = 'flex';
    }

    this.logLines = [];
    document.getElementById('log-lines').innerHTML = '';
    document.getElementById('log-count').textContent = '0 lines';
    if (!this.logOpen) this.toggleLog();

    // Navigate to the right stage
    if (isEditor) this.nav('render');
  },

  async stop() {
    await fetch('/api/stop', { method: 'POST' });
  },

  // ── SSE ──────────────────────────────────────────────
  _connectSSE() {
    const es = new EventSource('/api/events');
    es.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'status') this._applyStatus(msg.payload);
      if (msg.type === 'log')    this._appendLog(msg.line);
    };
    es.onerror = () => setTimeout(() => this._connectSSE(), 3000);
  },

  _applyStatus(s) {
    this.status = s;

    const pill    = document.getElementById('status-pill');
    const dot     = document.getElementById('status-dot');
    const text    = document.getElementById('status-text');
    const btnStop = document.getElementById('btn-stop');
    const allRunBtns = document.querySelectorAll('[id^="btn-run-"]');

    if (s.running) {
      _setClass(pill, 'running');
      _setClass(dot,  'running');
      text.textContent = 'Running · ' + s.running.replace('_', ' ');
      btnStop.disabled = false;
      allRunBtns.forEach(b => b.disabled = true);
      this._startElapsed(s.elapsed_s);
      document.getElementById('log-run-label').textContent = 'run_' + _today();
    } else {
      const code = s.last_exit_code;
      if (code === null || code === undefined) {
        _setClass(pill, 'idle'); _setClass(dot, 'idle');
        text.textContent = 'No run in progress';
      } else if (code === 0) {
        _setClass(pill, 'done'); _setClass(dot, 'done');
        text.textContent = 'Last run completed ✓';
        this._showRecap(s);
        this._loadOverview();
      } else {
        _setClass(pill, 'error'); _setClass(dot, 'error');
        text.textContent = 'Run failed · exit ' + code;
      }
      btnStop.disabled = true;
      allRunBtns.forEach(b => b.disabled = false);
      this._stopElapsed();
      document.getElementById('elapsed').textContent = '';
    }

    this._updateStepChips(s);
  },

  _showRecap(s) {
    // Only show after an agent run, not editors
    const cmd = s.last_command || '';
    if (EDITOR_CMDS.has(cmd)) return;

    const recap = document.getElementById('last-run-recap');
    recap.style.display = 'block';
    document.getElementById('recap-label').textContent = 'Last run · ' + _today();
    const pill = document.getElementById('recap-pill');
    pill.className = s.last_exit_code === 0 ? 'pill done' : 'pill error';
    pill.textContent = s.last_exit_code === 0 ? 'complete' : 'error ' + s.last_exit_code;
    // Nums will be populated from stats next load
  },

  _startElapsed(initial) {
    if (this.elapsedTimer) clearInterval(this.elapsedTimer);
    const start = Date.now() - (initial || 0) * 1000;
    const el    = document.getElementById('elapsed');
    const live  = document.getElementById('step-live-label');
    const tick  = () => {
      const sec = Math.round((Date.now() - start) / 1000);
      const m   = Math.floor(sec / 60);
      const s2  = sec % 60;
      el.textContent = ' · ' + (m > 0 ? `${m}m ${s2}s` : `${s2}s`);
    };
    tick();
    this.elapsedTimer = setInterval(tick, 1000);
  },

  _stopElapsed() {
    if (this.elapsedTimer) { clearInterval(this.elapsedTimer); this.elapsedTimer = null; }
  },

  _updateStepChips(s) {
    const cmd    = s.running || s.last_command || '';
    const status = s.step_status || {};
    const cur    = s.current_step || '';

    const isEditor = EDITOR_CMDS.has(cmd);
    const editorEl = document.getElementById('steps-editor');

    if (isEditor) {
      editorEl.style.display = 'flex';
      EDITOR_STEPS.forEach(k => _setChip('steps-editor', k, status[k] || ''));
    } else {
      editorEl.style.display = 'none';
      const steps = cmd === 'agent_quick' ? QUICK_STEPS : FULL_STEPS;
      steps.forEach(k => _setChip('steps-full', k, status[k] || ''));

      // Live label
      const liveEl = document.getElementById('step-live-label');
      if (s.running && cur) {
        const idx   = FULL_STEPS.indexOf(cur) + 1;
        const total = steps.length;
        liveEl.innerHTML = `<span style="color:#7a5b32">● ${STEP_META[cur]?.name || cur}</span> · step ${idx} of ${total}`;
      } else {
        liveEl.textContent = '';
      }
    }
  },

  // ── Overview strip ───────────────────────────────────
  async _loadOverview() {
    try {
      const res  = await fetch('/api/stats');
      const data = await res.json();
      const p = data.pool;
      const footage = (p.footage.priority || 0) + (p.footage.standard || 0);
      const audio   = (p.audio.priority   || 0) + (p.audio.standard   || 0);
      const images  = (p.images.priority  || 0) + (p.images.standard  || 0);
      const ready   = Object.values(data.final_review.ready_to_post || {}).reduce((a,b)=>a+b,0);

      _setText('ov-footage', footage || '—');
      _setText('ov-audio',   audio   || '—');
      _setText('ov-images',  images  || '—');
      _setText('ov-ready',   ready   || '—');

      // Rail widget
      _setText('rail-batch-value', images || '—');
      document.getElementById('rail-batch-fill').style.width =
        images ? Math.min(100, (images / 50) * 100) + '%' : '0%';
      _setText('rail-batch-sub', ready + ' ready to post');

      // Batch label in topbar
      const batches = data.batches || 0;
      document.getElementById('batch-label').textContent =
        batches ? 'batch · ' + _today() : '';

    } catch(e) { /* offline */ }
  },

  // ── Log ──────────────────────────────────────────────
  toggleLog() {
    this.logOpen = !this.logOpen;
    document.getElementById('log-drawer').classList.toggle('open', this.logOpen);
    document.getElementById('btn-log').classList.toggle('active', this.logOpen);
    document.getElementById('btn-log-label').textContent = this.logOpen ? 'Hide log' : 'Show log';
    if (this.logOpen) _scrollLog();
  },

  _appendLog(raw) {
    this.logLines.push(raw);
    const el  = document.getElementById('log-lines');
    const row = document.createElement('div');
    row.className = 'log-row';

    // Try to parse a timestamp if the line begins with HH:MM:SS
    const tsMatch = raw.match(/^(\d{2}:\d{2}:\d{2})\s+(.*)/s);
    const ts      = tsMatch ? tsMatch[1] : '';
    const body    = tsMatch ? tsMatch[2] : raw;

    // Step tag coloring
    const tagMatch = body.match(/^(\[step\d+[b]?\]|\[carousel\]|\[story\]|\[reel\])/i);
    const tag      = tagMatch ? tagMatch[1] : '';
    const rest     = tagMatch ? body.slice(tag.length) : body;

    const isOk   = /ok|done|complete/i.test(rest) && !/error/i.test(rest);
    const isWarn = /warn/i.test(rest);
    const isErr  = /error|exception|traceback|failed/i.test(rest);
    const tagCls = tag ? (isErr ? 'log-tag-error' : isWarn ? 'log-tag-warn' : 'log-tag-step') : '';

    row.innerHTML =
      `<span class="log-ts">${ts}</span>` +
      `<span class="log-tag ${tagCls}">${tag}</span>` +
      `<span class="log-msg" style="${isErr?'color:#c27264':isWarn?'color:var(--sand)':''}">${_esc(rest)}</span>`;

    // Blinking cursor on last line
    const prev = el.querySelector('.log-cursor');
    if (prev) prev.remove();
    const cursor = document.createElement('span');
    cursor.className = 'log-cursor';
    row.querySelector('.log-msg').appendChild(cursor);

    el.appendChild(row);
    document.getElementById('log-count').textContent = this.logLines.length + ' lines';

    const threshold = 80;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < threshold) {
      el.scrollTop = el.scrollHeight;
    }
  },

  // ── Keyboard ─────────────────────────────────────────
  _onKey(e) {
    if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) return;

    // Carousel slideshow lightbox takes highest keyboard priority
    if (this.finalreview.slideshowIdx >= 0) {
      if (e.key === 'ArrowRight') { this.finalreview.slideshowNav(1);   return; }
      if (e.key === 'ArrowLeft')  { this.finalreview.slideshowNav(-1);  return; }
      if (e.key === 'Escape')     { this.finalreview.closeSlideshow();  return; }
      return;
    }

    // Pool inventory lightbox (render stage)
    if (this.poolWidget.isLbOpen()) {
      if (e.key === 'ArrowRight') { this.poolWidget.navLb(1);   return; }
      if (e.key === 'ArrowLeft')  { this.poolWidget.navLb(-1);  return; }
      if (e.key === 'Escape')     { this.poolWidget.closeLb();  return; }
      return;
    }

    // Image pool lightbox
    if (this.images.lightboxIdx >= 0) {
      if (e.key === 'ArrowRight') { this.images.lightboxNav(1);   return; }
      if (e.key === 'ArrowLeft')  { this.images.lightboxNav(-1);  return; }
      if (e.key === 'Escape')     { this.images.closeLightbox();  return; }
      return;
    }

    if (this.currentStage === 'footage' && this.footage.activeType !== 'images') {
      if (e.key === 'k' || e.key === 'K')          { this.footage.rateAndAdvance('g'); return; }
      if (e.key === 'r' || e.key === 'R')          { this.footage.rateAndAdvance('b'); return; }
      if (e.key === 's' || e.key === 'S')          { this.footage.advance(1);          return; }
      if (e.key === 'ArrowRight')                   { this.footage.advance(1);          return; }
      if (e.key === 'ArrowLeft')                    { this.footage.advance(-1);         return; }
    }
  },

  // ─────────────────────────────────────────────────────
  // Sort media — footage / audio / images tabs
  // ─────────────────────────────────────────────────────
  footage: {
    allItems:   [],   // footage + audio from /api/media
    index:      0,
    activeType: 'footage',   // 'footage' | 'audio' | 'images'

    async load() {
      const res    = await fetch('/api/media');
      this.allItems = await res.json();
      this._updateTabCounts();
      this.setType(this.activeType, /*skipReload=*/true);
    },

    setType(type, skipReload) {
      document.querySelectorAll('audio').forEach(a => { a.pause(); a.currentTime = 0; });
      this.activeType = type;

      // Tab highlight
      ['footage','audio','images'].forEach(t => {
        const el = document.getElementById('tab-' + t);
        if (el) el.classList.toggle('active', t === type);
      });

      // Show/hide panels
      const reviewerEl  = document.getElementById('footage-reviewer');
      const progressEl  = document.getElementById('footage-progress-wrap');
      const imagesPanel = document.getElementById('images-tab-panel');

      if (type === 'images') {
        if (reviewerEl)  reviewerEl.style.display  = 'none';
        if (progressEl)  progressEl.style.display  = 'none';
        if (imagesPanel) imagesPanel.style.display  = '';
        document.getElementById('btn-footage-commit').textContent = 'Reject marked →';
        if (!skipReload) App.images.load();
        else             App.images._render();
        this._renderCommitBtn();
      } else {
        if (reviewerEl)  reviewerEl.style.display   = '';
        if (progressEl)  progressEl.style.display   = '';
        if (imagesPanel) imagesPanel.style.display   = 'none';
        document.getElementById('btn-footage-commit').textContent = 'Commit →';
        // Reset to first unreviewed item of this type
        const items = this._typed();
        this.index  = 0;
        const first = items.findIndex(i => !i.rating);
        if (first > 0) this.index = first;
        this._renderProgress();
        this._renderItem();
        this._renderCommitBtn();
      }
    },

    _typed() {
      return this.allItems.filter(i => i.type === this.activeType);
    },

    _updateTabCounts() {
      const all = this.allItems;
      const footagePending = all.filter(i => i.type === 'footage' && !i.rating).length;
      const audioPending   = all.filter(i => i.type === 'audio'   && !i.rating).length;
      _setText('tab-count-footage', footagePending ? footagePending + ' left' : '');
      _setText('tab-count-audio',   audioPending   ? audioPending   + ' left' : '');
    },

    _render() {
      this._renderProgress();
      this._renderItem();
      this._renderCommitBtn();
    },

    _renderProgress() {
      const items   = this._typed();
      const total   = items.length;
      const kept    = items.filter(i => i.rating === 'g').length;
      const rej     = items.filter(i => i.rating === 'b').length;
      const pending = total - kept - rej;

      const kw = total ? (kept / total * 100).toFixed(1) : 0;
      const rw = total ? (rej  / total * 100).toFixed(1) : 0;
      document.getElementById('prog-kept').style.width = kw + '%';
      document.getElementById('prog-rej').style.width  = rw + '%';

      const lbl = document.getElementById('footage-prog-label');
      if (!total) {
        lbl.textContent = 'No ' + this.activeType + ' yet — run the agent first';
        return;
      }
      lbl.innerHTML =
        `Not reviewed yet: <b>${pending}</b>  ·  kept <b style="color:var(--sage-d)">${kept}</b>  ·  rejected <b style="color:var(--terra)">${rej}</b>`;
    },

    _renderItem() {
      const el    = document.getElementById('footage-reviewer');
      const items = this._typed();

      if (!items.length) {
        el.innerHTML = `<div class="detail-empty" style="grid-column:1/-1">No ${this.activeType} yet — run the agent first.</div>`;
        return;
      }

      const item = items[this.index];
      if (!item) {
        const kept = items.filter(i => i.rating === 'g').length;
        const rej  = items.filter(i => i.rating === 'b').length;
        el.innerHTML = `
          <div class="review-done-card">
            <div class="review-done-icon">✓</div>
            <div class="review-done-title">Review complete</div>
            <div class="review-done-stats">${kept} kept &nbsp;·&nbsp; ${rej} rejected &nbsp;·&nbsp; ${items.length} total</div>
            <button class="btn btn-outline btn-sm" onclick="App.footage._confirmRestart()">Start over?</button>
          </div>`;
        return;
      }

      const isVideo = item.type === 'footage';
      const ratedG  = item.rating === 'g';
      const ratedB  = item.rating === 'b';

      const mediaHtml = isVideo
        ? `<div class="media-area media-video">
             <video controls preload="metadata" src="/media/${item.media_url}"></video>
           </div>`
        : `<div class="media-area media-audio">
             <div class="audio-card">
               <div class="audio-art">
                 <svg width="52" height="52" viewBox="0 0 24 24" fill="none"
                      stroke="#C9A77C" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
                   <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
                 </svg>
               </div>
               <audio id="aud-${this.index}" src="/media/${item.media_url}" preload="metadata"></audio>
               <div class="audio-controls-row">
                 <button class="audio-play-btn" id="aud-btn-${this.index}"
                         onclick="App.footage._audioToggle(${this.index})">
                   ${_playSvg()}
                 </button>
                 <div class="audio-progress-wrap"
                      onclick="App.footage._audioSeek(event,${this.index})">
                   <div class="audio-progress-fill" id="aud-prog-${this.index}"></div>
                 </div>
                 <span class="audio-time" id="aud-time-${this.index}">0:00</span>
               </div>
             </div>
           </div>`;

      el.innerHTML = `
        <div class="review-player">
          ${mediaHtml}
          <div class="review-actions">
            <button class="btn ${ratedG?'btn-sage':'btn-outline'} btn-lg"
                    onclick="App.footage.rateAndAdvance('g')">✓ Keep</button>
            <button class="btn ${ratedB?'btn-danger':'btn-outline'} btn-lg"
                    onclick="App.footage.rateAndAdvance('b')">✕ Reject</button>
            <button class="btn btn-ghost btn-lg"
                    onclick="App.footage.advance(1)">↷ Skip</button>
          </div>
          <div class="review-nav-row">
            <button class="btn btn-ghost btn-sm" ${this.index===0?'disabled':''}
                    onclick="App.footage.advance(-1)">← Prev</button>
            <span class="review-nav-pos">${this.index + 1} of ${items.length}</span>
            <button class="btn btn-ghost btn-sm" ${this.index>=items.length-1?'disabled':''}
                    onclick="App.footage.advance(1)">Next →</button>
          </div>
          <div class="kb-hints">
            <span class="kb-key">K</span> keep ·
            <span class="kb-key">R</span> reject ·
            <span class="kb-key">S</span> skip  ·
            <span class="kb-key">←</span><span class="kb-key">→</span> navigate
          </div>
        </div>

        <div class="review-meta-card">
          <div class="meta-header">
            <div>
              <div class="meta-clip-label">
                ${isVideo ? 'Clip' : 'Track'} ${this.index + 1} of ${items.length}
              </div>
              <div class="meta-clip-title">${_esc(item.title || item.filename)}</div>
            </div>
            <span class="meta-source-pill">${_esc(item.source)}</span>
          </div>

          <div class="meta-grid">
            <div>
              <div class="meta-field-k">score</div>
              <div class="meta-field-v"><span class="meta-score">${item.score}</span></div>
            </div>
            <div>
              <div class="meta-field-k">tier</div>
              <div class="meta-field-v">${item.tier}</div>
            </div>
            <div>
              <div class="meta-field-k">keyword</div>
              <div class="meta-field-v">${_esc(item.keyword || '—')}</div>
            </div>
            <div>
              <div class="meta-field-k">file</div>
              <div class="meta-field-v" style="font:400 11px var(--mono);word-break:break-all">${_esc(item.filename)}</div>
            </div>
          </div>

          ${item.url ? `<div class="meta-field-k" style="margin-bottom:6px">source url</div>
            <div class="meta-url">${_esc(item.url)}</div>` : ''}
        </div>`;

      // Autoplay video / wire audio after DOM is ready
      if (isVideo) {
        setTimeout(() => {
          const vid = el.querySelector('video');
          if (vid) vid.play().catch(() => {});
        }, 0);
      }
      if (!isVideo) {
        const idx = this.index;
        setTimeout(() => {
          const aud = document.getElementById('aud-' + idx);
          if (!aud) return;
          aud.play().catch(() => {});
          aud.addEventListener('timeupdate', () => {
            const pct  = aud.duration ? aud.currentTime / aud.duration * 100 : 0;
            const prog = document.getElementById('aud-prog-' + idx);
            const time = document.getElementById('aud-time-' + idx);
            if (prog) prog.style.width = pct + '%';
            if (time) time.textContent = _fmtTime(aud.currentTime);
          });
          aud.addEventListener('ended', () => {
            const btn = document.getElementById('aud-btn-' + idx);
            if (btn) btn.innerHTML = _playSvg();
          });
        }, 0);
      }
    },

    _audioToggle(idx) {
      const aud = document.getElementById('aud-' + idx);
      const btn = document.getElementById('aud-btn-' + idx);
      if (!aud || !btn) return;
      if (aud.paused) { aud.play(); btn.innerHTML = _pauseSvg(); }
      else            { aud.pause(); btn.innerHTML = _playSvg(); }
    },

    _audioSeek(e, idx) {
      const aud = document.getElementById('aud-' + idx);
      if (!aud || !aud.duration) return;
      aud.currentTime = (e.offsetX / e.currentTarget.clientWidth) * aud.duration;
    },

    _stopAudio() {
      const aud = document.getElementById('aud-' + this.index);
      if (aud) { aud.pause(); aud.currentTime = 0; }
    },

    _renderCommitBtn() {
      const btn = document.getElementById('btn-footage-commit');
      if (!btn) return;
      if (this.activeType === 'images') {
        const bad = App.images.items.filter(i => i.rating === 'b').length;
        btn.disabled = !bad;
      } else {
        const items = this._typed();
        const kept  = items.filter(i => i.rating === 'g').length;
        const rej   = items.filter(i => i.rating === 'b').length;
        btn.disabled = !(kept || rej);
      }
    },

    _confirmRestart() {
      const items = this._typed();
      if (!confirm(`All ${items.length} items already reviewed. Reset to beginning?`)) return;
      this.index = 0;
      this._renderItem();
      this._renderProgress();
    },

    async rateAndAdvance(rating) {
      const items = this._typed();
      const item  = items[this.index];
      if (!item) return;
      const newRating = item.rating === rating ? '' : rating;
      await fetch('/api/media/rate', {
        method:  'POST',
        headers: {'Content-Type':'application/json'},
        body:    JSON.stringify({ filename: item.filename, rating: newRating }),
      });
      // Update in allItems too so tab counts stay accurate
      const master = this.allItems.find(i => i.filename === item.filename && i.batch === item.batch);
      if (master) master.rating = newRating;
      item.rating = newRating;
      this._updateTabCounts();
      if (newRating) this.advance(1);
      else this._render();
    },

    advance(delta) {
      const items  = this._typed();
      const newIdx = this.index + delta;
      if (newIdx < 0 || newIdx >= items.length) return;
      this._stopAudio();
      this.index = newIdx;
      this._renderItem();
      this._renderProgress();
    },

    async commit() {
      if (this.activeType === 'images') {
        await App.images.commit();
        this._renderCommitBtn();
        return;
      }
      const items = this._typed();
      const kept  = items.filter(i => i.rating === 'g').length;
      const rej   = items.filter(i => i.rating === 'b').length;
      if (!kept && !rej) { alert('No items rated yet.'); return; }
      if (!confirm(`Move ${kept} → pool/  and  ${rej} → bad/ ? Cannot be undone.`)) return;
      const res  = await fetch('/api/media/commit', { method: 'POST' });
      const data = await res.json();
      alert(`Done — ${data.moved_good} → pool · ${data.moved_bad} → bad`);
      this.load();
      App._loadOverview();
    },
  },

  // ─────────────────────────────────────────────────────
  // Image review
  // ─────────────────────────────────────────────────────
  images: {
    items:       [],
    lightboxIdx: -1,

    async load() {
      const res  = await fetch('/api/images');
      this.items = await res.json();
      this._render();
    },

    _render() {
      const el = document.getElementById('images-grid');
      _setText('tab-count-images', this.items.length ? this.items.length + ' items' : '');
      App.footage._renderCommitBtn();

      if (!this.items.length) {
        el.innerHTML = '<div class="empty" style="grid-column:1/-1">No images in pool yet — run the agent first.</div>';
        return;
      }
      el.innerHTML = this.items.map((item, idx) => {
        const cls = item.rating === 'g' ? 'rated-g' : item.rating === 'b' ? 'rated-b' : '';
        return `
          <div class="image-card ${cls}">
            <img src="/media/${item.media_url}" loading="lazy" alt="${_esc(item.title)}"
                 onclick="App.images.openLightbox(${idx})">
            <div class="image-card-footer">
              <span class="image-card-title" title="${_esc(item.title)}">${_esc(item.keyword || item.title)}</span>
              <button class="btn ${item.rating==='g'?'btn-sage':'btn-outline'} btn-sm"
                      style="padding:2px 8px;font-size:11px"
                      onclick="event.stopPropagation();App.images.rate('${_esc(item.filename)}','g')">✓</button>
              <button class="btn ${item.rating==='b'?'btn-danger':'btn-outline'} btn-sm"
                      style="padding:2px 8px;font-size:11px"
                      onclick="event.stopPropagation();App.images.rate('${_esc(item.filename)}','b')">✗</button>
            </div>
          </div>`;
      }).join('');
    },

    openLightbox(idx) {
      this.lightboxIdx = idx;
      this._showLightbox();
    },

    _showLightbox() {
      const item = this.items[this.lightboxIdx];
      if (!item) return;
      document.getElementById('lightbox-img').src = '/media/' + item.media_url;
      document.getElementById('lightbox-counter').textContent =
        (this.lightboxIdx + 1) + ' / ' + this.items.length;
      document.getElementById('lightbox-prev').disabled = this.lightboxIdx === 0;
      document.getElementById('lightbox-next').disabled = this.lightboxIdx >= this.items.length - 1;
      const gBtn = document.getElementById('lightbox-rate-g');
      const bBtn = document.getElementById('lightbox-rate-b');
      if (gBtn) gBtn.classList.toggle('active', item.rating === 'g');
      if (bBtn) bBtn.classList.toggle('active', item.rating === 'b');
      document.getElementById('img-lightbox').style.display = 'flex';
      document.body.style.overflow = 'hidden';
    },

    async rateInLightbox(rating) {
      const item = this.items[this.lightboxIdx];
      if (!item) return;
      await this.rate(item.filename, rating);
      // rate() calls _render() which redraws the grid; restore lightbox state
      this._showLightbox();
    },

    closeLightbox() {
      document.getElementById('img-lightbox').style.display = 'none';
      document.body.style.overflow = '';
      this.lightboxIdx = -1;
    },

    lightboxBgClick(e) {
      if (e.target === e.currentTarget) this.closeLightbox();
    },

    lightboxNav(delta) {
      const n = this.lightboxIdx + delta;
      if (n < 0 || n >= this.items.length) return;
      this.lightboxIdx = n;
      this._showLightbox();
    },

    async rate(filename, rating) {
      const item = this.items.find(i => i.filename === filename);
      if (!item) return;
      const newRating = item.rating === rating ? '' : rating;
      await fetch('/api/images/rate', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ filename, rating: newRating }),
      });
      item.rating = newRating;
      this._render();
    },

    async commit() {
      const bad = this.items.filter(i => i.rating === 'b').length;
      if (!bad) { alert('No images marked for rejection.'); return; }
      if (!confirm(`Move ${bad} image(s) to bad/images/ ?`)) return;
      const res  = await fetch('/api/images/commit', {method:'POST'});
      const data = await res.json();
      alert(`Done — ${data.moved_bad} → bad/images/`);
      await this.load();
    },
  },

  // ─────────────────────────────────────────────────────
  // Final review — category tabs, one render at a time
  // ─────────────────────────────────────────────────────
  finalreview: {
    renders:        [],
    activeCategory: 'carousel',
    filterMode:     'all',        // 'all' | 'pending' | 'approved' | 'rejected'
    chosenVariants: {},
    _slidesMap:     {},
    slideshowKey:   null,
    slideshowIdx:   -1,

    async load() {
      const res    = await fetch('/api/renders');
      this.renders = await res.json();
      this._updateTabCounts();
      this.setCategory(this.activeCategory, true);
    },

    setCategory(cat, skipCountUpdate) {
      this.activeCategory = cat;
      this.filterMode = 'all';
      ['carousel','story','reel'].forEach(c => {
        document.getElementById('fr-tab-' + c)?.classList.toggle('active', c === cat);
      });
      if (!skipCountUpdate) this._updateTabCounts();
      this._renderReviewer();
    },

    setFilter(mode) {
      this.filterMode = mode;
      this._renderReviewer();
    },

    _itemMatches(verdict) {
      if (this.filterMode === 'all')      return true;
      if (this.filterMode === 'pending')  return !verdict;
      if (this.filterMode === 'approved') return verdict === 'postable';
      if (this.filterMode === 'rejected') return verdict === 'not_postable';
      return true;
    },

    _filterTabs() {
      const tabs = [
        { id: 'all',      label: 'All' },
        { id: 'pending',  label: 'Pending' },
        { id: 'approved', label: 'Approved' },
        { id: 'rejected', label: 'Not approved' },
      ];
      return '<div class="fr-filter-row">' + tabs.map(t =>
        `<button class="fr-filter-btn ${this.filterMode === t.id ? 'active' : ''}"
                 onclick="App.finalreview.setFilter('${t.id}')">${t.label}</button>`
      ).join('') + '</div>';
    },

    _catItems() {
      return this.renders.filter(r =>
        this.activeCategory === 'story'
          ? (r.format === 'story' || r.format === 'story_minimal')
          : r.format === this.activeCategory
      );
    },

    _updateTabCounts() {
      const all = this.renders;
      const pending = (formats) => all.filter(r => formats.includes(r.format) && r.reviewed < r.total).length;
      _setText('fr-count-carousel', pending(['carousel'])              || '');
      _setText('fr-count-story',    pending(['story','story_minimal']) || '');
      _setText('fr-count-reel',     pending(['reel'])                  || '');
    },

    async _renderReviewer() {
      const el    = document.getElementById('fr-reviewer');
      const items = this._catItems();
      if (!items.length) {
        el.innerHTML = this._filterTabs() +
          '<div class="detail-empty">No ' + this.activeCategory + ' renders yet — run an editor first.</div>';
        return;
      }

      el.innerHTML = this._filterTabs() + '<div class="detail-empty">Loading…</div>';

      const details = await Promise.all(
        items.map(r => fetch('/api/renders/' + r.name).then(res => res.json()))
      );

      const sections = details.map((detail, i) => this._buildDetail(items[i], detail))
                              .filter(Boolean).join('');

      el.innerHTML = this._filterTabs() +
        (sections || '<div class="detail-empty">No items match this filter.</div>');
    },

    _buildDetail(render, detail) {
      let content = '';
      if (detail.format === 'reel')                                       content = this._reelHtml(detail);
      else if (detail.format === 'carousel')                              content = this._carouselHtml(detail);
      else if (detail.format === 'story' || detail.format === 'story_minimal') content = this._storyHtml(detail);
      if (!content) return '';

      const safeN  = render.name.replace(/'/g, "\\'");
      const header = `<div class="fr-render-header">
        <span class="fr-render-name">${_esc(render.name)}</span>
        <span class="fr-progress-label">${render.reviewed} of ${render.total} reviewed</span>
        <button class="fr-delete-btn" onclick="App.finalreview.deleteRender('${safeN}')" title="Delete render from disk">✕ Delete</button>
      </div>`;
      return `<div class="fr-render-section">${header}${content}</div>`;
    },

    // ── Carousel ───────────────────────────────────────
    _carouselHtml(d) {
      if (!d.carousels?.length) return '';
      this._slidesMap = {};
      const visible = d.carousels.filter(c => this._itemMatches(c.verdict));
      if (!visible.length) return '';
      return '<div class="fr-carousel-grid">' + visible.map(c => {
        const key    = _esc(c.key || c.dir);
        this._slidesMap[c.key || c.dir] = c.slides.map(s => s.media_url);
        const vcls   = c.verdict || '';
        const slides = c.slides.map((s, i) =>
          `<img src="/media/${s.media_url}" alt="slide ${i+1}" loading="lazy"
                onclick="App.finalreview.openSlideshow('${key}',${i})">`
        ).join('');
        return `
          <div class="fr-carousel-card ${vcls}">
            <div class="fr-carousel-head">
              <span class="fr-carousel-name">${_esc(c.dir)}</span>
              <span class="render-format">${c.slides.length} slide${c.slides.length===1?'':'s'}</span>
            </div>
            ${c.theme ? `<div class="quote-text">${_esc(c.theme)}</div>` : ''}
            <div class="fr-slide-strip">${slides}</div>
            ${this._captionBox(c.caption, c.key)}
            ${this._verdictBar(d.name, c.key, c.verdict)}
          </div>`;
      }).join('') + '</div>';
    },

    // ── Story ──────────────────────────────────────────
    _storyHtml(d) {
      if (!d.images?.length) return '';
      const visible = d.images.filter(img => this._itemMatches(img.verdict));
      if (!visible.length) return '';
      return '<div class="fr-story-list">' + visible.map(img => {
        const vcls = img.verdict || '';
        return `
          <div class="fr-story-card ${vcls}">
            <img src="/media/${img.media_url}" alt="${_esc(img.filename)}" loading="lazy">
            <div class="fr-story-card-body">
              ${img.quote ? `<div class="quote-text">${_esc(img.quote)}</div>` : ''}
              ${this._captionBox(img.caption, img.key)}
              ${this._verdictBar(d.name, img.key, img.verdict)}
            </div>
          </div>`;
      }).join('') + '</div>';
    },

    // ── Reel ───────────────────────────────────────────
    _reelHtml(d) {
      if (!d.variants?.length || !this._itemMatches(d.verdict)) return '';
      const chosen = this.chosenVariants[d.name] || d.chosen || d.variants[0]?.filename || '';
      const v = d.variants.find(x => x.filename === chosen);

      const tabs = d.variants.map(vr => {
        const label = vr.filename.replace('draft_', '').replace('.mp4', '');
        const active = vr.filename === chosen ? 'active' : '';
        return `<button class="reel-tab ${active}"
                        onclick="App.finalreview._pickVariant('${_esc(d.name)}','${_esc(vr.filename)}')">
                  ${_esc(label)}<span class="reel-tab-size">${vr.size_mb} MB</span>
                </button>`;
      }).join('');

      const videoHtml = v
        ? `<video controls preload="metadata" src="/media/${v.media_url}"></video>`
        : '<div class="detail-empty">No video found.</div>';

      return `
        <div class="fr-reel-card ${d.verdict || ''}">
          <div class="fr-reel-layout">
            <div class="fr-reel-player">${videoHtml}</div>
            <div class="fr-reel-side">
              <div class="reel-tabs">${tabs}</div>
              ${this._captionBox(d.caption, d.name)}
              ${this._verdictBar(d.name, d.name, d.verdict, chosen)}
            </div>
          </div>
        </div>`;
    },

    _pickVariant(renderName, fn) {
      this.chosenVariants[renderName] = fn;
      this._renderReviewer();
    },

    openSlideshow(key, idx) {
      this.slideshowKey = key;
      this.slideshowIdx = idx;
      this._showSlideshow();
    },

    _showSlideshow() {
      const slides = this._slidesMap[this.slideshowKey] || [];
      const url    = slides[this.slideshowIdx];
      if (!url) return;
      document.getElementById('cs-lightbox-img').src = '/media/' + url;
      document.getElementById('cs-lightbox-counter').textContent =
        (this.slideshowIdx + 1) + ' / ' + slides.length;
      document.getElementById('cs-lightbox-prev').disabled = this.slideshowIdx === 0;
      document.getElementById('cs-lightbox-next').disabled = this.slideshowIdx >= slides.length - 1;
      document.getElementById('carousel-lightbox').style.display = 'flex';
      document.body.style.overflow = 'hidden';
    },

    closeSlideshow() {
      document.getElementById('carousel-lightbox').style.display = 'none';
      document.body.style.overflow = '';
      this.slideshowIdx = -1;
      this.slideshowKey = null;
    },

    slideshowNav(delta) {
      const slides = this._slidesMap[this.slideshowKey] || [];
      const n = this.slideshowIdx + delta;
      if (n < 0 || n >= slides.length) return;
      this.slideshowIdx = n;
      this._showSlideshow();
    },

    slideshowBgClick(e) {
      if (e.target === e.currentTarget) this.closeSlideshow();
    },

    // ── Caption box ────────────────────────────────────
    _captionBox(caption, id) {
      if (!caption) return '';
      const safeId = 'cap_' + (id || '').replace(/[^a-z0-9]/gi, '_');
      return `
        <div class="caption-box">
          <div class="caption-box-head">
            <span class="caption-box-label">Caption &amp; hashtags</span>
            <button class="caption-copy-btn" onclick="App.finalreview._copyCaption(this,'${safeId}')">Copy</button>
          </div>
          <textarea id="${safeId}" readonly>${_esc(caption)}</textarea>
        </div>`;
    },

    _copyCaption(btn, id) {
      const ta = document.getElementById(id);
      if (!ta) return;
      navigator.clipboard.writeText(ta.value).then(() => {
        btn.textContent = 'Copied ✓';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
      });
    },

    // ── Verdict bar ────────────────────────────────────
    _verdictBar(renderName, key, currentVerdict, chosenVariant) {
      const id      = 'note_' + key.replace(/[^a-z0-9]/gi,'_');
      const safeKey = key.replace(/'/g,"\\'");
      const safeRn  = renderName.replace(/'/g,"\\'");
      const safeCv  = (chosenVariant || '').replace(/'/g,"\\'");
      const badge   = currentVerdict
        ? `<span class="verdict-badge ${currentVerdict}">${currentVerdict === 'postable' ? '✓ Postable' : '✗ Not Postable'}</span>`
        : '';
      const reverse = currentVerdict
        ? `<button class="btn btn-ghost btn-sm" onclick="App.finalreview.reverse('${safeRn}','${safeKey}')">Reverse</button>`
        : '';
      return `
        <div class="verdict-bar">
          <input class="verdict-note" id="${id}" type="text" placeholder="Note (optional)" autocomplete="off">
          <button class="btn ${currentVerdict==='postable'?'btn-sage':'btn-outline'} btn-sm"
                  onclick="App.finalreview.verdict('${safeRn}','${safeKey}','postable','${safeCv}')">✓ Post</button>
          <button class="btn ${currentVerdict==='not_postable'?'btn-danger':'btn-outline'} btn-sm"
                  onclick="App.finalreview.verdict('${safeRn}','${safeKey}','not_postable','${safeCv}')">✗ Skip</button>
          ${badge}${reverse}
        </div>`;
    },

    async verdict(renderName, key, verdict, chosenVariant) {
      const noteEl = document.getElementById('note_' + key.replace(/[^a-z0-9]/gi,'_'));
      const note   = noteEl?.value.trim() || '';
      const res    = await fetch('/api/renders/' + renderName + '/verdict', {
        method:  'POST',
        headers: {'Content-Type':'application/json'},
        body:    JSON.stringify({ item_key: key, verdict, note, chosen_variant: chosenVariant || '' }),
      });
      const data = await res.json();
      if (data.error) { alert('Error: ' + data.error); return; }
      await this._refresh();
    },

    async reverse(renderName, key) {
      if (!confirm('Clear this verdict? The copied file will be deleted.')) return;
      const res = await fetch('/api/renders/' + renderName + '/verdict', {
        method:  'DELETE',
        headers: {'Content-Type':'application/json'},
        body:    JSON.stringify({ item_key: key }),
      });
      const data = await res.json();
      if (data.error) { alert('Error: ' + data.error); return; }
      await this._refresh();
    },

    async deleteRender(name) {
      if (!confirm(`Delete render "${name}" from disk? This cannot be undone.`)) return;
      const res  = await fetch('/api/renders/' + name, { method: 'DELETE' });
      const data = await res.json();
      if (data.error) { alert('Error: ' + data.error); return; }
      await this._refresh();
    },

    async _refresh() {
      const res    = await fetch('/api/renders');
      this.renders = await res.json();
      this._updateTabCounts();
      this._renderReviewer();
    },
  },

  // ─────────────────────────────────────────────────────
  // Timeline — weekly rhythm view (Stage 05)
  // ─────────────────────────────────────────────────────
  timeline: {
    schedule:  null,
    stats:     null,
    overrides: {},  // slot_id -> active(bool) — session-only optional toggles

    async load() {
      try {
        const res = await fetch('/api/schedule');
        if (!res.ok) throw new Error('no schedule');
        this.schedule = await res.json();
      } catch(e) {
        this.schedule = this._defaultSchedule();
      }
      try {
        const sres = await fetch('/api/stats');
        this.stats = await sres.json();
      } catch(e) { this.stats = null; }
      this._render();
    },

    _defaultSchedule() {
      return {
        timezone: "Europe/Berlin",
        slots: [
          { id:"sun_reel",     day:"sunday",    time_start:"19:00", time_end:"21:00", format:"reel",       label:"wind-down",           optional:false, active:true },
          { id:"tue_carousel", day:"tuesday",   time_start:"07:00", time_end:"08:00", format:"carousel",   label:"save peak",           optional:false, active:true },
          { id:"wed_quote",    day:"wednesday", time_start:"20:00", time_end:"21:00", format:"quote_post", label:"midweek soft moment", optional:true,  active:true },
          { id:"thu_reel",     day:"thursday",  time_start:"19:00", time_end:"21:00", format:"reel",       label:"midweek reach",       optional:false, active:true },
          { id:"sat_carousel", day:"saturday",  time_start:"09:00", time_end:"10:00", format:"carousel",   label:"slow weekend",        optional:false, active:true },
        ]
      };
    },

    _isActive(slot) {
      if (slot.id in this.overrides) return this.overrides[slot.id];
      return slot.active !== false;
    },

    _toggle(slotId) {
      const slot = (this.schedule.slots || []).find(s => s.id === slotId);
      if (!slot) return;
      this.overrides[slotId] = !this._isActive(slot);
      this._render();
    },

    _render() {
      const body = document.getElementById('timeline-body');
      if (!this.schedule) { body.innerHTML = '<div class="empty">Loading…</div>'; return; }

      const slots   = this.schedule.slots || [];
      const today   = new Date();
      const dayKeys = ['sunday','monday','tuesday','wednesday','thursday','friday','saturday'];
      const todayIdx = today.getDay();
      const ordered  = dayKeys.slice(todayIdx).concat(dayKeys.slice(0, todayIdx));

      const dateFor = {};
      ordered.forEach((d, i) => {
        const dt = new Date(today);
        dt.setDate(today.getDate() + i);
        dateFor[d] = dt;
      });

      const fmtShort = (d) => d.getDate() + ' ' + d.toLocaleString('en-US', { month: 'short' }).toLowerCase();
      const last  = dateFor[ordered[6]];
      const range = `${fmtShort(dateFor[ordered[0]])} — ${fmtShort(last)}, ${last.getFullYear()}`;

      const readyMap = (this.stats && this.stats.final_review && this.stats.final_review.ready_to_post) || {};
      const ready = {
        reel:       readyMap.reel     || 0,
        carousel:   readyMap.carousel || 0,
        quote_post: (readyMap.story || 0) + (readyMap.story_minimal || 0),
      };

      const activeSlots   = slots.filter(s => this._isActive(s));
      const optionalSlots = slots.filter(s => s.optional);
      const byFmt = {};
      activeSlots.forEach(s => { byFmt[s.format] = (byFmt[s.format] || 0) + 1; });

      const optInfo = optionalSlots.length > 0
        ? `<small>active · ${optionalSlots.length} optional</small>`
        : '<small>active</small>';

      const breakdown = ['reel','carousel','quote_post']
        .filter(f => byFmt[f])
        .map(f => {
          const label = f === 'quote_post' ? 'quote' : f;
          return byFmt[f] + ' ' + label + (byFmt[f] > 1 ? 's' : '');
        }).join(' · ');

      const weekHtml = ordered.map(day => {
        const dt = dateFor[day];
        const isToday = (dt.toDateString() === today.toDateString());
        const daySlots = slots.filter(s => s.day === day);
        const slotsHtml = daySlots.length
          ? daySlots.map(s => this._slotHtml(s, ready)).join('')
          : '<div class="tl-day-empty">—</div>';
        return `
          <div class="tl-day">
            <div class="tl-day-head">
              <div class="tl-day-name ${isToday ? 'today' : ''}">${day}</div>
              <div class="tl-day-date">${fmtShort(dt)}${isToday ? ' · today' : ''}</div>
            </div>
            ${slotsHtml}
          </div>`;
      }).join('');

      const tz = this.schedule.timezone || 'Europe/Berlin';

      body.innerHTML = `
        <div class="tl-head">
          <div>
            <div class="tl-caption">the shape of the week</div>
          </div>
          <div class="tl-week-meta">
            <div class="of">week of</div>
            <div class="range">${range}</div>
            <div class="tz">all times · ${_esc(tz)}</div>
          </div>
        </div>

        <div class="tl-main">
          <div class="tl-week">${weekHtml}</div>

          <aside class="tl-side">
            <div class="tl-panel tl-total">
              <div class="tl-panel-eyebrow">this week</div>
              <div class="count">${activeSlots.length} ${optInfo}</div>
              <div class="breakdown">${breakdown || '—'}</div>
            </div>

            <div class="tl-panel">
              <div class="tl-panel-eyebrow">legend</div>
              <ul class="tl-legend-list">
                <li>
                  <span class="tl-legend-swatch" style="background:var(--reel-col)"></span>
                  <span class="tl-legend-name">reel</span>
                  <span class="tl-legend-desc">moving image</span>
                </li>
                <li>
                  <span class="tl-legend-swatch" style="background:var(--carousel-col)"></span>
                  <span class="tl-legend-name">carousel</span>
                  <span class="tl-legend-desc">save · share</span>
                </li>
                <li>
                  <span class="tl-legend-swatch" style="background:var(--quote-col)"></span>
                  <span class="tl-legend-name">quote</span>
                  <span class="tl-legend-desc">single line</span>
                </li>
              </ul>
            </div>

            <div class="tl-note">
              <div class="body">
                single image and quote posts are sprinkled in only when the line
                is strong enough to save or send.
              </div>
            </div>

            <div class="tl-edit-link">
              edit this rhythm in <code>tools/rhythm_editor.html</code>
            </div>
          </aside>
        </div>`;
    },

    _slotHtml(slot, ready) {
      const active = this._isActive(slot);
      const cls = ['tl-slot'];
      if (slot.optional) cls.push('optional');
      if (!active)       cls.push('optional-off');

      const fmt      = slot.format;
      const fmtLabel = fmt === 'quote_post' ? 'quote post' : fmt;

      const [hStart] = (slot.time_start || '00:00').split(':').map(n => parseInt(n,10));
      const [hEnd]   = (slot.time_end   || slot.time_start || '00:00').split(':').map(n => parseInt(n,10));
      const ampm     = hStart < 12 ? 'am' : 'pm';
      const endAmpm  = hEnd   < 12 ? 'am' : 'pm';
      const h12      = ((hStart + 11) % 12) + 1;
      const hEnd12   = ((hEnd   + 11) % 12) + 1;
      const range    = (ampm === endAmpm) ? `–${hEnd12} ${ampm}` : `–${hEnd12} ${endAmpm}`;

      let windowText = '';
      if (hStart < 11)      windowText = slot.optional ? 'morning · optional' : 'morning';
      else if (hStart < 14) windowText = 'midday';
      else if (hStart < 18) windowText = 'afternoon';
      else                  windowText = slot.optional ? 'evening · optional' : 'evening';

      let badge = '';
      if (active && ready[fmt] > 0) {
        ready[fmt] -= 1;
        const fmtName = fmt === 'quote_post' ? 'quote' : fmt;
        badge = `<div class="tl-slot-badge">1 ${fmtName} ready</div>`;
      }

      const toggle = slot.optional
        ? `<button class="tl-toggle ${active ? 'on' : 'off'}"
                   title="optional · ${active ? 'on' : 'off'}"
                   onclick="App.timeline._toggle('${_esc(slot.id)}')"></button>`
        : '';

      return `
        <div class="${cls.join(' ')}" data-fmt="${_esc(fmt)}">
          ${toggle}
          <div class="tl-slot-fmt-row">
            <span class="tl-slot-dot"></span>
            <span class="tl-slot-fmt">${_esc(fmtLabel)}</span>
          </div>
          <div class="tl-slot-time">${h12}<span class="ampm">${range}</span></div>
          <div class="tl-slot-window">${windowText}</div>
          <div class="tl-slot-label">${_esc(slot.label || '')}</div>
          ${badge}
        </div>`;
    },
  },
};

// ─────────────────────────────────────────────────────────
// Pool widget — shows what's in sort_media/ before rendering
// ─────────────────────────────────────────────────────────

App.poolWidget = {
  _loaded:  false,
  _data:    null,
  _lbFmt:   null,
  _lbIdx:   -1,

  load() {
    if (this._loaded) return;
    const sub = document.getElementById('pool-widget-sub');
    if (sub) { sub.textContent = 'loading…'; sub.style.color = ''; }
    fetch('/api/pool-summary')
      .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(data => { this._data = data; this._render(data); this._loaded = true; })
      .catch(() => {
        const rows = document.getElementById('pool-rows');
        if (rows) rows.innerHTML = '<p style="color:var(--muted);font-size:.85rem;padding:.5rem .25rem">Could not load pool data.</p>';
      });
  },

  _render(data) {
    const rows = document.getElementById('pool-rows');
    if (!rows) return;

    const reels    = data.reels    || {};
    const carousel = data.carousel || {};
    const story    = data.story    || {};

    const reelTotal  = (reels.clips || 0) + (reels.sound || 0) + (reels.music || 0);
    const carTotal   = carousel.total || 0;
    const storyTotal = story.total    || 0;

    rows.innerHTML =
      this._row('reels',    'Reels',    reelTotal,  `${reels.clips||0} clips · ${reels.sound||0} sound · ${reels.music||0} music`, reels.items)    +
      this._row('carousel', 'Carousel', carTotal,   `${carousel.priority||0} priority · ${carousel.standard||0} standard`,          carousel.items) +
      this._row('story',    'Story',    storyTotal,  `${story.priority||0} priority · ${story.standard||0} standard`,               story.items);

    const sub = document.getElementById('pool-widget-sub');
    if (sub) sub.textContent = (reelTotal + carTotal) + ' items ready';

    // Row header toggles grid open/close — clicks on thumbs go to lightbox instead
    rows.querySelectorAll('.pool-row[data-toggle]').forEach(row => {
      row.querySelector('.pool-row-header').addEventListener('click', () => row.classList.toggle('is-open'));
    });
  },

  _row(fmt, label, total, detail, items) {
    if (!total) return `
      <div class="pool-row is-empty" data-fmt="${fmt}">
        <div class="pool-row-name">${label}</div>
        <div class="pool-row-total is-empty">0</div>
        <div class="pool-row-detail is-empty">Pool is empty — run <code>feedback.py</code> first</div>
      </div>`;

    const thumbs = (items || []).map((item, idx) => this._thumb(item, fmt, idx)).join('');
    return `
      <div class="pool-row" data-fmt="${fmt}" data-toggle="1">
        <div class="pool-row-header" style="display:contents;cursor:pointer">
          <div class="pool-row-name">${label}</div>
          <div class="pool-row-total">${total}<small>ready</small></div>
          <div class="pool-row-detail">${_esc(detail)}</div>
        </div>
        <div class="pool-row-panel">
          <div class="pool-grid">${thumbs}</div>
        </div>
      </div>`;
  },

  _thumb(item, fmt, idx) {
    const kind     = item.kind || 'image';
    const priority = item.priority ? ' is-priority' : '';
    const score    = typeof item.score === 'number' ? item.score.toFixed(1) : '';
    const title    = _esc((item.title || item.filename || '') + (score ? '  ·  ' + score : ''));
    let inner = '';

    if (kind === 'image' && item.media_url) {
      inner = `<img src="/media/${_esc(item.media_url)}" alt="" loading="lazy" onerror="this.style.display='none'">`;
    } else if (kind === 'video' && item.thumb_url) {
      inner = `<img src="/media/${_esc(item.thumb_url)}" alt="" loading="lazy" onerror="this.style.display='none'">`;
    } else if (kind === 'audio') {
      const seed = (item.filename || '').split('').reduce((a, c) => a + c.charCodeAt(0), 0);
      let wave = '<span class="pool-thumb-wave">';
      for (let i = 0; i < 12; i++) wave += `<span style="height:${14 + ((seed * (i+3) * 17) % 32)}px"></span>`;
      wave += '</span>';
      inner = wave;
    }

    return `<div class="pool-thumb${priority}" data-kind="${kind}" title="${title}" onclick="App.poolWidget.openLb('${fmt}',${idx})">${inner}</div>`;
  },

  // ── Lightbox ────────────────────────────────────────────────────────────

  openLb(fmt, idx) {
    this._lbFmt = fmt;
    this._lbIdx = idx;
    this._buildLb();
    this._showLb();
  },

  _buildLb() {
    if (document.getElementById('pool-lb')) return;
    const el = document.createElement('div');
    el.id        = 'pool-lb';
    el.className = 'pool-lb';
    el.innerHTML = `
      <div class="pool-lb-box">
        <div class="pool-lb-media" id="pool-lb-media"></div>
        <div class="pool-lb-foot">
          <div class="pool-lb-meta">
            <div class="pool-lb-title" id="pool-lb-title"></div>
            <div class="pool-lb-score" id="pool-lb-score"></div>
          </div>
          <div class="pool-lb-nav">
            <button class="pool-lb-btn" id="pool-lb-prev" onclick="App.poolWidget.navLb(-1)">&#8592;</button>
            <span class="pool-lb-counter" id="pool-lb-counter"></span>
            <button class="pool-lb-btn" id="pool-lb-next" onclick="App.poolWidget.navLb(1)">&#8594;</button>
            <button class="pool-lb-close" onclick="App.poolWidget.closeLb()">&#x2715;</button>
          </div>
        </div>
      </div>`;
    el.addEventListener('click', e => { if (e.target === el) this.closeLb(); });
    // Swipe support
    let tx = 0;
    el.addEventListener('touchstart', e => { tx = e.touches[0].clientX; }, { passive: true });
    el.addEventListener('touchend',   e => {
      const dx = e.changedTouches[0].clientX - tx;
      if (Math.abs(dx) > 40) this.navLb(dx < 0 ? 1 : -1);
    });
    document.body.appendChild(el);
  },

  _showLb() {
    const items = this._lbItems();
    const item  = items[this._lbIdx];
    if (!item) return;

    const media = document.getElementById('pool-lb-media');
    const kind  = item.kind || 'image';

    if (kind === 'video') {
      media.innerHTML = `<video src="/media/${_esc(item.media_url)}" controls autoplay loop style="max-height:60vh"></video>`;
    } else if (kind === 'audio') {
      const seed = (item.filename || '').split('').reduce((a, c) => a + c.charCodeAt(0), 0);
      let bars = '';
      for (let i = 0; i < 32; i++) bars += `<span style="height:${12 + ((seed * (i+3) * 11) % 44)}px"></span>`;
      media.innerHTML = `
        <div class="pool-lb-audio-wrap">
          <div class="pool-lb-big-wave">${bars}</div>
          <audio src="/media/${_esc(item.media_url)}" controls autoplay></audio>
        </div>`;
    } else {
      media.innerHTML = `<img src="/media/${_esc(item.media_url)}" alt="" style="max-height:60vh">`;
    }

    _setText('pool-lb-title', item.title || item.filename || '');
    const score = typeof item.score === 'number' ? `Score ${item.score.toFixed(1)}${item.priority ? '  ·  priority' : ''}` : '';
    _setText('pool-lb-score', score);
    _setText('pool-lb-counter', `${this._lbIdx + 1} / ${items.length}`);
    document.getElementById('pool-lb-prev').disabled = this._lbIdx === 0;
    document.getElementById('pool-lb-next').disabled = this._lbIdx >= items.length - 1;

    document.getElementById('pool-lb').classList.add('is-open');
    document.body.style.overflow = 'hidden';
  },

  closeLb() {
    const lb = document.getElementById('pool-lb');
    if (!lb) return;
    // Stop any playing media before closing
    lb.querySelectorAll('video,audio').forEach(m => { m.pause(); m.src = ''; });
    lb.classList.remove('is-open');
    document.body.style.overflow = '';
    this._lbIdx = -1;
  },

  navLb(delta) {
    const items = this._lbItems();
    const next  = this._lbIdx + delta;
    if (next < 0 || next >= items.length) return;
    // Stop current media before switching
    document.getElementById('pool-lb').querySelectorAll('video,audio').forEach(m => { m.pause(); m.src = ''; });
    this._lbIdx = next;
    this._showLb();
  },

  _lbItems() {
    if (!this._data || !this._lbFmt) return [];
    return (this._data[this._lbFmt] || {}).items || [];
  },

  isLbOpen() {
    const lb = document.getElementById('pool-lb');
    return lb && lb.classList.contains('is-open');
  },
};

// ─────────────────────────────────────────────────────────
// Intelligence module
// ─────────────────────────────────────────────────────────

App.intelligence = {
  _loaded:  false,
  _loading: false,
  _data:    null,

  load() {
    if (this._loaded || this._loading) return;
    this._loading = true;
    this._setLoading(true);
    fetch('/api/intelligence')
      .then(r => r.json())
      .then(d => {
        this._data    = d;
        this._loaded  = true;
        this._loading = false;
        this._setLoading(false);
        this._render(d);
      })
      .catch(err => {
        this._loading = false;
        this._setLoading(false);
        console.error('intelligence load failed', err);
      });
  },

  _setLoading(on) {
    const root = document.getElementById('stage-intelligence');
    if (!root) return;
    const existing = root.querySelector('.intel-loading');
    if (on && !existing) {
      const div = document.createElement('div');
      div.className   = 'intel-loading';
      div.textContent = 'Loading intelligence…';
      root.prepend(div);
    } else if (!on && existing) {
      existing.remove();
    }
  },

  _chips(containerId, items, kind) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = items.map(t =>
      `<span class="intel-chip is-${kind}">${_esc(t)}</span>`
    ).join('');
  },

  _themes(list, currentIndex) {
    const el = document.getElementById('intel-themes-list');
    if (!el) return;
    el.innerHTML = list.map((t, i) =>
      `<li class="intel-theme${i === currentIndex ? ' is-current' : ''}">` +
        `<span>${_esc(t)}</span>` +
        `<span class="intel-theme-badge">current</span>` +
      `</li>`
    ).join('');
  },

  _hooks(history) {
    const el = document.getElementById('intel-hooks-list');
    if (!el) return;
    const items = history.slice().reverse();
    el.innerHTML = items.map((h, i) =>
      `<li class="intel-hook">` +
        `<span class="intel-hook-num">${String(items.length - i).padStart(2, '0')}</span>` +
        _esc(h) +
      `</li>`
    ).join('');
  },

  _render(d) {
    _setText('intel-keywords-meta',      (d.high_keywords.length + d.low_keywords.length) + ' tracked');
    _setText('intel-keywords-high-count', d.high_keywords.length);
    _setText('intel-keywords-low-count',  d.low_keywords.length);
    this._chips('intel-keywords-high', d.high_keywords, 'high');
    this._chips('intel-keywords-low',  d.low_keywords,  'low');

    _setText('intel-tags-meta',      (d.high_tags.length + d.low_tags.length) + ' tracked');
    _setText('intel-tags-high-count', d.high_tags.length);
    _setText('intel-tags-low-count',  d.low_tags.length);
    this._chips('intel-tags-high', d.high_tags, 'high');
    this._chips('intel-tags-low',  d.low_tags,  'low');

    _setText('intel-themes-meta', d.themes.list.length + ' themes · current #' + (d.themes.current_index + 1));
    this._themes(d.themes.list, d.themes.current_index);

    _setText('intel-hooks-meta', d.hooks_history.length + ' hooks used');
    this._hooks(d.hooks_history);

    const foot = document.getElementById('intel-foot');
    if (foot) foot.innerHTML =
      `<span><strong>${d.rejected_count}</strong> rejected IDs</span>` +
      `<span><strong>${d.hooks_history.length}</strong> hooks deployed</span>` +
      `<span><strong>${d.themes.list.length}</strong> themes in rotation</span>`;
  },
};

// ─────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────

function _buildStepChips(containerId, steps) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = '';
  steps.forEach(k => {
    const m   = STEP_META[k] || { name: k, sub: '' };
    const div = document.createElement('div');
    div.className = 'step-pill';
    div.id        = containerId + '_' + k;
    div.innerHTML = `
      <div class="step-pill-top">
        <span class="step-dot"></span>
        <span class="step-pill-name">${m.name}</span>
      </div>
      <span class="step-pill-sub">${m.sub}</span>`;
    el.appendChild(div);
  });
}

function _setChip(containerId, key, state) {
  const el = document.getElementById(containerId + '_' + key);
  if (!el) return;
  el.className = 'step-pill' + (state ? ' ' + state : '');
  const dot = el.querySelector('.step-dot');
  if (dot) dot.className = 'step-dot' + (state ? ' ' + state : '');
}

function _setClass(el, kind) {
  el.className = el.className.replace(/\b(idle|running|done|error)\b/g, '').trim() + ' ' + kind;
}

function _setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function _scrollLog() {
  const el = document.getElementById('log-lines');
  if (el) el.scrollTop = el.scrollHeight;
}

function _today() {
  return new Date().toISOString().slice(0,10);
}

function _esc(s) {
  return String(s || '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

function _playSvg() {
  return `<svg viewBox="0 0 24 24" fill="white" style="margin-left:3px"><polygon points="5,3 20,12 5,21"/></svg>`;
}
function _pauseSvg() {
  return `<svg viewBox="0 0 24 24" fill="white"><rect x="5" y="3" width="4" height="18" rx="1"/><rect x="15" y="3" width="4" height="18" rx="1"/></svg>`;
}
function _fmtTime(s) {
  const m = Math.floor(s / 60);
  return m + ':' + String(Math.floor(s % 60)).padStart(2, '0');
}

// ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => App.init());
