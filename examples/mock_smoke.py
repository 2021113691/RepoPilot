"""Offline Day 1 smoke. Tool choices and final answer are scripted."""

from pathlib import Path

from repopilot.agent.loop import AgentLoop
from repopilot.models.base import ModelResponse, ToolCall
from repopilot.models.mock import MockBackend


ISSUE = "normalize_email() preserves uppercase domain characters in User@Example.COM. Locate the cause."


def main() -> None:
    workspace = Path(__file__).parent / "toy_repo"
    backend = MockBackend([
        ModelResponse(model="mock", tool_calls=[ToolCall("1", "list_files", {})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("2", "search_code", {"query": "normalize_email"})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("3", "read_file", {"path": "app/email_utils.py", "start_line": 1, "end_line": 6})]),
        ModelResponse(model="mock", content="app/email_utils.py:5 returns the original domain unchanged; normalize_email is the likely cause. Next inspect callers in app/service.py."),
    ])
    state = AgentLoop(backend, workspace).run(ISSUE)
    print(f"status={state.status} steps={state.step_count} tools={state.tool_sequence} files={sorted(state.files_seen)}")
    print(state.final_answer)
    print(f"trajectory=runs/{state.task_id}/trajectory.jsonl")


if __name__ == "__main__":
    main()
