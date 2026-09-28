import difflib
import inspect
import json
import shutil
import subprocess

from repopilot.agent.dynamic_hook import DynamicContextHook
from repopilot.agent.loop import AgentLoop
from repopilot.agent.state import AgentState
from repopilot.context.dynamic import DynamicRefresh, DynamicRetriever
from repopilot.context.failure import TracebackFrame, build_failure_query, extract_failure_evidence, failure_signature
from repopilot.context.lexical import ContextItem, RetrievedContext, estimate_tokens
from repopilot.context import SymbolRetriever, format_context
from repopilot.evaluation.toy_cases import ROOT
from repopilot.evaluation.trajectory import TrajectoryLogger
from repopilot.models.base import ModelResponse, ToolCall
from repopilot.models.mock import MockBackend
from repopilot.tools.tests import RunTests
from repopilot.tools.base import ToolResult

ISSUE = "create_receipt() in app/service.py fails for amount 1.75; it should preserve cents."


def _repo(tmp_path):
    source = ROOT / "examples/toy_cases/dynamic_rescue"
    root = tmp_path / "repo"
    shutil.copytree(source, root)
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "baseline"], cwd=root, check=True)
    return root


def _failed_result():
    return {
        "failed_tests": ["tests/test_receipt.py::test_receipt_preserves_cents"],
        "exception_type": "RuntimeError",
        "assertion_message": "round_price failed",
        "traceback_locations": ["internal/pricing.py:5"],
        "stdout": "FAILED tests/test_receipt.py::test_receipt_preserves_cents - RuntimeError\ninternal/pricing.py:5: in round_price\nE RuntimeError: round_price failed\n",
        "stderr": "",
    }


def _patch(path, before, after):
    return f"diff --git a/{path} b/{path}\n" + "".join(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}")
    )


def test_failure_evidence_extracts_test_and_traceback():
    evidence = extract_failure_evidence(_failed_result())
    assert evidence.failed_tests == ("tests/test_receipt.py::test_receipt_preserves_cents",)
    assert TracebackFrame("internal/pricing.py", 5, "round_price") in evidence.traceback_frames
    assert "internal/pricing.py" in evidence.mentioned_files
    assert "round_price" in evidence.mentioned_symbols
    assert evidence.exception_types[0] == "RuntimeError"


def test_failure_evidence_falls_back_to_pytest_failed_line():
    data = _failed_result()
    data["failed_tests"] = []
    evidence = extract_failure_evidence(data)
    assert evidence.failed_tests == ("tests/test_receipt.py::test_receipt_preserves_cents",)


def test_failure_evidence_extracts_assertion():
    data = _failed_result()
    data["assertion_message"] = "assert calculate_total(2) == 3"
    evidence = extract_failure_evidence(data)
    assert "calculate_total" in " ".join(evidence.assertion_messages)


def test_failure_query_merges_original_issue_and_new_symbols():
    evidence = extract_failure_evidence(_failed_result())
    query = build_failure_query(ISSUE, evidence)
    assert "create_receipt" in query.identifiers
    assert "round_price" in query.identifiers
    assert "internal/pricing.py" in query.file_hints
    assert "tests/test_receipt.py" in query.test_hints
    assert ISSUE in query.retrieval_text()


def test_failure_signature_is_deterministic():
    evidence = extract_failure_evidence(_failed_result())
    assert failure_signature(evidence) == failure_signature(extract_failure_evidence(_failed_result()))


def test_initial_miss_then_traceback_file_and_symbol_hit(tmp_path):
    root = _repo(tmp_path)
    initial = SymbolRetriever().retrieve(ISSUE, root).context
    assert "internal/pricing.py" not in [item.file for item in initial.items]
    evidence = extract_failure_evidence(_failed_result())
    refresh = DynamicRetriever().retrieve(ISSUE, root, initial, evidence)
    assert refresh.items and refresh.items[0].file == "internal/pricing.py"
    assert refresh.after_top3[0] == "internal/pricing.py"
    assert "internal/pricing.py" in refresh.new_files
    assert any(reason.startswith("traceback_file_hit") for reason in refresh.items[0].evidence)
    assert any(reason.startswith("traceback_symbol_hit") for reason in refresh.items[0].evidence)


def test_failed_test_source_bonus(tmp_path):
    root = _repo(tmp_path)
    initial = SymbolRetriever().retrieve(ISSUE, root).context
    evidence = extract_failure_evidence(_failed_result())
    refresh = DynamicRetriever().retrieve(ISSUE, root, initial, evidence)
    assert any("failed_test_source_relation" in item.evidence for item in refresh.items if item.file == "app/receipt.py") is False
    # A synthetic failed test with the same stem as service should grant the relation bonus.
    data = _failed_result()
    data["failed_tests"] = ["tests/test_service.py::test_receipt"]
    result = DynamicRetriever().retrieve(ISSUE, root, RetrievedContext([], estimate_tokens("# Retrieved Repository Context\n"), 0, 8000), extract_failure_evidence(data))
    assert any("failed_test_source_relation" in item.evidence for item in result.items if item.file == "app/service.py")


def test_novelty_skips_exact_span_and_allows_new_range(tmp_path):
    root = _repo(tmp_path)
    evidence = extract_failure_evidence(_failed_result())
    initial = SymbolRetriever().retrieve(ISSUE, root).context
    candidate = DynamicRetriever().retrieve(ISSUE, root, initial, evidence)
    pricing = next(item for item in candidate.items if item.file == "internal/pricing.py")
    covered = RetrievedContext([*initial.items, pricing], initial.total_tokens + pricing.token_cost, initial.candidate_count, 8000)
    again = DynamicRetriever().retrieve(ISSUE, root, covered, evidence)
    assert all(not (item.file == pricing.file and item.start_line == pricing.start_line and item.end_line == pricing.end_line) for item in again.items)
    header_only = ContextItem("internal/pricing.py", 1, 2, "header", 1.0, 10, [])
    partial = RetrievedContext([*initial.items, header_only], initial.total_tokens + 10, initial.candidate_count, 8000)
    new_range = DynamicRetriever().retrieve(ISSUE, root, partial, evidence)
    assert any(item.file == "internal/pricing.py" and any(reason.startswith("novelty: new_") for reason in item.evidence) for item in new_range.items)


