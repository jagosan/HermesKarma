"""Comprehensive test suite for Hermes Karma."""
import unittest
import sqlite3
import os
import json
import time
import hmac
import hashlib
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient

from api.main import app
from api.services.hermes_reader import hermes_reader
from api.services.metadata_service import metadata_service, MetadataService
from api.services.live_tracker import LiveTracker
from api.services.terminal_focus import terminal_focus_service
from api.services.webhook_service import webhook_service
from api.services.node_collector import node_collector, NodeTelemetryCollector
from api.plugins.hermes_karma_hook import HermesKarmaHook
from api.config import KARMA_METADATA_DB, DEFAULT_NODES


class TestHermesKarma(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_metadata.db"
        self.orig_db_path = metadata_service.db_path
        
        # Point global service to temp db for isolated testing
        metadata_service.db_path = self.db_file
        metadata_service._init_db()
        self.metadata_service = metadata_service

    def tearDown(self):
        metadata_service.db_path = self.orig_db_path
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
            status="open",
            assignee="jagosan",
            description="Details about db lock contention",
        )
        self.assertEqual(len(ticket_meta["tickets"]), 1)
        self.assertEqual(ticket_meta["tickets"][0]["ticket_key"], "#104")
        self.assertEqual(ticket_meta["tickets"][0]["status"], "open")

        # Update ticket status by key
        updated_rows = self.metadata_service.update_ticket_status_by_key(
            provider="github",
            ticket_key="#104",
            status="closed",
        )
        self.assertEqual(updated_rows, 1)
        refetched = self.metadata_service.get_session_meta("test_session_1")
        self.assertEqual(refetched["tickets"][0]["status"], "closed")

        # Query tickets by session / query sessions by ticket
        sessions = self.metadata_service.get_sessions_by_ticket("#104")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "test_session_1")

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

    # -----------------------------------------------------------------------
    # TASK-HK-101 Webhook & Ticket Sync Tests
    # -----------------------------------------------------------------------

    def test_github_webhook_issues(self):
        payload = {
            "action": "opened",
            "issue": {
                "number": 105,
                "title": "Fix memory pressure on Ollama node",
                "html_url": "https://github.com/jagosan/HermesKarma/issues/105",
                "state": "open",
                "body": "Referenced in @session:default/20260817_112233_a1b2c3 when debugging chunkito.",
                "assignee": {"login": "jagosan"}
            }
        }
        res = self.client.post(
            "/api/webhooks/github",
            json=payload,
            headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "deliv-gh-101"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ticket_key"], "#105")
        self.assertIn("20260817_112233_a1b2c3", data["linked_sessions"])

        # Check sessions by ticket endpoint
        sess_res = self.client.get("/api/tickets/%23105/sessions")
        self.assertEqual(sess_res.status_code, 200)
        sess_data = sess_res.json()
        self.assertEqual(sess_data["session_count"], 1)
        self.assertEqual(sess_data["links"][0]["session_id"], "20260817_112233_a1b2c3")

    def test_linear_webhook_issue(self):
        payload = {
            "action": "create",
            "type": "Issue",
            "data": {
                "id": "lin_uuid_123",
                "identifier": "ENG-402",
                "title": "Telemetry ingest pipeline latency spike",
                "url": "https://linear.app/team/issue/ENG-402",
                "state": {"name": "In Progress"},
                "description": "Session tracking trace at session_id: 20260817_140000_linear_test",
                "branchName": "feat/ENG-402-telemetry"
            }
        }
        res = self.client.post(
            "/api/webhooks/linear",
            json=payload,
            headers={"Linear-Delivery": "lin-deliv-001"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ticket_key"], "ENG-402")
        self.assertEqual(data["status"], "In Progress")
        self.assertIn("20260817_140000_linear_test", data["linked_sessions"])

    def test_jira_webhook_issue(self):
        payload = {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "PROJ-789",
                "self": "https://jira.company.com/rest/api/2/issue/10001",
                "fields": {
                    "summary": "Implement bi-directional webhook gateway",
                    "status": {"name": "To Do"},
                    "description": "Hermes session linked: hermes:20260817_153000_jira_test",
                    "assignee": {"displayName": "Jago San"}
                }
            }
        }
        res = self.client.post("/api/webhooks/jira", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["ticket_key"], "PROJ-789")
        self.assertEqual(data["status"], "To Do")
        self.assertIn("20260817_153000_jira_test", data["linked_sessions"])

    def test_github_signature_verification(self):
        secret = "super_secret_github_token"
        webhook_service.github_secret = secret
        payload = {"action": "closed", "issue": {"number": 99, "state": "closed"}}
        body_bytes = json.dumps(payload).encode("utf-8")
        
        valid_sig = "sha256=" + hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        
        # Test valid signature
        res_valid = self.client.post(
            "/api/webhooks/github",
            content=body_bytes,
            headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": valid_sig, "Content-Type": "application/json"}
        )
        self.assertEqual(res_valid.status_code, 200)

        # Test invalid signature
        res_invalid = self.client.post(
            "/api/webhooks/github",
            content=body_bytes,
            headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": "sha256=invalid_sig", "Content-Type": "application/json"}
        )
        self.assertEqual(res_invalid.status_code, 401)
        
        # Reset secret
        webhook_service.github_secret = ""

    def test_webhook_status_and_events_api(self):
        # Trigger an event first
        self.client.post(
            "/api/webhooks/github",
            json={"action": "test", "issue": {"number": 1, "title": "Test"}},
            headers={"X-GitHub-Event": "issues"}
        )

        status_res = self.client.get("/api/webhooks/status")
        self.assertEqual(status_res.status_code, 200)
        status_data = status_res.json()
        self.assertIn("endpoints", status_data)
        self.assertIn("stats", status_data)

        events_res = self.client.get("/api/webhooks/events?limit=10")
        self.assertEqual(events_res.status_code, 200)
        events_data = events_res.json()
        self.assertIn("events", events_data)
        self.assertGreaterEqual(events_data["count"], 1)

    def test_ticket_sync_endpoint(self):
        # First link a ticket
        self.client.post("/api/sessions/sess_sync_001/tickets", json={
            "provider": "jira",
            "ticket_key": "PROJ-101",
            "title": "Initial Title",
            "status": "In Progress"
        })

        # Now sync status
        sync_res = self.client.post("/api/tickets/jira/PROJ-101/sync", json={
            "status": "Done",
            "title": "Completed Title"
        })
        self.assertEqual(sync_res.status_code, 200)
        sync_data = sync_res.json()
        self.assertTrue(sync_data["success"])
        self.assertGreaterEqual(sync_data["rows_updated"], 1)

        # Verify through list
        list_res = self.client.get("/api/tickets?search=PROJ-101")
        self.assertEqual(list_res.status_code, 200)
        tickets = list_res.json()["tickets"]
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["status"], "Done")

    # -----------------------------------------------------------------------
    # TASK-HK-103 Multi-Node Telemetry Collector Tests (Chunkito AMD APU)
    # -----------------------------------------------------------------------

    def test_node_collector_local_metrics(self):
        collector = NodeTelemetryCollector()
        cpu = collector._read_local_cpu()
        self.assertIn("cores", cpu)
        self.assertIn("load_1m", cpu)
        self.assertIn("utilization_percent", cpu)

        mem = collector._read_local_meminfo()
        self.assertIn("total_gb", mem)
        self.assertIn("used_gb", mem)
        self.assertIn("free_gb", mem)
        self.assertIn("used_percent", mem)

        disk = collector._read_local_disk()
        self.assertIn("total_gb", disk)
        self.assertIn("used_gb", disk)

    def test_node_collector_remote_mock_and_apu_vram(self):
        collector = NodeTelemetryCollector()

        # Mock query ollama endpoint for chunkito
        async def mock_query(host, port, timeout_sec=2.5):
            return {
                "reachable": True,
                "version": "0.5.4",
                "latency_ms": 1.4,
                "loaded_models": [
                    {
                        "name": "deepseek-v4:latest",
                        "model": "deepseek-v4:latest",
                        "size_bytes": 45 * 1024**3,
                        "size_gb": 45.0,
                        "size_vram_bytes": 45 * 1024**3,
                        "size_vram_gb": 45.0,
                        "parameter_size": "70B",
                        "quantization_level": "Q4_K_M",
                        "format": "gguf",
                        "family": "deepseek2",
                        "expires_at": "2026-08-17T23:59:59Z",
                    }
                ],
                "available_models": [
                    {
                        "name": "deepseek-v4:latest",
                        "size_gb": 45.0,
                        "parameter_size": "70B",
                        "quantization": "Q4_K_M",
                    },
                    {
                        "name": "qwen2.5-coder:32b",
                        "size_gb": 19.5,
                        "parameter_size": "32B",
                        "quantization": "Q4_K_M",
                    }
                ],
                "error": None,
            }

        collector._query_ollama_endpoint = mock_query

        chunkito_node = next(n for n in DEFAULT_NODES if n["id"] == "chunkito")
        import asyncio
        telemetry = asyncio.run(collector.collect_node_telemetry(chunkito_node))

        self.assertEqual(telemetry["node_id"], "chunkito")
        self.assertEqual(telemetry["status"], "online")
        self.assertEqual(telemetry["latency_ms"], 1.4)
        self.assertEqual(telemetry["apu_vram"]["total_gb"], 118.0)
        self.assertEqual(telemetry["apu_vram"]["used_gb"], 45.0)
        self.assertEqual(telemetry["apu_vram"]["free_gb"], 73.0)
        self.assertAlmostEqual(telemetry["apu_vram"]["used_percent"], round(45.0 / 118.0 * 100, 1))

        # Check ollama specs
        self.assertEqual(telemetry["ollama"]["loaded_models_count"], 1)
        self.assertEqual(telemetry["ollama"]["max_loaded_models"], 1)
        self.assertFalse(telemetry["ollama"]["overloaded"])
        self.assertEqual(len(telemetry["ollama"]["loaded_models"]), 1)
        self.assertEqual(telemetry["ollama"]["loaded_models"][0]["parameter_size"], "70B")

    def test_node_collector_overloaded_alert(self):
        collector = NodeTelemetryCollector()

        # Simulate 2 models loaded concurrently when max_loaded_models=1
        async def mock_query_overloaded(host, port, timeout_sec=2.5):
            return {
                "reachable": True,
                "version": "0.5.4",
                "latency_ms": 2.1,
                "loaded_models": [
                    {
                        "name": "deepseek-v4:latest",
                        "size_vram_gb": 45.0,
                        "parameter_size": "70B",
                    },
                    {
                        "name": "qwen2.5-coder:32b",
                        "size_vram_gb": 19.5,
                        "parameter_size": "32B",
                    }
                ],
                "available_models": [],
                "error": None,
            }

        collector._query_ollama_endpoint = mock_query_overloaded
        chunkito_node = next(n for n in DEFAULT_NODES if n["id"] == "chunkito")
        import asyncio
        telemetry = asyncio.run(collector.collect_node_telemetry(chunkito_node))

        self.assertEqual(telemetry["status"], "warning")
        self.assertTrue(telemetry["ollama"]["overloaded"])
        self.assertEqual(telemetry["ollama"]["loaded_models_count"], 2)
        self.assertTrue(any("Memory Safety Alert" in alert for alert in telemetry["alerts"]))

    def test_node_collector_unreachable_node(self):
        collector = NodeTelemetryCollector()

        async def mock_query_down(host, port, timeout_sec=2.5):
            return {
                "reachable": False,
                "version": None,
                "latency_ms": None,
                "loaded_models": [],
                "available_models": [],
                "error": "ConnectTimeout",
            }

        collector._query_ollama_endpoint = mock_query_down
        chunkito_node = next(n for n in DEFAULT_NODES if n["id"] == "chunkito")
        import asyncio
        telemetry = asyncio.run(collector.collect_node_telemetry(chunkito_node))

        self.assertEqual(telemetry["status"], "offline")
        self.assertIsNone(telemetry["latency_ms"])
        self.assertEqual(telemetry["ollama"]["loaded_models_count"], 0)
        self.assertTrue(any("unreachable" in alert for alert in telemetry["alerts"]))

    def test_api_nodes_endpoints(self):
        # 1. GET /api/nodes
        res = self.client.get("/api/nodes")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("summary", data)
        self.assertIn("nodes", data)
        self.assertGreaterEqual(data["summary"]["total_nodes"], 2)
        self.assertIn("total_vram_gb", data["summary"])

        # 2. GET /api/nodes/beehive
        beehive_res = self.client.get("/api/nodes/beehive")
        self.assertEqual(beehive_res.status_code, 200)
        beehive_data = beehive_res.json()
        self.assertEqual(beehive_data["node_id"], "beehive")
        self.assertTrue(beehive_data["is_local"])

        # 3. GET /api/nodes/chunkito
        chunkito_res = self.client.get("/api/nodes/chunkito")
        self.assertEqual(chunkito_res.status_code, 200)
        chunkito_data = chunkito_res.json()
        self.assertEqual(chunkito_data["node_id"], "chunkito")
        self.assertEqual(chunkito_data["hardware"]["vram_gb"], 118)

        # 4. GET /api/nodes/chunkito/models
        models_res = self.client.get("/api/nodes/chunkito/models")
        self.assertEqual(models_res.status_code, 200)
        models_data = models_res.json()
        self.assertEqual(models_data["node_id"], "chunkito")
        self.assertIn("loaded_models", models_data)
        self.assertIn("available_models", models_data)

        # 5. GET /api/nodes/nonexistent_node -> 404
        not_found_res = self.client.get("/api/nodes/nonexistent_node")
        self.assertEqual(not_found_res.status_code, 404)

        # 6. POST /api/nodes/refresh
        refresh_res = self.client.post("/api/nodes/refresh")
        self.assertEqual(refresh_res.status_code, 200)
        self.assertTrue(refresh_res.json()["success"])

        # 7. POST /api/nodes/beehive/refresh
        refresh_single_res = self.client.post("/api/nodes/beehive/refresh")
        self.assertEqual(refresh_single_res.status_code, 200)
        self.assertTrue(refresh_single_res.json()["success"])

    def test_llama_server_inference_probing_and_slots(self):
        collector = NodeTelemetryCollector()

        async def mock_llama_server_query(host, port, timeout_sec=2.5):
            return {
                "reachable": True,
                "backend_type": "llama.cpp (llama-server)",
                "version": "llama-server (ROCm/Vulkan APU)",
                "latency_ms": 1.2,
                "loaded_models": [
                    {
                        "name": "qwen3.8-flash-next:262k",
                        "model": "qwen3.8-flash-next:262k",
                        "size_bytes": 93671559680,
                        "size_gb": 87.24,
                        "size_vram_gb": 87.24,
                        "parameter_size": "176.9B",
                        "quantization_level": "IQ4_XS - 4.25 bpw",
                        "format": "gguf",
                        "context_length": 262144,
                        "embedding_dim": 2560,
                        "family": "qwen",
                        "status": "resident_in_vram",
                    }
                ],
                "available_models": [
                    {
                        "name": "qwen3.8-flash-next:262k",
                        "size_gb": 87.24,
                        "parameter_size": "176.9B",
                        "quantization": "IQ4_XS - 4.25 bpw",
                        "context_length": 262144,
                    }
                ],
                "slots": [
                    {
                        "id": 0,
                        "n_ctx": 262144,
                        "is_processing": True,
                        "id_task": 31042,
                        "n_prompt_tokens_processed": 1420,
                        "n_prompt_tokens_cache": 500,
                    },
                    {
                        "id": 1,
                        "n_ctx": 262144,
                        "is_processing": False,
                        "id_task": 26429,
                        "n_prompt_tokens_processed": 0,
                        "n_prompt_tokens_cache": 0,
                    }
                ],
                "slot_summary": {
                    "total_slots": 2,
                    "active_slots": 1,
                    "idle_slots": 1,
                    "total_prompt_tokens_processed": 1420,
                    "total_prompt_tokens_cache": 500,
                    "active_tasks": [31042],
                },
                "error": None,
            }

        collector._query_ollama_endpoint = mock_llama_server_query
        chunkito_node = next(n for n in DEFAULT_NODES if n["id"] == "chunkito")
        import asyncio
        telemetry = asyncio.run(collector.collect_node_telemetry(chunkito_node))

        self.assertEqual(telemetry["status"], "online")
        self.assertEqual(telemetry["inference_engine"]["backend_type"], "llama.cpp (llama-server)")
        self.assertEqual(telemetry["inference_engine"]["loaded_models_count"], 1)
        self.assertEqual(telemetry["inference_engine"]["loaded_models"][0]["parameter_size"], "176.9B")
        self.assertEqual(telemetry["inference_engine"]["slot_summary"]["active_slots"], 1)
        self.assertEqual(telemetry["inference_engine"]["slot_summary"]["total_prompt_tokens_cache"], 500)
        self.assertEqual(telemetry["apu_vram"]["used_gb"], 87.24)
        self.assertAlmostEqual(telemetry["apu_vram"]["free_gb"], 118.0 - 87.24, places=1)

    def test_local_btop_and_amdgpu_metrics(self):
        collector = NodeTelemetryCollector()
        amdgpu = collector._read_local_amdgpu()
        self.assertIn("available", amdgpu)
        self.assertIn("vram_total_gb", amdgpu)
        self.assertIn("gpu_busy_percent", amdgpu)

        net = collector._read_local_network()
        self.assertIn("interfaces", net)

        mem = collector._read_local_meminfo()
        self.assertIn("cached_gb", mem)
        self.assertIn("available_gb", mem)

    def test_sessions_models_catalog_endpoint(self):
        res = self.client.get("/api/sessions/models/catalog")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("models", data)
        self.assertIsInstance(data["models"], list)
        if data["models"]:
            self.assertIn("name", data["models"][0])
            self.assertIn("provider", data["models"][0])
            self.assertIn("is_local", data["models"][0])

    def test_analytics_multi_model_precision(self):
        res = self.client.get("/api/analytics/overview")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("provider_distribution", data)
        self.assertIn("local_zero_cost_ratio_pct", data)
        self.assertIn("total_reasoning_tokens", data)
        self.assertIn("total_api_calls", data)

    def test_analytics_time_range_filtering(self):
        for tr in ["today", "7d", "30d", "all"]:
            res = self.client.get(f"/api/analytics/overview?time_range={tr}")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data.get("time_range"), tr)
            self.assertIn("model_distribution", data)
            self.assertIn("total_input_tokens", data)


if __name__ == "__main__":
    unittest.main()
