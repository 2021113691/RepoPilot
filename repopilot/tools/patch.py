"""Apply a bounded unified diff to existing tracked workspace files."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from .base import Tool, ToolResult


class ApplyPatch(Tool):
    name = "apply_patch"
    description = (
        "Apply a standard unified Git diff to existing tracked files. "
        "Supply diff --git, ---/+++, and @@ hunk lines; do not use Markdown fences. "
        "No file creation, deletion, rename, binary patch, or shell command is allowed."
    )
    parameters = {
        "type": "object",
        "properties": {"patch": {"type": "string", "description": "Unified Git diff text, including diff --git and hunk headers"}},
        "required": ["patch"],
    }

    def _execute(self, patch: str) -> ToolResult:
        if not isinstance(patch, str) or not patch.strip() or len(patch) > 50_000:
            return ToolResult(False, error="structured patch failure: patch must be 1..50000 characters")
        patch = patch.replace("\r\n", "\n")
        if not patch.endswith("\n"):
            patch += "\n"
        try:
            targets = self._targets(patch)
        except ValueError as exc:
            return ToolResult(False, error=f"structured patch failure: {exc}")
        git = shutil.which("git")
        if not git:
            return ToolResult(False, error="structured patch failure: git unavailable")
        top = self._git(git, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != self.workspace:
            return ToolResult(False, error="structured patch failure: workspace must be a Git repository root")
        paths = []
        for name in targets:
            if (self.workspace / name).is_symlink():
                return ToolResult(False, error=f"structured patch failure: symlink target is unsupported: {name}")
            try:
                path = self.resolve_path(name)
            except ValueError as exc:
                return ToolResult(False, error=f"structured patch failure: {exc}")
            if not path.is_file() or path.is_symlink():
                return ToolResult(False, error=f"structured patch failure: target must be an existing regular file: {name}")
            if self._git(git, "ls-files", "--error-unmatch", "--", name).returncode != 0:
                return ToolResult(False, error=f"structured patch failure: target is not tracked: {name}")
            paths.append(path)
        before = {path: path.read_bytes() for path in paths}
        check = self._git(git, "apply", "--whitespace=error", "--check", "-", input=patch)
        if check.returncode != 0:
            detail = check.stderr.strip()[:500]
            return ToolResult(False, error=f"structured patch failure: git apply --check rejected the patch: {detail}")
        applied = self._git(git, "apply", "--whitespace=error", "-", input=patch)
        if applied.returncode != 0:
            return ToolResult(False, error="structured patch failure: git apply rejected the patch")
        diff = self._git(git, "diff", "--no-ext-diff", "--no-color")
        numstat = self._git(git, "diff", "--numstat", "--no-ext-diff")
        if diff.returncode != 0 or numstat.returncode != 0:
            for path, data in before.items():
                path.write_bytes(data)
            return ToolResult(False, error="structured patch failure: post-apply diff failed; target files restored")
        modified: list[str] = []
        added = deleted = 0
        for line in numstat.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            plus, minus, name = parts
            modified.append(name)
            if plus.isdigit() and minus.isdigit():
                added += int(plus)
                deleted += int(minus)
        if not set(targets).issubset(set(modified)):
            for path, data in before.items():
                path.write_bytes(data)
            return ToolResult(False, error="structured patch failure: expected file change was missing; target files restored")
        return ToolResult(
            True,
            f"Applied patch to {', '.join(targets)}. Inspect the current diff and run tests.\n" + diff.stdout[:6000],
            metadata={
                "patch_files": targets,
                "files_modified": modified,
                "added_lines": added,
                "deleted_lines": deleted,
                "changed_loc": added + deleted,
                "current_diff": diff.stdout[:30000],
            },
        )

    def _targets(self, patch: str) -> list[str]:
        lines = patch.splitlines()
        if not lines[0].startswith("diff --git "):
            raise ValueError("expected a unified Git diff beginning with diff --git")
        blocks: list[list[str]] = []
        for line in lines:
            if line.startswith("diff --git "):
                blocks.append([])
            if not blocks:
                raise ValueError("unexpected text before the first file header")
            blocks[-1].append(line)
        if len(blocks) > 5:
            raise ValueError("patch may modify at most five files")
        targets: list[str] = []
        for block in blocks:
            header = re.fullmatch(r"diff --git a/([^\s]+) b/([^\s]+)", block[0])
            if not header or header.group(1) != header.group(2):
                raise ValueError("only updates to one existing path per file are supported")
            name = header.group(1)
            posix = PurePosixPath(name)
            if name.startswith("/") or "\\" in name or ":" in name or any(part in ("", ".", "..") for part in name.split("/")) or posix.is_absolute():
                raise ValueError("unsafe patch path")
            if name in targets:
                raise ValueError("duplicate file in patch")
            old_headers = [line for line in block if line.startswith("--- ")]
            new_headers = [line for line in block if line.startswith("+++ ")]
            if old_headers != [f"--- a/{name}"] or new_headers != [f"+++ b/{name}"]:
                raise ValueError("expected exactly one matching --- and +++ file header")
            if not any(line.startswith("@@ ") for line in block):
                raise ValueError("patch has no unified diff hunk")
            if any(line.startswith(("new file mode", "deleted file mode", "old mode", "new mode", "rename ", "copy ", "GIT binary patch", "Binary files", "similarity index")) for line in block):
                raise ValueError("file creation, deletion, rename, mode, and binary changes are unsupported")
            targets.append(name)
        return targets

    def _git(self, git: str, *args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
        if input is not None:
            # Text-mode stdin translates LF to CRLF on Windows, which breaks
            # patch context matching against LF files. Send UTF-8 bytes intact.
            result = subprocess.run(
                [git, *args], input=input.encode("utf-8"), cwd=self.workspace,
                capture_output=True, timeout=10,
            )
            return subprocess.CompletedProcess(
                result.args, result.returncode,
                result.stdout.decode("utf-8", errors="replace"),
                result.stderr.decode("utf-8", errors="replace"),
            )
        return subprocess.run(
            [git, *args], cwd=self.workspace, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=10,
        )
