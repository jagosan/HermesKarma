# Hermes Karma (hermes-karma) ☸️

**Deep local-first observability, telemetry, trajectory inspection, and session analytics dashboard for [Hermes Agent](https://hermes-agent.nousresearch.com).**

Inspired by and built as the Hermes Agent companion to [Claude Code Karma](https://github.com/JayantDevkar/claude-code-karma) by Jayant Devkar.

---

## 🌟 Overview

When operating heterogeneous agent fleets across local models (Ollama, vLLM, llama.cpp on nodes like *chunkito*) and cloud models (OpenRouter, Nous Portal, Anthropic, OpenAI, Gemini), keeping track of token usage, reasoning traces, execution trajectories, subagent dispatches, and memory evolution is critical.

**Hermes Karma** provides real-time telemetry, historical session analytics, skill evolution diffing, and terminal focus IPC tailored specifically for Hermes Agent's architecture (`~/.hermes/state.db` SQLite + WAL, `~/.hermes/skills/`, `MEMORY.md`, and `USER.md`).

---

## 🚀 Key Features

### 1. Multi-Platform Session Explorer
- Inspect conversation streams across all Hermes gateway channels: **CLI, WebUI, Telegram, Discord, Slack, Matrix, and API Batches**.
- Filter by model, status, source platform, and date ranges.
- Full context compression lineage viewer showing parent/child session handoffs and token savings.

### 2. Interactive Execution Timeline & Step Replay
- Visual step-by-step chronology of turns, `<thinking>` / reasoning traces, tool invocations, and tool results.
- Color-coded status markers (Success, Error, System Alert).
- Detailed tool inspection with formatted parameter and output viewers.

### 3. Token, Cost & Model Provider Analytics
- Granular breakdown of Prompt, Completion, Cache Read, Cache Write, and Reasoning tokens.
- Cost attribution separating zero-cost local inference ($0.00 on Ollama/vLLM) from commercial cloud APIs.
- KV cache hit rate analytics and trajectory velocity metrics.

### 4. Autonomous Skill Evolution & Memory Studio
- Live catalog of autonomous skills created and refined by Hermes Agent.
- Git-style version diffing to visualize how execution playbooks and self-evolution patches mutate across sessions.
- Inspector for persistent `MEMORY.md` and `USER.md` profile states.

### 5. Live Telemetry & Terminal Focus IPC
- Real-time session state streaming via Server-Sent Events (SSE) (Live, Running Tool, Waiting, Stale, Ended).
- **"Focus Terminal"** action: Automatically brings the active terminal window or tmux pane to focus via OS-level window management (`xdotool`, `wmctrl`, `tmux select-pane`, or AppleScript).

### 6. Subagents, Delegations & Cron Monitoring
- Dispatch tree visualization for background subagent tasks (`delegate_task`).
- Scheduled cron job status and execution history tracking.
- Ticket cross-referencing (Linear, GitHub, Jira) linked with session IDs in metadata.

---

## 🏗️ Architecture

```
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
│  └───────────┼──────────────────────────┼──────────────────────────────────────────┘
│              │                          │
│              ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          HERMES KARMA BACKEND (FastAPI)                         │
│  • Reads ~/.hermes/state.db (Read-Only WAL mode)                                │
│  • Reads ~/.hermes/config.yaml, MEMORY.md, USER.md, & skills directory          │
│  • Writes metadata to ~/.hermes_karma/metadata.db (Ticket Links, Tags, Notes)   │
│  • Terminal Focus IPC: xdotool / wmctrl / tmux socket / AppleScript             │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │ REST / SSE (Port 8020)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         HERMES KARMA FRONTEND (Web Dashboard)                   │
│  • Sessions & Gateway Channel Browser (Telegram / Slack / Discord / CLI tabs)   │
│  • Visual Trajectory & Step Execution Timeline                                  │
│  • Autonomous Skill Learning & Evolution Diff Viewer                            │
│  • Subagent Trees, Background Shells, Cron Jobs, & Analytics                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Quick Start

### Prerequisites
- Python 3.10+
- Modern Web Browser (or Node.js 18+ for building frontend assets)
- Hermes Agent installed and active

### 1. Installation
```bash
git clone https://github.com/jagosan/HermesKarma.git
cd HermesKarma

# Create virtual environment & install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt
```

### 2. Install Hermes Karma Plugin Hook
```bash
python3 api/scripts/install_hermes_plugin.py
```

### 3. Launch Dashboard
```bash
python3 -m api.main
```
Open **`http://localhost:8020`** in your browser.

---

## 🙏 Credits & Acknowledgments

- **[Claude Code Karma](https://github.com/JayantDevkar/claude-code-karma)** by [Jayant Devkar](https://github.com/JayantDevkar) — the original inspiration and pioneering design for agent session telemetry and trajectory analytics.
- **[Hermes Agent](https://hermes-agent.nousresearch.com)** by [Nous Research](https://nousresearch.com) — the open, autonomous agent framework powering this telemetry engine.

---

## 📄 License
MIT License. See `LICENSE` for details.
