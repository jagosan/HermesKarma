"""Hermes Agent Data Reader Service.
Connects to ~/.hermes/state.db in Read-Only WAL mode and parses config, skills, memory, and cron.
"""
import sqlite3
import os
import json
import yaml
import re
import difflib
from pathlib import Path
from typing import List, Dict, Any, Optional
from api.config import (
    HERMES_STATE_DB,
    HERMES_CONFIG_YAML,
    HERMES_MEMORY_FILE,
    HERMES_USER_FILE,
    HERMES_SKILLS_DIR,
    HERMES_CRON_DIR,
)
from api.services.metadata_service import metadata_service


class HermesReader:
    def __init__(self, db_path=HERMES_STATE_DB):
        self.db_path = Path(db_path)

    def _get_ro_conn(self) -> Optional[sqlite3.Connection]:
        if not self.db_path.exists():
            return None
        # Connect in read-only mode using SQLite URI
        uri = f"file:{self.db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON;")
        return conn

    def get_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        source: Optional[str] = None,
        model: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[float] = None,
        date_to: Optional[float] = None,
    ) -> Dict[str, Any]:
        conn = self._get_ro_conn()
        if not conn:
            return {"total": 0, "sessions": [], "limit": limit, "offset": offset}

        with conn:
            cur = conn.cursor()
            conditions = ["hidden = 0" if "hidden" in self._get_table_columns(cur, "sessions") else "1=1"]
            params = []

            if source:
                conditions.append("source = ?")
                params.append(source)
            if model:
                conditions.append("model LIKE ?")
                params.append(f"%{model}%")
            if search:
                conditions.append("(title LIKE ? OR id LIKE ? OR cwd LIKE ?)")
                params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
            if date_from:
                conditions.append("started_at >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("started_at <= ?")
                params.append(date_to)

            where_clause = " AND ".join(conditions)

            # Count total
            cur.execute(f"SELECT COUNT(*) FROM sessions WHERE {where_clause}", params)
            total = cur.fetchone()[0]

            # Fetch rows
            cur.execute(f"""
                SELECT * FROM sessions
                WHERE {where_clause}
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            rows = cur.fetchall()

            # Merge with metadata
            meta_map = metadata_service.get_all_session_metadata_map()
            sessions = []
            for r in rows:
                item = dict(r)
                sid = item.get("id")
                item["session_id"] = sid
                item["metadata"] = meta_map.get(sid, {
                    "tags": [],
                    "notes": "",
                    "starred": 0,
                    "tickets": [],
                    "custom_name": None
                })
                # Check for auto ticket detection from git_branch
                branch = item.get("git_branch") or ""
                detected_ticket = self._extract_ticket_from_branch(branch)
                if detected_ticket and not any(t["ticket_key"] == detected_ticket for t in item["metadata"].get("tickets", [])):
                    item["detected_ticket"] = detected_ticket
                sessions.append(item)

            return {
                "total": total,
                "sessions": sessions,
                "limit": limit,
                "offset": offset,
            }

    def _get_table_columns(self, cur: sqlite3.Cursor, table_name: str) -> List[str]:
        cur.execute(f"PRAGMA table_info('{table_name}')")
        return [col[1] for col in cur.fetchall()]

    def _extract_ticket_from_branch(self, branch: str) -> Optional[str]:
        if not branch:
            return None
        match = re.search(r'([A-Z]{2,10}-\d+|#\d+)', branch)
        return match.group(1) if match else None

    def get_session_by_id(self, session_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_ro_conn()
        if not conn:
            return None

        with conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
            if not row:
                return None
            session = dict(row)
            session["session_id"] = session["id"]
            session["metadata"] = metadata_service.get_session_meta(session_id)
            
            # Fetch model usage breakdown
            cur.execute("SELECT * FROM session_model_usage WHERE session_id = ?", (session_id,))
            session["model_usages"] = [dict(r) for r in cur.fetchall()]

            # Fetch delegations/subagents
            cur.execute("""
                SELECT * FROM async_delegations 
                WHERE origin_session = ? OR parent_session_id = ? OR origin_session_id = ?
            """, (session_id, session_id, session_id))
            session["delegations"] = [dict(r) for r in cur.fetchall()]

            return session

    def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        conn = self._get_ro_conn()
        if not conn:
            return []

        with conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM messages 
                WHERE session_id = ? 
                ORDER BY timestamp ASC, id ASC
            """, (session_id,))
            rows = cur.fetchall()
            messages = []
            for r in rows:
                m = dict(r)
                if m.get("tool_calls"):
                    try:
                        m["tool_calls_parsed"] = json.loads(m["tool_calls"])
                    except Exception:
                        m["tool_calls_parsed"] = None
                messages.append(m)
            return messages

    def get_session_timeline(self, session_id: str) -> List[Dict[str, Any]]:
        """Extract structured step-by-step timeline events for visual replay."""
        messages = self.get_session_messages(session_id)
        events = []
        step_index = 1

        for msg in messages:
            role = msg.get("role")
            ts = msg.get("timestamp")
            mid = msg.get("id")
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
            tool_calls = msg.get("tool_calls_parsed")
            tool_name = msg.get("tool_name")
            display_kind = msg.get("display_kind")

            # 1. If message has reasoning/thinking block
            if reasoning:
                events.append({
                    "id": f"step_{mid}_reasoning",
                    "step_number": step_index,
                    "type": "thought",
                    "role": role,
                    "timestamp": ts,
                    "title": "Reasoning & Plan",
                    "content": reasoning,
                    "message_id": mid,
                })
                step_index += 1

            # 2. If message is user input
            if role == "user":
                events.append({
                    "id": f"step_{mid}_user",
                    "step_number": step_index,
                    "type": "user_message",
                    "role": role,
                    "timestamp": ts,
                    "title": "User Message",
                    "content": content,
                    "message_id": mid,
                })
                step_index += 1

            # 3. If tool calls requested
            elif tool_calls and isinstance(tool_calls, list):
                for idx, tc in enumerate(tool_calls):
                    function_name = tc.get("function", {}).get("name", "tool")
                    args = tc.get("function", {}).get("arguments", "{}")
                    events.append({
                        "id": f"step_{mid}_call_{idx}",
                        "step_number": step_index,
                        "type": "tool_call",
                        "role": "assistant",
                        "timestamp": ts,
                        "title": f"Execute: {function_name}",
                        "tool_name": function_name,
                        "arguments": args,
                        "tool_call_id": tc.get("id"),
                        "message_id": mid,
                    })
                    step_index += 1

            # 4. If tool execution result
            elif role == "tool":
                is_error = "error" in content.lower() or "exception" in content.lower()
                events.append({
                    "id": f"step_{mid}_tool_result",
                    "step_number": step_index,
                    "type": "tool_result",
                    "role": role,
                    "timestamp": ts,
                    "title": f"Result: {tool_name or 'tool'}",
                    "tool_name": tool_name,
                    "tool_call_id": msg.get("tool_call_id"),
                    "content": content,
                    "status": "error" if is_error else "success",
                    "message_id": mid,
                })
                step_index += 1

            # 5. Assistant response content
            elif role == "assistant" and content:
                events.append({
                    "id": f"step_{mid}_assistant",
                    "step_number": step_index,
                    "type": "assistant_response",
                    "role": role,
                    "timestamp": ts,
                    "title": "Assistant Response",
                    "content": content,
                    "token_count": msg.get("token_count"),
                    "message_id": mid,
                })
                step_index += 1

        return events

    def get_session_subagents(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve hierarchy of subagents and delegations for this session."""
        conn = self._get_ro_conn()
        if not conn:
            return []

        with conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM async_delegations 
                WHERE origin_session = ? OR parent_session_id = ? OR origin_session_id = ?
                ORDER BY dispatched_at ASC
            """, (session_id, session_id, session_id))
            delegations = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("task_json"):
                    try:
                        d["task_parsed"] = json.loads(d["task_json"])
                    except Exception:
                        d["task_parsed"] = d["task_json"]
                if d.get("result_json"):
                    try:
                        d["result_parsed"] = json.loads(d["result_json"])
                    except Exception:
                        d["result_parsed"] = d["result_json"]
                delegations.append(d)
            return delegations

    def get_analytics_overview(self) -> Dict[str, Any]:
        """Aggregate total tokens, costs, models, and tools."""
        conn = self._get_ro_conn()
        if not conn:
            return {
                "total_sessions": 0,
                "total_messages": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read_tokens": 0,
                "total_cache_write_tokens": 0,
                "total_reasoning_tokens": 0,
                "total_estimated_cost_usd": 0.0,
                "total_actual_cost_usd": 0.0,
                "local_sessions_count": 0,
                "cloud_sessions_count": 0,
                "model_distribution": [],
                "source_distribution": [],
                "tool_distribution": [],
                "daily_activity": [],
            }

        with conn:
            cur = conn.cursor()
            # Basic aggregations
            cur.execute("""
                SELECT 
                    COUNT(*) as total_sessions,
                    SUM(message_count) as total_messages,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    SUM(cache_read_tokens) as total_cache_read_tokens,
                    SUM(cache_write_tokens) as total_cache_write_tokens,
                    SUM(reasoning_tokens) as total_reasoning_tokens,
                    SUM(estimated_cost_usd) as total_estimated_cost_usd,
                    SUM(actual_cost_usd) as total_actual_cost_usd
                FROM sessions
            """)
            summary_row = cur.fetchone()
            summary = dict(summary_row) if summary_row else {}

            # Provider / Model distribution
            cur.execute("""
                SELECT 
                    COALESCE(model, 'Unknown') as model,
                    COUNT(*) as session_count,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(cache_read_tokens) as cache_read_tokens,
                    SUM(estimated_cost_usd) as cost_usd
                FROM sessions
                GROUP BY model
                ORDER BY session_count DESC
            """)
            models = [dict(r) for r in cur.fetchall()]

            # Source platform distribution
            cur.execute("""
                SELECT 
                    COALESCE(source, 'cli') as source,
                    COUNT(*) as count
                FROM sessions
                GROUP BY source
                ORDER BY count DESC
            """)
            sources = [dict(r) for r in cur.fetchall()]

            # Tool usage distribution from messages
            cur.execute("""
                SELECT 
                    tool_name,
                    COUNT(*) as count
                FROM messages
                WHERE tool_name IS NOT NULL AND tool_name != ''
                GROUP BY tool_name
                ORDER BY count DESC
                LIMIT 30
            """)
            tools = [dict(r) for r in cur.fetchall()]

            # Daily activity (last 30 days)
            cur.execute("""
                SELECT 
                    strftime('%Y-%m-%d', datetime(started_at, 'unixepoch')) as date,
                    COUNT(*) as session_count,
                    SUM(input_tokens + output_tokens) as total_tokens,
                    SUM(estimated_cost_usd) as daily_cost
                FROM sessions
                WHERE started_at IS NOT NULL AND started_at > (strftime('%s', 'now') - 2592000)
                GROUP BY date
                ORDER BY date ASC
            """)
            daily = [dict(r) for r in cur.fetchall()]

            # Count local vs cloud
            local_count = 0
            cloud_count = 0
            for m in models:
                m_name = (m.get("model") or "").lower()
                if "ollama" in m_name or "local" in m_name or "gguf" in m_name or "vllm" in m_name or "chunkito" in m_name:
                    local_count += m.get("session_count", 0)
                else:
                    cloud_count += m.get("session_count", 0)

            # KV Cache hit rate calculation
            total_in = summary.get("total_input_tokens") or 0
            cache_read = summary.get("total_cache_read_tokens") or 0
            cache_hit_rate = round((cache_read / (total_in + cache_read) * 100), 2) if (total_in + cache_read) > 0 else 0.0

            return {
                **summary,
                "local_sessions_count": local_count,
                "cloud_sessions_count": cloud_count,
                "cache_hit_rate_pct": cache_hit_rate,
                "model_distribution": models,
                "source_distribution": sources,
                "tool_distribution": tools,
                "daily_activity": daily,
            }

    def get_skills_catalog(self) -> List[Dict[str, Any]]:
        """List all autonomous and custom skills in ~/.hermes/skills."""
        skills = []
        if not HERMES_SKILLS_DIR.exists():
            return skills

        for skill_path in HERMES_SKILLS_DIR.rglob("SKILL.md"):
            try:
                content = skill_path.read_text(encoding="utf-8")
                # Parse frontmatter
                meta = {}
                body = content
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        try:
                            meta = yaml.safe_load(parts[1]) or {}
                            body = parts[2].strip()
                        except Exception:
                            pass

                rel_path = skill_path.relative_to(HERMES_SKILLS_DIR)
                category = str(rel_path.parent) if rel_path.parent != Path(".") else "general"
                name = meta.get("name") or skill_path.parent.name
                desc = meta.get("description") or ""

                skills.append({
                    "name": name,
                    "category": category,
                    "description": desc,
                    "version": meta.get("version", "1.0.0"),
                    "path": str(skill_path),
                    "relative_path": str(rel_path),
                    "tags": meta.get("metadata", {}).get("hermes", {}).get("tags", []),
                    "content": content,
                    "body_preview": body[:300] + "..." if len(body) > 300 else body,
                    "mtime": skill_path.stat().st_mtime,
                })
            except Exception as e:
                continue

        skills.sort(key=lambda x: x["name"])
        return skills

    def get_skill_history(self, skill_name: str) -> Dict[str, Any]:
        """Fetch skill detail, snapshots, and diffs."""
        catalog = self.get_skills_catalog()
        matched = next((s for s in catalog if s["name"] == skill_name), None)
        if not matched:
            return {"error": "Skill not found"}

        # Look up snapshots from metadata.db if any
        # Also generate section analysis / evolution breakdown
        return {
            "skill": matched,
            "history": [
                {
                    "version": matched.get("version", "1.0.0"),
                    "timestamp": matched.get("mtime"),
                    "change_summary": "Current active version in ~/.hermes/skills/",
                    "content": matched.get("content"),
                }
            ]
        }

    def get_memory_state(self) -> Dict[str, Any]:
        """Read current MEMORY.md and USER.md."""
        memory_content = ""
        user_content = ""

        if HERMES_MEMORY_FILE.exists():
            memory_content = HERMES_MEMORY_FILE.read_text(encoding="utf-8")
        if HERMES_USER_FILE.exists():
            user_content = HERMES_USER_FILE.read_text(encoding="utf-8")

        # Parse bullet / paragraph blocks
        memory_items = [
            line.strip() for line in memory_content.split("§") if line.strip()
        ] if "§" in memory_content else [l for l in memory_content.splitlines() if l.strip()]

        user_items = [
            line.strip() for line in user_content.split("§") if line.strip()
        ] if "§" in user_content else [l for l in user_content.splitlines() if l.strip()]

        return {
            "memory": {
                "raw": memory_content,
                "items": memory_items,
                "character_count": len(memory_content),
                "mtime": HERMES_MEMORY_FILE.stat().st_mtime if HERMES_MEMORY_FILE.exists() else None,
            },
            "user": {
                "raw": user_content,
                "items": user_items,
                "character_count": len(user_content),
                "mtime": HERMES_USER_FILE.stat().st_mtime if HERMES_USER_FILE.exists() else None,
            }
        }

    def get_cron_jobs(self) -> List[Dict[str, Any]]:
        """Inspect scheduled jobs in ~/.hermes/cron/ or state."""
        jobs = []
        if not HERMES_CRON_DIR.exists():
            return jobs

        for f in HERMES_CRON_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                data["file"] = f.name
                jobs.append(data)
            except Exception:
                continue

        # Also scan state_meta or cron configs if present in DB
        conn = self._get_ro_conn()
        if conn:
            with conn:
                cur = conn.cursor()
                try:
                    cur.execute("SELECT * FROM state_meta WHERE key LIKE 'cron_%'")
                    for r in cur.fetchall():
                        jobs.append({"key": r[0], "value": r[1]})
                except Exception:
                    pass

        return jobs


hermes_reader = HermesReader()
