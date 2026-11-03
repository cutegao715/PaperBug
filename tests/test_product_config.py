import json
import pytest
from paperagent.harness.llm import LLMClient
from paperagent.tools.registry import describe_tools
from paperagent.harness.tool_executor import ToolExecutor
from paperagent.schemas.policy import ResearchPolicy
from paperagent.schemas.artifacts import ToolCall
from paperagent.workspace.workspace import Workspace


def configure(tmp_path, monkeypatch, data):
    p=tmp_path/"product.json";p.write_text(json.dumps(data))
    monkeypatch.setenv("PAPERAGENT_PRODUCT_CONFIG",str(p))
    for name in ("MODEL","BASE_URL","PROTOCOL"):
        monkeypatch.delenv("PAPERAGENT_LLM_"+name,raising=False)
    return p


def test_product_defaults_env_and_disabled_execution(tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch,{"models":{"primary":{"base_url":"https://example.invalid","model":"configured"}},"tools":{"disabled":["literature_search"]}})
    assert LLMClient(api_key="test").model=="configured"
    monkeypatch.setenv("PAPERAGENT_LLM_MODEL","env")
    assert LLMClient(api_key="test").model=="env"
    assert "literature_search" not in {t["tool_id"] for t in describe_tools()}
    ws=Workspace("p",tmp_path).init("test")
    result=ToolExecutor(ResearchPolicy(),ws).execute(ToolCall(tool_id="literature_search",input={"query":"x","provider":"offline_stub"}))
    assert result.status=="error" and "disabled" in str(result.errors)


def test_invalid_budget_rejected(tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch,{"budget":{"context_input_tokens":-1}})
    from paperagent.product_config import load
    with pytest.raises(ValueError):load()


@pytest.mark.parametrize("ratios", [{"skills": -1}, {"skills": 2}, {"unknown": .1}, {"skills": float("nan")}])
def test_invalid_ratios_rejected(tmp_path, monkeypatch, ratios):
    configure(tmp_path, monkeypatch, {"budget": {"section_ratios": ratios}})
    from paperagent.product_config import load
    with pytest.raises(ValueError): load()


def test_explicit_parameters_win_and_budget_env_overrides(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, {"models": {"primary": {"model": "config"}}, "budget": {"context_input_tokens": 15000}})
    monkeypatch.setenv("PAPERAGENT_LLM_MODEL", "env")
    monkeypatch.setenv("PAPERAGENT_CONTEXT_INPUT_TOKENS", "30000")
    from paperagent.product_config import load
    assert load().budget.context_input_tokens == 30000
    client = LLMClient(model="explicit", base_url="https://explicit.invalid", api_key="explicit", protocol="anthropic")
    assert client.model == "explicit" and client.protocol == "anthropic"
