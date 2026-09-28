# RepoPilot Day 3 Summary

## Goal

Compare the effect of issue-conditioned initial repository context while keeping the model, Agent Loop, tools, RepairPolicy, issues, budgets, and each pair's Git baseline identical. This is a five-case pilot, not a statistical claim.

## B0 Baseline Freeze

B0 is the Day 2 repair behavior at commit `6f6869d` with no pre-retrieval or initial context. [B0 config](../experiments/b0_naive.yaml) preserves the 20 model-step, 40 tool-call, 5 patch-attempt, and 5 test-run limits. The Day 3 shared Agent Loop adds an optional initial-context argument; the default B0 message sequence remains exactly `system, issue`. All five real B0 repairs passed, as did the existing Day 1/Day 2 tests.

The B1 rules and paired runner were frozen in commit `1ab10e5` **before** the real experiment. No retrieval weights or case-specific rules were changed after seeing results.

## B1 Static Retrieval

[B1 config](../experiments/b1_static.yaml) uses a fixed 8,000-token *estimated* retrieval budget. One deterministic retrieval runs before the first model call, then its structured context appears as a user message before the issue. It never reruns after a test failure. The Agent keeps the same six repository tools and repair policy.

Retrieval takes only `issue`, `workspace`, and `context_budget_tokens`. It uses tracked repository contents, never a gold patch, expected files, hidden tests, future failures, or manually labeled targets. The selected files are compared with finally modified files only after a run ends.

## Retrieval Signals

The query parser extracts dotted symbols, calls, snake/camel/Pascal identifiers, filename hints, and useful keywords; it removes a small fixed stopword list and email-address literals. Candidate discovery uses tracked file/path names and bounded, case-insensitive literal ripgrep hits, with a Python fallback. It caps results at 20 hits per term and searches at most 20 terms. A same-stem test/source pair gets a relation bonus.

Per-file score, with fixed constants, is:

```text
5 × filename_match + 2 × path_match
+ 4 × distinct_identifier_hits + 1 × distinct_keyword_hits
+ 2 × test_source_relation + 0.5 × min(4, matched_line_count)
```

Each hit yields a ±20-line window. Overlapping windows merge, snippets cap at 80 lines, and each file contributes at most three snippets. Items sort by `score / token_cost`, then score and path/line for deterministic ties. Greedy packing skips items that would exceed 8,000 estimated tokens.

Context and each item are costed by `ceil(UTF-8 bytes / 4)`. This is an approximation, **not** the provider's tokenizer or API usage. The B0/B1 `input_tokens` and `output_tokens` below use ModelScope's returned usage; the runner would record `null` if any call omitted it. Each B1 run saved `retrieval.json` with snippets, scores, reasons, estimated costs, candidate count, and selected count.

## Experiment Setup

- Provider/model: ModelScope / `Qwen/Qwen3.8-Flash-Next`; temperature `0`; maximum output `1024` tokens per call.
- Five paired cases: email, discount, regression, greeting, invoice. The last two add a competing filename and a cross-file calculation, respectively.
- Each pair starts from the **same Git commit** in two disposable workspaces. The runner checks a clean baseline, restores each workspace after collecting results, and logs commit/tree hashes in [day3_results.csv](day3_results.csv) and each run's `experiment.json`.
- B0/B1 share the same Agent Loop, model configuration, tool schemas, RepairPolicy, 20 steps, 40 tool calls, 5 patch attempts, 5 test runs, and issue text. Only B1 receives initial repository context.
- Task success requires the Day 2 verified status `tests_passed`; model self-report does not count. Latency is runner wall time from retrieval start through Agent completion, excluding case setup and restoration.

## Unit Tests

11 new tests cover query and identifier extraction, stopwords, filename/content hits, snippet merging, test/source relation, budget and deterministic ordering, credential/path filtering, ripgrep fallback, no-gold API shape, B0/B1 message placement, paired baseline equality, and result artifacts. Full suite in the `hello-agent` Python 3.10.21 environment: **55/55 passed**.

## Controlled Experiment

