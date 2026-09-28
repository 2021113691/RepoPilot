# RepoPilot

RepoPilot is a repository-level coding agent project focused on context engineering. **Current status: Day 1 Minimal Agent.** The implemented runtime can inspect a repository with read-only tools and record an agent trajectory. It does not edit or test code. One real ModelScope toy-repository smoke has completed.

## Architecture

```text
Issue + RuntimeContext -> ModelBackend -> AgentLoop
                                      -> ToolRegistry
                                         | list_files
                                         | search_code
                                         | read_file
                                         | git_diff
                         observations -> ModelBackend -> final analysis
                         events       -> runs/<task_id>/trajectory.jsonl
```

`ModelBackend` returns provider-neutral `ModelResponse`, `ToolCall`, and `TokenUsage` objects. The current real backend uses Python's standard library to call an OpenAI Chat Completions-compatible endpoint. `MockBackend` provides scripted responses for offline tests and smoke runs. The loop limits model steps and tool calls, and treats tool failures as observations.

## Installation

Requires Python 3.10 or newer. No runtime packages are required.

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

Run an offline scripted smoke first:

```powershell
python -m examples.mock_smoke
```

For a real endpoint, after configuring credentials:

```powershell
python -m repopilot --workspace examples/toy_repo --issue "normalize_email() preserves uppercase characters in the domain of User@Example.COM. Find the cause."
```

Other CLI flags: `--max-steps`, `--max-tool-calls`, `--model`, `--base-url`, and `--runs-dir`. The default limits are 20 model steps and 40 tool calls. Every run writes `trajectory.jsonl` and `summary.json` under `runs/<task_id>/`. Token counts remain `null` if the endpoint does not supply usage.

## Available tools

| Tool | Behavior |
| --- | --- |
| `list_files` | Lists relative paths with depth and entry limits; skips common generated directories and credential files. |
| `search_code` | Literal search with ripgrep, or a Python fallback; returns relative paths and line numbers. |
| `read_file` | Reads numbered line ranges, at most 250 lines per call. |
| `git_diff` | Shows `git diff` or `git diff --stat` when the workspace is the Git root. |

File paths are resolved against the workspace and rejected when they escape it, including symlink targets. Tool errors return structured failures to the model. Credential files such as `.env` are excluded from repository tools. The toy repository intentionally contains a domain-normalization bug for inspection; Day 1 does not fix it.

## Current limitations

- The real API has been exercised once with a toy repository; this is a smoke check, not a reliability or benchmark result. See [Day 1 report](reports/day1_summary.md).
- Python 3.10.21 in the `hello-agent` environment has passed the Day 1 tests and real API smoke.
- No patch, test-running, arbitrary shell, retrieval, or context management tools exist yet.
- The lightweight `.env` reader supports simple `KEY=VALUE` lines, not the full dotenv format.
- Shell detection uses the Windows parent process without extra packages, or optional `psutil` on other systems. It reports `unknown` if no reliable signal exists; `REPOPILOT_SHELL` can supply an explicit value.
- `git_diff` reads working-tree changes only and requires the workspace itself to be the Git root.
