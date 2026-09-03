// Hermes Karma Frontend Application Logic

let currentTab = 'sessions';
let currentPage = 0;
const pageSize = 25;
let activeSessionId = null;
let currentSessionData = null;
let modelChartInstance = null;
let providerChartInstance = null;
let toolChartInstance = null;
let allSkills = [];
let allDiscoveredModels = [];
let liveEventSource = null;

// Helper to escape HTML characters
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  lucide.createIcons();
  loadSessions();
  setupLiveStream();
});

// Tab Navigation
function switchTab(tabId) {
  currentTab = tabId;
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove('bg-brand-600/20', 'text-brand-400', 'border', 'border-brand-500/30', 'text-white');
    btn.classList.add('text-slate-400');
  });

  const activeBtn = document.getElementById(`tab-${tabId}`);
  if (activeBtn) {
    activeBtn.classList.remove('text-slate-400');
    activeBtn.classList.add('bg-brand-600/20', 'text-brand-400', 'border', 'border-brand-500/30', 'text-white');
  }

  document.querySelectorAll('.view-panel').forEach(panel => panel.classList.add('hidden'));
  const activePanel = document.getElementById(`view-${tabId}`);
  if (activePanel) activePanel.classList.remove('hidden');

  if (tabId === 'sessions') loadSessions();
  else if (tabId === 'analytics') loadAnalytics();
  else if (tabId === 'pantheon') loadPantheon();
  else if (tabId === 'skills') loadSkills();
  else if (tabId === 'memory') loadMemory();
  else if (tabId === 'live') loadLiveSessions();
  else if (tabId === 'cron') loadCron();
  else if (tabId === 'nodes') loadNodes();
  else if (tabId === 'tickets') loadTickets();

  lucide.createIcons();
}

// ----------------------------------------------------
// SESSIONS VIEW
// ----------------------------------------------------
let searchTimeout = null;
function debounceLoadSessions() {
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {
    currentPage = 0;
    loadSessions();
  }, 300);
}

function setPlatformFilter(platform) {
  document.getElementById('filterPlatform').value = platform;
  currentPage = 0;
  loadSessions();
}

function onModelSelectChange(val) {
  document.getElementById('filterModel').value = val;
  currentPage = 0;
  loadSessions();
}

async function loadSessions() {
  const tbody = document.getElementById('sessionsTableBody');
  const search = document.getElementById('sessionSearch').value;
  const platform = document.getElementById('filterPlatform').value;
  const model = document.getElementById('filterModel').value;

  const params = new URLSearchParams({
    limit: pageSize,
    offset: currentPage * pageSize
  });
  if (search) params.append('search', search);
  if (platform) params.append('source', platform);
  if (model) params.append('model', model);

  try {
    const res = await fetch(`/api/sessions?${params.toString()}`);
    const data = await res.json();
    
    // Update model dropdown if returned
    if (data.all_models && Array.isArray(data.all_models)) {
      populateModelDropdown(data.all_models);
    }

    document.getElementById('paginationInfo').innerText = `Showing ${Math.min(data.total, currentPage * pageSize + 1)} - ${Math.min(data.total, (currentPage + 1) * pageSize)} of ${data.total} sessions`;
    document.getElementById('prevPageBtn').disabled = currentPage === 0;
    document.getElementById('nextPageBtn').disabled = (currentPage + 1) * pageSize >= data.total;

    if (!data.sessions || data.sessions.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center py-12 text-slate-500">No matching sessions found.</td></tr>`;
      return;
    }

    tbody.innerHTML = data.sessions.map(s => {
      const started = s.started_at ? new Date(s.started_at * 1000).toLocaleString() : 'Unknown';
      const costVal = s.estimated_cost_usd !== null ? s.estimated_cost_usd : 0.0;
      const cost = costVal > 0 ? `$${costVal.toFixed(4)}` : '$0.00';
      const source = s.source || 'cli';
      const title = s.title || s.session_id.substring(0, 16);
      const branch = s.git_branch ? `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px] font-mono"><i data-lucide="git-branch" class="w-2.5 h-2.5 inline"></i> ${s.git_branch}</span>` : '';
      
      const tickets = (s.metadata?.tickets || []).map(t => 
        `<span class="px-1.5 py-0.5 rounded bg-brand-950/60 border border-brand-500/30 text-brand-300 text-[10px] font-mono">${t.ticket_key}</span>`
      ).join(' ');

      const detectedTicket = s.detected_ticket ? 
        `<span class="px-1.5 py-0.5 rounded bg-amber-950/60 border border-amber-500/30 text-amber-300 text-[10px] font-mono" title="Auto-detected from git branch">${s.detected_ticket}*</span>` : '';

      const subagentBadge = (s.subagent_count && s.subagent_count > 0) ?
        `<span class="px-1.5 py-0.5 rounded bg-indigo-950/80 border border-indigo-500/30 text-indigo-300 text-[10px] font-mono flex items-center space-x-1" title="${s.subagent_count} subagents dispatched"><i data-lucide="bot" class="w-2.5 h-2.5"></i><span>${s.subagent_count} sub</span></span>` : '';

      // Render comprehensive models used badges
      const modelsList = s.models_used || [];
      let modelPills = '';
      if (modelsList.length > 0) {
        modelPills = modelsList.map(m => {
          const mName = m.model || 'unknown';
          const isLocal = mName.includes('qwen') || mName.includes('chunkito') || mName.includes('deepseek') || mName.includes('gemma') || mName.includes('gpt-oss') || mName.includes('gguf');
          const badgeBg = isLocal ? 'bg-indigo-950/80 text-indigo-300 border-indigo-500/30' : 'bg-brand-950/80 text-brand-300 border-brand-500/30';
          return `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono truncate max-w-[150px] inline-block ${badgeBg}" title="${mName} (${(m.input_tokens || 0).toLocaleString()} tok)">${mName}</span>`;
        }).join(' ');
      } else {
        const topModel = s.model || 'default';
        modelPills = `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px] font-mono truncate max-w-[150px] inline-block">${topModel}</span>`;
      }

      const totalTok = (s.input_tokens || 0) + (s.output_tokens || 0);
      const cacheTok = s.cache_read_tokens || 0;
      const reasTok = s.reasoning_tokens || 0;

      return `
        <tr class="hover:bg-slate-800/30 transition group cursor-pointer" onclick="openSessionTimeline('${s.session_id}')">
          <td class="py-3 px-4 max-w-xs">
            <div class="font-medium text-slate-200 truncate group-hover:text-brand-400 transition" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
            <div class="text-[11px] text-slate-400 font-mono truncate flex flex-wrap items-center gap-1.5 mt-0.5">
              <span>${s.session_id}</span>
              ${branch}
              ${tickets}
              ${detectedTicket}
              ${subagentBadge}
            </div>
          </td>
          <td class="py-3 px-3">
            <span class="px-2 py-0.5 rounded-full text-xs font-semibold ${getPlatformBadgeClass(source)}">
              ${source}
            </span>
          </td>
          <td class="py-3 px-3 text-xs">
            <div class="flex flex-col gap-1 max-w-[180px]">
              ${modelPills}
            </div>
          </td>
          <td class="py-3 px-3 text-xs text-slate-300 font-mono">
            <div>${s.message_count || 0} msgs</div>
            <div class="text-slate-400 text-[11px]">${s.tool_call_count || 0} tools</div>
          </td>
          <td class="py-3 px-3 text-xs text-slate-300 font-mono">
            <div>${totalTok.toLocaleString()} tok</div>
            <div class="text-emerald-400 text-[10px]" title="Cache read tokens">${cacheTok.toLocaleString()} cache</div>
            ${reasTok > 0 ? `<div class="text-purple-400 text-[10px]" title="Reasoning tokens">${reasTok.toLocaleString()} reason</div>` : ''}
          </td>
          <td class="py-3 px-3 text-xs font-mono font-semibold ${costVal === 0 ? 'text-emerald-400' : 'text-amber-400'}">
            ${costVal === 0 ? '<span class="text-emerald-400 bg-emerald-950/60 border border-emerald-500/20 px-1.5 py-0.5 rounded text-[11px]">$0.00 (APU)</span>' : cost}
          </td>
          <td class="py-3 px-3 text-xs text-slate-400 whitespace-nowrap">
            ${started}
          </td>
          <td class="py-3 px-4 text-right" onclick="event.stopPropagation()">
            <button onclick="openSessionTimeline('${s.session_id}')" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-brand-600 hover:text-white text-slate-300 text-xs font-medium transition">
              Inspect
            </button>
          </td>
        </tr>
      `;
    }).join('');

    lucide.createIcons();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center py-12 text-rose-400">Failed to load sessions: ${err.message}</td></tr>`;
  }
}

