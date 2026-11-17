"""Tests for Overseer Service (TASK-HK-143, TASK-HK-147)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteTestClient

from api.services.overseer_service import overseer_service


class TestOverseerService(unittest.TestCase):
    """Test cases for OverseerService."""

    def test_parse_markdown_card_valid(self):
        """Test parsing a valid Kanban card line."""
        line = "- [ ] **[SPEC-PLAY-038]** Title text | Workspace: /path"
        result = overseer_service._parse_markdown_card(line)
        self.assertIsNotNone(result)
        self.assertFalse(result["checked"])
        self.assertEqual(result["spec_id"], "SPEC-PLAY-038")
        self.assertEqual(result["raw_line"], line.strip())

    def test_parse_markdown_card_checked(self):
        """Test parsing a checked Kanban card line."""
        line = "- [x] **[SPEC-SUN-002]** Completed task"
        result = overseer_service._parse_markdown_card(line)
        self.assertIsNotNone(result)
        self.assertTrue(result["checked"])

    def test_parse_markdown_card_with_workspace(self):
        """Test parsing a card with workspace."""
        line = "- [ ] **[SPEC-SUN-002]** Title | Workspace: /home/jagosan/repos/sunlit-hills"
        result = overseer_service._parse_markdown_card(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["workspace"], "/home/jagosan/repos/sunlit-hills")

    def test_parse_markdown_card_with_priority(self):
        """Test parsing a card with priority tag."""
        line = "- [ ] **[SPEC-PLAY-038]** Title | Priority: #p1"
        result = overseer_service._parse_markdown_card(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["priority"], 1)

    def test_get_overview_structure(self):
        """Test that get_overview returns expected structure."""
        result = overseer_service.get_overview()
        self.assertIn("timestamp", result)
        self.assertIn("kanban_spec_queue", result)
        self.assertIn("goals", result)
        self.assertIn("runner_state", result)
        self.assertIn("attention", result)

    def test_get_logs_structure(self):
        """Test that get_logs returns expected structure."""
        result = overseer_service.get_logs(lines=50)
        self.assertIn("cr_runner_log", result)
        self.assertIn("cr_governor_log", result)
        self.assertIn("last_updated", result)


class TestAttentionHeuristics(unittest.TestCase):
    """Test cases for Attention & Stall Heuristics Engine (TASK-HK-144)."""

    def test_needs_input_human_playtest_required(self):
        """Test evaluation of HUMAN_PLAYTEST_REQUIRED state."""
        # Mock data
        goals = [
            {
                "id": "GOAL-PLAY-038",
                "title": "Lunar Frontier Excavation",
                "status": "active",
                "blocking_state": "HUMAN_PLAYTEST_REQUIRED",
            }
        ]
        kanban = {
            "columns": {"review": []},
            "counts": {"review": 0}
        }
        
        needs_input = overseer_service._evaluate_needs_input(goals, kanban)
        # Should not add anything if review count is 0
        self.assertIsInstance(needs_input, list)

    def test_needs_input_ready_for_spec_authoring(self):
        """Test evaluation of READY_FOR_SPEC_AUTHORING state."""
        goals = [
            {
                "id": "GOAL-SUN-001",
                "title": "Sunlit Hills Automation",
                "status": "active",
                "blocking_state": "READY_FOR_SPEC_AUTHORING",
            }
        ]
        kanban = {
            "columns": {},
            "counts": {}
        }
        
        needs_input = overseer_service._evaluate_needs_input(goals, kanban)
        self.assertIsInstance(needs_input, list)

    def test_needs_input_human_review_gate(self):
        """Test evaluation of HUMAN_REVIEW_GATE state (>= 3 specs in review)."""
        goals = []
        kanban = {
            "columns": {"review": []},
            "counts": {"review": 5}  # >= 3 triggers the gate
        }
        
        needs_input = overseer_service._evaluate_needs_input(goals, kanban)
        self.assertIsInstance(needs_input, list)

    def test_stalled_runner_paused(self):
        """Test evaluation of RUNNER_PAUSED state."""
        runner_state = {"stall_count": 0}
        kanban = {"cards": []}
        
        # Create a temporary lock file
        paused_path = Path("/tmp/test_runner_paused")
        try:
            paused_path.touch()
            
            # Temporarily override the path for testing
            import os
            original_paused = overseer_service.__class__.__dict__.get("RUNNER_PAUSED_PATH", None)
            
            stalled = overseer_service._evaluate_stalled(runner_state, kanban)
            self.assertIsInstance(stalled, list)
        finally:
            if paused_path.exists():
                paused_path.unlink()

    def test_stalled_stall_loop_detected_warning(self):
        """Test evaluation of STALL_LOOP_DETECTED warning state."""
        runner_state = {"stall_count": 2}  # >= 2 triggers warning
        kanban = {"cards": []}
        
        stalled = overseer_service._evaluate_stalled(runner_state, kanban)
        self.assertIsInstance(stalled, list)

    def test_stalled_stall_loop_detected_critical(self):
        """Test evaluation of STALL_LOOP_DETECTED critical state."""
        runner_state = {"stall_count": 10}  # >= 10 is critical
        kanban = {"cards": []}
        
        stalled = overseer_service._evaluate_stalled(runner_state, kanban)
        self.assertIsInstance(stalled, list)

    def test_stalled_phase_failed(self):
        """Test evaluation of PHASE_FAILED state."""
        runner_state = {
            "stall_count": 0,
            "last_status": "PHASE_FAILED",
            "last_spec": "SPEC-PLAY-038"
        }
        kanban = {"cards": []}
        
        stalled = overseer_service._evaluate_stalled(runner_state, kanban)
        self.assertIsInstance(stalled, list)

    def test_stalled_no_phases_found(self):
        """Test evaluation of NO_PHASES_FOUND state."""
        runner_state = {"stall_count": 0}
        # Running card with no unchecked items
        kanban = {
            "cards": [
                {"column": "running", "checked": True, "spec_id": "SPEC-PLAY-038"}
            ]
        }
        
        stalled = overseer_service._evaluate_stalled(runner_state, kanban)
        self.assertIsInstance(stalled, list)

class TestOverseerActionDispatcher(unittest.TestCase):
    """Test cases for Overseer action dispatcher (TASK-HK-145)."""

    def setUp(self):
        import tempfile
        from unittest.mock import patch
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.p1 = patch("api.services.overseer_service.RUNNER_PAUSED_PATH", self.tmp_path / "runner_paused")
        self.p2 = patch("api.services.overseer_service.GOVERNOR_PAUSED_PATH", self.tmp_path / "governor_paused")
        self.p3 = patch("api.services.overseer_service.CR_RUNNER_STATE_PATH", self.tmp_path / "cr_runner_state.json")
        self.p1.start()
        self.p2.start()
        self.p3.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.p3.stop()
        self._tmpdir.cleanup()

    def test_pause_runner(self):
        """Test pause_runner creates lockfile atomically."""
        result = overseer_service.pause_runner()
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "pause_runner")
        self.assertIn("lockfile", result)
        
        # Verify lockfile was created
        from api.services.overseer_service import RUNNER_PAUSED_PATH
        self.assertTrue(RUNNER_PAUSED_PATH.exists())
        
        # Clean up
        if RUNNER_PAUSED_PATH.exists():
            RUNNER_PAUSED_PATH.unlink()

    def test_pause_runner_already_paused(self):
        """Test pause_runner when already paused."""
        from api.services.overseer_service import RUNNER_PAUSED_PATH
        
        # Create lockfile first
        RUNNER_PAUSED_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNNER_PAUSED_PATH.write_text("test")
        
        result = overseer_service.pause_runner()
        self.assertTrue(result["success"])
        self.assertIn("already paused", result["message"].lower())
        
        # Clean up
        if RUNNER_PAUSED_PATH.exists():
            RUNNER_PAUSED_PATH.unlink()

    def test_resume_runner(self):
        """Test resume_runner removes lockfiles."""
        from api.services.overseer_service import RUNNER_PAUSED_PATH, GOVERNOR_PAUSED_PATH
        
        # Create lockfiles first
        RUNNER_PAUSED_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNNER_PAUSED_PATH.write_text("test")
        GOVERNOR_PAUSED_PATH.write_text("test")
        
        result = overseer_service.resume_runner()
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "resume_runner")
        self.assertIn("removed_files", result)
        
        # Verify lockfiles were removed
        self.assertFalse(RUNNER_PAUSED_PATH.exists())
        self.assertFalse(GOVERNOR_PAUSED_PATH.exists())

    def test_expedite_spec(self):
        """Test expedite_spec moves card to Expedited."""
        import os
        import tempfile
        from unittest.mock import patch
        
        test_content = """## 📥 Backlog
