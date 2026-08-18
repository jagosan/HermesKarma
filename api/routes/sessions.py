"""Sessions API endpoints for Hermes Karma."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from api.services.hermes_reader import hermes_reader
from api.services.metadata_service import metadata_service
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api/sessions", tags=["Sessions"])


class SessionMetaUpdateRequest(BaseModel):
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    starred: Optional[bool] = None
    custom_name: Optional[str] = None


@router.get("")
def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: Optional[str] = None,
    model: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[float] = None,
    date_to: Optional[float] = None,
):
    """Retrieve filtered sessions list with metadata and ticket links."""
    return hermes_reader.get_sessions(
        limit=limit,
        offset=offset,
        source=source,
        model=model,
        search=search,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/{session_id}")
def get_session(session_id: str):
    """Retrieve session details, model usage, and transcript."""
    session = hermes_reader.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = hermes_reader.get_session_messages(session_id)
    return {
        **session,
        "messages": messages,
    }


@router.get("/{session_id}/timeline")
def get_session_timeline(session_id: str):
    """Retrieve structured chronological event stream for trajectory playback."""
    session = hermes_reader.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    timeline = hermes_reader.get_session_timeline(session_id)
    return {
        "session_id": session_id,
        "title": session.get("title"),
        "model": session.get("model"),
        "started_at": session.get("started_at"),
        "total_steps": len(timeline),
        "timeline": timeline,
    }


@router.get("/{session_id}/subagents")
def get_session_subagents(session_id: str):
    """Retrieve child delegations and subagents for this session."""
    session = hermes_reader.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    subagents = hermes_reader.get_session_subagents(session_id)
    return {
        "session_id": session_id,
        "subagents": subagents,
    }


@router.patch("/{session_id}/metadata")
def update_session_metadata(session_id: str, body: SessionMetaUpdateRequest):
    """Update custom tags, notes, and starred state."""
    session = hermes_reader.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return metadata_service.update_session_meta(
        session_id=session_id,
        tags=body.tags,
        notes=body.notes,
        starred=body.starred,
        custom_name=body.custom_name,
    )
