"""Literal code search with ripgrep and a standard-library fallback."""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path

from .base import Tool, ToolResult
from .files import IGNORED


class SearchCode(Tool):
    name = "search_code"
    description = "Search for a literal string in workspace files; returns relative file and line numbers."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "glob": {"type": "string", "description": "Optional file pattern, e.g. *.py"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
        },
        "required": ["query"],
    }

    def _execute(self, query: str, path: str = ".", glob: str | None = None, max_results: int = 30) -> ToolResult:
        if not isinstance(query, str) or not query or len(query) > 500 or "\n" in query:
            raise ValueError("query must be a nonempty single line up to 500 characters")
        if not isinstance(max_results, int) or not 1 <= max_results <= 100:
            raise ValueError("max_results must be 1..100")
        if glob is not None and (not isinstance(glob, str) or ".." in glob or "\\" in glob):
            raise ValueError("invalid glob")
        root = self.resolve_path(path)
        if not root.exists():
            return ToolResult(False, error="search path not found")
        if root.is_dir() and root.is_symlink():
            return ToolResult(False, error="searching symlink directories is unavailable")
        rg = shutil.which("rg")
        if rg:
            return self._ripgrep(rg, query, root, glob, max_results)
        return self._fallback(query, root, glob, max_results)

    def _ripgrep(self, rg: str, query: str, root: Path, glob: str | None, max_results: int) -> ToolResult:
        command = [rg, "--fixed-strings", "--line-number", "--no-heading", "--color", "never", "--with-filename", "--glob", "!.env", "--glob", "!.env.*", "--glob", "!*.pem", "--glob", "!*.key"]
        if glob:
            command += ["--glob", glob]
        command += ["--", query, self.relative(root) or "."]
        process = subprocess.Popen(command, cwd=self.workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        matches: list[str] = []
        try:
            assert process.stdout is not None
            for raw in process.stdout:
                parts = raw.rstrip("\n").split(":", 2)
                if len(parts) != 3:
                    continue
                candidate = self.resolve_path(parts[0])
                if not candidate.is_file():
                    continue
                matches.append(f"{self.relative(candidate)}:{parts[1]}: {parts[2][:500]}")
                if len(matches) >= max_results:
                    process.kill()
                    break
            process.communicate(timeout=5)
        except Exception:
            process.kill()
            process.communicate()
            raise
        if process.returncode not in (0, 1, -9) and len(matches) < max_results:
            return ToolResult(False, error=f"ripgrep exited with code {process.returncode}")
        return ToolResult(True, "\n".join(matches) or "(no matches)", metadata={"count": len(matches), "truncated": len(matches) >= max_results, "engine": "ripgrep"})

    def _fallback(self, query: str, root: Path, glob: str | None, max_results: int) -> ToolResult:
        matches: list[str] = []
        files = [root] if root.is_file() else self._files(root)
        for file in files:
            if glob and not fnmatch.fnmatch(file.name, glob) and not fnmatch.fnmatch(self.relative(file), glob):
                continue
            try:
                if file.stat().st_size > 2_000_000:
                    continue
                with file.open("r", encoding="utf-8-sig") as stream:
                    for number, line in enumerate(stream, 1):
                        if query in line:
                            matches.append(f"{self.relative(file)}:{number}: {line.rstrip()[:500]}")
                            if len(matches) >= max_results:
                                return ToolResult(True, "\n".join(matches), metadata={"count": len(matches), "truncated": True, "engine": "python"})
            except (OSError, UnicodeError):
                continue
        return ToolResult(True, "\n".join(matches) or "(no matches)", metadata={"count": len(matches), "truncated": False, "engine": "python"})

    def _files(self, root: Path):
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name not in IGNORED and not name.startswith(".env.") and not (Path(directory) / name).is_symlink()]
            for name in files:
                if name in IGNORED or name.startswith(".env.") or name.endswith((".pem", ".key")):
                    continue
                candidate = Path(directory) / name
                if candidate.is_symlink() or not candidate.resolve().is_relative_to(self.workspace):
                    continue
                yield candidate
