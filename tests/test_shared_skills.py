"""Production skill sources, compatibility, and resource behavior."""
from pathlib import Path
from importlib.resources import files
import json

import yaml
from paperagent.skills.loader import SkillLoader

ROOT = Path(__file__).resolve().parents[1]


def test_builtin_definitions_have_one_production_location():
    seen = {}
    for root in (ROOT / "src", ROOT / "backend/src"):
        for path in root.rglob("library/*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and "id" in data:
                seen.setdefault(data["id"], []).append(path)
    assert len(seen) == 35
    assert all(len(paths) == 1 for paths in seen.values()), seen


def test_cloud_capabilities_use_shared_implementations():
    from paperagent_cloud.research import capabilities
    from paperagent.skills.loader import SkillLoader
    from paperagent.workflows.catalog import workflow_spec
    from paperagent.workflows.design import contract, WORKFLOWS
    assert capabilities.SkillLoader is SkillLoader
    assert capabilities.workflow_spec is workflow_spec
    assert capabilities.contract is contract
    assert capabilities.WORKFLOWS is WORKFLOWS


def test_shared_catalog_and_alias_resources():
    from paperagent.workflows.catalog import SKILL_CATALOG
    data = yaml.safe_load(files("paperagent.skills").joinpath("skill_catalog.yaml").read_text(encoding="utf-8-sig"))
    assert data == SKILL_CATALOG
    shared = json.loads(files("paperagent.skills").joinpath("legacy_aliases.json").read_text(encoding="utf-8"))
    assert shared == SkillLoader(ROOT).alias_metadata()


def test_all_cloud_resources_and_legacy_keys_match_shared(tmp_path):
    from paperagent_cloud.research.capabilities import read_skill_resource, capability_catalog
    loader = SkillLoader(tmp_path)
    skills = loader.load_all()
    assert {s.id for s in skills} == {s["id"] for s in capability_catalog()["skills"]}
    for skill in skills:
        for resource in skill.resources:
            local = loader.read_resource(skill.key, resource.id)
            cloud = read_skill_resource(skill.key, resource.id)
            assert "source" not in cloud
            assert cloud == {k: v for k, v in local.items() if k != "source"}
    for old, new in loader.alias_metadata()["aliases"].items():
        assert loader.resolve("builtin:" + old + "@1.0").id == new
        for old_resource in loader.alias_metadata()["resources"].get(old, {}):
            assert read_skill_resource("builtin:" + old + "@1.0", old_resource)["status"] == "ok"


def test_user_overrides_remain_local_and_market_matches_cloud(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from paperagent import server
    from paperagent_cloud.research.capabilities import capability_catalog
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    with TestClient(server.app) as client:
        response = client.get("/api/v1/skills")
        assert response.status_code == 200
        assert {s["id"] for s in response.json()["skills"]} == {s["id"] for s in capability_catalog()["skills"]}
    for folder, name in [(tmp_path / "skills", "shared override"), (tmp_path / "projects/p/skills", "private override")]:
        folder.mkdir(parents=True)
        (folder / "paper_review.yaml").write_text(yaml.safe_dump({"id": "paper_review", "name": name}), encoding="utf-8")
    loader = SkillLoader(tmp_path)
    assert loader.resolve("paper_review").name == "shared override"
    assert loader.resolve("paper_review", "p").name == "private override"
    assert loader.resolve("builtin:paper_review@1.0", "p").source_kind == "builtin"
    assert next(s for s in capability_catalog()["skills"] if s["id"] == "paper_review")["name"] != "private override"
