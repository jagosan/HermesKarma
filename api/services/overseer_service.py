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

        columns: Dict[str, List[Dict[str, Any]]] = {
            "backlog": [],
            "queued": [],
            "expedited": [],
            "running": [],
            "paused": [],
            "review": [],
            "done": []
        }

        content = self._read_file_safe(path)
        if content is None:
            return {"cards": [], "columns": {}, "counts": {}}
        if not content:
            return {
                "cards": [],
                "columns": columns,
                "counts": {c: 0 for c in columns},
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

        lines = content.split("\n")
        cards = []
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
                    if current_column in columns:
                        columns[current_column].append(card)

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
        spec_id = spec_id.strip()
        if not spec_id:
            return None  # Empty spec_id is invalid
        spec_id = spec_id.split()[0]
        
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
                    if field == "stall_count":
                        state[field] = 0
                    elif field == "remediation_tried":
                        state[field] = ""
                    else:
                        state[field] = None
                    
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
        """Get detailed information for a specific spec, including review dossier."""
        kanban = self.parse_kanban_spec_queue()
        target_card = None

        for card in kanban.get("cards", []):
            if card.get("spec_id", "").upper() == spec_id.upper():
                target_card = card
                break

        if not target_card:
            board_path = self._get_kanban_path()
            if board_path.exists():
                for line in board_path.read_text(encoding="utf-8", errors="replace").split("\n"):
                    if spec_id.upper() in line.upper():
                        target_card = {
                            "spec_id": spec_id.upper(),
                            "raw_line": line,
                            "title": line.strip(),
                            "column": "unknown"
                        }
                        break

        if not target_card:
            raise HTTPException(status_code=404, detail=f"Spec {spec_id} not found")

        # Determine workspace
        raw_ws = target_card.get("workspace", "") or ""
        workspace = raw_ws.replace("`", "").replace('"', "").strip()
        card_line = str(target_card.get("raw_line", ""))
        if not workspace and ("HermesKarma" in card_line or "SPEC-HK" in spec_id):
            workspace = "/home/jagosan/repos/HermesKarma"
        elif not workspace and ("playground" in card_line or "PLAY" in spec_id):
            workspace = "/home/jagosan/repos/playground"
        elif not workspace and ("sunlit" in card_line or "SUN" in spec_id):
            workspace = "/home/jagosan/repos/sunlit-hills"
        elif not workspace and ("honeydew" in card_line or "HONEY" in spec_id):
            workspace = "/home/jagosan/repos/honeydew"
        elif not workspace and ("homelab" in card_line or "HL" in spec_id):
            workspace = "/home/jagosan/obsidian/vault/homelab"

        # Resolve spec file
        spec_content = ""
        spec_path = None
        candidate_paths = []

        if target_card.get("spec_path"):
            sp = target_card["spec_path"].replace("`", "").replace('"', "").strip()
            if workspace:
                candidate_paths.append(Path(workspace) / sp)
            candidate_paths.append(HERMES_DIR.parent / "obsidian/vault" / sp)
            candidate_paths.append(HERMES_DIR.parent / "obsidian/vault/homelab" / sp)
            candidate_paths.append(Path("/home/jagosan/repos/HermesKarma") / sp)

        if workspace and Path(workspace).exists():
            ws_specs = Path(workspace) / "specs"
            if ws_specs.exists():
                for sf in ws_specs.glob("*.md"):
                    candidate_paths.append(sf)

        vault_dir = HERMES_DIR.parent / "obsidian/vault"
        if (vault_dir / "homelab/specs").exists():
            for sf in (vault_dir / "homelab/specs").glob("*.md"):
                candidate_paths.append(sf)

        m_num = re.search(r"(\d+)", spec_id)
        num_str = m_num.group(1) if m_num else ""

        for cp in candidate_paths:
            if not cp.exists() or not cp.is_file():
                continue
            c_stem = cp.stem.lower()
            if spec_id.lower() in c_stem or (num_str and f"{int(num_str):02d}-" in c_stem):
                try:
                    spec_content = cp.read_text(encoding="utf-8", errors="replace")
                    spec_path = str(cp)
                    break
                except Exception:
                    continue

        # Extract Executive Summary / Objective
        summary = ""
        if spec_content:
            m_sum = re.search(
                r"## (?:1\.\s*)?(?:Executive Summary|Objective(?:\s*&\s*Scope)?)(.*?)(?=\n## |\Z)",
                spec_content,
                re.DOTALL | re.IGNORECASE,
            )
            if m_sum:
                summary = m_sum.group(1).strip()
            else:
                paragraphs = [p.strip() for p in spec_content.split("\n\n") if p.strip() and not p.strip().startswith("#")]
                if paragraphs:
                    summary = "\n\n".join(paragraphs[:3])

        # Extract Concerns / Invariants / Failure Domains
        concerns = []
        if spec_content:
            m_concerns = re.search(
                r"## (?:[0-9.]+\s*)?(?:Quality Invariants|Invariants|Security Guardrails|Pitfalls|Concerns|Failure Domains)(.*?)(?=\n## |\Z)",
                spec_content,
                re.DOTALL | re.IGNORECASE,
            )
            if m_concerns:
                c_text = m_concerns.group(1).strip()
                for line in c_text.split("\n"):
                    l_str = line.strip()
                    if l_str and (l_str[0].isdigit() or l_str.startswith(("-", "*"))):
                        concerns.append(l_str)
                if not concerns and c_text:
                    concerns = [c_text[:400]]

        # Extract "What Was Done" (Subtasks from project board)
        what_was_done = []
        board_ref = target_card.get("board", "") or ""
        board_clean = board_ref.replace("[[", "").replace("]]", "").replace("Board:", "").strip()
        if board_clean:
            b_path = HERMES_DIR.parent / "obsidian/vault" / (board_clean if board_clean.startswith("boards/") else f"boards/{board_clean}")
            if b_path.exists():
                try:
                    b_text = b_path.read_text(encoding="utf-8", errors="replace")
                    for bl in b_text.split("\n"):
                        bl_clean = bl.strip()
                        if spec_id.upper() in bl_clean.upper() or (num_str and f"Spec {int(num_str)}" in bl_clean) or (num_str and f"Spec {num_str}" in bl_clean):
                            if bl_clean.startswith("- ["):
                                what_was_done.append(bl_clean)
                except Exception:
                    pass

        # Query Git in workspace
        git_commits = []
        git_branch = ""
        git_commit_hash = ""
        if workspace and Path(workspace).exists():
            try:
                rc_b = subprocess.run(["git", "-C", workspace, "branch", "--show-current"], capture_output=True, text=True, timeout=5)
                git_branch = rc_b.stdout.strip()
                rc_h = subprocess.run(["git", "-C", workspace, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
                git_commit_hash = rc_h.stdout.strip()
                rc_log = subprocess.run(["git", "-C", workspace, "log", "-n", "6", "--oneline"], capture_output=True, text=True, timeout=5)
                if rc_log.stdout.strip():
                    git_commits = rc_log.stdout.strip().split("\n")
            except Exception:
                pass

        # Where Available / How to Test
        availability = {
            "workspace": workspace,
            "branch": git_branch,
            "commit": git_commit_hash,
            "urls": [],
            "test_instructions": "",
        }
        if "HermesKarma" in (workspace or "") or "SPEC-HK" in spec_id:
            availability["urls"].append({"label": "Live Hermes Karma (Tailscale)", "url": "https://karmes.jagosan.com"})
            availability["urls"].append({"label": "Local Dev Server", "url": "http://localhost:8020"})
            availability["test_instructions"] = "Open https://karmes.jagosan.com and navigate to the Overseer tab. Test suite: pytest tests/test_overseer.py"
        elif "playground" in (workspace or "") or "PLAY" in spec_id:
            availability["urls"].append({"label": "Lunar Frontier Web Client", "url": "http://localhost:5173"})
            availability["urls"].append({"label": "Tailscale Frontier", "url": "https://playground.jagosan.com"})
            availability["test_instructions"] = "cd /home/jagosan/repos/playground && npm run dev. NullEngine tests: npm test"
        elif "sunlit" in (workspace or "") or "SUN" in spec_id:
            availability["urls"].append({"label": "Home Assistant Dashboard", "url": "http://localhost:8123"})
            availability["test_instructions"] = "Inspect Frigate camera streams and MQTT events via mosquitto_sub -t 'sunlit/#'"
        elif "honeydew" in (workspace or "") or "HONEY" in spec_id:
            availability["urls"].append({"label": "Honeydew Web App", "url": "http://localhost:3000"})
            availability["test_instructions"] = "cd /home/jagosan/repos/honeydew && npm run dev"

        # Discover Screenshots & Visual Assets
        screenshots = []
        if workspace and Path(workspace).exists():
            ws_path = Path(workspace)
            for ad in [ws_path / "docs/assets", ws_path / "assets", ws_path / "screenshots", ws_path / "docs"]:
                if ad.exists():
                    for img in ad.glob("*"):
                        if img.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"):
                            screenshots.append({
                                "filename": img.name,
                                "path": str(img),
                                "api_url": f"/api/overseer/image?path={img}"
                            })

        if spec_content:
            img_refs = re.findall(r"!\[(.*?)\]\((.*?)\)", spec_content)
            for alt, ref in img_refs:
                if not ref.startswith("http"):
                    resolved_ref = (Path(spec_path).parent / ref if spec_path else Path(workspace or ".") / ref).resolve()
                    if resolved_ref.exists() and str(resolved_ref) not in [s["path"] for s in screenshots]:
                        screenshots.append({
                            "filename": resolved_ref.name,
                            "alt": alt,
                            "path": str(resolved_ref),
                            "api_url": f"/api/overseer/image?path={resolved_ref}"
                        })

        return {
            "spec_id": spec_id.upper(),
            "card": target_card,
            "title": target_card.get("title", ""),
            "column": target_card.get("column", ""),
            "priority": target_card.get("priority"),
            "engine": target_card.get("engine", "tigger"),
            "spec_path": spec_path,
            "summary": summary,
            "what_was_done": what_was_done,
            "git_commits": git_commits,
            "availability": availability,
            "concerns": concerns,
            "screenshots": screenshots[:6],
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

        rc = subprocess.run(
            ["git", "-C", str(git_dir), "add", "boards/Kanban-Spec-Queue.md"],
            capture_output=True,
            text=True,
            timeout=30
        ).returncode

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

    def _move_card_in_board(
        self,
        board_path: Path,
        spec_id: str,
        dest_column_name: str,
        mark_checked: bool = False,
        set_p0: bool = False,
    ) -> bool:
        """Atomically move a spec card to a destination column across markdown section headers."""
        if not board_path.exists():
            return False

        content = board_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")

        columns = [
            "📥 Backlog",
            "⏳ Queued",
            "🚨 Expedited",
            "🚧 Running",
            "⏸️ Paused",
            "🧪 Review & Ready to Test",
            "✅ Done & Archived",
        ]

        sections = {col: [] for col in columns}
        preamble = []
        current_col = None
        extracted_card = None

        for line in lines:
            stripped = line.strip()
            matched_col = None
            if stripped.startswith("## "):
                header_text = stripped[3:].strip()
                for col in columns:
                    if header_text in col or col in header_text:
                        matched_col = col
                        break

            if matched_col:
                current_col = matched_col
            elif current_col:
                # Check if this line is our target card
                if spec_id.upper() in stripped.upper() and (
                    f"**[{spec_id.upper()}]**" in stripped.upper() or f"**{spec_id.upper()}**" in stripped.upper()
                ):
                    extracted_card = line
                else:
                    sections[current_col].append(line)
            else:
                preamble.append(line)

        if not extracted_card:
            # Fallback looser search across lines
            for col, col_lines in sections.items():
                for idx, cl in enumerate(col_lines):
                    if spec_id.upper() in cl.upper():
                        extracted_card = col_lines.pop(idx)
                        break
                if extracted_card:
                    break

        if not extracted_card:
            return False

        card_line = extracted_card
        if mark_checked:
            card_line = re.sub(r"^(\s*-\s*)\[.\]", r"\1[x]", card_line)
            if "(Archived)" not in card_line:
                card_line = f"{card_line.rstrip()} (Archived)"
        else:
            card_line = re.sub(r"^(\s*-\s*)\[.\]", r"\1[ ]", card_line)

        if set_p0:
            if "#p0" not in card_line:
                if re.search(r"#p[12]\b", card_line):
                    card_line = re.sub(r"#p[12]\b", "#p0", card_line, count=1)
                else:
                    card_line = f"{card_line.rstrip()} #p0"

        # Resolve destination column
        target_col = None
        for col in columns:
            if dest_column_name.lower() in col.lower() or col.lower() in dest_column_name.lower():
                target_col = col
                break
        if not target_col:
            target_col = dest_column_name

        sections[target_col].append(card_line)

        out = []
        if preamble:
            out.extend(preamble)

        for col in columns:
            out.append(f"## {col}")
            for l in sections[col]:
                if l.strip():
                    out.append(l)
            out.append("")

        new_content = "\n".join(out).rstrip() + "\n"
        return self._write_file_atomic(board_path, new_content)

    def expedite_spec(self, spec_id: str) -> Dict[str, Any]:
        """Move spec card to Expedited column with #p0 priority."""
        try:
            board_path = self._get_kanban_path()
            if not board_path.exists():
                raise HTTPException(status_code=500, detail="Kanban-Spec-Queue.md not found")

            if not self._move_card_in_board(board_path, spec_id, "🚨 Expedited", mark_checked=False, set_p0=True):
                raise HTTPException(status_code=404, detail=f"Spec {spec_id} not found in spec queue board")

            if not self._commit_vault_changes(f"chore(spec-queue): expedite {spec_id} (#p0)"):
                pass

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
        """Move spec card from Review to Done & Archived."""
        try:
            board_path = self._get_kanban_path()
            if not board_path.exists():
                raise HTTPException(status_code=500, detail="Kanban-Spec-Queue.md not found")

            if not self._move_card_in_board(board_path, spec_id, "✅ Done & Archived", mark_checked=True):
                raise HTTPException(status_code=404, detail=f"Spec {spec_id} not found in spec queue board")

            # Also attempt to mark done on referenced project board if available
            kanban = self.parse_kanban_spec_queue()
            for card in kanban.get("cards", []):
                if card.get("spec_id", "").upper() == spec_id.upper() and card.get("board"):
                    b_ref = card["board"].replace("[[", "").replace("]]", "").replace("Board:", "").strip()
                    b_file = HERMES_DIR.parent / "obsidian/vault" / (b_ref if b_ref.startswith("boards/") else f"boards/{b_ref}")
                    if b_file.exists():
                        try:
                            b_text = b_file.read_text(encoding="utf-8", errors="replace")
                            b_updated = re.sub(rf"^(\s*-\s*)\[.\](\s*\*\*\[?{re.escape(spec_id)}\b)", r"\1[x]\2", b_text, flags=re.MULTILINE | re.IGNORECASE)
                            self._write_file_atomic(b_file, b_updated)
                            subprocess.run(["git", "-C", str(self._get_vault_git_dir()), "add", str(b_file)], capture_output=True, timeout=10)
                        except Exception:
                            pass

            if not self._commit_vault_changes(f"chore(spec-queue): archive {spec_id}"):
                pass

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
