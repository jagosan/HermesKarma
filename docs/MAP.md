# Structural Symbol Map (HermesKarma)

- `specs/`:
  - `specs/cost-estimation-engine.md`: SPEC-HK-002: Dynamic cost reconciliation & frontier pricing engine.
  - `specs/counterfactual-cloud-cost-and-savings.md`: SPEC-HK-003: Counterfactual cloud cost & hardware savings engine ("Shadow Cost").
  - `specs/cluster-gauge-node-view.md`: SPEC-HK-001: Multi-node APU telemetry & cluster gauges.
  - `specs/cluster-gauge-refinement-and-mobile-ui.md`: SPEC-HK-001-B: Mobile responsive drawer & gauge layout.

## Backend Services (`api/services/`)
- `hermes_reader.py`: `HermesReader`
  - `get_pantheon_profiles()` -> List[Dict] (Aggregates profiles, subagents, tokens, cost per persona)
  - `get_pantheon_profile_detail(profile_id)` -> Dict (SOUL.md, memory, skills, session history)
  - `get_sessions(limit, offset, source, model, search, persona)` -> Dict
  - `get_session_by_id(session_id)` -> Dict (Session details, subagents, model usages)
  - `get_session_messages(session_id)` -> List[Dict]
  - `get_session_timeline(session_id)` -> List[Dict]
  - `get_analytics_overview(time_range)` -> Dict (Includes dynamic cost reconciliation and monthly cap)
- `pricing_engine.py`: `PricingEngine`
  - `get_model_pricing(model_name, billing_provider)` -> Optional[ModelPricing]
  - `calculate_usage_cost(model, input, output, cache_read, cache_write, reasoning, stored_cost)` -> UsageCostEstimate
  - `is_local_model(model_name, billing_provider)` -> bool
- `live_tracker.py`: `LiveTracker`
  - `get_live_sessions()` -> List[Dict] (Active hooks, live delegations, live subagents)
  - `stream_live_events(interval)` -> AsyncGenerator (SSE emitter for live sessions & fleet)
- `node_collector.py`: `NodeTelemetryCollector` (Sysfs/APU & Prometheus node_exporter telemetry for Chunkito/Beehive)
- `metadata_service.py`: `MetadataService` (Tags, notes, ticket sync SQLite storage)

## REST API Routes (`api/routes/`)
- `pantheon.py`:
  - `GET /api/pantheon/profiles`: List all 9 swarm agents + aggregated metrics
  - `GET /api/pantheon/profiles/{profile_id}`: Full persona detail + historical sessions
- `sessions.py`:
  - `GET /api/sessions`: List sessions (supports `source=subagent`, `persona=<id>`)
  - `GET /api/sessions/{session_id}`: Single session details & subagents
- `live.py`:
  - `GET /api/live-sessions`: Active sessions snapshot
  - `GET /api/live-sessions/stream`: SSE live stream
- `analytics.py`:
  - `GET /api/analytics/overview`: High-precision multi-model token & reconciled cost analytics
- `nodes.py`:
  - `GET /api/nodes`: Multi-node cluster telemetry with Prometheus & APU hardware metrics

## Frontend DOM Elements & Components (`api/static/index.html` & `app.js`)
- `#tab-pantheon`, `#view-pantheon`: Pantheon Swarm multi-agent grid & detail drawer
- `#tab-sessions`, `#view-sessions`: Sessions list, subagent badges, filter dropdowns
- `#tab-live`, `#view-live`: Live running sessions & subagent cards
- `#tab-analytics`, `#view-analytics`: Cost & token distribution charts
- `#tab-nodes`, `#view-nodes`: Fleet APU nodes view
- `renderInstrumentCluster(node)`: SVG automotive instrument cluster component (thickened circular tracks, dropped digital readouts below pivots, dynamic 0-118GB / 0-16GB GTT & 0-100% TDP scales, high-contrast bold fonts)
- `#mobileMenuBtn`, `#mobileNavDrawer`: Responsive mobile navigation drawer and touch tab bar
- `#statTotalCost`, `#statSpendCap`: Reconciled spend card and monthly spend cap indicators
- `#table-model-breakdown`: Multi-model usage table with reconciled badges
