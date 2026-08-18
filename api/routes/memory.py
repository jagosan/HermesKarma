"""Memory & User Profile API endpoints for Hermes Karma."""
from fastapi import APIRouter
from api.services.hermes_reader import hermes_reader

router = APIRouter(prefix="/api/memory", tags=["Memory"])


@router.get("")
def get_memory_snapshot():
    """Retrieve structured memory items and user profile state."""
    return hermes_reader.get_memory_state()
