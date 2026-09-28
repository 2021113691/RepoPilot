# RepoPilot Day 4 Summary

## Goal

Test whether Python symbol definitions improve the initial static context ranking over frozen B1 lexical retrieval, and measure whether the Agent rereads code already present in initial context. This is a five-case pilot with one real model run per method and case, not a significance claim.

## Frozen Baselines

B0 and B1 implementation and the five paired results remain frozen at Day 3 commits `1ab10e5` and `3b70a6a`. Day 4 did not change `AgentLoop`, prompts, tools, RepairPolicy, or `lexical.py`. The B2 rules were frozen in commit `4616448` before its real API runs. B2 reuses B1's query extraction, lexical candidate discovery, initial context format, 8,000 estimated-token budget, and score/cost greedy packing.

Each B2 workspace is cloned from its clean Day 3 B1 workspace and checked against the **exact same baseline commit**. The runner also verifies the frozen B1 config, all three Agent budgets, B1/B2 context budgets, model, temperature, output cap, and timeout before running. It did not rerun B0/B1 or use their outcomes to retrieve context.

## Python Symbol Index

The standard-library `ast` index scans tracked, safe `*.py` files. A `SymbolRecord` contains `name`, `qualified_name`, `kind`, `file`, `start_line`, `end_line`, and `parent`; supported kinds are function, async function, class, and method. Nested names are built from enclosing class/function names, for example `UserService.normalize_email`. Index maps both name → definitions and file → symbols with deterministic order. A syntax error is recorded by file and skipped without aborting retrieval.

## Symbol-Aware Ranking

The frozen B1 lexical score is the base. B2 adds one definition bonus per matched symbol: `+10` qualified exact, `+8` exact name (case-insensitive matching with case-sensitive evidence), or `+3` partial name. Non-definition lexical references get `+2`; Python source files get `+2`; related tests get `+1`. These fixed weights apply to every case. Candidate ordering and packing remain `score / token_cost`, descending, with deterministic ties.

## Definition vs Reference

A definition is an AST `FunctionDef`, `AsyncFunctionDef`, or `ClassDef` span. A lexical identifier hit outside a selected definition is a reference signal; B2 does not attempt full name resolution. Evidence records the matched symbol, definition kind, file role, and lexical/reference reasons. A matched symbol snippet uses its complete AST span up to 80 lines. Longer symbols are clipped from their definition start; unmatched keyword hits retain B1's merged ±20-line windows.

## Source vs Test Role

Paths under `test/` or `tests/`, or files named `test_*.py` or `*_test.py`, are test role; other Python files are source role. B2 gives a small source bonus and keeps related tests retrievable. In this run, B1 Top1 was a test in email and regression; B2 Top1 was a source file in all five cases. A source-first rank is not necessarily the right repair target: invoice's Top1 was the caller `app/invoice.py`, while the modified implementation was `app/tax.py` at rank 4.

## Context Budget

B1 and B2 both use the fixed 8,000-token *estimated* budget and the same `ceil(UTF-8 bytes / 4)` heuristic; API input/output usage remains separate. B2 selected contexts cost 283–371 estimated tokens across these small cases. Retrieval runs once before the Agent; failure evidence never triggers a new search. The B2 result logs candidates, selected snippets, scores, reasons, symbol coverage, and parse errors in `retrieval.json`.

## Redundant Read Analysis

The metric is computed after a run from successful `read_file` events and the initial selected snippet ranges. **Same-file re-read** means the read file was already in initial context. **Overlap re-read** additionally requires an inclusive line-range intersection. Denominators are all `read_file` calls, including failed calls. A read of a different range in the same file counts only as same-file. B0 has no initial retrieved context, so both metrics are N/A. No read is blocked or served from a cache.

| Method | Read calls | Same-file re-reads | Overlap re-reads |
|---|---:|---:|---:|
| B0 | 21 | N/A | N/A |
| B1 | 27 | 22/27 (81.5%) | 21/27 (77.8%) |
| B2 | 21 | 18/21 (85.7%) | 18/21 (85.7%) |

Most B1/B2 reads revisited initial context. This is direct evidence of duplicated context consumption, and is consistent with B1's larger input-token cost in regression, where B1 had 7 overlapping reads versus B2's 2. It is **not** sufficient to attribute all token differences to rereading: B2's overlap ratio was higher overall, while its mean input tokens were lower than B1's. Patch retries, model calls, history growth, and model variability also matter.

## Unit Tests

15 new tests cover function/class/method/async extraction, qualified names, deterministic index, syntax-error isolation, exact-definition ranking, test retention, snippet and budget bounds, file roles, no-gold API shape, same-file/overlap/non-overlap read classification, and an end-to-end MockBackend B2 run. Full `hello-agent` Python 3.10.21 suite: **70/70 passed**, including the prior 55.

## B2 Experiment

Five B2 real API runs completed on the same five Day 3 cases with ModelScope `Qwen/Qwen3.8-Flash-Next`, temperature `0`, and maximum output `1024`. All reached verified `tests_passed`; each B2 clone was restored clean. The [raw Day 4 results](day4_results.csv) include 15 rows: frozen B0/B1 data enriched with retrospective re-read metrics, plus five new B2 runs. The earlier [Day 3 results](day3_results.csv) remain unchanged.

