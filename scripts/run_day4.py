"""Run B2 from frozen Day 3 baselines and compare all three methods."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import mean, median
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repopilot.__main__ import load_dotenv
from repopilot.agent.loop import AgentLoop
from repopilot.context import ContextItem, SymbolRetriever, file_role, format_context
from repopilot.evaluation.redundancy import analyze_redundant_reads
from repopilot.evaluation.toy_cases import CASES, ROOT
from repopilot.models.openai_compatible import BackendConfig, OpenAICompatibleBackend
from scripts.run_experiment import _clean, _disposable, _git, load_config

EXTRA_FIELDS = [
    "same_file_reread_calls", "overlapping_reread_calls", "same_file_reread_ratio",
    "overlapping_reread_ratio", "modified_file_rank", "top1_role", "top3_roles",
    "query_identifier_count", "matched_symbol_identifiers", "exact_definition_matches",
    "parse_error_count", "retrieval_latency_sec",
]


def _day3_rows(path: Path, runs_dir: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for key in ("files_read", "files_modified", "retrieved_files"):
            row[key] = json.loads(row[key])
        for key in ("success", "retrieval_hit1", "retrieval_hit3", "retrieval_hit5"):
            row[key] = None if row[key] == "" else row[key] == "True"
        for key in ("input_tokens", "output_tokens", "tool_calls", "read_calls", "search_calls", "test_runs", "patch_attempts", "unique_files_read"):
            row[key] = int(row[key]) if row[key] else None
        row["latency_sec"] = float(row["latency_sec"])
        row["initial_context_tokens"] = int(row["initial_context_tokens"])
        row["modified_file_rank"] = None
        row["top1_role"] = None
        row["top3_roles"] = None
        row["query_identifier_count"] = None
        row["matched_symbol_identifiers"] = None
        row["exact_definition_matches"] = None
        row["parse_error_count"] = None
        if row["method"] == "b0_naive":
            for key in EXTRA_FIELDS[:4]:
                row[key] = None
        else:
            retrieval = json.loads((runs_dir / row["task_id"] / "retrieval.json").read_text(encoding="utf-8"))
            items = [ContextItem(**item) for item in retrieval["items"]]
            row.update(analyze_redundant_reads(runs_dir / row["task_id"] / "trajectory.jsonl", items))
            files = row["retrieved_files"]
            modified = set(row["files_modified"])
            row["modified_file_rank"] = next((i for i, file in enumerate(files, 1) if file in modified), None)
            row["top1_role"] = file_role(files[0]) if files else None
            row["top3_roles"] = [file_role(file) for file in files[:3]]
    return rows


def _model_record(config: BackendConfig) -> dict:
    return {
        "provider_url": config.base_url, "model": config.model,
        "temperature": config.temperature, "max_output_tokens": config.max_tokens,
        "timeout_sec": config.timeout,
    }


def _verify_frozen(b2: dict, model_record: dict, b1_row: dict, day3_dir: Path) -> Path:
    b0 = load_config(ROOT / "experiments/b0_naive.yaml")
    b1 = load_config(ROOT / "experiments/b1_static.yaml")
    if b0["agent"] != b1["agent"] or b1["agent"] != b2["agent"]:
        raise ValueError("frozen B0/B1/B2 agent budgets differ")
    if b1["retrieval"]["context_budget_tokens"] != b2["retrieval"]["context_budget_tokens"]:
        raise ValueError("B1/B2 context budgets differ")
    manifest = json.loads((day3_dir / b1_row["task_id"] / "experiment.json").read_text(encoding="utf-8"))
    if manifest["config"] != b1 or manifest["model"] != model_record:
        raise ValueError("model or B1 config differs from frozen Day 3 run")
    baseline = Path(manifest["workspace"]).resolve(strict=True)
    if _git(baseline, "rev-parse", "HEAD") != b1_row["baseline_commit"] or not _clean(baseline):
        raise ValueError("frozen Day 3 baseline is unavailable or dirty")
    return baseline


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})


def run_b2(case_id: str, config: dict, backend: OpenAICompatibleBackend, baseline: Path, runs_dir: Path) -> dict:
    workspace = runs_dir.resolve() / "toy_cases" / f"{case_id}-b2-{uuid4().hex[:12]}"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(baseline), str(workspace)], check=True, capture_output=True, timeout=20)
    if not _disposable(workspace, runs_dir) or not _clean(workspace) or _git(workspace, "rev-parse", "HEAD") != _git(baseline, "rev-parse", "HEAD"):
        raise ValueError("B2 workspace is not a clean clone of the frozen baseline")
    baseline_commit = _git(workspace, "rev-parse", "HEAD")
    baseline_tree = _git(workspace, "rev-parse", "HEAD^{tree}")
    try:
        started = time.perf_counter()
        result = SymbolRetriever().retrieve(CASES[case_id].issue, workspace, config["retrieval"]["context_budget_tokens"])
        retrieval_latency = round(time.perf_counter() - started, 4)
        context = result.context
        state = AgentLoop(backend, workspace, runs_dir=runs_dir, repair=True, **config["agent"]).run(
            CASES[case_id].issue, initial_context=format_context(context), initial_context_tokens=context.total_tokens,
        )
        latency = round(time.perf_counter() - started, 4)
        directory = runs_dir.resolve() / state.task_id
        (directory / "retrieval.json").write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        redundancy = analyze_redundant_reads(directory / "trajectory.jsonl", context.items)
        summary_path = directory / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.update({"retrieval_mode": "symbol", **redundancy})
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with (directory / "trajectory.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": "symbol_retrieval_metrics", "mode": "symbol", **redundancy}, ensure_ascii=False) + "\n")
        manifest = {
            "case_id": case_id, "method": "b2_symbol", "issue": CASES[case_id].issue,
            "workspace": str(workspace), "baseline_commit": baseline_commit, "baseline_tree": baseline_tree,
            "config": config, "model": _model_record(backend.config),
            "retrieval_token_estimation": "ceil(UTF-8 bytes / 4)",
            "started_utc": state.started_at, "finished_utc": state.finished_at,
        }
        (directory / "experiment.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files = list(dict.fromkeys(item.file for item in context.items))
        modified = set(state.files_modified)
        calls = Counter(state.tool_sequence)
        rank = next((i for i, file in enumerate(files, 1) if file in modified), None)
        row = {
            "case_id": case_id, "method": "b2_symbol", "success": state.status == "tests_passed",
            "status": state.status, "input_tokens": state.input_tokens if state.token_usage_complete else None,
            "output_tokens": state.output_tokens if state.token_usage_complete else None,
            "llm_calls": state.llm_calls, "tool_calls": state.tool_calls,
            "list_calls": calls["list_files"], "search_calls": calls["search_code"], "read_calls": calls["read_file"],
            "apply_patch_calls": calls["apply_patch"], "run_tests_calls": calls["run_tests"],
            "unique_files_read": len(state.files_seen), "files_read": sorted(state.files_seen),
            "files_modified": sorted(modified), "changed_loc": state.changed_loc,
            "patch_attempts": state.repair_attempts, "test_runs": state.test_runs, "test_failures": state.test_failures,
            "latency_sec": latency, "initial_context_tokens": context.total_tokens,
            "retrieved_files": files, "retrieved_snippets": len(context.items), "candidate_count": context.candidate_count,
            "retrieval_latency_sec": retrieval_latency,
            "retrieval_hit1": rank is not None and rank <= 1 if modified else None,
            "retrieval_hit3": rank is not None and rank <= 3 if modified else None,
            "retrieval_hit5": rank is not None and rank <= 5 if modified else None,
            "task_id": state.task_id, "baseline_commit": baseline_commit, "baseline_tree": baseline_tree,
            **redundancy, "modified_file_rank": rank,
            "top1_role": file_role(files[0]) if files else None,
            "top3_roles": [file_role(file) for file in files[:3]],
            "query_identifier_count": result.query_identifier_count,
            "matched_symbol_identifiers": list(result.matched_symbol_identifiers),
            "exact_definition_matches": result.exact_definition_matches,
            "parse_error_count": len(result.parse_errors),
        }
        return row
    finally:
        if not _disposable(workspace, runs_dir):
            raise ValueError("refusing to restore a non-disposable workspace")
        _git(workspace, "reset", "--hard", baseline_commit)
        if not _clean(workspace):
            raise RuntimeError("B2 workspace did not restore cleanly")


def comparison(rows: list[dict]) -> str:
    methods = ("b0_naive", "b1_static", "b2_symbol")
    groups = {method: [row for row in rows if row["method"] == method] for method in methods}
    lines = ["| Metric | B0 Naive | B1 Lexical | B2 Symbol |", "|---|---:|---:|---:|"]
    lines.append("| Success | " + " | ".join(f"{sum(row['success'] for row in groups[method])}/{len(groups[method])}" for method in methods) + " |")
    for label, key in (("Input Tokens", "input_tokens"), ("Output Tokens", "output_tokens"),
                       ("Tool Calls", "tool_calls"), ("Search Calls", "search_calls"),
                       ("Read Calls", "read_calls"), ("Unique Files Read", "unique_files_read"),
                       ("Test Runs", "test_runs"), ("Patch Attempts", "patch_attempts"), ("Latency (s)", "latency_sec")):
        cells = []
        for method in methods:
            values = [row[key] for row in groups[method] if row.get(key) is not None]
            cells.append(f"{mean(values):.2f} / {median(values):.2f}" if values else "N/A")
        lines.append(f"| {label} (mean / median) | " + " | ".join(cells) + " |")
    for label, key in (("Hit@1", "retrieval_hit1"), ("Hit@3", "retrieval_hit3"), ("Hit@5", "retrieval_hit5")):
        cells = ["N/A"]
        for method in methods[1:]:
            values = [row[key] for row in groups[method] if row.get(key) is not None]
            cells.append(f"{sum(values)}/{len(values)}" if values else "N/A")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    for label, key in (("Same-file re-read calls", "same_file_reread_calls"), ("Overlap re-read calls", "overlapping_reread_calls")):
        cells = ["N/A"]
        for method in methods[1:]:
            values = [row[key] for row in groups[method] if row.get(key) is not None]
            cells.append(f"{sum(values)}/{sum(row['read_calls'] for row in groups[method])}" if values else "N/A")
        lines.append(f"| {label} / reads | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", choices=sorted(CASES), default=sorted(CASES))
    parser.add_argument("--day3-results", type=Path, default=ROOT / "reports/day3_results.csv")
    parser.add_argument("--day3-runs", type=Path, default=ROOT / "runs/day3")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs/day4")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/day4_results.csv")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    backend_config = BackendConfig.from_env()
    model_record = _model_record(backend_config)
    b2 = json.loads((ROOT / "experiments/b2_symbol.yaml").read_text(encoding="utf-8"))
    if b2["name"] != "b2_symbol" or b2["retrieval"].get("mode") != "symbol":
        raise ValueError("invalid B2 config")
    prior = _day3_rows(args.day3_results, args.day3_runs)
    selected = [row for row in prior if row["case_id"] in args.cases]
    if any(len([row for row in selected if row["case_id"] == case]) != 2 for case in args.cases):
        raise ValueError("each selected case needs frozen Day 3 B0 and B1 results")
    b2_rows = []
    for case in args.cases:
        b1_row = next(row for row in selected if row["case_id"] == case and row["method"] == "b1_static")
        baseline = _verify_frozen(b2, model_record, b1_row, args.day3_runs)
        row = run_b2(case, b2, OpenAICompatibleBackend(backend_config), baseline, args.runs_dir)
        if row["baseline_commit"] != b1_row["baseline_commit"]:
            raise RuntimeError("B2 baseline commit differs from Day 3")
        b2_rows.append(row)
        _write_csv(selected + b2_rows, args.output)
        print(json.dumps({key: row[key] for key in ("case_id", "status", "input_tokens", "tool_calls", "modified_file_rank", "top1_role", "task_id")}, ensure_ascii=False), flush=True)
    print(comparison(selected + b2_rows), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
