# Architectural Blueprint: Default 30-Day Window, GPU Telemetry History & AI Studio Temporal Usage Chart (SPEC-HK-006)

## 1. System Topology & Data Flow

```mermaid
graph TD
    subgraph Data Sources
        S1[State DBs: session_model_usage] --> HR[HermesReader Engine]
        S2[Sysfs / amdgpu_top / Node Exporter] --> NC[NodeTelemetryCollector]
    end

    subgraph Storage & Persistence
        NC -->|record_gpu_sample| MS[MetadataService / node_gpu_samples]
        MS -->|~/.hermes_karma/metadata.db| SQLITE[(SQLite WAL)]
    end

    subgraph Backend APIs
        HR -->|/api/analytics/overview?time_range=30d| RT_AN[Analytics Router]
        MS -->|/api/nodes/history?time_range=30d| RT_ND[Nodes Router]
        HR -->|/api/sessions?date_from=...| RT_SS[Sessions Router]
    end

    subgraph Frontend Dashboard (static/app.js)
        RT_AN -->|temporal_usage by model| TU[AI Studio Temporal Bar Chart]
        RT_ND -->|GPU load & power timeseries| GH[Fleet GPU History Chart]
        RT_SS -->|30d filtered sessions| SB[Sessions Browser]
    end
```

---

## 2. Interface Contracts & Schemas

### 2.1 Backend Contract: `GET /api/nodes/history`
- **Query Params**:
  - `time_range`: `'24h' | '7d' | '30d'` (default `'30d'`)
  - `node_id`: Optional node filter (default: return all active nodes)
- **Response**:
```typescript
interface NodeGpuHistoryResponse {
  time_range: string;
  bucket_interval_sec: number;
  nodes: {
    [nodeId: string]: Array<{
      timestamp: number;
      datetime: string;
      gpu_busy_percent: number;
      gtt_used_gb: number;
      gtt_total_gb: number;
      power_w: number;
      temperature_c: number;
      active_slots: number;
    }>;
  };
}
```

### 2.2 Backend Contract: `temporal_usage` inside `GET /api/analytics/overview`
```typescript
interface TemporalUsageData {
  bucket_type: "hour" | "day";
  time_range: string;
  buckets: string[]; // e.g. ["2026-08-28", "2026-08-29", ...] or ["2026-09-26 00:00", ...]
  labels: string[];  // e.g. ["Aug 28", "Aug 29", ...] or ["00:00", "01:00", ...]
  models: string[];  // list of active models in window
  series: {
    input_tokens: { [model: string]: number[] };
    output_tokens: { [model: string]: number[] };
    cost_usd: { [model: string]: number[] };
    total_tokens: { [model: string]: number[] };
  };
}
```

---

## 3. Architecture Decision Records (ADRs)

### ADR-HK-006A: Unified 30-Day Window as Default
- **Status**: Approved
- **Context**: Users previously landed on "All Time" views which caused unnecessary query latency over growing SQLite WAL state databases, and distorted short-to-medium term development cycles.
- **Decision**: Set `30d` as the standard default across backend endpoints and frontend view states. Maintain instantaneous switches to `24h` / `7d` / `month` / `all`.
- **Consequences**: Queries execute faster with indexable `started_at >= cutoff` and `last_seen >= cutoff` clauses; KPIs immediately reflect the active development cycle.

### ADR-HK-006B: Persistent Node GPU Timeseries in Metadata DB
- **Status**: Approved
- **Context**: Fleet view only exposed instantaneous point-in-time metrics. Historical insight into APU saturation, background subagent load, and thermal throttling was unavailable.
- **Decision**: Store periodic samples in `node_gpu_samples` within `~/.hermes_karma/metadata.db`. Provide automated baseline backfill from local model usage records so initial graph load is immediately rich and insightful. Auto-prune records older than 60 days.

### ADR-HK-006C: Multi-Metric Stacked Bar Chart for AI Studio Temporal Usage
- **Status**: Approved
- **Context**: Users need parity with Google AI Studio's granular usage graphs to inspect prompt caching efficiency, output token expansion, and cost accrued per model over daily and hourly intervals.
- **Decision**: Render a stacked bar chart using Chart.js with continuous zero-filled time buckets and a client-side metric toggle (`Input Tokens`, `Output Tokens`, `Accrued Cost ($)`, `Total Tokens`). Colors remain strictly synchronized with the existing Model Distribution palette.

---

## 4. 💡 Note to Future Self: Hosting Portability
- The timeseries persistence is isolated to `metadata.db` (SQLite WAL), requiring no external Prometheus TSDB, InfluxDB, or cloud agent.
- In headless edge deployments, `node_gpu_samples` continues to collect silently via the existing systemd daemon.