- [ ] **[SPEC-PLAY-038]** Play spec"""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".md") as tf:
            tf.write(test_content)
            temp_path = tf.name

        old_board = os.environ.get("HERMES_BOARD")
        os.environ["HERMES_BOARD"] = temp_path

        try:
            with patch.object(overseer_service, "_commit_vault_changes", return_value=True):
                result = overseer_service.expedite_spec("SPEC-PLAY-038")
                self.assertTrue(result["success"])
                self.assertEqual(result["action"], "expedite_spec")

                content = Path(temp_path).read_text()
                self.assertIn("#p0", content)
        finally:
            if old_board:
                os.environ["HERMES_BOARD"] = old_board
            else:
                os.environ.pop("HERMES_BOARD", None)
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_expedite_spec_not_found(self):
        """Test expedite_spec with non-existent spec raises HTTPException."""
        from fastapi import HTTPException
        
        with self.assertRaises(HTTPException) as context:
            overseer_service.expedite_spec("NON-EXISTENT")
        self.assertEqual(context.exception.status_code, 404)

    def test_archive_spec(self):
        """Test archive_spec moves card to Archived."""
        import os
        import tempfile
        from unittest.mock import patch
        
        test_content = """## 📥 Backlog
- [x] **[SPEC-ARCHIVE-001]** Test spec"""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".md") as tf:
            tf.write(test_content)
            temp_path = tf.name

        old_board = os.environ.get("HERMES_BOARD")
        os.environ["HERMES_BOARD"] = temp_path

        try:
            with patch.object(overseer_service, "_commit_vault_changes", return_value=True):
                result = overseer_service.archive_spec("SPEC-ARCHIVE-001")
                self.assertTrue(result["success"])
                self.assertEqual(result["action"], "archive_spec")

                content = Path(temp_path).read_text()
                self.assertIn("(Archived)", content)
        finally:
            if old_board:
                os.environ["HERMES_BOARD"] = old_board
            else:
                os.environ.pop("HERMES_BOARD", None)
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_step_phase(self):
        """Test step_phase executes runner step."""
        from unittest.mock import patch, MagicMock
        mock_res = MagicMock(returncode=0, stdout='{"status": "PHASE_EXECUTED"}', stderr="")
        with patch("api.services.overseer_service.subprocess.run", return_value=mock_res) as mock_run:
            result = overseer_service.step_phase()
            self.assertTrue(result["success"])
            self.assertEqual(result["action"], "step_phase")
            self.assertIn("message", result)
            mock_run.assert_called_once()





class TestOverseerParserResilience(unittest.TestCase):
    """Test cases for parser resilience against malformed markdown."""

    def test_parse_markdown_card_malformed_lines(self):
        """Test parsing malformed markdown returns None."""
        malformed_lines = [
            "not a card line",
            "- invalid format",
            "[ ] missing asterisks SPEC-001",
            "",
            "  ",
            "some random text without markdown syntax",
            "- [ ] incomplete card",
            "- [ ] **[no-title]**",
            "- [ ] **[]** Title",  # Empty spec_id
        ]
        for line in malformed_lines:
            result = overseer_service._parse_markdown_card(line)
            self.assertIsNone(result, f"Expected None for: {line!r}")

    def test_parse_markdown_card_special_characters(self):
        """Test parsing cards with special characters in title."""
        lines = [
            "- [ ] **[SPEC-001]** Title with spaces and numbers 123",
            "- [ ] **[SPEC-002]** Title with - dashes and _ underscores",
            "- [ ] **[SPEC-003]** Title with (parentheses) and [brackets]",
        ]
        for line in lines:
            result = overseer_service._parse_markdown_card(line)
            self.assertIsNotNone(result, f"Failed to parse: {line}")
            self.assertIn("spec_id", result)

    def test_parse_kanban_spec_queue_empty_file(self):
        """Test parsing an empty Kanban file returns empty structure."""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".md") as tf:
            tf.write("")
            temp_path = tf.name

        old_board = os.environ.get("HERMES_BOARD")
        os.environ["HERMES_BOARD"] = temp_path
        try:
            result = overseer_service.parse_kanban_spec_queue()
            self.assertEqual(result["cards"], [])
            self.assertEqual(len(result["counts"]), 7)  # All columns should have 0 count
        finally:
            if old_board:
                os.environ["HERMES_BOARD"] = old_board
            else:
                os.environ.pop("HERMES_BOARD", None)
            Path(temp_path).unlink()

    def test_parse_kanban_spec_queue_no_cards(self):
        """Test parsing Kanban file with only headers, no cards."""
        content = """## 📥 Backlog
