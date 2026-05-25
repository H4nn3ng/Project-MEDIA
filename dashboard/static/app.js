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

    // Schedule slot modal
    if (document.getElementById('sched-modal')?.classList.contains('open')) {
      if (e.key === 'Escape') { this.timeline.closeSlotModal(); return; }
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
    filterMode:     'pending',     // 'pending' | 'all' | 'approved' | 'rejected'
    chosenVariants: {},
    _slidesMap:     {},
    _detailsCache:  {},           // post_key -> {render_name, title, thumbnail_url, caption}
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
        // Cache data needed for schedule assignment
        this._detailsCache[c.key || c.dir] = {
          render_name:   d.name,
          title:         c.theme || c.dir,
          thumbnail_url: c.slides[0]?.media_url || '',
          caption:       c.caption  || '',
          format:        'carousel',
        };
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
        // Cache data for schedule assignment
        this._detailsCache[img.key] = {
          render_name:   d.name,
          title:         img.quote ? img.quote.slice(0, 60) : img.filename,
          thumbnail_url: img.media_url,
          caption:       img.caption || '',
          format:        'quote_post',
        };
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
      // Cache data for schedule assignment
      this._detailsCache[d.name] = {
        render_name:    d.name,
        title:          d.name,
        thumbnail_url:  '',
        video_url:      v?.media_url || '',
        chosen_variant: chosen,
        caption:        d.caption || '',
        format:         'reel',
      };

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

      // Auto-assign to timeline when approved; remove when rejected
      if (verdict === 'postable') {
        const cached = this._detailsCache[key] || {};
        fetch('/api/schedule-assignments', {
          method:  'POST',
          headers: {'Content-Type':'application/json'},
          body:    JSON.stringify({
            post_key:       key,
            render_name:    cached.render_name || renderName,
            title:          cached.title || key,
            thumbnail_url:  cached.thumbnail_url || '',
            video_url:      cached.video_url || '',
            chosen_variant: chosenVariant || cached.chosen_variant || '',
            caption:        cached.caption || '',
          }),
        }).catch(() => {});  // non-fatal — timeline still works without it
      } else if (verdict === 'not_postable') {
        fetch('/api/schedule-assignments/' + encodeURIComponent(key), { method: 'DELETE' })
          .catch(() => {});
      }

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
      // Remove from schedule when verdict is cleared
      fetch('/api/schedule-assignments/' + encodeURIComponent(key), { method: 'DELETE' })
        .catch(() => {});
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
  // Timeline — monthly calendar view (Stage 05)
  // ─────────────────────────────────────────────────────
  timeline: {
    schedule:   null,
    stats:      null,
    assignments:[],
    weeksAhead: {},
    overrides:  {},       // slot_id -> active(bool) — session-only
    viewMonth:  new Date().getMonth(),
    viewYear:   new Date().getFullYear(),

    // Carousel slideshow state for the timeline modal — independent from
    // the Approve section's slideshow (App.finalreview._slidesMap).
    _slideUrls: [],
    _slideIdx:  0,

    _openCarouselSlideshow(urls, startIdx) {
      this._slideUrls = urls;
      this._slideIdx  = startIdx || 0;
      this._updateCarouselSlideshow();
    },

    _updateCarouselSlideshow() {
      const img     = document.getElementById('sched-slide-img');
      const counter = document.getElementById('sched-slide-counter');
      const prev    = document.getElementById('sched-slide-prev');
      const next    = document.getElementById('sched-slide-next');
      if (!img) return;
      img.src = '/media/' + this._slideUrls[this._slideIdx];
      if (counter) counter.textContent = (this._slideIdx + 1) + ' / ' + this._slideUrls.length;
      if (prev)    prev.disabled  = this._slideIdx === 0;
      if (next)    next.disabled  = this._slideIdx >= this._slideUrls.length - 1;
    },

    slideshowNav(delta) {
      const n = this._slideIdx + delta;
      if (n < 0 || n >= this._slideUrls.length) return;
      this._slideIdx = n;
      this._updateCarouselSlideshow();
    },

    async load() {
      try {
        const res = await fetch('/api/schedule');
        if (!res.ok) throw new Error('no schedule');
        this.schedule = await res.json();
      } catch(e) {
        this.schedule = this._defaultSchedule();
      }
      // Retroactively assign posts approved before auto-assign was deployed
      try { await fetch('/api/schedule-auto-assign', { method: 'POST' }); } catch(e) {}
      // Backfill caption.txt for older approved carousels that were copied
      // before the verdict path wrote captions to the approved folder.
      try { await fetch('/api/backfill-captions', { method: 'POST' }); } catch(e) {}
      try {
        const sres = await fetch('/api/stats');
        this.stats = await sres.json();
      } catch(e) { this.stats = null; }
      try {
        const ares = await fetch('/api/schedule-assignments');
        this.assignments = await ares.json();
      } catch(e) { this.assignments = []; }
      try {
        const wres = await fetch('/api/schedule-weeks-ahead');
        this.weeksAhead = await wres.json();
      } catch(e) { this.weeksAhead = {}; }
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

    _mondayOf(dt) {
      const d   = new Date(dt);
      const day = d.getDay();
      d.setDate(d.getDate() - (day === 0 ? 6 : day - 1));
      d.setHours(0, 0, 0, 0);
      return d;
    },

    _isoDate(dt) {
      // Use LOCAL date components, not UTC. The Python side stores
      // week_start as a local-date ISO string (date.today() -> isoformat()).
      // toISOString() returns UTC, so in eastward timezones (e.g. Europe/Berlin)
      // local midnight Monday becomes 22:00 Sunday UTC, breaking the lookup.
      const y  = dt.getFullYear();
      const m  = String(dt.getMonth() + 1).padStart(2, '0');
      const d  = String(dt.getDate()).padStart(2, '0');
      return `${y}-${m}-${d}`;
    },

    _render() {
      const body = document.getElementById('timeline-body');
      if (!this.schedule) { body.innerHTML = '<div class="empty">Loading…</div>'; return; }

      const slots    = this.schedule.slots || [];
      const today    = new Date();
      const todayStr = this._isoDate(today);

      // assignment lookup: "slot_id|week_start" -> assignment
      const assignMap = {};
      (this.assignments || []).forEach(a => {
        assignMap[a.slot_id + '|' + a.week_start] = a;
      });

      // calendar grid for viewMonth / viewYear
      const firstDay    = new Date(this.viewYear, this.viewMonth, 1);
      const daysInMonth = new Date(this.viewYear, this.viewMonth + 1, 0).getDate();
      const startPad    = (firstDay.getDay() + 6) % 7;   // Mon=0 … Sun=6
      const totalCells  = Math.ceil((startPad + daysInMonth) / 7) * 7;
      const wdNames     = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];

      const cellsHtml = [];
      for (let i = 0; i < totalCells; i++) {
        const d           = new Date(this.viewYear, this.viewMonth, 1 + (i - startPad));
        const isThisMonth = d.getMonth() === this.viewMonth;
        const isToday     = this._isoDate(d) === todayStr;
        const dayNum      = d.getDate();
        const wdName      = wdNames[(d.getDay() + 6) % 7];
        const weekStart   = this._isoDate(this._mondayOf(d));

        const pillsHtml = slots
          .filter(s => s.day === wdName && this._isActive(s))
          .map(s => {
            const a       = assignMap[s.id + '|' + weekStart] || null;
            const label   = s.format === 'quote_post' ? 'story' : s.format;
            const tipTime = s.time_start || '';
            if (a) {
              const tipTitle = a.title ? ' · ' + a.title.slice(0, 38) : '';
              // Small at-a-glance preview of the assigned post. Images use <img>;
              // reels (video only) use <video preload="metadata"> so the browser
              // pulls only the poster frame, not the full file. The wrapper sits
              // outside the clickable pill so clicking it still bubbles to the
              // day cell — clicking the pill itself opens the modal.
              let thumbHtml = '';
              if (a.thumbnail_url) {
                thumbHtml = `<img class="tl-cal-thumb" src="/media/${_esc(a.thumbnail_url)}" alt="" loading="lazy">`;
              } else if (a.video_url) {
                thumbHtml = `<video class="tl-cal-thumb" src="/media/${_esc(a.video_url)}" muted preload="metadata"></video>`;
              }
              return `<div class="tl-cal-slot has-post" data-fmt="${_esc(s.format)}"
                           onclick="event.stopPropagation();App.timeline.openSlotModal('${_esc(a.id)}')"
                           title="${_esc(label + ' · ' + tipTime + tipTitle)}">${_esc(label)}</div>${thumbHtml}`;
            }
            return `<div class="tl-cal-slot" data-fmt="${_esc(s.format)}"
                         title="${_esc(label + ' · ' + tipTime + ' · empty')}">${_esc(label)}</div>`;
          }).join('');

        const cls = ['tl-cal-day'];
        if (!isThisMonth) cls.push('other-month');
        if (isToday)      cls.push('today');
        cellsHtml.push(`<div class="${cls.join(' ')}"><span class="tl-cal-day-num">${dayNum}</span>${pillsHtml}</div>`);
      }

      const aheadRows = [
        { fmt: 'reel',       label: 'Reels',      col: 'var(--reel-col)'     },
        { fmt: 'carousel',   label: 'Carousels',  col: 'var(--carousel-col)' },
        { fmt: 'quote_post', label: 'Story posts',col: 'var(--quote-col)'    },
      ].map(({ fmt, label, col }) => {
        const n    = (this.weeksAhead || {})[fmt] || 0;
        const desc = n === 0 ? 'this week only' : n === 1 ? '1 week ahead' : `${n} weeks ahead`;
        return `<div class="tl-ahead-row">
          <span class="tl-ahead-dot" style="background:${col}"></span>
          <span class="tl-ahead-label">${label}</span>
          <span class="tl-ahead-count ${n > 0 ? 'has-ahead' : ''}">${desc}</span>
        </div>`;
      }).join('');

      const monthDate  = new Date(this.viewYear, this.viewMonth, 1);
      const monthLabel = monthDate.toLocaleString('en-US', { month: 'long' }) + ' ' + this.viewYear;
      const tz         = this.schedule.timezone || 'Europe/Berlin';
      const dayHeaders = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        .map(n => `<div class="tl-cal-weekday">${n}</div>`).join('');

      body.innerHTML = `
        <div class="tl-head">
          <div class="tl-caption">content calendar</div>
          <div class="tl-cal-nav">
            <button class="tl-cal-nav-btn" onclick="App.timeline.prevMonth()">←</button>
            <span class="tl-cal-month-label">${_esc(monthLabel)}</span>
            <button class="tl-cal-nav-btn" onclick="App.timeline.nextMonth()">→</button>
          </div>
        </div>

        <div class="tl-main">
          <div class="tl-calendar">
            <div class="tl-cal-weekdays">${dayHeaders}</div>
            <div class="tl-cal-grid">${cellsHtml.join('')}</div>
          </div>

          <aside class="tl-side">
            <div class="tl-panel tl-ahead-panel">
              <div class="tl-panel-eyebrow">pipeline ahead</div>
              ${aheadRows}
            </div>
            <div class="tl-panel">
              <div class="tl-panel-eyebrow">legend</div>
              <ul class="tl-legend-list">
                <li>
                  <span class="tl-legend-swatch" style="background:var(--reel-col)"></span>
                  <span class="tl-legend-name">reel</span>
                  <span class="tl-legend-desc">9:16 video</span>
                </li>
                <li>
                  <span class="tl-legend-swatch" style="background:var(--carousel-col)"></span>
                  <span class="tl-legend-name">carousel</span>
                  <span class="tl-legend-desc">save · share</span>
                </li>
                <li>
                  <span class="tl-legend-swatch" style="background:var(--quote-col)"></span>
                  <span class="tl-legend-name">story post</span>
                  <span class="tl-legend-desc">single frame</span>
                </li>
              </ul>
            </div>
            <div class="tl-panel">
              <div class="tl-panel-eyebrow">timezone</div>
              <div style="font:400 12px var(--sans);color:var(--softer-muted)">${_esc(tz)}</div>
            </div>
          </aside>
        </div>`;
    },

    prevMonth() {
      if (this.viewMonth === 0) { this.viewMonth = 11; this.viewYear--; }
      else this.viewMonth--;
      this._render();
    },

    nextMonth() {
      if (this.viewMonth === 11) { this.viewMonth = 0; this.viewYear++; }
      else this.viewMonth++;
      this._render();
    },

    // ── Slot modal — shows assigned post details ────────────────────────
    openSlotModal(assignmentId) {
      const a = (this.assignments || []).find(x => x.id === assignmentId);
      if (!a) return;

      let lb = document.getElementById('sched-modal');
      if (!lb) {
        lb = document.createElement('div');
        lb.id = 'sched-modal';
        lb.innerHTML = `
          <div class="sched-modal-box" onclick="event.stopPropagation()">
            <button class="sched-modal-close" onclick="App.timeline.closeSlotModal()">✕</button>
            <div class="sched-modal-fmt" id="sched-fmt"></div>
            <div class="sched-modal-variant" id="sched-variant"></div>
            <div class="sched-modal-time" id="sched-time"></div>
            <div class="sched-modal-thumb-wrap" id="sched-thumb-wrap"></div>
            <div class="sched-modal-title" id="sched-title"></div>
            <div class="sched-modal-caption-label">Caption &amp; hashtags</div>
            <textarea class="sched-modal-caption" id="sched-caption" readonly></textarea>
            <div class="sched-modal-actions">
              <button class="btn btn-outline btn-sm" id="sched-copy-btn"
                      onclick="App.timeline._copyCaption()">Copy caption</button>
              <button class="btn btn-outline btn-sm" id="sched-files-btn"
                      onclick="App.timeline.openInFiles()">Open in files</button>
            </div>
          </div>`;
        lb.onclick = () => this.closeSlotModal();
        document.body.appendChild(lb);
      }

      const fmtLabel = a.format === 'quote_post' ? 'story post' : a.format;
      const dayStr   = a.slot_day.charAt(0).toUpperCase() + a.slot_day.slice(1);
      const timeStr  = a.slot_time || '';

      document.getElementById('sched-fmt').textContent    = fmtLabel;
      document.getElementById('sched-time').textContent   = `${dayStr}  ·  ${timeStr}  ·  week of ${a.week_start}`;
      document.getElementById('sched-title').textContent  = a.title || '';
      document.getElementById('sched-caption').value      = a.caption || '';

      const variantEl = document.getElementById('sched-variant');
      if (variantEl) {
        const vLabel = a.chosen_variant
          ? a.chosen_variant.replace('draft_', '').replace('.mp4', '')
          : '';
        variantEl.textContent    = vLabel;
        variantEl.style.display  = vLabel ? '' : 'none';
      }

      const thumbWrap = document.getElementById('sched-thumb-wrap');
      if (a.format === 'carousel') {
        // Carousels store only slide_01 in the assignment. Fetch the full slide
        // list from the render detail endpoint so the user can preview every
        // slide before posting. The strip is horizontally scrollable to stay
        // compact for 3–8 slides; clicking a slide enlarges it in place.
        thumbWrap.innerHTML = `<div class="sched-modal-slide-loading">Loading slides…</div>`;
        this._loadCarouselSlides(a, thumbWrap);
      } else if (a.video_url) {
        thumbWrap.innerHTML = `<video controls preload="metadata" src="/media/${a.video_url}"></video>`;
      } else if (a.thumbnail_url) {
        thumbWrap.innerHTML = `<img src="/media/${a.thumbnail_url}" alt="Post thumbnail">`;
      } else {
        thumbWrap.innerHTML = '';
      }

      // Store current assignment id for copy/open actions
      lb.dataset.assignId   = a.id;
      lb.dataset.folderPath = a.folder_path || '';

      lb.classList.add('open');
    },

    closeSlotModal() {
      document.getElementById('sched-modal')?.classList.remove('open');
    },

    _copyCaption() {
      const ta  = document.getElementById('sched-caption');
      const btn = document.getElementById('sched-copy-btn');
      if (!ta || !btn) return;
      navigator.clipboard.writeText(ta.value).then(() => {
        const orig = btn.textContent;
        btn.textContent = 'Copied ✓';
        setTimeout(() => { btn.textContent = orig; }, 2000);
      }).catch(() => {
        ta.select();
        document.execCommand('copy');
      });
    },

    openInFiles() {
      const lb   = document.getElementById('sched-modal');
      const btn  = document.getElementById('sched-files-btn');
      const path = lb?.dataset.folderPath || '';
      if (!path) return;
      fetch('/api/open-folder', {
        method:  'POST',
        headers: {'Content-Type':'application/json'},
        body:    JSON.stringify({ path }),
      }).then(r => {
        if (r.ok) return;
        // Surface backend errors (404 folder missing, 403 forbidden, etc.)
        // so the user knows the click did something, instead of silently
        // doing nothing. Brief in-button message, then restore.
        r.json().catch(() => ({})).then(j => {
          const msg = (j && j.error) ? j.error : 'open failed (' + r.status + ')';
          console.warn('[openInFiles]', msg, 'path=' + path);
          if (btn) {
            const orig = btn.textContent;
            btn.textContent = msg.length > 24 ? 'Folder missing' : msg;
            setTimeout(() => { btn.textContent = orig; }, 2200);
          }
        });
      }).catch(err => {
        console.warn('[openInFiles] network error', err);
      });
    },

    // ── Carousel slide loader ──────────────────────────────────────────
    // Calls /api/renders/<name> for the full slide list referenced by the
    // assignment, then renders an arrow-navigated single-slide viewer
    // inside the modal's thumb-wrap div. Independent from the Approve
    // section slideshow so the two modals can stay open simultaneously
    // without state collisions.
    _loadCarouselSlides(a, thumbWrap) {
      const renderName  = a.render_name || '';
      const carouselDir = (a.post_key || '').split('/')[1] || '';
      if (!renderName || !carouselDir) { thumbWrap.innerHTML = ''; return; }

      fetch(`/api/renders/${encodeURIComponent(renderName)}`)
        .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(detail => {
          const carousels = detail.carousels || [];
          const match     = carousels.find(c => c.dir === carouselDir);
          const slides    = match ? (match.slides || []) : [];
          if (!slides.length) {
            thumbWrap.innerHTML = a.thumbnail_url
              ? `<img src="/media/${_esc(a.thumbnail_url)}" alt="Post thumbnail">`
              : '<div class="sched-modal-slide-loading">No slides found.</div>';
            return;
          }
          const urls = slides.map(s => s.media_url);
          thumbWrap.innerHTML = `
            <div class="sched-slide-viewer">
              <button class="sched-slide-arrow" id="sched-slide-prev"
                      onclick="App.timeline.slideshowNav(-1)">‹</button>
              <img class="sched-slide-img" id="sched-slide-img" src="" alt="Slide">
              <button class="sched-slide-arrow" id="sched-slide-next"
                      onclick="App.timeline.slideshowNav(1)">›</button>
              <div class="sched-slide-counter" id="sched-slide-counter"></div>
            </div>`;
          this._openCarouselSlideshow(urls, 0);
        })
        .catch(err => {
          console.warn('[loadCarouselSlides] failed', err);
          thumbWrap.innerHTML = a.thumbnail_url
            ? `<img src="/media/${_esc(a.thumbnail_url)}" alt="Post thumbnail">`
            : '<div class="sched-modal-slide-loading">Could not load slides.</div>';
        });
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
