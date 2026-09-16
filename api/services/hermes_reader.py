"""Hermes Agent Data Reader Service.
Connects to ~/.hermes/state.db in Read-Only WAL mode and parses config, skills, memory, cron, and complete multi-model usage.
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
    HERMES_DIR,
    HERMES_STATE_DB,
    HERMES_CONFIG_YAML,
    HERMES_MEMORY_FILE,
    HERMES_USER_FILE,
    HERMES_SKILLS_DIR,
    HERMES_CRON_DIR,
    HERMES_PROFILES_DIR,
)
from api.services.metadata_service import metadata_service
from api.services.pricing_engine import pricing_engine


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

    def _get_all_state_dbs(self) -> List[Path]:
        """Return all state.db paths: primary ~/.hermes/state.db and all ~/.hermes/profiles/*/state.db."""
        dbs = []
        if self.db_path.exists():
            dbs.append(self.db_path)
        profiles_dir = HERMES_DIR / "profiles"
        if profiles_dir.exists():
            for p_db in sorted(profiles_dir.glob("*/state.db")):
                if p_db.is_file() and p_db != self.db_path:
                    dbs.append(p_db)
        return dbs

    def _get_ro_conn_for_db(self, db_path: Path) -> Optional[sqlite3.Connection]:
        if not db_path.exists():
            return None
        uri = f"file:{db_path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA query_only = ON;")
        return conn

    def _find_db_for_session(self, session_id: str) -> Optional[sqlite3.Connection]:
        """Locate which state.db contains the session_id and return an active read-only connection."""
        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,))
                if cur.fetchone():
                    return conn
                conn.close()
            except Exception:
                conn.close()
        return None

    def get_all_distinct_models(self) -> List[Dict[str, Any]]:
        """Retrieve all distinct models used across sessions, subagents, and config across all state databases."""
        models_map = {}
        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            with conn:
                cur = conn.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                # 1. From session_model_usage
                if "session_model_usage" in tables:
                    try:
                        cur.execute("""
                            SELECT 
                                model,
                                COALESCE(billing_provider, '') as provider,
                                COUNT(DISTINCT session_id) as session_count,
                                SUM(api_call_count) as total_calls
                            FROM session_model_usage
                            WHERE model IS NOT NULL AND model != ''
                            GROUP BY model
                        """)
                        for r in cur.fetchall():
                            m = dict(r)
                            m_name = m["model"]
                            if m_name not in models_map:
                                models_map[m_name] = {
                                    "name": m_name,
                                    "provider": m.get("provider") or self._classify_provider(m_name),
                                    "session_count": m.get("session_count", 0),
                                    "total_calls": m.get("total_calls", 0),
                                    "is_local": self._is_local_model(m_name, m.get("provider")),
                                }
                            else:
                                models_map[m_name]["session_count"] += m.get("session_count", 0)
                                models_map[m_name]["total_calls"] += m.get("total_calls", 0)
                    except Exception:
                        pass

                # 2. From sessions table
                if "sessions" in tables:
                    try:
                        cur.execute("""
                            SELECT DISTINCT model, COUNT(*) as cnt
                            FROM sessions
                            WHERE model IS NOT NULL AND model != ''
                            GROUP BY model
                        """)
                        for r in cur.fetchall():
                            m_name = r[0]
                            if m_name not in models_map:
                                models_map[m_name] = {
                                    "name": m_name,
                                    "provider": self._classify_provider(m_name),
                                    "session_count": r[1],
                                    "total_calls": r[1],
                                    "is_local": self._is_local_model(m_name, ""),
                                }
                            else:
                                if models_map[m_name]["session_count"] == 0:
                                    models_map[m_name]["session_count"] = r[1]
                    except Exception:
                        pass

        return sorted(list(models_map.values()), key=lambda x: (not x["is_local"], x["name"]))

    def _is_local_model(self, model_name: str, provider: Optional[str] = "") -> bool:
        """Determine if a model is served locally (Ollama / chunkito / llama.cpp / GGUF)."""
        m = (model_name or "").lower()
        p = (provider or "").lower()
        if p in ["ollama", "chunkito", "custom", "local", "vllm", "llamacpp"]:
            return True
        if any(k in m for k in [
            "qwen", "gemma", "deepseek", "gpt-oss", "gguf", "ud-iq2", "chunkito",
            "llama", "mistral", "phi", "strix", "local", ":q8_", ":iq", ":UD-"
        ]):
            return True
        return False

    def _classify_provider(self, model_name: str) -> str:
        """Infer provider category for display tags."""
        m = (model_name or "").lower()
        if "gemini" in m:
            return "Google Gemini"
        elif "claude" in m or "sonnet" in m or "opus" in m:
            return "Anthropic Claude"
        elif "chunkito" in m or "qwen3.8" in m or "qwen3-coder" in m:
            return "Chunkito APU"
        elif "deepseek" in m:
            return "Chunkito / Local"
        elif "qwen" in m or "gemma" in m or "gpt-oss" in m:
            return "Local Ollama / GGUF"
        elif "openrouter" in m:
            return "OpenRouter"
        return "Custom / Self-Hosted"

    def get_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        source: Optional[str] = None,
        model: Optional[str] = None,
        search: Optional[str] = None,
        persona: Optional[str] = None,
        date_from: Optional[float] = None,
        date_to: Optional[float] = None,
    ) -> Dict[str, Any]:
        all_candidates = []
        seen_ids = set()

        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            with conn:
                cur = conn.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "sessions" not in tables:
                    continue

                cols = self._get_table_columns(cur, "sessions")
                conditions = ["hidden = 0" if "hidden" in cols else "1=1"]
                params = []

                if source:
                    conditions.append("source = ?")
                    params.append(source)
                if model:
                    has_usage = "session_model_usage" in tables
                    has_deleg = "async_delegations" in tables
                    model_conds = ["sessions.model LIKE ?"]
                    m_params = [f"%{model}%"]
                    if has_usage:
                        model_conds.append("sessions.id IN (SELECT session_id FROM session_model_usage WHERE model LIKE ?)")
                        m_params.append(f"%{model}%")
                    if has_deleg:
                        model_conds.append("sessions.id IN (SELECT origin_session FROM async_delegations WHERE event_json LIKE ? OR task_json LIKE ?)")
                        m_params.extend([f"%{model}%", f"%{model}%"])
                    conditions.append(f"({' OR '.join(model_conds)})")
                    params.extend(m_params)
                if search:
                    search_cols = []
                    s_params = []
                    for sc in ["title", "id", "cwd", "git_branch"]:
                        if sc in cols:
                            search_cols.append(f"sessions.{sc} LIKE ?")
                            s_params.append(f"%{search}%")
                    if search_cols:
                        conditions.append(f"({' OR '.join(search_cols)})")
                        params.extend(s_params)
                if date_from:
                    conditions.append("sessions.started_at >= ?")
                    params.append(date_from)
                if date_to:
                    conditions.append("sessions.started_at <= ?")
                    params.append(date_to)

                where_clause = " AND ".join(conditions)
                cur.execute(f"SELECT * FROM sessions WHERE {where_clause} ORDER BY started_at DESC", params)
                for r in cur.fetchall():
                    rd = dict(r)
                    sid = rd["id"]
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)
                    # Hydrate first prompt & title if messages table exists
                    if "messages" in tables:
                        try:
                            msg_row = cur.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1", (sid,)).fetchone()
                            if msg_row and msg_row[0]:
                                rd["first_prompt"] = msg_row[0]
                                if not rd.get("title") or str(rd.get("title")).startswith("[Workspace::v1:"):
                                    rd["title"] = msg_row[0][:80].replace("\n", " ").strip()
                            elif not rd.get("title") and (rd.get("source") == "subagent" or rd.get("parent_session_id")):
                                rd["title"] = f"Subagent Task ({sid})"
                        except Exception:
                            if not rd.get("title") and (rd.get("source") == "subagent" or rd.get("parent_session_id")):
                                rd["title"] = f"Subagent Task ({sid})"
                    all_candidates.append(rd)

        # Sort all candidates across all databases by started_at DESC
        all_candidates.sort(key=lambda x: x.get("started_at") or 0, reverse=True)

        # Filter by persona if specified
        if persona:
            matched = []
            for rd in all_candidates:
                attr = self.attribute_session_persona(rd)
                if attr == persona.lower() or (rd.get("profile_name") and rd.get("profile_name").lower() == persona.lower()):
                    matched.append(rd)
            total = len(matched)
            rows = matched[offset:offset + limit]
        else:
            total = len(all_candidates)
            rows = all_candidates[offset:offset + limit]

        # Batch fetch model usages and delegations for selected rows
        session_ids = [r["id"] for r in rows]
        from collections import defaultdict
        usage_by_session = defaultdict(list)
        delegations_by_session = defaultdict(int)

        if session_ids:
            placeholders = ",".join(["?"] * len(session_ids))
            for db_path in self._get_all_state_dbs():
                conn = self._get_ro_conn_for_db(db_path)
                if not conn:
                    continue
                with conn:
                    cur = conn.cursor()
                    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                    if "session_model_usage" in tables:
                        try:
                            cur.execute(f"""
                                SELECT session_id, model, billing_provider, api_call_count, input_tokens, output_tokens, cache_read_tokens, reasoning_tokens, estimated_cost_usd
                                FROM session_model_usage
                                WHERE session_id IN ({placeholders})
                                ORDER BY input_tokens DESC
                            """, session_ids)
                            for ur in cur.fetchall():
                                usage_by_session[ur["session_id"]].append(dict(ur))
                        except Exception:
                            pass
                    if "async_delegations" in tables:
                        try:
                            cur.execute(f"""
                                SELECT origin_session, COUNT(*) as cnt
                                FROM async_delegations
                                WHERE origin_session IN ({placeholders})
                                GROUP BY origin_session
                            """, session_ids)
                            for dr in cur.fetchall():
                                delegations_by_session[dr["origin_session"]] += dr["cnt"]
                        except Exception:
                            pass

        meta_map = metadata_service.get_all_session_metadata_map()
        sessions = []
        for item in rows:
            sid = item.get("id")
            item["session_id"] = sid
            # Tag persona
            p_id = self.attribute_session_persona(item)
            if p_id and p_id in self.ROSTER_META:
                p_meta = self.ROSTER_META[p_id]
                item["persona_id"] = p_id
                item["persona_name"] = p_meta.get("name")
                item["persona_emoji"] = p_meta.get("emoji")
            else:
                item["persona_id"] = None
                item["persona_name"] = None
                item["persona_emoji"] = None

            item["is_subagent"] = item.get("source") == "subagent" or bool(item.get("parent_session_id"))

            item["metadata"] = meta_map.get(sid, {
                "tags": [],
                "notes": "",
                "starred": 0,
                "tickets": [],
                "custom_name": None
            })
            item["models_used"] = usage_by_session.get(sid, [])
            if not item["models_used"] and item.get("model"):
                item["models_used"] = [{
                    "model": item.get("model"),
                    "billing_provider": item.get("billing_provider") or "",
                    "input_tokens": item.get("input_tokens") or 0,
                    "output_tokens": item.get("output_tokens") or 0,
                    "cache_read_tokens": item.get("cache_read_tokens") or 0,
                    "reasoning_tokens": item.get("reasoning_tokens") or 0,
                    "estimated_cost_usd": item.get("estimated_cost_usd") or 0.0,
                }]
            item["subagent_count"] = delegations_by_session.get(sid, 0)
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
            "all_models": [m["name"] for m in self.get_all_distinct_models()],
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
        conn = self._find_db_for_session(session_id) or self._get_ro_conn()
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
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "session_model_usage" in tables:
                cur.execute("SELECT * FROM session_model_usage WHERE session_id = ?", (session_id,))
                session["model_usages"] = [dict(r) for r in cur.fetchall()]
            else:
                session["model_usages"] = []

            # Fetch delegations/subagents
            if "async_delegations" in tables:
                cur.execute("""
                    SELECT * FROM async_delegations 
                    WHERE origin_session = ? OR parent_session_id = ? OR origin_session_id = ?
                """, (session_id, session_id, session_id))
                session["delegations"] = [dict(r) for r in cur.fetchall()]
            else:
                session["delegations"] = []

            return session

    def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        conn = self._find_db_for_session(session_id) or self._get_ro_conn()
        if not conn:
            return []

        with conn:
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "messages" not in tables:
                return []
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
        conn = self._find_db_for_session(session_id) or self._get_ro_conn()
        if not conn:
            return []

        with conn:
            cur = conn.cursor()
            tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if "async_delegations" not in tables:
                return []
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

    def get_analytics_overview(self, time_range: str = "all") -> Dict[str, Any]:
        """Aggregate total tokens, costs, models, and tools with full multi-model and local vs cloud precision across all state databases."""
        import time
        from collections import defaultdict

        now_ts = time.time()
        cutoff_ts = None
        if time_range in ("today", "24h", "1d"):
            cutoff_ts = now_ts - 86400
        elif time_range in ("7d", "7days", "week"):
            cutoff_ts = now_ts - (7 * 86400)
        elif time_range in ("30d", "30days"):
            cutoff_ts = now_ts - (30 * 86400)
        elif time_range in ("month", "this_month"):
            import datetime
            now_dt = datetime.datetime.now()
            month_start = datetime.datetime(now_dt.year, now_dt.month, 1)
            cutoff_ts = month_start.timestamp()

        import datetime
        now_dt = datetime.datetime.now()
        month_start_ts = datetime.datetime(now_dt.year, now_dt.month, 1).timestamp()
        current_month_cost = 0.0

        total_sessions = 0
        total_messages = 0

        model_aggs: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "model": "",
            "session_ids": set(),
            "api_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": 0.0,
            "billing_provider": "",
        })

        source_counts: Dict[str, int] = defaultdict(int)
        tool_counts: Dict[str, int] = defaultdict(int)
        daily_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "session_count": 0,
            "total_tokens": 0,
            "daily_cost": 0.0,
        })

        for db_path in self._get_all_state_dbs():
            conn = self._get_ro_conn_for_db(db_path)
            if not conn:
                continue
            with conn:
                cur = conn.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

                # 1. Sessions summary and source distribution
                if "sessions" in tables:
                    if cutoff_ts is not None:
                        cur.execute("SELECT COUNT(*) as cnt, SUM(message_count) as msgs FROM sessions WHERE started_at >= ?", (cutoff_ts,))
                    else:
                        cur.execute("SELECT COUNT(*) as cnt, SUM(message_count) as msgs FROM sessions")
                    s_row = cur.fetchone()
                    if s_row and s_row["cnt"]:
                        total_sessions += (s_row["cnt"] or 0)
                        total_messages += (s_row["msgs"] or 0)

                    # Source platform distribution
                    cur.execute("SELECT COALESCE(source, 'cli') as source, COUNT(*) as cnt FROM sessions GROUP BY source")
                    for r in cur.fetchall():
                        source_counts[r["source"]] += r["cnt"]

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
                    """)
                    for r in cur.fetchall():
                        d_str = r["date"]
                        daily_stats[d_str]["session_count"] += (r["session_count"] or 0)
                        daily_stats[d_str]["total_tokens"] += (r["total_tokens"] or 0)
                        daily_stats[d_str]["daily_cost"] += (r["daily_cost"] or 0.0)

                # 2. Model usage breakdown
                has_usage = False
                if "session_model_usage" in tables:
                    if cutoff_ts is not None:
                        cur.execute("""
                            SELECT 
                                COALESCE(model, 'Unknown') as model,
                                session_id,
                                SUM(api_call_count) as api_call_count,
                                SUM(input_tokens) as input_tokens,
                                SUM(output_tokens) as output_tokens,
                                SUM(cache_read_tokens) as cache_read_tokens,
                                SUM(cache_write_tokens) as cache_write_tokens,
                                SUM(reasoning_tokens) as reasoning_tokens,
                                SUM(estimated_cost_usd) as cost_usd,
                                COALESCE(billing_provider, '') as billing_provider
                            FROM session_model_usage
                            WHERE (last_seen >= ? OR first_seen >= ?)
                            GROUP BY model, session_id
                        """, (cutoff_ts, cutoff_ts))
                    else:
                        cur.execute("""
                            SELECT 
                                COALESCE(model, 'Unknown') as model,
                                session_id,
                                SUM(api_call_count) as api_call_count,
                                SUM(input_tokens) as input_tokens,
                                SUM(output_tokens) as output_tokens,
                                SUM(cache_read_tokens) as cache_read_tokens,
                                SUM(cache_write_tokens) as cache_write_tokens,
                                SUM(reasoning_tokens) as reasoning_tokens,
                                SUM(estimated_cost_usd) as cost_usd,
                                COALESCE(billing_provider, '') as billing_provider
                            FROM session_model_usage
                            GROUP BY model, session_id
                        """)
                    u_rows = cur.fetchall()
                    if u_rows:
                        has_usage = True
                        for r in u_rows:
                            m_name = r["model"]
                            e = model_aggs[m_name]
                            e["model"] = m_name
                            if r["session_id"]:
                                e["session_ids"].add(r["session_id"])
                            e["api_call_count"] += (r["api_call_count"] or 0)
                            e["input_tokens"] += (r["input_tokens"] or 0)
                            e["output_tokens"] += (r["output_tokens"] or 0)
                            e["cache_read_tokens"] += (r["cache_read_tokens"] or 0)
                            e["cache_write_tokens"] += (r["cache_write_tokens"] or 0)
                            e["reasoning_tokens"] += (r["reasoning_tokens"] or 0)
                            e["cost_usd"] += (r["cost_usd"] or 0.0)
                            if r["billing_provider"] and not e["billing_provider"]:
                                e["billing_provider"] = r["billing_provider"]

                # Fallback to sessions table if session_model_usage was empty in this db
                if not has_usage and "sessions" in tables:
                    if cutoff_ts is not None:
                        cur.execute("""
                            SELECT 
                                COALESCE(model, 'Unknown') as model,
                                id as session_id,
                                SUM(api_call_count) as api_call_count,
                                SUM(input_tokens) as input_tokens,
                                SUM(output_tokens) as output_tokens,
                                SUM(cache_read_tokens) as cache_read_tokens,
                                SUM(cache_write_tokens) as cache_write_tokens,
                                SUM(reasoning_tokens) as reasoning_tokens,
                                SUM(estimated_cost_usd) as cost_usd,
                                COALESCE(billing_provider, '') as billing_provider
                            FROM sessions
                            WHERE started_at >= ?
                            GROUP BY model, id
                        """, (cutoff_ts,))
                    else:
                        cur.execute("""
                            SELECT 
                                COALESCE(model, 'Unknown') as model,
                                id as session_id,
                                SUM(api_call_count) as api_call_count,
                                SUM(input_tokens) as input_tokens,
                                SUM(output_tokens) as output_tokens,
                                SUM(cache_read_tokens) as cache_read_tokens,
                                SUM(cache_write_tokens) as cache_write_tokens,
                                SUM(reasoning_tokens) as reasoning_tokens,
                                SUM(estimated_cost_usd) as cost_usd,
                                COALESCE(billing_provider, '') as billing_provider
                            FROM sessions
                            GROUP BY model, id
                        """)
                    for r in cur.fetchall():
                        m_name = r["model"]
                        e = model_aggs[m_name]
                        e["model"] = m_name
                        if r["session_id"]:
                            e["session_ids"].add(r["session_id"])
                        e["api_call_count"] += (r["api_call_count"] or 0)
                        e["input_tokens"] += (r["input_tokens"] or 0)
                        e["output_tokens"] += (r["output_tokens"] or 0)
                        e["cache_read_tokens"] += (r["cache_read_tokens"] or 0)
                        e["cache_write_tokens"] += (r["cache_write_tokens"] or 0)
                        e["reasoning_tokens"] += (r["reasoning_tokens"] or 0)
                        e["cost_usd"] += (r["cost_usd"] or 0.0)
                        if r["billing_provider"] and not e["billing_provider"]:
                            e["billing_provider"] = r["billing_provider"]

                # 3. Tool usage distribution from messages
                if "messages" in tables:
                    try:
                        cur.execute("""
                            SELECT tool_name, COUNT(*) as count
                            FROM messages
                            WHERE tool_name IS NOT NULL AND tool_name != ''
                            GROUP BY tool_name
                        """)
                        for r in cur.fetchall():
                            tool_counts[r["tool_name"]] += r["count"]
                    except Exception:
                        pass

        # Annotate model rows with is_local and readable provider tag
        model_rows = []
        for m_name, d in model_aggs.items():
            model_rows.append({
                "model": m_name,
                "session_count": len(d["session_ids"]),
                "api_call_count": d["api_call_count"],
                "input_tokens": d["input_tokens"],
                "output_tokens": d["output_tokens"],
                "cache_read_tokens": d["cache_read_tokens"],
                "cache_write_tokens": d["cache_write_tokens"],
                "reasoning_tokens": d["reasoning_tokens"],
                "cost_usd": d["cost_usd"],
                "billing_provider": d["billing_provider"],
            })
        model_rows.sort(key=lambda x: (x["input_tokens"] + x["output_tokens"]), reverse=True)

        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        total_reasoning = 0
        total_api_calls = 0
        total_cost = 0.0

        local_tokens = 0
        cloud_tokens = 0
        local_sessions = 0
        cloud_sessions = 0

        total_raw_cost = 0.0
        enhanced_models = []
        for m in model_rows:
            inp = m.get("input_tokens") or 0
            out = m.get("output_tokens") or 0
            cread = m.get("cache_read_tokens") or 0
            cwrite = m.get("cache_write_tokens") or 0
            reas = m.get("reasoning_tokens") or 0
            calls = m.get("api_call_count") or 0
            stored_cost = m.get("cost_usd") or 0.0
            scnt = m.get("session_count") or 0

            is_local = self._is_local_model(m.get("model", ""), m.get("billing_provider", ""))
            
            # Reconcile via PricingEngine
            rec = pricing_engine.reconcile_usage(
                model_name=m.get("model", ""),
                input_tokens=inp,
                output_tokens=out,
                cache_read_tokens=cread,
                cache_write_tokens=cwrite,
                reasoning_tokens=reas,
                stored_cost_usd=stored_cost,
                billing_provider=m.get("billing_provider", ""),
            )
            cost = rec.cost_usd
            m["cost_usd"] = cost
            m["raw_cost_usd"] = rec.raw_stored_cost_usd
            m["is_reconciled"] = rec.is_reconciled
            m["is_local"] = is_local
            m["provider_category"] = self._classify_provider(m.get("model", ""))

            total_input += inp
            total_output += out
            total_cache_read += cread
            total_cache_write += cwrite
            total_reasoning += reas
            total_api_calls += calls
            total_cost += cost
            total_raw_cost += stored_cost

            if is_local:
                local_tokens += (inp + out)
                local_sessions += scnt
            else:
                cloud_tokens += (inp + out)
                cloud_sessions += scnt

            enhanced_models.append(m)

        # 3. Provider distribution
        provider_map: Dict[str, Dict[str, Any]] = {}
        for m in enhanced_models:
            p_name = m["provider_category"]
            if p_name not in provider_map:
                provider_map[p_name] = {
                    "provider": p_name,
                    "session_count": 0,
                    "api_call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cost_usd": 0.0,
                    "is_local": m["is_local"],
                }
            provider_map[p_name]["session_count"] += m.get("session_count", 0)
            provider_map[p_name]["api_call_count"] += m.get("api_call_count", 0)
            provider_map[p_name]["input_tokens"] += m.get("input_tokens", 0)
            provider_map[p_name]["output_tokens"] += m.get("output_tokens", 0)
            provider_map[p_name]["cache_read_tokens"] += m.get("cache_read_tokens", 0)
            provider_map[p_name]["cost_usd"] += m.get("cost_usd", 0.0)

        provider_distribution = sorted(
            list(provider_map.values()),
            key=lambda x: (x["input_tokens"] + x["output_tokens"]),
            reverse=True
        )

        # 4. Source platform distribution
        sources = [{"source": k, "count": v} for k, v in sorted(source_counts.items(), key=lambda x: x[1], reverse=True)]

        # 5. Tool usage distribution from messages
        tools = [{"tool_name": k, "count": v} for k, v in sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:30]]

        # 6. Daily activity (last 30 days)
        daily = [{"date": k, "session_count": v["session_count"], "total_tokens": v["total_tokens"], "daily_cost": v["daily_cost"]} for k, v in sorted(daily_stats.items(), key=lambda x: x[0])]

        # KV Cache hit rate calculation
        total_prompt_processed = total_input + total_cache_read
        cache_hit_rate = round((total_cache_read / total_prompt_processed * 100), 2) if total_prompt_processed > 0 else 0.0
        zero_cost_ratio = round((local_tokens / (local_tokens + cloud_tokens) * 100), 1) if (local_tokens + cloud_tokens) > 0 else 0.0

        spend_cap = 250.0
        # If current_month_cost was not computed from a filtered view, compute it across models for month
        if current_month_cost == 0.0:
            for m in enhanced_models:
                if not m.get("is_local"):
                    current_month_cost += m.get("cost_usd", 0.0)

        spend_cap_pct = round(min(100.0, (current_month_cost / spend_cap) * 100), 1) if spend_cap > 0 else 0.0
        reconciled_delta = round(max(0.0, total_cost - total_raw_cost), 4)

        return {
            "time_range": time_range,
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cache_read_tokens": total_cache_read,
            "total_cache_write_tokens": total_cache_write,
            "total_reasoning_tokens": total_reasoning,
            "total_api_calls": total_api_calls,
            "total_estimated_cost_usd": round(total_cost, 4),
            "total_actual_cost_usd": round(total_cost, 4),
            "raw_stored_cost_usd": round(total_raw_cost, 4),
            "reconciled_delta_usd": reconciled_delta,
            "current_month_cost_usd": round(current_month_cost, 2),
            "spend_cap_usd": spend_cap,
            "spend_cap_pct": spend_cap_pct,
            "local_sessions_count": local_sessions,
            "cloud_sessions_count": cloud_sessions,
            "local_tokens_total": local_tokens,
            "cloud_tokens_total": cloud_tokens,
            "local_zero_cost_ratio_pct": zero_cost_ratio,
            "cache_hit_rate_pct": cache_hit_rate,
            "model_distribution": enhanced_models,
            "provider_distribution": provider_distribution,
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

    ROSTER_META = {
        "owl": {
            "name": "Owl",
            "emoji": "🦉",
            "role": "Chief Architect & Master Reasoning",
            "domain": "Architecture blueprints, trade-off arbitration, failure domains, ADRs, Mermaid graphs",
            "default_model": "gemini-3.7-flash",
            "accent_color": "emerald",
        },
        "rabbit": {
            "name": "Rabbit",
            "emoji": "🐰",
            "role": "Taskmaster & Kanban Coordinator",
            "domain": "Decomposing specs into atomic Kanban tasks, dependency chains, sprint cadence",
            "default_model": "hf.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:UD-IQ2_M",
            "accent_color": "brand",
        },
        "tigger": {
            "name": "Tigger",
            "emoji": "🐯",
            "role": "DevOps & Systems Executor (Local Swarm)",
            "domain": "High-speed test-driven implementation, boilerplate scaffolding, code generation (12 slots w/ native 256k on chunkito)",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "amber",
        },
        "jagular": {
            "name": "Jagular",
            "emoji": "🐆",
            "role": "The Beast & Big Iron Forensics (Sole Hunter)",
            "domain": "Monster-context whole-repo architectural forensics (>50k–262k tokens, Qwen3.8-Flash-Next 177B on chunkito; requires mutual exclusion vacate protocol)",
            "default_model": "qwen3.8-flash-next:262k",
            "accent_color": "orange",
        },
        "piglet": {
            "name": "Piglet",
            "emoji": "🐷",
            "role": "Agile Scout & Log Triager",
            "domain": "Rapid log sniffing, tracebacks triage, lightweight classifications, micro-summaries",
            "default_model": "qwen3.5:latest",
            "accent_color": "rose",
        },
        "eeyore": {
            "name": "Eeyore",
            "emoji": "🫏",
            "role": "Safety Officer & Adversarial Auditor",
            "domain": "Pre-commit security audits, permission leakage, secret exposure, adversarial failure modes",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "indigo",
        },
        "pooh": {
            "name": "Pooh",
            "emoji": "🐻",
            "role": "Knowledge Gardener & Vault Curator",
            "domain": "Obsidian vault curation, runbook gardening, wikilinks indexing, daily logs (Chunkito 256k)",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "amber",
        },
        "coder": {
            "name": "Coder",
            "emoji": "💻",
            "role": "Autonomous Software Engineer",
            "domain": "Full-stack feature engineering, test-driven development, deep debugging, refactoring",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "blue",
        },
        "ingest": {
            "name": "Ingest",
            "emoji": "📥",
            "role": "Knowledge Ingest & Article Synthesizer",
            "domain": "Web extraction, PDF document synthesis, cognitive graph enrichment, note capture",
            "default_model": "qwen3-coder-30b:262k",
            "accent_color": "teal",
        },
    }

    def attribute_session_persona(self, session: Dict[str, Any]) -> Optional[str]:
        """Infer which Pantheon swarm agent a session belongs to."""
        # 1. Direct profile name match
        prof = (session.get("profile_name") or "").lower()
        if prof in self.ROSTER_META:
            return prof

        # 2. Textual persona mentions in title, goal, context, cwd, custom notes, or first prompt
        text = f"{session.get('title') or ''} {session.get('goal') or ''} {session.get('context') or ''} {session.get('cwd') or ''} {session.get('first_prompt') or ''}".lower()
        if "@owl" in text or "owl:" in text or "🦉" in text or "pantheon-swarm" in text or "pantheon swarm" in text or "/pantheon-swarm" in text:
            return "owl"
        if "@rabbit" in text or "rabbit:" in text or "🐰" in text:
            return "rabbit"
        if "@jagular" in text or "jagular:" in text or "🐆" in text:
            return "jagular"
        if "@tigger" in text or "tigger:" in text or "🐯" in text:
            return "tigger"
        if "@piglet" in text or "piglet:" in text or "🐷" in text:
            return "piglet"
        if "@eeyore" in text or "eeyore:" in text or "🫏" in text:
            return "eeyore"
        if "@pooh" in text or "pooh:" in text or "🐻" in text:
            return "pooh"
        if "@coder" in text or "coder:" in text or "💻" in text:
            return "coder"
        if "@ingest" in text or "ingest:" in text or "📥" in text:
            return "ingest"

        # 3. Model & source matching for subagents and delegations
        is_subagent = (
            session.get("source") == "subagent"
            or bool(session.get("parent_session_id"))
            or "delegation_id" in session
            or session.get("is_subagent")
        )
        model = (session.get("model") or "").lower()
        provider = (session.get("billing_provider") or "").lower()

        if is_subagent:
            if "ernie" in model:
                return "eeyore"
            elif "qwen3.5" in model:
                return "piglet"
            elif "qwen3-30b" in model:
                return "rabbit"
            elif "qwen3-coder" in model:
                return "coder"
            elif "qwen3.8-flash-next" in model:
                if re.search(r'\b(vault|gardener|gardening|curat|curating|wikilink|runbook)\b', text, re.IGNORECASE):
                    return "pooh"
                return "jagular"
            elif "qwen3.8-27b" in model or "qwen3.8" in model or "chunkito" in provider or "chunkito" in model:
                if re.search(r'\b(vault|gardener|gardening|curat|curating|wikilink|runbook)\b', text, re.IGNORECASE):
                    return "pooh"
                return "tigger"
            elif "gemini" in model:
                return "owl"

        return None

    def get_pantheon_profiles(self) -> List[Dict[str, Any]]:
        """Scan ~/.hermes/profiles/ and aggregate metadata, sessions, tokens, skills, and memory for each Pantheon swarm agent."""
        profiles_dir = HERMES_DIR / "profiles"
        roster_meta = self.ROSTER_META

        agent_profiles = []
        if not profiles_dir.exists():
            return agent_profiles

        import yaml

        # Fetch all subagent sessions from main state.db for persona aggregation
        main_conn = self._get_ro_conn()
        all_main_sessions = []
        if main_conn:
            try:
                with main_conn:
                    m_cur = main_conn.cursor()
                    rows = m_cur.execute("""
                        SELECT id, title, model, started_at, ended_at, message_count, 
                               tool_call_count, input_tokens, output_tokens, cache_read_tokens, 
                               estimated_cost_usd, git_branch, profile_name, source, parent_session_id, cwd, billing_provider
                        FROM sessions
                    """).fetchall()
                    for row in rows:
                        s_dict = dict(row)
                        try:
                            msg_row = m_cur.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'user' ORDER BY id ASC LIMIT 1", (s_dict["id"],)).fetchone()
                            if msg_row and msg_row[0]:
                                s_dict["first_prompt"] = msg_row[0]
                                if not s_dict.get("title") or s_dict.get("title").startswith("[Workspace::v1:"):
                                    s_dict["title"] = msg_row[0][:80].replace("\n", " ").strip()
                            elif not s_dict.get("title") and (s_dict.get("source") == "subagent" or s_dict.get("parent_session_id")):
                                s_dict["title"] = f"Subagent Task ({s_dict['id']})"
                        except Exception:
                            pass
                        all_main_sessions.append(s_dict)
            except Exception:
                pass

        for p_id, meta in roster_meta.items():
            p_dir = profiles_dir / p_id

            # 1. Profile config & soul
            prof_file = p_dir / "profile.yaml" if p_dir.exists() else None
            prof_data = {}
            if prof_file and prof_file.exists():
                try:
                    prof_data = yaml.safe_load(prof_file.read_text(encoding="utf-8")) or {}
                except Exception:
                    pass

            conf_file = p_dir / "config.yaml" if p_dir.exists() else None
            conf_data = {}
            if conf_file and conf_file.exists():
                try:
                    conf_data = yaml.safe_load(conf_file.read_text(encoding="utf-8")) or {}
                except Exception:
                    pass

            soul_file = p_dir / "SOUL.md" if p_dir.exists() else None
            soul_content = soul_file.read_text(encoding="utf-8").strip() if soul_file and soul_file.exists() else ""

            # Extract first paragraph of SOUL as personality summary
            soul_summary = ""
            if soul_content:
                paragraphs = [p.strip() for p in soul_content.split("\n\n") if p.strip() and not p.strip().startswith("#")]
                soul_summary = paragraphs[0] if paragraphs else soul_content[:200]

            model_name = conf_data.get("model", {}).get("default") or meta.get("default_model", "qwen3.8-flash-next:262k")
            provider_name = conf_data.get("model", {}).get("provider") or ("chunkito" if self._is_local_model(model_name) else "gemini")
            is_local = self._is_local_model(model_name, provider_name)

            # 2. Collect sessions from standalone profile DB (if exists)
            p_db = p_dir / "state.db" if p_dir.exists() else None
            sess_count = 0
            tokens_in = 0
            tokens_out = 0
            cache_read = 0
            cost_usd = 0.0
            msg_count = 0
            collected_sessions = []
            seen_ids = set()

            if p_db and p_db.exists():
                try:
                    conn = sqlite3.connect(f"file:{p_db}?mode=ro", uri=True)
                    conn.row_factory = sqlite3.Row
                    with conn:
                        cur = conn.cursor()
                        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                        if "sessions" in tables:
                            rows = cur.execute("""
                                SELECT id, title, model, started_at, message_count, tool_call_count,
                                       input_tokens, output_tokens, cache_read_tokens, estimated_cost_usd 
                                FROM sessions ORDER BY started_at DESC
                            """).fetchall()
                            for r in rows:
                                rd = dict(r)
                                seen_ids.add(rd["id"])
                                collected_sessions.append(rd)
                                sess_count += 1
                                tokens_in += rd.get("input_tokens") or 0
                                tokens_out += rd.get("output_tokens") or 0
                                cache_read += rd.get("cache_read_tokens") or 0
                                cost_usd += rd.get("estimated_cost_usd") or 0.0
                                msg_count += rd.get("message_count") or 0
                except Exception:
                    pass

            # 3. Collect attributed sessions & subagents from main database
            for s in all_main_sessions:
                if s["id"] in seen_ids:
                    continue
                # Match either exact profile_name or attributed persona
                if s.get("profile_name") == p_id or self.attribute_session_persona(s) == p_id:
                    seen_ids.add(s["id"])
                    collected_sessions.append(s)
                    sess_count += 1
                    tokens_in += s.get("input_tokens") or 0
                    tokens_out += s.get("output_tokens") or 0
                    cache_read += s.get("cache_read_tokens") or 0
                    cost_usd += s.get("estimated_cost_usd") or 0.0
                    msg_count += s.get("message_count") or 0

            # Sort collected sessions by started_at desc
            collected_sessions.sort(key=lambda x: x.get("started_at") or 0, reverse=True)

            # 4. Count specialized skills
            p_skills_dir = p_dir / "skills" if p_dir.exists() else None
            skills_count = len(list(p_skills_dir.rglob("SKILL.md"))) if p_skills_dir and p_skills_dir.exists() else 0

            # 5. Memory & User profile sizes
            p_mem_file = p_dir / "MEMORY.md" if p_dir.exists() else None
            p_user_file = p_dir / "USER.md" if p_dir.exists() else None
            mem_chars = len(p_mem_file.read_text(encoding="utf-8")) if p_mem_file and p_mem_file.exists() else 0
            user_chars = len(p_user_file.read_text(encoding="utf-8")) if p_user_file and p_user_file.exists() else 0

            agent_profiles.append({
                "id": p_id,
                "name": meta["name"],
                "emoji": meta["emoji"],
                "role": meta["role"],
                "domain": meta["domain"],
                "accent_color": meta["accent_color"],
                "description": prof_data.get("description") or meta["domain"],
                "personality_summary": soul_summary,
                "model": model_name,
                "provider": provider_name,
                "is_local": is_local,
                "provider_category": self._classify_provider(model_name),
                "total_sessions": sess_count,
                "total_messages": msg_count,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "total_tokens": tokens_in + tokens_out,
                "cache_read_tokens": cache_read,
                "estimated_cost_usd": round(cost_usd, 4),
                "skills_count": skills_count,
                "memory_chars": mem_chars,
                "user_chars": user_chars,
                "has_soul": bool(soul_content),
                "recent_sessions": collected_sessions[:5],
                "_all_sessions": collected_sessions,
            })

        return agent_profiles

    def get_pantheon_profile_detail(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full details, SOUL.md content, memory, skills, and session history for a specific profile."""
        profiles = self.get_pantheon_profiles()
        summary = next((p for p in profiles if p["id"] == profile_id), None)
        if not summary:
            return None

        # Extract collected sessions from summary cache
        all_sessions = summary.pop("_all_sessions", [])

        p_dir = HERMES_DIR / "profiles" / profile_id
        import yaml

        soul_file = p_dir / "SOUL.md" if p_dir.exists() else None
        soul_raw = soul_file.read_text(encoding="utf-8") if soul_file and soul_file.exists() else ""

        mem_file = p_dir / "MEMORY.md" if p_dir.exists() else None
        mem_raw = mem_file.read_text(encoding="utf-8") if mem_file and mem_file.exists() else ""

        user_file = p_dir / "USER.md" if p_dir.exists() else None
        user_raw = user_file.read_text(encoding="utf-8") if user_file and user_file.exists() else ""

        conf_file = p_dir / "config.yaml" if p_dir.exists() else None
        conf_raw = conf_file.read_text(encoding="utf-8") if conf_file and conf_file.exists() else ""
        conf_data = yaml.safe_load(conf_raw) if conf_raw else {}

        # Profile skills
        skills_list = []
        p_skills_dir = p_dir / "skills" if p_dir.exists() else None
        if p_skills_dir and p_skills_dir.exists():
            for sk_path in p_skills_dir.rglob("SKILL.md"):
                try:
                    sk_content = sk_path.read_text(encoding="utf-8")
                    sk_meta = {}
                    if sk_content.startswith("---"):
                        parts = sk_content.split("---", 2)
                        if len(parts) >= 3:
                            sk_meta = yaml.safe_load(parts[1]) or {}
                    skills_list.append({
                        "name": sk_meta.get("name") or sk_path.parent.name,
                        "description": sk_meta.get("description", ""),
                        "category": sk_meta.get("category", "specialized"),
                        "path": str(sk_path),
                    })
                except Exception:
                    pass

        # Cron jobs
        cron_list = []
        p_cron_dir = p_dir / "cron" if p_dir.exists() else None
        if p_cron_dir and p_cron_dir.exists():
            for cf in p_cron_dir.glob("*.json"):
                try:
                    cdata = json.loads(cf.read_text(encoding="utf-8"))
                    cdata["file"] = cf.name
                    cron_list.append(cdata)
                except Exception:
                    pass

        return {
            **summary,
            "soul_raw": soul_raw,
            "memory_raw": mem_raw,
            "user_raw": user_raw,
            "config_raw": conf_raw,
            "config": conf_data,
            "skills": skills_list,
            "sessions": all_sessions,
            "cron_jobs": cron_list,
        }


hermes_reader = HermesReader()
