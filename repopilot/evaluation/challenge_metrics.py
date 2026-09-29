"""Post-run trajectory scoring for the diagnostic B2/B3 challenge set."""

from __future__ import annotations

import json
from pathlib import Path

from repopilot.context.failure import FailureEvidence, TracebackFrame
from repopilot.context.lexical import ContextItem, RetrievedContext
from repopilot.evaluation.challenge import evidence_novelty, rank, ranked_files


def initial_context_equal(b2: dict, b3: dict) -> bool:
    """Compare selected snippets, scores, evidence, order, and retrieval metadata."""
    return b2 == b3


def rank_improvement(initial_rank: int | None, dynamic_rank: int | None) -> int | None:
    return initial_rank - dynamic_rank if initial_rank is not None and dynamic_rank is not None else None


def _evidence(value: dict) -> FailureEvidence:
    return FailureEvidence(
        tuple(value.get("failed_tests", [])), tuple(value.get("exception_types", [])),
        tuple(TracebackFrame(**frame) for frame in value.get("traceback_frames", [])),
        tuple(value.get("assertion_messages", [])), tuple(value.get("mentioned_files", [])),
        tuple(value.get("mentioned_symbols", [])), tuple(value.get("line_numbers", [])),
        value.get("raw_summary", ""),
    )


def _tool_files(event: dict) -> set[str]:
    if event.get("event") != "tool_call" or not event.get("success"):
        return set()
    metadata = event.get("metadata") or {}
    if event.get("tool") == "read_file":
        return {metadata["file"]} if isinstance(metadata.get("file"), str) else set()
    if event.get("tool") == "apply_patch":
        return set(metadata.get("patch_files") or [])
    return set()


def classify_failure(status: str, error: str | None, success: bool, events: list[dict], bug_file: str, initial_rank: int | None, dynamic_rank: int | None, context_used: bool) -> str:
    if success:
        return ""
    if error and "HTTP 429" in error:
        return "provider_rate_limited"
    if status == "tool_error":
        return "tool_failure"
    full_pass = any(event.get("event") == "test_run" and event.get("success") and event.get("scope") is None for event in events)
    if error and "maximum test runs reached" in error and full_pass:
        return "verification_budget_failure"
    if dynamic_rank is not None and not context_used:
        return "context_utilization_failure"
    if (initial_rank is None or initial_rank > 3) and dynamic_rank is None and not any(bug_file in _tool_files(event) for event in events):
        return "retrieval_failure"
    return "repair_reasoning_failure"


def score_run(directory: Path, issue: str, gold: dict) -> dict:
    """Call only after the Agent finished; gold is evaluation-only."""
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    retrieval = json.loads((directory / "retrieval.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (directory / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
    items = [ContextItem(**value) for value in retrieval["items"]]
    initial = RetrievedContext(items, retrieval["total_tokens"], retrieval["candidate_count"], retrieval["budget_tokens"])
    bug_file = gold["expected_bug_file"]
    initial_files = ranked_files(initial)
    initial_rank = rank(initial_files, bug_file)
    transitions = [event for event in events if event.get("event") == "dynamic_retrieval"]
    injections = [(index, event) for index, event in enumerate(events) if event.get("event") == "dynamic_context_injected"]
    dynamic_rank = next((position for event in transitions if (position := rank(list(dict.fromkeys(event.get("selected_files", []))), bug_file)) is not None), None)
    novelty = {"novel_file_clues": [], "novel_symbol_clues": [], "novel_assertion_terms": []}
    for event in events:
        if event.get("event") != "test_failure_evidence":
            continue
        current = evidence_novelty(issue, initial, _evidence(event["evidence"]))
        for key, values in current.items():
            novelty[key] = list(dict.fromkeys([*novelty[key], *values]))
    used_refreshes = 0
    used_files: set[str] = set()
    read_after: set[str] = set()
    patched_after: set[str] = set()
    for index, injection in injections:
        selected = set(injection.get("files", []))
        next_injection = next((other for other, _ in injections if other > index), len(events))
        observed = set()
        for event in events[index + 1:next_injection]:
            files = _tool_files(event)
            observed.update(files)
            if event.get("tool") == "read_file":
                read_after.update(files)
            if event.get("tool") == "apply_patch":
                patched_after.update(files)
        hits = selected & observed
        if hits:
            used_refreshes += 1
            used_files.update(hits)
    first_injection = injections[0][0] if injections else len(events)
    before_files = set().union(*(_tool_files(event) for event in events[:first_injection])) if first_injection else set()
    direction_changed = bool(injections and bug_file not in before_files and bug_file in read_after | patched_after)
    success = summary["status"] == "tests_passed"
    context_used = bool(used_files)
    rescue = bool(
        success and (initial_rank is None or initial_rank > 3) and injections
        and dynamic_rank is not None and dynamic_rank <= 3
        and context_used and direction_changed and bug_file in patched_after
        and any(event.get("event") == "test_failure_evidence" for event in events[:first_injection])
    )
    if not success:
        success_type = ""
    elif rescue:
        success_type = "dynamic_rescue_success"
    elif initial_rank is not None and initial_rank <= 3:
        success_type = "direct_static_success"
    else:
        success_type = "autonomous_exploration_success"
    tool_sequence = summary.get("tool_sequence", [])
    category = classify_failure(summary["status"], summary.get("error"), success, events, bug_file, initial_rank, dynamic_rank, context_used)
    return {
        "case_id": summary["case_id"], "method": summary["method"], "success": success,
        "status": summary["status"], "error": summary.get("error"), "evaluable": category != "provider_rate_limited",
        "input_tokens": summary["input_tokens"] if summary["token_usage_complete"] else None,
        "output_tokens": summary["output_tokens"] if summary["token_usage_complete"] else None,
        "llm_calls": summary["llm_calls"], "tool_calls": summary["tool_calls"],
        "search_calls": tool_sequence.count("search_code"), "read_calls": tool_sequence.count("read_file"),
        "patch_attempts": summary["repair_attempts"], "test_runs": summary["test_runs"],
        "test_failures": summary["test_failures"], "latency_sec": summary["latency_sec"],
        "initial_bug_file_rank": initial_rank, "initial_bug_file_selected": initial_rank is not None,
        "dynamic_refreshes": summary["dynamic_refresh_count"],
        "dynamic_context_tokens": summary["dynamic_context_tokens"],
        "dynamic_bug_file_rank": dynamic_rank, "rank_improvement": rank_improvement(initial_rank, dynamic_rank),
        "initial_miss_dynamic_hit": initial_rank is None and dynamic_rank is not None,
        "novel_file_clues": novelty["novel_file_clues"], "novel_symbol_clues": novelty["novel_symbol_clues"],
        "novel_assertion_terms": novelty["novel_assertion_terms"],
        "failure_evidence_novelty": bool(novelty["novel_file_clues"] or novelty["novel_symbol_clues"] or novelty["novel_assertion_terms"]),
        "dynamic_context_used": context_used, "useful_refreshes": used_refreshes,
        "dynamic_used_files": sorted(used_files), "read_after_refresh": sorted(read_after),
        "patched_after_refresh": sorted(patched_after), "direction_changed": direction_changed,
        "dynamic_rescue": rescue, "success_type": success_type,
        "final_modified_files": summary["files_modified"], "failure_category": category,
        "task_id": summary["task_id"], "baseline_commit": summary["baseline_commit"],
    }
