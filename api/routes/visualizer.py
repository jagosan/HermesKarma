"""Homelab 3D Spatial Visualizer API endpoints (SPEC-HK-009, TASK-HK-137).

Strictly read-only per architecture blueprint §5.3: the 3D viewport can never
dispatch mutative fleet controls through these routes. All payloads are
sanitized of private Tailscale/LAN IP material by VisualizerService.
"""
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from api.services.visualizer_service import visualizer_service

router = APIRouter(prefix="/api/visualizer", tags=["Homelab 3D Visualizer"])

# SSE headers: disable caches/proxies (incl. nginx buffering) so deltas reach
# the WebGL scene at the intended cadence.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform, must-revalidate",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.get("/topology")
async def get_topology():
    """Static declarative homelab layout: node chassis archetypes, spatial &
    network-topology coordinates, hardware capacities, docked services, and
    conduit definitions (SPEC-HK-009 §6.1)."""
    return visualizer_service.get_topology()


@router.get("/stream")
async def stream_telemetry(
    interval: float = Query(1.0, ge=0.25, le=10.0, description="Telemetry delta cadence in seconds"),
):
    """Server-Sent Events stream of 1Hz fleet telemetry deltas for the 3D
    scene (SPEC-HK-009 §6.2): GPU busy, temperature, power, VRAM, and
    Chunkito's normalized 12-slot inference ring. Unreachable nodes degrade
    to offline deltas without breaking the stream."""
    return StreamingResponse(
        visualizer_service.get_telemetry_stream(interval=interval),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
