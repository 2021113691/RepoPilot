"""Mutable state for one agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from repopilot.models.base import TokenUsage


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentState:
    issue: str
    workspace: Path
    task_id: str = field(default_factory=lambda: uuid4().hex)
    messages: list[dict] = field(default_factory=list)
    step_count: int = 0
    files_seen: set[str] = field(default_factory=set)
    files_modified: set[str] = field(default_factory=set)
    tool_calls: int = 0
    llm_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    status: str = "pending"
    final_answer: str | None = None
    error: str | None = None
    latency_sec: float | None = None
    tool_sequence: list[str] = field(default_factory=list)
    repair_mode: bool = False
    patch_count: int = 0
    repair_attempts: int = 0
    test_runs: int = 0
    test_failures: int = 0
    changed_loc: int = 0
    tests_passed: bool = False
    last_test_result: dict | None = None
    current_diff: str | None = None
    last_full_pass_patch_count: int = -1
    diff_reviewed_patch_count: int = -1

    def add_usage(self, usage: TokenUsage) -> None:
        if usage.input_tokens is not None:
            self.input_tokens = (self.input_tokens or 0) + usage.input_tokens
        if usage.output_tokens is not None:
            self.output_tokens = (self.output_tokens or 0) + usage.output_tokens
