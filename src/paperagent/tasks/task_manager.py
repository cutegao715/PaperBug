"""Task Manager: validated task lifecycle over the DomainStore.

Origins: user (explicit), agent (created during a run), system
(deadline/automation side effects). All three go through the same
state machine - Domain Layer owns the truth.
"""
from __future__ import annotations

from typing import Optional

from paperagent.domain.entities import TASK_TRANSITIONS, Task
from paperagent.domain.store import DomainStore


class InvalidTransition(Exception):
    pass


class TaskManager:
    def __init__(self, store: DomainStore):
        self.store = store

    def create(self, title: str, *, origin: str = "user",
               priority: int = 2, due_date: Optional[str] = None,
               project_id: Optional[str] = None,
               depends_on: Optional[list[str]] = None) -> Task:
        return self.store.add_task(title=title, origin=origin, priority=priority,
                                   due_date=due_date, project_id=project_id,
                                   depends_on=depends_on or [])

    def transition(self, task_id: str, new_status: str) -> Task:
        tasks = self.store.list_tasks()
        task = next((t for t in tasks if t.task_id == task_id), None)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        allowed = TASK_TRANSITIONS.get(task.status, set())
        if new_status not in allowed:
            raise InvalidTransition(
                f"{task.status} -> {new_status} is not a legal task transition")
        return self.store.update_task(task_id, status=new_status)

    def open_tasks(self) -> list[Task]:
        return [t for t in self.store.list_tasks()
                if t.status in ("todo", "in_progress")]

    def complete(self, task_id: str) -> Task:
        return self.transition(task_id, "done")