function populateModelDropdown(models) {
  allDiscoveredModels = models;
  const select = document.getElementById('filterModelSelect');
  if (!select) return;
  const currentVal = select.value;

  const options = ['<option value="">All Models</option>'];
  models.forEach(m => {
    const locTag = m.is_local ? '⚡ ' : '☁️ ';
    options.push(`<option value="${escapeHtml(m.name)}" ${m.name === currentVal ? 'selected' : ''}>${locTag}${escapeHtml(m.name)}</option>`);
  });
  select.innerHTML = options.join('');
}

function getPlatformBadgeClass(p) {
  const map = {
    cli: 'bg-emerald-950/40 text-emerald-400 border border-emerald-500/20',
    webui: 'bg-indigo-950/40 text-indigo-400 border border-indigo-500/20',
    telegram: 'bg-sky-950/40 text-sky-400 border border-sky-500/20',
    discord: 'bg-purple-950/40 text-purple-400 border border-purple-500/20',
    slack: 'bg-amber-950/40 text-amber-400 border border-amber-500/20',
    cron: 'bg-rose-950/40 text-rose-400 border border-rose-500/20',
  };
  return map[(p || '').toLowerCase()] || 'bg-slate-800 text-slate-300';
}

function changePage(delta) {
  currentPage += delta;
  if (currentPage < 0) currentPage = 0;
  loadSessions();
}

// ----------------------------------------------------
// SESSION TIMELINE & REPLAY DRAWER
// ----------------------------------------------------
async function openSessionTimeline(sessionId) {
  activeSessionId = sessionId;
  document.getElementById('timelineModal').classList.remove('hidden');
  document.getElementById('modalSessionId').innerText = sessionId;
  document.getElementById('modalSessionTitle').innerText = 'Loading session...';
  switchModalTab('replay');

  try {
    const [sessRes, timeRes] = await Promise.all([
      fetch(`/api/sessions/${sessionId}`),
      fetch(`/api/sessions/${sessionId}/timeline`)
    ]);

    const session = await sessRes.json();
    const timelineData = await timeRes.json();
    currentSessionData = session;

    document.getElementById('modalSessionTitle').innerText = session.title || 'Untitled Session';
    document.getElementById('modalSessionStatus').innerText = `${session.source || 'cli'} • ${session.model || 'default'}`;

    renderTimelineEvents(timelineData.timeline);
    renderRawTranscript(session.messages);
    renderSubagents(session.delegations || []);
    renderSessionMetaTab(session.metadata || {});

    lucide.createIcons();
  } catch (err) {
    document.getElementById('modalSessionTitle').innerText = 'Error loading session';
  }
}

function closeTimelineModal() {
  document.getElementById('timelineModal').classList.add('hidden');
  activeSessionId = null;
  currentSessionData = null;
}

function switchModalTab(tab) {
  ['replay', 'transcript', 'subagents', 'metadata'].forEach(t => {
    document.getElementById(`modalTab-${t}`).className = 'py-1 text-slate-400 hover:text-slate-200';
    document.getElementById(`modalContent-${t}`).classList.add('hidden');
  });

  document.getElementById(`modalTab-${tab}`).className = 'py-1 text-brand-400 border-b-2 border-brand-500 font-semibold';
  document.getElementById(`modalContent-${tab}`).classList.remove('hidden');
}

function renderTimelineEvents(events) {
  const container = document.getElementById('timelineEventsContainer');
  if (!events || events.length === 0) {
    container.innerHTML = `<div class="text-slate-500 text-xs">No timeline events recorded.</div>`;
    return;
  }

  container.innerHTML = events.map(e => {
    let icon = 'message-square';
    let iconBg = 'bg-slate-800 text-slate-300';
    let cardBg = 'bg-dark-950 border-slate-800';

    if (e.type === 'thought') {
      icon = 'brain';
      iconBg = 'bg-purple-900/60 text-purple-300 border border-purple-500/30';
      cardBg = 'bg-purple-950/10 border-purple-900/40';
    } else if (e.type === 'tool_call') {
      icon = 'terminal';
      iconBg = 'bg-amber-900/60 text-amber-300 border border-amber-500/30';
      cardBg = 'bg-amber-950/10 border-amber-900/40';
    } else if (e.type === 'tool_result') {
      icon = e.status === 'error' ? 'alert-triangle' : 'check-circle-2';
      iconBg = e.status === 'error' ? 'bg-rose-900/60 text-rose-300 border border-rose-500/30' : 'bg-emerald-900/60 text-emerald-300 border border-emerald-500/30';
      cardBg = e.status === 'error' ? 'bg-rose-950/10 border-rose-900/40' : 'bg-emerald-950/10 border-emerald-900/40';
    } else if (e.type === 'user_message') {
      icon = 'user';
      iconBg = 'bg-indigo-900/60 text-indigo-300 border border-indigo-500/30';
      cardBg = 'bg-indigo-950/10 border-indigo-900/40';
    } else if (e.type === 'assistant_response') {
      icon = 'bot';
      iconBg = 'bg-brand-900/60 text-brand-300 border border-brand-500/30';
    }

    const timeStr = e.timestamp ? new Date(e.timestamp * 1000).toLocaleTimeString() : '';

    return `
      <div class="relative group">
        <div class="absolute -left-[30px] top-1.5 w-6 h-6 rounded-full ${iconBg} flex items-center justify-center text-xs shadow-md">
          <i data-lucide="${icon}" class="w-3.5 h-3.5"></i>
        </div>

        <div class="border ${cardBg} rounded-xl p-4 shadow-sm text-xs space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-semibold text-slate-200 uppercase tracking-wider text-[11px]">${escapeHtml(e.title)}</span>
            <div class="flex items-center space-x-2 text-slate-400 font-mono text-[10px]">
              <span>Step #${e.step_number}</span>
              <span>•</span>
              <span>${timeStr}</span>
            </div>
          </div>

          ${e.arguments ? `
            <div class="mt-2">
              <div class="text-[10px] text-slate-400 font-mono mb-1">Arguments:</div>
              <pre class="p-2.5 rounded bg-dark-900 border border-slate-800 text-slate-300 font-mono overflow-x-auto text-[11px] whitespace-pre-wrap">${escapeHtml(e.arguments)}</pre>
            </div>
          ` : ''}

          ${e.content ? `
            <div class="mt-2">
              <pre class="p-2.5 rounded bg-dark-900 border border-slate-800 text-slate-300 font-mono overflow-x-auto text-[11px] whitespace-pre-wrap max-h-72 overflow-y-auto">${escapeHtml(e.content)}</pre>
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }).join('');
}

function renderRawTranscript(messages) {
  const container = document.getElementById('rawTranscriptContainer');
  if (!messages || messages.length === 0) {
    container.innerHTML = `<div class="text-slate-500 text-xs">No messages.</div>`;
    return;
  }

  container.innerHTML = messages.map(m => `
    <div class="p-3 rounded-lg border border-slate-800 bg-dark-950 space-y-1">
      <div class="flex justify-between text-slate-400 text-[10px]">
        <span class="font-bold text-slate-300 uppercase">${m.role}</span>
        <span>${m.timestamp ? new Date(m.timestamp * 1000).toLocaleString() : ''}</span>
      </div>
      <div class="text-slate-200 whitespace-pre-wrap">${escapeHtml(m.content || '(no content text)')}</div>
    </div>
  `).join('');
}

function renderSubagents(delegations) {
  const container = document.getElementById('subagentsContainer');
  if (!delegations || delegations.length === 0) {
    container.innerHTML = `<div class="text-slate-500 text-xs">No background subagents or delegations dispatched in this session.</div>`;
    return;
  }

  container.innerHTML = delegations.map(d => `
    <div class="p-4 rounded-xl border border-slate-800 bg-dark-950 space-y-2 text-xs">
      <div class="flex justify-between items-center">
        <span class="font-semibold text-slate-200 font-mono">${d.delegation_id}</span>
        <span class="px-2 py-0.5 rounded text-[10px] font-bold ${d.state === 'completed' ? 'bg-emerald-950 text-emerald-400' : 'bg-amber-950 text-amber-400'} uppercase">${d.state}</span>
      </div>
      <div class="text-slate-400 text-[11px]">Dispatched: ${d.dispatched_at ? new Date(d.dispatched_at * 1000).toLocaleString() : ''}</div>
      ${d.task_parsed ? `<div class="text-slate-300 bg-dark-900 p-2 rounded border border-slate-800 font-mono text-[11px] whitespace-pre-wrap">${escapeHtml(JSON.stringify(d.task_parsed, null, 2))}</div>` : ''}
    </div>
  `).join('');
}

