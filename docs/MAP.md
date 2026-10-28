# Structural Symbol Map (HermesKarma)

- `specs/`:
  - `specs/01-cluster-gauge-node-view.md`: SPEC-HK-001: Multi-node APU telemetry & cluster gauges.
  - `specs/02-cost-estimation-engine.md`: SPEC-HK-002: Dynamic cost reconciliation & frontier pricing engine.
  - `specs/03-counterfactual-cloud-cost-and-savings.md`: SPEC-HK-003: Counterfactual cloud cost & hardware savings engine ("Shadow Cost").
  - `specs/04-cluster-gauge-refinement-and-mobile-ui.md`: SPEC-HK-004: Mobile responsive drawer & gauge layout.
  - `specs/05-cluster-gauge-and-token-analytics-refinements.md`: SPEC-HK-005: Gauge telemetry binding fix, 240° clock sweep (-210° to +30°), classic palette, output tokens pie & tok/s HUD.
  - `specs/06-default-30d-gpu-history-and-usage-view.md`: SPEC-HK-006: Default 30-day temporal window, historical GPU load graph in Fleet view, and AI Studio temporal usage & cost stacked bar chart.
  - `specs/07-context-tax-spatial-navigation-roi-harness-telemetry.md`: SPEC-HK-007: Context Tax (4 tiers), Spatial Navigation ROI (MAP.md/graphify), Compaction Amnesia, Dead Skills audit & Ingress attribution.
  - `specs/08-declarative-fleet-config-and-portable-deployment.md`: SPEC-HK-008: Declarative Fleet Configuration (fleet.yaml/JSON), Universal Hardware Drivers (AMD/NVIDIA/Apple), 12-Factor Portability, Bootstrap CLI (karma init) & Cloud-Native K8s Deployment.
  - `specs/09-homelab-3d-cluster-visualizer.md`: SPEC-HK-009: Homelab 3D Spatial Visualizer (Interactive Hardware Topology, Translucent Cyber-Chassis & Real-Time Flow Conduits).

- `docs/architecture/`:
  - `docs/architecture/09-homelab-3d-cluster-visualizer.md`: System topology, Three.js scene graph, spatial coordinates, procedural chassis, dynamic Bezier conduits, and SSE streaming pipeline.

## Backend Services (`api/services/`)
- `hermes_reader.py`: `HermesReader`
  - `get_pantheon_profiles()` -> List[Dict] (Aggregates profiles, subagents, tokens, cost per persona)
  - `get_pantheon_profile_detail(profile_id)` -> Dict (SOUL.md, memory, skills, session history)
  - `get_sessions(limit, offset, source, model, search, persona)` -> Dict
  - `get_session_by_id(session_id)` -> Dict (Session details, subagents, model usages, context_audit)
  - `get_session_messages(session_id)` -> List[Dict]
  - `get_session_timeline(session_id)` -> List[Dict]
  - `get_analytics_overview(time_range)` -> Dict (Includes dynamic cost reconciliation, monthly cap, and AI Studio temporal_usage)
  - `get_context_tax_analytics(days)` -> Dict (4-tier context tax, spatial ROI, compaction stats, top thrash files, ingress breakdown)
- `pricing_engine.py`: `PricingEngine`
  - `get_model_pricing(model_name, billing_provider)` -> Optional[ModelPricing]
  - `calculate_usage_cost(model, input, output, cache_read, cache_write, reasoning, stored_cost)` -> UsageCostEstimate
  - `is_local_model(model_name, billing_provider)` -> bool
- `live_tracker.py`: `LiveTracker`
  - `get_live_sessions()` -> List[Dict] (Active hooks, live delegations, live subagents)
  - `evaluate_loop_guardrails(sessions)` -> List[Dict] (SPEC-HK-007 §3.6.2 runaway circuit breaker: consecutive tool errors, exploration thrash ratio, cloud cost ceiling; alerts via `webhook_service.send_alert` with per-session cooldown)
  - `stream_live_events(interval)` -> AsyncGenerator (SSE emitter for live sessions & fleet)
