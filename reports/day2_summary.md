# RepoPilot Day 2 Summary

## Implemented

The Day 1 runtime now has an optional repair mode (`--repair`) with `apply_patch`, `run_tests`, patch/test state, bounded retry, compact failure observations, and repair metrics. Read-only mode remains available. No retrieval engine or arbitrary shell tool was added.

## Apply Patch

`apply_patch` accepts one standard unified Git diff containing `diff --git`, matching `---`/`+++` headers, and `@@` hunks. It updates up to five existing tracked regular files per call. File creation, deletion, rename, mode changes, binary patches, absolute paths, `..`, `.git`, credential files, and workspace escapes are rejected. The tool runs `git apply --check` before `git apply`, without `--reject`. Git documents that default `git apply` rejects the entire patch when a hunk does not apply. It modifies only the working tree and never stages or commits. After application, it reads `git diff` and `git diff --numstat`; an unexpected post-check failure restores the target bytes. [Git apply documentation](https://git-scm.com/docs/git-apply)

## Test Runner

`run_tests` accepts full pytest, an existing workspace-relative Python test file or directory, or a file plus simple `::test_name` node ID. Scope strings with shell operators, absolute paths, `..`, unsupported node syntax, or nonexistent paths are rejected. It launches `[sys.executable, "-m", "pytest", ...]` with `shell=False`, `cwd=workspace`, a 1–120 second timeout, captured output, and credentials removed from the child environment.

`TestResult` records `success`, `exit_code`, `stdout`, `stderr`, `failed_tests`, `passed_count`, `failed_count`, `duration`, `timeout`, `exception_type`, `assertion_message`, and `traceback_locations`. Missing pytest counts remain `null`. The model sees an observation capped at 3,000 characters; the trajectory can retain longer sanitized output.

## Safety

Repair mode refuses a workspace whose Git root differs from the selected workspace or whose initial Git status is dirty. Path checks resolve symlinks before applying workspace and sensitive-file restrictions. The test runner executes fixed pytest arguments, never a model-provided command string. `.env` and other credential files are unavailable to repository tools. Test output and trajectory values redact known environment credential values; authorization fields are redacted.

## Repair Policy

### Protected Paths

`RepairPolicy` supplies configurable `protected_globs`. The toy default protects root and nested `tests/` and `test/` directories and Python files named `test_*.py` or `*_test.py`. Protected patterns override a writable whitelist.

### Writable Paths

Optional `writable_globs` limits writes to matching repository-relative paths. If omitted, otherwise safe tracked source files remain writable. A caller can pass a custom policy to `AgentLoop(..., repair_policy=...)` or directly to `ApplyPatch`.

### Atomic Rejection

After parsing and resolving every target, `apply_patch` checks the policy before `git apply --check` or any write. A patch containing both source and protected test paths is rejected as a whole. The tool returns `protected_path` or `not_writable` with normalized relative paths; the trajectory records `patch_rejected` and the summary counts protected rejections.

### Test-File Protection

Toy tests are read-only verification oracles under the default policy. This is a configurable benchmark rule, not a permanent prohibition on changing tests in other tasks. A mock Agent test confirms that a protected-path rejection is returned as an observation and the Agent can then repair source code and pass full pytest.

### Validation

The policy tests cover allowed source edits, protected test files, mixed-patch atomicity, nested test paths, filename patterns, `./` normalization, Windows separators, whitelist behavior, and explicit policy overrides. The full regression suite is run in the `hello-agent` Python 3.10 environment.

## Repair Loop

The loop supports `inspect -> patch -> git_diff -> pytest -> failure evidence -> patch -> pytest`. A model-written success statement cannot set the final status. `tests_passed` requires a successful full pytest run after the latest successful patch and inspection of that patch's diff. If the model tries to finish earlier, the loop asks it to continue. An incomplete run ends with a deterministic `Repair incomplete` result and last failing test IDs when available.

## Metrics

The summary adds `patch_count` (successful patches), `repair_attempts` (all patch calls), `test_runs`, `test_failures`, `files_modified`, `changed_loc` (added + deleted lines from `git diff --numstat`), `tests_passed`, and the last structured test result. It retains LLM/tool calls, input/output tokens, latency, and files seen. JSONL adds `patch_apply`, `test_run`, `test_failure`, and `repair_retry` events.

## Unit Tests

Python 3.10.21 (`hello-agent`): **44 passed**, including the original 14 Day 1 tests and 10 new repair-policy tests. Coverage includes valid and invalid patches, atomic multi-file failure, path and credential rejection, Git diff and LOC, full/file/node pytest, failure parsing, timeout, shell injection rejection, clean baseline, budgets, credential stripping/redaction, mock retry, regression detection, protected test paths, writable whitelists, and recovery from a policy rejection.

## Mock Retry Smoke

The discount case applies a plausible first patch, runs full pytest, observes the remaining half-cent rounding failure, then applies a second patch and passes full pytest. The mock trajectory contains `test_failure` and `repair_retry`; final status is `tests_passed`, with 2 successful patches, 2 test runs, and 1 test failure.

## Real API Repair Smoke

- Provider/model: ModelScope / `Qwen/Qwen3.8-Flash-Next`.
- Issue: `normalize_email()` preserves uppercase domain letters; `User@Example.COM` should become `User@example.com`.
- Isolated workspace: a fresh Git-committed copy of `examples/toy_repo` under `runs/toy_cases/email-b0eca11ecdc3`.
- Result: `tests_passed` in 10 model steps and 14 tool calls.
- Tool sequence: `list_files -> search_code -> read_file x4 -> list_files -> run_tests -> apply_patch -> apply_patch -> read_file -> run_tests -> git_diff -> run_tests`.
- The first targeted pytest run failed with `AssertionError`. The first patch call used an unsupported patch syntax and was rejected without changing files. The second patch call applied a unified diff successfully. Two subsequent full pytest runs passed (1 test passed each time).
- Changed file: `app/email_utils.py`. The patch changed the return to `f"{local}@{domain.lower()}"` while preserving the local part. It also clarified the function comment/docstring. Git reports 4 added and 2 deleted lines, so `changed_loc=6`.
- Metrics: 1 successful patch, 2 patch attempts, 3 test runs, 1 test failure, 22,513 input tokens, 2,115 output tokens, 45.6758 seconds total agent latency.
- The final model answer described the actual diff and passing full pytest. The original tracked toy fixture was not modified. The saved run is `runs/fd0c43506670449ba2590433096a9124/`.

## Toy Cases

1. **Email direct fix:** one real API repair passed. The case runner creates a fresh committed copy for each run.
2. **Discount failure then retry:** the baseline fails for zero rate and half-cent rounding. The deterministic mock repairs zero rate first, observes the remaining failure, then repairs rounding and passes.
3. **Slug regression:** a first patch passes the targeted repeated-space test but breaks a preexisting tab-preservation test. Full pytest detects the regression; a second mock patch passes the full suite.

The simple `examples.run_toy_case` runner prepares each case from a clean fixture, runs the agent, writes a trajectory and summary, and returns success only when the loop verifies the repair. It does not reset or overwrite an existing case directory.

## Known Issues

- Only the email case was exercised with the real model; discount and regression retry were verified with scripted mock tool choices.
- `apply_patch` intentionally supports one strict unified Git diff syntax and existing tracked files only. The real model's first patch call used another syntax and received a structured error before succeeding.
- Pytest output parsing is best effort; unusual formats may leave counts or exception details `null`.
- The toy repositories are small and do not establish reliability on real-world issues.
- Passing tests remain an imperfect correctness oracle; repair diffs should still be reviewed before using a result as evaluation data.

## Day 2 Done Gate

**PASS.** The constrained patch and pytest tools work, the repair loop retries after failure with explicit budgets, metrics are recorded, all 44 current tests pass, and one real model repair applied a patch and passed full pytest.

## Day 3 Entry

Day 2's controlled repair loop is ready for a frozen naive baseline. Next: compare model-driven browsing with static repository retrieval under the same model, tasks, and budgets.
