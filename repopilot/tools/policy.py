"""Configurable write targets for repository repair."""

from __future__ import annotations

import fnmatch
import posixpath
from dataclasses import dataclass, field
from functools import lru_cache


DEFAULT_PROTECTED_GLOBS = (
    "tests/**", "test/**", "**/tests/**", "**/test/**",
    "**/test_*.py", "**/*_test.py",
)


def _normalize(path: str) -> str:
    value = posixpath.normpath(path.replace("\\", "/"))
    if value in ("", ".", "..") or value.startswith(("/", "../")) or ":" in value.split("/")[0]:
        raise ValueError("unsafe repair policy path")
    return value


def _matches(path: str, pattern: str) -> bool:
    parts = path.split("/")
    globs = pattern.replace("\\", "/").split("/")

    @lru_cache(None)
    def match(i: int, j: int) -> bool:
        if j == len(globs):
            return i == len(parts)
        if globs[j] == "**":
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], globs[j]) and match(i + 1, j + 1)

    return match(0, 0)


@dataclass
class RepairPolicy:
    protected_globs: list[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED_GLOBS))
    writable_globs: list[str] | None = None

    def check(self, path: str) -> tuple[str, str | None]:
        """Return normalized repository path and optional rejection code."""
        normalized = _normalize(path)
        if any(_matches(normalized, glob) for glob in self.protected_globs):
            return normalized, "protected_path"
        if self.writable_globs is not None and not any(_matches(normalized, glob) for glob in self.writable_globs):
            return normalized, "not_writable"
        return normalized, None
