"""Task workflow layer.

Workflows are optional state-aware strategies for high-complexity research
tasks. They guide the general agent loop without replacing it.
"""

from paperagent.workflows.lifecycle import detect_lifecycle
from paperagent.workflows.registry import select_workflow

__all__ = ["select_workflow", "detect_lifecycle"]
