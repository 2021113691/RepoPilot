"""Run paired B0/B1 repairs from identical disposable Git baselines.

The .yaml configs use JSON syntax, which is a YAML 1.2 subset and needs no
runtime YAML dependency.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Callable
from uuid import uuid4
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repopilot.__main__ import load_dotenv
from repopilot.agent.loop import AgentLoop
from repopilot.context import StaticRetriever, format_context
from repopilot.evaluation.toy_cases import CASES, ROOT, prepare_case
from repopilot.models.base import ModelBackend
from repopilot.models.openai_compatible import BackendConfig, OpenAICompatibleBackend

FIELDNAMES = [
    "case_id", "method", "success", "status", "input_tokens", "output_tokens", "llm_calls", "tool_calls",
    "list_calls", "search_calls", "read_calls", "apply_patch_calls", "run_tests_calls", "unique_files_read",
    "files_read", "files_modified", "changed_loc", "patch_attempts", "test_runs", "test_failures",
    "latency_sec", "initial_context_tokens", "retrieved_files", "retrieved_snippets", "candidate_count",
    "retrieval_latency_sec",
    "retrieval_hit1", "retrieval_hit3", "retrieval_hit5", "task_id", "baseline_commit", "baseline_tree",
]


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["name"] not in {"b0_naive", "b1_static"}:
        raise ValueError("unknown experiment method")
    if set(data) != {"name", "retrieval", "agent"}:
        raise ValueError("experiment config must contain name, retrieval, agent")
    if set(data["agent"]) != {"max_steps", "max_tool_calls", "max_patch_attempts", "max_test_runs"}:
        raise ValueError("invalid agent budgets")
    if any(not isinstance(value, int) or value < 1 for value in data["agent"].values()):
        raise ValueError("agent budgets must be positive integers")
    if data["name"] == "b0_naive" and data["retrieval"] != {"enabled": False}:
        raise ValueError("B0 must have no retrieval")
    if data["name"] == "b1_static" and (data["retrieval"].get("mode") != "lexical" or data["retrieval"].get("enabled") is not True or not isinstance(data["retrieval"].get("context_budget_tokens"), int) or data["retrieval"]["context_budget_tokens"] < 1):
        raise ValueError("B1 requires a positive lexical context budget")
    return data


def _git(workspace: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15).stdout.strip()


def _clean(workspace: Path) -> bool:
    return not _git(workspace, "status", "--porcelain", "--untracked-files=all")


def _disposable(workspace: Path, runs_dir: Path) -> bool:
    return workspace.resolve().is_relative_to((runs_dir.resolve() / "toy_cases")) and workspace.resolve() != (runs_dir.resolve() / "toy_cases")


def run_one(case_id: str, config: dict, backend: ModelBackend, workspace: Path, runs_dir: Path, model_record: dict) -> dict:
    if not _disposable(workspace, runs_dir) or not _clean(workspace):
        raise ValueError("experiment workspace must be a clean disposable toy-case repository")
    baseline_commit = _git(workspace, "rev-parse", "HEAD")
    baseline_tree = _git(workspace, "rev-parse", "HEAD^{tree}")
    started = time.perf_counter()
    retrieval = None
    context = None
    retrieval_started = time.perf_counter()
    if config["retrieval"]["enabled"]:
        retrieval = StaticRetriever().retrieve(CASES[case_id].issue, workspace, config["retrieval"]["context_budget_tokens"])
        context = format_context(retrieval)
    retrieval_latency = round(time.perf_counter() - retrieval_started, 4) if retrieval is not None else 0.0
    state = AgentLoop(backend, workspace, runs_dir=runs_dir, repair=True, **config["agent"]).run(
        CASES[case_id].issue, initial_context=context,
        initial_context_tokens=retrieval.total_tokens if retrieval is not None else 0,
    )
    total_latency = round(time.perf_counter() - started, 4)
    directory = runs_dir.resolve() / state.task_id
    if retrieval is not None:
        (directory / "retrieval.json").write_text(json.dumps(retrieval.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    retrieved_files = list(dict.fromkeys(item.file for item in retrieval.items)) if retrieval is not None else []
    modified = set(state.files_modified)
    calls = Counter(state.tool_sequence)
    def hit(k: int) -> bool | None:
        return bool(modified.intersection(retrieved_files[:k])) if retrieval is not None and modified else None
    row = {
        "case_id": case_id, "method": config["name"], "success": state.status == "tests_passed",
        "status": state.status, "input_tokens": state.input_tokens if state.token_usage_complete else None,
        "output_tokens": state.output_tokens if state.token_usage_complete else None,
        "llm_calls": state.llm_calls, "tool_calls": state.tool_calls,
        "list_calls": calls["list_files"], "search_calls": calls["search_code"], "read_calls": calls["read_file"],
        "apply_patch_calls": calls["apply_patch"], "run_tests_calls": calls["run_tests"],
        "unique_files_read": len(state.files_seen), "files_read": sorted(state.files_seen),
        "files_modified": sorted(modified), "changed_loc": state.changed_loc,
        "patch_attempts": state.repair_attempts, "test_runs": state.test_runs, "test_failures": state.test_failures,
        "latency_sec": total_latency, "retrieval_latency_sec": retrieval_latency,
        "initial_context_tokens": state.initial_context_tokens,
        "retrieved_files": retrieved_files, "retrieved_snippets": len(retrieval.items) if retrieval else 0,
        "candidate_count": retrieval.candidate_count if retrieval else 0,
        "retrieval_hit1": hit(1), "retrieval_hit3": hit(3), "retrieval_hit5": hit(5),
        "task_id": state.task_id, "baseline_commit": baseline_commit, "baseline_tree": baseline_tree,
    }
    manifest = {
        "case_id": case_id, "method": config["name"], "issue": CASES[case_id].issue,
        "workspace": str(workspace), "baseline_commit": baseline_commit, "baseline_tree": baseline_tree,
        "config": config, "model": model_record, "retrieval_token_estimation": "ceil(UTF-8 bytes / 4)",
        "started_utc": state.started_at, "finished_utc": state.finished_at,
    }
    (directory / "experiment.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not _disposable(workspace, runs_dir):
        raise ValueError("refusing to restore a non-disposable workspace")
    _git(workspace, "reset", "--hard", baseline_commit)
    if not _clean(workspace):
        raise RuntimeError("experiment workspace did not restore cleanly")
    return row


def run_pair(case_id: str, b0: dict, b1: dict, backend_factory: Callable[[], ModelBackend], runs_dir: Path, model_record: dict) -> list[dict]:
    if b0["agent"] != b1["agent"] or b0["name"] != "b0_naive" or b1["name"] != "b1_static":
        raise ValueError("paired experiments require identical agent budgets and B0/B1 configs")
    _, first = prepare_case(case_id, runs_dir)
    second = runs_dir.resolve() / "toy_cases" / f"{case_id}-b1-{uuid4().hex[:12]}"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(first), str(second)], check=True, capture_output=True, timeout=20)
    if _git(first, "rev-parse", "HEAD") != _git(second, "rev-parse", "HEAD"):
        raise RuntimeError("paired case baselines differ")
    return [
        run_one(case_id, b0, backend_factory(), first, runs_dir, model_record),
        run_one(case_id, b1, backend_factory(), second, runs_dir, model_record),
    ]


def write_csv(rows: list[dict], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})


def _avg(rows: list[dict], key: str) -> str:
    values = [row[key] for row in rows if row[key] is not None]
    return f"{mean(values):.2f}" if values else "N/A"


def report_tables(rows: list[dict]) -> str:
    b0 = [row for row in rows if row["method"] == "b0_naive"]
    b1 = [row for row in rows if row["method"] == "b1_static"]
    lines = ["| Metric | B0 Naive | B1 Static |", "|---|---:|---:|",
             f"| Success | {sum(row['success'] for row in b0)}/{len(b0)} | {sum(row['success'] for row in b1)}/{len(b1)} |"]
    for label, key in (("Avg Input Tokens", "input_tokens"), ("Avg Output Tokens", "output_tokens"),
                       ("Avg Tool Calls", "tool_calls"), ("Avg Search Calls", "search_calls"),
                       ("Avg Read Calls", "read_calls"), ("Avg Unique Files Read", "unique_files_read"),
                       ("Avg Test Runs", "test_runs"), ("Avg Latency (s)", "latency_sec")):
        lines.append(f"| {label} | {_avg(b0, key)} | {_avg(b1, key)} |")
    hit_values = [row["retrieval_hit3"] for row in b1 if row["retrieval_hit3"] is not None]
    lines.append(f"| Retrieval Hit@3 | N/A | {sum(hit_values)}/{len(hit_values)} |" if hit_values else "| Retrieval Hit@3 | N/A | N/A |")
    lines += ["", "| Case | Δ Input Tokens | Δ Tool Calls | Δ Unique Files Read | Δ Latency (s) |", "|---|---:|---:|---:|---:|"]
    for left in b0:
        right = next((row for row in b1 if row["case_id"] == left["case_id"]), None)
        if right is None:
            continue
        delta_tokens = right["input_tokens"] - left["input_tokens"] if right["input_tokens"] is not None and left["input_tokens"] is not None else "N/A"
        lines.append(f"| {left['case_id']} | {delta_tokens} | {right['tool_calls'] - left['tool_calls']} | {right['unique_files_read'] - left['unique_files_read']} | {right['latency_sec'] - left['latency_sec']:.2f} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, help="Run one method from its .yaml config")
    parser.add_argument("--paired", action="store_true", help="Run B0 and B1 on each case from the same commit")
    parser.add_argument("--cases", nargs="+", choices=sorted(CASES), default=sorted(CASES))
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs" / "day3")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "day3_results.csv")
    args = parser.parse_args()
    if args.paired == bool(args.config):
        parser.error("choose exactly one of --paired or --config")
    load_dotenv(ROOT / ".env")
    model = BackendConfig.from_env()
    model_record = {"provider_url": model.base_url, "model": model.model, "temperature": model.temperature, "max_output_tokens": model.max_tokens, "timeout_sec": model.timeout}
    b0 = load_config(ROOT / "experiments" / "b0_naive.yaml")
    b1 = load_config(ROOT / "experiments" / "b1_static.yaml")
    if b0["agent"] != b1["agent"]:
        raise ValueError("B0/B1 budgets differ")
    factory = lambda: OpenAICompatibleBackend(model)
    rows = []
    for case_id in args.cases:
        if args.paired:
            result = run_pair(case_id, b0, b1, factory, args.runs_dir, model_record)
        else:
            case, workspace = prepare_case(case_id, args.runs_dir)
            config = b0 if args.config.resolve() == (ROOT / "experiments" / "b0_naive.yaml").resolve() else load_config(args.config)
            result = [run_one(case_id, config, factory(), workspace, args.runs_dir, model_record)]
        rows.extend(result)
        write_csv(rows, args.output)
        for row in result:
            print(json.dumps({key: row[key] for key in ("case_id", "method", "status", "input_tokens", "tool_calls", "task_id")}, ensure_ascii=False), flush=True)
    print(report_tables(rows), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
