"""Deterministic lexical retrieval from a clean Git repository."""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from repopilot.tools.base import is_sensitive_name

STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "bug", "by", "can", "cause", "code",
    "do", "does", "error", "file", "find", "fix", "for", "from", "in", "incorrect",
    "is", "issue", "it", "of", "on", "or", "patch", "preserve", "pytest", "return",
    "returns", "should", "test", "tests", "that", "the", "this", "to", "verify", "with",
})
WEIGHTS = {"filename": 5.0, "path": 2.0, "identifier": 4.0, "keyword": 1.0, "relation": 2.0, "density": 0.5}
WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*")
FILE_HINT = re.compile(r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+\.(?:py|md|txt|toml)(?!\w)")
PREAMBLE = "# Retrieved Repository Context\nSelected once from the issue and repository contents before repair. Verify snippets against current files.\n"


@dataclass(frozen=True)
class IssueQuery:
    identifiers: tuple[str, ...]
    keywords: tuple[str, ...]
    file_hints: tuple[str, ...]

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.identifiers, *self.keywords)))


def extract_query(issue: str) -> IssueQuery:
    # Email addresses can otherwise inject "Example.COM" as a false dotted symbol.
    cleaned = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", " ", issue)
    hints = tuple(dict.fromkeys(match.group(0).lower() for match in FILE_HINT.finditer(cleaned)))
    identifiers: list[str] = []
    keywords: list[str] = []
    for match in WORD.finditer(cleaned):
        raw = match.group(0)
        lower = raw.lower()
        if lower in STOPWORDS or len(lower) < 3 or lower.isdigit():
            continue
        symbol = "." in raw or "_" in raw or any(char.isupper() for char in raw[1:]) or cleaned[match.end():].startswith("()")
        if symbol:
            for part in (raw, *raw.split(".")):
                if part.lower() not in STOPWORDS and part.lower() not in identifiers:
                    identifiers.append(part.lower())
                components = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+", part.replace("_", " "))
                for component in components:
                    value = component.lower()
                    if len(value) >= 3 and value not in STOPWORDS and value not in identifiers:
                        identifiers.append(value)
        elif lower not in keywords:
            keywords.append(lower)
    return IssueQuery(tuple(identifiers), tuple(keywords), hints)


def estimate_tokens(text: str) -> int:
    """Fixed approximation: ceil(UTF-8 bytes / 4), not provider usage."""
    return max(1, math.ceil(len(text.encode("utf-8")) / 4))


@dataclass(frozen=True)
class ContextItem:
    file: str
    start_line: int
    end_line: int
    content: str
    score: float
    token_cost: int
    evidence: list[str]


@dataclass(frozen=True)
class RetrievedContext:
    items: list[ContextItem]
    total_tokens: int
    candidate_count: int
    budget_tokens: int
    token_estimation: str = "ceil(UTF-8 bytes / 4)"

    def as_dict(self) -> dict:
        return {
            "items": [asdict(item) for item in self.items],
            "total_tokens": self.total_tokens,
            "candidate_count": self.candidate_count,
            "selected_count": len(self.items),
            "budget_tokens": self.budget_tokens,
            "token_estimation": self.token_estimation,
            "retrieved_files": list(dict.fromkeys(item.file for item in self.items)),
        }


def format_item(item: ContextItem) -> str:
    reasons = "\n".join(f"- {evidence}" for evidence in item.evidence)
    numbered = "\n".join(f"{number} | {line}" for number, line in enumerate(item.content.splitlines(), item.start_line))
    return f"\n## {item.file}:{item.start_line}-{item.end_line}\nReason:\n{reasons}\n```text\n{numbered}\n```\n"


def format_context(result: RetrievedContext) -> str:
    return PREAMBLE + "".join(format_item(item) for item in result.items)


def _stem(name: str) -> str:
    stem = Path(name).stem.lower()
    if stem.startswith("test_"):
        stem = stem[5:]
    if stem.endswith("_test"):
        stem = stem[:-5]
    return stem


def _is_test(path: str) -> bool:
    parts = path.lower().split("/")
    return "tests" in parts or "test" in parts or Path(path).stem.startswith("test_") or Path(path).stem.endswith("_test")


def _merge_windows(hits: list[int], line_count: int, radius: int, max_lines: int) -> list[tuple[int, int]]:
    windows = sorted((max(1, hit - radius), min(line_count, hit + radius)) for hit in set(hits))
    merged: list[list[int]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, min(end, start + max_lines - 1)) for start, end in merged]