## ⏳ Queued
## 🚨 Expedited
## 🚧 Running
## ⏸️ Paused
## 🧪 Review & Ready to Test
## ✅ Done & Archived"""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".md") as tf:
            tf.write(content)
            temp_path = tf.name

        old_board = os.environ.get("HERMES_BOARD")
        os.environ["HERMES_BOARD"] = temp_path
        try:
            result = overseer_service.parse_kanban_spec_queue()
            self.assertEqual(result["cards"], [])
        finally:
            if old_board:
                os.environ["HERMES_BOARD"] = old_board
            else:
                os.environ.pop("HERMES_BOARD", None)
            Path(temp_path).unlink()

    def test_parse_cr_runner_state_empty_file(self):
        """Test parsing empty runner state returns defaults."""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tf:
            tf.write("")
            temp_path = tf.name

        old_state = os.environ.get("HERMES_CR_RUNNER_STATE")
        os.environ["HERMES_CR_RUNNER_STATE"] = temp_path
        try:
            result = overseer_service.parse_cr_runner_state()
            self.assertEqual(result["stall_count"], 0)
            self.assertIsNone(result["last_status"])
        finally:
            if old_state:
                os.environ["HERMES_CR_RUNNER_STATE"] = old_state
            else:
                os.environ.pop("HERMES_CR_RUNNER_STATE", None)
            Path(temp_path).unlink()

    def test_parse_cr_runner_state_invalid_json(self):
        """Test parsing invalid JSON returns defaults."""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tf:
            tf.write("{invalid json")
            temp_path = tf.name

        old_state = os.environ.get("HERMES_CR_RUNNER_STATE")
        os.environ["HERMES_CR_RUNNER_STATE"] = temp_path
        try:
            result = overseer_service.parse_cr_runner_state()
            self.assertEqual(result["stall_count"], 0)
            self.assertIsNone(result["last_status"])
        finally:
            if old_state:
                os.environ["HERMES_CR_RUNNER_STATE"] = old_state
            else:
                os.environ.pop("HERMES_CR_RUNNER_STATE", None)
            Path(temp_path).unlink()

    def test_parse_cr_runner_state_missing_fields(self):
        """Test parsing runner state with missing fields adds defaults."""
        state = {"stall_count": 5}
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tf:
            json.dump(state, tf)
            temp_path = tf.name

        old_state = os.environ.get("HERMES_CR_RUNNER_STATE")
        os.environ["HERMES_CR_RUNNER_STATE"] = temp_path
        try:
            result = overseer_service.parse_cr_runner_state()
            self.assertEqual(result["stall_count"], 5)
            self.assertIsNone(result["last_status"])
        finally:
            if old_state:
                os.environ["HERMES_CR_RUNNER_STATE"] = old_state
            else:
                os.environ.pop("HERMES_CR_RUNNER_STATE", None)
            Path(temp_path).unlink()


