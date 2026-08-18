"""Ticket Linking & Webhook Receiver API endpoints for GitHub, Linear, and Jira."""
from fastapi import APIRouter, HTTPException, Request, Header, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from api.services.metadata_service import metadata_service
from api.services.webhook_service import webhook_service
from api.config import GITHUB_WEBHOOK_SECRET, LINEAR_WEBHOOK_SECRET, JIRA_WEBHOOK_SECRET

router = APIRouter(prefix="/api", tags=["Tickets & Webhooks"])


class AddTicketRequest(BaseModel):
    provider: str  # github, linear, jira, etc.
    ticket_key: str  # ENG-402, #104, PROJ-123
    url: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = "open"
    assignee: Optional[str] = None
    description: Optional[str] = None


class TicketSyncRequest(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    assignee: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Ticket Linking & Query Endpoints
# ---------------------------------------------------------------------------

@router.get("/tickets")
def list_all_tickets(
    provider: Optional[str] = Query(None, description="Filter by provider (github, linear, jira)"),
    status: Optional[str] = Query(None, description="Filter by status (open, closed, merged, etc.)"),
    search: Optional[str] = Query(None, description="Search query across ticket keys, titles, session IDs"),
):
    """Retrieve all linked tickets across sessions with optional filters."""
    tickets = metadata_service.get_all_tickets(provider=provider, status=status, search=search)
    return {
        "tickets": tickets,
        "total": len(tickets),
    }


@router.get("/tickets/{ticket_key}/sessions")
def get_sessions_for_ticket(
    ticket_key: str,
    provider: Optional[str] = Query(None, description="Optional provider filter"),
):
    """Get all Hermes sessions linked to a specific ticket key."""
    links = metadata_service.get_sessions_by_ticket(ticket_key=ticket_key, provider=provider)
    return {
        "ticket_key": ticket_key,
        "provider": provider,
        "session_count": len(links),
        "links": links,
    }


@router.post("/sessions/{session_id}/tickets")
def link_ticket(session_id: str, body: AddTicketRequest):
    """Link a ticket key or URL to a Hermes session."""
    meta = metadata_service.add_ticket_link(
        session_id=session_id,
        provider=body.provider,
        ticket_key=body.ticket_key,
        url=body.url,
        title=body.title,
        status=body.status or "open",
        assignee=body.assignee,
        description=body.description,
    )
    return meta


@router.delete("/sessions/{session_id}/tickets/{ticket_key}")
def unlink_ticket(session_id: str, ticket_key: str):
    """Remove a ticket link from a session."""
    metadata_service.remove_ticket_link(session_id=session_id, ticket_key=ticket_key)
    return {"success": True, "session_id": session_id, "ticket_key": ticket_key}


@router.post("/tickets/{provider}/{ticket_key}/sync")
def sync_ticket_status(provider: str, ticket_key: str, body: TicketSyncRequest):
    """Manually update or sync ticket details across all linked sessions."""
    rows_updated = metadata_service.update_ticket_status_by_key(
        provider=provider,
        ticket_key=ticket_key,
        status=body.status,
        title=body.title,
        url=body.url,
        assignee=body.assignee,
        raw_data=body.raw_data,
    )
    return {
        "success": True,
        "provider": provider,
        "ticket_key": ticket_key,
        "rows_updated": rows_updated,
    }


# ---------------------------------------------------------------------------
# Webhook Receivers (GitHub, Linear, Jira, Generic)
# ---------------------------------------------------------------------------

@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header("push", alias="X-GitHub-Event"),
    x_github_delivery: Optional[str] = Header(None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """Receive and process incoming GitHub webhooks (Issues, PRs, Comments, Push)."""
    raw_body = await request.body()
    
    # Signature verification
    if not webhook_service.verify_github_signature(raw_body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {str(e)}")

    res = webhook_service.handle_github_webhook(
        event_type=x_github_event or "unknown",
        payload=payload,
        delivery_id=x_github_delivery,
    )
    return res


@router.post("/webhooks/linear")
async def linear_webhook(
    request: Request,
    linear_delivery: Optional[str] = Header(None, alias="Linear-Delivery"),
    linear_signature: Optional[str] = Header(None, alias="Linear-Signature"),
):
    """Receive and process incoming Linear webhooks (Issues, Comments)."""
    raw_body = await request.body()

    # Signature verification
    if not webhook_service.verify_linear_signature(raw_body, linear_signature):
        raise HTTPException(status_code=401, detail="Invalid Linear webhook signature")

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {str(e)}")

    res = webhook_service.handle_linear_webhook(
        payload=payload,
        delivery_id=linear_delivery,
    )
    return res


@router.post("/webhooks/jira")
async def jira_webhook(
    request: Request,
    token: Optional[str] = Query(None, description="Jira webhook security token"),
    x_atlassian_token: Optional[str] = Header(None, alias="X-Atlassian-Token"),
):
    """Receive and process incoming Jira webhooks (Issue Created, Updated, Comments)."""
    auth_token = token or x_atlassian_token
    if not webhook_service.verify_jira_signature(auth_token):
        raise HTTPException(status_code=401, detail="Invalid Jira webhook token/secret")

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {str(e)}")

    res = webhook_service.handle_jira_webhook(
        payload=payload,
    )
    return res


@router.post("/webhooks/{provider}")
async def generic_webhook(
    provider: str,
    request: Request,
):
    """Unified or custom webhook receiver endpoint."""
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {str(e)}")

    if provider.lower() == "github":
        event = request.headers.get("X-GitHub-Event", "generic")
        delivery = request.headers.get("X-GitHub-Delivery")
        return webhook_service.handle_github_webhook(event_type=event, payload=payload, delivery_id=delivery)
    elif provider.lower() == "linear":
        delivery = request.headers.get("Linear-Delivery")
        return webhook_service.handle_linear_webhook(payload=payload, delivery_id=delivery)
    elif provider.lower() == "jira":
        return webhook_service.handle_jira_webhook(payload=payload)
    else:
        return webhook_service.handle_generic_webhook(provider=provider, payload=payload)


# ---------------------------------------------------------------------------
# Webhook Status & Logs Endpoints
# ---------------------------------------------------------------------------

@router.get("/webhooks/events")
def list_webhook_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    provider: Optional[str] = Query(None),
):
    """Retrieve audit history of received webhook events."""
    events = metadata_service.get_webhook_events(limit=limit, offset=offset, provider=provider)
    return {
        "events": events,
        "count": len(events),
        "limit": limit,
        "offset": offset,
    }


@router.get("/webhooks/status")
def get_webhook_status():
    """Retrieve configured webhook receiver endpoints and summary statistics."""
    stats = metadata_service.get_webhook_stats()
    return {
        "endpoints": {
            "github": "/api/webhooks/github",
            "linear": "/api/webhooks/linear",
            "jira": "/api/webhooks/jira",
            "generic": "/api/webhooks/{provider}",
        },
        "secrets_configured": {
            "github": bool(GITHUB_WEBHOOK_SECRET),
            "linear": bool(LINEAR_WEBHOOK_SECRET),
            "jira": bool(JIRA_WEBHOOK_SECRET),
        },
        "stats": stats,
    }
