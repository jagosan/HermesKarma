# SPEC-HK-006: Default 30-Day Windows, Historical GPU Load Telemetry & AI Studio Temporal Usage Chart

## 1. Executive Summary
This specification defines the architecture, data contracts, and implementation plan for three major capabilities in **HermesKarma**:

1. **Default 30-Day Window Across All Views**:
   - Establish `Past 30 Days` (`30d`) as the unified default temporal scope across all views (Tokens & Cost Analytics, Sessions Browser, Fleet & APU Telemetry, and Pantheon Swarm).
   - Ensure UI filter buttons, API query defaults, and underlying SQL queries initialize with 30-day temporal boundaries (`now - 30 * 86400`) rather than unbound all-time scans.

2. **Historical GPU Load & Telemetry Graph in Fleet & APU View**:
   - Introduce persistent timeseries telemetry storage (`node_gpu_samples`) in `~/.hermes_karma/metadata.db`.
   - Record GPU core busy percentage, GTT VRAM allocation, power draw (W), temperature (°C), and active inference slots on each collector polling cycle.
   - Provide `/api/nodes/history` with temporal aggregation (hourly for 24h, daily for 7d/30d) and automated initial backfill from local session activity.
   - Embed a responsive multi-node historical line/area chart (`#fleetGpuHistoryChart`) in `#view-nodes` with time-range controls (`24h`, `7d`, `30d` default).

3. **AI Studio Temporal Usage Bar Chart in Tokens & Cost View**:
   - Deliver an interactive temporal usage analyzer matching Google AI Studio's usage dashboard:
     - **30-day / this-month view**: One stacked bar per day.
     - **24-hour view**: One stacked bar per hour.
     - **Breakdown by model**: Each bar is segmented by model with coordinated palette colors.
     - **Metric switcher**: Seamlessly toggle between **Input Tokens**, **Output Tokens**, and **Accrued Cost ($)** (with optional Total Tokens).
   - Backed by high-precision bucket aggregation in `hermes_reader.get_analytics_overview` extracting from `session_model_usage` WAL tables and dynamically reconciled via `PricingEngine`.

---

## 2. Technical Architecture & Data Contracts

### 2.1 Default 30-Day Time Window Architecture
- **Analytics API** (`/api/analytics/overview`):
  - Default query param: `time_range: str = "30d"`.
  - Frontend: `let currentAnalyticsRange = '30d';`
  - Active button in `index.html`: `data-range="30d"` styled with `bg-brand-500/20 text-brand-300 border border-brand-500/30`.
- **Sessions API** (`/api/sessions`):
  - Default: `date_from = now - (30 * 86400)`.
  - Add quick time range buttons (`24h`, `7d`, `30d` [Default], `All`) to `#view-sessions`.
  - Frontend `currentSessionsTimeRange = '30d'` passed via `date_from`.
- **Pantheon Swarm API** (`/api/pantheon/profiles`):
  - Support `time_range: str = "30d"` parameter to aggregate sessions and token spend over the last 30 days.

### 2.2 Persistent GPU Telemetry Schema (`metadata.db`)
In `~/.hermes_karma/metadata.db`:
```sql
CREATE TABLE IF NOT EXISTS node_gpu_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    gpu_busy_percent REAL NOT NULL DEFAULT 0.0,
    gtt_used_gb REAL NOT NULL DEFAULT 0.0,
    gtt_total_gb REAL NOT NULL DEFAULT 0.0,
    power_w REAL NOT NULL DEFAULT 0.0,
    temperature_c REAL NOT NULL DEFAULT 0.0,
    active_slots INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_node_gpu_samples_node_ts ON node_gpu_samples(node_id, timestamp);
```

#### API Endpoint: `GET /api/nodes/history`
- Query parameters:
  - `time_range`: `"24h" | "7d" | "30d"` (default: `"30d"`)
  - `node_id`: Optional node filter (e.g. `chunkito`, `beehive`, or all)
- Response Schema:
```json
{
  "time_range": "30d",
  "bucket_interval_sec": 86400,
  "nodes": {
    "chunkito": [
      {
        "timestamp": 1790400000.0,
        "datetime": "2026-09-25 12:00",
        "gpu_busy_percent": 34.5,
        "gtt_used_gb": 87.2,
        "power_w": 54.0,
        "temperature_c": 46.0,
        "active_slots": 2
      }
    ],
    "beehive": [
      {
        "timestamp": 1790400000.0,
        "datetime": "2026-09-25 12:00",
        "gpu_busy_percent": 4.2,
        "gtt_used_gb": 12.0,
        "power_w": 18.0,
        "temperature_c": 38.0,
        "active_slots": 0
      }
    ]
  }
}
```

