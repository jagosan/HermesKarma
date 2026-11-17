"""Hermes Karma Main FastAPI Application."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes import sessions, analytics, skills, memory, live, tickets, cron, nodes, pantheon, visualizer, overseer
from api.services.node_collector import node_collector

logger = logging.getLogger(__name__)


async def _node_telemetry_poll_loop(interval_sec: float = 30.0) -> None:
    """Continuously sample fleet node & GPU telemetry so history is recorded even with no browser open."""
    while True:
        try:
            await node_collector.get_all_nodes_telemetry(force_refresh=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Background node telemetry poll failed: %s", exc)
        await asyncio.sleep(interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    poll_task = None
    try:
        poll_sec = float(os.getenv("HERMES_KARMA_BG_POLL_SEC", "30"))
    except ValueError:
        poll_sec = 30.0

    if not os.getenv("PYTEST_CURRENT_TEST") and poll_sec > 0:
        poll_task = asyncio.create_task(_node_telemetry_poll_loop(poll_sec))
        logger.info("Started background node telemetry poller (interval=%ss)", poll_sec)

    try:
        yield
    finally:
        if poll_task is not None:
            poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)


app = FastAPI(
    title="Hermes Karma",
    description="Local-first observability, telemetry, trajectory inspection, and session analytics dashboard for Hermes Agent.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Register API routers
app.include_router(sessions.router)
app.include_router(analytics.router)
app.include_router(pantheon.router)
app.include_router(skills.router)
app.include_router(memory.router)
app.include_router(live.router)
app.include_router(tickets.router)
app.include_router(cron.router)
app.include_router(nodes.router)
app.include_router(visualizer.router)
app.include_router(overseer.router)

# Mount static files
static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(static_dir / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def serve_favicon():
        return FileResponse(static_dir / "favicon.ico")

    @app.get("/favicon.svg", include_in_schema=False)
    def serve_favicon_svg():
        return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8020))
    print(f"☸️  Starting Hermes Karma Dashboard on http://localhost:{port}")
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=True)
