"""ResearchPolicy: parameters only. Enforcement lives in ToolExecutor
and the decision loop - one enforcement point per dimension, never two.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchPolicy(BaseModel):
    """Hard limits for one run. Defaults are V0- conservative."""

    max_iterations: int = Field(default=24, ge=1)
    max_tool_calls: int = Field(default=40, ge=1)
    tool_timeout_s: float = Field(default=20.0, gt=0)
    max_search_results: int = Field(default=8, ge=1, le=20)
    # render/validate gate: kernel errors block finish, warnings do not
    block_finish_on_kernel_errors: bool = True
