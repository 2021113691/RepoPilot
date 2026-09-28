# RepoPilot Day 5 Summary

## Goal

Test whether a failed `run_tests` result can change repository relevance after B2's initial, issue-based retrieval misses a dependency. B3 adds execution-feedback-driven context to the same repair Agent. The five real API runs were attempted, but the provider returned persistent HTTP 429 after the first case; the real B3 comparison is incomplete.

## Frozen B0/B1/B2

B0/B1/B2 code, prompts, retrieval weights, configurations, and prior results were left unchanged. B3 calls the existing `SymbolRetriever` with the same issue and 8,000 estimated-token budget before the Agent starts. The B3 runner checks B0/B1/B2/B3 Agent limits, B2 initial retrieval settings, model settings, and each frozen Day 4 baseline commit. Only B3 was run. Its new hook is absent from B0/B1/B2 execution.

## Failure Evidence

`FailureEvidence` stores failed test nodes, exception types, traceback frames (`file`, optional `line` and `function`), assertion messages, mentioned files and symbols, line numbers, and a short summary. It reads only the current `TestResult` and its sanitized pytest stdout/stderr. Missing details stay empty; no gold patch, expected file, hidden test, or future result enters retrieval. A `FAILED tests/...` line is a fallback when the structured failed-test list is empty.

## Failure Query

`FailureQuery` retains the original issue and merges deterministic identifiers with failed-test paths, traceback files/functions, exception terms, and assertion terms. Its retrieval text is capped at 2,000 characters. The frozen B2 candidate engine receives this query; no second independent index or learned ranker was added.

## Dynamic Ranking

Each candidate receives its B2 score plus fixed, case-independent bonuses: exact traceback file `+10`, traceback symbol `+10`, failed-test-related source `+6`, assertion identifier `+5`, exception identifier `+4`, and failure keyword `+2`. Execution evidence can therefore raise a candidate that was weak under the issue alone. Selection prioritizes unseen files, then unseen symbols, then new ranges in known files; within a novelty tier, candidates use score divided by estimated token cost with deterministic ties. This is a candidate ordering heuristic, not proof of the root cause. No file relation was hardcoded for invoice.

## Dynamic Context Refresh

The Agent receives an appended context message after the failed test tool reply and after all replies from that model step. The message gives the failed test, traceback, exception, numbered snippets, ranking reasons, and novelty reasons. It does not replace the issue or system prompt. The initial context retains B2's 8,000 estimated-token ceiling; each refresh has a separate 4,000 estimated-token ceiling, including its header and evidence. At most two refreshes are injected. There is no fixed total context budget.

## Trigger Rules

Only an unsuccessful `run_tests` tool result with structured `test_result` metadata triggers extraction and retrieval. Patch rejection, read errors, search misses, model uncertainty, and successful tests do not trigger it. A test failure can be logged even when a repeated signature or the refresh cap prevents another injection.

## Novelty / Deduplication

Previously injected and initial line ranges are tracked. A fully covered snippet is skipped; a new file, new symbol, or uncovered range may be added. The signature is a deterministic SHA-256 prefix over sorted failed tests, traceback file/function pairs, exception types, and assertion messages. A repeated signature is not retrieved twice. This is range-based novelty; source edits can shift line numbers, and the ranker may still add irrelevant new files.

## Unit Tests

The Day 5 tests cover failed-test and traceback extraction, exception and assertion data, original-issue merge, traceback file/symbol bonuses, failed-test source relation, novelty, signature stability and duplicate suppression, trigger precision, the two-refresh cap, budget, no-gold API shape, and the mock rescue. Final full `hello-agent` suite: **82/82 passed**, including the prior 70 tests.

## Mock Dynamic Rescue

The issue points to `app/service.py`, while the defect is in `internal/pricing.py`. Initial B2 Top3 is `app/service.py`, `tests/test_receipt.py`, `app/models.py`; the pricing file is absent from the entire initial context. The scripted Agent first patches service, then actual pytest fails with `RuntimeError: round_price failed` and a traceback in `internal/pricing.py`. Evidence extracts the failed test, exception, traceback file, and `round_price` identifier. Novel context Top3 becomes `internal/pricing.py`, `internal/__init__.py`, `app/service.py`; pricing is newly injected at rank 1. The Agent then reads and patches pricing, inspects the diff, and full pytest passes. The mock controls Agent tool choices, while retrieval sees only the issue, repository, and real test result. This establishes one genuine dynamic rescue in the offline fixture, with an irrelevant `internal/__init__.py` also selected.

## Real B3 Experiment

