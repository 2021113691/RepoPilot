import difflib
import json

from repopilot.agent.loop import AgentLoop
from repopilot.evaluation.toy_cases import prepare_case
from repopilot.models.base import ModelResponse, ToolCall
from repopilot.models.mock import MockBackend


def patch_for(path, before, after):
    return f"diff --git a/{path} b/{path}\n" + "".join(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}")
    )


def scripted(*calls):
    return MockBackend([
        *(ModelResponse(model="mock", tool_calls=[ToolCall(str(i), name, arguments)]) for i, (name, arguments) in enumerate(calls, 1)),
        ModelResponse(model="mock", content="Root cause found; changed code; full pytest passed."),
    ])


def test_mock_failure_then_retry(tmp_path):
    case, workspace = prepare_case("discount", tmp_path / "runs")
    original = (workspace / "app/pricing.py").read_text(encoding="utf-8")
    first = original.replace("rate <= 0", "rate < 0")
    second = first.replace(
        '"""Toy discount calculation with two boundary problems."""\n',
        '"""Toy discount calculation with two boundary problems."""\n\nfrom decimal import Decimal, ROUND_HALF_UP\n',
    ).replace(
        "    return round(price * (1 - rate), 2)",
        '    amount = Decimal(str(price)) * (Decimal("1") - Decimal(str(rate)))\n    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))',
    )
    backend = scripted(
        ("read_file", {"path": "app/pricing.py"}),
        ("apply_patch", {"patch": patch_for("app/pricing.py", original, first)}),
        ("git_diff", {}),
        ("run_tests", {}),
        ("read_file", {"path": "tests/test_pricing.py"}),
        ("apply_patch", {"patch": patch_for("app/pricing.py", first, second)}),
        ("git_diff", {}),
        ("run_tests", {}),
    )
    state = AgentLoop(backend, workspace, runs_dir=tmp_path / "runs", repair=True).run(case.issue)
    assert state.status == "tests_passed", state.final_answer
    assert (state.patch_count, state.test_runs, state.test_failures, state.repair_attempts) == (2, 2, 1, 2)
    assert state.files_modified == {"app/pricing.py"}
    events = [json.loads(line) for line in (tmp_path / "runs" / state.task_id / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(event["event"] == "repair_retry" for event in events) == 1
    assert sum(event["event"] == "test_failure" for event in events) == 1
    assert backend.requests[4][0][-1]["content"].startswith("Test Result: FAILED")


def test_regression_requires_full_suite_and_second_patch(tmp_path):
    case, workspace = prepare_case("regression", tmp_path / "runs")
    original = (workspace / "app/slug.py").read_text(encoding="utf-8")
    first = original.replace('return text.lower().replace(" ", "-")', 'return "-".join(text.lower().split())')
    second = first.replace('"""Preserve non-space separators while normalizing literal spaces."""\n', '"""Preserve non-space separators while normalizing literal spaces."""\n\nimport re\n').replace('return "-".join(text.lower().split())', 'return re.sub(r" +", "-", text.lower())')
    backend = scripted(
        ("read_file", {"path": "app/slug.py"}),
        ("apply_patch", {"patch": patch_for("app/slug.py", original, first)}),
        ("git_diff", {}),
        ("run_tests", {"scope": case.targeted_test}),
        ("run_tests", {}),
        ("apply_patch", {"patch": patch_for("app/slug.py", first, second)}),
        ("git_diff", {}),
        ("run_tests", {}),
    )
    state = AgentLoop(backend, workspace, runs_dir=tmp_path / "runs", repair=True).run(case.issue)
    assert state.status == "tests_passed", state.final_answer
    assert state.patch_count == 2 and state.test_runs == 3 and state.test_failures == 1
    assert "test_tabs_remain_unchanged" in backend.requests[5][0][-1]["content"]


def test_model_claim_without_test_is_incomplete(tmp_path):
    case, workspace = prepare_case("email", tmp_path / "runs")
    backend = MockBackend([ModelResponse(content="The bug is fixed."), ModelResponse(content="Everything passes.")])
    state = AgentLoop(backend, workspace, runs_dir=tmp_path / "runs", repair=True, max_steps=2).run(case.issue)
    assert state.status == "budget_exhausted"
    assert state.tests_passed is False
    assert state.final_answer.startswith("Repair incomplete.")


def test_repair_rejects_dirty_baseline(tmp_path):
    case, workspace = prepare_case("email", tmp_path / "runs")
    (workspace / "app/email_utils.py").write_text("changed\n", encoding="utf-8")
    state = AgentLoop(MockBackend([]), workspace, runs_dir=tmp_path / "runs", repair=True).run(case.issue)
    assert state.status == "tool_error"
    assert "clean Git baseline" in state.error


def test_patch_attempt_budget_is_enforced(tmp_path):
    case, workspace = prepare_case("email", tmp_path / "runs")
    backend = MockBackend([
        ModelResponse(tool_calls=[ToolCall("1", "apply_patch", {"patch": "invalid"})]),
        ModelResponse(tool_calls=[ToolCall("2", "apply_patch", {"patch": "invalid"})]),
    ])
    state = AgentLoop(backend, workspace, runs_dir=tmp_path / "runs", repair=True, max_patch_attempts=1).run(case.issue)
    assert state.status == "budget_exhausted" and state.repair_attempts == 1


def test_test_run_budget_is_enforced(tmp_path):
    case, workspace = prepare_case("email", tmp_path / "runs")
    backend = MockBackend([
        ModelResponse(tool_calls=[ToolCall("1", "run_tests", {"scope": case.targeted_test})]),
        ModelResponse(tool_calls=[ToolCall("2", "run_tests", {})]),
    ])
    state = AgentLoop(backend, workspace, runs_dir=tmp_path / "runs", repair=True, max_test_runs=1).run(case.issue)
    assert state.status == "budget_exhausted" and state.test_runs == 1


def test_verified_repair_at_last_model_step_keeps_success(tmp_path):
    case, workspace = prepare_case("email", tmp_path / "runs")
    original = (workspace / "app/email_utils.py").read_text(encoding="utf-8")
    fixed = original.replace("{domain}", "{domain.lower()}")
    backend = MockBackend([ModelResponse(tool_calls=[
        ToolCall("1", "apply_patch", {"patch": patch_for("app/email_utils.py", original, fixed)}),
        ToolCall("2", "git_diff", {}),
        ToolCall("3", "run_tests", {}),
    ])])
    state = AgentLoop(backend, workspace, runs_dir=tmp_path / "runs", repair=True, max_steps=1).run(case.issue)
    assert state.status == "tests_passed"
    assert "Verified repair at the model-step limit" in state.final_answer


def test_mock_agent_recovers_after_protected_test_patch(tmp_path):
    case, workspace = prepare_case("email", tmp_path / "runs")
    test_path = workspace / "tests/test_email_utils.py"
    original_test = test_path.read_text(encoding="utf-8")
    changed_test = original_test.replace("User@example.com", "User@Example.COM")
    assert changed_test != original_test
    source_path = workspace / "app/email_utils.py"
    original_source = source_path.read_text(encoding="utf-8")
    fixed_source = original_source.replace("{domain}", "{domain.lower()}")
    backend = scripted(
        ("apply_patch", {"patch": patch_for("tests/test_email_utils.py", original_test, changed_test)}),
        ("apply_patch", {"patch": patch_for("app/email_utils.py", original_source, fixed_source)}),
        ("git_diff", {}),
        ("run_tests", {}),
    )
    state = AgentLoop(backend, workspace, runs_dir=tmp_path / "runs", repair=True).run(case.issue)
    assert state.status == "tests_passed", state.final_answer
    assert state.patch_count == 1 and state.repair_attempts == 2
    assert state.protected_patch_rejections == 1
    assert test_path.read_text(encoding="utf-8") == original_test
    assert "read-only verification oracles" in backend.requests[1][0][-1]["content"]
    directory = tmp_path / "runs" / state.task_id
    events = [json.loads(line) for line in (directory / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "patch_rejected" and event["reason"] == "protected_path" and event["paths"] == ["tests/test_email_utils.py"] for event in events)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["protected_patch_rejections"] == 1
