import json

from repopilot.agent.loop import AgentLoop
from repopilot.models.base import ModelResponse, TokenUsage, ToolCall
from repopilot.models.mock import MockBackend


def test_mock_agent_smoke(tmp_path):
    (tmp_path / "app.py").write_text("def normalize_email(address):\n    return address\n", encoding="utf-8")
    responses = [
        ModelResponse(tool_calls=[ToolCall("1", "list_files", {})], model="mock"),
        ModelResponse(tool_calls=[ToolCall("2", "search_code", {"query": "normalize_email"})], model="mock"),
        ModelResponse(tool_calls=[ToolCall("3", "read_file", {"path": "app.py", "start_line": 1, "end_line": 2})], model="mock"),
        ModelResponse(content="app.py:1 defines normalize_email; inspect its return logic next.", model="mock", token_usage=TokenUsage(5, 7)),
    ]
    backend = MockBackend(responses)
    state = AgentLoop(backend, tmp_path, tmp_path / "runs").run("Locate normalize_email")
    assert state.status == "completed"
    assert state.tool_sequence == ["list_files", "search_code", "read_file"]
    assert state.files_seen == {"app.py"}
    assert state.files_modified == set()
    assert state.input_tokens == 5 and state.output_tokens == 7
    assert backend.requests[0][0][0]["role"] == "system"
    summary = json.loads((tmp_path / "runs" / state.task_id / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "completed"
    events = [json.loads(line)["event"] for line in (tmp_path / "runs" / state.task_id / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events.count("llm_call") == 4 and events.count("tool_call") == 3


def test_tool_failure_is_observation(tmp_path):
    backend = MockBackend([
        ModelResponse(tool_calls=[ToolCall("1", "read_file", {"path": "../secret"})]),
        ModelResponse(content="The requested path is outside the workspace."),
    ])
    state = AgentLoop(backend, tmp_path, tmp_path / "runs").run("Inspect file")
    assert state.status == "completed"
    assert "workspace boundary violation" in backend.requests[1][0][-1]["content"]


def test_step_budget_stops_loop(tmp_path):
    backend = MockBackend([ModelResponse(tool_calls=[ToolCall("1", "list_files", {})])])
    state = AgentLoop(backend, tmp_path, tmp_path / "runs", max_steps=1).run("Inspect repo")
    assert state.status == "budget_exhausted" and state.step_count == 1


def test_tool_budget_stops_loop(tmp_path):
    backend = MockBackend([ModelResponse(tool_calls=[ToolCall("1", "list_files", {}), ToolCall("2", "list_files", {})])])
    state = AgentLoop(backend, tmp_path, tmp_path / "runs", max_tool_calls=1).run("Inspect repo")
    assert state.status == "budget_exhausted" and state.tool_calls == 1
