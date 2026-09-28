"""Deterministic Python AST definitions for tracked repository files."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .lexical import StaticRetriever


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    qualified_name: str
    kind: str
    file: str
    start_line: int
    end_line: int
    parent: str | None


def file_role(name: str) -> str:
    normalized = name.replace("\\", "/").lower()
    parts = normalized.split("/")
    stem = Path(parts[-1]).stem
    if any(part in {"test", "tests"} for part in parts[:-1]) or stem.startswith("test_") or stem.endswith("_test"):
        return "test"
    return "source" if normalized.endswith(".py") else "unknown"


class _DefinitionVisitor(ast.NodeVisitor):
    def __init__(self, file: str):
        self.file = file
        self.records: list[SymbolRecord] = []
        self.parents: list[tuple[str, str]] = []

    def _record(self, node: ast.AST, name: str, kind: str) -> None:
        parent = ".".join(part for part, _ in self.parents) or None
        qualified = f"{parent}.{name}" if parent else name
        decorators = getattr(node, "decorator_list", [])
        start = min([node.lineno, *(decorator.lineno for decorator in decorators)])
        self.records.append(SymbolRecord(name, qualified, kind, self.file, start, node.end_lineno, parent))
        self.parents.append((name, kind))
        self.generic_visit(node)
        self.parents.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node, node.name, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        kind = "method" if self.parents and self.parents[-1][1] == "class" else "function"
        self._record(node, node.name, kind)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = "method" if self.parents and self.parents[-1][1] == "class" else "async_function"
        self._record(node, node.name, kind)


@dataclass(frozen=True)
class SymbolIndex:
    by_name: dict[str, tuple[SymbolRecord, ...]]
    by_file: dict[str, tuple[SymbolRecord, ...]]
    parse_errors: dict[str, str]

    @classmethod
    def build(cls, workspace: Path) -> "SymbolIndex":
        root = workspace.resolve(strict=True)
        by_file: dict[str, tuple[SymbolRecord, ...]] = {}
        by_name: dict[str, list[SymbolRecord]] = {}
        errors: dict[str, str] = {}
        for name in StaticRetriever._files(root):
            if not name.lower().endswith(".py"):
                continue
            path = root / name
            try:
                if path.stat().st_size > 200_000:
                    errors[name] = "file_too_large"
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=name)
            except (SyntaxError, UnicodeError, OSError) as exc:
                errors[name] = type(exc).__name__
                continue
            visitor = _DefinitionVisitor(name)
            visitor.visit(tree)
            records = tuple(sorted(visitor.records, key=lambda item: (item.start_line, item.qualified_name)))
            by_file[name] = records
            for record in records:
                by_name.setdefault(record.name, []).append(record)
        return cls(
            {name: tuple(sorted(records, key=lambda item: (item.file, item.start_line))) for name, records in sorted(by_name.items())},
            dict(sorted(by_file.items())), dict(sorted(errors.items())),
        )
