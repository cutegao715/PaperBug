from paperagent.harness.llm import LLMClient
import requests
import pytest


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


def test_anthropic_protocol_posts_messages_and_parses_json(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append({
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        })
        return FakeResponse(200, {
            "content": [
                {"type": "text", "text": '{"action":"chat_reply","text":"ok"}'}
            ]
        })

    monkeypatch.setattr("paperagent.harness.llm.requests.post", fake_post)
    client = LLMClient(
        base_url="https://open.bigmodel.cn/api/anthropic",
        api_key="secret",
        model="glm-5.3",
        protocol="anthropic",
    )

    result = client.chat_json("system", '{"goal":"hi"}')

    assert result == {"action": "chat_reply", "text": "ok"}
    assert calls[0]["url"] == "https://open.bigmodel.cn/api/anthropic/v1/messages"
    assert calls[0]["headers"]["x-api-key"] == "secret"
    assert calls[0]["headers"]["anthropic-version"] == "2023-06-01"
    assert calls[0]["json"]["model"] == "glm-5.3"
    assert calls[0]["json"]["system"] == "system"


def test_anthropic_protocol_salvages_json_from_thinking_block(monkeypatch):
    def fake_post(url, json, headers, timeout):
        return FakeResponse(200, {
            "content": [
                {"type": "thinking",
                 "thinking": 'analysis... {"action":"chat_reply","text":"ok"}'}
            ]
        })

    monkeypatch.setattr("paperagent.harness.llm.requests.post", fake_post)
    client = LLMClient(
        base_url="https://open.bigmodel.cn/api/anthropic",
        api_key="secret",
        model="glm-5.3",
        protocol="anthropic",
    )

    assert client.chat_json("system", "{}") == {"action": "chat_reply", "text": "ok"}


def test_anthropic_protocol_retries_when_response_has_only_thinking(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(json)
        if len(calls) == 1:
            return FakeResponse(200, {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "glm-5.3",
                "content": [
                    {"type": "thinking", "thinking": "Let me understand the situation..."}
                ],
            })
        return FakeResponse(200, {
            "content": [
                {"type": "text", "text": '{"action":"chat_reply","text":"ok"}'}
            ]
        })

    monkeypatch.setattr("paperagent.harness.llm.requests.post", fake_post)
    client = LLMClient(
        base_url="https://open.bigmodel.cn/api/anthropic",
        api_key="secret",
        model="glm-5.3",
        protocol="anthropic",
    )

    assert client.chat_json("system", "{}") == {"action": "chat_reply", "text": "ok"}
    assert len(calls) == 2
    assert "exactly one compact JSON object only" in calls[1]["messages"][0]["content"]


def test_anthropic_protocol_max_tokens_is_configurable(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(json)
        return FakeResponse(200, {
            "content": [
                {"type": "text", "text": '{"action":"chat_reply","text":"ok"}'}
            ]
        })

    monkeypatch.setattr("paperagent.harness.llm.requests.post", fake_post)
    monkeypatch.setenv("PAPERAGENT_LLM_MAX_TOKENS", "8192")
    client = LLMClient(
        base_url="https://open.bigmodel.cn/api/anthropic",
        api_key="secret",
        model="glm-5.3",
        protocol="anthropic",
    )

    assert client.chat_json("system", "{}") == {"action": "chat_reply", "text": "ok"}
    assert calls[0]["max_tokens"] == 8192


def test_default_max_tokens_is_large_enough_for_section_drafting(monkeypatch):
    monkeypatch.delenv("PAPERAGENT_LLM_MAX_TOKENS", raising=False)

    assert LLMClient._max_tokens() == 8192


def test_tight_json_retry_prompt_forces_section_append():
    prompt = LLMClient._tight_json_retry_prompt("{}")

    assert "append_draft" in prompt
    assert "2500" in prompt
    assert "whole document" in prompt


def test_parse_json_reports_truncated_object():
    from paperagent.harness.llm import LLMClient

    with pytest.raises(ValueError, match="truncated JSON object"):
        LLMClient._parse_json('{"action":"write_draft","content":"long draft')


def test_parse_json_accepts_python_style_dict():
    from paperagent.harness.llm import LLMClient

    assert LLMClient._parse_json("{'action': 'chat_reply', 'text': 'ok',}") == {
        "action": "chat_reply",
        "text": "ok",
    }


def test_parse_json_repairs_unquoted_keys():
    from paperagent.harness.llm import LLMClient

    assert LLMClient._parse_json("{action: 'chat_reply', text: 'ok'}") == {
        "action": "chat_reply",
        "text": "ok",
    }


def test_connection_error_is_reported_as_llm_api_error(monkeypatch):
    from paperagent.harness.llm import LLMApiError

    def fake_post(url, json, headers, timeout):
        raise requests.exceptions.SSLError("boom")

    monkeypatch.setattr("paperagent.harness.llm.requests.post", fake_post)
    monkeypatch.setattr("paperagent.harness.llm.time.sleep", lambda _: None)
    client = LLMClient(
        base_url="https://open.bigmodel.cn/api/anthropic",
        api_key="secret",
        model="glm-5.3",
        protocol="anthropic",
    )

    with pytest.raises(LLMApiError, match="LLM connection error"):
        client.chat_json("system", "{}")
