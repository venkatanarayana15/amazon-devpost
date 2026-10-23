/* ==========================================================================
   DocOps Commander — web console
   This page is a real MCP client. It performs the initialize handshake, lists
   tools, and drives the agent with tools/call over Streamable HTTP at /mcp.
   ========================================================================== */

'use strict';

const MCP_URL = '/mcp';
const PROTOCOL_VERSION = '2025-11-25';
const SLA_WINDOW_HOURS = 12;

const DEMO_COMMANDS = [
  "Why are today's invoice jobs failing?",
  'Resolve safe failures and escalate high-risk documents',
  'What did we decide about Vendor Orion?'
];

/* ── element refs ─────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

const el = {
  convo: $('convo'),
  welcome: $('welcome'),
  welcomeSub: $('welcomeSub'),
  welcomeChips: $('welcomeChips'),
  form: $('composerForm'),
  input: $('cmdInput'),
  send: $('sendBtn'),
  tileRow: $('tileRow'),
  caseList: $('caseList'),
  memoryList: $('memoryList'),
  auditList: $('auditList'),
  caseCount: $('caseCount'),
  mcpPill: $('mcpPill'),
  mcpPillText: $('mcpPillText'),
  modal: $('modal'),
  modalKicker: $('modalKicker'),
  modalTitle: $('modalTitle'),
  modalBody: $('modalBody'),
  modalFoot: $('modalFoot'),
  btnApprove: $('btnApprove'),
  btnReject: $('btnReject'),
  btnClose: $('btnClose'),
  btnHelp: $('btnHelp'),
  btnReset: $('btnReset'),
  tabMemory: $('tabMemory'),
  tabCases: $('tabCases'),
  tabAudit: $('tabAudit'),
  toasts: $('toasts')
};

/* ── state ────────────────────────────────────────────────────────────── */

let rpcId = 0;
let mcpSession = null;
let agentSession = null;
let pendingAction = null;
let busy = false;
let pollTimer = null;
let lastFocused = null;

/* ── MCP transport ────────────────────────────────────────────────────── */

async function rpc(method, params, options) {
  const notify = options && options.notify;
  const body = { jsonrpc: '2.0', method: method };
  if (!notify) {
    rpcId += 1;
    body.id = rpcId;
  }
  if (params !== undefined) body.params = params;

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream'
  };
  if (mcpSession) headers['Mcp-Session-Id'] = mcpSession;

  const res = await fetch(MCP_URL, {
    method: 'POST',
    headers: headers,
    body: JSON.stringify(body)
  });

  const assigned = res.headers.get('Mcp-Session-Id');
  if (assigned) mcpSession = assigned;

  if (notify) return null;
  if (!res.ok) throw new Error('MCP transport error (HTTP ' + res.status + ')');

  const data = await res.json();
  if (data.error) throw new Error(data.error.message || 'MCP error');
  return data.result;
}

async function callTool(name, args) {
  const result = await rpc('tools/call', {
    name: name,
    arguments: args || {}
  });
  if (result && result.isError) {
    const text = (result.content || []).map((c) => c.text).join(' ');
    throw new Error(text || name + ' failed');
  }
  return (result && result.structuredContent) || {};
}

async function connectMcp() {
  el.mcpPill.classList.add('pending');
  el.mcpPill.classList.remove('bad');
  el.mcpPillText.textContent = 'Connecting to MCP…';

  const init = await rpc('initialize', {
    protocolVersion: PROTOCOL_VERSION,
    capabilities: {},
    clientInfo: { name: 'docops-commander-web', version: '1.0.0' }
  });

  await rpc('notifications/initialized', undefined, { notify: true });

  const list = await rpc('tools/list', {});
  const count = (list.tools || []).length;

  el.mcpPill.classList.remove('pending');
  el.mcpPillText.textContent = 'MCP ' + (init.protocolVersion || PROTOCOL_VERSION) + ' · ' + count + ' tools';
  el.mcpPill.title =
    'Connected to ' + init.serverInfo.name + ' ' + init.serverInfo.version +
    ' over Streamable HTTP — ' + count + ' tools';
  return count;
}

