"""Bounded pytest execution and compact failure evidence."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .base import Tool, ToolResult


@dataclass(frozen=True)
class TestResult:
    success: bool
    exit_code: int | None
    stdout: str
    stderr: str
    failed_tests: list[str]
    passed_count: int | None
    failed_count: int | None
    duration: float
    timeout: bool
    exception_type: str | None
    assertion_message: str | None
    traceback_locations: list[str]


def _safe_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    # A test may echo a credential that was already in the parent process.
    for key, secret in os.environ.items():
        if any(term in key.upper() for term in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION")) and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    return value[:100_000]


def parse_pytest_output(stdout: str, stderr: str, exit_code: int | None, duration: float, timeout: bool) -> TestResult:
    text = stdout + "\n" + stderr
    failed_tests = re.findall(r"^FAILED (.+?)(?: - |$)", text, re.MULTILINE)[:20]
    passed = re.search(r"\b(\d+) passed\b", text)
    failed = re.search(r"\b(\d+) failed\b", text)
    primary = re.search(r"^E\s+([\w.]*?(?:Error|Exception))(?::\s*(.*))?", text, re.MULTILINE)
    if not primary:
        primary = re.search(r"^FAILED .+? - ([\w.]*?(?:Error|Exception))(?::\s*(.*))?", text, re.MULTILINE)
    assertion = re.search(r"^E\s+(assert\b.*)", text, re.MULTILINE) if not primary else None
    locations = []
    for path, line in re.findall(r"(?m)^\s*([\w./\\-]+\.py):(\d+):?", text):
        location = f"{path.replace(chr(92), '/')}:{line}"
        if location not in locations:
            locations.append(location)
        if len(locations) >= 8:
            break
    return TestResult(
        success=exit_code == 0 and not timeout,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        failed_tests=failed_tests,
        passed_count=int(passed.group(1)) if passed else None,
        failed_count=int(failed.group(1)) if failed else None,
        duration=duration,
        timeout=timeout,
        exception_type=primary.group(1) if primary else ("AssertionError" if assertion else None),
        assertion_message=primary.group(2) if primary and primary.group(2) else (assertion.group(1) if assertion else None),
        traceback_locations=locations,
    )


def compact_observation(result: TestResult, scope: str | None) -> str:
    lines = [f"Test Result: {'PASSED' if result.success else 'FAILED'}", f"Scope: {scope or 'full pytest'}"]
    if result.passed_count is not None or result.failed_count is not None:
        lines.append(f"Counts: passed={result.passed_count}, failed={result.failed_count}")
    if result.failed_tests:
        lines.append("Failed tests:")
        lines.extend(f"- {node}" for node in result.failed_tests[:10])
    if result.exception_type:
        lines.append(f"Primary error: {result.exception_type}: {result.assertion_message or ''}".rstrip())
    if result.traceback_locations:
        lines.append("Relevant traceback:")
        lines.extend(result.traceback_locations[:6])
    if result.timeout:
        lines.append("Test execution timed out.")
    if not result.success and not result.failed_tests and not result.exception_type:
        lines.append("Output excerpt: " + (result.stderr or result.stdout)[-800:].replace("\n", " "))
    if result.success and scope:
        lines.append("Targeted tests passed. Run full pytest before claiming the repair is verified.")
    lines.append(f"Exit code: {result.exit_code}")
    return "\n".join(lines)[:3000]


class RunTests(Tool):
    name = "run_tests"
    description = "Run full pytest, a workspace-relative test path, or a pytest test node. No shell commands."
    parameters = {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "description": "Optional tests/test_file.py or tests/test_file.py::test_name"},
            "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60},
        },
    }

    def _execute(self, scope: str | None = None, timeout_sec: int = 60) -> ToolResult:
        if not isinstance(timeout_sec, int) or not 1 <= timeout_sec <= 120:
            return ToolResult(False, error="invalid timeout: expected 1..120 seconds")
        try:
            node = self._validate_scope(scope)
        except ValueError as exc:
            return ToolResult(False, error=f"invalid pytest scope: {exc}")
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        if node:
            command.append(node)
        environment = os.environ.copy()
        for key in list(environment):
            if any(term in key.upper() for term in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION")):
                environment.pop(key)
        environment.pop("PYTEST_ADDOPTS", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        begin = time.perf_counter()
        try:
            process = subprocess.run(
                command, cwd=self.workspace, env=environment, shell=False,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_sec,
            )
            stdout, stderr, code, timed_out = process.stdout, process.stderr, process.returncode, False
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, code, timed_out = exc.stdout, exc.stderr, None, True
        duration = round(time.perf_counter() - begin, 4)
        result = parse_pytest_output(_safe_output(stdout), _safe_output(stderr), code, duration, timed_out)
        return ToolResult(
            result.success, compact_observation(result, node),
            error=None if result.success else ("tests_timeout" if result.timeout else "tests_failed"),
            metadata={"test_result": asdict(result), "scope": node},
        )

    def _validate_scope(self, scope: str | None) -> str | None:
        if scope is None:
            return None
        if not isinstance(scope, str) or not scope or len(scope) > 300:
            raise ValueError("scope must be a nonempty path or node ID")
        scope = scope.replace("\\", "/")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+(?:::[A-Za-z_][A-Za-z0-9_]*)*", scope):
            raise ValueError("shell operators and unsupported node syntax are forbidden")
        path_text, *nodes = scope.split("::")
        if path_text.startswith(("/", "-")) or any(part in ("", ".", "..") for part in path_text.split("/")):
            raise ValueError("unsafe path")
        path = self.resolve_path(path_text)
        if not path.exists() or (not path.is_file() and not path.is_dir()):
            raise ValueError("test path does not exist")
        if nodes and (not path.is_file() or path.suffix != ".py"):
            raise ValueError("node ID requires a Python test file")
        if path.is_file() and path.suffix != ".py":
            raise ValueError("test file must end with .py")
        return path.relative_to(self.workspace).as_posix() + ("::" + "::".join(nodes) if nodes else "")
