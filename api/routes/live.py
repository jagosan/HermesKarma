"""Live Telemetry & Terminal Focus API endpoints."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse
from api.services.live_tracker import live_tracker
from api.services.terminal_focus import terminal_focus_service
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/live-sessions", tags=["Live Telemetry"])


class FocusTerminalRequest(BaseModel):
    pid: Optional[int] = None
    tmux_pane: Optional[str] = None
    tty: Optional[str] = None


@router.get("")
def get_live_sessions():
    """Retrieve snapshot of active/waiting/recent sessions."""
    return {
        "live_sessions": live_tracker.get_live_sessions()
    }


@router.get("/stream")
async def stream_live_sessions(interval: float = Query(2.0, ge=0.5, le=10.0)):
    """Server-Sent Events (SSE) stream for real-time dashboard telemetry."""
    return EventSourceResponse(live_tracker.stream_live_events(interval=interval))


@router.post("/{session_id}/focus-terminal")
def focus_terminal(session_id: str, body: Optional[FocusTerminalRequest] = None):
    """Trigger OS-level IPC to focus the active terminal window or tmux pane."""
    pid = body.pid if body else None
    tmux_pane = body.tmux_pane if body else None
    tty = body.tty if body else None
    result = terminal_focus_service.focus_session(
        session_id=session_id,
        pid=pid,
        tmux_pane=tmux_pane,
        tty=tty
    )
    return result
