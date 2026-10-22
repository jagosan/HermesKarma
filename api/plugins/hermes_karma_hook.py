"""
Hermes Karma Lifecycle Hook / Observer Plugin
Dispatches live session status, active tools, terminal window/pane IDs, and
SPEC-HK-007 ingress + context-tax metadata (ingress_channel, cold_start_tokens,
spatial-index usage flags, tool-error streaks, and compaction events) to
Hermes Karma via the live-session JSON spool.

All emissions are best-effort and never raise into the host agent: a Karma
outage must never take down a Hermes turn.
"""
import os
import json
import re
import time
from pathlib import Path

KARMA_LIVE_DIR = Path.home() / ".hermes_karma" / "live_sessions"
KARMA_LIVE_DIR.mkdir(parents=True, exist_ok=True)

# SPEC-HK-007 §3.5: canonical ingress gateway tags.
INGRESS_PLATFORM_MAP = {
    "cli": "cli",
    "terminal": "cli",
    "kanban": "cli",
    "oneshot": "cli",
    "webhook": "cli",
    "webui": "webui",
    "dashboard": "webui",
    "telegram": "telegram",
    "cron": "cron",
    "scheduler": "cron",
    "pantheon": "pantheon_subagent",
    "subagent": "pantheon_subagent",
    "delegation": "pantheon_subagent",
    "swarm": "pantheon_subagent",
}

# Spatial navigation telemetry (SPEC-HK-007 §3.2): MAP.md / graphify usage.
_SPATIAL_FILE_MARKERS = ("map.md", "00_ai_context_overview.md")
_READ_TOOLS = {
    "read_file", "read", "view_file", "view", "grep", "grep_code",
    "search_files", "find", "ls", "list_files", "glob",
}
_EDIT_TOOLS = {
    "write_file", "write", "edit_file", "edit", "patch", "patch_file",
    "multi_edit", "create_file", "str_replace_editor", "write_to_file",
}
_PATH_ARG_RE = re.compile(
    r'"(?:path|file_path|filepath|file|target_file)"\s*:\s*"([^"]+)"'
)

# Runaway loop circuit breaker (SPEC-HK-007 §3.6.2) — the hook records the
# streak; live_tracker/webhook_service decide when to alert.
MAX_CONSECUTIVE_TOOL_ERRORS = int(os.getenv("MAX_CONSECUTIVE_TOOL_ERRORS", "5"))


def derive_ingress_channel(platform=None, parent_session_id=None) -> str:
    """Map a platform/platform-string to the canonical SPEC-HK-007 ingress tag."""
    if parent_session_id:
        return "pantheon_subagent"
    key = str(platform or "").strip().lower()
    return INGRESS_PLATFORM_MAP.get(key, "cli")


def _ctx_get(ctx, *names):
    """First non-None attribute on the session context (or its .usage mapping)."""
    usage = getattr(ctx, "usage", None)
    for n in names:
        v = getattr(ctx, n, None)
        if v is None and isinstance(usage, dict):
            v = usage.get(n)
        if v is not None:
            return v
    return None


def _result_is_error(tool_result) -> bool:
    """Best-effort tool failure detection on whatever the host hands back."""
    if tool_result is None:
        return False
    is_err = getattr(tool_result, "is_error", None)
    if is_err is not None:
        return bool(is_err)
    if isinstance(tool_result, dict):
        if tool_result.get("is_error") or tool_result.get("error"):
            return True
        text = str(tool_result.get("content") or tool_result.get("output") or "")
    else:
        text = str(tool_result)
    low = text[:300].lower()
    return low.startswith("error") or "traceback (most recent call last)" in low