### 2.3 AI Studio Temporal Usage Data Contract
Enrich `/api/analytics/overview` with `temporal_usage`:
```json
{
  "temporal_usage": {
    "bucket_type": "day", // "hour" for 24h/today, "day" for 7d/month/30d
    "time_range": "30d",
    "buckets": ["2026-08-28", "2026-08-29", "..."],
    "labels": ["Aug 28", "Aug 29", "..."],
    "models": ["gemini-3.8-flash", "gemini-3.7-flash", "qwen3.8-flash-next:262k"],
    "series": {
      "input_tokens": {
        "gemini-3.8-flash": [120000, 340000, 0],
        "qwen3.8-flash-next:262k": [48000, 0, 0]
      },
      "output_tokens": {
        "gemini-3.8-flash": [8000, 22000, 0],
        "qwen3.8-flash-next:262k": [10500, 0, 0]
      },
      "cost_usd": {
        "gemini-3.8-flash": [0.18, 0.45, 0.0],
        "qwen3.8-flash-next:262k": [0.0, 0.0, 0.0]
      },
      "total_tokens": {
        "gemini-3.8-flash": [128000, 362000, 0],
        "qwen3.8-flash-next:262k": [58500, 0, 0]
      }
    }
  }
}
```

#### Bucket Computation Rules:
1. **24h / Today**:
   - `cutoff_ts = now - 86400`
   - Buckets: 24 hourly buckets formatted as `strftime('%Y-%m-%d %H:00', datetime(last_seen, 'unixepoch', 'localtime'))`.
   - Labels: `HH:00` (e.g. `14:00`, `15:00`).
2. **30d / Month / 7d**:
   - `cutoff_ts = now - (30 * 86400)` (or start of month for `month`)
   - Buckets: Daily buckets formatted as `strftime('%Y-%m-%d', datetime(last_seen, 'unixepoch', 'localtime'))`.
   - Labels: `MMM DD` (e.g. `Aug 28`, `Sep 15`).
3. **Model Grouping**:
   - Summed per bucket per model.
   - Cost computed per model bucket using `pricing_engine` reconciliation.
   - Continuous zero-filled timeline ensures no missing days or hours.

---

## 3. Implementation Plan & Swarm Assignment
- **Phase 1: Backend Architecture & Telemetry Engine** (`@tigger`):
  - Update `MetadataService` in `api/services/metadata_service.py` to create `node_gpu_samples` table, record methods, retrieval methods, and auto-backfill logic.
  - Wire `NodeTelemetryCollector` (`api/services/node_collector.py`) to log samples on every poll.
  - Add `/api/nodes/history` in `api/routes/nodes.py`.
  - Update `hermes_reader.get_analytics_overview` in `api/services/hermes_reader.py` to compute `temporal_usage` with hourly (24h) and daily (30d) buckets by model for input tokens, output tokens, and cost. Set default `time_range="30d"`.
  - Update `/api/sessions` and `hermes_reader.get_sessions` default time window to 30 days.
- **Phase 2: Frontend Implementation** (`@tigger`):
  - In `api/static/index.html`:
    - Set `30d` as active default for analytics time range buttons.
    - Add quick time-range buttons (`24h`, `7d`, `30d` [Default], `All`) to `#view-sessions`.
    - Add `#fleetGpuHistorySection` with canvas `#fleetGpuHistoryChart` and range selectors to `#view-nodes`.
    - Add AI Studio style `#temporalUsageSection` with metric toggles (`Input Tokens`, `Output Tokens`, `Cost ($)`, `Total Tokens`) and canvas `#temporalUsageChart` to `#view-analytics`.
  - In `api/static/app.js`:
    - Default `currentAnalyticsRange = '30d'`.
    - Implement `loadFleetGpuHistory(range)` and render `#fleetGpuHistoryChart`.
    - Implement `renderTemporalUsageChart(data, metric)` with metric switcher button handlers.
    - Wire `currentSessionsTimeRange = '30d'` into `loadSessions()`.
- **Phase 3: Verification & Automated Tests** (`@piglet`):
  - Update and expand `tests/test_karma.py` with test cases for 30d default, `/api/nodes/history`, and `temporal_usage` schema.
  - Execute `python3 -m unittest discover tests` to ensure 100% pass rate.
- **Phase 4: Adversarial Audit & Edge Cases** (`@eeyore`):
  - Audit zero-token days, empty state DBs, timezone daylight-savings transitions, and SQLite lock handling.
- **Phase 5: Knowledge Curation & Git Push** (`@pooh`):
  - Update `docs/MAP.md` and commit to Git remote repository.
