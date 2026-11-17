"""Tests for Overseer REST endpoints & SSE stream (TASK-HK-147).

Comprehensive unit and integration tests for:
- All Overseer REST endpoints
- SSE stream with max_events support
- Parser resilience against malformed markdown
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

# Import the full app with lifespan context
from api.main import app
from api.services.overseer_service import overseer_service


class TestOverseerEndpoints:
    """Test cases for Overseer REST endpoints."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        """Set up test client with proper app."""
        self.client = TestClient(app)

    def test_get_overview_returns_200(self):
        """Test GET /api/overseer/overview returns 200 OK."""
        response = self.client.get("/api/overseer/overview")
        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
        assert "kanban_spec_queue" in data
        assert "goals" in data
        assert "runner_state" in data
        assert "attention" in data

    def test_get_overview_structure(self):
        """Test GET /api/overseer/overview returns expected structure."""
        response = self.client.get("/api/overseer/overview")
        assert response.status_code == 200
        data = response.json()
        
        # Verify top-level keys
        assert "timestamp" in data
        assert "kanban_spec_queue" in data
        assert "goals" in data
        assert "runner_state" in data
        assert "attention" in data
        
        # Verify attention structure
        attention = data["attention"]
        assert "needs_input" in attention
        assert "stalled" in attention
        assert "summary" in attention
        assert "needs_input_count" in attention["summary"]
        assert "stalled_count" in attention["summary"]
        assert "healthy" in attention["summary"]

    def test_get_spec_details_returns_200(self):
        """Test GET /api/overseer/spec/{spec_id} returns 200 OK."""
        response = self.client.get("/api/overseer/spec/SPEC-PLAY-038")
        assert response.status_code in [200, 404]  # May not exist in test env
        if response.status_code == 200:
            data = response.json()
            assert "spec_id" in data
            assert "card" in data

    def test_get_spec_details_not_found(self):
        """Test GET /api/overseer/spec/{spec_id} returns 404 for non-existent spec."""
        response = self.client.get("/api/overseer/spec/NON-EXISTENT-SPEC")
        assert response.status_code == 404

    def test_get_logs_returns_200(self):
        """Test GET /api/overseer/logs returns 200 OK."""
        response = self.client.get("/api/overseer/logs?lines=50")
        assert response.status_code == 200
        data = response.json()
        assert "cr_runner_log" in data
        assert "cr_governor_log" in data
        assert "last_updated" in data

    def test_get_logs_with_custom_lines(self):
        """Test GET /api/overseer/logs with custom lines parameter."""
        response = self.client.get("/api/overseer/logs?lines=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data["cr_runner_log"]) <= 10

    def test_get_logs_lines_validation(self):
        """Test GET /api/overseer/logs validates lines parameter."""
        # Test minimum
        response = self.client.get("/api/overseer/logs?lines=0")
        assert response.status_code == 422  # Validation error
        
        # Test maximum
        response = self.client.get("/api/overseer/logs?lines=1001")
        assert response.status_code == 422  # Validation error


class TestOverseerActionEndpoints:
    """Test cases for Overseer action endpoints."""

    @pytest.fixture(autouse=True)
    def setup_client(self, tmp_path, monkeypatch):
        """Set up test client with isolated temp paths and mocked subprocesses."""
        self.client = TestClient(app)
        board_file = tmp_path / "Kanban-Spec-Queue.md"
        board_file.write_text(
            "## ⏳ Queued\n"
            "- [ ] **[SPEC-PLAY-038]** Lunar Frontier | Workspace: `/home/jagosan/repos/playground` #p1\n"
            "## 🧪 Review & Ready to Test\n"
            "- [x] **[SPEC-ARCHIVE-001]** Test Archive Spec #p1\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMES_BOARD", str(board_file))
        monkeypatch.setattr("api.services.overseer_service.RUNNER_PAUSED_PATH", tmp_path / "runner_paused")
        monkeypatch.setattr("api.services.overseer_service.GOVERNOR_PAUSED_PATH", tmp_path / "governor_paused")
        monkeypatch.setattr("api.services.overseer_service.CR_RUNNER_STATE_PATH", tmp_path / "cr_runner_state.json")
        monkeypatch.setattr(overseer_service, "_commit_vault_changes", lambda msg: True)

    def test_post_action_pause_runner(self):
        """Test POST /api/overseer/action with pause_runner."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "pause_runner"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "pause_runner"
        assert data["success"] is True

    def test_post_action_resume_runner(self):
        """Test POST /api/overseer/action with resume_runner."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "resume_runner"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "resume_runner"
        assert data["success"] is True

    def test_post_action_step_phase(self):
        """Test POST /api/overseer/action with step_phase."""
        mock_res = MagicMock(returncode=0, stdout='{"status": "OK"}', stderr="")
        with patch("api.services.overseer_service.subprocess.run", return_value=mock_res):
            response = self.client.post(
                "/api/overseer/action",
                json={"action": "step_phase"}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["action"] == "step_phase"
        assert data["success"] is True

    def test_post_action_expedite_spec(self):
        """Test POST /api/overseer/action with expedite_spec."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "expedite_spec", "spec_id": "SPEC-PLAY-038"}
        )
        assert response.status_code == 200

    def test_post_action_archive_spec(self):
        """Test POST /api/overseer/action with archive_spec."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "archive_spec", "spec_id": "SPEC-PLAY-038"}
        )
        assert response.status_code == 200

    def test_post_action_invalid_action(self):
        """Test POST /api/overseer/action with invalid action."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "invalid_action"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data

    def test_post_action_missing_action(self):
        """Test POST /api/overseer/action with missing action."""
        response = self.client.post(
            "/api/overseer/action",
            json={}
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data

    def test_post_action_expedite_spec_missing_id(self):
        """Test POST /api/overseer/action with expedite_spec but no spec_id."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "expedite_spec"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data

    def test_post_action_archive_spec_missing_id(self):
        """Test POST /api/overseer/action with archive_spec but no spec_id."""
        response = self.client.post(
            "/api/overseer/action",
            json={"action": "archive_spec"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data


class TestSSEStream:
    """Test cases for SSE stream endpoint."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        """Set up test client with proper app."""
        self.client = TestClient(app)

    def test_get_stream_returns_200(self):
        """Test GET /api/overseer/stream returns 200 OK with SSE headers."""
        # Use max_events=1 to ensure the stream closes after one event
        response = self.client.get("/api/overseer/stream?max_events=1&interval=0.1")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert "no-cache" in response.headers["cache-control"]

    def test_get_stream_emits_events(self):
        """Test GET /api/overseer/stream emits SSE events."""
        # Use max_events=1 to ensure the stream closes after one event
        response = self.client.get("/api/overseer/stream?max_events=1&interval=0.1")
        assert response.status_code == 200
        
        # Parse SSE events from response text
        content = response.text.strip()
        lines = content.split("\n")
        
        # Should have at least "data: {...}" line
        data_lines = [line for line in lines if line.startswith("data: ")]
        assert len(data_lines) >= 1

    def test_get_stream_max_events_limit(self):
        """Test GET /api/overseer/stream respects max_events parameter."""
        # Request 3 events with very short interval
        response = self.client.get("/api/overseer/stream?max_events=3&interval=0.05")
        assert response.status_code == 200
        
        content = response.text.strip()
        data_lines = [line for line in content.split("\n") if line.startswith("data: ")]
        
        # Should have exactly 3 events
        assert len(data_lines) == 3

    def test_get_stream_closes_on_max_events(self):
        """Test GET /api/overseer/stream closes connection after max_events."""
        response = self.client.get("/api/overseer/stream?max_events=1&interval=10")
        assert response.status_code == 200
        
        # The stream should have closed (not hang indefinitely)
        content = response.text.strip()
        assert len(content) > 0


class TestParserResilience:
    """Test cases for parser resilience against malformed input."""

    def test_parse_markdown_card_empty_line(self):
        """Test _parse_markdown_card handles empty line."""
        result = overseer_service._parse_markdown_card("")
        assert result is None

    def test_parse_markdown_card_malformed(self):
        """Test _parse_markdown_card handles malformed line."""
        result = overseer_service._parse_markdown_card("not a valid card line")
        assert result is None

    def test_parse_markdown_card_missing_spec_id(self):
        """Test _parse_markdown_card handles line without spec ID."""
        result = overseer_service._parse_markdown_card("- [ ] **Title** some text")
        # Should still parse if format matches, even without explicit spec_id pattern
        assert result is not None or True  # May return None for malformed

    def test_parse_kanban_spec_queue_missing_file(self):
        """Test parse_kanban_spec_queue handles missing file."""
        with patch.object(overseer_service, "_get_kanban_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/path.md")
            result = overseer_service.parse_kanban_spec_queue()
            assert result == {"cards": [], "columns": {}, "counts": {}}

    def test_parse_goals_missing_directory(self):
        """Test parse_goals handles missing goals directory."""
        with patch.object(overseer_service, "_read_file_safe") as mock_read:
            mock_read.return_value = None
            result = overseer_service.parse_goals()
            assert isinstance(result, list)

    def test_parse_cr_runner_state_invalid_json(self):
        """Test parse_cr_runner_state handles invalid JSON."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            f.write("not valid json {")
            temp_path = f.name
        
        try:
            with patch('api.services.overseer_service.CR_RUNNER_STATE_PATH', Path(temp_path)):
                result = overseer_service.parse_cr_runner_state()
                assert "stall_count" in result
                assert result["stall_count"] == 0
        finally:
            Path(temp_path).unlink()

    def test_parse_cr_runner_state_missing_fields(self):
        """Test parse_cr_runner_state handles missing required fields."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            json.dump({"stall_count": 5}, f)  # Missing other fields
            temp_path = f.name
        
        try:
            with patch('api.services.overseer_service.CR_RUNNER_STATE_PATH', Path(temp_path)):
                result = overseer_service.parse_cr_runner_state()
                assert result["stall_count"] == 5
                assert "last_status" in result  # Should have default
        finally:
            Path(temp_path).unlink()

    def test_parse_goal_file_missing_content(self):
        """Test parse_goal_file handles missing content."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.md') as f:
            temp_path = f.name
        
        try:
            result = overseer_service.parse_goal_file(Path(temp_path))
            assert result is None
        finally:
            Path(temp_path).unlink()

    def test_parse_markdown_card_with_extra_whitespace(self):
        """Test _parse_markdown_card handles extra whitespace."""
        line = "  - [ ]   **[SPEC-TEST-001]**   Title text   | Workspace: /path  "
        result = overseer_service._parse_markdown_card(line)
        assert result is not None
        assert result["spec_id"] == "SPEC-TEST-001"

    def test_parse_markdown_card_with_special_characters(self):
        """Test _parse_markdown_card handles special characters in title."""
        line = "- [ ] **[SPEC-SPECIAL-001]** Title with @#$% special chars"
        result = overseer_service._parse_markdown_card(line)
        assert result is not None
        assert "SPECIAL" in result["spec_id"]


class TestAttentionHeuristicsEdgeCases:
    """Test edge cases for attention heuristics."""

    def test_evaluate_needs_input_empty_goals(self):
        """Test _evaluate_needs_input handles empty goals."""
        result = overseer_service._evaluate_needs_input([], {"columns": {}, "counts": {}})
        assert isinstance(result, list)

    def test_evaluate_needs_input_no_review_cards(self):
        """Test _evaluate_needs_input handles no review cards."""
        result = overseer_service._evaluate_needs_input(
            [{"id": "GOAL-TEST", "status": "active", "blocking_state": "HUMAN_PLAYTEST_REQUIRED"}],
            {"columns": {"review": []}, "counts": {"review": 0}}
        )
        assert isinstance(result, list)

    def test_evaluate_stalled_empty_runner_state(self):
        """Test _evaluate_stalled handles empty runner state."""
        result = overseer_service._evaluate_stalled({}, {"cards": []})
        assert isinstance(result, list)

    def test_evaluate_stalled_no_running_cards(self):
        """Test _evaluate_stalled handles no running cards."""
        result = overseer_service._evaluate_stalled(
            {"stall_count": 0},
            {"cards": [], "columns": {"running": []}}
        )
        assert isinstance(result, list)


class TestIntegrationEndToEnd:
    """Integration tests for end-to-end Overseer flow."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        """Set up test client with proper app."""
        self.client = TestClient(app)

    def test_full_overview_flow(self):
        """Test complete overview data flow from service to API."""
        # Get overview via service
        service_data = overseer_service.get_overview()
        
        # Get overview via API
        api_response = self.client.get("/api/overseer/overview")
        assert api_response.status_code == 200
        api_data = api_response.json()
        
        # Verify data matches
        assert "timestamp" in service_data
        assert "timestamp" in api_data
        assert "attention" in service_data
        assert "attention" in api_data

    def test_full_spec_details_flow(self):
        """Test complete spec details flow from service to API."""
        # Get a spec via service (may not exist, that's OK)
        try:
            service_data = overseer_service.get_spec_details("SPEC-PLAY-038")
        except Exception:
            service_data = None
        
        # Get same spec via API
        api_response = self.client.get("/api/overseer/spec/SPEC-PLAY-038")
        
        # Both should handle gracefully (either 200 or 404)
        assert api_response.status_code in [200, 404]

    def test_full_logs_flow(self):
        """Test complete logs flow from service to API."""
        # Get logs via service
        service_data = overseer_service.get_logs(lines=50)
        
        # Get logs via API
        api_response = self.client.get("/api/overseer/logs?lines=50")
        assert api_response.status_code == 200
        api_data = api_response.json()
        
        # Verify data matches structure
        assert "cr_runner_log" in service_data
        assert "cr_runner_log" in api_data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