class StaticRetriever:
    def __init__(self, *, max_hits_per_term: int = 20, window_radius: int = 20, max_snippet_lines: int = 80, max_snippets_per_file: int = 3):
        if min(max_hits_per_term, window_radius, max_snippet_lines, max_snippets_per_file) < 1:
            raise ValueError("retrieval limits must be positive")
        self.max_hits_per_term = max_hits_per_term
        self.window_radius = window_radius
        self.max_snippet_lines = max_snippet_lines
        self.max_snippets_per_file = max_snippets_per_file

    def retrieve(self, issue: str, workspace: Path, context_budget_tokens: int = 8000) -> RetrievedContext:
        if not issue.strip() or context_budget_tokens < estimate_tokens(PREAMBLE):
            raise ValueError("issue and context budget must be nonempty")
        root = workspace.resolve(strict=True)
        files = self._files(root)
        query = extract_query(issue)
        hits = self._hits(root, files, query.terms)
        stems = {_stem(name): [] for name in files}
        for name in files:
            stems[_stem(name)].append(name)
        candidates: list[ContextItem] = []
        for name in files:
            path = root / name
            try:
                if path.stat().st_size > 200_000:
                    continue
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except (OSError, UnicodeError):
                continue
            if not lines:
                continue
            basename = Path(name).stem.lower()
            path_lower = name.lower()
            filename_terms = [term for term in query.terms if term in basename and len(term) >= 3]
            path_terms = [term for term in query.terms if term in path_lower and term not in filename_terms and len(term) >= 3]
            exact_hint = [hint for hint in query.file_hints if hint == path_lower or path_lower.endswith("/" + hint)]
            file_hits = hits.get(name, {})
            relation = any(other != name and _is_test(other) != _is_test(name) and (_stem(other) == _stem(name)) and (other in hits or any(term in Path(other).stem.lower() for term in query.terms)) for other in stems[_stem(name)])
            if not (filename_terms or path_terms or exact_hint or file_hits or relation):
                continue
            evidence = []
            if filename_terms or exact_hint:
                evidence.append("filename_match: " + ", ".join(sorted(set(filename_terms + exact_hint))))
            if path_terms:
                evidence.append("path_match: " + ", ".join(sorted(path_terms)))
            if relation:
                evidence.append("test_source_relation")
            matched_terms = set().union(*file_hits.values()) if file_hits else set()
            identifier_hits = [term for term in query.identifiers if term in matched_terms]
            keyword_hits = [term for term in query.keywords if term in matched_terms]
            if identifier_hits:
                evidence.append("identifier_match: " + ", ".join(sorted(identifier_hits)))
            if keyword_hits:
                evidence.append("keyword_hit: " + ", ".join(sorted(keyword_hits)))
            score = (
                WEIGHTS["filename"] * bool(filename_terms or exact_hint)
                + WEIGHTS["path"] * bool(path_terms)
                + WEIGHTS["identifier"] * len(identifier_hits)
                + WEIGHTS["keyword"] * len(keyword_hits)
                + WEIGHTS["relation"] * relation
                + WEIGHTS["density"] * min(4, len(file_hits))
            )
            anchors = sorted(file_hits) or [1]
            for start, end in _merge_windows(anchors, len(lines), self.window_radius, self.max_snippet_lines)[:self.max_snippets_per_file]:
                content = "\n".join(lines[start - 1:end])
                provisional = ContextItem(name, start, end, content, float(score), 0, evidence)
                cost = estimate_tokens(format_item(provisional))
                candidates.append(ContextItem(name, start, end, content, float(score), cost, evidence))
        ordered = sorted(candidates, key=lambda item: (-item.score / item.token_cost, -item.score, item.file, item.start_line))
        selected: list[ContextItem] = []
        total = estimate_tokens(PREAMBLE)
        per_file: dict[str, int] = {}
        for item in ordered:
            if per_file.get(item.file, 0) >= self.max_snippets_per_file or total + item.token_cost > context_budget_tokens:
                continue
            selected.append(item)
            per_file[item.file] = per_file.get(item.file, 0) + 1
            total += item.token_cost
        # Recompute rendered total: rounding each item separately is an upper bound.
        assert estimate_tokens(format_context(RetrievedContext(selected, total, len(candidates), context_budget_tokens))) <= total
        return RetrievedContext(selected, total, len(candidates), context_budget_tokens)

    @staticmethod
    def _files(root: Path) -> list[str]:
        process = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True, timeout=10)
        names = []
        for raw in process.stdout.split(b"\0"):
            if not raw:
                continue
            name = raw.decode("utf-8", errors="replace").replace("\\", "/")
            path = root / name
            if any(part == ".git" or is_sensitive_name(part) for part in Path(name).parts):
                continue
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                continue
            names.append(name)
        return sorted(names)

    def _hits(self, root: Path, files: list[str], terms: tuple[str, ...]) -> dict[str, dict[int, set[str]]]:
        result: dict[str, dict[int, set[str]]] = {}
        rg = shutil.which("rg")
        allowed = set(files)
        for term in terms[:20]:
            if rg:
                process = subprocess.Popen([rg, "-n", "-i", "-F", "--sort", "path", "--no-heading", "--color", "never", "--with-filename",
                    "--glob", "!.env*", "--glob", "!.npmrc", "--glob", "!.pypirc", "--glob", "!credentials.*",
                    "--glob", "!secrets.*", "--glob", "!*.pem", "--glob", "!*.key", "--", term, "."],
                    cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
                try:
                    assert process.stdout is not None
                    count = 0
                    for raw in process.stdout:
                        parts = raw.rstrip("\n").split(":", 2)
                        if len(parts) != 3:
                            continue
                        name = parts[0].removeprefix(".\\").removeprefix("./").replace("\\", "/")
                        if name not in allowed or not parts[1].isdigit():
                            continue
                        result.setdefault(name, {}).setdefault(int(parts[1]), set()).add(term)
                        count += 1
                        if count >= self.max_hits_per_term:
                            process.kill()
                            break
                    process.communicate(timeout=10)
                except Exception:
                    process.kill()
                    process.communicate()
                    raise
            else:
                count = 0
                for name in files:
                    try:
                        lines = (root / name).read_text(encoding="utf-8-sig").splitlines()
                    except (OSError, UnicodeError):
                        continue
                    for number, line in enumerate(lines, 1):
                        if term in line.lower():
                            result.setdefault(name, {}).setdefault(number, set()).add(term)
                            count += 1
                            if count >= self.max_hits_per_term:
                                break
                    if count >= self.max_hits_per_term:
                        break
        return result
