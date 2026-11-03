"""One discovery path for observations, workflow previews and the workspace UI."""
from __future__ import annotations

from paperagent.domain.store import DomainStore
from paperagent.skills.loader import SkillLoader
from paperagent.workflows.lifecycle import detect_lifecycle


def workflow_skill_metadata(ws, workflow):
    """Complete advisory directory for the UI; runtime shortlist stays bounded."""
    loader = SkillLoader(ws.root)
    refs = (workflow or {}).get("skill_recommendations", [])
    if not refs:
        return []
    effective = {(s.id, s.version): s for s in loader.load_all(ws.project_id)}
    rows = []
    for ref in refs:
        skill = effective.get((ref["skill_id"], ref["version"]))
        if skill is None:
            raise ValueError(f"Skill not found: {ref['skill_id']}@{ref['version']}")
        rows.append({**skill.metadata_view(), "workflow_recommendations": [ref]})
    return rows


def workspace_skill_metadata(ws, goal: str, *, workflow: dict | None = None,
                             lifecycle: dict | None = None, store=None, limit: int = 5) -> list[dict]:
    store = store or DomainStore(ws.root)
    profile = store.get_project_profile(ws.project_id)
    disciplines = profile.discipline_ids or [d.discipline_id for d in store.get_profile().disciplines]
    lifecycle = lifecycle or (workflow or {}).get("lifecycle") or detect_lifecycle(ws, goal)
    from paperagent.domain.manuscript import ManuscriptContext
    manuscript = ManuscriptContext(ws)
    if manuscript.path.exists():
        paper = manuscript.read()["profile"]
        goal = " ".join([goal, paper["article_type"], " ".join(paper["study_designs"]),
                         "english manuscript" if paper["language"].lower().startswith("en") else ""])
    return SkillLoader(ws.root).select_metadata(
        goal=goal, project_id=ws.project_id, project_type=profile.project_type,
        discipline_ids=disciplines,
        lifecycle_type=(lifecycle or {}).get("lifecycle_type"),
        recommendations=(workflow or {}).get("skill_recommendations", []), limit=limit,
    )