- `node_collector.py`: `NodeTelemetryCollector` (Sysfs/APU & Prometheus node_exporter telemetry for Chunkito/Beehive, records GPU timeseries)
- `metadata_service.py`: `MetadataService` (Tags, notes, ticket sync SQLite storage, node_gpu_samples timeseries persistence)

## REST API Routes (`api/routes/`)
- `pantheon.py`:
  - `GET /api/pantheon/profiles`: List all 10 swarm agents + aggregated metrics
  - `GET /api/pantheon/profiles/{profile_id}`: Full persona detail + historical sessions
- `sessions.py`:
  - `GET /api/sessions`: List sessions (supports `source=subagent`, `persona=<id>`, default 7d window)
  - `GET /api/sessions/{session_id}`: Single session details & subagents & context_audit
- `live.py`:
  - `GET /api/live-sessions`: Active sessions snapshot
  - `GET /api/live-sessions/stream`: SSE live stream
- `analytics.py`:
  - `GET /api/analytics/overview`: High-precision multi-model token & reconciled cost analytics + temporal_usage
  - `GET /api/analytics/context-tax`: 4-tier context tax, spatial ROI, compaction amnesia, top thrash files, ingress
- `skills.py`:
  - `GET /api/skills`: Extends base skills with dead/dormant 30d audit & tool error correlation
  - `GET /api/skills/roi`: Skill ROI & Dead Skill Auditor (`skill_token_weight` footprint, invocations, `skill_error_rate`, dormant flags)
- `nodes.py`:
  - `GET /api/nodes`: Multi-node cluster telemetry with Prometheus & APU hardware metrics
  - `GET /api/nodes/history`: Timeseries GPU load, power, and active slots for fleet APUs
- `visualizer.py`:
  - `GET /api/visualizer/topology`: Complete declarative homelab topology, physical chassis configs, docked services & conduit endpoints
  - `GET /api/visualizer/stream`: Real-time SSE telemetry delta stream (1Hz push with client-side 60Hz interpolation)

## Frontend DOM Elements & Components (`api/static/index.html` & `app.js`)
- `#tab-visualizer`, `#view-visualizer`: Homelab 3D Spatial Visualizer interactive WebGL canvas view
- `#homelabVisualizerCanvas`: Mount point for Three.js WebGL scene
- `#visInspectorDrawer`: Sliding frosted-glass telemetry & service inspection drawer
- `visualizer_scene.js`: Three.js procedural chassis generator, Bezier conduit flow particle system & camera orbit director
- `#tab-pantheon`, `#view-pantheon`: Pantheon Swarm multi-agent grid & detail drawer
- `#tab-sessions`, `#view-sessions`: Sessions list, subagent badges, filter dropdowns, 7d default range selector
- `.session-badge-ingress`, `.session-badge-thrash`: Badges for ingress channel and thrash warning
- `#tab-live`, `#view-live`: Live running sessions & subagent cards
- `#tab-analytics`, `#view-analytics`: Cost & token distribution charts, Context Tax, Spatial ROI
- `#contextTaxCompositionBar`: 4-tier stacked progress bar (Harness, Spatial, Exploration, Productive)
- `#spatialRoiScorecard`: Mapped vs Unmapped scorecard (Turns to edit, Net Tokens, Dollar savings, Map Drift)
- `#topThrashFilesTable`: Top 5 files read without edits
- `#temporalUsageChart`: AI Studio style stacked bar chart by model (Input, Output, Cost, Total Tokens)
- `#tab-nodes`, `#view-nodes`: Fleet APU nodes view
- `#fleetGpuHistoryChart`: Fleet GPU core busy % and power draw timeseries line/area chart
- `renderInstrumentCluster(node)`: SVG automotive instrument cluster component (240° clock sweep -210°→+30°, true telemetry via node.amdgpu/apu_vram/inference_engine, segmented cyan→amber→redline tracks, classic white needles, digital readouts at y=165/182)
- `#mobileMenuBtn`, `#mobileNavDrawer`: Responsive mobile navigation drawer and touch tab bar
- `#statTotalCost`, `#statSpendCap`: Reconciled spend card and monthly spend cap indicators
- `#table-model-breakdown`: Multi-model usage table with reconciled badges
