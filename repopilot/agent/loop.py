"""Bounded, provider-neutral read-only agent loop."""

from __future__ import annotations

import json
import time
from pathlib import Path

from repopilot.agent.prompts import system_prompt
from repopilot.agent.state import AgentState, utc_now
from repopilot.evaluation.trajectory import TrajectoryLogger
from repopilot.models.base import ModelBackend, ModelResponse
from repopilot.runtime.environment import RuntimeContext
from repopilot.tools.base import ToolRegistry
from repopilot.tools.files import ListFiles, ReadFile
from repopilot.tools.git import GitDiff
from repopilot.tools.search import SearchCode


def default_tools(workspace: Path) -> ToolRegistry:
    return ToolRegistry([ListFiles(workspace), SearchCode(workspace), ReadFile(workspace), GitDiff(workspace)])


class AgentLoop:
    def __init__(
        self,
        backend: ModelBackend,
        workspace: Path,
        runs_dir: Path = Path("runs"),
        max_steps: int = 20,
        max_tool_calls: int = 40,
    ):
        if max_steps < 1 or max_tool_calls < 1:
            raise ValueError("budgets must be positive")
        self.backend = backend
        self.workspace = workspace.resolve(strict=True)
        self.tools = default_tools(self.workspace)
        self.runs_dir = runs_dir
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls

    def run(self, issue: str) -> AgentState:
        if not issue.strip():
            raise ValueError("issue must not be empty")
        state = AgentState(issue=issue, workspace=self.workspace)
        logger = TrajectoryLogger(self.runs_dir, state.task_id)
        runtime = RuntimeContext.detect(self.workspace)
        run_start = time.perf_counter()
        state.messages = [
            {"role": "system", "content": system_prompt(runtime)},
            {"role": "user", "content": issue},
        ]
        logger.event("start", task_id=state.task_id, issue=issue, runtime=runtime.__dict__, max_steps=self.max_steps, max_tool_calls=self.max_tool_calls)
        state.status = "running"
        try:
            for step in range(1, self.max_steps + 1):
                state.step_count = step
                begin = time.perf_counter()
                response = self.backend.chat(state.messages, self.tools.schemas())
                latency = time.perf_counter() - begin
                state.llm_calls += 1
                state.add_usage(response.token_usage)
                logger.event(
                    "llm_call", step=step, model=response.model, input_tokens=response.token_usage.input_tokens,
                    output_tokens=response.token_usage.output_tokens, latency_sec=round(latency, 4),
                    finish_reason=response.finish_reason, content=response.content,
                    tool_calls=[{"id": call.id, "name": call.name, "arguments": call.arguments} for call in response.tool_calls],
                )
                state.messages.append(self._assistant_message(response))
                if not response.tool_calls:
                    state.final_answer = response.content or ""
                    state.status = "completed"
                    break
                for call in response.tool_calls:
                    if state.tool_calls >= self.max_tool_calls:
                        state.status = "budget_exhausted"
                        break
                    result = self.tools.execute(call.name, call.arguments)
                    state.tool_calls += 1
                    state.tool_sequence.append(call.name)
                    if result.success and call.name == "read_file" and "file" in result.metadata:
                        state.files_seen.add(result.metadata["file"])
                    state.messages.append({"role": "tool", "tool_call_id": call.id, "content": result.observation()})
                    logger.event(
                        "tool_call", step=step, tool=call.name, arguments=call.arguments,
                        success=result.success, error=result.error, observation=result.observation(),
                        metadata=result.metadata, latency_sec=round(result.duration, 4),
                    )
                if state.status == "budget_exhausted":
                    break
            else:
                state.status = "budget_exhausted"
        except Exception as exc:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            logger.event("error", step=state.step_count, error=state.error)
        finally:
            state.finished_at = utc_now()
            state.latency_sec = round(time.perf_counter() - run_start, 4)
            logger.event("finish", status=state.status, steps=state.step_count)
            logger.finish(state)
        return state

    @staticmethod
    def _assistant_message(response: ModelResponse) -> dict:
        message: dict = {"role": "assistant", "content": response.content}
        if response.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)}}
                for call in response.tool_calls
            ]
        return message
