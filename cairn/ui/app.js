/**
 * Cairn Blackboard — UI application
 *
 * No build step, no framework. Modern JS (ES2022 modules, fetch, EventSource).
 * marked.js (loaded via CDN in index.html) handles markdown rendering.
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  apiKey:          localStorage.getItem('cairn_api_key') || null,
  messages:        [],   // array of MessageSummary objects (newest first)
  selectedId:      null, // currently open message id
  selectedDb:      null, // topic_db of currently open message
  filters:         { db: '', agent: '', thread: '', type: '', tags: '', promote: '' },
  streamStatus:    'disconnected', // connected | connecting | disconnected
  eventSource:     null,
  knownTopics:     [],   // populated from /health on login
};

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

const API_BASE = window.location.origin;

async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      'Authorization': `Bearer ${state.apiKey}`,
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw Object.assign(new Error(body.detail || res.statusText), { status: res.status });
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

function showLogin() {
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
}

function showApp() {
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
}

async function attemptLogin(key) {
  const prev = state.apiKey;
  state.apiKey = key;
  try {
    // Fetch health (topic list) and validate the key simultaneously.
    // /health has no auth requirement, so we probe /messages?limit=1
    // to confirm the key is accepted before storing it.
    const [health] = await Promise.all([
      apiFetch('/health'),
      apiFetch('/messages?limit=1'),
    ]);
    state.knownTopics = health.topic_dbs || [];
    localStorage.setItem('cairn_api_key', key);
    populateDbFilter();
    return true;
  } catch (err) {
    state.apiKey = prev;
    return false;
  }
}

function logout() {
  localStorage.removeItem('cairn_api_key');
  state.apiKey = null;
  state.messages = [];
  state.selectedId = null;
  disconnectStream();
  showLogin();
}

// ---------------------------------------------------------------------------
// Filter controls
// ---------------------------------------------------------------------------

function populateDbFilter() {
  const sel = document.getElementById('filter-db');
  // Keep the "All databases" option, remove any previously added options.
  while (sel.options.length > 1) sel.remove(1);
  for (const slug of state.knownTopics) {
    const opt = document.createElement('option');
    opt.value = slug;
    opt.textContent = slug;
    sel.appendChild(opt);
  }
}

function readFilters() {
  state.filters = {
    db:      document.getElementById('filter-db').value,
    agent:   document.getElementById('filter-agent').value.trim(),
    thread:  document.getElementById('filter-thread').value.trim(),
    type:    document.getElementById('filter-type').value,
    tags:    document.getElementById('filter-tags').value.trim(),
    promote: document.getElementById('filter-promote').value,
  };
}

function clearFilters() {
  document.getElementById('filter-db').value      = '';
  document.getElementById('filter-agent').value   = '';
  document.getElementById('filter-thread').value  = '';
  document.getElementById('filter-type').value    = '';
  document.getElementById('filter-tags').value    = '';
  document.getElementById('filter-promote').value = '';
  state.filters = { db: '', agent: '', thread: '', type: '', tags: '', promote: '' };
  loadMessages();
}

function buildQueryString(extra = {}) {
  const params = new URLSearchParams();
  const f = state.filters;
  if (f.db)      params.set('db',           f.db);
  if (f.agent)   params.set('agent_id',     f.agent);
  if (f.thread)  params.set('thread_id',    f.thread);
  if (f.type)    params.set('message_type', f.type);
  if (f.tags)    params.set('tags',         f.tags);
  if (f.promote) params.set('promote',      f.promote);
  for (const [k, v] of Object.entries(extra)) if (v) params.set(k, v);
  return params.toString() ? '?' + params.toString() : '';
}

// ---------------------------------------------------------------------------
// Message loading
// ---------------------------------------------------------------------------

async function loadMessages() {
  try {
    const qs = buildQueryString({ limit: '200' });
    const data = await apiFetch(`/messages${qs}`);
    state.messages = data;
    renderMessageList();
    updateSidebarStats();
  } catch (err) {
    if (err.status === 401) { logout(); return; }
    showEmptyState(`Failed to load messages: ${err.message}`);
  }
}

async function loadMessageDetail(id, db) {
  try {
    const msg = await apiFetch(`/messages/${id}?db=${encodeURIComponent(db)}`);
    renderDetailPanel(msg);
    state.selectedId = id;
    state.selectedDb = db;
    // Update selected state on cards.
    document.querySelectorAll('.message-card').forEach(el => {
      el.classList.toggle('selected', el.dataset.id === id);
    });
  } catch (err) {
    console.error('Failed to load message detail:', err);
  }
}

// ---------------------------------------------------------------------------
// SSE stream
// ---------------------------------------------------------------------------

function connectStream() {
  if (state.eventSource) state.eventSource.close();

  setStreamStatus('connecting');

  const url = `${API_BASE}/stream?token=${encodeURIComponent(state.apiKey)}`;
  const es = new EventSource(url);
  state.eventSource = es;

  es.onopen = () => setStreamStatus('connected');

  es.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      // Only prepend if it passes current filters.
      if (messageMatchesFilters(msg)) {
        state.messages.unshift(msg);
        prependMessageCard(msg);
        updateMessageCount();
        updateSidebarStats();
      }
    } catch { /* malformed event — ignore */ }
  };

  es.onerror = () => {
    setStreamStatus('disconnected');
    // EventSource auto-reconnects; update label while it does.
    setTimeout(() => {
      if (es.readyState === EventSource.CONNECTING) setStreamStatus('connecting');
    }, 1000);
  };
}

function disconnectStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  setStreamStatus('disconnected');
}

function setStreamStatus(status) {
  state.streamStatus = status;
  const indicator = document.getElementById('stream-indicator');
  const label     = document.getElementById('stream-label');
  indicator.className = `stream-indicator ${status}`;
  label.textContent = { connected: 'Live', connecting: 'Connecting…', disconnected: 'Disconnected' }[status];
}

// ---------------------------------------------------------------------------
// Filter matching (for live SSE events)
// ---------------------------------------------------------------------------

function messageMatchesFilters(msg) {
  const f = state.filters;
  if (f.db      && msg.topic_db     !== f.db)           return false;
  if (f.agent   && msg.agent_id     !== f.agent)         return false;
  if (f.thread  && msg.thread_id    !== f.thread)        return false;
  if (f.type    && msg.message_type !== f.type)          return false;
  if (f.promote && msg.promote      !== f.promote)       return false;
  if (f.tags) {
    const wanted = f.tags.split(',').map(t => t.trim()).filter(Boolean);
    if (wanted.length && !wanted.some(t => (msg.tags || []).includes(t))) return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

function relativeTime(isoString) {
  const diff = Date.now() - new Date(isoString).getTime();
  const s = Math.floor(diff / 1000);
  if (s <  60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function typeBadgeHtml(type) {
  return `<span class="badge badge-type-${CSS.escape(type)}">${type}</span>`;
}

function tlpBadgeHtml(tlp) {
  if (!tlp) return '';
  return `<span class="badge badge-tlp-${tlp}">TLP:${tlp.toUpperCase()}</span>`;
}

function promoteBadgeHtml(promote) {
  if (!promote || promote === 'none') return '';
  return `<span class="badge badge-promote-${promote}">${promote}</span>`;
}

function confidenceHtml(confidence) {
  if (confidence == null) return '';
  const pct = Math.round(confidence * 100);
  return `<span class="confidence-bar" title="${pct}% confidence">
    <span class="confidence-fill" style="width:${pct}%"></span>
  </span>`;
}

function buildMessageCard(msg) {
  const card = document.createElement('div');
  card.className = 'message-card';
  card.dataset.id = msg.id;
  card.dataset.db = msg.topic_db;

  const preview = (msg.body || '').replace(/^#{1,3}\s+/gm, '').replace(/[`*_]/g, '').slice(0, 120);
  const tagChips = (msg.tags || []).map(t =>
    `<span class="tag-chip" data-tag="${t}">#${t}</span>`
  ).join('');

  card.innerHTML = `
    <div class="card-header">
      <span class="card-agent">${msg.agent_id}</span>
      ${typeBadgeHtml(msg.message_type)}
      <span class="badge badge-db">${msg.topic_db}</span>
      ${tlpBadgeHtml(msg.tlp_level)}
      ${promoteBadgeHtml(msg.promote)}
      ${confidenceHtml(msg.confidence)}
    </div>
    ${preview ? `<div class="card-preview">${escapeHtml(preview)}</div>` : ''}
    <div class="card-footer">
      ${tagChips}
      <span class="card-time">${relativeTime(msg.timestamp)}</span>
    </div>
  `;

  card.addEventListener('click', (e) => {
    // Tag chip click → add to filter instead of opening detail.
    if (e.target.classList.contains('tag-chip')) {
      const tag = e.target.dataset.tag;
      const tagsInput = document.getElementById('filter-tags');
      const current = tagsInput.value.split(',').map(t => t.trim()).filter(Boolean);
      if (!current.includes(tag)) {
        tagsInput.value = [...current, tag].join(', ');
      }
      return;
    }
    loadMessageDetail(msg.id, msg.topic_db);
  });

  return card;
}

function renderMessageList() {
  const list = document.getElementById('message-list');
  list.innerHTML = '';

  if (!state.messages.length) {
    showEmptyState('No messages match the current filters.');
    return;
  }

  document.getElementById('empty-state').classList.add('hidden');
  const frag = document.createDocumentFragment();
  for (const msg of state.messages) {
    frag.appendChild(buildMessageCard(msg));
  }
  list.appendChild(frag);
  updateMessageCount();
}

function prependMessageCard(msg) {
  const list = document.getElementById('message-list');
  const empty = document.getElementById('empty-state');
  empty.classList.add('hidden');

  const card = buildMessageCard(msg);
  card.classList.add('new-flash');
  list.prepend(card);
  updateMessageCount();
}

function renderDetailPanel(msg) {
  document.getElementById('detail-placeholder').classList.add('hidden');
  const content = document.getElementById('detail-content');
  content.classList.remove('hidden');

  // Meta row
  document.getElementById('detail-meta').innerHTML = `
    <strong>${msg.agent_id}</strong>
    ${typeBadgeHtml(msg.message_type)}
    <span class="badge badge-db">${msg.topic_db}</span>
    ${tlpBadgeHtml(msg.tlp_level)}
    ${promoteBadgeHtml(msg.promote)}
    ${confidenceHtml(msg.confidence)}
    ${msg.thread_id ? `<span style="color:var(--text-muted);font-size:.75rem">thread: ${msg.thread_id}</span>` : ''}
    ${msg.in_reply_to ? `<span style="color:var(--text-muted);font-size:.75rem">↩ ${msg.in_reply_to.slice(0,8)}…</span>` : ''}
    <span style="color:var(--text-muted);font-size:.72rem;margin-left:auto">${new Date(msg.timestamp).toLocaleString()}</span>
  `;

  // Tags
  const tagsEl = document.getElementById('detail-tags');
  tagsEl.innerHTML = (msg.tags || []).map(t =>
    `<span class="tag-chip" data-tag="${t}">#${t}</span>`
  ).join('') || '<span style="color:var(--text-muted);font-size:.75rem">no tags</span>';

  // Bind tag chips in detail panel too
  tagsEl.querySelectorAll('.tag-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const tag = chip.dataset.tag;
      const tagsInput = document.getElementById('filter-tags');
      const current = tagsInput.value.split(',').map(t => t.trim()).filter(Boolean);
      if (!current.includes(tag)) tagsInput.value = [...current, tag].join(', ');
    });
  });

  // Markdown body
  const body = msg.body || '_No body content._';
  document.getElementById('detail-body').innerHTML = marked.parse(body);

  // Raw frontmatter
  document.getElementById('detail-frontmatter').textContent =
    JSON.stringify(msg.frontmatter, null, 2);
}

function showEmptyState(msg) {
  const empty = document.getElementById('empty-state');
  empty.textContent = msg;
  empty.classList.remove('hidden');
}

function updateMessageCount() {
  document.getElementById('message-count').textContent = `${state.messages.length}`;
}

function updateSidebarStats() {
  const agents = new Set(state.messages.map(m => m.agent_id)).size;
  const threads = new Set(state.messages.map(m => m.thread_id).filter(Boolean)).size;
  const candidates = state.messages.filter(m => m.promote === 'candidate').length;
  document.getElementById('sidebar-stats').innerHTML =
    `${state.messages.length} messages<br>${agents} agents<br>${threads} threads<br>${candidates} promote candidates`;
}

function escapeHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ---------------------------------------------------------------------------
// Promotion Queue
// ---------------------------------------------------------------------------

const promoState = {
  candidates: [],
};

async function loadPromotionCandidates() {
  const statusVal = document.getElementById('promo-filter-status').value;
  const qs = statusVal ? `?status=${encodeURIComponent(statusVal)}` : '';
  try {
    const data = await apiFetch(`/promotions${qs}`);
    promoState.candidates = data;
    renderPromotionList();
    updatePromotionBadge();
  } catch (err) {
    if (err.status === 401) { logout(); return; }
    document.getElementById('promo-empty-state').textContent = `Failed to load: ${err.message}`;
  }
}

function updatePromotionBadge() {
  const pending = promoState.candidates.filter(c => c.status === 'pending_review').length;
  const badge = document.getElementById('promotion-badge');
  if (pending > 0) {
    badge.textContent = pending;
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}

function renderPromotionList() {
  const list = document.getElementById('promotion-list');
  list.innerHTML = '';

  if (!promoState.candidates.length) {
    const empty = document.getElementById('promo-empty-state');
    empty.textContent = 'No promotion candidates match the current filter.';
    empty.classList.remove('hidden');
    list.appendChild(empty);
    return;
  }

  const frag = document.createDocumentFragment();
  for (const cand of promoState.candidates) {
    frag.appendChild(buildPromoCard(cand));
  }
  list.appendChild(frag);
}

function buildPromoCard(cand) {
  const card = document.createElement('div');
  card.className = `promo-card status-${cand.status}`;
  card.dataset.id = cand.id;

  const isPending = cand.status === 'pending_review';
  const confText = cand.confidence != null ? `conf ${Math.round(cand.confidence * 100)}%` : '';
  const sourceChips = (cand.source_message_ids || [])
    .map(id => `<span>${id.slice(0, 8)}…</span>`).join('');
  const vaultLinkHtml = cand.vault_path
    ? `<div class="promo-vault-link">📄 ${escapeHtml(cand.vault_path)}</div>` : '';

  card.innerHTML = `
    <div class="promo-card-header">
      <span class="promo-entity" title="${escapeHtml(cand.entity)}">${escapeHtml(cand.entity)}</span>
      <span class="badge badge-type-${CSS.escape(cand.entity_type)}">${escapeHtml(cand.entity_type)}</span>
      <span class="promo-trigger">${escapeHtml(cand.trigger)}</span>
      ${promoteBadgeHtml(cand.status.replace('pending_review', 'candidate'))}
      <span class="promo-chevron">▾</span>
    </div>
    <div class="promo-card-body">
      <div class="promo-meta">
        <span>${new Date(cand.created_at).toLocaleString()}</span>
        ${confText ? `<span>${confText}</span>` : ''}
        ${cand.reviewer_id ? `<span>reviewed by ${escapeHtml(cand.reviewer_id)}</span>` : ''}
      </div>
      ${cand.source_message_ids?.length
        ? `<div class="promo-sources">Sources: ${sourceChips}</div>` : ''}
      <div>
        <div class="promo-narrative-label">Narrative (editable before promoting):</div>
        <textarea class="promo-narrative" ${!isPending ? 'readonly' : ''}
          rows="4">${escapeHtml(cand.narrative || '')}</textarea>
      </div>
      ${vaultLinkHtml}
      ${isPending ? `
        <div class="promo-actions">
          <button class="btn-promote" data-id="${cand.id}">Promote to vault</button>
          <button class="btn-dismiss" data-id="${cand.id}">Dismiss</button>
        </div>` : ''}
    </div>
  `;

  // Toggle expand/collapse
  card.querySelector('.promo-card-header').addEventListener('click', () => {
    card.classList.toggle('expanded');
    // Show reviewer bar when opening a pending card
    if (card.classList.contains('expanded') && isPending) {
      document.getElementById('reviewer-bar').classList.remove('hidden');
    }
  });

  // Promote button
  const promoteBtn = card.querySelector('.btn-promote');
  if (promoteBtn) {
    promoteBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      await doPromote(cand.id, card);
    });
  }

  // Dismiss button
  const dismissBtn = card.querySelector('.btn-dismiss');
  if (dismissBtn) {
    dismissBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      await doDismiss(cand.id, card);
    });
  }

  return card;
}

