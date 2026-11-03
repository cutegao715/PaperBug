import json
import pytest
from paperagent.harness.llm import LLMClient, LLMConfigError
from paperagent import secrets


@pytest.fixture
def credentials(monkeypatch):
    import keyring
    values = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, name: values.get(name))
    monkeypatch.setattr(keyring, "set_password", lambda service, name, value: values.__setitem__(name, value))
    for name in secrets.NAMES:
        monkeypatch.delenv("PAPERAGENT_" + name, raising=False)
    return values


def test_tiers_request_and_no_credential_leak(tmp_path, monkeypatch, credentials):
    config = tmp_path / "product.json"
    config.write_text(json.dumps({"models": {"primary": {"base_url": "https://primary.invalid", "model": "primary"},
        "fast": {"base_url": "https://fast.invalid", "model": "fast"}}}))
    monkeypatch.setenv("PAPERAGENT_PRODUCT_CONFIG", str(config))
    for prefix in ("PAPERAGENT_LLM_", "PAPERAGENT_FAST_LLM_"):
        for name in ("BASE_URL", "MODEL", "PROTOCOL"):
            monkeypatch.delenv(prefix + name, raising=False)
    credentials["LLM_API_KEY"] = "primary-secret"
    with pytest.raises(LLMConfigError, match="Incomplete"):
        LLMClient.for_tier("fast")
    credentials["FAST_LLM_API_KEY"] = "fast-secret"
    calls = []
    class Response:
        status_code = 200
        def json(self): return {"choices": [{"message": {"content": "{}"}}]}
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setattr("paperagent.harness.llm.requests.post", post)
    LLMClient.for_tier("fast").chat_json("system", "user")
    assert calls[0][0] == "https://fast.invalid/chat/completions"
    assert calls[0][1]["json"]["model"] == "fast"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer fast-secret"
    assert LLMClient.for_tier("primary").model == "primary"
    config.write_text(json.dumps({"models": {"primary": {"base_url": "https://primary.invalid", "model": "primary"}}}))
    credentials.pop("FAST_LLM_API_KEY")
    assert LLMClient.for_tier("fast").model == "primary"


def test_keyring_priority_cli_and_offline_env(tmp_path, monkeypatch, credentials, capsys):
    from paperagent.cli import build_parser
    from paperagent.api.control import _llm_configured
    monkeypatch.setenv("PAPERAGENT_LLM_API_KEY", "env-secret")
    monkeypatch.setenv("PAPERAGENT_LLM_MODEL", "test")
    monkeypatch.setenv("PAPERAGENT_LLM_BASE_URL", "https://test.invalid")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "stored-secret")
    args = build_parser().parse_args(["secrets", "set", "LLM_API_KEY"])
    args.func(args)
    assert secrets.get_secret("LLM_API_KEY") == "stored-secret"
    monkeypatch.delenv("PAPERAGENT_LLM_API_KEY")
    assert _llm_configured()
    args = build_parser().parse_args(["secrets", "list"])
    args.func(args)
    output = capsys.readouterr().out
    assert "LLM_API_KEY" in output and "stored-secret" not in output
    def fail(*args): raise RuntimeError("sensitive backend error")
    monkeypatch.setattr("keyring.get_password", fail)
    monkeypatch.setattr(secrets, "_warned", False)
    monkeypatch.setenv("PAPERAGENT_LLM_API_KEY", "env-secret")
    with pytest.warns(RuntimeWarning, match="unavailable"):
        assert secrets.get_secret("LLM_API_KEY") == "env-secret"


def test_env_warning_never_prints_key(tmp_path, monkeypatch):
    from paperagent import env
    monkeypatch.setattr(env, "_migration_warned", False)
    p = tmp_path / ".env"
    p.write_text("PAPERAGENT_LLM_API_KEY=private-test-value")
    monkeypatch.delenv("PAPERAGENT_LLM_API_KEY", raising=False)
    with pytest.warns(UserWarning) as captured:
        env.load_env_file(p)
        env.load_env_file(p)
    assert len(captured) == 1
    assert "private-test-value" not in str(captured[0].message)
