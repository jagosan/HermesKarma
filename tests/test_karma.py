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
from api.services.hermes_reader import hermes_reader, model_throughput_profile
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
        assert matched is not None
        self.assertEqual(matched["status"], "LIVE")

        hook.on_tool_start(ctx, "terminal", {"command": "ls -la"})
        sessions = tracker.get_live_sessions()
        matched = next((s for s in sessions if s["session_id"] == "test_hook_sess_001"), None)
        assert matched is not None
        self.assertEqual(matched["status"], "RUNNING_TOOL")
        self.assertEqual(matched["current_tool"], "terminal")

        hook.on_session_end(ctx)
        sessions = tracker.get_live_sessions()
        matched = next((s for s in sessions if s["session_id"] == "test_hook_sess_001"), None)
        assert matched is not None
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

    def test_pantheon_profiles_endpoint(self):
        res = self.client.get("/api/pantheon/profiles")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("summary", data)
        self.assertIn("profiles", data)
        self.assertGreaterEqual(data["summary"]["total_agents"], 10)
        
        profile_ids = [p["id"] for p in data["profiles"]]
        for required_id in ["owl", "rabbit", "tigger", "jagular", "piglet", "eeyore", "pooh", "coder", "ingest", "heffalump"]:
            self.assertIn(required_id, profile_ids)
            
        owl = next(p for p in data["profiles"] if p["id"] == "owl")
        self.assertEqual(owl["emoji"], "🦉")
        self.assertEqual(owl["name"], "Owl")
        self.assertIn("model", owl)

        jagular = next(p for p in data["profiles"] if p["id"] == "jagular")
        self.assertEqual(jagular["emoji"], "🐆")
        self.assertEqual(jagular["name"], "Jagular")
        self.assertEqual(jagular["model"], "qwen3.8-flash-next:262k")

        heffalump = next(p for p in data["profiles"] if p["id"] == "heffalump")
        self.assertEqual(heffalump["emoji"], "🐘")
        self.assertEqual(heffalump["name"], "Heffalump")
        self.assertEqual(heffalump["model"], "deepseek-v4:262k")

        tigger = next(p for p in data["profiles"] if p["id"] == "tigger")
        self.assertEqual(tigger["emoji"], "🐯")
        self.assertEqual(tigger["name"], "Tigger")
        self.assertIn(tigger["model"], ["qwen3-coder-next:262k", "qwen3.8-27b"])

    def test_pantheon_profile_detail_endpoint(self):
        res = self.client.get("/api/pantheon/profiles/owl")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["id"], "owl")
        self.assertIn("soul_raw", data)
        self.assertIn("skills", data)
        self.assertIn("sessions", data)
        self.assertTrue(len(data["soul_raw"]) > 0)

        # Test Jagular detail endpoint
        res_jag = self.client.get("/api/pantheon/profiles/jagular")
        self.assertEqual(res_jag.status_code, 200)
        data_jag = res_jag.json()
        self.assertEqual(data_jag["id"], "jagular")
        self.assertEqual(data_jag["name"], "Jagular")
        self.assertEqual(data_jag["emoji"], "🐆")
        self.assertEqual(data_jag["model"], "qwen3.8-flash-next:262k")
        self.assertTrue(len(data_jag["soul_raw"]) > 0)

        # Test Heffalump detail endpoint
        res_hef = self.client.get("/api/pantheon/profiles/heffalump")
        self.assertEqual(res_hef.status_code, 200)
        data_hef = res_hef.json()
        self.assertEqual(data_hef["id"], "heffalump")
        self.assertEqual(data_hef["name"], "Heffalump")
        self.assertEqual(data_hef["emoji"], "🐘")
        self.assertEqual(data_hef["model"], "deepseek-v4:262k")
        self.assertTrue(len(data_hef["soul_raw"]) > 0)
        
        # Test 404 for unknown profile
        res_404 = self.client.get("/api/pantheon/profiles/nonexistent_agent_xyz")
        self.assertEqual(res_404.status_code, 404)

    def test_persona_attribution_and_subagents(self):
        """Test attribution helper and sessions persona filtering."""
        # Subagent with qwen3.8-27b on chunkito -> Tigger
        tigger_sub = {
            "title": "Implement Vocabulous endpoints",
            "source": "subagent",
            "model": "qwen3.8-27b",
            "billing_provider": "chunkito"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(tigger_sub), "tigger")

        # Subagent with qwen3.8-flash-next on chunkito -> Jagular
        jagular_sub = {
            "title": "Whole-repo architectural forensics",
            "source": "subagent",
            "model": "qwen3.8-flash-next:262k",
            "billing_provider": "chunkito"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(jagular_sub), "jagular")

        # Explicit mention overrides model
        jagular_named = {
            "title": "Summoning @jagular for deep hunt",
            "source": "subagent",
            "model": "qwen3.8-27b",
            "billing_provider": "chunkito"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(jagular_named), "jagular")

        # Top-level session with pantheon-swarm command -> Owl
        owl_swarm = {
            "title": "[Workspace::v1: /workspace/playground]",
            "source": "webui",
            "model": "gemini-3.8-flash",
            "first_prompt": "/pantheon-swarm specs/01-playground-lobby.md"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(owl_swarm), "owl")

        # Subagent for Rabbit delegation -> Rabbit
        rabbit_sub = {
            "title": "@rabbit: Decompose specs/01-playground-lobby.md into Kanban tasks",
            "source": "subagent",
            "model": "hf.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:UD-IQ2_M"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(rabbit_sub), "rabbit")

        # Subagent with ERNIE -> Eeyore
        eeyore_sub = {
            "title": "Security audit",
            "source": "subagent",
            "model": "hf.co/unsloth/ERNIE-4.5-21B-A3B-Thinking-GGUF:Q4_K_M"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(eeyore_sub), "eeyore")

        # Subagent with deepseek-v4 on chunkito -> Heffalump
        heffalump_sub = {
            "title": "Massive context ingestion and math reasoning",
            "source": "subagent",
            "model": "deepseek-v4:262k",
            "billing_provider": "chunkito"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(heffalump_sub), "heffalump")

        # Explicit mention @heffalump
        heffalump_named = {
            "title": "Summoning @heffalump for 284B MoE synthesis",
            "source": "subagent",
            "model": "deepseek-v4"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(heffalump_named), "heffalump")

        # Subagent with vault keyword -> Pooh
        pooh_sub = {
            "title": "Obsidian vault runbook gardening",
            "source": "subagent",
            "model": "qwen3.8-flash-next:262k"
        }
        self.assertEqual(hermes_reader.attribute_session_persona(pooh_sub), "pooh")

        # Test API filtering by persona
        res = self.client.get("/api/sessions?persona=tigger")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("sessions", data)
        for s in data["sessions"]:
            self.assertEqual(s.get("persona_id"), "tigger")

    def test_pricing_engine_gemini_rates(self):
        """Verify published rates for Gemini 3.8 and 3.7 Flash and context caching."""
        from api.services.pricing_engine import pricing_engine

        # 1M input ($0.75) + 1M output ($3.75) + 1M cache read ($0.075) = $4.575
        cost = pricing_engine.calculate_cost(
            model_name="gemini-3.8-flash",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
        )
        self.assertAlmostEqual(cost, 4.575, places=4)

        # Gemini 3.7 Flash shares same rate
        cost_37 = pricing_engine.calculate_cost(
            model_name="gemini-3.7-flash",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=1_000_000,
        )
        self.assertAlmostEqual(cost_37, 4.575, places=4)

    def test_pricing_engine_local_apu_zero_cost(self):
        """Verify local APU hardware models strictly evaluate to $0.00."""
        from api.services.pricing_engine import pricing_engine

        for local_model in [
            "qwen3-coder-next:q8_0",
            "qwen3.8-flash-next:262k",
            "deepseek-v4:96k",
            "hf.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:UD-IQ2_M",
            "gemma4:12b",
            "gpt-oss:20b",
        ]:
            self.assertTrue(pricing_engine.is_local_model(local_model))
            cost = pricing_engine.calculate_cost(local_model, 10_000_000, 1_000_000)
            self.assertEqual(cost, 0.0)

            rec = pricing_engine.reconcile_usage(local_model, 10_000_000, 1_000_000, stored_cost_usd=0.0)
            self.assertEqual(rec.cost_usd, 0.0)
            self.assertTrue(rec.is_local)
            self.assertFalse(rec.is_reconciled)

    def test_pricing_engine_reconciliation_of_zero_cost_cloud(self):
        """Verify cloud models with 0.0 stored cost are reconciled to honest pricing."""
        from api.services.pricing_engine import pricing_engine

        # Historical WAL row with 0.0 stored cost
        rec = pricing_engine.reconcile_usage(
            model_name="gemini-3.7-flash",
            input_tokens=74_894_555,
            output_tokens=1_847_551,
            cache_read_tokens=519_248_816,
            stored_cost_usd=0.0,
            billing_provider="gemini",
        )
        self.assertTrue(rec.is_reconciled)
        self.assertFalse(rec.is_local)
        self.assertGreater(rec.cost_usd, 90.0)
        self.assertLess(rec.cost_usd, 110.0)

    def test_analytics_overview_spend_cap_metrics(self):
        """Verify analytics overview returns spend cap metrics and reconciled totals."""
        res = self.client.get("/api/analytics/overview?time_range=all")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("spend_cap_usd", data)
        self.assertEqual(data["spend_cap_usd"], 250.0)
        self.assertIn("current_month_cost_usd", data)
        self.assertIn("spend_cap_pct", data)
        self.assertIn("raw_stored_cost_usd", data)
        self.assertIn("reconciled_delta_usd", data)
        self.assertGreater(data["total_estimated_cost_usd"], 200.0)

    def test_analytics_overview_model_turns_and_throughput(self):
        """TASK-HK-105: every model row carries turns_count, output_tokens and throughput metadata."""
        res = self.client.get("/api/analytics/overview?time_range=all")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        models = data.get("model_distribution") or []
        self.assertGreater(len(models), 0)
        for m in models:
            self.assertIn("turns_count", m)
            self.assertIsInstance(m["turns_count"], int)
            self.assertGreaterEqual(m["turns_count"], 0)
            self.assertIn("output_tokens", m)
            self.assertIsInstance(m["output_tokens"], int)
            self.assertIn("throughput_tok_per_sec", m)
            self.assertIsInstance(m["throughput_tok_per_sec"], float)
            self.assertGreater(m["throughput_tok_per_sec"], 0.0)
            self.assertIn(m["throughput_tier"], ("ultra", "fast", "standard", "local_apu"))

    def test_analytics_overview_inference_velocity_summary(self):
        """TASK-HK-105: overview exposes the inference_velocity_summary block."""
        res = self.client.get("/api/analytics/overview?time_range=all")
        self.assertEqual(res.status_code, 200)
        summary = res.json().get("inference_velocity_summary")
        self.assertIsInstance(summary, dict)
        self.assertGreater(summary["apu_decode_tok_per_sec"], 0.0)
        self.assertGreater(summary["apu_prefill_tok_per_sec"], 0.0)
        self.assertEqual(summary["cloud_decode_tok_per_sec"], 145.0)
        self.assertIn("local_zero_cost_ratio_pct", summary)
        self.assertGreaterEqual(summary["local_zero_cost_ratio_pct"], 0.0)
        self.assertLessEqual(summary["local_zero_cost_ratio_pct"], 100.0)
        self.assertIsInstance(summary["fastest_model"], str)
        self.assertIsInstance(summary["longest_context_local_model"], str)

    def test_model_throughput_profile_baselines(self):
        """TASK-HK-105: empirical throughput baselines per pantheon roster model."""
        expectations = {
            "gemini-3.8-flash": (150.0, "ultra"),
            "gemini-3.7-flash": (135.0, "fast"),
            "gemini-3.5-flash": (110.0, "fast"),
            "gemini-3.1-pro-preview": (62.0, "standard"),
            "claude-3-opus": (45.0, "standard"),
            "qwen3.8-flash-next:262k": (35.0, "local_apu"),
            "qwen3-coder-next:262k": (15.5, "local_apu"),
            "qwen3.8-27b": (28.0, "local_apu"),
            "qwen2.5:14b": (41.5, "local_apu"),
            "jagular": (35.0, "local_apu"),
            "glm4:9b": (18.0, "local_apu"),
            "ernie-fixed": (20.0, "local_apu"),
            "qwen3.5:latest": (22.0, "local_apu"),
        }
        for name, (tok, tier) in expectations.items():
            got = model_throughput_profile(name)
            self.assertEqual(got, (tok, tier), f"throughput mismatch for {name}")
        # Unknown cloud vs local fallbacks
        self.assertEqual(model_throughput_profile("totally-unknown-cloud", is_local=False), (80.0, "standard"))
        self.assertEqual(model_throughput_profile("totally unknown local", is_local=True), (25.0, "local_apu"))

    def test_nodes_cluster_telltales(self):
        """Verify node collector emits cluster_telltales for instrument gauges."""
        res = self.client.get("/api/nodes")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("nodes", data)
        for node in data["nodes"]:
            self.assertIn("amdgpu", node)
            self.assertIn("cluster_telltales", node)
            tt = node["cluster_telltales"]
            self.assertIn("gear", tt)
            self.assertIn("active_slots", tt)
            self.assertIn("is_generating", tt)

    def test_nodes_gpu_history(self):
        """SPEC-HK-006: Verify GET /api/nodes/history timeseries endpoint."""
        res = self.client.get("/api/nodes/history?time_range=30d")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["time_range"], "30d")
        self.assertIn("nodes", data)
        # Should have generated backfill data for both chunkito and beehive
        self.assertTrue(len(data["nodes"].get("chunkito", [])) > 0)
        sample = data["nodes"]["chunkito"][0]
        self.assertIn("timestamp", sample)
        self.assertIn("gpu_busy_percent", sample)
        self.assertIn("power_w", sample)
        self.assertIn("active_slots", sample)

        # Beehive coordinator GPU history tracking
        self.assertIn("beehive", data["nodes"])
        self.assertTrue(len(data["nodes"].get("beehive", [])) > 0)
        b_sample = data["nodes"]["beehive"][0]
        self.assertIn("timestamp", b_sample)
        self.assertIn("gpu_busy_percent", b_sample)

    def test_analytics_temporal_usage(self):
        """SPEC-HK-006: Verify temporal_usage structure in analytics overview."""
        res = self.client.get("/api/analytics/overview?time_range=30d")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("temporal_usage", data)
        tu = data["temporal_usage"]
        self.assertEqual(tu["time_range"], "30d")
        self.assertEqual(tu["bucket_type"], "day")
        self.assertTrue(isinstance(tu["buckets"], list))
        self.assertTrue(isinstance(tu["models"], list))
        self.assertIn("series", tu)
        self.assertIn("input_tokens", tu["series"])
        self.assertIn("output_tokens", tu["series"])
        self.assertIn("total_tokens", tu["series"])
        self.assertIn("cost_usd", tu["series"])

        # Verify month time range starts on 1st of month
        res_m = self.client.get("/api/analytics/overview?time_range=month")
        self.assertEqual(res_m.status_code, 200)
        tu_m = res_m.json()["temporal_usage"]
        import datetime
        now = datetime.datetime.now()
        expected_prefix = now.strftime("%Y-%m-01")
        self.assertTrue(tu_m["buckets"][0].startswith(expected_prefix))

        # Verify today time range has hour buckets
        res_t = self.client.get("/api/analytics/overview?time_range=today")
        self.assertEqual(res_t.status_code, 200)
        tu_t = res_t.json()["temporal_usage"]
        self.assertEqual(tu_t["bucket_type"], "hour")
        self.assertEqual(len(tu_t["buckets"]), 25)

        # Verify all time range spans historical data
        res_a = self.client.get("/api/analytics/overview?time_range=all")
        self.assertEqual(res_a.status_code, 200)
        tu_a = res_a.json()["temporal_usage"]
        self.assertGreaterEqual(len(tu_a["buckets"]), 30)

    # ------------------------------------------------------------------
    # SPEC-HK-007 Phase 2: Context Tax API, Skill ROI auditor, loop guardrails
    # ------------------------------------------------------------------

    def test_api_analytics_context_tax(self):
        """GET /api/analytics/context-tax matches the blueprint schema."""
        res = self.client.get("/api/analytics/context-tax?days=30")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["window_days"], 30)
        for k in ("cold_start_tokens", "spatial_index_tokens",
                  "exploration_waste_tokens", "productive_tokens",
                  "amnesia_reread_tokens"):
            self.assertIn(k, data["token_breakdown"])
        roi = data["spatial_roi"]
        for k in ("mapped_sessions_count", "unmapped_sessions_count",
                  "avg_turns_to_first_edit_mapped", "avg_turns_to_first_edit_unmapped",
                  "avg_read_to_edit_ratio_mapped", "avg_read_to_edit_ratio_unmapped",
                  "net_exploration_tokens_saved", "counterfactual_spatial_savings_usd",
                  "map_drift"):
            self.assertIn(k, roi)
        for k in ("is_stale", "map_last_updated_iso", "commits_since_map_update",
                  "unindexed_files"):
            self.assertIn(k, roi["map_drift"])
        for k in ("total_compactions", "avg_compression_ratio",
                  "total_tokens_reclaimed", "total_amnesia_reread_tokens"):
            self.assertIn(k, data["compaction_stats"])
        self.assertIsInstance(data["top_thrash_files"], list)
        for ch in ("cli", "webui", "telegram", "cron", "pantheon_subagent"):
            self.assertIn(ch, data["ingress_breakdown"])

    def test_api_session_detail_surfaces_context_audit(self):
        """GET /api/sessions/{id} appends the SPEC-HK-007 context_audit object."""
        lst = self.client.get("/api/sessions?limit=1").json()
        sessions = lst.get("sessions") or []
        if not sessions:
            self.skipTest("no sessions in state.db")
        sid = sessions[0]["session_id"]
        detail = self.client.get(f"/api/sessions/{sid}").json()
        self.assertIn("context_audit", detail)
        ca = detail["context_audit"]
        for k in ("ingress_channel", "used_map_md", "used_graphify",
                  "turns_to_first_edit", "read_to_edit_ratio",
                  "cold_start_tokens", "spatial_index_tokens",
                  "exploration_waste_tokens", "productive_tokens",
                  "compaction_events", "amnesia_reread_tokens",
                  "untouched_files_read"):
            self.assertIn(k, ca)
        # Internal aggregation keys must not leak into the API payload.
        self.assertFalse(any(k.startswith("_") for k in ca.keys()))

    def test_api_skills_roi_endpoint(self):
        """GET /api/skills/roi returns the dead-skill audit; list is enriched."""
        res = self.client.get("/api/skills/roi?days=30")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["window_days"], 30)
        self.assertIn("totals", data)
        self.assertIn("dead_skills", data)
        self.assertIsInstance(data["skills"], list)
        for sk in data["skills"]:
            for k in ("name", "skill_token_weight", "router_description_tokens",
                      "body_tokens", "invocations", "invoking_sessions",
                      "skill_error_rate", "is_dormant_dead"):
                self.assertIn(k, sk)
            self.assertGreater(sk["skill_token_weight"], 0)
            self.assertEqual(
                sk["skill_token_weight"],
                sk["router_description_tokens"] + sk["body_tokens"],
            )
        # Existing list endpoint gains footprint + utility fields.
        lst = self.client.get("/api/skills")
        self.assertEqual(lst.status_code, 200)
        for sk in lst.json()["skills"]:
            self.assertIn("skill_token_weight", sk)
            self.assertIn("is_dormant_dead", sk)
            self.assertIn("invocations_30d", sk)

    def test_skill_roi_dead_skill_detection(self):
        """Auditor flags 0-invocation skills dead and correlates session errors."""
        skills_root = Path(self.temp_dir) / "skills"
        (skills_root / "alpha-skill").mkdir(parents=True)
        (skills_root / "alpha-skill" / "SKILL.md").write_text(
            "---\nname: alpha-skill\ndescription: Alpha does things\n---\n"
            + "Alpha body instructions for the agent go here. " * 20,
            encoding="utf-8",
        )
        (skills_root / "dead-skill").mkdir(parents=True)
        (skills_root / "dead-skill" / "SKILL.md").write_text(
            "---\nname: dead-skill\ndescription: Nobody uses me\n---\nDead body.\n",
            encoding="utf-8",
        )

        db_file = Path(self.temp_dir) / "roi_state.db"
        conn = sqlite3.connect(db_file)
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT)")
        now = time.time()
        conn.execute("INSERT INTO sessions VALUES ('roi_sess_1', ?, 1000, 100)", (now - 60,))
        calls = json.dumps([
            {"id": "c1", "function": {"name": "skill_view", "arguments": json.dumps({"name": "alpha-skill"})}},
            {"id": "c2", "function": {"name": "read_file", "arguments": json.dumps({"path": "/repo/x.py"})}},
            {"id": "c3", "function": {"name": "terminal", "arguments": json.dumps({"command": "ls"})}},
        ])
        conn.execute("INSERT INTO messages (session_id, role, content, tool_calls) VALUES ('roi_sess_1', 'assistant', '', ?)", (calls,))
        conn.execute("INSERT INTO messages VALUES (NULL, 'roi_sess_1', 'tool', 'file contents here', NULL)")
        conn.execute("INSERT INTO messages VALUES (NULL, 'roi_sess_1', 'tool', 'Error: no such file', NULL)")
        conn.commit()
        conn.close()

        import api.services.hermes_reader as hr_mod
        orig_dir = hr_mod.HERMES_SKILLS_DIR
        orig_dbs = hr_mod.HermesReader._get_all_state_dbs
        hermes_reader.invalidate_skill_roi_cache()
        try:
            hr_mod.HERMES_SKILLS_DIR = skills_root
            hr_mod.HermesReader._get_all_state_dbs = lambda self: [db_file]
            report = hermes_reader.get_skill_roi_audit(days=30)
        finally:
            hr_mod.HERMES_SKILLS_DIR = orig_dir
            hr_mod.HermesReader._get_all_state_dbs = orig_dbs
            hermes_reader.invalidate_skill_roi_cache()

        by_name = {s["name"]: s for s in report["skills"]}
        self.assertIn("alpha-skill", by_name)
        self.assertIn("dead-skill", by_name)
        self.assertTrue(by_name["dead-skill"]["is_dormant_dead"])
        self.assertEqual(by_name["dead-skill"]["invocations"], 0)
        self.assertIn("dead-skill", report["dead_skills"])
        self.assertFalse(by_name["alpha-skill"]["is_dormant_dead"])
        self.assertEqual(by_name["alpha-skill"]["invocations"], 1)
        self.assertEqual(by_name["alpha-skill"]["invoking_sessions"], 1)
        # 3 tool calls, 1 failed tool output => 1/3 error correlation.
        self.assertEqual(by_name["alpha-skill"]["skill_session_total_tool_calls"], 3)
        self.assertAlmostEqual(by_name["alpha-skill"]["skill_error_rate"], round(1 / 3, 4))
        self.assertGreater(by_name["alpha-skill"]["skill_token_weight"],
                           by_name["dead-skill"]["skill_token_weight"])
        self.assertEqual(report["totals"]["installed_skills"], 2)
        self.assertEqual(report["totals"]["dormant_dead_count"], 1)

    def test_runaway_loop_guardrail_thresholds(self):
        """SPEC-HK-007 §3.6.2 circuit breaker trips on the three thresholds."""
        tracker = LiveTracker(live_dir=Path(self.temp_dir) / "live")
        sessions = [
            {  # cron loop stuck erroring + thrashing
                "session_id": "runaway_1", "platform": "cron",
                "consecutive_tool_errors": 6, "turns": 12,
                "files_read": [f"/x/{i}.py" for i in range(10)],
                "files_edited": ["/x/0.py"],
            },
            {  # pantheon subagent exploring blindly (100% unedited reads)
                "session_id": "thrash_1", "ingress_channel": "pantheon_subagent",
                "is_subagent": True, "turns": 15,
                "files_read": [f"/y/{i}.py" for i in range(10)],
                "files_edited": [],
            },
            {  # cloud cost ceiling breach (100M input tokens)
                "session_id": "cost_1", "platform": "cron",
                "model": "gemini-3.8-flash",
                "total_prompt_tokens": 100_000_000, "total_output_tokens": 1_000_000,
            },
            {  # interactive human channel: never circuit-broken
                "session_id": "human_1", "platform": "telegram",
                "consecutive_tool_errors": 9, "turns": 40,
                "files_read": ["/a.py"], "files_edited": [],
            },
            {  # healthy subagent
                "session_id": "ok_1", "ingress_channel": "pantheon_subagent",
                "is_subagent": True, "turns": 4,
                "files_read": ["/b.py"], "files_edited": ["/b.py"],
            },
        ]
        violations = tracker.evaluate_loop_guardrails(sessions, send=False)
        by_sid = {}
        for v in violations:
            by_sid.setdefault(v["session_id"], set()).add(v["alert_type"])
        self.assertIn("consecutive_tool_errors", by_sid.get("runaway_1", set()))
        self.assertIn("exploration_thrash", by_sid.get("runaway_1", set()))
        self.assertEqual(by_sid.get("thrash_1"), {"exploration_thrash"})
        self.assertEqual(by_sid.get("cost_1"), {"cloud_cost_ceiling"})
        self.assertNotIn("human_1", by_sid)
        self.assertNotIn("ok_1", by_sid)

    def test_runaway_loop_alert_dispatch_and_cooldown(self):
        """Alerts fire once per (session, type) then cool down; send_alert never raises."""
        from api.services import webhook_service as ws_mod
        tracker = LiveTracker(live_dir=Path(self.temp_dir) / "live2")
        captured = []
        orig_send = ws_mod.webhook_service.send_alert

        def fake_send(alert_type, session_id=None, detail=None, payload=None):
            captured.append((alert_type, session_id))
            return {"sent": True}

        try:
            ws_mod.webhook_service.send_alert = fake_send
            sessions = [{
                "session_id": "loop_9", "platform": "cron",
                "consecutive_tool_errors": 7, "turns": 1,
            }]
            v1 = tracker.evaluate_loop_guardrails(sessions)
            self.assertEqual(len(captured), 1)
            self.assertIs(v1[0]["alerted"], True)
            # Second evaluation within cooldown window must not re-alert.
            v2 = tracker.evaluate_loop_guardrails(sessions)
            self.assertEqual(len(captured), 1)
            self.assertEqual(v2[0]["alerted"], "suppressed_cooldown")
        finally:
            ws_mod.webhook_service.send_alert = orig_send

        # No webhook URL configured: observable, silent, never raises.
        res = webhook_service.send_alert("test_alert", session_id="x", detail="d")
        self.assertFalse(res["sent"])
        self.assertEqual(res["reason"], "alert_webhook_not_configured")


if __name__ == "__main__":
    unittest.main()
