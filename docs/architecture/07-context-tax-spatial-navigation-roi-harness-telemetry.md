# Architectural Blueprint: Context Tax, Spatial Navigation ROI (`MAP.md` & `graphify`), and Harness Telemetry (SPEC-HK-007)

## 1. System Topology & Data Flow

```mermaid
graph TD
    subgraph Ingestion Layer
        S_STATE[~/.hermes/state.db] --> HR[HermesReader Engine]
        S_PROF[~/.hermes/profiles/*/state.db] --> HR
        HOOK[hermes_karma_hook.py] -->|Live Token & Ingress Metadata| META_DB[(metadata.db)]
    end

    subgraph Telemetry & Classification Engine (hermes_reader.py)
        HR --> CA[Context Tax Classifier: 4 Tiers]
        CA --> T1[Tier 1: Cold Start / Harness]
        CA --> T2[Tier 2: Spatial Index MAP.md / graphify]
        CA --> T3[Tier 3: Exploration Waste / Unedited Reads]
        CA --> T4[Tier 4: Productive Payload]
        HR --> ROI[Spatial ROI Engine: Mapped vs Unmapped]
        HR --> CMP[Compaction & Amnesia Re-Read Detector]
        HR --> CACHE[(Incremental Reader mtime Cache)]
    end

    subgraph Valuation & API Layer
        T3 & ROI --> PE[pricing_engine.py: Counterfactual Dollar Savings]
        HR & PE --> RT_CTX[/api/analytics/context-tax]
        HR --> RT_SES[/api/sessions/{id} with context_audit]
        HR --> RT_SKL[/api/routes/skills with dead skill audit]
    end

    subgraph Presentation Layer (index.html & app.js)
        RT_CTX --> CT_BAR[4-Tier Context Composition Stacked Bar]
        RT_CTX --> ROI_CARD[Spatial Navigation ROI Scorecard]
        RT_CTX --> THRASH_TBL[Top Thrash Files Leaderboard]
        RT_SES --> SES_BADGES[Session Ingress & Thrash Warning Badges]
    end
```

---

## 2. Interface Contracts & Schemas

### 2.1 Backend Contract: `GET /api/analytics/context-tax`
- **Query Params**:
  - `days`: int (default `30`)
- **Response Schema**:
```typescript
interface ContextTaxResponse {
  window_days: number;
  token_breakdown: {
    cold_start_tokens: number;
    spatial_index_tokens: number;
    exploration_waste_tokens: number;
    productive_tokens: number;
    amnesia_reread_tokens: number;
  };
  spatial_roi: {
    mapped_sessions_count: number;
    unmapped_sessions_count: number;
    avg_turns_to_first_edit_mapped: number;
    avg_turns_to_first_edit_unmapped: number;
    avg_read_to_edit_ratio_mapped: number;
    avg_read_to_edit_ratio_unmapped: number;
    net_exploration_tokens_saved: number;
    counterfactual_spatial_savings_usd: number;
    map_drift: {
      is_stale: boolean;
      map_last_updated_iso: string | null;
      commits_since_map_update: number;
      unindexed_files: string[];
    };
  };
  compaction_stats: {
    total_compactions: number;
    avg_compression_ratio: number;
    total_tokens_reclaimed: number;
    total_amnesia_reread_tokens: number;
  };
  top_thrash_files: Array<{
    path: string;
    unedited_read_count: number;
    wasted_tokens: number;
    in_map_md: boolean;
  }>;
  ingress_breakdown: {
    [channel in "cli" | "webui" | "telegram" | "cron" | "pantheon_subagent"]: {
      sessions: number;
      total_tokens: number;
    };
  };
}
```

### 2.2 Backend Contract: `context_audit` in `GET /api/sessions/{id}`
```typescript
interface SessionContextAudit {
  ingress_channel: "cli" | "webui" | "telegram" | "cron" | "pantheon_subagent";
  used_map_md: boolean;
  used_graphify: boolean;
  turns_to_first_edit: number | null;
  read_to_edit_ratio: number;
  cold_start_tokens: number;
  spatial_index_tokens: number;
  exploration_waste_tokens: number;
  productive_tokens: number;
  compaction_events: number;
  amnesia_reread_tokens: number;
  untouched_files_read: string[];
}
```

---

## 3. Failure Domain Analysis & Guardrails

1. **Pure Q&A / Research False Positives:**
   - In sessions with `0` edits, files read to answer user questions must not be penalized as "exploration thrash". Only repeated re-reads or empty search commands count towards waste.
2. **Incremental Reader Scalability:**
   - Parsing full message traces across dozens of sessions on every request adds CPU latency. The reader must maintain an in-memory or SQLite `mtime` cache indexed by `(session_id, db_mtime, message_count)` and recompute audits only for mutated sessions.
3. **Map Drift Grounding:**
   - If `docs/MAP.md` is absent or the repo has no git commits, the drift detector must degrade gracefully with `is_stale: false, commits_since_map_update: 0` without raising exceptions.

---

## 4. 💡 Note to Future Self: Hosting Portability
All telemetry is derived locally from SQLite WAL queries and filesystem `mtime` headers without external network dependencies or proprietary SaaS bridges. Telemetry extraction is isolated in `api/services/hermes_reader.py`, keeping the FastAPI server runnable on headless Linux APUs, macOS laptops, or edge compute clusters.