All **10 real API runs** completed; both methods repaired all five cases. The raw per-run metrics and task IDs are in [day3_results.csv](day3_results.csv). B1 estimated context ranged from 273 to 372 tokens in these small repositories, below the 8,000-token cap. B1 retrieved the finally modified file at rank 1 in 2/5 cases, by rank 3 in 5/5, and by rank 5 in 5/5. Hit@K uses the final modified file only for post-run analysis.

## Aggregate Results

| Metric | B0 Naive | B1 Static |
|---|---:|---:|
| Success | 5/5 | 5/5 |
| Avg Input Tokens | 27,512.4 | 32,177.0 |
| Avg Output Tokens | 2,281.2 | 2,343.0 |
| Avg LLM Calls | 11.4 | 11.4 |
| Avg Tool Calls | 12.8 | 13.8 |
| Avg List Calls | 1.0 | 1.0 |
| Avg Search Calls | 1.0 | 0.2 |
| Avg Read Calls | 4.2 | 5.4 |
| Avg Unique Files Read | 3.0 | 3.4 |
| Avg Test Runs | 3.0 | 3.2 |
| Avg Latency (s) | 53.71 | 51.79 |
| Retrieval Hit@1 | N/A | 2/5 |
| Retrieval Hit@3 | N/A | 5/5 |
| Retrieval Hit@5 | N/A | 5/5 |

B1 reduced explicit search calls but increased reads, tool calls, and input tokens on average. The average latency difference was small and mixed by case.

## Paired Results

The deltas below are **B1 minus B0**. Negative values mean B1 used less of that resource.

| Case | Δ Input Tokens | Δ Tool Calls | Δ Unique Files Read | Δ Latency (s) | B1 Hit@1/3/5 |
|---|---:|---:|---:|---:|---|
| email | −2,130 | −1 | +1 | −2.17 | 0/1/1 |
| discount | +12,226 | +4 | +1 | +15.81 | 1/1/1 |
| regression | +29,453 | +6 | −1 | +15.62 | 0/1/1 |
| greeting | −13,680 | −3 | 0 | −23.10 | 1/1/1 |
| invoice | −2,546 | −1 | +1 | −15.77 | 0/1/1 |

## Retrieval Case Study

**Greeting:** The issue asks `format_greeting(name)` to strip surrounding whitespace while preserving internal spaces. B1 ranked `app/greeting.py` first, followed by `tests/test_greeting.py`; the final patch modified `app/greeting.py`. B1's tool sequence began `list_files → read_file ×3 → apply_patch`, then a second patch and passing tests. B0 also found the file, but its first applied patch led to a failed test and another attempt. B1 used 35,627 versus 49,307 input tokens, 14 versus 17 tool calls, and 3 versus 4 test runs. Both ultimately passed. This is an observed paired difference, not proof that retrieval alone caused the lower cost.

## Failure Case Study

**Regression:** B1 ranked `tests/test_slug_target.py` and `tests/test_slug_existing.py` ahead of the eventual modified file `app/slug.py`, which appeared third. The word “existing” in the issue contributed a filename match for a test. B1's first patch call failed `git apply --check` for whitespace, then an applied patch failed a test before a second applied patch and final passing tests. It used 55,259 versus B0's 25,806 input tokens, 20 versus 14 tool calls, and 5 versus 3 test runs. The test-first ranking and repeated reads are plausible contributors; model nondeterminism and patch choices prevent a causal attribution from one pair.

## Limitations

Five small toy cases and one run per method cannot establish a reliable general effect or statistical significance. Temperature zero does not guarantee identical model behavior. All cases succeeded, so this pilot measures exploration cost more clearly than repair-success differences. B1's extra initial context is small here; larger repositories may behave differently. The token estimate is a fixed byte heuristic, while provider usage is actual API accounting. The retriever has no symbol or graph knowledge and can rank test snippets above repair targets.

## Day 4 Entry

**Day 3 Done Gate: PASS.** B0 remains operational; B1 is deterministic, bounded, logged, and gold-free; five pairs used identical baselines and budgets; metrics and full tests are available. Day 4 symbol-aware repository understanding is a reasonable next experiment, with the regression test-first ranking as a concrete motivation. Preserve these B0/B1 results and evaluate any new signal as a separate method rather than retroactively changing this frozen B1.
