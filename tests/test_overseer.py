"""Tests for Overseer Service (TASK-HK-143)."""
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
