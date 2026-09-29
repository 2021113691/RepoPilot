# RepoPilot Day 5.5: Dynamic Context Challenge Set

## Motivation

Day 5 implemented B3's failure-driven context refresh, but the General Repair Set produced 4/5 verified B3 repairs, only one refresh, and no real dynamic rescue. A mock case demonstrated the mechanism. This study asks whether five deliberately diagnostic repair tasks expose a measurable benefit when execution failure adds repository evidence that the initial issue lacks.

## Why the General Set Was Insufficient

In general-set `invoice`, the relevant `app/tax.py` ranked fourth initially, yet the Agent found and fixed it before any test failure; B3 never had a trigger. In `discount`, a refresh occurred, but verification budget exhaustion prevented a verified result. These cases did not isolate whether dynamic context can redirect repair after an initial miss.

## Challenge Set Definition

The **Dynamic Context Challenge Set is a diagnostic benchmark intentionally constructed to stress failure-driven context acquisition**. It is not a natural-distribution benchmark or a SWE-bench result. The unchanged General Repair Set remains the measure of ordinary small-repository repair behavior. Each challenge repository has 11–14 tracked files, normal read/search/test access, and a downstream defect; B2 is free to discover it autonomously.

## Qualification Criteria

The five fixtures and gate were fixed in commit `75fb898` before model experiments. An offline checker required: a failing baseline pytest run; expected bug file absent from B2 Top3; a failure file/symbol clue absent from both issue and initial selected context; and frozen B3 reranking of the baseline failure putting the bug file in dynamic Top3. All five passed. The [qualification CSV](day5_challenge_qualification.csv) records baseline commits, ranks, clues, and selected files. This gate uses gold labels offline; it never invokes a model or passes labels to the Agent.

## Cases

| Case | Dependency pattern | Expected bug file / symbol | B2 initial rank | Offline failure clue | Offline dynamic rank |
|---|---|---|---:|---|---:|
| c01_service_pricing | service → adjustment | `engine/adjustments.py` / `apply_rate` | absent | traceback in adjustments | 1 |
| c02_parser_normalizer | parser → normalizer | `core/canonical.py` / `clean_atom` | absent | traceback in canonical | 1 |
| c03_controller_serializer | controller → serializer | `wire/encoding.py` / `pack_number` | absent | traceback in encoding | 1 |
| c04_invoice_rounding | invoice → tax → rounding | `mathops/quantize.py` / `quantize_money` | absent | traceback in quantize | 1 |
| c05_cache_key | catalog service → key builder | `infra/key_codec.py` / `make_key` | absent | failing storage-key test | 2 |

Issues describe public behavior without naming these files. Each fixture includes source and tests that would also permit autonomous inspection. Gold labels are in `repopilot/evaluation/challenge_gold.json`, outside every copied Git baseline. `execute_method` receives only the public `ChallengeCase`, method/config/backend, baseline, and optional B2 retrieval artifact; scoring loads gold only after the Agent run finishes.

## Frozen B2/B3

No B2 or B3 retrieval code, weights, trigger, prompt, Agent Loop, tools, RepairPolicy, or Agent budget changed. The runner verifies their working-tree contents against frozen Day 5 commit `28f7340`. Both methods use ModelScope `Qwen/Qwen3.8-Flash-Next`, temperature 0, 1,024 maximum output tokens, the same 20-step/40-tool/5-patch/5-test limits, and 8,000 estimated initial-context tokens. B3 alone retains its frozen 4,000 estimated-token refresh and two-refresh cap. Transport retries repeat an identical request after provider timeouts or HTTP 429; interrupted provider runs are checkpointed and excluded from task-success denominators. No evaluable completed run was repeated to select a favorable outcome.

## Evaluation Protocol

Each pair clones the same clean, case-specific Git baseline. B2 runs first and saves `retrieval.json` and a full trajectory. Before B3 calls the model, the runner compares its fresh initial `retrieval.json` against B2's complete artifact, including snippets, scores, evidence, and order. **All five pairs matched exactly**, and their baseline commits matched. Both methods use the same Agent and tools; B3 adds only the existing failure hook. Final modified files, expected bug file, context use, rescue, and failure taxonomy are scored after the run. Provider interruptions were excluded; the final CSV contains ten evaluable runs.

## Metrics

Dynamic trigger rate is cases with at least one injected refresh divided by five. Tool-level context utilization means a file selected in an injected refresh was subsequently read or successfully patched before another refresh. Useful-refresh rate uses this operational definition; it does not prove causality. Strict dynamic rescue additionally requires an initial Top3 miss, a failed test before injection, dynamic Top3 hit, a new repair direction, a successful patch to the expected file after injection, and final verified success. Numeric rank improvement is reported only when both ranks exist; an initial miss followed by a dynamic hit is reported separately. Failure evidence novelty lists file/symbol/assertion clues absent from issue and initial context.

## B2 vs B3 Results

| Metric | B2 Static | B3 Dynamic |
|---|---:|---:|
| Verified success | 4/5 | 3/5 |
| Mean input tokens | 43,361 | 47,407 |
| Mean tool calls | 19.0 | 18.8 |
| Mean read calls | 8.4 | 7.8 |
| Mean test runs | 3.0 | 2.8 |
| Mean latency (s) | 106.23 | 109.63 |
| Dynamic trigger | N/A | 4/5 cases |
| Tool-level useful refresh | N/A | 3/4 refreshes |
| Strict dynamic rescue | N/A | 0/5 cases |

These are five diagnostic tasks with one evaluable run per method and case. B3's append-only refresh gives it a larger possible context budget; the token means are descriptive, not a budget-matched efficiency result. The [paired results CSV](day5_challenge_results.csv) holds per-run usage, status, clues, ranks, context use, and taxonomy.

