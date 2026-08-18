"""
Hermes Karma Lifecycle Hook / Observer Plugin
Dispatches live session status, active tools, and terminal window/pane IDs to Hermes Karma.
"""
import os
import json
import time
from pathlib import Path

KARMA_LIVE_DIR = Path.home() / ".hermes_karma" / "live_sessions"
KARMA_LIVE_DIR.mkdir(parents=True, exist_ok=True)


class HermesKarmaHook:
    """Observer hook for Hermes Agent lifecycle events."""

    def on_session_start(self, session_context):
        session_id = getattr(session_context, "session_id", str(session_context))
        payload = {
            "session_id": session_id,
            "title": getattr(session_context, "title", "Hermes Session") or "Hermes Session",
            "platform": getattr(session_context, "platform", "cli"),
            "model": getattr(session_context, "model", "unknown"),
            "status": "LIVE",
            "started_at": time.time(),
            "last_active": time.time(),
            "pid": os.getpid(),
            "tty": os.ttyname(0) if os.isatty(0) else None,
            "tmux_pane": os.getenv("TMUX_PANE"),
            "working_directory": str(Path.cwd()),
            "current_tool": None
        }
        self._write_state(session_id, payload)

    def on_tool_start(self, session_context, tool_name: str, tool_args=None):
        session_id = getattr(session_context, "session_id", str(session_context))
        self._update_status(session_id, "RUNNING_TOOL", current_tool=tool_name)

    def on_tool_complete(self, session_context, tool_name: str, tool_result=None):
        session_id = getattr(session_context, "session_id", str(session_context))
        self._update_status(session_id, "LIVE", current_tool=None)

    def on_turn_complete(self, session_context):
        session_id = getattr(session_context, "session_id", str(session_context))
        self._update_status(session_id, "WAITING", current_tool=None)

    def on_session_end(self, session_context):
        session_id = getattr(session_context, "session_id", str(session_context))
        self._update_status(session_id, "ENDED", current_tool=None)

    def _write_state(self, session_id: str, payload: dict):
        target = KARMA_LIVE_DIR / f"{session_id}.json"
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def _update_status(self, session_id: str, status: str, **kwargs):
        target = KARMA_LIVE_DIR / f"{session_id}.json"
        try:
            data = {}
            if target.exists():
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {
                    "session_id": session_id,
                    "started_at": time.time(),
                    "pid": os.getpid(),
                    "tmux_pane": os.getenv("TMUX_PANE"),
                }
            data["status"] = status
            data["last_active"] = time.time()
            data.update(kwargs)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


# Global singleton instance for direct import
karma_hook = HermesKarmaHook()