/* ── small helpers ────────────────────────────────────────────────────── */

const clock = () =>
  new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => {
    if (c === '&') return '&amp;';
    if (c === '<') return '&lt;';
    if (c === '>') return '&gt;';
    if (c === '"') return '&quot;';
    return '&#39;';
  });
}

function highlight(text) {
  return esc(polish(text))
    .replace(/(\$[\d,]+)/g, '<span class="hl-money">$1</span>')
    .replace(/\b(POLICY-[A-Z]+-\d+)\b/g, '<span class="hl-code">$1</span>')
    .replace(/\b(JOB-\d+)\b/g, '<span class="hl-code hl-job">$1</span>');
}

const PLURALS = {
  case: 'cases',
  failure: 'failures',
  vendor: 'vendors',
  document: 'documents',
  retry: 'retries',
  item: 'items',
  decision: 'decisions',
  policy: 'policies'
};

const PLURAL_RE = /(\d+)\s+((?:[\w-]+\s+){0,2}?)\b(case|failure|vendor|document|retry|item|decision|policy)\(s\)/gi;

function polish(text) {
  return String(text == null ? '' : text)
    .replace(PLURAL_RE, (match, n, middle, word) => {
      const singular = word.toLowerCase();
      const pre = middle ? middle.trim() + ' ' : '';
      return n + ' ' + pre + (Number(n) === 1 ? singular : PLURALS[singular]);
    })
    .replace(/(\d+)\s+x\s+/g, '$1 × ');
}

function scrollDown() {
  requestAnimationFrame(() => {
    el.convo.scrollTop = el.convo.scrollHeight;
  });
}

function emptyEl(text) {
  const div = document.createElement('div');
  div.className = 'empty';
  div.textContent = text;
  return div;
}

function toast(message, kind) {
  const div = document.createElement('div');
  div.className = 'toast' + (kind === 'err' ? ' err' : kind === 'info' ? ' info' : '');
  div.textContent = message;
  el.toasts.appendChild(div);
  window.setTimeout(() => {
    div.style.transition = 'opacity .2s ease';
    div.style.opacity = '0';
    window.setTimeout(() => div.remove(), 240);
  }, 4200);
}

/* ── conversation rendering ───────────────────────────────────────────── */

function appendUser(text) {
  const msg = document.createElement('article');
  msg.className = 'msg msg-user';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.setAttribute('aria-hidden', 'true');
  avatar.textContent = 'YOU';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  const p = document.createElement('p');
  p.className = 'reply-text';
  p.textContent = text;
  bubble.appendChild(p);

  msg.append(avatar, bubble);
  el.convo.appendChild(msg);
  scrollDown();
}

function appendTyping() {
  const msg = document.createElement('article');
  msg.className = 'msg msg-agent';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.setAttribute('aria-hidden', 'true');
  avatar.textContent = 'DOC';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  const dots = document.createElement('div');
  dots.className = 'typing';
  dots.setAttribute('role', 'status');
  dots.setAttribute('aria-label', 'DocOps is working');
  for (let i = 0; i < 3; i += 1) dots.appendChild(document.createElement('span'));
  bubble.appendChild(dots);

  msg.append(avatar, bubble);
  el.convo.appendChild(msg);
  scrollDown();
  return msg;
}

