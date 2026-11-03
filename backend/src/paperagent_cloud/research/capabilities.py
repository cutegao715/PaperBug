"""Prepare bounded original research methods for an authorized task."""
from pathlib import Path

from paperagent.skills.loader import SkillLoader
from paperagent.workflows.catalog import workflow_spec
from paperagent.workflows.design import contract
from paperagent.workflows.design import WORKFLOWS


def research_context(goal: str, workflow_id: str | None = None, skill_id: str | None = None) -> dict:
    workflow = workflow_spec(workflow_id) if workflow_id else None
    if workflow_id and not workflow:
        raise ValueError('Unknown workflow')
    loader = SkillLoader(Path(__file__).parent / '_empty_user_directory')
    selected = [loader.resolve(skill_id)] if skill_id else loader.select(goal=goal, limit=3)
    methods = []
    for skill in selected:
        fields = skill.context_view()
        fields['key'] = skill.key
        for key in ('source_path', 'source_project_id', 'shadowed_sources'):
            fields.pop(key, None)
        methods.append(fields)
    return {'workflow':contract(workflow) if workflow else None, 'skills':methods}


def read_skill_resource(key: str, resource_id: str, offset: int = 0, expected_sha256: str = '') -> dict:
    loader = SkillLoader(Path(__file__).parent / '_empty_user_directory')
    data = loader.read_resource(key, resource_id, offset=offset, max_chars=6000, expected_sha256=expected_sha256)
    data.pop('source', None)
    return data


def capability_catalog() -> dict:
    loader = SkillLoader(Path(__file__).parent / '_empty_user_directory')
    return {'skills': [{'id':s.id, 'name':s.name or s.id, 'description':s.description} for s in loader.load_all()],
            'workflows': [{'id':w['id'], 'name':w['name']} for w in WORKFLOWS]}
