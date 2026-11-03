"""Permission Gate: action-level authorization, separated from quality.

Levels:
- allow            local read/write, in-app notification
- require_approval external side effects (send email, submit to journal)
- deny             destructive by default

All V2-m1 loop actions are local/notify => allow. The gate is the
single enforcement point wired into the decision loop, so future
external actions cannot bypass it.
"""
from __future__ import annotations

from typing import Optional
from contextvars import ContextVar
from functools import wraps

_role = ContextVar('paperagent_execution_role', default='interactive')


def effective_role(role=None):
    # Ambient proactive execution cannot be elevated by a nested default client.
    return 'proactive' if _role.get() == 'proactive' else (role or _role.get())


def proactive_run(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        token = _role.set('proactive')
        try:
            return fn(*args, **kwargs)
        except PermissionError as exc:
            store = getattr(args[0], 'store', None) if args else None
            if store is not None:
                from paperagent.workspace.state_store import read_json, write_json, state_lock
                path = store.dir / 'proactive_denials.json'
                with state_lock(path):
                    rows = read_json(path, [])
                    write_json(path, (rows + [{'source':fn.__qualname__,'decision':'deny','error':str(exc)}])[-100:])
            raise
        finally:
            _role.reset(token)
    return wrapped


def role_run(fn):
    @wraps(fn)
    def wrapped(self,*args,**kwargs):
        token=_role.set(effective_role(self.role))
        try:
            return fn(self,*args,**kwargs)
        finally:
            _role.reset(token)
    return wrapped


def author_write(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if effective_role() != 'interactive':
            raise PermissionError('proactive author write denied')
        return fn(*args, **kwargs)
    return wrapped


def check_store_write(name):
    if effective_role() == 'proactive' and name not in {
        'notifications','radar_hits','opportunities','trigger_events'
    }:
        raise PermissionError(f'proactive domain write denied: {name}')

Decision = str  # "allow" | "deny" | "require_approval"


class ActionSpec:
    def __init__(self, kind: str, payload: Optional[dict] = None):
        self.kind = kind
        self.payload = payload or {}


# action kind -> required level
DEFAULT_POLICY: dict[str, Decision] = {
    # local, reversible
    "local_read": "allow",
    "local_write": "allow",
    "network_read": "allow",
    "task_create": "allow",
    "notify_in_app": "allow",
    # external, visible to the outside world
    "email_send": "require_approval",
    "external_submit": "require_approval",
    "deadline_modify": "require_approval",
    # destructive
    "project_delete": "deny",
    "file_overwrite_confirmed": "require_approval",
}


class PermissionGate:
    def __init__(self, policy: Optional[dict[str, Decision]] = None):
        self.policy = dict(DEFAULT_POLICY)
        if policy:
            self.policy.update(policy)

    def check(self, spec: ActionSpec, role=None) -> Decision:
        role = effective_role(role)
        if role not in {'interactive', 'proactive'}:
            return 'deny'
        if role == 'proactive' and spec.kind not in {'local_read','network_read','notify_in_app'}:
            return 'deny'
        return self.policy.get(spec.kind, "deny")  # unknown => deny

    def check_loop_action(self, action_name: str, role=None) -> Decision:
        """Map decision-loop AgentAction names to action kinds."""
        mapping = {
            "search": "local_read",
            "read_pdf": "local_read",
            "import_file": "local_write",
            "import_folder": "local_write",
            "tool_call": "local_read",
            "save_reference": "local_write",
            "save_evidence": "local_write",
            "write_draft": "local_write",
            "append_draft": "local_write",
            "read_draft": "local_read",
            "read_context": "local_read",
            "update_task_context": "local_write",
            "update_conversation_context": "local_write",
            "record_project_context": "local_write",
            "record_manuscript_context": "local_write",
            "save_paper_review": "local_write",
            "record_review_followup": "local_write",
            "replace_draft_section": "local_write",
            "update_draft_plan": "local_write",
            "ask_user": "notify_in_app",
            "chat_reply": "notify_in_app",
            "finish": "local_write",
        }
        kind = mapping.get(action_name)
        if kind is None:
            return "deny"
        return self.check(ActionSpec(kind), role=role)
