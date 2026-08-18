// Hermes Karma Frontend Application Logic

let currentTab = 'sessions';
let currentPage = 0;
const pageSize = 25;
let activeSessionId = null;
let currentSessionData = null;
let modelChartInstance = null;
let toolChartInstance = null;
let allSkills = [];
let liveEventSource = null;

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
    
    document.getElementById('paginationInfo').innerText = `Showing ${Math.min(data.total, currentPage * pageSize + 1)} - ${Math.min(data.total, (currentPage + 1) * pageSize)} of ${data.total} sessions`;
    document.getElementById('prevPageBtn').disabled = currentPage === 0;
    document.getElementById('nextPageBtn').disabled = (currentPage + 1) * pageSize >= data.total;

    if (!data.sessions || data.sessions.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center py-12 text-slate-500">No matching sessions found.</td></tr>`;
      return;
    }

    tbody.innerHTML = data.sessions.map(s => {
      const started = s.started_at ? new Date(s.started_at * 1000).toLocaleString() : 'Unknown';
      const cost = s.estimated_cost_usd !== null ? `$${s.estimated_cost_usd.toFixed(4)}` : '$0.00';
      const source = s.source || 'cli';
      const title = s.title || s.session_id.substring(0, 16);
      const branch = s.git_branch ? `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px] font-mono"><i data-lucide="git-branch" class="w-2.5 h-2.5 inline"></i> ${s.git_branch}</span>` : '';
      
      const tickets = (s.metadata?.tickets || []).map(t => 
        `<span class="px-1.5 py-0.5 rounded bg-brand-950/60 border border-brand-500/30 text-brand-300 text-[10px] font-mono">${t.ticket_key}</span>`
      ).join(' ');

      const detectedTicket = s.detected_ticket ? 
        `<span class="px-1.5 py-0.5 rounded bg-amber-950/60 border border-amber-500/30 text-amber-300 text-[10px] font-mono" title="Auto-detected from git branch">${s.detected_ticket}*</span>` : '';

      return `
        <tr class="hover:bg-slate-800/30 transition group cursor-pointer" onclick="openSessionTimeline('${s.session_id}')">
          <td class="py-3 px-4 max-w-xs">
            <div class="font-medium text-slate-200 truncate group-hover:text-brand-400 transition" title="${title}">${title}</div>
            <div class="text-[11px] text-slate-400 font-mono truncate flex items-center space-x-1.5 mt-0.5">
              <span>${s.session_id}</span>
              ${branch}
              ${tickets}
              ${detectedTicket}
            </div>
          </td>
          <td class="py-3 px-3">
            <span class="px-2 py-0.5 rounded-full text-xs font-semibold ${getPlatformBadgeClass(source)}">
              ${source}
            </span>
          </td>
          <td class="py-3 px-3 font-mono text-xs text-slate-300 truncate max-w-[140px]" title="${s.model || 'Default'}">
            ${s.model || 'default'}
          </td>
          <td class="py-3 px-3 text-xs text-slate-300 font-mono">
            <div>${s.message_count || 0} msgs</div>
            <div class="text-slate-400 text-[11px]">${s.tool_call_count || 0} tools</div>
          </td>
          <td class="py-3 px-3 text-xs text-slate-300 font-mono">
            <div>${((s.input_tokens || 0) + (s.output_tokens || 0)).toLocaleString()} tok</div>
            <div class="text-emerald-400 text-[11px]" title="Cache read tokens">${(s.cache_read_tokens || 0).toLocaleString()} cache</div>
          </td>
          <td class="py-3 px-3 text-xs font-mono font-semibold ${cost === '$0.00' ? 'text-emerald-400' : 'text-amber-400'}">
            ${cost}
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

function getPlatformBadgeClass(p) {
  const map = {
    cli: 'bg-emerald-950/40 text-emerald-400 border border-emerald-500/20',
    webui: 'bg-indigo-950/40 text-indigo-400 border border-indigo-500/20',
    telegram: 'bg-sky-950/40 text-sky-400 border border-sky-500/20',
    discord: 'bg-purple-950/40 text-purple-400 border border-purple-500/20',
    slack: 'bg-amber-950/40 text-amber-400 border border-amber-500/20',
  };
  return map[p.toLowerCase()] || 'bg-slate-800 text-slate-300';
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
        <!-- Node Marker -->
        <div class="absolute -left-[30px] top-1.5 w-6 h-6 rounded-full ${iconBg} flex items-center justify-center text-xs shadow-md">
          <i data-lucide="${icon}" class="w-3.5 h-3.5"></i>
        </div>

        <!-- Event Card -->
        <div class="border ${cardBg} rounded-xl p-4 shadow-sm text-xs space-y-2">
          <div class="flex items-center justify-between">
            <span class="font-semibold text-slate-200 uppercase tracking-wider text-[11px]">${e.title}</span>
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
      ${d.task_parsed ? `<div class="text-slate-300 bg-dark-900 p-2 rounded border border-slate-800 font-mono text-[11px] whitespace-pre-wrap">${JSON.stringify(d.task_parsed, null, 2)}</div>` : ''}
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
async function loadAnalytics() {
  try {
    const res = await fetch('/api/analytics/overview');
    const data = await res.json();

    document.getElementById('statTotalSessions').innerText = data.total_sessions.toLocaleString();
    document.getElementById('statTotalMessages').innerText = `${(data.total_messages || 0).toLocaleString()} total turns`;
    
    const totalTokens = (data.total_input_tokens || 0) + (data.total_output_tokens || 0);
    document.getElementById('statTotalTokens').innerText = totalTokens.toLocaleString();
    document.getElementById('statTokensSub').innerText = `In: ${(data.total_input_tokens || 0).toLocaleString()} | Out: ${(data.total_output_tokens || 0).toLocaleString()}`;

    document.getElementById('statCacheHitRate').innerText = `${data.cache_hit_rate_pct || 0}%`;
    document.getElementById('statCacheRead').innerText = `${(data.total_cache_read_tokens || 0).toLocaleString()} cached tokens`;

    document.getElementById('statTotalCost').innerText = `$${(data.total_estimated_cost_usd || 0).toFixed(2)}`;
    document.getElementById('statLocalSessions').innerText = `${data.local_sessions_count || 0} local sessions ($0.00)`;

    // Render Charts
    renderAnalyticsCharts(data);

    // Render Models Table
    const tbody = document.getElementById('modelsTableBody');
    tbody.innerHTML = (data.model_distribution || []).map(m => `
      <tr class="hover:bg-slate-800/40">
        <td class="py-2.5 px-3 font-semibold text-slate-200">${m.model}</td>
        <td class="py-2.5 px-3">${(m.session_count || 0).toLocaleString()}</td>
        <td class="py-2.5 px-3 text-slate-400">${(m.input_tokens || 0).toLocaleString()}</td>
        <td class="py-2.5 px-3 text-slate-400">${(m.output_tokens || 0).toLocaleString()}</td>
        <td class="py-2.5 px-3 text-emerald-400">${(m.cache_read_tokens || 0).toLocaleString()}</td>
        <td class="py-2.5 px-3 ${m.cost_usd ? 'text-amber-400 font-bold' : 'text-emerald-400'}">$${(m.cost_usd || 0).toFixed(4)}</td>
      </tr>
    `).join('');

  } catch (err) {
    console.error(err);
  }
}

function renderAnalyticsCharts(data) {
  // Model chart
  const modelCtx = document.getElementById('modelChart');
  if (modelChartInstance) modelChartInstance.destroy();

  const modelLabels = (data.model_distribution || []).map(m => m.model);
  const modelCounts = (data.model_distribution || []).map(m => m.session_count);

  modelChartInstance = new Chart(modelCtx, {
    type: 'doughnut',
    data: {
      labels: modelLabels,
      datasets: [{
        data: modelCounts,
        backgroundColor: ['#8b5cf6', '#6366f1', '#10b981', '#f59e0b', '#ec4899', '#06b6d4']
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#94a3b8', font: { size: 10 } } }
      }
    }
  });

  // Tool chart
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
        x: { ticks: { color: '#94a3b8', font: { size: 10 } } },
        y: { ticks: { color: '#94a3b8', font: { size: 10 } } }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });
}

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
      <div class="font-bold text-slate-200 text-xs font-mono">${s.name}</div>
      <div class="text-[11px] text-slate-400 truncate mt-0.5">${s.description || 'No description'}</div>
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

          <h3 class="font-bold text-slate-200 text-sm truncate" title="${s.title}">${s.title || 'Session'}</h3>
          <p class="text-xs text-slate-400 font-mono truncate">${s.session_id}</p>

          ${s.current_tool ? `
            <div class="p-2 rounded bg-dark-950 border border-amber-500/30 text-amber-300 font-mono text-xs flex items-center space-x-2">
              <i data-lucide="terminal" class="w-3.5 h-3.5"></i>
              <span>Running: <strong>${s.current_tool}</strong></span>
            </div>
          ` : ''}

          <div class="text-[11px] text-slate-400 font-mono space-y-0.5 pt-2 border-t border-slate-800/60">
            <div>PID: <span class="text-slate-200">${s.pid || 'N/A'}</span></div>
            <div>Pane: <span class="text-slate-200">${s.tmux_pane || 'N/A'}</span></div>
            <div class="truncate">Dir: <span class="text-slate-200">${s.working_directory || '~'}</span></div>
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
      const modelsCount = chunkito.ollama?.loaded_models_count || 0;
      el.className = isOnline ? 'text-indigo-400 text-[11px] flex items-center space-x-1' : 'text-slate-500 text-[11px] flex items-center space-x-1';
      el.innerHTML = `<span class="w-1.5 h-1.5 rounded-full ${isOnline ? 'bg-indigo-400 animate-pulse' : 'bg-slate-500'} inline-block"></span><span>${isOnline ? (modelsCount > 0 ? `118GB APU (${modelsCount} active)` : '118GB APU (idle)') : 'Offline'}</span>`;
    }
  }
}

// ----------------------------------------------------
// FLEET & NODES TELEMETRY VIEW
// ----------------------------------------------------
async function loadNodes(force = false) {
  try {
    const res = await fetch(`/api/nodes?force_refresh=${force}`);
    const data = await res.json();

    // Update KPI Header
    if (data.summary) {
      document.getElementById('kpiFleetNodes').innerText = data.summary.total_nodes;
      document.getElementById('kpiFleetOnlineSub').innerHTML = `
        <span class="w-1.5 h-1.5 rounded-full ${data.summary.online_nodes > 0 ? 'bg-emerald-400' : 'bg-rose-400'} inline-block"></span>
        <span>${data.summary.online_nodes} online (${data.summary.offline_nodes} offline)</span>
      `;
      document.getElementById('kpiTotalVram').innerText = `${data.summary.total_vram_gb.toFixed(1)} GB`;
      document.getElementById('kpiVramUtil').innerText = `${data.summary.used_vram_gb.toFixed(1)} GB`;
      document.getElementById('kpiVramUtilPct').innerText = `${data.summary.vram_utilization_pct}% allocated to models`;
      document.getElementById('kpiActiveModels').innerText = data.summary.loaded_models_total;
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
    const isWarning = n.status === 'warning' || (n.ollama && n.ollama.overloaded);
    
    let statusBadge = `
      <span class="px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-950/60 border border-emerald-500/30 text-emerald-400 flex items-center space-x-1.5">
        <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
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

    const ollama = n.ollama || {};
    const loadedModels = ollama.loaded_models || [];
    const availableModels = ollama.available_models || [];

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
                ${n.is_local ? '<span>•</span><span class="text-emerald-400">Local Gateway</span>' : '<span>•</span><span class="text-indigo-400">Tailscale</span>'}
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

          <!-- Hardware & APU/GPU Metrics -->
          <div class="p-4 rounded-lg bg-dark-950/80 border border-slate-800/90 space-y-3">
            <div class="flex items-center justify-between text-xs">
              <span class="text-slate-400 font-medium flex items-center space-x-1.5">
                <i data-lucide="cpu" class="w-3.5 h-3.5 text-indigo-400"></i>
                <span class="text-slate-200 font-semibold">${escapeHtml(n.hardware?.cpu || 'CPU')}</span>
              </span>
              <span class="text-slate-400 font-mono">${cpu.cores || 16} Cores / Load: ${cpu.load_1m !== undefined ? cpu.load_1m : '0.1'}</span>
            </div>

            <!-- APU Unified VRAM Visual Gauge -->
            <div class="space-y-1.5 pt-1">
              <div class="flex justify-between text-xs">
                <span class="text-slate-300 font-medium flex items-center space-x-1.5">
                  <i data-lucide="microchip" class="w-3.5 h-3.5 text-brand-400"></i>
                  <span>${n.hardware?.gpu ? escapeHtml(n.hardware.gpu) : 'Unified Memory / VRAM'}</span>
                </span>
                <span class="font-mono text-xs">
                  <strong class="text-brand-300">${usedVram.toFixed(1)} GB</strong>
                  <span class="text-slate-500"> / ${totalVram.toFixed(1)} GB (${vramPct}%)</span>
                </span>
              </div>

              <!-- Progress bar -->
              <div class="w-full bg-dark-900 rounded-full h-2.5 overflow-hidden border border-slate-800">
                <div class="h-full rounded-full transition-all duration-500 ${isWarning ? 'bg-gradient-to-r from-amber-500 to-rose-500' : 'bg-gradient-to-r from-brand-500 to-indigo-500'}" style="width: ${Math.min(100, Math.max(1, vramPct))}%"></div>
              </div>

              <div class="flex justify-between text-[11px] text-slate-500 font-mono pt-0.5">
                <span>Free: ${(totalVram - usedVram).toFixed(1)} GB</span>
                <span>${n.hardware?.gtt_size_mb ? `amdgpu.gttsize=${n.hardware.gtt_size_mb}MB` : `System RAM: ${mem.total_gb || 32}GB`}</span>
              </div>
            </div>
          </div>

          <!-- Ollama Inference Engine & Loaded Models -->
          <div class="p-4 rounded-lg bg-dark-950/80 border border-slate-800/90 space-y-3">
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2">
                <i data-lucide="brain-circuit" class="w-4 h-4 text-emerald-400"></i>
                <h4 class="text-xs font-bold text-slate-200">Local LLM Engine (Ollama)</h4>
                ${ollama.version ? `<span class="px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 text-[10px] font-mono">v${ollama.version}</span>` : ''}
              </div>
              
              <!-- Memory safety limit badge -->
              <div class="text-[11px] font-mono">
                <span class="px-2 py-0.5 rounded ${ollama.overloaded ? 'bg-rose-950/80 text-rose-300 border border-rose-500/40' : 'bg-emerald-950/60 text-emerald-300 border border-emerald-500/30'}">
                  ${ollama.loaded_models_count || 0}/${ollama.max_loaded_models || 1} Model Slots
                </span>
              </div>
            </div>

            <!-- Active Resident Models in VRAM -->
            ${loadedModels.length > 0 ? `
              <div class="space-y-2 pt-1">
                <div class="text-[11px] text-slate-400 font-semibold uppercase tracking-wider">Resident in VRAM:</div>
                <div class="space-y-1.5">
                  ${loadedModels.map(m => `
                    <div class="p-2.5 rounded-lg bg-dark-900 border border-brand-500/30 flex items-center justify-between text-xs">
                      <div>
                        <div class="font-bold text-brand-300 font-mono">${escapeHtml(m.name || m.model)}</div>
                        <div class="text-[10px] text-slate-400 font-mono mt-0.5">
                          <span>${m.parameter_size || 'N/A'}</span> • <span>${m.quantization_level || 'Default Quant'}</span> • <span>VRAM: <strong class="text-slate-200">${m.size_vram_gb || m.size_gb || 0} GB</strong></span>
                        </div>
                      </div>
                      <span class="px-2 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-500/30 text-[10px] font-mono">ACTIVE</span>
                    </div>
                  `).join('')}
                </div>
              </div>
            ` : `
              <div class="py-3 px-3 rounded-lg bg-dark-900/60 border border-slate-800/60 text-center text-xs text-slate-500">
                <span>No models resident in VRAM • APU memory free for on-demand inference</span>
              </div>
            `}

            <!-- Available Cached Model Catalog -->
            ${availableModels.length > 0 ? `
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
            ${n.is_local ? 'Gateway Host' : 'Remote APU Worker (chunkito)'}
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
          <div class="font-bold text-slate-200">${j.name || j.job_id || j.file || 'Cron Job'}</div>
          <div class="text-slate-400 font-mono mt-0.5">Schedule: ${j.schedule || 'Recurring'}</div>
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
// TICKETS VIEW & WEBHOOK AUDIT LOG
// ----------------------------------------------------
async function loadTickets() {
  await loadWebhookEvents();
}

async function loadWebhookEvents() {
  const tbody = document.getElementById('webhookEventsBody');
  if (!tbody) return;

  try {
    const res = await fetch('/api/webhooks/events?limit=20');
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-slate-500">No webhook receiver events logged yet.</td></tr>`;
      return;
    }
    const data = await res.json();
    const events = data.events || [];

    if (events.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-slate-500">No webhook receiver events logged yet.</td></tr>`;
      return;
    }

    tbody.innerHTML = events.map(e => `
      <tr class="hover:bg-slate-800/30 transition">
        <td class="py-2.5 px-4 text-slate-400">${new Date(e.created_at * 1000).toLocaleTimeString()}</td>
        <td class="py-2.5 px-4 font-semibold text-brand-300 uppercase">${escapeHtml(e.provider)}</td>
        <td class="py-2.5 px-4 text-slate-200">${escapeHtml(e.event_type)}</td>
        <td class="py-2.5 px-4 text-emerald-400 font-bold">${escapeHtml(e.ticket_key || '-')}</td>
        <td class="py-2.5 px-4 text-slate-400">${e.matched_sessions ? e.matched_sessions.length : 0} sessions</td>
        <td class="py-2.5 px-4 text-slate-300">${escapeHtml(e.result || 'OK')}</td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-slate-500">Webhook receiver ready (listening on /api/webhooks/*).</td></tr>`;
  }
}

// Utilities
function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
