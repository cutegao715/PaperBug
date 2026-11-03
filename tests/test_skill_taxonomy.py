"""Method specificity never determines ownership, loading, or execution order."""
import json

import pytest
import yaml

from paperagent.skills.loader import SkillLoader, SkillSpec
from paperagent.workflows.catalog import WORKFLOW_SKILLS


@pytest.mark.parametrize("kind", ["shared", "workspace"])
@pytest.mark.parametrize("capability", ["general", "lifecycle_specific", None])
def test_classification_is_independent_of_source_and_legacy_layer(tmp_path, kind, capability):
    folder = tmp_path / ("skills" if kind == "shared" else "projects/p/skills")
    folder.mkdir(parents=True)
    data = {"id": "academic_writing", "layer": "task", "procedure": ["PRIVATE_BODY"]}
    if capability:
        data["capability_type"] = capability
    (folder / "custom.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    loader = SkillLoader(tmp_path)
    skill = loader.resolve("academic_writing@1.0", "p")
    assert skill.source_kind == kind
    assert skill.capability_type == capability
    metadata = skill.metadata_view()
    assert metadata["capability_type"] == capability
    assert "PRIVATE_BODY" not in json.dumps(metadata)
    assert "PRIVATE_BODY" in skill.context_view()["procedure"]
    # Original source remains readable even when a differently classified method overrides it.
    assert loader.resolve("builtin:academic_writing@1.0", "p").capability_type == "general"
    assert not (tmp_path / "projects/p/tasks").exists()


def test_builtin_classifications_and_every_declared_resource_are_readable(tmp_path):
    loader = SkillLoader(tmp_path)
    skills = loader.load_all()
    assert len(skills) == 35
    assert all(s.capability_type for s in skills)
    assert sum(s.capability_type == "general" for s in skills) == 23
    for skill in skills:
        assert len({r.id for r in skill.resources}) == len(skill.resources)
        for resource in skill.resources:
            page = loader.read_resource(skill.key, resource.id)
            assert page["content"] and page["sha256"]
            assert page["content_role"] == "skill_guidance"
    assert loader.resolve("advisor_feedback").capability_type == "general"
    assert loader.resolve("reviewer_response").capability_type == "lifecycle_specific"


def test_resource_reuse_and_citation_discovery_need_no_workflow(tmp_path):
    loader = SkillLoader(tmp_path)
    writing = loader.read_resource("academic_writing", "prose")
    revision = loader.read_resource("paper_revision", "prose")
    assert writing["source"] == revision["source"]
    assert writing["sha256"] == revision["sha256"]
    candidates = loader.select_metadata(goal="引用核查 citation verification", limit=6)
    assert "citation_verification" in {s["id"] for s in candidates}
    assert all("resources" not in s and "procedure" not in s for s in candidates)
    recommendation = next(r for r in WORKFLOW_SKILLS["paper_writing"] if r["skill_id"] == "citation_verification")
    assert recommendation["when"]
    # Discovery and reading did not create a second evidence/memory system.
    assert list(tmp_path.iterdir()) == []


def test_unknown_capability_is_rejected_but_legacy_markdown_remains_readable(tmp_path):
    with pytest.raises(ValueError):
        SkillSpec(id="x", capability_type="workspace")
    folder = tmp_path / "skills/legacy"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: old-method\n---\nLegacy guidance", encoding="utf-8")
    legacy = SkillLoader(tmp_path).resolve("old-method")
    assert legacy.capability_type is None
    assert legacy.context_view()["instructions"] == "Legacy guidance"
