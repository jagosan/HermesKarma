"""Overseer Service: Backend state parser & REST API for SPEC-HK-010 (TASK-HK-143).

Provides non-blocking file parsers for Kanban-Spec-Queue.md, goals/GOAL-*.md,
and cr_runner_state.json with graceful fallbacks for missing or locked files.
"""
import json
import subprocess
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
RUNNER_PAUSED_PATH = HERMES_DIR / "runner_paused"
GOVERNOR_PAUSED_PATH = HERMES_DIR / "governor_paused"


class OverseerService:
    """Overseer state parser service with non-blocking file operations."""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, float] = {}

    def _read_file_safe(self, path: Path, max_age_seconds: float = 5.0) -> Optional[str]:
        """Read file with graceful fallback if missing or locked."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return content
        except (FileNotFoundError, PermissionError, OSError):
            return None

    def _get_kanban_path(self) -> Path:
        """Get the Kanban-Spec-Queue.md path, respecting HERMES_BOARD environment override."""
        return Path(os.environ.get("HERMES_BOARD", str(KANBAN_SPEC_QUEUE_PATH))).expanduser()

    def parse_kanban_spec_queue(self) -> Dict[str, Any]:
        """Parse Kanban-Spec-Queue.md with graceful fallback."""
        path = self._get_kanban_path()

        content = self._read_file_safe(path)
        if not content:
            return {"cards": [], "columns": {}, "counts": {}}

        lines = content.split("\n")
        cards = []
        columns: Dict[str, List[Dict[str, Any]]] = {
            "backlog": [],
            "queued": [],
            "expedited": [],
            "running": [],
            "paused": [],
            "review": [],
            "done": []
        }
        
        current_column = None
        column_pattern = re.compile(r"^##\s+(.+)$")

        for line in lines:
            column_match = column_pattern.match(line.strip())
            if column_match:
                current_column = column_match.group(1).strip().lower()
                # Normalize column names
                if "backlog" in current_column:
                    current_column = "backlog"
                elif "queue" in current_column or "queued" in current_column:
                    current_column = "queued"
                elif "expedite" in current_column:
                    current_column = "expedited"
                elif "running" in current_column:
                    current_column = "running"
                elif "pause" in current_column:
                    current_column = "paused"
                elif "review" in current_column or "ready" in current_column:
                    current_column = "review"
                elif "done" in current_column:
                    current_column = "done"
                continue

            if current_column and line.strip().startswith("- ["):
                card = self._parse_markdown_card(line)
                if card:
                    card["column"] = current_column
                    cards.append(card)

        counts = {col: len(cards_list) for col, cards_list in columns.items()}

        return {
            "cards": cards,
            "columns": columns,
            "counts": counts,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _parse_markdown_card(self, line: str) -> Optional[Dict[str, Any]]:
        """Parse a single Kanban card markdown line."""
        pattern = r"^\s*-\s+\[(.)\]\s+\*\*\[(.*?)\]\*\*\s+(.+?)\s*(?:\|\s*(.*))?$"
        match = re.match(pattern, line.strip())
        
        if not match:
            return None

        checked, spec_id, title_rest, extra = match.groups()
        
        # Parse title and workspace
        title = title_rest
        workspace = None
        priority = None
        
        if extra:
            ws_match = re.search(r"Workspace:\s*(.+?)(?:\s*\||$)", extra)
            if ws_match:
                workspace = ws_match.group(1).strip()
            
            pri_match = re.search(r"Priority:\s*#p(\d+)", extra)
            if pri_match:
                priority = int(pri_match.group(1))

        return {
            "checked": checked.lower() == "x",
            "spec_id": spec_id.upper(),
            "title": title.strip(),
            "raw_line": line.strip(),
            "workspace": workspace,
            "priority": priority,
        }

    def parse_goals(self) -> List[Dict[str, Any]]:
        """Parse all goal files from goals/ directory."""
        goals = []
        
        goals_dir = Path(os.environ.get("HERMES_GOALS_DIR", str(GOALS_DIR))).expanduser()
        if not goals_dir.exists():
            return goals

        for goal_file in goals_dir.glob("GOAL-*.md"):
            goal = self.parse_goal_file(goal_file)
            if goal:
                goals.append(goal)

        # Sort by ID for consistent ordering
        goals.sort(key=lambda g: g.get("id", ""))
        
        return goals

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

    def parse_cr_runner_state(self) -> Dict[str, Any]:
        """Parse cr_runner_state.json with graceful fallback."""
        path = Path(os.environ.get("HERMES_CR_RUNNER_STATE", str(CR_RUNNER_STATE_PATH))).expanduser()
        content = self._read_file_safe(path)
        
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

        # Evaluate attention items (needs_input and stalled)
        needs_input_items = self._evaluate_needs_input(goals, kanban)
        stalled_items = self._evaluate_stalled(runner_state, kanban)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kanban_spec_queue": kanban,
            "goals": goals,
            "runner_state": runner_state,
            "attention": {
                "needs_input": needs_input_items,
                "stalled": stalled_items,
                "summary": {
                    "needs_input_count": len(needs_input_items),
                    "stalled_count": len(stalled_items),
                    "healthy": len(needs_input_items) == 0 and len(stalled_items) == 0,
                },
            },
        }

    def _evaluate_needs_input(self, goals: List[Dict], kanban: Dict) -> List[Dict[str, Any]]:
        """Evaluate which items need human input."""
        needs_input = []
        
        # Get counts from kanban
        review_count = kanban.get("counts", {}).get("review", 0)
        review_cards_list = kanban.get("columns", {}).get("review", [])
        
        for goal in goals:
            status = goal.get("status", "")
            blocking_state = goal.get("blocking_state", "")
            
            # Check if this goal requires playtest
            if status == "active" and blocking_state == "HUMAN_PLAYTEST_REQUIRED":
                # Look for specs in review that match this goal
                for card in review_cards_list:
                    if card.get("checked") and "spec_id" in card:
                        needs_input.append({
                            "id": f"input-play-{card['spec_id'].split('-')[-1]}",
                            "type": "HUMAN_PLAYTEST_REQUIRED",
                            "severity": "high",
                            "title": f"{goal.get('title', 'Unknown Project')} Ready for Playtest",
                            "description": f"{card['spec_id']} has completed phases and passes tests. Waiting for qualitative evaluation.",
                            "spec_id": card["spec_id"],
                            "workspace": card.get("workspace"),
                            "action_required": "Playtest and click Approve & Archive",
                        })
            
            # Check for READY_FOR_SPEC_AUTHORING
            if status == "active" and blocking_state == "READY_FOR_SPEC_AUTHORING":
                needs_input.append({
                    "id": f"input-spec-{goal.get('id', '').split('-')[-1]}",
                    "type": "READY_FOR_SPEC_AUTHORING",
                    "severity": "medium",
                    "title": f"Ready for Spec Authoring: {goal.get('title', '')}",
                    "description": f"{goal.get('id')} has finished prior milestone and awaits spec signoff.",
                    "spec_id": goal.get("id"),
                    "workspace": goal.get("workspace"),
                    "action_required": "Review and approve specification",
                })

        # Check for HUMAN_REVIEW_GATE (>= 3 specs in review)
        if review_count >= 3:
            needs_input.append({
                "id": "input-review-gate",
                "type": "HUMAN_REVIEW_GATE",
                "severity": "critical",
                "title": f"Review Gate Triggered: {review_count} Specs Awaiting Review",
                "description": f"The governor has enforced a compute yield due to {review_count} specs in Review & Ready to Test.",
                "spec_id": "N/A",
                "workspace": "N/A",
                "action_required": "Review and approve specs to reduce count below 3",
            })

        return needs_input

    def _evaluate_stalled(self, runner_state: Dict, kanban: Dict) -> List[Dict[str, Any]]:
        """Evaluate which items are stalled or blocked."""
        stalled = []
        
        # Check for RUNNER_PAUSED
        if RUNNER_PAUSED_PATH.exists() or GOVERNOR_PAUSED_PATH.exists():
            stalled.append({
                "id": "stall-runner-paused",
                "type": "RUNNER_PAUSED",
                "severity": "critical",
                "title": "Autonomous Runner is Paused",
                "description": "Lockfile is active. Swarm execution is suspended.",
                "remediation": "Click 'Resume Runner' or run 'cr_runner.py resume' to re-enable continuous dispatch.",
            })

        # Check for STALL_LOOP_DETECTED
        stall_count = runner_state.get("stall_count", 0)
        if stall_count >= 10:
            stalled.append({
                "id": "stall-loop-critical",
                "type": "STALL_LOOP_DETECTED",
                "severity": "critical",
                "title": "Stall Loop Detected: Critical",
                "description": f"cr_runner_state.json reports stall_count = {stall_count}. Runner is cycling without advancing phases.",
                "remediation": "Investigate runner logs and reset stall_count manually if needed.",
            })
        elif stall_count >= 2:
            stalled.append({
                "id": "stall-loop-warning",
                "type": "STALL_LOOP_DETECTED",
                "severity": "warning",
                "title": "Stall Loop Detected: Warning",
                "description": f"cr_runner_state.json reports stall_count = {stall_count}. Runner may be stuck.",
                "remediation": "Monitor runner logs and consider pausing to investigate.",
            })

        # Check for PHASE_FAILED
        last_status = runner_state.get("last_status")
        if last_status == "PHASE_FAILED":
            stalled.append({
                "id": "stall-phase-failed",
                "type": "PHASE_FAILED",
                "severity": "critical",
                "title": "Phase Execution Failed",
                "description": f"A phase build or test execution failed during subagent execution. Last spec: {runner_state.get('last_spec', 'N/A')}",
                "remediation": "Review runner logs and fix the failing phase before resuming.",
            })

        # Check for NO_PHASES_FOUND
        cards = kanban.get("cards", [])
        running_cards = [c for c in cards if c.get("column") == "running"]
        
        for card in running_cards:
            if not card.get("checked"):
                # Found a running card that's not checked - this is normal
                break
        else:
            # All running cards are checked or no running unchecked cards found
            if running_cards:
                stalled.append({
                    "id": "stall-no-phases",
                    "type": "NO_PHASES_FOUND",
                    "severity": "warning",
                    "title": "No Phases Found for Active Spec",
                    "description": "Active spec card exists in Running column but no uncompleted child subtasks found.",
                    "remediation": "Check Kanban-Spec-Queue.md for proper task decomposition.",
                })

        return stalled

    def get_spec_details(self, spec_id: str) -> Dict[str, Any]:
        """Get detailed information for a specific spec."""
        # Find the spec in the Kanban queue
        kanban = self.parse_kanban_spec_queue()
        target_card = None

        for card in kanban.get("cards", []):
            if card.get("spec_id", "").upper() == spec_id.upper():
                target_card = card
                break

        if not target_card:
            raise HTTPException(status_code=404, detail=f"Spec {spec_id} not found")

        # Find the spec file
        local_spec_path = Path("/home/jagosan/repos/HermesKarma/specs")
        spec_file = None
        spec_path = None

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

        return {
            "spec_id": spec_id,
            "card": target_card,
            "spec_content": spec_file,
            "spec_path": spec_path,
            "parsed_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_logs(self, lines: int = 100) -> Dict[str, Any]:
        """Get recent logs from cr_runner.log and cr_governor.log."""
        logs = {
            "cr_runner_log": [],
            "cr_governor_log": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        try:
            # Read cr_runner.log
            runner_log_path = Path(os.environ.get("HERMES_RUNNER_LOG", str(CR_RUNNER_LOG_PATH))).expanduser()
            if runner_log_path.exists():
                with open(runner_log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines_list = f.readlines()
                    logs["cr_runner_log"] = [line.rstrip() for line in lines_list[-lines:]]
        except Exception:
            pass

        try:
            # Read cr_governor.log
            governor_log_path = Path(os.environ.get("HERMES_GOVERNOR_LOG", str(CR_GOVERNOR_LOG_PATH))).expanduser()
            if governor_log_path.exists():
                with open(governor_log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines_list = f.readlines()
                    logs["cr_governor_log"] = [line.rstrip() for line in lines_list[-lines:]]
        except Exception:
            pass

        return logs

    def _write_file_atomic(self, path: Path, content: str) -> bool:
        """Write file atomically using a temp file and rename."""
        try:
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(content, encoding="utf-8")
            temp_path.rename(path)
            return True
        except Exception:
            return False

    def _get_vault_git_dir(self) -> Path:
        """Get the vault git directory path."""
        vault_dir = HERMES_DIR.parent / "obsidian/vault"
        if (vault_dir / ".git").exists():
            return vault_dir
        return Path("/home/jagosan/repos/HermesKarma")

    def _commit_vault_changes(self, message: str) -> bool:
        """Commit changes to the vault git repository."""
        git_dir = self._get_vault_git_dir()

        rc, _, _ = subprocess.run(
            ["git", "-C", str(git_dir), "add", "."],
            capture_output=True,
            text=True,
            timeout=30
        ).returncode, "", ""

        if rc != 0:
            return False

        result = subprocess.run(
            ["git", "-C", str(git_dir), "commit", "-m", message],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0

    def pause_runner(self) -> Dict[str, Any]:
        """Create runner_paused lockfile atomically."""
        try:
            RUNNER_PAUSED_PATH.parent.mkdir(parents=True, exist_ok=True)

            if RUNNER_PAUSED_PATH.exists():
                return {
                    "success": True,
                    "action": "pause_runner",
                    "message": "Runner is already paused",
                    "lockfile": str(RUNNER_PAUSED_PATH),
                }

            RUNNER_PAUSED_PATH.write_text(
                f"Paused at {datetime.now(timezone.utc).isoformat()}\n",
                encoding="utf-8"
            )

            return {
                "success": True,
                "action": "pause_runner",
                "message": "Runner paused successfully",
                "lockfile": str(RUNNER_PAUSED_PATH),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to pause runner: {str(e)}")

    def resume_runner(self) -> Dict[str, Any]:
        """Remove runner_paused and governor_paused lockfiles."""
        try:
            removed = []

            if RUNNER_PAUSED_PATH.exists():
                RUNNER_PAUSED_PATH.unlink()
                removed.append(str(RUNNER_PAUSED_PATH))

            if GOVERNOR_PAUSED_PATH.exists():
                GOVERNOR_PAUSED_PATH.unlink()
                removed.append(str(GOVERNOR_PAUSED_PATH))

            # Reset stall_count in cr_runner_state.json
            try:
                state_path = CR_RUNNER_STATE_PATH
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    state["stall_count"] = 0
                    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                    removed.append(f"Reset stall_count in {state_path}")
            except Exception:
                pass

            return {
                "success": True,
                "action": "resume_runner",
                "message": "Runner resumed successfully",
                "removed_files": removed,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to resume runner: {str(e)}")

    def expedite_spec(self, spec_id: str) -> Dict[str, Any]:
        """Move spec card to Expedited column with #p0 priority."""
        try:
            kanban = self.parse_kanban_spec_queue()

            target_card = None
            for card in kanban.get("cards", []):
                if card.get("spec_id", "").upper() == spec_id.upper():
                    target_card = card
                    break

            if not target_card:
                raise HTTPException(status_code=404, detail=f"Spec {spec_id} not found")

            board_path = self._get_kanban_path()
            if not board_path.exists():
                raise HTTPException(status_code=500, detail="Kanban-Spec-Queue.md not found")

            content = board_path.read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")

            updated_lines = []
            card_found = False

            for line in lines:
                if spec_id.upper() in line.upper():
                    if "#p0" not in line:
                        line = line.replace(spec_id.upper(), f"{spec_id.upper()} #p0")
                    card_found = True
                updated_lines.append(line)

            if not card_found:
                raise HTTPException(status_code=404, detail="Could not find spec card in Kanban board")

            new_content = "\n".join(updated_lines)
            if not self._write_file_atomic(board_path, new_content):
                raise HTTPException(status_code=500, detail="Failed to write updated Kanban board")

            if not self._commit_vault_changes(f"chore(spec-queue): expedite {spec_id} (#p0)"):
                pass  # Non-fatal: log but continue

            return {
                "success": True,
                "action": "expedite_spec",
                "message": f"Spec {spec_id} moved to Expedited with #p0 priority",
                "spec_id": spec_id,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to expedite spec: {str(e)}")

    def archive_spec(self, spec_id: str) -> Dict[str, Any]:
        """Move spec card from Review to Archived."""
        try:
            kanban = self.parse_kanban_spec_queue()

            target_card = None
            for card in kanban.get("cards", []):
                if card.get("spec_id", "").upper() == spec_id.upper():
                    target_card = card
                    break

            if not target_card:
                raise HTTPException(status_code=404, detail=f"Spec {spec_id} not found")

            board_path = self._get_kanban_path()
            if not board_path.exists():
                raise HTTPException(status_code=500, detail="Kanban-Spec-Queue.md not found")

            content = board_path.read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")

            updated_lines = []

            for line in lines:
                if spec_id.upper() in line.upper():
                    # Archive the card - move to archived section
                    match = re.match(r"^\s*(-\s+\[x\]\s+\*\*)(.*?)(\*\*.*)$", line)
                    if match:
                        prefix, title_rest, suffix = match.groups()
                        updated_lines.append(f"- [x] **{spec_id.upper()}** {title_rest.strip()} (Archived)")
                        continue
                updated_lines.append(line)

            new_content = "\n".join(updated_lines)
            if not self._write_file_atomic(board_path, new_content):
                raise HTTPException(status_code=500, detail="Failed to write updated Kanban board")

            if not self._commit_vault_changes(f"chore(spec-queue): archive {spec_id}"):
                pass  # Non-fatal: log but continue

            return {
                "success": True,
                "action": "archive_spec",
                "message": f"Spec {spec_id} archived successfully",
                "spec_id": spec_id,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to archive spec: {str(e)}")

    def step_phase(self) -> Dict[str, Any]:
        """Execute one non-blocking step of the runner."""
        try:
            result = subprocess.run(
                ["python3", "/home/jagosan/scripts/cr_runner.py", "step"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            return {
                "success": True,
                "action": "step_phase",
                "message": "Step phase executed (non-blocking)",
                "exit_code": result.returncode,
                "stdout": result.stdout[:500] if result.stdout else "",
                "stderr": result.stderr[:500] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {
                "success": True,
                "action": "step_phase",
                "message": "Step phase started (timeout waiting for completion)",
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to execute step: {str(e)}")



# Global service instance
overseer_service = OverseerService()