function appendAgent(data) {
  const msg = document.createElement('article');
  msg.className = 'msg msg-agent';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.setAttribute('aria-hidden', 'true');
  avatar.textContent = 'DOC';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  const head = document.createElement('div');
  head.className = 'bubble-head';
  const name = document.createElement('span');
  name.className = 'agent-name';
  name.textContent = 'DocOps';
  const time = document.createElement('span');
  time.className = 'msg-time';
  time.textContent = clock();
  head.append(name, time);

  const text = document.createElement('div');
  text.className = 'reply-text';
  text.innerHTML = highlight(data.reply || '');

  bubble.append(head, text);

  if (data.pending_action) bubble.appendChild(planCard(data.pending_action));
  if (data.options && data.options.length) bubble.appendChild(optionsRow(data.options));
  if (data.trace && data.trace.length) {
    bubble.appendChild(traceBlock(data.trace, data.trace_ms));
  }

  msg.append(avatar, bubble);
  el.convo.appendChild(msg);
  scrollDown();
}

/* Highlight the decision cards a recall actually matched. */

function markMemoryMatches(matches) {
  const ids = new Set(
    (matches || [])
      .map((m) => (m && m.id != null ? String(m.id) : null))
      .filter(Boolean)
  );
  if (!ids.size) return;

  let hits = 0;
  document.querySelectorAll('#memoryList .mem').forEach((box) => {
    const on = ids.has(box.dataset.id || '');
    box.classList.toggle('match', on);
    if (on) hits += 1;
  });

  if (hits && el.tabMemory) el.tabMemory.classList.add('has-match');
}

function appendNote(text) {
  appendAgent({ reply: text });
}

function optionsRow(options) {
  const row = document.createElement('div');
  row.className = 'options';
  options.forEach((opt) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'opt';
    btn.textContent = opt.label;
    btn.addEventListener('click', () => {
      if (opt.send) send(opt.send);
    });
    row.appendChild(btn);
  });
  return row;
}

function traceBlock(trace, totalMs) {
  const details = document.createElement('details');
  details.className = 'trace';

  const summary = document.createElement('summary');
  const ms = totalMs != null
    ? totalMs
    : trace.reduce((sum, step) => sum + (step.ms || 0), 0);
  summary.textContent =
    trace.length + ' tool step' + (trace.length === 1 ? '' : 's') + ' · ' + ms + ' ms';
  details.appendChild(summary);

  const steps = document.createElement('div');
  steps.className = 'trace-steps';

  trace.forEach((step) => {
    const row = document.createElement('div');
    row.className = 'trace-step';

    const tool = document.createElement('span');
    tool.className = 'trace-tool';
    tool.textContent = step.tool;

    const result = document.createElement('span');
    result.className = 'trace-result';
    result.textContent = polish(step.result || '');

    const time = document.createElement('span');
    time.className = 'trace-ms';
    time.textContent = (step.ms != null ? step.ms : 0) + ' ms';

    row.append(tool, result, time);
    steps.appendChild(row);
  });

  details.appendChild(steps);
  return details;
}

/* ── plan card ────────────────────────────────────────────────────────── */

