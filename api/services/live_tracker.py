"""Live session tracker and SSE event stream for Hermes Karma."""
import asyncio
import json
import time
from pathlib import Path
from typing import List, Dict, Any, AsyncGenerator
from api.config import KARMA_LIVE_DIR, HERMES_STATE_DB
from api.services.hermes_reader import hermes_reader


class LiveTracker:
    def __init__(self, live_dir=KARMA_LIVE_DIR):
        self.live_dir = Path(live_dir)
        self.live_dir.mkdir(parents=True, exist_ok=True)

    def get_live_sessions(self) -> List[Dict[str, Any]]:
        now = time.time()
        sessions = {}

        # 1. Read from live hook json files
        for f in self.live_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sid = data.get("session_id")
                if not sid:
                    continue

                last_active = data.get("last_active", 0)
                status = data.get("status", "LIVE")

                # Auto-mark STALE if no heartbeat in 5 minutes and not explicitly ended
                if status not in ["ENDED", "STOPPED"] and (now - last_active) > 300:
                    status = "STALE"
                    data["status"] = "STALE"

                data["age_seconds"] = round(now - last_active, 1)
                sessions[sid] = data
            except Exception:
                continue

        # 2. Augment from recent sessions in state.db (last 10 minutes)
        recent = hermes_reader.get_sessions(limit=10)
        for s in recent.get("sessions", []):
            sid = s.get("session_id")
            last_activity = s.get("last_activity_at") or s.get("started_at") or 0
            if last_activity > (now - 600) and sid not in sessions:
                # Active session without hook
                ended_at = s.get("ended_at")
                status = "ENDED" if ended_at else ("LIVE" if (now - last_activity) < 120 else "WAITING")
                sessions[sid] = {
                    "session_id": sid,
                    "title": s.get("title") or "Hermes Session",
                    "platform": s.get("source") or "cli",
                    "model": s.get("model") or "unknown",
                    "status": status,
                    "started_at": s.get("started_at"),
                    "last_active": last_activity,
                    "age_seconds": round(now - last_activity, 1),
                    "working_directory": s.get("cwd"),
                    "pid": None,
                    "tty": None,
                    "tmux_pane": None,
                }

        results = list(sessions.values())
        results.sort(key=lambda x: x.get("last_active", 0), reverse=True)
        return results

    async def stream_live_events(self, interval: float = 2.0) -> AsyncGenerator[Dict[str, Any], None]:
        while True:
            live = self.get_live_sessions()
            yield {
                "event": "live_sessions_update",
                "data": json.dumps(live)
            }
            await asyncio.sleep(interval)


live_tracker = LiveTracker()
