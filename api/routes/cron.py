"""Cron jobs inspection API endpoints for Hermes Karma."""
from fastapi import APIRouter
from api.services.hermes_reader import hermes_reader

router = APIRouter(prefix="/api/cron", tags=["Cron"])


@router.get("")
def list_cron_jobs():
    """List scheduled Hermes cron jobs and status."""
    jobs = hermes_reader.get_cron_jobs()
    return {
        "total": len(jobs),
        "jobs": jobs,
    }
