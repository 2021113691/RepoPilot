"""Read-only Git diff for a workspace that is itself a Git repository."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import Tool, ToolResult


class GitDiff(Tool):
    name = "git_diff"
    description = "Show the working-tree Git diff or its stat, read-only."
    parameters = {
        "type": "object",
        "properties": {"stat": {"type": "boolean", "default": False}},
    }

    def _execute(self, stat: bool = False) -> ToolResult:
        if not isinstance(stat, bool):
            raise ValueError("stat must be boolean")
        git = shutil.which("git")
        if not git:
            return ToolResult(False, error="git unavailable")
        top = subprocess.run([git, "rev-parse", "--show-toplevel"], cwd=self.workspace, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if top.returncode != 0:
            return ToolResult(False, error="workspace is not a Git repository")
        if self.workspace != Path(top.stdout.strip()).resolve():
            return ToolResult(False, error="workspace is not the Git repository root")
        command = [git, "-c", "core.pager=cat", "diff", "--no-ext-diff", "--no-color"]
        if stat:
            command.append("--stat")
        result = subprocess.run(command, cwd=self.workspace, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if result.returncode != 0:
            return ToolResult(False, error=f"git diff exited with code {result.returncode}")
        return ToolResult(True, result.stdout[:30000] or "(no diff)", metadata={"truncated": len(result.stdout) > 30000})
