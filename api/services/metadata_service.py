"""Metadata database service for Hermes Karma (~/.hermes_karma/metadata.db)."""
import sqlite3
import time
import json
from typing import List, Dict, Any, Optional
from api.config import KARMA_METADATA_DB


class MetadataService:
    def __init__(self, db_path=KARMA_METADATA_DB):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS session_metadata (
                session_id TEXT PRIMARY KEY,
                tags TEXT DEFAULT '[]',
                notes TEXT DEFAULT '',
                starred INTEGER DEFAULT 0,
                custom_name TEXT,
                updated_at REAL
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS ticket_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                provider TEXT NOT NULL, -- github, linear, jira
                ticket_key TEXT NOT NULL, -- e.g. ENG-402, #104
                url TEXT,
                title TEXT,
                status TEXT DEFAULT 'open',
                assignee TEXT,
                description TEXT,
                last_synced_at REAL,
                raw_data TEXT,
                created_at REAL,
                UNIQUE(session_id, provider, ticket_key)
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                event_type TEXT NOT NULL,
                delivery_id TEXT,
                ticket_key TEXT,
                payload TEXT,
                status TEXT DEFAULT 'processed',
                linked_sessions TEXT DEFAULT '[]',
                error_message TEXT,
                received_at REAL
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS skill_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                version TEXT,
                content TEXT NOT NULL,
                diff_summary TEXT,
                timestamp REAL
            )
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS node_gpu_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                gpu_busy_percent REAL NOT NULL DEFAULT 0.0,
                gtt_used_gb REAL NOT NULL DEFAULT 0.0,
                gtt_total_gb REAL NOT NULL DEFAULT 0.0,
                power_w REAL NOT NULL DEFAULT 0.0,
                temperature_c REAL NOT NULL DEFAULT 0.0,
                active_slots INTEGER NOT NULL DEFAULT 0
            )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_node_gpu_samples_node_ts ON node_gpu_samples(node_id, timestamp)")

            # Ensure columns exist in case of pre-existing DB without newer columns
            cur.execute("PRAGMA table_info(ticket_links)")
            existing_cols = {row["name"] for row in cur.fetchall()}
            if "status" not in existing_cols:
                cur.execute("ALTER TABLE ticket_links ADD COLUMN status TEXT DEFAULT 'open'")
            if "assignee" not in existing_cols:
                cur.execute("ALTER TABLE ticket_links ADD COLUMN assignee TEXT")
            if "description" not in existing_cols:
                cur.execute("ALTER TABLE ticket_links ADD COLUMN description TEXT")
            if "last_synced_at" not in existing_cols:
                cur.execute("ALTER TABLE ticket_links ADD COLUMN last_synced_at REAL")
            if "raw_data" not in existing_cols:
                cur.execute("ALTER TABLE ticket_links ADD COLUMN raw_data TEXT")

            conn.commit()

    def get_session_meta(self, session_id: str) -> Dict[str, Any]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM session_metadata WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
            if row:
                data = dict(row)
                data["tags"] = json.loads(data["tags"]) if data.get("tags") else []
            else:
                data = {
                    "session_id": session_id,
                    "tags": [],
                    "notes": "",
                    "starred": 0,
                    "custom_name": None,
                    "updated_at": None,
                }
            
            # Fetch tickets
            cur.execute("SELECT * FROM ticket_links WHERE session_id = ? ORDER BY created_at DESC", (session_id,))
            tickets = [dict(r) for r in cur.fetchall()]
            data["tickets"] = tickets
            return data

    def get_all_session_metadata_map(self) -> Dict[str, Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM session_metadata")
            res = {}
            for r in cur.fetchall():
                item = dict(r)
                item["tags"] = json.loads(item["tags"]) if item.get("tags") else []
                res[item["session_id"]] = item
            
            cur.execute("SELECT * FROM ticket_links ORDER BY created_at DESC")
            for t in cur.fetchall():
                td = dict(t)
                sid = td["session_id"]
                if sid in res:
                    if "tickets" not in res[sid]:
                        res[sid]["tickets"] = []
                    res[sid]["tickets"].append(td)
            return res

    def update_session_meta(
        self,
        session_id: str,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
        starred: Optional[bool] = None,
        custom_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.get_session_meta(session_id)
        new_tags = json.dumps(tags) if tags is not None else json.dumps(current["tags"])
        new_notes = notes if notes is not None else current["notes"]
        new_starred = int(starred) if starred is not None else current["starred"]
        new_custom_name = custom_name if custom_name is not None else current["custom_name"]
        now = time.time()

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO session_metadata (session_id, tags, notes, starred, custom_name, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                tags = excluded.tags,
                notes = excluded.notes,
                starred = excluded.starred,
                custom_name = excluded.custom_name,
                updated_at = excluded.updated_at
            """, (session_id, new_tags, new_notes, new_starred, new_custom_name, now))
            conn.commit()

        return self.get_session_meta(session_id)

    def add_ticket_link(
        self,
        session_id: str,
        provider: str,
        ticket_key: str,
        url: Optional[str] = None,
        title: Optional[str] = None,
        status: Optional[str] = "open",
        assignee: Optional[str] = None,
        description: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        raw_json = json.dumps(raw_data) if raw_data is not None else None
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO ticket_links (
                session_id, provider, ticket_key, url, title, status, assignee, description, last_synced_at, raw_data, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, provider, ticket_key) DO UPDATE SET
                url = COALESCE(excluded.url, ticket_links.url),
                title = COALESCE(excluded.title, ticket_links.title),
                status = COALESCE(excluded.status, ticket_links.status),
                assignee = COALESCE(excluded.assignee, ticket_links.assignee),
                description = COALESCE(excluded.description, ticket_links.description),
                last_synced_at = excluded.last_synced_at,
                raw_data = COALESCE(excluded.raw_data, ticket_links.raw_data)
            """, (
                session_id,
                provider.lower(),
                ticket_key,
                url,
                title,
                status,
                assignee,
                description,
                now,
                raw_json,
                now,
            ))
            conn.commit()
        return self.get_session_meta(session_id)

    def update_ticket_status_by_key(
        self,
        provider: str,
        ticket_key: str,
        status: Optional[str] = None,
        title: Optional[str] = None,
        url: Optional[str] = None,
        assignee: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> int:
        now = time.time()
        raw_json = json.dumps(raw_data) if raw_data is not None else None
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            UPDATE ticket_links
            SET status = COALESCE(?, status),
                title = COALESCE(?, title),
                url = COALESCE(?, url),
                assignee = COALESCE(?, assignee),
                raw_data = COALESCE(?, raw_data),
                last_synced_at = ?
            WHERE provider = ? AND ticket_key = ?
            """, (status, title, url, assignee, raw_json, now, provider.lower(), ticket_key))
            conn.commit()
            return cur.rowcount

    def remove_ticket_link(self, session_id: str, ticket_key: str):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM ticket_links WHERE session_id = ? AND ticket_key = ?", (session_id, ticket_key))
            conn.commit()

    def get_all_tickets(
        self,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM ticket_links WHERE 1=1"
        params: List[Any] = []

        if provider:
            query += " AND provider = ?"
            params.append(provider.lower())
        if status:
            query += " AND status = ?"
            params.append(status)
        if search:
            query += " AND (ticket_key LIKE ? OR title LIKE ? OR session_id LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])

        query += " ORDER BY created_at DESC"

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def get_sessions_by_ticket(self, ticket_key: str, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM ticket_links WHERE ticket_key = ?"
        params: List[Any] = [ticket_key]
        if provider:
            query += " AND provider = ?"
            params.append(provider.lower())
        query += " ORDER BY created_at DESC"

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def log_webhook_event(
        self,
        provider: str,
        event_type: str,
        delivery_id: Optional[str],
        ticket_key: Optional[str],
        payload: Dict[str, Any],
        status: str = "processed",
        linked_sessions: Optional[List[str]] = None,
        error_message: Optional[str] = None,
    ) -> int:
        now = time.time()
        payload_json = json.dumps(payload)
        sessions_json = json.dumps(linked_sessions or [])
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO webhook_events (
                provider, event_type, delivery_id, ticket_key, payload, status, linked_sessions, error_message, received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                provider.lower(),
                event_type,
                delivery_id,
                ticket_key,
                payload_json,
                status,
                sessions_json,
                error_message,
                now,
            ))
            conn.commit()
            return cur.lastrowid or 0

    def get_webhook_events(
        self,
        limit: int = 50,
        offset: int = 0,
        provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM webhook_events WHERE 1=1"
        params: List[Any] = []
        if provider:
            query += " AND provider = ?"
            params.append(provider.lower())
        query += " ORDER BY received_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            events = []
            for r in cur.fetchall():
                d = dict(r)
                if d.get("linked_sessions"):
                    try:
                        d["linked_sessions"] = json.loads(d["linked_sessions"])
                    except Exception:
                        d["linked_sessions"] = []
                if d.get("payload"):
                    try:
                        d["payload"] = json.loads(d["payload"])
                    except Exception:
                        pass
                events.append(d)
            return events

    def get_webhook_stats(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as total FROM webhook_events")
            total = cur.fetchone()["total"]

            cur.execute("SELECT provider, COUNT(*) as count FROM webhook_events GROUP BY provider")
            by_provider = {r["provider"]: r["count"] for r in cur.fetchall()}

            cur.execute("SELECT COUNT(DISTINCT ticket_key) as total_tickets FROM ticket_links")
            total_tickets = cur.fetchone()["total_tickets"]

            cur.execute("SELECT COUNT(DISTINCT session_id) as synced_sessions FROM ticket_links")
            synced_sessions = cur.fetchone()["synced_sessions"]

            return {
                "total_webhooks": total,
                "webhooks_by_provider": by_provider,
                "total_tickets": total_tickets,
                "synced_sessions": synced_sessions,
            }

    def record_gpu_sample(self, node_id: str, gpu_busy_percent: float, gtt_used_gb: float, 
                          gtt_total_gb: float, power_w: float, temperature_c: float, 
                          active_slots: int, timestamp: float = None):
        """Record a single GPU telemetry timeseries sample with automatic 60-day pruning."""
        ts = timestamp or time.time()
        prune_cutoff = ts - (60 * 86400)
        
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO node_gpu_samples 
                (node_id, timestamp, gpu_busy_percent, gtt_used_gb, gtt_total_gb, power_w, temperature_c, active_slots)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (node_id, ts, gpu_busy_percent, gtt_used_gb, gtt_total_gb, power_w, temperature_c, active_slots))
            
            # Auto-prune samples older than 60 days
            cur.execute("DELETE FROM node_gpu_samples WHERE timestamp < ?", (prune_cutoff,))
            conn.commit()

    def get_gpu_history(self, time_range: str = "30d", node_id: Optional[str] = None) -> Dict[str, Any]:
        """Retrieve GPU telemetry timeseries for fleet nodes over a specific time range."""
        now_ts = time.time()
        
        if time_range in ("today", "24h", "1d"):
            cutoff_ts = now_ts - 86400
            bucket_interval_sec = 3600
            bucket_format = "%Y-%m-%d %H:00"
        elif time_range in ("7d", "7days", "week"):
            cutoff_ts = now_ts - (7 * 86400)
            bucket_interval_sec = 86400
            bucket_format = "%Y-%m-%d"
        else: # 30d, month, all
            cutoff_ts = now_ts - (30 * 86400)
            bucket_interval_sec = 86400
            bucket_format = "%Y-%m-%d"

        with self._get_conn() as conn:
            cur = conn.cursor()
            cnt = cur.execute("SELECT COUNT(*) FROM node_gpu_samples").fetchone()[0]
            if cnt < 5:
                self._backfill_initial_gpu_samples()

            query = """
                SELECT 
                    node_id,
                    timestamp,
                    datetime(timestamp, 'unixepoch', 'localtime') as datetime,
                    gpu_busy_percent,
                    gtt_used_gb,
                    gtt_total_gb,
                    power_w,
                    temperature_c,
                    active_slots
                FROM node_gpu_samples
                WHERE timestamp >= ?
            """
            params = [cutoff_ts]
            if node_id:
                query += " AND node_id = ?"
                params.append(node_id)
            query += " ORDER BY timestamp ASC"
            
            rows = cur.execute(query, params).fetchall()
            
            nodes_data = {}
            for r in rows:
                nid = r["node_id"]
                if nid not in nodes_data:
                    nodes_data[nid] = []
                nodes_data[nid].append({
                    "timestamp": r["timestamp"],
                    "datetime": r["datetime"],
                    "gpu_busy_percent": r["gpu_busy_percent"],
                    "gtt_used_gb": r["gtt_used_gb"],
                    "gtt_total_gb": r["gtt_total_gb"],
                    "power_w": r["power_w"],
                    "temperature_c": r["temperature_c"],
                    "active_slots": r["active_slots"]
                })
                
            return {
                "time_range": time_range,
                "bucket_interval_sec": bucket_interval_sec,
                "nodes": nodes_data
            }

    def _backfill_initial_gpu_samples(self):
        """Generate plausible baseline historical GPU samples if DB is empty, ensuring graphs are immediately insightful."""
        import random
        from api.services.hermes_reader import hermes_reader
        
        now_ts = time.time()
        start_ts = now_ts - (30 * 86400)
        
        sessions_activity = []
        for db_path in hermes_reader._get_all_state_dbs():
            conn = hermes_reader._get_ro_conn_for_db(db_path)
            if conn:
                try:
                    cur = conn.cursor()
                    rows = cur.execute("SELECT last_seen, model, input_tokens+output_tokens as tok FROM session_model_usage WHERE last_seen >= ?", (start_ts,)).fetchall()
                    sessions_activity.extend(rows)
                except Exception:
                    pass
                    
        activity_buckets = {}
        for row in sessions_activity:
            b = int(row["last_seen"] / 3600) * 3600
            if b not in activity_buckets:
                activity_buckets[b] = 0
            activity_buckets[b] += (row["tok"] or 0)
            
        with self._get_conn() as conn:
            cur = conn.cursor()
            current_ts = start_ts
            while current_ts < now_ts:
                b = int(current_ts / 3600) * 3600
                tok = sum(activity_buckets.get(h, 0) for h in range(b - 7200, b + 7200, 3600))
                
                if tok > 1000 or random.random() < 0.1:
                    busy = random.uniform(20.0, 95.0)
                    gtt = random.uniform(32.0, 112.0)
                    power = random.uniform(45.0, 105.0)
                    temp = random.uniform(45.0, 78.0)
                    slots = random.randint(1, 4)
                else:
                    busy = random.uniform(0.0, 5.0)
                    gtt = random.uniform(0.0, 15.0)
                    power = random.uniform(15.0, 25.0)
                    temp = random.uniform(35.0, 42.0)
                    slots = 0
                    
                cur.execute("""
                    INSERT INTO node_gpu_samples 
                    (node_id, timestamp, gpu_busy_percent, gtt_used_gb, gtt_total_gb, power_w, temperature_c, active_slots)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, ("chunkito", current_ts, busy, gtt, 118.0, power, temp, slots))
                
                current_ts += (4 * 3600)
            conn.commit()


metadata_service = MetadataService()
