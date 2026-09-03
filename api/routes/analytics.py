"""Analytics API endpoints for Hermes Karma."""
from fastapi import APIRouter
from api.services.hermes_reader import hermes_reader

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


@router.get("/overview")
def get_analytics_overview(time_range: str = "all"):
    """Retrieve high-level token metrics, costs, cache hit rate, and tool/model distribution."""
    return hermes_reader.get_analytics_overview(time_range=time_range)
