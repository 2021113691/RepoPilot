import copy
import csv
import inspect
import json
from pathlib import Path

import pytest

from repopilot.context.lexical import ContextItem, RetrievedContext
from repopilot.evaluation.challenge import load_gold, prepare_baseline, qualify_all
from repopilot.evaluation.challenge_cases import CASES, ChallengeCase
from repopilot.evaluation.challenge_metrics import initial_context_equal, rank_improvement, score_run
from repopilot.models.base import ModelResponse
from repopilot.models.mock import MockBackend
from repopilot.models.openai_compatible import BackendConfig
from scripts.run_day4 import _write_csv
from scripts.run_day5_challenge import TransportRetryBackend, execute_method


def test_five_cases_qualify_without_model(tmp_path):
    qualifications = qualify_all(tmp_path / "runs")
    assert len(qualifications) == 5
    assert all(item.qualified for item in qualifications)
    assert all(item.initial_bug_file_rank is None or item.initial_bug_file_rank > 3 for item in qualifications)
    assert all(item.expected_failure_clue_novel and item.offline_dynamic_bug_file_rank in (1, 2, 3) for item in qualifications)


def test_gold_labels_are_outside_agent_workspace(tmp_path):
    assert "gold" not in inspect.signature(execute_method).parameters
    gold = load_gold()
    case = CASES["c01_service_pricing"]
    baseline, commit = prepare_baseline(case, tmp_path / "runs")
    assert not any(path.name.endswith("gold.json") for path in baseline.rglob("*"))
    assert gold[case.case_id]["expected_bug_file"] not in case.issue
    backend = MockBackend([ModelResponse(model="mock", content="need more work")])
    backend.config = BackendConfig("https://api-inference.modelscope.cn/v1", "unused", "mock")
    directory = execute_method(
        case, "b2_symbol", {"retrieval": {"context_budget_tokens": 8000}, "agent": {"max_steps": 1}},
        backend, baseline, commit, tmp_path / "runs",
    )
    first_request = json.dumps(backend.requests[0][0], ensure_ascii=False)
    assert gold[case.case_id]["expected_bug_file"] not in first_request
    assert gold[case.case_id]["expected_bug_symbol"] not in case.issue
    assert (directory / "trajectory.jsonl").exists()


def test_baseline_rejects_fixture_change(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "module.py").write_text("x = 1\n", encoding="utf-8")
    case = ChallengeCase("local_case", fixture, "fix module")
    prepare_baseline(case, tmp_path / "runs")
    (fixture / "module.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fixture or issue changed"):
        prepare_baseline(case, tmp_path / "runs")


def test_initial_context_equality_checks_scores_and_order():
    context = RetrievedContext([ContextItem("app/service.py", 1, 2, "x = 1", 10.0, 15, ["reason"])], 20, 1, 8000)
    b2 = context.as_dict()
    b3 = copy.deepcopy(b2)
    assert initial_context_equal(b2, b3)
    b3["items"][0]["score"] = 11.0
    assert not initial_context_equal(b2, b3)


