"""Deterministic backend for loop tests and an offline smoke run."""

from __future__ import annotations

from collections import deque

from .base import ModelBackend, ModelResponse


class MockBackend(ModelBackend):
    def __init__(self, responses: list[ModelResponse]):
        self.responses = deque(responses)
        self.requests: list[tuple[list[dict], list[dict] | None]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        self.requests.append(([message.copy() for message in messages], tools))
        if not self.responses:
            raise RuntimeError("MockBackend has no response left")
        return self.responses.popleft()
