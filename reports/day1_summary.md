# RepoPilot Day 1 Summary

## Implemented

Minimal read-only agent runtime: provider-neutral model types, OpenAI-compatible HTTP backend, mock backend, runtime detection, bounded agent loop, four repository tools, CLI, JSONL trajectory, and summary JSON.

## Architecture

`Issue + RuntimeContext -> ModelBackend -> AgentLoop -> ToolRegistry -> observations -> ModelBackend -> final analysis`. The loop owns state and budgets; tools share a workspace boundary; the backend is replaceable without changing the loop.

## Runtime Detection

Observed in the `hello-agent` smoke environment: Windows 10.0.26200, PowerShell, Python 3.10.21, Windows path separator, Git available, ripgrep available. RuntimeContext uses the selected workspace as its working directory in the prompt. A shell that cannot be identified is reported as `unknown`. The default Anaconda environment has Python 3.13.9; the project supports Python 3.10+.

## Tools

- `list_files`: bounded, workspace-relative listing with generated directories and credential files excluded.
- `search_code`: bounded literal search with ripgrep and Python fallback.
- `read_file`: numbered ranges, maximum 250 lines per call.
- `git_diff`: read-only working-tree diff or stat; structured error when no Git repo is present.

All file paths are resolved and checked against the workspace after symlink resolution. Tool errors become observations.

## Tests

`hello-agent` Python running `-m pytest -q --basetemp=.test_tmp_hello -p no:cacheprovider`: **14 passed**. Covers runtime detection, boundary rejection, listing, search and fallback, line slicing, Git and non-Git behavior, backend parsing, mock loop, tool failure, and budgets. This environment required an approved test process because the default sandbox could not create pytest temporary directories.

## Mock Smoke

**PASS**. `python -m examples.mock_smoke` completed in 4 model steps with scripted calls `list_files -> search_code -> read_file`, read `app/email_utils.py`, produced a cause hypothesis, and wrote a trajectory under `runs/`. The final answer is scripted; this run validates orchestration, tools, and logging rather than model reasoning.

## Real API Smoke

**PASS after explicit user authorization.** The local `.env` supplied credentials for `https://api-inference.modelscope.cn/v1/chat/completions`. Automatic review initially rejected the smoke twice because it would send toy-repository content and runtime metadata externally. The user then explicitly approved that concrete transfer; one request sequence was run in `hello-agent`.

- Issue: `normalize_email()` preserves uppercase domain characters in `User@Example.COM`; locate the implementation and cause.
- Provider/model: ModelScope / `Qwen/Qwen3.8-Flash-Next`.
- Status: completed; 3 model steps, 6 tool calls, no tool errors.
- Tool sequence: `search_code -> list_files -> read_file -> read_file -> read_file -> read_file`.
- Files read: `app/email_utils.py`, `app/service.py`, `app/users.py`, `tests/test_email_utils.py`.
- Usage: 3,451 input tokens and 774 output tokens, as reported by the endpoint.
- Total agent latency: 12.9122 seconds.
- Final result: identified `app/email_utils.py:4-6`; the return statement reuses `domain` unchanged, whereas `tests/test_email_utils.py` expects a lowercase domain. The response proposed inspecting or testing the minimal `domain.lower()` change and correctly stated that no files were modified.

## Trajectory Example

The real run generated `runs/cc67f43fb4754b0d85da44d1adfc88a2/trajectory.jsonl` and `summary.json`; the offline mock run also generated `runs/bed70d10cef540e4ac071e444c0d710b/`. Each model event records step, model, usage if returned, latency, content, and tool calls. Each tool event records name, arguments, success, observation, metadata, and latency. The final summary records status, counts, files seen/modified, timestamps, and total latency. API keys and headers are never logged by the backend.

## Known Issues

- Only one real toy-repository smoke was run. It demonstrates the read-only path, not reliability across tasks.
- Only Python 3.10.21 and 3.13.9 have been exercised locally; other supported 3.10+ versions have not been separately tested.
- This repository has not been initialized as Git, so `git_diff` returns a structured non-repository error here; it is tested against a temporary Git repository.
- `.env` parsing is intentionally minimal. No dependency on a dotenv package.

## Day 2 Entry

Implement bounded `apply_patch` and `run_tests`, then test the inspect/patch/test/retry loop. Preserve the current backend, tool, state, and trajectory boundaries.
