"""Hermes Karma Main FastAPI Application."""
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.routes import sessions, analytics, skills, memory, live, tickets, cron, nodes, pantheon

app = FastAPI(
    title="Hermes Karma",
    description="Local-first observability, telemetry, trajectory inspection, and session analytics dashboard for Hermes Agent.",
    version="1.0.0",
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

# Mount static files
static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(static_dir / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8020))
    print(f"☸️  Starting Hermes Karma Dashboard on http://localhost:{port}")
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=True)
