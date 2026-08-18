"""Ticket Linking API endpoints for Linear, GitHub, and Jira."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from api.services.metadata_service import metadata_service

router = APIRouter(prefix="/api", tags=["Tickets"])


class AddTicketRequest(BaseModel):
    provider: str  # github, linear, jira
    ticket_key: str  # ENG-402, #104
    url: Optional[str] = None
    title: Optional[str] = None


@router.get("/tickets")
def list_all_tickets():
    """Retrieve all linked tickets across all sessions."""
    return {
        "tickets": metadata_service.get_all_tickets()
    }


@router.post("/sessions/{session_id}/tickets")
def link_ticket(session_id: str, body: AddTicketRequest):
    """Link a ticket key or URL to a session."""
    meta = metadata_service.add_ticket_link(
        session_id=session_id,
        provider=body.provider,
        ticket_key=body.ticket_key,
        url=body.url,
        title=body.title,
    )
    return meta


@router.delete("/sessions/{session_id}/tickets/{ticket_key}")
def unlink_ticket(session_id: str, ticket_key: str):
    """Remove a ticket link from a session."""
    metadata_service.remove_ticket_link(session_id=session_id, ticket_key=ticket_key)
    return {"success": True, "session_id": session_id, "ticket_key": ticket_key}
