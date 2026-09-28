"""B2 static context: frozen B1 lexical candidates plus AST definition signals."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .lexical import (
    PREAMBLE, ContextItem, RetrievedContext, StaticRetriever, estimate_tokens,
    extract_query, format_context, format_item,
)
from .symbols import SymbolIndex, SymbolRecord, file_role

SYMBOL_WEIGHTS = {
    "qualified_definition": 10.0,
    "exact_definition": 8.0,
    "partial_definition": 3.0,
    "reference": 2.0,
    "source_role": 2.0,
    "related_test": 1.0,
}


@dataclass(frozen=True)
class SymbolRetrieval:
    context: RetrievedContext
    query_identifier_count: int
    matched_symbol_identifiers: tuple[str, ...]
    exact_definition_matches: int
    parse_errors: dict[str, str]

    def as_dict(self) -> dict:
        return {
            **self.context.as_dict(), "mode": "symbol",
            "query_identifier_count": self.query_identifier_count,
            "matched_symbol_identifiers": list(self.matched_symbol_identifiers),
            "exact_definition_matches": self.exact_definition_matches,
            "parse_errors": self.parse_errors,
        }


def _match(record: SymbolRecord, identifiers: tuple[str, ...], raw_symbols: set[str]) -> tuple[float, str | None, str | None]:
    qualified = record.qualified_name.lower()
    name = record.name.lower()
    if "." in qualified and qualified in identifiers:
        return SYMBOL_WEIGHTS["qualified_definition"], f"qualified_symbol_definition: {record.qualified_name}", qualified
    if name in identifiers:
        label = "exact_symbol_definition" if record.name in raw_symbols else "case_insensitive_symbol_definition"
        return SYMBOL_WEIGHTS["exact_definition"], f"{label}: {record.qualified_name}", name
    for identifier in identifiers:
        if len(identifier) >= 4 and (identifier in name or name in identifier):
            return SYMBOL_WEIGHTS["partial_definition"], f"partial_symbol_definition: {record.qualified_name}", identifier
    return 0.0, None, None


def _item(file: str, start: int, end: int, content: str, score: float, evidence: list[str]) -> ContextItem:
    provisional = ContextItem(file, start, end, content, score, 0, evidence)
    return ContextItem(file, start, end, content, score, estimate_tokens(format_item(provisional)), evidence)


class SymbolRetriever:
    def __init__(self, *, max_symbol_lines: int = 80, lexical: StaticRetriever | None = None):
        if max_symbol_lines < 1:
            raise ValueError("max_symbol_lines must be positive")
        self.max_symbol_lines = max_symbol_lines
        self.lexical = lexical if lexical is not None else StaticRetriever()

    def retrieve(self, issue: str, workspace: Path, context_budget_tokens: int = 8000) -> SymbolRetrieval:
        if context_budget_tokens < estimate_tokens(PREAMBLE):
            raise ValueError("context budget is too small")
        root = workspace.resolve(strict=True)
        query = extract_query(issue)
        raw_symbols = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", issue))
        # Ask frozen B1 for all bounded lexical candidates; B2 applies its own
        # fixed score before the same score/cost greedy budget packing.
        lexical = self.lexical.retrieve(issue, root, 1_000_000)
        index = SymbolIndex.build(root)
        best_lexical: dict[str, float] = {}
        for item in lexical.items:
            best_lexical[item.file] = max(best_lexical.get(item.file, 0.0), item.score)
        symbol_items: list[ContextItem] = []
        matched_names: set[str] = set()
        exact_count = 0
        spans: dict[str, list[tuple[int, int]]] = {}
        for file, records in index.by_file.items():
            lines: list[str] | None = None
            for record in records:
                bonus, evidence, matched = _match(record, query.identifiers, raw_symbols)
                if not bonus or evidence is None:
                    continue
                if lines is None:
                    lines = (root / file).read_text(encoding="utf-8-sig").splitlines()
                matched_names.add(matched or record.name.lower())
                if bonus >= SYMBOL_WEIGHTS["exact_definition"]:
                    exact_count += 1
                start = record.start_line
                end = min(record.end_line, start + self.max_symbol_lines - 1)
                role = file_role(file)
                role_bonus = SYMBOL_WEIGHTS["source_role"] if role == "source" else 0.0
                reasons = [evidence, f"file_role: {role}", f"symbol: {record.qualified_name} [{record.kind} definition]"]
                score = best_lexical.get(file, 0.0) + bonus + role_bonus
                symbol_items.append(_item(file, start, end, "\n".join(lines[start - 1:end]), score, reasons))
                spans.setdefault(file, []).append((start, end))
        candidates = list(symbol_items)
        for item in lexical.items:
            if any(item.start_line <= end and start <= item.end_line for start, end in spans.get(item.file, [])):
                continue
            role = file_role(item.file)
            relation = role == "test" and "test_source_relation" in item.evidence
            reference_terms = [identifier for identifier in query.identifiers if len(identifier) >= 4 and identifier in item.content.lower()]
            is_reference = bool(reference_terms)
            bonus = (SYMBOL_WEIGHTS["source_role"] if role == "source" else 0.0)
            bonus += SYMBOL_WEIGHTS["related_test"] if relation else 0.0
            bonus += SYMBOL_WEIGHTS["reference"] if is_reference else 0.0
            reasons = [*item.evidence, f"file_role: {role}"]
            if relation:
                reasons.append("related_test")
            if is_reference:
                reasons.append("symbol_reference: " + ", ".join(sorted(reference_terms)))
            candidates.append(_item(item.file, item.start_line, item.end_line, item.content, item.score + bonus, reasons))
        # A symbol may also appear in an existing lexical snippet with an
        # adjacent, non-overlapping span; stable tie ordering is explicit.
        ordered = sorted(candidates, key=lambda item: (-item.score / item.token_cost, -item.score, item.file, item.start_line))
        selected: list[ContextItem] = []
        per_file: dict[str, int] = {}
        seen: set[tuple[str, int, int]] = set()
        total = estimate_tokens(PREAMBLE)
        for item in ordered:
            key = (item.file, item.start_line, item.end_line)
            if key in seen or per_file.get(item.file, 0) >= self.lexical.max_snippets_per_file or total + item.token_cost > context_budget_tokens:
                continue
            selected.append(item)
            seen.add(key)
            per_file[item.file] = per_file.get(item.file, 0) + 1
            total += item.token_cost
        context = RetrievedContext(selected, total, len(candidates), context_budget_tokens)
        assert estimate_tokens(format_context(context)) <= total
        return SymbolRetrieval(context, len(query.identifiers), tuple(sorted(matched_names)), exact_count, index.parse_errors)
