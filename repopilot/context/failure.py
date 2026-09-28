"""Deterministic evidence and query extraction from a failed pytest run."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import asdict, dataclass

from .lexical import extract_query

FRAME = re.compile(r"(?m)^\s*([\w./\\-]+\.py):(\d+)(?::\s*in\s+([A-Za-z_][A-Za-z_0-9]*))?")
EXCEPTION = re.compile(r"\b([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception))\b")


def _unique(values):
    return tuple(dict.fromkeys(value for value in values if value))


def _file(value: str) -> str | None:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if not normalized.endswith(".py") or normalized.startswith(("/", "../")) or normalized == ".." or ":" in normalized.split("/")[0]:
        return None
    return normalized.removeprefix("./")


@dataclass(frozen=True)
class TracebackFrame:
    file: str
    line: int | None
    function: str | None


@dataclass(frozen=True)
class FailureEvidence:
    failed_tests: tuple[str, ...]
    exception_types: tuple[str, ...]
    traceback_frames: tuple[TracebackFrame, ...]
    assertion_messages: tuple[str, ...]
    mentioned_files: tuple[str, ...]
    mentioned_symbols: tuple[str, ...]
    line_numbers: tuple[int, ...]
    raw_summary: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FailureQuery:
    identifiers: tuple[str, ...]
    file_hints: tuple[str, ...]
    test_hints: tuple[str, ...]
    exception_terms: tuple[str, ...]
    assertion_terms: tuple[str, ...]
    original_issue: str

    def retrieval_text(self) -> str:
        return " ".join((
            self.original_issue,
            *self.identifiers, *self.file_hints, *self.exception_terms, *self.assertion_terms,
        ))[:2000]


def extract_failure_evidence(test_result: dict) -> FailureEvidence:
    stdout = str(test_result.get("stdout") or "")[:20_000]
    stderr = str(test_result.get("stderr") or "")[:10_000]
    text = stdout + "\n" + stderr
    failed_values = test_result.get("failed_tests") or re.findall(r"(?m)^FAILED\s+([^\s]+)", text)
    failed = _unique(str(value)[:300] for value in failed_values[:20])
    frames: list[TracebackFrame] = []
    for match in FRAME.finditer(text):
        file = _file(match.group(1))
        if file:
            frame = TracebackFrame(file, int(match.group(2)), match.group(3))
            if frame not in frames:
                frames.append(frame)
        if len(frames) >= 16:
            break
    for location in (test_result.get("traceback_locations") or [])[:16]:
        match = re.fullmatch(r"(.+\.py):(\d+)", str(location))
        if match:
            file = _file(match.group(1))
            frame = TracebackFrame(file, int(match.group(2)), None) if file else None
            if frame and not any(item.file == frame.file and item.line == frame.line for item in frames):
                frames.append(frame)
    error_types = _unique([str(test_result.get("exception_type") or ""), *EXCEPTION.findall(text)])[:8]
    assertions = []
    primary = test_result.get("assertion_message")
    if primary:
        assertions.append(str(primary).strip()[:240])
    for match in re.finditer(r"(?m)^E\s+(assert\b[^\n]{0,240})", text):
        assertions.append(match.group(1).strip())
        if len(assertions) >= 4:
            break
    assertions = list(_unique(assertions))
    failed_files = [_file(node.split("::", 1)[0]) for node in failed]
    mentioned_files = _unique([*failed_files, *(frame.file for frame in frames)])
    text_for_symbols = " ".join((
        *(node.split("::")[-1] for node in failed),
        *(frame.function or "" for frame in frames),
        *assertions,
        *(str(test_result.get("assertion_message") or "").split()[:20]),
    ))
    query = extract_query(text_for_symbols)
    symbols = _unique([*query.identifiers, *(frame.function for frame in frames if frame.function)])[:30]
    summary = " | ".join([*failed[:3], *error_types[:2], *assertions[:2], *(f"{frame.file}:{frame.line}" for frame in frames[:4])])[:600]
    return FailureEvidence(
        failed, error_types, tuple(frames), tuple(assertions), mentioned_files,
        symbols, _unique(frame.line for frame in frames if frame.line is not None), summary,
    )


def build_failure_query(issue: str, evidence: FailureEvidence) -> FailureQuery:
    initial = extract_query(issue)
    failure_text = " ".join((*evidence.mentioned_symbols, *evidence.assertion_messages, *evidence.exception_types))
    failure = extract_query(failure_text)
    tests = _unique(node.split("::", 1)[0] for node in evidence.failed_tests)
    return FailureQuery(
        _unique((*initial.identifiers, *failure.identifiers, *evidence.mentioned_symbols)),
        _unique((*initial.file_hints, *evidence.mentioned_files)), tests,
        _unique(term.lower() for term in evidence.exception_types for term in extract_query(term).terms),
        _unique(extract_query(" ".join(evidence.assertion_messages)).terms),
        issue,
    )


def failure_signature(evidence: FailureEvidence) -> str:
    value = {
        "failed_tests": sorted(evidence.failed_tests),
        "frames": sorted((frame.file, frame.function or "") for frame in evidence.traceback_frames),
        "exceptions": sorted(evidence.exception_types),
        "assertions": sorted(evidence.assertion_messages),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
