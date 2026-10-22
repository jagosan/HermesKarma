"""Analytics API endpoints for Hermes Karma."""
from fastapi import APIRouter, Query
from api.services.hermes_reader import hermes_reader

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


@router.get("/overview")
def get_analytics_overview(time_range: str = "30d"):
    """Retrieve high-level token metrics, costs, cache hit rate, and tool/model distribution."""
    return hermes_reader.get_analytics_overview(time_range=time_range)


@router.get("/context-tax")
def get_context_tax(days: int = Query(30, ge=1, le=365)):
    """SPEC-HK-007: 4-tier Context Tax, Spatial Navigation ROI, compaction
    & amnesia stats, Top Thrash Files, and ingress gateway attribution over a
    rolling window (default 30 days)."""
    return hermes_reader.get_context_tax_analytics(days=days)
