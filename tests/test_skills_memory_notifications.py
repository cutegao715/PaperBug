from paperagent.domain.store import DomainStore
from paperagent.harness.context_assembler import assemble_assistant_state
from paperagent.skills.loader import SkillLoader
from paperagent.tasks.memory_manager import MemoryManager
from paperagent.tasks.notification_service import NotificationService
from paperagent.workspace.workspace import Workspace


def test_skill_loader_selects_builtin_and_project_skills(tmp_path):
    project_skill_dir = tmp_path / "projects" / "demo" / "skills"
    project_skill_dir.mkdir(parents=True)
    (project_skill_dir / "advisor.yaml").write_text(
        """
id: advisor_feedback
version: "1.0"
layer: project
scope:
  task_type: revise
triggers:
  - advisor
procedure:
  - Preserve explicit advisor constraints.
""",
        encoding="utf-8",
    )

    loader = SkillLoader(tmp_path)
    selected = loader.select(goal="revise with advisor feedback",
                             project_id="demo", task_type="revise", limit=8)

    ids = [s.id for s in selected]
    assert "academic_writing" in ids
    assert "advisor_feedback" in ids
    assert next(s for s in selected if s.id == "advisor_feedback").layer == "project"


def test_skill_loader_selects_phase5_scenario_and_discipline_skills(tmp_path):
    loader = SkillLoader(tmp_path)

    thesis = loader.select(project_type="thesis", limit=8)
    experiment = loader.select(project_type="experiment", limit=8)
    management = loader.select(discipline_ids=["management"], limit=8)
    cs = loader.select(discipline_ids=["computer_science"], limit=8)

    assert "thesis_planning" in {s.id for s in thesis}
    assert "experiment_design" in {s.id for s in experiment}
    assert "management_discipline" in {s.id for s in management}
    assert "computer_science_discipline" in {s.id for s in cs}


def test_memory_manager_layers_and_relevance(tmp_path):
    store = DomainStore(tmp_path)
    manager = MemoryManager(store)
    manager.remember("user", "Prefers concise academic writing",
                     tags=["writing"], importance=1)
    manager.remember("project", "Advisor asked for stronger theory framing",
                     scope={"project_id": "demo"}, tags=["advisor"])
    manager.remember("project", "Other project note",
                     scope={"project_id": "other"})

    relevant = manager.relevant(project_id="demo", goal="advisor theory")

    assert relevant["user"][0]["layer"] == "user"
    assert relevant["project"][0]["content"].startswith("Advisor")
    assert all(m["scope"].get("project_id") != "other"
               for m in relevant["project"])


def test_notification_service_deduplicates(tmp_path):
    store = DomainStore(tmp_path)
    service = NotificationService(store)

    first = service.notify(type="deadline", title="Deadline 逼近", ref="dl1",
                           dedup_key="deadline:dl1:h24")
    second = service.notify(type="deadline", title="Deadline 逼近", ref="dl1",
                            dedup_key="deadline:dl1:h24")

    assert first.notification_id == second.notification_id
    assert len(store.list_notifications()) == 1


def test_context_assembler_includes_v2_context(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "100000")
    ws = Workspace("demo", root=tmp_path).init("LLM writing review")
    store = DomainStore(tmp_path)
    from paperagent.domain.entities import ProjectProfile
    store.save_project_profile(ProjectProfile(
        project_id="demo",
        project_type="conference_paper",
        title="LLM writing review",
        discipline_ids=["education"],
        target_venue="Journal of Academic Writing",
    ))
    store.save_profile(store.get_profile().model_copy(
        update={"topics": ["LLM writing"]}))
    MemoryManager(store).remember("user", "Prefers structured outlines",
                                  tags=["writing"])
    store.add_standing_intent(description="关注 LLM writing",
                              structured_query={"query": "LLM writing"})
    store.add_watcher(name="submission inbox", watcher_type="submission",
                      source="email")

    state = assemble_assistant_state(
        ws, "LLM writing literature review", 1, 3, 3, None, None, [],
        store=store,
    )
    obs = state.to_observation()

    assert obs["profile"]["topics"] == ["LLM writing"]
    assert obs["project"]["profile"]["project_type"] == "conference_paper"
    assert obs["project"]["profile"]["discipline_ids"] == ["education"]
    skill_ids = {s["id"] for s in obs["skills"]}
    assert "submission_preparation" in skill_ids
    assert "education_discipline" in skill_ids
    assert any(t["tool_id"] == "literature_search" for t in obs["tools"])
    assert obs["memories"]["user"][0]["content"] == "Prefers structured outlines"
    assert obs["connector_state"][0]["source"] == "email"
    assert obs["project_memory"]["project_id"] == "demo"
    assert "lifecycle" in obs["project_memory"]
