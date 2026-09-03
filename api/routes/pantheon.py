"""Pantheon Swarm Multi-Agent Profiles API router."""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from api.services.hermes_reader import hermes_reader

router = APIRouter(prefix="/api/pantheon", tags=["Pantheon Swarm"])


@router.get("/profiles")
def get_pantheon_profiles():
    """List all Hundred Acre Wood pantheon swarm profiles with operational stats and models."""
    profiles = hermes_reader.get_pantheon_profiles()
    
    total_profiles = len(profiles)
    total_sessions = sum(p.get("total_sessions", 0) for p in profiles)
    total_tokens = sum(p.get("total_tokens", 0) for p in profiles)
    total_input = sum(p.get("input_tokens", 0) for p in profiles)
    total_output = sum(p.get("output_tokens", 0) for p in profiles)
    total_cost = sum(p.get("estimated_cost_usd", 0.0) for p in profiles)
    total_skills = sum(p.get("skills_count", 0) for p in profiles)
    local_agents = sum(1 for p in profiles if p.get("is_local"))

    return {
        "summary": {
            "total_agents": total_profiles,
            "local_agents": local_agents,
            "cloud_agents": total_profiles - local_agents,
            "total_sessions": total_sessions,
            "total_tokens": total_tokens,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_estimated_cost_usd": round(total_cost, 4),
            "total_skills": total_skills,
        },
        "profiles": profiles,
    }


@router.get("/profiles/{profile_id}")
def get_pantheon_profile_detail(profile_id: str):
    """Get rich detail, SOUL.md directives, memory, skills, and session history for a specific agent."""
    detail = hermes_reader.get_pantheon_profile_detail(profile_id=profile_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Pantheon agent '{profile_id}' not found")
    return detail
