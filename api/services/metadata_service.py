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


metadata_service = MetadataService()
