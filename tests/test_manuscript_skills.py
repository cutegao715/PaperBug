"""On-demand resources and paper knowledge reuse the conversation workspace."""
import hashlib
import json
from datetime import date
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from paperagent.agent.decision_loop import DecisionLoop
from paperagent.conversation import ConversationManager
from paperagent.domain.lifecycle import LifecycleService
from paperagent.domain.manuscript import ManuscriptContext, ManuscriptProfile, ManuscriptRecordInput
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.actions import AgentAction, ChatReplyAction, ReadContextAction, RecordManuscriptAction
from paperagent.schemas.policy import ResearchPolicy
from paperagent.skills.loader import SkillLoader
from paperagent.workspace.workspace import Workspace


def package(root, resource_path="references/guide.md"):
    folder = root / "skills" / "test-writing"
    (folder / "references").mkdir(parents=True)
    (folder / "references" / "guide.md").write_text("SECRET_GUIDANCE\n" * 100, encoding="utf-8")
    (folder / "writing.yaml").write_text(yaml.safe_dump({
        "id": "test_writing", "resources": [{"id": "guide", "path": resource_path}],
    }), encoding="utf-8")
    return folder


def record(kind="term", **kwargs):
    data = {"kind": kind, "title": "Population", "sources": [{"kind": "user_note", "key": "Author statement"}],
            "reason": "Author establishes terminology", "term": {"preferred": "participants", "discouraged_variants": ["subjects"]}}
    if kind != "term":
        data.pop("term")
    if kind == "argument":
        data.update(title="Inference boundary", argument={"question": "Is X associated with Y?", "boundary": "No causal claim"})
    if kind == "venue_rule":
        data.update(title="Anonymous review", content="Remove author identities in review copy",
                    venue_rule={"classification": "official_requirement", "source_url": "https://example.org/cfp"})
    return ManuscriptRecordInput.model_validate({**data, **kwargs})


def setup_paper(root):
    ws = Workspace("paper", root).init("英文期刊论文")
    LifecycleService(ws).save(lifecycle_type="paper_conference", stage="drafting", reason="开始写作")
    ws.write_draft("paper.md", "# Results\nThe subjects improved.\n")
    return ws, ManuscriptContext(ws)


def loop(ws, agent=None, iterations=5, **kwargs):
    return DecisionLoop(ws, ToolExecutor(ResearchPolicy(max_iterations=iterations), ws), agent or object(), log=lambda _: None, **kwargs)