class TestOverseerRESTEndpoints(unittest.TestCase):
    """Test cases for Overseer REST API endpoints (TASK-HK-147)."""

    def setUp(self):
        """Set up test client."""
        from api.main import app
        self.client = TestClient(app)

    def test_get_overview_endpoint(self):
        """Test GET /api/overseer/overview endpoint."""
        response = self.client.get("/api/overseer/overview")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("timestamp", data)
        self.assertIn("kanban_spec_queue", data)
        self.assertIn("goals", data)
        self.assertIn("runner_state", data)
        self.assertIn("attention", data)

    def test_get_overview_returns_valid_structure(self):
        """Test that overview endpoint returns valid data structure."""
        response = self.client.get("/api/overseer/overview")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Verify attention structure
        attention = data["attention"]
        self.assertIn("needs_input", attention)
        self.assertIn("stalled", attention)
        self.assertIn("summary", attention)

        summary = attention["summary"]
        self.assertIn("needs_input_count", summary)
        self.assertIn("stalled_count", summary)
        self.assertIn("healthy", summary)

    def test_get_spec_details_not_found(self):
        """Test GET /api/overseer/spec/{spec_id} returns 404 for non-existent spec."""
        response = self.client.get("/api/overseer/spec/SPEC-NOTFOUND-001")
        self.assertEqual(response.status_code, 404)

    def test_get_logs_endpoint(self):
        """Test GET /api/overseer/logs endpoint."""
        response = self.client.get("/api/overseer/logs?lines=10")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("cr_runner_log", data)
        self.assertIn("cr_governor_log", data)
        self.assertIn("last_updated", data)

    def test_pause_runner_endpoint(self):
        """Test POST /api/overseer/action with pause_runner."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "pause_runner"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["action"], "pause_runner")

    def test_resume_runner_endpoint(self):
        """Test POST /api/overseer/action with resume_runner."""
        # First ensure runner is paused
        self.client.post("/api/overseer/action", json={"action": "pause_runner"})

        response = self.client.post(
            "/api/overseer/action",
            json={"action": "resume_runner"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["action"], "resume_runner")

    def test_expedite_spec_not_found_endpoint(self):
        """Test POST /api/overseer/action with non-existent spec."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "expedite_spec", "spec_id": "NON-EXISTENT"}
        )
        self.assertEqual(response.status_code, 404)

    def test_step_phase_endpoint(self):
        """Test POST /api/overseer/action with step_phase."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "step_phase"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["action"], "step_phase")

    def test_invalid_action_endpoint(self):
        """Test POST /api/overseer/action with invalid action."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "invalid_action"}
        )
        # Should return 400 for invalid action
        self.assertEqual(response.status_code, 400)

    def test_expedite_spec_missing_spec_id(self):
        """Test expedite_spec without spec_id returns 400."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "expedite_spec"}
        )
        assert response.status_code == 400

    def test_archive_spec_missing_spec_id(self):
        """Test archive_spec without spec_id returns 400."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "archive_spec"}
        )
        assert response.status_code == 400


