"""Bounded, provider-neutral inspection and repair agent loop."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from repopilot.agent.prompts import system_prompt
from repopilot.agent.state import AgentState, utc_now
from repopilot.evaluation.trajectory import TrajectoryLogger
from repopilot.models.base import ModelBackend, ModelResponse
from repopilot.runtime.environment import RuntimeContext
from repopilot.tools.base import ToolRegistry, ToolResult
from repopilot.tools.files import ListFiles, ReadFile
from repopilot.tools.git import GitDiff
from repopilot.tools.patch import ApplyPatch
from repopilot.tools.policy import RepairPolicy
from repopilot.tools.search import SearchCode
from repopilot.tools.tests import RunTests


def default_tools(workspace: Path, repair: bool = False, repair_policy: RepairPolicy | None = None) -> ToolRegistry:
    tools = [ListFiles(workspace), SearchCode(workspace), ReadFile(workspace), GitDiff(workspace)]
    if repair:
        tools.extend([ApplyPatch(workspace, policy=repair_policy), RunTests(workspace)])
    return ToolRegistry(tools)


class AgentLoop:
    def __init__(
        self,
        backend: ModelBackend,
        workspace: Path,
        runs_dir: Path = Path("runs"),
        max_steps: int = 20,
        max_tool_calls: int = 40,
        repair: bool = False,
        max_patch_attempts: int = 5,
        max_test_runs: int = 5,
        repair_policy: RepairPolicy | None = None,
    ):
        if min(max_steps, max_tool_calls, max_patch_attempts, max_test_runs) < 1:
            raise ValueError("budgets must be positive")
        self.backend = backend
        self.workspace = workspace.resolve(strict=True)
        self.repair = repair
        self.tools = default_tools(self.workspace, repair, repair_policy)
        self.runs_dir = runs_dir
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_patch_attempts = max_patch_attempts
        self.max_test_runs = max_test_runs

    def run(self, issue: str) -> AgentState:
        if not issue.strip():
            raise ValueError("issue must not be empty")
        state = AgentState(issue=issue, workspace=self.workspace, repair_mode=self.repair)
        logger = TrajectoryLogger(self.runs_dir, state.task_id)
        runtime = RuntimeContext.detect(self.workspace)
        run_start = time.perf_counter()
        state.messages = [
            {"role": "system", "content": system_prompt(runtime, repair=self.repair)},
            {"role": "user", "content": issue},
        ]
        logger.event(
            "start", task_id=state.task_id, issue=issue, runtime=runtime.__dict__,
            repair_mode=self.repair, max_steps=self.max_steps, max_tool_calls=self.max_tool_calls,
            max_patch_attempts=self.max_patch_attempts, max_test_runs=self.max_test_runs,
        )
        state.status = "running"
        try:
            if self.repair:
                baseline_error = self._baseline_error()
                if baseline_error:
                    state.status = "tool_error"
                    state.error = baseline_error
                    logger.event("baseline_rejected", error=baseline_error)
                    return state
            for step in range(1, self.max_steps + 1):
                state.step_count = step
                begin = time.perf_counter()
                response = self.backend.chat(state.messages, self.tools.schemas())
                latency = time.perf_counter() - begin
                state.llm_calls += 1
                state.add_usage(response.token_usage)
                logger.event(
                    "llm_call", step=step, model=response.model, input_tokens=response.token_usage.input_tokens,
                    output_tokens=response.token_usage.output_tokens, latency_sec=round(latency, 4),
                    finish_reason=response.finish_reason, content=response.content,
                    tool_calls=[{"id": call.id, "name": call.name, "arguments": call.arguments} for call in response.tool_calls],
                )
                state.messages.append(self._assistant_message(response))
                if not response.tool_calls:
                    if not self.repair:
                        state.final_answer = response.content or ""
                        state.status = "completed"
                        break
                    if self._verified(state):
                        state.final_answer = response.content or "Tests passed after the latest patch."
                        state.status = "tests_passed"
                        break
                    reminder = self._verification_reminder(state)
                    state.messages.append({"role": "user", "content": reminder})
                    logger.event("verification_required", step=step, reason=reminder)
                    state.final_answer = response.content or ""
                    continue
                for call in response.tool_calls:
                    budget_error = self._budget_error(state, call.name)
                    if budget_error:
                        state.status = "budget_exhausted"
                        state.error = budget_error
                        logger.event("budget_exhausted", step=step, reason=budget_error)
                        break
                    if self.repair and call.name == "apply_patch" and state.patch_count > 0 and state.last_test_result and not state.last_test_result.get("success"):
                        logger.event("repair_retry", step=step, failed_tests=state.last_test_result.get("failed_tests", []))
                    result = self.tools.execute(call.name, call.arguments)
                    state.tool_calls += 1
                    state.tool_sequence.append(call.name)
                    if result.success and call.name == "read_file" and "file" in result.metadata:
                        state.files_seen.add(result.metadata["file"])
                    if self.repair:
                        self._track_repair(state, logger, step, call.name, call.arguments, result)
                    state.messages.append({"role": "tool", "tool_call_id": call.id, "content": result.observation()})
                    logger.event(
                        "tool_call", step=step, tool=call.name, arguments=call.arguments,
                        success=result.success, error=result.error, observation=result.observation(),
                        metadata=result.metadata, latency_sec=round(result.duration, 4),
                    )
                if state.status == "budget_exhausted":
                    break
            else:
                if self.repair and self._verified(state):
                    state.status = "tests_passed"
                    state.final_answer = self._verified_summary(state)
                else:
                    state.status = "budget_exhausted"
        except Exception as exc:
            state.status = "model_error" if self.repair else "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            logger.event("error", step=state.step_count, error=state.error)
        finally:
            if self.repair and state.status != "tests_passed":
                state.final_answer = self._incomplete_answer(state)
            state.finished_at = utc_now()
            state.latency_sec = round(time.perf_counter() - run_start, 4)
            logger.event("finish", status=state.status, steps=state.step_count)
            logger.finish(state)
        return state

    def _baseline_error(self) -> str | None:
        git = shutil.which("git")
        if not git:
            return "Repair requires Git, but git is unavailable."
        top = subprocess.run([git, "rev-parse", "--show-toplevel"], cwd=self.workspace, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != self.workspace:
            return "Repair workspace must be the root of a Git repository."
        status = subprocess.run([git, "status", "--porcelain", "--untracked-files=all"], cwd=self.workspace, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if status.returncode != 0:
            return "Could not read Git status for the repair workspace."
        if status.stdout.strip():
            return "Repair workspace has existing changes; start from a clean Git baseline."
        return None

    def _budget_error(self, state: AgentState, name: str) -> str | None:
        if state.tool_calls >= self.max_tool_calls:
            return "maximum tool calls reached"
        if self.repair and name == "apply_patch" and state.repair_attempts >= self.max_patch_attempts:
            return "maximum patch attempts reached"
        if self.repair and name == "run_tests" and state.test_runs >= self.max_test_runs:
            return "maximum test runs reached"
        return None

    def _track_repair(self, state: AgentState, logger: TrajectoryLogger, step: int, name: str, arguments: dict, result: ToolResult) -> None:
        if name == "apply_patch":
            state.repair_attempts += 1
            if result.success:
                state.patch_count += 1
                state.files_modified = set(result.metadata.get("files_modified", []))
                state.changed_loc = result.metadata.get("changed_loc", 0)
                state.current_diff = result.metadata.get("current_diff")
                state.tests_passed = False
                state.last_full_pass_patch_count = -1
                state.diff_reviewed_patch_count = -1
            logger.event("patch_apply", step=step, success=result.success, files=result.metadata.get("patch_files", []), error=result.error, changed_loc=state.changed_loc)
            reason = result.metadata.get("error_code")
            if reason in {"protected_path", "not_writable"}:
                if reason == "protected_path":
                    state.protected_patch_rejections += 1
                logger.event("patch_rejected", step=step, reason=reason, paths=result.metadata.get("paths", []))
        elif name == "git_diff" and result.success and not arguments.get("stat", False):
            state.current_diff = result.content
            state.diff_reviewed_patch_count = state.patch_count
        elif name == "run_tests" and "test_result" in result.metadata:
            test = result.metadata["test_result"]
            state.test_runs += 1
            state.last_test_result = test
            if result.success and result.metadata.get("scope") is None and state.patch_count > 0:
                state.tests_passed = True
                state.last_full_pass_patch_count = state.patch_count
            if not result.success:
                state.test_failures += 1
                state.tests_passed = False
            logger.event(
                "test_run", step=step, scope=result.metadata.get("scope"), success=result.success,
                failed_tests=test.get("failed_tests", []), duration_sec=test.get("duration"), exit_code=test.get("exit_code"),
            )
            if not result.success:
                logger.event(
                    "test_failure", step=step, failed_tests=test.get("failed_tests", []),
                    exception_type=test.get("exception_type"), traceback_locations=test.get("traceback_locations", []),
                )

    @staticmethod
    def _verified(state: AgentState) -> bool:
        return (
            state.patch_count > 0 and state.tests_passed
            and state.last_full_pass_patch_count == state.patch_count
            and state.diff_reviewed_patch_count == state.patch_count
        )

    @staticmethod
    def _verification_reminder(state: AgentState) -> str:
        if state.patch_count == 0:
            return "Repair incomplete: inspect the code, apply a patch, then inspect the diff and run tests."
        if state.diff_reviewed_patch_count != state.patch_count:
            return "Repair unverified: inspect the latest patch with git_diff, then run full pytest."
        if state.last_full_pass_patch_count != state.patch_count:
            return "Repair unverified: run full pytest after the latest patch; use any failure to revise the patch."
        return "Repair unverified: inspect the diff and rerun full pytest."

    @staticmethod
    def _incomplete_answer(state: AgentState) -> str:
        failed = (state.last_test_result or {}).get("failed_tests", [])
        lines = ["Repair incomplete.", f"Status: {state.status}."]
        if state.error:
            lines.append(f"Reason: {state.error}.")
        lines.append("Last failing tests: " + (", ".join(failed) if failed else "none recorded"))
        if state.final_answer:
            lines.append("Current hypothesis (unverified): " + state.final_answer[:2000])
        else:
            lines.append("Current hypothesis: not established; inspect the latest diff and test evidence.")
        return "\n".join(lines)

    @staticmethod
    def _verified_summary(state: AgentState) -> str:
        return (
            "Verified repair at the model-step limit.\n"
            f"Issue/root-cause context: {state.issue}\n"
            f"Files changed: {', '.join(sorted(state.files_modified))}.\n"
            f"Full pytest passed after the latest patch (exit code {(state.last_test_result or {}).get('exit_code')}).\n"
            "The latest diff was inspected; see current_diff in the run summary for exact changes."
        )

    @staticmethod
    def _assistant_message(response: ModelResponse) -> dict:
        message: dict = {"role": "assistant", "content": response.content}
        if response.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)}}
                for call in response.tool_calls
            ]
        return message
