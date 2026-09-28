"""Small OpenAI Chat Completions-compatible HTTP backend."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from .base import ModelBackend, ModelResponse, TokenUsage, ToolCall


@dataclass(frozen=True)
class BackendConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 1024
    timeout: float = 60.0

    @classmethod
    def from_env(cls, *, base_url: str | None = None, model: str | None = None) -> "BackendConfig":
        url = base_url or os.getenv("REPOPILOT_BASE_URL")
        name = model or os.getenv("REPOPILOT_MODEL")
        key = os.getenv("REPOPILOT_API_KEY")
        # Existing ModelScope setups often use Anthropic-prefixed variables for
        # other clients. Reuse them only when the host is ModelScope's OpenAI API.
        legacy_url = os.getenv("ANTHROPIC_BASE_URL", "")
        if not url and urlsplit(legacy_url).hostname == "api-inference.modelscope.cn":
            url = legacy_url
            name = name or os.getenv("MODEL_ID")
            key = key or os.getenv("ANTHROPIC_API_KEY")
        if not url or not name or not key:
            raise ValueError("Set REPOPILOT_BASE_URL, REPOPILOT_MODEL and REPOPILOT_API_KEY")
        if urlsplit(url).hostname == "api-inference.modelscope.cn" and not urlsplit(url).path.strip("/"):
            url = url.rstrip("/") + "/v1"
        return cls(
            base_url=url,
            api_key=key,
            model=name,
            temperature=float(os.getenv("REPOPILOT_TEMPERATURE", "0")),
            max_tokens=int(os.getenv("REPOPILOT_MAX_TOKENS", "1024")),
            timeout=float(os.getenv("REPOPILOT_TIMEOUT", "60")),
        )


class OpenAICompatibleBackend(ModelBackend):
    def __init__(self, config: BackendConfig):
        self.config = config

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        url = self.config.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                data = json.load(response)
        except HTTPError as exc:
            # Do not include server response bodies: providers may echo credentials.
            raise RuntimeError(f"Model API returned HTTP {exc.code}") from None
        except URLError as exc:
            raise RuntimeError(f"Model API connection failed: {exc.reason}") from None
        try:
            choice = data["choices"][0]
            message = choice["message"]
            calls = [
                ToolCall(
                    id=call["id"],
                    name=call["function"]["name"],
                    arguments=json.loads(call["function"]["arguments"]),
                )
                for call in (message.get("tool_calls") or [])
            ]
            if any(not isinstance(call.arguments, dict) for call in calls):
                raise ValueError("Tool arguments must be JSON objects")
            usage = data.get("usage") or {}
            return ModelResponse(
                content=message.get("content"),
                tool_calls=calls,
                token_usage=TokenUsage(usage.get("prompt_tokens"), usage.get("completion_tokens")),
                model=data.get("model", self.config.model),
                finish_reason=choice.get("finish_reason"),
                raw_response=data,
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid model response: {type(exc).__name__}") from None
