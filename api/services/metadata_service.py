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
                created_at REAL,
                UNIQUE(session_id, provider, ticket_key)
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
    ) -> Dict[str, Any]:
        now = time.time()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT OR REPLACE INTO ticket_links (session_id, provider, ticket_key, url, title, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, provider, ticket_key, url, title, now))
            conn.commit()
        return self.get_session_meta(session_id)

    def remove_ticket_link(self, session_id: str, ticket_key: str):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM ticket_links WHERE session_id = ? AND ticket_key = ?", (session_id, ticket_key))
            conn.commit()

    def get_all_tickets(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM ticket_links ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]


metadata_service = MetadataService()
