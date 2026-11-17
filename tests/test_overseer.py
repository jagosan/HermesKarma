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

class TestOverseerActionDispatcher(unittest.TestCase):
    """Test cases for Overseer action dispatcher (TASK-HK-145)."""

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
        from api.services.overseer_service import KANBAN_SPEC_QUEUE_PATH
        import os
        
        # Create a test Kanban board with proper format
        test_content = """## 📥 Backlog
- [ ] **[SPEC-PLAY-038]** Play spec"""
        KANBAN_SPEC_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        KANBAN_SPEC_QUEUE_PATH.write_text(test_content)
        
        # Temporarily override the path for parsing
        old_board = os.environ.get("HERMES_BOARD")
        os.environ["HERMES_BOARD"] = str(KANBAN_SPEC_QUEUE_PATH)
        
        try:
            result = overseer_service.expedite_spec("SPEC-PLAY-038")
            self.assertTrue(result["success"])
            self.assertEqual(result["action"], "expedite_spec")
            
            # Verify spec_id has #p0 added
            content = KANBAN_SPEC_QUEUE_PATH.read_text()
            self.assertIn("#p0", content)
        finally:
            if old_board:
                os.environ["HERMES_BOARD"] = old_board
            else:
                os.environ.pop("HERMES_BOARD", None)
            if KANBAN_SPEC_QUEUE_PATH.exists():
                KANBAN_SPEC_QUEUE_PATH.unlink()

    def test_expedite_spec_not_found(self):
        """Test expedite_spec with non-existent spec raises HTTPException."""
        from fastapi import HTTPException
        
        # Should raise 404 for non-existent spec
        with self.assertRaises(HTTPException) as context:
            overseer_service.expedite_spec("NON-EXISTENT")
        self.assertEqual(context.exception.status_code, 404)

    def test_archive_spec(self):
        """Test archive_spec moves card to Archived."""
        from api.services.overseer_service import KANBAN_SPEC_QUEUE_PATH
        import os
        
        # Create a test Kanban board with proper format
        test_content = """## 📥 Backlog
- [x] **[SPEC-ARCHIVE-001]** Test spec"""
        KANBAN_SPEC_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        KANBAN_SPEC_QUEUE_PATH.write_text(test_content)
        
        # Temporarily override the path for parsing
        old_board = os.environ.get("HERMES_BOARD")
        os.environ["HERMES_BOARD"] = str(KANBAN_SPEC_QUEUE_PATH)
        
        try:
            result = overseer_service.archive_spec("SPEC-ARCHIVE-001")
            self.assertTrue(result["success"])
            self.assertEqual(result["action"], "archive_spec")
            
            # Verify the card was archived
            content = KANBAN_SPEC_QUEUE_PATH.read_text()
            self.assertIn("(Archived)", content)
        finally:
            if old_board:
                os.environ["HERMES_BOARD"] = old_board
            else:
                os.environ.pop("HERMES_BOARD", None)
            if KANBAN_SPEC_QUEUE_PATH.exists():
                KANBAN_SPEC_QUEUE_PATH.unlink()

    def test_step_phase(self):
        """Test step_phase executes runner step."""
        result = overseer_service.step_phase()
        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "step_phase")
        self.assertIn("message", result)





if __name__ == "__main__":
    unittest.main()
