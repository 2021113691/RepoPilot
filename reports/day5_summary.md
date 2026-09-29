# RepoPilot Day 5 Summary

## Goal

Test whether a failed `run_tests` result can change repository relevance after B2's initial, issue-based retrieval misses a dependency. B3 adds execution-feedback-driven context to the same repair Agent. All five real B3 cases have now completed: four verified repairs and one run that exhausted its test budget.

## Frozen B0/B1/B2

B0/B1/B2 code, prompts, retrieval weights, configurations, and prior results were left unchanged. B3 calls the existing `SymbolRetriever` with the same issue and 8,000 estimated-token budget before the Agent starts. The B3 runner checks B0/B1/B2/B3 Agent limits, B2 initial retrieval settings, model settings, and each frozen Day 4 baseline commit. Only B3 was run. Its new hook is absent from B0/B1/B2 execution. An artifact audit confirmed that **all five B3 `retrieval.json` files are exactly equal to the corresponding frozen B2 initial retrieval files**, and all baseline commits match.

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

Five real runs completed using the same ModelScope `Qwen/Qwen3.8-Flash-Next` settings and clean Day 4 case baselines. The earlier HTTP 429 interruptions were infrastructure failures; `--resume` preserved the already completed `email` run and ran the four remaining cases. The [results CSV](day5_results.csv) contains one completed run per case. Earlier interrupted trajectories remain under ignored `runs/day5/`; no completed run was repeated to select a favorable outcome.

| Case | Status | Input tokens | Tools | Reads | Tests | Latency (s) | Dynamic refreshes |
|---|---|---:|---:|---:|---:|---:|---:|
| email | verified pass | 30,712 | 15 | 6 | 4 | 41.11 | 0 |
| discount | test budget exhausted | 49,145 | 17 | 5 | 5 | 93.75 | 1 |
| regression | verified pass | 18,236 | 9 | 4 | 2 | 39.00 | 0 |
| greeting | verified pass | 21,277 | 10 | 4 | 3 | 34.43 | 0 |
| invoice | verified pass | 24,534 | 10 | 3 | 3 | 36.49 | 0 |

`discount` was the only case with a failed test and dynamic refresh. The second accepted patch subsequently passed targeted and full pytest, but the Agent used its five test runs before inspecting a non-stat diff. When it finally requested the full diff, another full pytest was required by the existing verification policy and the test-run budget was exhausted. Its final patch and passing tests are visible in the trajectory, but the run status is correctly **not** `tests_passed`.

## B2/B3 Comparison

| Metric | Frozen B2 | B3 observed |
|---|---:|---:|
| Verified success | 5/5 | 4/5 |
| Mean input tokens | 29,903 | 28,781 |
| Mean tool calls | 13.2 | 12.2 |
| Mean read calls | 4.2 | 4.4 |
| Mean test runs | 3.4 | 3.4 |
| Mean latency (s) | 48.99 | 48.96 |
| Initial modified-file Hit@1/3/5 | 4/5, 4/5, 5/5 | 4/5, 4/5, 5/5 |
| Dynamic refreshes | N/A | 1 total; 1/5 cases |
| Dynamic rescue | N/A | 0/5 |

The mean B3 metrics include one incomplete repair and should not be read as an efficiency improvement. B3 is allowed up to 4,000 extra estimated context tokens per refresh, so the token comparison is not budget matched. One model run per case also cannot isolate a causal effect from model variation. The observed real experiment did **not** demonstrate a dynamic rescue.

## Invoice Case Study

Frozen B2 initial ranking was `app/invoice.py`, `app/__init__.py`, `tests/test_invoice.py`, `app/tax.py`; the finally modified file, `app/tax.py`, ranked fourth. B3 reproduced exactly the same initial retrieval. The Agent read three files, patched `app/tax.py`, and all three test runs passed, reaching `tests_passed` with 24,534 input tokens, 10 tool calls, and 36.49 seconds. There was no first test failure, so no `FailureEvidence`, dynamic rank, or dynamic rescue. The cross-file issue was resolved through the Agent's own inspection. B3's failure-only trigger cannot change context when the Agent succeeds on the first hypothesis.

## Non-trigger Cases

Email, regression, greeting, and invoice had no failed test and correctly recorded zero dynamic refreshes. They made 4, 2, 3, and 3 test runs respectively. Discount had one failed targeted test run and one refresh. Thus the observed trigger precision was 1/1 eligible failed test and 0/4 cases without a failed test; this small sample does not establish general precision.

## Context Transition Analysis

The mock demonstrates a true shift from absent `internal/pricing.py` to dynamic rank 1. In the real discount run, initial Top2 was `app/pricing.py`, `tests/test_pricing.py`. A failed targeted test reported `assert None == 10.0` and `assert None == 1.03`, with traceback lines only in the test file. The 311-token refresh selected `app/__init__.py`, `pyproject.toml`, then a new range of `app/pricing.py`. The finally modified pricing file moved from initial rank 1 to dynamic rank 3; the two new files were not modified and did not supply a new bug location. `dynamic_modified_file_hit` and `dynamic_rescue` were both false. The refresh may have helped inspect changed pricing code, but the trajectory cannot isolate its contribution from the failed test observation itself. No refresh selected zero snippets; this case still illustrates that *novel* context can have limited value.

The runner computes modified-file ranks and rescue metrics only after each run. It saves per-refresh Top3, scores/reasons, new files/symbols/snippets, signatures, context tokens, failure-to-context latency, and same-file/overlap re-reads. B3 made 19 same-file and 19 overlapping reads across 22 read calls; reads were measured, not suppressed.

## Limitations

The five toy repositories are small, and one run per case with temperature zero does not eliminate provider nondeterminism. Four cases did not exercise dynamic retrieval; the only real refresh did not rescue a missed file and the Agent exhausted its test budget after tests had passed. The append-only context can grow beyond the initial budget; the 4,000-token bound is per refresh and estimated with `ceil(UTF-8 bytes / 4)`. Traceback parsing depends on pytest output shape, and missing function names remain unknown. New-file priority can promote distractors before a high-scoring range in a known source file, as discount shows. Range-based deduplication is approximate after code edits. Modified-file hit is only one measure of value; a dependency snippet can help even if the final patch stays in the initial file.

## Day 6 Entry

The core Day 5 implementation gate is met by deterministic extraction and ranking, exact B2 initial retrieval reuse, failed-test-only trigger, novel context with budget and deduplication, the mock rescue, 82 passing tests, and five completed real B3 runs. The real evidence is mixed: 4/5 verified repairs, 1/5 refreshes, and 0/5 dynamic rescues. Before claiming a dynamic retrieval benefit, add harder cases where the initial context misses a dependency and a first patch actually fails. The discount trajectory also suggests reviewing verification scheduling separately. Fixed-total-budget replacement and structured context state remain candidate Day 6 work, with no benefit claimed yet.