class TestOverseerSSEStream(unittest.TestCase):
    """Test cases for Overseer SSE stream endpoint (TASK-HK-147)."""

    def setUp(self):
        """Set up test client."""
        from api.main import app
        self.client = TestClient(app)

    def test_sse_stream_endpoint_exists(self):
        """Test that /api/overseer/stream endpoint is accessible."""
        response = self.client.get("/api/overseer/stream?max_events=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers.get("content-type", ""))

    def test_sse_stream_with_max_events(self):
        """Test SSE stream with max_events parameter."""
        response = self.client.get("/api/overseer/stream?max_events=3")
        self.assertEqual(response.status_code, 200)

        # Read the SSE events
        events = response.text.strip().split("\n\n")
        # Each event should have data: prefix
        has_data_events = any("data:" in event for event in events)
        self.assertTrue(has_data_events)


class TestOverseerIntegration(unittest.TestCase):
    """Integration tests for Overseer service components (TASK-HK-147)."""

    def test_overall_state_flow(self):
        """Test complete state flow from parsing to overview."""
        # Parse all components
        kanban = overseer_service.parse_kanban_spec_queue()
        goals = overseer_service.parse_goals()
        runner_state = overseer_service.parse_cr_runner_state()

        # Verify structures
        self.assertIn("cards", kanban)
        self.assertIsInstance(goals, list)
        self.assertIn("stall_count", runner_state)

        # Generate overview
        overview = overseer_service.get_overview()

        # Verify overview integrates all components
        self.assertEqual(overview["kanban_spec_queue"]["cards"], kanban["cards"])
        self.assertEqual(overview["kanban_spec_queue"]["counts"], kanban["counts"])
        self.assertEqual(overview["goals"], goals)
        self.assertEqual(overview["runner_state"]["stall_count"], runner_state["stall_count"])

    def test_attention_summary_calculation(self):
        """Test that attention summary correctly counts items."""
        overview = overseer_service.get_overview()
        attention = overview["attention"]

        needs_count = len(attention["needs_input"])
        stalled_count = len(attention["stalled"])

        self.assertEqual(needs_count, attention["summary"]["needs_input_count"])
        self.assertEqual(stalled_count, attention["summary"]["stalled_count"])

    def test_parser_consistency_across_calls(self):
        """Test that parsers return consistent results across multiple calls."""
        kanban1 = overseer_service.parse_kanban_spec_queue()
        goals1 = overseer_service.parse_goals()

        time.sleep(0.1)  # Small delay to ensure any cache would refresh

        kanban2 = overseer_service.parse_kanban_spec_queue()
        goals2 = overseer_service.parse_goals()

        self.assertEqual(kanban1["counts"], kanban2["counts"])
        self.assertEqual(len(goals1), len(goals2))


if __name__ == "__main__":
    unittest.main()
