"""Skills API endpoints for Hermes Karma."""
from fastapi import APIRouter, HTTPException
from api.services.hermes_reader import hermes_reader

router = APIRouter(prefix="/api/skills", tags=["Skills"])


@router.get("")
def list_skills():
    """Retrieve all autonomous and active skills with category and metadata."""
    skills = hermes_reader.get_skills_catalog()
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