function renderSessionMetaTab(meta) {
  document.getElementById('metaTagsInput').value = (meta.tags || []).join(', ');
  document.getElementById('metaNotesInput').value = meta.notes || '';
  
  const ticketsList = document.getElementById('modalTicketsList');
  ticketsList.innerHTML = (meta.tickets || []).map(t => `
    <div class="flex items-center justify-between p-2 rounded bg-dark-900 border border-slate-800">
      <span class="font-mono font-bold text-brand-300">${t.provider.toUpperCase()}: ${t.ticket_key}</span>
      <button onclick="removeTicketLink('${t.ticket_key}')" class="text-rose-400 hover:text-rose-300 text-xs">Remove</button>
    </div>
  `).join('');
}

async function saveSessionMeta() {
  if (!activeSessionId) return;
  const tags = document.getElementById('metaTagsInput').value.split(',').map(s => s.trim()).filter(Boolean);
  const notes = document.getElementById('metaNotesInput').value;

  await fetch(`/api/sessions/${activeSessionId}/metadata`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags, notes })
  });
  alert('Metadata updated successfully');
  loadSessions();
}

async function addTicketToSession() {
  if (!activeSessionId) return;
  const provider = document.getElementById('ticketProviderInput').value;
  const ticket_key = document.getElementById('ticketKeyInput').value.trim();
  const url = document.getElementById('ticketUrlInput').value.trim();

  if (!ticket_key) return alert('Please provide ticket key');

  await fetch(`/api/sessions/${activeSessionId}/tickets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, ticket_key, url })
  });

  const res = await fetch(`/api/sessions/${activeSessionId}`);
  const sess = await res.json();
  renderSessionMetaTab(sess.metadata || {});
  loadSessions();
}

async function removeTicketLink(ticketKey) {
  if (!activeSessionId) return;
  await fetch(`/api/sessions/${activeSessionId}/tickets/${ticketKey}`, { method: 'DELETE' });
  const res = await fetch(`/api/sessions/${activeSessionId}`);
  const sess = await res.json();
  renderSessionMetaTab(sess.metadata || {});
  loadSessions();
}

async function triggerFocusActive() {
  if (!activeSessionId) return;
  try {
    const res = await fetch(`/api/live-sessions/${activeSessionId}/focus-terminal`, {
      method: 'POST'
    });
    const data = await res.json();
    if (data.success) {
      alert(`Terminal focus triggered via ${data.method}!`);
    } else {
      alert(`Terminal focus notice: ${data.message}`);
    }
  } catch (err) {
    alert(`Could not trigger focus: ${err.message}`);
  }
}

// ----------------------------------------------------
// ANALYTICS VIEW
// ----------------------------------------------------
let currentAnalyticsRange = 'all';

async function setAnalyticsTimeRange(range) {
  currentAnalyticsRange = range;
  document.querySelectorAll('.time-range-btn').forEach(btn => {
    if (btn.getAttribute('data-range') === range) {
      btn.className = 'time-range-btn px-3 py-1 rounded font-medium transition bg-brand-500/20 text-brand-300 border border-brand-500/30';
    } else {
      btn.className = 'time-range-btn px-3 py-1 rounded font-medium transition text-slate-400 hover:text-white';
    }
  });
  await loadAnalytics();
}
window.setAnalyticsTimeRange = setAnalyticsTimeRange;

async function loadAnalytics() {
  try {
    const res = await fetch(`/api/analytics/overview?time_range=${currentAnalyticsRange}`);
    const data = await res.json();

    document.getElementById('statTotalSessions').innerText = data.total_sessions.toLocaleString();
    document.getElementById('statTotalMessages').innerText = `${(data.total_messages || 0).toLocaleString()} turns • ${(data.total_api_calls || 0).toLocaleString()} API calls`;
    
    const totalTokens = (data.total_input_tokens || 0) + (data.total_output_tokens || 0);
    document.getElementById('statTotalTokens').innerText = totalTokens.toLocaleString();
    document.getElementById('statTokensSub').innerText = `In: ${(data.total_input_tokens || 0).toLocaleString()} | Out: ${(data.total_output_tokens || 0).toLocaleString()} | Reason: ${(data.total_reasoning_tokens || 0).toLocaleString()}`;

    document.getElementById('statCacheHitRate').innerText = `${data.cache_hit_rate_pct || 0}%`;
    document.getElementById('statCacheRead').innerText = `${(data.total_cache_read_tokens || 0).toLocaleString()} cached read tokens`;

    document.getElementById('statTotalCost').innerText = `$${(data.total_estimated_cost_usd || 0).toFixed(2)}`;
    document.getElementById('statLocalSessions').innerText = `${data.local_zero_cost_ratio_pct || 0}% local free tokens (${(data.local_sessions_count || 0).toLocaleString()} APU sessions)`;

    // Render Charts
    renderAnalyticsCharts(data);

    // Render Models Table
    const tbody = document.getElementById('modelsTableBody');
    tbody.innerHTML = (data.model_distribution || []).map(m => {
      const isLoc = m.is_local;
      const tagBadge = isLoc ? 
        '<span class="px-1.5 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-500/30 text-[10px] font-mono">⚡ APU / Local</span>' : 
        `<span class="px-1.5 py-0.5 rounded bg-brand-950/80 text-brand-300 border border-brand-500/30 text-[10px] font-mono">☁️ ${m.provider_category || 'Cloud'}</span>`;

      const costStr = m.cost_usd && m.cost_usd > 0 ? `$${m.cost_usd.toFixed(4)}` : '<span class="text-emerald-400">$0.00 (APU)</span>';

      return `
        <tr class="hover:bg-slate-800/40">
          <td class="py-2.5 px-3 font-semibold text-slate-200">${escapeHtml(m.model)}</td>
          <td class="py-2.5 px-3">${tagBadge}</td>
          <td class="py-2.5 px-3">${(m.session_count || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-slate-400">${(m.api_call_count || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-slate-400">${(m.input_tokens || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-slate-400">${(m.output_tokens || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-emerald-400">${(m.cache_read_tokens || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-purple-400">${(m.reasoning_tokens || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 font-bold">${costStr}</td>
        </tr>
      `;
    }).join('');

  } catch (err) {
    console.error(err);
  }
}

function renderAnalyticsCharts(data) {
  // 1. Model chart
  const modelCtx = document.getElementById('modelChart');
  if (modelChartInstance) modelChartInstance.destroy();

  const modelLabels = (data.model_distribution || []).map(m => m.model);
  const modelTokens = (data.model_distribution || []).map(m => (m.input_tokens || 0) + (m.output_tokens || 0));

  modelChartInstance = new Chart(modelCtx, {
    type: 'doughnut',
    data: {
      labels: modelLabels,
      datasets: [{
        data: modelTokens,
        backgroundColor: ['#8b5cf6', '#6366f1', '#10b981', '#f59e0b', '#ec4899', '#06b6d4', '#3b82f6', '#14b8a6', '#a855f7', '#e11d48']
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#94a3b8', font: { size: 9 }, boxWidth: 10 } }
      }
    }
  });

  // 2. Provider breakdown chart
  const providerCtx = document.getElementById('providerChart');
  if (providerChartInstance) providerChartInstance.destroy();

  const providerLabels = (data.provider_distribution || []).map(p => p.provider);
  const providerTokens = (data.provider_distribution || []).map(p => (p.input_tokens || 0) + (p.output_tokens || 0));

  providerChartInstance = new Chart(providerCtx, {
    type: 'bar',
    data: {
      labels: providerLabels,
      datasets: [{
        label: 'Tokens',
        data: providerTokens,
        backgroundColor: ['#10b981', '#8b5cf6', '#6366f1', '#f59e0b', '#06b6d4']
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#94a3b8', font: { size: 9 } } },
        y: { ticks: { color: '#94a3b8', font: { size: 9 } } }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });

  // 3. Tool chart
  const toolCtx = document.getElementById('toolChart');
  if (toolChartInstance) toolChartInstance.destroy();

  const toolLabels = (data.tool_distribution || []).slice(0, 8).map(t => t.tool_name);
  const toolCounts = (data.tool_distribution || []).slice(0, 8).map(t => t.count);

  toolChartInstance = new Chart(toolCtx, {
    type: 'bar',
    data: {
      labels: toolLabels,
      datasets: [{
        label: 'Executions',
        data: toolCounts,
        backgroundColor: '#6366f1'
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: '#94a3b8', font: { size: 9 } } },
        y: { ticks: { color: '#94a3b8', font: { size: 9 } } }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });
}

// ----------------------------------------------------
// PANTHEON SWARM VIEW
// ----------------------------------------------------
let currentPantheonProfiles = [];
let activePantheonAgent = null;

async function loadPantheon() {
  try {
    const res = await fetch('/api/pantheon/profiles');
    const data = await res.json();
    currentPantheonProfiles = data.profiles || [];
    const summ = data.summary || {};

    const kpiAgents = document.getElementById('pantheonKpiAgents');
    if (kpiAgents) kpiAgents.innerText = `${summ.total_agents || 8} Agents`;

    const kpiLocal = document.getElementById('pantheonKpiLocal');
    if (kpiLocal) kpiLocal.innerText = `${summ.local_agents || 7} APU/Local • ${summ.cloud_agents || 1} Cloud`;

    const kpiSessions = document.getElementById('pantheonKpiSessions');
    if (kpiSessions) kpiSessions.innerText = `${(summ.total_sessions || 0).toLocaleString()} Sessions`;

    const kpiTokens = document.getElementById('pantheonKpiTokens');
    if (kpiTokens) kpiTokens.innerText = (summ.total_tokens || 0).toLocaleString();

    const kpiTokensSub = document.getElementById('pantheonKpiTokensSub');
    if (kpiTokensSub) kpiTokensSub.innerText = `In: ${(summ.total_input_tokens || 0).toLocaleString()} | Out: ${(summ.total_output_tokens || 0).toLocaleString()}`;

    const kpiSkills = document.getElementById('pantheonKpiSkills');
    if (kpiSkills) kpiSkills.innerText = `${(summ.total_skills || 0).toLocaleString()} Skills`;

    const grid = document.getElementById('pantheonGrid');
    if (!grid) return;

    if (currentPantheonProfiles.length === 0) {
      grid.innerHTML = `<div class="col-span-full text-center py-12 text-slate-500">No agent profiles found in ~/.hermes/profiles/</div>`;
      return;
    }

    grid.innerHTML = currentPantheonProfiles.map(p => {
      const isLocal = p.is_local;
      const modelTag = isLocal ? 
        '<span class="px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-500/30 text-[10px] font-mono font-semibold">⚡ APU / Local</span>' : 
        '<span class="px-1.5 py-0.5 rounded bg-brand-950 text-brand-300 border border-brand-500/30 text-[10px] font-mono font-semibold">☁️ Frontier Cloud</span>';

      return `
        <div class="bg-dark-900 border border-slate-800/80 hover:border-brand-500/50 rounded-xl p-5 shadow-sm space-y-4 flex flex-col justify-between transition group">
          <div class="space-y-3.5">
            <div class="flex items-start justify-between">
              <div class="flex items-center space-x-3">
                <span class="text-3xl p-2 rounded-xl bg-dark-950 border border-slate-800 group-hover:scale-105 transition">${p.emoji || '🤖'}</span>
                <div>
                  <h3 class="text-base font-bold text-white tracking-tight">${escapeHtml(p.name)}</h3>
                  <span class="text-[11px] font-mono text-brand-300 font-medium">${escapeHtml(p.role)}</span>
                </div>
              </div>
            </div>

            <div class="p-2.5 rounded-lg bg-dark-950 border border-slate-800/80 space-y-1">
              <div class="flex items-center justify-between text-[11px] font-mono">
                <span class="text-slate-400">Model & Target:</span>
                ${modelTag}
              </div>
              <div class="text-xs font-mono font-bold text-slate-200 truncate" title="${escapeHtml(p.model)}">
                ${escapeHtml(p.model)}
              </div>
            </div>

            <p class="text-xs text-slate-400 line-clamp-3 leading-relaxed">
              ${escapeHtml(p.personality_summary || p.description || p.domain)}
            </p>

            <div class="grid grid-cols-2 gap-2 pt-2 border-t border-slate-800/60 font-mono text-[11px]">
              <div class="p-2 rounded bg-dark-950/60 border border-slate-800/60">
                <div class="text-slate-500 text-[10px]">Sessions</div>
                <div class="text-white font-bold">${(p.total_sessions || 0).toLocaleString()}</div>
              </div>
              <div class="p-2 rounded bg-dark-950/60 border border-slate-800/60">
                <div class="text-slate-500 text-[10px]">Tokens</div>
                <div class="text-indigo-300 font-bold">${(p.total_tokens || 0).toLocaleString()}</div>
              </div>
              <div class="p-2 rounded bg-dark-950/60 border border-slate-800/60">
                <div class="text-slate-500 text-[10px]">Skills</div>
                <div class="text-purple-300 font-bold">${p.skills_count || 0}</div>
              </div>
              <div class="p-2 rounded bg-dark-950/60 border border-slate-800/60">
                <div class="text-slate-500 text-[10px]">Memory</div>
                <div class="text-emerald-400 font-bold">${p.memory_chars > 0 ? `${p.memory_chars} c` : 'Ready'}</div>
              </div>
            </div>
          </div>

          <button onclick="openPantheonDrawer('${p.id}')" class="w-full py-2 px-3 rounded-lg bg-slate-800 hover:bg-brand-600/30 hover:text-brand-300 border border-slate-700 hover:border-brand-500/50 text-slate-200 text-xs font-medium flex items-center justify-center space-x-2 transition">
            <i data-lucide="eye" class="w-3.5 h-3.5"></i>
            <span>Deep Dive Profile</span>
          </button>
        </div>
      `;
    }).join('');

    lucide.createIcons();
  } catch (err) {
    console.error('Failed to load Pantheon profiles:', err);
  }
}
window.loadPantheon = loadPantheon;

async function openPantheonDrawer(profileId) {
  try {
    const res = await fetch(`/api/pantheon/profiles/${profileId}`);
    const p = await res.json();
    activePantheonAgent = p;

    document.getElementById('drawerAvatar').innerText = p.emoji || '🤖';
    document.getElementById('drawerName').innerText = `${p.name} (${p.id})`;
    document.getElementById('drawerRoleBadge').innerText = p.role;
    document.getElementById('drawerDomain').innerText = p.domain;

    const localBadge = document.getElementById('drawerLocalBadge');
    if (localBadge) {
      localBadge.innerText = p.is_local ? '⚡ APU / Local' : '☁️ Frontier Cloud';
      localBadge.className = p.is_local ? 
        'px-2 py-0.5 rounded text-xs font-mono font-semibold bg-emerald-950 text-emerald-300 border border-emerald-500/30' : 
        'px-2 py-0.5 rounded text-xs font-mono font-semibold bg-brand-950 text-brand-300 border border-brand-500/30';
    }

    document.getElementById('drawerSoulChars').innerText = `${(p.soul_raw || '').length.toLocaleString()} characters`;
    document.getElementById('drawerSoulRaw').innerText = p.soul_raw || 'No SOUL.md system prompt defined for this agent profile.';

    const sessCount = document.getElementById('drawerSessionsCount');
    if (sessCount) sessCount.innerText = `${(p.sessions || []).length} recorded sessions`;
    const sessTbody = document.getElementById('drawerSessionsTableBody');
    if (sessTbody) {
      if (!p.sessions || p.sessions.length === 0) {
        sessTbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-slate-500">No sessions executed in ~/.hermes/profiles/${p.id}/state.db yet</td></tr>`;
      } else {
        sessTbody.innerHTML = p.sessions.map(s => {
          const dateStr = s.started_at ? new Date(s.started_at * 1000).toLocaleString() : 'N/A';
          const tok = (s.input_tokens || 0) + (s.output_tokens || 0);
          return `
            <tr class="hover:bg-slate-800/40">
              <td class="py-2.5 px-3 text-brand-400 font-semibold">${escapeHtml(s.id)}</td>
              <td class="py-2.5 px-3 text-slate-200 font-medium">${escapeHtml(s.title || 'Untitled Session')}</td>
              <td class="py-2.5 px-3 text-slate-400">${escapeHtml(s.model || p.model)}</td>
              <td class="py-2.5 px-3 text-slate-400">${s.message_count || 0}</td>
              <td class="py-2.5 px-3 text-indigo-300">${tok.toLocaleString()}</td>
              <td class="py-2.5 px-3 text-slate-500 text-[11px]">${dateStr}</td>
            </tr>
          `;
        }).join('');
      }
    }

    const skillsCount = document.getElementById('drawerSkillsCount');
    if (skillsCount) skillsCount.innerText = `${(p.skills || []).length} specialized skills`;
    const skillsGrid = document.getElementById('drawerSkillsGrid');
    if (skillsGrid) {
      if (!p.skills || p.skills.length === 0) {
        skillsGrid.innerHTML = `<div class="col-span-full py-6 text-center text-slate-500">Inherits all core skills from ~/.hermes/skills/</div>`;
      } else {
        skillsGrid.innerHTML = p.skills.slice(0, 30).map(sk => `
          <div class="p-3 rounded-lg bg-dark-950 border border-slate-800 space-y-1">
            <div class="flex items-center justify-between">
              <span class="font-bold text-white font-mono text-xs">${escapeHtml(sk.name)}</span>
              <span class="px-1.5 py-0.5 rounded bg-slate-900 border border-slate-800 text-[10px] font-mono text-slate-400">${escapeHtml(sk.category)}</span>
            </div>
            <p class="text-[11px] text-slate-400 line-clamp-2">${escapeHtml(sk.description || 'Procedural skill playbook')}</p>
          </div>
        `).join('');
      }
    }

    document.getElementById('drawerMemoryRaw').innerText = p.memory_raw || 'No profile-specific MEMORY.md saved yet. Agent uses global memory.';
    document.getElementById('drawerUserRaw').innerText = p.user_raw || 'No profile-specific USER.md saved yet. Agent uses global user profile.';

    document.getElementById('drawerConfigRaw').innerText = p.config_raw || JSON.stringify(p.config || {}, null, 2);

    switchDrawerTab('soul');
    document.getElementById('pantheonDetailDrawer').classList.remove('hidden');
    lucide.createIcons();
  } catch (err) {
    console.error('Failed to open Pantheon drawer:', err);
  }
}
window.openPantheonDrawer = openPantheonDrawer;

function closePantheonDrawer() {
  const drawer = document.getElementById('pantheonDetailDrawer');
  if (drawer) drawer.classList.add('hidden');
}
window.closePantheonDrawer = closePantheonDrawer;

function switchDrawerTab(tabName) {
  const tabs = ['soul', 'sessions', 'skills', 'memory', 'config'];
  tabs.forEach(t => {
    const btn = document.getElementById(`drawer-tab-${t}`);
    const content = document.getElementById(`drawer-content-${t}`);
    if (t === tabName) {
      if (btn) btn.className = 'py-3 text-brand-400 border-b-2 border-brand-500 font-semibold';
      if (content) content.classList.remove('hidden');
    } else {
      if (btn) btn.className = 'py-3 text-slate-400 hover:text-slate-200';
      if (content) content.classList.add('hidden');
    }
  });
}
window.switchDrawerTab = switchDrawerTab;

// ----------------------------------------------------
// SKILLS VIEW
// ----------------------------------------------------
async function loadSkills() {
  try {
    const res = await fetch('/api/skills');
    const data = await res.json();
    allSkills = data.skills || [];
    document.getElementById('skillsCountPill').innerText = `${allSkills.length} Skills Cataloged`;
    renderSkillsList(allSkills);
  } catch (err) {
    console.error(err);
  }
}

function renderSkillsList(skills) {
  const container = document.getElementById('skillsListContainer');
  if (!skills.length) {
    container.innerHTML = `<div class="p-4 text-xs text-slate-500">No skills found.</div>`;
    return;
  }

  container.innerHTML = skills.map(s => `
    <div class="p-3 hover:bg-slate-800/50 cursor-pointer transition" onclick="selectSkill('${s.name}')">
      <div class="font-bold text-slate-200 text-xs font-mono">${escapeHtml(s.name)}</div>
      <div class="text-[11px] text-slate-400 truncate mt-0.5">${escapeHtml(s.description || 'No description')}</div>
      <div class="flex items-center space-x-2 mt-1.5 text-[10px] text-slate-500">
        <span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-medium">${s.category}</span>
        <span>v${s.version}</span>
      </div>
    </div>
  `).join('');
}

function filterSkillsList() {
  const query = document.getElementById('skillFilter').value.toLowerCase();
  const filtered = allSkills.filter(s => s.name.toLowerCase().includes(query) || (s.description && s.description.toLowerCase().includes(query)));
  renderSkillsList(filtered);
}

function selectSkill(skillName) {
  const skill = allSkills.find(s => s.name === skillName);
  if (!skill) return;

  document.getElementById('skillDetailEmpty').classList.add('hidden');
  document.getElementById('skillDetailContent').classList.remove('hidden');

  document.getElementById('skillDetailName').innerText = skill.name;
  document.getElementById('skillDetailDesc').innerText = skill.description || 'Autonomous skill playbook';
  document.getElementById('skillDetailCategory').innerText = `${skill.category} • v${skill.version}`;
  document.getElementById('skillDetailCode').innerText = skill.content;
}

// ----------------------------------------------------
// MEMORY & USER VIEW
// ----------------------------------------------------
async function loadMemory() {
  try {
    const res = await fetch('/api/memory');
    const data = await res.json();

    document.getElementById('memoryCharCount').innerText = `${data.memory?.character_count || 0} chars`;
    document.getElementById('userCharCount').innerText = `${data.user?.character_count || 0} chars`;

    const memContainer = document.getElementById('memoryItemsList');
    memContainer.innerHTML = (data.memory?.items || []).map(item => `
      <div class="p-3 rounded-lg border border-slate-800/80 bg-dark-950 text-xs text-slate-300 space-y-1">
        <p class="leading-relaxed whitespace-pre-wrap">${escapeHtml(item)}</p>
      </div>
    `).join('');

    const userContainer = document.getElementById('userItemsList');
    userContainer.innerHTML = (data.user?.items || []).map(item => `
      <div class="p-3 rounded-lg border border-slate-800/80 bg-dark-950 text-xs text-slate-300 space-y-1">
        <p class="leading-relaxed whitespace-pre-wrap">${escapeHtml(item)}</p>
      </div>
    `).join('');
  } catch (err) {
    console.error(err);
  }
}

// ----------------------------------------------------
// LIVE SESSIONS & SSE TELEMETRY
// ----------------------------------------------------
async function loadLiveSessions() {
  try {
    const res = await fetch('/api/live-sessions');
    const data = await res.json();
    renderLiveCards(data.live_sessions || []);
  } catch (err) {
    console.error(err);
  }
}

function renderLiveCards(sessions) {
  const container = document.getElementById('liveSessionsContainer');
  if (!sessions.length) {
    container.innerHTML = `<div class="col-span-3 text-center py-12 text-slate-500">No active live sessions detected.</div>`;
    return;
  }

  container.innerHTML = sessions.map(s => {
    let statusClass = 'bg-emerald-950 text-emerald-400 border border-emerald-500/30';
    if (s.status === 'RUNNING_TOOL') statusClass = 'bg-amber-950 text-amber-400 border border-amber-500/30 animate-pulse';
    else if (s.status === 'WAITING') statusClass = 'bg-indigo-950 text-indigo-400 border border-indigo-500/30';
    else if (s.status === 'STALE') statusClass = 'bg-slate-800 text-slate-400';

    return `
      <div class="bg-dark-900 border border-slate-800/80 rounded-xl p-5 shadow-sm space-y-3 flex flex-col justify-between">
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <span class="px-2.5 py-0.5 rounded-full text-[11px] font-bold ${statusClass}">
              ${s.status}
            </span>
            <span class="text-xs text-slate-400 font-mono">${s.platform || 'cli'}</span>
          </div>

          <h3 class="font-bold text-slate-200 text-sm truncate" title="${escapeHtml(s.title)}">${escapeHtml(s.title || 'Session')}</h3>
          <p class="text-xs text-slate-400 font-mono truncate">${s.session_id}</p>

          ${s.current_tool ? `
            <div class="p-2 rounded bg-dark-950 border border-amber-500/30 text-amber-300 font-mono text-xs flex items-center space-x-2">
              <i data-lucide="terminal" class="w-3.5 h-3.5"></i>
              <span>Running: <strong>${escapeHtml(s.current_tool)}</strong></span>
            </div>
          ` : ''}

          <div class="text-[11px] text-slate-400 font-mono space-y-0.5 pt-2 border-t border-slate-800/60">
            <div>PID: <span class="text-slate-200">${s.pid || 'N/A'}</span></div>
            <div>Pane: <span class="text-slate-200">${s.tmux_pane || 'N/A'}</span></div>
            <div class="truncate">Dir: <span class="text-slate-200">${escapeHtml(s.working_directory || '~')}</span></div>
          </div>
        </div>

        <div class="pt-3 border-t border-slate-800 flex items-center justify-between">
          <button onclick="openSessionTimeline('${s.session_id}')" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium">
            Inspect
          </button>

          <button onclick="triggerFocusSession('${s.session_id}', ${s.pid || 'null'}, '${s.tmux_pane || ''}')" class="px-3 py-1.5 rounded bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold flex items-center space-x-1 shadow-sm">
            <i data-lucide="terminal" class="w-3 h-3"></i>
            <span>Focus Terminal</span>
          </button>
        </div>
      </div>
    `;
  }).join('');

  lucide.createIcons();
}

async function triggerFocusSession(sessionId, pid, tmuxPane) {
  try {
    const res = await fetch(`/api/live-sessions/${sessionId}/focus-terminal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pid, tmux_pane: tmuxPane })
    });
    const data = await res.json();
    alert(data.success ? `Focused via ${data.method}` : data.message);
  } catch (err) {
    alert(err.message);
  }
}

function setupLiveStream() {
  if (liveEventSource) liveEventSource.close();
  liveEventSource = new EventSource('/api/live-sessions/stream');

  liveEventSource.addEventListener('live_sessions_update', (e) => {
    try {
      const data = JSON.parse(e.data);
      if (currentTab === 'live') {
        renderLiveCards(data);
      }
      const activeCount = data.filter(s => s.status === 'LIVE' || s.status === 'RUNNING_TOOL').length;
      document.getElementById('liveCountText').innerText = `${activeCount} Live Sessions`;
    } catch (err) {}
  });

  liveEventSource.addEventListener('fleet_nodes_update', (e) => {
    try {
      const data = JSON.parse(e.data);
      if (currentTab === 'nodes') {
        renderNodesGrid(data);
      }
      updateFleetStatusIndicators(data);
    } catch (err) {}
  });

  liveEventSource.onerror = () => {
    document.getElementById('liveCountText').innerText = 'Reconnecting...';
  };
}

function updateFleetStatusIndicators(nodes) {
  if (!nodes || !Array.isArray(nodes)) return;
  const beehive = nodes.find(n => n.node_id === 'beehive');
  const chunkito = nodes.find(n => n.node_id === 'chunkito');

  if (beehive) {
    const el = document.getElementById('sidebarBeehiveStatus');
    if (el) {
      const isOnline = beehive.status === 'online' || beehive.status === 'warning';
      el.className = isOnline ? 'text-emerald-400 text-[11px] flex items-center space-x-1' : 'text-slate-500 text-[11px] flex items-center space-x-1';
      el.innerHTML = `<span class="w-1.5 h-1.5 rounded-full ${isOnline ? 'bg-emerald-400' : 'bg-slate-500'} inline-block"></span><span>${isOnline ? 'Online (Local)' : 'Offline'}</span>`;
    }
  }

  if (chunkito) {
    const el = document.getElementById('sidebarChunkitoStatus');
    if (el) {
      const isOnline = chunkito.status === 'online' || chunkito.status === 'warning';
      const modelsCount = chunkito.inference_engine?.loaded_models_count || chunkito.ollama?.loaded_models_count || 0;
      el.className = isOnline ? 'text-indigo-400 text-[11px] flex items-center space-x-1' : 'text-slate-500 text-[11px] flex items-center space-x-1';
      el.innerHTML = `<span class="w-1.5 h-1.5 rounded-full ${isOnline ? 'bg-indigo-400 animate-pulse' : 'bg-slate-500'} inline-block"></span><span>${isOnline ? (modelsCount > 0 ? `118GB APU (${modelsCount} active)` : '118GB APU (idle)') : 'Offline'}</span>`;
    }
  }
}

// ----------------------------------------------------
// FLEET & NODES TELEMETRY VIEW (BTOP & AMDGPU_TOP DASHBOARD)
// ----------------------------------------------------
async function loadNodes(force = false) {
  try {
    const res = await fetch(`/api/nodes?force_refresh=${force}`);
    const data = await res.json();

    // Update KPI Header
    if (data.summary) {
      document.getElementById('kpiFleetNodes').innerText = `${data.summary.total_nodes} Nodes`;
      document.getElementById('kpiFleetOnlineSub').innerHTML = `
        <span class="w-1.5 h-1.5 rounded-full ${data.summary.online_nodes > 0 ? 'bg-emerald-400' : 'bg-rose-400'} inline-block"></span>
        <span>${data.summary.online_nodes} online (${data.summary.offline_nodes} offline)</span>
      `;
      document.getElementById('kpiTotalVram').innerText = `${data.summary.total_vram_gb.toFixed(1)} GB`;
      document.getElementById('kpiVramUtil').innerText = `${data.summary.used_vram_gb.toFixed(1)} GB`;
      document.getElementById('kpiVramUtilPct').innerText = `${data.summary.vram_utilization_pct}% allocated to resident models`;
      document.getElementById('kpiActiveModels').innerText = `${data.summary.loaded_models_total} Loaded`;
      const activeSlotsSub = document.getElementById('kpiActiveSlotsSub');
      if (activeSlotsSub) {
        activeSlotsSub.innerText = `${data.summary.active_slots_total || 0} active inference slot(s)`;
      }
    }

    const updatedEl = document.getElementById('nodesLastUpdated');
    if (updatedEl) {
      updatedEl.innerText = `Updated: ${new Date().toLocaleTimeString()}`;
    }

    updateFleetStatusIndicators(data.nodes);
    renderNodesGrid(data.nodes);
  } catch (err) {
    console.error('Error loading nodes telemetry:', err);
  }
}

function renderNodesGrid(nodes) {
  const container = document.getElementById('nodesGrid');
  if (!container) return;

  if (!nodes || nodes.length === 0) {
    container.innerHTML = `<div class="text-center py-12 text-slate-500 col-span-2">No fleet nodes configured.</div>`;
    return;
  }

  container.innerHTML = nodes.map(n => {
    const isOnline = n.status === 'online' || n.status === 'warning';
    const isWarning = n.status === 'warning' || (n.inference_engine && n.inference_engine.overloaded);
    
    let statusBadge = `
      <span class="px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-950/60 border border-emerald-500/30 text-emerald-400 flex items-center space-x-1.5">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span>Online</span>
      </span>
    `;
    if (isWarning) {
      statusBadge = `
        <span class="px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-950/60 border border-amber-500/30 text-amber-400 flex items-center space-x-1.5">
          <span class="w-2 h-2 rounded-full bg-amber-400 animate-ping"></span>
          <span>Warning (Limit)</span>
        </span>
      `;
    } else if (!isOnline) {
      statusBadge = `
        <span class="px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-800 border border-slate-700 text-slate-400 flex items-center space-x-1.5">
          <span class="w-2 h-2 rounded-full bg-slate-500"></span>
          <span>Offline / Unreachable</span>
        </span>
      `;
    }

    const latencyText = n.latency_ms !== null ? `${n.latency_ms} ms` : 'N/A';
    const tags = (n.tags || []).map(t => `<span class="px-2 py-0.5 rounded bg-slate-800/80 text-slate-300 text-[10px] font-mono border border-slate-700/60">${t}</span>`).join(' ');

    const apu = n.apu_vram || {};
    const totalVram = apu.total_gb || n.hardware?.vram_gb || 0;
    const usedVram = apu.used_gb || 0;
    const vramPct = apu.used_percent || (totalVram > 0 ? (usedVram / totalVram * 100).toFixed(1) : 0);

    const mem = n.memory || {};
    const cpu = n.cpu || {};
    const gpu = n.amdgpu || {};
    const disk = n.disk || {};

    const inf = n.inference_engine || n.ollama || {};
    const backendName = inf.backend_type || 'llama.cpp (llama-server)';
    const loadedModels = inf.loaded_models || [];
    const availableModels = inf.available_models || [];
    const slots = inf.slots || [];
    const slotSumm = inf.slot_summary || {};

    const alerts = (n.alerts || []).map(a => `
      <div class="p-2.5 rounded-lg bg-amber-950/40 border border-amber-500/30 text-amber-300 text-xs flex items-center space-x-2">
        <i data-lucide="alert-triangle" class="w-4 h-4 text-amber-400 shrink-0"></i>
        <span>${escapeHtml(a)}</span>
      </div>
    `).join('');

    return `
      <div class="bg-dark-900 border border-slate-800/80 rounded-xl p-5 shadow-sm space-y-5 flex flex-col justify-between">
        <div class="space-y-4">
          <!-- Node Header -->
          <div class="flex items-start justify-between">
            <div class="space-y-1">
              <div class="flex items-center space-x-2.5">
                <span class="text-xl">${n.is_local ? '🏠' : '⚡'}</span>
                <h3 class="text-base font-bold text-white tracking-tight">${escapeHtml(n.name)}</h3>
                <span class="px-2 py-0.5 rounded text-[10px] font-mono uppercase font-semibold ${n.is_local ? 'bg-slate-800 text-slate-300' : 'bg-indigo-950 border border-indigo-500/30 text-indigo-300'}">
                  ${n.role || (n.is_local ? 'Coordinator' : 'Worker')}
                </span>
              </div>
              <div class="text-xs text-slate-400 font-mono flex flex-wrap items-center gap-2 pt-0.5">
                <span>IP: <span class="text-slate-200">${n.tailscale_ip || n.host}</span></span>
                <span>•</span>
                <span>Latency: <span class="text-brand-300 font-semibold">${latencyText}</span></span>
                ${n.is_local ? '<span>•</span><span class="text-emerald-400 font-medium">Local Gateway</span>' : '<span>•</span><span class="text-indigo-400 font-medium">Tailscale Mesh</span>'}
              </div>
            </div>
            ${statusBadge}
          </div>

          <!-- Tags -->
          <div class="flex flex-wrap gap-1.5">
            ${tags}
          </div>

          <!-- Alerts if any -->
          ${alerts}

          <!-- 1. AMDGPU TOP & UNIFIED MEMORY GAUGE -->
          <div class="p-4 rounded-xl bg-dark-950 border border-slate-800 space-y-3">
            <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
              <div class="flex items-center space-x-2">
                <i data-lucide="microchip" class="w-4 h-4 text-brand-400"></i>
                <h4 class="text-xs font-bold text-slate-200 uppercase tracking-wider">amdgpu_top & APU Unified Pool</h4>
              </div>
              <div class="text-[11px] font-mono text-slate-400">
                ${gpu.power_w ? `<span class="text-amber-300">${gpu.power_w}W</span> • ` : ''}
                ${gpu.temperature_c ? `<span class="text-emerald-300">${gpu.temperature_c}°C</span>` : ''}
              </div>
            </div>

            <!-- APU Unified VRAM Progress Bar -->
            <div class="space-y-1.5">
              <div class="flex justify-between text-xs">
                <span class="text-slate-300 font-medium flex items-center space-x-1.5">
                  <span>${escapeHtml(n.hardware?.gpu || 'AMD APU Unified Memory')}</span>
                </span>
                <span class="font-mono text-xs">
                  <strong class="text-brand-300">${usedVram.toFixed(1)} GB</strong>
                  <span class="text-slate-500"> / ${totalVram.toFixed(1)} GB (${vramPct}%)</span>
                </span>
              </div>

              <div class="w-full bg-dark-900 rounded-full h-3 overflow-hidden border border-slate-800 relative">
                <div class="h-full rounded-full transition-all duration-500 ${isWarning ? 'bg-gradient-to-r from-amber-500 to-rose-500' : 'bg-gradient-to-r from-brand-500 via-indigo-500 to-emerald-400'}" style="width: ${Math.min(100, Math.max(1, vramPct))}%"></div>
              </div>

              <div class="flex justify-between text-[11px] text-slate-400 font-mono pt-0.5">
                <span>Free Headroom: <strong class="text-slate-200">${(totalVram - usedVram).toFixed(1)} GB</strong></span>
                <span>${n.hardware?.gtt_size_mb ? `amdgpu.gttsize=${n.hardware.gtt_size_mb}MB` : `System RAM: ${mem.total_gb || 32}GB`}</span>
              </div>
            </div>

            <!-- GTT & GPU Load Spark metrics -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1 font-mono text-[11px]">
              <div class="p-2 rounded bg-dark-900 border border-slate-800/80">
                <div class="text-slate-500 text-[10px]">GPU Core Load</div>
                <div class="text-white font-bold">${gpu.gpu_busy_percent !== undefined ? gpu.gpu_busy_percent : 0}%</div>
              </div>
              <div class="p-2 rounded bg-dark-900 border border-slate-800/80">
                <div class="text-slate-500 text-[10px]">GTT Allocation</div>
                <div class="text-indigo-300 font-bold">${gpu.gtt_total_gb || (totalVram > 32 ? '118.0 GB' : '7.4 GB')}</div>
              </div>
              <div class="p-2 rounded bg-dark-900 border border-slate-800/80">
                <div class="text-slate-500 text-[10px]">GPU Temp</div>
                <div class="text-emerald-400 font-bold">${gpu.temperature_c || 26.0}°C</div>
              </div>
              <div class="p-2 rounded bg-dark-900 border border-slate-800/80">
                <div class="text-slate-500 text-[10px]">Power / TDP</div>
                <div class="text-amber-400 font-bold">${gpu.power_w ? `${gpu.power_w}W` : '10W / 45W'}</div>
              </div>
            </div>
          </div>

          <!-- 2. BTOP HARDWARE & SYSTEM TELEMETRY -->
          <div class="p-4 rounded-xl bg-dark-950 border border-slate-800 space-y-3">
            <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
              <div class="flex items-center space-x-2">
                <i data-lucide="cpu" class="w-4 h-4 text-indigo-400"></i>
                <h4 class="text-xs font-bold text-slate-200 uppercase tracking-wider">btop System Telemetry</h4>
              </div>
              <div class="text-[11px] font-mono text-slate-400">
                Load: <strong class="text-slate-200">${cpu.load_1m || '0.1'}</strong>, ${cpu.load_5m || '0.1'}, ${cpu.load_15m || '0.1'}
              </div>
            </div>

            <div class="grid grid-cols-2 gap-3 text-xs">
              <!-- CPU Details -->
              <div class="p-2.5 rounded-lg bg-dark-900 border border-slate-800 space-y-1">
                <div class="text-[10px] text-slate-400 font-mono uppercase font-semibold">Processor & Cores</div>
                <div class="text-white font-medium truncate" title="${cpu.model_name || n.hardware?.cpu}">${cpu.model_name || n.hardware?.cpu || 'AMD Ryzen'}</div>
                <div class="text-[11px] text-slate-400 font-mono">
                  <span>${cpu.cores || 16} Cores</span> • <span>Util: <strong class="text-indigo-300">${cpu.utilization_percent || 5}%</strong></span> • <span>Temp: <strong class="text-emerald-400">${cpu.temperature_c || 32}°C</strong></span>
                </div>
              </div>

              <!-- RAM Details -->
              <div class="p-2.5 rounded-lg bg-dark-900 border border-slate-800 space-y-1">
                <div class="text-[10px] text-slate-400 font-mono uppercase font-semibold">System Memory & Swap</div>
                <div class="text-white font-medium font-mono">${mem.used_gb || 8} GB / ${mem.total_gb || 32} GB <span class="text-slate-400 text-[11px]">(${mem.used_percent || 25}%)</span></div>
                <div class="text-[10px] text-slate-400 font-mono">
                  <span>Avail: ${mem.available_gb || mem.free_gb || 24}GB</span> • <span>Cached: ${mem.cached_gb || 0}GB</span>
                </div>
              </div>
            </div>
          </div>

          <!-- 3. INFERENCE ENGINE & RESIDENT VRAM MODELS -->
          <div class="p-4 rounded-xl bg-dark-950 border border-slate-800 space-y-3">
            <div class="flex items-center justify-between border-b border-slate-800/80 pb-2">
              <div class="flex items-center space-x-2">
                <i data-lucide="brain-circuit" class="w-4 h-4 text-emerald-400"></i>
                <h4 class="text-xs font-bold text-slate-200 uppercase tracking-wider">Inference Engine (${backendName})</h4>
              </div>
              <span class="px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${inf.overloaded ? 'bg-rose-950 text-rose-300 border border-rose-500/30' : 'bg-emerald-950 text-emerald-300 border border-emerald-500/30'}">
                ${inf.loaded_models_count || 0}/${inf.max_loaded_models || 1} Resident Model
              </span>
            </div>

            <!-- Resident Models in VRAM -->
            ${loadedModels.length > 0 ? `
              <div class="space-y-2">
                ${loadedModels.map(m => `
                  <div class="p-3 rounded-lg bg-dark-900 border border-brand-500/30 flex items-center justify-between text-xs">
                    <div>
                      <div class="font-bold text-brand-300 font-mono text-sm">${escapeHtml(m.name || m.model)}</div>
                      <div class="text-[11px] text-slate-400 font-mono mt-0.5 flex flex-wrap items-center gap-2">
                        <span>Params: <strong class="text-slate-200">${m.parameter_size || '177B MoE'}</strong></span>
                        <span>•</span>
                        <span>Quant: <strong class="text-slate-200">${m.quantization_level || 'IQ4_XS'}</strong></span>
                        <span>•</span>
                        <span>Context: <strong class="text-slate-200">${(m.context_length || 262144).toLocaleString()} tok</strong></span>
                        <span>•</span>
                        <span>Resident VRAM: <strong class="text-indigo-300">${m.size_vram_gb || m.size_gb || 87.2} GB</strong></span>
                      </div>
                    </div>
                    <span class="px-2 py-1 rounded bg-emerald-950 text-emerald-400 border border-emerald-500/30 text-[10px] font-mono font-bold uppercase">Resident</span>
                  </div>
                `).join('')}
              </div>
            ` : `
              <div class="py-3 px-3 rounded-lg bg-dark-900/60 border border-slate-800/60 text-center text-xs text-slate-500">
                <span>No models resident in VRAM • APU memory free for on-demand inference</span>
              </div>
            `}

            <!-- 4. PARALLEL INFERENCE SLOTS GRID (btop style) -->
            ${slots.length > 0 ? `
              <div class="pt-2 border-t border-slate-800/80 space-y-2">
                <div class="flex justify-between items-center text-[11px] text-slate-400 font-mono">
                  <span class="font-semibold uppercase tracking-wider">Parallel Execution Slots (${slots.length} Slots):</span>
                  <span>Active: <strong class="text-amber-400">${slotSumm.active_slots || 0}</strong> | Idle: <strong class="text-emerald-400">${slotSumm.idle_slots || slots.length}</strong></span>
                </div>

                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  ${slots.map(s => {
                    const isProcessing = s.is_processing;
                    const slotStateClass = isProcessing ? 'border-amber-500/50 bg-amber-950/20' : 'border-slate-800 bg-dark-900';
                    const badgeClass = isProcessing ? 'bg-amber-950 text-amber-300 border-amber-500/30 animate-pulse' : 'bg-slate-800 text-slate-400';
                    const promptProcessed = s.n_prompt_tokens_processed || 0;
                    const promptCache = s.n_prompt_tokens_cache || 0;

                    return `
                      <div class="p-2.5 rounded-lg border ${slotStateClass} text-xs font-mono space-y-1.5">
                        <div class="flex items-center justify-between">
                          <span class="font-bold text-slate-200 text-[11px]">Slot #${s.id}</span>
                          <span class="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${badgeClass}">
                            ${isProcessing ? 'PROCESSING' : 'IDLE'}
                          </span>
                        </div>
                        <div class="text-[10px] text-slate-400 space-y-0.5">
                          <div>Ctx: ${(s.n_ctx || 262144).toLocaleString()} | Task: <span class="text-slate-300">${s.id_task || 'None'}</span></div>
                          <div>Tokens Processed: <span class="text-slate-200">${promptProcessed.toLocaleString()}</span> • Cache: <span class="text-emerald-400">${promptCache.toLocaleString()}</span></div>
                          <div class="text-[9px] text-slate-500">Reasoning: ${s.params?.reasoning_format || 'deepseek'} • Temp: ${s.params?.temperature ? s.params.temperature.toFixed(2) : '0.10'}</div>
                        </div>
                      </div>
                    `;
                  }).join('')}
                </div>
              </div>
            ` : ''}

            <!-- Available Cached Models on Disk -->
            ${availableModels.length > 0 && !slots.length ? `
              <div class="pt-2 border-t border-slate-800/60 text-xs">
                <div class="text-[11px] text-slate-400 font-medium mb-1.5">Cached Models on Disk (${availableModels.length}):</div>
                <div class="flex flex-wrap gap-1.5">
                  ${availableModels.map(m => `
                    <span class="px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-slate-300 text-[10px] font-mono" title="${m.size_gb} GB">
                      ${escapeHtml(m.name)} <span class="text-slate-500">(${m.size_gb}GB)</span>
                    </span>
                  `).join('')}
                </div>
              </div>
            ` : ''}
          </div>
        </div>

        <!-- Node Card Footer / Controls -->
        <div class="pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs">
          <div class="text-slate-500 font-mono text-[11px]">
            ${n.is_local ? 'Gateway Host (Beehive)' : 'Remote AMD APU Inference Worker (Chunkito)'}
          </div>
          <button onclick="refreshSingleNode('${n.node_id}')" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium flex items-center space-x-1.5 transition">
            <i data-lucide="refresh-cw" class="w-3 h-3"></i>
            <span>Refresh Node</span>
          </button>
        </div>
      </div>
    `;
  }).join('');

  lucide.createIcons();
}

async function refreshAllNodes() {
  await loadNodes(true);
}

async function refreshSingleNode(nodeId) {
  try {
    const res = await fetch(`/api/nodes/${nodeId}/refresh`, { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      await loadNodes(false);
    }
  } catch (err) {
    alert(`Failed to refresh node ${nodeId}: ${err.message}`);
  }
}

// ----------------------------------------------------
// CRON VIEW
// ----------------------------------------------------
async function loadCron() {
  try {
    const res = await fetch('/api/cron');
    const data = await res.json();
    const list = document.getElementById('cronList');

    if (!data.jobs || data.jobs.length === 0) {
      list.innerHTML = `<div class="text-xs text-slate-500">No scheduled cron jobs configured in ~/.hermes/cron/</div>`;
      return;
    }

    list.innerHTML = data.jobs.map(j => `
      <div class="p-4 rounded-lg bg-dark-950 border border-slate-800 flex justify-between items-center text-xs">
        <div>
          <div class="font-bold text-slate-200">${escapeHtml(j.name || j.job_id || j.file || 'Cron Job')}</div>
          <div class="text-slate-400 font-mono mt-0.5">Schedule: ${escapeHtml(j.schedule || 'Recurring')}</div>
        </div>
        <div class="text-right font-mono text-slate-400">
          <span class="px-2 py-0.5 rounded bg-slate-800 text-slate-300">${j.enabled ? 'ACTIVE' : 'READY'}</span>
        </div>
      </div>
    `).join('');
  } catch (err) {
    console.error(err);
  }
}

// ----------------------------------------------------
// TICKETS VIEW
// ----------------------------------------------------
let ticketSearchTimeout = null;
function debounceLoadTickets() {
  clearTimeout(ticketSearchTimeout);
  ticketSearchTimeout = setTimeout(() => {
    loadTicketsTab();
  }, 300);
}

async function loadTickets() {
  await loadTicketsTab();
  await loadWebhookEvents();
}

async function loadTicketsTab() {
  const tbody = document.getElementById('ticketsTableBody');
  const provider = document.getElementById('ticketFilterProvider').value;
  const search = document.getElementById('ticketSearchInput').value;

  const params = new URLSearchParams();
  if (provider) params.append('provider', provider);
  if (search) params.append('search', search);

  try {
    const res = await fetch(`/api/tickets?${params.toString()}`);
    const data = await res.json();

    document.getElementById('kpiTotalTickets').innerText = data.total_tickets || 0;
    document.getElementById('kpiSyncedSessions').innerText = data.synced_sessions_count || 0;

    const tickets = data.tickets || [];
    if (tickets.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="text-center py-8 text-slate-500">No linked tickets found.</td></tr>`;
      return;
    }

    tbody.innerHTML = tickets.map(t => {
      const providerBadge = t.provider === 'github' ? '<span class="text-slate-200 bg-slate-800 px-2 py-0.5 rounded">GitHub</span>' :
                            t.provider === 'linear' ? '<span class="text-indigo-300 bg-indigo-950 px-2 py-0.5 rounded">Linear</span>' :
                            '<span class="text-sky-300 bg-sky-950 px-2 py-0.5 rounded">Jira</span>';

      const sessionLinks = (t.linked_sessions || []).map(sid => `
        <button onclick="openSessionTimeline('${sid}')" class="text-brand-400 hover:underline mr-1.5">${sid.substring(0, 10)}</button>
      `).join('');

      return `
        <tr class="hover:bg-slate-800/30">
          <td class="py-3 px-4">${providerBadge}</td>
          <td class="py-3 px-4 font-bold text-white">${escapeHtml(t.ticket_key)}</td>
          <td class="py-3 px-4 max-w-xs truncate">${t.url ? `<a href="${t.url}" target="_blank" class="hover:underline text-slate-200">${escapeHtml(t.title || t.ticket_key)}</a>` : escapeHtml(t.title || '-')}</td>
          <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-[10px] uppercase font-bold bg-slate-800 text-slate-300">${escapeHtml(t.status || 'open')}</span></td>
          <td class="py-3 px-4 text-slate-400">${escapeHtml(t.assignee || '-')}</td>
          <td class="py-3 px-4">${sessionLinks || '<span class="text-slate-500">-</span>'}</td>
          <td class="py-3 px-4 text-right">
            <button onclick="syncTicketStatus('${t.provider}', '${t.ticket_key}')" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs">Sync</button>
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-8 text-rose-400">Failed to load tickets: ${err.message}</td></tr>`;
  }
}

async function loadWebhookEvents() {
  const tbody = document.getElementById('webhookEventsBody');
  try {
    const res = await fetch('/api/webhooks/events?limit=20');
    const data = await res.json();

    document.getElementById('kpiTotalWebhooks').innerText = data.count || 0;

    const events = data.events || [];
    if (!events.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-slate-500">No webhook events logged yet.</td></tr>`;
      return;
    }

    tbody.innerHTML = events.map(e => `
      <tr class="hover:bg-slate-800/30">
        <td class="py-2.5 px-4 text-slate-400">${new Date(e.received_at * 1000).toLocaleTimeString()}</td>
        <td class="py-2.5 px-4 font-bold text-slate-200 uppercase">${escapeHtml(e.provider)}</td>
        <td class="py-2.5 px-4 text-slate-300">${escapeHtml(e.event_type)}</td>
        <td class="py-2.5 px-4 text-brand-300">${escapeHtml(e.ticket_key || '-')}</td>
        <td class="py-2.5 px-4">${e.matched_sessions_count || 0} session(s)</td>
        <td class="py-2.5 px-4"><span class="text-emerald-400">${escapeHtml(e.result || 'ok')}</span></td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-rose-400">Error loading webhooks: ${err.message}</td></tr>`;
  }
}

async function syncTicketStatus(provider, ticketKey) {
  const newStatus = prompt(`Enter new status for ${ticketKey}:`, 'Done');
  if (!newStatus) return;

  try {
    const res = await fetch(`/api/tickets/${provider}/${encodeURIComponent(ticketKey)}/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus })
    });
    const data = await res.json();
    if (data.success) {
      alert(`Updated ${ticketKey} to ${newStatus}`);
      loadTicketsTab();
    }
  } catch (err) {
    alert(`Sync failed: ${err.message}`);
  }
}
