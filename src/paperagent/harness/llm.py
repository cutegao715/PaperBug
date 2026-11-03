"""Minimal chat client for OpenAI-compatible and Anthropic-compatible APIs.

Configuration via environment (auto-loaded from .env at startup):
    PAPERAGENT_LLM_PROTOCOL  openai | anthropic
    PAPERAGENT_LLM_BASE_URL  e.g. https://open.bigmodel.cn/api/paas/v4
    PAPERAGENT_LLM_API_KEY
    PAPERAGENT_LLM_MODEL     e.g. glm-4.6
"""
from __future__ import annotations

import json
import os
import ast
import re
import time
from typing import Optional

import requests
from contextvars import ContextVar

usage_meter = ContextVar("paperagent_usage_meter", default=None)
from requests import RequestException

RETRYABLE_STATUS = {429, 502, 503, 504}
MAX_RETRIES = 2
BACKOFF_S = 2.0
DEFAULT_MAX_TOKENS = 8192


class LLMConfigError(Exception):
    pass


class LLMApiError(Exception):
    pass


class LLMClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, protocol: Optional[str] = None,
                 timeout: float = 120.0, usage_callback=None):
        from paperagent.product_config import load
        from paperagent.secrets import get_secret
        config = load().models.primary
        self.base_url = (base_url if base_url is not None else config.base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else get_secret("LLM_API_KEY")
        self.model = model if model is not None else config.model
        self.protocol = (protocol if protocol is not None else config.protocol).lower()
        self.timeout = timeout
        self.usage_callback = usage_callback
        self.last_usage = None
        if not (self.base_url and self.api_key and self.model):
            raise LLMConfigError(
                "LLM not configured. Set PAPERAGENT_LLM_BASE_URL, "
                "PAPERAGENT_LLM_API_KEY, PAPERAGENT_LLM_MODEL (see .env.example). "
                "For an offline demo use: paperagent demo")
        if self.protocol not in {"openai", "anthropic"}:
            raise LLMConfigError(
                "Unsupported PAPERAGENT_LLM_PROTOCOL. Use 'openai' or 'anthropic'.")

    @classmethod
    def for_tier(cls, tier):
        from paperagent.product_config import load
        from paperagent.secrets import get_secret
        if tier == "primary": return cls()
        if tier != "fast": raise LLMConfigError("Unknown model tier")
        config = load().models.fast
        key = get_secret("FAST_LLM_API_KEY")
        if not (config.base_url or config.model or key): return cls()
        if not (config.base_url and config.model and key):
            raise LLMConfigError("Incomplete fast model configuration")
        return cls(base_url=config.base_url, model=config.model,
                   protocol=config.protocol, api_key=key)

    def chat_json(self, system: str, user: str) -> dict:
        """One chat call that must return a JSON object."""
        if self.protocol == "anthropic":
            return self._chat_json_anthropic(system, user)
        return self._chat_json_openai(system, user)

    def _chat_json_openai(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: Optional[str] = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._post(f"{self.base_url}/chat/completions",
                                     json=payload, headers=headers, timeout=self.timeout)
            except RequestException as e:
                last_error = f"LLM connection error: {e}"
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_S * (attempt + 1))
                    continue
                raise LLMApiError(last_error) from e
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                return self._parse_json(content)
            last_error = self._api_error_message(resp)
            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            break
        raise LLMApiError(last_error or f"HTTP {resp.status_code}")

    def _chat_json_anthropic(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": self._max_tokens(),
            "temperature": 0.2,
            "system": system,
            "messages": [
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": os.environ.get(
                "PAPERAGENT_ANTHROPIC_VERSION", "2023-06-01"),
            "content-type": "application/json",
        }
        last_error: Optional[str] = None
        tightened = False
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._post(self._anthropic_messages_url(),
                                     json=payload, headers=headers,
                                     timeout=self.timeout)
            except RequestException as e:
                last_error = f"LLM connection error: {e}"
                if attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_S * (attempt + 1))
                    continue
                raise LLMApiError(last_error) from e
            if resp.status_code == 200:
                data = resp.json()
                try:
                    content = self._anthropic_text(data)
                    return self._parse_json(content)
                except ValueError as e:
                    last_error = str(e)
                    if not tightened and attempt < MAX_RETRIES:
                        tightened = True
                        payload["messages"] = [
                            {"role": "user",
                             "content": self._tight_json_retry_prompt(user)}
                        ]
                        continue
                    raise
            last_error = self._api_error_message(resp)
            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            break
        raise LLMApiError(last_error or f"HTTP {resp.status_code}")

    def _post(self, url, *, json, headers, timeout):
        meter = usage_meter.get()
        if meter:
            json.setdefault("max_tokens", self._max_tokens())
        reservation = meter.before(json) if meter else None
        try:
            response = requests.post(url, json=json, headers=headers, timeout=timeout)
        except RequestException:
            if meter:
                meter.after(reservation, None, "unknown")
            raise
        usage = None
        if response.status_code == 200:
            try:
                raw = response.json().get("usage", {})
                incoming = raw.get("prompt_tokens", raw.get("input_tokens"))
                outgoing = raw.get("completion_tokens", raw.get("output_tokens"))
                if "prompt_tokens" not in raw and isinstance(incoming, int):
                    cached = [raw.get("cache_read_input_tokens", 0), raw.get("cache_creation_input_tokens", 0)]
                    if all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in cached):
                        incoming += sum(cached)
                    else:
                        incoming = None
                if all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (incoming,outgoing)):
                    usage = {"llm_input_tokens":incoming,"llm_output_tokens":outgoing}
            except (ValueError, AttributeError):
                pass
        if meter:
            meter.after(reservation, usage, "rejected" if response.status_code in {400,401,403,404,422,429} else "unknown")
        self.last_usage = usage
        if self.usage_callback and usage is not None:
            self.usage_callback(dict(usage))
        return response

    def _anthropic_messages_url(self) -> str:
        if self.base_url.endswith("/messages"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/messages"
        return f"{self.base_url}/v1/messages"

    @staticmethod
    def _max_tokens() -> int:
        raw = os.environ.get("PAPERAGENT_LLM_MAX_TOKENS", "").strip()
        if not raw:
            return DEFAULT_MAX_TOKENS
        try:
            return max(256, int(raw))
        except ValueError:
            return DEFAULT_MAX_TOKENS

    @staticmethod
    def _tight_json_retry_prompt(user: str) -> str:
        return (
            f"{user}\n\n"
            "The previous response did not include a final JSON action. "
            "Now respond with exactly one compact JSON object only. "
            "No thinking, no markdown, no commentary. "
            "If you are drafting a long grant application, argument sheet, "
            "report, or literature review, do NOT return the whole document "
            "in one write_draft action. Return one append_draft action for "
            "only the next section, keeping content under 2500 Chinese "
            "characters, then continue section by section in later turns."
        )

    @staticmethod
    def _anthropic_text(data: dict) -> str:
        parts = []
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        if parts:
            return "\n".join(parts)
        # Some Anthropic-compatible providers return nonstandard content
        # blocks such as {"type": "thinking", "thinking": "..."} even when
        # asked for JSON. If one of those blocks contains the JSON action,
        # salvage it instead of treating the response as empty.
        for block in data.get("content", []):
            if not isinstance(block, dict):
                continue
            for value in block.values():
                if isinstance(value, str) and "{" in value and "}" in value:
                    return value
        if isinstance(data.get("completion"), str):
            return data["completion"]
        raise ValueError(f"LLM returned no text content: {str(data)[:200]}")

    @staticmethod
    def _api_error_message(resp) -> str:
        """Surface the provider's own error code/message (e.g. GLM 1113 余额不足)."""
        try:
            data = resp.json()
            err = data.get("error", {})
            if isinstance(err, str):
                return f"LLM API error {resp.status_code}: {err}"
            code = err.get("code") or err.get("type") or resp.status_code
            message = err.get("message", resp.text[:200])
            return f"LLM API error {code}: {message}"
        except Exception:
            return f"LLM API error HTTP {resp.status_code}: {resp.text[:200]}"

    @staticmethod
    def _parse_json(content: str) -> dict:
        text = content.strip()
        if text.startswith("```"):
            match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I)
            if match:
                text = match.group(1).strip()
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end == -1:
            raise ValueError(
                "LLM returned a truncated JSON object. The action was likely "
                "too large for one response; retry with append_draft for one "
                f"section only. Partial response: {content[:200]}"
            )
        if start == -1 or end == -1:
            raise ValueError(f"LLM returned no JSON object: {content[:200]}")
        candidate = text[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
        repaired = re.sub(r"([{,]\s*)([A-Za-z_][\w-]*)(\s*:)", r'\1"\2"\3', repaired)
        repaired = repaired.replace("'", '"')
        return json.loads(repaired)