function planCard(action) {
  const wrap = document.createElement('div');
  wrap.className = 'plan';
  wrap.dataset.actionId = action.action_id;

  const head = document.createElement('div');
  head.className = 'plan-head';
  const title = document.createElement('span');
  title.className = 'plan-title';
  title.textContent = polish(action.title);
  const badge = document.createElement('span');
  badge.className = 'plan-badge';
  badge.textContent = 'Needs approval';
  head.append(title, badge);
  wrap.appendChild(head);

  (action.segments || []).forEach((seg, index) => {
    const row = document.createElement('div');
    row.className = 'segment';

    const marker = document.createElement('span');
    marker.className = 'seg-marker seg-' + seg.action_type;
    marker.textContent = String(index + 1);

    const body = document.createElement('div');
    body.className = 'seg-body';

    const label = document.createElement('div');
    label.className = 'seg-label';
    label.textContent = polish(seg.label);

    const detail = document.createElement('div');
    detail.className = 'seg-detail';
    detail.textContent = seg.detail || '';

    const jobs = document.createElement('div');
    jobs.className = 'seg-jobs';
    (seg.job_ids || []).forEach((jobId) => {
      const chip = document.createElement('span');
      chip.className = 'job-chip';
      chip.textContent = jobId;
      jobs.appendChild(chip);
    });

    body.append(label, detail, jobs);
    row.append(marker, body);
    wrap.appendChild(row);
  });

  if (action.risk_note) {
    const risk = document.createElement('div');
    risk.className = 'plan-risk';
    risk.textContent = polish(action.risk_note);
    wrap.appendChild(risk);
  }

  if (action.policies && action.policies.length) {
    const policies = document.createElement('div');
    policies.className = 'plan-policies';
    action.policies.forEach((id) => {
      const chip = document.createElement('span');
      chip.className = 'hl-code';
      chip.textContent = id;
      policies.appendChild(chip);
    });
    wrap.appendChild(policies);
  }

  const actions = document.createElement('div');
  actions.className = 'plan-actions';

  const later = document.createElement('button');
  later.type = 'button';
  later.className = 'btn-quiet';
  later.textContent = 'Not now';
  later.addEventListener('click', () => rejectPlan(action.action_id));

  const approve = document.createElement('button');
  approve.type = 'button';
  approve.className = 'btn-cta';
  approve.textContent = 'Review & approve';
  approve.addEventListener('click', () => openApproval(action));

  actions.append(later, approve);
  wrap.appendChild(actions);

  return wrap;
}

function markPlan(actionId, label, tone) {
  const cards = document.querySelectorAll('.plan');
  cards.forEach((card) => {
    if (card.dataset.actionId !== actionId) return;

    const head = card.querySelector('.plan-head .plan-badge');
    if (head) {
      head.textContent = label;
      head.style.background = tone === 'ok' ? 'var(--ok-bg)' : 'var(--surface-2)';
      head.style.color = tone === 'ok' ? 'var(--ok)' : 'var(--ink-3)';
      head.style.borderColor = tone === 'ok' ? 'var(--ok-line)' : 'var(--line)';
    }

    const row = card.querySelector('.plan-actions');
    if (!row) return;
    row.textContent = '';
    const note = document.createElement('span');
    note.className = 'plan-badge';
    note.textContent = label;
    if (tone === 'ok') {
      note.style.background = 'var(--ok-bg)';
      note.style.color = 'var(--ok)';
      note.style.borderColor = 'var(--ok-line)';
    } else {
      note.style.background = 'var(--surface-2)';
      note.style.color = 'var(--ink-3)';
      note.style.borderColor = 'var(--line)';
    }
    row.appendChild(note);
  });
}

/* ── modal ────────────────────────────────────────────────────────────── */

function openModal() {
  lastFocused = document.activeElement;
  el.modal.classList.add('is-open');
  el.modal.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  el.modal.classList.remove('is-open');
  el.modal.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
  pendingAction = null;
  if (lastFocused && typeof lastFocused.focus === 'function') {
    lastFocused.focus();
  }
  lastFocused = null;
}