def test_resource_metadata_then_paginated_content_and_change_detection(tmp_path):
    folder = package(tmp_path)
    loader = SkillLoader(tmp_path)
    spec = loader.resolve("shared:test_writing@1.0")
    assert spec.metadata_view()["resource_count"] == 1
    assert "SECRET_GUIDANCE" not in json.dumps(spec.context_view())
    assert spec.context_view()["resources"][0]["read_with"]["resource_id"] == "guide"
    first = loader.read_resource(spec.key, "guide", max_chars=500)
    second = loader.read_resource(spec.key, "guide", offset=first["next_offset"], expected_sha256=first["sha256"])
    assert first["content"] + second["content"] == (folder / "references/guide.md").read_bytes().decode("utf-8")
    (folder / "references/guide.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        loader.read_resource(spec.key, "guide", offset=500, expected_sha256=first["sha256"])
    with pytest.raises(ValueError, match="not declared"):
        loader.read_resource(spec.key, "references/guide.md")
    for args in ({"offset": -1}, {"max_chars": 50}):
        with pytest.raises(ValueError, match="bounds"):
            loader.read_resource(spec.key, "guide", **args)


@pytest.mark.parametrize("path", ["../outside.md", "C:/outside.md", "references/../../outside.md", "references\\guide.md", "scripts/run.py"])
def test_resource_path_and_executable_boundaries(tmp_path, path):
    package(tmp_path, path)
    with pytest.raises(ValueError):
        SkillLoader(tmp_path).read_resource("test_writing", "guide")


def test_resource_symlink_cannot_escape_package(tmp_path):
    folder = package(tmp_path, "references/link.md")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        (folder / "references/link.md").symlink_to(outside)
    except OSError:
        pytest.skip("Host does not allow symlinks")
    with pytest.raises(ValueError, match="inside"):
        SkillLoader(tmp_path).read_resource("test_writing", "guide")


def test_markdown_skill_packages_and_yaml_resources_are_not_extra_skills(tmp_path):
    folder = tmp_path / "skills" / "academic"
    (folder / "references").mkdir(parents=True)
    (folder / "agents").mkdir()
    (folder / "SKILL.md").write_text("---\nname: academic-test\ndescription: English prose\n---\nEntry instruction", encoding="utf-8")
    (folder / "references/prose.md").write_text("Prose detail", encoding="utf-8")
    (folder / "references/state.yaml").write_text("not: a skill", encoding="utf-8")
    (folder / "agents/openai.yaml").write_text("interface: {}", encoding="utf-8")
    yaml_folder = package(tmp_path)
    (yaml_folder / "references/settings.yaml").write_text("not: a skill", encoding="utf-8")
    loader = SkillLoader(tmp_path)
    shared = [s for s in loader.discover() if s.source_kind == "shared"]
    assert {s.id for s in shared} == {"academic-test", "test_writing"}
    md = loader.resolve("academic-test")
    assert "Entry instruction" not in json.dumps(md.metadata_view())
    assert md.instructions == "Entry instruction"
    assert loader.read_resource(md.key, "references/prose.md")["content"] == "Prose detail"


def test_resource_workspace_isolation_and_builtins_are_readable(tmp_path):
    ws = Workspace("a", tmp_path).init("研究")
    folder = package(ws.dir)
    loader = SkillLoader(tmp_path)
    assert loader.read_resource("workspace:test_writing@1.0", "guide", "a")["status"] == "ok"
    with pytest.raises(ValueError, match="not found"):
        loader.read_resource("workspace:test_writing@1.0", "guide", "b")
    (folder / "references/guide.md").unlink()
    result = loop(ws)._read_context(ReadContextAction(kind="skill_resource", key="workspace:test_writing@1.0", resource_id="guide"))
    assert result["status"] == "error"
    english = loader.resolve("builtin:english_academic_writing@1.0")
    assert len(english.resources) == 12
    for resource in english.resources:
        assert loader.read_resource(english.key, resource.id)["total_chars"] > 100


def test_manuscript_partial_records_confirmation_history_and_revisions(tmp_path):
    ws, service = setup_paper(tmp_path)
    assert service.context() == {} and not service.path.exists()
    proposal = service.record(record(), actor="agent", run_id="r1")
    assert proposal["status"] == "proposed"
    adopted = service.review(proposal["record_id"], expected_revision=1, status="confirmed", reason="Adopt author term")
    assert adopted["revision"] == 2
    with pytest.raises(ValueError, match="Agent cannot"):
        service.record(record(record_id=adopted["record_id"], expected_revision=2), actor="agent")
    with pytest.raises(ValueError, match="changed"):
        service.review(proposal["record_id"], expected_revision=1, status="withdrawn", reason="old request")
    changed = service.record(record(record_id=adopted["record_id"], expected_revision=2, term={"preferred": "respondents"}))
    assert changed["revision"] == 3 and changed["status"] == "confirmed"
    partial = service.record(record("argument"))
    assert partial["argument"]["result"] == ""
    service.review(changed["record_id"], expected_revision=3, status="withdrawn", reason="Remove obsolete term")
    assert len(service.read()["records"]) == 5
    assert [r["record_id"] for r in service.view()["active_records"]] == [partial["record_id"]]
    assert not (ws.dir / "tasks").exists()


def test_venue_provenance_does_not_silently_follow_target_changes(tmp_path):
    _, service = setup_paper(tmp_path)
    with pytest.raises(ValueError, match="target venue"):
        service.record(record("venue_rule"))
    service.set_profile(ManuscriptProfile(venue_type="conference", venue_name="ExampleConf 2026", track="Research"), 0, "Set venue")
    with pytest.raises(ValueError, match="changed"):
        service.set_profile(ManuscriptProfile(), 0, "Stale profile")
    with pytest.raises(ValidationError, match="check date"):
        record("venue_rule", venue_rule={"classification": "official_requirement", "verification": "checked"})
    with pytest.raises(ValidationError, match="source URL or imported"):
        record("venue_rule", venue_rule={"classification": "official_requirement", "verification": "checked", "checked_on": date.today().isoformat()})
    local = record("venue_rule", sources=[{"kind": "material", "key": "uploaded-author-instructions"}],
                   venue_rule={"classification": "official_requirement", "verification": "checked", "checked_on": date.today().isoformat()})
    assert local.venue_rule.source_url == ""
    with pytest.raises(ValueError, match="imported"):
        service.record(local)  # a claimed imported ID must actually exist
    rule = service.record(record("venue_rule", venue_rule={"classification": "official_requirement", "verification": "checked", "source_url": "https://example.org/cfp", "checked_on": date.today().isoformat()}), actor="agent")
    assert rule["venue_rule"]["verification"] == "unverified"
    service.review(rule["record_id"], expected_revision=1, status="confirmed", reason="Use pending verification")
    service.set_profile(ManuscriptProfile(venue_type="journal", venue_name="Example Journal"), service.read()["revision"], "Retarget")
    current = service.view()["active_records"][0]
    assert current["needs_review"] and current["venue_profile"]["venue_name"] == "ExampleConf 2026"
    service.review(rule["record_id"], expected_revision=2, status="confirmed", reason="Adoption alone")
    assert service.view()["active_records"][0]["needs_review"]
    assert len(service.read()["profile_history"]) == 2


def test_sources_related_versions_and_conflicting_terms_are_preserved(tmp_path):
    ws, service = setup_paper(tmp_path)
    term = service.record(record(sources=[{"kind": "draft", "key": "paper.md", "locator": "Results"}]))
    source = term["sources"][0]["snapshot"]
    argument = service.record(record("argument", relates_to=[term["record_id"]]))
    assert not any(r["needs_review"] for r in service.view()["active_records"])
    ws.write_draft("paper.md", "Revised findings")
    assert all(r["needs_review"] for r in service.view()["active_records"])
    assert "subjects" in Path(source["snapshot_path"]).read_text(encoding="utf-8")
    service.record(record(record_id=term["record_id"], expected_revision=1, term={"preferred": "respondents"}))
    current = next(r for r in service.view()["active_records"] if r["record_id"] == argument["record_id"])
    assert current["needs_review"] and "关联" in current["issues"][0]
    service.record(record(term={"preferred": "participants"}))
    assert all(r["needs_review"] for r in service.view()["active_records"] if r["kind"] == "term")
    other = ManuscriptContext(Workspace("other", tmp_path).init("other"))
    with pytest.raises(ValueError, match="active records"):
        other.record(record("argument", relates_to=[term["record_id"]]))
    for source in ({"kind": "draft", "key": "../paper.md"}, {"kind": "material", "key": "missing"}, {"kind": "evidence", "key": "missing"}):
        with pytest.raises((ValueError, FileNotFoundError)):
            other.record(record(sources=[source]))


def test_literal_diagnostics_bind_bytes_and_do_not_claim_semantic_validation(tmp_path):
    ws, service = setup_paper(tmp_path)
    (ws.drafts_dir / "paper.md").write_bytes(b"# Results\r\nThe subjects improved.\r\n")
    ws.write_draft("appendix.md", "subjects")
    service.record(record(artifact_names=["paper.md"]))
    service.record(record(title="Another concept", term={"preferred": "cases", "allowed_variants": ["subjects"], "discouraged_variants": ["subjects"]}))
    service.record(record(title="Unconfirmed", term={"preferred": "people", "discouraged_variants": ["improved"]}), actor="agent")
    result = service.inspect_text("paper.md")
    assert result["candidate_sha256"] == hashlib.sha256((ws.drafts_dir / "paper.md").read_bytes()).hexdigest()
    assert len(result["findings"]) == 1 and result["findings"][0]["line"] == 2
    assert result["status"] == "diagnostics_only" and result["checks"]["argument_validity"] == "not_checked"
    assert service.inspect_text("appendix.md")["findings"] == []
    ws.write_draft("paper.md", "The participants improved.")
    assert service.inspect_text("paper.md")["candidate_sha256"] != result["candidate_sha256"]
    with pytest.raises(ValueError):
        service.inspect_text("../outside.md")


def test_resume_reads_current_paper_knowledge_and_optional_guides_without_workflow(tmp_path):
    ws, service = setup_paper(tmp_path)
    term = service.record(record())
    service.record(record("argument"))
    service.set_profile(ManuscriptProfile(venue_name="Example Journal"), service.read()["revision"], "Set target")
    service.record(record("venue_rule"))
    actions = [
        ReadContextAction(kind="skill", key="builtin:english_academic_writing@1.0"),
        ReadContextAction(kind="skill_resource", key="builtin:english_academic_writing@1.0", resource_id="english_prose"),
        ReadContextAction(kind="manuscript", key=term["record_id"]),
        TypeAdapter(AgentAction).validate_python({"action": "record_manuscript_context", **record(title="Proposed term").model_dump(mode="json")}),
    ]
    class Writer:
        def decide(self, obs):
            assert obs["workflow"] is None and obs["task"] is None
            assert obs["manuscript"]["profile"]["venue_name"] == "Example Journal"
            return actions.pop(0)
    assert loop(ws, Writer(), iterations=4, workflow_mode="off").run("润色英文论文").status == "budget_exhausted"
    assert service.view()["active_records"][-1]["status"] == "proposed"
    fresh = Workspace("paper", tmp_path)
    class Resume:
        def decide(self, obs):
            assert {"term", "argument", "venue_rule"} <= {r["kind"] for r in obs["manuscript"]["records"]}
            assert "academic_writing" in {s["id"] for s in obs["skills"]}
            assert "procedure" not in json.dumps(obs["skills"])
            return ChatReplyAction(text="保留已确认术语与论证边界，按需继续。")
    assert loop(fresh, Resume()).run("继续").status == "finished"
    assert ConversationManager(fresh).get().workflow_mode == "off"
    assert not (ws.dir / "tasks").exists()
    full = loop(fresh)._read_context(ReadContextAction(kind="manuscript", key=term["record_id"], max_chars=20000))
    assert json.loads(full["content"])["current"]["term"]["preferred"] == "participants"


def test_context_preview_is_bounded_and_full_registry_remains_readable(tmp_path):
    ws, service = setup_paper(tmp_path)
    for n in range(14):
        service.record(record(title=f"Concept {n}", term={"preferred": f"term{n}", "definition": "ONLY_IN_FULL_RECORD"}))
    state = assemble_assistant_state(ws, "修改摘要", 1, 5, 10, None, None, [])
    assert state.current_context.manuscript["omitted"] == 2
    assert "ONLY_IN_FULL_RECORD" not in json.dumps(state.current_context.manuscript)
    full = loop(ws)._read_context(ReadContextAction(kind="manuscript", max_chars=20000))
    assert "ONLY_IN_FULL_RECORD" in full["content"]
    other = Workspace("other", tmp_path).init("other")
    key = service.read()["records"][0]["record_id"]
    assert loop(other)._read_context(ReadContextAction(kind="manuscript", key=key))["status"] == "error"


def test_http_paper_records_resource_reads_and_live_run_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_WORKSPACE", str(tmp_path))
    from paperagent.server import app, RUNS
    ws, _ = setup_paper(tmp_path)
    with TestClient(app) as client:
        base = "/api/v1/projects/paper/manuscript"
        assert client.get(base).json()["revision"] == 0
        assert client.get("/manuscript.js").status_code == 200
        saved = client.post(base + "/records", json=record().model_dump(mode="json"))
        assert saved.status_code == 200 and saved.json()["status"] == "confirmed"
        assert client.put(base + "/profile", json={"profile": {"venue_name": "Example Journal"}, "expected_revision": 0, "reason": "stale"}).status_code == 422
        assert client.post(base + "/check", json={"filename": "paper.md"}).json()["status"] == "diagnostics_only"
        assert client.get("/api/v1/projects/missing/manuscript").status_code == 404
        resource = client.get("/api/v1/skills/builtin:english_academic_writing@1.0/resource", params={"project_id": "paper", "resource_id": "venue"})
        assert resource.status_code == 200 and resource.json()["content"]
        assert client.get("/api/v1/skills/builtin:english_academic_writing@1.0/resource", params={"resource_id": "not-declared"}).status_code == 422
        monkeypatch.setitem(RUNS, "paper-test-running", SimpleNamespace(project_id="paper", done=Event()))
        assert client.post(base + "/records", json=record().model_dump(mode="json")).status_code == 409
        assert client.post(base + "/check", json={"filename": "paper.md"}).status_code == 409
        assert client.get(base).status_code == 200
