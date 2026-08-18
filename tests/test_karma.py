"""Comprehensive test suite for Hermes Karma."""
import unittest
import sqlite3
import os
import json
import time
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient

from api.main import app
from api.services.hermes_reader import hermes_reader
from api.services.metadata_service import MetadataService
from api.services.live_tracker import LiveTracker
from api.services.terminal_focus import terminal_focus_service
from api.plugins.hermes_karma_hook import HermesKarmaHook


class TestHermesKarma(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_metadata.db"
        self.metadata_service = MetadataService(db_path=self.db_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_metadata_service_crud(self):
        meta = self.metadata_service.get_session_meta("test_session_1")
        self.assertEqual(meta["session_id"], "test_session_1")
        self.assertEqual(meta["tags"], [])
        self.assertEqual(meta["notes"], "")

        # Update metadata
        updated = self.metadata_service.update_session_meta(
            session_id="test_session_1",
            tags=["homelab", "neo4j"],
            notes="Investigating systemd service issue",
            starred=True,
        )
        self.assertEqual(updated["tags"], ["homelab", "neo4j"])
        self.assertEqual(updated["notes"], "Investigating systemd service issue")
        self.assertEqual(updated["starred"], 1)

        # Add Ticket Link
        ticket_meta = self.metadata_service.add_ticket_link(
            session_id="test_session_1",
            provider="github",
            ticket_key="#104",
            url="https://github.com/jagosan/HermesKarma/issues/104",
            title="Fix database lock contention",
        )
        self.assertEqual(len(ticket_meta["tickets"]), 1)
        self.assertEqual(ticket_meta["tickets"][0]["ticket_key"], "#104")

        # Remove Ticket Link
        self.metadata_service.remove_ticket_link("test_session_1", "#104")
        after_removal = self.metadata_service.get_session_meta("test_session_1")
        self.assertEqual(len(after_removal["tickets"]), 0)

    def test_api_sessions_list(self):
        response = self.client.get("/api/sessions?limit=5")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("sessions", data)
        self.assertIn("total", data)
        self.assertIsInstance(data["sessions"], list)

    def test_api_analytics_overview(self):
        response = self.client.get("/api/analytics/overview")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("total_sessions", data)
        self.assertIn("model_distribution", data)
        self.assertIn("cache_hit_rate_pct", data)

    def test_api_skills(self):
        response = self.client.get("/api/skills")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("skills", data)
        self.assertIsInstance(data["skills"], list)

    def test_api_memory(self):
        response = self.client.get("/api/memory")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("memory", data)
        self.assertIn("user", data)

    def test_api_cron(self):
        response = self.client.get("/api/cron")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("jobs", data)

    def test_api_live_sessions(self):
        response = self.client.get("/api/live-sessions")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("live_sessions", data)

    def test_terminal_focus_api(self):
        response = self.client.post("/api/live-sessions/mock_sess/focus-terminal", json={
            "pid": 999999,
            "tmux_pane": None,
            "tty": None
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("success", data)

    def test_lifecycle_hook_flow(self):
        hook_dir = Path(self.temp_dir) / "live_sessions"
        hook_dir.mkdir(parents=True, exist_ok=True)
        tracker = LiveTracker(live_dir=hook_dir)

        class MockContext:
            session_id = "test_hook_sess_001"
            title = "Hook Testing Session"
            platform = "cli"
            model = "gemini-3.7-flash"

        ctx = MockContext()
        hook = HermesKarmaHook()
        import api.plugins.hermes_karma_hook as hkh
        hkh.KARMA_LIVE_DIR = hook_dir

        hook.on_session_start(ctx)
        sessions = tracker.get_live_sessions()
        matched = next((s for s in sessions if s["session_id"] == "test_hook_sess_001"), None)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["status"], "LIVE")

        hook.on_tool_start(ctx, "terminal", {"command": "ls -la"})
        sessions = tracker.get_live_sessions()
        matched = next((s for s in sessions if s["session_id"] == "test_hook_sess_001"), None)
        self.assertEqual(matched["status"], "RUNNING_TOOL")
        self.assertEqual(matched["current_tool"], "terminal")

        hook.on_session_end(ctx)
        sessions = tracker.get_live_sessions()
        matched = next((s for s in sessions if s["session_id"] == "test_hook_sess_001"), None)
        self.assertEqual(matched["status"], "ENDED")


if __name__ == "__main__":
    unittest.main()
