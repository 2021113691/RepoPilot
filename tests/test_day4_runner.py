import difflib
import json

from repopilot.evaluation.toy_cases import ROOT, prepare_case
from repopilot.models.base import ModelResponse, ToolCall
from repopilot.models.mock import MockBackend
from repopilot.models.openai_compatible import BackendConfig
from scripts.run_day4 import run_b2


def test_b2_runner_logs_symbols_redundancy_and_restores(tmp_path):
    _, baseline = prepare_case("email", tmp_path / "day3")
    source = (baseline / "app/email_utils.py").read_text(encoding="utf-8")
    fixed = source.replace("{domain}", "{domain.lower()}")
    patch = "diff --git a/app/email_utils.py b/app/email_utils.py\n" + "".join(
        difflib.unified_diff(source.splitlines(keepends=True), fixed.splitlines(keepends=True), fromfile="a/app/email_utils.py", tofile="b/app/email_utils.py")
    )
    backend = MockBackend([
        ModelResponse(model="mock", tool_calls=[ToolCall("1", "read_file", {"path": "app/email_utils.py"})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("2", "apply_patch", {"patch": patch})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("3", "git_diff", {})]),
        ModelResponse(model="mock", tool_calls=[ToolCall("4", "run_tests", {})]),
        ModelResponse(model="mock", content="verified"),
    ])
    backend.config = BackendConfig("https://example.test/v1", "unused", "mock")
    config = json.loads((ROOT / "experiments/b2_symbol.yaml").read_text(encoding="utf-8"))
    row = run_b2("email", config, backend, baseline, tmp_path / "day4")
    assert row["success"] and row["method"] == "b2_symbol"
    assert row["modified_file_rank"] == 1 and row["top1_role"] == "source"
    assert row["same_file_reread_calls"] == 1
    assert row["overlapping_reread_calls"] == 1
    directory = tmp_path / "day4" / row["task_id"]
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["retrieval_mode"] == "symbol"
    assert summary["overlapping_reread_calls"] == 1
    assert "exact_symbol_definition" in (directory / "retrieval.json").read_text(encoding="utf-8")
    assert (baseline / "app/email_utils.py").read_text(encoding="utf-8") == source
