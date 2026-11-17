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

    def test_get_logs_structure(self):
        """Test that get_logs returns expected structure."""
        result = overseer_service.get_logs(lines=50)
        self.assertIn("cr_runner_log", result)
        self.assertIn("cr_governor_log", result)
        self.assertIn("last_updated", result)


if __name__ == "__main__":
    unittest.main()
