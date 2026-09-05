# Structural Symbol Map (HermesKarma)

## Backend Services (`api/services/`)
- `hermes_reader.py`: `HermesReader`
  - `get_pantheon_profiles()` -> List[Dict] (Aggregates profiles, subagents, tokens, cost per persona)
  - `get_pantheon_profile_detail(profile_id)` -> Dict (SOUL.md, memory, skills, session history)
  - `get_sessions(limit, offset, source, model, search, persona)` -> Dict
  - `get_session_by_id(session_id)` -> Dict (Session details, subagents, model usages)
  - `get_session_messages(session_id)` -> List[Dict]
  - `get_session_timeline(session_id)` -> List[Dict]
  - `get_analytics_overview(time_range)` -> Dict
- `live_tracker.py`: `LiveTracker`
  - `get_live_sessions()` -> List[Dict] (Active hooks, live delegations, live subagents)
  - `stream_live_events(interval)` -> AsyncGenerator (SSE emitter for live sessions & fleet)
- `node_collector.py`: `NodeTelemetryCollector` (Sysfs/APU telemetry for Chunkito/Beehive)
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
  - `GET /api/analytics/overview`: High-precision multi-model token & cost analytics

## Frontend DOM Elements (`api/static/index.html` & `app.js`)
- `#tab-pantheon`, `#view-pantheon`: Pantheon Swarm multi-agent grid & detail drawer
- `#tab-sessions`, `#view-sessions`: Sessions list, subagent badges, filter dropdowns
- `#tab-live`, `#view-live`: Live running sessions & subagent cards
- `#tab-analytics`, `#view-analytics`: Cost & token distribution charts