function openApproval(action) {
  pendingAction = action;

  el.modalKicker.textContent = 'Human approval required';
  el.modalKicker.className = 'modal-kicker';
  el.modalTitle.textContent = polish(action.title);
  el.modalFoot.classList.remove('hidden');
  el.btnApprove.disabled = false;
  el.btnApprove.textContent = 'Approve plan';

  el.modalBody.textContent = '';

  const intro = document.createElement('p');
  intro.textContent =
    'Nothing has run yet. Approving executes this plan and writes the decision to memory.';
  el.modalBody.appendChild(intro);

  const list = document.createElement('div');
  list.className = 'summary-list';

  (action.segments || []).forEach((seg, index) => {
    const row = document.createElement('div');
    row.className = 'summary-row';

    const marker = document.createElement('span');
    marker.className = 'seg-marker seg-' + seg.action_type;
    marker.textContent = String(index + 1);

    const label = document.createElement('span');
    label.textContent = polish(seg.label);

    const count = document.createElement('span');
    count.textContent = String((seg.job_ids || []).length);

    row.append(marker, label, count);
    list.appendChild(row);
  });

  el.modalBody.appendChild(list);

  if (action.risk_note) {
    const risk = document.createElement('p');
    risk.style.marginTop = '11px';
    risk.textContent = polish(action.risk_note);
    el.modalBody.appendChild(risk);
  }

  if (action.policies && action.policies.length) {
    const policies = document.createElement('p');
    policies.style.marginTop = '11px';

    const strong = document.createElement('b');
    strong.textContent = 'Policies cited: ';
    policies.appendChild(strong);

    action.policies.forEach((id, index) => {
      const chip = document.createElement('span');
      chip.className = 'hl-code';
      chip.textContent = id;
      policies.appendChild(chip);
      if (index < action.policies.length - 1) {
        policies.appendChild(document.createTextNode(' '));
      }
    });

    el.modalBody.appendChild(policies);
  }

  openModal();
  el.btnApprove.focus();
}

const HELP_HTML = [
  '<p><b>DocOps Commander</b> is an agentic console for document-operations teams. ' +
    'It diagnoses processing failures, drafts a policy-cited resolution plan, and then ' +
    'waits for a human before anything reaches production.</p>',
  '<ul>',
  '<li><b>Diagnose</b> — ask why a group of documents is failing. You get failure patterns, ' +
    'SLA pressure and dollar exposure.</li>',
  '<li><b>Plan</b> — ask to resolve safe failures. The agent drafts a three-part plan ' +
    '(retry · human review · escalate) citing the policies it applied.</li>',
  '<li><b>Approve</b> — the approval gate is the only path to execution. Declining is ' +
    'recorded just as durably as approving.</li>',
  '<li><b>Recall</b> — ask what was decided about a vendor. The agent searches its decision ' +
    'memory and cites the original plan and outcome.</li>',
  '</ul>',
  '<p style="margin-top:10px">This page is a real <b>MCP client</b>: it performs the ' +
    '<code>initialize</code> handshake, lists tools and calls them over Streamable HTTP at ' +
    '<code>/mcp</code>. Narration is deterministic, so the demo needs no API key.</p>'
].join('');

function openHelp() {
  pendingAction = null;
  el.modalKicker.textContent = 'About this demo';
  el.modalKicker.className = 'modal-kicker plain';
  el.modalTitle.textContent = 'How DocOps Commander works';
  el.modalBody.innerHTML = HELP_HTML;
  el.modalFoot.classList.add('hidden');
  openModal();
  el.btnClose.focus();
}

/* ── board rendering ──────────────────────────────────────────────────── */

function renderTiles(tiles) {
  el.tileRow.textContent = '';
  tiles.forEach((tile) => {
    const box = document.createElement('div');
    box.className = 'tile ' + (tile.tone || 'info');

    const value = document.createElement('div');
    value.className = 'tile-value';
    value.textContent = tile.value;

    const label = document.createElement('div');
    label.className = 'tile-label';
    label.textContent = tile.label;

    const foot = document.createElement('div');
    foot.className = 'tile-foot';
    foot.textContent = tile.foot || '';

    box.append(value, label, foot);
    el.tileRow.appendChild(box);
  });
}

