# RepoPilot

RepoPilot is a repository-level coding agent project focused on context engineering. **Current status: Day 2 controlled repair loop.** It can inspect a Git repository, apply a constrained patch, run pytest, use failures to retry, and record the trajectory. Retrieval and context optimization are future work.

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

Repair summaries record patch attempts and successful patches, test runs and failures, modified files, added/deleted lines, changed LOC, model/tool calls, tokens, and latency. The full suite must pass after the latest patch before the agent reports `tests_passed`.

## Current limitations

- One real Day 2 repair smoke passed on the email toy case. The discount retry and regression scenarios have deterministic mock coverage. See [Day 2 report](reports/day2_summary.md).
- The patch tool supports existing tracked files and standard unified Git diffs; it does not create, delete, or rename files.
- There is no arbitrary shell, repository retrieval, symbol graph, context budget, or benchmark integration yet.
- The lightweight `.env` reader supports simple `KEY=VALUE` lines, not the full dotenv format.
- Shell detection uses the Windows parent process without extra packages, or optional `psutil` on other systems. It reports `unknown` if no reliable signal exists; `REPOPILOT_SHELL` can supply an explicit value.
- `git_diff` reads working-tree changes only and requires the workspace itself to be the Git root.
