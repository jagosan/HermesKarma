"""Skills API endpoints for Hermes Karma."""
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Query
from api.services.hermes_reader import hermes_reader

router = APIRouter(prefix="/api/skills", tags=["Skills"])


@router.get("/roi")
def get_skill_roi(days: int = Query(30, ge=1, le=365)):
    """SPEC-HK-007 §3.4: Skill ROI & "Dead Skill" Auditor.

    Returns per-skill static token footprint (``skill_token_weight``: router
    description + expanded SKILL.md body), invocation counts and invoking
    sessions over the rolling window, tool-call failure correlation
    (``skill_error_rate``), and dormant/dead flags for skills that burn base
    prompt tokens with 0 invocations in the window.
    """
    return hermes_reader.get_skill_roi_audit(days=days)


@router.get("")
def list_skills(audit: bool = True):
    """Retrieve all autonomous and active skills with category and metadata.

    SPEC-HK-007 §3.4: each entry is enriched with its static token footprint
    (``skill_token_weight``) and 30-day utility fields (invocations, error
    correlation, dormant/dead flag). Pass ``audit=false`` for the raw catalog.
    """
    skills = hermes_reader.get_skills_catalog()
    roi: Dict[str, Dict[str, Any]] = {}
    if audit:
        try:
            report = hermes_reader.get_skill_roi_audit(days=30)
            roi = {s["name"]: s for s in report.get("skills", [])}
        except Exception:
            roi = {}
    for sk in skills:
        r = roi.get(sk["name"]) or {}
        sk["skill_token_weight"] = r.get("skill_token_weight")
        sk["router_description_tokens"] = r.get("router_description_tokens")
        sk["body_tokens"] = r.get("body_tokens")
        sk["invocations_30d"] = r.get("invocations", 0)
        sk["skill_error_rate"] = r.get("skill_error_rate", 0.0)
        sk["is_dormant_dead"] = bool(r.get("is_dormant_dead", False))
    return {
        "total": len(skills),
        "skills": skills,
    }


@router.get("/{skill_name}")
def get_skill(skill_name: str):
    """Retrieve full skill markdown, frontmatter, and code."""
    catalog = hermes_reader.get_skills_catalog()
    matched = next((s for s in catalog if s["name"] == skill_name), None)
    if not matched:
        raise HTTPException(status_code=404, detail="Skill not found")
    return matched


@router.get("/{skill_name}/history")
def get_skill_history(skill_name: str):
    """Retrieve evolution changelog, snapshots, and diffs for a skill."""
    res = hermes_reader.get_skill_history(skill_name)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res
