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

        # 2. Augment with active swarm delegations and subagent processes from state.db
        conn = hermes_reader._get_ro_conn()
        if conn:
            try:
                with conn:
                    cur = conn.cursor()
                    # 2a. Active delegations
                    cur.execute("""
                        SELECT * FROM async_delegations 
                        WHERE state IN ('running', 'dispatched', 'pending')
                        ORDER BY dispatched_at DESC
                    """)
                    for d in cur.fetchall():
                        d_dict = dict(d)
                        did = d_dict["delegation_id"]
                        task_data = {}
                        if d_dict.get("task_json"):
                            try:
                                task_data = json.loads(d_dict["task_json"])
                            except Exception:
                                pass
                        
                        goal = task_data.get("goal") or "Swarm Subagent Task"
                        model = task_data.get("model") or d_dict.get("model") or "qwen3.8-flash-next:262k"
                        parent_sid = d_dict.get("parent_session_id") or d_dict.get("origin_session")
                        
                        # Persona mapping
                        p_info = {
                            "title": goal,
                            "source": "subagent",
                            "model": model,
                            "billing_provider": "chunkito" if "qwen" in model else "custom"
                        }
                        p_id = hermes_reader.attribute_session_persona(p_info)
                        meta = hermes_reader.ROSTER_META.get(p_id, {}) if p_id else {}
                        
                        # Check live transcript log for current step / active tool
                        current_tool = None
                        last_line_text = ""
                        transcript_path = Path(f"/home/jagosan/.hermes/cache/delegation/live/{did}/task-0.log")
                        if transcript_path.exists():
                            try:
                                t_lines = transcript_path.read_text(encoding="utf-8").strip().splitlines()
                                if t_lines:
                                    last_line_text = t_lines[-1]
                                    if "tool" in last_line_text.lower():
                                        current_tool = last_line_text.split("|")[-1].strip()[:60]
                            except Exception:
                                pass

                        dispatched_at = d_dict.get("dispatched_at") or now
                        sessions[did] = {
                            "session_id": did,
                            "title": goal[:90],
                            "platform": "subagent",
                            "model": model,
                            "status": "RUNNING_SUBAGENT",
                            "current_tool": current_tool,
                            "last_line": last_line_text[:100],
                            "started_at": dispatched_at,
                            "last_active": d_dict.get("updated_at") or dispatched_at,
                            "age_seconds": round(now - dispatched_at, 1),
                            "working_directory": None,
                            "parent_session_id": parent_sid,
                            "is_subagent": True,
                            "persona_id": p_id,
                            "persona_name": meta.get("name", "Swarm Worker"),
                            "persona_emoji": meta.get("emoji", "🤖"),
                            "pid": d_dict.get("owner_pid"),
                            "tty": None,
                            "tmux_pane": None,
                        }

                    # 2b. Active subagent sessions in sessions table
                    cur.execute("""
                        SELECT * FROM sessions 
                        WHERE (source = 'subagent' OR parent_session_id IS NOT NULL)
                          AND (ended_at IS NULL OR ended_at = 0)
                          AND started_at > ?
                        ORDER BY started_at DESC
                    """, (now - 7200,))
                    for s in cur.fetchall():
                        s_dict = dict(s)
                        sid = s_dict["id"]
                        parent_id = s_dict.get("parent_session_id")
                        
                        # If already tracked via active delegation under the same parent, merge/skip
                        already_tracked = any(
                            existing.get("parent_session_id") == parent_id and existing.get("is_subagent")
                            for existing in sessions.values()
                        )
                        if sid in sessions or already_tracked:
                            continue

                        # Extract title from user message if missing
                        title = s_dict.get("title")
                        if not title:
                            msg_row = cur.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1", (sid,)).fetchone()
                            title = msg_row[0][:80].replace("\n", " ").strip() if msg_row and msg_row[0] else f"Subagent ({sid})"

                        p_id = hermes_reader.attribute_session_persona(s_dict)
                        meta = hermes_reader.ROSTER_META.get(p_id, {}) if p_id else {}
                        st = s_dict.get("started_at") or now
                        last_act = s_dict.get("last_activity_at") or st

                        sessions[sid] = {
                            "session_id": sid,
                            "title": title,
                            "platform": "subagent",
                            "model": s_dict.get("model") or "unknown",
                            "status": "RUNNING_SUBAGENT",
                            "started_at": st,
                            "last_active": last_act,
                            "age_seconds": round(now - last_act, 1),
                            "working_directory": s_dict.get("cwd"),
                            "parent_session_id": s_dict.get("parent_session_id"),
                            "is_subagent": True,
                            "persona_id": p_id,
                            "persona_name": meta.get("name", "Swarm Worker"),
                            "persona_emoji": meta.get("emoji", "🤖"),
                            "pid": None,
                            "tty": None,
                            "tmux_pane": None,
                        }
            except Exception:
                pass

        # 3. Augment from recent general sessions in state.db (last 10 minutes)
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
                    "is_subagent": s.get("is_subagent", False),
                    "persona_id": s.get("persona_id"),
                    "persona_name": s.get("persona_name"),
                    "persona_emoji": s.get("persona_emoji"),
                    "parent_session_id": s.get("parent_session_id"),
                    "pid": None,
                    "tty": None,
                    "tmux_pane": None,
                }

        results = list(sessions.values())
        results.sort(key=lambda x: x.get("last_active", 0), reverse=True)
        return results

    async def stream_live_events(self, interval: float = 2.0) -> AsyncGenerator[Dict[str, Any], None]:
        ticks = 0
        while True:
            live = self.get_live_sessions()
            yield {
                "event": "live_sessions_update",
                "data": json.dumps(live)
            }

            # Every 5 ticks (~10s by default), emit fleet telemetry update
            if ticks % 5 == 0:
                try:
                    from api.services.node_collector import node_collector
                    nodes_data = await node_collector.get_all_nodes_telemetry(force_refresh=False)
                    yield {
                        "event": "fleet_nodes_update",
                        "data": json.dumps(nodes_data)
                    }
                except Exception:
                    pass

            ticks += 1
            await asyncio.sleep(interval)


live_tracker = LiveTracker()