function renderCases(cards) {
  el.caseList.textContent = '';

  if (!cards.length) {
    el.caseList.appendChild(emptyEl('No cases on the board. Everything is handled.'));
    return;
  }

  cards.forEach((card) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'case';
    item.setAttribute('aria-expanded', 'false');

    const prio = document.createElement('span');
    prio.className = 'prio ' + String(card.priority || 'P2').toLowerCase();
    prio.textContent = card.priority || 'P2';

    const main = document.createElement('div');
    main.className = 'case-main';

    const top = document.createElement('div');
    top.className = 'case-top';
    const vendor = document.createElement('span');
    vendor.className = 'case-vendor';
    vendor.textContent = card.vendor || 'Unknown vendor';
    const amount = document.createElement('span');
    amount.className = 'case-amount';
    amount.textContent = card.amount_label || '';
    top.append(vendor, amount);

    const meta = document.createElement('div');
    meta.className = 'case-meta';
    meta.textContent =
      (card.doc_type || 'document') + ' · ' + card.job_id +
      (card.sla_label ? ' · ' + card.sla_label : '');

    const tags = document.createElement('div');
    tags.className = 'case-tags';
    const status = document.createElement('span');
    status.className = 'pill ' + card.status;
    status.textContent = card.status_label || card.status;
    const failure = document.createElement('span');
    failure.className = 'pill tag';
    failure.textContent = card.failure_label || card.failure_code || '';
    tags.append(status, failure);

    const detail = document.createElement('div');
    detail.className = 'case-detail';
    detail.innerHTML =
      '<p><b>Why:</b> ' + esc(card.cause || '—') + '</p>' +
      '<p><b>Recommended:</b> ' + esc(card.recommendation_label || '—') +
      (card.remedy ? ' — ' + esc(card.remedy) : '') + '</p>' +
      '<p><b>Rationale:</b> ' + esc(card.rationale || '—') + '</p>' +
      '<p><b>Policy:</b> ' + esc((card.policies || []).join(', ') || 'none') + '</p>';

    main.append(top, meta, tags, detail);
    item.append(prio, main);

    item.addEventListener('click', () => {
      const open = item.classList.toggle('is-open');
      item.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    el.caseList.appendChild(item);
  });
}

function renderMemory(items) {
  el.memoryList.textContent = '';

  if (!items.length) {
    el.memoryList.appendChild(
      emptyEl('No decisions stored yet. Approve a plan and it will be remembered here.')
    );
    return;
  }

  items.forEach((decision) => {
    const box = document.createElement('div');
    box.className = 'mem';
    if (decision.id != null) box.dataset.id = String(decision.id);

    const top = document.createElement('div');
    top.className = 'mem-top';
    const title = document.createElement('span');
    title.textContent = polish(decision.title || 'Decision');
    const kind = document.createElement('span');
    const outcomeText = String(decision.outcome || '');
    const lower = outcomeText.toLowerCase();
    const declined = lower.indexOf('reject') !== -1;
    const approved = lower.indexOf('approve') !== -1;
    const held = lower.indexOf('held') !== -1;
    kind.className = 'mem-kind' + (declined ? ' rejected' : approved ? '' : ' waiting');
    kind.textContent = declined
      ? 'Declined'
      : approved
        ? 'Approved'
        : held
          ? 'Held'
          : 'Awaiting approval';
    top.append(title, kind);

    const detail = decision.detail || {};
    const summary = declined
      ? detail.reason
      : approved
        ? detail.result
        : held
          ? detail.reason || 'No jobs moved.'
          : detail.risk_note || 'Nothing has run yet.';

    const outcome = document.createElement('div');
    outcome.className = 'mem-out';
    outcome.textContent = polish(summary || '');

    const meta = document.createElement('div');
    meta.className = 'mem-meta';
    const when = decision.created_at
      ? new Date(decision.created_at).toLocaleString()
      : '';
    meta.textContent =
      when + ' · ' + (decision.job_ids || []).length + ' jobs · ' +
      (decision.policies || []).length + ' policies';

    box.append(top);
    if (summary) box.append(outcome);
    box.append(meta);
    el.memoryList.appendChild(box);
  });
}

function renderAudit(items) {
  el.auditList.textContent = '';

  if (!items.length) {
    el.auditList.appendChild(emptyEl('No audit events yet.'));
    return;
  }

  items.forEach((entry) => {
    const row = document.createElement('div');
    row.className = 'audit';

    const body = document.createElement('div');
    body.className = 'audit-body';

    const event = document.createElement('div');
    event.className = 'audit-event';
    event.textContent = String(entry.event || '').replace(/_/g, ' ');

    const detail = document.createElement('div');
    detail.className = 'audit-detail';
    detail.textContent = polish(entry.detail || '');

    body.append(event, detail);

    const time = document.createElement('span');
    time.className = 'audit-time';
    time.textContent = entry.created_at
      ? new Date(entry.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : '';

    row.append(body, time);
    el.auditList.appendChild(row);
  });
}

function renderBoard(board) {
  const stats = board.stats || {};
  renderTiles(stats.tiles || []);
  renderCases(board.cards || []);
  el.caseCount.textContent = String((board.cards || []).length);
  renderMemory(board.memory || []);
  renderAudit(board.audit || []);
}

function renderWelcome(board) {
  const stats = board.stats || {};
  const failed = stats.failed || 0;
  const value = stats.value_at_risk_label || '$0';
  const atRisk = stats.sla_at_risk || 0;
  const breached = stats.sla_breached || 0;

  if (failed > 0) {
    el.welcomeSub.textContent =
      failed + ' documents are failing, carrying ' + value + ' of exposure. ' +
      atRisk + ' sit inside the ' + SLA_WINDOW_HOURS + '‑hour SLA window' +
      (breached ? ' and ' + breached + ' is already breached.' : '.');
  } else {
    el.welcomeSub.textContent =
      'The board is clear. Every document has been handled — ask me what was decided.';
  }
}

function seedChips() {
  el.welcomeChips.textContent = '';
  const icon =
    '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M5 12h13M12 6l6 6-6 6"/></svg>';

  DEMO_COMMANDS.forEach((command) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'chip';
    chip.innerHTML = icon + '<span></span>';
    const label = chip.querySelector('span');
    label.textContent = command;
    chip.addEventListener('click', () => send(command));
    el.welcomeChips.appendChild(chip);
  });
}

/* ── agent actions ────────────────────────────────────────────────────── */

async function refreshBoard() {
  try {
    const board = await callTool('get_board');
    renderBoard(board);
    return board;
  } catch (err) {
    return null;
  }
}

async function send(text) {
  const value = String(text || '').trim();
  if (!value || busy) return;

  busy = true;
  el.send.disabled = true;
  el.input.value = '';
  el.welcome.classList.add('hidden');

  appendUser(value);
  const typing = appendTyping();

  try {
    const data = await callTool('orchestrate', {
      utterance: value,
      session_id: agentSession || undefined
    });

    if (data.session_id) agentSession = data.session_id;

    typing.remove();
    appendAgent(data);
    await refreshBoard();
    if (data.intent === 'recall' && Array.isArray(data.memory) && data.memory.length) {
      markMemoryMatches(data.memory);
    }

    if (data.pending_action && data.pending_action.requires_approval) {
      openApproval(data.pending_action);
    }
  } catch (err) {
    typing.remove();
    appendNote("I couldn't complete that: " + err.message);
    toast(err.message, 'err');
  } finally {
    busy = false;
    el.send.disabled = false;
    el.input.focus();
  }
}

async function approvePlan() {
  if (!pendingAction) return;
  const action = pendingAction;
  const actionId = action.action_id;

  el.btnApprove.disabled = true;
  el.btnApprove.textContent = 'Approving…';

  try {
    const result = await callTool('approve_plan', { action_id: actionId, actor: 'operator' });
    closeModal();

    if (result.state) renderBoard(result.state);
    markPlan(actionId, 'Approved', 'ok');

    const summary = result.summary || 'plan executed';
    appendNote('Approved. ' + summary.charAt(0).toUpperCase() + summary.slice(1) + '.');
    toast('Approved — ' + summary, 'ok');

    startRecoveryPoll();
  } catch (err) {
    toast(err.message, 'err');
    el.btnApprove.disabled = false;
    el.btnApprove.textContent = 'Approve plan';
  }
}

async function rejectPlan(actionId) {
  try {
    const result = await callTool('reject_plan', {
      action_id: actionId,
      reason: 'Operator declined at the approval gate'
    });

    if (result.state) renderBoard(result.state);
    markPlan(actionId, 'Declined', 'muted');

    if (pendingAction && pendingAction.action_id === actionId) closeModal();

    appendNote('Declined. Nothing executed; the decision is recorded in memory.');
    toast('Plan declined — nothing executed', 'info');
  } catch (err) {
    toast(err.message, 'err');
  }
}

function startRecoveryPoll() {
  if (pollTimer) window.clearInterval(pollTimer);

  let ticks = 0;
  pollTimer = window.setInterval(async () => {
    ticks += 1;
    const board = await refreshBoard();
    if (!board) return;

    const recovered = (board.stats && board.stats.recovered) || 0;
    if (recovered > 0 || ticks >= 6) {
      window.clearInterval(pollTimer);
      pollTimer = null;
      if (recovered > 0) {
        const noun = recovered === 1 ? 'document' : 'documents';
        appendNote(
          recovered + ' retried ' + noun + ' parsed successfully and moved to the posting queue.'
        );
        toast(recovered + ' retried ' + noun + ' recovered', 'ok');
      }
    }
  }, 1500);
}

async function resetDemo() {
  try {
    const board = await callTool('reset_demo');
    renderBoard(board);

    el.convo.querySelectorAll('.msg').forEach((node) => node.remove());
    el.welcome.classList.remove('hidden');
    renderWelcome(board);
    closeModal();

    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }

    agentSession = null;
    toast('Demo reset — 17 synthetic jobs restored', 'info');
  } catch (err) {
    toast(err.message, 'err');
  }
}

