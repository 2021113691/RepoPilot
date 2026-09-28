import difflib
import json

from repopilot.evaluation.toy_cases import CASES, ROOT
from repopilot.models.base import ModelResponse, ToolCall
from repopilot.models.mock import MockBackend
from scripts.run_experiment import load_config, report_tables, run_pair, write_csv


def _patch(path, before, after):
    return f"diff --git a/{path} b/{path}\n" + "".join(
        difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}")
    )


def test_paired_runner_same_baseline_and_metrics(tmp_path):
    path = ROOT / "examples/toy_repo/app/email_utils.py"
    original = path.read_text(encoding="utf-8")
    fixed = original.replace("{domain}", "{domain.lower()}")
    patch = _patch("app/email_utils.py", original, fixed)
    def backend():
        return MockBackend([
            ModelResponse(model="mock", tool_calls=[ToolCall("1", "apply_patch", {"patch": patch})]),
            ModelResponse(model="mock", tool_calls=[ToolCall("2", "git_diff", {})]),
            ModelResponse(model="mock", tool_calls=[ToolCall("3", "run_tests", {})]),
            ModelResponse(model="mock", content="verified"),
        ])
    b0 = load_config(ROOT / "experiments/b0_naive.yaml")
    b1 = load_config(ROOT / "experiments/b1_static.yaml")
    rows = run_pair("email", b0, b1, backend, tmp_path / "runs", {"model": "mock", "temperature": 0})
    assert len(rows) == 2 and all(row["success"] for row in rows)
    assert rows[0]["baseline_commit"] == rows[1]["baseline_commit"]
    assert rows[0]["baseline_tree"] == rows[1]["baseline_tree"]
    assert rows[0]["initial_context_tokens"] == 0 < rows[1]["initial_context_tokens"] <= 8000
    assert rows[0]["search_calls"] == rows[1]["search_calls"] == 0
    assert rows[1]["retrieval_hit5"] is True
    assert rows[0]["input_tokens"] is None and rows[1]["input_tokens"] is None
    retrieval = json.loads((tmp_path / "runs" / rows[1]["task_id"] / "retrieval.json").read_text(encoding="utf-8"))
    assert retrieval["selected_count"] == rows[1]["retrieved_snippets"]
    assert "app/email_utils.py" in retrieval["retrieved_files"]
    assert "N/A" in report_tables(rows)
    target = tmp_path / "results.csv"
    write_csv(rows, target)
    assert len(target.read_text(encoding="utf-8").splitlines()) == 3