def test_dynamic_refresh_budget_and_no_gold_api(tmp_path):
    root = _repo(tmp_path)
    initial = SymbolRetriever().retrieve(ISSUE, root).context
    evidence = extract_failure_evidence(_failed_result())
    result = DynamicRetriever().retrieve(ISSUE, root, initial, evidence, 500)
    assert result.total_tokens <= 500
    assert set(inspect.signature(DynamicRetriever.retrieve).parameters) == {"self", "issue", "workspace", "initial_context", "evidence", "refresh_budget_tokens", "seen_items"}


def test_trigger_only_failed_tests_and_suppress_duplicate_failure(tmp_path):
    root = _repo(tmp_path)
    initial = SymbolRetriever().retrieve(ISSUE, root).context
    state = AgentState(ISSUE, root)
    logger = TrajectoryLogger(tmp_path / "runs", state.task_id)
    hook = DynamicContextHook(ISSUE, root, initial)
    failed = ToolResult(False, metadata={"test_result": _failed_result()})
    for name in ("apply_patch", "read_file", "search_code"):
        hook.on_tool_result(state, logger, 1, name, {}, failed)
    hook.on_tool_result(state, logger, 1, "run_tests", {}, ToolResult(True, metadata={"test_result": _failed_result()}))
    hook.after_step(state, logger, 1)
    assert state.dynamic_refresh_count == 0
    hook.on_tool_result(state, logger, 2, "run_tests", {}, failed)
    hook.after_step(state, logger, 2)
    assert state.dynamic_refresh_count == 1
    hook.on_tool_result(state, logger, 3, "run_tests", {}, failed)
    hook.after_step(state, logger, 3)
    assert state.dynamic_refresh_count == 1
    assert len(state.failure_signatures_seen) == 1
    events = [json.loads(line) for line in logger.path.read_text(encoding="utf-8").splitlines()]
    assert sum(event["event"] == "dynamic_context_injected" for event in events) == 1


def test_dynamic_refresh_limit_is_two(tmp_path):
    root = _repo(tmp_path)
    initial = SymbolRetriever().retrieve(ISSUE, root).context
    state = AgentState(ISSUE, root)
    logger = TrajectoryLogger(tmp_path / "runs", state.task_id)

    class DistinctRetriever:
        def __init__(self):
            self.calls = 0

        def retrieve(self, issue, workspace, initial_context, evidence, budget, seen_items):
            self.calls += 1
            item = ContextItem(f"internal/new{self.calls}.py", 1, 1, "x = 1", 1, 15, ["novelty: new_file"])
            return DynamicRefresh((item,), 100, 1, (), (item.file,), (item.file,), (), 1, evidence.raw_summary)

    retriever = DistinctRetriever()
    hook = DynamicContextHook(ISSUE, root, initial, retriever=retriever)
    for step in range(1, 4):
        data = _failed_result()
        data["failed_tests"] = [f"tests/test_receipt.py::test_failure_{step}"]
        hook.on_tool_result(state, logger, step, "run_tests", {}, ToolResult(False, metadata={"test_result": data}))
        hook.after_step(state, logger, step)
    assert state.dynamic_refresh_count == 2
    assert retriever.calls == 2


def test_mock_dynamic_rescue(tmp_path):
    root = _repo(tmp_path)
    initial = SymbolRetriever().retrieve(ISSUE, root).context
    assert "internal/pricing.py" not in [item.file for item in initial.items]
    service = (root / "app/service.py").read_text(encoding="utf-8")
    first = service.replace("    return round_price(amount)", '    """Keep receipt amount precise."""\n    return round_price(amount)')
    pricing = (root / "internal/pricing.py").read_text(encoding="utf-8")
    fixed = pricing.replace('raise RuntimeError("round_price failed")', 'return round(value, 2)')
    backend = MockBackend([
        ModelResponse(model="mock", tool_calls=[ToolCall("1", "read_file", {"path": "app/service.py"})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("2", "apply_patch", {"patch": _patch("app/service.py", service, first)})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("3", "run_tests", {})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("4", "read_file", {"path": "internal/pricing.py"})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("5", "apply_patch", {"patch": _patch("internal/pricing.py", pricing, fixed)})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("6", "git_diff", {})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("7", "run_tests", {})]),
        ModelResponse(model="mock", content="verified"),
    ])
    hook = DynamicContextHook(ISSUE, root, initial)
    state = AgentLoop(backend, root, runs_dir=tmp_path / "runs", repair=True, event_hook=hook).run(
        ISSUE, initial_context=format_context(initial), initial_context_tokens=initial.total_tokens,
    )
    assert state.status == "tests_passed", state.error
    assert state.dynamic_refresh_count == 1
    assert "internal/pricing.py" in state.dynamic_new_files
    assert state.files_modified == {"app/service.py", "internal/pricing.py"}
    assert any(message["role"] == "user" and message["content"].startswith("# Failure-Driven Repository Context") and "internal/pricing.py" in message["content"] for message in backend.requests[3][0])
    events = [json.loads(line) for line in (tmp_path / "runs" / state.task_id / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "dynamic_retrieval" and event["after_top3"][0] == "internal/pricing.py" for event in events)
    assert any(event["event"] == "dynamic_context_injected" for event in events)
