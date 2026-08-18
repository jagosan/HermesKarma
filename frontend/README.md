# Hermes Karma Frontend

The frontend for Hermes Karma is a responsive web dashboard designed for fast trajectory replay, token cost analytics, live telemetry observation, and terminal window focus IPC.

### Running with Backend (Recommended)
When you launch the FastAPI backend (`python3 -m api.main`), it automatically serves the interactive frontend at `http://localhost:8020`.

### Running Standalone with Vite
```bash
npm install
npm run dev -- --port 5180
```