def _artifact(tmp_path: Path, *, provider_error: bool = False, used: bool = True) -> Path:
    directory = tmp_path / "run"
    directory.mkdir(exist_ok=True)
    summary = {
        "case_id": "c01_service_pricing", "method": "b3_dynamic", "status": "model_error" if provider_error else "tests_passed",
        "error": "RuntimeError: Model API returned HTTP 429" if provider_error else None,
        "token_usage_complete": not provider_error, "input_tokens": 100 if not provider_error else None,
        "output_tokens": 20 if not provider_error else None, "llm_calls": 3,
        "tool_calls": 4, "tool_sequence": ["read_file", "apply_patch", "read_file", "apply_patch"],
        "repair_attempts": 2, "test_runs": 2, "test_failures": 1,
        "latency_sec": 1.2, "dynamic_refresh_count": 1 if not provider_error else 0,
        "dynamic_context_tokens": 200 if not provider_error else 0,
        "files_modified": ["engine/adjustments.py"], "task_id": "task", "baseline_commit": "abc",
    }
    (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    retrieval = RetrievedContext([ContextItem("app/order_service.py", 1, 8, "OrderService", 10, 20, [])], 30, 1, 8000).as_dict()
    (directory / "retrieval.json").write_text(json.dumps(retrieval), encoding="utf-8")
    events = [] if provider_error else [
        {"event": "tool_call", "tool": "read_file", "success": True, "metadata": {"file": "app/order_service.py"}},
        {"event": "tool_call", "tool": "apply_patch", "success": True, "metadata": {"patch_files": ["app/order_service.py"]}},
        {"event": "test_failure_evidence", "evidence": {
            "failed_tests": ["tests/test_order_service.py::test_zero_promotion"], "exception_types": ["ValueError"],
            "traceback_frames": [{"file": "engine/adjustments.py", "line": 5, "function": "apply_rate"}],
            "assertion_messages": [], "mentioned_files": ["engine/adjustments.py"],
            "mentioned_symbols": ["apply_rate"], "line_numbers": [5], "raw_summary": "failed",
        }},
        {"event": "dynamic_retrieval", "selected_files": ["engine/adjustments.py"], "new_files": ["engine/adjustments.py"], "new_symbols": ["apply_rate"]},
        {"event": "dynamic_context_injected", "files": ["engine/adjustments.py"]},
    ]
    if used:
        events.extend([
            {"event": "tool_call", "tool": "read_file", "success": True, "metadata": {"file": "engine/adjustments.py"}},
            {"event": "tool_call", "tool": "apply_patch", "success": True, "metadata": {"patch_files": ["engine/adjustments.py"]}},
        ])
    if not provider_error:
        events.append({"event": "test_run", "scope": None, "success": True})
    (directory / "trajectory.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    return directory


def test_rescue_requires_context_use_and_direction_change(tmp_path):
    gold = {"expected_bug_file": "engine/adjustments.py"}
    issue = "OrderService.calculate_total fails"
    row = score_run(_artifact(tmp_path), issue, gold)
    assert row["dynamic_rescue"] and row["dynamic_context_used"] and row["direction_changed"]
    assert row["initial_miss_dynamic_hit"] and row["dynamic_bug_file_rank"] == 1
    assert row["success_type"] == "dynamic_rescue_success"
    assert "engine/adjustments.py" in row["novel_file_clues"]
    assert rank_improvement(None, 1) is None and rank_improvement(7, 1) == 6
    row_unused = score_run(_artifact(tmp_path, used=False), issue, gold)
    assert not row_unused["dynamic_rescue"] and not row_unused["dynamic_context_used"]


def test_provider_failure_excluded_and_results_csv(tmp_path):
    row = score_run(_artifact(tmp_path, provider_error=True), "OrderService fails", {"expected_bug_file": "engine/adjustments.py"})
    assert not row["evaluable"] and row["failure_category"] == "provider_rate_limited"
    output = tmp_path / "results.csv"
    _write_csv([row], output)
    with output.open(encoding="utf-8", newline="") as stream:
        saved = next(csv.DictReader(stream))
    assert saved["failure_category"] == "provider_rate_limited"
    assert saved["novel_file_clues"] == "[]"
    summary_path = tmp_path / "run" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["error"] = "TimeoutError: The read operation timed out"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    timeout_row = score_run(tmp_path / "run", "OrderService fails", {"expected_bug_file": "engine/adjustments.py"})
    assert not timeout_row["evaluable"] and timeout_row["failure_category"] == "provider_failure"


def test_transport_retry_repeats_identical_request(monkeypatch):
    monkeypatch.setattr("scripts.run_day5_challenge.time.sleep", lambda seconds: None)

    class FlakyBackend:
        config = BackendConfig("https://api-inference.modelscope.cn/v1", "unused", "mock")

        def __init__(self):
            self.calls = []

        def chat(self, messages, tools=None):
            self.calls.append((messages, tools))
            if len(self.calls) == 1:
                raise TimeoutError("read timed out")
            return ModelResponse(model="mock", content="ok")

    flaky = FlakyBackend()
    messages = [{"role": "user", "content": "fix"}]
    assert TransportRetryBackend(flaky).chat(messages, []).content == "ok"
    assert flaky.calls == [(messages, []), (messages, [])]
