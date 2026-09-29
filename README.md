# RepoPilot

RepoPilot is a repository-level coding agent project focused on context engineering. **Current status: Day 5 failure-driven dynamic context experiment.** It can inspect a Git repository, apply a constrained patch, run pytest, use failures to retry, and record the trajectory. B1 adds one initial lexical context; B2 adds Python AST definition signals; B3 uses failed tests to append newly relevant repository context. B0 remains the naive baseline.

## Architecture

```text
Issue + RuntimeContext -> ModelBackend -> AgentLoop
                                      -> list/search/read
                                      -> apply_patch -> git_diff -> run_tests
                                                         | PASS -> final result
                                                         | FAIL -> evidence -> retry
                         events       -> runs/<task_id>/trajectory.jsonl
```

`ModelBackend` returns provider-neutral `ModelResponse`, `ToolCall`, and `TokenUsage` objects. The current real backend uses Python's standard library to call an OpenAI Chat Completions-compatible endpoint. `MockBackend` provides scripted responses for offline tests. The existing read-only mode remains available; `--repair` enables patch and test tools on a clean Git repository. The loop limits model steps, tool calls, patch attempts, and test runs. A repair is verified only after the latest patch's diff is inspected and full pytest passes.

## Installation

Requires Python 3.10 or newer. The core runtime uses the standard library; repair mode requires pytest in the Python environment that runs the agent.

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```

If pytest is already installed, `python -m pytest -q` works directly from the repository root.

## Configuration

Place configuration in environment variables or a local `.env` file at the repository root. `.env` and `runs/` are ignored by Git. The CLI never takes an API key argument.

```text
REPOPILOT_API_KEY=your-token
REPOPILOT_BASE_URL=https://api-inference.modelscope.cn/v1
REPOPILOT_MODEL=Qwen/Qwen3.8-Flash-Next
```

Optional: `REPOPILOT_TEMPERATURE` (default `0`), `REPOPILOT_MAX_TOKENS` (default `1024`), `REPOPILOT_TIMEOUT` (default `60` seconds). For the current ModelScope setup, the CLI also accepts `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, and `MODEL_ID` when that base URL points to `api-inference.modelscope.cn`; it normalizes a bare host to `/v1`. This alias does not add support for Anthropic's native Messages API.

## Quick start

Run the Day 1 offline scripted inspection smoke:

```powershell
python -m examples.mock_smoke
```

For a read-only inspection with a real endpoint:

```powershell
python -m repopilot --workspace examples/toy_repo --issue "normalize_email() preserves uppercase characters in the domain of User@Example.COM. Find the cause."
```

For a real repair smoke, the case runner copies a toy case to a fresh ignored directory, commits its baseline, and invokes the same agent loop:

```powershell
python -m examples.run_toy_case --case email
```

Other cases: `discount` and `regression`. Direct repair on a clean Git root is also available with `python -m repopilot --repair --workspace PATH --issue "..."`. Repair defaults are 20 model steps, 40 tool calls, 5 patch attempts, and 5 test runs; corresponding `--max-*` flags are available. Every run writes `trajectory.jsonl` and `summary.json` under `runs/<task_id>/`. Token counts remain `null` if the endpoint does not supply usage.

## Available tools

| Tool | Behavior |
| --- | --- |
| `list_files` | Lists relative paths with depth and entry limits; skips common generated directories and credential files. |
| `search_code` | Literal search with ripgrep, or a Python fallback; returns relative paths and line numbers. |
| `read_file` | Reads numbered line ranges, at most 250 lines per call. |
| `git_diff` | Shows `git diff` or `git diff --stat` when the workspace is the Git root. |
| `apply_patch` | Applies a unified Git diff to existing tracked files only; available in repair mode. |
| `run_tests` | Runs full pytest, a test path, or a test node with a timeout; available in repair mode. |

File paths are resolved against the workspace and rejected when they escape it, including symlink targets. Credential files such as `.env` are excluded from repository tools. Repair starts only from a clean Git baseline. `apply_patch` validates the complete diff with `git apply --check` and applies it without `--reject`; it never stages or commits. `run_tests` builds a fixed argument list for `python -m pytest` with `shell=False`, validates the path or node ID, enforces a timeout, and removes credentials from the child environment. Test failures return compact evidence to the model while longer sanitized output remains in the trajectory. No arbitrary shell tool is exposed.

## Repair Safety

Repair targets are governed by a configurable `RepairPolicy`; toy repair benchmarks treat test files as read-only verification oracles. By default, paths under `tests/` or `test/`, including nested directories, and Python files named `test_*.py` or `*_test.py` are protected. Callers can supply `AgentLoop(..., repair_policy=RepairPolicy(...))` to change protected patterns or restrict writes with `writable_globs`. Protected patterns always take precedence over the writable list.

Before writing, `apply_patch` checks every target against the workspace boundary, symlink and credential restrictions, the repair policy, and Git tracking. A denied target rejects the whole patch with a `protected_path` or `not_writable` observation; the Agent can then patch an allowed source file. The trajectory records a `patch_rejected` event and the summary counts protected-path rejections.

Repair summaries record patch attempts and successful patches, test runs and failures, modified files, added/deleted lines, changed LOC, model/tool calls, tokens, and latency. The full suite must pass after the latest patch before the agent reports `tests_passed`.

## Day 3 B0/B1 experiment

### Experiment Modes

| Mode | Initial repository context |
| --- | --- |
| B0 Naive Agent | None; the Agent searches and reads files itself. |
| B1 Static Retrieval Agent | One issue-conditioned context message before the Agent starts. |