Five runs were attempted using the same ModelScope `Qwen/Qwen3.8-Flash-Next` settings and clean Day 4 case baselines. `email` completed with verified tests and no dynamic trigger. The first `discount` attempt made nine tool calls and had two passing test runs before HTTP 429 interrupted it. `regression`, `greeting`, and `invoice` received HTTP 429 on their first model call. A bounded 15-second and 45-second retry on a fresh `discount` run still returned HTTP 429. The runner now stops on persistent 429 and supports `--resume`; the [results CSV](day5_results.csv) retains one latest row per case. Earlier attempt trajectories remain under ignored `runs/day5/`.

| Case | Status | Input tokens | Tools | Reads | Tests | Latency (s) | Dynamic refreshes |
|---|---|---:|---:|---:|---:|---:|---:|
| email | verified pass | 30,712 | 15 | 6 | 4 | 41.11 | 0 |
| discount | HTTP 429 | unavailable | 0 | 0 | 0 | 62.20 | 0 |
| regression | HTTP 429 | unavailable | 0 | 0 | 0 | 1.65 | 0 |
| greeting | HTTP 429 | unavailable | 0 | 0 | 0 | 1.68 | 0 |
| invoice | HTTP 429 | unavailable | 0 | 0 | 0 | 1.41 | 0 |

The displayed `discount` row is its latest retry. The earlier partial trajectory is preserved but is not a completed comparable run.

## B2/B3 Comparison

| Metric | Frozen B2 | B3 observed |
|---|---:|---:|
| Verified success | 5/5 | 1/1 completed; 4 provider-limited |
| Email input tokens | 30,927 | 30,712 |
| Email tool calls | 14 | 15 |
| Email read calls | 3 | 6 |
| Email test runs | 5 | 4 |
| Email latency (s) | 51.05 | 41.11 |
| Email dynamic refreshes | N/A | 0 |
| Dynamic rescue | N/A | not observed in completed real runs |

Email had no dynamic refresh, so its difference reflects ordinary run variation, not a measured retrieval benefit. B3 is allowed up to 4,000 extra estimated context tokens per refresh, so input-token efficiency is not a budget-matched comparison with B2. No cross-case B3 average or causal claim is warranted.

## Invoice Case Study

Frozen B2 initial ranking was `app/invoice.py`, `app/__init__.py`, `tests/test_invoice.py`, `app/tax.py`; the B2 finally modified file, `app/tax.py`, ranked fourth. B3 reproduced that same initial ranking. The B3 invoice request then received HTTP 429 before any tool call, so there was no first test failure, `FailureEvidence`, dynamic rank, final modified file, or rescue result to measure. The missing values remain empty in the CSV. The mock demonstrates that a traceback can move a previously absent dependency to the top, but it cannot substitute for invoice's unfinished real run.

## Non-trigger Cases

Email passed with zero refreshes despite four test runs; none failed. The first discount attempt also logged two passing tests and zero refreshes before the provider error. The other three runs never reached a test. Thus no real run showed an unnecessary failure-triggered context injection, but trigger precision across all five real cases remains unmeasured.

## Context Transition Analysis

The mock transition is the only observed failure-driven rank shift: `internal/pricing.py` went from absent in initial context to rank 1 in the injected context. The mock's final patch touched service and pricing, and pricing was newly introduced dynamically, so its retrospective `dynamic_modified_file_hit` and `dynamic_rescue` are true. Real `email` had zero transitions; real invoice has no dynamic rank. The runner computes modified-file rank, `dynamic_modified_file_hit`, and `dynamic_rescue` only after an Agent run. It also saves per-refresh before/after Top3, selected scores/reasons, new files/symbols/snippets, failure signatures, context tokens, failure-to-context latency, and same-file/overlap re-reads. A retrieval with no selected novel snippet is flagged separately; none was observed in a completed real run. Reads are measured, not suppressed.

## Limitations

The five toy repositories are small, the model runs are nondeterministic despite temperature zero, and provider rate limiting prevented a real five-case B3 comparison. The append-only context can grow beyond the initial budget; the 4,000-token bound is per refresh and estimated with `ceil(UTF-8 bytes / 4)`. Traceback parsing depends on pytest output shape, and missing function names remain unknown. New-file priority can include distractors. Range-based deduplication is approximate after code edits. Modified-file hit is only one measure of value; a dependency snippet can help even if the final patch stays in the initial file.

## Day 6 Entry

The core Day 5 implementation gate is met by deterministic extraction and ranking, B2 initial retrieval reuse, failed-test-only trigger, novel context with budget and deduplication, the mock rescue, and passing tests. The **real experiment gate remains open** because four B3 runs are provider-limited. Resume the same five-case evaluation when the API permits, then assess whether fixed-total-budget replacement and structured context state are justified for Day 6.