async function doPromote(candidateId, card) {
  const reviewerIdentity = document.getElementById('reviewer-identity').value.trim();
  if (!reviewerIdentity) {
    alert('Enter your name or ID in the reviewer bar above before promoting.');
    return;
  }
  const narrative = card.querySelector('.promo-narrative')?.value || '';

  const promoteBtn  = card.querySelector('.btn-promote');
  const dismissBtn  = card.querySelector('.btn-dismiss');
  if (promoteBtn)  promoteBtn.disabled  = true;
  if (dismissBtn)  dismissBtn.disabled  = true;

  try {
    await apiFetch(`/promotions/${candidateId}/promote`, {
      method: 'POST',
      headers: {
        'X-Human-Reviewer': 'true',
        'X-Reviewer-Identity': reviewerIdentity,
      },
      body: JSON.stringify({ narrative }),
    });
    await loadPromotionCandidates();
  } catch (err) {
    alert(`Promote failed: ${err.message}`);
    if (promoteBtn) promoteBtn.disabled = false;
    if (dismissBtn) dismissBtn.disabled = false;
  }
}

async function doDismiss(candidateId, card) {
  const reviewerIdentity = document.getElementById('reviewer-identity').value.trim();
  if (!reviewerIdentity) {
    alert('Enter your name or ID in the reviewer bar above before dismissing.');
    return;
  }

  const promoteBtn  = card.querySelector('.btn-promote');
  const dismissBtn  = card.querySelector('.btn-dismiss');
  if (promoteBtn) promoteBtn.disabled = true;
  if (dismissBtn) dismissBtn.disabled = true;

  const reason = prompt('Reason for dismissal (optional):') || '';

  try {
    await apiFetch(`/promotions/${candidateId}/dismiss`, {
      method: 'POST',
      headers: {
        'X-Human-Reviewer': 'true',
        'X-Reviewer-Identity': reviewerIdentity,
      },
      body: JSON.stringify({ reason }),
    });
    await loadPromotionCandidates();
  } catch (err) {
    alert(`Dismiss failed: ${err.message}`);
    if (promoteBtn) promoteBtn.disabled = false;
    if (dismissBtn) dismissBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

function switchTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.classList.toggle('hidden', panel.id !== `tab-${tabName}`);
  });
  if (tabName === 'promotions') {
    loadPromotionCandidates();
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot() {
  if (state.apiKey) {
    const ok = await attemptLogin(state.apiKey);
    if (ok) {
      showApp();
      await loadMessages();
      connectStream();
      return;
    }
  }
  showLogin();
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const key = document.getElementById('api-key-input').value.trim();
  const errEl = document.getElementById('login-error');
  errEl.classList.add('hidden');
  const ok = await attemptLogin(key);
  if (ok) {
    showApp();
    await loadMessages();
    connectStream();
  } else {
    errEl.textContent = 'Invalid API key — check and try again.';
    errEl.classList.remove('hidden');
  }
});

document.getElementById('logout-btn').addEventListener('click', logout);

document.getElementById('apply-filters').addEventListener('click', () => {
  readFilters();
  loadMessages();
});

document.getElementById('clear-filters').addEventListener('click', clearFilters);

// Re-apply filters on Enter in text inputs.
['filter-agent', 'filter-thread', 'filter-tags'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { readFilters(); loadMessages(); }
  });
});

document.getElementById('detail-close').addEventListener('click', () => {
  document.getElementById('detail-content').classList.add('hidden');
  document.getElementById('detail-placeholder').classList.remove('hidden');
  document.querySelectorAll('.message-card.selected').forEach(el => el.classList.remove('selected'));
  state.selectedId = null;
  state.selectedDb = null;
});

// Refresh relative timestamps every minute.
setInterval(() => {
  document.querySelectorAll('.card-time').forEach((el, i) => {
    if (state.messages[i]) el.textContent = relativeTime(state.messages[i].timestamp);
  });
}, 60_000);

// Tab buttons
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// Promotion queue controls
document.getElementById('promo-filter-status').addEventListener('change', loadPromotionCandidates);
document.getElementById('promo-refresh').addEventListener('click', loadPromotionCandidates);

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

boot();
