"""LLMAgent: the real decision maker, backed by an OpenAI-compatible API.

It implements the same decide(observation) -> AgentAction interface as
ScriptedAgent; the loop cannot tell them apart.
"""
from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field, TypeAdapter

from paperagent.agent.prompts import ACTION_PROTOCOL
from paperagent.harness.llm import LLMClient
from paperagent.schemas.actions import AgentAction

_action_adapter = TypeAdapter(
    Annotated[AgentAction, Field(discriminator="action")]
)


class ActionParseError(Exception):
    pass


class LLMAgent:
    def __init__(self, client: LLMClient):
        self.client = client

    def decide(self, obs: dict):
        # Do not send both the legacy aliases and the structured state to the model.
        model_view = dict(obs.get("assistant_state") or obs)
        model_view["iteration"] = obs.get("iteration")
        user = json.dumps(model_view, ensure_ascii=False)
        raw = self.client.chat_json(ACTION_PROTOCOL, user)
        try:
            return _action_adapter.validate_python(raw)
        except Exception as e:
            raise ActionParseError(
                f"LLM returned an invalid action ({e}): "
                f"{json.dumps(raw, ensure_ascii=False)[:400]}"
            ) from e