## B0/B1/B2 Comparison

Continuous metrics show **mean / median** per five cases. Input/output tokens are provider usage; latency includes retrieval and Agent execution but excludes case setup/restoration.

| Metric | B0 Naive | B1 Lexical | B2 Symbol |
|---|---:|---:|---:|
| Success | 5/5 | 5/5 | 5/5 |
| Input Tokens | 27,512 / 24,463 | 32,177 / 35,340 | 29,903 / 30,927 |
| Output Tokens | 2,281 / 2,232 | 2,343 / 1,917 | 2,349 / 2,189 |
| Tool Calls | 12.8 / 13 | 13.8 / 14 | 13.2 / 13 |
| Search Calls | 1.0 / 1 | 0.2 / 0 | 0.4 / 0 |
| Read Calls | 4.2 / 4 | 5.4 / 6 | 4.2 / 4 |
| Unique Files Read | 3.0 / 3 | 3.4 / 3 | 2.8 / 3 |
| Test Runs | 3.0 / 3 | 3.2 / 3 | 3.4 / 3 |
| Patch Attempts | 1.6 / 1 | 2.2 / 2 | 2.2 / 2 |
| Latency (s) | 53.71 / 52.53 | 51.79 / 52.25 | 48.99 / 49.10 |
| Modified-file Hit@1 | N/A | 2/5 | 4/5 |
| Modified-file Hit@3 | N/A | 5/5 | 4/5 |
| Modified-file Hit@5 | N/A | 5/5 | 5/5 |

B2 improved Top1 ranking and reduced mean reads and input tokens relative to B1, but it did not beat B0's mean input tokens. No symbol-level gold label was available, so the report uses modified-file Hit@K and does not invent Definition Hit@K.

## Per-Case Results

Each triple is **B0 / B1 / B2**. Rank is the position of the finally modified file in initial context; B0 has no initial ranking.

| Case | Input Tokens | Tool Calls | Read Calls | B1 → B2 rank | B1 → B2 Top1 role |
|---|---:|---:|---:|---:|---|
| email | 14,872 / 12,742 / 30,927 | 9 / 8 / 14 | 2 / 3 / 3 | 2 → 1 | test → source |
| discount | 23,114 / 35,340 / 39,509 | 11 / 15 / 13 | 3 / 6 / 5 | 1 → 1 | source → source |
| regression | 25,806 / 55,259 / 24,696 | 14 / 20 / 12 | 6 / 8 / 3 | 3 → 1 | test → source |
| greeting | 49,307 / 35,627 / 21,821 | 17 / 14 / 11 | 6 / 6 / 4 | 1 → 1 | source → source |
| invoice | 24,463 / 21,917 / 32,563 | 13 / 12 / 16 | 4 / 4 / 6 | 3 → 4 | source → source |

Every B2 case had one exact AST definition match and zero parse errors; the issue query yielded 1–3 identifiers per case. The matched definition was not always the faulty implementation: invoice matched `invoice_total` in the caller, while the bug was in `apply_tax`.

## Regression Case Study

B1 ranked `tests/test_slug_target.py → tests/test_slug_existing.py → app/slug.py`. B2 ranked `app/slug.py → tests/test_slug_target.py → tests/test_slug_existing.py`, with `exact_symbol_definition: slugify` on the source snippet. The tests remain in context. B2 used 24,696 versus B1's 55,259 input tokens, 12 versus 20 tool calls, and 3 versus 8 reads. B1 made 7 overlapping reads, B2 made 2. This is the clearest case where structural ranking and lower exploration cost appeared together; one model run per method does not prove causality.

## Greeting Case Study

B1 already ranked the correct `app/greeting.py` first. B2 preserved that rank and reduced input tokens from 35,627 to 21,821, tool calls from 14 to 11, and reads from 6 to 4. It also ranked the unrelated `app/formatting.py` second through a partial `format` symbol match, showing that partial matching can pull in distractors even when the final task succeeds.

## Limitations

The five repositories are small, all methods passed all cases, and each condition has only one real model run per case. Temperature zero does not remove provider nondeterminism. B2 recognizes Python definitions but does not resolve calls or dependencies, so invoice's cross-file defect was ranked worse than in B1. Simple partial matching can also favor unrelated functions. Static context overlap is measured accurately but its causal token cost is not isolated. Large symbols are clipped at 80 lines. A reliable symbol-level Definition Hit@K would need an independent gold symbol annotation and is intentionally omitted.

## Day 5 Entry

**Day 4 Done Gate: PASS.** AST indexing, qualified names, syntax-error handling, deterministic definition-aware ranking, test retention, unchanged budget/packing, both re-read metrics, five real B2 runs, and all 70 tests are complete. Failure-driven dynamic context update is a reasonable next method, especially for invoice-style cases where the issue names a caller but the defect is elsewhere. Keep B0/B1/B2 frozen and compare a new condition; address duplicate reads as a separately measured context-management change.
