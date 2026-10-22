// Hermes Karma Frontend Application Logic

let currentTab = 'sessions';
let currentPage = 0;
const pageSize = 25;
let activeSessionId = null;
let currentSessionData = null;
let modelChartInstance = null;
let modelOutputChartInstance = null;
let velocityChartInstance = null;
let providerChartInstance = null;
let toolChartInstance = null;
let allSkills = [];
let allDiscoveredModels = [];
let liveEventSource = null;

// ----------------------------------------------------
// DURABLE MODEL COLOR PALETTE (TASK-HK-STABLE-PALETTE)
// Consistent color per model across all charts and sessions.
// Curated distinct hues for top models, Google Blue family for Gemini,
// Emerald/Teal for Qwen, and deterministic hashing for new future models.
// ----------------------------------------------------
const CURATED_MODEL_COLORS = {
  // Gemini family (Google Blues & Cyans)
  'gemini-3.8-flash': '#2563eb',       // Tailwind blue-600
  'gemini-3.7-flash': '#0284c7',       // Tailwind sky-600
  'gemini-3.5-flash': '#06b6d4',       // Tailwind cyan-500
  'gemini-3.6-flash': '#0ea5e9',       // Tailwind sky-500
  'gemini-3.1-pro-preview': '#1d4ed8', // Tailwind blue-700
  'gemini-3.1-pro': '#1e40af',         // Tailwind blue-800
  'gemini-3-flash-preview': '#38bdf8', // Tailwind sky-400
  'gemini-2.5-flash': '#0891b2',       // Tailwind cyan-600

  // Qwen family (Vibrant Emerald & Teals)
  'qwen3.8-flash-next': '#10b981',     // Tailwind emerald-500
  'qwen3.8-flash-next:262k': '#10b981',
  'qwen/qwen3.8-flash': '#10b981',
  'flash-next': '#10b981',
  'qwen3-coder-next': '#059669',       // Tailwind emerald-600
  'qwen3-coder-next:q8_0': '#059669',
  'qwen3-coder-next:262k': '#047857',  // Tailwind emerald-700
  'qwen3.8-27b': '#14b8a6',            // Tailwind teal-500
  'qwen3-coder-30b': '#0d9488',        // Tailwind teal-600
  'qwen3-coder-30b:262k': '#0d9488',
  'qwen3-coder-30b:128k': '#0f766e',   // Tailwind teal-700
  'qwen2.5:14b': '#16a34a',            // Tailwind green-600
  'qwen2.5': '#15803d',                // Tailwind green-700
  'qwen3.5:latest': '#22c55e',         // Tailwind green-500

  // DeepSeek family (Electric Indigo / Deep Violet)
  'deepseek-v4': '#6366f1',            // Tailwind indigo-500
  'deepseek-v4:262k': '#6366f1',
  'deepseek-v4:96k': '#6366f1',
  'deepseek-chat': '#4f46e5',          // Tailwind indigo-600
  'deepseek-reasoner': '#4338ca',      // Tailwind indigo-700
  'heffalump': '#8b5cf6',              // Tailwind violet-500 (Latent Reasoning Titan)

  // Claude / Anthropic family (Warm Amber / Terracotta)
  'claude-opus-5': '#d97706',          // Tailwind amber-600
  'claude-opus-4': '#b45309',          // Tailwind amber-700
  'claude-sonnet-latest': '#ea580c',   // Tailwind orange-600
  'claude-3-5-sonnet': '#f59e0b',      // Tailwind amber-500
  'claude-3-opus': '#c2410c',          // Tailwind orange-700

  // Jagular (Chunkito 177B exclusive boot)
  'jagular': '#f43f5e',                // Tailwind rose-500

  // GPT / OSS / OpenAI family (Lime)
  'gpt-oss:20b': '#84cc16',            // Tailwind lime-500
  'gpt-4o': '#65a30d',                 // Tailwind lime-600
  'gpt-4o-mini': '#a3e635',            // Tailwind lime-400

  // Gemma family (Violet / Purple)
  'gemma4:12b': '#a855f7',             // Tailwind purple-500
  'gemma4': '#9333ea',                 // Tailwind purple-600
  'gemma-2-9b': '#c084fc',             // Tailwind purple-400

  // Nvidia / Nemotron (Yellow / Gold)
  'nvidia/nemotron-3-ultra-550b-a55b:free': '#eab308' // Tailwind yellow-500
};

// Deterministic fallback palette for new future models
const DURABLE_FALLBACK_PALETTE = [
  '#38bdf8', '#fb7185', '#a78bfa', '#f472b6', '#34d399', '#fbbf24',
  '#818cf8', '#2dd4bf', '#fb923c', '#c084fc', '#4ade80', '#e879f9',
  '#60a5fa', '#f87171', '#a3e635', '#22d3ee', '#f43f5e', '#ec4899',
  '#10b981', '#6366f1', '#8b5cf6', '#d97706', '#06b6d4', '#14b8a6'
];

function djb2Hash(str) {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) + hash) + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}

function getModelColor(modelName) {
  if (!modelName) return '#94a3b8';
  let s = String(modelName).trim().toLowerCase();
  
  // Normalize vendor prefix
  ['google/', 'anthropic/', '~anthropic/', 'qwen/', 'meta-llama/'].forEach(p => {
    if (s.startsWith(p)) s = s.slice(p.length);
  });

  // Direct lookup
  if (CURATED_MODEL_COLORS[s]) return CURATED_MODEL_COLORS[s];

  // Specific version / pattern fallbacks
  if (s.includes('gemini-3.8')) return '#2563eb';
  if (s.includes('gemini-3.7')) return '#0284c7';
  if (s.includes('gemini-3.5')) return '#06b6d4';
  if (s.includes('gemini-3.6')) return '#0ea5e9';
  if (s.includes('gemini-3.1') || s.includes('gemini-3-pro')) return '#1d4ed8';
  if (s.includes('gemini-3-flash')) return '#38bdf8';
  if (s.includes('gemini')) return '#3b82f6';

  if (s.includes('flash-next') || s.includes('qwen3.8-flash')) return '#10b981';
  if (s.includes('coder-next')) return '#059669';
  if (s.includes('qwen3.8-27b') || s.includes('qwen3.8')) return '#14b8a6';
  if (s.includes('qwen3-coder')) return '#0d9488';
  if (s.includes('qwen3-30b') || s.includes('qwen3.6-35b') || s.includes('qwen3')) return '#0f766e';
  if (s.includes('qwen2.5')) return '#16a34a';
  if (s.includes('qwen')) return '#10b981';

  if (s.includes('deepseek')) return '#6366f1';
  if (s.includes('heffalump')) return '#8b5cf6';
  if (s.includes('claude')) return '#d97706';
  if (s.includes('jagular')) return '#f43f5e';
  if (s.includes('gpt')) return '#84cc16';
  if (s.includes('gemma')) return '#a855f7';
  if (s.includes('nemotron')) return '#eab308';

  // Deterministic stable hash for any newly added model
  return DURABLE_FALLBACK_PALETTE[djb2Hash(s) % DURABLE_FALLBACK_PALETTE.length];
}

// Fallback constant for backwards compatibility
const MODEL_CHART_COLORS = DURABLE_FALLBACK_PALETTE;

// Tailwind class sets for the throughput (tok/s) badges (TASK-HK-112)
const THROUGHPUT_BADGE_CLASSES = {
  ultra: 'bg-cyan-950/80 text-cyan-300 border-cyan-500/30',
  fast: 'bg-indigo-950/80 text-indigo-300 border-indigo-500/30',
  local_apu: 'bg-emerald-950/80 text-emerald-300 border-emerald-500/30',
  standard: 'bg-slate-800 text-slate-300 border-slate-700'
};

const THROUGHPUT_CHART_COLORS = {
  ultra: '#22d3ee',
  fast: '#6366f1',
  local_apu: '#10b981',
  standard: '#94a3b8'
};

function throughputBadge(m) {
  const tok = Number(m.throughput_tok_per_sec) || 0;
  const tier = THROUGHPUT_BADGE_CLASSES[m.throughput_tier] ? m.throughput_tier : 'standard';
  const isLocal = tier === 'local_apu';
  const label = isLocal
    ? `⚡ ${tok.toFixed(0)} tok/s (APU)`
    : `⚡ ${tok % 1 === 0 ? tok.toFixed(0) : tok.toFixed(1)} tok/s`;
  return `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono whitespace-nowrap ${THROUGHPUT_BADGE_CLASSES[tier]}" title="Decode throughput tier: ${tier}">${label}</span>`;
}

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

// Mobile drawer
function toggleMobileMenu() {
  const drawer = document.getElementById('mobile-drawer');
  const overlay = document.getElementById('mobile-drawer-overlay');
  if (!drawer) return;
  const open = drawer.classList.contains('flex');
  if (open) {
    closeMobileMenu();
  } else {
    drawer.classList.remove('hidden');
    drawer.classList.add('flex');
    if (overlay) overlay.classList.remove('hidden');
  }
}

