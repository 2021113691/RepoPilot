"""Post-run overlap of read_file observations with initial context snippets."""

from __future__ import annotations

import json
import posixpath
from pathlib import Path
from typing import Iterable

from repopilot.context.lexical import ContextItem


def _relative(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/")).removeprefix("./")


def analyze_redundant_reads(trajectory: Path, items: Iterable[ContextItem]) -> dict:
    initial: dict[str, list[tuple[int, int]]] = {}
    for item in items:
        initial.setdefault(_relative(item.file), []).append((item.start_line, item.end_line))
    read_calls = same_file = overlap = 0
    for raw in trajectory.read_text(encoding="utf-8").splitlines():
        event = json.loads(raw)
        if event.get("event") != "tool_call" or event.get("tool") != "read_file":
            continue
        read_calls += 1
        if not event.get("success"):
            continue
        metadata = event.get("metadata") or {}
        name = metadata.get("file")
        start = metadata.get("start_line")
        end = metadata.get("end_line")
        if not isinstance(name, str) or not isinstance(start, int) or not isinstance(end, int):
            continue
        windows = initial.get(_relative(name), [])
        if windows:
            same_file += 1
        if any(start <= context_end and context_start <= end for context_start, context_end in windows):
            overlap += 1
    return {
        "read_file_calls": read_calls,
        "same_file_reread_calls": same_file,
        "overlapping_reread_calls": overlap,
        "same_file_reread_ratio": same_file / read_calls if read_calls else None,
        "overlapping_reread_ratio": overlap / read_calls if read_calls else None,
    }
