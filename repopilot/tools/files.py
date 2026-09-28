"""Bounded file listing and line-range reading."""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolResult, is_sensitive_name


IGNORED = {".git", ".env", "__pycache__", ".venv", "venv", "node_modules", "dist", "build"}


class ListFiles(Tool):
    name = "list_files"
    description = "List workspace-relative files and directories with depth and entry limits."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative directory", "default": "."},
            "max_depth": {"type": "integer", "minimum": 0, "maximum": 10, "default": 3},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        },
    }

    def _execute(self, path: str = ".", max_depth: int = 3, max_entries: int = 100) -> ToolResult:
        if not isinstance(max_depth, int) or not 0 <= max_depth <= 10:
            raise ValueError("max_depth must be 0..10")
        if not isinstance(max_entries, int) or not 1 <= max_entries <= 500:
            raise ValueError("max_entries must be 1..500")
        root = self.resolve_path(path)
        if not root.is_dir():
            return ToolResult(False, error="directory not found")
        entries: list[str] = []
        truncated = False

        def walk(directory: Path, depth: int) -> None:
            nonlocal truncated
            if depth > max_depth or truncated:
                return
            for child in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
                if child.name in IGNORED or is_sensitive_name(child.name):
                    continue
                resolved = child.resolve()
                if not resolved.is_relative_to(self.workspace):
                    continue
                if len(entries) >= max_entries:
                    truncated = True
                    return
                is_dir = child.is_dir()
                entries.append(f"{self.relative(child)}/" if is_dir else self.relative(child))
                if is_dir and not child.is_symlink():
                    walk(child, depth + 1)

        walk(root, 0)
        return ToolResult(True, "\n".join(entries) or "(empty)", metadata={"count": len(entries), "truncated": truncated})


class ReadFile(Tool):
    name = "read_file"
    description = "Read up to 250 numbered lines from a workspace-relative text file."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1, "default": 1},
            "end_line": {"type": "integer", "minimum": 1, "description": "Inclusive line number"},
        },
        "required": ["path"],
    }

    def _execute(self, path: str, start_line: int = 1, end_line: int | None = None) -> ToolResult:
        if not isinstance(start_line, int) or start_line < 1:
            raise ValueError("start_line must be positive")
        if end_line is None:
            end_line = start_line + 249
        if not isinstance(end_line, int) or end_line < start_line or end_line - start_line >= 250:
            raise ValueError("line range must contain 1..250 lines")
        target = self.resolve_path(path)
        if not target.is_file():
            return ToolResult(False, error="file not found")
        lines: list[str] = []
        more = False
        with target.open("r", encoding="utf-8-sig") as stream:
            for number, line in enumerate(stream, 1):
                if number < start_line:
                    continue
                if number > end_line:
                    more = True
                    break
                lines.append(f"{number} | {line.rstrip()}")
        if not lines:
            return ToolResult(False, error="start_line exceeds file length")
        text = "\n".join(lines)
        if more:
            text += f"\n[More lines available; continue from line {end_line + 1}]"
        return ToolResult(True, text, metadata={"file": self.relative(target), "start_line": start_line, "end_line": start_line + len(lines) - 1, "truncated": more})
