"""Overseer API routes for SPEC-HK-010 (TASK-HK-143).

REST & SSE endpoints for overseer state monitoring.
"""
import asyncio
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.services.overseer_service import overseer_service

router = APIRouter(prefix="/api/overseer", tags=["Overseer"])

# SSE headers: disable caches/proxies so deltas reach the frontend at intended cadence.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform, must-revalidate",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Constants
HERMES_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
KANBAN_SPEC_QUEUE_PATH = HERMES_DIR.parent / "obsidian/vault/boards/Kanban-Spec-Queue.md"
CR_RUNNER_STATE_PATH = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "cr_runner_state.json"


@router.get("/overview")
async def get_overview():
    """Get complete unified state of all projects, spec queue, runner status."""
    return overseer_service.get_overview()


@router.get("/spec/{spec_id}")
async def get_spec_details(spec_id: str):
    """Get detailed information for a specific spec."""
    return overseer_service.get_spec_details(spec_id)


@router.get("/logs")
async def get_logs(lines: int = Query(100, ge=1, le=1000)):
    """Get recent execution logs from cr_runner.log and cr_governor.log."""
    return overseer_service.get_logs(lines)


@router.get("/stream")
async def stream_overseer_updates(
    interval: float = Query(2.0, ge=0.5, le=10.0),
    max_events: int = Query(None, ge=1, description="Maximum events to emit before closing stream"),
):
    """Server-Sent Events stream for overseer state updates.

    Emits deltas when cr_runner_state.json mtime, lockfile state, or queue
    board mtime changes. Supports ?max_events=N to prevent test client hangs.
    """
    last_mtime = None
    last_lockfile = None
    event_count = 0

    async def generate_events():
        nonlocal last_mtime, last_lockfile, event_count

        while True:
            try:
                # Check cr_runner_state.json mtime
                if CR_RUNNER_STATE_PATH.exists():
                    current_mtime = CR_RUNNER_STATE_PATH.stat().st_mtime
                else:
                    current_mtime = None

                # Check lockfile state
                runner_paused_path = HERMES_DIR / "runner_paused"
                current_lockfile = runner_paused_path.exists()

                # Check Kanban-Spec-Queue.md mtime
                if KANBAN_SPEC_QUEUE_PATH.exists():
                    queue_mtime = KANBAN_SPEC_QUEUE_PATH.stat().st_mtime
                else:
                    queue_mtime = None

                # Only emit if something changed
                if current_mtime != last_mtime or current_lockfile != last_lockfile or queue_mtime != last_mtime:
                    overview = overseer_service.get_overview()
                    data = {
                        "timestamp": overview["timestamp"],
                        "kanban_spec_queue": overview["kanban_spec_queue"]["counts"],
                        "runner_state": overview["runner_state"],
                        "lockfile_paused": current_lockfile,
                    }

                    yield f"data: {json.dumps(data)}\n\n"
                    event_count += 1

                    # Update last known state
                    last_mtime = current_mtime if current_mtime is not None else queue_mtime
                    last_lockfile = current_lockfile

                # Check max_events limit
                if max_events is not None and event_count >= max_events:
                    break

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception:
                # On error, emit an error event and stop
                yield "data: {\"error\": \"stream error\"}\n\n"
                break
@router.post("/action")
async def execute_action(action_request: dict):
    """Execute operator commands with validation and atomic file mutations."""
    from fastapi import Query
    
    action = action_request.get("action")
    spec_id = action_request.get("spec_id")
    notes = action_request.get("notes", "")
    
    valid_actions = ["pause_runner", "resume_runner", "expedite_spec", "archive_spec", "step_phase"]
    
    if not action:
        raise HTTPException(status_code=400, detail="Action is required")
    
    if action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"Invalid action. Must be one of: {valid_actions}")
    
    # Validate spec_id for actions that require it
    if action in ["expedite_spec", "archive_spec"]:
        if not spec_id:
            raise HTTPException(status_code=400, detail="spec_id is required for this action")
    
    try:
        if action == "pause_runner":
            result = overseer_service.pause_runner()
        elif action == "resume_runner":
            result = overseer_service.resume_runner()
        elif action == "expedite_spec":
            result = overseer_service.expedite_spec(spec_id)
        elif action == "archive_spec":
            result = overseer_service.archive_spec(spec_id)
        elif action == "step_phase":
            result = overseer_service.step_phase()
        
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to execute {action}: {str(e)}")




    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