/* ── wiring ───────────────────────────────────────────────────────────── */

el.form.addEventListener('submit', (event) => {
  event.preventDefault();
  send(el.input.value);
});

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((other) => {
      other.classList.remove('is-active');
      other.setAttribute('aria-selected', 'false');
    });
    tab.classList.add('is-active');
    tab.setAttribute('aria-selected', 'true');

    const name = tab.dataset.tab;
    el.caseList.classList.toggle('hidden', name !== 'cases');
    el.memoryList.classList.toggle('hidden', name !== 'memory');
    el.auditList.classList.toggle('hidden', name !== 'audit');
    if (name === 'memory') tab.classList.remove('has-match');
  });
});

el.btnApprove.addEventListener('click', approvePlan);

el.btnReject.addEventListener('click', () => {
  if (pendingAction) rejectPlan(pendingAction.action_id);
  else closeModal();
});

el.btnClose.addEventListener('click', closeModal);

el.modal.addEventListener('click', (event) => {
  if (event.target === el.modal) closeModal();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && el.modal.classList.contains('is-open')) {
    closeModal();
  }
});

el.btnHelp.addEventListener('click', openHelp);
el.btnReset.addEventListener('click', resetDemo);

/* ── boot ─────────────────────────────────────────────────────────────── */

(async function boot() {
  seedChips();

  try {
    await connectMcp();
  } catch (err) {
    el.mcpPill.classList.remove('pending');
    el.mcpPill.classList.add('bad');
    el.mcpPillText.textContent = 'MCP offline';
    el.welcomeSub.textContent =
      'The MCP server is not reachable. Start the server with ' +
      'python -m uvicorn backend.main:app --port 8010, then reload.';
    toast('MCP server unreachable — start uvicorn and reload', 'err');
    return;
  }

  const board = await refreshBoard();
  if (board) {
    renderWelcome(board);
  } else {
    el.welcomeSub.textContent = 'Connected to MCP, but the board could not be loaded.';
  }
})();
