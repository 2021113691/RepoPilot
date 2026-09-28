"""Run B3 only against frozen Day 4 baselines and persist transition metrics."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repopilot.__main__ import load_dotenv
from repopilot.agent.dynamic_hook import DynamicContextHook
from repopilot.agent.loop import AgentLoop
from repopilot.context import SymbolRetriever, format_context
from repopilot.evaluation.redundancy import analyze_redundant_reads
from repopilot.evaluation.toy_cases import CASES, ROOT
from repopilot.models.openai_compatible import BackendConfig, OpenAICompatibleBackend
from scripts.run_day4 import _model_record, _write_csv
from scripts.run_experiment import _clean, _disposable, _git

RESULT_FIELDS = (
    "case_id", "method", "success", "status", "error", "input_tokens", "output_tokens",
    "llm_calls", "tool_calls", "read_calls", "search_calls", "test_runs", "patch_attempts",
    "latency_sec", "initial_context_tokens", "initial_hit1", "initial_hit3", "initial_hit5",
    "initial_modified_file_rank", "dynamic_modified_file_rank", "dynamic_refreshes",
    "dynamic_context_tokens", "dynamic_items_added", "dynamic_new_files", "dynamic_new_symbols",
    "failure_signatures", "failure_to_new_context_latency_sec", "dynamic_modified_file_hit",
    "dynamic_rescue", "triggered_without_novel_context", "same_file_rereads", "overlap_rereads",
    "initial_files", "dynamic_files", "files_modified", "task_id", "baseline_commit", "baseline_tree",
)


class RateLimitRetryBackend:
    """B3 runner-only bounded backoff for provider HTTP 429 responses."""

    def __init__(self, backend: OpenAICompatibleBackend):
        self.backend = backend
        self.config = backend.config

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        for delay in (0, 15, 45):
            if delay:
                time.sleep(delay)
            try:
                return self.backend.chat(messages, tools)
            except RuntimeError as exc:
                if str(exc) != "Model API returned HTTP 429" or delay == 45:
                    raise
        raise AssertionError("unreachable")


def frozen_inputs(config: dict, day4_results: Path, day4_runs: Path, model: dict, cases: list[str]) -> dict[str, dict]:
    b2 = json.loads((ROOT / "experiments/b2_symbol.yaml").read_text(encoding="utf-8"))
    b1 = json.loads((ROOT / "experiments/b1_static.yaml").read_text(encoding="utf-8"))
    b0 = json.loads((ROOT / "experiments/b0_naive.yaml").read_text(encoding="utf-8"))
    if config["name"] != "b3_dynamic" or config["agent"] != b2["agent"] or b2["agent"] != b1["agent"] or b1["agent"] != b0["agent"]:
        raise ValueError("B0/B1/B2/B3 agent settings differ")
    initial = config["retrieval"]["initial"]
    dynamic = config["retrieval"]["dynamic"]
    if initial != {"mode": b2["retrieval"]["mode"], "context_budget_tokens": b2["retrieval"]["context_budget_tokens"]}:
        raise ValueError("B3 initial retrieval differs from frozen B2")
    if dynamic != {"enabled": True, "refresh_budget_tokens": 4000, "max_refreshes": 2}:
        raise ValueError("unexpected B3 dynamic settings")
    with day4_results.open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["method"] == "b2_symbol"]
    result = {}
    for case in cases:
        matching = [row for row in rows if row["case_id"] == case]
        if len(matching) != 1:
            raise ValueError(f"expected exactly one frozen B2 row for {case}")
        row = matching[0]
        manifest = json.loads((day4_runs / row["task_id"] / "experiment.json").read_text(encoding="utf-8"))
        if manifest["config"] != b2 or manifest["model"] != model or manifest["baseline_commit"] != row["baseline_commit"]:
            raise ValueError(f"frozen B2 manifest mismatch: {case}")
        baseline = Path(manifest["workspace"]).resolve(strict=True)
        if not _clean(baseline) or _git(baseline, "rev-parse", "HEAD") != row["baseline_commit"]:
            raise ValueError(f"frozen B2 baseline unavailable or dirty: {case}")
        result[case] = {"row": row, "baseline": baseline}
    return result


def _rank(files: list[str], modified: set[str]) -> int | None:
    return next((index for index, file in enumerate(files, 1) if file in modified), None)


def _previous_rows(runs_dir: Path, cases: list[str]) -> list[dict]:
    latest: dict[str, dict] = {}
    for path in runs_dir.glob("*/summary.json"):
        summary = json.loads(path.read_text(encoding="utf-8"))
        case = summary.get("case_id")
        if summary.get("method") == "b3_dynamic" and case in cases:
            if case not in latest or summary["finished_at"] > latest[case]["finished_at"]:
                latest[case] = summary
    rows = []
    for case in cases:
        if case not in latest:
            continue
        summary = latest[case]
        row = {key: summary.get(key) for key in RESULT_FIELDS}
        if row["triggered_without_novel_context"] is None:
            row["triggered_without_novel_context"] = summary.get("triggered_no_useful_new_context", False)
        rows.append(row)
    return rows


def run_b3(case_id: str, config: dict, backend: RateLimitRetryBackend, baseline: Path, runs_dir: Path) -> dict:
    workspace = runs_dir.resolve() / "toy_cases" / f"{case_id}-b3-{uuid4().hex[:12]}"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(baseline), str(workspace)], check=True, capture_output=True, timeout=20)
    if not _disposable(workspace, runs_dir) or not _clean(workspace) or _git(workspace, "rev-parse", "HEAD") != _git(baseline, "rev-parse", "HEAD"):
        raise ValueError("B3 workspace is not a clean clone of the frozen baseline")
    baseline_commit = _git(workspace, "rev-parse", "HEAD")
    baseline_tree = _git(workspace, "rev-parse", "HEAD^{tree}")
    try:
        started = time.perf_counter()
        issue = CASES[case_id].issue
        initial = SymbolRetriever().retrieve(issue, workspace, config["retrieval"]["initial"]["context_budget_tokens"])
        retrieval_latency = round(time.perf_counter() - started, 4)
        context = initial.context
        dynamic = config["retrieval"]["dynamic"]
        hook = DynamicContextHook(issue, workspace, context, dynamic["refresh_budget_tokens"], dynamic["max_refreshes"])
        state = AgentLoop(backend, workspace, runs_dir=runs_dir, repair=True, event_hook=hook, **config["agent"]).run(
            issue, initial_context=format_context(context), initial_context_tokens=context.total_tokens,
        )
        latency = round(time.perf_counter() - started, 4)
        directory = runs_dir.resolve() / state.task_id
        (directory / "retrieval.json").write_text(json.dumps(initial.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        events = [json.loads(line) for line in (directory / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
        transitions = [event for event in events if event["event"] == "dynamic_retrieval"]
        (directory / "dynamic_transitions.json").write_text(json.dumps(transitions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        redundancy = analyze_redundant_reads(directory / "trajectory.jsonl", context.items)
        initial_files = list(dict.fromkeys(item.file for item in context.items))
        dynamic_files = list(dict.fromkeys(file for event in transitions for file in event["selected_files"]))
        modified = set(state.files_modified)
        initial_rank = _rank(initial_files, modified)
        dynamic_rank = _rank(dynamic_files, modified)
        new_file_hit = bool(modified & state.dynamic_new_files)
        rescue = state.status == "tests_passed" and (initial_rank is None or initial_rank > 3) and dynamic_rank is not None and dynamic_rank <= 3
        calls = Counter(state.tool_sequence)
        # Objective lower bound on an unhelpful trigger: retrieval selected nothing new.
        no_value = bool(transitions) and not any(event["new_snippets"] for event in transitions)
        row = {
            "case_id": case_id, "method": "b3_dynamic", "success": state.status == "tests_passed", "status": state.status,
            "error": state.error,
            "input_tokens": state.input_tokens if state.token_usage_complete else None,
            "output_tokens": state.output_tokens if state.token_usage_complete else None,
            "llm_calls": state.llm_calls, "tool_calls": state.tool_calls, "read_calls": calls["read_file"],
            "search_calls": calls["search_code"], "test_runs": state.test_runs, "patch_attempts": state.repair_attempts,
            "latency_sec": latency, "initial_context_tokens": context.total_tokens,
            "initial_hit1": initial_rank is not None and initial_rank <= 1 if modified else None,
            "initial_hit3": initial_rank is not None and initial_rank <= 3 if modified else None,
            "initial_hit5": initial_rank is not None and initial_rank <= 5 if modified else None,
            "initial_modified_file_rank": initial_rank, "dynamic_modified_file_rank": dynamic_rank,
            "dynamic_refreshes": state.dynamic_refresh_count, "dynamic_context_tokens": state.dynamic_context_tokens,
            "dynamic_items_added": len(state.dynamic_context_items), "dynamic_new_files": sorted(state.dynamic_new_files),
            "dynamic_new_symbols": sorted(state.dynamic_new_symbols),
            "failure_signatures": sorted(state.failure_signatures_seen),
            "failure_to_new_context_latency_sec": state.failure_to_new_context_latency_sec,
            "dynamic_modified_file_hit": new_file_hit, "dynamic_rescue": rescue,
            "triggered_without_novel_context": no_value,
            "same_file_rereads": redundancy["same_file_reread_calls"], "overlap_rereads": redundancy["overlapping_reread_calls"],
            "initial_files": initial_files, "dynamic_files": dynamic_files,
            "files_modified": sorted(modified), "task_id": state.task_id,
            "baseline_commit": baseline_commit, "baseline_tree": baseline_tree,
        }
        summary_path = directory / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.update({"retrieval_mode": "dynamic", "initial_retrieval_mode": "symbol", **redundancy, **row})
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "case_id": case_id, "method": "b3_dynamic", "issue": issue, "workspace": str(workspace),
            "baseline_commit": baseline_commit, "baseline_tree": baseline_tree,
            "config": config, "model": _model_record(backend.config),
            "retrieval_token_estimation": "ceil(UTF-8 bytes / 4)",
            "started_utc": state.started_at, "finished_utc": state.finished_at,
        }
        (directory / "experiment.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return row
    finally:
        if not _disposable(workspace, runs_dir):
            raise ValueError("refusing to restore a non-disposable workspace")
        _git(workspace, "reset", "--hard", baseline_commit)
        if not _clean(workspace):
            raise RuntimeError("B3 workspace did not restore cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", choices=sorted(CASES), default=list(CASES))
    parser.add_argument("--day4-results", type=Path, default=ROOT / "reports/day4_results.csv")
    parser.add_argument("--day4-runs", type=Path, default=ROOT / "runs/day4")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs/day5")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/day5_results.csv")
    parser.add_argument("--resume", action="store_true", help="Keep successful B3 rows and retry unfinished cases")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    backend_config = BackendConfig.from_env()
    config = json.loads((ROOT / "experiments/b3_dynamic.yaml").read_text(encoding="utf-8"))
    frozen = frozen_inputs(config, args.day4_results, args.day4_runs, _model_record(backend_config), args.cases)
    rows = _previous_rows(args.runs_dir, args.cases) if args.resume else []
    if rows:
        _write_csv(rows, args.output)
    for case in args.cases:
        if any(row["case_id"] == case and row["status"] == "tests_passed" for row in rows):
            continue
        row = run_b3(case, config, RateLimitRetryBackend(OpenAICompatibleBackend(backend_config)), frozen[case]["baseline"], args.runs_dir)
        if row["baseline_commit"] != frozen[case]["row"]["baseline_commit"]:
            raise RuntimeError("B3 baseline commit differs from frozen B2")
        rows = [old for old in rows if old["case_id"] != case] + [row]
        rows.sort(key=lambda item: args.cases.index(item["case_id"]))
        _write_csv(rows, args.output)
        print(json.dumps({key: row[key] for key in ("case_id", "status", "input_tokens", "tool_calls", "dynamic_refreshes", "dynamic_rescue", "task_id")}, ensure_ascii=False), flush=True)
        if row["error"] and "HTTP 429" in row["error"]:
            raise RuntimeError("B3 provider rate limit persisted after bounded backoff; resume later")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
