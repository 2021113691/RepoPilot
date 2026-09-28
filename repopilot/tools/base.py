"""Tool contracts and workspace path checks."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class WorkspaceViolation(ValueError):
    pass


@dataclass(frozen=True)
class ToolResult:
    success: bool
    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    duration: float = 0.0

    def observation(self) -> str:
        return self.content if self.success else f"ERROR: {self.error or 'tool failed'}"


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be a directory")

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }

    def resolve_path(self, path: str = ".") -> Path:
        target = (self.workspace / path).resolve()
        try:
            relative = target.relative_to(self.workspace)
        except ValueError:
            raise WorkspaceViolation("workspace boundary violation") from None
        if any(part == ".git" or part == ".env" or part.startswith(".env.") or part.endswith((".pem", ".key")) for part in relative.parts):
            raise ValueError("sensitive path is not available to repository tools")
        return target

    def relative(self, path: Path) -> str:
        return path.relative_to(self.workspace).as_posix()

    def execute(self, **kwargs: Any) -> ToolResult:
        start = time.perf_counter()
        try:
            result = self._execute(**kwargs)
        except WorkspaceViolation:
            result = ToolResult(False, error="workspace boundary violation")
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            result = ToolResult(False, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(result.success, result.content, result.error, result.metadata, time.perf_counter() - start)

    @abstractmethod
    def _execute(self, **kwargs: Any) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self.tools = {tool.name: tool for tool in tools}

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self.tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(False, error=f"unknown tool: {name}")
        try:
            return tool.execute(**arguments)
        except Exception as exc:
            # Tool failures are observations, not agent crashes.
            return ToolResult(False, error=f"tool failed: {type(exc).__name__}: {exc}")
