# Specification 07: Context Tax, Spatial Navigation ROI (`MAP.md` & `graphify`), and Harness Telemetry

## 1. Objective & Scope
Expand `HermesKarma` beyond raw token volume and counterfactual cloud pricing into **Context Efficiency and Harness Optimization**. This specification introduces telemetry to measure:
1. **The 4-Tier Context Tax:** Breaking down input prompt tokens into Base Harness Overhead, Spatial Index (`docs/MAP.md` / `graphify`), Exploration Thrash, and Productive Payload.
2. **Spatial Navigation ROI (`MAP.md` & `graphify`):** Quantifying whether loading `MAP.md` or querying `graphify` reduces blind file exploration, lowers turns-to-first-edit, and saves net tokens.
3. **Context Compaction & "Amnesia Tax" Telemetry:** Tracking Hermes context compaction events, compression ratios, and post-compaction file re-reads.
4. **Skill ROI & Ingress Attribution:** Identifying dormant/high-overhead skills in `routes/skills.py` and attributing token burn across Hermes ingress gateways (`cli`, `webui`, `telegram`, `cron`, `pantheon`).
5. **Incremental Reader Caching & Loop Guardrails:** Keeping `hermes_reader.py` fast at scale and alerting via `webhook_service.py` on runaway autonomous loops.

---

## 2. Target Architecture & Affected Modules

* **Read / Parse Layer:** `api/services/hermes_reader.py`, `api/plugins/hermes_karma_hook.py`
* **Valuation & Counterfactual Layer:** `api/services/pricing_engine.py`
* **API Routes:** `api/routes/analytics.py`, `api/routes/skills.py`, `api/routes/sessions.py`, `api/routes/pantheon.py`
* **Alerting Layer:** `api/services/live_tracker.py`, `api/services/webhook_service.py`
* **Frontend UI:** `api/static/index.html`, `api/static/app.js`
* **Documentation:** `docs/MAP.md`, `SPEC.md`

---

## 3. Feature Specifications

### 3.1 The 4-Tier "Context Tax" Anatomy
Every Hermes session turn's input token footprint (`prompt_tokens`) must be classified into four mutually exclusive buckets:

1. **Tier 1: Base Harness Tax (`cold_start_tokens`)**
   * **Definition:** Tokens consumed prior to task execution by the system prompt, `MEMORY.md`, `USER.md`, active `SKILL.md` router descriptions, and injected tool/MCP JSON schemas.
   * **Estimation Rule:** Derived from Turn 1 baseline input tokens minus user prompt length, or explicitly emitted by `hermes_karma_hook.py`.
2. **Tier 2: Spatial Index Tax (`spatial_index_tokens`)**
   * **Definition:** Tokens explicitly spent reading `docs/MAP.md`, `00_AI_CONTEXT_OVERVIEW.md`, or executing `graphify` topology/subgraph queries.
3. **Tier 3: Exploration Burn / File Thrash (`exploration_waste_tokens`)**
   * **Definition:** Tokens returned by read/search tools (`read_file`, `view_file`, `grep`, `search_files`, `find`, directory listings) for files that are **never modified** (`write_file`, `edit_file`, `patch`) during the session and are not the sole target of a read-only Q&A session.
   * **Exclusion:** In pure Q&A / research sessions (zero file edits across the entire session), classify only redundant re-reads or failed grep/glob sweeps as Exploration Burn.
4. **Tier 4: Productive Working Payload (`productive_tokens`)**
   * **Definition:** User prompts, files read that were subsequently modified, targeted test outputs, and retained conversational state.

---

### 3.2 `MAP.md` & `graphify` Spatial ROI Engine
Measure the effectiveness of local architectural indexing (`docs/MAP.md` and `graphify`) at preventing blind repository traversal.

#### A. Session Classification
Tag every coding session (sessions with $\ge 1$ file write/edit/patch tool call) with a boolean `is_spatially_guided`:
* `used_map_md = True` if `MAP.md` (or configured map filename) was injected or read within the first 3 turns of the session.
* `used_graphify = True` if any `graphify` tool, skill, or graph artifact was invoked or read during the session.
* `is_spatially_guided = used_map_md or used_graphify`

#### B. Core Navigation Metrics
For every coding session, calculate:
* **Turns-to-First-Edit (`turns_to_first_edit`):** Number of assistant tool-call turns elapsed before the first mutating file operation (`write`, `edit`, `patch`).
* **Read-to-Edit Hit Ratio (`read_to_edit_ratio`):**
  $$\text{Read-to-Edit Ratio} = \frac{\text{Distinct Files Read (excluding MAP.md)}}{\max(\text{Distinct Files Edited}, 1)}$$
  *(Ideal ratio approaches $1.0\text{–}2.0$; high ratios $> 5.0$ indicate repository thrashing).*