B0 is frozen at commit `6f6869d` and configured by [experiments/b0_naive.yaml](experiments/b0_naive.yaml). B1 uses [experiments/b1_static.yaml](experiments/b1_static.yaml). Both configs share Agent budgets; both use the same model settings from `.env`, tools, RepairPolicy, and clean baseline commit for each paired case. B0 starts with the original system and issue messages. B1 inserts a single retrieved repository context before the issue. Retrieval never runs again after test failures.

```text
Issue -> deterministic lexical retrieval -> snippet ranking
      -> token-budget packing -> initial repository context -> same repair Agent
```

Run all five paired toy cases with the configured real model:

```powershell
python scripts/run_experiment.py --paired --cases email discount regression greeting invoice
```

The runner clones each disposable case baseline, verifies the commits match, writes trajectories and B1 `retrieval.json` files under `runs/day3/`, collects `reports/day3_results.csv`, and restores the disposable working trees. `--config experiments/b0_naive.yaml` or `--config experiments/b1_static.yaml` runs one method. The `.yaml` configs use JSON syntax, a YAML 1.2 subset readable without another dependency.

### B2 Symbol-Aware Static Context

B2 adds Python AST definitions and lexical references to the frozen B1 candidate discovery. It ranks implementation definitions ahead of ordinary references, keeps related test snippets available, and uses the same 8,000 estimated-token budget and `score / token_cost` greedy packing. Retrieval still runs only once before the same repair Agent starts.

```text
Issue -> lexical query -> tracked Python AST index -> definition/reference matching
      -> symbol-aware ranking -> same token-budget packing
      -> initial repository context -> same repair Agent
```

Run B2 on the five Day 3 cases using the clean, frozen Day 3 workspaces under `runs/day3/`:

```powershell
python scripts/run_day4.py --cases email discount regression greeting invoice
```

The runner checks Day 3 model/config/baseline consistency, clones each exact baseline commit, and writes B2 trajectories plus `reports/day4_results.csv`. It also analyzes saved B1 trajectories for read calls that revisit an initially retrieved file or overlap an initially retrieved line range. B0 has no initial retrieval, so those ratios are recorded as N/A. This analysis only measures `read_file` behavior; it never changes tool permissions or caches observations.

The five-case comparison, including the regression ranking improvement and invoice cross-file miss, is in [Day 4 report](reports/day4_summary.md) and [raw results](reports/day4_results.csv).

### B3 Failure-Driven Dynamic Context

B3 starts with the exact B2 symbol-aware initial retrieval. Only a failed `run_tests` result can trigger a refresh. A deterministic extractor takes failed test nodes, pytest traceback files/lines/functions, exception types, and short assertion messages from that run. The retriever combines these signals with the original issue, applies fixed bonuses to B2 candidates, and prioritizes unseen files, unseen symbols, and new line ranges. Fully covered snippets and repeated failure signatures are skipped. B0/B1/B2 configurations and prior experiment results remain frozen.

```text
Issue -> B2 symbol-aware initial context -> repair Agent -> failed test
      -> failure evidence -> dynamic reranking -> novel context -> Agent retry
```

The B3 context is an additional observation after the failed test tool reply. It describes evidence and candidate code without asserting a root cause. Each refresh is limited to 4,000 **estimated** tokens, with at most two refreshes per run. The initial B2 budget remains 8,000 estimated tokens. **Dynamic context is append-only and does not yet enforce a fixed total context budget.** B3 token usage therefore is not a budget-matched efficiency comparison with B2.

After configuring `.env`, run B3 on the same five frozen case baselines:

```powershell
python scripts/run_day5.py --cases email discount regression greeting invoice
```

The runner checks B0/B1/B2/B3 Agent budgets, B2 initial retrieval settings, provider settings, and baseline commits before running. It writes trajectories, failure evidence, context transitions, and per-case metrics under `runs/day5/`, plus [Day 5 results](reports/day5_results.csv) and the [Day 5 report](reports/day5_summary.md). Final modified-file ranks and rescue metrics are computed only after each run; they never enter retrieval.

The five real B3 runs completed with **4/5 verified repairs**. One failed test in `discount` triggered a refresh, but it did not introduce a new repair file; that run exhausted its test budget after later tests passed. `invoice` repaired the cross-file defect without a failed test, so no dynamic refresh occurred. The mock fixture demonstrates a true dynamic rescue; the real five-case set did not.

Retrieval uses deterministic issue tokens, tracked file names, bounded ripgrep content hits, and a test/source name relation. It merges nearby ±20-line windows, caps snippets at 80 lines, and greedily packs by relevance score divided by estimated token cost under an 8,000-token budget. Estimated context tokens use `ceil(UTF-8 bytes / 4)`; they are **not** API token usage. `input_tokens` and `output_tokens` come from provider usage only and are `null` in experiment results if any model call omits usage. The case-level Hit@1/3/5 values compare retrieved files with finally modified files only after repair; those labels never enter retrieval.

## Current limitations

- One real Day 2 repair smoke passed on the email toy case. The discount retry and regression scenarios have deterministic mock coverage. See [Day 2 report](reports/day2_summary.md).
- The patch tool supports existing tracked files and standard unified Git diffs; it does not create, delete, or rename files.
- There is no arbitrary shell, dependency graph, fixed total dynamic context budget, or benchmark integration yet.
- The lightweight `.env` reader supports simple `KEY=VALUE` lines, not the full dotenv format.
- Shell detection uses the Windows parent process without extra packages, or optional `psutil` on other systems. It reports `unknown` if no reliable signal exists; `REPOPILOT_SHELL` can supply an explicit value.
- `git_diff` reads working-tree changes only and requires the workspace itself to be the Git root.
