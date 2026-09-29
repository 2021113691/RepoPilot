"""Controlled B2/B3 runs on the fixed Day 5.5 diagnostic challenge set."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repopilot.__main__ import load_dotenv
from repopilot.agent.dynamic_hook import DynamicContextHook
from repopilot.agent.loop import AgentLoop
from repopilot.context import SymbolRetriever, format_context
from repopilot.evaluation.challenge import prepare_baseline, qualify_all
from repopilot.evaluation.challenge_cases import CASES, ROOT, ChallengeCase
from repopilot.evaluation.challenge_metrics import initial_context_equal, score_run
from repopilot.models.openai_compatible import BackendConfig, OpenAICompatibleBackend
from scripts.run_day4 import _model_record, _write_csv
from scripts.run_day5 import RateLimitRetryBackend
from scripts.run_experiment import _clean, _disposable, _git

FROZEN_METHOD_COMMIT = "28f7340"
FROZEN_METHOD_PATHS = (
    "repopilot/context/lexical.py", "repopilot/context/symbol_retrieval.py",
    "repopilot/context/dynamic.py", "repopilot/context/failure.py",
    "repopilot/agent/dynamic_hook.py", "repopilot/agent/loop.py",
    "repopilot/agent/prompts.py", "repopilot/tools",
    "experiments/b2_symbol.yaml", "experiments/b3_dynamic.yaml",
)


class TransportRetryBackend:
    """Experiment-only retry for identical requests lost to transport timeouts."""

    def __init__(self, backend: OpenAICompatibleBackend):
        self.base = RateLimitRetryBackend(backend)
        self.config = backend.config

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        for delay in (0, 5, 20):
            if delay:
                time.sleep(delay)
            try:
                return self.base.chat(messages, tools)
            except (TimeoutError, RuntimeError) as exc:
                text = str(exc)
                if not isinstance(exc, TimeoutError) and "connection failed" not in text:
                    raise
                if delay == 20:
                    raise
        raise AssertionError("unreachable")


def frozen_settings(config_b2: dict, config_b3: dict, model: BackendConfig) -> None:
    if config_b2["agent"] != config_b3["agent"]:
        raise ValueError("B2/B3 Agent budgets differ")
    if config_b2["retrieval"]["mode"] != "symbol" or config_b3["retrieval"]["initial"] != {"mode": "symbol", "context_budget_tokens": config_b2["retrieval"]["context_budget_tokens"]}:
        raise ValueError("B2/B3 initial retrieval differs")
    if config_b3["retrieval"]["dynamic"] != {"enabled": True, "refresh_budget_tokens": 4000, "max_refreshes": 2}:
        raise ValueError("B3 refresh settings changed")
    if model.model != "Qwen/Qwen3.8-Flash-Next" or model.temperature != 0 or model.max_tokens != 1024 or urlsplit(model.base_url).hostname != "api-inference.modelscope.cn":
        raise ValueError("challenge must use the frozen ModelScope model settings")
    diff = subprocess.run(["git", "diff", "--quiet", FROZEN_METHOD_COMMIT, "--", *FROZEN_METHOD_PATHS], cwd=ROOT, capture_output=True, timeout=20)
    if diff.returncode != 0:
        raise ValueError("frozen B2/B3 implementation differs from Day 5 commit")


def _existing_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _paired_b2_row(rows: list[dict], case_id: str, runs_dir: Path, baseline_commit: str, model_record: dict, config_b2: dict) -> dict:
    matching = [row for row in rows if row["case_id"] == case_id and row["method"] == "b2_symbol" and row["failure_category"] not in {"provider_rate_limited", "provider_failure"}]
    if len(matching) != 1:
        raise ValueError(f"B3 requires one evaluable B2 run for {case_id}")
    row = matching[0]
    manifest = json.loads((runs_dir / row["task_id"] / "experiment.json").read_text(encoding="utf-8"))
    if manifest["config"] != config_b2 or manifest["model"] != model_record or manifest["baseline_commit"] != baseline_commit:
        raise ValueError(f"B2 pair differs in config, model, or baseline: {case_id}")
    return row


def execute_method(
    case: ChallengeCase, method: str, config: dict, backend,
    baseline: Path, baseline_commit: str, runs_dir: Path,
    expected_initial: dict | None = None,
) -> Path:
    """Run the Agent with public inputs only; this function has no gold labels."""
    if method not in {"b2_symbol", "b3_dynamic"}:
        raise ValueError("unknown challenge method")
    workspace = runs_dir.resolve() / "toy_cases" / f"{case.case_id}-{method}-{uuid4().hex[:12]}"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(baseline), str(workspace)], check=True, capture_output=True, timeout=20)
    if not _disposable(workspace, runs_dir) or not _clean(workspace) or _git(workspace, "rev-parse", "HEAD") != baseline_commit:
        raise ValueError("challenge workspace is not a clean clone of its baseline")
    try:
        started = time.perf_counter()
        budget = config["retrieval"]["context_budget_tokens"] if method == "b2_symbol" else config["retrieval"]["initial"]["context_budget_tokens"]
        retrieval = SymbolRetriever().retrieve(case.issue, workspace, budget)
        if expected_initial is not None and not initial_context_equal(expected_initial, retrieval.as_dict()):
            raise ValueError(f"B2/B3 initial retrieval mismatch: {case.case_id}")
        hook = None
        if method == "b3_dynamic":
            dynamic = config["retrieval"]["dynamic"]
            hook = DynamicContextHook(case.issue, workspace, retrieval.context, dynamic["refresh_budget_tokens"], dynamic["max_refreshes"])
        state = AgentLoop(backend, workspace, runs_dir=runs_dir, repair=True, event_hook=hook, **config["agent"]).run(
            case.issue, initial_context=format_context(retrieval.context), initial_context_tokens=retrieval.context.total_tokens,
        )
        directory = runs_dir.resolve() / state.task_id
        (directory / "retrieval.json").write_text(json.dumps(retrieval.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary_path = directory / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary.update({
            "case_id": case.case_id, "method": method, "baseline_commit": baseline_commit,
            "baseline_tree": _git(workspace, "rev-parse", "HEAD^{tree}"),
            "latency_sec": round(time.perf_counter() - started, 4),
            "initial_retrieval_mode": "symbol",
        })
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "case_id": case.case_id, "method": method, "issue": case.issue,
            "workspace": str(workspace), "baseline_commit": baseline_commit,
            "config": config, "model": _model_record(backend.config),
            "started_utc": state.started_at, "finished_utc": state.finished_at,
        }
        (directory / "experiment.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return directory
    finally:
        if not _disposable(workspace, runs_dir):
            raise ValueError("refusing to restore a non-disposable challenge workspace")
        _git(workspace, "reset", "--hard", baseline_commit)
        if not _clean(workspace):
            raise RuntimeError("challenge workspace did not restore cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=("b2", "b3"), required=True)
    parser.add_argument("--case", nargs="+", choices=sorted(CASES), default=list(CASES))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--runs-dir", type=Path, default=ROOT / "runs/day5_challenge_experiment")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/day5_challenge_results.csv")
    parser.add_argument("--qualification-output", type=Path, default=ROOT / "reports/day5_challenge_qualification.csv")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    model_config = BackendConfig.from_env()
    b2 = json.loads((ROOT / "experiments/b2_symbol.yaml").read_text(encoding="utf-8"))
    b3 = json.loads((ROOT / "experiments/b3_dynamic.yaml").read_text(encoding="utf-8"))
    frozen_settings(b2, b3, model_config)
    qualifications = qualify_all(args.runs_dir, list(CASES))
    _write_csv([item.as_dict() for item in qualifications], args.qualification_output)
    if any(not item.qualified for item in qualifications):
        raise ValueError("one or more challenge cases failed offline qualification")
    rows = _existing_rows(args.output) if args.resume else []
    if args.output.exists() and not args.resume:
        raise ValueError("results exist; use --resume to preserve paired runs")
    method = "b2_symbol" if args.method == "b2" else "b3_dynamic"
    config = b2 if args.method == "b2" else b3
    for case_id in args.case:
        existing = [row for row in rows if row["case_id"] == case_id and row["method"] == method]
        if len(existing) > 1:
            raise ValueError(f"duplicate experiment rows: {case_id} {method}")
        if existing and existing[0]["failure_category"] not in {"provider_rate_limited", "provider_failure"}:
            continue
        case = CASES[case_id]
        baseline, commit = prepare_baseline(case, args.runs_dir)
        expected = None
        if method == "b3_dynamic":
            b2_row = _paired_b2_row(rows, case_id, args.runs_dir, commit, _model_record(model_config), b2)
            expected = json.loads((args.runs_dir / b2_row["task_id"] / "retrieval.json").read_text(encoding="utf-8"))
        directory = execute_method(case, method, config, TransportRetryBackend(OpenAICompatibleBackend(model_config)), baseline, commit, args.runs_dir, expected)
        # Evaluation labels are loaded only after AgentLoop and trajectory writes finish.
        from repopilot.evaluation.challenge import load_gold
        row = score_run(directory, case.issue, load_gold()[case_id])
        rows = [old for old in rows if not (old["case_id"] == case_id and old["method"] == method)] + [row]
        rows.sort(key=lambda item: (list(CASES).index(item["case_id"]), item["method"]))
        _write_csv(rows, args.output)
        print(json.dumps({key: row[key] for key in ("case_id", "method", "status", "dynamic_refreshes", "dynamic_bug_file_rank", "dynamic_context_used", "dynamic_rescue", "task_id")}, ensure_ascii=False), flush=True)
        if row["failure_category"] in {"provider_rate_limited", "provider_failure"}:
            raise RuntimeError("provider request failed; checkpoint saved for --resume")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