| Case | Initial bug rank | B2 result | B3 refresh | Dynamic bug rank | Context used | B3 result | Rescue |
|---|---:|---|---:|---:|---|---|---|
| c01_service_pricing | absent | PASS | 0 | none | no | PASS | no |
| c02_parser_normalizer | absent | patch budget | 1 | 1 | no after refresh | patch budget | no |
| c03_controller_serializer | absent | PASS | 1 | 1 | yes | PASS | no |
| c04_invoice_rounding | absent | PASS | 1 | 1 | yes | patch budget | no |
| c05_cache_key | absent | PASS | 1 | 2 | yes | PASS | no |

## Dynamic Trigger Analysis

Baseline pytest failure evidence made all five cases offline qualified. In real B3 trajectories, c01 repaired the fault before any test failure and therefore never triggered a refresh. The other four each had one failed `run_tests` result and one refresh. No patch rejection or model uncertainty triggered retrieval. Compared with the General Repair Set's 1/5, the challenge set's 4/5 trigger rate shows that its construction exposed the intended failure condition more often.

## Rank Shift Analysis

All five expected bug files were absent from initial selected context. Among the four triggered B3 runs, actual failure evidence moved three bug files to dynamic rank 1 and one to rank 2; all four had novel evidence absent from issue and initial context. Numeric `initial_rank - dynamic_rank` is undefined for these initial misses, so the CSV records `initial_miss_dynamic_hit=True` instead. This is strong evidence that the frozen B3 retriever can turn failure output into a correct candidate ranking on these fixtures; it is not evidence of a completed rescue.

## Context Utilization

Three of four injected refreshes were followed by a read or successful patch of a selected file. In c03 and c05, the Agent had **already read the expected bug file before the failed test**, then read and patched it again after injection. Their successful repairs cannot be attributed to the refresh. In c04, the Agent first read `mathops/quantize.py` after the refresh, which is a genuine direction shift, but no patch was accepted. In c02, the Agent had read `core/canonical.py` before the refresh and did not read or successfully patch an injected file afterward. Tool-level utilization is therefore 3/4, while demonstrated causal benefit remains unproven.

## Dynamic Rescue Cases

**None.** The closest successful cases are c03 and c05: novel traceback or failed-test evidence ranked the right file, the Agent used the injected context, patched that file, and passed. Both had found the file beforehand, so they fail the direction-change requirement. Calling either a rescue would conflate dynamic context with ordinary autonomous exploration.

## Non-rescue Cases

**c04_invoice_rounding** is the clearest retrieval-success/repair-failure example. The first B3 action ran pytest; failure evidence included `mathops/quantize.py`, absent from the issue and B2 initial context. The refresh ranked that file first, and the Agent read it. It then submitted five malformed unified diffs; all were rejected, so no source file changed and the patch-attempt budget ended the run. B2, with the same baseline and tools but no refresh, found and repaired `mathops/quantize.py` and passed. The failure is patch generation/control, not a missed retrieval rank.

**c02_parser_normalizer** had a traceback clue for `core/canonical.py` and dynamic rank 1. The Agent had read canonical before the test, then made five rejected patch attempts after the refresh, often targeting the parser instead. It never successfully patched or reread an injected file. Both B2 and B3 exhausted patch attempts. This is a context-use observation plus a primary repair-reasoning failure.

## Failure Taxonomy

| Category | Observed evaluable runs | Evidence |
|---|---:|---|
| retrieval_failure | 0 | Every triggered B3 refresh placed the expected file in Top2. |
| context_utilization_failure | 0 as primary | c02 is a secondary utilization miss, but all five B3 patch attempts were rejected. |
| repair_reasoning_failure | 3 | B2 c02; B3 c02 and c04 exhausted patch attempts. |
| verification_budget_failure | 0 | No challenge run exhausted test budget after a verified patch. |
| tool_failure | 0 | Patch rejections came from model-generated malformed diffs, not a tool outage. |
| provider_failure | 0 final rows | Earlier c03 B2 read timeouts were checkpointed and excluded; the evaluable retry passed. |

## Harness Tests

Eight new tests cover the offline five-case qualification gate, gold isolation from Agent workspaces, baseline freeze checks, exact initial-context comparison, rank shift and strict rescue scoring, provider-failure exclusion, CSV output, and identical-request transport retry. The full `hello-agent` suite passed **90/90 tests**, including the previous 82.

## Limitations

The challenge fixtures intentionally create downstream errors and are not representative of the natural frequency of such bugs. Five cases and one model run per pair do not support significance or general repair superiority claims. Some B2/B3 differences may be stochastic model behavior despite temperature zero. The operational utilization metric can count a reread of a file the Agent already knew; strict rescue addresses this by requiring a direction change. The benchmark does not vary repository scale, context budget, or model. Failure categories were reviewed from trajectories and should not be treated as perfect automatic diagnoses.

## Implications for Day 6

The Day 5.5 gate is **PASS as an evaluation protocol**: five qualified cases, isolated gold labels, ten evaluable paired runs with exact initial-context and baseline equality, trigger/rank/use/rescue metrics, trajectory review, and passing tests. The result is **not a B3 effectiveness win**: 4/5 B2 versus 3/5 B3 verified success and zero strict rescues. Frozen B3 usually ranked the right file when triggered; the observed bottleneck was mainly repair execution and verification behavior, with c02 also showing limited post-refresh context use. Prioritize structured context state and utilization analysis, alongside a separate Agent patch/control study. Fixed-budget replacement should wait until a causal dynamic-context benefit is demonstrated; further weight tuning is not justified by these five cases alone.
