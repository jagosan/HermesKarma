
Problem statement: It is challenging to track and manage model usage across a heterogenous model strategy. And no dedicated 1:1 telemetry, trajectory inspection, and session analytics dashboard like Claude Code Karma (https://github.com/JayantDevkar/claude-code-karma) currently exists specifically tailored for Hermes Agent. While Hermes Agent has general interaction frontends and fleet surfaces (such as hermes-workspace and mission-control), none provide the deep local-first observability, token/cost breakdowns, subagent tree tracking, and lifecycle replay that claude-code-karma offers.
Below is the complete architectural and functional specification for Hermes Karma (hermes-karma), including the bridge between Claude Code's file model and Hermes Agent's native storage engine.
Hermes Karma (hermes-karma): Technical Specification
1. Overview & Comparison
Feature / Dimension
Claude Code Karma (~/.claude/)
Hermes Karma (~/.hermes/)
Primary Data Source
~/.claude/projects/*/session.jsonl (30-day auto-cleanup)
~/.hermes/state.db (SQLite + FTS5, permanent) & trajectory_samples.jsonl
Configuration
~/.claude/config.json
~/.hermes/config.yaml
Memory & User State
Ephemeral memory dumps / session context
~/.hermes/MEMORY.md & ~/.hermes/USER.md
Skills Ecosystem
Slash commands /skill & Claude plugins
~/.hermes/skills/ (agentskills.io standard) + self-evolution patches
Agent Invocations
CLI & Claude Desktop
Unified Gateway (CLI, Discord, Slack, Telegram, Signal, Matrix)
Live Telemetry
Shell hooks (~/.claude/hooks/*.py)
Hermes Lifecycle Plugin / SQLite write-ahead log (WAL) polling

2. System Architecture
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              HERMES RUNTIME                                     │
│  ┌────────────────┐   ┌─────────────────────────────┐   ┌────────────────────┐  │
│  │ CLI / Gateway  │──▶│ ~/.hermes/state.db (SQLite) │◀──│ ~/.hermes/skills/  │  │
│  │ (Multi-Client) │   │ (Messages, Tools, Tokens)   │   │ (Autonomous Skills)│  │
│  └────────┬───────┘   └──────────────┬──────────────┘   └────────────────────┘  │
│           │                          │                                          │
│           ▼ (Lifecycle Plugin)       │ (WAL Streaming / Queries)                │
│  ┌─────────────────────────────────┐ │                                          │
│  │ hermes-karma-plugin (Hook/Event)│ │                                          │
└───────────┼──────────────────────────┼──────────────────────────────────────────┘
            │                          │
            ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          HERMES KARMA BACKEND (FastAPI)                         │
│  • Reads ~/.hermes/state.db (Read-Only WAL mode)                                │
│  • Reads ~/.hermes/config.yaml, MEMORY.md, USER.md, & skills directory          │
│  • Writes metadata to ~/.hermes_karma/metadata.db (Ticket Links, Tags, Notes)   │
│  • Terminal Focus IPC: xdotool / AppleScript / tmux socket                      │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │ REST / SSE (Port 8020)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         HERMES KARMA FRONTEND (SvelteKit)                       │
│  • Sessions & Gateway Channel Browser (Telegram / Slack / Discord / CLI tabs)   │
│  • Visual Trajectory & Step Execution Timeline                                  │
│  • Autonomous Skill Learning & Evolution Diff Viewer                            │
│  • Subagent Trees, Background Shells, Cron Jobs, & MCP Tool Analytics           │
└─────────────────────────────────────────────────────────────────────────────────┘


3. Hermes Storage Adaptation & Schema Mapping
Claude Code relies on raw JSONL files per project. Hermes Agent stores conversation history and metadata directly in SQLite (~/.hermes/state.db) along with ShareGPT trajectory exports.
3.1 Data Ingestion Engine
Hermes Karma connects to ~/.hermes/state.db in Read-Only / WAL mode to eliminate locks:
# backend/services/hermes_db.py
from sqlalchemy.ext.asyncio import create_async_engine
import os

HERMES_DB_PATH = os.path.expanduser("~/.hermes/state.db")
DATABASE_URL = f"sqlite+aiosqlite:///{HERMES_DB_PATH}?uri=true&mode=ro"

engine = create_async_engine(DATABASE_URL, connect_args={"check_same_thread": False})


3.2 Schema Extraction Mappings
Session Index: Extracted from sessions table (fields: session_id, title, source_platform, model, started_at, ended_at, parent_session_id).
Message & Tool Call Stream: Extracted from messages joined with tool_calls and tool_results.
Token & Cost Ledger: Hermes calculates turn-level input, output, and cached tokens per model provider (Nous Portal, OpenRouter, Anthropic, OpenAI, Local Ollama/vLLM).
Skills Ledger: Scanned from ~/.hermes/skills/*/SKILL.md (YAML frontmatter + markdown execution logic).
Trajectories: Parsed from ~/.hermes/trajectory_samples.jsonl and failed_trajectories.jsonl.
4. Live Session Tracking & Hook Implementation
To match Karma’s live state monitoring (Live, Waiting, Stale, Stopped, Ended) and "Focus Terminal" button:
4.1 Hermes Lifecycle Hook / Plugin
Hermes supports plugin hooks and RPC events. Hermes Karma installs a lightweight observer plugin:
# ~/.hermes/plugins/hermes_karma_hook.py
"""
Hermes Karma Lifecycle Hook
Dispatches live session status and active terminal window IDs to Hermes Karma.
"""
import os
import json
import time
from pathlib import Path

KARMA_LIVE_DIR = Path.home() / ".hermes_karma" / "live_sessions"
KARMA_LIVE_DIR.mkdir(parents=True, exist_ok=True)

class HermesKarmaHook:
    def on_session_start(self, session_context):
        payload = {
            "session_id": session_context.session_id,
            "title": session_context.title or "Untitled Session",
            "platform": session_context.platform,  # 'cli', 'discord', 'telegram', etc.
            "model": session_context.model,
            "status": "LIVE",
            "started_at": time.time(),
            "last_active": time.time(),
            "pid": os.getpid(),
            "tty": os.ttyname(0) if os.isatty(0) else None,
            "tmux_pane": os.getenv("TMUX_PANE"),
            "working_directory": str(Path.cwd())
        }
        self._write_state(session_context.session_id, payload)

    def on_tool_start(self, session_context, tool_name, tool_args):
        self._update_status(session_context.session_id, "RUNNING_TOOL", current_tool=tool_name)

    def on_turn_complete(self, session_context):
        self._update_status(session_context.session_id, "WAITING")

    def on_session_end(self, session_context):
        self._update_status(session_context.session_id, "ENDED")

    def _write_state(self, session_id, payload):
        target = KARMA_LIVE_DIR / f"{session_id}.json"
        with open(target, "w") as f:
            json.dump(payload, f, indent=2)

    def _update_status(self, session_id, status, **kwargs):
        target = KARMA_LIVE_DIR / f"{session_id}.json"
        if target.exists():
            with open(target, "r") as f:
                data = json.load(f)
            data["status"] = status
            data["last_active"] = time.time()
            data.update(kwargs)
            with open(target, "w") as f:
                json.dump(data, f, indent=2)


5. Core Feature Specifications
5.1 Multi-Platform Session Explorer
Unlike Claude Code (which only tracks CLI/Desktop), Hermes Karma surfaces all conversation streams:
Gateway Source Filter: Filter sessions by source channel (CLI, Telegram, Discord, Slack, Matrix, API Batch).
Lineage & Compressions: Visual representation of parent/child relationships created when Hermes executes context compression (/compress).
5.2 Interactive Execution Timeline
Visualizes thought blocks (<thinking>), code executions, subagent dispatches, and tool outputs chronologically.
Filter timeline events by Tool Name, Status (success vs. error), and Execution Backend (Local, Docker, Modal, Daytona, SSH).
5.3 Autonomous Skill Evolution & Memory Studio
Hermes autonomous skill creation and self-refinement are central features:
Skill Catalog & Version Diffing: Displays all skills generated after complex workflows (≥5 tool calls) and allows users to view Git-style diffs showing how a skill adapted across sessions.
Persistent Memory Explorer: Inspect and search MEMORY.md and USER.md timeline evolutions.
5.4 Token, Cost & Model Provider Analytics
Aggregates usage across local providers (Ollama / vLLM / llama.cpp @ $0.00) vs. commercial providers (Nous Portal, OpenRouter, Anthropic, OpenAI).
Visual breakdown of cached prompt savings (KV-cache hits) and trajectory generation velocity.
5.5 Ticket Linking (GitHub, Linear, Jira)
Read-only cross-referencing between ticket keys (e.g., ENG-402, #104) and Hermes session IDs stored in ~/.hermes_karma/metadata.db.
Automatic detection from git branch naming (feat/ENG-402-hermes-adapter).
5.6 Multi-Node & AMD APU Fleet Telemetry Collector
Tailscale mesh observability across heterogeneous compute nodes:
• Beehive Coordinator: Local gateway host metrics (CPU load, RAM, disk, SQLite storage).
• Chunkito AMD APU Worker (100.71.183.123): AMD Ryzen AI Max+ 395 Strix Halo APU telemetry with 118GB Unified VRAM (amdgpu.gttsize=120832), Ollama resident model tracking, VRAM allocation gauges, and OOM safety guard monitoring (OLLAMA_MAX_LOADED_MODELS=1 enforcement).
• Live Telemetry Stream: Real-time SSE updates broadcast to the dashboard without polling overhead.

6. API Endpoint Specification (FastAPI / Port 8020)
Sessions & Replays
GET /api/sessions: Query and filter sessions (pagination, platform, date range, model).
GET /api/sessions/{session_id}: Full transcript, tool calls, and execution metadata.
GET /api/sessions/{session_id}/timeline: Structured event stream for step-by-step UI replay.
GET /api/sessions/{session_id}/subagents: Subagent dispatch hierarchy tree.
Skills & Memory
GET /api/skills: All active and auto-generated skills with invocation metrics.
GET /api/skills/{skill_name}/history: Evolution changelog and version diffs.
GET /api/memory: Current snapshot and update history for MEMORY.md and USER.md.
Live Tracking & Terminal Control
GET /api/live-sessions: Real-time session statuses streamed over SSE.
POST /api/live-sessions/{session_id}/focus-terminal: Uses OS window manager commands (macOS AppleScript, Linux wmctrl/xdotool, or tmux select-pane) to bring the active terminal window to the front.
Fleet & Node Telemetry
GET /api/nodes: Real-time fleet overview, node health, APU VRAM utilization, and model residency.
GET /api/nodes/{node_id}: Detailed hardware, memory, and Ollama metrics for a specific node.
POST /api/nodes/refresh: On-demand live refresh across all fleet nodes.
POST /api/nodes/{node_id}/refresh: On-demand live refresh for a single node.
GET /api/nodes/{node_id}/models: Active resident models in VRAM and cached disk catalog.
Tickets & Analytics
GET /api/analytics/overview: High-level tokens, costs, cache rates, and tool distribution.
POST /api/sessions/{session_id}/tickets: Link external ticket identifiers.
GET /api/cron: Inspect Hermes scheduled cron jobs and unattended execution histories.
7. Setup & Run Instructions
# 1. Clone the repository
git clone https://github.com/your-org/hermes-karma.git
cd hermes-karma

# 2. Install Backend & Hook
cd api
pip install -r requirements.txt
python scripts/install_hermes_plugin.py  # Symlinks hook to ~/.hermes/plugins/
uvicorn main:app --reload --port 8020

# 3. Launch Frontend (in another terminal)
cd ../frontend
npm install
npm run dev -- --port 5180