class HermesKarmaHook:
    """Observer hook for Hermes Agent lifecycle events."""

    def on_session_start(self, session_context):
        session_id = getattr(session_context, "session_id", str(session_context))
        platform = getattr(session_context, "platform", "cli")

        # Tier 1 cold-start baseline: an explicit emission always wins; else
        # derive from the system prompt size (~4 chars/token).
        cold = _ctx_get(
            session_context,
            "cold_start_tokens", "system_prompt_tokens", "turn1_prompt_tokens",
        )
        if cold is None:
            sys_prompt = getattr(session_context, "system_prompt", None)
            if sys_prompt:
                cold = max(1, len(str(sys_prompt)) // 4)

        payload = {
            "session_id": session_id,
            "title": getattr(session_context, "title", "Hermes Session") or "Hermes Session",
            "platform": platform,
            "ingress_channel": derive_ingress_channel(
                platform, getattr(session_context, "parent_session_id", None)
            ),
            "model": getattr(session_context, "model", "unknown"),
            "status": "LIVE",
            "started_at": time.time(),
            "last_active": time.time(),
            "pid": os.getpid(),
            "tty": os.ttyname(0) if os.isatty(0) else None,
            "tmux_pane": os.getenv("TMUX_PANE"),
            "working_directory": str(Path.cwd()),
            "current_tool": None,
            # --- SPEC-HK-007 context-tax telemetry ---
            "cold_start_tokens": int(cold) if cold is not None else None,
            "turn1_prompt_tokens": None,
            "turns": 0,
            "total_prompt_tokens": 0,
            "total_output_tokens": 0,
            "used_map_md": False,
            "used_graphify": False,
            "files_read": [],
            "files_edited": [],
            "consecutive_tool_errors": 0,
            "max_consecutive_tool_errors": 0,
            "loop_guard_triggered": False,
            "compaction_events": [],
        }
        self._write_state(session_id, payload)

    def on_tool_start(self, session_context, tool_name: str, tool_args=None):
        session_id = getattr(session_context, "session_id", str(session_context))
        name = str(tool_name or "").strip().lower()

        try:
            blob = tool_args if isinstance(tool_args, str) else json.dumps(
                tool_args or {}, default=str
            )
        except Exception:
            blob = str(tool_args or "")
        blob_l = blob.lower()

        spatial = any(mk in blob_l for mk in _SPATIAL_FILE_MARKERS)
        graphify = ("graphify" in name) or ("graphify" in blob_l)
        editing = name in _EDIT_TOOLS
        reading = name in _READ_TOOLS

        path = None
        m = _PATH_ARG_RE.search(blob)
        if m:
            path = m.group(1)
        elif isinstance(tool_args, dict):
            for key in ("path", "file_path", "filepath", "file"):
                v = tool_args.get(key)
                if isinstance(v, str) and v.strip():
                    path = v.strip()
                    break

        self._update_status(session_id, "RUNNING_TOOL", current_tool=tool_name)

        if spatial or graphify or editing or (reading and path):
            def apply(d, path=path, spatial=spatial, graphify=graphify,
                      editing=editing, reading=reading):
                if spatial:
                    d["used_map_md"] = True
                if graphify:
                    d["used_graphify"] = True
                if path:
                    key = "files_edited" if editing else "files_read"
                    lst = d.setdefault(key, [])
                    if path not in lst:
                        lst.append(path)
                return d
            self._mutate(session_id, apply)

    def on_tool_complete(self, session_context, tool_name: str, tool_result=None):
        session_id = getattr(session_context, "session_id", str(session_context))
        is_error = _result_is_error(tool_result)
        self._update_status(session_id, "LIVE", current_tool=None)

        def apply(d):
            streak = int(d.get("consecutive_tool_errors") or 0)
            streak = streak + 1 if is_error else 0
            d["consecutive_tool_errors"] = streak
            d["max_consecutive_tool_errors"] = max(
                int(d.get("max_consecutive_tool_errors") or 0), streak
            )
            d["loop_guard_triggered"] = streak >= MAX_CONSECUTIVE_TOOL_ERRORS
            return d
        self._mutate(session_id, apply)

    def on_turn_complete(self, session_context):
        session_id = getattr(session_context, "session_id", str(session_context))
        prompt = _ctx_get(session_context, "prompt_tokens", "input_tokens", "last_prompt_tokens")
        output = _ctx_get(session_context, "completion_tokens", "output_tokens")

        def apply(d):
            d["turns"] = int(d.get("turns") or 0) + 1
            if d["turns"] == 1 and prompt is not None and d.get("turn1_prompt_tokens") is None:
                d["turn1_prompt_tokens"] = int(prompt)
            if prompt is not None:
                d["total_prompt_tokens"] = int(d.get("total_prompt_tokens") or 0) + int(prompt)
            if output is not None:
                d["total_output_tokens"] = int(d.get("total_output_tokens") or 0) + int(output)
            return d
        self._mutate(session_id, apply)

    def on_session_end(self, session_context):
        session_id = getattr(session_context, "session_id", str(session_context))
        self._update_status(session_id, "ENDED", current_tool=None)

    def on_context_compaction(self, session_context, pre_tokens=None, post_tokens=None, **meta):
        """SPEC-HK-007 §3.3: record a compaction boundary (Amnesia Tax anchor)."""
        session_id = getattr(session_context, "session_id", str(session_context))
        pre = pre_tokens if pre_tokens is not None else _ctx_get(
            session_context, "pre_compaction_tokens"
        )
        post = post_tokens if post_tokens is not None else _ctx_get(
            session_context, "post_compaction_tokens"
        )
        pre = int(pre) if pre is not None else 0
        post = int(post) if post is not None else 0

        event = {
            "at": time.time(),
            "pre_compaction_tokens": pre,
            "post_compaction_tokens": post,
            "tokens_reclaimed": max(0, pre - post),
            "compression_ratio": round(1.0 - (post / pre), 4) if pre > 0 else 0.0,
        }
        for k in ("trigger", "reason", "map_dropped"):
            if meta.get(k) is not None:
                event[k] = meta[k]

        def apply(d):
            d.setdefault("compaction_events", []).append(event)
            # Snapshot what was loaded pre-compaction for Amnesia Re-Read checks.
            d["amnesia_watch_reads"] = list(d.get("files_read") or [])
            return d
        self._mutate(session_id, apply)

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

    def _mutate(self, session_id: str, fn):
        """Read-modify-write helper for incremental telemetry fields."""
        target = KARMA_LIVE_DIR / f"{session_id}.json"
        try:
            data = {}
            if target.exists():
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data = fn(data) or data
            data["last_active"] = time.time()
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass


# Global singleton instance for direct import
karma_hook = HermesKarmaHook()