* **Counterfactual Exploration Savings (`net_spatial_token_savings`):**
  Compare the rolling 30-day average `exploration_waste_tokens` of **Unmapped Coding Sessions** ($\bar{E}_{\text{unmapped}}$) against **Spatially Guided Sessions** ($\bar{E}_{\text{mapped}}$), subtracting the upfront cost of `spatial_index_tokens` ($S$):
  $$\text{Net Spatial Savings (Tokens)} = (\bar{E}_{\text{unmapped}} - E_{\text{session}}) - S_{\text{session}}$$
  Feed this delta into `pricing_engine.py` to compute **Counterfactual Dollar Savings from `MAP.md` / `graphify`**.

#### C. "Top Thrash Files" & Map Drift Detector
1. **Top Thrash Leaderboard:** Aggregate the top 10 file paths across all sessions that accumulate the highest `exploration_waste_tokens` (frequently read during coding sessions, rarely/never edited). Surface these as **"Candidates to Document in `MAP.md`"**.
2. **Map Staleness / Drift Warning:** Compare the last modified timestamp (`mtime` or `git log -1 --format=%ct`) of `docs/MAP.md` and the `graphify` graph artifact against the repository's latest commits. Flag a `map_drift_warning` in the API response if:
   * More than $N$ commits (default `15`) or নতুন route/service files have been added since `docs/MAP.md` was last updated.

---

### 3.3 Context Compaction & "Amnesia Tax" Telemetry
Track how Hermes handles long-context pressure when sessions hit compaction thresholds.

* **Compaction Event Capture:** Record every compaction boundary in a session with:
  * `pre_compaction_tokens`: Context size immediately prior to compaction.
  * `post_compaction_tokens`: Context size immediately following compaction.
  * `tokens_reclaimed`: $\text{pre} - \text{post}$ and `compression_ratio`: $1 - (\text{post} / \text{pre})$.
* **Post-Compaction "Amnesia Tax" (`amnesia_reread_tokens`):**
  * Maintain a set of file paths read prior to the compaction event (`pre_compaction_reads`).
  * Any `read_file` call within $K$ turns (default $K=5$) *after* compaction targeting a file in `pre_compaction_reads` (without an intervening edit to that file) is flagged as an **Amnesia Re-Read**.
  * Sum the tokens of these redundant reads into `amnesia_reread_tokens` and check whether `docs/MAP.md` was dropped during compaction.

---

### 3.4 Skill ROI & "Dead Skill" Auditor (`api/routes/skills.py`)
Extend the existing skills endpoint to audit the token footprint vs. utility of installed Hermes skills:

* **Skill Static Footprint (`skill_token_weight`):** Estimate the token length of each installed `SKILL.md` file (both its brief router description and full expanded body).
* **Dormant / Dead Skills (30d):** Flag skills with `0` invocations over the selected time window (default 30 days) that still consume router description tokens in the base prompt.
* **Skill Error Correlation:** Calculate the tool-call failure rate within turns where a given skill was invoked (`failed_tool_calls / total_tool_calls_in_skill_session`).

---

### 3.5 Ingress Gateway Attribution & Local Inference Speed
1. **Ingress Source Tagging (`ingress_channel`):**
   Every session parsed by `hermes_reader.py` must be tagged with its origin gateway:
   * `cli` | `webui` | `telegram` | `cron` | `pantheon_subagent`
2. **Local Inference Performance Correlation (`nodes.py` + `sessions.py`):**
   When a session turn runs against a local model node, capture and expose:
   * `ttft_ms` (Time to First Token in milliseconds, if available in log/hook metadata).
   * `tokens_per_sec` (Output generation speed).
   * Pair counterfactual cloud dollar savings with average local generation speed (`tok/s`).

---

### 3.6 Incremental Reader Cache & Runaway Loop Circuit Breaker
1. **File `mtime` Cache in `hermes_reader.py`:**
   * Cache parsed session summaries and context audits in memory (or lightweight SQLite table) keyed by `(filepath, st_mtime_ns, file_size)`.
   * Only re-parse session logs whose `st_mtime_ns` or `file_size` has changed since the last scan.
2. **Runaway Loop & Anomaly Alerts (`live_tracker.py` + `webhook_service.py`):**
   * Trigger a webhook alert if an active `cron` or `pantheon` session exceeds configurable thresholds:
     * `MAX_CONSECUTIVE_TOOL_ERRORS` (default: `5`)
     * `MAX_SESSION_EXPLORATION_THRASH_RATIO` (default: `> 80%` waste after `10+` turns)
     * `MAX_SINGLE_SESSION_CLOUD_COST_USD` (default: `$5.00`)

---

## 4. API Contract Additions

