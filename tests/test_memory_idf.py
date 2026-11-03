from paperagent.domain.store import DomainStore
from paperagent.tasks.memory_manager import MemoryManager


def test_rare_term_beats_common_term_without_scope_pollution(tmp_path):
    manager = MemoryManager(DomainStore(tmp_path))
    rare = manager.remember("project", "bayesian", scope={"project_id":"p"})
    for i in range(8):
        manager.remember("project", "method", scope={"project_id":"p"}, importance=1)
    for i in range(20):
        manager.remember("project", "bayesian", scope={"project_id":"other"})
    rows = manager.relevant(project_id="p", goal="method bayesian")["project"]
    assert rows[0]["memory_id"] == rare.memory_id
    assert len(rows) == 8
    assert all(r["scope"]["project_id"] == "p" for r in rows)
