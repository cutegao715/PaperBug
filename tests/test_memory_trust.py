import pytest
from paperagent.domain.entities import MemoryRecord
from paperagent.domain.store import DomainStore
from paperagent.tasks.memory_manager import MemoryManager
from paperagent.harness.context_budget import fit_context, ContextBudgetExceeded
from paperagent.agent.state import AssistantState, CurrentContext


def test_legacy_default_and_protected_replace(tmp_path):
    assert MemoryRecord(memory_id="old",layer="project",content="old").trust == "model_transient"
    manager=MemoryManager(DomainStore(tmp_path))
    adopted=manager.remember("project","method",scope={"project_id":"p"},trust="user_adopted")
    manager.remember("project","method method",scope={"project_id":"p"})
    assert manager.relevant(project_id="p",goal="method")["project"][0]["memory_id"]==adopted.memory_id
    with pytest.raises(ValueError):manager.replace(adopted.memory_id,"changed")
    assert manager.replace(adopted.memory_id,"user correction",actor="user").trust=="user_adopted"


def test_adoption_is_linked_idempotent_and_withdrawal_invalidates(tmp_path):
    manager=MemoryManager(DomainStore(tmp_path))
    row={"record_id":"r","revision":1,"status":"confirmed","title":"Decision","content":"Use method"}
    manager.sync_adopted_record("p","manuscript",row)
    manager.sync_adopted_record("p","manuscript",row)
    rows=manager.relevant(project_id="p",goal="method")["project"]
    assert len(rows)==1 and rows[0]["trust"]=="user_adopted"
    assert rows[0]["ref"]=="manuscript:r"
    manager.sync_adopted_record("p","manuscript",{**row,"revision":2,"status":"withdrawn"})
    assert manager.relevant(project_id="p")["project"]==[]


def test_adopted_memory_is_never_silently_evicted():
    state=AssistantState(goal="goal",current_context=CurrentContext(memories={"project":[{"trust":"user_adopted","content":"x"*100000}]}))
    with pytest.raises(ContextBudgetExceeded):fit_context(state,input_budget=18000)


def test_stale_adoption_cannot_rollback_or_revive(tmp_path):
    manager = MemoryManager(DomainStore(tmp_path))
    row = {"record_id": "r", "revision": 2, "status": "confirmed", "content": "new"}
    manager.sync_adopted_record("p", "project", row)
    manager.sync_adopted_record("p", "project", {**row, "revision": 1, "content": "old"})
    assert "new" in manager.relevant(project_id="p")["project"][0]["content"]
    manager.sync_adopted_record("p", "project", {**row, "revision": 3, "status": "withdrawn"})
    manager.sync_adopted_record("p", "project", row)
    assert not manager.relevant(project_id="p")["project"]


def test_project_api_adopts_and_withdraws_linked_memory(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from paperagent.api.v1.project import create_router
    from paperagent.workspace.workspace import Workspace
    ws = Workspace("p", tmp_path).init("test")
    app = FastAPI()
    app.include_router(create_router(lambda _: ws, lambda _: None))
    manager = MemoryManager(DomainStore(tmp_path))
    with TestClient(app) as client:
        response = client.post("/projects/p/dossier/records", json={
            "kind": "objective", "stage": "application", "content": "method objective",
            "sources": [{"kind": "user_note", "key": "user"}], "reason": "user decision"})
        assert response.status_code == 200, response.text
        row = response.json()
        assert manager.relevant(project_id="p")["project"][0]["trust"] == "user_adopted"
        response = client.post(f"/projects/p/dossier/records/{row['record_id']}/review", json={
            "expected_revision": row["revision"], "status": "withdrawn", "reason": "correction"})
        assert response.status_code == 200, response.text
        assert not manager.relevant(project_id="p")["project"]
