"""Overseer Service: Backend state parser & REST API for SPEC-HK-010 (TASK-HK-143).

Provides non-blocking file parsers for Kanban-Spec-Queue.md, goals/GOAL-*.md,
and cr_runner_state.json with graceful fallbacks for missing or locked files.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException


# Constants
HERMES_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
KANBAN_SPEC_QUEUE_PATH = HERMES_DIR.parent / "obsidian/vault/boards/Kanban-Spec-Queue.md"
GOALS_DIR = HERMES_DIR.parent / "obsidian/vault/goals"
CR_RUNNER_STATE_PATH = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "cr_runner_state.json"
CR_RUNNER_LOG_PATH = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "logs" / "cr_runner.log"
CR_GOVERNOR_LOG_PATH = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "logs" / "cr_governor.log"


class OverseerService:
    """Overseer state parser service with non-blocking file operations."""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, float] = {}

    def _read_file_safe(self, path: Path, max_age_seconds: float = 5.0) -> Optional[str]:
        """Read file with graceful fallback if missing or locked."""
        try:
            current_time = time.time()
            if str(path) in self._cache and (current_time - self._cache_timestamps.get(str(path), 0)) < max_age_seconds:
                return self._cache[str(path)]

            if not path.exists():
                return None

            # Try to acquire a read lock (non-blocking)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                self._cache[str(path)] = content
                self._cache_timestamps[str(path)] = current_time
                return content

        except (IOError, OSError, PermissionError):
            # File is momentarily locked or inaccessible - return cached or None
            return self._cache.get(str(path))

    def _parse_markdown_card(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single Kanban card line."""
        # Match patterns like:
        # - [ ] **[SPEC-PLAY-038]** Title text | Workspace: /path | Board: [[link]]
        # - [x] **[SPEC-SUN-002]** Completed task
        match = re.match(
            r"^\s*-\s*\[(?P<checked>[x ])\]\s*\*\*\[(?P<spec_id>[\w-]+)\]\*\*.*$",
            line,
            re.IGNORECASE,
        )
        if not match:
            return None

        result = {
            "checked": match.group("checked").lower() == "x",
            "spec_id": match.group("spec_id").upper(),
            "raw_line": line.strip(),
        }

        # Extract workspace if present
        ws_match = re.search(r"Workspace:\s*([^\|]+)", line)
        if ws_match:
            result["workspace"] = ws_match.group(1).strip()

        # Extract board link if present
        board_match = re.search(r"Board:\s*\[\[([^\]]+)\]\]", line)
        if board_match:
            result["board_link"] = board_match.group(1).strip()

        # Extract spec link if present
        spec_match = re.search(r"\[\[([^\]]+)\]\]", line)
        if spec_match and "board_link" not in result:
            result["spec_link"] = spec_match.group(1).strip()

        # Extract priority tag
        priority_match = re.search(r"#p(\d+)", line)
        if priority_match:
            result["priority"] = int(priority_match.group(1))

        return result

    def parse_kanban_spec_queue(self) -> Dict[str, Any]:
        """Parse Kanban-Spec-Queue.md into structured state."""
        content = self._read_file_safe(KANBAN_SPEC_QUEUE_PATH)
        if not content:
            return {
                "cards": [],
                "columns": {
                    "backlog": [],
                    "queued": [],
                    "expedited": [],
                    "running": [],
                    "paused": [],
                    "review": [],
                    "done": [],
                },
                "counts": {"backlog": 0, "queued": 0, "expedited": 0, "running": 0, "paused": 0, "review": 0, "done": 0},
            }

        cards = []
        columns = {
            "backlog": [],
            "queued": [],
            "expedited": [],
            "running": [],
            "paused": [],
            "review": [],
            "done": [],
        }

        current_column = None
        column_headers = {
            "## 📥 Backlog": "backlog",
            "## ⏳ Queued": "queued",
            "## 🚨 Expedited": "expedited",
            "## 🚧 Running": "running",
            "## ⏸️ Paused": "paused",
            "## 🧪 Review & Ready to Test": "review",
            "## ✅ Done & Archived": "done",
        }

        for line in content.split("\n"):
            # Check for column headers
            for header, col_name in column_headers.items():
                if header.strip() in line.strip():
                    current_column = col_name
                    break

            # Parse cards
            card = self._parse_markdown_card(line)
            if card:
                card["column"] = current_column
                cards.append(card)
                if current_column and current_column in columns:
                    columns[current_column].append(card)

        counts = {col: len(cards_list) for col, cards_list in columns.items()}

        return {
            "cards": cards,
            "columns": columns,
            "counts": counts,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def parse_goal_file(self, path: Path) -> Optional[Dict[str, Any]]:
        """Parse a single goal file (GOAL-*.md)."""
        content = self._read_file_safe(path)
        if not content:
            return None

        result = {
            "id": path.stem.upper(),
            "file_path": str(path),
        }

        # Try to extract from YAML frontmatter
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if frontmatter_match:
            try:
                import yaml

                frontmatter = yaml.safe_load(frontmatter_match.group(1))
                result.update(frontmatter or {})
            except Exception:
                pass

        # Extract title from first H1
        h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if h1_match:
            result["title"] = h1_match.group(1).strip()

        # Extract status from frontmatter or body
        status_match = re.search(r"status:\s*(\w+)", content)
        if status_match:
            result["status"] = status_match.group(1).strip()

        # Extract blocking state
        blocking_match = re.search(r"blocking_state:\s*(\w+)", content)
        if blocking_match:
            result["blocking_state"] = blocking_match.group(1).strip()

        return result

    def parse_goals(self) -> List[Dict[str, Any]]:
        """Parse all goal files from goals/ directory."""
        goals = []

        if not GOALS_DIR.exists():
            return goals

        for goal_file in GOALS_DIR.glob("GOAL-*.md"):
            goal = self.parse_goal_file(goal_file)
            if goal:
                goals.append(goal)

        # Sort by ID for consistent ordering
        goals.sort(key=lambda g: g.get("id", ""))

        return goals

    def parse_cr_runner_state(self) -> Dict[str, Any]:
        """Parse cr_runner_state.json with graceful fallback."""
        content = self._read_file_safe(CR_RUNNER_STATE_PATH)
        if not content:
            return {
                "stall_count": 0,
                "last_status": None,
                "last_spec": None,
                "remediation_tried": "",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

        try:
            state = json.loads(content)
            # Ensure required fields exist
            for field in ["stall_count", "last_status", "last_spec", "remediation_tried"]:
                if field not in state:
                    state[field] = None if field == "last_spec" else 0
            if "updated_at" not in state:
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
            return state
        except json.JSONDecodeError:
            return {
                "stall_count": 0,
                "last_status": None,
                "last_spec": None,
                "remediation_tried": "",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

    def get_overview(self) -> Dict[str, Any]:
        """Get complete unified state overview."""
        kanban = self.parse_kanban_spec_queue()
        goals = self.parse_goals()
        runner_state = self.parse_cr_runner_state()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kanban_spec_queue": kanban,
            "goals": goals,
            "runner_state": runner_state,
        }

    def get_spec_details(self, spec_id: str) -> Dict[str, Any]:
        """Get detailed information for a specific spec."""
        # Find the spec in the Kanban queue
        kanban = self.parse_kanban_spec_queue()
        target_card = None

        for card in kanban["cards"]:
            if card.get("spec_id", "").upper() == spec_id.upper():
                target_card = card
                break

        if not target_card:
            raise HTTPException(status_code=404, detail=f"Spec {spec_id} not found")

        # Find the spec file
        spec_file = None
        spec_path = None

        # Try to find in local workspace first
        local_spec_path = Path("/home/jagosan/repos/HermesKarma/specs")
        if local_spec_path.exists():
            for f in local_spec_path.glob("*.md"):
                if spec_id.lower() in f.stem.lower():
                    spec_file = f.read_text(encoding="utf-8", errors="replace")
                    spec_path = str(f)
                    break

        # If not found locally, check vault
        if not spec_file:
            vault_spec_path = HERMES_DIR.parent / "obsidian/vault"
            if vault_spec_path.exists():
                for root, dirs, files in os.walk(vault_spec_path):
                    for f in files:
                        if f.endswith(".md") and spec_id.lower() in f.lower():
                            try:
                                with open(os.path.join(root, f), "r", encoding="utf-8", errors="replace") as fp:
                                    spec_file = fp.read()
                                    spec_path = os.path.join(root, f)
                                    break
                            except Exception:
                                continue

        # Parse architecture link from docs/MAP.md if available
        architecture_link = None
        map_path = Path("/home/jagosan/repos/HermesKarma/docs/MAP.md")
        if map_path.exists():
            map_content = self._read_file_safe(map_path)
            if map_content:
                # Try to find reference to this spec
                if spec_id.lower() in map_content.lower():
                    architecture_link = "/docs/MAP.md"

        return {
            "spec_id": spec_id,
            "card": target_card,
            "spec_file_path": spec_path,
            "architecture_link": architecture_link,
            "parsed_spec_content": spec_file[:5000] if spec_file else None,  # Truncate for response
        }

    def get_logs(self, lines: int = 100) -> Dict[str, Any]:
        """Get recent execution logs."""
        logs = {
            "cr_runner_log": [],
            "cr_governor_log": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        # Read cr_runner.log
        if CR_RUNNER_LOG_PATH.exists():
            try:
                with open(CR_RUNNER_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    lines_list = f.readlines()
                    logs["cr_runner_log"] = [line.rstrip() for line in lines_list[-lines:]]
            except Exception:
                pass

        # Read cr_governor.log
        if CR_GOVERNOR_LOG_PATH.exists():
            try:
                with open(CR_GOVERNOR_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    lines_list = f.readlines()
                    logs["cr_governor_log"] = [line.rstrip() for line in lines_list[-lines:]]
            except Exception:
                pass

        return logs


# Global service instance
overseer_service = OverseerService()