function closeMobileMenu() {
  const drawer = document.getElementById('mobile-drawer');
  const overlay = document.getElementById('mobile-drawer-overlay');
  if (drawer) {
    drawer.classList.add('hidden');
    drawer.classList.remove('flex');
  }
  if (overlay) overlay.classList.add('hidden');
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
  document.querySelectorAll('.tab-btn, .mobile-tab-btn').forEach(btn => {
    btn.classList.remove('bg-brand-600/20', 'text-brand-400', 'border', 'border-brand-500/30', 'text-white');
    btn.classList.add('text-slate-400');
  });

  [document.getElementById(`tab-${tabId}`), document.getElementById(`tab-${tabId}-m`)].forEach(activeBtn => {
    if (!activeBtn) return;
    activeBtn.classList.remove('text-slate-400');
    activeBtn.classList.add('bg-brand-600/20', 'text-brand-400', 'border', 'border-brand-500/30', 'text-white');
  });

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
let currentSessionsTimeRange = '30d';

function setSessionsTimeRange(range) {
  currentSessionsTimeRange = range;
  document.querySelectorAll('.sessions-time-btn').forEach(btn => {
    if (btn.getAttribute('data-range') === range) {
      btn.className = 'sessions-time-btn px-2.5 py-1 rounded font-medium transition bg-brand-500/20 text-brand-300 border border-brand-500/30';
    } else {
      btn.className = 'sessions-time-btn px-2.5 py-1 rounded font-medium transition text-slate-400 hover:text-white';
    }
  });
  currentPage = 0;
  loadSessions();
}
window.setSessionsTimeRange = setSessionsTimeRange;

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
  const personaSelect = document.getElementById('filterPersonaSelect');
  const persona = personaSelect ? personaSelect.value : '';

  const params = new URLSearchParams({
    limit: pageSize,
    offset: currentPage * pageSize
  });
  if (search) params.append('search', search);
  if (platform) params.append('source', platform);
  if (model) params.append('model', model);
  if (persona) params.append('persona', persona);
  
  if (currentSessionsTimeRange && currentSessionsTimeRange !== 'all') {
    const now = Math.floor(Date.now() / 1000);
    let cutoff = now;
    if (currentSessionsTimeRange === '24h' || currentSessionsTimeRange === 'today') {
      cutoff -= 86400;
    } else if (currentSessionsTimeRange === '7d') {
      cutoff -= 7 * 86400;
    } else if (currentSessionsTimeRange === '30d') {
      cutoff -= 30 * 86400;
    }
    params.append('date_from', cutoff);
  }

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

      // Persona badge
      let personaBadge = '';
      if (s.persona_id) {
        personaBadge = `<span class="px-1.5 py-0.5 rounded bg-amber-950/80 border border-amber-500/40 text-amber-300 text-[10px] font-mono flex items-center space-x-1" title="Swarm Persona: ${s.persona_name} (${s.is_subagent ? 'Subagent' : 'Profile'})"><span>${s.persona_emoji || '🤖'}</span><span>${s.persona_name}${s.is_subagent ? ' Sub' : ''}</span></span>`;
      } else if (s.is_subagent) {
        personaBadge = `<span class="px-1.5 py-0.5 rounded bg-indigo-950/80 border border-indigo-500/30 text-indigo-300 text-[10px] font-mono">🤖 Subagent</span>`;
      }

      // Render comprehensive models used badges
      const modelsList = s.models_used || [];
      let modelPills = '';
      if (modelsList.length > 0) {
        modelPills = modelsList.map(m => {
          const mName = m.model || 'unknown';
          const mColor = getModelColor(mName);
          return `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono truncate max-w-[150px] inline-flex items-center gap-1 bg-dark-900 border-slate-700/60 text-slate-200" title="${escapeHtml(mName)} (${(m.input_tokens || 0).toLocaleString()} tok)"><span class="w-1.5 h-1.5 rounded-full flex-shrink-0" style="background-color: ${mColor};"></span><span>${escapeHtml(mName)}</span></span>`;
        }).join(' ');
      } else {
        const topModel = s.model || 'default';
        const mColor = getModelColor(topModel);
        modelPills = `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono truncate max-w-[150px] inline-flex items-center gap-1 bg-dark-900 border-slate-700/60 text-slate-200"><span class="w-1.5 h-1.5 rounded-full flex-shrink-0" style="background-color: ${mColor};"></span><span>${escapeHtml(topModel)}</span></span>`;
      }

      const totalTok = (s.input_tokens || 0) + (s.output_tokens || 0);
      const cacheTok = s.cache_read_tokens || 0;
      const reasTok = s.reasoning_tokens || 0;

      // SPEC-HK-007 §5.5: ingress gateway, spatial guidance (MAP/GRAPH) &
      // thrash-warning pills. Fields ride the session list payload; missing
      // audit data simply renders no badge.
      const ingressBadge = ingressBadgeHtml(s.ingress_channel || source);
      const mapBadge = s.used_map_md ? `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono font-semibold bg-cyan-950/60 text-cyan-300 border-cyan-500/30" title="Spatially guided: docs/MAP.md injected or read in first turns">MAP</span>` : '';
      const graphBadge = s.used_graphify ? `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono font-semibold bg-violet-950/60 text-violet-300 border-violet-500/30" title="Spatially guided: graphify topology query invoked">GRAPH</span>` : '';
      const r2eRatio = Number(s.read_to_edit_ratio) || 0;
      const thrashBadge = r2eRatio > 5.0 ? `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono font-semibold bg-amber-950/80 text-amber-300 border-amber-500/40" title="Read-to-Edit ratio ${r2eRatio.toFixed(1)} — repository thrashing (&gt;5.0)">⚠ THRASH</span>` : '';

      return `
        <tr class="hover:bg-slate-800/30 transition group cursor-pointer" onclick="openSessionTimeline('${s.session_id}')">
          <td class="py-3 px-4 max-w-xs">
            <div class="font-medium text-slate-200 truncate group-hover:text-brand-400 transition" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
            <div class="text-[11px] text-slate-400 font-mono truncate flex flex-wrap items-center gap-1.5 mt-0.5">
              <span>${s.session_id}</span>
              ${ingressBadge}
              ${mapBadge}
              ${graphBadge}
              ${thrashBadge}
              ${personaBadge}
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
let currentAnalyticsRange = '30d';
let currentTemporalMetric = 'input_tokens'; // SPEC-HK-006

async function setTemporalMetric(metric) {
  currentTemporalMetric = metric;
  document.querySelectorAll('.temporal-metric-btn').forEach(btn => {
    if (btn.getAttribute('data-metric') === metric) {
      btn.className = 'temporal-metric-btn px-3 py-1 rounded font-medium transition bg-brand-500/20 text-brand-300 border border-brand-500/30';
    } else {
      btn.className = 'temporal-metric-btn px-3 py-1 rounded font-medium transition text-slate-400 hover:text-white';
    }
  });
  if (window.lastAnalyticsData) {
    renderTemporalUsageChart(window.lastAnalyticsData);
  }
}
window.setTemporalMetric = setTemporalMetric;

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
    window.lastAnalyticsData = data;

    // TASK-HK-110: Total Turns is the hero stat; sessions/API calls are subtitles
    const turnsHero = document.getElementById('statTotalMessages');
    if (turnsHero) turnsHero.innerText = `${(data.total_messages || 0).toLocaleString()} Turns`;
    const turnsSub = document.getElementById('statTurnsSub');
    if (turnsSub) turnsSub.innerText = `${(data.total_sessions || 0).toLocaleString()} sessions • ${(data.total_api_calls || 0).toLocaleString()} API calls`;
    
    const totalTokens = (data.total_input_tokens || 0) + (data.total_output_tokens || 0);
    document.getElementById('statTotalTokens').innerText = totalTokens.toLocaleString();
    document.getElementById('statTokensSub').innerText = `In: ${(data.total_input_tokens || 0).toLocaleString()} | Out: ${(data.total_output_tokens || 0).toLocaleString()} | Reason: ${(data.total_reasoning_tokens || 0).toLocaleString()}`;

    document.getElementById('statCacheHitRate').innerText = `${data.cache_hit_rate_pct || 0}%`;
    document.getElementById('statCacheRead').innerText = `${(data.total_cache_read_tokens || 0).toLocaleString()} cached read tokens`;

    document.getElementById('statTotalCost').innerText = `$${(data.total_estimated_cost_usd || 0).toFixed(2)}`;
    const capInfo = data.spend_cap_usd ? ` • Month: $${(data.current_month_cost_usd || 0).toFixed(2)} / $${data.spend_cap_usd.toFixed(0)} cap (${data.spend_cap_pct || 0}%)` : '';
    document.getElementById('statLocalSessions').innerText = `${data.local_zero_cost_ratio_pct || 0}% local free tokens (${(data.local_sessions_count || 0).toLocaleString()} APU sessions)${capInfo}`;

    // TASK-HK-111: Generation Velocity & Inference Throughput HUD
    renderVelocityHud(data);

    // Render Charts
    renderAnalyticsCharts(data);
    renderTemporalUsageChart(data);

    // Render Models Table
    const tbody = document.getElementById('modelsTableBody');
    tbody.innerHTML = (data.model_distribution || []).map(m => {
      const isLoc = m.is_local;
      const tagBadge = isLoc ? 
        '<span class="px-1.5 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-500/30 text-[10px] font-mono">⚡ APU / Local</span>' : 
        `<span class="px-1.5 py-0.5 rounded bg-brand-950/80 text-brand-300 border border-brand-500/30 text-[10px] font-mono">☁️ ${m.provider_category || 'Cloud'}</span>`;

      const recBadge = m.is_reconciled ? '<span class="ml-1.5 px-1 py-0.2 rounded bg-cyan-950/80 text-cyan-300 border border-cyan-500/30 text-[9px] font-mono" title="Reconciled against published pricing card">⚡ Reconciled</span>' : '';
      const costStr = m.cost_usd && m.cost_usd > 0 ? `$${m.cost_usd.toFixed(4)}${recBadge}` : '<span class="text-emerald-400">$0.00 (APU)</span>';
      const mColor = getModelColor(m.model);

      return `
        <tr class="hover:bg-slate-800/40">
          <td class="py-2.5 px-3 font-semibold text-slate-200">
            <span class="inline-block w-2.5 h-2.5 rounded-full mr-2 align-middle shadow-sm flex-shrink-0" style="background-color: ${mColor};"></span>
            ${escapeHtml(m.model)}
          </td>
          <td class="py-2.5 px-3">${tagBadge}</td>
          <td class="py-2.5 px-3 font-semibold text-indigo-300">${(m.turns_count || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3">${(m.session_count || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-slate-400">${(m.api_call_count || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-slate-400">${(m.input_tokens || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-slate-400">${(m.output_tokens || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-emerald-400">${(m.cache_read_tokens || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3 text-purple-400">${(m.reasoning_tokens || 0).toLocaleString()}</td>
          <td class="py-2.5 px-3">${throughputBadge(m)}</td>
          <td class="py-2.5 px-3 font-bold">${costStr}</td>
        </tr>
      `;
    }).join('');

  } catch (err) {
    console.error(err);
  }
  // SPEC-HK-007 §5: Context Tax & Spatial ROI (independent fetch, synced to range)
  loadContextTax();
}

// ----------------------------------------------------
// SPEC-HK-007: CONTEXT TAX & SPATIAL NAVIGATION ROI
// ----------------------------------------------------
const CONTEXT_TIER_META = [
  { key: 'cold_start_tokens', tier: 'Tier 1', label: 'Base Harness', color: '#6366f1', desc: 'System prompt, MEMORY.md/USER.md, skill router descriptions & tool schemas' },
  { key: 'spatial_index_tokens', tier: 'Tier 2', label: 'Spatial Index', color: '#22d3ee', desc: 'docs/MAP.md, AI context overview & graphify topology queries' },
  { key: 'exploration_waste_tokens', tier: 'Tier 3', label: 'Exploration Thrash', color: '#f43f5e', desc: 'Read/search tokens for files never modified in the session' },
  { key: 'productive_tokens', tier: 'Tier 4', label: 'Productive Context', color: '#10b981', desc: 'User prompts, edited-file reads, targeted test output & retained state' },
];

const INGRESS_BADGE_META = {
  cli: { label: 'CLI', cls: 'bg-emerald-950/60 text-emerald-300 border-emerald-500/30' },
  webui: { label: 'WEBUI', cls: 'bg-indigo-950/60 text-indigo-300 border-indigo-500/30' },
  telegram: { label: 'TG', cls: 'bg-sky-950/60 text-sky-300 border-sky-500/30' },
  cron: { label: 'CRON', cls: 'bg-rose-950/60 text-rose-300 border-rose-500/30' },
  pantheon_subagent: { label: 'SWARM', cls: 'bg-purple-950/60 text-purple-300 border-purple-500/30' },
};

function analyticsRangeToDays(range) {
  switch (range) {
    case 'today': return 1;
    case '7d': return 7;
    case 'month': return 30;
    case 'all': return 365;
    case '30d':
    default: return 30;
  }
}

function fmtTok(n) {
  const v = Number(n) || 0;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(v);
}

function ingressBadgeHtml(ch, extraCls = '') {
  const meta = INGRESS_BADGE_META[ch] ||
    { label: String(ch || '?').toUpperCase().slice(0, 6), cls: 'bg-slate-800 text-slate-300 border-slate-700/60' };
  return `<span class="px-1.5 py-0.5 rounded border text-[10px] font-mono font-semibold whitespace-nowrap ${meta.cls} ${extraCls}" title="Ingress gateway: ${escapeHtml(ch)}">${meta.label}</span>`;
}

async function loadContextTax() {
  const bar = document.getElementById('contextTaxCompositionBar');
  if (!bar) return;
  const days = analyticsRangeToDays(currentAnalyticsRange);
  try {
    const res = await fetch(`/api/analytics/context-tax?days=${days}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderContextTax(data);
  } catch (err) {
    console.error('context-tax load failed:', err);
    bar.innerHTML = `<div class="flex-1 flex items-center justify-center text-[10px] text-rose-400">Failed to load context tax telemetry (${escapeHtml(err.message)})</div>`;
  }
}
window.loadContextTax = loadContextTax;

function renderContextTax(data) {
  const breakdown = data.token_breakdown || {};
  const roi = data.spatial_roi || {};
  const drift = roi.map_drift || {};
  const compaction = data.compaction_stats || {};

  // --- Map drift pill (header) ---
  const driftPill = document.getElementById('mapDriftPill');
  if (driftPill) {
    const unindexed = (drift.unindexed_files || []).length;
    if (drift.is_stale) {
      driftPill.innerHTML = `<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-amber-950/80 text-amber-300 border border-amber-500/40" title="docs/MAP.md is ${drift.commits_since_map_update || 0} commits behind HEAD${unindexed ? ` • ${unindexed} unindexed file candidates` : ''}">⚠ Map Drift Detected (${drift.commits_since_map_update || 0} commits)</span>`;
    } else {
      driftPill.innerHTML = `<span class="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-950/80 text-emerald-300 border border-emerald-500/40" title="docs/MAP.md updated ${escapeHtml(drift.map_last_updated_iso || 'recently')}">✓ Map Synced</span>`;
    }
  }

  // --- 4-Tier Context Composition Bar ---
  const bar = document.getElementById('contextTaxCompositionBar');
  const legend = document.getElementById('contextTaxLegend');
  const totalTokEl = document.getElementById('contextTaxTotalTokens');
  const tierTotal = CONTEXT_TIER_META.reduce((acc, t) => acc + (Number(breakdown[t.key]) || 0), 0);
  if (totalTokEl) totalTokEl.innerText = tierTotal > 0 ? `${tierTotal.toLocaleString()} input tokens • ${data.window_days || 0}d window` : '';
  if (bar) {
    if (tierTotal <= 0) {
      bar.innerHTML = `<div class="flex-1 flex items-center justify-center text-[10px] text-slate-600">No tier-classified input tokens in this window</div>`;
    } else {
      bar.innerHTML = CONTEXT_TIER_META.map(t => {
        const v = Number(breakdown[t.key]) || 0;
        if (v <= 0) return '';
        const pct = (v / tierTotal) * 100;
        return `<div class="h-full transition-all" style="flex-grow: ${pct}; flex-basis: 0%; min-width: 4px; background-color: ${t.color};" title="${escapeHtml(`${t.tier} ${t.label}`)}: ${v.toLocaleString()} tokens (${pct.toFixed(1)}%)"></div>`;
      }).join('');
    }
  }
  if (legend) {
    if (tierTotal <= 0) {
      legend.innerHTML = '';
    } else {
      legend.innerHTML = CONTEXT_TIER_META.map(t => {
        const v = Number(breakdown[t.key]) || 0;
        const pct = ((v / tierTotal) * 100).toFixed(1);
        return `<span class="inline-flex items-center gap-1.5 text-slate-400" title="${escapeHtml(t.desc)}"><span class="w-2 h-2 rounded-full flex-shrink-0" style="background-color: ${t.color};"></span><span class="text-slate-300">${escapeHtml(t.label)}</span> <span class="font-mono">${pct}%</span> <span class="font-mono text-slate-500">(${fmtTok(v)})</span></span>`;
      }).join('');
    }
  }

  // --- Spatial ROI Scorecard (Mapped vs Unmapped) ---
  const scorecard = document.getElementById('spatialRoiScorecard');
  if (scorecard) {
    const mappedN = Number(roi.mapped_sessions_count) || 0;
    const unmappedN = Number(roi.unmapped_sessions_count) || 0;
    const netSaved = Number(roi.net_exploration_tokens_saved) || 0;
    const usdSaved = Number(roi.counterfactual_spatial_savings_usd) || 0;
    const col = (name, cls, sessions, ttfe, ratio, tip) => `
      <div class="rounded-lg border ${cls} p-3.5" title="${escapeHtml(tip)}">
        <div class="flex items-center justify-between gap-2">
          <span class="text-[10px] font-semibold uppercase tracking-wider opacity-90">${escapeHtml(name)}</span>
          <span class="px-1.5 py-0.5 rounded bg-dark-950/80 border border-slate-700/60 text-[10px] font-mono text-slate-300">${sessions.toLocaleString()} sess</span>
        </div>
        <div class="mt-2.5 grid grid-cols-2 gap-2 text-center">
          <div>
            <div class="text-xl font-bold font-mono">${ttfe.toFixed(1)}</div>
            <div class="text-[10px] text-slate-400">Avg Turns to First Edit</div>
          </div>
          <div>
            <div class="text-xl font-bold font-mono">${ratio.toFixed(2)}</div>
            <div class="text-[10px] text-slate-400">Read-to-Edit Ratio</div>
          </div>
        </div>
      </div>`;
    scorecard.innerHTML = `
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        ${col('Mapped · MAP.md / graphify', 'border-emerald-500/30 bg-emerald-950/30 text-emerald-200', mappedN,
          Number(roi.avg_turns_to_first_edit_mapped) || 0, Number(roi.avg_read_to_edit_ratio_mapped) || 0,
          'Coding sessions that loaded MAP.md in the first turns or invoked graphify')}
        ${col('Unmapped · Blind Traversal', 'border-slate-700 bg-dark-950/40 text-slate-300', unmappedN,
          Number(roi.avg_turns_to_first_edit_unmapped) || 0, Number(roi.avg_read_to_edit_ratio_unmapped) || 0,
          'Coding sessions with no spatial index guidance')}
      </div>
      <div class="flex flex-wrap items-center gap-x-8 gap-y-1.5 mt-3 pt-2.5 border-t border-slate-800/60 text-xs">
        <span class="text-slate-400">Net exploration tokens saved:
          <b class="font-mono ${netSaved >= 0 ? 'text-emerald-300' : 'text-rose-300'}">${netSaved.toLocaleString()}</b></span>
        <span class="text-slate-400">Counterfactual USD saved:
          <b class="font-mono ${usdSaved >= 0 ? 'text-emerald-300' : 'text-rose-300'}">$${usdSaved.toFixed(4)}</b></span>
        <span class="text-[10px] text-slate-500" title="Net = (E_unmapped_avg − E_session) − S_session per mapped session, priced via pricing_engine">Net = (E&#772;<sub>unmapped</sub> − E<sub>session</sub>) − S<sub>session</sub></span>
      </div>`;
  }

  // --- Compaction & Amnesia Tax indicator ---
  const compEl = document.getElementById('compactionAmnesiaStats');
  if (compEl) {
    const compactions = Number(compaction.total_compactions) || 0;
    const ratio = Number(compaction.avg_compression_ratio) || 0;
    const reclaimed = Number(compaction.total_tokens_reclaimed) || 0;
    const amnesia = Number(compaction.total_amnesia_reread_tokens) || 0;
    const amnesiaPct = (reclaimed + amnesia) > 0 ? (amnesia / (reclaimed + amnesia)) * 100 : 0;
    const tile = (label, value, sub, cls, tip) => `
      <div class="rounded-lg border border-slate-800 bg-dark-950/40 px-3.5 py-2.5" ${tip ? `title="${escapeHtml(tip)}"` : ''}>
        <div class="text-[10px] font-semibold uppercase tracking-wider text-slate-500">${escapeHtml(label)}</div>
        <div class="text-lg font-bold font-mono mt-0.5 ${cls}">${value}</div>
        <div class="text-[10px] text-slate-500 mt-0.5">${escapeHtml(sub)}</div>
      </div>`;
    compEl.innerHTML =
      tile('Compaction Events', compactions.toLocaleString(), 'context boundary boundaries', 'text-white') +
      tile('Avg Compression Ratio', `${(ratio * 100).toFixed(1)}%`, 'tokens discarded at compaction', 'text-cyan-300') +
      tile('Tokens Reclaimed', fmtTok(reclaimed), `${reclaimed.toLocaleString()} tokens freed by compaction`, 'text-emerald-300') +
      tile('Amnesia Tax', `${fmtTok(amnesia)} <span class="text-xs ${amnesiaPct > 30 ? 'text-rose-300' : 'text-amber-300'}">(${amnesiaPct.toFixed(1)}%)</span>`, 'post-compaction re-reads of known files', 'text-amber-300',
        'Amnesia Tax % = amnesia re-read tokens as a share of (reclaimed + amnesia) tokens. High values mean the agent keeps re-reading files it already saw before compaction.');
  }

  // --- Top Thrash Files table (5 rows) ---
  const thrashBody = document.getElementById('topThrashFilesBody');
  if (thrashBody) {
    const files = (data.top_thrash_files || []).slice(0, 5);
    if (files.length === 0) {
      thrashBody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-slate-600">No exploration thrash recorded in this window.</td></tr>`;
    } else {
      thrashBody.innerHTML = files.map(f => {
        const inMap = f.in_map_md
          ? '<span class="px-1.5 py-0.5 rounded bg-emerald-950/60 border border-emerald-500/30 text-emerald-300 text-[10px]" title="Documented in docs/MAP.md">in MAP</span>'
          : '<span class="px-1.5 py-0.5 rounded bg-amber-950/60 border border-amber-500/30 text-amber-300 text-[10px]" title="Not documented in docs/MAP.md — candidate for an architectural summary">undocumented</span>';
        return `<tr class="hover:bg-slate-800/40">
          <td class="py-2 px-3 max-w-md truncate text-slate-200" title="${escapeHtml(f.path)}">${escapeHtml(f.path)}</td>
          <td class="py-2 px-3 text-slate-300">${(f.unedited_read_count || 0).toLocaleString()}×</td>
          <td class="py-2 px-3 text-rose-300">${(f.wasted_tokens || 0).toLocaleString()} <span class="text-slate-500">(${fmtTok(f.wasted_tokens || 0)})</span></td>
          <td class="py-2 px-3">${inMap}</td>
        </tr>`;
      }).join('');
    }
  }

  // --- Ingress gateway attribution strip ---
  const strip = document.getElementById('ingressAttributionStrip');
  if (strip) {
    const ingress = data.ingress_breakdown || {};
    const entries = Object.entries(ingress).filter(([, v]) => (v && ((v.sessions || 0) > 0 || (v.total_tokens || 0) > 0)));
    if (entries.length === 0) {
      strip.innerHTML = `<span class="text-[11px] text-slate-600">No ingress-attributed sessions in this window.</span>`;
    } else {
      entries.sort((a, b) => ((b[1].total_tokens || 0)) - ((a[1].total_tokens || 0)));
      strip.innerHTML = entries.map(([ch, v]) =>
        `${ingressBadgeHtml(ch)}<span class="text-[11px] font-mono text-slate-400 mr-2">${(v.sessions || 0).toLocaleString()} sess · ${fmtTok(v.total_tokens || 0)} tok</span>`
      ).join('');
    }
  }

  lucide.createIcons();
}
window.renderContextTax = renderContextTax;

function renderVelocityHud(data) {
  const v = data.inference_velocity_summary || {};
  const fmt = (val) => (typeof val === 'number' ? val.toFixed(1) : '~0.0');

  const apuDecode = document.getElementById('hudApuDecode');
  if (apuDecode) {
    const liveTag = v.is_live_telemetry ? ' <span class="px-1 py-0.5 rounded text-[9px] font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 align-middle">LIVE</span>' : '';
    apuDecode.innerHTML = `~${fmt(v.apu_decode_tok_per_sec ?? 22.5)} <span class="text-xs font-medium text-emerald-400/70">tok/s</span>${liveTag}`;
  }

  const apuPrefill = document.getElementById('hudApuPrefill');
  if (apuPrefill) apuPrefill.innerHTML = `~${fmt(v.apu_prefill_tok_per_sec ?? 294.0)} <span class="text-xs font-medium text-cyan-400/70">tok/s</span>`;

  const cloudDecode = document.getElementById('hudCloudDecode');
  if (cloudDecode) cloudDecode.innerHTML = `~${fmt(v.cloud_decode_tok_per_sec ?? 145.0)} <span class="text-xs font-medium text-indigo-400/70">tok/s</span>`;

  const fastest = document.getElementById('hudFastestModel');
  if (fastest) {
    const name = v.fastest_model || 'gemini-3.8-flash';
    fastest.innerHTML = `⚡ Fastest: <span class="font-semibold">${escapeHtml(name)}</span>`;
    fastest.title = `${name} leads the roster on decode throughput`;
  }

  const longest = document.getElementById('hudLongestContextModel');
  if (longest) {
    const name = v.longest_context_local_model || 'qwen3.8-flash-next:262k';
    longest.innerHTML = `🧠 Longest local context: <span class="font-semibold">${escapeHtml(name)}</span>`;
    longest.title = `${name} — longest context window served on the local APU`;
  }
}

function renderAnalyticsCharts(data) {
  // 1. Model chart (total tokens: prompt + completion)
  const modelCtx = document.getElementById('modelChart');
  if (modelCtx) {
    if (modelChartInstance) {
      try { modelChartInstance.destroy(); } catch (e) {}
    }
    const modelLabels = (data.model_distribution || []).map(m => m.model);
    const modelTokens = (data.model_distribution || []).map(m => (m.input_tokens || 0) + (m.output_tokens || 0));
    const modelColors = modelLabels.map(m => getModelColor(m));

    try {
      modelChartInstance = new Chart(modelCtx, {
        type: 'doughnut',
        data: {
          labels: modelLabels,
          datasets: [{
            data: modelTokens,
            backgroundColor: modelColors
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
    } catch (err) {
      console.warn('Error creating model chart:', err);
    }
  }

  // 1b. Output tokens doughnut — same labels/colors as the total-tokens chart (TASK-HK-109)
  const modelOutputCtx = document.getElementById('modelOutputChart');
  if (modelOutputCtx) {
    if (modelOutputChartInstance) {
      try { modelOutputChartInstance.destroy(); } catch (e) {}
    }
    const outputLabels = (data.model_distribution || []).map(m => m.model);
    const outputTokens = (data.model_distribution || []).map(m => m.output_tokens || 0);
    const outputColors = outputLabels.map(m => getModelColor(m));

    try {
      modelOutputChartInstance = new Chart(modelOutputCtx, {
        type: 'doughnut',
        data: {
          labels: outputLabels,
          datasets: [{
            label: 'Output Tokens',
            data: outputTokens,
            backgroundColor: outputColors
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom', labels: { color: '#94a3b8', font: { size: 9 }, boxWidth: 10 } },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  const total = outputTokens.reduce((a, b) => a + b, 0);
                  const val = ctx.parsed || 0;
                  const pct = total > 0 ? ((val / total) * 100).toFixed(1) : '0.0';
                  return ` ${ctx.label}: ${val.toLocaleString()} out tok (${pct}%)`;
                }
              }
            }
          }
        }
      });
    } catch (err) {
      console.warn('Error creating model output chart:', err);
    }
  }

  // 1c. Generation velocity comparator — horizontal bar of decode tok/s (TASK-HK-112)
  const velocityCtx = document.getElementById('velocityChart');
  if (velocityCtx) {
    if (velocityChartInstance) {
      try { velocityChartInstance.destroy(); } catch (e) {}
    }
    const ranked = (data.model_distribution || [])
      .slice()
      .sort((a, b) => ((b.throughput_tok_per_sec || 0) - (a.throughput_tok_per_sec || 0)));
    const velocityLabels = ranked.map(m => m.model);
    const velocityValues = ranked.map(m => m.throughput_tok_per_sec || 0);
    const velocityColors = velocityLabels.map(m => getModelColor(m));

    try {
      velocityChartInstance = new Chart(velocityCtx, {
        type: 'bar',
        data: {
          labels: velocityLabels,
          datasets: [{
            label: 'Decode (tok/s)',
            data: velocityValues,
            backgroundColor: velocityColors,
            borderRadius: 3,
            barThickness: 'flex',
            maxBarThickness: 16
          }]
        },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              beginAtZero: true,
              title: { display: true, text: 'tok/s', color: '#64748b', font: { size: 9 } },
              ticks: { color: '#94a3b8', font: { size: 9 } },
              grid: { color: 'rgba(148,165,184,0.08)' }
            },
            y: { ticks: { color: '#cbd5e1', font: { size: 9 } } }
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  const m = ranked[ctx.dataIndex] || {};
                  const tier = m.throughput_tier || 'standard';
                  const val = ctx.parsed && typeof ctx.parsed.x === 'number' ? ctx.parsed.x : (ctx.parsed || 0);
                  return ` ${val.toFixed(1)} tok/s · ${tier === 'local_apu' ? 'local APU' : tier}`;
                }
              }
            }
          }
        }
      });
    } catch (err) {
      console.warn('Error creating velocity chart:', err);
    }
  }

  // 2. Provider breakdown chart
  const providerCtx = document.getElementById('providerChart');
  if (providerCtx) {
    if (providerChartInstance) {
      try { providerChartInstance.destroy(); } catch (e) {}
    }
    const providerLabels = (data.provider_distribution || []).map(p => p.provider);
    const providerTokens = (data.provider_distribution || []).map(p => (p.input_tokens || 0) + (p.output_tokens || 0));

    try {
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
    } catch (err) {
      console.warn('Error creating provider chart:', err);
    }
  }

  // 3. Tool chart
  const toolCtx = document.getElementById('toolChart');
  if (toolCtx) {
    if (toolChartInstance) {
      try { toolChartInstance.destroy(); } catch (e) {}
    }
    const toolLabels = (data.tool_distribution || []).slice(0, 8).map(t => t.tool_name);
    const toolCounts = (data.tool_distribution || []).slice(0, 8).map(t => t.count);

    try {
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
    } catch (err) {
      console.warn('Error creating tool chart:', err);
    }
  }
}

// ----------------------------------------------------
// SPEC-HK-006: TEMPORAL USAGE & COST ANALYZER
// ----------------------------------------------------
let temporalUsageChartInstance = null;

function renderTemporalUsageChart(data) {
  const ctx = document.getElementById('temporalUsageChart');
  if (!ctx) return;
  if (!data.temporal_usage) return;
  
  if (temporalUsageChartInstance) {
    try { temporalUsageChartInstance.destroy(); } catch (e) {}
  }

  const tData = data.temporal_usage;
  const labels = tData.labels;
  const metric = currentTemporalMetric || 'input_tokens';
  const seriesDict = tData.series[metric] || {};
  
  const datasets = [];
  tData.models.forEach((m) => {
    const rawData = seriesDict[m] || [];
    // Only include if model has any data in this window
    if (rawData.some(v => v > 0)) {
      datasets.push({
        label: m,
        data: rawData,
        backgroundColor: getModelColor(m),
        borderWidth: 0,
        fill: true,
      });
    }
  });

  const titleMap = {
    'input_tokens': 'Input Tokens (Prompt)',
    'output_tokens': 'Output Tokens (Completion)',
    'cost_usd': 'Accrued Cost ($)',
    'total_tokens': 'Total Tokens (Input + Output)'
  };
  
  const isCost = metric === 'cost_usd';

  try {
    temporalUsageChartInstance = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: datasets
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { 
            stacked: true,
            ticks: { color: '#94a3b8', font: { size: 10 } },
            grid: { color: '#1e293b' }
          },
          y: { 
            stacked: true,
            ticks: { 
              color: '#94a3b8', 
              font: { size: 10 },
              callback: function(value) {
                if (isCost) return '$' + value.toFixed(2);
                if (value >= 1000000) return (value / 1000000).toFixed(1) + 'M';
                if (value >= 1000) return (value / 1000).toFixed(1) + 'k';
                return value;
              }
            },
            grid: { color: '#1e293b' }
          }
        },
        plugins: {
          legend: { position: 'bottom', labels: { color: '#cbd5e1', font: { size: 10 }, boxWidth: 12, usePointStyle: true } },
          tooltip: {
            mode: 'index',
            intersect: false,
            callbacks: {
              label: (context) => {
                let val = context.parsed.y;
                return ` ${context.dataset.label}: ${isCost ? '$' + val.toFixed(4) : val.toLocaleString()}`;
              },
              footer: (tooltipItems) => {
                let total = tooltipItems.reduce((sum, item) => sum + item.parsed.y, 0);
                return `\nTotal: ${isCost ? '$' + total.toFixed(4) : total.toLocaleString()}`;
              }
            }
          }
        }
      }
    });
  } catch (err) {
    console.warn('Error creating temporal usage chart:', err);
  }
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
    if (kpiAgents) kpiAgents.innerText = `${summ.total_agents || 10} Agents`;

    const headerBadge = document.getElementById('pantheonHeaderBadge');
    if (headerBadge) headerBadge.innerText = `${summ.total_agents || 10} Autonomous Specialists`;

    const kpiLocal = document.getElementById('pantheonKpiLocal');
    if (kpiLocal) kpiLocal.innerText = `${summ.local_agents || 9} APU/Local • ${summ.cloud_agents || 1} Cloud`;

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
        sessTbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-slate-500">No sessions executed for this agent yet</td></tr>`;
      } else {
        sessTbody.innerHTML = p.sessions.map(s => {
          const dateStr = s.started_at ? new Date(s.started_at * 1000).toLocaleString() : 'N/A';
          const tok = (s.input_tokens || 0) + (s.output_tokens || 0);
          const isSub = s.source === 'subagent' || s.parent_session_id;
          const subBadge = isSub ? '<span class="ml-1.5 px-1.5 py-0.5 rounded bg-indigo-950/80 border border-indigo-500/30 text-indigo-300 text-[9px] font-mono">Subagent</span>' : '';
          return `
            <tr class="hover:bg-slate-800/40 cursor-pointer" onclick="closePantheonDrawer(); openSessionTimeline('${s.id}')">
              <td class="py-2.5 px-3 text-brand-400 font-semibold font-mono text-xs">${escapeHtml(s.id)}${subBadge}</td>
              <td class="py-2.5 px-3 text-slate-200 font-medium text-xs">${escapeHtml(s.title || 'Untitled Session')}</td>
              <td class="py-2.5 px-3 text-slate-400 font-mono text-xs">${escapeHtml(s.model || p.model)}</td>
              <td class="py-2.5 px-3 text-slate-400 font-mono text-xs">${s.message_count || 0}</td>
              <td class="py-2.5 px-3 text-indigo-300 font-mono text-xs">${tok.toLocaleString()}</td>
              <td class="py-2.5 px-3 text-slate-500 text-[11px] font-mono">${dateStr}</td>
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
    if (s.status === 'RUNNING_TOOL' || s.status === 'RUNNING_SUBAGENT') {
      statusClass = 'bg-amber-950 text-amber-300 border border-amber-500/40 animate-pulse';
    } else if (s.status === 'WAITING') {
      statusClass = 'bg-indigo-950 text-indigo-400 border border-indigo-500/30';
    } else if (s.status === 'STALE') {
      statusClass = 'bg-slate-800 text-slate-400';
    }

    const isSubagent = s.is_subagent || s.platform === 'subagent';
    const personaPill = s.persona_id ? 
      `<span class="px-2 py-0.5 rounded bg-brand-950/80 border border-brand-500/30 text-brand-300 text-[11px] font-mono font-semibold flex items-center space-x-1"><span>${s.persona_emoji || '🤖'}</span><span>${s.persona_name}</span></span>` : '';

    return `
      <div class="bg-dark-900 border ${isSubagent ? 'border-amber-500/40' : 'border-slate-800/80'} rounded-xl p-5 shadow-sm space-y-3 flex flex-col justify-between">
        <div class="space-y-2">
          <div class="flex items-center justify-between">
            <span class="px-2.5 py-0.5 rounded-full text-[11px] font-bold ${statusClass}">
              ${s.status}
            </span>
            <div class="flex items-center space-x-1.5">
              ${personaPill}
              <span class="text-xs text-slate-400 font-mono">${s.platform || 'cli'}</span>
            </div>
          </div>

          <h3 class="font-bold text-slate-200 text-sm truncate" title="${escapeHtml(s.title)}">${escapeHtml(s.title || 'Session')}</h3>
          <div class="flex items-center justify-between text-xs text-slate-400 font-mono">
            <span class="truncate max-w-[160px]">${s.session_id}</span>
            <span class="text-indigo-400 font-semibold truncate max-w-[120px]">${s.model || 'model'}</span>
          </div>

          ${s.current_tool ? `
            <div class="p-2 rounded bg-dark-950 border border-amber-500/30 text-amber-300 font-mono text-xs flex items-center space-x-2">
              <i data-lucide="terminal" class="w-3.5 h-3.5"></i>
              <span>Tool: <strong>${escapeHtml(s.current_tool)}</strong></span>
            </div>
          ` : (s.last_line ? `
            <div class="p-2 rounded bg-dark-950 border border-slate-800 text-slate-300 font-mono text-xs truncate">
              <span class="text-slate-500">Log:</span> ${escapeHtml(s.last_line)}
            </div>
          ` : '')}

          <div class="text-[11px] text-slate-400 font-mono space-y-0.5 pt-2 border-t border-slate-800/60">
            ${s.parent_session_id ? `<div>Parent: <a href="#" onclick="openSessionTimeline('${s.parent_session_id}'); return false;" class="text-indigo-400 hover:underline">${s.parent_session_id}</a></div>` : ''}
            <div>Age: <span class="text-slate-200">${s.age_seconds ? `${s.age_seconds}s ago` : 'Active'}</span></div>
            <div>PID: <span class="text-slate-200">${s.pid || 'Subagent'}</span> • Pane: <span class="text-slate-200">${s.tmux_pane || 'N/A'}</span></div>
            <div class="truncate">Dir: <span class="text-slate-200">${escapeHtml(s.working_directory || '~')}</span></div>
          </div>
        </div>

        <div class="pt-3 border-t border-slate-800 flex items-center justify-between">
          <button onclick="openSessionTimeline('${s.session_id}')" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium">
            Inspect
          </button>

          <button onclick="triggerFocusSession('${s.session_id}', ${s.pid || 'null'}, '${s.tmux_pane || ''}')" class="px-3 py-1.5 rounded bg-brand-600 hover:bg-brand-500 text-white text-xs font-semibold flex items-center space-x-1 shadow-sm">
            <i data-lucide="terminal" class="w-3 h-3"></i>
            <span>Focus</span>
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
let fleetGpuHistoryChartInstance = null;
let currentFleetGpuTimeRange = '30d';

async function setFleetGpuTimeRange(range) {
  currentFleetGpuTimeRange = range;
  document.querySelectorAll('.fleet-gpu-btn').forEach(btn => {
    if (btn.getAttribute('data-range') === range) {
      btn.className = 'fleet-gpu-btn px-3 py-1 rounded font-medium transition bg-brand-500/20 text-brand-300 border border-brand-500/30';
    } else {
      btn.className = 'fleet-gpu-btn px-3 py-1 rounded font-medium transition text-slate-400 hover:text-white';
    }
  });
  loadFleetGpuHistory();
}
window.setFleetGpuTimeRange = setFleetGpuTimeRange;

async function loadFleetGpuHistory() {
  const ctx = document.getElementById('fleetGpuHistoryChart');
  if (!ctx) return;

  try {
    const res = await fetch(`/api/nodes/history?time_range=${currentFleetGpuTimeRange}`);
    const data = await res.json();

    if (fleetGpuHistoryChartInstance) {
      try { fleetGpuHistoryChartInstance.destroy(); } catch(e) {}
    }

    const chunkito = data.nodes['chunkito'] || [];
    const beehive = data.nodes['beehive'] || [];
    if (chunkito.length === 0 && beehive.length === 0) return;

    const primarySeries = chunkito.length > 0 ? chunkito : beehive;

    const labels = primarySeries.map(s => {
      // Shorten label based on range
      if (currentFleetGpuTimeRange === '24h' || currentFleetGpuTimeRange === 'today') {
        const parts = s.datetime.split(' ');
        return parts.length > 1 ? parts[1] : s.datetime;
      }
      return s.datetime.split(' ')[0];
    });

    const busyData = chunkito.map(s => s.gpu_busy_percent);
    const powerData = chunkito.map(s => s.power_w);

    // Two-pointer nearest-timestamp alignment for beehive GPU load
    let bIdx = 0;
    const beehiveBusyData = primarySeries.map(p => {
      if (beehive.length === 0) return 0;
      const targetTs = p.timestamp;
      while (bIdx < beehive.length - 1 && Math.abs(beehive[bIdx + 1].timestamp - targetTs) < Math.abs(beehive[bIdx].timestamp - targetTs)) {
        bIdx++;
      }
      return beehive[bIdx] ? beehive[bIdx].gpu_busy_percent : 0;
    });

    fleetGpuHistoryChartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'GPU Core Busy (%)',
            data: busyData,
            borderColor: '#06b6d4',
            backgroundColor: 'rgba(6, 182, 212, 0.1)',
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 10,
            fill: true,
            yAxisID: 'y'
          },
          {
            label: 'Beehive GPU Busy (%)',
            data: beehiveBusyData,
            borderColor: '#10b981',
            backgroundColor: 'rgba(16, 185, 129, 0.05)',
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 10,
            fill: false,
            yAxisID: 'y'
          },
          {
            label: 'Power Draw (W)',
            data: powerData,
            borderColor: '#f59e0b',
            borderWidth: 1,
            borderDash: [5, 5],
            pointRadius: 0,
            pointHitRadius: 10,
            fill: false,
            yAxisID: 'y1'
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        scales: {
          x: { 
            ticks: { color: '#94a3b8', font: { size: 10 } },
            grid: { color: '#1e293b' }
          },
          y: { 
            type: 'linear',
            display: true,
            position: 'left',
            min: 0,
            max: 100,
            ticks: { color: '#06b6d4', font: { size: 10 }, callback: v => v + '%' },
            grid: { color: '#1e293b' }
          },
          y1: {
            type: 'linear',
            display: true,
            position: 'right',
            min: 0,
            ticks: { color: '#f59e0b', font: { size: 10 }, callback: v => v + ' W' },
            grid: { drawOnChartArea: false }
          }
        },
        plugins: {
          legend: { position: 'bottom', labels: { color: '#cbd5e1', font: { size: 10 }, usePointStyle: true } }
        }
      }
    });
  } catch(err) {
    console.warn('Error loading fleet GPU history:', err);
  }
}

