"""Provider-neutral model request and response types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    model: str | None = None
    finish_reason: str | None = None
    raw_response: Any = field(default=None, repr=False)


class ModelBackend(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        """Return one assistant turn, possibly containing tool calls."""