### `GET /api/analytics/context-tax?days=30`
Returns aggregated Context Tax, Spatial ROI, and Compaction metrics:
```json
{
  "window_days": 30,
  "token_breakdown": {
    "cold_start_tokens": 1420000,
    "spatial_index_tokens": 310000,
    "exploration_waste_tokens": 2890000,
    "productive_tokens": 9450000,
    "amnesia_reread_tokens": 410000
  },
  "spatial_roi": {
    "mapped_sessions_count": 48,
    "unmapped_sessions_count": 19,
    "avg_turns_to_first_edit_mapped": 2.3,
    "avg_turns_to_first_edit_unmapped": 6.4,
    "avg_read_to_edit_ratio_mapped": 1.65,
    "avg_read_to_edit_ratio_unmapped": 4.90,
    "net_exploration_tokens_saved": 1640000,
    "counterfactual_spatial_savings_usd": 8.20,
    "map_drift": {
      "is_stale": false,
      "map_last_updated_iso": "2026-10-20T18:00:00Z",
      "commits_since_map_update": 4,
      "unindexed_files": []
    }
  },
  "compaction_stats": {
    "total_compactions": 27,
    "avg_compression_ratio": 0.84,
    "total_tokens_reclaimed": 1890000,
    "total_amnesia_reread_tokens": 410000
  },
  "top_thrash_files": [
    {
      "path": "api/services/hermes_reader.py",
      "unedited_read_count": 14,
      "wasted_tokens": 112000,
      "in_map_md": true
    }
  ],
  "ingress_breakdown": {
    "cli": { "sessions": 35, "total_tokens": 6200000 },
    "telegram": { "sessions": 18, "total_tokens": 2100000 },
    "cron": { "sessions": 12, "total_tokens": 3400000 },
    "pantheon_subagent": { "sessions": 29, "total_tokens": 2370000 }
  }
}
```

### `GET /api/sessions/{session_id}` (Extended Fields)
Append a `context_audit` object to individual session detail payloads:
```json
"context_audit": {
  "ingress_channel": "cli",
  "used_map_md": true,
  "used_graphify": true,
  "turns_to_first_edit": 2,
  "read_to_edit_ratio": 1.5,
  "cold_start_tokens": 3800,
  "spatial_index_tokens": 1450,
  "exploration_waste_tokens": 2100,
  "productive_tokens": 18400,
  "compaction_events": 1,
  "amnesia_reread_tokens": 0,
  "untouched_files_read": ["api/config.py"]
}
```

---

## 5. Frontend UI Requirements (`index.html` & `app.js`)

Add a **"Context Tax & Spatial ROI"** section inside the Analytics view (and badges on Session cards):

1. **4-Tier Context Composition Bar:**
   A horizontal stacked progress bar showing the percentage split of input tokens across **Base Harness**, **Spatial Index (`MAP.md`/`graphify`)**, **Exploration Thrash**, and **Productive Context**.
2. **`MAP.md` + `graphify` A/B Scorecard Card:**
   * Displays side-by-side comparison of **Mapped vs. Unmapped** sessions (*Avg Turns to First Edit*, *Read-to-Edit Hit Ratio*, and *Net Tokens / USD Saved*).
   * Displays a green **"Map Synced"** or amber **"Map Drift Detected ($N$ commits behind)"** pill badge in the header.
3. **Compaction & Amnesia Indicator:**
   Shows total tokens reclaimed via compaction alongside the **Amnesia Tax %** (percentage of compacted sessions that immediately re-read previously loaded files).
4. **Top Thrash Files Table:**
   A compact 5-row table showing files most frequently read without edits, helping the user see which modules need clearer architectural summaries in `docs/MAP.md`.
5. **Session List Badges:**
   Add compact pills on session rows for:
   * Ingress source (`CLI`, `TG`, `CRON`, `SWARM`)
   * `MAP` / `GRAPH` badge when spatial indexing was used
   * `THRASH` warning badge if `read_to_edit_ratio > 5.0`

---

## 6. Acceptance Criteria & Verification (`tests/test_karma.py`)

1. **Unit Test — Context Bucket Classification:** Given a mock Hermes session with system prompt, a `read_file("docs/MAP.md")` call, 3 unedited file reads, and 1 edited file read, `hermes_reader` accurately partitions tokens into `cold_start_tokens`, `spatial_index_tokens`, `exploration_waste_tokens`, and `productive_tokens`.
2. **Unit Test — Pure Q&A Exemption:** A read-only session with `0` edits does not classify primary target file reads as `exploration_waste_tokens`.
3. **Unit Test — Compaction Amnesia Detection:** A simulated compaction event followed by a `read_file` of an already-read file within 5 turns increments `amnesia_reread_tokens`.
4. **Unit Test — Reader Cache Hit:** Calling the analytics endpoint twice without modifying underlying session files results in a 100% cache hit in `hermes_reader.py`.
5. **UI Render Check:** The Analytics tab renders the 4-Tier Context Composition bar, Spatial ROI scorecard, and Top Thrash Files table cleanly on both desktop and mobile viewports.