async function loadNodes(force = false) {
  loadFleetGpuHistory();
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

function renderInstrumentCluster(n) {
  // ══ Telemetry binding (SPEC-HK-005 §2.3) ════════════════════════════════════
  // n.hardware.gpu is a human-readable STRING, never a metrics object. Real APU
  // telemetry is published on n.amdgpu / n.apu_vram / n.inference_engine.
  const gpu = n.amdgpu || {};
  const apu = n.apu_vram || {};
  const inf = n.inference_engine || n.ollama || {};
  const hw = n.hardware || {};
  const sys = n.system || n.cpu || {};

  const gpuLoad = gpu.gpu_busy_percent !== undefined ? gpu.gpu_busy_percent : 0;
  const temp = gpu.temperature_c !== undefined ? gpu.temperature_c : (gpu.temp_c || 30);
  const power = gpu.power_w !== undefined ? gpu.power_w : 0;
  const gttTotal = gpu.gtt_total_gb || apu.total_gb || hw.vram_gb || (hw.ram_gb ? (hw.ram_gb > 64 ? 118 : 16) : 16);
  const gttUsed = Math.min(gpu.gtt_used_gb !== undefined ? gpu.gtt_used_gb : (apu.used_gb || 0), gttTotal);
  const tdpMax = sys.tdp_w || (hw.ram_gb > 64 ? 120 : 54);
  const activeSlots = inf.slot_summary ? (inf.slot_summary.active_slots || 0) : 0;
  const totalSlots = inf.slot_summary ? (inf.slot_summary.total_slots || 0) : (inf.slots ? inf.slots.length : 2);

  const clampPct = (p) => Math.min(100, Math.max(0, isFinite(p) ? p : 0));
  const tempPct = clampPct(((temp - 30) / 80) * 100); // coolant band: 30 °C → 110 °C
  const gttPct = gttTotal > 0 ? clampPct((gttUsed / gttTotal) * 100) : 0;
  const powerPct = tdpMax > 0 ? clampPct((power / tdpMax) * 100) : 0;
  const slotPct = totalSlots > 0 ? clampPct((activeSlots / totalSlots) * 100) : 0;

  // ══ 240° clock-sweep geometry (SPEC-HK-005 §2.1) ════════════════════════════
  // 0% at 8 o'clock (-210°) · 50% at 12 o'clock (-90°) · 100% at 4 o'clock (+30°)
  const A0 = -210;          // sweep start (lower left)
  const SPAN = 240;         // total sweep
  const A1 = A0 + SPAN;     // +30 (lower right)
  const _rad = (d) => d * Math.PI / 180;
  const _polar = (cx, cy, r, a) => [cx + r * Math.cos(_rad(a)), cy + r * Math.sin(_rad(a))];
  const theta = (p) => A0 + clampPct(p) * (SPAN / 100);      // arc end angle for P%
  const needleRot = (p) => theta(p) + 90;                    // needle polygon points north

  function arc(cx, cy, r, a0, a1) {
    if (a1 <= a0) return '';
    const [x0, y0] = _polar(cx, cy, r, a0);
    const [x1, y1] = _polar(cx, cy, r, a1);
    const large = Math.abs(a1 - a0) > 180 ? 1 : 0;
    return `M ${x0.toFixed(1)} ${y0.toFixed(1)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  }

  // Major (3px) + optional minor tick ruler around the sweep
  function ruler(cx, cy, rIn, rOut, marks, labelSize, minorEvery) {
    const out = [];
    if (minorEvery) {
      for (let p = 0; p <= 100; p += minorEvery) {
        const majors = marks.some((m) => Math.abs(m[0] - p) < 0.001);
        if (majors) continue;
        const [x1, y1] = _polar(cx, cy, rOut - 5, theta(p));
        const [x2, y2] = _polar(cx, cy, rOut, theta(p));
        out.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="#475569" stroke-width="1.5"/>`);
      }
    }
    marks.forEach(([p, label, color]) => {
      const a = theta(p);
      const [x1, y1] = _polar(cx, cy, rIn, a);
      const [x2, y2] = _polar(cx, cy, rOut, a);
      out.push(`<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${color}" stroke-width="3" stroke-linecap="round"/>`);
      if (label !== null && label !== undefined) {
        const [tx, ty] = _polar(cx, cy, rOut + 11, a);
        out.push(`<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" dy="0.32em" fill="${color}" font-size="${labelSize || 10}" font-weight="700" font-family="${MONO}" text-anchor="middle">${label}</text>`);
      }
    });
    return out.join('\n        ');
  }

  // ══ Classic automotive palette (SPEC-HK-005 §2.2) ═══════════════════════════
  const C = {
    rail: '#232a38', face: '#141a26', bezel: '#1e293b',
    white: '#ffffff', slate: '#94a3b8', sky: '#38bdf8',
    cyan: '#00e5ff', amber: '#ff9100', red: '#ff1744', tach: '#ff3b30', idle: '#475569'
  };
  const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  const SANS = 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif';

  const tempColor = temp >= 85 ? C.red : temp >= 70 ? C.amber : C.cyan;
  const powerColor = powerPct > 90 ? C.red : C.amber;
  const slotColor = slotPct > 0 ? C.cyan : C.idle;

  const cid = n.id ? String(n.id).replace(/[^a-zA-Z0-9_-]/g, '') : (n.node_id ? String(n.node_id).replace(/[^a-zA-Z0-9_-]/g, '') : 'node');

  // One dial = bezel + rail + redline zone + active fill (segmented gradient) +
  // ruler + needle + digital readouts in the open lower sector.
  function dial(cfg) {
    const { cx, cy, r } = cfg;
    const tw = cfg.trackW || 12;
    const faceR = cfg.faceR || r + 9;
    const hubR = cfg.hubR || 6.5;
    const pct = clampPct(cfg.pct);
    const g = [];

    g.push(`<circle cx="${cx}" cy="${cy}" r="${faceR}" fill="url(#face-${cid})" stroke="${C.bezel}" stroke-width="1"/>`);
    g.push(`<path d="${arc(cx, cy, r, A0, A1)}" fill="none" stroke="${C.rail}" stroke-width="${tw}" stroke-linecap="round"/>`);

    if (cfg.redlineFrom !== undefined) {
      g.push(`<path d="${arc(cx, cy, r, theta(cfg.redlineFrom), A1)}" fill="none" stroke="${C.red}" stroke-width="${tw}" stroke-linecap="round" opacity="0.3"/>`);
    }

    // Active fill: colour segments (0–70 cyan, 70–85 amber, 85–100 redline) or one solid hue.
    const segs = cfg.segments || [[0, 100, cfg.color]];
    let leading = segs[0][2];
    let drewAny = false;
    segs.forEach(([from, to, col], i) => {
      const f = Math.min(from, pct);
      const t = Math.min(to, pct);
      if (t <= f) return;
      leading = col;
      drewAny = true;
      const capLast = i === segs.length - 1 || t < to ? 'round' : 'butt';
      const d = arc(cx, cy, r, theta(f), theta(t));
      if (d) g.push(`<path d="${d}" fill="none" stroke="${col}" stroke-width="${tw}" stroke-linecap="${i === 0 ? 'round' : capLast}"/>`);
    });
    if (!drewAny) {
      // Rest position nub so a dead-flat needle still reads as "0", not "missing".
      const [nx, ny] = _polar(cx, cy, r, A0);
      g.push(`<circle cx="${nx.toFixed(1)}" cy="${ny.toFixed(1)}" r="${(tw / 2).toFixed(1)}" fill="${cfg.color}" opacity="0.8"/>`);
    }

    if (cfg.marks && cfg.marks.length) {
      g.push(ruler(cx, cy, cfg.tickIn || r - 1, cfg.tickOut || r + 4, cfg.marks, cfg.tickLabelSize, cfg.minorEvery));
    }

    const L = cfg.needleLen || (r - 8);
    const hubR2 = (hubR + 4.5).toFixed(1);
    g.push(`<g transform="rotate(${needleRot(pct).toFixed(2)} ${cx} ${cy})">
          <polygon points="${(cx - 2.8).toFixed(1)},${cy} ${(cx + 2.8).toFixed(1)},${cy} ${(cx + 0.9).toFixed(1)},${(cy - L).toFixed(1)} ${(cx - 0.9).toFixed(1)},${(cy - L).toFixed(1)}" fill="${cfg.needle}" stroke="${cfg.needleEdge || 'none'}" stroke-width="${cfg.needleEdge ? 1 : 0}"/>
          <circle cx="${cx}" cy="${cy}" r="${hubR2}" fill="${cfg.hubGlow || cfg.color}" opacity="0.22"/>
          <circle cx="${cx}" cy="${cy}" r="${hubR}" fill="${cfg.hub || C.white}" stroke="${cfg.hubRing || cfg.color}" stroke-width="2"/>
        </g>`);

    const valueY = cfg.valueY || 165;
    const labelY = cfg.labelY || 182;
    g.push(`<text x="${cx}" y="${valueY}" fill="${cfg.valueColor || C.white}" font-size="${cfg.valueSize || 22}" font-weight="700" font-family="${MONO}" text-anchor="middle">${cfg.value}</text>`);
    g.push(`<text x="${cx}" y="${labelY}" fill="${cfg.labelColor || C.slate}" font-size="${cfg.labelSize || 9}" font-weight="700" font-family="${SANS}" text-anchor="middle" letter-spacing="1.4">${cfg.label}</text>`);

    return g.join('\n        ');
  }

  // ── Layout (viewBox 0 0 760 215): readouts sit on the y=165 / y=182 baseline ──
  // TEMP cx=65 · GPU TACH cx=245 · SLOTS cx=380 · GTT cx=515 · POWER cx=695
  const svg = `
    <svg viewBox="0 0 760 215" class="w-full h-auto" style="max-height: 230px" role="img" aria-label="${n.name || 'Node'} instrument cluster: GPU load ${Math.round(gpuLoad)} percent, GTT memory ${gttUsed.toFixed(1)} of ${gttTotal.toFixed(1)} gigabytes, temperature ${temp.toFixed(0)} degrees, power ${power.toFixed(0)} of ${tdpMax} watts, ${activeSlots} of ${totalSlots} slots active">
      <defs>
        <radialGradient id="face-${cid}" cx="50%" cy="42%" r="68%">
          <stop offset="0%" stop-color="${C.face}" />
          <stop offset="100%" stop-color="#0a0d13" />
        </radialGradient>
      </defs>

      <rect x="0" y="0" width="760" height="215" fill="#0a0d13" />
      <rect x="0" y="0" width="760" height="3" fill="${C.bezel}" />
      <rect x="0" y="212" width="760" height="3" fill="${C.bezel}" />

      <!-- Node identity strip -->
      <text x="12" y="18" fill="${C.white}" font-size="11" font-weight="900" font-family="${SANS}" letter-spacing="1">${(n.name || 'NODE').toUpperCase()}</text>
      <text x="748" y="18" fill="${C.sky}" font-size="10" font-weight="700" font-family="${SANS}" text-anchor="end">${n.role || ''}</text>

      <!-- 1. COOLANT TEMP (30–110 °C) -->
      <g>
        ${dial({
          cx: 65, cy: 110, r: 40, faceR: 45, trackW: 10, hubR: 5,
          pct: tempPct, color: C.cyan,
          segments: [[0, 50, C.cyan], [50, 68.75, C.amber], [68.75, 100, C.red]],
          needle: C.white, needleEdge: tempColor, hub: C.white, hubRing: tempColor, hubGlow: tempColor,
          marks: [[0, '30', C.white], [50, '70', C.white], [100, '110', C.white]],
          tickIn: 41, tickOut: 46, tickLabelSize: 8.5,
          value: `${temp.toFixed(1)}&#176;`, valueColor: C.white, valueSize: 15,
          label: 'TEMP', labelColor: C.slate
        })}
      </g>

      <!-- 2. GPU LOAD TACHOMETER (0–100%, redline from 85%) -->
      <g>
        ${dial({
          cx: 245, cy: 110, r: 75, faceR: 84, trackW: 12, hubR: 6.5,
          pct: gpuLoad, color: C.cyan,
          segments: [[0, 70, C.cyan], [70, 85, C.amber], [85, 100, C.red]],
          redlineFrom: 85,
          needle: C.tach, needleEdge: '', hub: C.white, hubRing: C.tach, hubGlow: C.tach,
          marks: [[0, '0', C.white], [25, '25', C.white], [50, '50', C.white], [75, '75', C.white], [100, '100', C.red]],
          tickIn: 87, tickOut: 93, tickLabelSize: 10, minorEvery: 5,
          value: `${Math.round(gpuLoad)}%`, valueColor: C.white, valueSize: 22,
          label: 'GPU LOAD', labelColor: C.sky
        })}
      </g>

      <!-- 3. INFERENCE SLOTS -->
      <g>
        ${dial({
          cx: 380, cy: 110, r: 32, faceR: 37, trackW: 10, hubR: 4.5,
          pct: slotPct, color: C.idle,
          needle: C.white, needleEdge: slotColor, hub: C.white, hubRing: slotColor, hubGlow: slotColor,
          marks: [],
          value: `${activeSlots}/${totalSlots}`, valueColor: C.white, valueSize: 15,
          label: 'SLOTS', labelColor: C.slate
        })}
      </g>

      <!-- 4. GTT UNIFIED MEMORY SPEEDO (0–gttTotal GB) -->
      <g>
        ${dial({
          cx: 515, cy: 110, r: 75, faceR: 84, trackW: 12, hubR: 6.5,
          pct: gttPct, color: C.cyan,
          needle: C.white, needleEdge: C.cyan, hub: C.white, hubRing: C.cyan, hubGlow: C.cyan,
          marks: [[0, '0', C.cyan], [25, Math.round(gttTotal / 4), C.white], [50, Math.round(gttTotal / 2), C.white], [75, Math.round(3 * gttTotal / 4), C.white], [100, Math.round(gttTotal), C.cyan]],
          tickIn: 87, tickOut: 93, tickLabelSize: 10, minorEvery: 5,
          value: `${gttPct.toFixed(0)}%`, valueColor: C.white, valueSize: 22,
          label: `${gttUsed.toFixed(1)} / ${gttTotal.toFixed(1)} GB GTT`, labelColor: C.cyan, labelSize: 9
        })}
      </g>

      <!-- 5. POWER / TDP FUEL GAUGE (0–${tdpMax}W, redline above 90%) -->
      <g>
        ${dial({
          cx: 695, cy: 110, r: 40, faceR: 45, trackW: 10, hubR: 5,
          pct: powerPct, color: C.amber,
          segments: [[0, 90, C.amber], [90, 100, C.red]],
          redlineFrom: 90,
          needle: C.white, needleEdge: powerColor, hub: C.white, hubRing: powerColor, hubGlow: powerColor,
          marks: [[0, '0', C.white], [50, '50', C.white], [100, '100', C.white]],
          tickIn: 41, tickOut: 46, tickLabelSize: 8.5,
          value: `${Math.round(powerPct)}%`, valueColor: C.white, valueSize: 15,
          label: `${power.toFixed(1)}/${tdpMax}W TDP`, labelColor: C.amber, labelSize: 8.5
        })}
      </g>
    </svg>
  `;
  return svg;
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

            <!-- Automotive Instrument Cockpit Cluster -->
            ${renderInstrumentCluster(n)}
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
                          <div>Ctx: ${(s.n_ctx || 262144).toLocaleString()} | Task: <span class="text-slate-300">${s.id_task || 'Idle'}</span></div>
                          <div>Tokens Processed: <span class="text-slate-200">${promptProcessed.toLocaleString()}</span> • Cache: <span class="text-emerald-400">${promptCache.toLocaleString()}</span></div>
                          <div class="text-[9px] text-slate-500">Worker Model: <span class="text-brand-300 font-semibold">${loadedModels[0]?.name || 'qwen3.8-flash-next'}</span> • Temp: ${s.params?.temperature !== undefined ? s.params.temperature.toFixed(2) : '0.70'}</div>
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

// Explicit window bindings for inline HTML handlers
window.switchTab = switchTab;
window.setAnalyticsTimeRange = setAnalyticsTimeRange;
window.loadPantheon = loadPantheon;
window.openPantheonDrawer = openPantheonDrawer;
window.closePantheonDrawer = closePantheonDrawer;
window.switchDrawerTab = switchDrawerTab;
window.loadSessions = loadSessions;
window.loadAnalytics = loadAnalytics;
window.renderVelocityHud = renderVelocityHud;
window.throughputBadge = throughputBadge;
window.loadSkills = loadSkills;
window.loadMemory = loadMemory;
window.loadLiveSessions = loadLiveSessions;
window.loadCron = loadCron;
window.loadNodes = loadNodes;
window.loadTickets = loadTickets;
window.changePage = changePage;
window.filterSkillsList = filterSkillsList;
window.refreshAllNodes = refreshAllNodes;
window.refreshSingleNode = refreshSingleNode;
window.switchModalTab = switchModalTab;
window.selectSkill = selectSkill;
window.addTicketToSession = addTicketToSession;
window.syncTicketStatus = syncTicketStatus;
